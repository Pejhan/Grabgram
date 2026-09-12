from __future__ import annotations

import sys
import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tg_downloader"))

from config import Config
from database import Database
from engine import BandwidthLimiter, DownloaderEngine


class MediaDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_audio_video_photo_and_regular_documents(self) -> None:
        video_attribute = type("DocumentAttributeVideo", (), {})()
        video_attribute.duration = 90
        cases = (
            (SimpleNamespace(
                id=1, document=SimpleNamespace(
                    id=101, mime_type="video/mp4", size=2_000,
                    attributes=[video_attribute],
                ), photo=None,
                file=SimpleNamespace(name="clip.mp4", ext=".mp4", size=2_000, mime_type="video/mp4"),
            ), "clip.mp4", 90),
            (SimpleNamespace(
                id=2, document=None, photo=SimpleNamespace(id=102),
                file=SimpleNamespace(name=None, ext=".jpg", size=3_000, mime_type="image/jpeg"),
            ), "telegram-2.jpg", 0),
            (SimpleNamespace(
                id=3, document=SimpleNamespace(id=103, mime_type="application/pdf", size=4_000, attributes=[]),
                photo=None,
                file=SimpleNamespace(name="notes.pdf", ext=".pdf", size=4_000, mime_type="application/pdf"),
            ), "notes.pdf", 0),
        )
        for message, expected_name, expected_duration in cases:
            info = DownloaderEngine._media_info(message)
            self.assertIsNotNone(info)
            self.assertEqual(expected_name, info.filename)
            self.assertEqual(expected_duration, info.duration)

    async def test_channel_type_scan_is_count_sorted_across_media_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "test.sqlite3")
            events = []
            engine = DownloaderEngine(
                Config(1, "hash", root / "session", db.path, root / "downloads"),
                db, lambda event, data: events.append((event, data)),
            )

            def message(message_id: int, extension: str):
                is_photo = extension == ".jpg"
                return SimpleNamespace(
                    id=message_id, date=datetime.now(timezone.utc),
                    document=None if is_photo else SimpleNamespace(
                        id=message_id + 100, mime_type="application/octet-stream",
                        size=2_000, attributes=[],
                    ),
                    photo=SimpleNamespace(id=message_id + 100) if is_photo else None,
                    file=SimpleNamespace(
                        name=f"file-{message_id}{extension}", ext=extension,
                        size=2_000, mime_type="image/jpeg" if is_photo else "application/octet-stream",
                    ),
                )

            class Client:
                async def get_entity(self, _identifier):
                    return SimpleNamespace(id=123, username="sample_channel")

                async def iter_messages(self, _entity):
                    for index, extension in enumerate((".mp4", ".jpg", ".mp4", ".pdf", ".jpg", ".mp4"), 1):
                        yield message(index, extension)

            engine._client = Client()
            await engine._inspect_channel_types("sample", "request-1")
            progress = [
                data["files_fetched"] for event, data in events
                if event == "channel_types_progress"
            ]
            self.assertEqual(0, progress[0])
            self.assertIn(1, progress)
            payload = events[-1][1]
            self.assertEqual("sample_channel", payload["username"])
            self.assertEqual(
                [(".mp4", 3), (".jpg", 2), (".pdf", 1)],
                [(item["extension"], item["count"]) for item in payload["file_types"]],
            )

    async def test_channel_folder_accepts_only_resolved_name_and_underscore_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "test.sqlite3")
            events = []
            engine = DownloaderEngine(
                Config(1, "hash", root / "session", db.path, root / "downloads"),
                db, lambda event, data: events.append((event, data)),
            )

            class Client:
                async def get_entity(self, _identifier):
                    return SimpleNamespace(id=123, username="sample_channel", title="Sample")

            engine._client = Client()
            await engine._add_channel("sample", "unrelated", 0, 0)
            self.assertFalse(db.channels())
            self.assertIn("sample_channel_", events[-1][1]["error"])

            with patch.object(
                engine, "_track_authenticated_task",
                side_effect=lambda coroutine: coroutine.close(),
            ):
                await engine._add_channel("sample", "sample_channel_audio", 0, 0)
            self.assertEqual("sample_channel_audio", db.channels()[0].folder)

            with patch.object(
                engine, "_track_authenticated_task",
                side_effect=lambda coroutine: coroutine.close(),
            ):
                await engine._add_channel(
                    "sample", "", 0, 0, folder_suffix="_video",
                )
            self.assertEqual("sample_channel_video", db.channels()[0].folder)


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


class ConnectionStatusTests(unittest.TestCase):
    def test_connection_events_are_deduplicated_and_keep_proxy_context(self) -> None:
        events = []
        engine = object.__new__(DownloaderEngine)
        engine.callback = lambda event, data: events.append((event, data))
        engine._connection_status = None
        engine._mtproto_proxy_enabled = True

        engine._set_connection_status("connecting")
        engine._set_connection_status("connecting")
        engine._set_connection_status("connected")

        self.assertEqual(
            [
                ("connection", {"status": "connecting", "mtproto_proxy": True}),
                ("connection", {"status": "connected", "mtproto_proxy": True}),
            ],
            events,
        )


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.db = Database(root / "test.sqlite3")
        self.events = []
        self.engine = DownloaderEngine(
            Config(1, "hash", root / "session", self.db.path, root / "downloads"),
            self.db, lambda event, data: self.events.append((event, data)),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_phone_request_prompts_for_code(self) -> None:
        client = SimpleNamespace(
            is_user_authorized=AsyncMock(return_value=False),
            send_code_request=AsyncMock(
                return_value=SimpleNamespace(phone_code_hash="code-hash")
            )
        )
        self.engine._client = client
        self.engine._ensure_client_connected = AsyncMock()

        await self.engine._request_sign_in(" +15551234567 ")

        client.send_code_request.assert_awaited_once_with("+15551234567")
        self.assertEqual("+15551234567", self.engine._auth_phone)
        self.assertEqual("code-hash", self.engine._auth_phone_code_hash)
        self.assertEqual(("authentication_code_requested", {}), self.events[-1])

    async def test_code_can_continue_to_two_step_password(self) -> None:
        SessionPasswordNeededError = type("SessionPasswordNeededError", (Exception,), {})
        client = SimpleNamespace(sign_in=AsyncMock(side_effect=SessionPasswordNeededError()))
        self.engine._client = client
        self.engine._auth_phone = "+15551234567"
        self.engine._auth_phone_code_hash = "code-hash"

        await self.engine._submit_sign_in_code("12345")

        client.sign_in.assert_awaited_once_with(
            phone="+15551234567", code="12345", phone_code_hash="code-hash",
        )
        self.assertEqual(("authentication_password_required", {}), self.events[-1])

    async def test_successful_password_activates_session(self) -> None:
        self.engine._client = SimpleNamespace(sign_in=AsyncMock())
        self.engine._activate_authenticated_session = AsyncMock()

        await self.engine._submit_sign_in_password("secret password")

        self.engine._client.sign_in.assert_awaited_once_with(password="secret password")
        self.engine._activate_authenticated_session.assert_awaited_once_with()

    async def test_session_activation_works_without_saved_channels(self) -> None:
        self.engine._client = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(
                    first_name="Sample", last_name="User", username="sample_user",
                )
            ),
            remove_event_handler=Mock(),
        )
        self.engine._running = asyncio.Event()
        self.engine._running.set()

        await self.engine._activate_authenticated_session()

        self.assertTrue(self.engine._authenticated)
        self.assertEqual(
            (
                "authentication",
                {
                    "status": "signed_in", "display_name": "Sample User",
                    "username": "sample_user",
                },
            ),
            self.events[-2],
        )
        await self.engine._deactivate_authenticated_session()
        self.assertFalse(self.engine._authenticated)

    async def test_second_account_requires_sign_out_first(self) -> None:
        client = SimpleNamespace(send_code_request=AsyncMock())
        self.engine._client = client
        self.engine._authenticated = True

        await self.engine._request_sign_in("+15551234567")

        client.send_code_request.assert_not_awaited()
        self.assertEqual("authentication_error", self.events[-1][0])
        self.assertIn("Sign out", self.events[-1][1]["error"])

    async def test_sign_out_invalidates_client_and_returns_to_signed_out_state(self) -> None:
        client = SimpleNamespace(log_out=AsyncMock())
        self.engine._client = client
        self.engine._authenticated = True
        self.engine._deactivate_authenticated_session = AsyncMock()

        await self.engine._sign_out()

        self.engine._deactivate_authenticated_session.assert_awaited_once_with()
        client.log_out.assert_awaited_once_with()
        self.assertIsNone(self.engine._client)
        self.assertEqual(("authentication", {"status": "signed_out"}), self.events[-1])


class ChannelPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_waits_until_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "test.sqlite3")
            channel_id = db.add_channel("test", 123, "Test", "Test", 0, 0)
            config = Config(1, "hash", root / "session", db.path, root / "downloads")
            engine = DownloaderEngine(config, db, lambda *_args: None)
            engine._running = asyncio.Event()
            engine._running.set()
            engine._paused_channel_ids.add(channel_id)

            waiting = asyncio.create_task(engine._wait_until_channel_ready(channel_id))
            await asyncio.sleep(0.02)
            self.assertFalse(waiting.done())
            engine._paused_channel_ids.discard(channel_id)
            await asyncio.wait_for(waiting, timeout=0.3)


class ReaddedChannelFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_readding_cancels_active_row_removed_by_new_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "test.sqlite3")
            channel_id = db.add_channel("test", 123, "Test", "Test", 0, 0)
            channel = db.channels()[0]
            db.add_media(
                channel=channel, message_id=1, document_id=1, file_name="song.mp3",
                size_bytes=1_000, duration_seconds=10, message_date="2026-01-01T00:00:00+00:00",
                priority=10,
            )
            row = db.claim_next_queued()
            config = Config(1, "hash", root / "session", db.path, root / "downloads")
            partial = config.download_root / channel.folder / "song [1].mp3.part"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"partial")
            engine = DownloaderEngine(config, db, lambda *_args: None)
            engine._running = asyncio.Event()
            engine._running.set()

            class Client:
                async def get_entity(self, _identifier):
                    return type("Entity", (), {"id": 123, "title": "Test"})()

                async def get_messages(self, *_args, **_kwargs):
                    await asyncio.Event().wait()

            engine._client = Client()
            task = asyncio.create_task(engine._download_one(row))
            engine._active_download_task = task
            engine._active_media_id = int(row["id"])
            engine._active_channel_id = channel_id
            await asyncio.sleep(0)

            await engine._add_channel("test", "Test", 0, 2_000)
            await task
            await asyncio.sleep(0)

            self.assertFalse(db.media_exists(int(row["id"])))
            self.assertFalse(partial.exists())


class RemoveChannelDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_removal_discards_only_cancelled_active_download(self) -> None:
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
            channel_folder = config.download_root / channel.folder
            channel_folder.mkdir(parents=True)
            partial = channel_folder / "song [1].mp3.part"
            partial.write_bytes(b"x" * 400)
            db.update_progress(int(row["id"]), 400)

            db.add_media(
                channel=channel, message_id=2, document_id=2, file_name="complete.mp3",
                size_bytes=200, duration_seconds=10, message_date="2026-01-02T00:00:00+00:00",
                priority=10,
            )
            completed_row = next(item for item in db.media_for_channel(channel_id) if item["message_id"] == 2)
            completed = channel_folder / "complete [2].mp3"
            completed.write_bytes(b"y" * 200)
            db.mark_downloading(int(completed_row["id"]))
            db.mark_downloaded(int(completed_row["id"]), str(completed), 200)

            engine = DownloaderEngine(config, db, lambda *_args: None)
            engine._running = asyncio.Event()
            engine._running.set()

            class BlockingClient:
                async def get_messages(self, *_args, **_kwargs):
                    await asyncio.Event().wait()

            engine._client = BlockingClient()
            task = asyncio.create_task(engine._download_one(row))
            await asyncio.sleep(0)
            db.set_channel_enabled(channel_id, False)
            engine._removed_channel_media_id = int(row["id"])
            task.cancel()
            await task

            self.assertEqual(1, db.queue_count(channel_id))
            self.assertEqual([], db.failed_media_for_channel(channel_id))
            active_row = next(item for item in db.media_for_channel(channel_id) if item["message_id"] == 1)
            completed_row = next(item for item in db.media_for_channel(channel_id) if item["message_id"] == 2)
            self.assertEqual("queued", active_row["status"])
            self.assertEqual(0, active_row["bytes_downloaded"])
            self.assertFalse(partial.exists())
            self.assertEqual("downloaded", completed_row["status"])
            self.assertTrue(completed.exists())
            self.assertFalse(db.channels()[0].enabled)


if __name__ == "__main__":
    unittest.main()
