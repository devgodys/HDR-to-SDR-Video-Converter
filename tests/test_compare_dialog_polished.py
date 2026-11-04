import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QSize, QProcess
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.compare_dialog import TonemapComparisonDialog
from hdr2sdr.ui.tonemap_comparison import TonemapCompareItem, TonemapComparator


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _make_dummy_pixmap(w: int = 120, h: int = 80, color: QColor = Qt.GlobalColor.red) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(color)
    return pm


def test_initial_render_temporary_preview_and_overlay(qapp, tmp_path):
    w = MainWindow()
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    # Simulate source preview frame in main window
    src_pixmap = _make_dummy_pixmap(320, 180, Qt.GlobalColor.blue)
    w._current_preview_pixmap = src_pixmap

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # 1. Overlay badge is visible and correctly positioned
        assert hasattr(dlg, "render_overlay_badge")
        assert dlg.render_overlay_badge.isVisible()
        assert "Rendering…" in dlg.render_overlay_badge.title_lbl.text()
        assert "(#1)" in dlg.render_overlay_badge.title_lbl.text()
        assert "Temporary preview" in dlg.render_overlay_badge.subtitle_lbl.text()

        # 2. Workspace image is NOT empty black - it holds scaled temporary source preview
        lbl_pm = dlg.image_lbl.pixmap()
        assert lbl_pm is not None and not lbl_pm.isNull()

        # 3. Actions must NOT treat the temporary preview as final result
        assert not dlg.fullscreen_btn.isEnabled()
        assert not dlg.save_btn.isEnabled()

        # 4. When variant 0 finishes rendering:
        ready_pixmap = _make_dummy_pixmap(320, 180, Qt.GlobalColor.green)
        dlg.variants[0].status = "ready"
        dlg.variants[0].pixmap = ready_pixmap
        dlg._on_variant_finished(0, dlg.variants[0], True)
        qapp.processEvents()

        # Overlay hidden, real processed image displayed, actions enabled
        assert not dlg.render_overlay_badge.isVisible()
        assert dlg.image_lbl.pixmap() is not None
        assert dlg.fullscreen_btn.isEnabled()
        assert dlg.save_btn.isEnabled()

        # 5. Selecting variant 1 (which is still pending) uses variant 0 as temporary preview
        dlg._select_variant(1)
        qapp.processEvents()

        assert dlg.render_overlay_badge.isVisible()
        assert "(#2)" in dlg.render_overlay_badge.title_lbl.text()
        assert not dlg.fullscreen_btn.isEnabled()
        assert not dlg.save_btn.isEnabled()
        # Pixmap still visible (last ready variant displayed temporarily)
        assert dlg.image_lbl.pixmap() is not None

        dlg.close()
    w.close()


def test_compare_frame_bar_contrast_and_disabled_states(qapp, tmp_path):
    w = MainWindow()
    w.duration = 100.0
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # Neutral background on image container (#09090B)
        dlg_ss = dlg.styleSheet()
        assert "#compareImageContainer { background: #09090B;" in dlg_ss
        assert "#compareFrameBar { background: #18181B;" in dlg_ss

        # Contrast styling for frame section label and position label
        assert "QLabel#compareFrameSectionLbl" in dlg_ss
        assert "QLabel#comparePositionLbl" in dlg_ss
        assert "QLabel#compareFrameSectionLbl:disabled" in dlg_ss
        assert "QLabel#comparePositionLbl:disabled" in dlg_ss

        # Visible labels in frame bar
        assert hasattr(dlg, "frame_section_lbl")
        assert dlg.preview_position_label.isVisible()
        assert dlg.preview_slider.isEnabled()
        assert dlg.frame_section_lbl.isEnabled()
        assert dlg.preview_position_label.isEnabled()

        # When slider is disabled, labels sync to disabled state
        dlg.preview_slider.setEnabled(False)
        dlg._update_position_label()
        assert not dlg.preview_position_label.isEnabled()
        assert not dlg.frame_section_lbl.isEnabled()

        dlg.close()
    w.close()


def test_test_clip_duration_presets_and_custom_toggle(qapp, tmp_path):
    w = MainWindow()
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # Auto-render is off by default; timeline scrub bar sits above actions_card;
        # only the single ScrubSpinBox value chip ("5 s") is visible for duration
        assert not dlg.auto_render_chk.isChecked()
        assert dlg.scrub_bar.isVisible()
        assert dlg.scrub_bar.geometry().bottom() <= dlg.actions_card.geometry().top()
        assert dlg.test_clip_dur_chip.isVisible()
        assert dlg.test_clip_dur_spin.isVisible()
        assert dlg.btn_dur_3s.isHidden()
        assert dlg.btn_dur_5s.isHidden()
        assert dlg.btn_dur_10s.isHidden()
        assert dlg.btn_dur_custom.isHidden()
        assert dlg.test_clip_dur_spin.value() == 5
        assert dlg.test_clip_dur_spin.width() >= 80

        # Click 10s preset: spinbox updates to 10, 10s checked
        dlg.btn_dur_10s.click()
        assert dlg.btn_dur_10s.isChecked()
        assert not dlg.btn_dur_5s.isChecked()
        assert dlg.test_clip_dur_spin.value() == 10

        # Set manual custom duration to 15s directly in spinbox: both 5s and 10s uncheck
        dlg.test_clip_dur_spin.setValue(15)
        assert dlg.test_clip_dur_spin.value() == 15
        assert dlg._custom_duration == 15
        assert not dlg.btn_dur_5s.isChecked()
        assert not dlg.btn_dur_10s.isChecked()

        # Switch back to 5s preset: 5s checked, spinbox shows 5
        dlg.btn_dur_5s.click()
        assert dlg.btn_dur_5s.isChecked()
        assert not dlg.btn_dur_10s.isChecked()
        assert dlg.test_clip_dur_spin.value() == 5

        dlg.close()
    w.close()


def test_test_clip_status_reserved_space_and_lifecycle(qapp, tmp_path):
    w = MainWindow()
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # 1. Reserved vertical space: fixed height 20px avoids layout jumping
        assert dlg.test_clip_status_lbl.height() == 20 or dlg.test_clip_status_lbl.minimumHeight() == 20

        # 2. Idle state before launch displays readiness
        idle_text = dlg.test_clip_status_lbl.text()
        assert "Ready to render test clip" in idle_text
        assert "(5 s)" in idle_text

        # 3. Changing duration updates idle message
        dlg.btn_dur_custom.click()
        dlg.test_clip_dur_spin.setValue(10)
        assert "(10 s)" in dlg.test_clip_status_lbl.text()

        # 4. Cancel message
        dlg._cancel_test_clip()
        assert "cancelled" in dlg.test_clip_status_lbl.text().lower()

        # 5. Error state with detail
        failed_out = tmp_path / "failed_clip.mkv"
        dlg._last_test_clip_error = "Encoder error: unsupported pixel format"
        dlg._on_test_clip_finished(failed_out, dlg.clip_manager.generation)
        assert "unsupported pixel format" in dlg.test_clip_status_lbl.text()
        assert not dlg.test_clip_open_btn.isEnabled()

        # 6. Success state with file size and Open button enabled
        success_out = tmp_path / "test_clip.mkv"
        success_out.write_bytes(b"0" * (2 * 1024 * 1024))
        dlg._on_test_clip_finished(success_out, dlg.clip_manager.generation)
        assert "Test clip ready (2.0 MB)" in dlg.test_clip_status_lbl.text()
        assert dlg.test_clip_open_btn.isEnabled()

        dlg.close()
    w.close()


def test_russian_localization_polish(qapp, tmp_path):
    w = MainWindow()
    w.i18n.set_language("ru")
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # 1. Custom button localized
        assert dlg.btn_dur_custom.text() == "Свой…"

        # 2. Idle status localized
        assert "Готов к рендеру тестового клипа (5 с)" in dlg.test_clip_status_lbl.text()

        # 3. Temporary preview overlay badge localized
        assert "Рендеринг… (#1)" in dlg.render_overlay_badge.title_lbl.text()
        assert "Временный предпросмотр — результат появится по завершении" in dlg.render_overlay_badge.subtitle_lbl.text()

        dlg.close()
    w.i18n.set_language("en")
    w.close()


def test_run_test_clip_does_not_call_wait_for_started(qapp, tmp_path):
    """Verify _run_test_clip does not block the UI via waitForStarted."""
    w = MainWindow()
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        with patch.object(QProcess, "waitForStarted") as mock_wait:
            mock_wait.side_effect = AssertionError("waitForStarted must not be called!")
            with patch.object(QProcess, "start"):
                dlg._run_test_clip()
                qapp.processEvents()

                assert not mock_wait.called
                assert dlg._test_clip_proc is not None
                assert dlg.test_clip_btn.text() == dlg.tr("Cancel clip")

        dlg._cancel_test_clip()
        dlg.close()
    w.close()


def test_test_clip_failed_to_start_restores_buttons_and_shows_error(qapp, tmp_path):
    """Verify FailedToStart frees process and temporary files, restores buttons, and shows localized error."""
    w = MainWindow()
    w.i18n.set_language("ru")
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        with patch.object(QProcess, "start"):
            dlg._run_test_clip()
            qapp.processEvents()

            assert dlg._test_clip_proc is not None
            assert dlg.test_clip_btn.text() == dlg.tr("Cancel clip")
            proc = dlg._test_clip_proc

            # Simulate FailedToStart error
            proc.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
            qapp.processEvents()

            # Process reference cleared
            assert dlg._test_clip_proc is None
            # Buttons restored from Cancel clip to Render test clip (role 'go')
            assert dlg.test_clip_btn.text() == dlg.tr("Render test clip")
            assert dlg.test_clip_btn.property("role") == "go"
            assert not dlg.test_clip_open_btn.isEnabled()
            assert dlg.test_clip_open_btn.property("role") == ""
            # Localized error shown in Russian
            assert dlg.test_clip_status_lbl.text() == "Не удалось запустить FFmpeg"
            assert "#F87171" in dlg.test_clip_status_lbl.styleSheet()

        dlg.close()
    w.i18n.set_language("en")
    w.close()


def test_seek_rewind_starts_new_generation_and_cancel_button_accessible(qapp, tmp_path):
    """Verify:
    1. After all finished, seeking starts new comparison with Cancel button visible & enabled.
    2. After active rendering, seeking starts new comparison and old cancelled signals do not overwrite new status.
    3. Clicking Cancel cancels the active new generation."""
    w = MainWindow()
    w.duration = 100.0
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # --- Case 1: After completion, seek restarts comparison with visible Cancel button ---
        dlg._on_all_finished()
        qapp.processEvents()
        assert dlg.cancel_btn.isHidden()
        assert not dlg.cancel_btn.isEnabled()

        # Enable auto-render so seeking triggers automatic re-render
        dlg.auto_render_chk.setChecked(True)
        # Rewind to new position
        dlg.preview_slider.setValue(25)
        dlg._on_seek_debounce_timeout()
        qapp.processEvents()

        # Cancel button must now be visible and enabled
        assert dlg.cancel_btn.isVisible()
        assert dlg.cancel_btn.isEnabled()
        assert "Rendering 1 of" in dlg.status_lbl.text()

        # Cancel button cancels this new run
        dlg.cancel_btn.click()
        qapp.processEvents()
        assert dlg.cancel_btn.isHidden()
        assert not dlg.cancel_btn.isEnabled()
        assert "cancelled" in dlg.status_lbl.text().lower()

        # --- Case 2: During active render, seek restarts comparison without old signals overwriting new status ---
        dlg.preview_slider.setValue(50)
        dlg._on_seek_debounce_timeout()
        qapp.processEvents()

        assert dlg.cancel_btn.isVisible()
        assert dlg.cancel_btn.isEnabled()
        new_gen = dlg._expected_comparator_gen

        # Simulate an old comparator emitting all_finished from a previous generation
        old_comp = TonemapComparator(
            ffmpeg_exe="ffmpeg",
            src_path=str(dummy_src),
            seek=10.0,
            base_settings=dlg.base_settings,
            variants=dlg.variants,
        )
        old_comp._gen = 999  # Stale generation

        # Stale all_finished must NOT hide the new Cancel button or overwrite status
        with patch.object(dlg, "comparator", old_comp):
            dlg._on_all_finished()
            qapp.processEvents()

        assert dlg.cancel_btn.isVisible()
        assert dlg.cancel_btn.isEnabled()
        assert "cancelled" not in dlg.status_lbl.text().lower()

        dlg.close()
    w.close()


def test_cancel_clicked_shows_exact_ready_count_and_cancelled_status(qapp, tmp_path):
    """Verify:
    - after Cancel, status shows exact ready count and 'cancelled';
    - progress bar matches ready count;
    - Cancel button is disabled and hidden;
    - signals from cancelled generation do not overwrite this final status."""
    w = MainWindow()
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("sample")
    w.src_edit.setText(str(dummy_src))

    with patch.object(TonemapComparator, "start"):
        dlg = TonemapComparisonDialog(w)
        dlg.show()
        qapp.processEvents()

        # Mark 3 variants as ready
        dlg.variants[0].status = "ready"
        dlg.variants[0].pixmap = _make_dummy_pixmap(100, 100)
        dlg.variants[1].status = "ready"
        dlg.variants[1].pixmap = _make_dummy_pixmap(100, 100)
        dlg.variants[2].status = "ready"
        dlg.variants[2].pixmap = _make_dummy_pixmap(100, 100)
        # Mark 4th as rendering, rest pending
        dlg.variants[3].status = "rendering"
        for v in dlg.variants[4:]:
            v.status = "pending"

        # Simulate in-progress rendering state with active Cancel button
        dlg.cancel_btn.setEnabled(True)
        dlg.cancel_btn.show()

        # Click Cancel
        dlg.cancel_btn.click()
        qapp.processEvents()

        # Progress matches exactly 3 ready variants
        assert dlg.progress_bar.value() == 3

        # Status text shows exact count and 'cancelled'
        total_count = len(dlg.variants)
        expected_status = dlg.tr("{ready} of {total} variants ready (cancelled)", ready=3, total=total_count)
        assert dlg.status_lbl.text() == expected_status

        # Cancel button is hidden and disabled
        assert dlg.cancel_btn.isHidden()
        assert not dlg.cancel_btn.isEnabled()

        # The rendering item was cancelled
        assert dlg.variants[3].status == "cancelled"

        # Stale signals arriving from cancelled generation must NOT change the final status
        stale_item = dlg.variants[3]
        dlg._on_variant_started(3, stale_item)
        dlg._on_variant_finished(3, stale_item, False)
        dlg._on_all_finished()
        qapp.processEvents()

        assert dlg.status_lbl.text() == expected_status
        assert dlg.progress_bar.value() == 3
        assert dlg.cancel_btn.isHidden()

        dlg.close()
    w.close()


def test_main_window_live_preview_compact_layout_and_scaling(qapp, tmp_path):
    """Verify:
    - Live preview card layout has tightened margins and spacing (no bloated gaps);
    - Gaps between title and frame, frame and meta label, and meta label and slider row are reduced;
    - Timeline controls widget height is compact (28px);
    - Layout remains intact and unclipped across DPI scales (100% to 200%)."""
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # 1. Inspect layout metrics
    card_layout = w.preview_card.layout()
    body_layout = w.preview_card.body

    # Outer card margins and spacing tightened with bottom room for scrub slider handle
    margins = card_layout.contentsMargins()
    assert margins.top() <= 10
    assert 10 <= margins.bottom() <= 16
    assert card_layout.spacing() <= 4

    # Body spacing between image, meta label, and controls tightened
    assert body_layout.spacing() <= 2

    # Timeline controls row has room so the 22px round slider handle never clips
    assert w.preview_controls_widget.height() <= 34
    assert w.preview_slider.property("role") == "compact-slider"
    ctrl_margins = w.preview_controls_widget.layout().contentsMargins()
    assert ctrl_margins.top() == 0
    assert ctrl_margins.bottom() >= 2

    # 2. DPI scaling check (100% - 200%)
    f = qapp.font()
    orig_point_size = f.pointSize()
    try:
        for scale in (1.0, 1.25, 1.5, 2.0):
            f.setPointSize(int(orig_point_size * scale))
            qapp.setFont(f)
            w.resize(1080, 720)
            qapp.processEvents()

            assert w.preview_label.minimumHeight() >= 200
            assert w.preview_controls_widget.height() <= 36
            assert not w.preview_controls_widget.isVisible()
            assert not w.frame_strip.isVisible()
    finally:
        f.setPointSize(orig_point_size)
        qapp.setFont(f)
        w.close()

