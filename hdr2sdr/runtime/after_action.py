"""What the PC does once every conversion has finished successfully.

Windows only (elsewhere supported() is False and nothing is ever attempted).
No Qt in here; the window shows the countdown (ui/after_action.py) and calls:

    begin(action, seconds)   when the countdown starts
    abort(action)            if the person cancels it
    execute(action)          when it reaches zero

  shutdown   begin() asks Windows itself to shut down in `seconds`
             (`shutdown /s /t N`), abort() takes that back (`shutdown /a`) -
             so even if this app died during the countdown the PC would still
             do what the person asked, and Windows shows its own notice.
             Apps with unsaved work may hold a shutdown back: /f (force) is
             deliberately NOT used - losing someone's other documents is worse
             than a PC that stays on.
  hibernate  execute() runs `shutdown /h`.
  sleep      execute() calls SetSuspendState(hibernate=False): a real sleep,
             not the hibernate that `rundll32 powrprof.dll,SetSuspendState`
             falls back to on some machines.
"""
from __future__ import annotations

import subprocess
import sys

NONE = "none"
SLEEP = "sleep"
HIBERNATE = "hibernate"
SHUTDOWN = "shutdown"
ACTIONS = (NONE, SLEEP, HIBERNATE, SHUTDOWN)

COUNTDOWN_SECONDS = 60


def supported():
    return sys.platform.startswith("win")


def _run(argv):
    """Run a command, True if it exited 0. Never raises."""
    try:
        kw = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(argv, capture_output=True, timeout=15, **kw).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _suspend(hibernate):
    """SetSuspendState(hibernate, forceCritical=True, disableWakeEvent=False). True on success."""
    try:
        import ctypes
        return bool(ctypes.windll.powrprof.SetSuspendState(bool(hibernate), True, False))
    except (OSError, AttributeError):
        return False


def begin(action, seconds=COUNTDOWN_SECONDS, run=_run):
    """Start whatever has to be scheduled up front. True if nothing went wrong."""
    if action == SHUTDOWN:
        return run(["shutdown", "/s", "/t", str(int(seconds)), "/c",
                    "HDR to SDR Video Converter: all conversions finished."])
    return True


def abort(action, run=_run):
    """Take back what begin() scheduled."""
    if action == SHUTDOWN:
        return run(["shutdown", "/a"])      # fails harmlessly if nothing is pending
    return True


def execute(action, run=_run, suspend=_suspend):
    """The countdown reached zero. True if the system accepted the request."""
    if action == SHUTDOWN:
        return True                         # already scheduled by begin()
    if action == HIBERNATE:
        return run(["shutdown", "/h"])
    if action == SLEEP:
        return suspend(False)
    return False
