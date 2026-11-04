"""Golden-snapshot regression test: the real MainWindow, driven through the
matrix in tools/golden_capture.py, must keep producing exactly the
snapshot committed at golden/commands.json.

This is the only test in the suite that needs Qt (it constructs a real
MainWindow), so it is collected separately from test_command_builders.py
and test_progress.py, which must never gain this dependency.

If this test fails after an intentional behaviour change, regenerate the
snapshot deliberately:

    QT_QPA_PLATFORM=offscreen python3 tools/golden_capture.py golden/commands.json

and diff it by hand before committing - do not regenerate to make a
failure go away without reading the diff first.
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pyside6 = pytest.importorskip("PySide6", reason="golden snapshot test needs PySide6/Qt")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

GOLDEN_PATH = ROOT / "golden" / "commands.json"


@pytest.fixture(scope="module")
def captured():
    import golden_capture
    return golden_capture.capture()


def test_golden_file_exists():
    assert GOLDEN_PATH.exists(), (
        "No golden snapshot yet - run "
        "`QT_QPA_PLATFORM=offscreen python3 tools/golden_capture.py golden/commands.json` "
        "once to create the baseline."
    )


def test_matches_committed_snapshot(captured):
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    mismatched = {k: (expected.get(k), captured.get(k))
                  for k in set(expected) | set(captured) if expected.get(k) != captured.get(k)}
    assert not mismatched, (
        f"{len(mismatched)} variation(s) changed vs golden/commands.json: "
        f"{sorted(mismatched)[:10]}"
    )


def test_no_new_or_missing_variations(captured):
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert set(captured) == set(expected), (
        "The variation matrix in tools/golden_capture.py changed shape - "
        "regenerate the snapshot after confirming the new/removed cases "
        "are intentional."
    )
