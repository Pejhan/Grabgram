from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from tg_downloader.database import Database, MediaRecord


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.db = Database(self.path)
        channel_id = self.db.add_channel("test", 123, "Test", "Test", 60, 1_000)
        self.channel = next(c for c in self.db.channels() if c.id == channel_id)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add(self, message_id: int, *, priority: int = 10, duration: int = 120, size: int = 5_000) -> bool:
        return self.db.add_media(
            channel=self.channel, message_id=message_id, document_id=message_id,
            file_name=f"song-{message_id}.mp3", size_bytes=size, duration_seconds=duration,
            message_date=f"2026-01-{message_id:02d}T00:00:00+00:00", priority=priority,
        )

    def test_thresholds_require_both_minimums(self) -> None:
        self.assertFalse(self.add(1, duration=59))
        self.assertFalse(self.add(2, size=999))
        self.assertTrue(self.add(3, duration=60, size=1_000))

    def test_media_batch_uses_one_revision_and_records_latest_seen_message(self) -> None:
        before = self.db.revision
        inserted = self.db.add_media_batch(
            self.channel,
            [
                MediaRecord(10, 10, "first.mp3", 5_000, 120, ""),
                MediaRecord(11, None, None, 0, 0, ""),
                MediaRecord(12, 12, "too-small.mp3", 999, 120, ""),
                MediaRecord(13, 13, "second.mp3", 5_000, 120, ""),
            ],
            priority=10,
        )
        self.assertEqual(2, inserted)
        self.assertEqual(before + 1, self.db.revision)
        channel = next(item for item in self.db.channels() if item.id == self.channel.id)
        self.assertEqual(13, channel.last_seen_message_id)

    def test_channel_overview_and_tab_counts_share_runtime_totals(self) -> None:
        self.add(1)
        self.add(2)
        active = self.db.claim_next_queued()
        channel, queued, downloading = self.db.channel_overviews(True)[0]
        self.assertEqual(self.channel.id, channel.id)
        self.assertEqual(2, queued)
        self.assertTrue(downloading)
        self.assertEqual(
            {"queue": 2, "downloaded": 0, "failed": 0, "removed": 0},
            self.db.tab_counts(self.channel.id),
        )
        self.assertIsNotNone(active)

    def test_undated_media_ignores_length_but_still_obeys_size(self) -> None:
        self.assertTrue(self.add(1, duration=0, size=1_000))
        self.assertFalse(self.add(2, duration=0, size=999))

    def test_selected_media_types_are_persistent_and_filter_new_rows(self) -> None:
        self.db.add_channel("test", 123, "Test", "Test", 60, 1_000, ("mp4", ".jpg"))
        channel = self.db.channels()[0]
        self.assertEqual((".jpg", ".mp4"), channel.media_types)
        self.assertTrue(self.db.add_media(
            channel=channel, message_id=1, document_id=1, file_name="clip.mp4",
            size_bytes=5_000, duration_seconds=120, message_date="", priority=10,
        ))
        self.assertTrue(self.db.add_media(
            channel=channel, message_id=2, document_id=2, file_name="photo.jpg",
            size_bytes=5_000, duration_seconds=0, message_date="", priority=10,
        ))
        self.assertFalse(self.db.add_media(
            channel=channel, message_id=3, document_id=3, file_name="song.mp3",
            size_bytes=5_000, duration_seconds=120, message_date="", priority=10,
        ))
        self.assertEqual((".jpg", ".mp4"), Database(self.path).channels()[0].media_types)

    def test_readding_with_selected_types_discards_existing_unselected_rows(self) -> None:
        self.assertTrue(self.add(1))
        downloaded = self.db.next_queued()
        self.db.mark_downloading(int(downloaded["id"]))
        self.db.mark_downloaded(int(downloaded["id"]), "song-1.mp3", 5_000)
        self.assertTrue(self.db.add_media(
            channel=self.channel, message_id=2, document_id=2, file_name="photo.jpg",
            size_bytes=5_000, duration_seconds=0, message_date="", priority=10,
        ))
        self.db.add_channel("test", 123, "Test", "Test", 60, 1_000, (".jpg",))
        remaining = self.db.media_for_channel(self.channel.id)
        self.assertEqual(
            {"song-1.mp3", "photo.jpg"},
            {str(row["file_name"]) for row in remaining},
        )
        kept_download = next(row for row in remaining if row["file_name"] == "song-1.mp3")
        self.assertEqual("downloaded", kept_download["status"])

    def test_live_item_has_priority_over_backlog(self) -> None:
        self.add(1, priority=10)
        self.add(2, priority=0)
        self.assertEqual(2, self.db.next_queued()["message_id"])
        self.assertEqual({2: 1, 1: 2}, self.db.queue_positions())

    def test_channel_and_file_priorities_control_queue_with_hidden_rank(self) -> None:
        self.add(1)
        self.add(2)
        second_id = self.db.add_channel("second", 456, "Second", "Second", 0, 0)
        second = next(channel for channel in self.db.channels() if channel.id == second_id)
        self.db.add_media(
            channel=second, message_id=3, document_id=3, file_name="second.mp3",
            size_bytes=5_000, duration_seconds=120, message_date="2026-02-01T00:00:00+00:00",
            priority=10,
        )

        self.db.set_channel_priority(second_id, "high")
        self.assertEqual(3, self.db.next_queued()["message_id"])

        self.db.set_channel_priority(second_id, "low")
        own_rows = {int(row["message_id"]): int(row["id"]) for row in self.db.media_for_channel(self.channel.id)}
        self.db.set_media_priority([own_rows[1]], "high")
        self.db.set_media_priority([own_rows[2]], "low")
        self.assertEqual(1, self.db.next_queued()["message_id"])

        # Reassigning inside a level gives the item a new internal rank at the front.
        self.db.set_media_priority([own_rows[2]], "high")
        self.assertEqual(2, self.db.next_queued()["message_id"])
        reopened_second = next(channel for channel in Database(self.path).channels() if channel.id == second_id)
        self.assertEqual(2, reopened_second.priority_level)

    def test_invalid_priority_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.db.set_channel_priority(self.channel.id, "urgent")

    def test_priority_shift_buttons_move_one_level_and_stop_at_boundaries(self) -> None:
        self.add(1)
        self.add(2)
        rows = {int(row["message_id"]): int(row["id"]) for row in self.db.media_for_channel(self.channel.id)}

        self.db.shift_channel_priority(self.channel.id, -1)
        self.assertEqual(0, next(channel for channel in self.db.channels() if channel.id == self.channel.id).priority_level)
        self.db.shift_channel_priority(self.channel.id, -1)
        self.assertEqual(0, next(channel for channel in self.db.channels() if channel.id == self.channel.id).priority_level)

        self.db.shift_media_priority([rows[1], rows[2]], -1)
        levels = {
            int(row["message_id"]): int(row["priority_level"])
            for row in self.db.media_for_channel(self.channel.id)
        }
        self.assertEqual({1: 0, 2: 0}, levels)
        self.db.shift_media_priority([rows[2]], 1)
        self.db.shift_media_priority([rows[2]], 1)
        self.db.shift_media_priority([rows[2]], 1)
        levels = {
            int(row["message_id"]): int(row["priority_level"])
            for row in self.db.media_for_channel(self.channel.id)
        }
        self.assertEqual({1: 0, 2: 2}, levels)

    def test_existing_database_is_migrated_to_priority_columns(self) -> None:
        legacy_path = Path(self.temp.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript("""
                CREATE TABLE channels (
                    id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE,
                    identifier TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    folder TEXT NOT NULL, min_duration_seconds INTEGER NOT NULL DEFAULT 0,
                    min_size_bytes INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
                    paused INTEGER NOT NULL DEFAULT 0, media_types TEXT NOT NULL DEFAULT '',
                    scan_complete INTEGER NOT NULL DEFAULT 0,
                    last_seen_message_id INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE media (
                    id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL, telegram_document_id INTEGER,
                    file_name TEXT NOT NULL, size_bytes INTEGER NOT NULL DEFAULT 0,
                    duration_seconds INTEGER NOT NULL DEFAULT 0, message_date TEXT,
                    status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 10,
                    bytes_downloaded INTEGER NOT NULL DEFAULT 0, output_path TEXT,
                    error TEXT, downloaded_at TEXT,
                    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(channel_id, message_id)
                );
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO channels(identifier, telegram_id, title, folder)
                VALUES('legacy', 999, 'Legacy', 'Legacy');
            """)
            connection.commit()
        finally:
            connection.close()

        migrated = Database(legacy_path)
        channel = migrated.channels()[0]
        self.assertEqual(1, channel.priority_level)
        self.assertEqual(channel.id, channel.priority_rank)
        with migrated.connect() as connection:
            media_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(media)")
            }
        self.assertTrue({"priority_level", "priority_rank"}.issubset(media_columns))

    def test_downloaded_record_survives_missing_local_file_and_restart(self) -> None:
        self.add(1)
        item = self.db.next_queued()
        self.db.mark_downloading(item["id"])
        self.db.mark_downloaded(item["id"], "a-file-that-does-not-exist.mp3", 5_000)
        reopened = Database(self.path)
        self.assertIsNone(reopened.next_queued())
        self.assertEqual("downloaded", reopened.media_for_channel(self.channel.id)[0]["status"])

    def test_interrupted_download_is_requeued_on_restart(self) -> None:
        self.add(1)
        item = self.db.next_queued()
        self.db.mark_downloading(item["id"])
        reopened = Database(self.path)
        self.assertEqual(1, reopened.next_queued()["message_id"])

    def test_duplicate_message_is_ignored(self) -> None:
        self.assertTrue(self.add(1))
        self.assertFalse(self.add(1, priority=0))

    def test_live_duplicate_promotes_scanner_item(self) -> None:
        self.add(1, priority=10)
        self.add(2, priority=10)
        self.assertFalse(self.add(1, priority=0))
        self.assertEqual(1, self.db.next_queued()["message_id"])

    def test_only_one_item_can_be_claimed_at_a_time(self) -> None:
        self.add(1)
        self.add(2)
        first = self.db.claim_next_queued()
        self.assertIsNotNone(first)
        self.assertIsNone(self.db.claim_next_queued())
        self.db.mark_downloaded(first["id"], "song.mp3", first["size_bytes"])
        self.assertIsNotNone(self.db.claim_next_queued())

    def test_paused_channel_is_persistent_and_skipped_by_queue(self) -> None:
        self.add(1)
        self.db.set_channel_paused(self.channel.id, True)
        self.assertTrue(self.db.channels(enabled_only=True)[0].paused)
        self.assertIsNone(self.db.next_queued())
        self.assertEqual({}, self.db.queue_positions())

        reopened = Database(self.path)
        self.assertTrue(reopened.channels(enabled_only=True)[0].paused)
        reopened.set_channel_paused(self.channel.id, False)
        self.assertEqual(1, reopened.next_queued()["message_id"])

    def test_downloaded_and_pending_queries_are_separate_and_paged(self) -> None:
        self.add(1)
        self.add(2)
        item = self.db.claim_next_queued()
        self.db.mark_downloaded(item["id"], "song.mp3", item["size_bytes"])
        self.assertEqual(1, self.db.pending_count(self.channel.id))
        self.assertEqual(1, self.db.status_count(self.channel.id, "downloaded"))
        self.assertEqual(1, len(self.db.pending_media_for_channel(self.channel.id)))
        self.assertEqual(item["id"], self.db.downloaded_media_for_channel(self.channel.id)[0]["id"])
        self.assertEqual([], self.db.downloaded_media_for_channel(self.channel.id, limit=1, offset=1))

    def test_queue_downloaded_and_failed_queries_are_independent(self) -> None:
        self.add(1)
        self.add(2)
        self.add(3)
        downloaded = self.db.claim_next_queued()
        self.db.mark_downloaded(downloaded["id"], "song.mp3", downloaded["size_bytes"])
        failed = self.db.claim_next_queued()
        self.db.mark_failed(failed["id"], "test failure")

        self.assertEqual(1, self.db.queue_count(self.channel.id))
        self.assertEqual(1, self.db.status_count(self.channel.id, "downloaded"))
        self.assertEqual(1, self.db.status_count(self.channel.id, "failed"))
        self.assertEqual("queued", self.db.queued_media_for_channel(self.channel.id)[0]["status"])
        downloaded_row = self.db.downloaded_media_for_channel(self.channel.id)[0]
        self.assertEqual("downloaded", downloaded_row["status"])
        self.assertIsNotNone(downloaded_row["downloaded_at"])
        self.assertEqual("failed", self.db.failed_media_for_channel(self.channel.id)[0]["status"])
        self.db.retry([failed["id"]])
        self.assertEqual([], self.db.failed_media_for_channel(self.channel.id))
        self.assertEqual(2, self.db.queue_count(self.channel.id))

    def test_filename_search_filters_all_tabs_counts_and_pages_with_like(self) -> None:
        self.add(1)
        self.add(2)
        self.add(3)
        rows = {int(row["message_id"]): row for row in self.db.media_for_channel(self.channel.id)}
        self.db.mark_downloading(int(rows[1]["id"]))
        self.db.mark_downloaded(int(rows[1]["id"]), "song-1.mp3", 5_000)
        self.db.mark_downloading(int(rows[2]["id"]))
        self.db.mark_failed(int(rows[2]["id"]), "test failure")

        self.assertEqual(
            {"queue": 1, "downloaded": 1, "failed": 1, "removed": 0},
            self.db.tab_counts(self.channel.id, "SONG-"),
        )
        self.assertEqual(1, len(self.db.queued_media_for_channel(self.channel.id, search_query="-3.")))
        self.assertEqual(1, len(self.db.downloaded_media_for_channel(self.channel.id, search_query="ong-1")))
        self.assertEqual(1, len(self.db.failed_media_for_channel(self.channel.id, search_query="song-2")))
        self.assertEqual([], self.db.failed_media_for_channel(self.channel.id, search_query="missing"))

    def test_removed_media_is_persistent_skipped_and_restorable(self) -> None:
        self.add(1)
        self.add(2)
        rows = {int(row["message_id"]): row for row in self.db.media_for_channel(self.channel.id)}
        active_id = int(rows[2]["id"])
        self.db.mark_downloading(active_id)

        self.db.remove_media([active_id, int(rows[1]["id"])])
        self.assertIsNone(self.db.next_queued())
        self.assertEqual(0, self.db.pending_count(self.channel.id))
        self.assertFalse(self.db.mark_downloaded(active_id, "should-not-exist.mp3", 5_000))
        self.assertEqual(2, self.db.status_count(self.channel.id, "removed"))
        self.assertEqual(2, len(Database(self.path).removed_media_for_channel(self.channel.id)))

        self.db.restore_removed_media([int(rows[1]["id"])])
        restored = self.db.next_queued()
        self.assertIsNotNone(restored)
        self.assertEqual(1, int(restored["message_id"]))
        self.assertEqual(1, self.db.status_count(self.channel.id, "removed"))

    def test_media_lists_sort_all_rows_before_pagination(self) -> None:
        self.add(1, size=3_000)
        self.add(2, size=9_000)
        self.add(3, size=6_000)

        ascending = self.db.queued_media_for_channel(
            self.channel.id, limit=2, sort_column="size",
        )
        descending = self.db.queued_media_for_channel(
            self.channel.id, limit=2, sort_column="size", descending=True,
        )
        self.assertEqual([1, 3], [int(row["message_id"]) for row in ascending])
        self.assertEqual([2, 3], [int(row["message_id"]) for row in descending])

        # Unknown values cannot be interpolated into SQL and fall back to default order.
        default_rows = self.db.queued_media_for_channel(
            self.channel.id, sort_column="not-a-column",
        )
        self.assertEqual([3, 2, 1], [int(row["message_id"]) for row in default_rows])

    def test_filename_search_reports_match_count_for_every_enabled_channel(self) -> None:
        self.add(1)
        second_id = self.db.add_channel("second", 456, "Second", "Second", 0, 0)
        third_id = self.db.add_channel("third", 789, "Third", "Third", 0, 0)
        second = next(channel for channel in self.db.channels() if channel.id == second_id)
        self.db.add_media(
            channel=second, message_id=10, document_id=10, file_name="favorite-song.mp3",
            size_bytes=5_000, duration_seconds=120, message_date="", priority=10,
        )
        self.db.set_channel_enabled(third_id, False)

        self.assertEqual(
            {self.channel.id: 1, second_id: 1},
            self.db.channel_match_counts("song", enabled_only=True),
        )

    def test_integer_setting_is_persistent(self) -> None:
        self.db.set_setting_int("speed_limit_bytes_per_second", 123_456)
        reopened = Database(self.path)
        self.assertEqual(123_456, reopened.setting_int("speed_limit_bytes_per_second"))

    def test_revision_changes_for_structure_but_not_progress_bytes(self) -> None:
        before_add = self.db.revision
        self.add(1)
        after_add = self.db.revision
        self.assertGreater(after_add, before_add)
        item = self.db.claim_next_queued()
        after_claim = self.db.revision
        self.db.update_progress(item["id"], 100)
        self.assertEqual(after_claim, self.db.revision)
        self.db.mark_failed(item["id"], "failed")
        self.assertGreater(self.db.revision, after_claim)

    def test_archived_channel_can_be_restored_without_losing_history(self) -> None:
        self.add(1)
        item = self.db.claim_next_queued()
        self.db.mark_downloaded(item["id"], "song.mp3", item["size_bytes"])
        self.db.set_channel_enabled(self.channel.id, False)
        self.assertEqual([], self.db.channels(enabled_only=True))

        restored_id = self.db.add_channel("test", 123, "Test restored", "Restored", 0, 0)
        self.assertEqual(self.channel.id, restored_id)
        self.assertEqual(1, self.db.status_count(restored_id, "downloaded"))
        self.assertEqual("Test restored", self.db.channels(enabled_only=True)[0].title)

    def test_readding_reapplies_thresholds_but_preserves_downloaded_history(self) -> None:
        for message_id, duration, size in (
            (1, 120, 5_000),
            (2, 240, 7_000),
            (3, 240, 5_000),
            (4, 120, 7_000),
        ):
            self.add(message_id, duration=duration, size=size)

        rows = {int(row["message_id"]): row for row in self.db.media_for_channel(self.channel.id)}
        self.db.mark_downloading(int(rows[1]["id"]))
        self.db.mark_downloaded(int(rows[1]["id"]), "small-downloaded.mp3", 5_000)
        self.db.mark_downloading(int(rows[2]["id"]))
        self.db.mark_downloaded(int(rows[2]["id"]), "kept-downloaded.mp3", 7_000)
        self.db.mark_downloading(int(rows[3]["id"]))
        self.db.mark_failed(int(rows[3]["id"]), "test failure")

        restored_id = self.db.add_channel("test", 123, "Test", "Test", 180, 6_000)
        remaining = self.db.media_for_channel(restored_id)
        self.assertEqual([1, 2], sorted(int(row["message_id"]) for row in remaining))
        self.assertTrue(all(row["status"] == "downloaded" for row in remaining))
        restored = self.db.channels(enabled_only=True)[0]
        self.assertEqual(180, restored.min_duration_seconds)
        self.assertEqual(6_000, restored.min_size_bytes)


if __name__ == "__main__":
    unittest.main()
