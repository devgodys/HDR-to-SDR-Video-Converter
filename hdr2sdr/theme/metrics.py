"""Corner rounding the person can set in the theme panel ("Card rounding",
"Button rounding"): one shared, app-wide value each, read by every Card /
GlassButton paint and by the stylesheet at the moment it paints, so changing
it needs no widget rebuilding."""
from __future__ import annotations

DEFAULT_CARD_RADIUS = 16
DEFAULT_BUTTON_RADIUS = 8
MAX_CARD_RADIUS = 28
MAX_BUTTON_RADIUS = 14       # QSS border-radius larger than half the button height renders square

_radii = {"card": DEFAULT_CARD_RADIUS, "button": DEFAULT_BUTTON_RADIUS}


def card_radius() -> int:
    return _radii["card"]


def button_radius() -> int:
    return _radii["button"]


def set_radii(card=None, button=None) -> None:
    if card is not None:
        _radii["card"] = max(0, min(MAX_CARD_RADIUS, int(card)))
    if button is not None:
        _radii["button"] = max(0, min(MAX_BUTTON_RADIUS, int(button)))


def load_radii(settings) -> None:
    """Read the saved values (QSettings); bad/missing ones fall back to the defaults."""
    def _get(key, default):
        try:
            return int(settings.value(key, default))
        except (TypeError, ValueError):
            return default
    set_radii(_get("radius_card", DEFAULT_CARD_RADIUS), _get("radius_button", DEFAULT_BUTTON_RADIUS))
