"""The "all done - the PC will sleep/hibernate/shut down in N s" countdown.

A themed QMessageBox is the view; this controller owns the deadline. It uses
a monotonic deadline (not "60 ticks"), so a busy GUI thread can't stretch the
countdown, and it can never fire twice: whichever of expired / cancelled
happens first wins. Closing the box any way other than letting it run out
(Cancel button, Esc, the X) counts as Cancel.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal


class AfterActionCountdown(QObject):
    expired = Signal()
    cancelled = Signal()

    def __init__(self, box, template, action_label, seconds, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self._box = box
        self._template = template
        self._label = action_label
        self._seconds = seconds
        self._clock = clock
        self._deadline = None
        self._done = False
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        box.finished.connect(lambda _code: self.cancel())     # X / Esc / Cancel button

    def text_for(self, remaining):
        return (self._template.replace("{action}", self._label)
                .replace("{seconds}", str(remaining)))

    def start(self):
        self._deadline = self._clock() + self._seconds
        self._box.setWindowModality(Qt.WindowModality.NonModal)
        self._box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._tick()
        self._box.show()
        self._box.raise_()
        self._box.activateWindow()
        self._timer.start()

    def _tick(self):
        if self._done or self._deadline is None:
            return
        left = self._deadline - self._clock()
        self._box.setText(self.text_for(max(0, math.ceil(left))))
        if left <= 0:
            self._finish(expired=True)

    def cancel(self):
        if not self._done:
            self._finish(expired=False)

    @property
    def done(self):
        return self._done

    def _finish(self, expired):
        self._done = True
        self._timer.stop()
        try:
            self._box.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._box.close()
        self._box.deleteLater()
        (self.expired if expired else self.cancelled).emit()
