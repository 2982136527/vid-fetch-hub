"""Base crawler class — proxy/STRM mode only. No video downloading."""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Optional


@dataclass
class VideoInfo:
    """Normalized video information across all sources."""
    source: str
    video_id: str
    title: str
    page_url: str  # URL of the page containing the video
    cover_urls: list[str] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    duration_seconds: int = 0
    rating: float = 0.0
    year: str = ""
    actors: list[str] = field(default_factory=list)


class BaseCrawler(ABC):
    """Abstract base crawler. Scrapes metadata + covers only (no video download)."""

    def __init__(self, config: Any, db: Any, downloader: Any):
        self.config = config
        self.db = db
        self.dl = downloader
        self.site_config = config.site_config(self.name)
        self.logger = logging.getLogger(f"crawler.{self.name}")

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def get_latest_videos(self) -> AsyncGenerator[VideoInfo, None]:
        ...

    async def get_all_videos(self) -> AsyncGenerator[VideoInfo, None]:
        """Default: just delegates to get_latest_videos. Override for pagination."""
        async for v in self.get_latest_videos():
            yield v

    async def crawl(self, backfill: bool = False) -> int:
        """Crawl videos.

        - backfill=True: process ALL videos from this source, skip already-downloaded
        - backfill=False: process NEW videos, stop after 3 consecutive known videos
        """
        count = 0
        known_streak = 0
        async for video in self.get_all_videos():
            if not self.db.is_downloaded(self.name, video.video_id):
                try:
                    await self._process_video(video)
                    count += 1
                    known_streak = 0
                except Exception as e:
                    self.logger.error(f"Error processing {video.video_id}: {e}")
                    known_streak = 0  # Don't count errors as "known"
            elif not backfill:
                known_streak += 1
                if known_streak >= 3:
                    self.logger.info(f"Hit {known_streak} known videos in a row, stopping incremental")
                    break
            else:
                known_streak += 1
        self.logger.info(f"Crawled {count} new videos from {self.name}")
        return count

    async def _process_video(self, video: VideoInfo):
        """Generate STRM, download covers, write NFO."""
        from emby import sanitize_filename, generate_nfo

        output_dir = self.config.output_dir
        by_site = self.config.organize_by_site
        safe_title = sanitize_filename(video.title) or f"video_{video.video_id}"

        if by_site:
            folder = output_dir / sanitize_filename(self.name) / safe_title
        else:
            folder = output_dir / f"{sanitize_filename(self.name)} - {safe_title}"
        folder.mkdir(parents=True, exist_ok=True)

        # --- STRM file with proxy URL ---
        proxy_url = self._build_proxy_url(video)
        strm_file = folder / f"{safe_title}.strm"
        with open(strm_file, "w") as f:
            f.write(proxy_url)
        strm_path = str(strm_file)

        # --- Download covers ---
        poster_url, fanart_url = self._select_poster_fanart(video.cover_urls)
        poster_path = fanart_path = ""

        # Cover CDN headers (some sources need Referer)
        cover_headers = {"User-Agent": self.config.user_agent}
        if self.name == "18mh":
            cover_headers["Referer"] = "https://18mh.net/"

        # ACFAN: covers are AES-encrypted on CDN, use headless browser to render
        if self.name == "acfan" and video.page_url and (poster_url or fanart_url):
            try:
                from core.headless import get_browser
                browser = await get_browser()
                pp = folder / "poster.jpg"
                ok = await browser.screenshot_cover(video.page_url, pp)
                if ok:
                    poster_path = str(pp)
                    fanart_path = str(pp)
                    self.logger.info(f"Cover extracted via headless browser: {pp}")
            except Exception as e:
                self.logger.warning(f"Headless cover failed [{video.video_id}]: {e}")
        elif poster_url:
            ext = self._get_ext(poster_url, ".jpg")
            pp = folder / f"poster{ext}"
            try:
                temp = await self.dl.download_file(poster_url, pp, headers=cover_headers, label=f"{video.video_id}/poster")
                poster_path = str(temp)
            except Exception as e:
                self.logger.warning(f"Poster download failed [{video.video_id}]: {e}")

        if fanart_url:
            ext = self._get_ext(fanart_url, ".jpg")
            fp = folder / f"fanart{ext}"
            if fanart_url == poster_url and poster_path:
                fanart_path = poster_path
            else:
                try:
                    await self.dl.download_file(fanart_url, fp, headers=cover_headers, label=f"{video.video_id}/fanart")
                    fanart_path = str(fp)
                except Exception as e:
                    self.logger.warning(f"Fanart download failed [{video.video_id}]: {e}")
        elif poster_path:
            fanart_path = poster_path

        # --- NFO ---
        nfo = generate_nfo(
            title=video.title,
            year=video.year,
            plot=video.description,
            tags=video.tags,
            genres=video.categories,
            studio=self.name,
            rating=video.rating,
            runtime_seconds=video.duration_seconds,
            actors=video.actors,
            video_id=video.video_id,
            source_url=video.page_url,
        )
        nfo_path = folder / "movie.nfo"
        with open(nfo_path, "w", encoding="utf-8") as f:
            f.write(nfo)

        # --- Database ---
        self.db.mark_downloaded(
            source=self.name,
            video_id=video.video_id,
            title=video.title,
            page_url=video.page_url,
            strm_path=strm_path,
            poster_path=poster_path,
            fanart_path=fanart_path,
            nfo_path=str(nfo_path),
        )
        self.logger.info(f"OK: {video.title} [{video.video_id}] → {proxy_url}")

    def _build_proxy_url(self, video: VideoInfo) -> str:
        """Build the proxy URL that goes into the STRM file."""
        from urllib.parse import quote
        base = self.config.proxy_public_url
        return f"{base}/proxy/{self.name}/{quote(video.video_id)}"

    def _select_poster_fanart(self, urls: list[str]) -> tuple[Optional[str], Optional[str]]:
        if not urls:
            return None, None
        vertical, horizontal = [], []
        for u in urls:
            low = u.lower()
            if any(v in low for v in ["vertical", "poster", "vthumb"]):
                vertical.append(u)
            elif any(h in low for h in ["horizontal", "fanart", "landscape"]):
                horizontal.append(u)
            else:
                horizontal.append(u)
        if vertical and horizontal:
            return vertical[0], horizontal[0]
        if vertical:
            return vertical[0], vertical[0]
        if horizontal:
            return horizontal[0], horizontal[0]
        return None, None

    @staticmethod
    def _get_ext(url: str, default: str = ".jpg") -> str:
        m = re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", url, re.I)
        return f".{m.group(1)}" if m else default

    def _make_headers(self, **kw) -> dict:
        h = {"User-Agent": self.config.user_agent}
        h.update(kw)
        return h
