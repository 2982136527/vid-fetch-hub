"""XVideos crawler.

Scrapes video list from /new/{page}, gets video metadata
from the video page. STRM/proxy mode — no video downloads.
"""

import re
from typing import AsyncGenerator

from crawlers import BaseCrawler, VideoInfo


class XVideosCrawler(BaseCrawler):
    @property
    def name(self) -> str:
        return "xvideos"

    async def _parse_video_page(self, video_url: str) -> VideoInfo | None:
        """Parse a single video page for metadata."""
        try:
            html = await self.dl.get_text(video_url)

            # Extract title
            title_m = re.search(r'<title>([^<]+)', html)
            title = title_m.group(1).strip() if title_m else ""

            # Extract video ID
            vid_m = re.search(r'/video\.([a-z0-9]+)/', video_url)
            video_id = vid_m.group(1) if vid_m else ""

            # Extract description
            desc_m = re.search(
                r'<meta\s+name="description"\s+content="([^"]+)"', html, re.I
            )
            description = desc_m.group(1) if desc_m else ""

            # Extract tags
            tags = re.findall(
                r'<a[^>]*href="/tags/[^"]*"[^>]*class="[^"]*is-keyword[^"]*"[^>]*>([^<]+)',
                html,
            )

            # Extract duration
            duration_m = re.search(r"<span class=\"duration\">(\d+)</span>", html)
            duration = int(duration_m.group(1)) if duration_m else 0

            # Extract thumbnail/uuid for covers - use og:image tag
            cover_urls = []
            og_m = re.search(
                r'<meta\s+property="og:image"\s+content="([^"]+)"', html, re.I
            )
            if og_m:
                cover_urls.append(og_m.group(1))
            else:
                # Fallback: mozaique_full
                thumb_m = re.search(
                    r'(https://thumb-cdn77\.xvideos-cdn\.com/[^/]+/\d+/mozaique_full\.jpg)',
                    html,
                )
                if thumb_m:
                    cover_urls.append(thumb_m.group(1))

            return VideoInfo(
                source=self.name,
                video_id=video_id or str(hash(video_url)),
                title=title or f"xvideos_{video_id}",
                page_url=video_url,
                cover_urls=cover_urls,
                description=description,
                tags=tags,
                duration_seconds=duration * 60,  # duration is in minutes
            )
        except Exception as e:
            self.logger.error(f"Error parsing video page {video_url}: {e}")
            return None

    async def _scrape_list_page(self, url: str) -> list[str]:
        """Scrape a list page and return video URLs."""
        try:
            html = await self.dl.get_text(url)
            # Extract video links from list
            urls = re.findall(
                r'href="(/video\.[a-z0-9]+/[^"]*)"', html
            )
            return list(set(f"https://www.xvideos.com{u}" for u in urls))
        except Exception as e:
            self.logger.error(f"Error scraping list page {url}: {e}")
            return []

    async def get_latest_videos(self) -> AsyncGenerator[VideoInfo, None]:
        page = 1
        while True:
            url = f"https://www.xvideos.com/new/{page}"
            video_urls = await self._scrape_list_page(url)
            if not video_urls:
                break
            for vu in video_urls:
                video = await self._parse_video_page(vu)
                if video:
                    yield video
            page += 1
            max_pages = self.site_config.get("max_pages", 0)
            if max_pages and page > max_pages:
                break

    async def get_all_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for video in self.get_latest_videos():
            yield video
