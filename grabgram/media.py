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


def message_filename(text: str, original_filename: str, message_id: int) -> str:
    """Use message text as the stem while keeping the media's extension."""
    stem = " ".join(text.split())
    stem = INVALID_FILENAME.sub("_", stem).strip(" .")
    if not stem:
        return original_filename
    # Leave room for the extension and the message-id suffix on disk, even in UTF-8.
    stem = stem.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")
    return safe_filename(f"{stem}{Path(original_filename).suffix}", message_id)


def manual_filename(name: str, current_filename: str, message_id: int) -> str:
    """Accept an edited filename while preserving the file's existing extension."""
    name = name.strip()
    if INVALID_FILENAME.search(name):
        raise ValueError("File names cannot contain path separators or Windows-invalid characters.")
    extension = Path(current_filename).suffix
    if extension and name.casefold().endswith(extension.casefold()):
        name = name[:-len(extension)]
    if not name.strip(" ."):
        raise ValueError("Enter a non-empty file name.")
    return message_filename(name, current_filename, message_id)


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TB"


def unique_output_path(
    folder: Path, filename: str, message_id: int, sequence_prefix: str = "",
) -> Path:
    """A stable message-id suffix prevents same-name Telegram files colliding."""
    clean = safe_filename(filename, message_id)
    path = Path(clean)
    return folder / f"{sequence_prefix}{path.stem} [{message_id}]{path.suffix}"


def saved_sequence_prefix(output: Path, filename: str, message_id: int) -> str:
    base = unique_output_path(Path("."), filename, message_id).name
    prefix = output.name[:-len(base)] if output.name.endswith(base) else ""
    return prefix if re.fullmatch(r"\d+ ", prefix) else ""
