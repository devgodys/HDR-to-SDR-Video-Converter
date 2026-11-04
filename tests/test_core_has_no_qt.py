"""Guards the one rule the whole package split depends on: hdr2sdr/core/
never imports PySide6, directly or transitively.

This is what makes core/ testable without a QApplication (see
test_command_builders.py and test_progress.py) and what stops a future
change from accidentally wiring a widget into a command builder again.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CORE_DIR = Path(__file__).resolve().parent.parent / "hdr2sdr" / "core"


def _core_files():
    return sorted(CORE_DIR.glob("*.py"))


def test_core_directory_is_not_empty():
    """Fails loudly if this test is ever pointed at the wrong path,
    rather than silently passing with zero files checked."""
    assert _core_files(), f"no .py files found under {CORE_DIR}"


def test_no_core_module_imports_pyside6():
    offenders = {}
    for path in _core_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad = [a.name for a in node.names if a.name.split(".")[0] == "PySide6"]
            elif isinstance(node, ast.ImportFrom):
                bad = [node.module] if node.module and node.module.split(".")[0] == "PySide6" else []
            else:
                continue
            if bad:
                offenders.setdefault(path.name, []).extend(bad)
    assert not offenders, f"core/ modules importing PySide6: {offenders}"


def test_core_modules_import_cleanly_without_qapplication():
    """Belt and braces: even if a module avoided importing PySide6 by
    name, it could still reach it indirectly. Importing each module in a
    process with no QApplication (and PySide6 not even pre-loaded) is the
    real proof."""
    import subprocess
    for path in _core_files():
        mod = f"hdr2sdr.core.{path.stem}"
        result = subprocess.run(
            [sys.executable, "-c", f"import sys; assert 'PySide6' not in sys.modules; import {mod}; "
                                    f"assert 'PySide6' not in sys.modules, '{mod} pulled in PySide6'"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"{mod}: {result.stderr}"
