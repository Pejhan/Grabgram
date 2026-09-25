import tkinter as tk
import unittest
from unittest.mock import patch

from grabgram.text_display import DisplayStringVar, display_text


class TextDisplayTests(unittest.TestCase):
    def setUp(self):
        display_text.cache_clear()
        self.platform = patch("grabgram.text_display.sys.platform", "linux")
        self.platform.start()

    def tearDown(self):
        self.platform.stop()
        display_text.cache_clear()

    def test_joined_persian_in_visual_order(self):
        self.assertEqual(display_text("سلام"), "\ufee1\ufefc\ufeb3")

    def test_mixed_filename_preserves_latin_and_numbers(self):
        rendered = display_text("Current file: سلام 123.mp4")
        self.assertTrue(rendered.startswith("Current file: "))
        self.assertIn("123", rendered)
        self.assertTrue(rendered.endswith(".mp4"))
        self.assertNotIn("سلام", rendered)

    def test_persian_half_space_prevents_joining(self):
        self.assertNotEqual(display_text("میروم"), display_text("می\u200cروم"))
        self.assertIn("\ufbfd", display_text("می\u200cروم"))

    def test_english_unchanged(self):
        self.assertEqual(display_text("video_123.mp4"), "video_123.mp4")

    def test_native_platform_unchanged(self):
        with patch("grabgram.text_display.sys.platform", "win32"):
            self.assertEqual(display_text("سلام"), "سلام")

    def test_label_variable_shapes_initial_and_updated_values(self):
        interpreter = tk.Tcl()
        variable = DisplayStringVar(master=interpreter, value="سلام")
        self.assertEqual(variable.get(), "\ufee1\ufefc\ufeb3")
        variable.set("سلام.mp4")
        self.assertEqual(variable.get(), "\ufee1\ufefc\ufeb3.mp4")
