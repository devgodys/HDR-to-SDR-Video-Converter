"""Runs runtime.tools.install() on a worker thread so downloading and
hashing ~200 MB never blocks the window; log lines come back as queued
signals and are written to the Activity log by the main window."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from hdr2sdr.runtime import tools


class ToolInstallThread(QThread):
    log = Signal(str)
    done = Signal(bool)

    def __init__(self, names, channel, tools_dir, parent=None):
        super().__init__(parent)
        self._names = list(names)
        self._channel = channel
        self._tools_dir = tools_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ok = tools.install(self._names, self._channel, self._tools_dir,
                           log=lambda line: self.log.emit(line + "\n"),
                           cancel=lambda: self._cancel)
        self.done.emit(ok)
