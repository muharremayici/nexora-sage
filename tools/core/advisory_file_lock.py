"""Small cross-platform advisory file-lock primitive for local SAGE coordination."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import TextIO


class AdvisoryFileLock:
    """Hold byte zero of a stable lock endpoint until ``release`` is called.

    The endpoint is deliberately never unlinked by this class. Recreating a
    lock path after release can split ownership across old and new inodes.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle: TextIO | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        handle = self.path.open("r+", encoding="utf-8")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("\n")
                handle.flush()
            self._lock_handle(handle)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        self._handle = handle
        return True

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def read_held_text(self) -> str:
        if self._handle is None:
            raise RuntimeError("Cannot read advisory lock metadata without holding the lock.")
        self._handle.seek(0)
        return self._handle.read()

    def write_text(self, content: str) -> None:
        if self._handle is None:
            raise RuntimeError("Cannot write advisory lock metadata without holding the lock.")
        self._handle.seek(0)
        self._handle.truncate(0)
        self._handle.write(content)
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            self._unlock_handle(handle)
        finally:
            handle.close()

    @staticmethod
    def _lock_handle(handle: TextIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle: TextIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
