from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from hdr2sdr.theme.theme_tokens import LIGHT_BASE_TOKENS
from hdr2sdr.ui.widgets.combo import RoundedComboPopup


class _Scrim(QWidget):
    """Dim layer under a modal OverlayPanel; a click on it dismisses the panel."""
    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        event.accept()

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, 70))


class OverlayPanel(QWidget):
    """A rounded card shown *inside* the main window - the same look and the
    same reasons (no OS window chrome, no Windows title bar) as
    RoundedComboPopup, which the Tools / language dropdowns use - but for
    arbitrary content: add widgets to `.body`.

    modal=False: a light popup next to a button; closes on an outside click.
    modal=True: dims the window behind it and centres the card; only Escape,
    a click on the dim area or one of the card's own buttons closes it."""
    RADIUS = 12
    closed = Signal()

    def __init__(self, window, modal=False):
        super().__init__(window)
        self._win = window
        self._modal = modal
        self._scrim = None
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setObjectName("overlayPanel")
        self._bg = QColor(LIGHT_BASE_TOKENS["bg_card"])
        self._border = QColor(LIGHT_BASE_TOKENS["border"])
        m = self.RADIUS + 6
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(m, m - 2, m, m - 2)
        self.body.setSpacing(10)
        self.hide()
        self.closed_at = 0.0

    # ---- content helpers -------------------------------------------------
    def add_title(self, text):
        lbl = QLabel(text)
        lbl.setProperty("role", "overlay-title")
        f = lbl.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1)
        lbl.setFont(f)
        self.body.addWidget(lbl)
        return lbl

    def set_theme(self, bg, border):
        self._bg = QColor(bg)
        self._border = QColor(border)
        self.update()

    # ---- showing ---------------------------------------------------------
    def _prepare(self, width):
        win = self._win
        if self.parentWidget() is not win:
            self.setParent(win)
        self.setFixedWidth(width)
        self.layout().activate()
        self.resize(width, self.layout().sizeHint().height())
        if self._modal:
            if self._scrim is None:
                self._scrim = _Scrim(win)
                self._scrim.clicked.connect(self.close_panel)
            self._scrim.setGeometry(win.rect())
            self._scrim.show()
            self._scrim.raise_()

    def open_above(self, anchor, width=340, align_right=True):
        """Open over `anchor` (a widget), flush with its right edge; falls
        back to below it when there is no room above."""
        self._prepare(width)
        win, margin = self._win, 6
        bounds = win.rect()
        top_left = anchor.mapTo(win, QPoint(0, 0))
        x = top_left.x() + (anchor.width() - self.width() if align_right else 0)
        y = top_left.y() - self.height() - 6
        if y < margin:
            y = top_left.y() + anchor.height() + 6
        x = max(margin, min(x, bounds.right() - margin - self.width()))
        y = max(margin, min(y, bounds.bottom() - margin - self.height()))
        self.move(x, y)
        self._finish_show()

    def open_centered(self, width=380):
        self._prepare(width)
        c = self._win.rect().center()
        self.move(c.x() - self.width() // 2, max(10, c.y() - self.height() // 2))
        self._finish_show()

    def _finish_show(self):
        self.show()
        self.raise_()
        self.setFocus()

    def close_panel(self):
        self.hide()

    # ---- dismissal (an in-window child gets none of Qt::Popup's for free) --
    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event):
        super().hideEvent(event)
        QApplication.instance().removeEventFilter(self)
        if self._scrim is not None:
            self._scrim.hide()
        self.closed_at = time.monotonic()
        self.closed.emit()

    def _combo_popup_open(self):
        return any(p.isVisible() for p in self._win.findChildren(RoundedComboPopup))

    def _inside(self, gp):
        if QRect(self.mapToGlobal(QPoint(0, 0)), self.size()).contains(gp):
            return True
        # A dropdown opened from inside this card is a sibling overlay, not a
        # child, but a click in it is still "inside".
        for p in self._win.findChildren(RoundedComboPopup):
            if p.isVisible() and QRect(p.mapToGlobal(QPoint(0, 0)), p.size()).contains(gp):
                return True
        return False

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if not self._modal:
                try:
                    gp = event.globalPosition().toPoint()
                except Exception:
                    return False
                if not self._inside(gp):
                    self.hide()                     # click still reaches its target
        elif et == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if not self._combo_popup_open():
                self.hide()
                return True
        elif obj is self._win and et in (QEvent.Type.WindowDeactivate, QEvent.Type.Move):
            if not self._modal:
                self.hide()
        elif obj is self._win and et == QEvent.Type.Resize:
            if self._modal and self._scrim is not None:
                self._scrim.setGeometry(self._win.rect())
                c = self._win.rect().center()
                self.move(c.x() - self.width() // 2, max(10, c.y() - self.height() // 2))
            else:
                self.hide()
        return False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        p.fillPath(path, self._bg)
        p.setPen(QPen(self._border, 1))
        p.drawPath(path)
