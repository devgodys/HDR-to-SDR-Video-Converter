"""Picking a row in the custom dropdown works for both the combo box and the
menu button (which has no `activated` signal)."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QWidget


def test_both_widgets_can_pick_a_row():
    QApplication.instance() or QApplication([])
    from hdr2sdr.ui.widgets.combo import RoundedComboBox, RoundedComboPopup, RoundedMenuButton
    host = QWidget()
    combo = RoundedComboBox(host)
    combo.addItems(["a", "b"])
    got = []
    combo.activated.connect(got.append)
    popup = RoundedComboPopup(combo)
    popup.list.addItem("a")
    popup.list.addItem("b")
    popup._pick(popup.list.item(1))
    assert got == [1] and combo.currentIndex() == 1

    btn = RoundedMenuButton(host)
    btn.set_items(["x", "y"], 0)
    picked = []
    btn.activatedIndex.connect(picked.append)
    popup2 = RoundedComboPopup(btn)
    popup2.list.addItem("x")
    popup2.list.addItem("y")
    popup2._pick(popup2.list.item(1))
    assert picked == [1]
