"""Per-user advisory locks; never kill processes using a broad name pattern."""
from __future__ import annotations

import os
from pathlib import Path

from .platform_paths import data_dir


class UserInstanceLock:
    def __init__(self, name: str = "engine", *, directory: Path | None = None) -> None:
        base = directory or data_dir()
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{name}.lock"
        self._file = None

    def acquire(self) -> None:
        self._file = self.path.open("a+", encoding="ascii")
        os.chmod(self.path, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0, os.SEEK_END)
                if self._file.tell() == 0:
                    self._file.write(" ")
                    self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            raise RuntimeError("Another Voice Bridge instance is already running. Close it before starting a second one.") from exc
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()))
        self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
        # Do not unlink: processes waiting on the old inode could otherwise
        # coexist with a new lock file bearing the same path.

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.close()
