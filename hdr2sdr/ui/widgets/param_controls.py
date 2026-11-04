"""Compact parameter controls that replace the old "slider + number box +
round -/+ buttons" combination.

The old rows showed one value three times (handle, number, buttons) and
needed ~90 px of height per parameter. The widgets here show it once:

* ``ParamSlider``   - value lives *inside* the control. Three looks that can be
                      compared live (right-click > Control style):
                      "fill"  - rounded bar, filled up to the value (default),
                      "scrub" - number field you drag sideways (Blender/Resolve),
                      "track" - slim classic track with a value chip.
* ``ColorPad2D``    - one 2-D pad for brightness (y) x saturation (x).
* ``TierSlider``    - CRF slider split into named quality bands (unified compact pill bar).
* ``ScrubSpinBox``  - flat number field: drag to change, click to type.

Every widget keeps the small slice of the QSpinBox/QSlider API the rest of
the app already uses (value/setValue/valueChanged/setRange/...), so
``MainWindow`` can swap them in without touching the code that reads them.
"""
from __future__ import annotations

import re
import weakref

from PySide6.QtCore import (
    Qt, QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, QSize, QTimer,
    Property, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QSpinBox, QWidget,
)

from hdr2sdr.theme.theme_tokens import build_tray_menu_qss, window_tokens
from hdr2sdr.ui.style_helpers import set_role
from hdr2sdr.ui.widgets.sliders import PreviewScrubSlider, RoundHandleSlider

__all__ = [
    "ParamSlider", "ColorPad2D", "TierSlider", "ScrubSpinBox", "make_value_chip",
    "LinkButton", "TimelineSlider", "CONTROL_STYLES", "DEFAULT_CONTROL_STYLE", "TEMPERATURE_GRADIENT",
]

# (key, English label) - the labels go through the window's tr().
CONTROL_STYLES = (
    ("fill", "Fill bar"),
    ("scrub", "Scrub field"),
    ("track", "Track and value"),
    ("pad", "Colour pad"),
)
DEFAULT_CONTROL_STYLE = "pad"

# Cool -> neutral -> warm, for the colour-temperature control.
TEMPERATURE_GRADIENT = (
    (0.00, "#6EA8FF"),
    (0.45, "#F2F4FF"),
    (0.55, "#FFF1DD"),
    (1.00, "#FFA24C"),
)


# ---- small helpers ---------------------------------------------------------

def _tokens(widget: QWidget) -> dict:
    return window_tokens(widget.window())


def _alpha(color, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _translate(widget: QWidget, text: str) -> str:
    """Translate through the owning window's tr() when it has the app's
    translator; otherwise (tests, bare widgets) return the English text."""
    fn = getattr(widget.window(), "tr", None)
    if fn is None:
        return text
    try:
        return str(fn(text))
    except Exception:
        return text


def _fmt_signed(v: int) -> str:
    return f"+{v}" if v > 0 else ("\u2212" + str(-v) if v < 0 else "0")


def build_style_menu(owner: QWidget, current: str, include_pad: bool, on_choose,
                     reset_text: str | None = None, on_reset=None, reset_enabled: bool = True) -> QMenu:
    """Right-click menu shared by ParamSlider and ColorPad2D."""
    menu = QMenu(owner)
    menu.setStyleSheet(build_tray_menu_qss(_tokens(owner)))
    if on_reset is not None:
        act = menu.addAction(reset_text or _translate(owner, "Reset to default"))
        act.setEnabled(reset_enabled)
        act.triggered.connect(lambda _=False: on_reset())
        menu.addSeparator()
    sub = menu.addMenu(_translate(owner, "Control style"))
    for key, label in CONTROL_STYLES:
        if key == "pad" and not include_pad:
            continue
        a = sub.addAction(_translate(owner, label))
        a.setCheckable(True)
        a.setChecked(key == current)
        a.triggered.connect(lambda _=False, k=key: on_choose(k))
    return menu


# ============================================================================
# ParamSlider
# ============================================================================

class ParamSlider(QWidget):
    """One integer parameter, value shown inside the control.

    Input: drag (absolute for fill/track, relative for scrub), click, wheel
    while focused, arrow keys / PageUp / PageDown / Home / End, double-click
    to type a number, Shift while dragging for fine control, right-click for
    reset and the control-style switch."""

    valueChanged = Signal(int)
    currentIndexChanged = Signal(int)
    currentTextChanged = Signal(str)
    styleChanged = Signal(str)     # emitted on every instance when the global style changes
    styleChosen = Signal(str)      # emitted only on the instance the user picked it from

    _instances: "weakref.WeakSet[ParamSlider]" = weakref.WeakSet()
    _global_style = DEFAULT_CONTROL_STYLE

    # -- global style ----------------------------------------------------
    @classmethod
    def global_style(cls) -> str:
        return cls._global_style

    @classmethod
    def set_global_style(cls, name: str) -> None:
        if name not in dict(CONTROL_STYLES):
            name = DEFAULT_CONTROL_STYLE
        cls._global_style = name
        for w in list(cls._instances):
            try:
                w._style_changed()
            except RuntimeError:        # C++ object already gone
                pass

    # -- construction ----------------------------------------------------
    def __init__(self, label: str = "", minimum: int = 0, maximum: int = 100, value: int = 0,
                 *, step: int = 1, default: int | None = None, bidirectional: bool = False,
                 origin: float | None = None, formatter=None, signed: bool = False,
                 suffix: str = "", gradient=None, pad_capable: bool = False,
                 choices=None, display_choices=None, ticks: bool = False,
                 segmented: bool = False, parent=None):
        super().__init__(parent)
        self._label = label
        self._choices = tuple(choices) if choices is not None else None
        self._display_choices = tuple(display_choices) if display_choices is not None else None
        if self._choices:
            minimum, maximum = 0, max(0, len(self._choices) - 1)
        self._min, self._max = int(minimum), max(int(minimum), int(maximum))
        self._value = max(self._min, min(self._max, int(value)))
        self._step = max(1, int(step))
        self._default = default
        self._bidir = bool(bidirectional)
        self._origin = float(origin if origin is not None else 0)
        self._formatter = formatter
        self._signed = signed
        self._suffix = suffix
        self._gradient = tuple(gradient) if gradient else None
        self.pad_capable = pad_capable
        self._ticks = bool(ticks)
        self._segmented = bool(segmented)

        self._hover = False
        self._dragging = False
        self._relative = False
        self._press_x = 0.0
        self._press_val = self._value
        self._value_before_press = self._value
        self._anchor_x = 0.0
        self._anchor_val = float(self._value)
        self._editing = False
        self._edit_cancelled = False
        self._editor: QLineEdit | None = None

        self._anim_pos = float(self._value)
        self._anim = QPropertyAnimation(self, b"animPos", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._snap)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(120)
        ParamSlider._instances.add(self)
        self._apply_height()
        self.resize(220, self.height())

    # -- QSpinBox / QSlider compatible surface ----------------------------
    def value(self) -> int:
        return self._value

    def minimum(self) -> int:
        return self._min

    def maximum(self) -> int:
        return self._max

    def singleStep(self) -> int:
        return self._step

    def setSingleStep(self, step: int) -> None:
        self._step = max(1, int(step))

    def setRange(self, lo: int, hi: int) -> None:
        self._min, self._max = int(lo), max(int(lo), int(hi))
        clamped = max(self._min, min(self._max, self._value))
        self._snap()
        if clamped != self._value:
            self.setValue(clamped)
        self.update()

    def setMinimum(self, lo: int) -> None:
        self.setRange(lo, self._max)

    def setMaximum(self, hi: int) -> None:
        self.setRange(self._min, hi)

    def setValue(self, v: int) -> None:
        self._set_value(int(v), animate=True)

    def text(self) -> str:
        return self._label

    def setText(self, text: str) -> None:
        if text != self._label:
            self._label = text
            self.setAccessibleName(text)
            self.update()

    def valueText(self) -> str:
        return self._format(self._value)

    def setDefaultValue(self, v: int | None) -> None:
        self._default = v

    def defaultValue(self) -> int:
        if self._default is not None:
            return int(self._default)
        if self._bidir:
            return int(round(self._origin))
        return self._min

    def reset(self) -> None:
        self.setValue(self.defaultValue())

    def isModified(self) -> bool:
        return self._value != self.defaultValue()

    def setFormatter(self, fn) -> None:
        self._formatter = fn
        self.update()

    def _get_bidir(self) -> bool:
        return self._bidir

    def _set_bidir(self, flag: bool) -> None:
        self._bidir = bool(flag)
        self.update()

    bidirectional = property(_get_bidir, _set_bidir)

    def _get_origin(self) -> float:
        return self._origin

    def _set_origin(self, v: float) -> None:
        self._origin = float(v)
        self.update()

    origin_value = property(_get_origin, _set_origin)

    # -- animation ---------------------------------------------------------
    def _get_anim_pos(self) -> float:
        return self._anim_pos

    def _set_anim_pos(self, v: float) -> None:
        self._anim_pos = v
        self.update()

    animPos = Property(float, _get_anim_pos, _set_anim_pos)

    def _snap(self, *_a) -> None:
        self._anim.stop()
        self._anim_pos = float(self._value)
        self.update()

    def _pos_now(self) -> float:
        if self._anim.state() == QPropertyAnimation.State.Running:
            return self._anim_pos
        return float(self._value)

    def _set_value(self, v: int, *, animate: bool) -> None:
        v = max(self._min, min(self._max, v))
        if v == self._value:
            return
        self._value = v
        if animate and not self._dragging and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._anim_pos)
            self._anim.setEndValue(float(v))
            self._anim.start()
        else:
            self._snap()
        self.valueChanged.emit(v)
        self.currentIndexChanged.emit(v)
        if self._choices and 0 <= v - self._min < len(self._choices):
            self.currentTextChanged.emit(str(self._choices[v - self._min]))
        else:
            self.currentTextChanged.emit(self._format(v))
        self.update()

    # -- QComboBox compatible surface (for discrete choice sliders like Priority) --
    def count(self) -> int:
        return len(self._choices) if self._choices else (self._max - self._min + 1)

    def currentIndex(self) -> int:
        return self._value - self._min if self._choices else self._value

    def setCurrentIndex(self, idx: int) -> None:
        self.setValue((self._min + int(idx)) if self._choices else int(idx))

    def itemText(self, idx: int) -> str:
        if self._choices and 0 <= idx < len(self._choices):
            return str(self._choices[idx])
        return self._format(self._min + idx)

    def currentText(self) -> str:
        if self._choices and 0 <= self._value - self._min < len(self._choices):
            return str(self._choices[self._value - self._min])
        return self._format(self._value)

    def findText(self, text: str) -> int:
        if not self._choices:
            return -1
        t_str = str(text)
        for i, c in enumerate(self._choices):
            if c == t_str:
                return i
        for i, c in enumerate(self._choices):
            if t_str.lower() in str(c).lower():
                return i
        return -1

    def setCurrentText(self, text: str) -> None:
        idx = self.findText(text)
        if idx >= 0:
            self.setCurrentIndex(idx)

    # -- style -------------------------------------------------------------
    def style_name(self) -> str:
        s = ParamSlider._global_style
        if s == "pad":                  # only the colour group has a pad; 1-D sliders use the fill bar look
            s = "fill"
        return s

    def _apply_height(self) -> None:
        self.setFixedHeight({"fill": 26, "scrub": 26, "track": 42}[self.style_name()])

    def _style_changed(self) -> None:
        self._end_edit(commit=False)
        self._apply_height()
        self.updateGeometry()
        self.update()
        self.styleChanged.emit(ParamSlider._global_style)

    def sizeHint(self) -> QSize:
        return QSize(220, self.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(120, self.height())

    # -- text --------------------------------------------------------------
    def _format(self, v: int) -> str:
        if self._formatter is not None:
            return self._formatter(v)
        idx = int(round(v)) - self._min
        if self._display_choices and 0 <= idx < len(self._display_choices):
            return _translate(self, str(self._display_choices[idx]))
        if self._choices and 0 <= idx < len(self._choices):
            return _translate(self, str(self._choices[idx]))
        if self._signed or self._bidir:
            return _fmt_signed(v - int(round(self._origin))) if self._origin else _fmt_signed(v)
        return f"{v}{self._suffix}"

    def _font(self, bold: bool = False) -> QFont:
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        return f

    # -- geometry ----------------------------------------------------------
    def _field_rect(self) -> QRectF:
        return QRectF(1.0, 1.0, max(1.0, self.width() - 2.0), max(1.0, self.height() - 2.0))

    def _frac(self, v: float) -> float:
        if self._segmented and self._max >= self._min and self.style_name() != "track":
            n_seg = self._max - self._min + 1
            return max(0.0, min(1.0, (v - self._min + 1.0) / n_seg))
        return (v - self._min) / (self._max - self._min) if self._max > self._min else 0.0

    def _track_x_range(self) -> tuple[float, float]:
        d = self._handle_d()
        return 2.0 + d / 2.0, self.width() - 2.0 - d / 2.0

    @staticmethod
    def _handle_d() -> float:
        return 14.0

    def _x_for(self, v: float) -> float:
        if self.style_name() == "track":
            x0, x1 = self._track_x_range()
            return x0 + self._frac(v) * (x1 - x0)
        r = self._field_rect()
        return r.left() + self._frac(v) * r.width()

    def _chip_rect(self) -> QRectF:
        fm = QFontMetricsF(self._font(True))
        w = max(44.0, fm.horizontalAdvance(self._format(self._value)) + 16.0)
        return QRectF(self.width() - w - 1.0, 0.5, w, 19.0)

    def _value_for_x(self, x: float) -> float:
        if self.style_name() == "track":
            x0, x1 = self._track_x_range()
            f = (x - x0) / max(1.0, x1 - x0)
            f = max(0.0, min(1.0, f))
            return self._min + f * (self._max - self._min)
        r = self._field_rect()
        f = max(0.0, min(1.0, (x - r.left()) / max(1.0, r.width())))
        if self._segmented and self._max >= self._min:
            n_seg = self._max - self._min + 1
            return float(self._min + min(n_seg - 1, int(f * n_seg)))
        return self._min + f * (self._max - self._min)

    def _quantize(self, v: float) -> int:
        q = round((v - self._min) / self._step) * self._step + self._min
        return int(max(self._min, min(self._max, q)))

    # -- painting ------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        t = _tokens(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.style_name() == "track":
            self._paint_track(p, t)
        else:
            self._paint_field(p, t, self.style_name())
        p.end()

    def _origin_or_min(self) -> float:
        if self._segmented and self.style_name() != "track":
            return float(self._min - 1)
        return self._origin if self._bidir else float(self._min)

    def _gradient_fill(self, left: float, right: float, alpha: float) -> QLinearGradient:
        g = QLinearGradient(left, 0, right, 0)
        for pos, col in self._gradient:
            g.setColorAt(pos, _alpha(col, alpha))
        return g

    def _paint_field(self, p: QPainter, t: dict, style: str) -> None:
        en = self.isEnabled()
        r = self._field_rect()
        rad = min(r.height() / 2.0, float(t.get("radius_button", 8)) + 2.0)
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, QColor(t["bg_field"] if en else t["bg_disabled"]))

        accent = QColor(t["accent"])
        x_val = self._x_for(self._pos_now())
        x_org = self._x_for(self._origin_or_min())
        lo, hi = sorted((x_org, x_val))
        has_accent_fill = (style == "fill" and not self._gradient and (hi - lo > 0.5))
        fill_rect = QRectF(lo, r.top(), hi - lo, r.height()) if has_accent_fill else QRectF()

        p.save()
        p.setClipPath(path)
        if style == "fill":
            if self._gradient:
                # Colour temperature: preserve gradient fill & rules unchanged
                grad_a = 0.40 if t.get("is_dark", True) else 0.62
                p.fillRect(r, self._gradient_fill(r.left(), r.right(), grad_a if en else 0.18))
                if en:
                    p.fillRect(QRectF(x_val - 2.0, r.top(), 4.0, r.height()), QColor(0, 0, 0, 110))
                    p.fillRect(QRectF(x_val - 1.0, r.top(), 2.0, r.height()), QColor(255, 255, 255, 240))
            elif has_accent_fill:
                # Unified theme-colored fill for Cores, Priority, Brightness, Saturation
                p.fillRect(fill_rect, _alpha(accent, 0.88 if en else 0.22))
                edge = x_val
                p.fillRect(QRectF(edge - 1.0, r.top(), 2.0, r.height()), _alpha(accent, 1.0 if en else 0.35))
        else:                                       # scrub: just a hairline progress under the text
            bar_h = 2.5
            if self._gradient:
                p.fillRect(QRectF(r.left(), r.bottom() - bar_h, r.width(), bar_h),
                           self._gradient_fill(r.left(), r.right(), 0.9 if en else 0.3))
                p.fillRect(QRectF(x_val - 1.5, r.bottom() - 5.5, 3.0, 5.5), QColor(t["text"]))
            else:
                if hi - lo > 0.5:
                    p.fillRect(QRectF(lo, r.bottom() - bar_h, hi - lo, bar_h), _alpha(accent, 0.95 if en else 0.3))
        if self._bidir and en and not self._gradient:
            p.fillRect(QRectF(x_org - 0.5, r.top() + 4.0, 1.0, max(2.0, r.height() - 8.0)), _alpha(t["border_strong"], 0.9))
        elif self._bidir and en and self._gradient:
            p.fillRect(QRectF(x_org - 0.5, r.bottom() - 5.5, 1.0, 5.5), _alpha(t["border_strong"], 0.9))

        if self._ticks and en:
            on_acc = QColor(t.get("on_accent", "#FFFFFF"))
            if self._segmented and self._max > self._min:
                n_seg = self._max - self._min + 1
                for i in range(1, n_seg):
                    xt = round(r.left() + (i / n_seg) * r.width()) + 0.5
                    in_fill = has_accent_fill and (lo <= xt <= hi)
                    div_c = _alpha(on_acc, 0.45) if in_fill else _alpha(t["border_strong"], 0.55)
                    notch_c = _alpha(on_acc, 0.80) if in_fill else _alpha(t["text_secondary"], 0.85)
                    p.fillRect(QRectF(xt - 0.5, r.top() + 3.0, 1.0, max(2.0, r.height() - 6.0)), div_c)
                    p.fillRect(QRectF(xt - 0.75, r.top(), 1.5, 3.0), notch_c)
                    p.fillRect(QRectF(xt - 0.75, r.bottom() - 3.0, 1.5, 3.0), notch_c)
            elif self._max > self._min:
                span = self._max - self._min
                step_cnt = span if span <= 32 else max(4, span // 2)
                for i in range(1, step_cnt):
                    xt = round(r.left() + (i / step_cnt) * r.width()) + 0.5
                    is_major = (step_cnt >= 8) and ((i % 4) == 0)
                    th = 3.2 if is_major else 2.0
                    in_fill = has_accent_fill and (lo <= xt <= hi)
                    if in_fill:
                        tc = _alpha(on_acc, 0.75 if is_major else 0.42)
                    else:
                        tc = _alpha(t["border_strong"], 0.88 if is_major else 0.55)
                    p.fillRect(QRectF(xt - 0.5, r.top(), 1.0, th), tc)
                    p.fillRect(QRectF(xt - 0.5, r.bottom() - th, 1.0, th), tc)
        p.restore()

        # border: hover/focus/drag raise it to the accent colour, like the app's other fields
        if self._dragging or (self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus
                              and self._focus_from_keyboard):
            pen = QPen(QColor(t["accent_hover"]), 1.6)
        elif self._hover and en:
            pen = QPen(accent, 1.3)
        else:
            pen = QPen(QColor(t["border"]), 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # text (split-color when accent fill is active so text stays legible over any theme color)
        pad = 8.0 if r.width() < 200.0 else 10.0
        txt_col = QColor(t["text"] if en else t["text_secondary"])
        on_acc_col = QColor(t.get("on_accent", "#FFFFFF")) if en else txt_col
        modified = self.isModified()
        val_text = self._format(self._value)
        fm_val = QFontMetricsF(self._font(modified))
        vw = fm_val.horizontalAdvance(val_text)
        fm_lbl = QFontMetricsF(self._font(False))
        avail = max(10.0, r.width() - 2 * pad - vw - 8.0)
        elided_lbl = fm_lbl.elidedText(self._label, Qt.TextElideMode.ElideRight, avail)
        val_rect = QRectF(r.right() - pad - vw - 2.0, r.top(), vw + 4.0, r.height())
        lbl_rect = QRectF(r.left() + pad, r.top(), avail, r.height())
        val_flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextDontClip
        lbl_flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextDontClip

        def _draw_texts(val_c: QColor, lbl_c: QColor) -> None:
            p.setFont(self._font(modified))
            p.setPen(val_c)
            p.drawText(val_rect, val_flags, val_text)
            p.setFont(self._font(False))
            p.setPen(lbl_c)
            p.drawText(lbl_rect, lbl_flags, elided_lbl)

        base_val_c = txt_col if (modified or not self._bidir) else _alpha(t["text_secondary"], 1.0)
        base_lbl_c = _alpha(txt_col, 0.90)
        if has_accent_fill and en:
            # 1) Unfilled regions
            if lo > r.left():
                p.save()
                p.setClipRect(QRectF(r.left(), r.top(), lo - r.left(), r.height()))
                _draw_texts(base_val_c, base_lbl_c)
                p.restore()
            if hi < r.right():
                p.save()
                p.setClipRect(QRectF(hi, r.top(), r.right() - hi, r.height()))
                _draw_texts(base_val_c, base_lbl_c)
                p.restore()
            # 2) Filled accent region
            p.save()
            p.setClipRect(fill_rect)
            _draw_texts(on_acc_col, _alpha(on_acc_col, 0.95))
            p.restore()
        else:
            _draw_texts(base_val_c, base_lbl_c)

        if style == "scrub" and (self._hover or self._dragging) and en:
            self._paint_chevrons(p, r, _alpha(t["text_secondary"], 0.9))

    @staticmethod
    def _paint_chevrons(p: QPainter, r: QRectF, color: QColor) -> None:
        cy = r.center().y()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for sign, x in ((-1, r.left() + 6.0), (1, r.right() - 6.0)):
            tri = QPainterPath()
            tri.moveTo(x + sign * 2.5, cy)
            tri.lineTo(x - sign * 1.5, cy - 3.0)
            tri.lineTo(x - sign * 1.5, cy + 3.0)
            tri.closeSubpath()
            p.drawPath(tri)

    def _paint_track(self, p: QPainter, t: dict) -> None:
        en = self.isEnabled()
        w = float(self.width())
        accent = QColor(t["accent"])
        modified = self.isModified()

        # header: label left, value chip right
        chip = self._chip_rect()
        fm = QFontMetricsF(self._font(False))
        avail = max(10.0, chip.left() - 10.0)
        p.setFont(self._font(False))
        p.setPen(QColor(t["text_secondary"] if en else t["border_strong"]))
        p.drawText(QRectF(2.0, 0.0, avail, 19.0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextDontClip,
                   fm.elidedText(self._label, Qt.TextElideMode.ElideRight, avail))

        chip_path = QPainterPath()
        chip_path.addRoundedRect(chip, 6, 6)
        p.fillPath(chip_path, QColor(t["bg_field"] if en else t["bg_disabled"]))
        p.setPen(QPen(accent if (self._hover and en and chip.contains(self._mouse_pos())) else QColor(t["border"]), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(chip_path)
        p.setFont(self._font(modified))
        p.setPen(QColor(t["text"] if en else t["text_secondary"]))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextDontClip, self._format(self._value))

        # track
        cy = 31.0
        gh = 5.0
        groove = QRectF(2.0, cy - gh / 2.0, w - 4.0, gh)
        x_val = self._x_for(self._pos_now())
        x_org = self._x_for(self._origin_or_min())
        p.setPen(Qt.PenStyle.NoPen)
        if self._gradient:
            p.setBrush(self._gradient_fill(groove.left(), groove.right(), 0.9 if en else 0.3))
            p.drawRoundedRect(groove, gh / 2.0, gh / 2.0)
        else:
            p.setBrush(QColor(t["border"]))
            p.drawRoundedRect(groove, gh / 2.0, gh / 2.0)
            lo, hi = sorted((x_org, x_val))
            if hi - lo > 0.5:
                p.setBrush(accent if en else QColor(t["border_strong"]))
                p.drawRoundedRect(QRectF(lo, groove.top(), hi - lo, gh), gh / 2.0, gh / 2.0)
        if self._bidir:
            p.setPen(QPen(_alpha(t["border_strong"], 0.95), 1.5))
            p.drawLine(QPointF(x_org, groove.top() - 2.5), QPointF(x_org, groove.bottom() + 2.5))

        d = self._handle_d()
        handle = QRectF(x_val - d / 2.0, cy - d / 2.0, d, d)
        if self._dragging:
            pen = QPen(QColor(t["accent_hover"]), 1.5)
        elif self._hover and en and not chip.contains(self._mouse_pos()):
            pen = QPen(accent, 1.5)
        else:
            pen = QPen(QColor(t["border"]), 1.0)
        p.setPen(pen)
        p.setBrush(QColor(t["knob_fg"]) if en else QColor(t["border"]))
        p.drawEllipse(handle)
        if self.hasFocus() and self._focus_from_keyboard:
            p.setPen(QPen(_alpha(accent, 0.55), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(handle.adjusted(-3, -3, 3, 3))

    # -- hover / focus bookkeeping -------------------------------------------
    _focus_from_keyboard = False

    def _mouse_pos(self) -> QPointF:
        return QPointF(self.mapFromGlobal(QCursor.pos()))

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._focus_from_keyboard = event.reason() not in (
            Qt.FocusReason.MouseFocusReason, Qt.FocusReason.OtherFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._focus_from_keyboard = False
        self.update()
        super().focusOutEvent(event)

    # -- mouse ---------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled() or self._editing:
            super().mousePressEvent(event)
            return
        pos = event.position()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self.style_name() == "track" and self._chip_rect().contains(pos):
            self._begin_edit()
            event.accept()
            return
        self._dragging = True
        self._relative = self.style_name() == "scrub"
        self._value_before_press = self._value
        self._press_x = pos.x()
        self._press_val = self._value
        self._anchor_x = pos.x()
        self._anchor_val = float(self._value)
        if self.style_name() in ("fill", "track"):
            self._set_value(self._quantize(self._value_for_x(pos.x())), animate=False)
            self._anchor_val = float(self._value)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            if self.style_name() == "track":
                self.setCursor(Qt.CursorShape.IBeamCursor if self._chip_rect().contains(event.position())
                               else Qt.CursorShape.ArrowCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.update()
            super().mouseMoveEvent(event)
            return
        x = event.position().x()
        span = self._max - self._min
        style = self.style_name()
        width = (self._track_x_range()[1] - self._track_x_range()[0]) if style == "track" else self._field_rect().width()
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if fine and not self._relative:
            # switch to relative mode from the current value, so nothing jumps when Shift goes down
            self._relative = True
            self._anchor_x, self._anchor_val = x, float(self._value)
        if self._relative:
            gain = 0.2 if fine else 1.0
            v = self._anchor_val + (x - self._anchor_x) / max(1.0, width) * span * gain
            self._set_value(self._quantize(v), animate=False)
            if fine:                        # fine mode integrates small steps, so re-anchor every move
                self._anchor_x, self._anchor_val = x, float(self._value)
        else:
            self._set_value(self._quantize(self._value_for_x(x)), animate=False)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self._relative = False
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._dragging = False
            if self._choices:
                self.reset()
            else:
                # the first click of the double-click already jumped the value - undo that
                self._set_value(self._value_before_press, animate=False)
                self._begin_edit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        if not self.hasFocus() or not self.isEnabled():
            event.ignore()                  # let the surrounding panel scroll instead
            return
        notches = event.angleDelta().y() / 120.0
        if notches:
            mult = 5 if event.modifiers() & Qt.KeyboardModifier.ControlModifier else 1
            self.setValue(self._value + int(round(notches)) * self._step * mult)
        event.accept()

    def keyPressEvent(self, event):
        k = event.key()
        big = max(self._step, int(round((self._max - self._min) / 10.0 / self._step)) * self._step)
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.setValue(self._value + self._step)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self.setValue(self._value - self._step)
        elif k == Qt.Key.Key_PageUp:
            self.setValue(self._value + big)
        elif k == Qt.Key.Key_PageDown:
            self.setValue(self._value - big)
        elif k == Qt.Key.Key_Home:
            self.setValue(self._min)
        elif k == Qt.Key.Key_End:
            self.setValue(self._max)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            self._begin_edit()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.reset()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # -- inline number entry ---------------------------------------------------
    def _ensure_editor(self) -> QLineEdit:
        if self._editor is None:
            ed = QLineEdit(self)
            ed.setObjectName("paramEdit")
            ed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            ed.installEventFilter(self)
            ed.editingFinished.connect(self._on_editing_finished)
            ed.hide()
            self._editor = ed
        return self._editor

    def _style_editor(self, ed: QLineEdit) -> None:
        t = _tokens(self)
        ed.setStyleSheet(
            f"QLineEdit#paramEdit {{ background: {t['bg_card']}; color: {t['text']};"
            f" border: 1.5px solid {t['accent']}; border-radius: 5px; padding: 0px 6px; }}")
        ed.setFont(self._font(True))

    def _begin_edit(self) -> None:
        if self._editing or not self.isEnabled():
            return
        self._editing = True
        self._edit_cancelled = False
        ed = self._ensure_editor()
        self._style_editor(ed)
        if self.style_name() == "track":
            chip = self._chip_rect()
            ed.setGeometry(chip.adjusted(-6, 0, 0, 0).toRect())
        else:
            r = self._field_rect()
            w = min(r.width() - 12.0, 92.0)
            ed.setGeometry(QRectF(r.right() - w - 3.0, r.top() + 1.0, w, max(16.0, r.height() - 2.0)).toRect())
        ed.setText(str(self._value))
        ed.show()
        ed.setFocus(Qt.FocusReason.OtherFocusReason)
        ed.selectAll()

    def _end_edit(self, commit: bool) -> None:
        if not self._editing:
            return
        ed = self._editor
        if commit and ed is not None:
            m = re.search(r"[-+\u2212]?\s*\d+(?:[.,]\d+)?", ed.text())
            if m:
                raw = m.group().replace("\u2212", "-").replace(" ", "").replace(",", ".")
                try:
                    v = int(round(float(raw)))
                    self._set_value(max(self._min, min(self._max, v)), animate=True)
                except ValueError:
                    pass
        self._editing = False
        if ed is not None:
            ed.hide()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def _on_editing_finished(self) -> None:
        if self._editing:
            self._end_edit(commit=not self._edit_cancelled)

    def eventFilter(self, obj, event):
        if obj is self._editor and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._edit_cancelled = True
                self._end_edit(commit=False)
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # QLineEdit ignores Return after emitting editingFinished, so the key would travel up
                # to this widget and reopen the editor. Commit here and swallow it.
                self._end_edit(commit=True)
                return True
        return super().eventFilter(obj, event)

    # -- context menu ------------------------------------------------------------
    def build_context_menu(self) -> QMenu:
        return build_style_menu(
            self, ParamSlider._global_style, self.pad_capable, self._choose_style,
            reset_text=_translate(self, "Reset to default"), on_reset=self.reset,
            reset_enabled=self.isModified())

    def contextMenuEvent(self, event):
        self.build_context_menu().exec(event.globalPos())
        event.accept()

    def _choose_style(self, key: str) -> None:
        self.styleChosen.emit(key)
        ParamSlider.set_global_style(key)


# ============================================================================
# ColorPad2D
# ============================================================================

class ColorPad2D(QWidget):
    """Brightness (vertical) x saturation (horizontal) on one 2-D pad.

    Works on two existing controls (anything with value/setValue/minimum/
    maximum/valueChanged/text) and keeps in step with them in both directions.
    Tracks continuous sub-pixel floating-point coordinates while dragging so
    the knob glides smoothly without stepping on the integer grid, and keeps
    caption positions anchored so text never jumps when values change."""

    styleChosen = Signal(str)
    HEIGHT = 128

    # 4x4 ordered Bayer matrix (normalized to -0.5..+0.5 LSB) to eliminate 8-bit gradient banding
    _BAYER4 = (
        (-0.46875,  0.03125, -0.34375,  0.15625),
        ( 0.28125, -0.21875,  0.40625, -0.09375),
        (-0.28125,  0.21875, -0.40625,  0.09375),
        ( 0.46875, -0.03125,  0.34375, -0.15625),
    )

    def __init__(self, x_ctrl, y_ctrl, parent=None):
        super().__init__(parent)
        self._x, self._y = x_ctrl, y_ctrl
        self._dragging = False
        self._fine_drag = False
        self._anchor_pos = QPointF(0.0, 0.0)
        self._anchor_fx = 0.5
        self._anchor_fy = 0.5
        self._hover = False
        self._fx = self._model_fx()
        self._fy = self._model_fy()
        self._bg_cache_key = None
        self._bg_cache_pm: QPixmap | None = None
        self.setFixedHeight(self.HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        x_ctrl.valueChanged.connect(self._on_x_model_changed)
        y_ctrl.valueChanged.connect(self._on_y_model_changed)

    def _model_fx(self) -> float:
        span = max(1, self._x.maximum() - self._x.minimum())
        return max(0.0, min(1.0, (self._x.value() - self._x.minimum()) / span))

    def _model_fy(self) -> float:
        span = max(1, self._y.maximum() - self._y.minimum())
        return max(0.0, min(1.0, (self._y.value() - self._y.minimum()) / span))

    def _quantize_x(self, fx: float) -> int:
        return self._x.minimum() + round(max(0.0, min(1.0, fx)) * (self._x.maximum() - self._x.minimum()))

    def _quantize_y(self, fy: float) -> int:
        return self._y.minimum() + round(max(0.0, min(1.0, fy)) * (self._y.maximum() - self._y.minimum()))

    def _on_x_model_changed(self, val: int) -> None:
        if not self._dragging and self._quantize_x(self._fx) != val:
            self._fx = self._model_fx()
        self.update()

    def _on_y_model_changed(self, val: int) -> None:
        if not self._dragging and self._quantize_y(self._fy) != val:
            self._fy = self._model_fy()
        self.update()

    # geometry
    def _area(self) -> QRectF:
        return QRectF(1.0, 1.0, max(1.0, self.width() - 2.0), max(1.0, self.height() - 2.0))

    def _inner(self) -> QRectF:
        return self._area().adjusted(16, 14, -16, -16)

    def _point_for(self) -> QPointF:
        r = self._inner()
        fx = self._fx if self._dragging else self._model_fx()
        fy = self._fy if self._dragging else self._model_fy()
        return QPointF(r.left() + fx * r.width(), r.bottom() - fy * r.height())

    def _visual_point(self) -> QPointF:
        r = self._inner()
        if not self._dragging:
            # Keep sub-pixel position if it still rounds to the current model integer
            if self._quantize_x(self._fx) != self._x.value():
                self._fx = self._model_fx()
            if self._quantize_y(self._fy) != self._y.value():
                self._fy = self._model_fy()
        return QPointF(r.left() + self._fx * r.width(), r.bottom() - self._fy * r.height())

    def _apply_fractions(self, fx: float, fy: float) -> None:
        self._fx = max(0.0, min(1.0, fx))
        self._fy = max(0.0, min(1.0, fy))
        self._x.setValue(self._quantize_x(self._fx))
        self._y.setValue(self._quantize_y(self._fy))
        self.update()

    def _set_from(self, pos: QPointF, fine: bool = False) -> None:
        r = self._inner()
        w = max(1.0, r.width())
        h = max(1.0, r.height())
        if fine:
            if not self._fine_drag:
                self._fine_drag = True
                self._anchor_pos = QPointF(pos)
                self._anchor_fx = self._fx
                self._anchor_fy = self._fy
            gain = 0.22
            fx = self._anchor_fx + (pos.x() - self._anchor_pos.x()) / w * gain
            fy = self._anchor_fy + (self._anchor_pos.y() - pos.y()) / h * gain
            self._anchor_pos = QPointF(pos)
            self._anchor_fx = max(0.0, min(1.0, fx))
            self._anchor_fy = max(0.0, min(1.0, fy))
            self._apply_fractions(self._anchor_fx, self._anchor_fy)
        else:
            self._fine_drag = False
            fx = (pos.x() - r.left()) / w
            fy = (r.bottom() - pos.y()) / h
            self._apply_fractions(fx, fy)

    def isModified(self) -> bool:
        return self._x.value() != 0 or self._y.value() != 0

    def reset(self) -> None:
        self._fx = 0.5
        self._fy = 0.5
        self._x.setValue(0)
        self._y.setValue(0)
        self.update()

    def _backdrop_pixmap(self, w: int, h: int, t: dict, en: bool) -> QPixmap:
        """Build a cached, smooth 2-D saturation x brightness backdrop with ordered
        dithering so there is zero 8-bit Mach banding in Dark or Light themes."""
        bg_c = QColor(t["bg_field"] if en else t["bg_disabled"])
        acc_c = QColor(t["accent"])
        sec_c = QColor(t["text_secondary"])
        is_dark = bool(t.get("is_dark", True))
        key = (w, h, bg_c.rgba(), acc_c.rgba(), sec_c.rgba(), is_dark, en)
        if self._bg_cache_key == key and self._bg_cache_pm is not None:
            return self._bg_cache_pm

        # Render at 1/2 resolution with Bayer dithering, then smooth-scale to (w, h)
        gw = max(24, min(160, (w + 1) // 2))
        gh = max(16, min(80, (h + 1) // 2))
        img = QImage(gw, gh, QImage.Format.Format_ARGB32)

        br, bg_g, bb = bg_c.redF(), bg_c.greenF(), bg_c.blueF()
        ar, ag, ab = acc_c.redF(), acc_c.greenF(), acc_c.blueF()
        sr, sg, sb = sec_c.redF(), sec_c.greenF(), sec_c.blueF()
        bayer = self._BAYER4

        for iy in range(gh):
            # fy: 0.0 at top (bright), 1.0 at bottom (dark)
            fy = iy / max(1, gh - 1)
            # Smooth Hermite curve for vertical brightness
            sy = fy * fy * (3.0 - 2.0 * fy)
            # Top lifts luminance smoothly; bottom deepens shadow smoothly
            if is_dark:
                lum_shift = (1.0 - sy) * 0.11 - sy * 0.16
            else:
                lum_shift = (1.0 - sy) * 0.06 - sy * 0.11
            b_row = bayer[iy & 3]

            for ix in range(gw):
                # fx: 0.0 at left (desaturated), 1.0 at right (saturated)
                fx = ix / max(1, gw - 1)
                sx = fx * fx * (3.0 - 2.0 * fx)

                if en:
                    # Blend from cool muted neutral on the left to rich accent tint on the right
                    w_sec = (1.0 - sx) * (0.14 if is_dark else 0.10)
                    w_acc = sx * (0.26 if is_dark else 0.22)
                    w_bg = 1.0 - w_sec - w_acc
                    r = br * w_bg + sr * w_sec + ar * w_acc + lum_shift
                    g = bg_g * w_bg + sg * w_sec + ag * w_acc + lum_shift
                    b = bb * w_bg + sb * w_sec + ab * w_acc + lum_shift
                else:
                    r = br + lum_shift * 0.35
                    g = bg_g + lum_shift * 0.35
                    b = bb + lum_shift * 0.35

                dith = b_row[ix & 3] / 255.0
                ri = max(0, min(255, int(round((r + dith) * 255.0))))
                gi = max(0, min(255, int(round((g + dith) * 255.0))))
                bi = max(0, min(255, int(round((b + dith) * 255.0))))
                img.setPixel(ix, iy, (0xFF << 24) | (ri << 16) | (gi << 8) | bi)

        scaled = img.scaled(max(1, w), max(1, h), Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        pm = QPixmap.fromImage(scaled)
        self._bg_cache_key = key
        self._bg_cache_pm = pm
        return pm

    # painting
    def paintEvent(self, _event):
        t = _tokens(self)
        en = self.isEnabled()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = self._area()
        rad = float(t.get("radius_button", 8)) + 2.0
        path = QPainterPath()
        path.addRoundedRect(area, rad, rad)
        accent = QColor(t["accent"])
        c = self._visual_point()

        p.save()
        p.setClipPath(path)
        pm = self._backdrop_pixmap(int(area.width()) + 2, int(area.height()) + 2, t, en)
        p.drawPixmap(area.toRect(), pm)

        # Soft radial glow following the knob for smooth visual feedback
        if en:
            glow = QRadialGradient(c, 46.0)
            glow.setColorAt(0.0, _alpha(accent, 0.24 if (self._dragging or self._hover) else 0.15))
            glow.setColorAt(0.55, _alpha(accent, 0.07))
            glow.setColorAt(1.0, _alpha(accent, 0.0))
            p.fillRect(area, glow)

        inner = self._inner()
        cx0, cy0 = inner.center().x(), inner.center().y()
        # Neutral origin crosshairs
        p.setPen(QPen(_alpha(t["border_strong"], 0.75 if en else 0.35), 1.0, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(cx0, area.top() + 14), QPointF(cx0, area.bottom() - 16))
        p.drawLine(QPointF(area.left() + 16, cy0), QPointF(area.right() - 16, cy0))

        # Subtle active crosshair guides through the knob while dragging/hovering
        if en and (self._dragging or self._hover or self.isModified()):
            guide_a = 0.38 if self._dragging else 0.20
            p.setPen(QPen(_alpha(accent, guide_a), 1.0, Qt.PenStyle.SolidLine))
            if abs(c.x() - cx0) > 1.5 or abs(c.y() - cy0) > 1.5:
                p.drawLine(QPointF(cx0, cy0), c)
        p.restore()

        p.setPen(QPen(QColor(t["accent"]) if (self._hover or self._dragging) and en else QColor(t["border"]), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # Captions: fixed value slot width and painter-device boundingRect so text NEVER jumps
        cap = QFont(self.font())
        cap.setPointSizeF(8.0)
        cap.setWeight(QFont.Weight.Normal)
        val = QFont(cap)
        val.setWeight(QFont.Weight.DemiBold)
        val_col = QColor(t["text"] if en else t["text_secondary"])
        lab_col = _alpha(t["text_secondary"], 0.95)

        def caption(painter: QPainter, rect: QRectF, label: str, value_text: str) -> None:
            flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextDontClip
            painter.setFont(cap)
            lbl_w = painter.boundingRect(QRectF(0, 0, 1000.0, rect.height()), flags, label).width()
            painter.setFont(val)
            val_slot_w = max(
                painter.boundingRect(QRectF(0, 0, 1000.0, rect.height()), flags, s).width()
                for s in ("+20", "−20", "0")
            )
            gap = 5.0
            total = lbl_w + gap + val_slot_w
            x0 = round(rect.center().x() - total / 2.0)
            x_val = round(x0 + lbl_w + gap)

            painter.setFont(cap)
            painter.setPen(lab_col)
            painter.drawText(QRectF(x0, rect.top(), lbl_w + 4.0, rect.height()), flags, label)
            painter.setFont(val)
            painter.setPen(val_col)
            painter.drawText(QRectF(x_val, rect.top(), val_slot_w + 8.0, rect.height()), flags, value_text)

        caption(p, QRectF(area.left(), area.bottom() - 16, area.width(), 14),
                self._x.text(), _fmt_signed(self._x.value()))
        p.save()
        p.translate(area.left() + 3, area.center().y())
        p.rotate(-90)
        caption(p, QRectF(-area.height() / 2, 0, area.height(), 14),
                self._y.text(), _fmt_signed(self._y.value()))
        p.restore()

        # Axis ends: + brighter / more saturated, - the opposite
        p.setFont(val)
        p.setPen(lab_col)
        p.drawText(QRectF(area.right() - 16, cy0 - 8, 14, 16), Qt.AlignmentFlag.AlignCenter, "+")
        p.drawText(QRectF(area.left() + 17, cy0 - 8, 14, 16), Qt.AlignmentFlag.AlignCenter, "−")
        p.drawText(QRectF(cx0 - 7, area.top() + 1, 14, 14), Qt.AlignmentFlag.AlignCenter, "+")
        p.drawText(QRectF(cx0 - 7, area.bottom() - 30, 14, 14), Qt.AlignmentFlag.AlignCenter, "−")

        # Knob
        d = 16.0
        knob = QRectF(c.x() - d / 2, c.y() - d / 2, d, d)
        if (self.hasFocus() or self._dragging) and en:
            p.setPen(QPen(_alpha(accent, 0.45), 2.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(knob.adjusted(-3.0, -3.0, 3.0, 3.0))
        p.setPen(QPen(QColor(t["accent_hover"] if self._dragging else (t["accent"] if self._hover and en else t["border"])), 1.6))
        p.setBrush(QColor(t["knob_fg"]) if en else QColor(t["border"]))
        p.drawEllipse(knob)
        p.end()

    # input
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._dragging = True
            self._fine_drag = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._set_from(e.position(), fine=bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._set_from(e.position(), fine=bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self._fine_drag = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.update()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._dragging = False
            self._fine_drag = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.reset()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Right:
            self._x.setValue(self._x.value() + 1)
            self._fx = self._model_fx()
        elif k == Qt.Key.Key_Left:
            self._x.setValue(self._x.value() - 1)
            self._fx = self._model_fx()
        elif k == Qt.Key.Key_Up:
            self._y.setValue(self._y.value() + 1)
            self._fy = self._model_fy()
        elif k == Qt.Key.Key_Down:
            self._y.setValue(self._y.value() - 1)
            self._fy = self._model_fy()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.reset()
        else:
            super().keyPressEvent(e)
            return
        self.update()
        e.accept()

    def wheelEvent(self, e):
        e.ignore()

    def build_context_menu(self) -> QMenu:
        return build_style_menu(
            self, ParamSlider.global_style(), True, self._choose_style,
            reset_text=_translate(self, "Reset to default"), on_reset=self.reset,
            reset_enabled=self.isModified())

    def contextMenuEvent(self, e):
        self.build_context_menu().exec(e.globalPos())
        e.accept()

    def _choose_style(self, key: str) -> None:
        self.styleChosen.emit(key)
        ParamSlider.set_global_style(key)


# ============================================================================
# TierSlider  (Quality / CRF — unified compact pill bar)
# ============================================================================

class TierSlider(RoundHandleSlider):
    """CRF slider whose track is divided into named quality bands inside a
    unified compact pill bar matching ``ParamSlider`` (Cores, Priority, Colour
    temperature).

    Left = lowest quality, right = best (the value itself runs the other way,
    so the slider is built with invertedAppearance, like the one it replaces).
    Every band gets the same width and its own labelled stretch of the CRF
    scale - "High" (17-21 on the 0-51 scale) is only five values wide and
    would be a sliver on a linear track, while "Low" spans 24. Clicking a
    band label jumps to that band's representative value; dragging anywhere
    scrubs continuously across the CRF range."""

    HANDLE_D = 14
    TRACK_H = 13
    LABEL_H = 13
    HEIGHT = 26

    def __init__(self, *args, **kwargs):
        super().__init__(Qt.Orientation.Horizontal, *args, **kwargs)
        self._tiers: list[dict] = []
        self._labeler = lambda s: s
        self._hover_label: int | None = None
        self._bar_hover = False
        self.setProperty("role", "tier-slider")
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(220, self.HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(80, self.HEIGHT)

    # -- configuration --------------------------------------------------------
    def set_tiers(self, bounds, reps: dict | None = None, labeler=None) -> None:
        """``bounds``: ((lo, hi, label_key), ...) ascending by CRF.
        ``reps``: label_key -> representative CRF used by a label click."""
        if labeler is not None:
            self._labeler = labeler
        tiers = []
        for lo, hi, key in reversed(list(bounds or ())):      # worst (highest CRF) on the left
            rep = (reps or {}).get(key, (lo + hi) // 2)
            tiers.append({"lo": int(lo), "hi": int(hi), "key": key, "rep": max(lo, min(hi, int(rep)))})
        covers = (tiers and min(b["lo"] for b in tiers) <= self.minimum()
                  and max(b["hi"] for b in tiers) >= self.maximum())
        # clip the outer bands to the real range so a 0-51 scale never shows 52-63 cells
        if covers:
            for b in tiers:
                b["lo"] = max(b["lo"], self.minimum())
                b["hi"] = min(b["hi"], self.maximum())
            tiers = [b for b in tiers if b["hi"] >= b["lo"]]
        self._tiers = tiers if covers else []
        self.update()

    def set_labeler(self, fn) -> None:
        self._labeler = fn
        self.update()

    def tiers(self) -> list[dict]:
        return list(self._tiers)

    def active_tier_index(self) -> int | None:
        v = self.value()
        for i, b in enumerate(self._tiers):
            if b["lo"] <= v <= b["hi"]:
                return i
        return None

    # -- geometry ----------------------------------------------------------------
    def _field_rect(self) -> QRectF:
        return QRectF(1.0, 1.0, max(1.0, self.width() - 2.0), max(1.0, self.height() - 2.0))

    def _groove_rect(self) -> QRectF:
        return self._field_rect()

    def _x_range(self) -> tuple[float, float]:
        r = self._field_rect()
        return r.left(), r.right()

    def _frac_for(self, v: float) -> float:
        lo, hi = self.minimum(), self.maximum()
        if not self._tiers:
            f = (v - lo) / (hi - lo) if hi > lo else 1.0
            return 1.0 - f if self.invertedAppearance() else f
        v = max(float(lo), min(float(hi), v))
        a, b = int(v // 1), int(-(-v // 1))
        if a != b:
            fa, fb = self._frac_for(float(a)), self._frac_for(float(b))
            return fa + (fb - fa) * (v - a)
        n_t = len(self._tiers)
        for i, b in enumerate(self._tiers):
            if b["lo"] <= a <= b["hi"]:
                n = b["hi"] - b["lo"] + 1
                return (i + (b["hi"] - a + 0.5) / n) / n_t
        return 1.0

    def _value_for_frac(self, f: float) -> int:
        lo, hi = self.minimum(), self.maximum()
        f = max(0.0, min(1.0, f))
        if not self._tiers:
            if self.invertedAppearance():
                f = 1.0 - f
            return int(round(lo + f * (hi - lo)))
        n_t = len(self._tiers)
        i = min(int(f * n_t), n_t - 1)
        b = self._tiers[i]
        n = b["hi"] - b["lo"] + 1
        k = min(int((f * n_t - i) * n), n - 1)
        return b["hi"] - k

    def _handle_rect(self) -> QRectF:
        x0, x1 = self._x_range()
        cx = x0 + self._frac_for(self._effective_pos()) * (x1 - x0)
        cy = self.height() / 2.0
        d = self.HANDLE_D
        return QRectF(cx - d / 2.0, cy - d / 2.0, d, d)

    def _label_rects(self) -> list[QRectF]:
        if not self._tiers:
            return []
        r = self._field_rect()
        bw = r.width() / len(self._tiers)
        return [QRectF(r.left() + i * bw, r.top(), bw, r.height()) for i in range(len(self._tiers))]

    def _label_at(self, pos) -> int | None:
        for i, r in enumerate(self._label_rects()):
            if r.contains(pos):
                return i
        return None

    # -- painting ------------------------------------------------------------------
    def paintEvent(self, _event):
        t = window_tokens(self.window())
        en = self.isEnabled()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = self._field_rect()
        rad = min(r.height() / 2.0, float(t.get("radius_button", 8)) + 2.0)
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, QColor(t["bg_field"] if en else t["bg_disabled"]))

        accent = QColor(t["accent"])
        on_acc = QColor(t.get("on_accent", "#FFFFFF"))
        x0, x1 = self._x_range()
        pos = self._effective_pos()
        if (self.invertedAppearance() and pos <= self.minimum()) or (not self.invertedAppearance() and pos >= self.maximum()):
            x_val = r.right()
            fill_w = r.width()
        elif (self.invertedAppearance() and pos >= self.maximum()) or (not self.invertedAppearance() and pos <= self.minimum()):
            x_val = r.left()
            fill_w = 0.0
        else:
            x_val = x0 + self._frac_for(pos) * (x1 - x0)
            fill_w = max(0.0, min(r.width(), x_val - r.left()))
        fill_rect = QRectF(r.left(), r.top(), fill_w, r.height())

        p.save()
        p.setClipPath(path)
        if fill_w > 0.5:
            p.fillRect(fill_rect, _alpha(accent, 0.88 if en else 0.22))
            p.fillRect(QRectF(x_val - 1.0, r.top(), 2.0, r.height()), _alpha(accent, 1.0 if en else 0.35))

        n_t = len(self._tiers)
        if n_t:
            bw = r.width() / n_t
            for i in range(1, n_t):                      # band dividers + top/bottom notches (засечки)
                xt = round(r.left() + i * bw) + 0.5
                in_fill = en and (xt <= x_val)
                div_c = _alpha(on_acc, 0.45) if in_fill else _alpha(t["border_strong"], 0.55)
                notch_c = _alpha(on_acc, 0.80) if in_fill else _alpha(t["text_secondary"], 0.85)
                p.fillRect(QRectF(xt - 0.5, r.top() + 3.0, 1.0, max(2.0, r.height() - 6.0)), div_c)
                p.fillRect(QRectF(xt - 0.75, r.top(), 1.5, 3.0), notch_c)
                p.fillRect(QRectF(xt - 0.75, r.bottom() - 3.0, 1.5, 3.0), notch_c)
        p.restore()

        if self.isSliderDown() or (self.hasFocus() and en):
            pen = QPen(QColor(t["accent_hover"]), 1.6)
        elif self._bar_hover and en:
            pen = QPen(accent, 1.3)
        else:
            pen = QPen(QColor(t["border"]), 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # band labels inside the unified pill bar (split-color over filled vs unfilled region)
        active = self.active_tier_index()
        base = QFont(self.font())
        base.setPointSizeF(8.5)
        rects = self._label_rects()
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextDontClip

        def _draw_band_labels(filled_pass: bool) -> None:
            for i, lr in enumerate(rects):
                f = QFont(base)
                is_active = (i == active)
                f.setWeight(QFont.Weight.DemiBold if is_active else QFont.Weight.Normal)
                f.setUnderline(self._hover_label == i and en)
                p.setFont(f)
                if not en:
                    col = QColor(t["border_strong"])
                elif filled_pass:
                    col = on_acc if is_active else _alpha(on_acc, 0.90)
                else:
                    col = QColor(t["text"] if is_active else t["text_secondary"])
                p.setPen(col)
                text = self._labeler(self._tiers[i]["key"])
                text = QFontMetricsF(f).elidedText(text, Qt.TextElideMode.ElideRight, lr.width() - 4)
                p.drawText(lr, flags, text)

        if fill_w > 0.5 and en:
            if x_val < r.right():
                p.save()
                p.setClipRect(QRectF(x_val, r.top(), r.right() - x_val, r.height()))
                _draw_band_labels(False)
                p.restore()
            p.save()
            p.setClipRect(fill_rect)
            _draw_band_labels(True)
            p.restore()
        else:
            _draw_band_labels(False)

        p.end()

    # -- input ---------------------------------------------------------------------
    def _apply_x(self, x: float) -> None:
        x0, x1 = self._x_range()
        self.setSliderPosition(self._value_for_frac((x - x0) / max(1.0, x1 - x0)))

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            super().mousePressEvent(e)
            return
        pos = e.position()
        idx = self._label_at(pos)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setSliderDown(True)
        if idx is not None:
            lr = self._label_rects()[idx]
            near_label_center = abs(pos.x() - lr.center().x()) <= lr.width() * 0.35
            if pos.y() > self.TRACK_H - 1 or near_label_center:
                self.setValue(self._tiers[idx]["rep"])
                e.accept()
                return
        self._apply_x(pos.x())
        e.accept()

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            self._apply_x(e.position().x())
            e.accept()
            return
        idx = self._label_at(e.position())
        if idx != self._hover_label:
            self._hover_label = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx is not None else Qt.CursorShape.SizeHorCursor)
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.isSliderDown():
            self.setSliderDown(False)
            self.update()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):
        self._bar_hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._bar_hover = False
        if self._hover_label is not None:
            self._hover_label = None
        self.update()
        super().leaveEvent(e)


# ============================================================================
# ScrubSpinBox / value chip
# ============================================================================

class ScrubSpinBox(QSpinBox):
    """Flat number field: drag sideways to change the value, click to type.

    Typing, the arrow keys and the wheel (only while focused, same as every
    other control in the app) keep working exactly like a QSpinBox - this is
    still one, with the up/down buttons gone."""

    def __init__(self, prefix: str = "", suffix: str = "", px_per_step: float = 6.0, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPrefix(prefix)
        self.setSuffix(suffix)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._px_per_step = float(px_per_step)
        self._press: tuple[float, int, bool] | None = None
        self._scrubbing = False
        le = self.lineEdit()
        le.installEventFilter(self)
        le.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        # Colours/border come from the app-wide QSpinBox rule; only the padding is
        # overridden, and the (hidden) buttons get no width so the edit field fills the chip.
        self.setObjectName("scrubChip")
        self.setStyleSheet("QSpinBox#scrubChip { padding: 0px 4px; }"
                           " QSpinBox#scrubChip:focus { padding: 0px 3px; }")

    def _fit_edit_field(self) -> None:
        # With the app's proxy style + QSS, Qt reserves the (hidden) button area and leaves the
        # edit field ~1 px wide. The chip has no buttons, so give the field the whole interior.
        r = self.rect().adjusted(2, 2, -2, -2)
        if self.lineEdit().geometry() != r:
            self.lineEdit().setGeometry(r)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_edit_field()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_edit_field()

    def event(self, e):
        handled = super().event(e)
        if e.type() in (QEvent.Type.StyleChange, QEvent.Type.Polish, QEvent.Type.LayoutRequest):
            self._fit_edit_field()
        return handled

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.lineEdit():
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._press = (event.globalPosition().x(), self.value(), obj.hasFocus())
                self._scrubbing = False
            elif (et == QEvent.Type.MouseMove and self._press is not None
                  and (event.buttons() & Qt.MouseButton.LeftButton)):
                dx = event.globalPosition().x() - self._press[0]
                if not self._scrubbing and abs(dx) < 4:
                    return False
                self._scrubbing = True
                px = self._px_per_step * (4.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0)
                self.setValue(self._press[1] + int(dx / px) * self.singleStep())
                return True
            elif et == QEvent.Type.MouseButtonRelease and self._press is not None:
                scrubbed = self._scrubbing
                self._press, self._scrubbing = None, False
                if scrubbed:
                    obj.deselect()
                    return True
                QTimer.singleShot(0, self.selectAll)    # plain click: ready to type
        return super().eventFilter(obj, event)


def make_value_chip(spin: ScrubSpinBox, width: int = 96, height: int = 26) -> QWidget:
    """Wrap a ScrubSpinBox in a plain fixed-size holder (``.spin`` gives it back)."""
    spin.setFixedSize(width, height)
    wrap = QWidget()
    wrap.setObjectName("valueChipWrap")
    wrap.setStyleSheet("QWidget#valueChipWrap { background: transparent; }")
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(spin)
    wrap.spin = spin
    wrap.setFixedSize(width, height)
    wrap.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    original = spin.setEnabled

    def set_enabled(flag):          # keep the old make_stepper() contract: enabling the spin enables the holder
        original(flag)

    spin.setEnabled = set_enabled
    return wrap


class LinkButton(QPushButton):
    """Text-only button ("Reset") that takes its colours from the theme."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        f = QFont(self.font())
        f.setPointSizeF(9.0)
        return QSize(int(QFontMetricsF(f).horizontalAdvance(self.text())) + 14, 22)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event):
        t = _tokens(self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPointSizeF(9.0)
        hot = self.underMouse() and self.isEnabled()
        f.setUnderline(hot)
        p.setFont(f)
        p.setPen(QColor(t["accent"] if hot else (t["text_secondary"] if self.isEnabled() else t["border_strong"])))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus():
            p.setPen(QPen(_alpha(t["accent"], 0.6), 1.2))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 2, -1, -2), 5, 5)
        p.end()


class _TimeBubble(QLabel):
    """Small floating label that shows the time under the mouse."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def show_at(self, text: str, global_pos, tokens: dict) -> None:
        self.setStyleSheet(
            f"QLabel {{ background: {tokens['bg_card_alt']}; color: {tokens['text']};"
            f" border: 1px solid {tokens['border_strong']}; border-radius: 6px; padding: 2px 8px; }}")
        self.setText(text)
        self.adjustSize()
        self.move(int(global_pos.x() - self.width() / 2), int(global_pos.y() - self.height() - 14))
        if not self.isVisible():
            self.show()


class TimelineSlider(PreviewScrubSlider):
    """Live-preview position slider: click anywhere on the track to jump there
    (a plain QSlider only pages towards the click), and hovering shows the
    time under the mouse in a small bubble. ``formatter`` turns seconds into text.
    Everything else (value/values/low/high/setValues) is PreviewScrubSlider."""

    def __init__(self, *args, formatter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._fmt = formatter or (lambda s: str(int(s)))
        self._bubble: _TimeBubble | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_formatter(self, fn) -> None:
        self._fmt = fn

    def _x_range(self) -> tuple[float, float]:
        g = self._groove_rect()
        d = self.HANDLE_D
        return g.left() + d / 2.0, g.right() - d / 2.0

    def value_at(self, x: float) -> int:
        x0, x1 = self._x_range()
        f = max(0.0, min(1.0, (x - x0) / max(1.0, x1 - x0)))
        return int(round(self.minimum() + f * (self.maximum() - self.minimum())))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled() and self.maximum() > self.minimum():
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.setSliderDown(True)
            self.setSliderPosition(self.value_at(e.position().x()))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            self.setSliderPosition(self.value_at(e.position().x()))
            self._show_bubble(e.position().x())
            e.accept()
            return
        if self.isEnabled() and self.maximum() > self.minimum():
            self._show_bubble(e.position().x())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.isSliderDown():
            self.setSliderDown(False)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        if self._bubble is not None:
            self._bubble.hide()
        super().leaveEvent(e)

    def hideEvent(self, e):
        if self._bubble is not None:
            self._bubble.hide()
        super().hideEvent(e)

    def _show_bubble(self, x: float) -> None:
        if self._bubble is None:
            self._bubble = _TimeBubble()
        x0, x1 = self._x_range()
        x = max(x0, min(x1, x))
        top = self.mapToGlobal(QPointF(x, 0).toPoint())
        self._bubble.show_at(self._fmt(self.value_at(x)), top, _tokens(self))
