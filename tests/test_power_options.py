"""'Keep the PC awake' options: sensible defaults, saved between sessions,
and they actually decide what is asked of Windows."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from hdr2sdr.runtime import power


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(power, "keep_awake", lambda display=None: log.append(("awake", display)) or True)
    monkeypatch.setattr(power, "release", lambda: log.append(("release",)) or True)
    return log


def make_window():
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    for _ in range(4):
        app.processEvents()
    return w


@pytest.fixture
def win():
    w = make_window()
    w.i18n.settings.remove("keep_awake")
    w.i18n.settings.remove("keep_display_on")
    yield w
    w.i18n.settings.remove("keep_awake")
    w.i18n.settings.remove("keep_display_on")
    w.close()


def test_defaults_awake_on_screen_off(win, calls):
    w = make_window()                         # fresh window = fresh read of the (cleared) settings
    try:
        assert w.keep_awake_chk.isChecked() and not w.keep_display_chk.isChecked()
        assert w.keep_display_chk.isEnabled()
        w._apply_power_state()
        assert calls == [("awake", False)]
    finally:
        w.close()


def test_display_option_follows_the_main_one(win, calls):
    win.keep_awake_chk.setChecked(False)
    assert not win.keep_display_chk.isEnabled()          # can't keep the screen on without the PC awake
    win.keep_display_chk.setChecked(False)
    win._apply_power_state()
    assert calls[-1] == ("release",)                     # nothing held: the PC may sleep
    win.keep_awake_chk.setChecked(True)
    win.keep_display_chk.setChecked(True)
    win._apply_power_state()
    assert calls[-1] == ("awake", True)


def test_choices_are_remembered_next_time(win):
    win.keep_awake_chk.setChecked(False)
    win.keep_display_chk.setChecked(True)
    again = make_window()
    try:
        assert not again.keep_awake_chk.isChecked() and again.keep_display_chk.isChecked()
    finally:
        again.close()


def test_changing_it_mid_conversion_applies_immediately(win, calls):
    class Running:                                     # stands in for the ffmpeg QProcess
        def state(self):
            return QProcess.ProcessState.Running
    win.proc = Running()
    try:
        calls.clear()
        win.keep_awake_chk.setChecked(False)
        assert calls and calls[-1] == ("release",)
        win.keep_awake_chk.setChecked(True)
        assert calls[-1][0] == "awake"
    finally:
        win.proc = None


def test_labels_and_tooltips_are_translated(win):
    win.change_language("uk")
    QApplication.instance().processEvents()
    assert win.keep_awake_chk.text() == "Не давати ПК засинати під час конвертації"
    assert win.keep_display_chk.text() == "Також не вимикати екран"
    assert "Windows" in win.keep_awake_chk.toolTip() and "сну" in win.keep_awake_chk.toolTip()
    win.change_language("en")


def test_power_more_btn_in_controls_bottom_row_and_available_during_conversion(win):
    from PySide6.QtCore import QPoint

    app = QApplication.instance()
    # 1. power_more_btn is inside cores_panel_widget (bottom row of Controls), NOT in run_idle_page
    assert not win.run_idle_page.isAncestorOf(win.power_more_btn)
    assert win.controls_card.isAncestorOf(win.power_more_btn)
    assert win.cores_panel_widget.isAncestorOf(win.power_more_btn)

    # 2. The CPU row has [cores_slider] [priority_slider] [...] side-by-side (no dropdown).
    app.processEvents()
    slider_x = win.cores_slider.mapTo(win.cores_panel_widget, QPoint(0, 0)).x()
    prio_pos = win.priority_combo.mapTo(win.cores_panel_widget, QPoint(0, 0))
    more_pos = win.power_more_btn.mapTo(win.cores_panel_widget, QPoint(0, 0))
    assert win.cores_slider.isVisibleTo(win.cores_panel_widget)
    assert win.priority_combo.isVisibleTo(win.cores_panel_widget)
    assert slider_x < prio_pos.x() < more_pos.x()
    assert win.cores_panel_widget.isAncestorOf(win.priority_combo)
    prio_cy = prio_pos.y() + win.priority_combo.height() // 2
    more_cy = more_pos.y() + win.power_more_btn.height() // 2
    assert abs(prio_cy - more_cy) <= 4
    stack_bottom = win.run_stack.mapTo(win.controls_card, QPoint(0, win.run_stack.height())).y()
    more_top = win.power_more_btn.mapTo(win.controls_card, QPoint(0, 0)).y()
    assert more_top >= stack_bottom

    # 3. Clicking power_more_btn opens More options relative to the button
    win.power_dialog.hide()
    win.power_dialog.closed_at = 0.0
    win.power_more_btn.click()
    app.processEvents()
    assert win.power_dialog.isVisible()
    btn_win_pos = win.power_more_btn.mapTo(win, QPoint(0, 0))
    # Popup opens above (or below if no room) and horizontally aligned with power_more_btn's right edge
    assert win.power_dialog.geometry().bottom() <= btn_win_pos.y() or win.power_dialog.y() >= btn_win_pos.y()
    win.power_dialog.hide()
    win.power_dialog.closed_at = 0.0

    # 4. Remains visible, enabled, and functional while a conversion is active
    class Running:
        def state(self):
            return QProcess.ProcessState.Running

    win.proc = Running()
    try:
        win.update_run_controls()
        app.processEvents()
        assert win.run_stack.currentIndex() == 1
        assert win.power_more_btn.isVisible()
        assert win.power_more_btn.isEnabled()
        win.power_more_btn.click()
        app.processEvents()
        assert win.power_dialog.isVisible()
        win.power_dialog.hide()
    finally:
        win.proc = None
        win.update_run_controls()
        app.processEvents()

