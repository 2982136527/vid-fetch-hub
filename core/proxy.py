"""Proxy server — receives playback requests from Emby via STRM files,
fetches fresh video URLs from source sites in real-time, and redirects."""

import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import unquote

import aiohttp
from aiohttp import web

logger = logging.getLogger("proxy")


class VideoProxy:
    """HTTP proxy that resolves fresh video URLs on-the-fly."""

    def __init__(self, config, db, downloader):
        self.config = config
        self.db = db
        self.dl = downloader
        self.host = config.proxy_host
        self.port = config.proxy_port
        self.cache_ttl = config.proxy_cache_seconds
        self._cache: dict[str, tuple[str, float]] = {}
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._server: Optional[asyncio.AbstractServer] = None

    async def _resolve_video_url(self, source: str, video_id: str) -> tuple[Optional[str], Optional[str]]:
        """Scrape the video page to get a fresh playback URL.
        Returns (video_url, page_url) tuple.
        """
        logger.info(f"Resolving fresh URL: {source}/{video_id}")

        # Look up page_url from DB
        record = self.db.get_video(source, video_id)
        if not record:
            logger.warning(f"Video not found in DB: {source}/{video_id}")
            return None, None

        page_url = record.get("page_url", "")
        if not page_url:
            logger.warning(f"No page_url for {source}/{video_id}")
            return None, None

        logger.info(f"Scraping page for fresh URL: {page_url}")

        # --- ACFAN (JSON API, no page scraping needed) ---
        if source == "acfan":
            from crawlers.acfan import AcfanCrawler
            crawler = AcfanCrawler(self.config, self.db, self.dl)
            await crawler._resolve_domain()
            detail = await crawler._get_video_detail(int(video_id))
            if detail:
                video_url = detail.get("playPath") or detail.get("videoUrl", "")
                if video_url and not video_url.startswith("http"):
                    video_url = f"{crawler._api_base}/api/m3u8/h5/decode?path={video_url}"
                if video_url:
                    return video_url, page_url
            return None, None

        # Scrape page to get fresh video URL
        try:
            html = await self.dl.get_text(page_url)

            # --- PornHub: use mediaDefinitions (full video, not preview) ---
            if source == "pornhub":
                defs = self._extract_json_array(html, "mediaDefinitions")
                hls_url = None
                gm_url = None

                if defs:
                    for d in defs:
                        url = d.get("videoUrl", "").replace("\\/", "/")
                        if not url:
                            continue
                        if "get_media" in url:
                            gm_url = url
                        elif not hls_url:
                            hls_url = url

                    # 1. Try best quality HLS/MP4 first
                    if hls_url:
                        return hls_url, page_url

                    # 2. Try get_media (server-side fresh URL)
                    if gm_url:
                        return gm_url, page_url

                # 3. Last resort: data-mediabook preview
                mb = re.search(r'data-mediabook="([^"]+)"', html)
                if mb:
                    return mb.group(1).replace("&amp;", "&"), page_url

                logger.warning(f"No playable URL for {source}/{video_id}")

            elif source in ("youporn", "thumbzilla", "tube8", "redtube"):
                m = re.search(r'data-mediabook="([^"]+)"', html)
                if m:
                    return m.group(1).replace("&amp;", "&"), page_url

            # --- XVideos (JSON API) — prefer MP4 high, HLS as fallback ---
            elif source == "xvideos":
                encoded = re.search(r'/video\.([a-z0-9]+)/', page_url)
                if encoded:
                    eid = encoded.group(1)
                    text = await self.dl.get_text(
                        f"https://www.xvideos.com/html5player/getvideo/{eid}/0"
                    )
                    import json
                    try:
                        data = json.loads(text)
                        # mp4_high = 360p. HLS has better quality but needs segment rewriting.
                        url = (data.get("mp4_high") or data.get("mp4_low") or data.get("hls", ""))
                        if url:
                            return url.replace("\\/", "/"), page_url
                    except json.JSONDecodeError:
                        for part in text.split("&"):
                            if "=" in part:
                                k, v = part.split("=", 1)
                                if k in ("mp4_high", "mp4_low", "hls", "video_url"):
                                    return unquote(v), page_url

            # --- Madou (JWT token) ---
            elif source == "madou":
                share_m = re.search(
                    r'src="?https://dash\.madou\.club/share/([a-f0-9]+)', html
                )
                if not share_m:
                    share_m = re.search(
                        r"src='https://dash\.madou\.club/share/([a-f0-9]+)", html
                    )
                if share_m:
                    sid = share_m.group(1)
                    ifhtml = await self.dl.get_text(
                        f"https://dash.madou.club/share/{sid}",
                        headers={"Referer": page_url},
                    )
                    token_m = re.search(r"""token\s*=\s*['"]([^'"]+)['"]""", ifhtml)
                    m3u8_m = re.search(r"""var m3u8\s*=\s*'([^']+)'""", ifhtml)
                    if m3u8_m:
                        m3u8_path = m3u8_m.group(1)
                        url = f"https://dash.madou.club{m3u8_path}"
                        if token_m:
                            url += f"?token={token_m.group(1)}"
                        return url, page_url

            # --- 18mh (auth_key) ---
            elif source == "18mh":
                detail_m = re.search(
                    r"const _detail_\s*=\s*(\{.*?\});", html, re.DOTALL
                )
                if detail_m:
                    import json
                    d = json.loads(detail_m.group(1).replace("\\/", "/"))
                    fresh_url = d.get("url") or d.get("view_url", "")
                    if fresh_url:
                        return fresh_url, page_url

        except Exception as e:
            logger.error(f"Failed to resolve {source}/{video_id}: {e}")

        return None, None

    def _extract_json_array(self, text: str, key: str) -> Optional[list]:
        """Extract a JSON array value for a given key, handling nested brackets."""
        import json as _json
        pattern = f'"{key}"\\s*:\\s*'
        m = re.search(pattern, text)
        if not m:
            return None
        start = m.end()
        if start >= len(text) or text[start] != '[':
            return None
        # Walk through brackets, handling strings with escapes
        depth = 0
        i = start
        while i < len(text):
            ch = text[i]
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    try:
                        return _json.loads(text[start:i+1])
                    except Exception:
                        return None
            elif ch == '"':
                i += 1
                while i < len(text) and text[i] != '"':
                    if text[i] == '\\':
                        i += 1
                    i += 1
            i += 1
        return None

    def _get_proxy_headers(self, source: str, page_url: str) -> dict:
        """Get the headers needed to access video CDN for each source."""
        base_headers = {
            "User-Agent": self.config.user_agent,
        }
        if source in ("youporn", "redtube", "thumbzilla", "tube8", "pornhub"):
            base_headers["Cookie"] = "access=1"
            base_headers["Referer"] = page_url or f"https://www.{source}.com/"
        elif source == "xvideos":
            base_headers["Referer"] = "https://www.xvideos.com/"
        elif source == "acfan":
            base_headers["Referer"] = page_url or "https://521.acfan.com/"
        elif source == "madou":
            base_headers["Referer"] = page_url or "https://madou.club/"
        elif source == "18mh":
            base_headers["Referer"] = "https://18mh.net/"
        return base_headers

    async def _proxy_stream(self, video_url: str, source: str, page_url: str,
                            request: web.Request) -> web.StreamResponse:
        """Fetch video from CDN with proper headers and stream to client."""
        headers = self._get_proxy_headers(source, page_url)
        timeout = aiohttp.ClientTimeout(total=3600)

        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(video_url, ssl=False) as resp:
                if resp.status != 200:
                    logger.warning(f"CDN returned {resp.status} for {video_url[:80]}")
                    return web.Response(
                        status=resp.status,
                        text=f"CDN error: {resp.status}",
                    )

                ctype = resp.headers.get("Content-Type", "video/mp4")
                if ".m3u8" in video_url and ctype == "text/plain":
                    ctype = "application/vnd.apple.mpegurl"

                # For HLS playlists, rewrite relative URLs to absolute CDN URLs
                # so the player can fetch segments directly from CDN
                if ".m3u8" in video_url:
                    data = await resp.read()
                    text = data.decode("utf-8", errors="ignore")
                    # Determine CDN base URL (everything up to the m3u8 filename)
                    import re as _re
                    base_match = _re.match(r'(https?://.*\/)[^/]+\.m3u8', video_url)
                    if base_match:
                        cdn_base = base_match.group(1)
                        # Rewrite relative paths to absolute CDN URLs
                        def make_abs(m):
                            path = m.group(1)
                            if not path.startswith("http"):
                                return f"{cdn_base}{path}"
                            return m.group(0)
                        text = _re.sub(r'^([a-zA-Z0-9_\-./?=&%:+]+\.(?:ts|m3u8)(?:[?&][^\s#]*)?)', make_abs, text, flags=_re.MULTILINE)
                        data = text.encode("utf-8")

                    sr = web.StreamResponse(
                        status=200,
                        headers={
                            "Content-Type": ctype,
                            "Content-Disposition": "inline",
                            "Accept-Ranges": "bytes",
                        },
                    )
                    sr.headers["Content-Length"] = str(len(data))
                    await sr.prepare(request)
                    await sr.write(data)
                    return sr

                sr = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": ctype,
                        "Content-Disposition": "inline",
                        "Accept-Ranges": "bytes",
                    },
                )
                cl = resp.headers.get("Content-Length")
                if cl:
                    sr.headers["Content-Length"] = cl

                await sr.prepare(request)

                async for chunk in resp.content.iter_chunked(65536):
                    await sr.write(chunk)

                return sr

    async def _handle_proxy(self, request: web.Request) -> web.StreamResponse:
        """Handle /proxy/{source}/{video_id} → stream video content."""
        source = request.match_info.get("source", "")
        video_id = request.match_info.get("video_id", "")

        if not source or not video_id:
            return web.Response(status=400, text="Missing source or video_id")

        cache_key = f"{source}:{video_id}"
        now = time.time()

        # Check cache
        if cache_key in self._cache:
            cached_url, expiry, cached_page = self._cache[cache_key]
            if now < expiry:
                logger.info(f"Cache HIT: {cache_key}")
                return await self._proxy_stream(cached_url, source, cached_page, request)

        # Resolve fresh URL
        fresh_url, page_url = await self._resolve_video_url(source, video_id)
        if not fresh_url:
            return web.Response(
                status=502,
                text=f"Cannot resolve video URL for {source}/{video_id}",
            )

        # Cache
        self._cache[cache_key] = (fresh_url, now + self.cache_ttl, page_url)
        logger.info(f"Proxying: {source}/{video_id} → {fresh_url[:100]}...")

        return await self._proxy_stream(fresh_url, source, page_url, request)

    async def _handle_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "running",
            "proxy_for": f"http://{self.host}:{self.port}/proxy/{{source}}/{{video_id}}",
            "cache_size": len(self._cache),
            "db_stats": self.db.get_stats() if self.db else {},
        })

    async def start(self):
        """Start the HTTP proxy server."""
        self._app = web.Application()
        self._app.router.add_get("/proxy/{source}/{video_id}", self._handle_proxy)
        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_get("/", self._handle_status)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._server = web.TCPSite(self._runner, self.host, self.port)
        await self._server.start()
        logger.info(f"Proxy running at http://{self.host}:{self.port}")
        logger.info(f"STRM format: http://{self.host}:{self.port}/proxy/{{source}}/{{video_id}}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
        logger.info("Proxy stopped")

    @property
    def running(self) -> bool:
        return self._server is not None
