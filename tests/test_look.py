"""The one shared "look": defaults, built-in presets, migration from the old
three per-theme profiles, and the dialog's preset matching."""
import json

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings

from hdr2sdr.theme import look as L


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


def test_fresh_install_is_a_plain_flat_base(settings):
    look = L.load_look(settings)
    assert look["base"] == "light" and look["stops"] is None and look["intensity"] == 0
    assert not look["neon_effect"] and look["ui_style"] == "classic"
    assert L.load_look(settings) == look                      # stable on the second load


def test_builtin_presets():
    assert L.builtin_preset("light")["base"] == "light" and L.builtin_preset("light")["stops"] is None
    assert L.builtin_preset("dark")["base"] == "dark" and L.builtin_preset("dark")["intensity"] == 0
    neon = L.builtin_preset("neon")
    assert neon["neon_effect"] and neon["stops"] and neon["intensity"] > 0 and neon["base"] == "dark"
    assert L.palette_name(neon) == "neon" and L.palette_name(L.builtin_preset("dark")) == "dark"


def test_migration_keeps_active_theme_and_other_customised_ones(settings):
    settings.setValue("theme_name", "dark")
    settings.setValue("dark_gradient_stops", json.dumps(["#ff0000", "#00ff00"]))
    settings.setValue("dark_gradient_intensity", 55)
    settings.setValue("dark_ui_style", "glass")
    settings.setValue("neon_gradient_stops", json.dumps(["#00e5ff", "#7b2fd8", "#ff2e6c"]))
    settings.setValue("light_presets", json.dumps([dict(name="Mine", stops=["#111111", "#222222"])]))
    look = L.load_look(settings)
    assert look["base"] == "dark" and look["stops"] == ["#ff0000", "#00ff00"]
    assert look["intensity"] == 55 and look["ui_style"] == "glass"
    assert "Mine" in [p["name"] for p in L.load_presets(settings)]
    slots = L.load_slots(settings)
    assert slots["dark"]["stops"] == ["#ff0000", "#00ff00"]          # the active one lands in its own slot
    assert slots["neon"]["neon_effect"] and slots["neon"]["stops"] == ["#00e5ff", "#7b2fd8", "#ff2e6c"]


def test_never_customised_old_install_becomes_flat(settings):
    settings.setValue("theme_name", "light")
    look = L.load_look(settings)
    assert look["stops"] is None and look["intensity"] == 0


def test_bad_stored_values_fall_back(settings):
    settings.setValue("look_migrated", "1")
    settings.setValue("look_base", "purple")
    settings.setValue("look_gradient_stops", "not json")
    settings.setValue("look_bg_effect", "bogus")
    look = L.load_look(settings)
    assert look == L.default_look("light")


def test_save_and_load_roundtrip(settings):
    look = L.builtin_preset("neon")
    L.save_look(settings, look)
    settings.setValue("look_migrated", "1")
    assert {k: L.load_look(settings)[k] for k in L.LOOK_FIELDS} == {k: look[k] for k in L.LOOK_FIELDS}


def test_header_button_cycles_light_dark_neon():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.i18n.settings.remove("look_slots")
    w._set_look(L.builtin_preset("light"), persist=False)
    seen = []
    for _ in range(3):
        w.mode_btn.click()
        seen.append((w.theme_name, w._mode_icon_kind(), w.look["ui_style"]))
    assert seen == [("dark", "moon", "classic"), ("neon", "bolt_filled", "classic"), ("light", "sun", "classic")]
    w.close()


def test_each_builtin_preset_remembers_its_own_applied_look(settings):
    dark = L.builtin_preset("dark")
    dark["stops"], dark["intensity"] = ["#123456", "#abcdef"], 50
    L.save_slot(settings, dark)
    slots = L.load_slots(settings)
    assert slots["dark"]["stops"] == ["#123456", "#abcdef"]
    assert slots["light"]["stops"] is None and slots["neon"]["neon_effect"]
    assert L.slot_key(dark) == "dark" and L.slot_key(L.builtin_preset("neon")) == "neon"


def test_save_look_marks_migrated_and_legacy_neon_keeps_default_stops(tmp_path):
    s_apply = QSettings(str(tmp_path / "apply.ini"), QSettings.Format.IniFormat)
    neon = L.builtin_preset("neon")
    L.save_look(s_apply, neon)
    assert s_apply.value("look_migrated") == "1"
    saved = L.load_look(s_apply)
    assert saved["neon_effect"] and saved["stops"] == neon["stops"] and saved["base"] == "dark"

    s_legacy = QSettings(str(tmp_path / "legacy_neon.ini"), QSettings.Format.IniFormat)
    s_legacy.setValue("theme_name", "neon")
    migrated = L.load_look(s_legacy)
    assert migrated["neon_effect"] and migrated["stops"] == neon["stops"] and migrated["base"] == "dark"
    assert L.load_slots(s_legacy)["neon"]["stops"] == neon["stops"]
