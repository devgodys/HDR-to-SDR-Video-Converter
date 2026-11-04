from __future__ import annotations

from PySide6.QtCore import (
    Qt, QTimer, QEvent, QPropertyAnimation, QEasingCurve, Property, QRectF,
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QLinearGradient,
)
from PySide6.QtWidgets import (
    QWidget, QSpinBox,
)

from hdr2sdr.theme.metrics import button_radius
from hdr2sdr.theme.theme_tokens import window_tokens




class ProgressShimmer(QWidget):
    """A soft diagonal highlight that sweeps across a QProgressBar's filled
    portion on a loop, so it reads as "working" rather than static -
    without relying on translucent child-widget compositing, which caused
    dark background artifacts on some Windows systems. This ordinary child
    widget paints a semi-transparent gradient with SourceOver blending.

    Parent it directly to the QProgressBar it decorates
    (ProgressShimmer(pbar)) - it tracks the bar's size, value and
    min/max on its own and starts/stops its repaint timer to match
    whether there's anything to animate over.
    """

    def __init__(self, pbar):
        super().__init__(pbar)
        self._pbar = pbar
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._phase = 0.0  # 0..1, wraps
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        pbar.valueChanged.connect(lambda _v: self._update_running())
        self._sync_geometry()
        self.show()
        self._update_running()

    def _sync_geometry(self):
        self.setGeometry(0, 0, self._pbar.width(), self._pbar.height())

    def _current_fraction(self):
        rng = self._pbar.maximum() - self._pbar.minimum()
        if rng <= 0:
            return 0.0
        return max(0.0, min(1.0, (self._pbar.value() - self._pbar.minimum()) / rng))

    def _update_running(self):
        active = self._pbar.isVisible() and self._current_fraction() > 0.0
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_running()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self):
        self._sync_geometry()
        self._phase += 0.012
        if self._phase >= 1.0:
            self._phase -= 1.0
        self._update_running()
        self.update()

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        fill_w = w * self._current_fraction()
        if w <= 0 or h <= 0 or fill_w <= 1:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = h / 2.0
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, fill_w, h), radius, radius)
        painter.setClipPath(clip)
        # The band sweeps a little past each edge so it fades in/out at
        # the ends of its run instead of popping into view.
        band_w = max(40.0, w * 0.22)
        x = -band_w + self._phase * (fill_w + band_w * 2)
        grad = QLinearGradient(x, 0, x + band_w, 0)
        grad.setColorAt(0.0, QColor(255, 255, 255, 0))
        grad.setColorAt(0.5, QColor(255, 255, 255, 75))
        grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRect(QRectF(0, 0, fill_w, h))
        painter.end()


class AnimatedStepperOverlay(QWidget):
    """Overlays a QSpinBox with a simple slide+fade transition: the old
    value slides out and fades while the new one slides in from the
    opposite edge and fades in - exactly two whole numbers ever drawn,
    never more. It's only shown for the ~220ms of an actual value change -
    typing, focus, wheel-scroll and everything else about the underlying
    QSpinBox keeps working exactly as before the rest of the time.

    (Previously an iOS-picker-style wheel that drew every intermediate
    value as a separate item - looked fine for small single-step spins
    like CRF, but a big singleStep (the bitrate field's 500) meant dozens
    of neighbouring items crammed into the same tight spacing, a blur of
    overlapping digits rather than a readable transition. Treating the
    value as one atomic string sidesteps that whole class of problem
    rather than trying to tune spacing/grid-alignment around it.)

    Geometry is locked to spin's own rect exactly (see _sync_geometry) -
    not a wider strip centered over it - so sliding text can never spill
    onto the +/- buttons on either side, and the field it's drawn on
    never changes size between resting and mid-transition."""

    DURATION_MS = 220

    def __init__(self, spin: QSpinBox):
        super().__init__(spin)
        self._spin = spin
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

        self._old_text = str(spin.value())
        self._new_text = str(spin.value())
        self._progress = 1.0  # 1.0 = at rest, fully settled on _new_text
        self._direction = 1
        self._last_value = spin.value()
        self._anim = None

        spin.installEventFilter(self)
        spin.valueChanged.connect(self._on_value_changed)
        QTimer.singleShot(0, self._sync_geometry)

    def _get_progress(self):
        return self._progress

    def _set_progress(self, value):
        self._progress = value
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def eventFilter(self, obj, event):
        if obj is self._spin and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
        return False

    def _sync_geometry(self):
        # Match spin's own geometry exactly (same parent, same
        # coordinate space - spin IS self.parent() here) rather than a
        # wider strip: a wider overlay either spills onto the +/-
        # buttons or, if clamped to the gap between them, ends up
        # bigger than spin's own resting box (that gap also includes
        # the row's inter-widget spacing), which then visibly changes
        # size the moment the transition starts. Matching spin means
        # zero overlap and zero resize, by construction, for free.
        self.setGeometry(0, 0, self._spin.width(), self._spin.height())

    def _on_value_changed(self, new_value):
        old_value = self._last_value
        self._last_value = new_value
        if new_value == old_value:
            return
        self._start_transition(old_value, new_value)

    def _start_transition(self, old_value, new_value):
        # Mid-flight retrigger (autorepeat holding +/-, or several quick
        # clicks) - the number currently on screen becomes the new "old"
        # text, wherever the slide had actually gotten to, rather than
        # snapping back to the value the *previous* transition started
        # from.
        self._old_text = self._current_old_text()
        self._new_text = str(new_value)
        self._direction = 1 if new_value > old_value else -1
        if self._anim is not None:
            self._anim.stop()

        self._sync_geometry()
        self.show()
        self.raise_()

        anim = QPropertyAnimation(self, b"progress", self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(self.DURATION_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._finish_transition)
        self._anim = anim
        self._progress = 0.0
        anim.start()

    def _current_old_text(self):
        """Whichever text is actually showing right now - _new_text once
        settled, or a blend point mid-transition. Only used to decide what
        the *next* transition's outgoing text should be, so it just needs
        to be old_text past the halfway point (new_text has visually taken
        over the centre by then) and new_text otherwise; doesn't need to
        be pixel-exact."""
        return self._new_text if self._progress >= 0.5 else self._old_text

    def _finish_transition(self):
        self.hide()
        self._anim = None
        self._progress = 1.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = window_tokens(self.window())

        # This widget's own geometry is already locked to spin's rect
        # (see _sync_geometry) and Qt never lets a widget paint outside
        # its own rect, so filling the whole thing can't spill onto the
        # buttons on either side.
        field_rect = QRectF(self.rect())
        br = min(button_radius(), 13)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(field_rect, br, br)
        painter.fillPath(bg_path, QColor(t["bg_field"]))
        painter.setPen(QPen(QColor(t["border"]), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(field_rect.adjusted(0.5, 0.5, -0.5, -0.5), br, br)
        # Clip to the field's own rounded rect so a mid-slide number can
        # never visibly poke out past the top/bottom edge of the box.
        painter.setClipPath(bg_path)

        base_font = self._spin.font()
        h = self.height()
        p = self._progress
        eased_out = p * (2 - p)  # quick ease-out, matches the QPropertyAnimation curve closely enough for a pixel offset

        painter.setFont(base_font)
        text_rect_at = lambda dy: QRectF(0, dy, self.width(), h)

        # Outgoing number: slides a full height away in _direction and
        # fades out.
        old_color = QColor(t["text"])
        old_color.setAlphaF(max(0.0, 1.0 - eased_out * 1.4))
        painter.setPen(old_color)
        painter.drawText(text_rect_at(-self._direction * eased_out * h),
                          Qt.AlignmentFlag.AlignCenter, self._old_text)

        # Incoming number: starts a full height away on the opposite side
        # and slides/fades into the centre.
        new_color = QColor(t["text"])
        new_color.setAlphaF(min(1.0, eased_out * 1.4))
        painter.setPen(new_color)
        painter.drawText(text_rect_at(self._direction * (1.0 - eased_out) * h),
                          Qt.AlignmentFlag.AlignCenter, self._new_text)
