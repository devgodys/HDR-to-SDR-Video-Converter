"""hdr2sdr.runtime.power - no Qt, and a no-op everywhere but Windows."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hdr2sdr.runtime import power


def test_flags(monkeypatch):
    seen = []
    monkeypatch.setattr(power, "_set", lambda f: seen.append(f) or True)
    power.keep_awake(display=False)
    power.keep_awake(display=True)
    power.release()
    assert seen[0] == power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED
    assert seen[1] == seen[0] | power.ES_DISPLAY_REQUIRED
    assert seen[2] == power.ES_CONTINUOUS


def test_noop_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert power.keep_awake() is False
    assert power.release() is False
