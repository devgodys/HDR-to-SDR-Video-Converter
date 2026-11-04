"""Step 0, part 2: theme/geometry characterisation.

Cycles the real MainWindow through every theme x glass x neon combination,
several times over, and records the window's minimumSizeHint() plus the
min/max size of every widget that carries a size constraint.

The point is the QSS `min-height` vs setFixedSize() trap from the previous
session: a stylesheet rule can quietly push a widget's minimum above its
programmatic maximum, which then leaks into the whole window's
minimumSizeHint(). That is invisible in a screenshot and impossible to
notice by eye, but it shows up immediately as a changed number here.

Cycled repeatedly (not just once per combination) because the failure mode
is order-dependent: a role change that leaks only shows up after switching
*away* and back, not on the first application of a theme.

Usage:
    QT_QPA_PLATFORM=offscreen python3 tools/theme_smoke.py out.json
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import hdr2sdr.ui.main_window as app

CYCLES = 3
COMBOS = [(t, g, n)
          for t in ("light", "dark", "neon")
          for g in (False, True)
          for n in (False, True)]


def widget_key(w):
    name = w.objectName() or ""
    role = w.property("role") or ""
    return f"{type(w).__name__}#{name}[{role}]"


def constrained_widgets(win):
    """Every widget whose min and max differ from Qt's wide-open defaults -
    i.e. everything that could develop an inconsistent min > max."""
    out = {}
    counts = {}
    for w in win.findChildren(QWidget):
        mn, mx = w.minimumSize(), w.maximumSize()
        interesting = (mn.width() > 0 or mn.height() > 0
                       or mx.width() < 16777215 or mx.height() < 16777215)
        if not interesting:
            continue
        key = widget_key(w)
        counts[key] = counts.get(key, 0) + 1
        out[f"{key}#{counts[key]}"] = {
            "min": [mn.width(), mn.height()],
            "max": [mx.width(), mx.height()],
            "hint": [w.sizeHint().width(), w.sizeHint().height()],
            "inconsistent": (mn.width() > mx.width()) or (mn.height() > mx.height()),
        }
    return out


def run():
    qapp = QApplication.instance() or QApplication(sys.argv)
    win = app.MainWindow()
    result = {"combos": {}, "errors": [], "inconsistent": [], "warmup": {}}

    # Warm-up pass, deliberately not recorded.
    #
    # Straight after build() the root QVBoxLayout still holds a stale cached
    # minimum for the main splitter (970px) even though the splitter's own
    # minimumSizeHint() already says 996px. The first theme application that
    # touches enough widgets to force a full relayout makes the root layout
    # re-read it, and the window's minimumSizeHint jumps by 26px - once,
    # irreversibly, and with no widget's own sizeHint having changed.
    #
    # That is a real (if minor) pre-existing quirk, not a refactor
    # regression, so it is burned in here rather than left to fire on every
    # future run and drown out the regressions this harness is actually for.
    # See the notes file for the fix option and why it isn't taken blindly.
    before = win.minimumSizeHint()
    for theme, glass, neon in COMBOS:
        win.theme_name = theme
        win.ui_style = "glass" if glass else "flat"
        win.neon_effect = neon
        win.apply_theme()
        qapp.processEvents()
    after = win.minimumSizeHint()
    result["warmup"] = {
        "minimumSizeHint_after_build": [before.width(), before.height()],
        "minimumSizeHint_after_first_theme_pass": [after.width(), after.height()],
    }

    for cycle in range(CYCLES):
        for theme, glass, neon in COMBOS:
            label = f"{theme}/glass={int(glass)}/neon={int(neon)}"
            try:
                win.theme_name = theme
                win.ui_style = "glass" if glass else "flat"
                win.neon_effect = neon
                win.apply_theme()
                qapp.processEvents()
            except Exception as exc:  # noqa: BLE001 - characterisation, report everything
                result["errors"].append({"cycle": cycle, "combo": label,
                                         "error": f"{type(exc).__name__}: {exc}"})
                continue

            msh = win.minimumSizeHint()
            widgets = constrained_widgets(win)
            bad = [k for k, v in widgets.items() if v["inconsistent"]]
            if bad:
                result["inconsistent"].append({"cycle": cycle, "combo": label, "widgets": bad})

            entry = {"minimumSizeHint": [msh.width(), msh.height()], "widgets": widgets}
            prev = result["combos"].get(label)
            if prev is None:
                result["combos"][label] = entry
            elif prev != entry:
                # Same combination, different result on a later cycle: that is
                # exactly the order-dependent leak this harness exists to catch.
                result["errors"].append({
                    "cycle": cycle, "combo": label,
                    "error": "unstable across cycles",
                    "first_msh": prev["minimumSizeHint"],
                    "now_msh": entry["minimumSizeHint"],
                })
    del qapp
    return result


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("golden/theme.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    data = run()
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"combos: {len(data['combos'])}  errors: {len(data['errors'])}  "
          f"inconsistent: {len(data['inconsistent'])} -> {out}")
    for e in data["errors"][:10]:
        print("  ERROR", e)
    for e in data["inconsistent"][:10]:
        print("  MIN>MAX", e)
