"""HTTP downloader with retry, rate limiting, and progress tracking."""

import asyncio
import time
import random
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

import aiohttp
import aiofiles


class Downloader:
    """Handles HTTP requests and file downloads with rate limiting."""

    def __init__(
        self,
        user_agent: str = "",
        timeout: int = 30,
        retry_max: int = 3,
        retry_delay: int = 5,
        rate_limit: tuple[float, float] = (1.0, 3.0),
        max_concurrent: int = 3,
        http_proxy: str = "",
        https_proxy: str = "",
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.retry_max = retry_max
        self.retry_delay = retry_delay
        self.rate_min, self.rate_max = rate_limit
        self.max_concurrent = max_concurrent
        self.http_proxy = http_proxy
        self.https_proxy = https_proxy or http_proxy
        self._last_request = 0.0
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._session: Optional[aiohttp.ClientSession] = None
        self.on_progress: Optional[Callable[[str, int, int], None]] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"User-Agent": self.user_agent}
            timeout_obj = aiohttp.ClientTimeout(total=self.timeout)
            kwargs = {"headers": headers, "timeout": timeout_obj}
            if self.http_proxy:
                kwargs["proxy"] = self.http_proxy
            self._session = aiohttp.ClientSession(**kwargs)
        return self._session

    async def _rate_limit(self):
        """Wait to respect rate limits."""
        elapsed = time.time() - self._last_request
        delay = random.uniform(self.rate_min, self.rate_max)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request = time.time()

    async def get_text(
        self, url: str, headers: Optional[dict] = None, cookies: Optional[dict] = None
    ) -> str:
        """GET a URL and return text content with retry logic."""
        last_exc = None
        for attempt in range(self.retry_max + 1):
            try:
                await self._rate_limit()
                session = await self._get_session()
                async with session.get(
                    url, headers=headers, cookies=cookies, ssl=False
                ) as resp:
                    resp.raise_for_status()
                    return await resp.text()
            except Exception as e:
                last_exc = e
                if attempt < self.retry_max:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
        raise last_exc  # type: ignore

    async def get_json(
        self, url: str, headers: Optional[dict] = None, cookies: Optional[dict] = None
    ) -> dict:
        """GET a URL and return JSON."""
        text = await self.get_text(url, headers=headers, cookies=cookies)
        import json

        return json.loads(text)

    async def post_json(
        self,
        url: str,
        data: Optional[dict] = None,
        json_data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        """POST to a URL and return JSON."""
        last_exc = None
        for attempt in range(self.retry_max + 1):
            try:
                await self._rate_limit()
                session = await self._get_session()
                async with session.post(
                    url, data=data, json=json_data, headers=headers, ssl=False
                ) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            except Exception as e:
                last_exc = e
                if attempt < self.retry_max:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
        raise last_exc  # type: ignore

    async def download_file(
        self,
        url: str,
        dest: str | Path,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        label: str = "",
    ) -> Path:
        """Download a file with progress tracking."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_exc = None

        for attempt in range(self.retry_max + 1):
            try:
                await self._rate_limit()
                session = await self._get_session()
                async with session.get(
                    url, headers=headers, cookies=cookies, ssl=False
                ) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    async with aiofiles.open(dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            if self.on_progress and total > 0:
                                self.on_progress(label or str(dest.name), downloaded, total)
                    return dest
            except Exception as e:
                last_exc = e
                if attempt < self.retry_max:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                # Clean up partial download
                if dest.exists():
                    dest.unlink()

        raise last_exc  # type: ignore

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
