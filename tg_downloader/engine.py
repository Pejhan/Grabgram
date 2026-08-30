from __future__ import annotations

import asyncio
import mimetypes
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .config import Config
    from .database import Channel, Database
    from .media import safe_filename, unique_output_path
    from .proxy import load_mtproto_proxy, telethon_client_options
except ImportError:  # Supports running the module files directly as well.
    from config import Config
    from database import Channel, Database
    from media import safe_filename, unique_output_path
    from proxy import load_mtproto_proxy, telethon_client_options


EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class MediaInfo:
    media_id: int
    filename: str
    size: int
    duration: int


class BandwidthLimiter:
    """A simple coroutine-friendly limiter shared by the single download worker."""

    TELEGRAM_ALIGNMENT = 4096

    def __init__(self, bytes_per_second: int = 0):
        self.bytes_per_second = max(0, int(bytes_per_second))
        self._next_slot = 0.0

    def set_rate(self, bytes_per_second: int) -> None:
        self.bytes_per_second = max(0, int(bytes_per_second))
        self._next_slot = 0.0

    async def wait_for(self, byte_count: int) -> None:
        if self.bytes_per_second <= 0 or byte_count <= 0:
            return
        now = time.monotonic()
        self._next_slot = max(now, self._next_slot) + byte_count / self.bytes_per_second
        await asyncio.sleep(max(0.0, self._next_slot - now))

    def preferred_chunk_size(self) -> int:
        """Choose UI/throttling chunks; this is not Telegram's API request limit."""
        if self.bytes_per_second <= 0:
            return 128 * 1024
        target = max(self.TELEGRAM_ALIGNMENT, min(128 * 1024, self.bytes_per_second // 4))
        alignment = self.TELEGRAM_ALIGNMENT
        return max(alignment, ((target + alignment - 1) // alignment) * alignment)

    @classmethod
    def aligned_resume_offset(cls, offset: int) -> int:
        """Rewind an interrupted file to a Telegram-compatible 4 KiB boundary."""
        return max(0, int(offset) - int(offset) % cls.TELEGRAM_ALIGNMENT)


class DownloaderEngine:
    """Owns Telethon and asyncio on a background thread; safe to control from a GUI event loop."""

    def __init__(self, config: Config, db: Database, callback: EventCallback):
        self.config = config
        self.db = db
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._commands: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None
        self._running: asyncio.Event | None = None
        self._stopping = False
        self._client: Any = None
        self._connection_status: str | None = None
        self._mtproto_proxy_enabled = False
        self._active_download_task: asyncio.Task[None] | None = None
        self._active_media_id: int | None = None
        self._active_channel_id: int | None = None
        self._removed_channel_media_id: int | None = None
        self._filtered_channel_media_id: int | None = None
        self._removed_channel_ids: set[int] = set()
        self._paused_channel_ids = {channel.id for channel in db.channels(True) if channel.paused}
        self._limiter = BandwidthLimiter(db.setting_int("speed_limit_bytes_per_second", 0))
        self._avatar_dir = db.path.parent / "avatars"
        self._avatar_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self.db.requeue_interrupted_downloads()
        self._thread = threading.Thread(target=self._thread_main, name="telegram-downloader", daemon=True)
        self._thread.start()

    def _call_loop(self, callback: Callable[[], None]) -> bool:
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(callback)
            return True
        except RuntimeError:
            # The worker may close its event loop between is_closed() and the call.
            return False

    def command(self, name: str, **payload: Any) -> bool:
        commands = self._commands
        if commands is None:
            return False
        return self._call_loop(lambda: commands.put_nowait((name, payload)))

    def pause(self) -> bool:
        running = self._running
        if running and self._call_loop(running.clear):
            self._emit("state", paused=True)
            return True
        return False

    def resume(self) -> bool:
        running = self._running
        if running and self._call_loop(running.set):
            self._emit("state", paused=False)
            return True
        return False

    def stop(self) -> None:
        self._stopping = True
        self.command("stop")

    def _emit(self, event: str, **payload: Any) -> None:
        self.callback(event, payload)

    def _set_connection_status(self, status: str) -> None:
        """Publish transport health only when it actually changes."""
        if status == self._connection_status:
            return
        self._connection_status = status
        self._emit(
            "connection", status=status,
            mtproto_proxy=self._mtproto_proxy_enabled,
        )

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self._emit("fatal", error=f"{type(exc).__name__}: {exc}")
        finally:
            # Do not leave UI controls pointing at the loop closed by asyncio.run().
            self._loop = None
            self._commands = None
            self._running = None
            self._client = None

    async def _main(self) -> None:
        try:
            from telethon import TelegramClient, connection, events
        except ImportError:
            self._emit("fatal", error="Telethon is not installed. Run: python -m pip install -r requirements.txt")
            return

        self._loop = asyncio.get_running_loop()
        self._commands = asyncio.Queue()
        self._running = asyncio.Event()
        self._running.set()
        mtproxy = load_mtproto_proxy(self.db)
        self._mtproto_proxy_enabled = mtproxy.enabled
        self._client = TelegramClient(
            str(self.config.session_file), self.config.api_id, self.config.api_hash,
            **telethon_client_options(mtproxy, connection),
        )
        self._set_connection_status("connecting")
        await self._client.connect()
        if not await self._client.is_user_authorized():
            self._emit("fatal", error="Telegram session is not authorized. Close the app and run: python -m tg_downloader.auth")
            await self._client.disconnect()
            return

        self._client.add_event_handler(self._on_new_message, events.NewMessage())
        self._set_connection_status("connected")
        self._emit("state", paused=False)

        scans = [asyncio.create_task(self._scan_channel(channel, full=True)) for channel in self.db.channels(True)]
        avatars = [asyncio.create_task(self._cache_avatar(channel)) for channel in self.db.channels(True)]
        tasks = scans + avatars + [
            asyncio.create_task(self._download_loop()),
            asyncio.create_task(self._command_loop()),
            asyncio.create_task(self._periodic_scan()),
            asyncio.create_task(self._connection_monitor()),
        ]
        try:
            while not self._stopping:
                await asyncio.sleep(0.25)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._client.disconnect()
            self._set_connection_status("disconnected")

    async def _connection_monitor(self) -> None:
        """Reflect Telethon reconnects without confusing operation failures with transport health."""
        while True:
            await asyncio.sleep(1)
            client = self._client
            try:
                connected = bool(client is not None and client.is_connected())
            except Exception:
                connected = False
            self._set_connection_status("connected" if connected else "connecting")

    async def _command_loop(self) -> None:
        assert self._commands is not None
        while True:
            name, data = await self._commands.get()
            if name == "stop":
                self._stopping = True
            elif name == "add_channel":
                await self._add_channel(**data)
            elif name == "inspect_channel_types":
                asyncio.create_task(self._inspect_channel_types(**data))
            elif name == "rescan":
                channel = next((c for c in self.db.channels() if c.id == data["channel_id"]), None)
                if channel:
                    asyncio.create_task(self._scan_channel(channel, full=False))
            elif name == "set_speed_limit":
                bytes_per_second = max(0, int(data["bytes_per_second"]))
                self._limiter.set_rate(bytes_per_second)
                self.db.set_setting_int("speed_limit_bytes_per_second", bytes_per_second)
                self._emit("state", speed_limit_bytes_per_second=bytes_per_second)
            elif name == "set_channel_paused":
                channel_id = int(data["channel_id"])
                paused = bool(data["paused"])
                self.db.set_channel_paused(channel_id, paused)
                if paused:
                    self._paused_channel_ids.add(channel_id)
                else:
                    self._paused_channel_ids.discard(channel_id)
                self._emit("channel_pause_changed", channel_id=channel_id, paused=paused)
            elif name == "remove_channel":
                channel_id = int(data["channel_id"])
                self.db.set_channel_enabled(channel_id, False)
                self._removed_channel_ids.add(channel_id)
                self._paused_channel_ids.discard(channel_id)
                task = self._active_download_task
                if task is not None and not task.done() and self._active_channel_id == channel_id:
                    self._removed_channel_media_id = self._active_media_id
                    task.cancel()
                self._emit("channel_removed", channel_id=channel_id)

    async def _wait_until_channel_ready(self, channel_id: int) -> None:
        """Wait for both the global and channel-specific download controls."""
        assert self._running is not None
        while True:
            await self._running.wait()
            if channel_id not in self._paused_channel_ids:
                return
            await asyncio.sleep(0.1)

    async def _add_channel(self, identifier: str, folder: str, min_duration_seconds: int,
                           min_size_bytes: int, media_types: tuple[str, ...] = ()) -> None:
        try:
            entity = await self._client.get_entity(identifier)
            title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(entity.id)
            folder = folder.strip() or str(getattr(entity, "username", None) or entity.id)
            self._folder_path(folder)
            channel_id = self.db.add_channel(
                identifier, int(entity.id), title, folder, min_duration_seconds, min_size_bytes,
                media_types,
            )
            task = self._active_download_task
            active_media_id = self._active_media_id
            if (
                task is not None and not task.done()
                and self._active_channel_id == channel_id
                and active_media_id is not None
                and not self.db.media_exists(active_media_id)
            ):
                self._filtered_channel_media_id = active_media_id
                task.cancel()
            self._removed_channel_ids.discard(channel_id)
            self._paused_channel_ids.discard(channel_id)
            channel = next(c for c in self.db.channels() if c.id == channel_id)
            self._emit("channel_added", channel_id=channel_id)
            asyncio.create_task(self._scan_channel(channel, full=True))
            asyncio.create_task(self._cache_avatar(channel))
        except Exception as exc:
            self._emit("error", error=f"Could not add {identifier}: {type(exc).__name__}: {exc}")

    async def _inspect_channel_types(self, identifier: str, request_id: str) -> None:
        try:
            entity = await self._client.get_entity(identifier)
            counts: Counter[str] = Counter()
            async for message in self._client.iter_messages(entity):
                info = self._media_info(message)
                if info is not None:
                    counts[Path(info.filename).suffix.lower() or ".file"] += 1
                await asyncio.sleep(0)
            file_types = [
                {"extension": extension, "count": count}
                for extension, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ]
            self._emit(
                "channel_types", request_id=request_id,
                username=str(getattr(entity, "username", None) or ""),
                file_types=file_types,
            )
        except Exception as exc:
            self._emit(
                "channel_types", request_id=request_id, file_types=[],
                error=f"Could not inspect channel: {type(exc).__name__}: {exc}",
            )

    def _folder_path(self, folder: str) -> Path:
        root = self.config.download_root.resolve()
        target = (root / folder).resolve()
        if target == root or not target.is_relative_to(root):
            raise ValueError("Channel folder must be a relative folder inside the configured download root")
        return target

    async def _cache_avatar(self, channel: Channel) -> None:
        if channel.telegram_id is None:
            return
        target = self._avatar_dir / f"{channel.telegram_id}.jpg"
        if target.exists():
            self._emit("avatar_ready", channel_id=channel.id, path=str(target))
            return
        try:
            entity = await self._client.get_entity(channel.identifier)
            saved = await self._client.download_profile_photo(entity, file=str(target))
            if saved:
                self._emit("avatar_ready", channel_id=channel.id, path=str(saved))
        except asyncio.CancelledError:
            raise
        except Exception:
            # An absent or inaccessible channel image should never interrupt downloads.
            return

    @staticmethod
    def _media_info(message: Any) -> MediaInfo | None:
        document = getattr(message, "document", None)
        photo = getattr(message, "photo", None)
        media = document or photo
        if media is None:
            return None
        telegram_file = getattr(message, "file", None)
        duration = 0
        filename = str(getattr(telegram_file, "name", "") or "")
        performer = ""
        title = ""
        for attr in getattr(document, "attributes", []) if document is not None else []:
            cls = type(attr).__name__
            if cls == "DocumentAttributeAudio":
                duration = int(getattr(attr, "duration", 0) or 0)
                performer = getattr(attr, "performer", "") or ""
                title = getattr(attr, "title", "") or ""
            elif cls == "DocumentAttributeVideo":
                duration = int(getattr(attr, "duration", 0) or 0)
            elif cls == "DocumentAttributeFilename":
                filename = getattr(attr, "file_name", "") or ""
        mime_type = str(
            getattr(telegram_file, "mime_type", "")
            or getattr(document, "mime_type", "")
            or ("image/jpeg" if photo is not None else "")
        )
        extension = str(getattr(telegram_file, "ext", "") or "")
        if not extension.startswith("."):
            extension = f".{extension}" if extension else ""
        extension = extension.lower() or mimetypes.guess_extension(mime_type) or ".file"
        if not filename and (performer or title):
            filename = f"{performer} - {title}".strip(" -") + extension
        return MediaInfo(
            media_id=int(getattr(media, "id", 0) or 0),
            filename=safe_filename(filename, message.id, extension),
            size=int(
                getattr(telegram_file, "size", 0)
                or getattr(document, "size", 0)
                or 0
            ),
            duration=duration,
        )

    async def _record_message(self, channel: Channel, message: Any, priority: int) -> bool:
        self.db.note_seen(channel.id, int(message.id))
        info = self._media_info(message)
        if not info:
            return False
        date = message.date
        if date and date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        added = self.db.add_media(
            channel=channel, message_id=int(message.id), document_id=info.media_id,
            file_name=info.filename, size_bytes=info.size, duration_seconds=info.duration,
            message_date=date.isoformat() if date else "", priority=priority,
        )
        if added:
            self._emit("queue_changed", channel_id=channel.id)
        return added

    async def _scan_channel(self, channel: Channel, full: bool) -> None:
        try:
            self._emit("scan", channel_id=channel.id, active=True)
            latest = next((c for c in self.db.channels() if c.id == channel.id), channel)
            kwargs = {} if full else {"min_id": latest.last_seen_message_id}
            async for message in self._client.iter_messages(channel.identifier, **kwargs):
                if channel.id in self._removed_channel_ids:
                    break
                await self._record_message(channel, message, priority=10 if full else 0)
                await asyncio.sleep(0)
            if full:
                self.db.set_scan_complete(channel.id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._emit("error", error=f"Scan failed for {channel.title}: {type(exc).__name__}: {exc}")
        finally:
            self._emit("scan", channel_id=channel.id, active=False)

    async def _periodic_scan(self) -> None:
        while True:
            await asyncio.sleep(self.config.scan_interval_seconds)
            for channel in self.db.channels(True):
                await self._scan_channel(channel, full=False)

    async def _on_new_message(self, event: Any) -> None:
        chat = await event.get_chat()
        telegram_id = getattr(chat, "id", None)
        if telegram_id is None:
            return
        channel = self.db.channel_by_telegram_id(int(telegram_id))
        if channel and channel.enabled:
            # Live arrivals always precede historical backlog (but do not abort a file in progress).
            await self._record_message(channel, event.message, priority=0)

    async def _download_loop(self) -> None:
        while True:
            assert self._running is not None
            await self._running.wait()
            row = self.db.claim_next_queued()
            if row is None:
                await asyncio.sleep(0.5)
                continue
            self._active_media_id = int(row["id"])
            self._active_channel_id = int(row["channel_id"])
            self._active_download_task = asyncio.create_task(self._download_one(row))
            try:
                await self._active_download_task
            finally:
                self._active_download_task = None
                self._active_media_id = None
                self._active_channel_id = None
                self._removed_channel_media_id = None
                self._filtered_channel_media_id = None

    async def _download_one(self, row: Any) -> None:
        media_id = int(row["id"])
        channel_id = int(row["channel_id"])
        partial: Path | None = None
        self._emit("download_started", media_id=media_id, channel_id=row["channel_id"])
        try:
            folder = self._folder_path(row["folder"])
            folder.mkdir(parents=True, exist_ok=True)
            output = unique_output_path(folder, row["file_name"], int(row["message_id"]))
            partial = Path(f"{output}.part")
            await self._wait_until_channel_ready(channel_id)
            message = await self._client.get_messages(row["identifier"], ids=int(row["message_id"]))
            downloadable = getattr(message, "document", None) or getattr(message, "photo", None)
            if not message or downloadable is None:
                raise RuntimeError("Telegram message or media is no longer available")
            expected = int(row["size_bytes"])
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > expected:
                partial.unlink()
                offset = 0
            elif offset:
                aligned_offset = BandwidthLimiter.aligned_resume_offset(offset)
                if aligned_offset != offset:
                    with partial.open("r+b") as partial_file:
                        partial_file.truncate(aligned_offset)
                    offset = aligned_offset
            self.db.update_progress(media_id, offset)
            self._emit("progress", media_id=media_id, channel_id=row["channel_id"],
                       downloaded=offset, total=expected)
            last_update = 0.0
            chunk_size = self._limiter.preferred_chunk_size()
            with partial.open("ab") as handle:
                async for chunk in self._client.iter_download(
                    downloadable, offset=offset, chunk_size=chunk_size
                ):
                    await self._wait_until_channel_ready(channel_id)
                    await self._limiter.wait_for(len(chunk))
                    await self._wait_until_channel_ready(channel_id)
                    handle.write(chunk)
                    offset += len(chunk)
                    now = time.monotonic()
                    if now - last_update >= 0.5:
                        handle.flush()
                        self.db.update_progress(media_id, offset)
                        self._emit("progress", media_id=media_id, channel_id=row["channel_id"],
                                   downloaded=offset, total=expected)
                        last_update = now
                handle.flush()
            self.db.update_progress(media_id, offset)
            self._emit("progress", media_id=media_id, channel_id=row["channel_id"],
                       downloaded=offset, total=expected)
            if expected and offset != expected:
                raise RuntimeError(f"Incomplete download: received {offset} of {expected} bytes")
            partial.replace(output)
            self.db.mark_downloaded(media_id, str(output), offset)
            self._emit("download_finished", media_id=media_id, channel_id=row["channel_id"])
        except asyncio.CancelledError:
            if self._removed_channel_media_id == media_id:
                self._discard_partial(partial)
                self.db.reset_active_download(media_id)
                return
            if self._filtered_channel_media_id == media_id:
                self._discard_partial(partial)
                return
            self.db.requeue(media_id)
            raise
        except Exception as exc:
            self.db.mark_failed(media_id, f"{type(exc).__name__}: {exc}")
            self._emit("error", error=f"Download failed: {row['file_name']}: {exc}")

    def _discard_partial(self, partial: Path | None) -> None:
        if partial is None:
            return
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            # A zero-byte fallback still prevents a future resume if Windows
            # temporarily refuses to remove the closed partial file.
            try:
                partial.write_bytes(b"")
            except OSError as exc:
                self._emit("error", error=f"Could not discard partial file: {exc}")
