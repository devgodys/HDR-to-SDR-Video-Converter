import os
import sys
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QPoint, QSize, QRect, QProcess
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.widgets.sliders import RoundHandleSlider
from hdr2sdr.i18n.localizer import Localizer
from hdr2sdr.core.utils import exe


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    for f in ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"):
        if Path(f).exists():
            from PySide6.QtGui import QFontDatabase
            QFontDatabase.addApplicationFont(f)
    return app


def test_round_handle_slider_bidirectional(qapp):
    slider = RoundHandleSlider(Qt.Orientation.Horizontal)
    slider.setRange(-20, 20)
    slider.setValue(0)
    slider.resize(200, 32)
    assert slider.bidirectional is False

    slider.bidirectional = True
    slider.origin_value = 0.0

    # Ensure paintEvent executes without error for positive, negative, and zero values
    for val in (-15, 0, 10):
        slider.setValue(val)
        pix = QPixmap(slider.size())
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        slider.paintEvent(None)
        p.end()


def test_preview_auto_launch_without_preset_and_cleanup(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # Create dummy source video file
    dummy_src = tmp_path / "test_hdr.mkv"
    dummy_src.write_text("test video content")

    # Initial state: preview has placeholder
    assert "Choose a source video" in w.preview_label.text()
    assert w.preview_position_label.text() == "0:00"

    # Set source and simulate analysis
    w.src_edit.setText(str(dummy_src))
    w._set_source(str(dummy_src), analyze=False)

    # Frame preview timer should have stopped, proc should be cleared, pixmap cleared
    assert w._current_preview_pixmap is None

    # Simulate completed probe data for standard HDR10
    doc = {
        "format": {"duration": "120.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
                "side_data_list": [
                    {
                        "side_data_type": "Mastering display metadata",
                        "max_luminance": "1000/1",
                        "min_luminance": "50/10000",
                    }
                ]
            }
        ]
    }
    w._apply_analysis_document(doc)

    # 1. Preview timer was scheduled automatically without requiring preset button press
    assert w._frame_preview_timer.isActive()

    # 2. Track QProcess creation and execution: proving the preview process is launched
    started_processes = []
    def _tracking_start(*args, **kwargs):
        started_processes.append(w._frame_preview_proc)
        return None

    with patch.object(QProcess, "start", side_effect=_tracking_start):
        # Trigger frame preview directly (as the debounce timer does)
        w._frame_preview_timer.stop()
        w._run_frame_preview()

    assert len(started_processes) == 1, "Expected preview QProcess to be started after analysis"
    proc = started_processes[0]
    assert proc is w._frame_preview_proc
    assert "ffmpeg" in proc.program().lower()
    proc_args = proc.arguments()
    assert "-i" in proc_args
    assert str(dummy_src) in proc_args
    assert "-frames:v" in proc_args
    assert "1" in proc_args
    assert w.preview_label.text() == w.tr("Rendering preview frame…")

    w._stop_frame_preview_process()
    w.close()

    # 3. Real video rendering verification if FFmpeg is available
    ffmpeg_bin = shutil.which("ffmpeg") or exe("ffmpeg")
    if ffmpeg_bin:
        real_video = tmp_path / "valid_sample.mp4"
        cmd_gen = [
            str(ffmpeg_bin), "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-x264opts", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-y", str(real_video),
        ]
        res = subprocess.run(cmd_gen, capture_output=True)
        if res.returncode == 0 and real_video.is_file():
            w2 = MainWindow()
            w2.show()
            qapp.processEvents()
            w2.src_edit.setText(str(real_video))
            w2._set_source(str(real_video), analyze=False)
            w2._apply_analysis_document({
                "format": {"duration": "2.0"},
                "streams": [{
                    "codec_type": "video", "codec_name": "h264", "width": 320, "height": 240,
                    "color_transfer": "bt709", "color_primaries": "bt709", "color_space": "bt709"
                }]
            })
            assert w2._frame_preview_timer.isActive()
            # Stop debounce timer and run preview directly to prevent re-entrant trigger
            w2._frame_preview_timer.stop()
            w2._run_frame_preview()
            assert w2._frame_preview_proc is not None
            proc2 = w2._frame_preview_proc
            finished = proc2.waitForFinished(10000)
            assert finished, "Preview ffmpeg process must finish within timeout"
            qapp.processEvents()
            # If GPU tonemapping failed (e.g. Vulkan OOM), MainWindow triggers CPU fallback
            if w2._frame_preview_proc is not None and w2._frame_preview_proc != proc2:
                w2._frame_preview_proc.waitForFinished(10000)
                qapp.processEvents()

            # Confirm real frame actually appeared!
            assert w2._frame_preview_path.exists(), "Rendered preview PNG must exist on disk"
            assert w2._current_preview_pixmap is not None, "Preview pixmap must be loaded"
            assert not w2.preview_label.pixmap().isNull(), "Preview label must hold non-null pixmap"
            assert not hasattr(w2, "preview_expand_btn")
            assert not hasattr(w2, "preview_save_btn")

            w2._stop_frame_preview_process()
            w2.close()


def test_preview_updates_on_manual_method_and_preset_change(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    dummy_src = tmp_path / "test_hdr.mkv"
    dummy_src.write_text("test video content")
    w.src_edit.setText(str(dummy_src))
    doc = {
        "format": {"duration": "120.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
            }
        ]
    }
    w._apply_analysis_document(doc)

    # 1. Manual change of tonemap method triggers schedule
    w._frame_preview_timer.stop()
    assert not w._frame_preview_timer.isActive()
    w._select_curve("Hable \u00b7 CPU tonemap")
    assert w._frame_preview_timer.isActive()

    # 2. Applying preset triggers schedule
    w._frame_preview_timer.stop()
    w._apply_optimal_preset()
    assert w._frame_preview_timer.isActive()

    # 3. Filter toggles trigger schedule
    w._frame_preview_timer.stop()
    w.deband_chk.setChecked(not w.deband_chk.isChecked())
    assert w._frame_preview_timer.isActive()

    w._frame_preview_timer.stop()
    w.autocrop_chk.setChecked(not w.autocrop_chk.isChecked())
    assert w._frame_preview_timer.isActive()

    w._frame_preview_timer.stop()
    w.resharpen_chk.setChecked(not w.resharpen_chk.isChecked())
    assert w._frame_preview_timer.isActive()

    w._frame_preview_timer.stop()
    w.denoise_combo.setCurrentText("Light" if w.denoise_combo.currentText() == "Off" else "Off")
    assert w._frame_preview_timer.isActive()

    w.close()


def test_reset_old_frame_on_source_switch(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    file1 = tmp_path / "file1.mkv"
    file1.write_text("file 1 content")
    file2 = tmp_path / "file2.mkv"
    file2.write_text("file 2 content")

    w.src_edit.setText(str(file1))
    w._set_source(str(file1), analyze=False)

    # Simulate an active preview frame
    dummy_pix = QPixmap(100, 100)
    dummy_pix.fill(Qt.GlobalColor.red)
    w._current_preview_pixmap = dummy_pix
    w.preview_label.setPixmap(dummy_pix)
    w._set_preview_meta("File 1 preview at 0:10")

    assert w._current_preview_pixmap is not None
    assert not w.preview_label.pixmap().isNull()

    # Switch to file2
    w._set_source(str(file2), analyze=False)

    # Verify stale frame and meta are cleared immediately
    assert w._current_preview_pixmap is None
    assert w.preview_label.pixmap().isNull()
    assert w.preview_meta_label.text() == ""
    assert not hasattr(w, "preview_expand_btn")
    assert not hasattr(w, "preview_save_btn")

    w.close()


def test_source_analysis_known_and_unknown_fields(qapp):
    w = MainWindow()

    # Stream with missing color transfer and primaries
    doc_missing = {
        "format": {"duration": "90.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                # color_transfer and color_primaries omitted
            }
        ]
    }
    w._apply_analysis_document(doc_missing)
    info_missing = w.analysis_label.text()
    # Must omit unknown transfer and primaries from short summary line (no "Unknown · Unknown" or guessed values)
    assert "Unknown · Unknown" not in info_missing
    assert "smpte2084" not in info_missing
    assert "bt2020" not in info_missing
    assert "1920×1080" in info_missing
    assert "8-bit" in info_missing
    assert "1m 30s" in info_missing

    # Stream with known transfer and primaries
    doc_known = {
        "format": {"duration": "90.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
            }
        ]
    }
    w._apply_analysis_document(doc_known)
    info_known = w.analysis_label.text()
    assert "bt.2020" in info_known.lower()
    assert "smpte2084" in info_known.lower() or "pq" in info_known.lower() or "hdr10" in info_known.lower()

    w.close()


def test_dv_profile_and_level_separate_display(qapp):
    w = MainWindow()

    # Both profile and level known: Profile 5, Level 6
    dv_both = {
        "side_data_list": [
            {
                "side_data_type": "DOVI configuration record",
                "dv_profile": 5,
                "dv_level": 6,
                "rpu_present_flag": 1,
            }
        ]
    }
    res_both = w._hdr_metadata_details(dv_both, {})
    assert any("Profile 5 \u00b7 Level 6" in line for line in res_both)
    assert not any("Profile 5.6" in line for line in res_both)

    # Profile known, level unknown
    dv_prof = {
        "side_data_list": [
            {
                "side_data_type": "DOVI configuration record",
                "dv_profile": 5,
                "rpu_present_flag": 1,
            }
        ]
    }
    res_prof = w._hdr_metadata_details(dv_prof, {})
    assert any("Profile 5" in line for line in res_prof)
    assert not any("Level" in line for line in res_prof)

    # Profile unknown
    dv_none = {
        "side_data_list": [
            {
                "side_data_type": "DOVI configuration record",
                "rpu_present_flag": 1,
            }
        ]
    }
    res_none = w._hdr_metadata_details(dv_none, {})
    assert any("Profile" in line for line in res_none)

    w.close()


def test_dv_profile5_incompatibility_reason_and_actions(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    dummy_src = tmp_path / "test_dv_p5.mp4"
    dummy_src.write_text("dummy")
    w.src_edit.setText(str(dummy_src))

    # Set analysis doc with DV Profile 5
    doc = {
        "format": {"duration": "60.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "side_data_list": [
                    {
                        "side_data_type": "DOVI configuration record",
                        "dv_profile": 5,
                        "dv_level": 6,
                        "rpu_present_flag": 1,
                    }
                ]
            }
        ]
    }
    w._apply_analysis_document(doc)

    # --- Case 1: GPU tonemap is AVAILABLE (has_bt2390 = True) ---
    w.has_bt2390 = True

    # 1.1 Incompatible curve (CPU tonemap) -> displays reason + Switch to GPU action link
    w._select_curve("Hable \u00b7 CPU tonemap")
    w._run_frame_preview()
    preview_text_cpu = w.preview_label.text()
    assert "Profile 5" in preview_text_cpu
    assert "action:switch_gpu" in preview_text_cpu

    # Clicking action:switch_gpu switches to BT.2390
    w._on_preview_action_clicked("action:switch_gpu")
    assert "BT.2390" in w.method_combo.currentText()

    # 1.2 Incompatible backend (HandBrake) -> displays reason + Switch to FFmpeg & GPU action link
    w.backend_combo.setCurrentText("HandBrake")
    w._run_frame_preview()
    preview_text_hb = w.preview_label.text()
    assert "Profile 5" in preview_text_hb
    assert "action:switch_gpu" in preview_text_hb
    assert "FFmpeg" in preview_text_hb

    # Clicking action:switch_gpu switches to FFmpeg backend
    w._on_preview_action_clicked("action:switch_gpu")
    assert w.backend_combo.currentText() == "FFmpeg"

    # --- Case 2: GPU tonemap is UNAVAILABLE (has_bt2390 = False) ---
    w.has_bt2390 = False

    # 2.1 CPU tonemap without GPU capability -> localized explanation, NO action link
    w._select_curve("Hable \u00b7 CPU tonemap")
    w._run_frame_preview()
    preview_text_no_gpu_cpu = w.preview_label.text()
    assert "Profile 5" in preview_text_no_gpu_cpu
    assert "action:switch" not in preview_text_no_gpu_cpu
    assert "<a href" not in preview_text_no_gpu_cpu

    # 2.2 HandBrake backend without GPU capability -> localized explanation, NO action link
    w.backend_combo.setCurrentText("HandBrake")
    w._run_frame_preview()
    preview_text_no_gpu_hb = w.preview_label.text()
    assert "Profile 5" in preview_text_no_gpu_hb
    assert "action:switch" not in preview_text_no_gpu_hb
    assert "<a href" not in preview_text_no_gpu_hb

    # 2.3 Source analysis also omits the action link when GPU tonemap is unavailable
    w._apply_analysis_document(doc)
    analysis_text = w.analysis_label.text()
    assert "Profile 5" in analysis_text
    assert "action:switch" not in analysis_text

    w.backend_combo.setCurrentText("FFmpeg")
    w.encoder_combo.setCurrentText("CPU · H.264")
    w.close()


def test_quality_label_and_custom_preset_highlighting(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()
    w.backend_combo.setCurrentText("FFmpeg")
    w.encoder_combo.setCurrentText("CPU · H.264")

    # 1. Absence of Custom button
    assert not hasattr(w, "preset_custom_btn"), "Custom button must be removed from MainWindow"
    assert w.preset_segment_frame.layout().count() == 3, "Preset segment must only have 3 buttons: Optimal, Best, Fast"
    segment_buttons = [w.preset_segment_frame.layout().itemAt(i).widget() for i in range(3)]
    assert segment_buttons == [w.preset_optimal_btn, w.preset_best_btn, w.preset_fast_btn]

    # 2. Quality title and mode combo in unified row
    w.quality_mode_combo.setCurrentIndex(0)
    assert w.quality_head_label.text() == w.tr("Quality")
    assert w.quality_mode_combo.currentText() == "CRF/CQ"

    w.quality_mode_combo.setCurrentIndex(1)
    assert w.quality_head_label.text() == w.tr("Quality")
    assert w.quality_mode_combo.currentText() == "Bitrate"
    w.quality_mode_combo.setCurrentIndex(0)

    # 2.1 Pattern verification for Quality:
    # Row 1: Title & tier left, stepper & mode combo right
    # Row 2: Full-width slider below
    assert w.q_head_row.indexOf(w.quality_head_label) >= 0
    assert w.q_head_row.indexOf(w.quality_mode_combo) >= 0
    assert w.q_head_row.indexOf(w.quality_stepper) >= 0
    assert w.quality_slider.parent() is not None

    # 3. Dynamic matching of presets and unchecking when parameters diverge
    doc = {
        "format": {"duration": "120.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
            }
        ]
    }
    w.src_edit.setText("test.mp4")
    w._apply_analysis_document(doc)

    # Optimal preset
    w._apply_optimal_preset()
    assert w.preset_optimal_btn.isChecked()
    assert not w.preset_best_btn.isChecked()
    assert not w.preset_fast_btn.isChecked()

    # Changing brightness -> all 3 presets must uncheck immediately (no nearest preset selected!)
    w.brightness_spin.setValue(10)
    assert not w.preset_optimal_btn.isChecked()
    assert not w.preset_best_btn.isChecked()
    assert not w.preset_fast_btn.isChecked()

    # Restoring brightness to neutral 0 -> Optimal is re-highlighted
    w.brightness_spin.setValue(0)
    assert w.preset_optimal_btn.isChecked()
    assert not w.preset_best_btn.isChecked()
    assert not w.preset_fast_btn.isChecked()

    # Changing colour temperature -> all 3 presets uncheck
    w.colortemperature_spin.setValue(5000)
    assert not w.preset_optimal_btn.isChecked()
    assert not w.preset_best_btn.isChecked()
    assert not w.preset_fast_btn.isChecked()

    # Restoring colour temperature to neutral 6500 K -> Optimal re-highlighted
    w.colortemperature_spin.setValue(6500)
    assert w.preset_optimal_btn.isChecked()

    # Changing quality spin to non-preset value -> all 3 presets uncheck
    w.quality_spin.setValue(35)
    assert not w.preset_optimal_btn.isChecked()
    assert not w.preset_best_btn.isChecked()
    assert not w.preset_fast_btn.isChecked()

    # Best quality preset
    w._apply_best_quality_preset()
    assert w.preset_best_btn.isChecked()
    assert not w.preset_optimal_btn.isChecked()
    assert not w.preset_fast_btn.isChecked()

    # Fast preset
    w._apply_fast_preset()
    assert w.preset_fast_btn.isChecked()
    assert not w.preset_optimal_btn.isChecked()
    assert not w.preset_best_btn.isChecked()

    # 4. Sequentially toggle Deband, Auto-crop, Resharpen, and Denoise (!= Off)
    # Each must uncheck all 3 presets, and restoring each must re-highlight Optimal
    w.encoder_combo.setCurrentText("CPU · H.264")
    w._apply_optimal_preset()
    assert w.preset_optimal_btn.isChecked()

    # Deband
    w.deband_chk.setChecked(True)
    assert not w.preset_optimal_btn.isChecked() and not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()
    w.deband_chk.setChecked(False)
    assert w.preset_optimal_btn.isChecked() and not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()

    # Auto-crop
    w.autocrop_chk.setChecked(True)
    assert not w.preset_optimal_btn.isChecked() and not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()
    w.autocrop_chk.setChecked(False)
    assert w.preset_optimal_btn.isChecked() and not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()

    # Resharpen
    w.resharpen_chk.setChecked(True)
    assert not w.preset_optimal_btn.isChecked() and not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()
    w.resharpen_chk.setChecked(False)
    assert w.preset_optimal_btn.isChecked() and not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()

    # Denoise (!= Off)
    w.denoise_combo.setCurrentText("Light")
    assert not w.preset_optimal_btn.isChecked() and not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()
    w.denoise_combo.setCurrentText("Off")
    assert w.preset_optimal_btn.isChecked() and not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()

    # 5. Check unified control structure and neutral origins
    assert w.brightness_slider.bidirectional is True
    assert w.brightness_slider.origin_value == 0.0
    assert w.saturation_slider.bidirectional is True
    assert w.saturation_slider.origin_value == 0.0
    assert w.colortemperature_slider.bidirectional is True
    assert w.colortemperature_slider.origin_value == 6500.0
    assert w.quality_slider.bidirectional is False

    # Check 6500 K neutrality: at the neutral value the fill marker sits exactly on the origin mark
    cts = w.colortemperature_slider
    cts.setValue(6500)
    cts._snap()
    assert cts.value() == 6500 and not cts.isModified()
    assert abs(cts._x_for(cts._pos_now()) - cts._x_for(cts.origin_value)) < 0.5, \
        "At 6500 K the marker must align with the origin mark"

    w.backend_combo.setCurrentText("FFmpeg")
    w.close()


def test_advanced_filters_grid_and_collapse(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # Grid has 4 items (Deband, Auto-crop, Resharpen, Denoise)
    assert w.adv_grid_layout.count() == 4

    # Force narrow layout
    w._update_conversion_adaptive_layout(240)
    assert w._adv_grid_is_two_col is False
    # Check that widgets are stacked vertically in col 0
    for row in range(4):
        item = w.adv_grid_layout.itemAtPosition(row, 0)
        assert item is not None and item.widget() is not None
    assert w.adv_grid_layout.itemAtPosition(0, 0).widget() is w.deband_chk
    assert w.adv_grid_layout.itemAtPosition(1, 0).widget() is w.autocrop_chk
    assert w.adv_grid_layout.itemAtPosition(2, 0).widget() is w.resharpen_chk
    assert w.adv_grid_layout.itemAtPosition(3, 0).widget() is w.denoise_container

    # Force wide layout
    w._update_conversion_adaptive_layout(450)
    assert w._adv_grid_is_two_col is True
    assert w.adv_grid_layout.itemAtPosition(0, 0).widget() is w.deband_chk
    assert w.adv_grid_layout.itemAtPosition(0, 1).widget() is w.autocrop_chk
    assert w.adv_grid_layout.itemAtPosition(1, 0).widget() is w.resharpen_chk
    assert w.adv_grid_layout.itemAtPosition(1, 1).widget() is w.denoise_container

    # Denoise is in adv_grid_layout beside resharpen
    assert w.adv_grid_layout.indexOf(w.denoise_container) >= 0
    w.adv_filters_btn.click()
    qapp.processEvents()
    assert w.denoise_label.geometry().right() <= w.denoise_combo.geometry().left()

    # Check bidirectional sliders
    assert w.brightness_slider.bidirectional is True
    assert w.saturation_slider.bidirectional is True
    assert w.colortemperature_slider.bidirectional is True
    assert w.colortemperature_slider.origin_value == 6500.0

    # Check desat highlights tooltip
    assert len(w.desat_chk.toolTip()) > 30

    w.close()


def test_geometry_quality_and_advanced_filters_at_narrow_and_scaled(qapp):
    w = MainWindow()
    w.change_language("en")
    w.show()
    qapp.processEvents()

    # 1. Quality tier label and CRF/CQ combobox hidden, Denoise button lengthened
    assert not w.quality_tier_lbl.isVisible()
    assert not w.quality_mode_combo.isVisible()
    assert w.quality_tier_lbl.width() == 0
    assert w.denoise_combo.maximumWidth() == 150

    # 2. Boundary checks in English (threshold 149 px with tier and combo hidden)
    assert w._q_row_threshold in (149, 195), f"Expected threshold 149 or 195, got {w._q_row_threshold}"
    en_threshold = w._q_row_threshold

    for width in (120, 140, en_threshold - 1, en_threshold, 240, 368):
        w.conv_scroll_content.setFixedWidth(width)
        w._update_conversion_adaptive_layout(width)
        qapp.processEvents()

        # Geometry checks: non-zero width and height, no clipping
        assert w.quality_slider.width() > 0, f"quality_slider width is zero at {width}px"
        assert w.quality_slider.height() > 0, f"quality_slider height is zero at {width}px"
        assert w.quality_stepper.width() >= 92, f"quality_stepper width clipped at {width}px"
        assert w.quality_head_label.width() > 0, f"quality_head_label width is zero at {width}px"

        if width < w._q_row_threshold:
            # Stepper wraps to q_sub_widget below header row
            assert not w._q_is_beside, f"Expected _q_is_beside=False at {width}px"
            assert w.q_sub_widget.isVisible(), f"Expected q_sub_widget to be visible at {width}px"
            assert w.q_sub_row.indexOf(w.quality_stepper) >= 0
        else:
            # Stepper remains in first row (q_head_row)
            assert w._q_is_beside, f"Expected _q_is_beside=True at {width}px"
            assert not w.q_sub_widget.isVisible(), f"Expected q_sub_widget to be hidden at {width}px"
            assert w.q_head_row.indexOf(w.quality_stepper) >= 0
            card_r = w.q_head_row.parentWidget().mapTo(w.conv_scroll_content, w.q_head_row.parentWidget().rect().topRight()).x()
            q_r = w.quality_stepper.mapTo(w.conv_scroll_content, w.quality_stepper.rect().topRight()).x()
            assert q_r <= card_r, f"Quality stepper overflows card at {width}px: Q_R={q_r} Card_R={card_r}"

    # 3. Russian language threshold recalculation (threshold 161 or 207 px)
    w.change_language("ru")
    qapp.processEvents()
    assert w.quality_head_label.text() == "Качество"
    ru_threshold = w._q_row_threshold
    assert ru_threshold in (161, 207), f"Expected Russian threshold 161 or 207, got {ru_threshold}"

    # Below Russian threshold
    w.conv_scroll_content.setFixedWidth(ru_threshold - 1)
    w._update_conversion_adaptive_layout(ru_threshold - 1)
    qapp.processEvents()
    assert not w._q_is_beside
    assert w.q_sub_widget.isVisible()

    # At exact Russian threshold
    w.conv_scroll_content.setFixedWidth(ru_threshold)
    w._update_conversion_adaptive_layout(ru_threshold)
    qapp.processEvents()
    assert w._q_is_beside
    assert not w.q_sub_widget.isVisible()
    card_r = w.q_head_row.parentWidget().mapTo(w.conv_scroll_content, w.q_head_row.parentWidget().rect().topRight()).x()
    q_r = w.quality_stepper.mapTo(w.conv_scroll_content, w.quality_stepper.rect().topRight()).x()
    assert q_r <= card_r, f"Quality stepper overflows card in Russian: Q_R={q_r} Card_R={card_r}"

    # Restore English
    w.change_language("en")
    qapp.processEvents()
    assert w.quality_head_label.text() == "Quality"
    assert w._q_row_threshold == en_threshold

    # 4. DPI scaling metrics check and actual Conversion width transitions at 100%, 150%, and 200%
    from PySide6.QtGui import QFont
    for dpi_pct, pt_size in ((100, 9), (150, 13), (200, 18)):
        font = QFont("Segoe UI", pt_size)
        w.encoder_combo.setFont(font)
        w.quality_head_label.setFont(font)
        w.quality_head_label.updateGeometry()
        w._update_conversion_adaptive_layout()
        qapp.processEvents()

        threshold = w._q_row_threshold
        if dpi_pct == 100:
            assert threshold in (149, 195), f"Expected threshold 149/195 at 100% font scale, got {threshold}"
        elif dpi_pct == 150:
            assert threshold in (199, 205, 206), f"Expected threshold ~199-206 at 150% font scale, got {threshold}"
        elif dpi_pct == 200:
            assert threshold in (242, 243, 244, 282, 283), f"Expected threshold ~242-283 at 200% font scale, got {threshold}"

        # Test actual Conversion width below threshold
        w.conv_scroll_content.setFixedWidth(threshold - 5)
        w._update_conversion_adaptive_layout(threshold - 5)
        qapp.processEvents()
        assert not w._q_is_beside, f"Expected wrapped stepper below threshold ({threshold - 5}px) at DPI {dpi_pct}%"
        assert w.q_sub_widget.isVisible(), f"Expected q_sub_widget visible below threshold at DPI {dpi_pct}%"
        assert w.quality_slider.width() > 0, f"quality_slider width zero below threshold at DPI {dpi_pct}%"
        assert w.quality_slider.height() > 0, f"quality_slider height zero below threshold at DPI {dpi_pct}%"
        assert w.quality_stepper.width() >= 92, f"quality_stepper clipped below threshold at DPI {dpi_pct}%"

        # Test actual Conversion width at threshold
        w.conv_scroll_content.setFixedWidth(threshold)
        w._update_conversion_adaptive_layout(threshold)
        qapp.processEvents()
        assert w._q_is_beside, f"Expected beside stepper at threshold ({threshold}px) at DPI {dpi_pct}%"
        assert not w.q_sub_widget.isVisible(), f"Expected q_sub_widget hidden at threshold at DPI {dpi_pct}%"
        assert w.quality_slider.width() > 0, f"quality_slider width zero at threshold at DPI {dpi_pct}%"
        assert w.quality_slider.height() > 0, f"quality_slider height zero at threshold at DPI {dpi_pct}%"
        assert w.quality_stepper.width() >= 92, f"quality_stepper clipped at threshold at DPI {dpi_pct}%"
        card_r = w.q_head_row.parentWidget().mapTo(w.conv_scroll_content, w.q_head_row.parentWidget().rect().topRight()).x()
        q_r = w.quality_stepper.mapTo(w.conv_scroll_content, w.quality_stepper.rect().topRight()).x()
        assert q_r <= card_r, f"Quality stepper overflows card at DPI {dpi_pct}%: Q_R={q_r} Card_R={card_r}"

    # 5. Bitrate mode boundary check and containment in English and Russian
    font = QFont("Segoe UI", 9)
    w.encoder_combo.setFont(font)
    w.quality_head_label.setFont(font)
    w.quality_head_label.updateGeometry()

    w.quality_mode_combo.setCurrentText("Bitrate")
    qapp.processEvents()
    en_br_thresh = w._q_row_threshold
    assert en_br_thresh in (173, 219, 306), f"Expected EN Bitrate threshold, got {en_br_thresh}"

    # Below threshold in Bitrate mode
    w.conv_scroll_content.setFixedWidth(en_br_thresh - 1)
    w._update_conversion_adaptive_layout(en_br_thresh - 1)
    qapp.processEvents()
    assert not w._q_is_beside
    assert w.q_sub_widget.isVisible()

    # At exact threshold in Bitrate mode
    w.conv_scroll_content.setFixedWidth(en_br_thresh)
    w._update_conversion_adaptive_layout(en_br_thresh)
    qapp.processEvents()
    assert w._q_is_beside
    assert not w.q_sub_widget.isVisible()
    card_r = w.q_head_row.parentWidget().mapTo(w.conv_scroll_content, w.q_head_row.parentWidget().rect().topRight()).x()
    br_r = w.bitrate_stepper.mapTo(w.conv_scroll_content, w.bitrate_stepper.rect().topRight()).x()
    assert br_r <= card_r, f"Bitrate stepper overflows card at threshold: Bitrate_R={br_r} Card_R={card_r}"

    # Russian Bitrate mode
    w.change_language("ru")
    qapp.processEvents()
    ru_br_thresh = w._q_row_threshold
    assert ru_br_thresh in (185, 231, 306), f"Expected RU Bitrate threshold, got {ru_br_thresh}"

    w.conv_scroll_content.setFixedWidth(ru_br_thresh - 1)
    w._update_conversion_adaptive_layout(ru_br_thresh - 1)
    qapp.processEvents()
    assert not w._q_is_beside
    assert w.q_sub_widget.isVisible()

    w.conv_scroll_content.setFixedWidth(ru_br_thresh)
    w._update_conversion_adaptive_layout(ru_br_thresh)
    qapp.processEvents()
    assert w._q_is_beside
    assert not w.q_sub_widget.isVisible()
    card_r = w.q_head_row.parentWidget().mapTo(w.conv_scroll_content, w.q_head_row.parentWidget().rect().topRight()).x()
    br_r = w.bitrate_stepper.mapTo(w.conv_scroll_content, w.bitrate_stepper.rect().topRight()).x()
    assert br_r <= card_r, f"Bitrate stepper overflows card in Russian: Bitrate_R={br_r} Card_R={card_r}"

    # Restore defaults
    w.change_language("en")
    w.quality_mode_combo.setCurrentText("CRF/CQ")
    w.quality_spin.setValue(18)
    w.conv_scroll_content.setMinimumWidth(0)
    w.conv_scroll_content.setMaximumWidth(16777215)
    w.conv_scroll.viewport().setMinimumWidth(0)
    w.conv_scroll.viewport().setMaximumWidth(16777215)
    w.conv_scroll.setMinimumWidth(0)
    w.conv_scroll.setMaximumWidth(16777215)
    w._update_conversion_adaptive_layout()
    qapp.processEvents()

    # 6. Denoise width and spacing
    assert w.denoise_combo.maximumWidth() == 150

    w.close()


def test_output_folder_summary_params_and_cpu_cores(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # Output folder button
    assert w.output_folder_btn.text() == w.tr("Output folder")
    assert "output folder" in w.output_folder_btn.toolTip().lower()

    # Set custom folder
    out_dir = tmp_path / "custom_out"
    out_dir.mkdir()
    w.output_folder = out_dir
    w._update_output_folder_btn()
    assert str(out_dir) in w.output_folder_btn.toolTip()

    # CPU cores & priority
    assert "CPU cores & priority" in w.cores_title_lbl.text()
    assert len(w.cores_title_lbl.toolTip()) > 20
    assert w.cores_note_lbl.isHidden()

    # Output summary with valid source
    dummy_src = tmp_path / "source.mp4"
    dummy_src.write_text("test")
    w.src_edit.setText(str(dummy_src))
    w.duration = 120.0
    w.src_width = 3840
    w.src_height = 2160
    w.update_bitrate_estimate()

    # Bitrate label only shows bitrate, without duplicate file size
    assert "Approx. output bitrate:" in w.bitrate_label.text()
    assert "Mbps" in w.bitrate_label.text()
    assert "for this source" not in w.bitrate_label.text()
    assert "GB" not in w.bitrate_label.text()
    assert "MB" not in w.bitrate_label.text()

    # Output summary shows resolution, codec, and approximate file size
    assert w.summary_params_label.isVisible()
    summary_text = w.summary_params_label.text()
    assert "Output summary" in summary_text
    assert "approx." in summary_text
    assert "GB" in summary_text or "MB" in summary_text

    w.close()


def test_localization_switching(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # Switch to Russian
    w.change_language("ru")
    qapp.processEvents()

    assert w.output_folder_btn.text() == "Папка вывода"
    assert "Ядра CPU и приоритет" in w.cores_title_lbl.text()
    assert "Качество" in w.quality_head_label.text()

    # Switch back to English
    w.change_language("en")
    qapp.processEvents()
    assert w.output_folder_btn.text() == "Output folder"
    assert "CPU cores & priority" in w.cores_title_lbl.text()
    assert w.quality_head_label.text() == "Quality"

    w.close()
