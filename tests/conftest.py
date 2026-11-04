"""Shared test setup: headless Qt, and a throw-away settings directory so the
tests never read or overwrite the developer's real app settings (language,
theme, tools channel, ...)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import site
_orig_user_site = site.getusersitepackages()
if _orig_user_site and Path(_orig_user_site).exists():
    _pp = os.environ.get("PYTHONPATH", "")
    if _orig_user_site not in _pp:
        os.environ["PYTHONPATH"] = f"{_orig_user_site}{os.pathsep}{_pp}" if _pp else _orig_user_site

_cfg = tempfile.mkdtemp(prefix="hdr2sdr-test-cfg-")
os.environ["XDG_CONFIG_HOME"] = _cfg
os.environ["HOME"] = _cfg
os.environ["APPDATA"] = _cfg
os.environ["LOCALAPPDATA"] = _cfg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _cfg)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, _cfg)
except ImportError:
    from tests.qt_stubs import install_qtcore_stubs_if_missing
    install_qtcore_stubs_if_missing()

import pytest


@pytest.fixture(autouse=True)
def _fresh_conversion_preferences():
    """Force QSettings to use the isolated INI directory (never the Windows Registry)
    and clear all saved keys around every test while keeping look_migrated set so
    module-scoped MainWindow instances stay in migrated mode."""
    try:
        from PySide6.QtCore import QSettings
    except ImportError:
        yield
        return

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _cfg)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, _cfg)

    def clear():
        settings = QSettings("DevGodys", "HDR to SDR Video Converter")
        settings.clear()
        settings.setValue("look_migrated", "1")
        settings.sync()
        ini_dir = Path(QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope,
            "DevGodys", "HDR to SDR Video Converter",
        ).fileName()).parent
        for extra in ("conversion_history.json", "unfinished_runs.json"):
            try:
                (ini_dir / extra).unlink(missing_ok=True)
            except OSError:
                pass

    clear()
    yield
    clear()
