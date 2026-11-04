import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow

    result = MainWindow()
    yield result
    result.close()


def test_portable_setup_refuses_to_replace_tools_during_conversion(window, monkeypatch):
    notices = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *args: notices.append(args[2])))
    window.proc = object()

    window._start_portable_dependency_setup(force_ffmpeg=True)

    assert window._setup_proc is None
    assert notices == ["Finish or stop the current conversion/setup first."]
    window.proc = None


def test_portable_setup_refuses_a_second_setup(window, monkeypatch):
    notices = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *args: notices.append(args[2])))
    window._setup_proc = object()

    window._start_portable_dependency_setup(force_ffmpeg=True)

    assert notices == ["Finish or stop the current conversion/setup first."]
    window._setup_proc = None


def test_tools_moved_to_settings_section_order_and_handlers(window, monkeypatch):
    import sys
    from hdr2sdr.runtime import tools as tools_runtime
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog

    app = QApplication.instance()
    window.show()
    for _ in range(4):
        app.processEvents()

    # 1. Main window Conversion card keeps Activity on its place, and no longer contains Tools controls
    assert window.conv_card.isAncestorOf(window.activity_btn)
    assert window.activity_btn.isVisible()
    assert getattr(window, "tools_channel_btn", None) is None or not window.conv_card.isAncestorOf(window.tools_channel_btn)
    assert getattr(window, "install_btn", None) is None or not window.conv_card.isAncestorOf(window.install_btn)

    # 2. Callbacks on MainWindow before opening Settings do not fail on missing/removed widgets
    window._retranslate_tools_channel()
    window._set_install_btn_enabled(False)
    window._set_install_btn_enabled(True)
    window._on_setup_item_error("FFmpeg")
    window.apply_theme()

    # 3. Open Settings on Windows: Tools section is between Appearance and About
    monkeypatch.setattr(sys, "platform", "win32")
    dlg = ThemeCustomizeDialog(window)
    dlg.show()
    for _ in range(6):
        app.processEvents()

    try:
        assert list(dlg._nav_buttons.keys()) == ["general", "appearance", "tools", "about"]
        assert dlg._nav_tools_btn.isVisible()
        nav_y = [dlg._nav_buttons[k].y() for k in ("general", "appearance", "tools", "about")]
        assert nav_y[0] < nav_y[1] < nav_y[2] < nav_y[3]

        # Switch to Tools section
        dlg._nav_tools_btn.click()
        for _ in range(4):
            app.processEvents()
        assert dlg.current_section == "tools"
        assert dlg._section_stack.currentWidget() is dlg._tools_page

        # Two separate Tools controls exist and are visible inside _tools_page
        assert dlg._tools_page.isAncestorOf(dlg.tools_channel_btn)
        assert dlg._tools_page.isAncestorOf(dlg.install_btn)
        assert dlg.tools_channel_btn is not dlg.install_btn
        assert dlg.tools_channel_btn.isVisible()
        assert dlg.install_btn.isVisible()

        # Channel picker uses existing _on_tools_channel_picked / _tools_channel logic
        orig_channel = window._tools_channel()
        try:
            dlg.tools_channel_btn.setCurrentIndex(1)
            assert window._tools_channel() == tools_runtime.LATEST
            assert "latest" in dlg.tools_channel_btn.text().lower()

            dlg.tools_channel_btn.setCurrentIndex(0)
            assert window._tools_channel() == tools_runtime.STABLE
            assert "stable" in dlg.tools_channel_btn.text().lower()
        finally:
            window.i18n.settings.setValue("tools_channel", orig_channel)
            window._retranslate_tools_channel()

        # Check for updates button calls window.check_for_updates
        called = []
        monkeypatch.setattr(window, "check_for_updates", lambda: called.append("check_for_updates"))
        dlg.install_btn.clicked.disconnect()
        dlg.install_btn.clicked.connect(window.check_for_updates)
        dlg.install_btn.click()
        assert called == ["check_for_updates"]

        # Enabling/disabling install_btn during setup updates the Settings button live
        window._set_install_btn_enabled(False)
        assert not dlg.install_btn.isEnabled()
        window._on_setup_item_error("FFmpeg")
        assert dlg.install_btn.isEnabled()
    finally:
        dlg._closing_confirmed = True
        dlg.close()
        dlg.deleteLater()
        for _ in range(4):
            app.processEvents()

    # 4. After Settings dialog is closed and deleted, MainWindow callbacks must not touch deleted C++ widgets
    assert getattr(window, "tools_channel_btn", None) is None
    assert getattr(window, "install_btn", None) is None
    window._retranslate_tools_channel()
    window._set_install_btn_enabled(False)
    window._on_setup_item_error("FFmpeg")
    window._on_portable_setup_finished(False)
    window.apply_theme()

    # 5. On non-Windows platforms, Tools section and controls are hidden
    monkeypatch.setattr(sys, "platform", "linux")
    dlg_linux = ThemeCustomizeDialog(window)
    dlg_linux.show()
    for _ in range(4):
        app.processEvents()
    try:
        assert not dlg_linux._nav_tools_btn.isVisible()
        assert not dlg_linux.tools_channel_btn.isVisible()
        assert not dlg_linux.install_btn.isVisible()
        assert getattr(window, "tools_channel_btn", None) is None
        assert getattr(window, "install_btn", None) is None
    finally:
        dlg_linux._closing_confirmed = True
        dlg_linux.close()
        dlg_linux.deleteLater()
        for _ in range(4):
            app.processEvents()


def test_app_event_filters_teardown_after_windows_settings_dialog(window, monkeypatch):
    import sys
    import shiboken6
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog

    app = QApplication.instance()
    monkeypatch.setattr(sys, "platform", "win32")
    window.show()
    for _ in range(4):
        app.processEvents()

    assert getattr(window, "_app_filters_installed", False) is True
    assert getattr(window._native_chrome, "_win_ref", None) is not None
    assert window._native_chrome._win is window

    dlg = ThemeCustomizeDialog(window)
    dlg.show()
    dlg.select_section("tools")
    for _ in range(6):
        app.processEvents()

    # Simulate the exact C++ destruction order where a nested QVBoxLayout wrapper is
    # already invalidated by ~QVBoxLayoutWrapper() before QObject::~QObject() sends ChildRemoved.
    host = QWidget()
    outer = QVBoxLayout(host)
    inner = QVBoxLayout()
    child_row = QHBoxLayout()
    inner.addLayout(child_row)
    outer.addLayout(inner)
    shiboken6.delete( host )
    assert not shiboken6.isValid(inner)

    ev = QEvent(QEvent.Type.ChildRemoved)
    assert window.eventFilter(inner, ev) is False
    assert window._native_chrome.eventFilter(inner, ev) is False

    dlg._closing_confirmed = True
    dlg.close()
    window.close()
    assert getattr(window, "_app_filters_installed", False) is False


