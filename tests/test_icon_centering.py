"""Icon-only buttons in classic mode: the glyph is centred on the button's true
centre. QRect.center() truncates to an int, which put the icon up to 1px up-left
of centre (visible on HiDPI); the paint code must use QRectF."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import hdr2sdr.ui.widgets.buttons as buttons


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("w,h", [(38, 38), (37, 37), (34, 38), (40, 34), (39, 35)])
def test_classic_icon_is_centred_on_the_true_centre(app, monkeypatch, w, h):
    seen = []
    monkeypatch.setattr(buttons, "_draw_icon_glyph",
                        lambda painter, kind, color: seen.append(painter.worldTransform().map(QPointF(11, 11))))
    btn = buttons.GlassButton("")
    btn.setProperty("icon_kind", "palette")
    btn.setFixedSize(w, h)
    btn.setIconSize(QSize(22, 22))
    btn.show()
    btn.grab()                                   # forces a paintEvent in classic mode (no window ui_style)
    assert seen, "the classic-mode icon pass did not run"
    c = QRectF(btn.rect()).center()
    assert seen[-1].x() == pytest.approx(c.x(), abs=1e-6)
    assert seen[-1].y() == pytest.approx(c.y(), abs=1e-6)


def test_queue_item_remove_button_cross_is_symmetrically_centred(app):
    from hdr2sdr.ui.main_window import MainWindow, _QueueItemWidget, _QueueRemoveButton

    w = MainWindow()
    w.queue_list.addItem("sample_test.mp4")
    w.show()
    app.processEvents()

    item = w.queue_list.item(0)
    row = w.queue_list.itemWidget(item)
    assert isinstance(row, _QueueItemWidget)
    btn = row.remove_btn
    assert isinstance(btn, _QueueRemoveButton)
    assert btn.text() == "×"

    # Test both unselected and selected states
    for selected in (False, True):
        w.queue_list.setCurrentRow(0 if selected else -1)
        app.processEvents()

        img = QImage(18, 18, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        btn.render(img)

        t = w.tokens
        fg = QColor(t.get("on_accent", "#ffffff") if selected else t.get("text_secondary", "#9ca3af"))

        min_x, max_x = 18, -1
        min_y, max_y = 18, -1
        for y in range(18):
            for x in range(18):
                c = img.pixelColor(x, y)
                if abs(c.red() - fg.red()) < 30 and abs(c.green() - fg.green()) < 30 and abs(c.blue() - fg.blue()) < 30 and c.alpha() > 50:
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)

        assert min_x != 18, "No cross stroke pixels rendered!"
        top = min_y
        bottom = 17 - max_y
        left = min_x
        right = 17 - max_x
        assert top == bottom, f"Vertical asymmetry in queue item remove button: top={top}, bottom={bottom}"
        assert left == right, f"Horizontal asymmetry in queue item remove button: left={left}, right={right}"

