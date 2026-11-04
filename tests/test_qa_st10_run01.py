"""QA tests for ST-10 (UI strings in tr()) and RUN-01 (M07 full run through MainWindow)."""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.core.utils import exe


def get_qapp():
    return QApplication.instance() or QApplication([])


def test_st_10_ui_strings_in_tr():
    """ST-10: User-facing UI strings in hdr2sdr/ui/ utilize tr() translations (INV-12)."""
    ui_dir = ROOT / "hdr2sdr" / "ui"
    py_files = list(ui_dir.glob("*.py"))
    assert len(py_files) >= 5

    # Check that i18n English dictionary exists and has keys
    en_json = ROOT / "i18n" / "en.json"
    assert en_json.is_file()
    import json
    with en_json.open("r", encoding="utf-8") as f:
        translations = json.load(f)
    assert len(translations) >= 50

    # Ensure MainWindow methods use self.tr
    main_window_py = (ui_dir / "main_window.py").read_text(encoding="utf-8")
    tr_calls = re.findall(r"self\.tr\([\"'](.*?)[\"']\)", main_window_py)
    assert len(tr_calls) >= 50


def test_run_01_m07_full_run_through_window(tmp_path):
    """RUN-01: Full conversion of M07 (2s HDR10) through MainWindow interface completes with DONE and valid output."""
    ffmpeg = exe("ffmpeg")
    if not ffmpeg:
        import pytest
        pytest.skip("FFmpeg required for RUN-01")

    m07_path = ROOT / "qa" / "_media" / "M07_hdr10_2s.mkv"
    if not m07_path.is_file():
        import pytest
        pytest.skip("M07 test media not found")

    qapp = get_qapp()
    w = MainWindow()

    out_file = tmp_path / "M07_out.mp4"
    w._set_source(str(m07_path), analyze=False)
    w.dst_edit.setText(str(out_file))
    w.kind = "hdr"
    w.duration = 2.0

    # Fast preset
    w._apply_fast_preset()

    # Run conversion
    w.start()
    assert w.proc is not None

    # Wait for completion
    loop = QEventLoop()
    w.proc.finished.connect(lambda *_: loop.quit())
    # Timeout after 30 seconds
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(30000)

    loop.exec()

    # Process pending events for completion handler
    qapp.processEvents()

    assert out_file.is_file()
    assert out_file.stat().st_size > 10000
    assert "Done" in w.status_label.text()
