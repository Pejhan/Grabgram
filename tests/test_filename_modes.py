from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from grabgram.config import Config
from grabgram.database import Database
from grabgram.engine import DownloaderEngine


class FileFilenameModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = Database(self.root / "ledger.sqlite3")
        self.channel_id = self.db.add_channel("test", 123, "Test", "Test", 0, 0)
        self.events = []
        self.engine = DownloaderEngine(
            Config(1, "hash", self.root / "session", self.db.path, self.root / "downloads"),
            self.db, lambda event, data: self.events.append((event, data)),
        )

    def message(self, message_id=42, text="Summer trip", reply=None):
        return SimpleNamespace(
            id=message_id, date=datetime.now(timezone.utc), message=text, is_reply=reply is not None,
            get_reply_message=AsyncMock(return_value=reply), photo=None,
            document=SimpleNamespace(id=message_id + 100, attributes=[], mime_type="video/mp4", size=8192),
            file=SimpleNamespace(name="clip.mp4", ext=".mp4", size=8192, mime_type="video/mp4"),
        )

    async def discover(self, message=None):
        await self.engine._record_message(self.db.channels()[0], message or self.message(), 10)
        return self.db.media_for_channel(self.channel_id)[0]

    def row(self, media_id):
        return self.db.media_for_ids(self.channel_id, [media_id])[0]

    async def switch(self, media_id, mode):
        await self.engine._set_media_filename_mode(self.channel_id, [media_id], mode)

    def complete(self, row, prefix=""):
        output = self.root / "downloads" / "Test" / f"{prefix}clip [{row['message_id']}].mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"saved video")
        self.db.mark_downloading(row["id"])
        self.db.mark_downloaded(row["id"], str(output), 8192)
        return output

    async def test_queued_file_switches_both_ways_offline_and_survives_rescan(self):
        row = await self.discover()
        await self.switch(row["id"], "message")
        changed = self.row(row["id"])
        self.assertEqual("Summer trip.mp4", changed["file_name"])
        self.assertEqual("message", changed["filename_mode"])
        self.assertEqual("queued", changed["status"])
        self.assertIsNone(changed["output_path"])
        await self.discover()
        self.assertEqual("Summer trip.mp4", self.row(row["id"])["file_name"])
        reopened = Database(self.db.path)
        self.assertEqual("message", reopened.media_for_ids(self.channel_id, [row["id"]])[0]["filename_mode"])
        self.assertEqual("telegram", reopened.channels()[0].filename_mode)
        await self.switch(row["id"], "telegram")
        self.assertEqual("clip.mp4", self.row(row["id"])["file_name"])
        self.assertFalse(any(event == "error" for event, _ in self.events))

    async def test_manual_name_works_offline_and_survives_rescan_and_restart(self):
        row = await self.discover()
        await self.switch(row["id"], "message")
        await self.engine._set_media_filename_mode(
            self.channel_id, [row["id"]], "manual", manual_names={row["id"]: "Summer trip edited.mp4"},
        )
        item = self.row(row["id"])
        self.assertEqual("Summer trip edited.mp4", item["file_name"])
        self.assertEqual("manual", item["filename_mode"])
        await self.discover()
        reopened = Database(self.db.path)
        self.assertEqual("Summer trip edited.mp4", reopened.media_for_ids(self.channel_id, [row["id"]])[0]["file_name"])
        await self.switch(row["id"], "telegram")
        self.assertEqual("clip.mp4", self.row(row["id"])["file_name"])
        await self.switch(row["id"], "message")
        self.assertEqual("Summer trip.mp4", self.row(row["id"])["file_name"])
        self.assertFalse(any(event == "error" for event, _ in self.events))

    async def test_manual_name_renames_completed_file_and_preserves_sequence(self):
        row = await self.discover()
        old = self.complete(row, "001 ")
        await self.engine._set_media_filename_mode(
            self.channel_id, [row["id"]], "manual", manual_names={row["id"]: "Edited clip"},
        )
        new = old.with_name("001 Edited clip [42].mp4")
        self.assertFalse(old.exists())
        self.assertEqual(b"saved video", new.read_bytes())
        self.assertEqual("downloaded", self.row(row["id"])["status"])
        self.assertEqual("manual", self.row(row["id"])["filename_mode"])
        await self.switch(row["id"], "telegram")
        self.assertEqual(b"saved video", old.read_bytes())

    async def test_invalid_manual_name_and_collision_leave_existing_file_unchanged(self):
        row = await self.discover()
        old = self.complete(row)
        collision = old.with_name("Edited clip [42].mp4")
        collision.write_bytes(b"other file")
        for name in ("", "../outside.mp4", "Edited clip.mp4"):
            await self.engine._set_media_filename_mode(
                self.channel_id, [row["id"]], "manual", manual_names={row["id"]: name},
            )
            self.assertEqual(b"saved video", old.read_bytes())
            self.assertEqual(b"other file", collision.read_bytes())
            self.assertIsNone(self.row(row["id"])["filename_mode"])

    async def test_completed_file_is_renamed_with_sequence_prefix_and_without_redownload(self):
        row = await self.discover()
        old = self.complete(row, "001 ")
        await self.switch(row["id"], "message")
        new = old.with_name("001 Summer trip [42].mp4")
        self.assertFalse(old.exists())
        self.assertEqual(b"saved video", new.read_bytes())
        changed = self.row(row["id"])
        self.assertEqual("downloaded", changed["status"])
        self.assertEqual(8192, changed["bytes_downloaded"])
        self.assertEqual(str(new), changed["output_path"])
        await self.switch(row["id"], "telegram")
        self.assertEqual(b"saved video", old.read_bytes())
        self.assertFalse(new.exists())

    async def test_partial_file_is_renamed_and_resumes_at_existing_offset(self):
        row = await self.discover()
        output = self.root / "downloads" / "Test" / "001 clip [42].mp4"
        output.parent.mkdir(parents=True)
        partial = Path(f"{output}.part")
        partial.write_bytes(b"x" * 4096)
        self.db.mark_downloading(row["id"])
        self.db.set_output_path(row["id"], str(output))
        self.db.update_progress(row["id"], 4096)
        self.db.mark_failed(row["id"], "Connection lost")
        await self.switch(row["id"], "message")
        new_output = output.with_name("001 Summer trip [42].mp4")
        new_partial = Path(f"{new_output}.part")
        self.assertFalse(partial.exists())
        self.assertEqual(b"x" * 4096, new_partial.read_bytes())
        self.assertEqual("failed", self.row(row["id"])["status"])
        self.assertEqual(4096, self.row(row["id"])["bytes_downloaded"])
        offsets = []

        async def chunks(*_args, offset, **_kwargs):
            offsets.append(offset)
            yield b"y" * (8192 - offset)

        self.engine._client = SimpleNamespace(get_messages=AsyncMock(return_value=self.message()), iter_download=chunks)
        self.engine._wait_until_channel_ready = AsyncMock()
        self.db.retry([row["id"]])
        await self.engine._download_one(self.db.claim_next_queued())
        self.assertEqual([4096], offsets)
        self.assertEqual(b"x" * 4096 + b"y" * 4096, new_output.read_bytes())

    async def test_active_switch_preserves_partial_data_and_download_worker_continues(self):
        row = await self.discover()
        partial_written = asyncio.Event()
        finished = asyncio.Event()
        blocker = asyncio.Event()
        offsets = []

        async def chunks(*_args, offset, **_kwargs):
            offsets.append(offset)
            if not offset:
                yield b"x" * 4096
                partial_written.set()
                await blocker.wait()
            yield b"y" * 4096

        def callback(event, data):
            self.events.append((event, data))
            if event == "download_finished":
                finished.set()

        self.engine.callback = callback
        self.engine._client = SimpleNamespace(get_messages=AsyncMock(return_value=self.message()), iter_download=chunks)
        self.engine._running = asyncio.Event()
        self.engine._running.set()
        self.engine._wait_until_channel_ready = AsyncMock()
        worker = asyncio.create_task(self.engine._download_loop())
        try:
            await asyncio.wait_for(partial_written.wait(), 2)
            await self.switch(row["id"], "message")
            await asyncio.wait_for(finished.wait(), 2)
            output = self.root / "downloads" / "Test" / "Summer trip [42].mp4"
            self.assertEqual(b"x" * 4096 + b"y" * 4096, output.read_bytes())
            self.assertEqual([0, 4096], offsets)
            self.assertFalse(worker.done())
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_name_collision_keeps_both_files_and_original_mode(self):
        row = await self.discover()
        old = self.complete(row)
        collision = old.with_name("Summer trip [42].mp4")
        collision.write_bytes(b"other file")
        await self.switch(row["id"], "message")
        self.assertEqual(b"saved video", old.read_bytes())
        self.assertEqual(b"other file", collision.read_bytes())
        self.assertIsNone(self.row(row["id"])["filename_mode"])
        self.assertEqual(str(old), self.row(row["id"])["output_path"])
        self.assertTrue(any(event == "error" for event, _ in self.events))

    async def test_database_failure_rolls_back_local_rename(self):
        row = await self.discover()
        old = self.complete(row)
        with patch.object(self.db, "set_media_filename", side_effect=RuntimeError("Database unavailable")):
            await self.switch(row["id"], "message")
        self.assertEqual(b"saved video", old.read_bytes())
        self.assertFalse(old.with_name("Summer trip [42].mp4").exists())
        self.assertEqual("clip.mp4", self.row(row["id"])["file_name"])

    async def test_uncached_reply_is_fetched_and_then_switches_offline(self):
        message = self.message(text="", reply=SimpleNamespace(message="Reply title"))
        row = await self.discover(message)
        self.engine._authenticated = True
        self.engine._client = SimpleNamespace(get_messages=AsyncMock(return_value=message))
        await self.switch(row["id"], "reply")
        self.assertEqual("Reply title.mp4", self.row(row["id"])["file_name"])
        message.get_reply_message.assert_awaited_once()
        self.engine._authenticated = False
        await self.switch(row["id"], "telegram")
        await self.switch(row["id"], "reply")
        self.engine._client.get_messages.assert_awaited_once()

    async def test_message_and_reply_modes_use_independent_text_sources(self):
        message = self.message(text="Attached title", reply=SimpleNamespace(message="Replied title"))
        row = await self.discover(message)
        await self.switch(row["id"], "message")
        self.assertEqual("Attached title.mp4", self.row(row["id"])["file_name"])
        message.get_reply_message.assert_not_awaited()
        self.engine._authenticated = True
        self.engine._client = SimpleNamespace(get_messages=AsyncMock(return_value=message))
        await self.switch(row["id"], "reply")
        self.assertEqual("Replied title.mp4", self.row(row["id"])["file_name"])
        message.get_reply_message.assert_awaited_once()
        await self.discover(message)
        self.assertEqual("Replied title.mp4", self.row(row["id"])["file_name"])
        self.engine._authenticated = False
        await self.switch(row["id"], "message")
        self.assertEqual("Attached title.mp4", self.row(row["id"])["file_name"])
        await self.switch(row["id"], "telegram")
        self.assertEqual("clip.mp4", self.row(row["id"])["file_name"])

    async def test_empty_message_mode_uses_original_without_using_reply(self):
        message = self.message(text="", reply=SimpleNamespace(message="Replied title"))
        row = await self.discover(message)
        await self.switch(row["id"], "message")
        self.assertEqual("clip.mp4", self.row(row["id"])["file_name"])
        self.assertEqual("message", self.row(row["id"])["filename_mode"])
        message.get_reply_message.assert_not_awaited()

    async def test_reply_mode_without_reply_uses_original_instead_of_caption(self):
        row = await self.discover(self.message(text="Attached title"))
        await self.switch(row["id"], "reply")
        self.assertEqual("clip.mp4", self.row(row["id"])["file_name"])
        self.assertEqual("reply", self.row(row["id"])["filename_mode"])

    async def test_old_combined_message_cache_is_invalidated_on_migration(self):
        row = await self.discover()
        with self.db.connect() as conn:
            conn.execute("UPDATE media SET message_file_name='Legacy reply.mp4'")
            conn.execute("ALTER TABLE media DROP COLUMN reply_file_name")
        migrated = Database(self.db.path)
        item = migrated.media_for_ids(self.channel_id, [row["id"]])[0]
        self.assertIsNone(item["message_file_name"])
        self.assertIsNone(item["reply_file_name"])
        self.assertEqual("clip.mp4", item["telegram_file_name"])

    async def test_enabling_numbering_moves_unnumbered_partial_and_updates_list(self):
        row = await self.discover()
        output = self.root / "downloads" / "Test" / "clip [42].mp4"
        output.parent.mkdir(parents=True)
        partial = Path(f"{output}.part")
        partial.write_bytes(b"x" * 4096)
        self.db.mark_downloading(row["id"])
        self.db.set_output_path(row["id"], str(output))
        self.db.update_progress(row["id"], 4096)
        self.db.mark_failed(row["id"], "Connection lost")
        self.db.add_channel("test", 123, "Test", "Test", 0, 0, sequence_prefix=True, paused=True)
        self.db.finalize_attachment_sequence(self.channel_id)
        self.db.set_scan_complete(self.channel_id)
        page = self.db.failed_media_for_channel(self.channel_id)[0]
        self.assertEqual("1 clip [42].mp4", page["display_file_name"])
        self.assertEqual("telegram", page["effective_filename_mode"])
        self.db.set_channel_paused(self.channel_id, False)
        self.db.retry([row["id"]])
        offsets = []

        async def chunks(*_args, offset, **_kwargs):
            offsets.append(offset)
            yield b"y" * (8192 - offset)

        self.engine._client = SimpleNamespace(get_messages=AsyncMock(return_value=self.message()), iter_download=chunks)
        self.engine._wait_until_channel_ready = AsyncMock()
        await self.engine._download_one(self.db.claim_next_queued())
        numbered = output.with_name("1 clip [42].mp4")
        self.assertFalse(partial.exists())
        self.assertEqual([4096], offsets)
        self.assertEqual(b"x" * 4096 + b"y" * 4096, numbered.read_bytes())
        self.assertEqual(str(numbered), self.row(row["id"])["output_path"])
        self.assertEqual("1 clip [42].mp4", self.db.downloaded_media_for_channel(self.channel_id)[0]["display_file_name"])

    async def test_adding_channel_starts_paused_and_scans_numbering_before_resume(self):
        message = self.message()

        class Client:
            async def get_entity(self, _identifier):
                return SimpleNamespace(id=123, username="test", title="Test")

            async def iter_messages(self, *_args, **_kwargs):
                yield message

            async def get_messages(self, *_args, **_kwargs):
                return message

            async def iter_download(self, *_args, **_kwargs):
                yield b"video___" * 1024

        self.engine._client = Client()
        with patch.object(self.engine, "_track_authenticated_task", side_effect=lambda coro: coro.close()):
            await self.engine._add_channel("test", "", 0, 0, sequence_prefix=True)
        channel = self.db.channels()[0]
        self.assertTrue(channel.paused)
        self.assertTrue(channel.sequence_prefix)
        self.assertIn(channel.id, self.engine._paused_channel_ids)
        await self.engine._scan_channel(channel, full=True)
        self.assertTrue(self.db.channels()[0].scan_complete)
        self.assertIsNone(self.db.claim_next_queued())
        self.assertEqual("1 clip [42].mp4", self.db.queued_media_for_channel(channel.id)[0]["display_file_name"])
        self.engine._running = asyncio.Event()
        self.engine._running.set()
        self.db.set_channel_paused(channel.id, False)
        self.engine._paused_channel_ids.discard(channel.id)
        await self.engine._download_one(self.db.claim_next_queued())
        output = self.root / "downloads" / channel.folder / "1 clip [42].mp4"
        self.assertEqual(b"video___" * 1024, output.read_bytes())

    async def test_numbering_resumes_legacy_partial_without_a_remembered_path(self):
        row = await self.discover()
        legacy = self.root / "downloads" / "Test" / "clip [42].mp4.part"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"x" * 4096)
        self.db.update_progress(row["id"], 4096)
        self.db.add_channel("test", 123, "Test", "Test", 0, 0, sequence_prefix=True)
        self.db.finalize_attachment_sequence(self.channel_id)
        self.db.set_scan_complete(self.channel_id)
        offsets = []

        async def chunks(*_args, offset, **_kwargs):
            offsets.append(offset)
            yield b"y" * (8192 - offset)

        self.engine._client = SimpleNamespace(get_messages=AsyncMock(return_value=self.message()), iter_download=chunks)
        self.engine._wait_until_channel_ready = AsyncMock()
        await self.engine._download_one(self.db.claim_next_queued())
        self.assertFalse(legacy.exists())
        self.assertEqual([4096], offsets)
        self.assertEqual(b"x" * 4096 + b"y" * 4096, legacy.with_name("1 clip [42].mp4").read_bytes())

    async def test_uncached_legacy_file_requires_sign_in_then_uses_telegram_fallback(self):
        channel = self.db.channels()[0]
        self.db.add_media(
            channel=channel, message_id=42, document_id=142, file_name="Legacy.mp4",
            size_bytes=8192, duration_seconds=0, message_date="", priority=10,
        )
        row = self.db.media_for_channel(self.channel_id)[0]
        await self.switch(row["id"], "message")
        self.assertEqual("Legacy.mp4", self.row(row["id"])["file_name"])
        self.assertTrue(any(event == "error" for event, _ in self.events))
        self.engine._authenticated = True
        self.engine._client = SimpleNamespace(get_messages=AsyncMock(return_value=self.message(text="")))
        await self.switch(row["id"], "message")
        self.assertEqual("clip.mp4", self.row(row["id"])["file_name"])

    async def test_bulk_selection_leaves_other_channels_and_statuses_unchanged(self):
        first = await self.discover()
        second_message = self.message(43, "Another trip")
        await self.engine._record_message(self.db.channels()[0], second_message, 10)
        second = next(row for row in self.db.media_for_channel(self.channel_id) if row["message_id"] == 43)
        self.db.remove_media([second["id"]])
        other_id = self.db.add_channel("other", 456, "Other", "Other", 0, 0)
        other = self.db.channel_by_telegram_id(456)
        await self.engine._record_message(other, self.message(), 10)
        other_row = self.db.media_for_channel(other_id)[0]
        await self.engine._set_media_filename_mode(
            self.channel_id, [first["id"], second["id"], other_row["id"]], "message",
        )
        self.assertEqual("Summer trip.mp4", self.row(first["id"])["file_name"])
        self.assertEqual("Another trip.mp4", self.row(second["id"])["file_name"])
        self.assertEqual("removed", self.row(second["id"])["status"])
        self.assertEqual("clip.mp4", self.db.media_for_channel(other_id)[0]["file_name"])
