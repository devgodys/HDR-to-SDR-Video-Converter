from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QColor, QFontDatabase, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QPushButton, QComboBox,
)
from hdr2sdr.theme.theme_tokens import (
    FONT_BODY_CANDIDATES,
    FONT_MONO_CANDIDATES,
    FONT_SEMI_CANDIDATES,
    build_progress_bar_qss,
)


def resolve_font_from_families(
    candidates: list[str] | tuple[str, ...],
    available_families: list[str] | tuple[str, ...],
    default: str = "Segoe UI",
) -> str:
    """Pure helper: resolve the first matching font family (case-insensitive)
    from ``available_families``, falling back to ``default``.
    """
    fams = {f.lower(): f for f in available_families}
    for c in candidates:
        if c.lower() in fams:
            return fams[c.lower()]
    return default


def pick_font(candidates=FONT_BODY_CANDIDATES, default="Segoe UI"):
    return resolve_font_from_families(candidates, QFontDatabase.families(), default)


# ---- small widget helpers -----------------------------------------------

def set_role(widget, role):
    """Attach a QSS-selectable role (e.g. label colour, button colour) and
    force a restyle - Qt caches style output per-widget, so a property
    change alone doesn't repaint until you unpolish/polish."""
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()  # GlassButton reads "role" straight from paintEvent()
    # rather than QSS, so it needs an explicit repaint here too - polish()
    # alone schedules a style-sheet refresh, not necessarily our own paint.


def style_progress_bar(pbar, t, theme_name):
    """Theme a plain QProgressBar (used for both the Pro mode PROGRESS card
    and the Mini mode window) with the current theme's colours.

    A plain QProgressBar with QSS avoids the dark background artifacts that
    translucent custom child-widget painting caused on some Windows systems.
    Qt's normal style pipeline draws the groove and chunk, which stay
    semi-transparent so the bar reads as part of its card.
    """
    pbar.setStyleSheet(build_progress_bar_qss(t, theme_name, pbar.height()))


def _attach_press_bounce(btn: QPushButton):
    """Small spring-like press feedback: the button dips inward on
    press and overshoots back to size on release, instead of the flat
    instant colour swap QSS alone gives you. Geometry-based rather than
    a custom scale property, so it needs nothing beyond QPropertyAnimation
    on the widget's own built-in "geometry" property."""
    anim = QPropertyAnimation(btn, b"geometry", btn)
    state = {"orig": None}

    def on_pressed():
        state["orig"] = btn.geometry()
        anim.stop()
        anim.setDuration(70)
        anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        anim.setStartValue(btn.geometry())
        anim.setEndValue(state["orig"].adjusted(2, 2, -2, -2))
        anim.start()

    def on_released():
        if state["orig"] is None:
            return
        anim.stop()
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.setStartValue(btn.geometry())
        anim.setEndValue(state["orig"])
        anim.start()

    btn.pressed.connect(on_pressed)
    btn.released.connect(on_released)


def chevron_icon_path(hex_color, size=20):
    """Render (and cache on disk) a small down-chevron PNG in the given
    colour, for use as QComboBox::down-arrow - QSS can't draw a shape
    itself, and the native OS triangle sits inside its own boxed-off
    ::drop-down section with a divider line next to it. One flat icon,
    one fixed colour, no per-state variants: that's what caused the
    earlier "doubled arrow" glitch (a second, differently-styled image
    swapped in on hover and briefly rendered on top of the first)."""
    cache_dir = Path(tempfile.gettempdir()) / "hdr2sdr_ui_icons"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"chevron_{hex_color.lstrip('#')}_{size}.png"
    if not path.exists():
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(hex_color))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        w, h = size * 0.5, size * 0.26
        cx, cy = size / 2, size / 2
        arrow = QPainterPath()
        arrow.moveTo(cx - w / 2, cy - h / 2)
        arrow.lineTo(cx, cy + h / 2)
        arrow.lineTo(cx + w / 2, cy - h / 2)
        painter.drawPath(arrow)
        painter.end()
        pix.save(str(path), "PNG")
    return str(path).replace("\\", "/")


def loosen_combo(combo, min_chars=12):
    """QComboBox defaults to sizing itself so its *widest item* always fits
    without eliding - with entries like "ST2094-40 (HDR10+) . GPU
    libplacebo/Vulkan" that alone sets a large floor under how narrow the
    whole panel can ever get, no matter how far a splitter is dragged.
    Switching to a fixed content-length budget lets the widget (and
    everything containing it) shrink freely; the current selection just
    elides with "..." instead, which is fine since the full text is still
    visible in the dropdown and via hover tooltip."""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(min_chars)
    combo.currentTextChanged.connect(combo.setToolTip)
    combo.setToolTip(combo.currentText())
