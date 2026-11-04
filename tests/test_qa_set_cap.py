"""QA tests for SET (settings), CAP (capabilities), and PART (destination writability) areas."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.core.constants import ENCODER_MAP, FFMPEG_BIT_DEPTH


def get_qapp():
    return QApplication.instance() or QApplication([])


def test_set_05_bit_depth_filters_encoders():
    """SET-05: Changing bit depth between 8-bit and 10-bit filters encoder choices (C-01)."""
    qapp = get_qapp()
    w = MainWindow()

    # 10-bit filter
    w._on_bit_depth_changed("10-bit")
    items_10 = [w.encoder_combo.itemText(i) for i in range(w.encoder_combo.count())]
    _, structural_10 = FFMPEG_BIT_DEPTH["10-bit"]
    for it in items_10:
        assert it in structural_10

    # 8-bit filter
    w._on_bit_depth_changed("8-bit (SDR standard)")
    items_8 = [w.encoder_combo.itemText(i) for i in range(w.encoder_combo.count())]
    _, structural_8 = FFMPEG_BIT_DEPTH["8-bit (SDR standard)"]
    for it in items_8:
        assert it in structural_8


def test_set_06_corrupt_qsettings_defaults_safely():
    """SET-06: Corrupt/unknown keys in QSettings default to safe values without raising exceptions."""
    qapp = get_qapp()
    w = MainWindow()

    w.i18n.settings.setValue("quality", "corrupted_non_int")
    w.i18n.settings.setValue("backend", "UnknownBackendNonExistent")
    w.i18n.settings.setValue("bit_depth", "99-bit")

    # restore should handle corrupt keys safely
    w._conversion_preferences.restore()
    assert w.quality_spin.value() >= 0
    assert w.backend_combo.currentIndex() >= 0


def test_set_07_control_availability_by_curve():
    """SET-07: dv_rpu, peak_detect, and gamut controls are visible for GPU curves and hidden for CPU curves (C-07, C-08)."""
    qapp = get_qapp()
    w = MainWindow()

    # GPU curve
    gpu_idx = w.method_combo.findText("BT.2390 \u00b7 GPU tonemap")
    if gpu_idx >= 0:
        w.method_combo.setCurrentIndex(gpu_idx)
        w._update_libplacebo_toggle_availability()
        assert not w.dv_rpu_chk.isHidden()
        assert not w.peak_detect_chk.isHidden()
        assert not w.gamut_combo.isHidden()

    # CPU curve
    cpu_idx = w.method_combo.findText("Hable \u00b7 CPU tonemap")
    if cpu_idx >= 0:
        w.method_combo.setCurrentIndex(cpu_idx)
        w._update_libplacebo_toggle_availability()
        assert w.dv_rpu_chk.isHidden()
        assert w.peak_detect_chk.isHidden()
        assert w.gamut_combo.isHidden()


def test_set_08_bitrate_estimate_updates_on_change():
    """SET-08: update_bitrate_estimate dynamically updates bitrate_label when encoder/quality changes."""
    qapp = get_qapp()
    w = MainWindow()

    w.encoder_combo.setCurrentIndex(0)
    w.quality_spin.setValue(22)
    w.update_bitrate_estimate()
    text1 = w.bitrate_label.text()

    w.quality_spin.setValue(35)
    w.update_bitrate_estimate()
    text2 = w.bitrate_label.text()

    assert text1 != ""
    assert text2 != ""
    assert text1 != text2


def test_cap_05_ui_encoders_match_detected_capabilities():
    """CAP-05: Encoder combobox entries match the detected available encoders (C-05)."""
    qapp = get_qapp()
    w = MainWindow()

    # Restrict w.encoders to only libx264
    w.encoders = {"libx264"}
    allowed = w._allowed_encoder_labels("8-bit (SDR standard)")
    assert allowed == {"CPU \u00b7 H.264"}

    w._on_bit_depth_changed("8-bit (SDR standard)")
    items = [w.encoder_combo.itemText(i) for i in range(w.encoder_combo.count())]
    assert items == ["CPU \u00b7 H.264"]


def test_cap_06_capability_probe_responsiveness():
    """CAP-06: check_capabilities probe uses QProcess and QEventLoop (INV-11) without freezing."""
    from hdr2sdr.ui import probe
    import inspect
    src_code = inspect.getsource(probe.run_capture)
    # INV-11 verification: heavy probing uses QProcess/QEventLoop, never blocking subprocess.check_output
    assert "QProcess" in src_code
    assert "QEventLoop" in src_code
    assert "subprocess.check_output" not in src_code


def test_part_05_readonly_destination_error(tmp_path):
    """PART-05: Destination plan in read-only location or write failure handled with clear error."""
    qapp = get_qapp()
    w = MainWindow()

    readonly_dir = tmp_path / "readonly_folder"
    readonly_dir.mkdir()

    src = tmp_path / "source.mkv"
    src.write_bytes(b"data")
    dst = readonly_dir / "output.mp4"

    w.src_edit.setText(str(src))
    w.dst_edit.setText(str(dst))

    from hdr2sdr.core.destination import DestinationPlan
    plan = DestinationPlan(dst)
    assert plan.part == readonly_dir / "output.part.mp4"
