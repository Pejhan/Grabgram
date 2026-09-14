from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


PRIORITY_LEVELS = {"high": 0, "medium": 1, "low": 2}
PRIORITY_NAMES = ("high", "medium", "low")


def priority_level(priority: str | int) -> int:
    if isinstance(priority, str):
        try:
            return PRIORITY_LEVELS[priority.strip().lower()]
        except KeyError as exc:
            raise ValueError(f"Unknown priority: {priority}") from exc
    value = int(priority)
    if value not in range(len(PRIORITY_NAMES)):
        raise ValueError(f"Unknown priority level: {priority}")
    return value


def priority_name(level: int) -> str:
    try:
        return PRIORITY_NAMES[int(level)]
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"Unknown priority level: {level}") from exc


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
    paused INTEGER NOT NULL DEFAULT 0,
    media_types TEXT NOT NULL DEFAULT '',
    priority_level INTEGER NOT NULL DEFAULT 1,
    priority_rank INTEGER NOT NULL DEFAULT 0,
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
    priority_level INTEGER NOT NULL DEFAULT 1,
    priority_rank INTEGER NOT NULL DEFAULT 0,
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    output_path TEXT,
    error TEXT,
    downloaded_at TEXT,
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, message_id)
);

CREATE INDEX IF NOT EXISTS media_queue_idx
ON media(status, priority, message_date DESC, id DESC);

CREATE INDEX IF NOT EXISTS media_channel_status_idx
ON media(channel_id, status);

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
    paused: bool
    media_types: tuple[str, ...]
    priority_level: int
    priority_rank: int
    scan_complete: bool
    last_seen_message_id: int


@dataclass(frozen=True)
class MediaRecord:
    message_id: int
    document_id: int | None
    file_name: str | None
    size_bytes: int
    duration_seconds: int
    message_date: str


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
            if "paused" not in columns:
                conn.execute("ALTER TABLE channels ADD COLUMN paused INTEGER NOT NULL DEFAULT 0")
            if "media_types" not in columns:
                conn.execute("ALTER TABLE channels ADD COLUMN media_types TEXT NOT NULL DEFAULT ''")
            if "priority_level" not in columns:
                conn.execute("ALTER TABLE channels ADD COLUMN priority_level INTEGER NOT NULL DEFAULT 1")
            if "priority_rank" not in columns:
                conn.execute("ALTER TABLE channels ADD COLUMN priority_rank INTEGER NOT NULL DEFAULT 0")
                conn.execute("UPDATE channels SET priority_rank=id")
            media_columns = {row["name"] for row in conn.execute("PRAGMA table_info(media)")}
            if "priority_level" not in media_columns:
                conn.execute("ALTER TABLE media ADD COLUMN priority_level INTEGER NOT NULL DEFAULT 1")
            if "priority_rank" not in media_columns:
                conn.execute("ALTER TABLE media ADD COLUMN priority_rank INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """CREATE INDEX IF NOT EXISTS media_user_priority_idx
                   ON media(status, priority_level, priority_rank, priority,
                            message_date DESC, id DESC)"""
            )
            # An interrupted process resumes its current file and retries failures.
            conn.execute("UPDATE media SET status='queued' WHERE status='downloading'")

    @staticmethod
    def _channel(row: sqlite3.Row) -> Channel:
        return Channel(
            id=row["id"], telegram_id=row["telegram_id"], identifier=row["identifier"],
            title=row["title"], folder=row["folder"],
            min_duration_seconds=row["min_duration_seconds"], min_size_bytes=row["min_size_bytes"],
            enabled=bool(row["enabled"]), paused=bool(row["paused"]),
            media_types=tuple(filter(None, str(row["media_types"]).split(","))),
            priority_level=int(row["priority_level"]), priority_rank=int(row["priority_rank"]),
            scan_complete=bool(row["scan_complete"]),
            last_seen_message_id=int(row["last_seen_message_id"]),
        )

    def channels(self, enabled_only: bool = False) -> list[Channel]:
        sql = (
            "SELECT * FROM channels"
            + (" WHERE enabled=1" if enabled_only else "")
            + " ORDER BY priority_level, priority_rank, id"
        )
        with self.connect() as conn:
            return [self._channel(row) for row in conn.execute(sql)]

    def channel_overviews(self, enabled_only: bool = False) -> list[tuple[Channel, int, bool]]:
        where = "WHERE c.enabled=1" if enabled_only else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT c.*,
                       COALESCE(SUM(CASE WHEN m.status IN ('queued','downloading') THEN 1 ELSE 0 END), 0) queued,
                       COALESCE(MAX(CASE WHEN m.status='downloading' THEN 1 ELSE 0 END), 0) downloading
                    FROM channels c LEFT JOIN media m ON m.channel_id=c.id
                    {where}
                    GROUP BY c.id
                    ORDER BY c.priority_level, c.priority_rank, c.id"""
            ).fetchall()
        return [
            (self._channel(row), int(row["queued"]), bool(row["downloading"]))
            for row in rows
        ]

    def channel_by_telegram_id(self, telegram_id: int) -> Channel | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM channels WHERE telegram_id=?", (telegram_id,)).fetchone()
            return self._channel(row) if row else None

    def add_channel(self, identifier: str, telegram_id: int, title: str, folder: str,
                    min_duration_seconds: int, min_size_bytes: int,
                    media_types: tuple[str, ...] = ()) -> int:
        normalized_types = tuple(sorted({
            extension if extension.startswith(".") else f".{extension}"
            for extension in (item.strip().lower() for item in media_types) if extension
        }))
        serialized_types = ",".join(normalized_types)
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM channels WHERE identifier=? OR telegram_id=? LIMIT 1",
                (identifier, telegram_id),
            ).fetchone()
            if existing:
                channel_id = int(existing["id"])
                conn.execute(
                    """UPDATE channels SET identifier=?, telegram_id=?, title=?, folder=?,
                       min_duration_seconds=?, min_size_bytes=?, media_types=?,
                       enabled=1, paused=0, scan_complete=0
                       WHERE id=?""",
                    (identifier, telegram_id, title, folder, min_duration_seconds,
                     min_size_bytes, serialized_types, channel_id),
                )
                conn.execute(
                    """DELETE FROM media WHERE channel_id=?
                       AND status<>'downloaded'
                       AND ((duration_seconds > 0 AND duration_seconds < ?) OR size_bytes < ?)""",
                    (channel_id, min_duration_seconds, min_size_bytes),
                )
                if normalized_types:
                    rows = conn.execute(
                        """SELECT id, file_name FROM media
                           WHERE channel_id=? AND status<>'downloaded'""", (channel_id,)
                    ).fetchall()
                    discard_ids = [
                        int(row["id"]) for row in rows
                        if Path(str(row["file_name"])).suffix.lower() not in normalized_types
                    ]
                    if discard_ids:
                        marks = ",".join("?" for _ in discard_ids)
                        conn.execute(f"DELETE FROM media WHERE id IN ({marks})", discard_ids)
                self._bump_revision()
                return channel_id
            next_rank = int(conn.execute(
                "SELECT COALESCE(MAX(priority_rank), 0) + 1 rank FROM channels WHERE priority_level=1"
            ).fetchone()["rank"])
            cur = conn.execute(
                """INSERT INTO channels(identifier, telegram_id, title, folder,
                   min_duration_seconds, min_size_bytes, media_types,
                   priority_level, priority_rank) VALUES(?,?,?,?,?,?,?,?,?)""",
                (identifier, telegram_id, title, folder, min_duration_seconds,
                 min_size_bytes, serialized_types, PRIORITY_LEVELS["medium"], next_rank),
            )
            channel_id = int(cur.lastrowid)
        self._bump_revision()
        return channel_id

    def set_channel_enabled(self, channel_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE channels SET enabled=? WHERE id=?", (int(enabled), channel_id))
        self._bump_revision()

    def set_channel_paused(self, channel_id: int, paused: bool) -> None:
        with self.connect() as conn:
            changed = conn.execute(
                "UPDATE channels SET paused=? WHERE id=? AND enabled=1",
                (int(paused), channel_id),
            ).rowcount
        if changed:
            self._bump_revision()

    def set_channel_priority(self, channel_id: int, priority: str | int) -> None:
        level = priority_level(priority)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT priority_level FROM channels WHERE id=?", (channel_id,)
            ).fetchone()
            if row is None:
                return
            rank = int(conn.execute(
                """SELECT COALESCE(MIN(priority_rank), 0) - 1 rank
                   FROM channels WHERE priority_level=?""", (level,)
            ).fetchone()["rank"])
            changed = conn.execute(
                "UPDATE channels SET priority_level=?, priority_rank=? WHERE id=?",
                (level, rank, channel_id),
            ).rowcount
        if changed:
            self._bump_revision()

    def shift_channel_priority(self, channel_id: int, amount: int) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT priority_level FROM channels WHERE id=?", (channel_id,)
            ).fetchone()
        if row is None:
            return
        current = int(row["priority_level"])
        target = min(len(PRIORITY_NAMES) - 1, max(0, current + int(amount)))
        if target != current:
            self.set_channel_priority(channel_id, target)

    def set_media_priority(self, media_ids: list[int], priority: str | int) -> None:
        if not media_ids:
            return
        level = priority_level(priority)
        with self.connect() as conn:
            first_rank = int(conn.execute(
                """SELECT COALESCE(MIN(priority_rank), 0) - ? rank
                   FROM media WHERE priority_level=?""", (len(media_ids), level)
            ).fetchone()["rank"])
            changed = 0
            for offset, media_id in enumerate(media_ids):
                changed += conn.execute(
                    "UPDATE media SET priority_level=?, priority_rank=? WHERE id=?",
                    (level, first_rank + offset, int(media_id)),
                ).rowcount
        if changed:
            self._bump_revision()

    def shift_media_priority(self, media_ids: list[int], amount: int) -> None:
        if not media_ids:
            return
        marks = ",".join("?" for _ in media_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT id, priority_level FROM media WHERE id IN ({marks})",
                [int(media_id) for media_id in media_ids],
            ).fetchall()
        current_levels = {int(row["id"]): int(row["priority_level"]) for row in rows}
        grouped: dict[int, list[int]] = {}
        for media_id in media_ids:
            current = current_levels.get(int(media_id))
            if current is None:
                continue
            target = min(len(PRIORITY_NAMES) - 1, max(0, current + int(amount)))
            if target != current:
                grouped.setdefault(target, []).append(int(media_id))
        for target, ids in grouped.items():
            self.set_media_priority(ids, target)

    def set_scan_complete(self, channel_id: int, complete: bool = True) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE channels SET scan_complete=? WHERE id=?", (int(complete), channel_id))

    def note_seen(self, channel_id: int, message_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE channels SET last_seen_message_id=MAX(last_seen_message_id, ?)
                   WHERE id=?""", (message_id, channel_id)
            )

    def add_media_batch(
        self, channel: Channel, records: list[MediaRecord], priority: int,
    ) -> int:
        if not records:
            return 0
        inserted_count = 0
        changed = False
        with self.connect() as conn:
            conn.execute(
                """UPDATE channels SET last_seen_message_id=MAX(last_seen_message_id, ?)
                   WHERE id=?""",
                (max(record.message_id for record in records), channel.id),
            )
            for record in records:
                if record.file_name is None:
                    continue
                if (
                    (record.duration_seconds > 0
                     and record.duration_seconds < channel.min_duration_seconds)
                    or record.size_bytes < channel.min_size_bytes
                ):
                    continue
                if (
                    channel.media_types
                    and Path(record.file_name).suffix.lower() not in channel.media_types
                ):
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO media(
                       channel_id,message_id,telegram_document_id,file_name,size_bytes,
                       duration_seconds,message_date,priority) VALUES(?,?,?,?,?,?,?,?)""",
                    (channel.id, record.message_id, record.document_id, record.file_name,
                     record.size_bytes, record.duration_seconds, record.message_date, priority),
                )
                inserted_count += int(cur.rowcount > 0)
                changed = changed or cur.rowcount > 0
                if priority == 0:
                    changed = bool(conn.execute(
                        """UPDATE media SET priority=0 WHERE channel_id=? AND message_id=?
                           AND status='queued'""", (channel.id, record.message_id)
                    ).rowcount) or changed
        if changed:
            self._bump_revision()
        return inserted_count

    def add_media(self, *, channel: Channel, message_id: int, document_id: int | None,
                  file_name: str, size_bytes: int, duration_seconds: int,
                  message_date: str, priority: int) -> bool:
        return self.add_media_batch(
            channel,
            [MediaRecord(
                message_id, document_id, file_name, size_bytes, duration_seconds, message_date,
            )],
            priority,
        ) > 0

    def next_queued(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """SELECT m.*, c.identifier, c.folder, c.title AS channel_title
                   FROM media m JOIN channels c ON c.id=m.channel_id
                   WHERE m.status='queued' AND c.enabled=1 AND c.paused=0
                   ORDER BY c.priority_level, c.priority_rank, c.id,
                            m.priority_level, m.priority_rank, m.priority,
                            m.message_date DESC, m.id DESC LIMIT 1"""
            ).fetchone()

    def claim_next_queued(self) -> sqlite3.Row | None:
        """Atomically claim one item, but only when no other item is active."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT m.*, c.identifier, c.folder, c.title AS channel_title
                   FROM media m JOIN channels c ON c.id=m.channel_id
                   WHERE m.status='queued' AND c.enabled=1 AND c.paused=0
                     AND NOT EXISTS (SELECT 1 FROM media WHERE status='downloading')
                   ORDER BY c.priority_level, c.priority_rank, c.id,
                            m.priority_level, m.priority_rank, m.priority,
                            m.message_date DESC, m.id DESC LIMIT 1"""
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

    def mark_downloaded(self, media_id: int, output_path: str, size: int) -> bool:
        with self.connect() as conn:
            changed = conn.execute(
                """UPDATE media SET status='downloaded', bytes_downloaded=?, output_path=?,
                   downloaded_at=CURRENT_TIMESTAMP, error=NULL
                   WHERE id=? AND status='downloading'""", (size, output_path, media_id)
            ).rowcount
        if changed:
            self._bump_revision()
        return bool(changed)

    def mark_failed(self, media_id: int, error: str) -> None:
        with self.connect() as conn:
            changed = conn.execute(
                """UPDATE media SET status='failed', error=?
                   WHERE id=? AND status='downloading'""",
                (error[:1000], media_id),
            ).rowcount
        if changed:
            self._bump_revision()

    def requeue(self, media_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE media SET status='queued' WHERE id=? AND status='downloading'", (media_id,))
        self._bump_revision()

    def reset_active_download(self, media_id: int) -> None:
        """Requeue an active item with no retained partial-download state."""
        with self.connect() as conn:
            changed = conn.execute(
                """UPDATE media SET status='queued', bytes_downloaded=0, output_path=NULL,
                   downloaded_at=NULL, error=NULL WHERE id=? AND status='downloading'""",
                (media_id,),
            ).rowcount
        if changed:
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

    def remove_media(self, media_ids: list[int]) -> None:
        """Exclude queued or failed media from downloading until explicitly restored."""
        if not media_ids:
            return
        marks = ",".join("?" for _ in media_ids)
        with self.connect() as conn:
            changed = conn.execute(
                f"""UPDATE media SET status='removed', bytes_downloaded=0,
                    output_path=NULL, downloaded_at=NULL, error=NULL
                    WHERE id IN ({marks})
                      AND status IN ('queued','downloading','failed')""",
                [int(media_id) for media_id in media_ids],
            ).rowcount
        if changed:
            self._bump_revision()

    def restore_removed_media(self, media_ids: list[int]) -> None:
        """Return explicitly removed media to the download queue."""
        if not media_ids:
            return
        marks = ",".join("?" for _ in media_ids)
        with self.connect() as conn:
            changed = conn.execute(
                f"""UPDATE media SET status='queued', priority=0, bytes_downloaded=0,
                    output_path=NULL, downloaded_at=NULL, error=NULL
                    WHERE id IN ({marks}) AND status='removed'""",
                [int(media_id) for media_id in media_ids],
            ).rowcount
        if changed:
            self._bump_revision()

    def media_for_channel(self, channel_id: int, limit: int = 500) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT * FROM media WHERE channel_id=?
                   ORDER BY CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1
                     WHEN 'failed' THEN 2 ELSE 3 END,
                   priority_level, priority_rank, priority,
                   message_date DESC, id DESC LIMIT ?""", (channel_id, limit)
            ))

    def pending_media_for_channel(self, channel_id: int, limit: int = 500, offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT * FROM media
                   WHERE channel_id=? AND status NOT IN ('downloaded','removed')
                   ORDER BY CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                   priority_level, priority_rank, priority,
                   message_date DESC, id DESC LIMIT ? OFFSET ?""",
                (channel_id, limit, offset),
            ))

    @staticmethod
    def _name_filter(search_query: str | None) -> tuple[str, tuple[str, ...]]:
        query = (search_query or "").strip()
        return (" AND file_name LIKE ?", (f"%{query}%",)) if query else ("", ())

    @staticmethod
    def _media_order(sort_column: str | None, descending: bool,
                     default: str) -> str:
        columns = {
            "name": "file_name COLLATE NOCASE",
            "duration": "duration_seconds",
            "size": "size_bytes",
            "status": "status COLLATE NOCASE",
            "date_added": "message_date",
            "date_downloaded": "downloaded_at",
            "error": "error COLLATE NOCASE",
        }
        expression = columns.get(sort_column or "")
        if expression is None:
            return default
        direction = "DESC" if descending else "ASC"
        return f"{expression} {direction}, id {direction}"

    def queued_media_for_channel(self, channel_id: int, limit: int = 500,
                                 offset: int = 0,
                                 search_query: str | None = None,
                                 sort_column: str | None = None,
                                 descending: bool = False) -> list[sqlite3.Row]:
        name_sql, name_params = self._name_filter(search_query)
        order = self._media_order(
            sort_column, descending,
            "CASE status WHEN 'downloading' THEN 0 ELSE 1 END, "
            "priority_level, priority_rank, priority, message_date DESC, id DESC",
        )
        with self.connect() as conn:
            return list(conn.execute(
                   f"""SELECT * FROM media WHERE channel_id=? AND status IN ('queued','downloading')
                   {name_sql}
                   ORDER BY {order} LIMIT ? OFFSET ?""",
                (channel_id, *name_params, limit, offset),
            ))

    def failed_media_for_channel(self, channel_id: int, limit: int = 250,
                                 offset: int = 0,
                                 search_query: str | None = None,
                                 sort_column: str | None = None,
                                 descending: bool = False) -> list[sqlite3.Row]:
        name_sql, name_params = self._name_filter(search_query)
        order = self._media_order(sort_column, descending, "message_date DESC, id DESC")
        with self.connect() as conn:
            return list(conn.execute(
                f"""SELECT * FROM media WHERE channel_id=? AND status='failed'
                   {name_sql}
                   ORDER BY {order} LIMIT ? OFFSET ?""",
                (channel_id, *name_params, limit, offset),
            ))

    def downloaded_media_for_channel(self, channel_id: int, limit: int = 250,
                                     offset: int = 0,
                                     search_query: str | None = None,
                                     sort_column: str | None = None,
                                     descending: bool = False) -> list[sqlite3.Row]:
        name_sql, name_params = self._name_filter(search_query)
        order = self._media_order(sort_column, descending, "downloaded_at DESC, id DESC")
        with self.connect() as conn:
            return list(conn.execute(
                f"""SELECT * FROM media WHERE channel_id=? AND status='downloaded'
                   {name_sql}
                   ORDER BY {order} LIMIT ? OFFSET ?""",
                (channel_id, *name_params, limit, offset),
            ))

    def removed_media_for_channel(self, channel_id: int, limit: int = 250,
                                  offset: int = 0,
                                  search_query: str | None = None,
                                  sort_column: str | None = None,
                                  descending: bool = False) -> list[sqlite3.Row]:
        name_sql, name_params = self._name_filter(search_query)
        order = self._media_order(sort_column, descending, "message_date DESC, id DESC")
        with self.connect() as conn:
            return list(conn.execute(
                f"""SELECT * FROM media WHERE channel_id=? AND status='removed'
                   {name_sql}
                   ORDER BY {order} LIMIT ? OFFSET ?""",
                (channel_id, *name_params, limit, offset),
            ))

    def status_count(self, channel_id: int, status: str,
                     search_query: str | None = None) -> int:
        name_sql, name_params = self._name_filter(search_query)
        with self.connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) count FROM media
                    WHERE channel_id=? AND status=?{name_sql}""",
                (channel_id, status, *name_params),
            ).fetchone()
            return int(row["count"])

    def tab_counts(self, channel_id: int,
                   search_query: str | None = None) -> dict[str, int]:
        name_sql, name_params = self._name_filter(search_query)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT status, COUNT(*) count FROM media
                   WHERE channel_id=?{name_sql} GROUP BY status""",
                (channel_id, *name_params),
            ).fetchall()
        by_status = {str(row["status"]): int(row["count"]) for row in rows}
        return {
            "queue": by_status.get("queued", 0) + by_status.get("downloading", 0),
            "downloaded": by_status.get("downloaded", 0),
            "failed": by_status.get("failed", 0),
            "removed": by_status.get("removed", 0),
        }

    def channel_match_counts(self, search_query: str,
                             enabled_only: bool = False) -> dict[int, int]:
        """Return filename-match totals for each channel using a substring LIKE."""
        name_sql, name_params = self._name_filter(search_query)
        enabled_sql = " AND c.enabled=1" if enabled_only else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT c.id channel_id, COUNT(m.id) count
                    FROM channels c LEFT JOIN media m
                      ON m.channel_id=c.id{name_sql}
                    WHERE 1=1{enabled_sql}
                    GROUP BY c.id""",
                name_params,
            ).fetchall()
        return {int(row["channel_id"]): int(row["count"]) for row in rows}

    def pending_count(self, channel_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) count FROM media
                   WHERE channel_id=? AND status NOT IN ('downloaded','removed')""",
                (channel_id,),
            ).fetchone()
            return int(row["count"])

    def queue_count(self, channel_id: int, search_query: str | None = None) -> int:
        name_sql, name_params = self._name_filter(search_query)
        with self.connect() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) count FROM media
                   WHERE channel_id=? AND status IN ('queued','downloading'){name_sql}""",
                (channel_id, *name_params),
            ).fetchone()
            return int(row["count"])

    def media_ids_by_status(self, channel_id: int, status: str,
                            search_query: str | None = None) -> list[int]:
        name_sql, name_params = self._name_filter(search_query)
        with self.connect() as conn:
            return [int(row["id"]) for row in conn.execute(
                f"SELECT id FROM media WHERE channel_id=? AND status=?{name_sql}",
                (channel_id, status, *name_params),
            )]

    def active_download(self, channel_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM media WHERE channel_id=? AND status='downloading' LIMIT 1", (channel_id,)
            ).fetchone()

    def media_exists(self, media_id: int) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM media WHERE id=?", (media_id,)).fetchone() is not None

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
                   WHERE m.status='queued' AND c.enabled=1 AND c.paused=0
                   ORDER BY c.priority_level, c.priority_rank, c.id,
                            m.priority_level, m.priority_rank, m.priority,
                            m.message_date DESC, m.id DESC"""
            ).fetchall()
        return {int(row["id"]): position for position, row in enumerate(rows, 1)}

    def channel_stats(self, channel_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) count, COALESCE(SUM(size_bytes),0) bytes FROM media WHERE channel_id=? GROUP BY status",
                (channel_id,),
            ).fetchall()
        result: dict[str, Any] = {"queued": 0, "downloading": 0, "downloaded": 0, "failed": 0,
                                  "removed": 0,
                                  "total": 0, "bytes": 0}
        for row in rows:
            result[row["status"]] = row["count"]
            result["total"] += row["count"]
            result["bytes"] += row["bytes"]
        return result
