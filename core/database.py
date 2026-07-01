"""SQLite database for tracking crawled content."""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


class Database:
    def __init__(self, db_path: str | Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    title TEXT,
                    page_url TEXT,
                    strm_path TEXT,
                    poster_path TEXT,
                    fanart_path TEXT,
                    nfo_path TEXT,
                    downloaded_at TEXT,
                    UNIQUE(source, video_id)
                );
                CREATE TABLE IF NOT EXISTS crawl_state (
                    source TEXT PRIMARY KEY,
                    last_page INTEGER DEFAULT 0,
                    last_crawled_at TEXT,
                    cursor TEXT,
                    backfill_done INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source);
                CREATE INDEX IF NOT EXISTS idx_videos_downloaded ON videos(downloaded_at);
            """)
            self._conn.commit()

    def is_downloaded(self, source: str, video_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM videos WHERE source = ? AND video_id = ?",
                (source, video_id),
            ).fetchone()
            return row is not None

    def mark_downloaded(
        self,
        source: str,
        video_id: str,
        title: str = "",
        page_url: str = "",
        strm_path: str = "",
        poster_path: str = "",
        fanart_path: str = "",
        nfo_path: str = "",
    ):
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO videos
                (source, video_id, title, page_url, strm_path,
                 poster_path, fanart_path, nfo_path, downloaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source,
                    video_id,
                    title,
                    page_url,
                    strm_path,
                    poster_path,
                    fanart_path,
                    nfo_path,
                    datetime.now().isoformat(),
                ),
            )
            self._conn.commit()

    def get_video(self, source: str, video_id: str) -> Optional[dict]:
        """Get a single video record (used by proxy to find page_url)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM videos WHERE source = ? AND video_id = ?",
                (source, video_id),
            ).fetchone()
            return dict(row) if row else None

    def get_crawl_state(self, source: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM crawl_state WHERE source = ?", (source,)
            ).fetchone()
            return dict(row) if row else None

    def save_crawl_state(self, source: str, last_page: int = 0, cursor: str = ""):
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO crawl_state
                (source, last_page, last_crawled_at, cursor, backfill_done)
                VALUES (?, ?, ?, ?, ?)""",
                (source, last_page, datetime.now().isoformat(), cursor, 0),
            )
            self._conn.commit()

    def is_backfill_done(self, source: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT backfill_done FROM crawl_state WHERE source = ?", (source,)
            ).fetchone()
            return bool(row["backfill_done"]) if row else False

    def mark_backfill_done(self, source: str):
        with self._lock:
            self._conn.execute(
                "UPDATE crawl_state SET backfill_done = 1 WHERE source = ?",
                (source,),
            )
            self._conn.commit()

    def get_stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) as c FROM videos").fetchone()["c"]
            by_source = {
                row["source"]: row["c"]
                for row in self._conn.execute(
                    "SELECT source, COUNT(*) as c FROM videos GROUP BY source"
                ).fetchall()
            }
            return {"total": total, "by_source": by_source}

    def close(self):
        self._conn.close()
