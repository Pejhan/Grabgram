from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    folder TEXT NOT NULL,
    min_duration_seconds INTEGER NOT NULL DEFAULT 0,
    min_size_bytes INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    scan_complete INTEGER NOT NULL DEFAULT 0,
    last_seen_message_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL,
    telegram_document_id INTEGER,
    file_name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    message_date TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 10,
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    output_path TEXT,
    error TEXT,
    downloaded_at TEXT,
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, message_id)
);

CREATE INDEX IF NOT EXISTS media_queue_idx
ON media(status, priority, message_date DESC, id DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Channel:
    id: int
    telegram_id: int | None
    identifier: str
    title: str
    folder: str
    min_duration_seconds: int
    min_size_bytes: int
    enabled: bool
    scan_complete: bool
    last_seen_message_id: int


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._revision = 0
        self._revision_lock = threading.Lock()
        self._initialize()

    @property
    def revision(self) -> int:
        with self._revision_lock:
            return self._revision

    def _bump_revision(self) -> None:
        with self._revision_lock:
            self._revision += 1

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(channels)")}
            if "last_seen_message_id" not in columns:
                conn.execute("ALTER TABLE channels ADD COLUMN last_seen_message_id INTEGER NOT NULL DEFAULT 0")
            # An interrupted process resumes its current file and retries failures.
            conn.execute("UPDATE media SET status='queued' WHERE status='downloading'")

    @staticmethod
    def _channel(row: sqlite3.Row) -> Channel:
        return Channel(
            id=row["id"], telegram_id=row["telegram_id"], identifier=row["identifier"],
            title=row["title"], folder=row["folder"],
            min_duration_seconds=row["min_duration_seconds"], min_size_bytes=row["min_size_bytes"],
            enabled=bool(row["enabled"]), scan_complete=bool(row["scan_complete"]),
            last_seen_message_id=int(row["last_seen_message_id"]),
        )

    def channels(self, enabled_only: bool = False) -> list[Channel]:
        sql = "SELECT * FROM channels" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY title"
        with self.connect() as conn:
            return [self._channel(row) for row in conn.execute(sql)]

    def channel_by_telegram_id(self, telegram_id: int) -> Channel | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM channels WHERE telegram_id=?", (telegram_id,)).fetchone()
            return self._channel(row) if row else None

    def add_channel(self, identifier: str, telegram_id: int, title: str, folder: str,
                    min_duration_seconds: int, min_size_bytes: int) -> int:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM channels WHERE identifier=? OR telegram_id=? LIMIT 1",
                (identifier, telegram_id),
            ).fetchone()
            if existing:
                channel_id = int(existing["id"])
                conn.execute(
                    """UPDATE channels SET identifier=?, telegram_id=?, title=?, folder=?,
                       min_duration_seconds=?, min_size_bytes=?, enabled=1, scan_complete=0
                       WHERE id=?""",
                    (identifier, telegram_id, title, folder, min_duration_seconds,
                     min_size_bytes, channel_id),
                )
                self._bump_revision()
                return channel_id
            cur = conn.execute(
                """INSERT INTO channels(identifier, telegram_id, title, folder,
                   min_duration_seconds, min_size_bytes) VALUES(?,?,?,?,?,?)""",
                (identifier, telegram_id, title, folder, min_duration_seconds, min_size_bytes),
            )
            channel_id = int(cur.lastrowid)
        self._bump_revision()
        return channel_id

    def set_channel_enabled(self, channel_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE channels SET enabled=? WHERE id=?", (int(enabled), channel_id))
        self._bump_revision()

    def set_scan_complete(self, channel_id: int, complete: bool = True) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE channels SET scan_complete=? WHERE id=?", (int(complete), channel_id))

    def note_seen(self, channel_id: int, message_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE channels SET last_seen_message_id=MAX(last_seen_message_id, ?)
                   WHERE id=?""", (message_id, channel_id)
            )

    def add_media(self, *, channel: Channel, message_id: int, document_id: int | None,
                  file_name: str, size_bytes: int, duration_seconds: int,
                  message_date: str, priority: int) -> bool:
        if duration_seconds < channel.min_duration_seconds or size_bytes < channel.min_size_bytes:
            return False
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO media(
                   channel_id,message_id,telegram_document_id,file_name,size_bytes,
                   duration_seconds,message_date,priority) VALUES(?,?,?,?,?,?,?,?)""",
                (channel.id, message_id, document_id, file_name, size_bytes,
                 duration_seconds, message_date, priority),
            )
            inserted = cur.rowcount > 0
            if priority == 0:
                # A live event may race the historical scanner. Promote the already-seen
                # row so it still gets new-item priority without creating a duplicate.
                conn.execute(
                    """UPDATE media SET priority=0 WHERE channel_id=? AND message_id=?
                       AND status='queued'""", (channel.id, message_id)
                )
        if inserted or priority == 0:
            self._bump_revision()
        return inserted

    def next_queued(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """SELECT m.*, c.identifier, c.folder, c.title AS channel_title
                   FROM media m JOIN channels c ON c.id=m.channel_id
                   WHERE m.status='queued' AND c.enabled=1
                   ORDER BY m.priority ASC, m.message_date DESC, m.id DESC LIMIT 1"""
            ).fetchone()

    def claim_next_queued(self) -> sqlite3.Row | None:
        """Atomically claim one item, but only when no other item is active."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT m.*, c.identifier, c.folder, c.title AS channel_title
                   FROM media m JOIN channels c ON c.id=m.channel_id
                   WHERE m.status='queued' AND c.enabled=1
                     AND NOT EXISTS (SELECT 1 FROM media WHERE status='downloading')
                   ORDER BY m.priority ASC, m.message_date DESC, m.id DESC LIMIT 1"""
            ).fetchone()
            if row is not None:
                conn.execute("UPDATE media SET status='downloading', error=NULL WHERE id=?", (row["id"],))
        if row is not None:
            self._bump_revision()
        return row

    def mark_downloading(self, media_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE media SET status='downloading', error=NULL WHERE id=?", (media_id,))
        self._bump_revision()

    def update_progress(self, media_id: int, downloaded: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE media SET bytes_downloaded=? WHERE id=?", (downloaded, media_id))

    def mark_downloaded(self, media_id: int, output_path: str, size: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE media SET status='downloaded', bytes_downloaded=?, output_path=?,
                   downloaded_at=CURRENT_TIMESTAMP, error=NULL WHERE id=?""", (size, output_path, media_id)
            )
        self._bump_revision()

    def mark_failed(self, media_id: int, error: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE media SET status='failed', error=? WHERE id=?", (error[:1000], media_id))
        self._bump_revision()

    def requeue(self, media_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE media SET status='queued' WHERE id=? AND status='downloading'", (media_id,))
        self._bump_revision()

    def requeue_interrupted_downloads(self) -> None:
        """Make work claimed by a stopped engine available to a replacement worker."""
        with self.connect() as conn:
            changed = conn.execute("UPDATE media SET status='queued' WHERE status='downloading'").rowcount
        if changed:
            self._bump_revision()

    def retry(self, media_ids: list[int], include_downloaded: bool = False) -> None:
        if not media_ids:
            return
        marks = ",".join("?" for _ in media_ids)
        allowed = "('failed')" if not include_downloaded else "('failed','downloaded')"
        with self.connect() as conn:
            changed = conn.execute(
                f"""UPDATE media SET status='queued', priority=0, bytes_downloaded=0,
                    error=NULL, downloaded_at=NULL WHERE id IN ({marks}) AND status IN {allowed}""", media_ids
            ).rowcount
        if changed:
            self._bump_revision()

    def media_for_channel(self, channel_id: int, limit: int = 500) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT * FROM media WHERE channel_id=?
                   ORDER BY CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1
                     WHEN 'failed' THEN 2 ELSE 3 END,
                   priority ASC, message_date DESC, id DESC LIMIT ?""", (channel_id, limit)
            ))

    def pending_media_for_channel(self, channel_id: int, limit: int = 500, offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT * FROM media WHERE channel_id=? AND status!='downloaded'
                   ORDER BY CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                   priority ASC, message_date DESC, id DESC LIMIT ? OFFSET ?""",
                (channel_id, limit, offset),
            ))

    def queued_media_for_channel(self, channel_id: int, limit: int = 500,
                                 offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT * FROM media WHERE channel_id=? AND status IN ('queued','downloading')
                   ORDER BY CASE status WHEN 'downloading' THEN 0 ELSE 1 END,
                   priority ASC, message_date DESC, id DESC LIMIT ? OFFSET ?""",
                (channel_id, limit, offset),
            ))

    def failed_media_for_channel(self, channel_id: int, limit: int = 250,
                                 offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT * FROM media WHERE channel_id=? AND status='failed'
                   ORDER BY message_date DESC, id DESC LIMIT ? OFFSET ?""",
                (channel_id, limit, offset),
            ))

    def downloaded_media_for_channel(self, channel_id: int, limit: int = 250,
                                     offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT * FROM media WHERE channel_id=? AND status='downloaded'
                   ORDER BY downloaded_at DESC, id DESC LIMIT ? OFFSET ?""",
                (channel_id, limit, offset),
            ))

    def status_count(self, channel_id: int, status: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) count FROM media WHERE channel_id=? AND status=?", (channel_id, status)
            ).fetchone()
            return int(row["count"])

    def pending_count(self, channel_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) count FROM media WHERE channel_id=? AND status!='downloaded'", (channel_id,)
            ).fetchone()
            return int(row["count"])

    def queue_count(self, channel_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) count FROM media
                   WHERE channel_id=? AND status IN ('queued','downloading')""", (channel_id,)
            ).fetchone()
            return int(row["count"])

    def media_ids_by_status(self, channel_id: int, status: str) -> list[int]:
        with self.connect() as conn:
            return [int(row["id"]) for row in conn.execute(
                "SELECT id FROM media WHERE channel_id=? AND status=?", (channel_id, status)
            )]

    def active_download(self, channel_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM media WHERE channel_id=? AND status='downloading' LIMIT 1", (channel_id,)
            ).fetchone()

    def setting_int(self, key: str, default: int = 0) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        try:
            return int(row["value"]) if row else default
        except (TypeError, ValueError):
            return default

    def setting_text(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting_int(self, key: str, value: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO settings(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, str(int(value)))
            )

    def set_setting_text(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO settings(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, str(value))
            )

    def queue_positions(self) -> dict[int, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT m.id FROM media m JOIN channels c ON c.id=m.channel_id
                   WHERE m.status='queued' AND c.enabled=1
                   ORDER BY m.priority ASC, m.message_date DESC, m.id DESC"""
            ).fetchall()
        return {int(row["id"]): position for position, row in enumerate(rows, 1)}

    def channel_stats(self, channel_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) count, COALESCE(SUM(size_bytes),0) bytes FROM media WHERE channel_id=? GROUP BY status",
                (channel_id,),
            ).fetchall()
        result: dict[str, Any] = {"queued": 0, "downloading": 0, "downloaded": 0, "failed": 0,
                                  "total": 0, "bytes": 0}
        for row in rows:
            result[row["status"]] = row["count"]
            result["total"] += row["count"]
            result["bytes"] += row["bytes"]
        return result
