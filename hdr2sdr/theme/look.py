"""The app's "look": ONE shared set of appearance settings.

Before, light / dark / neon each kept their own gradient, effect, accent,
glass and glow settings. Now there is a single record (``look``) and the
three old themes are just *presets* of it:

  Light  - light base, flat, no glow
  Dark   - dark base, flat, no glow
  Neon   - dark base, neon gradient, glow on

``base`` ("light" / "dark") is the "Light base theme" switch. The palette
(the THEMES key everything paints from) is derived: with the Neon effect on
it is the neon family (near-black or lilac-white, see _NEON_BASE_PALETTES),
otherwise plain light or dark.

This module is pure data + QSettings (no widgets), so it can be tested alone.
"""
from __future__ import annotations

import copy
import json

from PySide6.QtGui import QColor

from hdr2sdr.theme.metrics import DEFAULT_BUTTON_RADIUS, DEFAULT_CARD_RADIUS
from hdr2sdr.theme.palettes import (
    NEON_BG_EFFECTS, _NEON_BASE_PALETTES, _NEON_DEFAULT_STOPS, _THEME_DEFAULT_INTENSITY,
)

BASES = ("light", "dark")
BUILTIN_KEYS = ("light", "dark", "neon")
MAX_USER_PRESETS = 20

# Fields of a look (what is persisted under "look_<field>").
LOOK_FIELDS = ("base", "stops", "direction", "intensity", "effect", "button_custom", "button_hex",
               "ui_style", "neon_effect")
# What a preset may carry on top of the look.
PRESET_EXTRA = ("radius_card", "radius_button")


# ---- basic helpers ----------------------------------------------------------
def _valid_hex(value):
    return isinstance(value, str) and QColor(value).isValid()


def _valid_stops(value):
    return (isinstance(value, list) and 2 <= len(value) <= 3 and all(_valid_hex(c) for c in value))


def _to_int(value, default, lo=None, hi=None):
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def default_look(base="light"):
    """Factory look for a base: a plain flat light/dark background."""
    return dict(base=base if base in BASES else "light", stops=None, direction=180, intensity=0,
                effect="none", button_custom=False, button_hex=None, ui_style="classic",
                neon_effect=False)


def palette_name(look):
    """The THEMES key this look paints with."""
    return "neon" if look.get("neon_effect") else (look.get("base") if look.get("base") in BASES else "light")


def clean_look(raw):
    """A complete, valid look from whatever was stored (missing/bad -> default)."""
    raw = raw if isinstance(raw, dict) else {}
    look = default_look(raw.get("base", "light"))
    if _valid_stops(raw.get("stops")):
        look["stops"] = list(raw["stops"])
    look["direction"] = _to_int(raw.get("direction"), 180, 0, 360)
    look["intensity"] = _to_int(raw.get("intensity"), 0, 0, 100)
    look["effect"] = raw.get("effect") if raw.get("effect") in NEON_BG_EFFECTS else "none"
    look["button_hex"] = raw.get("button_hex") if _valid_hex(raw.get("button_hex")) else None
    look["button_custom"] = bool(raw.get("button_custom")) and look["button_hex"] is not None
    look["ui_style"] = raw.get("ui_style") if raw.get("ui_style") in ("classic", "glass") else "classic"
    look["neon_effect"] = bool(raw.get("neon_effect", False))
    return look


# ---- built-in presets ---------------------------------------------------------
def builtin_preset(key):
    """Full preset (look + rounding) for "light" / "dark" / "neon"."""
    if key == "neon":
        look = default_look("dark")
        look.update(stops=list(_NEON_DEFAULT_STOPS), intensity=_THEME_DEFAULT_INTENSITY["neon"],
                    neon_effect=True)
    else:
        look = default_look("dark" if key == "dark" else "light")
    look["radius_card"] = DEFAULT_CARD_RADIUS
    look["radius_button"] = DEFAULT_BUTTON_RADIUS
    return look


# ---- user presets ---------------------------------------------------------------
def clean_preset(item):
    """Validate one stored preset. Old presets (colour-only) simply lack the
    newer fields - those are then left alone when the preset is applied."""
    if not isinstance(item, dict) or not _valid_stops(item.get("stops")):
        return None
    out = dict(
        name=str(item.get("name") or "").strip()[:40] or "Untitled",
        stops=list(item["stops"]),
        direction=_to_int(item.get("direction"), 180, 0, 360),
        intensity=_to_int(item.get("intensity"), 60, 0, 100),
        effect=item.get("effect") if item.get("effect") in NEON_BG_EFFECTS else "none",
        button_custom=bool(item.get("button_custom", False)),
        button_hex=item.get("button_hex") if _valid_hex(item.get("button_hex")) else None,
    )
    if item.get("base") in BASES:
        out["base"] = item["base"]
    if item.get("ui_style") in ("classic", "glass"):
        out["ui_style"] = item["ui_style"]
    if "neon_effect" in item:
        out["neon_effect"] = bool(item["neon_effect"])
    for key in PRESET_EXTRA:
        if key in item:
            out[key] = _to_int(item[key], 0, 0, 40)
    return out


def load_presets(settings):
    raw = settings.value("look_presets", None)
    if not isinstance(raw, str):
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [p for p in (clean_preset(i) for i in data) if p is not None]


def save_presets(settings, presets):
    settings.setValue("look_presets", json.dumps(presets[-MAX_USER_PRESETS:]))


# ---- persistence ----------------------------------------------------------------
def save_look(settings, look):
    settings.setValue("look_base", look["base"])
    settings.setValue("look_gradient_stops", json.dumps(look["stops"]) if look["stops"] else "")
    settings.setValue("look_gradient_direction", look["direction"])
    settings.setValue("look_gradient_intensity", look["intensity"])
    settings.setValue("look_bg_effect", look["effect"])
    settings.setValue("look_button_color_enabled", "true" if look["button_custom"] else "false")
    settings.setValue("look_button_color", look["button_hex"] or "")
    settings.setValue("look_ui_style", look["ui_style"])
    settings.setValue("look_neon_effect", "true" if look["neon_effect"] else "false")
    # Kept in step for anything still reading the old key.
    settings.setValue("theme_name", palette_name(look))
    settings.setValue("look_migrated", "1")


def _read_look(settings):
    stops = None
    raw_stops = settings.value("look_gradient_stops", "")
    if isinstance(raw_stops, str) and raw_stops:
        try:
            parsed = json.loads(raw_stops)
            stops = parsed if _valid_stops(parsed) else None
        except (ValueError, TypeError):
            stops = None
    return clean_look(dict(
        base=settings.value("look_base", "light"),
        stops=stops,
        direction=settings.value("look_gradient_direction", 180),
        intensity=settings.value("look_gradient_intensity", 0),
        effect=settings.value("look_bg_effect", "none"),
        button_custom=_to_bool(settings.value("look_button_color_enabled", "false")),
        button_hex=settings.value("look_button_color", "") or None,
        ui_style=settings.value("look_ui_style", "classic"),
        neon_effect=_to_bool(settings.value("look_neon_effect", "false")),
    ))


def load_look(settings):
    """The saved look. The first time (nothing saved under the new keys) it is
    built from the old per-theme settings - see migrate_legacy()."""
    if str(settings.value("look_migrated", "")) != "1":
        look = migrate_legacy(settings)
        save_look(settings, look)
        settings.setValue("look_migrated", "1")
        return look
    return _read_look(settings)


# ---- migration from the three separate per-theme settings -----------------------
def _legacy_theme(settings, key):
    """What the old per-theme storage held for `key` (light/dark/neon),
    including the even older single-accent keys. Pure read, no side effects."""
    stops = None
    raw = settings.value(f"{key}_gradient_stops", None)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if _valid_stops(parsed) or (isinstance(parsed, list) and len(parsed) == 3
                                        and all(_valid_hex(c) for c in parsed)):
                stops = parsed
        except (ValueError, TypeError):
            pass
    single_accent = None
    if stops is None:
        if key == "neon":
            raw = settings.value("neon_gradient_stops", None)
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if _valid_stops(parsed):
                        stops = parsed
                except (ValueError, TypeError):
                    pass
        else:
            legacy_accent = settings.value(f"accent_color_{key}", None)
            if _valid_hex(legacy_accent):
                single_accent = legacy_accent
    effect = settings.value(f"{key}_bg_effect", None)
    if effect not in NEON_BG_EFFECTS:
        legacy_effect = settings.value("neon_bg_effect" if key == "neon" else "glass_bg_effect", "none")
        effect = legacy_effect if legacy_effect in NEON_BG_EFFECTS else "none"
    button_custom = _to_bool(settings.value(f"{key}_button_color_enabled", "false"))
    button_hex = settings.value(f"{key}_button_color", None)
    button_hex = button_hex if _valid_hex(button_hex) else None
    ui_style = settings.value(f"{key}_ui_style", None)
    if ui_style not in ("classic", "glass"):
        ui_style = settings.value("ui_style", "classic") if key == "light" else "classic"
    neon_raw = settings.value(f"{key}_neon_effect", None)
    if neon_raw is None:
        neon_raw = settings.value("neon_effect", "false") if key == "light" else ("true" if key == "neon" else "false")
    base = settings.value(f"{key}_neon_base", None)
    if base is None and key == "neon":
        base = settings.value("neon_theme_base", "dark")
    return dict(
        stops=stops, single_accent=single_accent,
        direction=_to_int(settings.value(f"{key}_gradient_direction", 180), 180, 0, 360),
        intensity=_to_int(settings.value(f"{key}_gradient_intensity", _THEME_DEFAULT_INTENSITY.get(key, 60)),
                          _THEME_DEFAULT_INTENSITY.get(key, 60), 0, 100),
        effect=effect, button_custom=button_custom, button_hex=button_hex,
        ui_style=ui_style if ui_style in ("classic", "glass") else "classic",
        neon_effect=_to_bool(neon_raw), neon_base=base if base in _NEON_BASE_PALETTES else "dark",
        saved_stops=isinstance(settings.value(f"{key}_gradient_stops", None), str),
    )


def _legacy_to_look(key, old):
    base = key if key in BASES else old["neon_base"]
    look = default_look(base)
    look.update(direction=old["direction"], intensity=old["intensity"], effect=old["effect"],
                ui_style=old["ui_style"], neon_effect=old["neon_effect"],
                button_custom=old["button_custom"], button_hex=old["button_hex"])
    if old["stops"] and _valid_stops(old["stops"]):
        look["stops"] = list(old["stops"])
    elif old["single_accent"]:
        # an old install that only ever picked ONE accent colour
        look["button_custom"], look["button_hex"] = True, old["single_accent"]
    elif look["neon_effect"]:
        look["stops"] = list(_NEON_DEFAULT_STOPS)
    if look["stops"] is None and not look["neon_effect"] and not look["button_custom"]:
        look["intensity"] = 0          # never customised: the new default is a plain flat base
    return clean_look(look)


def migrate_legacy(settings):
    """Build the one shared look from the old three. The theme that was active
    becomes the current look; each of the other two that had really been
    customised is kept as a user preset ("Light (previous)" ...) so nothing the
    person set up is lost. Presets saved per theme before are merged into the
    one shared list."""
    active = settings.value("theme_name", "light")
    active = active if active in BUILTIN_KEYS else "light"
    old = {key: _legacy_theme(settings, key) for key in BUILTIN_KEYS}
    look = _legacy_to_look(active, old[active])

    presets = []
    names = set()

    def add(preset):
        clean = clean_preset(preset)
        if clean is None:
            return
        base_name, n = clean["name"], 2
        while clean["name"] in names:
            clean["name"] = f"{base_name} {n}"[:40]
            n += 1
        names.add(clean["name"])
        presets.append(clean)

    for key in BUILTIN_KEYS:
        raw = settings.value(f"{key}_presets", None)
        if not isinstance(raw, str):
            continue
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            if key == "neon":                       # neon presets carried the neon base; they were neon-glow looks
                item["base"] = item.get("neon_base") if item.get("neon_base") in BASES else "dark"
                item["neon_effect"] = True
            add(item)
    # Each old theme's own customisation becomes that built-in preset's saved look.
    slots = {}
    for key in BUILTIN_KEYS:
        if key == active:
            continue
        prev = _legacy_to_look(key, old[key])
        prev["neon_effect"] = key == "neon"
        if key != "neon":
            prev["base"] = key
        if old[key]["saved_stops"] or old[key]["single_accent"]:
            slots[key] = clean_look(prev)
    slots[slot_key(look)] = copy.deepcopy(look)
    save_slots(settings, slots)
    if presets:
        save_presets(settings, presets)
    return look


# ---- the three built-in presets, each with its own saved customisation -----------
def slot_key(look):
    """Which built-in preset a look belongs to: Neon if the neon effect is on, else its base."""
    return "neon" if look.get("neon_effect") else (look.get("base") if look.get("base") in BASES else "light")


def load_slots(settings):
    """{"light": look, "dark": look, "neon": look}: what each built-in preset currently
    looks like (factory unless the user applied changes while on it)."""
    try:
        raw = json.loads(settings.value("look_slots", "") or "{}")
    except (ValueError, TypeError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in BUILTIN_KEYS:
        factory = builtin_preset(key)
        out[key] = clean_look(raw[key]) if isinstance(raw.get(key), dict) else {f: factory[f] for f in LOOK_FIELDS}
    return out


def save_slots(settings, slots):
    settings.setValue("look_slots", json.dumps({k: clean_look(v) for k, v in slots.items()}))


def save_slot(settings, look):
    """Remember `look` as the saved look of the built-in preset it belongs to."""
    slots = load_slots(settings)
    slots[slot_key(look)] = clean_look(look)
    save_slots(settings, slots)


def copy_look(look):
    return copy.deepcopy(look)
