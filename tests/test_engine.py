from __future__ import annotations

import sys
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tg_downloader"))

from config import Config
from database import Database
from engine import BandwidthLimiter, DownloaderEngine


class BandwidthLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_unlimited_rate_does_not_sleep(self) -> None:
        limiter = BandwidthLimiter(0)
        with patch("engine.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await limiter.wait_for(1024)
            sleep.assert_not_awaited()

    async def test_limited_rate_schedules_bytes_at_requested_rate(self) -> None:
        limiter = BandwidthLimiter(100)
        with patch("engine.time.monotonic", return_value=10.0), \
             patch("engine.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await limiter.wait_for(100)
            sleep.assert_awaited_once_with(1.0)

    def test_limited_chunk_size_is_small_enough_for_smooth_updates(self) -> None:
        self.assertEqual(128 * 1024, BandwidthLimiter(0).preferred_chunk_size())
        self.assertEqual(4096, BandwidthLimiter(10 * 1024).preferred_chunk_size())
        self.assertEqual(128 * 1024, BandwidthLimiter(10 * 1024 * 1024).preferred_chunk_size())

    def test_custom_speed_produces_aligned_throttling_chunk(self) -> None:
        chunk_size = BandwidthLimiter(333 * 1024).preferred_chunk_size()
        self.assertEqual(0, chunk_size % BandwidthLimiter.TELEGRAM_ALIGNMENT)

    def test_partial_download_resume_offset_is_rewound_to_alignment(self) -> None:
        self.assertEqual(8192, BandwidthLimiter.aligned_resume_offset(10_000))
        self.assertEqual(8192, BandwidthLimiter.aligned_resume_offset(8192))


class ClosedLoopControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = object.__new__(DownloaderEngine)
        self.engine._loop = asyncio.new_event_loop()
        self.engine._loop.close()
        self.engine._commands = Mock()
        self.engine._running = Mock()
        self.engine._stopping = False

    def test_controls_do_not_raise_after_worker_loop_closes(self) -> None:
        self.assertFalse(self.engine.command("rescan", channel_id=1))
        self.assertFalse(self.engine.pause())
        self.assertFalse(self.engine.resume())
        self.engine.stop()
        self.assertTrue(self.engine._stopping)


class RemoveChannelDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_removal_requeues_cancelled_active_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "test.sqlite3")
            channel_id = db.add_channel("test", 123, "Test", "Test", 0, 0)
            channel = db.channels()[0]
            db.add_media(
                channel=channel, message_id=1, document_id=1, file_name="song.mp3",
                size_bytes=1000, duration_seconds=10, message_date="2026-01-01T00:00:00+00:00",
                priority=10,
            )
            row = db.claim_next_queued()
            config = Config(1, "hash", root / "session", db.path, root / "downloads")
            engine = DownloaderEngine(config, db, lambda *_args: None)
            engine._running = asyncio.Event()
            engine._running.set()

            class BlockingClient:
                async def get_messages(self, *_args, **_kwargs):
                    await asyncio.Event().wait()

            engine._client = BlockingClient()
            task = asyncio.create_task(engine._download_one(row))
            await asyncio.sleep(0)
            engine._removed_channel_media_id = int(row["id"])
            task.cancel()
            await task

            self.assertEqual(1, db.queue_count(channel_id))
            self.assertEqual([], db.failed_media_for_channel(channel_id))


if __name__ == "__main__":
    unittest.main()
