"""Regression tests for the second review round (REVIEW_v2.md, ids N-xx).

Same as test_review_findings.py: these are ordinary tests now that the findings are fixed.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_excepthook_log_does_not_grow_on_a_repeating_error(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    import threading
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    from hdr2sdr import __main__ as entry
    state = entry.install_excepthook()
    for _ in range(2000):
        try:
            1 / 0
        except ZeroDivisionError:
            sys.excepthook(*sys.exc_info())
    assert os.path.getsize(state["log"]) < 16 * 1024


def test_background_exception_ui_delivery_runs_on_gui_thread(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    import threading
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    main_thread = threading.get_ident()
    delivered = []
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: delivered.append(threading.get_ident())))
    from hdr2sdr import __main__ as entry
    state = entry.install_excepthook()
    def raise_in_worker():
        try:
            raise RuntimeError("worker failure")
        except RuntimeError:
            import types
            exc_type, exc_value, exc_traceback = sys.exc_info()
            threading.excepthook(types.SimpleNamespace(
                exc_type=exc_type, exc_value=exc_value,
                exc_traceback=exc_traceback))
    worker = threading.Thread(target=raise_in_worker)
    worker.start()
    worker.join()
    for _ in range(10):
        app.processEvents()
        if delivered:
            break
    assert delivered == [main_thread]


def test_runner_flushes_final_error_fragment_without_newline_on_nonzero_exit():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QProcess
    from hdr2sdr.core.progress import ErrorHintScanner
    from hdr2sdr.runtime.conversion import ConversionRunner

    runner = ConversionRunner()
    scanner = ErrorHintScanner(encoder_label="NVIDIA NVENC \u00b7 H.265")
    events = []

    def on_parsed(line, _stats, consumed):
        scanner.feed(line)
        events.append(("line", line, consumed))

    def on_finished(code, _status):
        events.append(("finished", code, scanner.hw_encoder))

    runner.parsed_line.connect(on_parsed)
    runner.finished.connect(on_finished)

    class PendingStdoutProcess:
        def __init__(self):
            self._unread = b"nvenc: no capable devices found"

        def readAllStandardOutput(self):
            chunk, self._unread = self._unread, b""
            return chunk

    runner.process = PendingStdoutProcess()
    runner.consume_output(b"frame=1\n")
    runner._forward_finished(1, QProcess.ExitStatus.NormalExit)

    assert events == [
        ("line", "frame=1", True),
        ("line", "nvenc: no capable devices found", False),
        ("finished", 1, True),
    ]
    assert scanner.hw_encoder is True


@pytest.fixture
def win(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    for _ in range(4):
        app.processEvents()
    # whatever the user is asked, they answer "Yes" - the code must not rely on the question
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    yield w
    w.close()


def test_destination_equal_to_source_is_refused(win, tmp_path):
    clip = tmp_path / "movie.mkv"
    clip.write_bytes(b"x")
    win.src_edit.setText(str(clip))
    win.dst_edit.setText(str(clip))
    win._queue_controller.running = False
    assert win._confirm_destination() is False


def test_start_reentry_during_preflight_is_ignored(win, monkeypatch):
    calls = []
    def preflight():
        calls.append("entered")
        win.start()
        return "busy"
    monkeypatch.setattr(win, "_start_impl", preflight)
    win.start()
    assert calls == ["entered"]
