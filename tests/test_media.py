from __future__ import annotations

import unittest
from pathlib import Path

from tg_downloader.media import safe_filename, unique_output_path


class MediaTests(unittest.TestCase):
    def test_windows_unsafe_filename_is_cleaned(self) -> None:
        self.assertEqual("bad_name_.mp3", safe_filename('bad:name?.mp3', 10))

    def test_empty_filename_gets_message_name(self) -> None:
        self.assertEqual("telegram-10.ogg", safe_filename("", 10, ".ogg"))

    def test_output_names_include_message_id(self) -> None:
        self.assertEqual(Path("Music") / "same [42].mp3", unique_output_path(Path("Music"), "same.mp3", 42))


if __name__ == "__main__":
    unittest.main()
