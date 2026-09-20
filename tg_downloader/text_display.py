"""Display-only shaping for Tk's Linux text renderer.

Keep logical Unicode in the database, paths, searches and editable widgets.
Windows and macOS retain their native rendering.
"""

from functools import lru_cache
import sys
import tkinter as tk
import unicodedata

from arabic_reshaper import ArabicReshaper
from bidi import get_display


_reshaper = ArabicReshaper(configuration={
    "delete_harakat": False,
    "shift_harakat_position": True,
})


@lru_cache(maxsize=4096)
def display_text(text: str) -> str:
    """Join Arabic-script letters and order mixed text for Linux Tk display."""
    if not sys.platform.startswith("linux"):
        return text
    if not any(unicodedata.bidirectional(char) in {"R", "AL"} for char in text):
        return text
    # These widgets belong to an English UI; keep extensions, numbers and
    # English prefixes in an LTR paragraph while ordering RTL runs correctly.
    return get_display(_reshaper.reshape(text), base_dir="L")


class DisplayStringVar(tk.StringVar):
    """A variable used only by labels, never by input or persistence code."""

    def set(self, value: str) -> None:
        super().set(display_text(value))

    initialize = set
