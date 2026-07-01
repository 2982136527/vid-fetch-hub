"""Tube8 crawler.

Scrapes video list from /newest.html, etc.
STRM/proxy mode — no video downloads.
"""

import re
from typing import AsyncGenerator

from crawlers import BaseCrawler, VideoInfo


class Tube8Crawler(BaseCrawler):
    @property
    def name(self) -> str:
        return "tube8"

    async def _parse_video_page(self, video_url: str) -> VideoInfo | None:
        try:
            html = await self.dl.get_text(video_url)

            title_m = re.search(r'<title>([^<]+)', html)
            title = title_m.group(1).strip() if title_m else ""

            vid_m = re.search(r"/porn-video/(\d+)/", video_url)
            video_id = vid_m.group(1) if vid_m else ""

            cover_urls = []
            poster_m = re.search(r'data-poster="([^"]+)"', html)
            if poster_m:
                cover_urls.append(poster_m.group(1))

            desc_m = re.search(
                r'<meta\s+name="description"\s+content="([^"]+)"', html, re.I
            )
            description = desc_m.group(1) if desc_m else ""

            tags = re.findall(
                r'<a[^>]*href="/porntags/([^"]+)"[^>]*>([^<]+)</a>', html
            )
            tag_names = [t[1] for t in tags]

            dur_m = re.search(r'"duration":\s*(\d+)', html)
            duration = int(dur_m.group(1)) if dur_m else 0

            return VideoInfo(
                source=self.name,
                video_id=video_id,
                title=title or f"tube8_{video_id}",
                page_url=video_url,
                cover_urls=cover_urls,
                description=description,
                tags=tag_names,
                duration_seconds=duration,
            )
        except Exception as e:
            self.logger.error(f"Error parsing {video_url}: {e}")
            return None

    async def _scrape_list_page(self, url: str) -> list[str]:
        try:
            html = await self.dl.get_text(url)
            urls = re.findall(r'href="(/porn-video/\d+/)"', html)
            base = "https://www.tube8.com"
            return list(set(f"{base}{u}" for u in urls))
        except Exception as e:
            self.logger.error(f"Error scraping {url}: {e}")
            return []

    async def _paginate(self, start_url: str) -> AsyncGenerator[str, None]:
        page = 1
        url = start_url
        while True:
            vids = await self._scrape_list_page(url)
            if not vids:
                break
            for v in vids:
                yield v
            page += 1
            max_pages = self.site_config.get("max_pages", 0)
            if max_pages and page > max_pages:
                break
            sep = "&" if "?" in start_url else "?"
            url = f"{start_url}{sep}page={page}"

    async def get_latest_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for vu in self._paginate("https://www.tube8.com/newest.html"):
            video = await self._parse_video_page(vu)
            if video:
                yield video

    async def get_all_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for video in self.get_latest_videos():
            yield video
