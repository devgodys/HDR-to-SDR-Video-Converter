from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from hdr2sdr.ui.tooltip_layout import TOOLTIP_MAX_WIDTH


class _AppToolTip(QLabel):
    """Replacement for the native QToolTip, installed as an app-wide event
    filter (see MainWindow.eventFilter). Needed because the native tooltip
    stays solid black on some Windows/Qt combinations no matter what
    palette or style-sheet is set on it - both QApplication.setPalette()
    and the dedicated QToolTip.setPalette() were tried and neither stuck,
    so this sidesteps native tooltip rendering entirely and draws a plain
    QLabel of our own instead."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWordWrap(True)
        self.setMaximumWidth(TOOLTIP_MAX_WIDTH)
