import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QPoint, QSize, QProcess, QEvent
from PySide6.QtGui import QColor, QPixmap, QKeyEvent
from PySide6.QtWidgets import QApplication, QFileDialog

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.compare_dialog import TonemapComparisonDialog, FullscreenFrameDialog
from hdr2sdr.ui.tonemap_comparison import TonemapCompareItem, TonemapComparator
from hdr2sdr.ui.widgets.sliders import PreviewScrubSlider


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_main_window_live_preview_minimal_controls(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # 1. Verify Live preview card exists and has only the required elements
    assert hasattr(w, "preview_card")
    assert hasattr(w, "preview_label")
    assert hasattr(w, "preview_position_label")
    assert hasattr(w, "preview_slider")

    # The slider must be single scrub slider (PreviewScrubSlider)
    assert isinstance(w.preview_slider, PreviewScrubSlider)

    # 2. Verify removed buttons and separate range label are NOT in Live preview layout
    layout_widgets = []
    for i in range(w.preview_card.body.count()):
        item = w.preview_card.body.itemAt(i)
        if item and item.widget():
            layout_widgets.append(item.widget())

    for i in range(w.preview_controls_widget.layout().count()):
        item = w.preview_controls_widget.layout().itemAt(i)
        if item and item.widget():
            layout_widgets.append(item.widget())

    assert w.preview_label in layout_widgets
    assert w.preview_position_label in layout_widgets
    assert w.preview_slider in layout_widgets

    # Former controls must NOT be present as active attributes on MainWindow or in preview card
    assert not hasattr(w, "preview_expand_btn")
    assert not hasattr(w, "preview_save_btn")
    assert not hasattr(w, "test_clip_btn")
    assert not hasattr(w, "test_clip_open_btn")
    assert not hasattr(w, "preview_duration_label")

    # 3. Verify compare_methods_btn is relocated (not inside preview card layout)
    assert hasattr(w, "compare_methods_btn")
    assert w.compare_methods_btn not in layout_widgets

    # 4. Scrubbing behavior: changing slider value updates position label and schedules preview
    w.preview_slider.setRange(0, 100)
    w.preview_slider.setValue(45)
    qapp.processEvents()

    assert w.preview_position_label.text() == "0:45 / 1:40"
    assert w._preview_seek_seconds() == 45.0

    w.close()


def test_compare_dialog_cohesive_workspace_structure(qapp, tmp_path):
    w = MainWindow()
    w.duration = 120.0
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # 1. Neutral background around the image
        img_container_ss = dlg.image_container.styleSheet()
        assert "#09090B" in img_container_ss or "#compareImageContainer" in img_container_ss

        # 2. Header card is borderless and clean
        header_ss = dlg.header_card.styleSheet()
        assert "border: none" in header_ss
        assert "background: transparent" in header_ss

        # 3. Compact Frame Action Bar directly under the image
        assert hasattr(dlg, "frame_bar")
        assert hasattr(dlg, "fullscreen_btn")
        assert hasattr(dlg, "save_btn")
        assert hasattr(dlg, "preview_position_label")
        assert hasattr(dlg, "preview_slider")

        # Initial state with no pixmap: frame actions disabled
        assert not dlg.fullscreen_btn.isEnabled()
        assert not dlg.save_btn.isEnabled()

        # Tooltips localized
        assert dlg.fullscreen_btn.toolTip() != ""
        assert dlg.save_btn.toolTip() != ""
        assert dlg.preview_slider.toolTip() != ""

        # Position display includes current seek and total duration
        assert "0:00 / 2:00" in dlg.preview_position_label.text()

        # 4. Test clip section: duration presets + spinbox + action buttons + status
        assert hasattr(dlg, "actions_card")
        assert hasattr(dlg, "clip_section_lbl")
        assert hasattr(dlg, "clip_dur_lbl")
        assert hasattr(dlg, "btn_dur_3s")
        assert hasattr(dlg, "btn_dur_5s")
        assert hasattr(dlg, "btn_dur_10s")
        assert hasattr(dlg, "test_clip_dur_spin")
        assert hasattr(dlg, "test_clip_btn")
        assert hasattr(dlg, "test_clip_open_btn")
        assert hasattr(dlg, "test_clip_status_lbl")

        # Default duration is 5s, preset 5s is checked
        assert dlg.test_clip_dur_spin.value() == 5
        assert dlg.btn_dur_5s.isChecked()
        assert not dlg.btn_dur_3s.isChecked()
        assert not dlg.btn_dur_10s.isChecked()

        # Render test clip button is prominent (role 'go')
        assert dlg.test_clip_btn.property("role") == "go"
        assert not dlg.test_clip_open_btn.isEnabled()

        dlg.close()
    w.close()


def test_compare_dialog_duration_presets_and_custom(qapp, tmp_path):
    w = MainWindow()
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # Click 3s preset
        dlg.btn_dur_3s.click()
        assert dlg.test_clip_dur_spin.value() == 3
        assert dlg.btn_dur_3s.isChecked()
        assert not dlg.btn_dur_5s.isChecked()
        assert not dlg.btn_dur_10s.isChecked()

        # Click 10s preset
        dlg.btn_dur_10s.click()
        assert dlg.test_clip_dur_spin.value() == 10
        assert dlg.btn_dur_10s.isChecked()
        assert not dlg.btn_dur_3s.isChecked()
        assert not dlg.btn_dur_5s.isChecked()

        # Set custom manual value in spinbox (e.g. 7s)
        dlg.test_clip_dur_spin.setValue(7)
        assert dlg.test_clip_dur_spin.value() == 7
        assert not dlg.btn_dur_3s.isChecked()
        assert not dlg.btn_dur_5s.isChecked()
        assert not dlg.btn_dur_10s.isChecked()

        # Click 5s preset returns to 5s
        dlg.btn_dur_5s.click()
        assert dlg.test_clip_dur_spin.value() == 5
        assert dlg.btn_dur_5s.isChecked()

        dlg.close()
    w.close()


def test_compare_dialog_timeline_scrubbing_and_updating(qapp, tmp_path):
    w = MainWindow()
    w.i18n.settings.setValue("compare_auto_render", True)
    w.duration = 200.0
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start") as mock_start:
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # Scrub slider to 50 seconds
        dlg.preview_slider.setValue(50)
        assert dlg.seek == 50.0
        assert "0:50 / 3:20" in dlg.preview_position_label.text()
        assert "0:50" in dlg.active_variant_meta.text()

        # Trigger seek debounce timeout
        dlg._seek_debounce_timer.stop()
        dlg._on_seek_debounce_timeout()

        assert dlg.comparator.seek == 50.0
        assert mock_start.call_count >= 2  # initial + seek reload

        dlg.close()
    w.close()


def test_fullscreen_frame_dialog_aspect_ratio_and_return(qapp):
    orig_w, orig_h = 1920, 1080
    pix = QPixmap(orig_w, orig_h)
    pix.fill(QColor("#2563EB"))

    dlg = FullscreenFrameDialog(pix, title="Fullscreen Test")
    dlg.show()
    qapp.processEvents()

    assert "Fullscreen Test" in dlg.windowTitle()
    assert hasattr(dlg, "back_btn")
    assert hasattr(dlg, "hint_lbl")

    dlg.resize(800, 800)
    qapp.processEvents()
    dlg._update_scaled_pixmap()

    scaled_pix = dlg.image_lbl.pixmap()
    assert scaled_pix is not None and not scaled_pix.isNull()
    ratio_orig = orig_w / orig_h
    ratio_scaled = scaled_pix.width() / scaled_pix.height()
    assert abs(ratio_orig - ratio_scaled) < 0.02

    closed_states = []
    dlg.accepted.connect(lambda: closed_states.append("accepted"))
    dlg.back_btn.click()
    qapp.processEvents()
    assert "accepted" in closed_states


def test_fullscreen_frame_dialog_esc_key(qapp):
    pix = QPixmap(320, 240)
    pix.fill(QColor("#10B981"))
    dlg = FullscreenFrameDialog(pix, title="Esc Test")
    dlg.show()
    qapp.processEvents()

    rejected_states = []
    dlg.rejected.connect(lambda: rejected_states.append("rejected"))

    esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dlg.keyPressEvent(esc_event)
    qapp.processEvents()

    assert "rejected" in rejected_states


def test_compare_dialog_save_filename_includes_method_and_gamut(qapp, tmp_path, monkeypatch):
    w = MainWindow()
    src_file = tmp_path / "my_movie.mkv"
    src_file.write_text("data")
    w.src_edit.setText(str(src_file))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.src_path = str(src_file)

        pix_gpu = QPixmap(100, 100)
        pix_gpu.fill(QColor("#38BDF8"))
        var_gpu = TonemapCompareItem(
            method="BT.2390 · GPU tonemap",
            engine="gpu",
            gamut="perceptual",
            pixmap=pix_gpu,
        )

        dlg.variants = [var_gpu]
        dlg._selected_index = 0

        proposed_filenames = []

        def mock_getSaveFileName(parent, caption, dir_path, filter_str):
            proposed_filenames.append(Path(dir_path).name)
            return ("", "")

        monkeypatch.setattr(QFileDialog, "getSaveFileName", mock_getSaveFileName)

        dlg._save_active_frame()
        assert len(proposed_filenames) == 1
        assert "my_movie" in proposed_filenames[0].lower()
        assert "bt.2390" in proposed_filenames[0].lower()
        assert "perceptual" in proposed_filenames[0].lower()

        pix_cpu = QPixmap(100, 100)
        pix_cpu.fill(QColor("#F59E0B"))
        var_cpu = TonemapCompareItem(
            method="Hable · CPU tonemap",
            engine="cpu",
            gamut="N/A",
            pixmap=pix_cpu,
        )
        dlg.variants.append(var_cpu)
        dlg._selected_index = 1

        dlg._save_active_frame()
        assert len(proposed_filenames) == 2
        assert "my_movie" in proposed_filenames[1].lower()
        assert "hable" in proposed_filenames[1].lower()
        assert "cpu" in proposed_filenames[1].lower()

        dlg.close()
    w.close()


def test_compare_dialog_test_clip_command_and_cancellation(qapp, tmp_path, monkeypatch):
    w = MainWindow()
    src_file = tmp_path / "movie_hdr.mkv"
    src_file.write_text("content")
    w.src_edit.setText(str(src_file))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.src_path = str(src_file)
        dlg.seek = 42.0

        var = TonemapCompareItem(
            method="Spline · GPU tonemap",
            engine="gpu",
            gamut="clip",
            pixmap=QPixmap(100, 100),
        )
        dlg.variants = [var]
        dlg._selected_index = 0

        dlg.test_clip_dur_spin.setValue(12)

        started_cmds = []

        def mock_start(proc_instance):
            started_cmds.append((proc_instance.program(), proc_instance.arguments()))
            return None

        monkeypatch.setattr(QProcess, "start", mock_start)
        monkeypatch.setattr(QProcess, "waitForStarted", lambda *args: True)

        with patch("hdr2sdr.ui.compare_dialog.exe", return_value="ffmpeg"):
            dlg._run_test_clip()

        assert len(started_cmds) == 1
        prog, args = started_cmds[0]
        assert "-ss" in args
        ss_idx = args.index("-ss")
        assert float(args[ss_idx + 1]) == 42.0
        assert "-t" in args
        t_idx = args.index("-t")
        assert args[t_idx + 1] == "12"

        # Button text changed to Cancel and tinted stop-ready
        assert dlg.test_clip_btn.text() == dlg.tr("Cancel clip")
        assert dlg.test_clip_btn.property("role") == "stop-ready"

        # Clicking again triggers cancellation and restores role go
        dlg._run_test_clip()
        assert dlg.test_clip_btn.text() == dlg.tr("Render test clip")
        assert dlg.test_clip_btn.property("role") == "go"
        assert "cancelled" in dlg.test_clip_status_lbl.text().lower()

        dlg.close()
    w.close()


def test_compare_dialog_test_clip_error_reporting(qapp, tmp_path):
    w = MainWindow()
    src_file = tmp_path / "movie_hdr.mkv"
    src_file.write_text("content")
    w.src_edit.setText(str(src_file))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg._test_clip_proc = MagicMock()
        dlg._last_test_clip_error = "CUDA out of memory"

        # Simulate finished with missing or 0-size output file
        dummy_out = tmp_path / "test_clip.mkv"
        dlg._on_test_clip_finished(dummy_out, generation=0)

        # Button restored, open button stays disabled
        assert dlg.test_clip_btn.text() == dlg.tr("Render test clip")
        assert not dlg.test_clip_open_btn.isEnabled()

        # Status label displays clear error message with detail
        status_text = dlg.test_clip_status_lbl.text()
        assert "CUDA out of memory" in status_text or "failed" in status_text.lower() or "ошибка" in status_text.lower()

        dlg.close()
    w.close()


def test_compare_dialog_apply_to_conversion(qapp, tmp_path):
    w = MainWindow()
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.seek = 75.0
        var = TonemapCompareItem(
            method="Reinhard · CPU tonemap",
            engine="cpu",
            gamut="N/A",
            pixmap=QPixmap(100, 100),
        )
        dlg.variants = [var]
        dlg._selected_index = 0

        dlg._apply_active_variant()

        assert w.method_combo.currentText() == "Reinhard · CPU tonemap"
        assert w.preview_slider.value() == 75

        dlg.close()
    w.close()
