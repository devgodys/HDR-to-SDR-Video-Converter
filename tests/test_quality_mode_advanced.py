import json
import os
from pathlib import Path
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QSize, QPoint, QPointF, QMimeData, QUrl, QEvent
from PySide6.QtGui import QFont, QKeyEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from hdr2sdr.core.commands import build_ffmpeg_command, build_handbrake_command, ConversionSettings
from hdr2sdr.ui.main_window import MainWindow, _QueueItemWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_quality_row_simplified_and_selector_relocated(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # 1. Mode selector is in primary Quality row in v24_9c
    assert w.q_head_row.indexOf(w.quality_mode_combo) >= 0, "CRF/CQ selector must be in q_head_row"
    assert w.q_head_row.indexOf(w.quality_head_label) >= 0
    assert w.quality_slider.parent() is not None
    assert w.bitrate_label.parent() is not None

    # Default mode is CRF/CQ, tier text and combo hidden per latest UI redesign
    assert w.quality_mode_combo.currentText() == "CRF/CQ"
    assert w.quality_stepper.isVisible()
    assert w.quality_slider.isVisible()
    assert not w.quality_tier_lbl.isVisible()
    assert not w.quality_mode_combo.isVisible()
    assert not w.bitrate_stepper.isVisible()
    assert not w.bitrate_slider.isVisible()
    assert w.hwaccel_chk.parent() is not None
    assert "Bitrate" not in w.adv_encoding_btn.text()
    assert "(1)" not in w.adv_encoding_btn.text()

    # 2. Advanced encoding button is removed/hidden from visible Encoding section
    assert hasattr(w, "adv_encoding_btn")
    assert hasattr(w, "adv_encoding_panel")
    assert not w.adv_encoding_btn.isVisible()
    assert not w.adv_encoding_panel.isVisible()
    assert not w._section_bodies["encoding"].isAncestorOf(w.adv_encoding_btn)

    w.close()


def test_adv_encoding_expand_collapse_preserves_values(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # Set custom values in CRF mode
    w.quality_spin.setValue(22)
    assert w.quality_spin.value() == 22
    assert w.quality_slider.value() == 22

    # Switch to Bitrate mode and set custom target bitrate
    w.quality_mode_combo.setCurrentText("Bitrate")
    qapp.processEvents()
    w.target_bitrate_spin.setValue(12500)
    assert w.target_bitrate_spin.value() == 12500
    assert w.bitrate_slider.value() == 12500
    assert w.bitrate_stepper.isVisible()
    assert w.bitrate_slider.isVisible()
    assert not w.quality_stepper.isVisible()
    assert not w.quality_slider.isVisible()
    assert "(Bitrate)" in w.adv_encoding_btn.text()
    assert "(1)" not in w.adv_encoding_btn.text()

    # Values remain preserved
    assert w.quality_mode_combo.currentText() == "Bitrate"
    assert w.target_bitrate_spin.value() == 12500
    assert w.bitrate_slider.value() == 12500
    assert w.quality_spin.value() == 22

    w.close()


def test_command_building_preserves_both_modes(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # 1. CRF/CQ mode command building
    w.quality_mode_combo.setCurrentText("CRF/CQ")
    w.quality_spin.setValue(19)
    prefs_crf = w.collect_settings()
    assert prefs_crf.quality_mode == "CRF/CQ"
    assert prefs_crf.quality == 19

    ffmpeg_crf = build_ffmpeg_command(prefs_crf)
    assert "-crf" in ffmpeg_crf
    assert ffmpeg_crf[ffmpeg_crf.index("-crf") + 1] == "19"

    prefs_crf_hb = w.collect_settings(backend="HandBrake")
    hb_crf, _ = build_handbrake_command(prefs_crf_hb)
    assert "-q" in hb_crf
    assert hb_crf[hb_crf.index("-q") + 1] == "19"

    # 2. Bitrate mode command building
    w.quality_mode_combo.setCurrentText("Bitrate")
    w.target_bitrate_spin.setValue(9500)
    prefs_br = w.collect_settings()
    assert prefs_br.quality_mode == "Bitrate"
    assert prefs_br.target_bitrate_kbps == 9500

    ffmpeg_br = build_ffmpeg_command(prefs_br)
    assert "-b:v" in ffmpeg_br
    assert ffmpeg_br[ffmpeg_br.index("-b:v") + 1] == "9500k"

    prefs_br_hb = w.collect_settings(backend="HandBrake")
    hb_br, _ = build_handbrake_command(prefs_br_hb)
    assert "-b" in hb_br
    assert hb_br[hb_br.index("-b") + 1] == "9500"
    assert "--multi-pass" in hb_br

    w.close()


def test_preset_and_bitrate_estimation_sync(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    dummy_src = tmp_path / "test_hdr.mkv"
    dummy_src.write_text("test video content")
    w.src_edit.setText(str(dummy_src))
    doc = {
        "format": {"duration": "100.0"},
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
    qapp.processEvents()

    # Applying preset switches to CRF/CQ and syncs preset button
    w.quality_mode_combo.setCurrentText("Bitrate")
    assert not w.preset_optimal_btn.isChecked()

    w.preset_optimal_btn.click()
    qapp.processEvents()
    assert w.quality_mode_combo.currentText() == "CRF/CQ"
    assert w.preset_optimal_btn.isChecked()

    # Switching to Bitrate unchecks CRF presets
    w.quality_mode_combo.setCurrentText("Bitrate")
    qapp.processEvents()
    assert not w.preset_optimal_btn.isChecked()

    # Bitrate estimate in Bitrate mode reflects target bitrate directly
    w.target_bitrate_spin.setValue(10000)
    qapp.processEvents()
    assert "10.0 Mbps" in w.bitrate_label.text()
    assert "Output summary" in w.summary_params_label.text()
    # 10000 kbps * 100 s / 8 = 125 MB ~= 0.1 GB
    assert "approx." in w.summary_params_label.text()

    w.close()


def test_localization_across_languages_and_mode_indicators(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # 1. English
    w.change_language("en")
    qapp.processEvents()
    assert w.quality_head_label.text() == "Quality"
    assert "Advanced encoding" in w.adv_encoding_btn.text()
    assert w.rate_control_lbl.text() == "Encoding mode:"

    # Mode indicator in English
    w.quality_mode_combo.setCurrentText("CRF/CQ")
    qapp.processEvents()
    assert "Bitrate" not in w.adv_encoding_btn.text()

    w.quality_mode_combo.setCurrentText("Bitrate")
    qapp.processEvents()
    assert "(Bitrate)" in w.adv_encoding_btn.text()

    # 2. Russian
    w.change_language("ru")
    qapp.processEvents()
    assert w.quality_head_label.text() == "Качество"
    assert "Расширенное кодирование" in w.adv_encoding_btn.text()
    assert w.rate_control_lbl.text() == "Режим кодирования:"

    # Mode indicator in Russian
    assert "(Режим: Bitrate)" in w.adv_encoding_btn.text()

    w.quality_mode_combo.setCurrentText("CRF/CQ")
    qapp.processEvents()
    assert "Режим: Bitrate" not in w.adv_encoding_btn.text()

    # 3. German
    w.change_language("de")
    qapp.processEvents()
    assert "Erweiterte Kodierung" in w.adv_encoding_btn.text()
    assert w.rate_control_lbl.text() == "Kodierungsmodus:"

    w.quality_mode_combo.setCurrentText("Bitrate")
    qapp.processEvents()
    assert "(Modus: Bitrate)" in w.adv_encoding_btn.text()

    # Restore English
    w.change_language("en")
    qapp.processEvents()

    w.close()


def test_conversion_preferences_quality_mode_persistence(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # 1. Switch to Bitrate mode and save preferences
    w.quality_mode_combo.setCurrentText("Bitrate")
    w._conversion_preferences.save()

    raw = w.i18n.settings.value("conversion_preferences", "{}")
    stored = json.loads(raw) if isinstance(raw, str) else dict(raw)
    assert stored.get("quality_mode") == "Bitrate", "Key 'quality_mode' must store 'Bitrate' without changing setting key"

    # 2. Change in-memory state to CRF/CQ and verify restore() restores Bitrate
    w.quality_mode_combo.setCurrentText("CRF/CQ")
    assert w.quality_mode_combo.currentText() == "CRF/CQ"

    w._conversion_preferences.restore()
    qapp.processEvents()
    assert w.quality_mode_combo.currentText() == "Bitrate"
    assert "(Bitrate)" in w.adv_encoding_btn.text()

    # 3. Save as CRF/CQ and verify restore() restores CRF/CQ
    w.quality_mode_combo.setCurrentText("CRF/CQ")
    w._conversion_preferences.save()

    raw_crf = w.i18n.settings.value("conversion_preferences", "{}")
    stored_crf = json.loads(raw_crf) if isinstance(raw_crf, str) else dict(raw_crf)
    assert stored_crf.get("quality_mode") == "CRF/CQ"

    w.quality_mode_combo.setCurrentText("Bitrate")
    w._conversion_preferences.restore()
    qapp.processEvents()
    assert w.quality_mode_combo.currentText() == "CRF/CQ"
    assert "Bitrate" not in w.adv_encoding_btn.text()

    w.close()


def test_adaptive_layout_and_dpi_scaling(qapp):
    w = MainWindow()
    w.change_language("en")
    w.show()
    qapp.processEvents()

    for dpi_pct, pt_size in ((100, 9), (150, 13), (200, 18)):
        font = QFont("Segoe UI", pt_size)
        w.encoder_combo.setFont(font)
        w.quality_head_label.setFont(font)
        w.quality_tier_lbl.setFont(font)
        w.quality_head_label.updateGeometry()
        w.quality_tier_lbl.updateGeometry()
        w._update_conversion_adaptive_layout()
        qapp.processEvents()

        threshold = w._q_row_threshold
        assert threshold >= 140, f"Valid threshold expected, got {threshold}"

        # Test above threshold: stepper is beside label
        w.conv_scroll_content.setFixedWidth(threshold + 50)
        w._update_conversion_adaptive_layout(threshold + 50)
        qapp.processEvents()
        assert w._q_is_beside
        assert not w.q_sub_widget.isVisible()
        assert w.q_head_row.indexOf(w.quality_stepper) >= 0

        # Test below threshold: stepper wraps to sub_row
        w.conv_scroll_content.setFixedWidth(threshold - 10)
        w._update_conversion_adaptive_layout(threshold - 10)
        qapp.processEvents()
        assert not w._q_is_beside
        assert w.q_sub_widget.isVisible()
        assert w.q_sub_row.indexOf(w.quality_stepper) >= 0

    w.close()


def test_files_card_bottom_row_removed_and_output_folder_relocated(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # 1. Bottom row of buttons removed from Files card
    # files.body contains ONLY files_empty_drop_widget and queue_list
    assert w.files_card.body.count() == 2
    assert w.files_card.body.itemAt(0).widget() is w.files_empty_drop_widget
    assert w.files_card.body.itemAt(1).widget() is w.queue_list
    assert w.queue_remove_btn is None
    assert w.queue_add_btn is w.files_browse_btn

    # 2. Output folder relocated to Output section
    assert w.output_folder_btn.parent() is not None
    assert w.output_folder_btn.text() == "Output folder"
    assert w.output_folder_btn.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu

    # 3. Path selection and persistence
    custom_dir = tmp_path / "custom_sdr_out"
    custom_dir.mkdir()
    w.output_folder = custom_dir
    w._update_output_folder_btn()
    assert str(custom_dir) in w.output_folder_btn.toolTip()
    w._conversion_preferences.save()
    qapp.processEvents()

    # Check persistence in settings
    raw = w.i18n.settings.value("conversion_preferences", "{}")
    values = json.loads(raw)
    assert values.get("output_folder") == str(custom_dir)

    w.close()


def test_adding_files_via_browse_and_drop(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # Initial empty state
    assert w.queue_list.count() == 0
    assert w.files_empty_drop_widget.isVisible()
    assert not w.queue_list.isVisible()

    # Create dummy video files
    f1 = tmp_path / "video_sample1.mp4"
    f1.write_text("dummy video 1")
    f2 = tmp_path / "video_sample2.mkv"
    f2.write_text("dummy video 2")

    # Add f1 via queue addition (same pipeline as browse_source)
    w._add_files_to_queue([str(f1)], trust_extension=True)
    qapp.processEvents()

    assert w.queue_list.count() == 1
    assert not w.files_empty_drop_widget.isVisible()
    assert w.queue_list.isVisible()
    assert w.src_edit.text() == str(f1)

    item0 = w.queue_list.item(0)
    widget0 = w.queue_list.itemWidget(item0)
    assert isinstance(widget0, _QueueItemWidget)
    assert widget0.label.text() == f1.name
    assert widget0.remove_btn.text() == "×"
    assert widget0.remove_btn.toolTip() == "Remove"

    # Add f2 via dropEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f2))])
    drop_ev = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    w.dropEvent(drop_ev)
    qapp.processEvents()

    assert w.queue_list.count() == 2
    assert w.src_edit.text() == str(f1)  # active source preserved

    item1 = w.queue_list.item(1)
    widget1 = w.queue_list.itemWidget(item1)
    assert isinstance(widget1, _QueueItemWidget)
    assert widget1.label.text() == f2.name

    w.close()


def test_removing_files_via_row_button_and_context_menu(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    f1 = tmp_path / "clip1.mp4"
    f2 = tmp_path / "clip2.mp4"
    f3 = tmp_path / "clip3.mp4"
    for f in (f1, f2, f3):
        f.write_text("video")

    w._add_files_to_queue([str(f1), str(f2), str(f3)], trust_extension=True)
    qapp.processEvents()
    assert w.queue_list.count() == 3
    assert w.src_edit.text() == str(f1)

    # 1. Remove f1 via row remove button
    widget0 = w.queue_list.itemWidget(w.queue_list.item(0))
    widget0.remove_btn.click()
    qapp.processEvents()

    assert w.queue_list.count() == 2
    assert w.src_edit.text() == str(f2)  # automatically loaded and analyzed next item

    # 2. Remove f3 via context menu action
    w.queue_list.setCurrentRow(1)  # row 1 is clip3
    w._remove_selected_queue_items()
    qapp.processEvents()

    assert w.queue_list.count() == 1
    assert w.src_edit.text() == str(f2)

    # 3. Remove last file (f2) via keyboard Delete key
    del_ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    handled = w.eventFilter(w.queue_list, del_ev)
    assert handled is True
    qapp.processEvents()

    # Clean state when last file is removed
    assert w.queue_list.count() == 0
    assert w.files_empty_drop_widget.isVisible()
    assert not w.queue_list.isVisible()
    assert w.src_edit.text() == ""
    assert w.dst_edit.text() == ""
    assert w.kind == "unknown"
    assert "Select a video to analyze" in w.analysis_label.text()
    assert "Choose a source video" in w.preview_label.text()

    w.close()


def test_multi_selection_deletion(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    f1 = tmp_path / "batch1.mp4"
    f2 = tmp_path / "batch2.mp4"
    f3 = tmp_path / "batch3.mp4"
    f4 = tmp_path / "batch4.mp4"
    for f in (f1, f2, f3, f4):
        f.write_text("video")

    w._add_files_to_queue([str(f1), str(f2), str(f3), str(f4)], trust_extension=True)
    qapp.processEvents()
    assert w.queue_list.count() == 4

    # Select batch1 and batch3 (multi-selection)
    w.queue_list.clearSelection()
    w.queue_list.item(0).setSelected(True)
    w.queue_list.item(2).setSelected(True)

    # Trigger deletion
    w._remove_selected_queue_items()
    qapp.processEvents()

    assert w.queue_list.count() == 2
    rem1 = w.queue_list.itemWidget(w.queue_list.item(0)).label.text()
    rem2 = w.queue_list.itemWidget(w.queue_list.item(1)).label.text()
    assert rem1 == f2.name
    assert rem2 == f4.name

    w.close()


def test_deleting_files_during_active_conversion_does_not_break_process(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    f1 = tmp_path / "active_run.mp4"
    f2 = tmp_path / "queued_job.mp4"
    f1.write_text("video 1")
    f2.write_text("video 2")

    w._add_files_to_queue([str(f1), str(f2)], trust_extension=True)
    qapp.processEvents()
    assert w.queue_list.count() == 2
    assert w.src_edit.text() == str(f1)

    # Simulate running conversion on f1
    w._is_proc_running = lambda: True
    w._run_context.source = str(f1)

    # User removes queued file f2 while f1 is converting
    widget1 = w.queue_list.itemWidget(w.queue_list.item(1))
    widget1.remove_btn.click()
    qapp.processEvents()

    assert w.queue_list.count() == 1
    assert w.src_edit.text() == str(f1), "Active running conversion source must not be changed"

    # User removes remaining queued item while converting
    widget0 = w.queue_list.itemWidget(w.queue_list.item(0))
    widget0.remove_btn.click()
    qapp.processEvents()

    assert w.queue_list.count() == 0
    assert w.src_edit.text() == str(f1), "Active running conversion source must not be cleared"
    assert "active_run.mp4" in w.files_drop_label.text()

    # Reset running guard before close
    w._is_proc_running = lambda: False
    w.close()
