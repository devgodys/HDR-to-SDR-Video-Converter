from __future__ import annotations

from PySide6.QtCore import (
    Qt, QSize, QPropertyAnimation, QEasingCurve, Property, QRectF,
)
from PySide6.QtGui import (
    QColor, QFontMetrics, QPainter,
)
from PySide6.QtWidgets import (
    QAbstractButton, QSizePolicy,
)
from hdr2sdr.theme.theme_tokens import LIGHT_BASE_TOKENS, pick_toggle_knob_color, window_tokens


class ToggleSwitch(QAbstractButton):
    """iOS-style animated toggle switch with a trailing (or leading) text label -
    drop-in replacement for QCheckBox(text): same isChecked() /
    setChecked() / toggled API, so call sites and translation logic
    (which just calls .text()/.setText()/.toolTip()) don't need to
    change. Unlike a QSS-only checkbox indicator, the knob position is
    a real animated QPropertyAnimation, not an instant state swap.

    When ``self.property("label_left")`` is True, the label is drawn on the
    left and the switch track sits flush at the right edge (used for right-aligned
    header toggles like ``Auto-render`` in ``TonemapComparisonDialog``)."""

    def __init__(self, text="", parent=None, *, label_left: bool = False):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if label_left:
            self.setProperty("label_left", True)
        self._track_w = 40
        self._track_h = 22
        self._pos = 0.0  # 0 = off, 1 = on
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_to)

    def _get_knob_pos(self):
        return self._pos

    def _set_knob_pos(self, value):
        self._pos = value
        self.update()

    knobPos = Property(float, _get_knob_pos, _set_knob_pos)

    def _animate_to(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self.update()
        self._anim.start()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        extra = (12 + fm.horizontalAdvance(self.text())) if self.text() else 0
        return QSize(self._track_w + extra, max(self._track_h, fm.height()) + 6)

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(self._track_w + (20 if self.text() else 0), max(self._track_h, fm.height()) + 6)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        win_tok = window_tokens(self.window())
        off_hex = self.property("off_color") or win_tok.get("border") or LIGHT_BASE_TOKENS["off_fallback"]
        on_hex = self.property("on_color") or win_tok.get("accent") or LIGHT_BASE_TOKENS["accent"]
        txt_hex = self.property("text_color") or win_tok.get("text") or LIGHT_BASE_TOKENS["text"]

        off = QColor(off_hex)
        on = QColor(on_hex)
        mix = QColor(
            round(off.red() + (on.red() - off.red()) * self._pos),
            round(off.green() + (on.green() - off.green()) * self._pos),
            round(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        if not self.isEnabled():
            mix.setAlpha(140)

        label_left = bool(self.property("label_left"))
        track_x = float(self.width() - self._track_w) if (label_left and self.text()) else 0.0
        track = QRectF(track_x, (self.height() - self._track_h) / 2.0, self._track_w, self._track_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mix)
        painter.drawRoundedRect(track, self._track_h / 2.0, self._track_h / 2.0)

        knob_d = self._track_h - 4
        knob_x = track.x() + 2 + self._pos * (self._track_w - knob_d - 4)
        knob_off = QColor(LIGHT_BASE_TOKENS["knob_fg"])
        knob_on = QColor(self.property("knob_on_color") or pick_toggle_knob_color(on.name()))
        knob_col = QColor(
            round(knob_off.red() + (knob_on.red() - knob_off.red()) * self._pos),
            round(knob_off.green() + (knob_on.green() - knob_off.green()) * self._pos),
            round(knob_off.blue() + (knob_on.blue() - knob_off.blue()) * self._pos),
        )
        painter.setBrush(knob_col)
        painter.drawEllipse(QRectF(knob_x, track.y() + 2, knob_d, knob_d))

        if self.text():
            text_color = QColor(txt_hex)
            if not self.isEnabled():
                text_color.setAlpha(130)
            painter.setPen(text_color)
            painter.setFont(self.font())
            avail_w = max(0.0, float(self.width() - self._track_w - 8))
            fm = QFontMetrics(self.font())
            draw_str = fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, int(avail_w)) if avail_w > 0 else self.text()
            if label_left:
                text_rect = QRectF(0.0, 0.0, avail_w, float(self.height()))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, draw_str)
            else:
                text_rect = QRectF(self._track_w + 8.0, 0.0, avail_w, float(self.height()))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, draw_str)
        painter.end()
