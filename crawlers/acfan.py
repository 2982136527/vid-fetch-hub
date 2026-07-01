"""ACFAN crawler.

ACFAN uses dynamic domains (三级跳转), has proper JSON API.
Has coverImg[] (horizontal) and verticalImg[] (vertical) covers.
Cover CDN uses AES-256-GCM encryption → headless browser needed for decode.
STRM/proxy mode — no video downloads.

Domain resolution: 521.acfan.com → level2 → level3 (API server)
"""

import json
import re
from typing import AsyncGenerator

from crawlers import BaseCrawler, VideoInfo


class AcfanCrawler(BaseCrawler):
    def __init__(self, config, db, downloader):
        super().__init__(config, db, downloader)
        self._api_base = None
        self._level2_base = None
        self._img_domain = None
        self._token = None
        self._user_id = None

    @property
    def name(self) -> str:
        return "acfan"

    async def _resolve_domain(self) -> str:
        """Resolve the dynamic API domain.

        Flow: 521.acfan.com → level2 (SPA 导航页)
              → level2 main JS chunk → find lazy chunks
              → check each chunk until we find level3 domain (API server)
        """
        if self._api_base:
            return self._api_base

        permanent_page = self.site_config.get(
            "permanent_page", "https://521.acfan.com/"
        )
        self.logger.info(f"Resolving ACFAN domain from {permanent_page}")

        # Level 1 → Level 2
        resp_text = await self.dl.get_text(permanent_page)
        m = re.search(r"document\.location\s*=\s*'([^']+)'", resp_text)
        if not m:
            raise RuntimeError("Cannot find level2 domain from permanent page")
        level2 = m.group(1).rstrip("/")
        self.logger.info(f"Level2: {level2}")

        # Level 2 → find main JS chunk
        resp2 = await self.dl.get_text(f"{level2}/")
        main_hash = re.search(r'"assets/index\.([a-f0-9]+)\.js"', resp2)
        if not main_hash:
            main_hash = re.search(r'assets/index\.([a-f0-9]+)\.js', resp2)
        if not main_hash:
            raise RuntimeError("Cannot find main JS chunk in level2 page")

        # Load main chunk, find all lazy-imported chunks
        js_url = f"{level2}/assets/index.{main_hash.group(1)}.js"
        self.logger.info(f"Loading main chunk: {js_url}")
        js_text = await self.dl.get_text(js_url)

        chunk_hashes = set(re.findall(r'\.\/index\.([a-f0-9]+)\.js', js_text))
        chunk_hashes.add(main_hash.group(1))

        # Scan each chunk for level3 domain
        found_api = None
        for h in chunk_hashes:
            try:
                chunk_url = f"{level2}/assets/index.{h}.js"
                chunk_text = await self.dl.get_text(chunk_url)
                dm = re.search(r"(https?://[a-zA-Z0-9]+\.dtnuaic6\.work)", chunk_text)
                if dm:
                    found_api = dm.group(1)
                    self._api_base = found_api
                    self._level2_base = level2
                    self.logger.info(f"API base (level3): {self._api_base}")
                    return self._api_base
            except Exception:
                continue

        raise RuntimeError("Cannot find level3 domain in any JS chunk")
        self._level2_base = level2
        self.logger.info(f"API base (level3): {self._api_base}")
        return self._api_base

    async def _get_img_domain(self) -> str:
        """Get image CDN domain."""
        if self._img_domain:
            return self._img_domain
        api_base = await self._resolve_domain()
        try:
            data = await self.dl.get_text(
                f"{api_base}/api/sys/public/imageCdnDomain"
            )
            result = json.loads(data)
            if result.get("code") == 200:
                self._img_domain = result["data"]
        except Exception:
            pass
        if not self._img_domain:
            self._img_domain = ""
        return self._img_domain

    async def _login(self):
        """Login to ACFAN if credentials provided."""
        username = self.site_config.get("login", {}).get("username", "")
        password = self.site_config.get("login", {}).get("password", "")
        if not username or not password:
            return

        api_base = await self._resolve_domain()
        try:
            result = await self.dl.post_json(
                f"{api_base}/api/user/v1/public/account/login",
                json_data={"account": username, "password": password},
            )
            if result.get("code") == 200:
                self._token = result["data"]["token"]
                self._user_id = result["data"].get("userId")
                if not self._img_domain:
                    self._img_domain = result["data"].get("imgDomain", "")
                self.logger.info("ACFAN login successful")
            else:
                self.logger.warning(f"ACFAN login failed: {result}")
        except Exception as e:
            self.logger.warning(f"ACFAN login error: {e}")

    async def _get_video_detail(self, video_id: int) -> dict | None:
        """Get video details from ACFAN API."""
        api_base = await self._resolve_domain()
        try:
            data = await self.dl.get_text(
                f"{api_base}/api/video/public/getVideoById?videoId={video_id}"
            )
            result = json.loads(data)
            if result.get("code") == 200:
                return result["data"]
        except Exception as e:
            self.logger.warning(f"Failed to get video {video_id}: {e}")
        return None

    async def _get_category_videos(
        self, classify_title: str, page: int = 1
    ) -> list[dict]:
        """Get videos by category."""
        api_base = await self._resolve_domain()
        try:
            data = await self.dl.get_text(
                f"{api_base}/api/video/public/newGetByClassify?page={page}&pageSize=40&sortType=0&classifyTitle={classify_title}"
            )
            result = json.loads(data)
            if result.get("code") == 200:
                d = result.get("data", {})
                # API returns data under "data" key
                return d.get("data", []) or d.get("list", [])
        except Exception as e:
            self.logger.warning(
                f"Failed to get category {classify_title} page {page}: {e}"
            )
        return []

    async def _video_info_from_api(
        self, vid_data: dict
    ) -> VideoInfo | None:
        """Convert API response to VideoInfo."""
        video_id = vid_data.get("videoId")
        if not video_id:
            return None

        detail = await self._get_video_detail(video_id)
        if not detail:
            return None

        title = detail.get("title", "") or detail.get("subtitle", "")

        # Cover URLs
        cover_urls = []
        img_domain = await self._get_img_domain()

        # Horizontal covers
        for cover in detail.get("coverImg", []):
            if img_domain and not cover.startswith("http"):
                cover_urls.append(f"{img_domain}{cover}")
            else:
                cover_urls.append(cover)

        # Vertical covers
        for vcover in detail.get("verticalImg", []):
            if img_domain and not vcover.startswith("http"):
                cover_urls.append(f"{img_domain}{vcover}")
            else:
                cover_urls.append(vcover)

        # Tags
        tags = detail.get("tagTitles", [])

        # Duration
        duration = detail.get("playTime", 0)

        # Year from createdAt
        created = detail.get("createdAt", "")
        year = created[:4] if created else ""

        return VideoInfo(
            source=self.name,
            video_id=str(video_id),
            title=title or f"acfan_{video_id}",
            page_url=f"{self._api_base}/play/{video_id}" if self._api_base else "",
            cover_urls=cover_urls,
            description=detail.get("subtitle", ""),
            tags=tags,
            duration_seconds=duration,
            year=year,
        )

    async def get_latest_videos(self) -> AsyncGenerator[VideoInfo, None]:
        await self._resolve_domain()
        await self._login()
        await self._get_img_domain()

        classify_titles = self.site_config.get("classify_titles", [])
        if not classify_titles:
            classify_titles = [
                "动漫", "视频", "国产", "日本", "欧美"
            ]

        for ct in classify_titles:
            page = 1
            while True:
                videos = await self._get_category_videos(ct, page)
                if not videos:
                    break
                for v in videos:
                    video = await self._video_info_from_api(v)
                    if video:
                        yield video
                page += 1
                max_pages = self.site_config.get("max_pages", 0)
                if max_pages and page > max_pages:
                    break

    async def get_all_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for video in self.get_latest_videos():
            yield video
