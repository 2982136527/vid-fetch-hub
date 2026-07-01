"""Main scheduler/daemon that runs continuously and coordinates all crawlers."""

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from core import Config
from core.database import Database
from core.downloader import Downloader

logger = logging.getLogger("scheduler")


class Scheduler:
    """Orchestrates crawling across all enabled sites."""

    def __init__(self, config: Config):
        self.config = config
        self._running = False
        self._crawlers: list = []
        self._db: Optional[Database] = None
        self._dl: Optional[Downloader] = None

    def _setup_logging(self):
        level = logging.INFO
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        logging.basicConfig(level=level, format=fmt, stream=sys.stdout)

    def _init_db(self):
        db_dir = self.config.output_dir / ".db"
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db = Database(db_dir / "crawl_state.sqlite")

    def _init_downloader(self):
        self._dl = Downloader(
            user_agent=self.config.user_agent,
            timeout=self.config.timeout,
            retry_max=self.config.retry_max,
            retry_delay=self.config.retry_delay,
            rate_limit=self.config.rate_limit,
            max_concurrent=self.config.max_concurrent_downloads,
        )

    def _init_crawlers(self):
        """Initialize all enabled site crawlers."""
        from crawlers.xvideos import XVideosCrawler
        from crawlers.youporn import YouPornCrawler
        from crawlers.redtube import RedTubeCrawler
        from crawlers.thumbzilla import ThumbzillaCrawler
        from crawlers.acfan import AcfanCrawler
        from crawlers.pornhub import PornHubCrawler
        from crawlers.tube8 import Tube8Crawler
        from crawlers.madou import MadouCrawler
        from crawlers.eighteen_mh import EighteenMHCrawler

        crawler_map = {
            "xvideos": XVideosCrawler,
            "youporn": YouPornCrawler,
            "redtube": RedTubeCrawler,
            "thumbzilla": ThumbzillaCrawler,
            "acfan": AcfanCrawler,
            "pornhub": PornHubCrawler,
            "tube8": Tube8Crawler,
            "madou": MadouCrawler,
            "eighteen_mh": EighteenMHCrawler,
        }

        for name, cls in crawler_map.items():
            if self.config.is_site_enabled(name):
                crawler = cls(self.config, self._db, self._dl)
                self._crawlers.append(crawler)
                logger.info(f"Initialized crawler: {name}")

        if not self._crawlers:
            logger.warning("No crawlers enabled! Check config.yaml")

    async def run_once(self, backfill: bool = False):
        """Run all crawlers once."""
        logger.info(
            f"Starting {'backfill' if backfill else 'incremental'} crawl..."
        )

        total = 0
        for crawler in self._crawlers:
            try:
                if backfill:
                    count = await crawler.crawl_all()
                else:
                    count = await crawler.crawl_latest()
                total += count
            except Exception as e:
                logger.error(f"Error in crawler {crawler.name}: {e}")

        # Print stats
        stats = self._db.get_stats()
        logger.info(
            f"Crawl complete. New videos: {total}. "
            f"Total in DB: {stats['total']}. "
            f"By source: {stats['by_source']}"
        )
        return total

    async def run_daemon(self, backfill_first: bool = True):
        """Run the daemon - continuous loop with periodic checks."""
        self._running = True
        interval = self.config.check_interval_minutes * 60

        logger.info(f"Daemon started. Check interval: {self.config.check_interval_minutes} min")

        if backfill_first:
            logger.info("Running initial backfill...")
            await self.run_once(backfill=True)

        while self._running:
            logger.info("Checking for new content...")
            try:
                await self.run_once(backfill=False)
            except Exception as e:
                logger.error(f"Error during check: {e}")

            # Wait for next interval (check _running periodically)
            for _ in range(interval):
                if not self._running:
                    break
                await asyncio.sleep(1)

        logger.info("Daemon stopped.")

    def stop(self):
        """Stop the daemon gracefully."""
        self._running = False
        logger.info("Stopping scheduler...")

    async def start(self, mode: str = "daemon", backfill_first: bool = True):
        """Start the scheduler in the specified mode.

        Args:
            mode: "daemon" for continuous, "once" for single run, "backfill" for full backfill
            backfill_first: run backfill before daemon mode
        """
        self._setup_logging()
        logger.info("=== Vid-Fetch-Hub ===")

        self._init_db()
        self._init_downloader()
        self._init_crawlers()

        if mode == "once":
            await self.run_once(backfill=False)
        elif mode == "backfill":
            await self.run_once(backfill=True)
        else:  # daemon
            await self.run_daemon(backfill_first=backfill_first)

        # Cleanup
        if self._dl:
            await self._dl.close()
        if self._db:
            self._db.close()

        logger.info("Done.")
