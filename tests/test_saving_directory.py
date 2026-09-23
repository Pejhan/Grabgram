from pathlib import Path
import asyncio
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tg_downloader.config import Config
from tg_downloader.database import Database
from tg_downloader.engine import DownloaderEngine
from tg_downloader.proxy import MtProtoProxy
from tg_downloader.ui import SettingsDialog


class SavingDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = Database(self.root / 'ledger.sqlite3')
        self.config = Config(1, 'hash', self.root / 'session', self.db.path,
                             self.root / 'downloads')

    def engine(self):
        return DownloaderEngine(self.config, self.db, lambda *_: None)

    def dialog(self, value):
        proxy = MtProtoProxy()
        return SimpleNamespace(
            current_proxy=Mock(return_value=proxy), original=proxy,
            saving_directory=Mock(get=Mock(return_value=value)),
            original_directory=str(self.config.download_root), db=self.db,
            destroy=Mock(), master=None,
        )

    def test_directory_is_persisted_and_applies_after_restart(self):
        running = self.engine()
        destination = self.root / 'My media'
        dialog = self.dialog(str(destination))
        with patch('tg_downloader.ui.messagebox.showinfo'):
            SettingsDialog.save(dialog)
        self.assertTrue(destination.is_dir())
        self.assertEqual(running._folder_path('channel'), self.config.download_root / 'channel')
        reopened_db = Database(self.db.path)
        restarted = DownloaderEngine(self.config, reopened_db, lambda *_: None)
        self.assertEqual(restarted._folder_path('channel'), destination / 'channel')
        with self.assertRaises(ValueError):
            restarted._folder_path('../outside')
        dialog.destroy.assert_called_once()

    def test_changed_directory_resets_progress_and_discards_destination_partial(self):
        self.db.add_channel('channel', 123, 'Channel', 'channel', 0, 0)
        channel = self.db.channels()[0]
        for message_id in (1, 2):
            self.db.add_media(channel=channel, message_id=message_id, document_id=message_id,
                              file_name='song.mp3', size_bytes=8192, duration_seconds=10,
                              message_date='', priority=10)
        rows = self.db.media_for_channel(channel.id)
        unfinished, completed = rows
        self.db.update_progress(unfinished['id'], 4096)
        self.db.mark_downloading(completed['id'])
        self.db.mark_downloaded(completed['id'], '/old/completed.mp3', 8192)
        self.engine()
        destination = self.root / 'new'
        self.db.set_setting_text('download_root', str(destination))
        engine = self.engine()
        rows = {row['id']: row for row in self.db.media_for_channel(channel.id)}
        self.assertEqual(rows[unfinished['id']]['bytes_downloaded'], 0)
        self.assertEqual(rows[completed['id']]['bytes_downloaded'], 8192)
        self.assertEqual(rows[completed['id']]['output_path'], '/old/completed.mp3')
        row = self.db.claim_next_queued()
        folder = destination / 'channel'
        folder.mkdir(parents=True)
        partial = folder / f"song [{row['message_id']}].mp3.part"
        partial.write_bytes(b'x' * 4096)
        offsets = []

        async def chunks(_media, *, offset, chunk_size):
            offsets.append(offset)
            yield b'y' * 8192

        engine._client = SimpleNamespace(
            get_messages=AsyncMock(return_value=SimpleNamespace(document=object())),
            iter_download=chunks,
        )
        engine._wait_until_channel_ready = AsyncMock()
        asyncio.run(engine._download_one(row))
        self.assertEqual(offsets, [0])
        self.assertEqual(partial.with_suffix('').read_bytes(), b'y' * 8192)
        # An unchanged directory keeps ordinary partial-download progress.
        self.db.update_progress(row['id'], 1234)
        self.engine()
        current = {r['id']: r for r in self.db.media_for_channel(channel.id)}
        self.assertEqual(current[row['id']]['bytes_downloaded'], 1234)
        self.assertEqual(current[row['id']]['restart_download'], 0)

    def test_invalid_directory_does_not_save_or_close(self):
        file = self.root / 'file'
        file.write_text('existing')
        for value in ('', 'relative/path', str(file)):
            with self.subTest(value=value):
                dialog = self.dialog(value)
                with patch('tg_downloader.ui.messagebox.showwarning') as warning:
                    SettingsDialog.save(dialog)
                warning.assert_called_once()
                dialog.destroy.assert_not_called()
                self.assertEqual(self.db.setting_text('download_root'), '')
        self.assertEqual(file.read_text(), 'existing')


if __name__ == '__main__':
    unittest.main()
