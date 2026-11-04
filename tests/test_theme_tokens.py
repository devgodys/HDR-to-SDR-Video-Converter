"""Unit and QSS tests for hdr2sdr.theme.theme_tokens (Stages 1a–1b)."""
from __future__ import annotations

import copy
from unittest.mock import patch

import hdr2sdr.theme.theme_tokens as tt
from hdr2sdr.theme.palettes import THEMES, _theme_is_dark
from hdr2sdr.theme.theme_tokens import (
    DARK_BASE_TOKENS,
    FONT_BODY_CANDIDATES,
    FONT_SEMI_CANDIDATES,
    LIGHT_BASE_TOKENS,
    NEON_DARK_BASE_TOKENS,
    NEON_LIGHT_BASE_TOKENS,
    SPACING_SCALE,
    WINDOW_RADIUS,
    build_combo_view_qss,
    build_glass_button_qss,
    build_main_window_qss,
    build_message_box_qss,
    build_mini_window_qss,
    build_progress_bar_qss,
    build_theme_dialog_qss,
    build_tokens,
    build_tooltip_qss,
    build_tray_menu_qss,
    contrast_ratio,
    mix_hex,
    parse_hex_rgb,
    relative_luminance,
    window_tokens,
)
from tools.redesign_verify import compute_contrast_table


def test_build_tokens_does_not_mutate_global_themes():
    snapshot = copy.deepcopy(THEMES)
    custom_look = dict(
        base="dark",
        stops=["#FF1122", "#33FF44", "#5566FF"],
        direction=135,
        intensity=70,
        effect="grid",
        button_custom=True,
        button_hex="#AABBCC",
        ui_style="glass",
        neon_effect=False,
    )
    tok = build_tokens(custom_look, radius_card=20, radius_button=10)
    assert tok["accent"] == "#AABBCC"
    assert tok["INDIGO"] == "#AABBCC"
    assert tok["accent_hover"] == mix_hex("#AABBCC", "#000000", 0.28)
    assert tok["INDIGO2"] == tok["accent_hover"]
    assert tok["radius_card"] == 20
    assert tok["radius_button"] == 10
    assert tok["radius_window"] == WINDOW_RADIUS == 8
    assert SPACING_SCALE == (4, 8, 12, 16, 24)
    assert THEMES == snapshot


def test_stage_1b_base_palette_values_and_surface_steps():
    dark = build_tokens(theme_name="dark")
    assert dark["bg_window"] == "#0E0F12"
    assert dark["bg_card"] == "#16181D"
    assert dark["bg_field"] == "#1F222A"
    assert dark["border"] == "#2A2E38"
    assert dark["text"] == "#E8EAF0"
    assert dark["text_secondary"] == "#8B93A5"
    assert dark["accent"] == "#4F7CFF"
    assert dark["accent_hover"] == "#6B93FF"
    assert dark["success"] == "#34D399"
    assert dark["warning"] == "#FBBF24"
    assert dark["danger"] == "#F87171"

    light = build_tokens(theme_name="light")
    assert light["bg_window"] == "#EEEEEB"
    assert light["bg_card"] == "#FFFFFF"
    assert light["bg_field"] == "#F3F3F0"
    assert light["border"] == "#DCDCD6"
    assert light["text"] == "#16181D"
    assert light["text_secondary"] == "#656C7A"
    assert light["accent"] == "#1A66E0"
    assert light["accent_hover"] == "#2F7BF2"
    assert light["on_accent"] == "#FFFFFF"
    assert light["success"] == "#34D399"
    assert light["warning"] == "#FBBF24"
    assert light["danger"] == "#F87171"

    # Distinct surface step progression in both Dark and Light
    dark_steps = [
        sum(parse_hex_rgb(dark[k]))
        for k in ("bg_window", "bg_card", "bg_card_alt", "bg_field", "bg_disabled", "border")
    ]
    assert dark_steps == sorted(dark_steps) and len(set(dark_steps)) == len(dark_steps)

    light_steps = [
        sum(parse_hex_rgb(light[k]))
        for k in ("bg_card", "bg_card_alt", "bg_field", "bg_window", "bg_disabled", "border")
    ]
    assert light_steps == sorted(light_steps, reverse=True) and len(set(light_steps)) == len(light_steps)


def test_stage_1b_wcag_contrast_thresholds():
    rows = compute_contrast_table()
    assert len(rows) >= 32
    failing = [r for r in rows if not r["meets_target"]]
    assert failing == [], f"Contrast failures: {failing}"


def test_stage_1b_font_candidates_prioritize_segoe_ui_variable():
    assert FONT_BODY_CANDIDATES[0] == "Segoe UI Variable Text"
    assert "Segoe UI" in FONT_BODY_CANDIDATES
    assert FONT_SEMI_CANDIDATES[0] == "Segoe UI Variable Display"
    assert "Segoe UI Semibold" in FONT_SEMI_CANDIDATES
    assert "Segoe UI" in FONT_SEMI_CANDIDATES


def test_build_tokens_neon_dark_and_light_bases():
    neon_dark = build_tokens(dict(base="dark", neon_effect=True, stops=None, button_custom=False))
    neon_light = build_tokens(dict(base="light", neon_effect=True, stops=None, button_custom=False))
    assert neon_dark["theme_name"] == "neon" and neon_dark["is_dark"] is True
    assert neon_dark["bg_window"] == NEON_DARK_BASE_TOKENS["bg_window"] == "#050507"
    assert neon_light["theme_name"] == "neon" and neon_light["is_dark"] is False
    assert neon_light["bg_window"] == NEON_LIGHT_BASE_TOKENS["bg_window"] == "#F6F4FF"


def test_window_tokens_returns_cached_tokens_without_calling_build_tokens():
    class _DummyWin:
        def __init__(self, look: dict):
            self.look = look
            self.theme_name = "neon" if look.get("neon_effect") else look.get("base", "light")
            self.tokens = build_tokens(look)

    win = _DummyWin(dict(base="dark", neon_effect=False, stops=None, button_custom=False))
    expected_tok = win.tokens

    with patch.object(tt, "build_tokens", wraps=tt.build_tokens) as spy:
        for _ in range(50):
            assert window_tokens(win) is expected_tok
            assert window_tokens(None)["theme_name"] == "light"
        assert spy.call_count == 0


def test_window_tokens_caches_fallback_on_uninitialized_window():
    class _BareWidget:
        theme_name = "dark"

    bare = _BareWidget()
    with patch.object(tt, "build_tokens", wraps=tt.build_tokens) as spy:
        tok1 = window_tokens(bare)
        tok2 = window_tokens(bare)
        assert tok1 is tok2
        assert tok1["theme_name"] == "dark"
        assert getattr(bare, "tokens") is tok1
        assert spy.call_count == 0


def test_theme_is_dark_accepts_str_dict_and_window_object():
    assert _theme_is_dark("dark") is True
    assert _theme_is_dark("neon") is True
    assert _theme_is_dark("light") is False
    assert _theme_is_dark(build_tokens(theme_name="dark")) is True
    assert _theme_is_dark(build_tokens(theme_name="light")) is False
    assert _theme_is_dark(dict(BG="#0E0F12")) is True
    assert _theme_is_dark(dict(BG="#EEEEEB")) is False

    class _Win:
        tokens = build_tokens(theme_name="neon")

    assert _theme_is_dark(_Win()) is True


def test_wcag_luminance_and_contrast_math():
    assert abs(relative_luminance("#000000") - 0.0) < 1e-6
    assert abs(relative_luminance("#FFFFFF") - 1.0) < 1e-6
    assert abs(contrast_ratio("#000000", "#FFFFFF") - 21.0) < 1e-3
    assert abs(contrast_ratio("#FFFFFF", "#FFFFFF") - 1.0) < 1e-6


def test_golden_qss_equality_for_light_dark_neon():
    for name, base_table in (
        ("light", LIGHT_BASE_TOKENS),
        ("dark", DARK_BASE_TOKENS),
        ("neon", NEON_DARK_BASE_TOKENS),
    ):
        t = build_tokens(theme_name=name)
        for key in ("BG", "CARD", "CARD2", "FIELD", "DISABLED", "BORDER", "TXT", "MUTED",
                    "INDIGO", "INDIGO2", "GREEN", "GREEN2", "RED", "RED2", "AMBER", "AMBER2",
                    "LOG_BG", "LOG_FG"):
            assert t[key] == base_table[key] == THEMES[name][key]

        for style in ("classic", "glass"):
            qss = build_main_window_qss(
                t,
                button_radius_px=8,
                chevron_icon="/tmp/chevron.png",
                font="Segoe UI",
                font_semi="Segoe UI Semibold",
                mono="Cascadia Mono",
                touch_slider_height=40,
                ui_style=style,
            )
            assert f"QWidget {{ background: {t['BG']}; color: {t['TXT']}; font-family: 'Segoe UI'; }}" in qss
            assert f"QPushButton[role=\"go\"] {{\n            background: {t['INDIGO']}; color: {t['on_accent']};" in qss
            if style == "glass":
                assert build_glass_button_qss(t) in qss
            else:
                assert 'QToolButton[role="theme"]:pressed' not in qss

        assert f"background: {t['CARD']};" in build_tooltip_qss(t, button_radius_px=8)
        assert f"selection-background-color: {t['INDIGO']};" in build_theme_dialog_qss(t)
        assert f"background: {t['INDIGO']}; color: {t['on_accent']};" in build_mini_window_qss(t)
        assert f"background: {t['CARD']};" in build_tray_menu_qss(t)
        assert f"background: {t['CARD']};" in build_message_box_qss(t)
        assert f"background-color: {t['CARD']};" in build_combo_view_qss(t)
        pbar_qss = build_progress_bar_qss(t, name, 18)
        assert "border-radius: 9px;" in pbar_qss
        assert f"color: {t['TXT']};" in pbar_qss


def test_stage_2_pick_on_accent_extreme_and_base_colors():
    from hdr2sdr.theme.theme_tokens import (
        ON_ACCENT_DARK,
        ON_ACCENT_LIGHT,
        adjust_card_bg_for_gradient,
        average_gradient_hex,
        is_accent_contrast_low,
        pick_on_accent,
    )

    assert ON_ACCENT_DARK == "#0B1220"
    assert ON_ACCENT_LIGHT == "#FFFFFF"

    cases = [
        ("#FAFAFA", ON_ACCENT_DARK),   # near white -> dark text
        ("#08080A", ON_ACCENT_LIGHT),  # near black -> white text
        ("#FFD400", ON_ACCENT_DARK),   # yellow -> dark text
        ("#00E5FF", ON_ACCENT_DARK),   # cyan -> dark text
        ("#7B2FD8", ON_ACCENT_LIGHT),  # purple -> white text
        ("#4F7CFF", ON_ACCENT_DARK),   # dark base accent
        ("#1A66E0", ON_ACCENT_LIGHT),  # light base accent
    ]
    for bg_hex, expected_fg in cases:
        chosen = pick_on_accent(bg_hex)
        assert chosen == expected_fg, f"For {bg_hex}: expected {expected_fg}, got {chosen}"
        assert contrast_ratio(chosen, bg_hex) >= 4.5

    # Contrast of Start (role="go") in all base themes and with custom accents
    for name in ("light", "dark", "neon"):
        t = build_tokens(theme_name=name)
        assert t["on_accent"] == pick_on_accent(t["accent"])
        assert contrast_ratio(t["on_accent"], t["accent"]) >= 4.5

    for custom_acc in ("#FAFAFA", "#08080A", "#FFD400", "#00E5FF", "#7B2FD8"):
        t_custom = build_tokens(
            dict(base="dark", stops=[custom_acc, "#112233"], button_custom=True, button_hex=custom_acc)
        )
        assert t_custom["on_accent"] == pick_on_accent(custom_acc)
        assert contrast_ratio(t_custom["on_accent"], t_custom["accent"]) >= 4.5

    # Gradient auto-contrast adjustment
    avg_grad = average_gradient_hex(["#FFFFFF", "#FFFFEE"], "#EEEEEB", 100)
    adj_card, _, adjusted = adjust_card_bg_for_gradient(
        "#3A3D45", "#E8EAF0", ["#FFFFFF", "#FFFFEE"], "#0E0F12", 100
    )
    assert adjusted is True
    assert contrast_ratio("#E8EAF0", adj_card) >= 4.5

    # Low accent contrast warning helper (< 3:1 with bg_card or on_accent)
    low, bg_r, _ = is_accent_contrast_low("#20242C", "#16181D")
    assert low is True and bg_r < 3.0
    low_ok, bg_r_ok, txt_r_ok = is_accent_contrast_low("#4F7CFF", "#16181D")
    assert low_ok is False and bg_r_ok >= 3.0 and txt_r_ok >= 4.5


def test_stage_2_qt_autocontrast_and_customize_warning():
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return

    app = QApplication.instance() or QApplication([])
    from hdr2sdr.theme.look import builtin_preset, default_look
    from hdr2sdr.theme.theme_tokens import contrast_ratio, pick_on_accent
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog
    from hdr2sdr.ui.main_window import MainWindow

    w = MainWindow()
    for preset_key in ("light", "dark", "neon"):
        w._set_look(builtin_preset(preset_key), persist=False)
        assert w.go_btn.property("role") == "go"
        on_acc = pick_on_accent(w.tokens["accent"])
        assert w.tokens["on_accent"] == on_acc
        assert contrast_ratio(on_acc, w.tokens["accent"]) >= 4.5
        assert f"background: {w.tokens['accent']}; color: {on_acc};" in w.styleSheet()

    # Custom extreme accents on MainWindow.go_btn
    for acc in ("#FAFAFA", "#08080A", "#FFD400", "#00E5FF", "#7B2FD8"):
        look = dict(default_look("dark"), stops=[acc, "#223344"], intensity=45, button_custom=True, button_hex=acc)
        w._set_look(look, persist=False)
        expected_fg = pick_on_accent(acc)
        assert w.tokens["on_accent"] == expected_fg
        assert contrast_ratio(expected_fg, acc) >= 4.5
        assert f"background: {acc}; color: {expected_fg};" in w.styleSheet()

    # Customize dialog non-blocking warning when accent contrast < 3:1 with bg_card
    dlg = ThemeCustomizeDialog(w)
    dlg._button_swatch_btn.click()
    dlg._hex_edit.setText("#1A1D24")
    dlg._hex_edit.editingFinished.emit()
    assert not dlg._contrast_warn_label.isHidden()
    dlg._hex_edit.setText("#4F7CFF")
    dlg._hex_edit.editingFinished.emit()
    assert dlg._contrast_warn_label.isHidden()
    dlg.reject()
    w.close()


def test_stage_3_preset_matching_pure_and_tolerance():
    from hdr2sdr.core.constants import match_active_preset

    # Unknown kind or Bitrate mode -> None
    assert match_active_preset(
        kind="unknown", encoder="CPU \u00b7 H.265", method="BT.2390 \u00b7 GPU tonemap", quality=18
    ) is None
    assert match_active_preset(
        kind="hdr", encoder="CPU \u00b7 H.265", method="BT.2390 \u00b7 GPU tonemap", quality=18, quality_mode="Bitrate"
    ) is None

    # Optimal: optimal_curve + default quality (18 ± 1)
    assert match_active_preset(
        kind="hdr", encoder="NVIDIA NVENC \u00b7 H.264", method="BT.2390 \u00b7 GPU tonemap",
        quality=18, optimal_curve="BT.2390 \u00b7 GPU tonemap", fast_encoder="NVIDIA NVENC \u00b7 H.265",
        default_quality=18,
    ) == "optimal"
    assert match_active_preset(
        kind="hdr", encoder="NVIDIA NVENC \u00b7 H.264", method="BT.2390 \u00b7 GPU tonemap",
        quality=19, optimal_curve="BT.2390 \u00b7 GPU tonemap", fast_encoder="NVIDIA NVENC \u00b7 H.265",
        default_quality=18,
    ) == "optimal"

    # Best quality: CPU · H.265 + optimal_curve + CRF 16 (± 1)
    assert match_active_preset(
        kind="hdr", encoder="CPU \u00b7 H.265", method="BT.2390 \u00b7 GPU tonemap",
        quality=16, optimal_curve="BT.2390 \u00b7 GPU tonemap", default_quality=18,
    ) == "best"
    assert match_active_preset(
        kind="hdr", encoder="CPU \u00b7 H.265", method="BT.2390 \u00b7 GPU tonemap",
        quality=15, optimal_curve="BT.2390 \u00b7 GPU tonemap", default_quality=18,
    ) == "best"

    # Fast (CPU fallback): CPU · H.264 + fast_curve + CRF 23 (± 1)
    assert match_active_preset(
        kind="hdr", encoder="CPU \u00b7 H.264", method="BT.2390 \u00b7 GPU tonemap",
        quality=23, optimal_curve="BT.2390 \u00b7 GPU tonemap",
        fast_encoder="CPU \u00b7 H.264", fast_curve="BT.2390 \u00b7 GPU tonemap",
        default_quality=18,
    ) == "fast"

    # Custom settings (Variant A -> None)
    assert match_active_preset(
        kind="hdr", encoder="CPU \u00b7 H.265", method="Reinhard \u00b7 CPU tonemap",
        quality=29, optimal_curve="BT.2390 \u00b7 GPU tonemap", default_quality=18,
    ) is None


def test_stage_3_quality_tier_boundaries_and_range_reset():
    from hdr2sdr.core.constants import (
        QUALITY_TIER_BOUNDS_51,
        QUALITY_TIER_BOUNDS_AV1,
        quality_tier_label,
        should_reset_quality_on_range_change,
    )

    assert len(QUALITY_TIER_BOUNDS_51) == 4
    assert len(QUALITY_TIER_BOUNDS_AV1) == 4

    # 0-51 scale (H.264/H.265/hardware): 0-16 Max, 17-21 High, 22-27 Medium, 28-51 Low
    for enc in ("CPU \u00b7 H.264", "CPU \u00b7 H.265", "NVIDIA NVENC \u00b7 H.265"):
        assert quality_tier_label(enc, 0) == "Maximum"
        assert quality_tier_label(enc, 16) == "Maximum"
        assert quality_tier_label(enc, 17) == "High"
        assert quality_tier_label(enc, 21) == "High"
        assert quality_tier_label(enc, 22) == "Medium"
        assert quality_tier_label(enc, 27) == "Medium"
        assert quality_tier_label(enc, 28) == "Low"
        assert quality_tier_label(enc, 51) == "Low"

    # 0-63 scale (CPU · AV1): 0-23 Max, 24-32 High, 33-40 Medium, 41-63 Low
    assert quality_tier_label("CPU \u00b7 AV1", 0) == "Maximum"
    assert quality_tier_label("CPU \u00b7 AV1", 23) == "Maximum"
    assert quality_tier_label("CPU \u00b7 AV1", 24) == "High"
    assert quality_tier_label("CPU \u00b7 AV1", 32) == "High"
    assert quality_tier_label("CPU \u00b7 AV1", 33) == "Medium"
    assert quality_tier_label("CPU \u00b7 AV1", 40) == "Medium"
    assert quality_tier_label("CPU \u00b7 AV1", 41) == "Low"
    assert quality_tier_label("CPU \u00b7 AV1", 63) == "Low"

    # Range change resets ONLY when old value == old default (or out of new range)
    assert should_reset_quality_on_range_change(18, 18, 0, 63) is True
    assert should_reset_quality_on_range_change(22, 18, 0, 63) is False
    assert should_reset_quality_on_range_change(30, 30, 0, 51) is True
    assert should_reset_quality_on_range_change(25, 30, 0, 51) is False
    assert should_reset_quality_on_range_change(60, 30, 0, 51) is True


def test_stage_3_button_disabled_kofi_and_section_qss_rules():
    for name in ("light", "dark", "neon"):
        t = build_tokens(theme_name=name)
        pro_qss = build_main_window_qss(
            t,
            button_radius_px=8,
            chevron_icon="/tmp/chevron.png",
            font="Segoe UI",
            font_semi="Segoe UI Semibold",
            mono="Cascadia Mono",
        )
        mini_qss = build_mini_window_qss(t, button_radius_px=8)
        for qss in (pro_qss, mini_qss):
            assert 'QPushButton[role="panel-toggle"]:disabled' in qss
            assert 'QPushButton[role="preset-segment-btn"]' in qss
            assert 'QPushButton:disabled' in qss
        # Ko-fi ghost style in Pro QSS
        assert 'QPushButton[role="kofi"]' in pro_qss
        assert f"border: 1px solid {t['kofi']};" in pro_qss
        # Small-caps section-title uses text_secondary
        assert f"QLabel[role=\"section-title\"] {{\n            color: {t['text_secondary']};" in pro_qss
        # ComboBox constant 2px focus/open border with compensated padding
        assert "QComboBox:focus, QSpinBox:focus { border: 2px solid" in pro_qss
        assert "padding: 5px 7px;" in pro_qss
        assert 'QComboBox[popupOpen="true"] { border: 2px solid' in pro_qss


def test_stage_3_qt_components_stack_presets_and_combo_geometry():
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QComboBox
    except ImportError:
        return

    app = QApplication.instance() or QApplication([])
    from hdr2sdr.core.constants import PROGRESS_BAR_HEIGHT
    from hdr2sdr.theme.look import builtin_preset
    from hdr2sdr.ui.main_window import MainWindow
    from hdr2sdr.ui.mini_window import MiniModeWindow

    w = MainWindow()
    mini = MiniModeWindow(w)
    w.show()
    for _ in range(5):
        app.processEvents()

    # Unified progress bar height
    assert w.pbar.height() == mini.pbar.height() == PROGRESS_BAR_HEIGHT == 18

    # Controls QStackedWidget: page 0 ([Start]) in idle, page 1 ([Pause][Stop]) when running
    assert w.run_stack.currentIndex() == 0
    assert w.run_idle_page.height() == w.run_active_page.height() or w.run_stack.height() == 36
    w.proc = object()
    w.paused = False
    w.update_run_controls()
    assert w.run_stack.currentIndex() == 1
    assert w.pause_btn.isEnabled() and w.stop_btn.isEnabled()
    w.paused = True
    w.update_run_controls()
    assert w.pause_btn.property("role") == "resume-ready"
    w.proc = None
    w.paused = False
    w.update_run_controls()
    assert w.run_stack.currentIndex() == 0

    # Presets segmented control in Pro mode
    w.kind = "hdr"
    w._last_analysis_raw = "{}"
    for b in (w.preset_optimal_btn, w.preset_best_btn, w.preset_fast_btn):
        b.setEnabled(True)
    w._apply_optimal_preset()
    assert w.preset_optimal_btn.isChecked()
    assert not w.preset_best_btn.isChecked() and not w.preset_fast_btn.isChecked()
    w._apply_best_quality_preset()
    assert w.preset_best_btn.isChecked()
    assert not w.preset_optimal_btn.isChecked() and not w.preset_fast_btn.isChecked()
    # Custom settings -> all 3 unchecked + empty preset hint (hidden by default)
    w.quality_spin.setValue(31)
    assert not w.preset_optimal_btn.isChecked()
    assert not w.preset_best_btn.isChecked()
    assert not w.preset_fast_btn.isChecked()
    assert w.preset_hint_label.text() == ""

    # Neon selective glow: Cards do not have _shadow enabled; neutral buttons do not glow
    w._set_look(builtin_preset("neon"), persist=False)
    assert w.go_btn._shadow.isEnabled() is True
    assert w.queue_add_btn._shadow.isEnabled() is False
    assert w.files_card._shadow.isEnabled() is False

    # ComboBox geometry before/after focus does not change
    for combo in w.findChildren(QComboBox):
        if not combo.isVisible():
            continue
        before = combo.geometry().getRect()
        combo.setFocus()
        combo.setProperty("popupOpen", True)
        combo.style().unpolish(combo)
        combo.style().polish(combo)
        app.processEvents()
        during = combo.geometry().getRect()
        combo.setProperty("popupOpen", False)
        combo.clearFocus()
        combo.style().unpolish(combo)
        combo.style().polish(combo)
        app.processEvents()
        after = combo.geometry().getRect()
        assert before == during == after

    mini.close()
    w.close()


def test_stage_4_spacing_grid_and_stepper_glow_room():
    from pathlib import Path
    import hdr2sdr.theme.theme_tokens as tt

    assert tt.SPACING_SCALE == (4, 8, 12, 16, 24)
    root = Path(__file__).resolve().parent.parent
    stepper_src = (root / "hdr2sdr/ui/widgets/stepper.py").read_text(encoding="utf-8")
    assert "STEPPER_GLOW_ROOM = 5" in stepper_src
    assert "class StepperRowLayout(QHBoxLayout):" in stepper_src
    assert "class LeftRoomLayout(QVBoxLayout):" in stepper_src

    containers_src = (root / "hdr2sdr/ui/widgets/containers.py").read_text(encoding="utf-8")
    assert "outer.setContentsMargins(16, 12, 16, 12)" in containers_src
    assert "outer.setSpacing(8)" in containers_src
    assert "self.body.setSpacing(8)" in containers_src


def test_stage_4_qt_empty_state_accordions_and_wheel_guard():
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return

    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    for _ in range(5):
        app.processEvents()

    # 1. Empty queue state (v24_7): compact drop zone visible, Source analysis & Live preview ALWAYS visible,
    # and window + Files card heights remain identical before and after adding/removing files.
    w.queue_list.clear()
    w._queue_controller.paths.clear()
    w._update_queue_hint()
    for _ in range(5):
        app.processEvents()
    assert not w.files_empty_drop_widget.isHidden()
    assert w.queue_list.isHidden()
    assert not w.analysis_card.isHidden()
    assert not w.preview_card.isHidden()
    win_h_empty = w.height()
    files_h_empty = w.files_card.height()

    # Adding first video switches drop zone -> queue_list without changing window or Files card height
    w.queue_list.addItem("test_video.mkv")
    for _ in range(5):
        app.processEvents()
    assert w.files_empty_drop_widget.isHidden()
    assert not w.queue_list.isHidden()
    assert not w.analysis_card.isHidden()
    assert not w.preview_card.isHidden()
    assert w.height() == win_h_empty
    assert w.files_card.height() == files_h_empty

    # 2. Collapsible sections in right column (P1-1: encoding, tonemap, output; Additional removed)
    assert "encoding" in w._section_bodies
    assert "tonemap" in w._section_bodies
    assert "output" in w._section_bodies
    assert "additional" not in w._section_bodies
    assert not w._section_bodies["encoding"].isHidden()
    assert not w._section_bodies["output"].isHidden()

    w.toggle_conversion_section("output")
    assert w._section_bodies["output"].isHidden()
    assert str(w.i18n.settings.value("section_expanded_output")).lower() == "false"

    w.toggle_conversion_section("output")
    assert not w._section_bodies["output"].isHidden()
    assert str(w.i18n.settings.value("section_expanded_output")).lower() == "true"

    # 3. Wheel guard: spinboxes, sliders, and comboboxes ignore wheel without focus
    w.quality_spin.clearFocus()
    w.quality_slider.clearFocus()
    w.encoder_combo.clearFocus()
    q_before = w.quality_spin.value()
    enc_before = w.encoder_combo.currentIndex()

    wheel_ev = QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPointF(0, 120).toPoint(),
        QPointF(0, 120).toPoint(),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    w.quality_spin.wheelEvent(wheel_ev)
    assert w.quality_spin.value() == q_before
    w.quality_slider.wheelEvent(wheel_ev)
    assert w.quality_slider.value() == q_before
    w.encoder_combo.wheelEvent(wheel_ev)
    assert w.encoder_combo.currentIndex() == enc_before

    w.close()


def test_v24_5_no_unscoped_stylesheets_and_layout_invariants():
    from pathlib import Path
    import re
    from hdr2sdr.core.constants import should_reset_quality_on_range_change
    from hdr2sdr.theme.theme_tokens import build_scroll_area_qss, build_tokens

    root = Path(__file__).resolve().parent.parent
    # P0-1 static check: no setStyleSheet("background: transparent;") without selector in hdr2sdr/ui/
    for py_path in sorted((root / "hdr2sdr" / "ui").rglob("*.py")):
        src = py_path.read_text(encoding="utf-8")
        assert 'setStyleSheet("background: transparent;")' not in src, f"Unscoped stylesheet in {py_path.name}"
        assert 'setStyleSheet("background: transparent; border: none;")' not in src, f"Unscoped stylesheet in {py_path.name}"

    # P0-2 static check: conv.body.addStretch(1) is not present in main_window.py
    mw_src = (root / "hdr2sdr" / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "conv.body.addStretch(1)" not in mw_src
    assert "conv.body.addWidget(self.conv_scroll, 1)" in mw_src

    # P1-1 static check: collapsible section arrows use expanded / not body_w.isHidden(), never body_w.isVisible()
    assert "body_w.isVisible()" not in mw_src
    assert "expanded = body_w.isHidden()" in mw_src

    # P1-2 check: build_scroll_area_qss produces scoped scrollbar rules for convScroll and analysisScroll
    for tname in ("light", "dark", "neon"):
        t = build_tokens(theme_name=tname)
        sq = build_scroll_area_qss(t, "convScroll")
        assert "QScrollArea#convScroll QScrollBar:vertical" in sq
        assert "QScrollArea#convScroll QScrollBar::handle:vertical" in sq
        assert f"background: {t['border']};" in sq

    # P1-4 check: switching between two 0-51 encoders (default 18 -> default 18) returns False
    assert should_reset_quality_on_range_change(18, 18, 0, 51, 18) is False
    assert should_reset_quality_on_range_change(18, 18, 0, 63, 30) is True
    assert should_reset_quality_on_range_change(30, 30, 0, 51, 18) is True
    assert should_reset_quality_on_range_change(22, 18, 0, 63, 30) is False


def test_v24_5_qt_p0_p1_p2_regression_suite():
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPointF, QSettings, Qt
        from PySide6.QtGui import QColor, QMouseEvent
        from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QWidget
    except ImportError:
        return

    import json
    import tempfile
    from pathlib import Path
    from hdr2sdr.theme.look import builtin_preset
    from hdr2sdr.theme.theme_tokens import parse_hex_rgb
    from hdr2sdr.ui.conversion_preferences import ConversionPreferences
    from hdr2sdr.ui.main_window import MainWindow
    from hdr2sdr.ui.mini_window import MiniModeWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    mini = MiniModeWindow(w)

    # v24_7 A.1: With empty queue, Source analysis & Live preview are ALWAYS visible with their placeholders
    assert w.queue_list.count() == 0
    assert w.analysis_card.isHidden() is False
    assert w.preview_card.isHidden() is False
    assert w.files_empty_drop_widget.isHidden() is False

    # P1-1: Before first show(), expanded sections show '▾' and collapsed 'Additional' shows '▸'
    assert w._section_headers["encoding"][0].text().startswith("\u25be ")
    assert w._section_headers["tonemap"][0].text().startswith("\u25be ")
    assert w._section_headers["output"][0].text().startswith("\u25be ")
    assert "additional" not in w._section_headers

    w.show()
    mini.show()
    for _ in range(5):
        app.processEvents()

    # P0-1(a): Walk all QWidgets in MainWindow and MiniModeWindow: no widget has an unscoped stylesheet rule
    for top in (w, mini):
        for child in [top, *top.findChildren(QWidget)]:
            ss = (child.styleSheet() or "").strip()
            if not ss:
                continue
            assert "{" in ss and not ss.lower().startswith("background"), (
                f"Unscoped stylesheet on {type(child).__name__}#{child.objectName()}: {ss!r}"
            )

    # P0-1(b) & (c): Pixel test on go_btn (Start) and backend_combo, and non-empty grab on run_stack
    for theme_key in ("dark", "light"):
        w._set_look(builtin_preset(theme_key), persist=False)
        for _ in range(5):
            app.processEvents()
        t = w.tokens

        # grab() of go_btn: sample pixel near left-center (away from centered text) is close to t["accent"]
        go_img = w.go_btn.grab().toImage()
        assert not go_img.isNull() and go_img.width() > 20 and go_img.height() > 10
        sample_c = go_img.pixelColor(max(6, go_img.width() // 6), go_img.height() // 2)
        acc_r, acc_g, acc_b = parse_hex_rgb(t["accent"])
        card_r, card_g, card_b = parse_hex_rgb(t["bg_card"])
        dist_acc = abs(sample_c.red() - acc_r) + abs(sample_c.green() - acc_g) + abs(sample_c.blue() - acc_b)
        dist_card = abs(sample_c.red() - card_r) + abs(sample_c.green() - card_g) + abs(sample_c.blue() - card_b)
        assert dist_acc < dist_card and dist_acc < 60, f"go_btn pixel {sample_c.name()} not close to accent {t['accent']}"

        # grab() of backend_combo: background pixel is close to t["bg_field"]
        cb_img = w.backend_combo.grab().toImage()
        assert not cb_img.isNull() and cb_img.width() > 20
        cb_c = cb_img.pixelColor(cb_img.width() - 28, 4)
        fld_r, fld_g, fld_b = parse_hex_rgb(t["bg_field"])
        dist_fld = abs(cb_c.red() - fld_r) + abs(cb_c.green() - fld_g) + abs(cb_c.blue() - fld_b)
        assert dist_fld < 50, f"backend_combo pixel {cb_c.name()} not close to bg_field {t['bg_field']}"

        # run_stack page Start has visible text (non-uniform pixels across grab)
        stack_img = w.run_stack.grab().toImage()
        colors_seen = {
            stack_img.pixelColor(x, stack_img.height() // 2).rgb()
            for x in range(4, stack_img.width() - 4, 2)
        }
        assert len(colors_seen) >= 2, "Start text not rendered on run_stack"

        # P0-2: conv_scroll height >= 300px and bottom buttons visible
        assert w.conv_scroll.height() >= 300, f"conv_scroll too short: {w.conv_scroll.height()}"
        assert w.activity_btn.isVisible()

        # P0-3: No horizontal clipping inside conv_scroll.viewport() across min / normal / wide sizes
        # P1-1: Additional section removed; all three sections (encoding, tonemap, output) are expanded
        for test_w, test_h in ((w.minimumWidth(), w.minimumHeight()), (1080, 1050), (1360, 1080)):
            w.resize(test_w, test_h)
            for _ in range(4):
                app.processEvents()
            vp_w = w.conv_scroll.viewport().width()
            assert vp_w >= w.conv_scroll_content.minimumSizeHint().width(), (
                f"Viewport {vp_w} < content minimumSizeHint {w.conv_scroll_content.minimumSizeHint().width()}"
            )
            for child in [c for T in (QComboBox, QPushButton, QLabel) for c in w.conv_scroll_content.findChildren(T)]:
                if not child.isVisible():
                    continue
                top_right_in_content = child.mapTo(w.conv_scroll_content, child.rect().topRight())
                assert top_right_in_content.x() <= vp_w + 2, (
                    f"Widget {type(child).__name__} ({getattr(child, 'text', lambda: '')()}) "
                    f"right={top_right_in_content.x()} > viewport={vp_w} at size {(test_w, test_h)}"
                )
        pass

    # P1-4: Quality = 18 preserved across NVENC H.265 restore and encoder switch
    ini_path = Path(tempfile.mkdtemp()) / "prefs_test.ini"
    s = QSettings(str(ini_path), QSettings.Format.IniFormat)
    s.setValue("conversion_preferences", json.dumps({
        "backend": "FFmpeg",
        "bit_depth": "8-bit (SDR standard)",
        "encoder": "NVIDIA NVENC \u00b7 H.265",
        "quality": 18,
    }))
    prefs = ConversionPreferences(
        s,
        w._conversion_preference_controls(),
        w._restore_output_folder,
        lambda: w.output_folder,
        lambda: w._on_bit_depth_changed(w.bit_depth_combo.currentText()),
        w,
    )
    prefs.restore()
    assert w.quality_spin.value() == 18

    # Switching CPU · H.264 -> NVIDIA NVENC · H.265 with quality=18 keeps 18
    w.encoder_combo.setCurrentText("CPU \u00b7 H.264")
    w.quality_spin.setValue(18)
    if w.encoder_combo.findText("NVIDIA NVENC \u00b7 H.265") >= 0:
        w.encoder_combo.setCurrentText("NVIDIA NVENC \u00b7 H.265")
        assert w.quality_spin.value() == 18

    # Even if an old settings file had quality=0 saved accidentally, restore clamps it to 18
    s.setValue("conversion_preferences", json.dumps({
        "backend": "FFmpeg",
        "bit_depth": "8-bit (SDR standard)",
        "encoder": "NVIDIA NVENC \u00b7 H.265",
        "quality": 0,
    }))
    prefs.restore()
    assert w.quality_spin.value() == 18

    mini.close()
    w.close()



def test_v24_7_window_and_files_geometry_invariant_on_queue_add_remove(tmp_path):
    """v24_7 Section A: Source analysis and Live preview are always visible with
    their placeholders, _animate_panels_reveal is gone, and window + Files card
    geometries remain completely unchanged on queue add and remove."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    mw_text = (root / "hdr2sdr" / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "_animate_panels_reveal" not in mw_text

    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return

    from hdr2sdr.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show()
    for _ in range(6):
        app.processEvents()

    # Empty state: Source analysis & Live preview visible with placeholders
    assert w.queue_list.count() == 0
    assert w.analysis_card.isVisible()
    assert w.preview_card.isVisible()
    assert "Select a video to analyze it automatically." in w.analysis_label.text()
    assert "Choose a source video to see an instant preview of the current settings." in w.preview_label.text()
    assert w.files_empty_drop_widget.isVisible()
    assert not w.queue_list.isVisible()

    win_geo_before = w.geometry().getRect()
    files_geo_before = w.files_card.geometry().getRect()

    # Emulate adding a file via _add_files_to_queue
    dummy_video = tmp_path / "sample.mkv"
    dummy_video.write_bytes(b"\x00" * 64)
    w._add_files_to_queue([str(dummy_video)], trust_extension=True)
    for _ in range(6):
        app.processEvents()

    assert w.queue_list.count() == 1
    assert not w.files_empty_drop_widget.isVisible()
    assert w.queue_list.isVisible()
    assert w.analysis_card.isVisible()
    assert w.preview_card.isVisible()
    assert w.geometry().getRect() == win_geo_before
    assert w.files_card.geometry().getRect() == files_geo_before

    # Remove the file via _remove_queue_item
    w.queue_list.setCurrentRow(0)
    w._remove_queue_item()
    for _ in range(6):
        app.processEvents()

    assert w.queue_list.count() == 0
    assert w.files_empty_drop_widget.isVisible()
    assert not w.queue_list.isVisible()
    assert w.geometry().getRect() == win_geo_before
    assert w.files_card.geometry().getRect() == files_geo_before
    w.close()



def test_all_token_keys_used_in_hdr2sdr_exist_in_build_tokens():
    """v24_7b Item 2 (pure Python AST + regex): every token key accessed via
    t["..."], t['...'], tokens["..."], self.tokens["..."], window_tokens(...)["..."],
    or .get("...") without a default across hdr2sdr/**/*.py must exist in
    build_tokens() for light, dark, neon, and neon_light."""
    import ast
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    token_var_names = {"t", "tokens", "_tokens", "tok"}
    re_sub = re.compile(r'\b(?:self\.)?(?:t|tokens|_tokens|tok)\[\s*[\'"]([a-zA-Z0-9_]+)[\'"]\s*\]')
    re_get_no_default = re.compile(r'\b(?:self\.)?(?:t|tokens|_tokens|tok)\.get\(\s*[\'"]([a-zA-Z0-9_]+)[\'"]\s*\)')

    def _is_token_expr(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id in token_var_names:
            return True
        if isinstance(node, ast.Attribute) and node.attr == "tokens":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("window_tokens", "build_tokens"):
            return True
        return False

    used = []
    for p in sorted((root / "hdr2sdr").rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and _is_token_expr(node.value):
                sl = node.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    used.append((rel, node.lineno, sl.value, "ast_subscript"))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if _is_token_expr(node.func.value) and len(node.args) == 1 and not node.keywords:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                        used.append((rel, node.lineno, arg0.value, "ast_get_no_default"))
        for lineno, line in enumerate(src.splitlines(), 1):
            for m in re_sub.finditer(line):
                used.append((rel, lineno, m.group(1), "regex_subscript"))
            for m in re_get_no_default.finditer(line):
                used.append((rel, lineno, m.group(1), "regex_get_no_default"))

    assert len(used) >= 100, f"Expected to scan many token accesses, got {len(used)}"

    palettes = {
        "light": build_tokens(theme_name="light"),
        "dark": build_tokens(theme_name="dark"),
        "neon": build_tokens(theme_name="neon"),
        "neon_light": build_tokens(theme_name="neon_light"),
        "neon_light_look": build_tokens(dict(base="light", neon_effect=True)),
    }
    missing = []
    for rel, lineno, key, kind in used:
        for pname, pdict in palettes.items():
            if key not in pdict:
                missing.append((rel, lineno, key, kind, pname))
    assert not missing, f"Missing token keys in build_tokens(): {missing}"


def test_v24_7b_default_window_size_preview_layout_and_saved_geometry():
    """v24_7b/7c Item 3:
    - Fresh profile opens at 1080x1050 (capped only by real desktop screen height minus margins).
    - Window minimumHeight keeps preview_label height >= 200px.
    - Placeholder preview_label sits strictly above preview_meta_label and preview_controls_widget
      (never overlapping timeline_row), both at default height and at minimumHeight.
    - Saved window geometry in QSettings takes priority over default size on screens from 800x800 to 4K."""
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return

    from hdr2sdr.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show()
    for _ in range(6):
        app.processEvents()

    avail = w.screen().availableGeometry() if w.screen() is not None else None
    is_offscreen = QApplication.platformName().lower() == "offscreen"
    if avail is not None and ((not is_offscreen and avail.height() > 0) or avail.height() >= 960):
        expected_default_h = min(max(w.DEFAULT_WINDOW_HEIGHT, w._compute_minimum_window_height()), max(720, avail.height() - 32))
    else:
        expected_default_h = max(w.DEFAULT_WINDOW_HEIGHT, w._compute_minimum_window_height())

    # 1. Default size on fresh profile matches expected_default_h and keeps preview_label >= 200px
    default_size = (w.width(), w.height())
    assert w.width() == 1080
    assert w.height() == expected_default_h
    assert w.minimumHeight() >= min(960, expected_default_h)
    assert w.preview_label.height() >= 200

    # 2. Placeholder is strictly inside preview area above preview_meta_label and timeline controls
    lbl_bottom = w.preview_label.mapTo(w.preview_card, w.preview_label.rect().bottomLeft()).y()
    meta_top = w.preview_meta_label.mapTo(w.preview_card, w.preview_meta_label.rect().topLeft()).y()
    assert lbl_bottom < meta_top, (lbl_bottom, meta_top)
    assert not w.preview_controls_widget.isVisible()

    # Even when shrunk to minimumHeight, preview_label >= 200px and never overlaps timeline controls
    w.resize(w.minimumWidth(), w.minimumHeight())
    for _ in range(6):
        app.processEvents()
    assert w.preview_label.height() >= 198
    lbl_bottom_min = w.preview_label.mapTo(w.preview_card, w.preview_label.rect().bottomLeft()).y()
    meta_top_min = w.preview_meta_label.mapTo(w.preview_card, w.preview_meta_label.rect().topLeft()).y()
    assert lbl_bottom_min < meta_top_min, (lbl_bottom_min, meta_top_min)
    assert not w.preview_controls_widget.isVisible()
    settings_ref = w.i18n.settings
    w.close()
    settings_ref.remove("window_geometry")
    settings_ref.remove("window_size")

    # 3. Saved window geometry in QSettings takes priority over default 1080x1050 (screen-aware: 800x800 .. 4K)
    w2 = MainWindow()
    w2.show()
    for _ in range(4):
        app.processEvents()
    avail2 = w2.screen().availableGeometry() if w2.screen() is not None else None
    if avail2 is not None:
        target_w = max(w2.minimumWidth(), min(1240, avail2.width() - 80))
        if target_w == default_size[0]:
            target_w = max(w2.minimumWidth(), default_size[0] - 80)
        target_h = max(w2.minimumHeight(), min(1120, avail2.height() - 80))
    else:
        target_w, target_h = 1240, 1120
    w2.resize(target_w, target_h)
    for _ in range(4):
        app.processEvents()
    saved_size = (w2.width(), w2.height())
    w2.close()

    w3 = MainWindow()
    w3.show()
    for _ in range(6):
        app.processEvents()
    assert getattr(w3, "_has_saved_geometry", False) is True
    assert (w3.width(), w3.height()) == saved_size
    assert (w3.width(), w3.height()) != default_size
    settings_ref = w3.i18n.settings
    w3.close()
    settings_ref.remove("window_geometry")
    settings_ref.remove("window_size")



def test_v24_8_p0_p1_suite():
    """v24_8 verification suite for P0-1, P0-2, P0-3, P0-4, P1-1, P1-2."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent

    # Pure-Python structural checks
    stepper_src = (root / "hdr2sdr/ui/widgets/stepper.py").read_text(encoding="utf-8")
    assert "row.setSpacing(10)" in stepper_src

    mw_src = (root / "hdr2sdr/ui/main_window.py").read_text(encoding="utf-8")
    assert "self.analysis_scroll.setFixedHeight(" not in mw_src
    assert "progress.body.addWidget(self.speed_label)" not in mw_src
    assert '_make_collapsible_section("additional"' not in mw_src

    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return

    import json
    import tempfile
    from hdr2sdr.ui.conversion_preferences import ConversionPreferences
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog
    from hdr2sdr.ui.main_window import MainWindow
    from hdr2sdr.ui.mini_window import MiniModeWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    mini = MiniModeWindow(w)
    w.show()
    mini.show()
    for _ in range(6):
        app.processEvents()

    # P0-1: Stepper spacing >= 10px (minus.right + 10 <= spin.left, spin.right + 10 <= plus.left)
    w.adv_filters_btn.setChecked(True)
    w.adv_filters_btn.click()
    for _ in range(4):
        app.processEvents()
    # The main window no longer uses -/+ steppers (value chip + band slider, ParamSliders, a cores
    # dropdown); the chip's edit field must actually have room for its text.
    chip = w.quality_stepper.spin
    assert chip.lineEdit().width() >= 40, chip.lineEdit().geometry()
    assert chip.text() == "CRF 18"
    assert mini.engine.quality_stepper is w.quality_stepper      # the Mini window drives the same controls

    dlg = ThemeCustomizeDialog(w)
    dlg.show()
    for _ in range(4):
        app.processEvents()
    # Check visible gallery header stepper buttons (+ / -) on Themes tab
    assert dlg._save_preset_btn.isVisible() and dlg._delete_preset_btn.isVisible()
    assert dlg._save_preset_btn.geometry().right() + 6 <= dlg._delete_preset_btn.geometry().left()
    if dlg._preset_combo.isVisible():
        assert dlg._preset_combo.geometry().right() + 10 <= dlg._save_preset_btn.geometry().left()
    # Switch to Colors tab and expand Customize colors so _hex_edit and _remove_btn are visible before checking geometry
    dlg.select_appearance_tab("colors")
    dlg._set_customize_colors_expanded(True)
    for _ in range(4):
        app.processEvents()
    assert dlg._hex_edit.isVisible() and dlg._remove_btn.isVisible()
    assert dlg._hex_edit.geometry().right() + 10 <= dlg._remove_btn.geometry().left()
    dlg.close()

    # Right column viewport still fits content without horizontal clipping
    assert w.conv_scroll.viewport().width() >= w.conv_scroll_content.minimumSizeHint().width()

    # P0-2: 13-line Dolby Vision analysis text fits without vertical scrolling at default size
    # and does not change window, Files, Progress, or Controls height
    win_h0 = w.height()
    files_h0 = w.files_card.height()
    prog_h0 = w.progress_card.height()
    ctrl_h0 = w.controls_card.height()
    dv_13_lines = "<br>".join([f"Line {i}: Dolby Vision Profile 8.1 · Mastering display 0.005–1000 nits" for i in range(1, 14)])
    w.analysis_label.setText(dv_13_lines)
    for _ in range(6):
        app.processEvents()
    assert w.analysis_scroll.verticalScrollBar().maximum() == 0
    assert w.height() == win_h0
    assert w.files_card.height() == files_h0
    assert w.progress_card.height() == prog_h0
    assert w.controls_card.height() == ctrl_h0

    # P0-3: preview_label has 0 vertical contentsMargins and minimumHeight >= 200
    m = w.preview_label.contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0)
    assert w.preview_label.minimumHeight() >= 200

    # P0-4: speed_label is not in progress.body; stats appear inline in status_label with full tooltip
    assert w.speed_label.parent() is None
    assert w.pbar.minimumWidth() >= 160
    w.set_state("run", "Converting\u2026 42%")
    w.speed_label.setText("1.45x realtime \u00b7 18 fps \u00b7 6.2 MB/s \u00b7 ETA 12:30")
    for _ in range(4):
        app.processEvents()
    assert "Converting" not in w.status_label.text()
    assert "42%" in w.status_label.text()
    assert "ETA 12:30" in w.status_label.text()
    assert "Converting\u2026 42%" in w.status_label.toolTip()
    assert "18 fps" in w.status_label.toolTip()
    assert "6.2 MB/s" in w.status_label.toolTip()
    assert "18 fps" in w.pbar.toolTip()

    # P1-1: CPU cores & priority is inside Controls card; Additional section is gone; Advanced filters is in Output
    assert "additional" not in w._section_bodies
    assert w.controls_card.isAncestorOf(w.cores_panel_widget)
    assert w._section_bodies["output"].isAncestorOf(w.adv_filters_btn)
    assert w._section_bodies["output"].isAncestorOf(w.adv_filters_panel)

    # P1-2: Gamut mapping located in tonemap section, visible only on GPU curve and persisted in ConversionPreferences
    assert w._section_bodies["tonemap"].isAncestorOf(w.gamut_mapping_row_widget)
    assert w._section_bodies["tonemap"].isAncestorOf(w.gamut_combo)
    w.method_combo.setCurrentText("BT.2390 \u00b7 GPU tonemap")
    w._update_libplacebo_toggle_availability()
    assert not w.gamut_combo.isHidden()
    w.method_combo.setCurrentText("Hable \u00b7 CPU tonemap")
    w._update_libplacebo_toggle_availability()
    assert w.gamut_combo.isHidden()
    w.method_combo.setCurrentText("BT.2390 \u00b7 GPU tonemap")
    w._update_libplacebo_toggle_availability()

    ini_path = Path(tempfile.mkdtemp()) / "prefs_gamut.ini"
    s = QSettings(str(ini_path), QSettings.Format.IniFormat)
    s.setValue("conversion_preferences", json.dumps({"gamut_mode": "Relative"}))
    prefs = ConversionPreferences(
        s,
        w._conversion_preference_controls(),
        w._restore_output_folder,
        lambda: w.output_folder,
        lambda: w._on_bit_depth_changed(w.bit_depth_combo.currentText()),
        w,
    )
    prefs.restore()
    assert w.gamut_combo.currentText() == "Relative"

    mini.close()
    w.close()



def test_v24_8b_d1_d2_column_widths_steppers_and_status_label_no_clipping():
    """v24_8b D1 & D2:
    - D1: Across minimum, default (1080), and wide (1440) window widths, all steppers keep
      both gaps == 10px (minus.right + 10 <= spin.left, spin.right + 10 <= plus.left),
      stepper width >= 146px, and right column viewport.width() >= content.minimumSizeHint().width()
      with no horizontal scrollbar.
    - D2: Across minimum, default (1080), and wide (1440) window widths and roles (info, warn, good),
      the rendered text width measured with the real DemiBold/role font is <= status_label.width(),
      pbar.width() >= 160px, and pbar.isTextVisible() is False (no duplicate percentage)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    mw_text = (root / "hdr2sdr/ui/main_window.py").read_text(encoding="utf-8")
    assert "self.status_label.fontMetrics()" not in mw_text

    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QFont, QFontMetrics
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return

    from hdr2sdr.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show()
    w.adv_filters_btn.setChecked(True)
    w.adv_filters_btn.click()
    for _ in range(6):
        app.processEvents()

    assert w.pbar.isTextVisible() is False

    test_widths = (w.minimumWidth(), 1080, 1440)
    for win_w in test_widths:
        w.resize(win_w, w.height())
        for _ in range(6):
            app.processEvents()

        # D1: Right column viewport >= content minimumSizeHint and no horizontal scrollbar
        vp_w = w.conv_scroll.viewport().width()
        content_min_w = w.conv_scroll_content.minimumSizeHint().width()
        assert vp_w >= content_min_w, f"Viewport {vp_w} < content minimumSizeHint {content_min_w} at width {win_w}"
        assert w.conv_scroll.horizontalScrollBar().maximum() == 0

        # D1: the value controls never get squeezed below a usable width at any window width
        for ctrl, min_w in ((w.quality_stepper, 80), (w.cores_stepper, 100),
                            (w.brightness_stepper, 120), (w.saturation_stepper, 120),
                            (w.colortemperature_stepper, 120)):
            assert ctrl.width() >= min_w, f"{ctrl} width={ctrl.width()} < {min_w} at win_w={win_w}"

        # D2: Across roles info/warn/good (mapped via kind='run'/'paused'/'done')
        for kind, expected_role in (("run", "info"), ("paused", "warn"), ("done", "good")):
            w.set_state(kind, "Converting\u2026 42%")
            w.speed_label.setText("0.60x realtime \u00b7 18 fps \u00b7 4.1 MB/s \u00b7 ETA 12m 30s")
            for _ in range(4):
                app.processEvents()
            assert w.status_label.property("role") == expected_role
            txt = w.status_label.text()
            if kind == "run":
                assert "Converting" not in txt
                assert "42%" in txt
                assert "ETA 12m 30s" in txt
            elif kind == "paused":
                assert "Paused" in txt
                assert "42%" in txt
            else:
                assert "Converting\u2026 42%" in txt
            f_real = QFont(w.status_label.font())
            if f_real.pointSize() <= 0:
                f_real.setPointSize(9)
            f_real.setWeight(QFont.Weight.DemiBold)
            real_text_w = max(
                QFontMetrics(f_real).horizontalAdvance(txt),
                QFontMetrics(w.status_label.font()).horizontalAdvance(txt),
            )
            assert real_text_w <= w.status_label.width(), (
                f"Clipped status_label at win_w={win_w}, role={expected_role}: "
                f"text_w={real_text_w} > label_w={w.status_label.width()} ({txt!r})"
            )
            assert w.pbar.width() >= 160, f"pbar width {w.pbar.width()} < 160 at win_w={win_w}"

    w.close()


def test_v24_9_settings_redesign_structure_and_scoped_qss():
    """Pure-Python structural & AST verification of v24_9 Settings dialog redesign."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent

    # 1. Spec saved in docs/REDESIGN_SETTINGS_SPEC.md
    spec_path = root / "docs" / "REDESIGN_SETTINGS_SPEC.md"
    assert spec_path.is_file(), "docs/REDESIGN_SETTINGS_SPEC.md must exist"
    spec_text = spec_path.read_text(encoding="utf-8")
    assert "Hold to compare" in spec_text and "Glass & Shape" in spec_text

    # 2. Check theme_customize.py AST for required methods, attributes, and scoped setStyleSheet
    tc_path = root / "hdr2sdr" / "ui" / "dialogs" / "theme_customize.py"
    tc_src = tc_path.read_text(encoding="utf-8")
    tc_tree = ast.parse(tc_src)

    method_names = {
        node.name for node in ast.walk(tc_tree) if isinstance(node, ast.FunctionDef)
    }
    for req_method in (
        "select_section",
        "select_appearance_tab",
        "_on_hold_compare_pressed",
        "_on_hold_compare_released",
        "_revert_to_initial",
        "_request_close",
        "_prompt_unsaved_close",
        "closeEvent",
        "reject",
        "_apply",
        "_reset",
        "_refresh_preset_combo",
        "_on_preset_selected",
        "_build_look",
        "_live_apply",
        "_load_state",
        "_save_preset_as",
        "_rename_user_preset",
        "_duplicate_preset",
        "_delete_user_preset",
        "_export_presets_json",
        "_import_presets_json",
    ):
        assert req_method in method_names, f"Missing method {req_method} in ThemeCustomizeDialog"

    # Verify no unscoped widget-level setStyleSheet in theme_customize.py
    for node in ast.walk(tc_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setStyleSheet":
            # Allow self.setStyleSheet(...) and dlg.setStyleSheet(...)
            target = node.func.value
            if isinstance(target, ast.Name) and target.id in ("self", "dlg"):
                continue
            # All other setStyleSheet calls in theme_customize.py must contain '#' selector
            arg_str = ast.unparse(node.args[0]) if node.args else ""
            assert "#" in arg_str, f"Unscoped setStyleSheet at line {node.lineno}: {arg_str}"

    # 3. Check Ctrl+, shortcut in main_window.py
    mw_src = (root / "hdr2sdr" / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "Ctrl+," in mw_src and "settings_shortcut" in mw_src

    # 4. Check v24_9b fixes (T1-T3, U1-U6)
    assert "def _escape_mnemonic(" in tc_src
    assert "_escape_mnemonic(win.tr('Shape'))" in tc_src or '_escape_mnemonic(win.tr("Shape"))' in tc_src
    assert "self._preset_combo.hide()" in tc_src
    assert "self._customize_colors_btn.setChecked(False)" in tc_src
    assert "self._customize_colors_panel.setVisible(False)" in tc_src
    assert "body_row.addWidget(self._nav_card)" in tc_src
    assert "body_row.addWidget(self._content_card, 1)" in tc_src
    assert "body_row.addWidget(self._live_preview_card)" in tc_src
    assert tc_src.index("body_row.addWidget(self._content_card, 1)") < tc_src.index("body_row.addWidget(self._live_preview_card)")

    td_src = (root / "tests" / "test_theme_dialog.py").read_text(encoding="utf-8")
    assert "from PySide6.QtCore import QKeySequence" not in td_src
    assert "from PySide6.QtGui import" in td_src and "QKeySequence" in td_src

    cs_src = (root / "tools" / "capture_screens.py").read_text(encoding="utf-8")
    for s_key in ("general", "themes", "colors", "glass_shape", "about", "default", "min"):
        assert s_key in cs_src, f"Missing {s_key} in tools/capture_screens.py"
    assert "DEFAULT_WIDTH = 820" in tc_src and "DEFAULT_HEIGHT = 660" in tc_src
    assert "MIN_WIDTH = 760" in tc_src and "MIN_HEIGHT = 600" in tc_src
    assert "setFixedWidth(328)" not in tc_src
    assert "class _FlowLayout(" in tc_src


def test_v24_9c_ui_scaling_and_layout_polish():
    """Verify v24_9c UI scaling polish across MainWindow and ThemeCustomizeDialog:
    1) quality_mode_combo is placed in q_head_row so quality_slider/bitrate_slider get full row width.
    2) adv_filters_btn and _customize_colors_btn carry drawer_header=True with non-blinding checked QSS.
    3) pbar width is stabilized with a per-run high-water mark (_run_status_min_w) and >=35% row reservation.
    4) GlassButton lead_icon centers combined (icon + gap + text) and QSS adds left padding for lead_icon.
    5) RoundedComboBox draws a vector chevron when no external chevron_icon is set on the window.
    6) ThemeCustomizeDialog stacks Card/Button rounding sliders vertically, balances _FlowLayout rows,
       and renders a themed 16:11 preview canvas with a live summary card underneath."""
    from pathlib import Path
    from hdr2sdr.theme.theme_tokens import build_button_states_qss, build_tokens

    root = Path(__file__).resolve().parent.parent
    mw_src = (root / "hdr2sdr/ui/main_window.py").read_text(encoding="utf-8")
    tc_src = (root / "hdr2sdr/ui/dialogs/theme_customize.py").read_text(encoding="utf-8")
    btn_src = (root / "hdr2sdr/ui/widgets/buttons.py").read_text(encoding="utf-8")
    cmb_src = (root / "hdr2sdr/ui/widgets/combo.py").read_text(encoding="utf-8")

    # 1. quality_mode_combo in q_head_row
    assert "q_head_row.addWidget(self.quality_mode_combo)" in mw_src
    assert "qf.addWidget(self.quality_mode_combo)" not in mw_src

    # 2. drawer_header property and QSS
    assert 'self.adv_filters_btn.setProperty("drawer_header", True)' in mw_src
    assert 'self._customize_colors_btn.setProperty("drawer_header", True)' in tc_src
    for tname in ("light", "dark", "neon"):
        qss = build_button_states_qss(build_tokens(theme_name=tname))
        assert 'QPushButton[role="panel-toggle"][drawer_header="true"]:checked' in qss
        assert "QPushButton[lead_icon]" in qss

    # 3. Progress bar stabilization & auto-scroll on Advanced filters expand
    assert "_run_status_min_w" in mw_src
    assert "ensureWidgetVisible(self.saturation_slider" in mw_src

    # 4. GlassButton lead_icon combined centering & RoundedComboBox vector chevron
    assert "total_w = size + gap + text_w" in btn_src
    assert "drawPolyline" in cmb_src

    # 5. ThemeCustomizeDialog vertical rounding sliders, distribute_rows, and preview details frame
    assert "distribute_rows=True" in tc_src
    assert "previewDetailsFrame" in tc_src
    assert "swatchCustomDivider" in tc_src
    assert "dirtyStatusPill" in tc_src


def test_v24_9c_compact_conversion_layout():
    """Verify Part A: Compact Conversion layout in Pro mode and user settings."""
    from PySide6.QtWidgets import QApplication
    from hdr2sdr.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show()
    app.processEvents()

    # 1. Dynamic detailed description setting
    assert w.preset_hint_label.wordWrap()
    w.kind = "hdr"
    w._last_analysis_raw = '{"color_transfer": "smpte2084"}'
    for b in (w.preset_optimal_btn, w.preset_best_btn, w.preset_fast_btn):
        b.setEnabled(True)
    w._sync_preset_segment_state()
    assert w.preset_optimal_btn.isChecked()
    assert w.preset_hint_label.toolTip() != ""
    # By default show_detailed_conversion_info is False, so hint label is hidden
    assert not w.preset_hint_label.isVisible()
    # When enabled, hint label is shown with the detailed description
    w.show_detailed_conversion_info = True
    w._sync_preset_segment_state()
    assert w.preset_hint_label.isVisible()
    assert w.preset_hint_label.text() != ""

    # 2. Paired row for Encoder + Bit depth
    assert hasattr(w, "enc_grid")
    assert w._section_bodies["encoding"].isAncestorOf(w.encoder_combo)
    assert w._section_bodies["encoding"].isAncestorOf(w.bit_depth_combo)
    assert w._section_bodies["encoding"].isAncestorOf(w.backend_combo)

    # Adaptive switching between 2-column and 1-column layout
    w._update_conversion_adaptive_layout(600)
    assert w._enc_is_two_col is True
    assert w._q_is_beside is True
    assert w.q_head_row.indexOf(w.quality_stepper) != -1

    w._update_conversion_adaptive_layout(300)
    assert w._enc_is_two_col is False
    assert w._q_is_beside is False
    assert w.q_sub_row.indexOf(w.quality_stepper) != -1

    # 3. HDR tone mapping order: Method -> Gamut mapping -> tm_toggles_layout
    assert hasattr(w, "tm_toggles_layout")
    assert w._section_bodies["tonemap"].isAncestorOf(w.method_combo)
    assert w._section_bodies["tonemap"].isAncestorOf(w.dv_rpu_chk)
    assert w._section_bodies["tonemap"].isAncestorOf(w.peak_detect_chk)
    assert w._section_bodies["tonemap"].isAncestorOf(w.gamut_mapping_row_widget)
    assert w._section_bodies["tonemap"].isAncestorOf(w.gamut_combo)

    tm_layout = w._section_bodies["tonemap"].layout()
    items = [tm_layout.itemAt(i).widget() or tm_layout.itemAt(i).layout() for i in range(tm_layout.count())]
    m_idx = items.index(w.method_combo)
    g_idx = items.index(w.gamut_mapping_row_widget)
    t_idx = items.index(w.tm_toggles_layout)
    assert m_idx < g_idx < t_idx

    # 4. Dolby Vision RPU auto-detection and manual override
    dovi_stream = {
        "color_transfer": "smpte2084",
        "side_data_list": [
            {
                "side_data_type": "DOVI configuration record",
                "dv_profile": 8,
                "dv_level": 6,
                "rpu_present_flag": 1,
                "bl_present_flag": 1,
            }
        ]
    }
    w._apply_analysis_document({"streams": [dovi_stream]})
    assert w.dv_rpu_chk.isChecked()
    assert w._dv_rpu_auto_enabled
    assert "Recommended settings" not in w.analysis_label.text()

    # Reset on non-DV or clearing source
    sdr_stream = {"color_transfer": "bt709"}
    w._set_source("plain_sdr.mp4", analyze=False)
    w._apply_analysis_document({"streams": [sdr_stream]})
    assert not w.dv_rpu_chk.isChecked()
    assert not w._dv_rpu_auto_enabled

    # Manual override preserves user toggle
    w.dv_rpu_chk.click()
    assert w._dv_rpu_user_override
    assert w.dv_rpu_chk.isChecked()
    w._apply_analysis_document({"streams": [sdr_stream]})
    assert w.dv_rpu_chk.isChecked()

    # Loading a new file resets override
    w._set_source("another.mp4", analyze=False)
    assert not w._dv_rpu_user_override
    assert not w.dv_rpu_chk.isChecked()

    # 5. Reliable bitrate/size estimate
    w.duration = 0.0
    w.src_width = None
    w.src_height = None
    w.update_bitrate_estimate()
    assert "for this source" not in w.bitrate_label.text()

    # 6. Advanced filters shortened labels and active filter count in header
    assert w.brightness_label.text() == w.tr("Brightness")
    assert w.saturation_label.text() == w.tr("Saturation")
    assert w.colortemperature_label.text() == w.tr("Colour temperature")
    assert "(-20 to +20" in w.brightness_label.toolTip()
    assert "(-20 to +20" in w.saturation_label.toolTip()
    assert "3000K-10000K" in w.colortemperature_label.toolTip()

    # Header active filter count
    assert w._active_advanced_filters_count() == 0
    assert "(" not in w.adv_filters_btn.text()
    w.brightness_spin.setValue(5)
    assert w._active_advanced_filters_count() == 1
    assert "(1)" in w.adv_filters_btn.text()
    w.colortemperature_spin.setValue(5000)
    assert w._active_advanced_filters_count() == 2
    assert "(2)" in w.adv_filters_btn.text()
    w.brightness_spin.setValue(0)
    w.colortemperature_spin.setValue(6500)
    assert w._active_advanced_filters_count() == 0
    assert "(" not in w.adv_filters_btn.text()

    w.close()


def test_v24_9c_progress_compact_and_settings_theme_cycle():
    """Verify Part B: Compact Progress row, tooltip-only speed/resource stats,
    and robustness across Settings opening, theme switching (Light/Dark/Neon), and closing."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontMetrics
    from hdr2sdr.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1080, 1050)
    w.show()
    for _ in range(4):
        app.processEvents()

    # 1. Inline status: short percentage + ETA only, no 'Converting' inline
    w.set_state("run", "Converting\u2026 15%")
    w.pbar.setValue(150)
    from hdr2sdr.core.progress import ProgressStats
    w._latest_progress_stats = ProgressStats(pct=15.0, fps=24.0, speed_x=1.2, eta_sec=5760, out_sec=120.0, mbps=8.5)
    w._update_inline_progress_status()
    for _ in range(4):
        app.processEvents()

    txt = w.status_label.text()
    assert "Converting" not in txt
    assert "15%" in txt
    assert "ETA 1h 36m 00s" in txt
    # Speed and FPS in tooltips of both label and pbar
    tip = w.status_label.toolTip()
    assert "Converting\u2026 15%" in tip
    assert "24" in tip and "fps" in tip
    assert "1.2" in tip and "realtime" in tip
    assert w.pbar.toolTip() == tip

    # 2. Sample resources goes to tooltip, resource_label stays hidden (when HAVE_PSUTIL is True)
    from hdr2sdr.ui.main_window import HAVE_PSUTIL
    w._latest_resource_text = "This process: 35% CPU · System: 48% CPU · GPU: 62%"
    w._update_inline_progress_status()
    if HAVE_PSUTIL:
        assert w.resource_label.isHidden()
    assert "GPU: 62%" in w.status_label.toolTip()
    assert "GPU: 62%" in w.pbar.toolTip()

    # 3. Settings interaction & Theme switching (Light -> Dark -> Neon)
    from hdr2sdr.theme.look import load_slots
    slots = load_slots(w.i18n.settings)
    assert w.pbar.width() >= 180

    for theme_name in ("light", "dark", "neon"):
        w._set_look(slots[theme_name])
        for _ in range(4):
            app.processEvents()
        # Verify status label is never clipped
        f_real = QFont(w.status_label.font())
        f_real.setWeight(QFont.Weight.DemiBold)
        real_text_w = max(
            QFontMetrics(f_real).horizontalAdvance(w.status_label.text()),
            QFontMetrics(w.status_label.font()).horizontalAdvance(w.status_label.text()),
        )
        assert real_text_w <= w.status_label.width(), f"Clipped in theme {theme_name}: {real_text_w} > {w.status_label.width()}"
        assert w.pbar.width() >= 180, f"Crushed pbar in theme {theme_name}: {w.pbar.width()} < 180"

    w.close()


def test_v24_9c_conversion_bounds_and_adaptive_scaling():
    """Verify v24_9c Conversion card layout:
    1) Actual geometry() and contentsRect() of all visible widgets across window resizing and theme changes.
    2) No horizontal clipping beyond conv_scroll.viewport() and no widget overlaps.
    3) Adaptive transition between 2-column mode and 1-column mode when real container narrows/widens.
    4) Settings dialog open/close lifecycle maintains exact geometry stability.
    5) Light, Dark, and Neon theme switching verified.
    6) Vertical scroll reachability to bottom section.
    7) Full bounds check across QT_SCALE_FACTOR=1.0, 1.5, and 2.0 in clean subprocesses using the same runtime.
    """
    import os
    import sys
    import subprocess
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPoint, QRect
    from hdr2sdr.ui.main_window import MainWindow
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog
    from hdr2sdr.theme.look import load_slots

    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show()
    for _ in range(4):
        app.processEvents()

    targets = [
        ("encoder_combo", w.encoder_combo),
        ("bit_depth_combo", w.bit_depth_combo),
        ("quality_mode_combo", w.quality_mode_combo),
        ("quality_slider", getattr(w, "quality_slider", None)),
        ("bitrate_label", w.bitrate_label),
        ("gamut_combo", w.gamut_combo),
        ("dv_rpu_chk", w.dv_rpu_chk),
        ("peak_detect_chk", w.peak_detect_chk),
        ("desat_chk", w.desat_chk),
    ]

    vp = w.conv_scroll.viewport()

    # 1. Settings dialog open/close lifecycle preserves geometry exactly
    geo_before = {name: widget.geometry() for name, widget in targets if widget and widget.isVisible()}
    dlg = ThemeCustomizeDialog(w)
    dlg.show()
    for _ in range(4):
        app.processEvents()
    geo_during = {name: widget.geometry() for name, widget in targets if widget and widget.isVisible()}
    for name in geo_before:
        assert geo_before[name] == geo_during[name], f"{name} geometry shifted during Settings dialog open"
    dlg.reject()
    for _ in range(4):
        app.processEvents()
    geo_after = {name: widget.geometry() for name, widget in targets if widget and widget.isVisible()}
    for name in geo_before:
        assert geo_before[name] == geo_after[name], f"{name} geometry corrupted after closing Settings dialog"

    # 2. Geometry check across window sizes
    for win_w, win_h in [(1080, 1050), (920, 960), (1400, 1050)]:
        w.resize(win_w, win_h)
        for _ in range(4):
            app.processEvents()
        assert w.width() == win_w, f"Window width {w.width()} != requested {win_w}"
        vp_w = vp.width()
        assert vp_w > 0, "Viewport has non-positive width"
        for name, widget in targets:
            if widget is None or not widget.isVisible():
                continue
            assert widget.geometry().width() > 0, f"{name} zero width"
            assert widget.geometry().height() > 0, f"{name} zero height"
            assert widget.contentsRect().width() > 0, f"{name} zero contentsRect width"
            pt = widget.mapTo(vp, QPoint(0, 0))
            rect = QRect(pt, widget.size())
            assert rect.left() >= 0, f"{name} left ({rect.left()}) < 0"
            assert rect.right() <= vp_w + 4, f"{name} right ({rect.right()}) > viewport width ({vp_w})"

    # 3. Theme switching check (Light, Dark, Neon)
    slots = load_slots(w.i18n.settings)
    for theme_name in ("light", "dark", "neon"):
        w._set_look(slots[theme_name])
        for _ in range(4):
            app.processEvents()
        vp_w = vp.width()
        for name, widget in targets:
            if widget is None or not widget.isVisible():
                continue
            pt = widget.mapTo(vp, QPoint(0, 0))
            rect = QRect(pt, widget.size())
            assert rect.left() >= 0 and rect.right() <= vp_w + 4, f"{name} overflows viewport in theme {theme_name}"

    # 4. Adaptive layout transitions via real container narrowing (no fictitious parameter)
    # Wide container -> 2-column mode
    wide_w = max(520, getattr(w, "_q_row_threshold", 450) + 20)
    w.conv_scroll.resize(wide_w, w.conv_scroll.height())
    for _ in range(4):
        app.processEvents()
    assert w._enc_is_two_col is True
    assert w._q_is_beside is True
    enc_r2 = QRect(w.encoder_combo.mapTo(vp, QPoint(0, 0)), w.encoder_combo.size())
    bd_r2 = QRect(w.bit_depth_combo.mapTo(vp, QPoint(0, 0)), w.bit_depth_combo.size())
    assert not enc_r2.intersects(bd_r2), f"2-column combos overlap: {enc_r2} intersects {bd_r2}"
    assert enc_r2.top() == bd_r2.top(), "2-column combos should be on the same horizontal row"
    assert enc_r2.right() < bd_r2.left(), "2-column encoder combo must be strictly to the left of bit depth combo"

    # Narrow container -> 1-column mode
    w.conv_scroll.resize(250, w.conv_scroll.height())
    for _ in range(4):
        app.processEvents()
    assert w._enc_is_two_col is False
    assert w._q_is_beside is False
    enc_r1 = QRect(w.encoder_combo.mapTo(vp, QPoint(0, 0)), w.encoder_combo.size())
    bd_r1 = QRect(w.bit_depth_combo.mapTo(vp, QPoint(0, 0)), w.bit_depth_combo.size())
    assert not enc_r1.intersects(bd_r1), f"1-column combos overlap: {enc_r1} intersects {bd_r1}"
    assert bd_r1.top() > enc_r1.bottom(), "1-column combos should be stacked vertically"

    # Restore wide container
    w.conv_scroll.resize(wide_w, w.conv_scroll.height())
    for _ in range(4):
        app.processEvents()
    assert w._enc_is_two_col is True
    assert w._q_is_beside is True

    # 5. Scroll reachability: scroll to bottom and verify bottom-most section enters viewport
    w.conv_scroll.resize(400, 250)
    for _ in range(4):
        app.processEvents()
    sb = w.conv_scroll.verticalScrollBar()
    assert sb is not None
    assert sb.maximum() > 0, "Expected non-zero scroll maximum for constrained scroll area height"
    sb.setValue(sb.maximum())
    for _ in range(4):
        app.processEvents()
    cont_pt = w.container_combo.mapTo(vp, QPoint(0, 0))
    cont_rect = QRect(cont_pt, w.container_combo.size())
    assert cont_rect.top() >= 0, f"Bottom container combo top ({cont_rect.top()}) above viewport"
    assert cont_rect.bottom() <= vp.height() + 4, f"Bottom container combo bottom ({cont_rect.bottom()}) below viewport ({vp.height()})"

    w.close()

    # 6. Verify clean startup, runtime integrity, and bounds across QT_SCALE_FACTOR=1.0, 1.5, 2.0
    sub_code = (
        "import os\n"
        "import sys\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from PySide6.QtCore import QPoint, QRect\n"
        "from hdr2sdr.ui.main_window import MainWindow\n"
        "from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog\n"
        "from hdr2sdr.theme.look import load_slots\n"
        "app = QApplication.instance() or QApplication([])\n"
        "w = MainWindow()\n"
        "w.show()\n"
        "for _ in range(4):\n"
        "    app.processEvents()\n"
        "dlg = ThemeCustomizeDialog(w)\n"
        "dlg.show()\n"
        "for _ in range(2):\n"
        "    app.processEvents()\n"
        "dlg.reject()\n"
        "for _ in range(2):\n"
        "    app.processEvents()\n"
        "vp = w.conv_scroll.viewport()\n"
        "targets = [\n"
        "    ('encoder_combo', w.encoder_combo),\n"
        "    ('bit_depth_combo', w.bit_depth_combo),\n"
        "    ('quality_mode_combo', w.quality_mode_combo),\n"
        "    ('quality_slider', getattr(w, 'quality_slider', None)),\n"
        "    ('bitrate_label', w.bitrate_label),\n"
        "    ('gamut_combo', w.gamut_combo),\n"
        "    ('dv_rpu_chk', w.dv_rpu_chk),\n"
        "    ('peak_detect_chk', w.peak_detect_chk),\n"
        "    ('desat_chk', w.desat_chk),\n"
        "]\n"
        "for win_w, win_h in [(1080, 1050), (920, 960), (1400, 1050)]:\n"
        "    w.resize(win_w, win_h)\n"
        "    for _ in range(3):\n"
        "        app.processEvents()\n"
        "    vp_w = vp.width()\n"
        "    for name, widget in targets:\n"
        "        if widget is None or not widget.isVisible():\n"
        "            continue\n"
        "        pt = widget.mapTo(vp, QPoint(0, 0))\n"
        "        rect = QRect(pt, widget.size())\n"
        "        assert rect.left() >= 0 and rect.right() <= vp_w + 4\n"
        "w.conv_scroll.resize(450, w.conv_scroll.height())\n"
        "for _ in range(3):\n"
        "    app.processEvents()\n"
        "assert w._enc_is_two_col is True\n"
        "enc_r = QRect(w.encoder_combo.mapTo(vp, QPoint(0, 0)), w.encoder_combo.size())\n"
        "bd_r = QRect(w.bit_depth_combo.mapTo(vp, QPoint(0, 0)), w.bit_depth_combo.size())\n"
        "assert not enc_r.intersects(bd_r)\n"
        "assert enc_r.right() < bd_r.left()\n"
        "w.conv_scroll.resize(250, w.conv_scroll.height())\n"
        "for _ in range(3):\n"
        "    app.processEvents()\n"
        "assert w._enc_is_two_col is False\n"
        "enc_r1 = QRect(w.encoder_combo.mapTo(vp, QPoint(0, 0)), w.encoder_combo.size())\n"
        "bd_r1 = QRect(w.bit_depth_combo.mapTo(vp, QPoint(0, 0)), w.bit_depth_combo.size())\n"
        "assert not enc_r1.intersects(bd_r1)\n"
        "assert bd_r1.top() > enc_r1.bottom()\n"
        "w.conv_scroll.resize(400, 250)\n"
        "for _ in range(3):\n"
        "    app.processEvents()\n"
        "sb = w.conv_scroll.verticalScrollBar()\n"
        "assert sb.maximum() > 0\n"
        "sb.setValue(sb.maximum())\n"
        "for _ in range(3):\n"
        "    app.processEvents()\n"
        "c_pt = w.container_combo.mapTo(vp, QPoint(0, 0))\n"
        "c_rect = QRect(c_pt, w.container_combo.size())\n"
        "assert c_rect.top() >= 0 and c_rect.bottom() <= vp.height() + 4\n"
        "w.close()\n"
    )
    for s in ("1.0", "1.5", "2.0"):
        env = {
            **os.environ,
            "QT_SCALE_FACTOR": s,
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONPATH": os.pathsep.join(sys.path),
        }
        res = subprocess.run([sys.executable, "-c", sub_code], env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"Scale {s} failed:\n{res.stderr}\n{res.stdout}"


