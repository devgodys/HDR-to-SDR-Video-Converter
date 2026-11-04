"""Theme/geometry regression test: cycles the real MainWindow through every
theme x glass x neon combination and checks for the Qt traps from the
previous debugging session - see tools/theme_smoke.py's module docstring
for what exactly is being guarded against.

Needs Qt, same as test_golden_snapshot.py.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="theme smoke test needs PySide6/Qt")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture(scope="module")
def result():
    import theme_smoke
    return theme_smoke.run()


def test_every_combo_applies_without_raising(result):
    assert not result["errors"], result["errors"]


def test_no_widget_has_inconsistent_min_greater_than_max(result):
    """The specific QSS `min-height` vs setFixedSize() trap: a role/class
    change leaving a widget's minimum size above its maximum. Silent in a
    screenshot, fatal to layout."""
    assert not result["inconsistent"], result["inconsistent"]
