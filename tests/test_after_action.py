"""runtime/after_action: the exact system commands, and that nothing is ever
attempted off Windows or for 'do nothing'."""
from hdr2sdr.runtime import after_action as A


class Recorder:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.ok


def test_shutdown_is_scheduled_up_front_and_can_be_taken_back():
    r = Recorder()
    assert A.begin(A.SHUTDOWN, 60, run=r)
    assert r.calls[0][:4] == ["shutdown", "/s", "/t", "60"] and "/f" not in r.calls[0]   # never forced
    assert A.abort(A.SHUTDOWN, run=r) and r.calls[1] == ["shutdown", "/a"]
    r2 = Recorder()
    assert A.execute(A.SHUTDOWN, run=r2, suspend=lambda h: pytest_fail()) and r2.calls == []   # already scheduled


def pytest_fail():
    raise AssertionError("must not be called")


def test_hibernate_and_sleep_happen_at_zero_not_before():
    r = Recorder()
    assert A.begin(A.HIBERNATE, 60, run=r) and A.begin(A.SLEEP, 60, run=r) and r.calls == []
    assert A.execute(A.HIBERNATE, run=r, suspend=lambda h: pytest_fail()) and r.calls == [["shutdown", "/h"]]
    got = []
    assert A.execute(A.SLEEP, run=Recorder(), suspend=lambda h: got.append(h) or True)
    assert got == [False]                      # a real sleep, not hibernate


def test_cancel_for_sleep_and_hibernate_needs_no_system_call():
    r = Recorder()
    assert A.abort(A.SLEEP, run=r) and A.abort(A.HIBERNATE, run=r) and r.calls == []


def test_nothing_does_nothing():
    r = Recorder()
    assert A.begin(A.NONE, run=r) and A.abort(A.NONE, run=r) and r.calls == []
    assert A.execute(A.NONE, run=r, suspend=lambda h: pytest_fail()) is False and r.calls == []


def test_failure_is_reported_not_raised():
    assert A.execute(A.HIBERNATE, run=Recorder(ok=False)) is False
    assert A._run(["/definitely/not/a/program"]) is False


def test_supported_only_on_windows(monkeypatch):
    monkeypatch.setattr(A.sys, "platform", "linux")
    assert not A.supported()
    monkeypatch.setattr(A.sys, "platform", "win32")
    assert A.supported()
