from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_file: Path
    database_file: Path
    download_root: Path
    scan_interval_seconds: int = 60


def load_config(path: Path) -> Config:
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration not found: {path}\n"
            "Copy config.example.json to config.json and enter your Telegram API credentials."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent.resolve()

    def local_path(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else base / candidate

    api_id = int(os.getenv("TG_API_ID", raw.get("api_id", 0)))
    api_hash = os.getenv("TG_API_HASH", raw.get("api_hash", "")).strip()
    if not api_id or not api_hash or api_hash.startswith("replace-"):
        raise ValueError("Set api_id and api_hash in config.json (or TG_API_ID/TG_API_HASH).")

    config = Config(
        api_id=api_id,
        api_hash=api_hash,
        session_file=local_path(raw.get("session_file", "data/telegram")),
        database_file=local_path(raw.get("database_file", "data/downloader.sqlite3")),
        download_root=local_path(raw.get("download_root", "downloads")),
        scan_interval_seconds=max(15, int(raw.get("scan_interval_seconds", 60))),
    )
    config.session_file.parent.mkdir(parents=True, exist_ok=True)
    config.database_file.parent.mkdir(parents=True, exist_ok=True)
    config.download_root.mkdir(parents=True, exist_ok=True)
    return config
