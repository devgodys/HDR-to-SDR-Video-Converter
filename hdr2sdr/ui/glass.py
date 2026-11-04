from __future__ import annotations

from array import array
import random

from PySide6.QtCore import (
    Qt, QRectF,
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QLinearGradient, QRadialGradient, QPixmap, QImage,
)
from PySide6.QtWidgets import (
    QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect,
)




# ---- anti-banding ------------------------------------------------------
# A slow gradient over a dark colour only has a few dozen distinct 8-bit
# values across a whole card, so it shows up as visible stripes / a
# "checkered" look (1-level steps are very visible on dark colours). The
# cure is dithering: render in 16 bits per channel, add less-than-one-level
# of random noise, and only THEN round down to 8 bits - the noise decides
# per pixel whether to round up or down, so the steps dissolve into a
# fine grain nobody can see. (Noise added after the rounding wouldn't help:
# the steps would still be there, just noisy.)
_NOISE_SIZE = 256
_noise_tile = None


def _noise_tile64():
    """256x256 tile, every pixel r=g=b= a random 16-bit value in [0, 257)
    (just under one 8-bit level), alpha opaque. Fixed seed, so the grain
    never shimmers between repaints. Built once, from pure-Python bytes
    operations (no numpy dependency)."""
    global _noise_tile
    if _noise_tile is None:
        n = _NOISE_SIZE * _NOISE_SIZE
        rng = random.Random(0xD17E5)
        lum = array("H", (v % 257 for v in array("H", rng.randbytes(2 * n))))
        buf = array("H", bytes(8 * n))
        buf[0::4] = lum
        buf[1::4] = lum
        buf[2::4] = lum
        buf[3::4] = array("H", [65535]) * n
        img = QImage(buf.tobytes(), _NOISE_SIZE, _NOISE_SIZE, _NOISE_SIZE * 8,
                     QImage.Format.Format_RGBA64_Premultiplied)
        _noise_tile = img.copy()
    return _noise_tile


def add_dither(img64: QImage):
    """Add the sub-level noise to an OPAQUE Format_RGBA64_Premultiplied
    image in place (Plus composition = per-channel addition)."""
    tile = _noise_tile64()
    p = QPainter(img64)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    dpr = img64.devicePixelRatio() or 1.0
    step = _NOISE_SIZE / dpr
    w, h = img64.width() / dpr, img64.height() / dpr
    y = 0.0
    while y < h:
        x = 0.0
        while x < w:
            p.drawImage(QRectF(x, y, step, step), tile)
            x += step
        y += step
    p.end()


def finish_dithered(img64: QImage) -> QImage:
    """Dither a 16-bit image and quantise it to the 8-bit format Qt paints."""
    add_dither(img64)
    out = img64.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    out.setDevicePixelRatio(img64.devicePixelRatio())
    return out


def blurred_backdrop(pixmap: QPixmap, scale: float = 0.18, blur: float = 32.0) -> QPixmap:
    """The shared blurred copy of the window background every glass card
    and button crops its material from: downscale, blur the small copy
    (cheap), scale back up - the scale-up is done in 16 bits and dithered,
    so the smooth gradient the blur produces doesn't turn into stripes."""
    small = pixmap.scaled(
        max(1, round(pixmap.width() * scale)), max(1, round(pixmap.height() * scale)),
        Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    blurred_small = blur_pixmap(small, blur * scale)
    small64 = blurred_small.toImage().convertToFormat(QImage.Format.Format_RGBA64_Premultiplied)
    big64 = small64.scaled(pixmap.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    big64.setDevicePixelRatio(1.0)
    out = QPixmap.fromImage(finish_dithered(big64))
    out.setDevicePixelRatio(pixmap.devicePixelRatio())
    return out


def blur_pixmap(src: QPixmap, radius: float) -> QPixmap:
    """QGraphicsBlurEffect only works on live QGraphicsItems, not on a
    QPixmap directly - wrap it in a throwaway QGraphicsPixmapItem/Scene,
    attach the effect, and render the scene to an offscreen pixmap."""
    if src.isNull():
        return src
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(src)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    # PerformanceHint (box-blur approximation) instead of QualityHint (true
    # Gaussian) - visually near-identical once the image has already been
    # downscaled 4x below, but noticeably cheaper, and this runs on every
    # re-blur tick so the saving is recurring, not one-off.
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.PerformanceHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    result = QPixmap(src.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    scene.render(painter, QRectF(result.rect()), QRectF(src.rect()))
    painter.end()
    return result


def draw_refractive_backdrop(painter, rect, radius, backdrop_pixmap, backdrop_src_rect,
                              edge_band=10.0, edge_zoom=1.16):
    """Draw the blurred-backdrop crop that sits behind a glass shape.

    This used to fake edge refraction by drawing the crop twice - a
    magnified copy over the whole shape, then a normal 1:1 copy on top of
    it, inset by `edge_band` - so only a thin ring at the rim showed the
    stretched layer. On a backdrop with a real tonal range (the neon
    green-to-purple gradient, any big card such as the mini window's drop
    zone) the two copies don't match, so the seam between them showed as a
    second, slightly different rounded rectangle inside every glass shape
    ("square in a square"). It is a single plain 1:1 crop now; the
    edge_band/edge_zoom parameters are accepted (and ignored) so existing
    callers keep working."""
    if backdrop_pixmap is None or backdrop_pixmap.isNull():
        return
    painter.drawPixmap(rect, backdrop_pixmap, backdrop_src_rect)


def paint_glass_path(painter, path, rect, radius, backdrop_pixmap, backdrop_src_rect,
                      tint_top_alpha=65, tint_bottom_alpha=20, hover_pos=None,
                      edge_band=10.0, edge_zoom=1.16):
    """Shared "liquid glass" fill: crop of a pre-blurred backdrop (with a
    refractive edge, see draw_refractive_backdrop) + a tint gradient + an
    optional cursor-following highlight, clipped to an arbitrary rounded
    path. Used by every Glass* widget so they all read as the same
    material rather than several lookalikes."""
    painter.setClipPath(path)
    draw_refractive_backdrop(painter, rect, radius, backdrop_pixmap, backdrop_src_rect,
                              edge_band, edge_zoom)
    tint = QLinearGradient(0, rect.top(), 0, rect.bottom())
    tint.setColorAt(0.0, QColor(255, 255, 255, tint_top_alpha))
    tint.setColorAt(1.0, QColor(255, 255, 255, tint_bottom_alpha))
    painter.fillPath(path, QBrush(tint))
    if hover_pos is not None:
        spec = QRadialGradient(hover_pos, rect.height() * 3)
        spec.setColorAt(0.0, QColor(255, 255, 255, 80))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, QBrush(spec))
    painter.setClipping(False)


def _mix_color(color1, color2, frac):
    """Linear-blend two colours (frac=0 -> color1, frac=1 -> color2),
    accepting and returning QColor so callers that already have (or want)
    a QColor - GlassButton's paintEvent, the hot-ish path this was
    originally written for - don't have to round-trip through a hex
    string just to get back a QColor immediately after. QColor(str) also
    accepts names/rgba() forms beyond plain '#RRGGBB', which the old
    hand-rolled hex slicing didn't."""
    c1, c2 = QColor(color1), QColor(color2)
    return QColor(
        round(c1.red() + (c2.red() - c1.red()) * frac),
        round(c1.green() + (c2.green() - c1.green()) * frac),
        round(c1.blue() + (c2.blue() - c1.blue()) * frac),
    )


def _mix_hex(hex1, hex2, frac):
    """Same blend as _mix_color(), as a '#RRGGBB' string - for callers
    that need one to store (THEMES[...]["INDIGO2"], used elsewhere as
    plain text interpolated into QSS)."""
    return _mix_color(hex1, hex2, frac).name()


def draw_rim_border(painter, rect, radius, width=1.2):
    grad = QLinearGradient(0, 0, rect.width(), rect.height())
    grad.setColorAt(0.0, QColor(255, 255, 255, 190))
    grad.setColorAt(0.45, QColor(255, 255, 255, 35))
    grad.setColorAt(1.0, QColor(255, 255, 255, 120))
    painter.setPen(QPen(QBrush(grad), width))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect.adjusted(width / 2, width / 2, -width / 2, -width / 2), radius, radius)


def _glass_backdrop_for(widget):
    """Every Glass* widget shares this instead of taking a constructor
    argument, so existing call sites (Card(title), etc.) don't need to
    change - it just walks up to the top-level window and asks for
    get_blurred_bg() if that window provides it (MainWindow does).
    Returns None outside MainWindow (e.g. in isolated tests), in which
    case callers fall back to a flat fill."""
    win = widget.window()
    getter = getattr(win, "get_blurred_bg", None)
    return getter() if getter is not None else None
