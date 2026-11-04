from __future__ import annotations

from PySide6.QtCore import (
    Qt, QRectF, QRect, QPoint, QPointF,
)
from PySide6.QtGui import (
    QColor, QImage, QPainter, QPainterPath, QPen, QBrush, QLinearGradient, QRadialGradient,
)
from PySide6.QtWidgets import (
    QPushButton, QGraphicsDropShadowEffect,
)

from hdr2sdr.theme.metrics import button_radius
from hdr2sdr.theme.palettes import (
    _neon_active, _theme_is_dark,
)
from hdr2sdr.theme.theme_tokens import pick_on_accent, window_tokens
from hdr2sdr.ui.glass import (
    _glass_backdrop_for, _mix_color, draw_refractive_backdrop,
)
from hdr2sdr.ui.icons import _draw_icon_glyph




class GlassButton(QPushButton):
    """QPushButton that, in glass mode, paints itself in the "Liquid
    Glass" material (Apple's iOS 26 / macOS Tahoe language, which this
    app's glass mode follows): a real cropped-and-blurred slice of
    whatever's behind the button - the same backdrop Card used to use
    before content panels went flat - stands in for actual light
    passing through glass, topped with a thin colour/neutral veil and
    a short, bright highlight arc concentrated at the top of the rim
    (the way a lens catches light at its edge) rather than a big
    diagonal sheen painted across the whole face - that flat glossy
    wash is what reads as dated Aero/Vista-style glass, not this.
    Falls back to super().paintEvent() in classic mode (or outside
    MainWindow, where there's no backdrop to sample), so every existing
    QPushButton(...) call site can use this drop-in replacement
    unchanged - same text/icon/checked/hover/disabled behaviour.

    Buttons that already carry a status colour via their "role"
    property (Start, Stop/Pause/Resume, Ko-fi, ready/busy icon chips,
    a checked panel-toggle) get that colour as a glass *tint* over the
    real backdrop rather than a flat fill. Everything else renders as
    neutral (colourless) glass."""

    # role -> accent colour key(s) in THEMES to tint this button with.
    _TINT_ROLES = {
        "go": "accent", "stop-ready": "danger", "pause-ready": "warning",
        "resume-ready": "success", "icon-btn-ready": "success", "icon-btn-busy": "warning",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Same fix as Card's WA_TranslucentBackground: without it the
        # widget's own rectangular backing store is opaque outside our
        # rounded clip path, and QGraphicsDropShadowEffect composites
        # that whole opaque rect (not just the pill's alpha shape) -
        # visible as a hard-edged, blocky rectangular halo hugging every
        # round/pill button instead of a soft shadow following its shape.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._hover = False
        self.setMouseTracking(True)
        self._hover_pos = None
        # A soft drop shadow gives the pill a little elevation off the
        # background even where fill/border contrast alone isn't quite
        # enough (e.g. a near-white button on a near-white light-theme
        # background) - kept subtle so it reads as depth, not a badge.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 25))
        shadow.setEnabled(False)  # apply_theme() turns this on/off per ui_style
        self.setGraphicsEffect(shadow)
        self._shadow = shadow

    def initStyleOption(self, option):
        super().initStyleOption(option)
        if option.text and "&" in option.text:
            option.text = option.text.replace("&&", "&").replace("&", "&&")

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._hover_pos = None
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        self._hover_pos = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def _tint_hex(self, t):
        role = self.property("role") or ""
        if role == "kofi":
            return None
        key = self._TINT_ROLES.get(role)
        if key is not None:
            return t[key]
        if role in ("panel-toggle", "preset-segment-btn") and self.isCheckable() and self.isChecked():
            if self.property("drawer_header"):
                return None
            return t["accent"]
        return None

    def _icon_tint_hex(self, t, win):
        """Accent colour for an icon_kind glyph specifically (as opposed
        to _tint_hex above, which tints the whole button as a coloured
        glass fill) - for the small set of icon-only header buttons
        (theme swatch, appearance toggle) that want to stand out from
        the neutral-glass majority (Add videos, Remove, Output, ...)
        without becoming a filled/tinted pill themselves. Set via the
        "icon_accent" property:
          "always" - always this theme's accent colour (theme_customize_btn
                     - a colour swatch that should read as one regardless
                     of mode, glass or classic).
          "auto"   - accent colour only in glass mode or neon (mode_btn -
                     mirrors the earlier header icon accent colouring:
                     `INDIGO if (ui_style == "glass" or neon) else TXT`).
          unset/anything else - no accent, falls through to the plain
                     TXT/MUTED colour every other icon_kind button uses
                     (language_button/globe stays this way deliberately -
                     it was never accented even before this button became
                     a GlassButton)."""
        mode = self.property("icon_accent") or ""
        if mode == "always":
            return t["accent"]
        if mode == "auto" and (getattr(win, "ui_style", "classic") == "glass" or _neon_active(win)):
            return t["accent"]
        return None

    def _paint_classic_stepper(self):
        """Hand-painted circle for the classic-mode -/+ stepper buttons.

        QSS `border-radius: 13px` on a 26x26 QPushButton looks like it
        should paint a perfect circle, but Qt's style engine is prone to
        the same per-axis DPI rounding as the QSlider handle QSS was
        (see RoundHandleSlider's docstring) - on a fractional display
        scale it can come out visibly squashed (measured ~39px wide by
        ~34px tall on one screenshot) rather than round, and noticeably
        smaller/differently-sized than the slider handle next to it even
        though both are nominally 26px. Painting the ring with
        QPainter.drawEllipse() on one shared diameter for both axes
        guarantees an actual circle and keeps it the same on-screen size
        as RoundHandleSlider's handle (same 26px source size, same
        immunity to the rounding bug).
        """
        win = self.window()
        t = window_tokens(win)
        enabled = self.isEnabled()
        pressed = self.isDown()
        hover = self._hover

        if not enabled:
            border, fill, text_color = QColor(t["border"]), None, QColor(t["text_secondary"])
        elif pressed:
            border, fill, text_color = QColor(t["accent_hover"]), QColor(t["accent_hover"]), QColor(pick_on_accent(t["accent_hover"]))
        elif hover:
            border, fill, text_color = QColor(t["accent"]), QColor(t["accent"]), QColor(pick_on_accent(t["accent"]))
        else:
            border, fill, text_color = QColor(t["border"]), None, QColor(t["text"])

        # Inset by half the stroke width so the ring's outer edge stays
        # inside the widget bounds instead of getting clipped.
        d = min(self.width(), self.height()) - 1.5
        rect = QRectF((self.width() - d) / 2.0, (self.height() - d) / 2.0, d, d)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        # Draw the +/- as geometric strokes, not text: this mirrors the
        # glass-mode stepper (see its paintEvent comment) - a "+"/"\u2212"
        # glyph's ink position depends on the font's own hinting/rounding,
        # so no amount of ascent/descent or bounding-box math reliably
        # centers it (that's what the last two attempts here ran into).
        # Two lines centered on the circle's own center are exact by
        # construction instead of measured.
        pen = QPen(text_color, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        half = d * 0.22
        cx, cy = rect.center().x(), rect.center().y()
        painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
        if self.text() == "+":
            painter.drawLine(QPointF(cx, cy - half), QPointF(cx, cy + half))
        painter.end()

    def _paint_lead_icon(self, painter, color):
        """A drawn glyph (property "lead_icon") just left of the label -
        for buttons like "Surprise me" / "Revert" and compact Compare actions.
        Combined (icon + gap + text) is centred horizontally so the icon never
        collides with the left border."""
        kind = self.property("lead_icon")
        if not kind or not self.text():
            return
        compact = bool(self.property("compact_action"))
        size = 13 if compact else 15
        gap = 5 if compact else 6.5
        text_w = self.fontMetrics().horizontalAdvance(self.text().replace("&&", "&"))
        cx, cy = QRectF(self.rect()).center().x(), QRectF(self.rect()).center().y()
        if kind == "check" and not compact:
            gap = 5.0
            glyph_left = 4.8 * (size / 22.0)
            glyph_w = (17.2 - 4.8) * (size / 22.0)
            visual_total_w = glyph_w + gap + text_w
            x = max(5.0, cx - visual_total_w / 2.0 - glyph_left + 1.0)
        else:
            total_w = size + gap + text_w
            x = 7.0 if compact else max(8.0, cx - total_w / 2.0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(x, cy - size / 2.0)
        painter.scale(size / 22.0, size / 22.0)
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _draw_icon_glyph(painter, kind, color)
        painter.restore()

    def paintEvent(self, event):
        win = self.window()
        if getattr(win, "ui_style", "classic") != "glass":
            if (self.property("role") or "") == "stepper":
                self._paint_classic_stepper()
                return
            super().paintEvent(event)
            # super().paintEvent() draws self.icon() via the native style -
            # but icon-only buttons here carry their glyph as icon_kind
            # (see _draw_icon_glyph()'s docstring for why), which the
            # native style knows nothing about, so nothing got drawn for
            # them in classic mode. Painted as a second pass on top of the
            # already-finished native background/border, in the same
            # colour classic mode already uses for this button's text.
            if self.property("lead_icon") and self.text():
                t = window_tokens(win)
                lead = QPainter(self)
                role_val = self.property("role") or ""
                tint_hex = self._tint_hex(t) if self.isEnabled() else None
                if role_val == "kofi":
                    lead_col = QColor(t.get("kofi", "#FF5E5B"))
                elif tint_hex is not None:
                    lead_col = QColor(pick_on_accent(tint_hex))
                else:
                    lead_col = QColor(t["text"] if self.isEnabled() else t["text_secondary"])
                self._paint_lead_icon(lead, lead_col)
                lead.end()
            icon_kind = self.property("icon_kind")
            if icon_kind and not self.text():
                t = window_tokens(win)
                override_hex = self.property("icon_override_color")
                accent_hex = self._icon_tint_hex(t, win) if self.isEnabled() else None
                if override_hex:
                    color = QColor(override_hex if self.isEnabled() else "#71717A")
                else:
                    color = QColor(accent_hex if accent_hex is not None
                                    else (t["text"] if self.isEnabled() else t["text_secondary"]))
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                size = min(self.iconSize().width(), self.iconSize().height())
                # QRectF, not QRect: QRect.center() truncates to an int
                # ((left + right) / 2 with right = left + width - 1), so on
                # any button the icon landed 0.5-1px up-left of the true
                # centre. glass mode already used QRectF and was fine.
                c = QRectF(self.rect()).center()
                painter.translate(c.x() - size / 2.0, c.y() - size / 2.0)
                painter.scale(size / 22.0, size / 22.0)
                pen = QPen(color, 1.7)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                _draw_icon_glyph(painter, icon_kind, color)
                painter.end()
            return

        t = window_tokens(win)
        is_dark = _theme_is_dark(t)
        enabled = self.isEnabled()
        pressed = self.isDown()
        tint_hex = self._tint_hex(t) if enabled else None

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        # Rectangular corners, matching classic mode's border-radius: 8px,
        # rather than a full stadium/pill - only the round -/+ steppers
        # stay circular, same as they already are in classic (their QSS
        # there is 13px on a 26px button, i.e. also height/2).
        role = self.property("role") or ""
        radius = rect.height() / 2.0 if role == "stepper" else float(button_radius())
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        # ---- offscreen buffer for every fill clipped to `path` below
        # (backdrop crop, veil, lensing arc, hover catch-light).
        # setClipPath() on the real widget painter isn't antialiased on
        # Qt's raster engine - a hard binary cut, not a soft alpha edge -
        # which is invisible at a window's 16px corner radius but very
        # visible at this button's 8px radius (the "buttons look jagged,
        # window doesn't" bug). Same fix as the main window's rounded
        # corners: paint the clipped content into its own ARGB buffer at
        # the real device-pixel-ratio, multiply it by an antialiased
        # rounded-rect mask (CompositionMode_DestinationIn), and blit
        # that once onto the widget - the rim stroke and text below are
        # unaffected, since drawRoundedRect()/drawText() were already
        # being drawn antialiased and unclipped (painter.setClipping
        # (False) below), so they aren't routed through this buffer.
        dpr = self.devicePixelRatioF()
        buf = QImage(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)),
                     QImage.Format.Format_ARGB32_Premultiplied)
        buf.setDevicePixelRatio(dpr)
        buf.fill(Qt.GlobalColor.transparent)
        real_painter, painter = painter, QPainter(buf)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ---- base material: a real crop of the blurred backdrop
        # (same source Card used before content panels went flat)
        # stands in for light actually passing through glass - this is
        # what makes the pill read as seeing *through* something, not
        # as a tinted plastic chip. Falls back to a flat neutral fill
        # where no backdrop is available (e.g. isolated use outside
        # MainWindow). ----
        painter.setClipPath(path)
        bg = _glass_backdrop_for(self)
        if bg is not None and not bg.isNull():
            top_left = self.mapTo(win, QPoint(0, 0))
            src_rect = QRectF(QRect(top_left, self.size()))
            # Small edge band/zoom here vs Card's - a button is a fraction
            # of a card's size, so the same absolute band would eat most
            # of its face; kept subtle enough to still read as a rim, not
            # a warped centre.
            draw_refractive_backdrop(painter, rect, radius, bg, src_rect, edge_band=4.0, edge_zoom=1.10)
        else:
            painter.fillPath(path, QColor(t["bg_card"]))

        # ---- veil: colour (accent buttons) or neutral wash over the
        # real backdrop above. Accent buttons are pushed close to fully
        # opaque - a lot of backdrop bleeding through is what was
        # muddying/dulling the colour, and a vivid solid-reading colour
        # is more legible as "this is the action button" than a pastel
        # tint anyway; the material still reads as glass from the real
        # blurred backdrop at the true-translucent rim/highlight edges
        # and from neutral (untinted) buttons, which stay properly
        # translucent. ----
        veil = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        if tint_hex is not None:
            hover_mix = 0.10 if (self._hover and not pressed) else 0.0
            top_c = _mix_color(tint_hex, "#FFFFFF", hover_mix) if hover_mix else QColor(tint_hex)
            top_c.setAlpha(255 if not pressed else 235)
            bot_c = _mix_color(tint_hex, "#000000", 0.12 if is_dark else 0.08)
            bot_c.setAlpha(255 if not pressed else 240)
        else:
            if is_dark:
                top_a = (40 if pressed else (70 if self._hover else 50))
                bot_a = (15 if pressed else (30 if self._hover else 10))
            else:
                top_a = (100 if pressed else (160 if self._hover else 110))
                bot_a = (40 if pressed else (70 if self._hover else 30))
            top_c = QColor(255, 255, 255, min(top_a, 255))
            bot_c = QColor(255, 255, 255, bot_a)
        if not enabled:
            top_c.setAlpha(top_c.alpha() // 3)
            bot_c.setAlpha(max(bot_c.alpha() // 3, 6))
        veil.setColorAt(0.0, top_c)
        veil.setColorAt(1.0, bot_c)
        painter.fillPath(path, QBrush(veil))

        # ---- lensing highlight: real glass concentrates its brightest
        # light where the material curves away from the viewer, at the
        # edge - not as a wash painted across the whole face (that big
        # diagonal "glossy dome" is exactly what reads as dated
        # Aero/Vista glass, not modern liquid glass). This strokes a
        # short bright arc hugging just the top of the rim and lets it
        # fall off fast toward the sides, like a lens catching light at
        # one point rather than glowing all over. ----
        if enabled:
            hl_rect = rect.adjusted(0.6, 0.6, -0.6, -0.6)
            hl_radius = hl_rect.height() / 2.0 if role == "stepper" else max(radius - 0.6, 0.0)
            hl_path = QPainterPath()
            hl_path.addRoundedRect(hl_rect, hl_radius, hl_radius)
            if is_dark:
                peak = 55 if pressed else 100
            else:
                peak = 90 if pressed else 165
            edge = QLinearGradient(hl_rect.topLeft(), QPointF(hl_rect.left(), hl_rect.bottom()))
            edge.setColorAt(0.0, QColor(255, 255, 255, peak))
            edge.setColorAt(0.24, QColor(255, 255, 255, 0))
            edge.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(QPen(QBrush(edge), 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(hl_path)

        # ---- cursor-following catch light: the same idea as Card's old
        # hover highlight - keeps glass responsive to interaction, which
        # is as much a part of the liquid-glass material as its look. ----
        if enabled and self._hover and not pressed and self._hover_pos is not None:
            spec = QRadialGradient(self._hover_pos, rect.height() * 1.6)
            spec.setColorAt(0.0, QColor(255, 255, 255, 55 if is_dark else 75))
            spec.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, QBrush(spec))

        painter.setClipping(False)

        # Mask has to cover the whole buffer, not just fillPath(path) on
        # the buffer's own painter - CompositionMode_DestinationIn only
        # touches pixels an actual draw call covers, so filling just the
        # rounded path leaves the square corners *outside* it untouched
        # (still opaque) instead of turning them transparent.
        mask = QImage(buf.size(), QImage.Format.Format_ARGB32_Premultiplied)
        mask.setDevicePixelRatio(dpr)
        mask.fill(Qt.GlobalColor.transparent)
        mp = QPainter(mask)
        mp.setRenderHint(QPainter.RenderHint.Antialiasing)
        mp.fillPath(path, QColor(0, 0, 0, 255))
        mp.end()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask)
        painter.end()

        painter = real_painter
        painter.drawImage(0, 0, buf)

        # ---- rim: crisp and edge-only - the lensing arc above now
        # carries the "shine", so this just needs to keep the pill's
        # outline clean against a background close in tone to the fill,
        # not do double duty as the highlight the way it used to. ----
        if role == "kofi":
            kofi_c = QColor(t.get("kofi_hover" if self._hover else "kofi", "#FF5E5B"))
            if not enabled:
                kofi_c.setAlpha(102)
            painter.setPen(QPen(kofi_c, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
        else:
            rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
            if is_dark:
                rim.setColorAt(0.0, QColor(255, 255, 255, 120))
                rim.setColorAt(0.5, QColor(255, 255, 255, 20))
                rim.setColorAt(1.0, QColor(255, 255, 255, 40))
            else:
                rim.setColorAt(0.0, QColor(255, 255, 255, 255))
                rim.setColorAt(0.5, QColor(255, 255, 255, 100))
                rim.setColorAt(1.0, QColor(255, 255, 255, 200))
            painter.setPen(QPen(QBrush(rim), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)

        # ---- label: drawn directly with drawText()/QIcon.paint() rather
        # than through the style's CE_PushButtonLabel, which (via
        # QStyleSheetStyle, since the app is stylesheet-driven) applies
        # the button's own QSS box-model padding/icon-spacing on top of
        # our already-custom frame - that's what was nudging text like
        # the round -/+ steppers' off-centre. Icon-only buttons (Live
        # Preview's action row) hit the icon branch below instead of
        # drawText() with an empty string, which would otherwise just
        # silently paint nothing. ----
        painter.setFont(self.font())
        # Slightly under full opacity - glass mode's own material is
        # translucent, so fully opaque (255-alpha) text/icon next to it
        # reads as a harder, flatter layer sitting on top rather than
        # part of the same soft surface.
        icon_accent_hex = self._icon_tint_hex(t, win) if enabled else None
        if tint_hex is not None:
            eff_bg = _mix_color(top_c.name(), bot_c.name(), 0.5).name()
            text_color = QColor(pick_on_accent(eff_bg))
            text_color.setAlpha(232)
        elif icon_accent_hex is not None:
            text_color = QColor(icon_accent_hex)
            text_color.setAlpha(225)
        elif not enabled:
            text_color = QColor(t["text"])
            text_color.setAlpha(102)
        elif role in ("panel-toggle", "preset-segment-btn") and not self._hover:
            text_color = QColor(t["text_secondary"])
            text_color.setAlpha(255)
        else:
            text_color = QColor(t["text"])
            text_color.setAlpha(240)
        painter.setPen(QPen(text_color))
        text = self.text().replace("&&", "&")
        icon_kind = self.property("icon_kind")
        if not text and icon_kind:
            # Drawn live at this button's own resolution/colour instead of
            # going through a pre-baked pixmap (icon().pixmap(iconSize())
            # then a SourceIn tint pass, which is what this used to do) -
            # that raster-then-rescale-then-recolour route made icons look
            # visibly softer than the rest of the app's hand-painted
            # controls. See _draw_icon_glyph()'s docstring.
            size = min(self.iconSize().width(), self.iconSize().height())
            painter.save()
            painter.translate(rect.center().x() - size / 2.0, rect.center().y() - size / 2.0)
            painter.scale(size / 22.0, size / 22.0)
            pen = QPen(text_color, 1.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setOpacity(0.91)
            _draw_icon_glyph(painter, icon_kind, text_color)
            painter.restore()
        elif role == "stepper":
            # Drawing "+"/"\u2212" as text at all was the real source of
            # the lingering off-centre minus - no amount of bounding-box
            # math fixes a glyph whose own hinting/rounding nudges its
            # ink a fraction of a px from where the font metrics say it
            # is. Drawing the two strokes directly instead sidesteps
            # fonts entirely: they're centred on the button's own
            # geometric centre (== the circle's centre, since radius is
            # exactly rect.height()/2), which is exact by construction
            # rather than measured.
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(text_color, 1.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            half = min(rect.width(), rect.height()) * 0.22
            cx, cy = rect.center().x(), rect.center().y()
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
            if text == "+":
                painter.drawLine(QPointF(cx, cy - half), QPointF(cx, cy + half))
        else:
            # Every other label: plain ascent/descent-box centering,
            # same as classic mode's QSS-driven buttons use - keeps
            # ordinary text (which almost always lacks a descender in
            # this app's short labels) sitting at the same vertical spot
            # glass and classic modes agree on, instead of drifting
            # label-by-label with however much ink each string happens
            # to have.
            if role == "kofi" and text.startswith("♥ "):
                heart_str = "♥ "
                rest_str = text[len(heart_str):]
                fm = painter.fontMetrics()
                hw = fm.horizontalAdvance(heart_str)
                rw = fm.horizontalAdvance(rest_str)
                total_w = hw + rw
                start_x = rect.center().x() - total_w / 2.0
                heart_rect = QRectF(start_x, rect.top(), hw, rect.height())
                rest_rect = QRectF(start_x + hw, rect.top(), rw, rect.height())
                hc = QColor(t.get("kofi", "#FF5E5B"))
                hc.setAlpha(232 if enabled else 102)
                painter.setPen(QPen(hc))
                painter.drawText(heart_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, heart_str)
                painter.setPen(QPen(text_color))
                painter.drawText(rest_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, rest_str)
            elif self.property("lead_icon") and text:
                if self.property("lead_icon") == "check" and not bool(self.property("compact_action")):
                    size = 15
                    gap = 6.5
                    glyph_w = (17.2 - 4.8) * (size / 22.0)
                    shift = (glyph_w + gap) / 2.0
                else:
                    size = 16
                    gap = 6
                    shift = (size + gap) / 2.0
                text_rect = rect.adjusted(shift, 0.0, shift, 0.0)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
            else:
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            self._paint_lead_icon(painter, text_color)


class TitleBarButton(QPushButton):
    """Small round hover-highlight button for a frameless window's own
    title bar (minimize "-", close "x") - ported from liquid_glass_kit.py's
    TitleBarButton, adapted to read this app's own THEMES dict (same key
    names: RED/BORDER/TXT) instead of carrying a second copy of the
    palette. Intentionally much simpler than GlassButton (flat circle,
    no gradient/blur/glass-vs-classic split) - a window control needs to
    read as "system chrome" at a glance, not as another piece of app
    content, the same reason native minimize/close buttons everywhere
    are plain glyphs rather than styled like the app's own buttons."""

    # text given by callers -> vector glyph (see paintEvent)
    _GLYPHS = {"\u2013": "minimize", "\u25a1": "maximize", "\u2752": "restore", "\u00d7": "close"}

    def __init__(self, symbol, danger=False, parent=None):
        super().__init__(symbol, parent)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._danger = danger
        self._hover = False

    def initStyleOption(self, option):
        super().initStyleOption(option)
        if option.text and "&" in option.text:
            option.text = option.text.replace("&&", "&").replace("&", "&&")

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        win = self.window()
        t = window_tokens(win)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._hover:
            painter.setBrush(QColor(t["danger"]) if self._danger else QColor(t["border"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect)
        text_color = QColor(pick_on_accent(t["danger"])) if (self._hover and self._danger) else QColor(t["text"])
        glyph = self._GLYPHS.get(self.text())
        if glyph is None:
            painter.setPen(QPen(text_color))
            painter.setFont(self.font())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
            return
        # Drawn as vector lines on the button's exact centre instead of as a
        # font glyph: "-", the square and "x" sit at different heights and
        # widths in every font (the "-" rides low, the "x" high), which is
        # what made the three controls look off-centre and uneven.
        pen = QPen(text_color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        c = QRectF(self.rect()).center()
        h = 5.0                                   # half the glyph size
        if glyph == "minimize":
            painter.drawLine(QPointF(c.x() - h, c.y()), QPointF(c.x() + h, c.y()))
        elif glyph == "maximize":
            painter.drawRect(QRectF(c.x() - h, c.y() - h, 2 * h, 2 * h))
        elif glyph == "restore":
            d = 2.0                               # offset of the back square
            painter.drawRect(QRectF(c.x() - h, c.y() - h + d, 2 * h - d, 2 * h - d))
            painter.drawPolyline([QPointF(c.x() - h + d, c.y() - h + d), QPointF(c.x() - h + d, c.y() - h),
                                  QPointF(c.x() + h, c.y() - h), QPointF(c.x() + h, c.y() + h - d),
                                  QPointF(c.x() + h - d, c.y() + h - d)])
        else:                                     # close
            painter.drawLine(QPointF(c.x() - h, c.y() - h), QPointF(c.x() + h, c.y() + h))
            painter.drawLine(QPointF(c.x() - h, c.y() + h), QPointF(c.x() + h, c.y() - h))
