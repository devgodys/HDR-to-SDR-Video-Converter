from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Frozen (PyInstaller): _MEIPASS is the extraction root, exactly as before
# the package split - unaffected either way.
#
# Dev mode: this file now lives at <repo_root>/hdr2sdr/paths.py rather than
# at <repo_root>/hdr_to_sdr_gui_qt.py directly, so Path(__file__).parent
# would resolve to the hdr2sdr/ package directory itself, not the repo
# root - i18n/ (which sits at the repo root, alongside the hdr2sdr/
# package, not inside it) would silently stop being found. That's exactly
# the trap flagged before this migration started: the failure is dev-mode
# only, so it would not show up in a PyInstaller build - only when running
# from source. .parent.parent corrects for the package's extra directory
# level.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
I18N_DIR = ROOT / "i18n"
PORTABLE_TOOLS_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "HDR-to-SDR-Converter" / "tools"
CPU_COUNT = os.cpu_count() or 4
