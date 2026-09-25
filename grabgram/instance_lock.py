from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class InstanceLock:
    """An OS-held lock preventing two downloader processes using the same database."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: BinaryIO | None = path.open("a+b")
        if path.stat().st_size == 0:
            self._handle.write(b"0")
            self._handle.flush()
        self._handle.seek(0)
        try:
            self._lock()
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise RuntimeError("Another Grabgram instance is already running.") from exc

    def _lock(self) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def close(self) -> None:
        if self._handle is None:
            return
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "InstanceLock":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
