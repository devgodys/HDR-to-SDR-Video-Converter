from __future__ import annotations

import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer, QPoint
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from hdr2sdr.core.commands import build_vf_chain, ConversionSettings
from hdr2sdr.core.constants import GAMUT_MAPPING_LABELS
from hdr2sdr.ui.preview_cache import PreviewCache, compute_preview_cache_key
from hdr2sdr.ui.tonemap_comparison import (
    TonemapCompareItem,
    TonemapComparator,
    build_comparison_variants,
)
from hdr2sdr.ui.compare_dialog import TonemapComparisonDialog, ElidedLabel
from hdr2sdr.ui.main_window import MainWindow


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_build_comparison_variants_gpu_and_cpu():
    """Verify building comparative variants:
    - GPU methods get available gamut modes (Perceptual, Relative, Clip).
    - CPU methods get gamut="N/A" (no unsupported combinations).
    - DV Profile 5 excludes CPU methods."""
    variants = build_comparison_variants(has_bt2390=True, has_st2094=False, dv_profile=None)
    assert len(variants) > 10

    gpu_variants = [v for v in variants if v.engine == "gpu"]
    cpu_variants = [v for v in variants if v.engine == "cpu"]
    assert len(gpu_variants) > 0
    assert len(cpu_variants) > 0

    for v in gpu_variants:
        assert v.gamut in GAMUT_MAPPING_LABELS
        assert "GPU" in v.method

    for v in cpu_variants:
        assert v.gamut == "N/A"
        assert "CPU" in v.method

    cpu_only = build_comparison_variants(has_bt2390=False, has_st2094=False, dv_profile=None)
    assert all(v.engine == "cpu" for v in cpu_only)
    assert all(v.gamut == "N/A" for v in cpu_only)
    assert len(cpu_only) == len(cpu_variants)

    dv5_variants = build_comparison_variants(has_bt2390=True, has_st2094=False, dv_profile=5)
    assert all(v.engine == "gpu" for v in dv5_variants)
    assert len(dv5_variants) == len(gpu_variants)


def test_comparison_shared_settings_and_seek_position(tmp_path):
    """Verify all comparative variants retain identical position, filters,
    and shared processing settings from the user's configuration."""
    dummy_src = tmp_path / "sample.mkv"
    dummy_src.write_text("dummy")

    base = ConversionSettings(
        src=str(dummy_src),
        dst=str(tmp_path / "out.mp4"),
        brightness=8,
        saturation=-4,
        colortemperature=5500,
        deband=True,
        resharpen=True,
        denoise="Light",
        dv_rpu=True,
        peak_detect=False,
    )

    variants = build_comparison_variants(has_bt2390=True, has_st2094=False)
    cache = PreviewCache(cache_dir=tmp_path / "cache")
    comparator = TonemapComparator(
        ffmpeg_exe="ffmpeg",
        src_path=str(dummy_src),
        seek=42.5,
        base_settings=base,
        variants=variants,
        cache=cache,
    )

    assert comparator.seek == 42.5
    assert comparator.base_settings.brightness == 8
    assert comparator.base_settings.saturation == -4
    assert comparator.base_settings.colortemperature == 5500
    assert comparator.base_settings.deband is True
    assert comparator.base_settings.resharpen is True
    assert comparator.base_settings.denoise == "Light"

    for v in variants[:3]:
        v_gamut = v.gamut if v.engine == "gpu" else "Perceptual"
        v_settings = replace(base, tonemap_method=v.method, gamut_mode=v_gamut)
        vf = build_vf_chain(v_settings)
        assert "hqdn3d" in vf
        assert "deband=" in vf
        assert "unsharp=" in vf
        assert "eq=brightness=" in vf


def test_preview_cache_key_file_modification_and_params(tmp_path):
    """Verify cache key formation:
    - Normalizes path and incorporates size and mtime_ns without full content scans.
    - Key changes when file is modified.
    - Key changes when preview-affecting params change.
    - Key is unaffected by encode-only parameters (e.g. encoder, quality, dst)."""
    dummy_src = tmp_path / "clip.mp4"
    dummy_src.write_text("initial video stream")

    s_base = {
        "tonemap_method": "BT.2390 · GPU tonemap",
        "gamut_mode": "Perceptual",
        "brightness": 0,
        "quality": 22,
        "encoder_label": "libx265",
        "dst": "C:/out.mp4",
    }

    k1 = compute_preview_cache_key(dummy_src, 10.0, s_base)
    time.sleep(0.02)

    # Modify file content and size
    dummy_src.write_text("modified longer video stream payload with new size")
    k2 = compute_preview_cache_key(dummy_src, 10.0, s_base)
    assert k1 != k2, "Key must change when source file size / mtime_ns changes"

    # Encode-only parameter changes should NOT change the preview cache key
    s_encode_change = {
        "tonemap_method": "BT.2390 · GPU tonemap",
        "gamut_mode": "Perceptual",
        "brightness": 0,
        "quality": 35,
        "encoder_label": "SVT-AV1",
        "dst": "D:/other_out.mkv",
    }
    k3 = compute_preview_cache_key(dummy_src, 10.0, s_encode_change)
    assert k2 == k3, "Key must NOT change when non-preview parameters change"

    # Image-affecting parameter changes MUST change the key
    s_img_change = dict(s_base, brightness=5)
    k4 = compute_preview_cache_key(dummy_src, 10.0, s_img_change)
    assert k3 != k4, "Key must change when brightness changes"


def test_preview_cache_memory_lru_and_disk_prune(qapp, tmp_path):
    """Verify PreviewCache memory LRU eviction and bounded disk cache pruning."""
    cache_dir = tmp_path / "bounded_cache"
    cache = PreviewCache(
        cache_dir=cache_dir,
        max_memory_items=2,
        max_disk_items=3,
    )

    img = QImage(64, 64, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.green)
    pix = QPixmap.fromImage(img)

    for i in range(5):
        k = f"pv_test_{i}"
        cache.put(k, pix)

    # Memory cache must not exceed max_memory_items (2)
    assert len(cache._memory_cache) <= 2
    # Disk cache must not exceed max_disk_items (3)
    files = list(cache_dir.glob("pv_*.png"))
    assert len(files) <= 3

    # Clear empties both
    cache.clear()
    assert len(cache._memory_cache) == 0
    assert len(list(cache_dir.glob("pv_*.png"))) == 0


def test_comparator_async_failed_to_start_handling(qapp, tmp_path):
    """Verify asynchronous FFmpeg execution handles FailedToStart without blocking GUI,
    marks variant as error with error_code='failed_to_start', and advances to subsequent variants."""
    dummy_src = tmp_path / "dummy.mp4"
    dummy_src.write_text("data")

    base = ConversionSettings(src=str(dummy_src), dst=str(tmp_path / "out.mp4"))
    variants = [
        TonemapCompareItem(method="BT.2390 · GPU tonemap", gamut="Perceptual", engine="gpu"),
        TonemapCompareItem(method="Hable · CPU tonemap", gamut="N/A", engine="cpu"),
    ]
    cache = PreviewCache(cache_dir=tmp_path / "cache")

    # Non-existent binary path to trigger FailedToStart
    comparator = TonemapComparator(
        ffmpeg_exe=str(tmp_path / "nonexistent_binary.exe"),
        src_path=str(dummy_src),
        seek=1.0,
        base_settings=base,
        variants=variants,
        cache=cache,
    )

    loop = QEventLoop()
    comparator.all_finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)  # watchdog timeout

    comparator.start()
    loop.exec()

    # Both variants should cleanly fail with error_code='failed_to_start' without blocking
    assert variants[0].status == "error"
    assert variants[0].error_code == "failed_to_start"
    assert "Failed to start" in variants[0].error_message
    assert variants[1].status == "error"
    assert variants[1].error_code == "failed_to_start"
    assert "Failed to start" in variants[1].error_message


def test_comparator_cancel_and_progress_display(qapp, tmp_path):
    """Verify cancellation terminates active process, marks pending items cancelled,
    and reflects accurate ready count rather than 100%."""
    dummy_src = tmp_path / "dummy.mp4"
    dummy_src.write_text("data")

    base = ConversionSettings(src=str(dummy_src), dst=str(tmp_path / "out.mp4"))
    variants = [
        TonemapCompareItem(method="BT.2390 · GPU tonemap", gamut="Perceptual", engine="gpu"),
        TonemapCompareItem(method="Hable · CPU tonemap", gamut="N/A", engine="cpu"),
        TonemapCompareItem(method="Mobius · CPU tonemap", gamut="N/A", engine="cpu"),
    ]
    # Mark first item pre-cached and ready
    variants[0].status = "ready"
    variants[0].pixmap = QPixmap(10, 10)

    cache = PreviewCache(cache_dir=tmp_path / "cache")
    comparator = TonemapComparator(
        ffmpeg_exe="ffmpeg",
        src_path=str(dummy_src),
        seek=5.0,
        base_settings=base,
        variants=variants,
        cache=cache,
    )

    comparator.cancel()
    assert comparator._cancelled is True
    assert comparator.active_proc is None
    assert variants[1].status == "cancelled"
    assert variants[2].status == "cancelled"


def test_compare_final_status_all_ready_errors_and_cancelled(qapp, tmp_path):
    """Verify TonemapComparisonDialog._on_all_finished():
    - All ready: shows 'All variants ready', progress bar is 100%.
    - Some errors: shows 'Ready: {ready} of {total} · Errors: {errors}', progress bar is ready_count.
    - Cancelled: shows '{ready} of {total} variants ready (cancelled)', progress bar is ready_count."""
    w = MainWindow()
    w.show()
    qapp.processEvents()

    src = tmp_path / "sample.mp4"
    src.write_text("data")
    w._set_source(str(src), analyze=False)
    qapp.processEvents()

    dlg = TonemapComparisonDialog(w)
    dlg.comparator.cancel()  # Stop background execution for deterministic unit test
    qapp.processEvents()

    total = len(dlg.variants)
    assert total >= 3

    # Case 1: All ready
    dlg.comparator._cancelled = False
    for v in dlg.variants:
        v.status = "ready"
    dlg._on_all_finished()
    assert dlg.status_lbl.text() == w.tr("All variants ready")
    assert dlg.progress_bar.value() == total

    # Case 2: Finished with errors (e.g. 2 errors, rest ready)
    dlg.comparator._cancelled = False
    for v in dlg.variants:
        v.status = "ready"
    dlg.variants[0].status = "error"
    dlg.variants[0].error_code = "failed_to_start"
    dlg.variants[1].status = "error"
    dlg.variants[1].error_code = "process_failed"
    dlg.variants[1].exit_code = 1
    ready_count = total - 2

    dlg._on_all_finished()
    expected_err_text = w.tr(
        "Ready: {ready} of {total} · Errors: {errors}",
        ready=ready_count,
        total=total,
        errors=2,
    )
    assert dlg.status_lbl.text() == expected_err_text
    assert dlg.status_lbl.text() != w.tr("All variants ready")
    # Progress bar reflects successfully completed variants, NOT 100%
    assert dlg.progress_bar.value() == ready_count

    # Case 3: Cancelled
    dlg.comparator._cancelled = True
    for v in dlg.variants:
        v.status = "pending"
    dlg.variants[0].status = "ready"
    dlg.variants[1].status = "cancelled"
    dlg.variants[2].status = "cancelled"

    dlg._on_all_finished()
    expected_cancel_text = w.tr(
        "{ready} of {total} variants ready (cancelled)",
        ready=1,
        total=total,
    )
    assert dlg.status_lbl.text() == expected_cancel_text
    assert dlg.progress_bar.value() == 1

    dlg.reject()
    w.close()


def test_error_codes_and_localization(qapp, tmp_path):
    """Verify localized error formatting in compare dialog for:
    - Failed to start FFmpeg
    - Corrupt or unreadable preview frame
    - Process failed with exit code and technical stderr detail
    - Verification in English and Russian."""
    w = MainWindow()
    w.show()
    qapp.processEvents()

    src = tmp_path / "sample.mp4"
    src.write_text("data")
    w._set_source(str(src), analyze=False)
    qapp.processEvents()

    dlg = TonemapComparisonDialog(w)
    dlg.comparator.cancel()
    qapp.processEvents()

    # 1. English tests
    item_start_err = TonemapCompareItem(
        method="BT.2390", gamut="Perceptual", engine="gpu",
        status="error", error_code="failed_to_start",
    )
    assert dlg._format_item_error(item_start_err) == "Failed to start FFmpeg"

    item_corrupt = TonemapCompareItem(
        method="BT.2390", gamut="Perceptual", engine="gpu",
        status="error", error_code="corrupt_image",
    )
    assert dlg._format_item_error(item_corrupt) == "Corrupt or unreadable preview frame"

    item_exit = TonemapCompareItem(
        method="BT.2390", gamut="Perceptual", engine="gpu",
        status="error", error_code="process_failed", exit_code=18,
    )
    assert dlg._format_item_error(item_exit) == "FFmpeg failed with exit code 18"

    item_stderr = TonemapCompareItem(
        method="BT.2390", gamut="Perceptual", engine="gpu",
        status="error", error_code="process_failed", exit_code=1,
        error_detail="No such filter: 'libplacebo'",
    )
    assert dlg._format_item_error(item_stderr) == "FFmpeg failed with exit code 1: No such filter: 'libplacebo'"

    # 2. Russian localization verification
    orig_lang = w.i18n.language
    w.i18n.set_language("ru")
    try:
        assert dlg._format_item_error(item_start_err) == "Не удалось запустить FFmpeg"
        assert dlg._format_item_error(item_corrupt) == "Повреждённый или нечитаемый кадр превью"
        assert dlg._format_item_error(item_exit) == "Сбой FFmpeg с кодом завершения 18"
        assert dlg._format_item_error(item_stderr) == "Сбой FFmpeg с кодом завершения 1: No such filter: 'libplacebo'"

        # Status text in Russian
        summary_ru = dlg.tr(
            "Ready: {ready} of {total} · Errors: {errors}",
            ready=15,
            total=18,
            errors=3,
        )
        assert summary_ru == "Готово: 15 из 18 · Ошибок: 3"
    finally:
        w.i18n.set_language(orig_lang)

    dlg.reject()
    w.close()


def test_compare_dialog_header_labels_have_no_borders_or_frame_leak(qapp, tmp_path):
    """Verify targeted UI requirement 1:
    - Header card labels (active_variant_title, active_variant_meta, disclaimer_lbl)
      have explicit style without borders or background.
    - Header card uses an ID-scoped selector (#compareHeaderCard) so QFrame stylesheet
      does not leak rounded borders onto child QLabel widgets."""
    w = MainWindow()
    w.show()
    qapp.processEvents()

    src = tmp_path / "sample.mp4"
    src.write_text("data")
    w._set_source(str(src), analyze=False)
    qapp.processEvents()

    dlg = TonemapComparisonDialog(w)
    dlg.comparator.cancel()
    dlg.show()
    qapp.processEvents()

    # Header card uses ID-scoped objectName
    assert dlg.header_card.objectName() == "compareHeaderCard"

    # All three labels have explicit no-border / transparent styles
    title_ss = dlg.active_variant_title.styleSheet()
    assert "border: none" in title_ss
    assert "background: transparent" in title_ss

    meta_ss = dlg.active_variant_meta.styleSheet()
    assert "border: none" in meta_ss
    assert "background: transparent" in meta_ss

    disclaimer_ss = dlg.disclaimer_lbl.styleSheet()
    assert "border: none" in disclaimer_ss
    assert "background: transparent" in disclaimer_ss

    # Header card style sheet scopes QFrame to ID and sets child QLabels to border: none
    card_ss = dlg.header_card.styleSheet()
    assert "#compareHeaderCard" in card_ss
    assert "border: none" in card_ss

    dlg.reject()
    w.close()


def test_compare_dialog_list_no_horizontal_scrollbar_and_eliding(qapp, tmp_path):
    """Verify targeted UI requirements 2 and 3:
    - Variants list has horizontal scrollbar disabled (ScrollBarAlwaysOff) and invisible.
    - Long variant names are elided with ellipsis without pushing GPU/CPU badge, current tag,
      or status label off-screen or out of the viewport.
    - Gamut mapping stays clean on the second line."""
    w = MainWindow()
    w.show()
    qapp.processEvents()

    src = tmp_path / "sample.mp4"
    src.write_text("data")
    w._set_source(str(src), analyze=False)
    qapp.processEvents()

    dlg = TonemapComparisonDialog(w)
    dlg.comparator.cancel()
    dlg.resize(920, 600)
    dlg.show()
    qapp.processEvents()

    # Horizontal scrollbar policy must be ScrollBarAlwaysOff and not visible
    assert dlg.list_widget.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert dlg.list_widget.horizontalScrollBar().isVisible() is False
    assert dlg.list_widget.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded

    # Add a variant with an unusually long name to verify eliding behaviour
    long_item = TonemapCompareItem(
        method="ST2094-40 (ST 2094-40 / Annex B / Extended Dynamic Range Curve) · GPU tonemap",
        gamut="Perceptual",
        engine="gpu",
        status="rendering",
    )
    dlg.variants.append(long_item)
    dlg._populate_list()
    qapp.processEvents()

    # Horizontal scrollbar must remain strictly invisible
    assert dlg.list_widget.horizontalScrollBar().isVisible() is False

    last_item = dlg.list_widget.item(dlg.list_widget.count() - 1)
    last_widget = dlg.list_widget.itemWidget(last_item)
    assert isinstance(last_widget.name_lbl, ElidedLabel)

    # Name is elided with ellipsis, status is right-aligned and within widget bounds
    assert last_widget.status_lbl.x() > last_widget.name_lbl.x()
    assert last_widget.status_lbl.x() + last_widget.status_lbl.width() <= last_widget.width()
    assert last_widget.gamut_lbl.y() > last_widget.name_lbl.y()

    dlg.reject()
    w.close()


def test_compare_dialog_bottom_bar_buttons_accessible_at_minimum_size(qapp, tmp_path):
    """Verify targeted UI requirement 4:
    - At minimum dialog size (920x600) and across system DPI scales (100% to 200%),
      all bottom action buttons (Cancel, Save frame…, Apply to conversion, Close)
      remain visible, unclipped, and fully accessible within the dialog window.
    - Progress bar compresses dynamically rather than holding a rigid fixed width."""
    w = MainWindow()
    w.show()
    qapp.processEvents()

    src = tmp_path / "sample.mp4"
    src.write_text("data")
    w._set_source(str(src), analyze=False)
    qapp.processEvents()

    f = qapp.font()
    orig_point_size = f.pointSize()

    try:
        for scale in (1.0, 1.25, 1.5, 1.75, 2.0):
            f.setPointSize(int(orig_point_size * scale))
            qapp.setFont(f)

            dlg = TonemapComparisonDialog(w)
            dlg.comparator.cancel()
            dlg.cancel_btn.show()
            dlg.cancel_btn.setEnabled(True)
            dlg.resize(920, 600)
            dlg.show()
            qapp.processEvents()

            dlg_w = dlg.width()
            # Action buttons must not exceed dialog width
            for btn_name in ("cancel_btn", "save_btn", "apply_btn"):
                btn = getattr(dlg, btn_name)
                assert btn.isVisible(), f"{btn_name} should be visible at scale {scale}"
                pos = btn.mapTo(dlg, QPoint(0, 0))
                assert pos.x() >= 0
                assert pos.x() + btn.width() <= dlg_w + 1, (
                    f"At DPI scale {scale*100}%, {btn_name} right edge ({pos.x() + btn.width()}) "
                    f"exceeded dialog width ({dlg_w})"
                )

            # Progress bar width must compress below its max (140) when space is tight
            assert dlg.progress_bar.width() <= 140

            dlg.reject()
    finally:
        f.setPointSize(orig_point_size)
        qapp.setFont(f)
        w.close()
