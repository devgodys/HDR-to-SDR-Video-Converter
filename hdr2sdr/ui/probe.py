"""Run a short helper program (ffprobe, ffmpeg -filters, ...) and collect its
output WITHOUT freezing the window.

subprocess.check_output() blocks the GUI thread: on a network drive, a huge
file or a slow antivirus scan the whole window stops repainting for seconds.
run_capture() starts a QProcess and waits in a local event loop instead - the
window keeps repainting and timers/progress keep running - but with user
input excluded, so a click can't start a second action in the middle of the
call. Every call has a timeout and the process is killed if it expires.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEventLoop, QProcess, QTimer


class ProbeError(OSError):
    """The helper couldn't be run, timed out, or exited with an error."""


@dataclass
class CaptureResult:
    code: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    error: str = ""

    @property
    def ok(self):
        return self.code == 0 and not self.timed_out and not self.error

    def text(self, stream="stdout", errors="replace"):
        return getattr(self, stream).decode("utf-8", errors=errors)


def run_capture(argv, timeout=30.0, merge_stderr=False):
    """Run argv, return a CaptureResult. Never raises for failures - check .ok."""
    proc = QProcess()
    proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels if merge_stderr
                               else QProcess.ProcessChannelMode.SeparateChannels)
    loop = QEventLoop()
    state = {"timed_out": False, "error": ""}

    def on_error(err):
        if err == QProcess.ProcessError.FailedToStart:
            state["error"] = f"could not start {argv[0]}"
            loop.quit()

    def on_timeout():
        state["timed_out"] = True
        proc.kill()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(on_timeout)
    proc.errorOccurred.connect(on_error)
    proc.finished.connect(lambda *_: loop.quit())
    proc.start(str(argv[0]), [str(a) for a in argv[1:]])
    if proc.state() == QProcess.ProcessState.NotRunning and not state["error"]:
        state["error"] = f"could not start {argv[0]}"
    if not state["error"]:
        timer.start(int(timeout * 1000))
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    timer.stop()
    if proc.state() != QProcess.ProcessState.NotRunning:
        proc.kill()
        proc.waitForFinished(1000)
    code = None if state["error"] else proc.exitCode()
    out = bytes(proc.readAllStandardOutput())
    err = b"" if merge_stderr else bytes(proc.readAllStandardError())
    return CaptureResult(code, out, err, state["timed_out"], state["error"])


def capture_text(argv, timeout=30.0, merge_stderr=False, errors="replace"):
    """stdout text of a run that must succeed; raises ProbeError (an OSError)
    otherwise - the drop-in replacement for subprocess.check_output()."""
    r = run_capture(argv, timeout, merge_stderr)
    if r.timed_out:
        raise ProbeError(f"{argv[0]} did not answer within {timeout:g} s")
    if r.error:
        raise ProbeError(r.error)
    if r.code != 0:
        raise ProbeError(f"{argv[0]} exited with code {r.code}")
    return r.text("stdout", errors)
