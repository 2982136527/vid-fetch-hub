"""18mh (禁漫天堂) crawler - Video section only.

Scrapes video list from /mv/all, video details from /mv/detail/{id}.
STRM/proxy mode — no video downloads.
Covers have auth_key signatures.
"""

import json
import re
from typing import AsyncGenerator

from crawlers import BaseCrawler, VideoInfo


class EighteenMHCrawler(BaseCrawler):
    @property
    def name(self) -> str:
        return "18mh"

    async def _parse_video_detail(self, video_url: str, video_id: str) -> VideoInfo | None:
        """Parse video detail page for _detail_ variable."""
        try:
            html = await self.dl.get_text(video_url)

            # Extract _detail_ JS variable
            detail_m = re.search(r"const _detail_\s*=\s*(\{.*?\});", html, re.DOTALL)
            if not detail_m:
                return None

            detail_str = detail_m.group(1)
            # Clean up JS escaping
            detail_str = detail_str.replace("\\/", "/")
            detail = json.loads(detail_str)

            title = detail.get("title", "")
            summary = detail.get("summary", "")
            cover = detail.get("cover", "")
            duration = detail.get("duration", 0)
            view_num = detail.get("view_num", "0")
            category = detail.get("category_Str", "")
            created_at = detail.get("created_at", "")
            year = created_at[:4] if created_at else ""

            # Extract tags from _detail_["tag"] array
            tags = []
            raw_tags = detail.get("tag", [])
            if isinstance(raw_tags, list):
                for t in raw_tags:
                    if isinstance(t, dict) and t.get("title"):
                        tags.append(t["title"])

            # Transform cover URL: pic.nhoqpp.cn encrypts, use expose.eisees.com instead
            cover_urls = []
            if cover:
                path_m = __import__("re").search(r'pic\.nhoqpp\.cn(/[^?]+)', cover)
                cover_urls.append(f"https://expose.eisees.com{path_m.group(1)}" if path_m else cover)

            return VideoInfo(
                source=self.name,
                video_id=video_id,
                title=title or f"18mh_{video_id}",
                page_url=video_url,
                cover_urls=cover_urls,
                description=summary or "",
                tags=tags,
                categories=[category] if category else [],
                duration_seconds=duration,
                year=year,
            )
        except Exception as e:
            self.logger.error(f"Error parsing video detail {video_url}: {e}")
            return None

    async def _scrape_video_list_page(self, url: str) -> list[tuple[str, str]]:
        """Scrape video list page, return list of (video_url, video_id)."""
        try:
            html = await self.dl.get_text(url)
            results = []
            # Find video links in the list
            for m in re.finditer(
                r'href="(/mv/detail/(\d+))"', html
            ):
                path, vid = m.groups()
                results.append((f"https://18mh.net{path}", vid))
            return list(set(results))
        except Exception as e:
            self.logger.error(f"Error scraping list {url}: {e}")
            return []

    async def _paginate_videos(
        self, category: str = ""
    ) -> AsyncGenerator[tuple[str, str], None]:
        """Iterate through paginated video lists."""
        page = 1
        base_path = f"/mv/all/{category}" if category else "/mv/all"
        max_pages = self.site_config.get("max_pages", 0)

        while True:
            if page == 1:
                url = f"https://18mh.net{base_path}"
            else:
                url = f"https://18mh.net{base_path}/page/{page}"

            videos = await self._scrape_video_list_page(url)
            if not videos:
                break

            for vu, vi in videos:
                yield vu, vi

            page += 1
            if max_pages and page > max_pages:
                break

    async def get_latest_videos(self) -> AsyncGenerator[VideoInfo, None]:
        categories = self.site_config.get("categories", [])
        cats_to_scrape = categories if categories else [""]  # "" = all

        for cat in cats_to_scrape:
            async for vu, vi in self._paginate_videos(cat):
                video = await self._parse_video_detail(vu, vi)
                if video:
                    yield video

    async def get_all_videos(self) -> AsyncGenerator[VideoInfo, None]:
        async for video in self.get_latest_videos():
            yield video
