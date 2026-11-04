import time
import sys

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication


@pytest.fixture
def win():
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow

    window = MainWindow()
    yield window
    window.close()


class DelayedWorker(QThread):
    completed = Signal(int, str, object, object)

    def __init__(self, generation, executable, source):
        super().__init__()
        self.generation = generation
        self.source = source
        self.cancel_requested = False

    def requestInterruption(self):
        self.cancel_requested = True
        super().requestInterruption()

    def run(self):
        time.sleep(0.15)
        if not self.isInterruptionRequested():
            self.completed.emit(self.generation, self.source, {"fake": True}, None)


def wait_for_worker(app, worker):
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    worker.wait(3000)
    app.processEvents()


def test_real_worker_emits_parsed_json_from_background_process():
    from hdr2sdr.ui.analyze_worker import AnalyzeWorker

    app = QApplication.instance() or QApplication([])
    worker = AnalyzeWorker(7, sys.executable, "sample.mkv",
                           ["-c", "import json; print(json.dumps({'streams': []}))"])
    received = []
    worker.completed.connect(lambda *args: received.append(args))
    worker.start()
    wait_for_worker(app, worker)
    assert len(received) == 1
    assert received[0] == (7, "sample.mkv", {"streams": []}, None)


def test_real_worker_cancels_process_on_interruption():
    from hdr2sdr.ui.analyze_worker import AnalyzeWorker

    app = QApplication.instance() or QApplication([])
    worker = AnalyzeWorker(8, sys.executable, "sample.mkv",
                           ["-c", "import time; time.sleep(20)"])
    received = []
    worker.completed.connect(lambda *args: received.append(args))
    worker.start()
    QTimer.singleShot(100, worker.requestInterruption)
    started = time.monotonic()
    wait_for_worker(app, worker)
    assert time.monotonic() - started < 3
    assert received == []


def test_analysis_runs_asynchronously_and_applies_result_once(win, monkeypatch, tmp_path):
    from hdr2sdr.ui import main_window

    app = QApplication.instance()
    source = tmp_path / "source.mkv"
    source.touch()
    calls = []
    monkeypatch.setattr(main_window, "exe", lambda _name: "fake-ffprobe")
    monkeypatch.setattr(main_window, "AnalyzeWorker", DelayedWorker)
    monkeypatch.setattr(win, "_apply_analysis_document", lambda doc: calls.append(doc) or True)
    win._set_source(str(source), analyze=False)

    assert win.analyze()
    worker = next(iter(win._analysis_workers.values()))
    assert worker.isRunning()
    wait_for_worker(app, worker)
    assert calls == [{"fake": True}]


def test_changing_source_cancels_old_analysis_result(win, monkeypatch, tmp_path):
    from hdr2sdr.ui import main_window
    app = QApplication.instance()
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.touch()
    second.touch()
    calls = []
    monkeypatch.setattr(main_window, "exe", lambda _name: "fake-ffprobe")
    monkeypatch.setattr(main_window, "AnalyzeWorker", DelayedWorker)
    monkeypatch.setattr(win, "_apply_analysis_document", lambda doc: calls.append(doc) or True)
    win._set_source(str(first), analyze=False)
    assert win.analyze()
    worker = next(iter(win._analysis_workers.values()))
    win._set_source(str(second), analyze=False)
    wait_for_worker(app, worker)
    assert worker.cancel_requested
    assert calls == []
