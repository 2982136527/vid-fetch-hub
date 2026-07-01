"""RedTube crawler.

Scrapes video list from /newest, /hot, etc.
STRM/proxy mode — no video downloads.
"""

import re
from typing import AsyncGenerator

from crawlers import BaseCrawler, VideoInfo


class RedTubeCrawler(BaseCrawler):
    @property
    def name(self) -> str:
        return "redtube"

    async def _parse_video_page(self, video_url: str) -> VideoInfo | None:
        """Parse a single video page."""
        try:
            html = await self.dl.get_text(video_url)

            # Title
            title_m = re.search(r'<title>([^<]+)', html)
            title = title_m.group(1).strip() if title_m else ""

            # Video ID (from URL path)
            vid_m = re.search(r"/(\d+)(?:\?|$)", video_url)
            video_id = vid_m.group(1) if vid_m else ""

            # Cover/screenshot path template (from data-path, not data-mediabook)
            cover_urls = []
            path_m = re.search(r'data-path="([^"]+)"', html)
            if path_m:
                path_template = path_m.group(1)
                cover_urls.append(path_template.replace("{index}", "0"))

            # Description
            desc_m = re.search(
                r'<meta\s+name="description"\s+content="([^"]+)"', html, re.I
            )
            description = desc_m.group(1) if desc_m else ""

            # Tags
            tags = re.findall(
                r'<a[^>]*class="[^"]*video-tag[^"]*"[^>]*>([^<]+)</a>', html
            )

            # Duration
            dur_m = re.search(r'"duration":\s*(\d+)', html)
            duration = int(dur_m.group(1)) if dur_m else 0

            return VideoInfo(
                source=self.name,
                video_id=video_id,
                title=title or f"redtube_{video_id}",
                page_url=video_url,
                cover_urls=cover_urls,
                description=description,
                tags=tags,
                duration_seconds=duration,
            )
        except Exception as e:
            self.logger.error(f"Error parsing {video_url}: {e}")
            return None

    async def _scrape_list_page(self, url: str) -> list[str]:
        """Scrape list page for video URLs."""
        try:
            html = await self.dl.get_text(url)
            # RedTube uses /{videoId} format
            urls = re.findall(r'href="(/\d+)"', html)
            base = "https://www.redtube.com"
            return list(set(f"{base}{u}" for u in urls))
        except Exception as e:
            self.logger.error(f"Error scraping {url}: {e}")
            return []

    async def _get_video_urls_from_pages(self, start_url: str, param: str = "page") -> AsyncGenerator[str, None]:
        """Iterate paginated list pages."""
        page = 1
        url = start_url
        while True:
            video_urls = await self._scrape_list_page(url)
            if not video_urls:
                break
            for vu in video_urls:
                yield vu
            page += 1
            max_pages = self.site_config.get("max_pages", 0)
            if max_pages and page > max_pages:
                break
            url = f"{start_url}?{param}={page}"

    async def get_latest_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for vu in self._get_video_urls_from_pages("https://www.redtube.com/newest"):
            video = await self._parse_video_page(vu)
            if video:
                yield video

    async def get_all_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for video in self.get_latest_videos():
            yield video
