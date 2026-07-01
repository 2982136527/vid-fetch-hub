"""YouPorn crawler.

Scrapes video list from pages like /most_viewed/, /newest/, etc.
STRM/proxy mode — no video downloads.
"""

import re
from typing import AsyncGenerator
from urllib.parse import urljoin

from crawlers import BaseCrawler, VideoInfo


class YouPornCrawler(BaseCrawler):
    @property
    def name(self) -> str:
        return "youporn"

    async def _parse_video_page(self, video_url: str) -> VideoInfo | None:
        """Parse a single watch page."""
        try:
            html = await self.dl.get_text(video_url)

            # Title
            title_m = re.search(r'<title>([^<]+)', html)
            title = title_m.group(1).strip() if title_m else ""

            # Video ID
            vid_m = re.search(r'/watch/(\d+)/', video_url)
            video_id = vid_m.group(1) if vid_m else ""

            # Poster/cover (from data-poster, not data-mediabook)
            cover_urls = []
            poster_m = re.search(r'data-poster="([^"]+)"', html)
            if poster_m:
                cover_urls.append(poster_m.group(1))

            # Description
            desc_m = re.search(
                r'<meta\s+name="description"\s+content="([^"]+)"', html, re.I
            )
            description = desc_m.group(1) if desc_m else ""

            # Tags
            tags = re.findall(
                r'<a[^>]*href="/porntags/([^"]+)"[^>]*>([^<]+)</a>', html
            )
            tag_names = [t[1] for t in tags]

            # Duration
            dur_m = re.search(r'"duration":\s*(\d+)', html)
            duration = int(dur_m.group(1)) if dur_m else 0

            # Rating
            rating_m = re.search(r'"rating":\s*([\d.]+)', html)
            rating = float(rating_m.group(1)) if rating_m else 0.0

            return VideoInfo(
                source=self.name,
                video_id=video_id,
                title=title or f"youporn_{video_id}",
                page_url=video_url,
                cover_urls=cover_urls,
                description=description,
                tags=tag_names,
                duration_seconds=duration,
                rating=rating,
            )
        except Exception as e:
            self.logger.error(f"Error parsing {video_url}: {e}")
            return None

    async def _scrape_list_page(self, url: str) -> list[str]:
        """Scrape a list page for video URLs."""
        try:
            html = await self.dl.get_text(url)
            urls = re.findall(r'href="(/watch/\d+/)"', html)
            base = "https://www.youporn.com"
            return list(set(f"{base}{u}" for u in urls))
        except Exception as e:
            self.logger.error(f"Error scraping {url}: {e}")
            return []

    async def _get_video_urls_from_pages(self, start_url: str) -> AsyncGenerator[str, None]:
        """Iterate through paginated list pages."""
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
            # Check for next page
            try:
                html = await self.dl.get_text(url)
                next_m = re.search(r'href="([^"]*page=?\d+[^"]*)"[^>]*>Next', html, re.I)
                if next_m:
                    url = urljoin(start_url, next_m.group(1))
                else:
                    break
            except:
                break

    async def get_latest_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for vu in self._get_video_urls_from_pages("https://www.youporn.com/most_viewed/"):
            video = await self._parse_video_page(vu)
            if video:
                yield video

    async def get_all_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for vu in self._get_video_urls_from_pages("https://www.youporn.com/most_viewed/"):
            video = await self._parse_video_page(vu)
            if video:
                yield video
