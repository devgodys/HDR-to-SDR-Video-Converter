"""QA tests for PRV (preview), CMP (comparison dialog), and TCL (test clip) areas."""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QProcess
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QMessageBox

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.compare_dialog import TonemapComparisonDialog


def get_qapp():
    return QApplication.instance() or QApplication([])


def test_prv_04_gpu_preview_fails_falls_back_to_hable_cpu(tmp_path):
    """PRV-04: If GPU preview fails (output file missing), it automatically retries with Hable CPU."""
    qapp = get_qapp()
    w = MainWindow()

    src = tmp_path / "hdr_source.mkv"
    src.write_bytes(b"dummy_video_data")
    w.src_edit.setText(str(src))
    w._dv_profile = None

    # Set method to GPU method (BT.2390)
    idx = w.method_combo.findText("BT.2390 \u00b7 GPU tonemap")
    if idx >= 0:
        w.method_combo.setCurrentIndex(idx)

    # Ensure frame preview path does not exist initially
    w._frame_preview_path.unlink(missing_ok=True)
    w._frame_preview_cpu_fallback = False

    with patch.object(w, "_run_frame_preview") as mock_run:
        # Simulate finished event on GPU render where output file is missing
        w._on_frame_preview_finished(w._frame_preview_gen)
        # Should call _run_frame_preview(force_cpu_tonemap=True)
        mock_run.assert_called_once_with(force_cpu_tonemap=True)


def test_prv_06_black_frame_preview(tmp_path):
    """PRV-06: M20 black section preview displays black frame cleanly without error banner."""
    qapp = get_qapp()
    w = MainWindow()

    # Create a 64x64 solid black PNG at _frame_preview_path
    black_img = QImage(64, 64, QImage.Format.Format_RGB32)
    black_img.fill(QColor(0, 0, 0))
    black_img.save(str(w._frame_preview_path))

    w._frame_preview_gen = 5
    w._on_frame_preview_finished(5)

    # Should not show error label; pixmap should be valid
    assert "Could not render" not in w.preview_label.text()
    assert not w.preview_label.pixmap().isNull()


def test_cmp_01_open_compare_preflight(monkeypatch):
    """CMP-01: open_tonemap_comparison blocks with informational message if source missing or FFmpeg missing."""
    qapp = get_qapp()
    w = MainWindow()
    w.src_edit.setText("")

    info_called = []
    warn_called = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *args: info_called.append((title, text)))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *args: warn_called.append((title, text)))

    # Missing source
    w.open_tonemap_comparison()
    assert len(info_called) == 1
    assert "Choose a source" in info_called[0][1]

    # Source exists, but FFmpeg missing
    fake_src = Path("c:/dummy/source.mkv")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    w.src_edit.setText(str(fake_src))

    with patch("hdr2sdr.ui.main_window.exe", lambda name: None):
        w.open_tonemap_comparison()
        assert len(warn_called) == 1
        assert "Install FFmpeg" in warn_called[0][1]


def test_cmp_05_close_dialog_terminates_render(tmp_path, monkeypatch):
    """CMP-05: Closing TonemapComparisonDialog while render is active terminates processes (INV-03)."""
    qapp = get_qapp()
    w = MainWindow()
    src = tmp_path / "compare_src.mkv"
    src.write_bytes(b"data")
    w.src_edit.setText(str(src))

    dlg = TonemapComparisonDialog(w)
    # Mock comparator and test clip cancellation
    mock_cancel = MagicMock()
    dlg.comparator.cancel = mock_cancel
    mock_clip_cancel = MagicMock()
    dlg._cancel_test_clip = mock_clip_cancel

    dlg.close()

    mock_cancel.assert_called_once()
    mock_clip_cancel.assert_called_once()


def test_tcl_03_source_shorter_than_clip(tmp_path):
    """TCL-03: When source is shorter than default 5s clip (e.g. 2s), test clip command generates cleanly."""
    qapp = get_qapp()
    w = MainWindow()
    src = tmp_path / "short_2s.mkv"
    src.write_bytes(b"data")
    out = tmp_path / "test_clip.mp4"

    w.src_edit.setText(str(src))
    w.dst_edit.setText(str(tmp_path / "final.mp4"))
    w.duration = 2.0  # 2 seconds total

    cmd = w._test_clip_command(out)
    assert any(str(src) in arg for arg in cmd)
    assert any(str(out) in arg for arg in cmd)
    # Check that -t (duration) is present
    assert "-t" in cmd
    idx = cmd.index("-t")
    dur_val = float(cmd[idx + 1])
    assert dur_val <= 5.0


def test_tcl_05_test_clip_blocked_during_conversion(monkeypatch):
    """TCL-05: Clicking Render Test Clip while a conversion is running shows Conversion running message."""
    qapp = get_qapp()
    w = MainWindow()

    monkeypatch.setattr(w, "_is_proc_running", lambda: True)

    info_called = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *args: info_called.append((title, text)))

    w._run_test_clip()

    assert len(info_called) >= 1
    assert "Conversion running" in info_called[0][0]
    assert "Wait for the current conversion" in info_called[0][1]
