from __future__ import annotations

import re
from pathlib import Path


INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, message_id: int, extension: str = ".audio") -> str:
    name = INVALID_FILENAME.sub("_", name).strip(" .")
    if not name:
        name = f"telegram-{message_id}{extension}"
    stem = Path(name).stem
    suffix = Path(name).suffix or extension
    if stem.upper() in RESERVED:
        stem = f"_{stem}"
    return f"{stem[:180]}{suffix[:20]}"


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TB"


def unique_output_path(folder: Path, filename: str, message_id: int) -> Path:
    """A stable message-id suffix prevents same-name Telegram files colliding."""
    clean = safe_filename(filename, message_id)
    path = Path(clean)
    return folder / f"{path.stem} [{message_id}]{path.suffix}"
