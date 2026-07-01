"""Headless browser for ACFAN cover image decryption.

ACFAN's CDN encrypts cover images with AES-256-GCM. The Vue frontend
decrypts them client-side via WebCrypto. The only way to get decrypted
covers is to render the video page in a real browser.

This module uses Playwright to:
1. Navigate to the video page
2. Wait for the cover <img> to load (decrypted by Vue)
3. Extract the image URL or take a screenshot
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("headless")


class HeadlessBrowser:
    """Manages a singleton Playwright browser instance for cover extraction."""

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            logger.info("Headless browser started")

    async def _get_page(self):
        await self._ensure_browser()
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
        return self._page

    async def extract_cover_url(self, page_url: str) -> Optional[str]:
        """Navigate to a video page and extract the rendered cover image URL.

        The Vue frontend decrypts the cover on-the-fly. We wait for the
        <img> tag to appear with a valid src (data: or blob: URL).
        """
        async with self._lock:
            try:
                page = await self._get_page()
                await page.goto(page_url, wait_until="networkidle", timeout=30000)

                # Wait for cover image to load (Vue renders it after decrypt)
                # The cover is typically inside a .poster or .cover element
                await page.wait_for_selector(
                    'img[src*="dcjrsb"], img[src*="xszc666"], img[src^="data:"], img[src^="blob:"]',
                    timeout=15000,
                )

                # Extract the image data
                # Strategy 1: Try to get the image src as a data URL
                src = await page.evaluate("""() => {
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        if (img.complete && img.naturalWidth > 100) {
                            if (img.src.startsWith('data:') || img.src.startsWith('blob:')) {
                                return img.src;
                            }
                            // If it's a regular URL, the browser already decrypted it.
                            // Return the URL - we'll download it through the browser.
                            if (img.src.includes('dcjrsb') || img.src.includes('xszc666') || img.src.includes('jhimage')) {
                                return img.src;
                            }
                        }
                    }
                    return null;
                }""")

                if not src:
                    return None

                # If the src is a standard URL (not data/blob), the browser's
                # rendering pipeline has decrypted it. We can screenshot the element
                # or try to get the data via canvas.
                if src and (src.startswith("data:") or src.startswith("blob:")):
                    return src

                # For regular URLs: take a screenshot of the image element
                # This captures the browser-rendered (decrypted) image
                cover_data = await page.evaluate("""async () => {
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        if (img.complete && img.naturalWidth > 100) {
                            const canvas = document.createElement('canvas');
                            canvas.width = img.naturalWidth;
                            canvas.height = img.naturalHeight;
                            const ctx = canvas.getContext('2d');
                            ctx.drawImage(img, 0, 0);
                            return canvas.toDataURL('image/jpeg', 0.9);
                        }
                    }
                    return null;
                }""")

                return cover_data

            except Exception as e:
                logger.warning(f"Failed to extract cover from {page_url}: {e}")
                return None

    async def screenshot_cover(self, page_url: str, output_path: Path) -> bool:
        """Render the page and save the cover image.

        ACFAN uses Vue + Vant lazy-loading. The cover images are rendered
        as canvas-drawn or base64 inline images after JS decryption.
        We wait for any reasonably-sized image to appear, then capture via canvas.
        """
        async with self._lock:
            try:
                page = await self._get_page()
                await page.goto(page_url, wait_until="networkidle", timeout=30000)

                # ACFAN lazy-loads images via van-image component.
                # Wait for the page to render, then find loaded covers.
                await asyncio.sleep(3)

                # Loop: wait for images to load, try to extract
                for attempt in range(5):
                    img_data = await page.evaluate("""() => {
                        const imgs = document.querySelectorAll('img');
                        for (const img of imgs) {
                            if (img.complete && img.naturalWidth > 300) {
                                try {
                                    const canvas = document.createElement('canvas');
                                    canvas.width = img.naturalWidth;
                                    canvas.height = img.naturalHeight;
                                    const ctx = canvas.getContext('2d');
                                    ctx.drawImage(img, 0, 0);
                                    return canvas.toDataURL('image/jpeg', 0.92);
                                } catch(e) { continue; }
                            }
                        }
                        return null;
                    }""")

                    if img_data and img_data.startswith("data:image/jpeg;base64,"):
                        import base64
                        b64 = img_data.split(",", 1)[1]
                        with open(output_path, "wb") as f:
                            f.write(base64.b64decode(b64))
                        logger.info(f"Cover extracted via headless: {output_path}")
                        return True

                    # Not loaded yet, wait and retry
                    await asyncio.sleep(2)

                return False

            except Exception as e:
                logger.warning(f"Cover extraction failed for {page_url}: {e}")
                # Screenshot for debugging
                try:
                    await page.screenshot(path=str(output_path.with_suffix('.debug.png')))
                except:
                    pass
                return False

    async def close(self):
        if self._page and not self._page.is_closed():
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_pw"):
            await self._pw.stop()
        self._browser = None
        self._page = None
        logger.info("Headless browser stopped")


# Singleton
_browser_instance: Optional[HeadlessBrowser] = None


async def get_browser() -> HeadlessBrowser:
    global _browser_instance
    if _browser_instance is None:
        _browser_instance = HeadlessBrowser()
    return _browser_instance


async def close_browser():
    global _browser_instance
    if _browser_instance:
        await _browser_instance.close()
        _browser_instance = None
