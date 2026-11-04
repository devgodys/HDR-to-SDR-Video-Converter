"""The screen eyedropper: right pixel on scaled / multi-monitor desktops, and it
always cleans up after itself."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.widgets.sliders import _EyedropperOverlay


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def screen(w, h, dpr, base):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    for y in range(h):
        for x in range(w):
            img.setPixel(x, y, QColor(base + (x * 255 // w) % 100, (y * 255 // h) % 255, 40).rgb())
    img.setDevicePixelRatio(dpr)
    return img


def test_scaled_display_samples_the_physical_pixel(app):
    img = screen(1920, 1080, 1.5, 10)                       # 150% monitor: 1280x720 logical
    ov = _EyedropperOverlay([(QRect(0, 0, 1280, 720), img)])
    for lx, ly in ((10, 10), (640, 360), (1270, 710)):
        assert ov._color_at_global(lx, ly) == QColor(img.pixel(int(lx * 1.5), int(ly * 1.5)))
    ov.cancel()


def test_two_monitors_with_different_scaling(app):
    left, right = screen(1920, 1080, 1.0, 10), screen(2560, 1440, 1.25, 90)
    ov = _EyedropperOverlay([(QRect(0, 0, 1920, 1080), left), (QRect(1920, 0, 2048, 1152), right)])
    assert ov._color_at_global(100, 100) == QColor(left.pixel(100, 100))
    assert ov._color_at_global(3000, 600) == QColor(right.pixel(int((3000 - 1920) * 1.25), int(600 * 1.25)))
    assert ov._color_at_global(9999, 9999) == QColor("#000000")          # outside every screen
    ov.cancel()


def _run(app, action):
    shots = [(QRect(0, 0, 400, 300), screen(400, 300, 1.0, 20))]
    ov = _EyedropperOverlay(shots)
    got = []
    ov.picked.connect(got.append)
    ov._cursor_pos = lambda: QPoint(100, 80)
    ov.start()
    for _ in range(4):
        app.processEvents()
    action(ov)
    for _ in range(4):
        app.processEvents()
    return ov, got


def test_left_click_picks_and_closes_everything(app):
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(105, 85),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    ov, got = _run(app, lambda o: o._catchers[0].mousePressEvent(ev))
    assert got == [QColor(screen(400, 300, 1.0, 20).pixel(100, 80)).name()]
    assert not ov._catchers[0].isVisible() and not ov._ring.isVisible()


def test_escape_right_click_and_focus_loss_cancel(app):
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    right = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(105, 85),
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    for action in (lambda o: o._catchers[0].keyPressEvent(esc),
                   lambda o: o._catchers[0].mousePressEvent(right),
                   lambda o: o._on_app_state(Qt.ApplicationState.ApplicationInactive)):
        ov, got = _run(app, action)
        assert got == [] and not ov._catchers[0].isVisible() and not ov._ring.isVisible()
