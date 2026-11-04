from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QImage,
)




def clipping_pct(img, step=3, near=5):
    """Rough percentage of the frame that's crushed to near-black or blown
    to near-white after tone mapping - a quick visual sanity check that the
    chosen curve/brightness isn't destroying shadow or highlight detail.
    Samples every `step`th pixel (not every pixel) since this runs on the
    GUI thread every ~1.5s and only needs to be roughly right, not exact."""
    # A 4K frame made this loop cost ~260 ms on the GUI thread; the percentages do not need
    # that resolution, so look at a ~480 px wide copy.
    if img.width() > 480:
        img = img.scaledToWidth(480, Qt.TransformationMode.FastTransformation)
    gray = img.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h = gray.width(), gray.height()
    if w == 0 or h == 0:
        return 0.0, 0.0
    stride = gray.bytesPerLine()
    ptr = gray.constBits()
    try:
        ptr.setsize(stride * h)
    except AttributeError:
        pass  # some PySide6 versions size constBits() automatically
    buf = bytes(ptr)
    shadow = highlight = total = 0
    for y in range(0, h, step):
        row = y * stride
        for x in range(0, w, step):
            v = buf[row + x]
            total += 1
            if v <= near:
                shadow += 1
            elif v >= 255 - near:
                highlight += 1
    if not total:
        return 0.0, 0.0
    return shadow / total * 100, highlight / total * 100
