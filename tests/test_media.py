from __future__ import annotations

import unittest
from pathlib import Path

from grabgram.media import manual_filename, message_filename, safe_filename, unique_output_path


class MediaTests(unittest.TestCase):
    def test_manual_filename_accepts_current_extension_or_no_extension(self) -> None:
        for name in ("Edited name", "Edited name.mp4", "Edited name.MP4"):
            self.assertEqual("Edited name.mp4", manual_filename(name, "Current name.mp4", 42))
        self.assertEqual("Trip v1.2.mp4", manual_filename("Trip v1.2", "Current name.mp4", 42))

    def test_manual_filename_rejects_empty_names_and_paths(self) -> None:
        for name in ("", "  ", "...", ".mp4", "../clip.mp4", "folder\\clip.mp4", "bad:name.mp4"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                manual_filename(name, "Current name.mp4", 42)

    def test_message_name_keeps_media_extension_and_flattens_lines(self) -> None:
        self.assertEqual(
            "Summer trip. Day 1.mp4",
            message_filename("  Summer trip.\n Day 1  ", "clip.mp4", 42),
        )

    def test_message_name_cleans_paths_and_reserved_names(self) -> None:
        self.assertEqual("Trip_ beach_.jpg", message_filename("Trip: beach?", "photo.jpg", 42))
        self.assertEqual("_CON.pdf", message_filename("CON", "notes.pdf", 42))

    def test_unusable_message_keeps_telegram_filename(self) -> None:
        for text in ("", " \n\t", " ... "):
            self.assertEqual("clip.mp4", message_filename(text, "clip.mp4", 42))

    def test_long_unicode_message_fits_filesystem_limit_after_id_suffix(self) -> None:
        name = message_filename("سفر 🌅 " * 100, "clip.mp4", 42)
        output = unique_output_path(Path("Videos"), name, 42)
        self.assertLessEqual(len(output.name.encode("utf-8")), 255)
        self.assertTrue(output.name.endswith(" [42].mp4"))

    def test_windows_unsafe_filename_is_cleaned(self) -> None:
        self.assertEqual("bad_name_.mp3", safe_filename('bad:name?.mp3', 10))

    def test_empty_filename_gets_message_name(self) -> None:
        self.assertEqual("telegram-10.ogg", safe_filename("", 10, ".ogg"))

    def test_output_names_include_message_id(self) -> None:
        self.assertEqual(Path("Music") / "same [42].mp3", unique_output_path(Path("Music"), "same.mp3", 42))

    def test_sequence_prefix_precedes_filename_and_keeps_collision_suffix(self) -> None:
        self.assertEqual(
            Path("Videos") / "001 Summer trip [42].mp4",
            unique_output_path(Path("Videos"), "Summer trip.mp4", 42, "001 "),
        )


if __name__ == "__main__":
    unittest.main()
