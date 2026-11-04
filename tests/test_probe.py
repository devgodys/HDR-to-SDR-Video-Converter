"""run_capture / capture_text: helper programs run without freezing the window,
with a timeout, and with user input excluded while waiting."""
import sys
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.probe import ProbeError, capture_text, run_capture

PY = sys.executable


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_captures_stdout_and_exit_code(app):
    r = run_capture([PY, "-c", "print('hello'); import sys; sys.exit(3)"])
    assert r.code == 3 and r.text().strip() == "hello" and not r.ok


def test_merge_stderr(app):
    code = "import sys; print('out'); print('err', file=sys.stderr)"
    assert "err" in capture_text([PY, "-c", code], merge_stderr=True)
    assert "err" not in capture_text([PY, "-c", code])


def test_timeout_kills_the_process(app):
    t = time.monotonic()
    r = run_capture([PY, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert r.timed_out and time.monotonic() - t < 5
    with pytest.raises(ProbeError):
        capture_text([PY, "-c", "import time; time.sleep(30)"], timeout=0.3)


def test_missing_program_is_reported_not_raised(app):
    r = run_capture(["/definitely/not/a/program"])
    assert r.error and not r.ok
    with pytest.raises(ProbeError):
        capture_text(["/definitely/not/a/program"])


def test_nonzero_exit_raises_in_capture_text(app):
    with pytest.raises(ProbeError):
        capture_text([PY, "-c", "import sys; sys.exit(1)"])


def test_window_keeps_running_while_waiting(app):
    """Timers fire during the wait - the GUI thread isn't frozen."""
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(20)
    run_capture([PY, "-c", "import time; time.sleep(0.6)"], timeout=10)
    timer.stop()
    assert len(ticks) >= 8
