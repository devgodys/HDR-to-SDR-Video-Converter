"""Single source of truth for UI design tokens (colors, spacing, radii) and QSS builders.

Stage 1b: updates Dark/Light base tokens to the new palette while preserving
Neon's own dark/light base tokens and user custom looks.
"""
from __future__ import annotations

from hdr2sdr.theme.metrics import (
    DEFAULT_BUTTON_RADIUS,
    DEFAULT_CARD_RADIUS,
    MAX_BUTTON_RADIUS,
    MAX_CARD_RADIUS,
    button_radius,
    card_radius,
)

# ---- Spacing scale (px) -----------------------------------------------------
SPACE_4 = 4
SPACE_8 = 8
SPACE_12 = 12
SPACE_16 = 16
SPACE_24 = 24
SPACING_SCALE = (SPACE_4, SPACE_8, SPACE_12, SPACE_16, SPACE_24)

# ---- Window chrome constants ------------------------------------------------
WINDOW_RADIUS = 8
WINDOW_BORDER = 1.0

# ---- Typography candidate lists ---------------------------------------------
FONT_BODY_CANDIDATES = (
    "Segoe UI Variable Text",
    "Segoe UI Variable Display",
    "Segoe UI",
    "SF Pro Text",
    "Inter",
    "Ubuntu",
    "Helvetica Neue",
)
FONT_SEMI_CANDIDATES = (
    "Segoe UI Variable Display",
    "Segoe UI Variable Text",
    "Segoe UI Semibold",
    "Segoe UI",
    "SF Pro Display",
    "Inter SemiBold",
    "Ubuntu",
)
FONT_MONO_CANDIDATES = (
    "Cascadia Mono",
    "Consolas",
    "Menlo",
    "SF Mono",
    "Ubuntu Mono",
    "Courier New",
)

# ---- Contrast & color math (pure Python, no Qt required) --------------------
ON_ACCENT_DARK = "#0B1220"
ON_ACCENT_LIGHT = "#FFFFFF"
KOFI_ACCENT = "#FF5E5B"
KOFI_HOVER = "#F04642"
PREVIEW_FALLBACK_STOPS = ("#CA48C8", "#48C8CA")


def parse_hex_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse '#RRGGBB' or '#RGB' into (r, g, b) ints in 0..255."""
    s = (hex_color or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return (0, 0, 0)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0, min(255, int(round(r)))):02x}{max(0, min(255, int(round(g)))):02x}{max(0, min(255, int(round(b)))):02x}"


def mix_hex(hex1: str, hex2: str, frac: float) -> str:
    """Linearly blend two '#RRGGBB' colors (frac=0 -> hex1, frac=1 -> hex2)."""
    r1, g1, b1 = parse_hex_rgb(hex1)
    r2, g2, b2 = parse_hex_rgb(hex2)
    return rgb_to_hex(
        r1 + (r2 - r1) * frac,
        g1 + (g2 - g1) * frac,
        b1 + (b2 - b1) * frac,
    )


def is_dark_color(hex_color: str) -> bool:
    """HSL lightness < 0.5 (matches QColor(hex_color).lightnessF() < 0.5)."""
    r, g, b = parse_hex_rgb(hex_color)
    lightness = (max(r, g, b) + min(r, g, b)) / (2.0 * 255.0)
    return lightness < 0.5


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.1 relative luminance in 0.0..1.0."""
    r, g, b = parse_hex_rgb(hex_color)

    def _chan(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * _chan(r) + 0.7152 * _chan(g) + 0.0722 * _chan(b)


def contrast_ratio(hex1: str, hex2: str) -> float:
    """WCAG 2.1 contrast ratio between two hex colors (1.0..21.0)."""
    l1 = relative_luminance(hex1)
    l2 = relative_luminance(hex2)
    bright, dark = (l1, l2) if l1 >= l2 else (l2, l1)
    return (bright + 0.05) / (dark + 0.05)


WCAG_TEXT_THRESHOLD = 4.5
WCAG_ACCENT_WARNING_THRESHOLD = 3.0


def pick_toggle_knob_color(track_hex: str, min_ratio: float = 3.0) -> str:
    """Keep ToggleSwitch knob white (#FFFFFF) in ON state if contrast >= 3:1, else dark (#0B1220)."""
    if contrast_ratio(ON_ACCENT_LIGHT, track_hex) >= min_ratio:
        return ON_ACCENT_LIGHT
    return ON_ACCENT_DARK


def pick_on_accent(bg_hex: str) -> str:
    """Pick the best contrasting foreground (#0B1220 or #FFFFFF) for ``bg_hex``
    using WCAG 2.1 relative_luminance / contrast_ratio (threshold 4.5:1).
    """
    c_dark = contrast_ratio(ON_ACCENT_DARK, bg_hex)
    c_light = contrast_ratio(ON_ACCENT_LIGHT, bg_hex)
    return ON_ACCENT_DARK if c_dark >= c_light else ON_ACCENT_LIGHT


def average_gradient_hex(
    stops: list[str] | tuple[str, ...] | None,
    bg_window: str = "#0E0F12",
    intensity: int = 100,
) -> str:
    """Compute the effective average color of a gradient over ``bg_window``."""
    if not stops or intensity <= 0:
        return bg_window
    rgbs = [parse_hex_rgb(s) for s in stops]
    avg_r = sum(c[0] for c in rgbs) / len(rgbs)
    avg_g = sum(c[1] for c in rgbs) / len(rgbs)
    avg_b = sum(c[2] for c in rgbs) / len(rgbs)
    avg_stop = rgb_to_hex(round(avg_r), round(avg_g), round(avg_b))
    alpha = max(0.0, min(1.0, intensity / 100.0))
    return mix_hex(bg_window, avg_stop, alpha)


def adjust_card_bg_for_gradient(
    bg_card: str,
    text_hex: str,
    stops: list[str] | tuple[str, ...] | None,
    bg_window: str,
    intensity: int,
    threshold: float = WCAG_TEXT_THRESHOLD,
) -> tuple[str, str, bool]:
    """Evaluate text contrast over the average gradient color; if below
    ``threshold``, darken or lighten the card backing (without mutating user colors).
    Returns ``(adjusted_bg_card, gradient_avg_hex, was_adjusted)``.
    """
    grad_avg = average_gradient_hex(stops, bg_window, intensity)
    if not stops or intensity <= 0:
        return bg_card, grad_avg, False
    if contrast_ratio(text_hex, grad_avg) >= threshold and contrast_ratio(text_hex, bg_card) >= threshold:
        return bg_card, grad_avg, False
    pole = "#000000" if not is_dark_color(text_hex) else "#FFFFFF"
    best = mix_hex(bg_card, pole, 0.25)
    for step in range(1, 21):
        candidate = mix_hex(bg_card, pole, step * 0.05)
        if contrast_ratio(text_hex, candidate) >= threshold:
            best = candidate
            break
    return best, grad_avg, True


def rgba_str(hex_color: str, alpha_float: float) -> str:
    """Convert ``#RRGGBB`` and ``alpha_float`` (0.0..1.0) into ``rgba(r, g, b, a_255)``."""
    r, g, b = parse_hex_rgb(hex_color)
    a = max(0, min(255, int(round(alpha_float * 255))))
    return f"rgba({r}, {g}, {b}, {a})"


def build_button_states_qss(tokens: dict, button_radius_px: int = 8) -> str:
    """Shared QSS builder for button states (enabled / hover / pressed / focus / disabled)
    including ``role="panel-toggle"`` and segmented preset buttons across Pro and Mini.
    """
    t = tokens
    br = min(button_radius_px, 13)
    dis_txt = rgba_str(t["text"], 0.40)
    return f"""
        QPushButton {{
            background: {t['bg_card']}; color: {t['text']}; border: 1px solid {t['border']};
            border-radius: {br}px; padding: 8px 14px;
        }}
        QPushButton:hover {{ background: {t['bg_card_alt']}; }}
        QPushButton:pressed {{ background: {t['bg_field']}; }}
        QPushButton:focus {{ border-color: {t['accent']}; outline: none; }}
        QPushButton:disabled {{
            color: {dis_txt}; background: {t['bg_disabled']}; border: 1px solid {t['border']};
        }}
        QPushButton[role="go"] {{
            background: {t['accent']}; color: {t['on_accent']}; font-weight: 600;
            border: none; min-height: 20px;
        }}
        QPushButton[role="go"]:hover {{ background: {t['accent_hover']}; }}
        QPushButton[role="go"]:pressed {{ background: {t['accent_hover']}; }}
        QPushButton[role="go"]:disabled {{
            color: {dis_txt}; background: {t['bg_disabled']}; border: 1px solid {t['border']};
        }}
        QPushButton[role="panel-toggle"] {{
            background: {t['bg_card_alt']}; border: 1px solid {t['border']}; border-radius: {br}px;
            padding: 6px 14px; color: {t['text_secondary']};
        }}
        QPushButton[role="panel-toggle"]:hover {{
            background: {t['bg_card']}; color: {t['text']}; border-color: {t['accent']};
        }}
        QPushButton[role="panel-toggle"]:pressed {{
            background: {t['bg_field']}; color: {t['text']}; border-color: {t['accent']};
        }}
        QPushButton[role="panel-toggle"]:focus {{
            border-color: {t['accent']}; outline: none;
        }}
        QPushButton[lead_icon] {{
            padding: 8px 10px 8px 30px;
        }}
        QPushButton[role="panel-toggle"]:checked {{
            background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']};
        }}
        QPushButton[role="panel-toggle"]:checked:hover {{
            background: {t['accent_hover']}; border-color: {t['accent_hover']};
        }}
        QPushButton[role="panel-toggle"][drawer_header="true"]:checked {{
            background: {t['bg_card_alt']}; color: {t['text']}; border: 1px solid {t['accent']};
        }}
        QPushButton[role="panel-toggle"][drawer_header="true"]:checked:hover {{
            background: {t['bg_card']}; color: {t['text']}; border-color: {t['accent_hover']};
        }}
        QPushButton[role="panel-toggle"]:disabled {{
            color: {dis_txt}; background: {t['bg_disabled']}; border: 1px solid {t['border']};
        }}
        QFrame[role="preset-segment"] {{
            background: {t['bg_field']}; border: 1px solid {t['border']}; border-radius: {br}px;
            padding: 2px;
        }}
        QPushButton[role="preset-segment-btn"] {{
            background: transparent; border: 1px solid transparent; border-radius: {max(2, br - 2)}px;
            padding: 6px 8px; color: {t['text_secondary']}; font-weight: 500;
        }}
        QPushButton[role="preset-segment-btn"]:hover {{
            background: {t['bg_card_alt']}; color: {t['text']};
        }}
        QPushButton[role="preset-segment-btn"]:pressed {{
            background: {t['bg_card']}; color: {t['text']};
        }}
        QPushButton[role="preset-segment-btn"]:checked {{
            background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; font-weight: 600;
        }}
        QPushButton[role="preset-segment-btn"]:checked:hover {{
            background: {t['accent_hover']}; border-color: {t['accent_hover']};
        }}
        QPushButton[role="preset-segment-btn"]:disabled {{
            color: {dis_txt}; background: transparent; border-color: transparent;
        }}
    """


def is_accent_contrast_low(
    accent_hex: str,
    bg_card_hex: str,
    on_accent_hex: str | None = None,
    min_ratio: float = WCAG_ACCENT_WARNING_THRESHOLD,
) -> tuple[bool, float, float]:
    """Return ``(is_low, bg_ratio, text_ratio)`` checking if ``accent_hex`` has
    contrast < ``min_ratio`` (3.0:1) against ``bg_card_hex`` or ``on_accent_hex``.
    """
    resolved_on = on_accent_hex or pick_on_accent(accent_hex)
    bg_ratio = contrast_ratio(accent_hex, bg_card_hex)
    txt_ratio = contrast_ratio(resolved_on, accent_hex)
    return (bg_ratio < min_ratio or txt_ratio < min_ratio), bg_ratio, txt_ratio


# ---- Base token tables (Stage 1b: new Dark/Light values + Neon preserved) ----
# NOTE: Legacy uppercase keys (BG, CARD, INDIGO, etc.) are intentionally kept
# in sync alongside semantic keys until the end of Stage 3.
_LEGACY_TO_SEMANTIC = (
    ("BG", "bg_window"),
    ("CARD", "bg_card"),
    ("CARD2", "bg_card_alt"),
    ("FIELD", "bg_field"),
    ("DISABLED", "bg_disabled"),
    ("BORDER", "border"),
    ("TXT", "text"),
    ("MUTED", "text_secondary"),
    ("INDIGO", "accent"),
    ("INDIGO2", "accent_hover"),
    ("GREEN", "success"),
    ("GREEN2", "success_hover"),
    ("RED", "danger"),
    ("RED2", "danger_hover"),
    ("AMBER", "warning"),
    ("AMBER2", "warning_hover"),
    ("LOG_BG", "log_bg"),
    ("LOG_FG", "log_fg"),
)


def _sync_aliases(d: dict) -> dict:
    """Keep semantic token keys and uppercase legacy palette keys in sync."""
    for legacy_key, semantic_key in _LEGACY_TO_SEMANTIC:
        if semantic_key in d:
            d[legacy_key] = d[semantic_key]
        elif legacy_key in d:
            d[semantic_key] = d[legacy_key]
    return d


LIGHT_BASE_TOKENS = _sync_aliases(dict(
    bg_window="#EEEEEB",
    bg_card="#FFFFFF",
    bg_card_alt="#F8F8F5",
    bg_field="#F3F3F0",
    bg_disabled="#E6E6E1",
    border="#DCDCD6",
    border_strong="#B2B5B6",
    text="#16181D",
    text_secondary="#656C7A",
    accent="#1A66E0",
    accent_hover="#2F7BF2",
    on_accent="#FFFFFF",
    success="#34D399",
    success_hover="#10B981",
    danger="#F87171",
    danger_hover="#EF4444",
    warning="#FBBF24",
    warning_hover="#F59E0B",
    log_bg="#F7F7F4",
    log_fg="#2B2E36",
    knob_fg="#FFFFFF",
    off_fallback="#B4B2A9",
))

DARK_BASE_TOKENS = _sync_aliases(dict(
    bg_window="#0E0F12",
    bg_card="#16181D",
    bg_card_alt="#1C1F26",
    bg_field="#1F222A",
    bg_disabled="#252830",
    border="#2A2E38",
    border_strong="#4C515E",
    text="#E8EAF0",
    text_secondary="#8B93A5",
    accent="#4F7CFF",
    accent_hover="#6B93FF",
    on_accent="#0B1220",
    success="#34D399",
    success_hover="#10B981",
    danger="#F87171",
    danger_hover="#EF4444",
    warning="#FBBF24",
    warning_hover="#F59E0B",
    log_bg="#0A0B0E",
    log_fg="#D7DCE6",
))

NEON_DARK_BASE_TOKENS = _sync_aliases(dict(
    bg_window="#050507",
    bg_card="#0D0D14",
    bg_card_alt="#13131F",
    bg_field="#0B0B12",
    bg_disabled="#1A1A24",
    border="#2E2E45",
    border_strong="#494962",
    text="#E8FBFF",
    text_secondary="#7C7C99",
    accent="#00E5FF",
    accent_hover="#00A8BF",
    on_accent="#0B1220",
    success="#39FF88",
    success_hover="#22C766",
    danger="#FF2E6C",
    danger_hover="#D6134F",
    warning="#FFD23F",
    warning_hover="#E0AF1E",
    log_bg="#020204",
    log_fg="#39FF88",
))

NEON_LIGHT_BASE_TOKENS = _sync_aliases(dict(
    bg_window="#F6F4FF",
    bg_card="#FFFFFF",
    bg_card_alt="#ECE7FB",
    bg_field="#FFFFFF",
    bg_disabled="#E5E0F7",
    border="#DAD2F2",
    border_strong="#B3ACCE",
    text="#1B1730",
    text_secondary="#6B658B",
    accent="#007B99",
    accent_hover="#00627A",
    on_accent="#FFFFFF",
    success="#1E9E5A",
    success_hover="#167C46",
    danger="#D6134F",
    danger_hover="#B30E40",
    warning="#BF7A1E",
    warning_hover="#9C6418",
    log_bg="#FAF9FD",
    log_fg="#3A2E63",
))

BASE_TOKEN_SETS = {
    "light": LIGHT_BASE_TOKENS,
    "dark": DARK_BASE_TOKENS,
    "neon": NEON_DARK_BASE_TOKENS,
    "neon_light": NEON_LIGHT_BASE_TOKENS,
}


def build_tokens(
    look: dict | None = None,
    *,
    theme_name: str | None = None,
    radius_card: int | None = None,
    radius_button: int | None = None,
) -> dict:
    """Pure function: assemble a complete token dictionary from ``look``
    without mutating any global theme dictionary.
    """
    if isinstance(look, dict):
        base = look.get("base") if look.get("base") in ("light", "dark") else "light"
        neon_effect = bool(look.get("neon_effect", False))
        ui_style = look.get("ui_style") if look.get("ui_style") in ("classic", "glass") else "classic"
        resolved_name = "neon" if neon_effect else base
        if neon_effect:
            src = NEON_LIGHT_BASE_TOKENS if base == "light" else NEON_DARK_BASE_TOKENS
        else:
            src = DARK_BASE_TOKENS if base == "dark" else LIGHT_BASE_TOKENS
        tokens = dict(src)
        stops = look.get("stops")
        accent = (
            look.get("button_hex")
            if (look.get("button_custom") and look.get("button_hex"))
            else (stops[0] if stops else None)
        )
        if accent:
            tokens["accent"] = accent
            tokens["accent_hover"] = mix_hex(accent, "#000000", 0.28)
    else:
        resolved_name = theme_name if theme_name in ("light", "dark", "neon", "neon_light") else "light"
        neon_effect = resolved_name in ("neon", "neon_light")
        base = "dark" if resolved_name in ("dark", "neon") else "light"
        if resolved_name == "neon_light":
            resolved_name = "neon"
            src = NEON_LIGHT_BASE_TOKENS
        elif resolved_name == "neon":
            src = NEON_DARK_BASE_TOKENS
        elif resolved_name == "dark":
            src = DARK_BASE_TOKENS
        else:
            src = LIGHT_BASE_TOKENS
        ui_style = "classic"
        tokens = dict(src)

    tokens["on_accent"] = pick_on_accent(tokens["accent"])
    tokens["on_accent_hover"] = pick_on_accent(tokens["accent_hover"])
    tokens["on_danger"] = pick_on_accent(tokens["danger"])
    tokens["on_warning"] = pick_on_accent(tokens["warning"])
    tokens["on_success"] = pick_on_accent(tokens["success"])
    stops = look.get("stops") if isinstance(look, dict) else None
    intensity = int(look.get("intensity", 0) or 0) if isinstance(look, dict) else 0
    adj_card, grad_avg, card_adjusted = adjust_card_bg_for_gradient(
        tokens["bg_card"],
        tokens["text"],
        stops,
        tokens["bg_window"],
        intensity,
    )
    tokens["gradient_avg"] = grad_avg
    tokens["card_bg_adjusted"] = card_adjusted
    if card_adjusted:
        tokens["bg_card"] = adj_card
        tokens["border_strong"] = mix_hex(tokens["border"], tokens["text_secondary"], 0.35)
    elif "border_strong" not in tokens:
        tokens["border_strong"] = mix_hex(tokens["border"], tokens["text_secondary"], 0.35)
    _sync_aliases(tokens)
    dark = is_dark_color(tokens["bg_window"])
    tokens["is_dark"] = dark
    tokens["base"] = base
    tokens["theme_name"] = resolved_name
    tokens["ui_style"] = ui_style
    tokens["neon_effect"] = neon_effect
    tokens["on_accent_dark"] = ON_ACCENT_DARK
    tokens["on_accent_light"] = ON_ACCENT_LIGHT
    tokens["kofi"] = KOFI_ACCENT
    tokens["kofi_hover"] = KOFI_HOVER
    tokens["preview_fallback_stops"] = PREVIEW_FALLBACK_STOPS
    tokens["knob_fg"] = pick_toggle_knob_color(tokens["accent"])
    tokens["glass_scrim_dark_alpha"] = 85 if (card_adjusted and dark) else 60
    tokens["glass_card_tint"] = (
        ((78, 36) if dark else (180, 120))
        if card_adjusted
        else ((58, 18) if dark else (65, 20))
    )
    tokens["radius_window"] = WINDOW_RADIUS
    tokens["radius_card"] = int(card_radius() if radius_card is None else radius_card)
    tokens["radius_button"] = int(button_radius() if radius_button is None else radius_button)
    tokens["space_4"] = SPACE_4
    tokens["space_8"] = SPACE_8
    tokens["space_12"] = SPACE_12
    tokens["space_16"] = SPACE_16
    tokens["space_24"] = SPACE_24
    return tokens


_DEFAULT_TOKENS_BY_NAME: dict[str, dict] = {
    name: build_tokens(
        theme_name=name,
        radius_card=DEFAULT_CARD_RADIUS,
        radius_button=DEFAULT_BUTTON_RADIUS,
    )
    for name in ("light", "dark", "neon")
}


def window_tokens(win) -> dict:
    """Resolve the active token dict for a top-level window or dialog.

    Returns the cached ``win.tokens`` in O(1) without calling ``build_tokens``
    on ``paintEvent`` paths. If ``win`` has no cached ``tokens`` yet (or if an
    external test/script directly mutated ``win.theme_name``), computes or looks
    up the token dict once and caches it on ``win.tokens`` so subsequent
    repaints do not rebuild tokens. When ``win is None`` (e.g. a parentless
    overlay widget), returns the pre-built module-level light token dict.
    """
    if win is not None:
        tok = getattr(win, "tokens", None)
        theme_name = getattr(win, "theme_name", None)
        if isinstance(tok, dict):
            if theme_name is None or tok.get("theme_name") == theme_name:
                return tok
        look = getattr(win, "look", None)
        if isinstance(look, dict) and (
            theme_name is None
            or ("neon" if look.get("neon_effect") else look.get("base")) == theme_name
        ):
            tok = build_tokens(look)
        elif isinstance(theme_name, str) and theme_name in _DEFAULT_TOKENS_BY_NAME:
            tok = _DEFAULT_TOKENS_BY_NAME[theme_name]
        else:
            tok = _DEFAULT_TOKENS_BY_NAME["light"]
        try:
            setattr(win, "tokens", tok)
        except Exception:
            pass
        return tok
    return _DEFAULT_TOKENS_BY_NAME["light"]


# ---- Pure QSS builders ------------------------------------------------------

def build_glass_button_qss(tokens: dict) -> str:
    """QSS for header QToolButtons while ui_style == 'glass'."""
    if tokens.get("is_dark", is_dark_color(tokens["bg_window"])):
        return """
            QToolButton[role="theme"] {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,40), stop:1 rgba(255,255,255,14));
                border: 1px solid rgba(255,255,255,55);
                border-bottom-color: rgba(0,0,0,90); border-radius: 8px;
            }
            QToolButton[role="theme"]:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,64), stop:1 rgba(255,255,255,26));
            }
            QToolButton[role="theme"]:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,22), stop:1 rgba(255,255,255,8));
            }
            """
    return """
        QToolButton[role="theme"] {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255,255,255,195), stop:1 rgba(255,255,255,120));
            border: 1px solid rgba(255,255,255,225);
            border-bottom-color: rgba(20,20,20,35); border-radius: 8px;
        }
        QToolButton[role="theme"]:hover {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255,255,255,230), stop:1 rgba(255,255,255,160));
        }
        QToolButton[role="theme"]:pressed {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255,255,255,140), stop:1 rgba(255,255,255,95));
        }
        """



def build_scroll_area_qss(tokens: dict, object_name: str | None = None) -> str:
    """Shared QSS builder for QScrollArea (conv_scroll and analysis_scroll):
    transparent frame/background, thin 8px rounded vertical scrollbar with
    border/text_secondary handle and zero-height arrow buttons across Light,
    Dark, Glass, and Neon themes.
    """
    t = tokens
    sel = f"QScrollArea#{object_name}" if object_name else "QScrollArea"
    return f"""
        {sel} {{
            background: transparent;
            border: none;
        }}
        {sel} QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 2px 1px 2px 1px;
            border: none;
        }}
        {sel} QScrollBar::handle:vertical {{
            background: {t['border']};
            border-radius: 4px;
            min-height: 24px;
        }}
        {sel} QScrollBar::handle:vertical:hover {{
            background: {t['text_secondary']};
        }}
        {sel} QScrollBar:vertical[inactive="true"]::handle:vertical,
        {sel} QScrollBar:vertical[inactive="true"]::handle:vertical:hover {{
            background: transparent;
        }}
        {sel} QScrollBar::add-line:vertical,
        {sel} QScrollBar::sub-line:vertical {{
            height: 0px;
            border: none;
            background: transparent;
        }}
        {sel} QScrollBar::add-page:vertical,
        {sel} QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
    """


def build_main_window_qss(
    tokens: dict,
    *,
    button_radius_px: int,
    chevron_icon: str,
    font: str,
    font_semi: str,
    mono: str,
    touch_slider_height: int = 40,
    ui_style: str = "classic",
) -> str:
    """Pure QSS builder for MainWindow."""
    t = tokens
    br = min(button_radius_px, 13)
    glass_gaps = """
        QSplitter#mainSplitter, QSplitter#mainSplitter::handle, QWidget#glassPanel {
            background: transparent;
        }
        """
    qss = f"""
        QWidget {{ background: {t['bg_window']}; color: {t['text']}; font-family: '{font}'; }}
        QWidget#mainWindowRoot {{ background: transparent; }}
        {glass_gaps}
        /* QFrame[role="card"] background/border is painted directly in
           Card.paintEvent() now (liquid-glass backdrop crop + tint +
           rim border) instead of through QSS - this selector is kept
           only so nothing else accidentally matches it. */
        QLabel[role="card-title"] {{
            font-family: '{font_semi}'; font-size: 10pt; font-weight: 600; color: {t['text']};
        }}
        QLabel {{ background: transparent; }}
        QLabel[role="muted"] {{ color: {t['text_secondary']}; font-size: 9pt; }}
        QLabel[role="info"] {{ color: {t['accent']}; font-weight: 600; font-size: 9pt; }}
        QLabel[role="good"] {{ color: {t['success']}; font-weight: 600; font-size: 9pt; }}
        QLabel[role="bad"] {{ color: {t['danger']}; font-weight: 600; font-size: 9pt; }}
        QLabel[role="warn"] {{ color: {t['warning']}; font-weight: 600; font-size: 9pt; }}
        QLabel[role="title"] {{ font-family: '{font_semi}'; font-size: 20pt; font-weight: 600; }}
        QLabel[role="subtitle"] {{ color: {t['text_secondary']}; }}
        QLabel[role="mono"] {{ font-family: '{mono}'; font-size: 9pt; color: {t['text_secondary']}; }}
        /* Small caps-style group header used to break the long CONVERSION
           card into scannable sections ("ENCODING" / "HDR TONE MAPPING" /
           "OUTPUT") instead of one undifferentiated wall of fields. Qt's
           style sheets don't support text-transform/letter-spacing, so the
           label text itself is upper-cased in code; this just supplies the
           colour/weight/divider.
           Uses TXT (the same full-contrast colour every ordinary field
           label like "Backend"/"Encoder" already uses), not MUTED - MUTED
           is one fixed mid-grey per base theme (light/dark/neon), tuned
           only against that theme's own flat BG. This app also lets a
           custom coloured gradient wash paint on top of BG (Color
           Intensity in "Customize your theme"), and a mid-grey has no
           contrast guarantee against an arbitrary user-picked gradient -
           it read as nearly invisible on a saturated pink/orange custom
           background even though it was "themed" correctly by base-theme
           rules. TXT is chosen precisely for max contrast against BG in
           every theme, so it carries that same guarantee here. Kept
           visually secondary (not "shouting" like INDIGO, which this app
           reserves for the primary action/progress/in-progress status)
           through smaller size + the divider line instead of colour. */
        QLabel[role="section-title"] {{
            color: {t['text_secondary']}; font-weight: 600; font-size: 8pt;
            padding-top: 6px; padding-bottom: 2px;
            border-bottom: 1px solid {t['border']}; margin-bottom: 2px;
        }}
        QLabel[role="field-label"] {{
            color: {t['text_secondary']}; font-size: 9pt;
        }}
        QPushButton[role="section-accordion"] {{
            background: transparent; border: none; border-bottom: 1px solid {t['border']};
            border-radius: 0px; color: {t['text_secondary']}; font-weight: 600; font-size: 8pt;
            text-align: left; padding: 6px 2px 3px 2px; margin-bottom: 2px;
        }}
        QPushButton[role="section-accordion"]:hover {{
            color: {t['text']}; background: transparent;
        }}
        QLineEdit, QComboBox, QSpinBox {{
            background: {t['bg_field']}; border: 1px solid {t['border']}; border-radius: {br}px; padding: 6px 8px;
        }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {t['accent']}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {t['accent']}; padding: 5px 7px; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {t['text_secondary']}; background: {t['bg_disabled']}; }}
        /* NOTE: focus/open borders are 2px with padding reduced by 1px
           (5px 7px vs the base 1px + 6px 8px) so the box size stays
           identical. Qt rounds "1.5px" up to 2px, which made every combo
           2px taller while focused/open and shifted everything below it
           (worst for Backend, the topmost combo in the card). */
        QComboBox:on {{ border: 2px solid {t['accent']}; padding: 5px 7px; }}
        /* RoundedComboBox can't rely on :on - that pseudo-state is set by
           QComboBox's own real showPopup(), which RoundedComboBox never
           calls (see its docstring). It sets this Qt property instead,
           toggled in RoundedComboPopup.open_below()/hideEvent(), for the
           exact same open-state border highlight. */
        QComboBox[popupOpen="true"] {{ border: 2px solid {t['accent']}; padding: 5px 7px; }}
        QComboBox#denoiseChipCombo {{ padding: 2px 20px 2px 8px; }}
        QComboBox#denoiseChipCombo:focus, QComboBox#denoiseChipCombo:on, QComboBox#denoiseChipCombo[popupOpen="true"] {{
            border: 2px solid {t['accent']}; padding: 1px 19px 1px 7px;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: border; subcontrol-position: top right;
            width: 24px; border: none; background: transparent;
        }}
        QComboBox::down-arrow {{
            image: none; width: 0px; height: 0px; border: none;
        }}
        QComboBox QAbstractItemView {{
            /* Square, not rounded - reverting an attempt at rounding this.
               QComboBox's popup is a native top-level window
               (QComboBoxPrivateContainer), not a plain child widget like
               Card, and border-radius here doesn't actually round its
               visible edge (this rule alone left it looking square
               regardless). The follow-up attempt - WA_TranslucentBackground
               on that popup window, the same technique that fixes Card's
               corner-triangle artifact - made it worse: the popup rendered
               as an opaque solid-black box instead of transparent,
               presumably because this window doesn't have proper alpha-
               compositing support the way a plain in-tree widget does.
               Reverted both. Properly rounding a native QComboBox popup
               would need a different approach (e.g. a custom popup widget
               instead of the native one) - untested and risky to attempt
               blind, so leaving this square rather than breaking it again. */
            background: {t['bg_card']}; border: 1px solid {t['border']}; border-radius: 0px;
            padding: 4px; outline: none;
        }}
        QComboBoxPrivateScroller {{
            background: {t['bg_card']}; border: none;
        }}
        QComboBox QAbstractItemView QScrollBar:vertical {{
            background: transparent; width: 10px; margin: 4px 2px 4px 0px; border: none;
        }}
        QComboBox QAbstractItemView QScrollBar::handle:vertical {{
            background: {t['border']}; border-radius: 4px; min-height: 24px;
        }}
        QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {{
            background: {t['text_secondary']};
        }}
        QComboBox QAbstractItemView QScrollBar::add-line:vertical,
        QComboBox QAbstractItemView QScrollBar::sub-line:vertical {{
            height: 0px; border: none; background: transparent;
        }}
        QComboBox QAbstractItemView QScrollBar::add-page:vertical,
        QComboBox QAbstractItemView QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 7px 10px; border-radius: 6px; min-height: 20px; color: {t['text']}; background: transparent;
        }}
        QComboBox QAbstractItemView::item:focus {{ border: none; outline: none; }}
        QComboBox QAbstractItemView::item:hover {{ background: {t['bg_card_alt']}; }}
        QComboBox QAbstractItemView::item:selected {{ background: {t['accent']}; color: {t['on_accent']}; }}
        QListWidget {{
            background: {t['bg_card']}; border: 1px solid {t['border']}; border-radius: {br}px;
            padding: 4px; outline: none; color: {t['text']};
        }}
        QListWidget::item {{
            padding: 0 4px; border-radius: 6px; background: transparent;
        }}
        QListWidget::item:hover {{ background: {t['bg_card_alt']}; }}
        QListWidget::item:selected {{ background: {t['accent']}; color: {t['on_accent']}; }}
        QListWidget::item:selected:!active {{ background: {t['accent']}; color: {t['on_accent']}; }}
        QToolButton#queueItemRemoveBtn {{
            border: none;
            border-radius: 9px;
            background: transparent;
            padding: 0;
            margin: 0;
        }}
        QToolButton#queueItemRemoveBtn:hover {{
            background: {t['bg_card_alt']};
        }}
        QSpinBox[role="stepper-spin"] {{
            padding: 0px 2px; text-align: center; qproperty-alignment: AlignCenter;
        }}
        QSpinBox[role="stepper-spin"]::up-button, QSpinBox[role="stepper-spin"]::down-button {{
            width: 0px; border: none;
        }}
        QPushButton[role="stepper"] {{
            /* Classic mode no longer paints from this rule -
               GlassButton._paint_classic_stepper() draws the circle by
               hand instead (border-radius here rounded non-uniformly at
               fractional DPI, same bug RoundHandleSlider works around).
               font-size stays live since drawText() still reads self.font(). */
            background: transparent; border: 1px solid {t['border']}; border-radius: 13px;
            padding: 0px 0px 2px 0px; font-size: 11pt; color: {t['text']};
        }}
        QPushButton[role="stepper"]:hover {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; }}
        QPushButton[role="stepper"]:pressed {{ background: {t['accent_hover']}; color: {t.get('on_accent_hover', t['on_accent'])}; }}
        QPushButton[role="stepper"]:disabled {{ color: {t['text_secondary']}; background: transparent; border-color: {t['border']}; }}
        QLabel[role="stepper-overlay"] {{
            background: {t['bg_field']}; color: {t['text']}; border: none; padding: 0px;
        }}
        {build_button_states_qss(t, button_radius_px)}
        QPushButton[role="stop-ready"] {{ background: {t['danger']}; color: {t.get('on_danger', pick_on_accent(t['danger']))}; border: none; min-height: 20px; }}
        QPushButton[role="stop-ready"]:hover {{ background: {t['danger_hover']}; }}
        QPushButton[role="pause-ready"] {{ background: {t['warning']}; color: {t.get('on_warning', pick_on_accent(t['warning']))}; border: none; min-height: 20px; }}
        QPushButton[role="pause-ready"]:hover {{ background: {t['warning_hover']}; }}
        QPushButton[role="resume-ready"] {{ background: {t['success']}; color: {t.get('on_success', pick_on_accent(t['success']))}; border: none; min-height: 20px; }}
        QPushButton[role="resume-ready"]:hover {{ background: {t['success_hover']}; }}
        QPushButton[role="theme"], QToolButton[role="theme"] {{
            background: {t['bg_card']}; border: 1px solid {t['border']}; border-radius: {br}px; padding: 7px 10px;
        }}
        QToolButton[role="theme"]::menu-indicator {{ image: none; width: 0px; }}
        QPushButton[role="kofi"] {{
            background: transparent; color: {t['text']}; border: 1px solid {t['kofi']}; border-radius: {br}px;
        }}
        QPushButton[role="kofi"]:hover {{
            background: rgba(255, 94, 91, 28); color: {t['text']}; border: 1px solid {t['kofi_hover']};
        }}
        QPushButton[role="toggle"] {{
            background: transparent; border: none; color: {t['text_secondary']}; font-family: '{font_semi}'; text-align: left;
        }}
        QPushButton[role="toggle"]:hover {{ color: {t['accent']}; }}
        QFrame[role="toolbar"] {{
            background: {t['bg_window']}; border: 1px solid {t['border']}; border-radius: {br}px;
        }}
        QPushButton[role="preview-view"] {{
            background: transparent; color: {t['accent']}; border: 1.5px solid {t['accent']};
            border-radius: {br}px; padding: 5px 16px;
        }}
        QPushButton[role="preview-view"]:hover {{ background: {t['accent']}; color: {t['on_accent']}; }}
        QPushButton[role="preview-save"] {{
            background: transparent; color: {t['success']}; border: 1.5px solid {t['success']};
            border-radius: {br}px; padding: 5px 16px;
        }}
        QPushButton[role="preview-save"]:hover {{ background: {t['success']}; color: white; }}
        QPushButton[role="preview-view"]:disabled, QPushButton[role="preview-save"]:disabled {{
            background: transparent; color: {t['text_secondary']}; border-color: {t['border']};
        }}
        QPushButton[role="icon-btn"] {{
            background: transparent; color: {t['text_secondary']}; border: 1.5px solid {t['border']};
            border-radius: {br}px; font-size: 13pt; padding: 0px;
        }}
        QPushButton[role="icon-btn"]:hover {{ background: {t['bg_card_alt']}; color: {t['accent']}; border-color: {t['accent']}; }}
        QPushButton[role="icon-btn"]:disabled {{ color: {t['border']}; border-color: {t['border']}; background: transparent; }}
        QPushButton[role="icon-btn-ready"] {{
            background: {t['success']}; color: white; border: 1.5px solid {t['success']};
            border-radius: {br}px; font-size: 13pt; padding: 0px;
        }}
        QPushButton[role="icon-btn-ready"]:hover {{ background: {t['success_hover']}; border-color: {t['success_hover']}; }}
        QPushButton[role="icon-btn-busy"] {{
            background: {t['warning']}; color: white; border: 1.5px solid {t['warning']};
            border-radius: {br}px; font-size: 13pt; padding: 0px;
        }}
        QPushButton[role="icon-btn-busy"]:hover {{ background: {t['warning_hover']}; border-color: {t['warning_hover']}; }}
        QSlider {{ min-height: {touch_slider_height}px; background: transparent; }}
        QSlider::groove:horizontal {{
            /* 2px left/right margin keeps the groove (and so the handle's
               travel range) inset from the widget's exact edges, so the
               hand-painted handle's outline stroke has room to render
               fully at the min/max ends instead of getting clipped by
               the widget boundary. */
            height: 6px; border-radius: 3px; background: {t['border']}; border: none; margin: 0px 2px;
        }}
        QSlider::sub-page:horizontal {{
            height: 6px; border-radius: 3px; background: {t['accent']}; border: none; margin: 0px 2px;
        }}
        QSlider::add-page:horizontal {{
            height: 6px; border-radius: 3px; background: {t['border']}; border: none; margin: 0px 2px;
        }}
        QSlider::handle:horizontal {{
            /* Invisible - only here so QSlider reserves a 22x22 hit-test
               slot at the right position; RoundHandleSlider paints the
               actual visible handle itself (see class docstring). Size
               matches HANDLE_D (22px, level with ToggleSwitch's track
               height); vertical margin -9px brings the total tap zone
               back up to TOUCH_SLIDER_HEIGHT (22 + 2*9 = 40px). */
            width: 22px; height: 22px; margin: -9px 0;
            background: transparent; border: none;
        }}
        /* Compact variant for the two Advanced-filters sliders (Brightness/
           Saturation) - they don't need the full touch-sized 40px groove
           the primary Quality/Bitrate sliders get. This has to override
           BOTH rules above via the same [role=...] attribute-selector
           pattern used everywhere else in this stylesheet, not a plain
           widget.setFixedHeight(28) call: the bare QSlider min-height:40px
           rule above is a Qt style-sheet box-model property, and those
           take precedence over a C++-side setFixedHeight/setMinimumSize/
           setMaximumSize call on the same widget once any style sheet is
           in play - fighting it from Python produced a widget with
           contradictory min/max heights (40 vs 28) and the broken,
           off-centre handle this replaces. The handle's own vertical
           margin is rescaled too (-3px instead of -9px), since -9px
           assumes a 40px-tall slider (22 + 2*9 = 40) and would overflow
           a 28px one (22 + 2*9 = 40 > 28).
        */
        QSlider[role="compact-slider"] {{ min-height: 28px; max-height: 28px; }}
        QSlider[role="compact-slider"]::handle:horizontal {{
            width: 22px; height: 22px; margin: -3px 0;
            background: transparent; border: none;
        }}
        QSlider[role="tier-slider"] {{ min-height: 26px; max-height: 26px; }}
        QSlider[role="tier-slider"]::handle:horizontal {{
            width: 18px; height: 18px; margin: -1px 0;
            background: transparent; border: none;
        }}
        QPlainTextEdit[role="log"] {{
            background: transparent; color: {t['log_fg']}; border: none;
            font-family: '{mono}'; font-size: 9pt;
        }}
        QSplitter::handle {{ background: transparent; margin: 0 2px; }}
        QSplitter::handle:hover {{ background: transparent; }}
        {build_scroll_area_qss(t, 'convScroll')}
        {build_scroll_area_qss(t, 'analysisScroll')}
        """
    if ui_style == "glass":
        qss += build_glass_button_qss(t)
    return qss


def build_tooltip_qss(tokens: dict, *, button_radius_px: int) -> str:
    t = tokens
    br = min(button_radius_px, 13)
    return f"""
            QLabel {{
                background: {t['bg_card']}; color: {t['text']}; border: 1px solid {t['border']};
                border-radius: {br}px; padding: 8px 10px; font-size: 9pt;
            }}
        """


def build_combo_view_qss(tokens: dict) -> str:
    t = tokens
    return f"""
                QAbstractItemView {{
                    background-color: {t['bg_card']}; color: {t['text']};
                    border: 1px solid {t['border']}; outline: none;
                }}
                QAbstractItemView::item {{
                    padding: 7px 10px; border-radius: 6px; min-height: 20px;
                    color: {t['text']}; background-color: transparent;
                }}
                QAbstractItemView::item:hover {{ background-color: {t['bg_card_alt']}; }}
                QAbstractItemView::item:selected {{ background-color: {t['accent']}; color: {t['on_accent']}; }}
            """


def build_tray_menu_qss(tokens: dict) -> str:
    t = tokens
    return f"""
            QMenu {{ background: {t['bg_card']}; color: {t['text']};
                     border: 1px solid {t['border']}; border-radius: 10px; padding: 6px; }}
            QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; margin: 1px 0px; }}
            QMenu::item:selected {{ background: {t['accent']}; color: {t['on_accent']}; }}
            QMenu::item:disabled {{ color: {t['text_secondary']}; background: transparent; }}
            QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}
        """


def build_message_box_qss(tokens: dict) -> str:
    t = tokens
    return f"""
            QMessageBox {{ background: {t['bg_card']}; }}
            QMessageBox QLabel {{ background: transparent; color: {t['text']}; }}
            QPushButton {{
                background: {t['bg_card_alt']}; color: {t['text']}; border: 1px solid {t['border']};
                border-radius: 8px; padding: 8px 16px; min-width: 76px;
            }}
            QPushButton:hover {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; }}
            QPushButton:disabled {{ color: {t['text_secondary']}; background: {t['bg_disabled']}; border-color: {t['border']}; }}
            QPushButton:default {{ border: 1.5px solid {t['accent']}; }}
        """


def build_mini_window_qss(tokens: dict, button_radius_px: int | None = None) -> str:
    t = tokens
    br = int(t.get("radius_button", 8) if button_radius_px is None else button_radius_px)
    return f"""
            QWidget#miniRoot {{ background: transparent; }}
            QLabel {{ color: {t['text']}; background: transparent; }}
            {build_button_states_qss(t, br)}
        """


def build_theme_dialog_qss(tokens: dict, button_radius_px: int | None = None) -> str:
    t = tokens
    br = min(int(t.get("radius_button", button_radius()) if button_radius_px is None else button_radius_px), 13)
    return f"""
            QDialog {{ background: transparent; }}
            QLabel {{ color: {t['text']}; background: transparent; }}
            QLineEdit {{
                background: {t['bg_field']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: {br}px; padding: 5px 8px;
            }}
            QToolButton {{
                background: {t['bg_field']}; border: 1px solid {t['border']};
                border-radius: {br}px; padding: 4px;
            }}
            QToolButton:disabled {{ opacity: 0.5; }}
            QComboBox {{
                background: {t['bg_field']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: {br}px; padding: 4px 26px 4px 8px;
            }}
            QComboBox::drop-down {{ border: none; width: 22px; background: transparent; }}
            QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; border: none; }}
            QComboBox QAbstractItemView {{
                background: {t['bg_card']}; color: {t['text']};
                selection-background-color: {t['accent']};
            }}
            {build_button_states_qss(t, br)}
            QPushButton[role="preset-segment-btn"] {{ padding: 5px 6px; }}
            {build_scroll_area_qss(t, 'settingsContentScroll')}
        """


def build_progress_bar_qss(tokens: dict, theme_name: str, height: int) -> str:
    t = tokens
    gr_r, gr_g, gr_b = parse_hex_rgb(t["bg_disabled"])
    gr_a = 110 if theme_name == "light" else 95
    bd_r, bd_g, bd_b = parse_hex_rgb(t["border"])
    bd_a = 175
    ch_r, ch_g, ch_b = parse_hex_rgb(t["accent"])
    ch2_r, ch2_g, ch2_b = parse_hex_rgb(t["accent_hover"])
    radius = height // 2
    return f"""
        QProgressBar {{
            background: rgba({gr_r}, {gr_g}, {gr_b}, {gr_a});
            border: 1px solid rgba({bd_r}, {bd_g}, {bd_b}, {bd_a});
            border-radius: {radius}px;
            color: {t['text']};
            font-weight: 600;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba({ch_r}, {ch_g}, {ch_b}, 225),
                stop:1 rgba({ch2_r}, {ch2_g}, {ch2_b}, 225));
            border-radius: {radius}px;
        }}
    """
