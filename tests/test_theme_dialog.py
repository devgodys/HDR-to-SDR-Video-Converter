"""The theme panel: frameless with window controls, Undo changes goes back to
the theme applied at open time (dialog stays open), Reset is something else,
the button colour is edited inline (no separate colour window)."""
import copy

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton


def pump(n=10):
    for _ in range(n):
        QApplication.instance().processEvents()


@pytest.fixture(scope="module")
def window():
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    pump()
    from hdr2sdr.theme.look import builtin_preset
    w._set_look(builtin_preset("neon"), persist=False)
    pump()
    yield w
    w.close()


def state(w):
    c = w._get_theme_custom(w.theme_name)
    return dict(stops=c["stops"], direction=c["direction"], intensity=c["intensity"], effect=c["effect"],
                button_custom=c["button_custom"], ui_style=w.ui_style, neon=w.neon_effect,
                indigo=w.tokens["INDIGO"])


@pytest.fixture
def dialog(window):
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog
    d = ThemeCustomizeDialog(window, window.theme_name)
    d.show()
    pump(15)
    yield d
    d.reject()
    pump(5)


def test_frameless_without_in_window_heading(dialog):
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not [l for l in dialog.findChildren(QLabel) if l.text() == dialog.windowTitle()]
    assert dialog._close_btn and dialog._minimize_btn
    texts = [b.text() for b in dialog.findChildren(QPushButton)]
    assert len([t for t in texts if t]) >= 4


def test_undo_goes_back_and_keeps_dialog_open(window, dialog):
    before = copy.deepcopy(state(window))
    assert not dialog._undo_btn.isEnabled()
    for _ in range(3):
        dialog._surprise_me()
        pump(4)
    dialog._dir_slider.setValue(45)
    dialog._glass_toggle.setChecked(not dialog._glass_toggle.isChecked())
    pump(6)
    assert copy.deepcopy(state(window)) != before and dialog._undo_btn.isEnabled()
    dialog._undo_btn.click()
    pump(15)
    assert copy.deepcopy(state(window)) == before
    assert dialog.isVisible() and not dialog._undo_btn.isEnabled()
    assert dialog._dir_slider.value() == before["direction"]


def test_reset_to_default_is_not_undo_and_can_itself_be_undone(window, dialog):
    dialog._surprise_me()
    pump(6)
    dialog._apply()                                   # something non-default is now the applied look
    pump(6)
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog
    dialog2 = ThemeCustomizeDialog(window)
    dialog2.show()
    pump(10)
    try:
        before = copy.deepcopy(state(window))
        dialog2._reset()
        pump(8)
        assert dialog2._undo_btn.isEnabled()
        dialog2._undo_btn.click()
        pump(10)
        assert copy.deepcopy(state(window)) == before
    finally:
        dialog2.reject()
        pump(5)


def test_close_button_discards_unapplied_changes(window, dialog):
    before = copy.deepcopy(state(window))
    dialog._surprise_me()
    pump(4)
    dialog._close_btn.click()
    pump(10)
    assert not dialog.isVisible() and copy.deepcopy(state(window)) == before


def test_button_colour_uses_the_inline_editor(window, dialog, monkeypatch):
    import hdr2sdr.ui.dialogs.theme_customize as mod
    assert not hasattr(mod, "QColorDialog")                       # no separate colour window any more
    stops = list(dialog._stops)
    dialog._button_swatch_btn.click()
    pump()
    assert dialog._editing_button and not dialog._button_custom   # selecting alone changes nothing
    dialog._hex_edit.setText("#FF3366")
    dialog._hex_edit.editingFinished.emit()
    pump()
    assert dialog._button_custom and dialog._button_hex.lower() == "#ff3366" and dialog._stops == stops
    dialog._swatch_buttons[1].click()                            # back to a gradient stop
    pump()
    assert not dialog._editing_button and dialog._button_hex.lower() == "#ff3366"
    dialog._reset_button_colour()
    pump()
    assert not dialog._button_custom


def test_builtin_presets_are_listed_and_picking_one_applies_it(window, dialog):
    names = [dialog._preset_combo.itemText(i) for i in range(dialog._preset_combo.count())]
    assert names[:3] == ["Light", "Dark", "Neon"]
    dialog._preset_combo.setCurrentIndex(1)
    dialog._preset_combo.activated.emit(1)             # what the popup does when a row is clicked
    pump(6)
    assert window.look["base"] == "dark" and window.look["stops"] is None and not window.look["neon_effect"]
    assert dialog._preset_combo.currentText() == "Dark"
    dialog._preset_combo.activated.emit(2)             # Neon
    pump(6)
    assert window.look["neon_effect"] and window.look["stops"] and window.theme_name == "neon"
    dialog._glass_toggle.setChecked(True)              # tweaked: still shows the preset it started from
    pump(6)
    assert dialog._preset_combo.currentText() == "Neon"


def test_language_changes_immediately(window, dialog):
    idx = dialog._lang_combo.findData("uk")
    dialog._lang_combo.setCurrentIndex(idx)
    dialog._lang_combo.activated.emit(idx)
    pump(6)
    assert window.i18n.language == "uk"
    texts = [l.text() for l in dialog.findChildren(QLabel)]
    assert "Presets" not in texts and "Пресети" in texts      # the window itself follows
    dialog._lang_combo.activated.emit(dialog._lang_combo.findData("system"))
    pump(6)
    assert window.i18n.language != "uk"


def test_apply_persists_the_shared_look(window, dialog):
    from hdr2sdr.theme.look import load_look
    dialog._preset_combo.activated.emit(2)             # Neon
    pump(6)
    dialog._apply()
    pump(6)
    saved = load_look(window.i18n.settings)
    assert saved["neon_effect"] and saved["stops"] and saved["base"] == "dark"


def test_header_button_and_presets_share_each_presets_saved_look(window, dialog):
    from hdr2sdr.theme.look import builtin_preset, load_slots, save_slots
    slots = load_slots(window.i18n.settings)
    slots["dark"]["stops"], slots["dark"]["intensity"] = ["#102030", "#a0b0c0"], 55
    save_slots(window.i18n.settings, slots)
    dialog._refresh_preset_combo()
    dialog._preset_combo.activated.emit(1)             # Dark -> its saved colours, not the factory ones
    pump(6)
    assert window.look["stops"] == ["#102030", "#a0b0c0"]
    dialog._apply()
    pump(6)
    window._set_look(builtin_preset("light"), persist=False)
    window.mode_btn.click()                            # light -> dark: same saved look again
    pump(6)
    assert window.theme_name == "dark" and window.look["stops"] == ["#102030", "#a0b0c0"]


# ==============================================================================
# v24_9 Settings / ThemeCustomizeDialog Redesign Test Suite
# ==============================================================================

def test_v24_9_sections_and_appearance_subtabs_navigation(window, dialog):
    """1. Switching General / Appearance / About sections and Themes / Colors / Glass & Shape sub-tabs."""
    # Default opens on Appearance -> Themes
    assert dialog.current_section == "appearance"
    assert dialog._section_stack.currentIndex() == 1
    assert dialog.current_appearance_tab == "themes"
    assert dialog._appearance_stack.currentIndex() == 0

    # U1: Glass & Shape button has escaped ampersand (&&) so Qt does not consume & as mnemonic
    assert dialog._tab_glass_shape_btn.text() == "Shape"

    # U3: _preset_combo is hidden; save/delete buttons are hidden per latest redesign
    assert not dialog._preset_combo.isVisible()
    assert not dialog._save_preset_btn.isVisible()
    assert not dialog._delete_preset_btn.isVisible()
    assert not dialog._save_as_new_btn.isVisible()
    assert dialog._preset_cards and all(c.menu_btn.property("icon_kind") == "dots" for c in dialog._preset_cards)

    # Switch Appearance sub-tabs (U4: Customize colors collapsed by default)
    dialog.select_appearance_tab("colors")
    pump(5)
    assert dialog.current_appearance_tab == "colors"
    assert dialog._appearance_stack.currentIndex() == 1
    assert dialog._subtab_buttons["colors"].isChecked()
    assert not dialog._subtab_buttons["themes"].isChecked()
    assert not dialog._customize_colors_btn.isChecked()
    assert not dialog._customize_colors_panel.isVisible()

    dialog.select_appearance_tab("glass_shape")
    pump(5)
    assert dialog.current_appearance_tab == "glass_shape"
    assert dialog._appearance_stack.currentIndex() == 2
    assert dialog._subtab_buttons["glass_shape"].isChecked()

    # Switch top-level sections: General, Tools, About, back to Appearance (U5)
    assert list(dialog._nav_buttons.keys()) == ["general", "appearance", "tools", "about"]
    dialog.select_section("general")
    pump(5)
    assert dialog.current_section == "general"
    assert dialog._section_stack.currentIndex() == 0
    assert dialog._nav_buttons["general"].isChecked()
    assert not dialog._nav_buttons["appearance"].isChecked()
    assert dialog._lang_combo.isVisible()
    assert dialog._skip_mini_toggle.isVisible()
    assert dialog._keep_awake_toggle.isVisible()
    assert dialog._keep_display_toggle.isVisible()

    dialog.select_section("tools")
    pump(5)
    assert dialog.current_section == "tools"
    assert dialog._section_stack.currentIndex() == 2
    assert dialog._nav_buttons["tools"].isChecked()
    assert dialog.tools_channel_btn.isVisible()
    assert dialog.install_btn.isVisible()
    assert dialog.tools_channel_btn is not dialog.install_btn

    dialog.select_section("about")
    pump(5)
    assert dialog.current_section == "about"
    assert dialog._section_stack.currentIndex() == 3
    assert dialog._nav_buttons["about"].isChecked()
    from hdr2sdr.runtime.updates import APP_VERSION
    assert APP_VERSION in dialog._about_version_label.text()
    assert dialog._about_kofi_btn.isVisible()
    assert dialog._about_activity_btn.isVisible()

    dialog.select_section("appearance")
    dialog.select_appearance_tab("themes")
    pump(5)
    assert dialog.current_section == "appearance"
    assert dialog.current_appearance_tab == "themes"


def test_v24_9_dirty_status_and_revert_restores_look_and_radii(window, dialog):
    """2 & 3. Clean status -> edit -> Unsaved changes + Revert active -> Revert restores look & radii and keeps dialog open."""
    from hdr2sdr.theme.theme_tokens import button_radius, card_radius

    before_state = copy.deepcopy(state(window))
    before_card_r = card_radius()
    before_btn_r = button_radius()

    assert not dialog.is_dirty()
    assert not dialog._revert_btn.isEnabled()
    assert dialog._dirty_status_label.text() == window.tr("No unsaved changes")

    # Modify look and radii
    dialog._surprise_me()
    dialog._card_radius_slider.setValue(10 if before_card_r != 10 else 18)
    dialog._btn_radius_slider.setValue(6 if before_btn_r != 6 else 14)
    pump(8)

    assert dialog.is_dirty()
    assert dialog._revert_btn.isEnabled()
    assert dialog._dirty_status_label.text() == window.tr("Unsaved changes")
    assert (copy.deepcopy(state(window)) != before_state) or (card_radius() != before_card_r)

    # Click Revert (_undo_btn / _revert_btn)
    dialog._revert_btn.click()
    pump(12)

    assert dialog.isVisible()
    assert not dialog.is_dirty()
    assert not dialog._revert_btn.isEnabled()
    assert dialog._dirty_status_label.text() == window.tr("No unsaved changes")
    assert copy.deepcopy(state(window)) == before_state
    assert card_radius() == before_card_r
    assert button_radius() == before_btn_r


def test_v24_9_close_confirmation_discard_save_cancel(window):
    """4. Closing with unsaved changes: Cancel keeps open, Discard rolls back win.look & radii, Save persists."""
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog
    from hdr2sdr.theme.look import load_look
    from hdr2sdr.theme.theme_tokens import button_radius, card_radius

    # Part A: Cancel keeps dialog open, Discard rolls back
    d1 = ThemeCustomizeDialog(window, window.theme_name)
    d1.show()
    pump(10)
    orig_look = copy.deepcopy(window.look)
    orig_cr = card_radius()

    d1._surprise_me()
    d1._card_radius_slider.setValue(8 if orig_cr != 8 else 20)
    pump(6)
    assert d1.is_dirty()

    # Cancel on close keeps dialog open and dirty
    d1._unsaved_close_handler = lambda _dlg: "cancel"
    d1._close_btn.click()
    pump(6)
    assert d1.isVisible() and d1.is_dirty()

    # Discard on close rolls back look and radii and closes dialog
    d1._unsaved_close_handler = lambda _dlg: "discard"
    d1._close_btn.click()
    pump(10)
    assert not d1.isVisible()
    assert window.look == orig_look
    assert card_radius() == orig_cr

    # Part B: Save on close saves look and closes dialog
    d2 = ThemeCustomizeDialog(window, window.theme_name)
    d2.show()
    pump(10)
    d2._surprise_me()
    pump(6)
    edited_look = copy.deepcopy(window.look)
    assert d2.is_dirty()

    d2._unsaved_close_handler = lambda _dlg: "save"
    d2._close_btn.click()
    pump(10)
    assert not d2.isVisible()
    assert window.look == edited_look
    saved = load_look(window.i18n.settings)
    assert saved["stops"] == edited_look["stops"]


def test_v24_9_hold_to_compare_switches_preview_snapshot(window, dialog):
    """5. Hold to compare shows initial applied snapshot while pressed and returns to live snapshot on release."""
    dialog._capture_live_snapshot()
    pump(5)
    initial_key = dialog._applied_snapshot_pixmap.cacheKey()

    dialog._surprise_me()
    pump(6)
    dialog._capture_live_snapshot()
    pump(5)
    live_key = dialog._live_snapshot_pixmap.cacheKey()
    assert live_key != initial_key

    # Press Hold to compare
    dialog._hold_compare_btn.pressed.emit()
    pump(4)
    assert dialog._holding_compare is True
    assert dialog._preview_Showing_applied is True

    # Release Hold to compare
    dialog._hold_compare_btn.released.emit()
    pump(4)
    assert dialog._holding_compare is False
    assert dialog._preview_Showing_applied is False


def test_v24_9_user_preset_save_rename_duplicate_delete_and_json_io(window, dialog, tmp_path):
    """6. Save as new / Rename / Duplicate / Delete user preset + Import/Export JSON update list and QSettings."""
    from hdr2sdr.theme.look import load_presets, save_presets

    settings = window.i18n.settings
    orig_presets = copy.deepcopy(load_presets(settings))
    try:
        save_presets(settings, [])
        dialog._refresh_preset_combo()
        pump(4)

        # Save as new
        dialog._surprise_me()
        pump(4)
        dialog._save_preset_as(name="My Custom Look")
        pump(6)
        names = [p["name"] for p in load_presets(settings)]
        assert "My Custom Look" in names
        assert dialog._preset_combo.currentText() == "My Custom Look"
        assert not dialog._modified_badge.isVisible()

        # Modifying look updates modified state
        dialog._dir_slider.setValue((dialog._dir_slider.value() + 30) % 360)
        pump(6)
        assert not dialog._modified_badge.isHidden() or any(c.is_modified for c in dialog._preset_cards if c.is_selected)

        # Rename user preset
        dialog._rename_user_preset(old_name="My Custom Look", new_name="Renamed Look")
        pump(6)
        names = [p["name"] for p in load_presets(settings)]
        assert "Renamed Look" in names and "My Custom Look" not in names
        assert dialog._preset_combo.currentText() == "Renamed Look"

        # Duplicate preset
        dialog._duplicate_preset("Renamed Look")
        pump(6)
        names = [p["name"] for p in load_presets(settings)]
        assert "Renamed Look (Copy)" in names

        # Export JSON & Import JSON
        json_file = tmp_path / "presets_export.json"
        exported_count = dialog._export_presets_json(file_path=str(json_file))
        assert exported_count == 2 and json_file.is_file()

        # Delete both user presets
        dialog._delete_user_preset("Renamed Look (Copy)")
        dialog._delete_user_preset("Renamed Look")
        pump(6)
        assert load_presets(settings) == []

        # Re-import from JSON
        imported_count = dialog._import_presets_json(file_path=str(json_file))
        pump(6)
        assert imported_count == 2
        names_after = [p["name"] for p in load_presets(settings)]
        assert "Renamed Look" in names_after and "Renamed Look (Copy)" in names_after
    finally:
        save_presets(settings, orig_presets)
        dialog._refresh_preset_combo()


def test_v24_9_no_horizontal_scrollbar_or_clipping_and_shortcut_size(window, dialog):
    """7, 8 & D1-D3. Dialog 1000x660 default / 900x600 min, no horizontal scrollbar or right-edge
    clipping across min/default/wide sizes, all child widgets mapTo(viewport).right() <= viewport.width(),
    button/tab/label text fits within width - padding (including left navigation), and Ctrl+, shortcut works."""
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QFontMetrics, QKeySequence
    from PySide6.QtWidgets import QWidget

    # Check minimum (760x600) and default (820x660) dimensions (D1)
    assert dialog.minimumWidth() >= 760
    assert dialog.minimumHeight() >= 600
    assert dialog.DEFAULT_WIDTH == 820
    assert dialog.DEFAULT_HEIGHT == 660

    # Check shortcut Ctrl+, exists on MainWindow
    assert hasattr(window, "settings_shortcut")
    assert window.settings_shortcut.key() == QKeySequence("Ctrl+,")

    # Live window preview and Save as new are removed/hidden per latest UI redesign
    dialog.resize(dialog.DEFAULT_WIDTH, dialog.DEFAULT_HEIGHT)
    pump(8)
    assert 136 <= dialog._nav_card.width() <= 185
    assert dialog._nav_card.geometry().right() <= dialog._content_card.geometry().left()
    assert not dialog._live_preview_card.isVisible()
    assert not dialog._toggle_preview_btn.isVisible()
    assert not dialog._save_as_new_btn.isVisible()
    assert dialog._content_card.geometry().right() <= dialog.width()

    def _assert_container_and_text_fit(dlg, tag: str):
        # 1. Navigation column check (D3)
        nav = dlg._nav_card
        for nw in nav.findChildren(QWidget):
            if not nw.isVisible() or nw.width() <= 0 or nw.height() <= 0:
                continue
            nrect = QRect(nw.mapTo(nav, QPoint(0, 0)), nw.size())
            assert nrect.right() <= nav.width(), f"Nav child {nw} right={nrect.right()} > nav.width={nav.width()} ({tag})"
        for nbtn in nav.findChildren(QPushButton):
            if not nbtn.isVisible() or not nbtn.text():
                continue
            ntxt = nbtn.text().replace("&&", "&")
            ntw = QFontMetrics(nbtn.font()).horizontalAdvance(ntxt)
            assert ntw <= nbtn.width() - 8, f"Nav button {ntxt!r} text_w={ntw} > width-8={nbtn.width()-8} ({tag})"

        # 2. Content scroll viewport check (D3)
        vp = dlg._content_scroll.viewport()
        hbar = dlg._content_scroll.horizontalScrollBar()
        assert not hbar.isVisible(), f"Horizontal scrollbar visible ({tag})"
        assert dlg._content_scroll_widget.width() <= vp.width(), (
            f"_content_scroll_widget.width()={dlg._content_scroll_widget.width()} > viewport.width()={vp.width()} ({tag})"
        )
        for cw in dlg._content_scroll_widget.findChildren(QWidget):
            if not cw.isVisible() or cw.width() <= 0 or cw.height() <= 0:
                continue
            crect = QRect(cw.mapTo(vp, QPoint(0, 0)), cw.size())
            assert crect.right() <= vp.width(), (
                f"Content child {cw.objectName() or type(cw).__name__} right={crect.right()} > viewport.width()={vp.width()} ({tag})"
            )
        for cbtn in dlg._content_scroll_widget.findChildren(QPushButton):
            if not cbtn.isVisible() or not cbtn.text():
                continue
            ctxt = cbtn.text().replace("&&", "&")
            ctw = QFontMetrics(cbtn.font()).horizontalAdvance(ctxt)
            pad = 4 if (cbtn.property("role") == "stepper" or cbtn.width() <= 32) else 8
            assert ctw <= cbtn.width() - pad, (
                f"Button {ctxt!r} text_w={ctw} > width-{pad}={cbtn.width()-pad} ({tag})"
            )
        for clbl in dlg._content_scroll_widget.findChildren(QLabel):
            if not clbl.isVisible() or not clbl.text() or clbl.wordWrap():
                continue
            ltw = QFontMetrics(clbl.font()).horizontalAdvance(clbl.text())
            assert ltw <= clbl.width() - 2, (
                f"Label {clbl.text()!r} text_w={ltw} > width-2={clbl.width()-2} ({tag})"
            )

    assert dialog._content_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    test_sizes = [
        (dialog.minimumWidth(), dialog.minimumHeight()),
        (dialog.DEFAULT_WIDTH, dialog.DEFAULT_HEIGHT),
        (1180, 740),
    ]
    for w_px, h_px in test_sizes:
        dialog.resize(w_px, h_px)
        pump(8)
        for sec in ("general", "appearance", "about"):
            dialog.select_section(sec)
            pump(6)
            if sec == "appearance":
                for tab in ("themes", "colors", "glass_shape"):
                    dialog.select_appearance_tab(tab)
                    if tab == "colors":
                        dialog._set_customize_colors_expanded(False)
                        pump(6)
                        _assert_container_and_text_fit(dialog, f"{w_px}x{h_px}/{sec}/{tab}(collapsed)")
                        dialog._set_customize_colors_expanded(True)
                    pump(6)
                    _assert_container_and_text_fit(dialog, f"{w_px}x{h_px}/{sec}/{tab}")
            else:
                _assert_container_and_text_fit(dialog, f"{w_px}x{h_px}/{sec}")
