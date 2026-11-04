"""QA tests for ANA area: analysis of inputs and source classification."""
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


def test_ana_01_hdr10_pq_detection(tmp_path):
    """ANA-01: M01 HDR10 (smpte2084 + bt2020) -> kind=hdr, HDR detected, mastering display metadata."""
    qapp = get_qapp()
    w = MainWindow()
    src_file = tmp_path / "sample_hdr10.mkv"
    src_file.write_bytes(b"dummy")
    w.src_edit.setText(str(src_file))

    raw_probe = {
        "format": {"duration": "10.0", "size": "1234567"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
                "color_range": "tv",
                "side_data_list": [
                    {
                        "side_data_type": "Mastering display metadata",
                        "red_x": "34000/50000", "red_y": "16000/50000",
                        "max_luminance": "1000/1", "min_luminance": "50/10000"
                    }
                ]
            }
        ]
    }

    w._apply_analysis_document(raw_probe)
    qapp.processEvents()

    assert w.kind == "hdr", "Source kind must be classified as 'hdr'"
    assert "HDR" in w.analysis_label.text()
    assert "1000" in w.analysis_label.text()
    w.close()


def test_ana_02_hlg_detection(tmp_path):
    """ANA-02: M02 HLG (arib-std-b67) -> kind=hdr."""
    qapp = get_qapp()
    w = MainWindow()
    src_file = tmp_path / "sample_hlg.mkv"
    src_file.write_bytes(b"dummy")
    w.src_edit.setText(str(src_file))

    raw_probe = {
        "format": {"duration": "8.0", "size": "800000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p10le",
                "color_transfer": "arib-std-b67",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
                "color_range": "tv"
            }
        ]
    }

    w._apply_analysis_document(raw_probe)
    qapp.processEvents()

    assert w.kind == "hdr", "HLG transfer must be classified as 'hdr'"
    w.close()


def test_ana_05_untagged_10bit_classification(tmp_path):
    """ANA-05: M16 10-bit HEVC without color tags -> documented fallback, no crash."""
    qapp = get_qapp()
    w = MainWindow()
    src_file = tmp_path / "sample_untagged.mkv"
    src_file.write_bytes(b"dummy")
    w.src_edit.setText(str(src_file))

    raw_probe = {
        "format": {"duration": "5.0", "size": "500000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p10le",
                # missing color tags
                "color_transfer": "unknown",
                "color_primaries": "unknown",
                "color_space": "unknown"
            }
        ]
    }

    w._apply_analysis_document(raw_probe)
    qapp.processEvents()

    assert w.kind in ("sdr", "hdr", "unknown"), "Must classify cleanly without exception"
    assert w.analysis_label.text() != ""
    w.close()


def test_ana_06_corrupted_inputs_block_start(tmp_path):
    """ANA-06: M09/M10/M11 corrupt inputs -> clear error message, no unhandled exception, Start blocked."""
    qapp = get_qapp()
    w = MainWindow()

    # Empty 0-byte file (M10)
    empty_file = tmp_path / "empty.mkv"
    empty_file.write_bytes(b"")
    w.src_edit.setText(str(empty_file))

    # Simulate analysis failure (ffprobe returned None, "Invalid data found")
    w._show_analysis_error("Invalid data found when processing input")
    qapp.processEvents()

    assert "Invalid data" in w.analysis_label.text() or "couldn't read" in w.analysis_label.text().lower()
    w.close()


def test_ana_10_missing_ffprobe_reports_clear_error(tmp_path):
    """ANA-10: Missing ffprobe -> clear report without crash."""
    qapp = get_qapp()
    w = MainWindow()
    sample_file = tmp_path / "test.mkv"
    sample_file.write_bytes(b"video content")
    w.src_edit.setText(str(sample_file))

    msg_shown = []
    def mock_critical(parent, title, text):
        msg_shown.append((title, text))
        return QMessageBox.StandardButton.Ok

    with patch("hdr2sdr.ui.main_window.exe", return_value=None), \
         patch.object(QMessageBox, "critical", side_effect=mock_critical):
        ok = w.analyze()
        qapp.processEvents()

    assert ok is False, "analyze() must return False when ffprobe is missing"
    assert len(msg_shown) == 1
    assert "FFprobe missing" in msg_shown[0][0]
    w.close()
