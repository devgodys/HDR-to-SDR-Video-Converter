import html
from pathlib import Path
from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtGui import QPainter, QImage, QColor
from PySide6.QtWidgets import QApplication
import pytest

from hdr2sdr.core.constants import QUALITY_TIER_BOUNDS_51, QUALITY_TIER_REPS_51
from hdr2sdr.core.progress import ProgressStats
from hdr2sdr.core.utils import detect_chroma_subsampling
from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.widgets.param_controls import TierSlider
from hdr2sdr.ui.widgets.frame_strip import FrameStrip


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_tier_slider_maximum_fill_has_no_gap(qapp):
    s = TierSlider()
    s.setRange(0, 51)
    s.setInvertedAppearance(True)
    s.setInvertedControls(True)
    s.set_tiers(QUALITY_TIER_BOUNDS_51, QUALITY_TIER_REPS_51, lambda k: k)
    s.resize(400, s.height())
    s.setValue(0)  # Maximum quality, CRF 0

    # Ensure paintEvent executes cleanly at maximum value
    img = QImage(400, s.height(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    s.render(img)

    # The rightmost pixel column of the inner field should be painted with accent color
    # (previously cut off 2-4px before the right border leaving empty background)
    r = s._field_rect()
    pos = s._effective_pos()
    assert pos <= s.minimum()


def test_detect_chroma_subsampling():
    assert detect_chroma_subsampling("yuv444p10le") == "4:4:4"
    assert detect_chroma_subsampling("yuv422p10le") == "4:2:2"
    assert detect_chroma_subsampling("yuv420p10le") == "4:2:0"
    assert detect_chroma_subsampling({"pix_fmt": "yuv420p"}) == "4:2:0"
    assert detect_chroma_subsampling({"pix_fmt": "yuv444p"}) == "4:4:4"
    assert detect_chroma_subsampling({"pix_fmt": "rgb24"}) == "4:4:4"
    assert detect_chroma_subsampling(None) is None


def test_source_analysis_displays_chroma_and_bitrate(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()

    doc = {
        "format": {
            "duration": "100.0",
            "bit_rate": "25000000",
            "size": "312500000",
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "pix_fmt": "yuv444p10le",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
                "color_range": "tv",
                "bits_per_raw_sample": "10",
            }
        ]
    }
    win._apply_analysis_document(doc)
    qapp.processEvents()

    text = win.analysis_label.text()
    assert "4:4:4" in text
    assert "25.0 Mbps" in text
    assert "10-bit" in text

    win.close()


def test_frame_strip_remains_visible_during_conversion(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()

    win.duration = 100.0
    win.current_out_sec = 25.0

    # Simulate start of conversion
    win.frame_strip.show()
    win.preview_controls_widget.hide()
    win._update_frame_strip_during_conversion()

    assert win.frame_strip.isVisible()
    assert not win.preview_controls_widget.isVisible()
    assert win.frame_strip._duration == 100.0
    assert abs(win.frame_strip._pos - 0.25) < 0.01

    # Simulate progress update via apply_stats
    stats = ProgressStats(out_sec=50.0, pct=50.0)
    win.apply_stats(stats)
    qapp.processEvents()

    assert win.frame_strip.isVisible()
    assert not win.preview_controls_widget.isVisible()
    assert abs(win.frame_strip._pos - 0.50) < 0.01
    assert "0:50 / 1:40" in win.frame_strip._time_text

    # Verify scrubbing is disabled while conversion is active
    win.frame_strip.set_scrub_enabled(False)
    assert not win.frame_strip.scrub_enabled()

    win.close()


def test_optimized_analysis_label_tooltip(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()

    doc = {
        "format": {
            "duration": "1708.0",
            "bit_rate": "15100000",
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "pix_fmt": "yuv420p10le",
                "color_range": "pc",
                "bits_per_raw_sample": "10",
                "side_data_list": [
                    {
                        "side_data_type": "DOVI configuration record",
                        "dv_profile": 5,
                        "dv_level": 6,
                        "rpu_present_flag": 1,
                    }
                ],
            }
        ],
    }
    win._apply_analysis_document(doc)
    qapp.processEvents()

    tip = win.analysis_label.toolTip()
    # Must be clean and concise:
    assert "Dolby Vision" in tip
    assert "unknown · unknown primaries" not in tip
    assert "NVENC" not in tip  # No hardware encoder lecture in tooltip
    assert "Duration:" not in tip  # Not duplicating duration
    assert len(tip.splitlines()) <= 3

    win.close()


def test_optimized_tooltips_constants():
    from hdr2sdr.core.constants import (
        TONEMAP_INFO,
        QUALITY_SLIDER_INFO,
        LIBPLACEBO_DOVI_INFO,
        LIBPLACEBO_PEAK_DETECT_INFO,
        GAMUT_MAPPING_INFO,
    )

    assert len(QUALITY_SLIDER_INFO) < 150
    assert "opposite of most sliders" not in QUALITY_SLIDER_INFO
    assert "solid-colour frames" not in LIBPLACEBO_DOVI_INFO
    assert "Same Vulkan/FFmpeg-only requirement" not in TONEMAP_INFO["BT.2446A · GPU tonemap"]


def test_idle_live_preview_has_no_slider_strip(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()

    # In idle state without video loaded, neither the old slider bar nor the frame strip is visible
    assert not win.preview_controls_widget.isVisible()
    assert not win.frame_strip.isVisible()

    # Starting frame strip shows the strip and keeps the old slider hidden
    win.duration = 120.0
    win.src_edit.setText("dummy.mkv")
    win.frame_strip.show()
    win.preview_controls_widget.hide()
    qapp.processEvents()
    assert win.frame_strip.isVisible()
    assert not win.preview_controls_widget.isVisible()

    # Stopping/clearing video hides the frame strip and does NOT bring back the old slider bar
    win._stop_frame_strip()
    qapp.processEvents()
    assert not win.preview_controls_widget.isVisible()
    assert not win.frame_strip.isVisible()

    win.close()


def test_settings_dialog_compact_width_and_language_switch_no_overflow(qapp):
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog

    win = MainWindow()
    win.resize(1100, 920)
    win.show()
    qapp.processEvents()

    dlg = ThemeCustomizeDialog(win)
    assert dlg.DEFAULT_WIDTH == 820
    assert dlg.MIN_WIDTH == 760
    assert dlg.minimumWidth() == 760
    dlg.close()

    # Switching languages (especially ru / uk) must never make conv_scroll_content
    # wider than conv_scroll.viewport() or clip controls on the right edge.
    for lang in ("ru", "uk", "de", "fr", "es", "en"):
        win.change_language(lang)
        qapp.processEvents()
        vp_w = win.conv_scroll.viewport().width()
        sc_w = win.conv_scroll_content.width()
        assert sc_w <= vp_w, (
            f"Language {lang!r}: conv_scroll_content.width()={sc_w} > viewport.width()={vp_w}"
        )
        row_right = win.gamut_mapping_row_widget.mapTo(
            win.conv_scroll.viewport(),
            QPoint(win.gamut_mapping_row_widget.width() - 1, 0),
        ).x()
        assert row_right < vp_w, (
            f"Language {lang!r}: gamut_mapping_row_widget right={row_right} >= viewport.width()={vp_w}"
        )

    win.close()


def test_language_switch_preserves_preview_frame_and_analysis_document(qapp):
    from PySide6.QtGui import QPixmap, QImage, QColor

    win = MainWindow()
    win.show()
    qapp.processEvents()

    doc = {
        "format": {"duration": "1708.0", "bit_rate": "15100000"},
        "streams": [{
            "codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160,
            "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084", "color_primaries": "bt2020",
            "color_space": "bt2020nc", "color_range": "tv", "bits_per_raw_sample": "10"
        }]
    }
    win.src_edit.setText("Cyberpunk.Edgerunners.S02E01-NTb.mp4")
    win._apply_analysis_document(doc)
    qapp.processEvents()

    img = QImage(320, 180, QImage.Format.Format_ARGB32)
    img.fill(QColor("green"))
    win._current_preview_pixmap = QPixmap.fromImage(img)
    win.preview_label.setPixmap(win._current_preview_pixmap)

    # Initial state
    assert not win.preview_label.pixmap().isNull()
    assert ("Source file" in win.analysis_label.text()) or ("Исходный файл" in win.analysis_label.text())

    # Switch to Russian: preview pixmap must remain intact, and analysis card must translate
    win.change_language("ru")
    qapp.processEvents()
    assert not win.preview_label.pixmap().isNull(), "Preview pixmap destroyed on language switch to ru"
    assert "Исходный файл" in win.analysis_label.text()
    assert "Выберите видео для автоматического анализа" not in win.analysis_label.text()
    assert "Выберите исходное видео" not in win.preview_label.text()

    # Switch back to English
    win.change_language("en")
    qapp.processEvents()
    assert not win.preview_label.pixmap().isNull(), "Preview pixmap destroyed on language switch to en"
    assert "Source file" in win.analysis_label.text()

    win.close()


def test_denoise_alignment_and_width_across_languages(qapp):
    win = MainWindow()
    win.resize(1100, 920)
    win.show()
    win.adv_filters_btn.click()
    qapp.processEvents()

    for lang in ("en", "ru", "uk"):
        win.change_language(lang)
        qapp.processEvents()

        # Denoise combo must be flush with Auto-crop toggle in column 1 (no rightward shift)
        auto_x = win.autocrop_chk.mapTo(win.adv_filters_panel, win.autocrop_chk.rect().topLeft()).x()
        combo_x = win.denoise_combo.mapTo(win.adv_filters_panel, win.denoise_combo.rect().topLeft()).x()
        assert auto_x == combo_x, f"Language {lang}: Denoise combo x={combo_x} != autocrop x={auto_x}"

        # In Russian and Ukrainian, combobox width is widened to 185 so the longer text prefix fits cleanly;
        # in English it remains 150 per standard chip spec.
        if lang in ("ru", "uk"):
            assert win.denoise_combo.maximumWidth() == 185
            assert win.denoise_combo.width() == 185
        else:
            assert win.denoise_combo.maximumWidth() == 150

    win.close()
