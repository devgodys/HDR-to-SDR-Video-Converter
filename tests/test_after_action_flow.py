"""After conversion: sleep / hibernate / shut down - only after everything
succeeded, with a cancellable countdown, never after Stop / an error / a
result that needs a look."""
import shutil
import subprocess

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMessageBox

from hdr2sdr.runtime import after_action
from hdr2sdr.ui.after_action import AfterActionCountdown


def pump(n=4):
    for _ in range(n):
        QApplication.instance().processEvents()


# ---------------------------------------------------------------- the countdown itself
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_countdown(seconds=60):
    app = QApplication.instance() or QApplication([])
    box = QMessageBox()
    clock = Clock()
    c = AfterActionCountdown(box, "{action} in {seconds} s.", "Shut down", seconds, clock=clock)
    events = []
    c.expired.connect(lambda: events.append("expired"))
    c.cancelled.connect(lambda: events.append("cancelled"))
    return c, box, clock, events


def test_counts_down_and_expires_exactly_once():
    c, box, clock, events = make_countdown(60)
    c.start()
    assert box.text() == "Shut down in 60 s."
    clock.t += 14.2
    c._tick()
    assert box.text() == "Shut down in 46 s." and events == []
    clock.t += 100
    c._tick()
    c._tick()
    assert events == ["expired"] and c.done
    c.cancel()                                   # too late: can't turn into a cancel afterwards
    assert events == ["expired"]


def test_cancel_before_zero_wins_and_never_expires():
    c, box, clock, events = make_countdown(60)
    c.start()
    clock.t += 10
    c.cancel()
    clock.t += 500
    c._tick()
    assert events == ["cancelled"]


def test_closing_the_box_any_way_counts_as_cancel():
    c, box, clock, events = make_countdown(60)
    c.start()
    box.done(0)                                  # Esc / X / the Cancel button all end up here
    assert events == ["cancelled"]


# ---------------------------------------------------------------- wired into the window
@pytest.fixture
def win(monkeypatch):
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    pump()
    log = []
    monkeypatch.setattr(after_action, "supported", lambda: True)
    monkeypatch.setattr(after_action, "begin", lambda a, s=60, run=None: log.append(("begin", a, s)) or True)
    monkeypatch.setattr(after_action, "abort", lambda a, run=None: log.append(("abort", a)) or True)
    monkeypatch.setattr(after_action, "execute", lambda a, run=None, suspend=None: log.append(("execute", a)) or True)
    w._after_log = log
    yield w
    w._cancel_after_action_countdown()
    w.close()


def choose(w, action):
    w.after_action_combo.setCurrentIndex(w.after_action_combo.findData(action))


def test_duplicate_completion_transition_is_logged_without_raising(win, monkeypatch):
    from hdr2sdr.runtime.lifecycle import RunState, transition_safely
    from hdr2sdr.ui import main_window

    errors = []
    monkeypatch.setattr(main_window, "log_suppressed", errors.append)
    win._run_lifecycle.state = RunState.DONE
    transition_safely(win._run_lifecycle, RunState.FAILED, main_window.log_suppressed)
    assert win._run_lifecycle.state == RunState.FAILED
    assert len(errors) == 1 and "done" in errors[0] and "failed" in errors[0]


def test_default_is_do_nothing_and_does_nothing(win):
    assert win.after_action_combo.currentData() == "none"
    win._all_done()
    assert win._after_log == [] and win._after_countdown is None


def test_shutdown_countdown_then_execute(win):
    choose(win, "shutdown")
    win._all_done()
    assert win._after_log == [("begin", "shutdown", 60)] and win._after_countdown is not None
    ctrl = win._after_countdown
    ctrl._clock = lambda: 10 ** 9                # fast-forward past the deadline
    ctrl._tick()
    pump()
    assert win._after_log[-1] == ("execute", "shutdown") and win._after_countdown is None


def test_cancel_takes_the_scheduled_action_back(win):
    choose(win, "shutdown")
    win._all_done()
    win._after_countdown.cancel()
    pump()
    assert win._after_log == [("begin", "shutdown", 60), ("abort", "shutdown")] and win._after_countdown is None


def test_sleep_and_hibernate_use_the_same_flow(win):
    for act in ("sleep", "hibernate"):
        win._after_log.clear()
        choose(win, act)
        win._all_done()
        win._after_countdown._clock = lambda: 10 ** 9
        win._after_countdown._tick()
        pump()
        assert win._after_log == [("begin", act, 60), ("execute", act)]


def test_a_result_that_needs_a_look_blocks_it(win):
    choose(win, "shutdown")
    win._note_item_result(False)                 # output check not happy
    win._all_done()
    assert win._after_log == []
    win._queue_controller.batch_clean = True
    win._hints.gpu_tonemap = True           # GPU ran out of memory: flat frames
    win._note_item_result(True)
    win._all_done()
    assert win._after_log == []


def test_starting_a_new_conversion_cancels_the_pending_action(win, monkeypatch):
    choose(win, "shutdown")
    win._all_done()
    assert win._after_countdown is not None

    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(stop))
    try:
        win.start()                              # nothing selected: it complains - after cancelling the countdown
    except Stop:
        pass
    pump()
    assert win._after_countdown is None and ("abort", "shutdown") in win._after_log


def test_closing_the_window_never_leaves_a_scheduled_shutdown(win):
    choose(win, "shutdown")
    win._all_done()
    win.close()
    pump()
    assert ("abort", "shutdown") in win._after_log


# ---------------------------------------------------------------- when it may fire
def test_queue_that_drained_by_itself_fires_once(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win, "_all_done", lambda: calls.append(1))
    win._queue_controller.paths.clear()
    win._queue_controller.running = True
    win._start_next_queue_item()
    assert calls == [1]
    win._start_next_queue_item()                 # controller stopped: nothing more
    assert calls == [1]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_standalone_finish_fires_only_on_real_success(win, monkeypatch, tmp_path):
    out = tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    calls = []
    monkeypatch.setattr(win, "_all_done", lambda: calls.append(1))
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    win.dst_edit.setText(str(out))
    win.duration = 2.0
    win.using_hb = False

    def finish(code, stopping=False):
        win.proc = object()
        win.stopping = stopping
        win._resume_coordinator.concat_phase = False
        win._resume_coordinator.parts = []
        win._resume_coordinator.final_destination = None
        win._resume_coordinator.output_override = None
        win._queue_controller.running = False
        win._queue_controller.batch_clean = True
        win._hints.gpu_tonemap = False
        win._on_finished(code, QProcess.ExitStatus.NormalExit)

    finish(0)
    assert calls == [1]                          # success
    calls.clear()
    finish(1)
    assert calls == []                           # error
    finish(0, stopping=True)
    assert calls == []                           # stopped by the person


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_joined_resume_parts_fire_only_after_the_join_succeeded(win, monkeypatch, tmp_path):
    out = tmp_path / "joined.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    calls = []
    monkeypatch.setattr(win, "_all_done", lambda: calls.append(1))
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    win.duration = 2.0

    def join(code):
        win.proc = object()
        win.stopping = False
        win._resume_coordinator.concat_list_path = None
        win._resume_coordinator.final_destination = out
        win._resume_coordinator.parts = []
        win._queue_controller.running = False
        win._queue_controller.batch_clean = True
        win._hints.gpu_tonemap = False
        win._on_concat_finished(code, QProcess.ExitStatus.NormalExit)

    join(1)
    assert calls == []                           # joining failed: no sleep/shutdown
    # the join writes "<name>.part<ext>" and the app renames it to the real name on success
    out.replace(out.with_name(f"{out.stem}.part{out.suffix}"))
    join(0)
    assert calls == [1]
