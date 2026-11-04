from __future__ import annotations

from PySide6.QtCore import (
    Qt, QSize, QRectF, QPointF,
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QIcon, QPixmap,
)

from hdr2sdr.paths import ROOT
from hdr2sdr.theme.theme_tokens import pick_on_accent




def _swatch_icon(hex_color, size=22):
    """Filled rounded square, edge-to-edge with only a hairline rim -
    the neon accent-colour picker button's icon. Deliberately not
    routed through the shared glyph renderer (which uses a
    single *outline* colour per icon; this one's entire job is to show
    a filled swatch of an arbitrary, user-chosen colour). Insets by just
    1px (barely more than the rim stroke itself needs for antialiasing)
    combined with the swatch buttons' own zero-padding/transparent QSS
    (see _rebuild_swatches/the button-colour swatch) so the colour reads
    as filling the whole button, not sitting inside a visible ring of
    the button's own FIELD-coloured background."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(1, 1, size - 2, size - 2)
    painter.setBrush(QColor(hex_color))
    painter.setPen(QPen(QColor(255, 255, 255, 110), 1.2))
    painter.drawRoundedRect(r, 4, 4)
    painter.end()
    return QIcon(pixmap)


def _pipette_icon(hex_color, size=36):
    """A rounded colour tile with a colour-drop glyph on it - the
    "pick a colour from the screen" button. The tile is filled with the
    colour currently being edited; the glyph is dark or light, whichever
    contrasts with it."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    tile = QColor(hex_color)
    painter.setBrush(tile)
    painter.setPen(QPen(QColor(255, 255, 255, 110), 1.2))
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.22, size * 0.22)

    ink = QColor(pick_on_accent(tile.name()))
    u = size / 36.0
    painter.translate(size / 2, size / 2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    # a water drop - the universally understood "colour" glyph (the first
    # hand-drawn pipette read as a syringe, the second as a microphone)
    drop = QPainterPath()
    drop.moveTo(0, -12.5 * u)
    drop.cubicTo(2.6 * u, -7.5 * u, 8.6 * u, -2.6 * u, 8.6 * u, 3.4 * u)
    drop.arcTo(QRectF(-8.6 * u, -5.2 * u, 17.2 * u, 17.2 * u), 0, -180)
    drop.cubicTo(-8.6 * u, -2.6 * u, -2.6 * u, -7.5 * u, 0, -12.5 * u)
    drop.closeSubpath()
    painter.drawPath(drop)
    # small highlight, in the tile colour at partial strength, so it reads as glossy
    hl = QColor(tile)
    hl.setAlpha(150)
    painter.setBrush(hl)
    painter.drawEllipse(QRectF(-5.2 * u, 1.2 * u, 3.2 * u, 5.4 * u))
    painter.end()
    return QIcon(pixmap)


def _draw_icon_glyph(painter, kind, color):
    """Draw icon glyph paths directly so a
    GlassButton can paint one directly at its own live resolution/colour
    instead of going through a pre-baked, fixed-colour 22x22 QPixmap - the
    raster-then-rescale-then-recolour route (icon().pixmap(iconSize()),
    then a SourceIn tint pass) that GlassButton.paintEvent() used briefly
    made icons look visibly softer than the rest of the app's hand-painted
    controls (sliders, gradient bars, ...), which never round-trip through
    a pixmap at all. Assumes the caller has already set up a 22x22
    coordinate space (translate/scale) and the pen colour; only the fill
    colour for filled glyphs is looked up here, from `color`."""
    if kind == "globe":
        # Circle + meridian + equator + two latitude lines (chords of the
        # circle at +-3.8 from centre, so they end exactly on the rim).
        painter.drawEllipse(QPointF(11, 11), 7.6, 7.6)
        painter.drawEllipse(QPointF(11, 11), 3.3, 7.6)
        painter.drawLine(QPointF(3.4, 11), QPointF(18.6, 11))
        for dy in (-3.9, 3.9):
            half = (7.6 ** 2 - dy ** 2) ** 0.5
            painter.drawLine(QPointF(11 - half, 11 + dy), QPointF(11 + half, 11 + dy))
    elif kind == "sun":
        painter.drawEllipse(7, 7, 8, 8)
        for x1, y1, x2, y2 in ((11, 2, 11, 4.5), (11, 17.5, 11, 20), (2, 11, 4.5, 11), (17.5, 11, 20, 11),
                                (4.6, 4.6, 6.4, 6.4), (15.6, 15.6, 17.4, 17.4),
                                (17.4, 4.6, 15.6, 6.4), (6.4, 15.6, 4.6, 17.4)):
            painter.drawLine(x1, y1, x2, y2)
    elif kind in ("drop", "drop_filled"):
        # Liquid-glass style toggle icon: a simple water-drop. Outline for
        # the light-glass mode, filled for dark-glass - same fill-vs-
        # outline convention as sun (outline, light) vs moon (filled,
        # dark) below, so the single combined mode button reads
        # consistently: outline = light variant, filled = dark variant.
        path = QPainterPath()
        path.moveTo(11, 2.5)
        path.cubicTo(15.5, 8.5, 18, 12, 18, 14.5)
        path.cubicTo(18, 18.6, 14.9, 21.5, 11, 21.5)
        path.cubicTo(7.1, 21.5, 4, 18.6, 4, 14.5)
        path.cubicTo(4, 12, 6.5, 8.5, 11, 2.5)
        path.closeSubpath()
        if kind == "drop_filled":
            painter.setBrush(QColor(color))
        painter.drawPath(path)
    elif kind == "palette":
        # Artist's palette: a rounded blob with a bite taken out of the
        # lower right (the thumb notch), one hole, and four paint dabs -
        # the dabs are what make it read as "palette" rather than a
        # generic blob/cloud at 22px.
        path = QPainterPath()
        path.moveTo(11, 2.8)
        path.cubicTo(16.4, 2.8, 19.8, 6.4, 19.8, 10.6)
        path.cubicTo(19.8, 13.9, 17.6, 14.9, 15.6, 14.6)
        path.cubicTo(13.9, 14.4, 13.2, 15.5, 13.9, 16.6)
        path.cubicTo(14.8, 18.1, 13.7, 19.4, 11.3, 19.4)
        path.cubicTo(6.4, 19.4, 2.6, 15.6, 2.6, 10.9)
        path.cubicTo(2.6, 6.4, 6.2, 2.8, 11, 2.8)
        path.closeSubpath()
        painter.drawPath(path)
        painter.setBrush(QColor(color))
        for cx, cy in ((6.9, 8.4), (10.1, 5.8), (14.0, 7.0), (6.6, 12.9)):
            painter.drawEllipse(QPointF(cx, cy), 1.15, 1.15)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    elif kind in ("bolt", "bolt_filled"):
        # Neon/cyberpunk mode toggle icon: a simple lightning bolt, filled
        # (all four neon variants are dark, so this is always the
        # "bolt_filled" form in practice - "bolt" is kept as a plain
        # outline in case a light neon variant ever comes back).
        path = QPainterPath()
        path.moveTo(12.6, 1.8)
        path.lineTo(4.6, 12.4)
        path.lineTo(10.0, 12.4)
        path.lineTo(8.8, 20.2)
        path.lineTo(17.4, 9.2)
        path.lineTo(12.0, 9.2)
        path.closeSubpath()
        if kind == "bolt_filled":
            painter.setBrush(QColor(color))
        painter.drawPath(path)
    elif kind == "maximize":
        # Four corner brackets, like a fullscreen/expand icon - used for
        # the Live Preview panel's icon-only action buttons.
        for (x1, y1), (x2, y2), (x3, y3) in (
                ((4, 8), (4, 4), (8, 4)), ((14, 4), (18, 4), (18, 8)),
                ((18, 14), (18, 18), (14, 18)), ((8, 18), (4, 18), (4, 14))):
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            painter.drawLine(QPointF(x2, y2), QPointF(x3, y3))
    elif kind == "camera":
        painter.drawRoundedRect(QRectF(3, 7, 16, 12), 2, 2)
        painter.drawRect(QRectF(8, 4, 6, 3))
        painter.drawEllipse(QPointF(11, 13), 3.4, 3.4)
    elif kind in ("compare", "split"):
        painter.drawRoundedRect(QRectF(3, 4, 7, 14), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(12, 4, 7, 14), 1.5, 1.5)
    elif kind == "scissors":
        painter.drawEllipse(QPointF(6, 6), 2.3, 2.3)
        painter.drawEllipse(QPointF(6, 16), 2.3, 2.3)
        painter.drawLine(QPointF(7.9, 7.4), QPointF(19, 18))
        painter.drawLine(QPointF(7.9, 14.6), QPointF(19, 4))
    elif kind == "cross":
        painter.drawLine(QPointF(6, 6), QPointF(16, 16))
        painter.drawLine(QPointF(16, 6), QPointF(6, 16))
    elif kind == "sparkles":
        # a big four-point star and a small one - "surprise me"
        painter.setBrush(QColor(color))
        for cx, cy, r in ((9.5, 12.5, 6.2), (16.5, 5.5, 3.0)):
            star = QPainterPath()
            k = r * 0.28
            star.moveTo(cx, cy - r)
            star.quadTo(cx + k, cy - k, cx + r, cy)
            star.quadTo(cx + k, cy + k, cx, cy + r)
            star.quadTo(cx - k, cy + k, cx - r, cy)
            star.quadTo(cx - k, cy - k, cx, cy - r)
            painter.drawPath(star)
    elif kind == "gear":
        # a cog: 8 teeth around a ring, with a hole in the middle - "settings"
        painter.setBrush(Qt.BrushStyle.NoBrush)
        import math
        path = QPainterPath()
        pitch = math.pi / 4
        for i in range(8):
            a = i * pitch
            pts = ((a - 0.30, 6.9), (a - 0.17, 9.4), (a + 0.17, 9.4), (a + 0.30, 6.9))
            for j, (ang, r) in enumerate(pts):
                pt = QPointF(11 + r * math.cos(ang), 11 + r * math.sin(ang))
                if i == 0 and j == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawEllipse(QPointF(11, 11), 2.8, 2.8)
    elif kind == "undo":
        # a curved arrow pointing back-left - "revert"
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(7.5, 5.5)
        path.lineTo(4, 9.5)
        path.lineTo(7.5, 13.5)
        painter.drawPath(path)
        curve = QPainterPath()
        curve.moveTo(4, 9.5)
        curve.lineTo(12.5, 9.5)
        curve.quadTo(18, 9.5, 18, 14)
        curve.quadTo(18, 18.5, 12.5, 18.5)
        curve.lineTo(9, 18.5)
        painter.drawPath(curve)
    elif kind in ("dots", "more"):
        # Three dots in a row - the "more options" button.
        painter.setBrush(QColor(color))
        for x in (5.0, 11.0, 17.0):
            painter.drawEllipse(QPointF(x, 11), 1.7, 1.7)
    elif kind == "stop":
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(5.5, 5.5, 11, 11), 2, 2)
    elif kind == "play":
        path = QPainterPath()
        path.moveTo(6.5, 4)
        path.lineTo(18, 11)
        path.lineTo(6.5, 18)
        path.closeSubpath()
        painter.setBrush(QColor(color))
        painter.drawPath(path)
    elif kind == "save_frame":
        # Down-arrow into a bottom tray — universally understood "save frame" glyph
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(11, 3.5), QPointF(11, 13.5))
        painter.drawLine(QPointF(7.2, 9.8), QPointF(11, 13.8))
        painter.drawLine(QPointF(14.8, 9.8), QPointF(11, 13.8))
        painter.drawLine(QPointF(4.2, 14.5), QPointF(4.2, 18.2))
        painter.drawLine(QPointF(4.2, 18.2), QPointF(17.8, 18.2))
        painter.drawLine(QPointF(17.8, 18.2), QPointF(17.8, 14.5))
    elif kind == "save_all":
        # Stacked frames + down-arrow — "save all frames"
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(3.5, 6.5, 11.5, 11.0), 1.8, 1.8)
        painter.drawLine(QPointF(7.0, 3.8), QPointF(18.2, 3.8))
        painter.drawLine(QPointF(18.2, 3.8), QPointF(18.2, 14.2))
        painter.drawLine(QPointF(9.2, 9.2), QPointF(9.2, 14.8))
        painter.drawLine(QPointF(6.8, 12.4), QPointF(9.2, 15.0))
        painter.drawLine(QPointF(11.6, 12.4), QPointF(9.2, 15.0))
    elif kind == "check":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(4.8, 11.0), QPointF(9.0, 15.2))
        painter.drawLine(QPointF(9.0, 15.2), QPointF(17.2, 6.2))
    else:  # moon
        path = QPainterPath()
        path.addEllipse(4, 3, 15, 16)
        cutout = QPainterPath()
        cutout.addEllipse(9, 2, 15, 16)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path.subtracted(cutout))


def find_icon():
    """Look next to the script (or, when frozen, next to the unpacked exe
    contents) for an icon file, so dropping one in with a recognized name
    is enough - no code change needed. .ico works cross-platform for the
    in-app QIcon; only the exe's own file icon needs a real .ico/.icns via
    PyInstaller's --icon flag (see the build instructions)."""
    for name in ("icon.ico", "icon.png", "app.ico", "app.png"):
        p = ROOT / name
        if p.is_file():
            return str(p)
    return None


# Sizes Windows actually requests for a top-level window/taskbar icon across
# its scale-factor table (100%-400%): 16/20/24/30/32/36/40/48/60/64/72/80/96,
# plus 128/256 for Alt+Tab and jump-list thumbnails on very high DPI.
ICON_SIZES = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 128, 256)


def load_app_icon():
    """Build the runtime QIcon (title bar / taskbar button / Alt+Tab) from
    the icons/icon-<size>.png set, registering every size explicitly via
    addFile(..., QSize(...)).

    QIcon(path) on a single file - even a multi-resolution .ico - only ever
    reads that file's first embedded frame; it does NOT auto-select the
    matching size the way Windows' own shell icon extraction does. Loading
    every PNG explicitly is the documented way to get a crisp, exact match
    at each size instead of Qt scaling one fixed pixmap up or down.

    icon.ico (via find_icon()) is kept as the fallback for a plain
    Python-from-source run where the icons/ folder hasn't been unpacked
    next to the script, and is still what --icon embeds as the .exe's own
    file icon at compile time - Explorer/the shell extracts the matching
    size from that file natively, so it doesn't need this treatment."""
    icon = QIcon()
    icons_dir = ROOT / "icons"
    for size in ICON_SIZES:
        p = icons_dir / f"icon-{size}.png"
        if p.is_file():
            icon.addFile(str(p), QSize(size, size))
    if not icon.isNull():
        return icon
    p = find_icon()
    return QIcon(p) if p else QIcon()


def video_drop_glyph(color, size=40):
    """Line-art video play glyph for empty-queue drop zones (Pro and Mini)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    m = size * 0.09
    rect = QRectF(m, m * 1.6, size - 2 * m, size - 3.2 * m)
    painter.drawRoundedRect(rect, size * 0.14, size * 0.14)
    tri = QPainterPath()
    cx, cy = rect.center().x(), rect.center().y()
    w, h = rect.width() * 0.22, rect.height() * 0.28
    tri.moveTo(cx - w * 0.55, cy - h)
    tri.lineTo(cx - w * 0.55, cy + h)
    tri.lineTo(cx + w * 0.9, cy)
    tri.closeSubpath()
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(tri)
    painter.end()
    return QPixmap(pixmap)
