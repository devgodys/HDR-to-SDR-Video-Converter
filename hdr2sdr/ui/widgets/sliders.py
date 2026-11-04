from __future__ import annotations

from PySide6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, Property, QRectF, QPointF, QObject, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QPainter, QPen, QBrush, QLinearGradient,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QSlider,
)

from hdr2sdr.theme.theme_tokens import window_tokens




class RoundHandleSlider(QSlider):
    """QSlider whose handle is hand-painted, not QSS-boxed.

    ``QSlider::handle { width: 26px; height: 26px; }`` looks like a
    perfect square on paper, but at a fractional Windows display-scale
    factor Qt can round the box's width and height to a different number
    of *physical* pixels from each other - the QSS box model resolves
    each edge independently rather than through one shared transform.
    The result is a handle that measures ~8% wider than tall on screen
    (confirmed by pixel-measuring a screenshot), which a 1px border reads
    as visibly egg-shaped even with the app-wide PassThrough DPI rounding
    policy set in main().

    Painting the handle ourselves sidesteps that class of bug entirely:
    QPainter.drawEllipse() is given one QRectF built from a single
    diameter used for both width and height, so there is nothing left
    for per-edge DPI rounding to disagree on - it's a circle by
    construction, not by hoping two independently-rounded numbers match.

    The groove and filled portion are hand-painted too now, not left to
    QSS - both driven by an animated position (see animPos/_anim_pos)
    rather than jumping straight to the new value, so a stepper button (or
    anything else that calls setValue() rather than dragging) glides the
    handle and fill there over 180ms/OutCubic - the same duration/curve
    ToggleSwitch's knob already uses - instead of teleporting. An actual
    mouse drag still tracks the cursor 1:1 with no animated lag (see
    _animate_to/sliderChange); the animation is only for the "value
    changed some other way" case a plain QSlider can't tell apart from a
    drag on its own.

    Subclasses that need a different groove background (see _HueSlider's
    rainbow gradient) override _paint_groove_background() rather than
    supplying their own QSS or falling back to super().paintEvent() -
    there's no native QSS groove/sub-page/add-page painting to fall back
    to any more, since none of that can be driven by the animated
    position."""

    HANDLE_D = 22  # matches ToggleSwitch's track height (22px) so the
    # slider handle, stepper buttons and toggle knobs all read as the
    # same size on screen instead of the handle looking oversized next
    # to a toggle switch.
    GROOVE_H = 6  # overridden by _HueSlider (10px, to fit its gradient)
    PAINT_FILL = True  # _HueSlider turns this off - a rainbow strip has
    # no "progress" to fill, unlike every other slider in the app.

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._handle_hover = False
        self.bidirectional: bool = False
        self.origin_value: float = 0.0
        self._anim_pos = float(self.value())
        self._pos_anim = QPropertyAnimation(self, b"animPos", self)
        self._pos_anim.setDuration(180)
        self._pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.valueChanged.connect(self._animate_to)
        # A range change (e.g. the CPU-cores slider being re-ranged to the
        # real core count after construction) can move where the current
        # value sits without emitting valueChanged, which left _anim_pos
        # pointing at a stale position - the handle then rested short of
        # the end of the groove even with the value at its maximum.
        self.rangeChanged.connect(self._snap_to_value)
        self._pos_anim.finished.connect(self._snap_to_value)

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)

    def _snap_to_value(self, *_args):
        self._pos_anim.stop()
        self._anim_pos = float(self.value())
        self.update()

    def _effective_pos(self):
        # While an animation is actually running, follow it; at rest,
        # always use the real value so the handle can never be left
        # parked at a stale intermediate position.
        if self._pos_anim.state() == QPropertyAnimation.State.Running:
            return self._anim_pos
        return float(self.value())

    def _get_anim_pos(self):
        return self._anim_pos

    def _set_anim_pos(self, value):
        self._anim_pos = value
        self.update()

    animPos = Property(float, _get_anim_pos, _set_anim_pos)

    def _animate_to(self, value):
        if self.isSliderDown():
            self._pos_anim.stop()
            self._anim_pos = float(value)
            self.update()
            return
        self._pos_anim.stop()
        self._pos_anim.setStartValue(self._anim_pos)
        self._pos_anim.setEndValue(float(value))
        self._pos_anim.start()

    def sliderChange(self, change):
        if change == QSlider.SliderChange.SliderValueChange and self.isSliderDown():
            self._pos_anim.stop()
            self._anim_pos = float(self.value())
            self.update()
        super().sliderChange(change)

    EDGE_MARGIN = 2.0  # room for the handle's outline stroke at the ends

    def _groove_rect(self):
        # Geometry is computed here rather than asked from the QSS style:
        # the style's groove rect and the style's handle travel disagreed
        # by a few pixels, so at the maximum value the handle stopped
        # short of the end of the groove and a stub of track stuck out
        # past it. One shared formula for both guarantees the handle's
        # right edge lands exactly on the groove's right end at max (and
        # its left edge on the groove's left end at min).
        cy = self.rect().center().y()
        h = self.GROOVE_H
        m = self.EDGE_MARGIN
        return QRectF(m, cy - h / 2.0, max(1.0, self.width() - 2 * m), h)

    def _handle_rect(self):
        lo, hi = self.minimum(), self.maximum()
        pos = max(lo, min(hi, self._effective_pos()))
        frac = (pos - lo) / (hi - lo) if hi > lo else 1.0
        # The handle is painted here, not by the style, so it has to follow
        # invertedAppearance itself - the mouse mapping (done natively by
        # QSlider) already does. Without this, a slider with inverted
        # appearance (Quality: right = better = lower CRF) moved the handle
        # AWAY from the cursor while dragging and jumped between the ends.
        if self.invertedAppearance():
            frac = 1.0 - frac
        d = self.HANDLE_D
        groove = self._groove_rect()
        cx = groove.left() + d / 2.0 + frac * max(0.0, groove.width() - d)
        cy = self.rect().center().y()
        return QRectF(cx - d / 2.0, cy - d / 2.0, d, d)

    def _paint_groove_background(self, painter, groove):
        t = window_tokens(self.window())
        painter.setBrush(QColor(t["border"]))
        painter.drawRoundedRect(groove, groove.height() / 2.0, groove.height() / 2.0)

    def mouseMoveEvent(self, event):
        was_hover = self._handle_hover
        self._handle_hover = self._handle_rect().contains(event.position())
        if was_hover != self._handle_hover:
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._handle_hover:
            self._handle_hover = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = window_tokens(self.window())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        groove = self._groove_rect()
        self._paint_groove_background(painter, groove)

        handle_rect = self._handle_rect()
        if self.bidirectional:
            lo, hi = self.minimum(), self.maximum()
            orig = max(lo, min(hi, self.origin_value))
            orig_frac = (orig - lo) / (hi - lo) if hi > lo else 0.5
            if self.invertedAppearance():
                orig_frac = 1.0 - orig_frac
            d = self.HANDLE_D
            orig_cx = groove.left() + d / 2.0 + orig_frac * max(0.0, groove.width() - d)

            # Subtle zero/origin tick mark
            tick_pen = QPen(QColor(t.get("border_hover", t["border"])), 1.5)
            painter.setPen(tick_pen)
            painter.drawLine(QPointF(orig_cx, groove.top() - 2), QPointF(orig_cx, groove.bottom() + 2))
            painter.setPen(Qt.PenStyle.NoPen)

            if self.PAINT_FILL:
                hx = handle_rect.center().x()
                x_left = min(orig_cx, hx)
                x_right = max(orig_cx, hx)
                fill_w = max(0.0, x_right - x_left)
                if fill_w > 1.0:
                    fill = QRectF(x_left, groove.top(), fill_w, groove.height())
                    painter.setBrush(QColor(t["accent"]))
                    painter.drawRoundedRect(fill, groove.height() / 2.0, groove.height() / 2.0)
        else:
            if self.PAINT_FILL:
                fill_w = max(0.0, min(groove.width(), handle_rect.center().x() - groove.left()))
                if fill_w > 0:
                    fill = QRectF(groove.left(), groove.top(), fill_w, groove.height())
                    painter.setBrush(QColor(t["accent"]))
                    painter.drawRoundedRect(fill, groove.height() / 2.0, groove.height() / 2.0)

        if self.isSliderDown():
            border, width = QColor(t["accent_hover"]), 1.5
        elif self._handle_hover:
            border, width = QColor(t["accent"]), 1.5
        else:
            border, width = QColor(t["border"]), 1.0

        painter.setPen(QPen(border, width))
        painter.setBrush(QColor(t["knob_fg"]) if self.isEnabled() else QColor(t["border"]))
        painter.drawEllipse(handle_rect)
        painter.end()


class PreviewScrubSlider(RoundHandleSlider):
    """Single-handle horizontal scrub slider for Live preview."""

    def __init__(self, *args, **kwargs):
        if not args and "orientation" not in kwargs:
            super().__init__(Qt.Orientation.Horizontal, **kwargs)
        else:
            super().__init__(*args, **kwargs)
        self.setProperty("role", "compact-slider")

    def values(self):
        v = self.value()
        return (v, v)

    def low(self):
        return self.value()

    def high(self):
        return self.value()

    def setValues(self, lo, hi=None, emit=True):
        self.setValue(int(lo))

    def active_handle(self):
        return None


class RangeSlider(QWidget):
    """Two-handle horizontal range slider - the trim bar under Live
    Preview. Plain QSlider only ever has one handle, so this is its own
    small widget rather than a QSlider subclass, but it reuses
    RoundHandleSlider's approach for the same reason: paint the handles
    as QPainter.drawEllipse() circles from one shared diameter, not a QSS
    box, so they can't end up egg-shaped at a fractional display scale
    (see RoundHandleSlider's docstring for the full explanation).

    Emits rangeChanged(low, high) whenever either handle moves - dragging
    fires it continuously (for live scrubbing), not just on release."""

    HANDLE_D = 22
    TRACK_H = 6
    rangeChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min, self._max = 0, 100
        self._low, self._high = 0, 100
        self._active = None   # "low" | "high" | None, whichever is being dragged
        self._hover = None    # "low" | "high" | None, whichever the mouse is over
        self.setMouseTracking(True)
        self.setMinimumHeight(self.HANDLE_D)

    def setRange(self, lo, hi):
        self._min, self._max = lo, max(lo, hi)
        self._low = max(self._min, min(self._low, self._max))
        self._high = max(self._min, min(self._high, self._max))
        if self._low > self._high:
            self._low = self._high
        self.update()

    def setValues(self, lo, hi, emit=True):
        lo = max(self._min, min(int(lo), self._max))
        hi = max(self._min, min(int(hi), self._max))
        if lo > hi:
            lo, hi = hi, lo
        changed = (lo, hi) != (self._low, self._high)
        self._low, self._high = lo, hi
        if changed:
            self.update()
            if emit:
                self.rangeChanged.emit(self._low, self._high)

    def values(self):
        return self._low, self._high

    def active_handle(self):
        """"low"/"high" while that handle is being dragged, else None -
        lets a caller (e.g. Live Preview) show the frame for whichever
        end the user is actually adjusting right now."""
        return self._active

    def _usable_width(self):
        return max(1, self.width() - self.HANDLE_D)

    def _x_for_value(self, v):
        span = max(1, self._max - self._min)
        return self.HANDLE_D / 2 + (v - self._min) / span * self._usable_width()

    def _value_for_x(self, x):
        frac = max(0.0, min(1.0, (x - self.HANDLE_D / 2) / self._usable_width()))
        return round(self._min + frac * (self._max - self._min))

    def _handle_rect(self, v):
        cx, cy, d = self._x_for_value(v), self.rect().center().y(), self.HANDLE_D
        return QRectF(cx - d / 2.0, cy - d / 2.0, d, d)

    def mousePressEvent(self, event):
        if not self.isEnabled():
            return
        pos = event.position()
        # Overlapping handles (low == high, e.g. right after setRange())
        # both occupy the same rect - check "high" first so a click there
        # doesn't get stolen by "low" underneath it.
        if self._handle_rect(self._high).contains(pos):
            self._active = "high"
        elif self._handle_rect(self._low).contains(pos):
            self._active = "low"
        else:
            # Clicking the bare track jumps the nearer handle to that spot,
            # same convention most trim/range sliders use.
            v = self._value_for_x(pos.x())
            self._active = "low" if abs(v - self._low) <= abs(v - self._high) else "high"
            self._drag_to(v)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        hover = ("high" if self._handle_rect(self._high).contains(pos)
                 else "low" if self._handle_rect(self._low).contains(pos) else None)
        if hover != self._hover:
            self._hover = hover
            self.update()
        if self._active:
            self._drag_to(self._value_for_x(pos.x()))
        super().mouseMoveEvent(event)

    def _drag_to(self, v):
        if self._active == "low":
            self.setValues(min(v, self._high), self._high)
        else:
            self.setValues(self._low, max(v, self._low))

    def mouseReleaseEvent(self, event):
        self._active = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._hover:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = window_tokens(self.window())
        y = self.rect().center().y() - self.TRACK_H / 2.0
        x0, x1 = self.HANDLE_D / 2.0, self.width() - self.HANDLE_D / 2.0
        lo_x, hi_x = self._x_for_value(self._low), self._x_for_value(self._high)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t["border"]))
        painter.drawRoundedRect(QRectF(x0, y, x1 - x0, self.TRACK_H), self.TRACK_H / 2, self.TRACK_H / 2)
        painter.setBrush(QColor(t["accent"] if self.isEnabled() else t["border"]))
        painter.drawRoundedRect(QRectF(lo_x, y, max(0.0, hi_x - lo_x), self.TRACK_H),
                                 self.TRACK_H / 2, self.TRACK_H / 2)

        for which, v in (("low", self._low), ("high", self._high)):
            if not self.isEnabled():
                border, width = QColor(t["border"]), 1.0
            elif self._active == which:
                border, width = QColor(t["accent_hover"]), 1.5
            elif self._hover == which:
                border, width = QColor(t["accent"]), 1.5
            else:
                border, width = QColor(t["border"]), 1.0
            painter.setPen(QPen(border, width))
            painter.setBrush(QColor(t["knob_fg"]) if self.isEnabled() else QColor(t["border"]))
            painter.drawEllipse(self._handle_rect(v))
        painter.end()


class _HueSlider(RoundHandleSlider):
    """Horizontal 0-359 hue slider. Reuses RoundHandleSlider's hand-painted
    circular handle (see that class's docstring for why it's hand-painted
    rather than QSS-boxed) and only adds a full-saturation rainbow groove,
    painted directly with QLinearGradient rather than a QSS string - the
    base class no longer paints its groove via QSS at all (see its
    docstring), so there's no qlineargradient() stop-list to fall back
    to; drawing the same 7-stop rainbow with QPainter directly is no
    harder and keeps this in the same code path as the animated handle."""

    GROOVE_H = 10
    PAINT_FILL = False  # a rainbow strip has no "progress" to fill

    def __init__(self, *args, **kwargs):
        super().__init__(Qt.Orientation.Horizontal, *args, **kwargs)
        self.setRange(0, 359)

    def _paint_groove_background(self, painter, groove):
        grad = QLinearGradient(groove.left(), 0, groove.right(), 0)
        for i in range(7):
            grad.setColorAt(i / 6, QColor.fromHsv(round(i / 6 * 359), 255, 255))
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(groove, groove.height() / 2.0, groove.height() / 2.0)


class _SVPicker(QWidget):
    """2D saturation (x-axis) x value (y-axis) picker for a fixed hue -
    the big square in the theme panel. Painted as a white-to-hue
    horizontal gradient with a transparent-to-black vertical gradient
    layered on top, since Qt has no single-primitive 2D gradient; the
    two overlaid QLinearGradients produce the same result a real 2D one
    would."""

    changed = Signal(float, float)  # s, v, each in [0, 1]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._hue = 0
        self._s = 1.0
        self._v = 1.0

    def set_hue(self, hue):
        self._hue = hue
        self.update()

    def set_sv(self, s, v):
        self._s, self._v = s, v
        self.update()

    def _pick(self, pos):
        x = min(max(pos.x(), 0), max(0, self.width() - 1))
        y = min(max(pos.y(), 0), max(0, self.height() - 1))
        self._s = x / max(1, self.width() - 1)
        self._v = 1.0 - y / max(1, self.height() - 1)
        self.update()
        self.changed.emit(self._s, self._v)

    @staticmethod
    def _event_pos(event):
        return event.position().toPoint() if hasattr(event, "position") else event.pos()

    def mousePressEvent(self, event):
        self._pick(self._event_pos(event))

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(self._event_pos(event))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        hue_color = QColor.fromHsv(self._hue, 255, 255)

        t = window_tokens(self.window())
        sat_grad = QLinearGradient(rect.topLeft(), rect.topRight())
        sat_grad.setColorAt(0.0, QColor(t["on_accent_light"]))
        sat_grad.setColorAt(1.0, hue_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(sat_grad))
        painter.drawRoundedRect(rect, 8, 8)

        val_grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        val_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        val_grad.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.setBrush(QBrush(val_grad))
        painter.drawRoundedRect(rect, 8, 8)

        cx = self._s * (rect.width() - 1)
        cy = (1.0 - self._v) * (rect.height() - 1)
        ring_color = t["on_accent_dark"] if self._v > 0.6 and self._s < 0.6 else t["on_accent_light"]
        painter.setPen(QPen(QColor(ring_color), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 7, 7)
        painter.end()


class _GradientPreviewBar(QWidget):
    """Flat strip painted with the theme panel's live multi-stop
    gradient - the thin swatch above the per-stop button row, mirroring
    the reference layout's gradient preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        t = window_tokens(self.window())
        self._stops = list(t["preview_fallback_stops"])

    def set_stops(self, stops):
        t = window_tokens(self.window())
        self._stops = list(stops) if stops else [t["preview_fallback_stops"][0]]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        grad = QLinearGradient(rect.topLeft(), rect.topRight())
        n = max(1, len(self._stops) - 1)
        for i, hex_color in enumerate(self._stops):
            grad.setColorAt(i / n if n else 0.0, QColor(hex_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(rect, 8, 8)
        painter.end()


class _EyedropperCatcher(QWidget):
    """One per screen: a full-screen window that is visually invisible but
    still catches clicks. It is a normal window at ~1% constant opacity
    (not a per-pixel-translucent one): Windows lets mouse events fall
    through fully transparent pixels of those, and re-composites the whole
    window on every repaint, which is what made the old single huge
    translucent overlay lag and flicker. This one never repaints."""

    def __init__(self, geometry, controller, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                          | Qt.WindowType.WindowStaysOnTopHint)
        self._controller = controller
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(0, 0, 0))
        self.setPalette(pal)
        self.setWindowOpacity(0.01)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(geometry)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._controller.finish_at_cursor()
        else:                                   # right / middle click cancels
            self._controller.cancel()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._controller.cancel()


class _EyedropperRing(QWidget):
    """The little live-colour ring (+ hex text) that follows the cursor.
    A tiny window of its own, moved by the controller's timer - moving a
    44px window is cheap and smooth, repainting a desktop-sized
    translucent one on every mouse move is not. Transparent for input, so
    it never steals the click."""

    W, H = 112, 34

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                          | Qt.WindowType.WindowStaysOnTopHint
                          | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(self.W, self.H)
        self._color = QColor("#000000")

    def set_color(self, color):
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24, 200))
        painter.drawRoundedRect(QRectF(0, 0, self.W, self.H), 10, 10)
        t = window_tokens(None)
        painter.setBrush(self._color)
        painter.setPen(QPen(QColor(t["on_accent_light"]), 2))
        painter.drawEllipse(QRectF(5, 5, 24, 24))
        painter.setPen(QColor(t["on_accent_light"]))
        painter.drawText(QRectF(34, 0, self.W - 38, self.H),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self._color.name().upper())
        painter.end()


class _EyedropperOverlay(QObject):
    """Pick a colour from anywhere on the desktop (every monitor).

    Each screen is grabbed once up front (grabWindow(0)) by the caller and
    passed in as `snapshots`: a list of (screen geometry in logical/global
    px, QImage grabbed from that screen). While active:
      - an invisible click-catcher window per screen takes the click
        (left = pick, right click / Esc / the app losing focus = cancel);
      - a timer polls the cursor and moves a small ring window that shows
        the colour (and its hex) under it.
    Sampling maps the cursor's GLOBAL logical position to the right screen
    and then to that screen's PHYSICAL pixel (x image.devicePixelRatio()) -
    indexing the image with logical coordinates reads the wrong pixel on
    any scaled display (125%/150%/...)."""

    picked = Signal(str)

    def __init__(self, snapshots, parent=None):
        super().__init__(parent)
        self._snapshots = list(snapshots)
        self._catchers = [_EyedropperCatcher(geo, self, parent.window() if parent is not None else None)
                          for geo, _img in self._snapshots]
        self._ring = _EyedropperRing()
        self._timer = QTimer(self)
        self._timer.setInterval(8)
        self._timer.timeout.connect(self._tick)
        self._done = False
        self._watching_app = False

    # -- sampling -----------------------------------------------------
    def _color_at_global(self, gx, gy):
        for geo, image in self._snapshots:
            if geo.contains(gx, gy):
                dpr = image.devicePixelRatio() or 1.0
                px = min(max(int((gx - geo.x()) * dpr), 0), image.width() - 1)
                py = min(max(int((gy - geo.y()) * dpr), 0), image.height() - 1)
                return QColor(image.pixel(px, py))
        return QColor("#000000")

    def _cursor_pos(self):
        return QCursor.pos()

    # -- lifecycle ----------------------------------------------------
    def start(self):
        for c in self._catchers:
            c.show()
        pos = self._cursor_pos()
        active = next((c for c in self._catchers if c.geometry().contains(pos)), self._catchers[0])
        active.raise_()
        active.activateWindow()
        active.setFocus()
        self._ring.show()
        self._ring.raise_()
        self._tick()
        self._timer.start()
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)
            self._watching_app = True

    def _on_app_state(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            self.cancel()          # never leave an invisible full-screen catcher behind

    def _tick(self):
        pos = self._cursor_pos()
        self._ring.set_color(self._color_at_global(pos.x(), pos.y()))
        # sit bottom-right of the cursor; flip to the other side near a screen edge
        x, y = pos.x() + 18, pos.y() + 18
        for geo, _img in self._snapshots:
            if geo.contains(pos):
                if x + self._ring.W > geo.right():
                    x = pos.x() - 18 - self._ring.W
                if y + self._ring.H > geo.bottom():
                    y = pos.y() - 18 - self._ring.H
                break
        self._ring.move(x, y)

    def finish_at_cursor(self):
        pos = self._cursor_pos()
        self._finish(self._color_at_global(pos.x(), pos.y()).name())

    def cancel(self):
        self._finish(None)

    def _finish(self, hex_color):
        if self._done:
            return
        self._done = True
        self._timer.stop()
        app = QApplication.instance()
        if app is not None and self._watching_app:
            self._watching_app = False
            app.applicationStateChanged.disconnect(self._on_app_state)
        self._ring.close()
        self._ring.deleteLater()
        for c in self._catchers:
            c.close()
            c.deleteLater()
        self.deleteLater()
        if hex_color:
            self.picked.emit(hex_color)
