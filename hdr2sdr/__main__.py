from __future__ import annotations

import sys

from PySide6.QtCore import (
    Qt, QObject, Signal, Slot,
)
from PySide6.QtWidgets import (
    QApplication, QMessageBox,
)

from hdr2sdr.ui.icons import load_app_icon
from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.mini_window import MiniModeWindow
from hdr2sdr.ui.widgets.combo import NoMenuStylePopupStyle




def install_excepthook(win=None):
    """A --windowed PyInstaller build has no console, so sys.stderr is None and any exception
    raised inside a Qt slot (button click, timer, signal handler, ...) that Python's default
    excepthook would normally print just gets silently discarded - the app looks like it's
    doing nothing. This routes uncaught exceptions to a log file, the Activity log and a
    dialog instead.

    Installed first thing (win=None: an error while the window is being built still gets
    logged); set state["win"] once the window exists. The same error text is shown only
    once every few seconds, so a failing timer cannot stack up dozens of modal dialogs."""
    import threading
    import time
    import traceback
    state = {"win": win, "last": "", "at": 0.0, "log": _log_path()}

    class ExceptionBridge(QObject):
        failed = Signal(str)

        @Slot(str)
        def deliver(self, msg):
            w = state["win"]
            try:
                if w is not None:
                    w.write("\n[error] Unhandled exception:\n" + msg + "\n")
            except Exception:
                pass
            try:
                QMessageBox.critical(w, "Unexpected error", msg[-2000:])
            except Exception:
                pass

    bridge = ExceptionBridge()
    bridge.failed.connect(bridge.deliver)
    state["bridge"] = bridge

    def report(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        now = time.monotonic()
        if msg == state["last"] and now - state["at"] < 10:
            return                      # same error again: no log line, no dialog
        state["last"], state["at"] = msg, now
        try:
            with open(state["log"], "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except Exception:
            pass
        try:
            bridge.failed.emit(msg)
        except Exception:
            pass

    sys.excepthook = report
    threading.excepthook = lambda a: report(a.exc_type, a.exc_value, a.exc_traceback)
    return state


def _log_path():
    import os
    from pathlib import Path
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    folder = Path(base) / "HDR-to-SDR-Converter"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        log = folder / "errors.log"
        if log.exists() and log.stat().st_size > 512 * 1024:      # keep it small: start over
            log.replace(folder / "errors.old.log")
        return str(log)
    except OSError:
        return os.devnull


def main():
    # Must be set before the QApplication is constructed. On a fractional
    # Windows display-scale setting (125%, 150%, etc.) Qt's default DPI
    # rounding policy can round a widget's width and height to a different
    # number of physical pixels from each other, so a QSS box declared as
    # a perfect square (e.g. the 26x26 slider handle above) ends up very
    # slightly non-square on screen - a thin 1px circle outline makes that
    # read as a visibly "egg-shaped" handle even though the stylesheet
    # itself is correct. PassThrough keeps the fractional factor exact
    # instead of rounding each dimension independently, so square QSS
    # boxes stay square at any scale factor.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    # Force Fusion instead of leaving Qt to pick whatever native style is
    # available (windowsvista/windows11 on Windows, etc.). Fusion is a
    # QStyle Qt fully implements itself, so custom QSS - especially the
    # QSlider groove/sub-page/add-page split used for the quality/brightness
    # sliders - paints exactly as designed. Native styles partially ignore
    # that QSS and fall back to their own slider chrome, which is what
    # produced the misaligned/overlapping slider look on Windows.
    app.setStyle("Fusion")
    app.setStyle(NoMenuStylePopupStyle(app.style()))
    # Kept False: both windows now quit the app explicitly from their own
    # closeEvent() (MainWindow's and MiniModeWindow's) rather than relying
    # on Qt's own "last window closed" tracking - Mini mode is hidden, not
    # actually closed, while Pro mode is open (MiniModeWindow.open_pro),
    # so leaving this at its default True could interact with that hidden-
    # not-closed window in ways not worth relying on here.
    app.setQuitOnLastWindowClosed(False)
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    hook_state = install_excepthook()
    try:
        win = MainWindow()
        hook_state["win"] = win
        if not icon.isNull():
            win.setWindowIcon(icon)
        # "Always open Pro mode" (see MiniModeWindow.skip_mini_toggle /
        # ThemeCustomizeDialog's mirrored toggle) - when set, skip creating
        # the Mini launcher entirely and go straight to the full interface.
        # Safe to skip: every other site that talks to win._mini_window
        # already does so via getattr(win, "_mini_window", None), so simply
        # never setting that attribute here is enough - nothing needs it to
        # exist.
        skip_mini = str(win.i18n.settings.value("skip_mini_window", "false")).lower() == "true"
        if skip_mini:
            win.show()
        else:
            mini = MiniModeWindow(win)
            if not icon.isNull():
                mini.setWindowIcon(icon)
            mini.show()
    except Exception:
        sys.excepthook(*sys.exc_info())
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
