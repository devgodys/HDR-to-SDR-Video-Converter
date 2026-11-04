from __future__ import annotations

from hdr2sdr.theme.theme_tokens import (
    DARK_BASE_TOKENS,
    LIGHT_BASE_TOKENS,
    NEON_DARK_BASE_TOKENS,
    NEON_LIGHT_BASE_TOKENS,
    is_dark_color,
    window_tokens,
)

#Legacykeys mirrored from base token tables
_LEGACY_KEYS = (
    "BG", "CARD", "CARD2", "FIELD", "DISABLED", "BORDER", "TXT", "MUTED",
    "INDIGO", "INDIGO2", "GREEN", "GREEN2", "RED", "RED2", "AMBER", "AMBER2",
    "LOG_BG", "LOG_FG",
)

# ---- palette ---------------------------------------------------------------
# Three base palettes - light/dark/neon. "Glass" is NOT a fourth/fifth
# palette here: it's the orthogonal `ui_style` flag ("classic"/"glass")
# that MainWindow carries alongside theme_name, and every one of these
# three palettes can be painted in either ui_style (see Card/GlassButton/
# _BgLayer, which all just read theme_name + ui_style off the window and
# combine them at paint time). Blue is reserved for the primary action,
# progress fill, and in-progress status text across all of them.
THEMES = {
    "light": {k: LIGHT_BASE_TOKENS[k] for k in _LEGACY_KEYS},
    "dark": {k: DARK_BASE_TOKENS[k] for k in _LEGACY_KEYS},
    "neon": {k: NEON_DARK_BASE_TOKENS[k] for k in _LEGACY_KEYS},
}

NEON_THEMES = ("neon",)

_NEON_BASE_KEYS = (
    "BG", "CARD", "CARD2", "FIELD", "DISABLED", "BORDER", "TXT", "MUTED",
    "GREEN", "GREEN2", "RED", "RED2", "AMBER", "AMBER2", "LOG_BG", "LOG_FG",
)
_NEON_DARK_BASE = {k: NEON_DARK_BASE_TOKENS[k] for k in _NEON_BASE_KEYS}
_NEON_LIGHT_BASE = {k: NEON_LIGHT_BASE_TOKENS[k] for k in _NEON_BASE_KEYS}
_NEON_BASE_PALETTES = {"dark": _NEON_DARK_BASE, "light": _NEON_LIGHT_BASE}

_NEON_DEFAULTS = dict(THEMES["neon"])
_LIGHT_DEFAULTS = dict(THEMES["light"])
_DARK_DEFAULTS = dict(THEMES["dark"])

_NEON_DEFAULT_STOPS = [_NEON_DEFAULTS["INDIGO"], "#7B2FD8", _NEON_DEFAULTS["RED"]]
_LIGHT_DEFAULT_STOPS = [_LIGHT_DEFAULTS["INDIGO"], _LIGHT_DEFAULTS["AMBER"], _LIGHT_DEFAULTS["RED"]]
_DARK_DEFAULT_STOPS = [_DARK_DEFAULTS["INDIGO"], _DARK_DEFAULTS["AMBER"], _DARK_DEFAULTS["RED"]]

_THEME_DEFAULTS = {"light": _LIGHT_DEFAULTS, "dark": _DARK_DEFAULTS, "neon": _NEON_DEFAULTS}
_THEME_DEFAULT_STOPS = {"light": _LIGHT_DEFAULT_STOPS, "dark": _DARK_DEFAULT_STOPS, "neon": _NEON_DEFAULT_STOPS}
_THEME_DEFAULT_INTENSITY = {"light": 30, "dark": 45, "neon": 60}

NEON_BG_EFFECTS = ("none", "grid", "dots", "diagonal", "waves", "rings", "crosshatch", "stars")


def _theme_is_dark(name):
    """True for whichever theme variant currently reads as dark. Reads
    the token/palette BG lightness rather than hardcoding by name."""
    if isinstance(name, dict):
        bg = name.get("bg_window") or name.get("BG")
        return is_dark_color(bg) if bg else True
    if not isinstance(name, str) and name is not None:
        tok = window_tokens(name)
        return bool(tok.get("is_dark", is_dark_color(tok["bg_window"])))
    theme = THEMES.get(name)
    if theme is None:
        return name == "dark" or name in NEON_THEMES
    return is_dark_color(theme["BG"])


def _neon_active(win):
    """True whenever `win` should render with the coloured "neon glow"
    look. Driven purely by the independent Neon effect flag now."""
    return bool(getattr(win, "neon_effect", False))
