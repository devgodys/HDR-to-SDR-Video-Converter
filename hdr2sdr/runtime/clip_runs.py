"""Temporary test-clip directories and stale-run protection."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class ClipRunManager:
    """Give every render an isolated output folder and invalidate old callbacks."""

    def __init__(self, temp_dir=None):
        self.generation = 0
        self.temp_dir = Path(temp_dir) if temp_dir is not None else None
        self._directories: set[Path] = set()

    def begin(self) -> tuple[int, Path]:
        self.generation += 1
        directory = Path(tempfile.mkdtemp(
            prefix="hdr2sdr-test-clip-", dir=self.temp_dir))
        self._directories.add(directory)
        return self.generation, directory

    def cancel(self) -> None:
        self.generation += 1

    def is_current(self, generation: int) -> bool:
        return generation == self.generation

    def cleanup(self, directory) -> None:
        directory = Path(directory)
        if directory not in self._directories:
            return
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            pass
        except OSError:
            return
        self._directories.discard(directory)

    def cleanup_all(self) -> None:
        for directory in tuple(self._directories):
            self.cleanup(directory)
