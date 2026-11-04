"""Keep Windows from sleeping while a conversion runs.

A multi-hour 4K encode should not depend on the user's power plan: if the
PC idles into sleep mid-run, ffmpeg's GPU/decoder state does not reliably
survive the wake-up. SetThreadExecutionState is the supported way to say
"I'm busy" - the request lapses by itself if the app dies, so there is
nothing to clean up after a crash.

No Qt and no side effects on import. Off Windows every call is a harmless
no-op returning False.

SetThreadExecutionState is per *thread*, so keep_awake()/release() must be
called from the same thread (the GUI thread - QProcess signals all arrive
there).

KEEP_DISPLAY_ON: also stops the monitor from switching off. Default False
because a screen that stays lit for hours is a real cost (power, OLED
burn-in) and the system-awake request is what protects the encode. Flip it
if a given GPU/driver turns out to misbehave when the display powers down.
"""
from __future__ import annotations

import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

KEEP_DISPLAY_ON = False


def _set(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        fn = ctypes.windll.kernel32.SetThreadExecutionState
        fn.argtypes = [ctypes.c_uint32]
        fn.restype = ctypes.c_uint32
        return fn(flags) != 0
    except Exception:
        return False


def keep_awake(display: bool | None = None) -> bool:
    """Idempotent: calling it again just renews the request."""
    if display is None:
        display = KEEP_DISPLAY_ON
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if display:
        flags |= ES_DISPLAY_REQUIRED
    return _set(flags)


def release() -> bool:
    """Back to the normal power plan. Safe to call when nothing is held."""
    return _set(ES_CONTINUOUS)
