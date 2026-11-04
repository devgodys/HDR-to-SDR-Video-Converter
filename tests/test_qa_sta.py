"""QA tests for STA area: start, preflight checks, queue initialization, and error handling."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMessageBox

from hdr2sdr.ui.main_window import MainWindow


def get_qapp():
    return QApplication.instance() or QApplication([])


def test_sta_01_start_without_files(monkeypatch):
    """STA-01: Start without source/destination files displays critical alert."""
    qapp = get_qapp()
    w = MainWindow()
    w.src_edit.setText("")
    w.dst_edit.setText("")

    alert_called = []

    def mock_critical(parent, title, text, *args, **kwargs):
        alert_called.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "critical", mock_critical)
    w.start()

    assert len(alert_called) >= 1
    title, msg = alert_called[0]
    assert title == "Files"
    assert "Choose source and output files" in msg


def test_sta_02_missing_ffmpeg(tmp_path, monkeypatch):
    """STA-02: Missing FFmpeg blocks conversion with clear message."""
    qapp = get_qapp()
    w = MainWindow()
    src = tmp_path / "video.mkv"
    src.write_bytes(b"dummy")
    dst = tmp_path / "out.mp4"
    w.src_edit.setText(str(src))
    w.dst_edit.setText(str(dst))
    w.kind = "hdr"

    # Set backend to FFmpeg
    idx = w.backend_combo.findText("FFmpeg")
    if idx >= 0:
        w.backend_combo.setCurrentIndex(idx)

    alert_called = []

    def mock_critical(parent, title, text, *args, **kwargs):
        alert_called.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "critical", mock_critical)
    # Mock exe to return None for ffmpeg
    with patch("hdr2sdr.ui.main_window.exe", lambda name: None if name == "ffmpeg" else "c:/path/dummy"):
        w.start()

    assert any("FFmpeg" in title and "required" in msg for title, msg in alert_called)


def test_sta_14_source_deleted_after_queueing(tmp_path):
    """STA-14: If source is deleted/moved after being queued, queue skips it safely without hang."""
    qapp = get_qapp()
    w = MainWindow()

    src1 = tmp_path / "video1.mkv"
    src1.write_bytes(b"data1")
    src2 = tmp_path / "video2.mkv"
    src2.write_bytes(b"data2")

    w.queue_list.clear()
    w.queue_list.addItem(str(src1))
    w.queue_list.addItem(str(src2))
    w._queue_controller.paths = [str(src1), str(src2)]
    w._queue_controller.current_index = 0
    w._queue_controller.running = True

    # Delete src1
    src1.unlink()

    # Now when start() is called for the queued item
    w.src_edit.setText(str(src1))
    w.dst_edit.setText(str(tmp_path / "video1.mp4"))
    
    log_messages = []
    w.write = lambda msg: log_messages.append(msg)

    w.start()

    # Verify skipped log
    assert any("skipped" in m.lower() and str(src1.name) in m for m in log_messages)


def test_sta_15_libplacebo_fallback_sequence(tmp_path):
    """STA-15: DEF-0006 / INV-20 / C-06 fallback sequence test.
    OOM sets _job_tonemap_override -> user changing method_combo clears override -> DV P5 check respects new method.
    """
    qapp = get_qapp()
    w = MainWindow()

    # 1. Simulate OOM setting override
    w._job_tonemap_override = "Hable (film-like, standard)"
    assert w._job_tonemap_override == "Hable (film-like, standard)"

    # 2. User changes method_combo
    # Find another index
    new_idx = 1 if w.method_combo.currentIndex() == 0 else 0
    w.method_combo.setCurrentIndex(new_idx)

    # 3. Verify override was cleared
    assert w._job_tonemap_override is None, "Changing method_combo must clear _job_tonemap_override"

    # 4. Dolby Vision Profile 5 check: if method is CPU, DV P5 cannot start
    w._dv_profile = 5
    w.kind = "dolby"
    w.has_bt2390 = True  # has libplacebo
    
    # Set to a CPU method
    idx_cpu = -1
    for i in range(w.method_combo.count()):
        txt = w.method_combo.itemText(i)
        if "Hable" in txt or "Mobius" in txt or "Reinhard" in txt:
            idx_cpu = i
            break
    if idx_cpu >= 0:
        w.method_combo.setCurrentIndex(idx_cpu)
        engine, _ = w._dv_profile, w.method_combo.currentText()
        from hdr2sdr.core.commands import tonemap_lookup
        from hdr2sdr.runtime.workflow import RunWorkflow, DolbyVisionAction
        engine, _ = tonemap_lookup(w.method_combo.currentText())
        dovi_act = RunWorkflow.dolby_vision_action(5, "dolby", using_handbrake=False, tonemap_engine=engine, has_libplacebo=True, unattended=False)
        assert dovi_act == DolbyVisionAction.BLOCK_PROFILE5
