from __future__ import annotations

from PySide6.QtCore import (
    Qt, QSize, QEvent, QObject,
)
from PySide6.QtWidgets import (
    QWidget, QSpinBox, QHBoxLayout, QVBoxLayout, QSizePolicy,
)

from hdr2sdr.ui.style_helpers import (
    _attach_press_bounce, set_role,
)
from hdr2sdr.ui.widgets.buttons import GlassButton
from hdr2sdr.ui.widgets.progress import AnimatedStepperOverlay




# Empty room kept around the round -/+ buttons inside the wrapper widget.
# A child's QGraphicsDropShadowEffect (the neon glow / glass shadow) can't
# paint outside its parent widget, and the wrapper used to be exactly as
# big as the buttons - so the glow was cut off in a straight line on every
# side of every stepper. The room is given back by StepperRowLayout so it
# doesn't move anything on screen.
STEPPER_GLOW_ROOM = 5


class StepperRowLayout(QHBoxLayout):
    """QHBoxLayout for a row that starts with a make_stepper() wrapper.

    The wrapper carries STEPPER_GLOW_ROOM of empty margin on every side;
    this layout hands that room back to the surroundings so the row takes
    exactly the space it did before the margin existed: it lays its items
    out in a rectangle that is wider to the left (the round buttons stay
    aligned with the content above) and as much taller at top and bottom
    as the wrapper's margin actually makes the row taller (nothing if
    another item in the row, e.g. a ~32px rounded combo box, was already
    that tall), and reports a correspondingly smaller size hint. Qt
    ignores negative layout margins, which is why this is a layout
    subclass and not just setContentsMargins(-5, ...).

    The left extension needs STEPPER_GLOW_ROOM of free space to the left
    of the row - from a margin in the layout that holds it, or by that
    layout having a left margin of STEPPER_GLOW_ROOM itself."""

    def __init__(self, parent=None):
        super().__init__(parent) if parent is not None else super().__init__()
        self._g = STEPPER_GLOW_ROOM

    def _extra(self):
        """Per-side extra height the wrapper's margin adds to this row."""
        g = self._g
        wrap_h = None
        others = 0
        for i in range(self.count()):
            it = self.itemAt(i)
            if it is None or it.isEmpty():
                continue
            h = it.sizeHint().height()
            if wrap_h is None:
                wrap_h = h          # first visible item = the stepper wrapper
            else:
                others = max(others, h)
        if wrap_h is None:
            return 0
        after = max(wrap_h, others)
        before = max(wrap_h - 2 * g, others)
        return max(0, (after - before) // 2)

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(max(0, s.width() - self._g), max(0, s.height() - 2 * self._extra()))

    def minimumSize(self):
        s = super().minimumSize()
        return QSize(max(0, s.width() - self._g), max(0, s.height() - 2 * self._extra()))

    def setGeometry(self, rect):
        e = self._extra()
        super().setGeometry(rect.adjusted(-self._g, -e, 0, e))


class LeftRoomLayout(QVBoxLayout):
    """Single-widget holder that lets `widget` extend STEPPER_GLOW_ROOM to
    the left of where the surrounding layout would put it (Qt ignores
    negative margins, so this overrides setGeometry instead). Used for a
    panel that holds StepperRowLayout rows but whose own left edge is
    where the card's content starts: with its inner layout given a left
    margin of STEPPER_GLOW_ROOM, the panel's content stays exactly where
    it was while the strip to its left gives the round buttons' glow room
    to paint instead of being clipped by the panel's edge."""

    def __init__(self, widget):
        super().__init__()
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(0)
        self.addWidget(widget)

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(max(0, s.width() - STEPPER_GLOW_ROOM), s.height())

    def minimumSize(self):
        s = super().minimumSize()
        return QSize(max(0, s.width() - STEPPER_GLOW_ROOM), s.height())

    def setGeometry(self, rect):
        super().setGeometry(rect.adjusted(-STEPPER_GLOW_ROOM, 0, 0, 0))


def make_stepper(spin: QSpinBox) -> QWidget:
    """Wrap a QSpinBox with round -/+ buttons and hide its native
    up/down buttons (boxy and, on some native Windows styles, prone to
    QSS-vs-native-chrome mismatches - see QSlider groove note in main()).
    spin itself is untouched: same widget, same .value()/.setValue()/
    .valueChanged/.setEnabled() any existing code already uses - only its
    container widget is new, so call sites just need to add that
    container to a layout instead of the bare spin box."""
    minus_btn = GlassButton("\u2212")
    plus_btn = GlassButton("+")
    for b in (minus_btn, plus_btn):
        set_role(b, "stepper")
        b.setFixedSize(22, 22)  # level with ToggleSwitch's track height
        # (22px) and RoundHandleSlider.HANDLE_D, so buttons/handles/toggle
        # knobs all read as the same size instead of the steppers/handle
        # looking oversized next to a toggle switch.
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        _attach_press_bounce(b)
    class _SpinWheelGuard(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
                event.ignore()
                return True
            return False

    spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    guard = _SpinWheelGuard(spin)
    spin.installEventFilter(guard)
    spin._wheel_guard = guard
    orig_wheel = spin.wheelEvent

    def _guarded_wheel(event):
        if not spin.hasFocus():
            event.ignore()
            return
        orig_wheel(event)

    spin.wheelEvent = _guarded_wheel
    set_role(spin, "stepper-spin")
    AnimatedStepperOverlay(spin)
    spin.setFixedHeight(22)  # match the round buttons exactly so the row
    # doesn't rely on QHBoxLayout's cross-axis centering (which left the
    # number a few px off from the +/- buttons when their sizeHints differed)
    minus_btn.clicked.connect(spin.stepDown)
    plus_btn.clicked.connect(spin.stepUp)

    def sync_enabled():
        minus_btn.setEnabled(spin.isEnabled() and spin.value() > spin.minimum())
        plus_btn.setEnabled(spin.isEnabled() and spin.value() < spin.maximum())

    spin.valueChanged.connect(lambda _v: sync_enabled())
    sync_enabled()

    wrap = QWidget()
    wrap.setObjectName("stepperWrap")
    wrap.setStyleSheet("QWidget#stepperWrap { background: transparent; }")
    row = QHBoxLayout(wrap)
    g = STEPPER_GLOW_ROOM
    row.setContentsMargins(g, g, g, g)
    row.setSpacing(10)
    row.addWidget(minus_btn)
    row.addWidget(spin)
    row.addWidget(plus_btn)
    wrap.minus_btn = minus_btn
    wrap.spin = spin
    wrap.plus_btn = plus_btn
    spin_w = max(spin.minimumWidth(), spin.maximumWidth(), spin.sizeHint().width(), 72)
    req_w = 2 * g + minus_btn.minimumWidth() + 2 * row.spacing() + spin_w + plus_btn.minimumWidth()
    wrap.setMinimumWidth(req_w)
    wrap.setFixedWidth(req_w)
    wrap.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    row.activate()

    # Existing code toggles spin.setEnabled(...) directly (e.g. brightness
    # controls). Piggyback on that instead of requiring every call site to
    # also remember to enable/disable the two new buttons: wrap setEnabled
    # so the buttons always mirror the spin box's state without touching
    # any of the ~6 existing .setEnabled(...) call sites.
    original_set_enabled = spin.setEnabled

    def set_enabled(enabled):
        original_set_enabled(enabled)
        sync_enabled()

    spin.setEnabled = set_enabled
    return wrap
