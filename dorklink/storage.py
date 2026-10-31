from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from .models import FileLink


class ScanStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    pages_visited INTEGER NOT NULL DEFAULT 0,
                    files_found INTEGER NOT NULL DEFAULT 0,
                    files_reachable INTEGER NOT NULL DEFAULT 0,
                    errors INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS file_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                    url TEXT NOT NULL,
                    source_page TEXT NOT NULL,
                    anchor_text TEXT NOT NULL DEFAULT '',
                    download_section TEXT NOT NULL DEFAULT '',
                    extension TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    content_type TEXT NOT NULL DEFAULT '',
                    content_length INTEGER,
                    reachable INTEGER NOT NULL DEFAULT 0,
                    checked_at TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    UNIQUE(scan_id, url)
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(file_links)")}
            if "download_section" not in columns:
                db.execute("ALTER TABLE file_links ADD COLUMN download_section TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def start_scan(self, target: str, started_at: str) -> int:
        with self._connect() as db:
            cur = db.execute("INSERT INTO scans(target, started_at) VALUES (?, ?)", (target, started_at))
            return int(cur.lastrowid)

    def add_file(self, scan_id: int, item: FileLink) -> None:
        self.add_files(scan_id, [item])

    def add_files(self, scan_id: int, items: Iterable[FileLink]) -> None:
        batch = list(items)
        if not batch:
            return
        with self._connect() as db:
            db.executemany(
                """INSERT OR IGNORE INTO file_links
                (scan_id,url,source_page,anchor_text,download_section,extension,http_status,content_type,content_length,reachable,checked_at,error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (scan_id, item.url, item.source_page, item.anchor_text, item.download_section, item.extension, item.http_status,
                     item.content_type, item.content_length, int(item.reachable), item.checked_at, item.error)
                    for item in batch
                ],
            )

    def update_file_evidence(self, scan_id: int, item: FileLink) -> None:
        """Persist additional source pages/labels discovered for a deduplicated URL."""
        with self._connect() as db:
            db.execute(
                "UPDATE file_links SET source_page=?, anchor_text=?, download_section=? WHERE scan_id=? AND url=?",
                (item.source_page, item.anchor_text, item.download_section, scan_id, item.url),
            )

    def finish_scan(self, scan_id: int, finished_at: str, pages: int, files: int, reachable: int, errors: int) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE scans SET finished_at=?, pages_visited=?, files_found=?, files_reachable=?, errors=? WHERE id=?",
                (finished_at, pages, files, reachable, errors, scan_id),
            )

    def rows(self, scan_id: int) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute("SELECT * FROM file_links WHERE scan_id=? ORDER BY url", (scan_id,)))

    def latest_scan(self) -> tuple[dict | None, list[dict]]:
        """Return the newest scan and its saved rows, including an interrupted scan."""
        with self._connect() as db:
            scan = db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
            if scan is None:
                return None, []
            rows = db.execute(
                "SELECT * FROM file_links WHERE scan_id=? ORDER BY url", (scan["id"],)
            ).fetchall()
            return dict(scan), [dict(row) for row in rows]
