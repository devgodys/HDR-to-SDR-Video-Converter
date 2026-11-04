from __future__ import annotations

import math
import random

from PySide6.QtCore import (
    Qt, QRectF, QRect, QPoint, QPointF,
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QBrush, QLinearGradient, QImage,
)
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QFrame, QGraphicsDropShadowEffect,
)

from hdr2sdr.theme.metrics import card_radius
from hdr2sdr.theme.palettes import (
    NEON_BG_EFFECTS, THEMES, _THEME_DEFAULT_INTENSITY, _neon_active, _theme_is_dark,
)
from hdr2sdr.theme.theme_tokens import window_tokens
from hdr2sdr.ui.glass import (
    _glass_backdrop_for, draw_rim_border, paint_glass_path, add_dither,
)
from hdr2sdr.ui.style_helpers import set_role


# Corner radius of every frameless top-level window (main, mini, dialogs),
# in LOGICAL pixels - Qt scales it itself, never multiply by the device
# pixel ratio by hand. 8 matches native Windows 11 top-level windows.
# Background, border and mask all read this one constant so they can't
# drift apart. Cards inside keep their own larger Card.RADIUS on purpose.
WINDOW_RADIUS = 8
WINDOW_BORDER = 1.0


def window_radius(win):
    """Effective outer radius: a maximized/fullscreen window fills the
    screen, so its corners are square (0)."""
    try:
        if win.isMaximized() or win.isFullScreen():
            return 0
    except Exception:
        pass
    return WINDOW_RADIUS


class _TitleBarDragArea(QWidget):
    """Wraps a frameless window's own header row (title, and whatever
    buttons sit in it) so the row doubles as a drag handle. Only needed
    because the window is frameless (see MiniModeWindow._frameless /
    MainWindow._frameless) - a normal window gets this for free from the
    OS's own title bar. windowHandle().startSystemMove() hands the
    actual drag off to the window manager (correct behaviour on every
    monitor/DPI setting, edge-snapping included) rather than this app
    tracking mouse deltas itself. Clicks that land on a real child
    widget (a button in the row) are consumed by that widget first and
    never reach this mousePressEvent, so those stay clickable.

    The whole widget is drag-only, full stop - an earlier version tried
    to also forward presses very close to its top edge into
    MainWindow's own startSystemResize(TopEdge) instead, on the theory
    that the sliver of dead space that left above an undivided titlebar
    was worse. It wasn't: dragging the window by that same sliver still
    reads as "try to move it", and a resize gesture only reacts to
    vertical motion - drag mostly sideways, which is how people actually
    grab a title bar, and a resize-in-progress does nothing, which reads
    as the exact same dead zone this was meant to fix. Reliable drag
    across the *entire* row wins over also supporting resize-by-the-
    very-top-pixel specifically, which is a much rarer gesture (the
    other three edges + all four corners still resize normally - see
    MainWindow.mousePressEvent/mouseMoveEvent - this only gives up
    growing a window upward from its own top edge).

    Double-click toggles maximize/restore, same as any native title bar -
    only wired up if the window actually has a working toggle to call
    (MainWindow does, via toggle_maximize_restore(); MiniModeWindow
    doesn't support maximizing at all, so this is simply a no-op there)."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            toggle = getattr(self.window(), "toggle_maximize_restore", None)
            if toggle is not None:
                toggle()
        super().mouseDoubleClickEvent(event)


def paint_frameless_surface(widget, radius, border_hex, paint_fn, ring=None):
    """Paint a frameless top-level window's whole surface with smooth
    rounded corners and a 1px border.

    `paint_fn(painter)` draws the contents (backdrop, effects, ...) over
    the widget's full rect. QPainter.setClipPath()/setMask() aren't
    antialiased on the raster engine (stair-stepped corners), so instead:
    paint into an offscreen buffer at the real device-pixel ratio - 16 bits
    per channel, dithered right before the 8-bit rounding (see
    glass.add_dither: that is what keeps slow dark gradients from banding)
    - then multiply by an antialiased rounded-rect mask (DestinationIn)
    and draw the result once. The border is a solid fill of the OUTER
    shape underneath the content, with the mask following the INNER
    contour, so each edge is antialiased exactly once with no see-through
    seam. `ring` (optional QColor) is laid over the border colour first,
    e.g. the neon accent outline."""
    w = WINDOW_BORDER
    rect = QRectF(widget.rect())
    outer = QPainterPath()
    outer.addRoundedRect(rect, radius, radius)
    inner = QPainterPath()
    inner.addRoundedRect(rect.adjusted(w, w, -w, -w), max(radius - w, 0), max(radius - w, 0))

    dpr = widget.devicePixelRatioF()
    img = QImage(max(1, round(widget.width() * dpr)), max(1, round(widget.height() * dpr)),
                 QImage.Format.Format_RGBA64_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    bp = QPainter(img)
    bp.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_fn(bp)
    bp.end()
    add_dither(img)

    # Mask = a full-size image (not fillPath on the buffer): composition
    # modes only touch pixels a draw call covers, so DestinationIn with
    # just the path would leave the corners untouched.
    mask_img = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    mask_img.setDevicePixelRatio(dpr)
    mask_img.fill(Qt.GlobalColor.transparent)
    mp = QPainter(mask_img)
    mp.setRenderHint(QPainter.RenderHint.Antialiasing)
    mp.fillPath(inner, QColor(0, 0, 0, 255))
    mp.end()
    bp = QPainter(img)
    bp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    bp.drawImage(0, 0, mask_img)
    bp.end()
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)

    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(outer, QColor(border_hex))
    if ring is not None:
        painter.fillPath(outer, ring)
    painter.drawImage(0, 0, img)
    painter.end()


class _BgLayer(QWidget):
    """Sits behind every other child of MainWindow. Every Glass* widget
    crops its own region out of a blurred snapshot of *this* widget
    specifically (not of the window as a whole), so that snapshot can
    never recursively contain any card/control that's already glass -
    it's always just the plain background underneath everything.

    Painted with a few large, muted colour-accent glows (theme INDIGO/
    GREEN/AMBER) instead of a flat fill - a single flat colour blurs
    into the same flat colour, so the glass crop over it reads as
    nothing more than a tinted border; the accents give the blur
    something to actually reveal.

    Static (phase fixed at 0) - an earlier version drifted the glow
    centres on a timer for a "surface is alive" effect, but that meant
    a background QTimer re-grabbing and re-blurring the window every
    ~1.4s just to sit idle, which showed up as constant background CPU
    use. Not worth the cost for a subtle ambient effect, so this now
    behaves like the plain classic backdrop: blurred once on build/
    resize/theme-change and left alone until something actually
    changes."""

    def __init__(self, parent=None):
        super().__init__(parent)
    def paintEvent(self, event):
        win = self.window()
        if not getattr(win, "_frameless", False):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint_contents(painter, win, frameless=False)
            painter.end()
            return

        # Frameless (rounded window) path. QPainter.setClipPath() is *not*
        # antialiased on the raster engine - the hard clip edge is what made
        # the window's rounded corners look stair-stepped even though the
        # border stroke on top of it was smooth. Instead: paint everything
        # unclipped into an offscreen ARGB buffer at the screen's real
        # device-pixel ratio, then multiply it by an antialiased rounded-
        # rect mask (DestinationIn) so the corner alpha is smooth, and draw
        # that buffer onto the window once.
        t = window_tokens(win)
        paint_frameless_surface(
            self, window_radius(win), t["border"],
            lambda bp: self._paint_contents(bp, win, frameless=True))

    def _paint_contents(self, painter, win, frameless):
        theme_name = getattr(win, "theme_name", "light")
        t = window_tokens(win)
        is_dark = _theme_is_dark(t)

        painter.fillRect(self.rect(), QColor(t["bg_window"]))

        self._paint_backdrop(painter, t, theme_name, is_dark)

        # ---- dark-mode scrim: every Glass* widget crops its material
        # from this layer, so darkening it here (once) darkens every
        # glass surface in the app at once. Sits over the backdrop rather
        # than under it so the accent colours are muted/moodier, not
        # brighter - full-strength colour under dark glass read as a too-
        # bright wash rather than dark tinted glass. Only glass ui_style
        # crops this layer at all, so it's a no-op elsewhere. ----
        if is_dark and getattr(win, "ui_style", "classic") == "glass":
            painter.fillRect(self.rect(), QColor(0, 0, 0, t.get("glass_scrim_dark_alpha", 60)))

        effect = self._effect_for(win, theme_name)
        w, h = self.width(), self.height()
        if effect == "grid":
            self._paint_grid_effect(painter, t, is_dark, w, h)
        elif effect == "dots":
            self._paint_dots_effect(painter, t, is_dark, w, h)
        elif effect == "diagonal":
            self._paint_diagonal_effect(painter, t, is_dark, w, h)
        elif effect == "waves":
            self._paint_waves_effect(painter, t, is_dark, w, h)
        elif effect == "rings":
            self._paint_rings_effect(painter, t, is_dark, w, h)
        elif effect == "crosshatch":
            self._paint_crosshatch_effect(painter, t, is_dark, w, h)
        elif effect == "stars":
            self._paint_stars_effect(painter, t, is_dark, w, h)

    @staticmethod
    def _effect_for(win, theme_name):
        custom = win._get_theme_custom(theme_name) if hasattr(win, "_get_theme_custom") else None
        effect = (custom or {}).get("effect", "none")
        return effect if effect in NEON_BG_EFFECTS else "none"

    def _paint_backdrop(self, painter, t, theme_name, is_dark):
        """Backdrop shared by every theme - light/dark/neon, classic or
        glass alike (see ThemeCustomizeDialog: one panel, one storage
        shape, one painting routine, instead of neon getting a real
        gradient and every other theme getting a fixed 3-glow ambient
        fill). If the user has picked a custom gradient via the
        "Customize your theme" panel, that's a real angled QLinearGradient
        across the 2-3 chosen stops, opacity driven by the panel's Color
        Intensity slider. Otherwise this falls back to two saturated
        corner glows in that theme's own INDIGO/RED - each theme's factory
        look, before anyone has ever opened the panel. On top of either,
        an optional overlay effect (grid / dots / none - the panel's
        "Background Effect" picker) is drawn by the caller. This never
        needs a live backdrop crop - Cards paint their own flat fill over
        it - so it's cheap enough to draw straight into this static,
        rarely-repainted layer."""
        w, h = self.width(), self.height()
        win = self.window()
        custom = win._get_theme_custom(theme_name) if hasattr(win, "_get_theme_custom") else None
        stops = (custom or {}).get("stops")

        if stops:
            direction = custom.get("direction", 180)
            intensity = custom.get("intensity", _THEME_DEFAULT_INTENSITY.get(theme_name, 60))
            angle = math.radians(direction)
            # Project a line through the rect's centre at `angle`, long
            # enough to span the diagonal, so every direction from 0-360
            # sweeps corner-to-corner rather than clipping short at the
            # widget's edges for non-axis-aligned angles.
            diag = math.hypot(w, h) / 2.0
            cx, cy = w / 2.0, h / 2.0
            dx, dy = math.cos(angle) * diag, math.sin(angle) * diag
            grad = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
            n = max(1, len(stops) - 1)
            alpha = round(255 * (intensity / 100.0))
            for i, hex_color in enumerate(stops):
                color = QColor(hex_color)
                color.setAlpha(alpha)
                grad.setColorAt(i / n if n else 0.0, color)
            painter.fillRect(self.rect(), grad)
        # No gradient picked: the plain flat base (the Light / Dark presets).

    @staticmethod
    def _paint_grid_effect(painter, t, is_dark, w, h):
        """The city-grid of thin lines the neon backdrop always used to
        draw unconditionally - now one selectable option among several
        (see NEON_BG_EFFECTS) rather than baked in."""
        grid = QColor(t["accent"])
        grid.setAlpha(20 if is_dark else 14)
        painter.setPen(QPen(grid, 1))
        step = 42
        for x in range(0, w, step):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            painter.drawLine(0, y, w, y)

    @staticmethod
    def _paint_dots_effect(painter, t, is_dark, w, h):
        """Alternative overlay to the grid: a sparse field of small filled
        dots at the same 42px spacing the grid uses, for a starfield/
        halftone feel instead of ruled lines."""
        dot = QColor(t["accent"])
        dot.setAlpha(45 if is_dark else 32)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(dot))
        step = 42
        r = 1.4
        for x in range(step // 2, w, step):
            for y in range(step // 2, h, step):
                painter.drawEllipse(QPointF(x, y), r, r)

    @staticmethod
    def _paint_diagonal_effect(painter, t, is_dark, w, h):
        """Diagonal hatch lines - the grid effect's slanted sibling, same
        accent colour and step spacing, just swept at 45 degrees for a
        less rigid, more "fabric weave" texture."""
        line = QColor(t["accent"])
        line.setAlpha(18 if is_dark else 13)
        painter.setPen(QPen(line, 1))
        step = 34
        # Sweep lines across the full w+h diagonal span (not just 0..w)
        # so the pattern still covers every corner regardless of aspect
        # ratio, the same way the angled gradient backdrop above does.
        for offset in range(-h, w + h, step):
            painter.drawLine(offset, 0, offset + h, h)

    @staticmethod
    def _paint_waves_effect(painter, t, is_dark, w, h):
        """A handful of horizontal sine-wave ribbons - a softer, more
        organic overlay than the grid/dots/diagonal options, for anyone
        who wants texture without straight rulings."""
        wave = QColor(t["accent"])
        wave.setAlpha(30 if is_dark else 20)
        painter.setPen(QPen(wave, 1.4))
        amplitude, wavelength, spacing = 9, 90, 52
        for base_y in range(0, h + spacing, spacing):
            path = QPainterPath()
            path.moveTo(0, base_y)
            x = 0
            while x <= w:
                y = base_y + amplitude * math.sin((x / wavelength) * 2 * math.pi)
                path.lineTo(x, y)
                x += 8
            painter.drawPath(path)

    @staticmethod
    def _paint_rings_effect(painter, t, is_dark, w, h):
        """Concentric rings expanding from the widget's centre - a
        radar/sonar-style texture, distinct from the evenly-tiled grid/
        dots/diagonal/waves options."""
        ring = QColor(t["accent"])
        ring.setAlpha(24 if is_dark else 17)
        painter.setPen(QPen(ring, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = w / 2.0, h / 2.0
        max_r = math.hypot(max(cx, w - cx), max(cy, h - cy))
        step = 46
        r = step
        while r <= max_r:
            painter.drawEllipse(QPointF(cx, cy), r, r)
            r += step

    @staticmethod
    def _paint_crosshatch_effect(painter, t, is_dark, w, h):
        """The diagonal effect's two-directions sibling: the same
        45-degree lines as _paint_diagonal_effect, plus a mirrored set
        sweeping the other way, so the two crossing sets read as a
        woven-fabric texture rather than one-directional hatching."""
        line = QColor(t["accent"])
        line.setAlpha(13 if is_dark else 9)
        painter.setPen(QPen(line, 1))
        step = 34
        for offset in range(-h, w + h, step):
            painter.drawLine(offset, 0, offset + h, h)
        for offset in range(-h, w + h, step):
            painter.drawLine(offset, 0, offset - h, h)

    @staticmethod
    def _paint_stars_effect(painter, t, is_dark, w, h):
        """A sparse scatter of variable-size dots at fixed, seeded
        positions - a starfield/confetti texture rather than the dots
        effect's even grid. Seeded with a constant so the pattern is
        stable across repaints (no flicker) instead of re-randomising
        on every paint."""
        star = QColor(t["accent"])
        painter.setPen(Qt.PenStyle.NoPen)
        rng = random.Random(1337)
        cell = 46
        for gx in range(0, w + cell, cell):
            for gy in range(0, h + cell, cell):
                if rng.random() < 0.55:
                    continue
                x = gx + rng.uniform(-cell * 0.4, cell * 0.4)
                y = gy + rng.uniform(-cell * 0.4, cell * 0.4)
                r = rng.uniform(0.8, 2.4)
                star.setAlpha(round(rng.uniform(18, 45) if is_dark else rng.uniform(12, 30)))
                painter.setBrush(QBrush(star))
                painter.drawEllipse(QPointF(x, y), r, r)


class Card(QFrame):
    """A titled card in the "liquid glass" material: a live-cropped,
    blurred slice of whatever's behind it (see MainWindow._bg_layer /
    get_blurred_bg()) instead of a flat fill, plus a tint gradient and
    a gradient rim-light border - the same material GlassButton uses,
    so panels and floating controls read as one consistent surface
    rather than glass being reserved for buttons alone. Falls back to
    a flat CARD-coloured fill in classic ui_style, or if used somewhere
    with no MainWindow ancestor (get_blurred_bg unavailable), so it
    never renders as a blank/broken widget.

    Same title-label-in-flow layout as before, so the existing generic
    QLabel i18n pass in retranslate_static_ui() still picks up the
    title automatically - no separate translation wiring needed."""

    RADIUS = 16

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Without this, the four small corner triangles outside the
        # rounded-rect clip path (paintEvent only fills *inside* it)
        # default to opaque black instead of showing whatever's behind
        # the card - visible as a little dark wedge at each rounded
        # corner once the background behind cards is no longer a flat
        # opaque fill (glass mode's blur/glow, or the transparent
        # splitter gutter next to it).
        #
        # An earlier version of this also had paintEvent pre-fill those
        # wedges itself (matching colour/backdrop) instead of trusting
        # this attribute alone, to chase a black corner that showed up
        # specifically on the log drawer's side_card. That turned out to
        # be the wrong fix: painting the wedges opaque changes what
        # QGraphicsDropShadowEffect (see the shadow below) treats as this
        # widget's silhouette when it generates the drop shadow - instead
        # of a clean rounded shadow, the shadow picks up a visible square
        # notch at each corner, on every card, which is worse than the
        # original bug. Reverted; whatever made side_card's corners go
        # solid black needs a fix that doesn't touch what paintEvent
        # itself fills.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._hover_pos = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        self.title_label = QLabel(title)
        set_role(self.title_label, "card-title")
        outer.addWidget(self.title_label)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 45))
        # Not applied via setGraphicsEffect() any more - every card's
        # elevation shadow is gone now, not just the log drawer's. Still
        # built and kept as self._shadow (never attached to the widget,
        # so it costs nothing and draws nothing) purely so apply_theme()'s
        # existing card._shadow.setColor/setBlurRadius/setOffset calls
        # for neon-glow re-tuning keep working untouched rather than
        # needing their own removal.
        self._shadow = shadow

    def mouseMoveEvent(self, event):
        self._hover_pos = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_pos = None
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = window_tokens(self.window())
        style = getattr(self.window(), "ui_style", "classic")
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, card_radius(), card_radius())

        if style != "glass":
            painter.setClipPath(path)
            painter.fillPath(path, QColor(t["bg_card"]))
            painter.setClipping(False)
            if _neon_active(self.window()):
                border = QColor(t["border"])
                painter.setPen(QPen(border, 1.0))
            else:
                painter.setPen(QPen(QColor(t["border"]), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), card_radius(), card_radius())
            # QFrame's own paintEvent would repaint on top of what we just
            # drew - see the note below about why it's never called here.
            return

        bg = _glass_backdrop_for(self)
        is_dark = _theme_is_dark(t)
        # Was (30, 8) for dark. That thin a veil only reads as "glass" over
        # a backdrop with real tonal range - once the base dark theme went
        # near-black, this made every card blend into the window instead of
        # lifting off it, which is the "mud" look. Bumped closer to (but
        # still under) light's (65, 20) so dark-mode cards actually get a
        # visible edge again instead of nearly vanishing into the backdrop.
        tint_top, tint_bottom = t.get("glass_card_tint", (58, 18) if is_dark else (65, 20))
        if bg is not None and not bg.isNull():
            top_left = self.mapTo(self.window(), QPoint(0, 0))
            src_rect = QRect(top_left, self.size())
            paint_glass_path(painter, path, rect, card_radius(), bg, src_rect, tint_top, tint_bottom,
                              hover_pos=self._hover_pos)
        else:
            painter.setClipPath(path)
            painter.fillPath(path, QColor(t["bg_card"]))
            painter.setClipping(False)
        draw_rim_border(painter, rect, card_radius())
        # Neon+glass: layer the same accent-coloured edge classic-mode
        # neon cards use (softer alpha, since the neutral rim-light above
        # already gives the glass edge) on top of the glass material, so
        # a neon card still reads as neon rather than turning into
        # generic neutral glass once the glass effect is switched on.
        if _neon_active(self.window()):
            border = QColor(t["border"])
            painter.setPen(QPen(border, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), card_radius(), card_radius())
