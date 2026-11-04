import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_ui_redesign_session3_requirements(qapp, tmp_path):
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # 1. Hardware decode relocated under Quality
    enc_body_w = w._section_bodies["encoding"]
    enc_layout = enc_body_w.layout()
    assert enc_layout.indexOf(w.hwaccel_chk) >= 0
    assert enc_layout.indexOf(w.q_box) >= 0
    assert enc_layout.indexOf(w.hwaccel_chk) > enc_layout.indexOf(w.q_box), (
        "Hardware-accelerated decode must be positioned under Quality"
    )

    # 2. Quality tier text (e.g. 'Maximum') hidden
    assert not w.quality_tier_lbl.isVisible(), "quality_tier_lbl must be hidden"

    # 3. CRF/CQ combobox hidden
    assert not w.quality_mode_combo.isVisible(), "quality_mode_combo must be hidden"

    # 4. Denoise combobox lengthened
    assert w.denoise_combo.maximumWidth() == 150, "denoise_combo must have lengthened width"
    assert w.denoise_combo.minimumWidth() >= 120

    # 5. Source analysis displays video bitrate
    dummy_video = tmp_path / "hdr_source.mp4"
    dummy_video.write_text("dummy")
    w.src_edit.setText(str(dummy_video))

    doc = {
        "format": {
            "duration": "120.0",
            "bit_rate": "18400000",
            "size": "276000000",
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "bit_rate": "18400000",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
                "color_range": "pc",
                "bits_per_raw_sample": "10",
            }
        ]
    }
    w._apply_analysis_document(doc)
    qapp.processEvents()

    assert w.src_bitrate == 18400000.0
    analysis_text = w.analysis_label.text()
    assert "18.4 Mbps" in analysis_text, f"Expected 18.4 Mbps in analysis text, got: {analysis_text}"
    assert "3840×2160" in analysis_text
    assert "10-bit" in analysis_text
    assert "2m 00s" in analysis_text

    # Test fallback to file size / duration calculation when streams omit bitrate
    doc_no_br = {
        "format": {
            "duration": "10.0",
            "size": "10000000",  # 10 MB in 10s = 8 Mbps
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "bits_per_raw_sample": "8",
            }
        ]
    }
    w._apply_analysis_document(doc_no_br)
    qapp.processEvents()
    assert w.src_bitrate == 8000000.0
    assert "8.0 Mbps" in w.analysis_label.text()

    w.close()


def test_settings_dialog_no_save_as_new_and_no_live_preview(qapp):
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog

    w = MainWindow()
    w.show()
    qapp.processEvents()

    dlg = ThemeCustomizeDialog(w)
    dlg.show()
    qapp.processEvents()

    # 1. Live window preview card and toggle button are hidden
    assert not dlg._live_preview_card.isVisible(), "Live window preview card must be hidden"
    assert not dlg._toggle_preview_btn.isVisible(), "Live window preview toggle button must be hidden"

    # 2. Save as new and gallery preset buttons (+ / -) are hidden
    assert not dlg._save_as_new_btn.isVisible(), "Save as new card must be hidden"
    assert not dlg._save_preset_btn.isVisible(), "Save preset (+) button must be hidden"
    assert not dlg._delete_preset_btn.isVisible(), "Delete preset (-) button must be hidden"

    # 3. Exactly 3 built-in theme presets in gallery
    dlg.select_section("appearance")
    dlg.select_appearance_tab("themes")
    qapp.processEvents()

    assert len(dlg._preset_cards) == 3, f"Expected 3 preset cards, got {len(dlg._preset_cards)}"
    titles = [c.preset_title for c in dlg._preset_cards]
    assert "Light" in titles and "Dark" in titles and "Neon" in titles

    # Gallery grid only contains the 3 theme cards (no Save as new card)
    assert dlg._preset_gallery_layout.count() == 3

    # 4. Resizing dialog preserves hidden state of live preview card
    for sz in ((800, 600), (950, 640), (1000, 660), (1180, 720)):
        dlg.resize(*sz)
        qapp.processEvents()
        assert not dlg._live_preview_card.isVisible()
        assert not dlg._toggle_preview_btn.isVisible()
        assert dlg._content_card.geometry().right() <= dlg.width()

    dlg.close()
    w.close()


def test_theme_dialog_menu_layout_and_button_uniformity(qapp):
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog

    w = MainWindow()
    w.show()
    qapp.processEvents()

    dlg = ThemeCustomizeDialog(w)
    dlg.show()
    qapp.processEvents()

    # 1. Preset cards: Light, Dark, Neon must have identical heights and widths (within 1px integer rounding)
    assert len(dlg._preset_cards) == 3
    card_w = [c.width() for c in dlg._preset_cards]
    assert max(card_w) - min(card_w) <= 1, f'Preset cards have uneven widths: {card_w}'
    card_h = [c.height() for c in dlg._preset_cards]
    assert len(set(card_h)) == 1, f'Preset cards have uneven heights: {card_h}'

    # 2. Preset action buttons: Surprise me and Reset to preset must have identical width and height
    assert abs(dlg._surprise_btn.width() - dlg._reset_btn.width()) <= 1, (
        f'Unequal action button widths: {dlg._surprise_btn.width()} vs {dlg._reset_btn.width()}'
    )
    assert dlg._surprise_btn.height() == dlg._reset_btn.height()

    # 3. Footer buttons: Revert and Save changes must have identical width and height
    assert dlg._revert_btn.width() == dlg._save_btn.width(), (
        f'Unequal footer button widths: {dlg._revert_btn.width()} vs {dlg._save_btn.width()}'
    )
    assert dlg._revert_btn.height() == dlg._save_btn.height() == 36

    # 4. Dirty status label must be clean text (transparent background and no border)
    status_qss = dlg._dirty_status_label.styleSheet()
    assert 'background: transparent' in status_qss or 'background: none' in status_qss or not status_qss
    assert 'border: none' in status_qss

    dlg.close()
    w.close()
