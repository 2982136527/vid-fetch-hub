"""Madou (麻豆社) crawler.

WordPress-based site. STRM/proxy mode — no video downloads.
Covers: poster.jpg from the video CDN.
"""

import re
from typing import AsyncGenerator
from urllib.parse import unquote

from crawlers import BaseCrawler, VideoInfo


class MadouCrawler(BaseCrawler):
    @property
    def name(self) -> str:
        return "madou"

    async def _parse_list_page(self, url: str) -> list[dict]:
        """Parse a list page for article metadata."""
        try:
            html = await self.dl.get_text(url)
            articles = []

            # Extract article cards
            pattern = (
                r'class="excerpt[^"]*".*?'
                r'href="(https://madou\.club/[^"]+\.html)"[^>]*>.*?'
                r'data-src="([^"]+)".*?'
                r'<h2><a[^>]*href="[^"]*"[^>]*>([^<]+)</a></h2>'
            )
            for m in re.finditer(pattern, html, re.DOTALL):
                article_url, thumb, title = m.groups()

                # Extract post ID
                pid_m = re.search(rf'href="{re.escape(article_url)}".*?data-pid="(\d+)"', html, re.DOTALL)
                post_id = pid_m.group(1) if pid_m else ""

                # Extract category
                cat_m = re.search(
                    rf'data-pid="{post_id}".*?category/([^"]+)"', html, re.DOTALL
                )
                category = unquote(cat_m.group(1)) if cat_m else ""

                articles.append({
                    "url": article_url,
                    "title": title.strip(),
                    "post_id": post_id,
                    "thumbnail": thumb,
                    "category": category,
                })
            return articles
        except Exception as e:
            self.logger.error(f"Error scraping list page {url}: {e}")
            return []

    async def _parse_article_page(self, url: str) -> VideoInfo | None:
        """Parse a single article page for video details."""
        try:
            html = await self.dl.get_text(url)

            # Title
            title_m = re.search(r'class="article-title">([^<]+)', html)
            title = title_m.group(1).strip() if title_m else ""

            # Post ID
            pid_m = re.search(r"postid-(\d+)", html)
            post_id = pid_m.group(1) if pid_m else ""

            # Extract share_id from iframe (used for cover poster.jpg)
            iframe_m = re.search(
                r'src=https://dash\.madou\.club/share/([a-f0-9]+)', html
            )
            share_id = iframe_m.group(1) if iframe_m else ""

            # Tags
            tags = re.findall(r'rel="tag">([^<]+)', html)

            # Categories
            cats = re.findall(r'rel="category tag">([^<]+)', html)

            # Cover images (poster.jpg from CDN, plus thumbnail from list page)
            cover_urls = []

            if share_id:
                cover_urls.append(
                    f"https://dash.madou.club/videos/{share_id}/poster.jpg"
                )

            # Also try to get thumbnail from the article page
            thumb_m = re.search(
                r'data-src="(https://madou\.club/covers/[^"]+)"', html
            )
            if thumb_m and thumb_m.group(1) not in cover_urls:
                cover_urls.append(thumb_m.group(1))

            return VideoInfo(
                source=self.name,
                video_id=post_id or share_id,
                title=title or f"madou_{post_id}",
                page_url=url,
                cover_urls=cover_urls,
                description="",
                tags=tags,
                categories=cats,
            )
        except Exception as e:
            self.logger.error(f"Error parsing article {url}: {e}")
            return None

    async def get_latest_videos(self) -> AsyncGenerator[VideoInfo, None]:
        """Madou: scrape RSS feed for latest, and paginate for full catalog."""
        # First try RSS for latest
        try:
            rss = await self.dl.get_text("https://madou.club/feed")
            urls = re.findall(
                r"<link>https://madou\.club/[^<]+\.html</link>", rss
            )
            for url_match in urls:
                url = url_match.replace("<link>", "").replace("</link>", "")
                video = await self._parse_article_page(url)
                if video:
                    yield video
        except Exception as e:
            self.logger.warning(f"RSS failed, falling back to page scrape: {e}")

    async def get_all_videos(self) -> AsyncGenerator[VideoInfo, None]:
        """Scrape all 471 pages of madou for complete catalog."""
        page = 1
        max_pages_setting = self.site_config.get("max_pages", 0)
        max_page = 471  # From docs

        while page <= max_page:
            url = (
                f"https://madou.club/page/{page}"
                if page > 1
                else "https://madou.club/"
            )
            articles = await self._parse_list_page(url)
            if not articles:
                break

            for article in articles:
                video = await self._parse_article_page(article["url"])
                if video:
                    yield video

            page += 1
            if max_pages_setting and page > max_pages_setting:
                break

            # Save crawl state periodically
            self.db.save_crawl_state(self.name, last_page=page)
