"""Tests for _AppToolTip window/screen bounding and pure place_tooltip logic."""
from __future__ import annotations

try:
    import pytest
except ImportError:  # pragma: no cover - allows direct execution without pytest
    pytest = None  # type: ignore[assignment]

from hdr2sdr.ui.tooltip_layout import (
    TOOLTIP_MAX_WIDTH,
    TOOLTIP_OFFSET_X,
    TOOLTIP_OFFSET_Y_BELOW,
    TOOLTIP_WINDOW_MARGIN,
    place_tooltip,
    tooltip_max_width,
)


def _rect_contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ix >= ox and iy >= oy and (ix + iw) <= (ox + ow) and (iy + ih) <= (oy + oh)


def _rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (ax < bx + bw) and (ax + aw > bx) and (ay < by + bh) and (ay + ah > by)


def test_place_tooltip_default_bottom_right():
    win = (100, 100, 960, 640)
    cursor = (250, 220)
    tip = (180, 36)
    px, py = place_tooltip(win, cursor, tip)
    assert (px, py) == (250 + TOOLTIP_OFFSET_X, 220 + TOOLTIP_OFFSET_Y_BELOW)
    assert _rect_contains(win, (px, py, tip[0], tip[1]))


def test_place_tooltip_right_bottom_left_top_edges():
    win = (100, 50, 800, 600)
    wx, wy, ww, wh = win
    tip = (220, 48)

    # 1. Near right edge: shifted left so right edge == window_right - 8
    rx, ry = place_tooltip(win, (wx + ww - 10, wy + 200), tip)
    assert rx + tip[0] == wx + ww - TOOLTIP_WINDOW_MARGIN
    assert _rect_contains(win, (rx, ry, tip[0], tip[1]))

    # 2. Near bottom edge: placed above cursor and completely inside window
    cursor_bottom = (wx + 200, wy + wh - 12)
    bx, by = place_tooltip(win, cursor_bottom, tip)
    assert by + tip[1] <= cursor_bottom[1]
    assert _rect_contains(win, (bx, by, tip[0], tip[1]))

    # 3. Near/beyond left and top edges: clamped inside window with margin
    lx, ly = place_tooltip(win, (wx - 20, wy - 20), tip)
    assert lx >= wx + TOOLTIP_WINDOW_MARGIN
    assert ly >= wy + TOOLTIP_WINDOW_MARGIN
    assert _rect_contains(win, (lx, ly, tip[0], tip[1]))

    # 4. Bottom-right corner simultaneously
    brx, bry = place_tooltip(win, (wx + ww - 2, wy + wh - 2), tip)
    assert brx + tip[0] == wx + ww - TOOLTIP_WINDOW_MARGIN
    assert bry + tip[1] <= wy + wh - 2
    assert _rect_contains(win, (brx, bry, tip[0], tip[1]))


def test_place_tooltip_minimum_window_and_mini_mode_max_width():
    # Standard Pro window >= 436px caps at 420
    assert tooltip_max_width(960) == TOOLTIP_MAX_WIDTH
    # Mini mode minimum width 420 -> 420 - 16 = 404
    assert tooltip_max_width(420) == 404
    # Narrow Mini mode width 300 -> 300 - 16 = 284
    assert tooltip_max_width(300) == 284

    for win in ((0, 0, 420, 380), (50, 50, 300, 240)):
        wx, wy, ww, wh = win
        max_w = tooltip_max_width(ww)
        tip = (max_w, 64)
        for cursor in (
            (wx + 4, wy + 4),
            (wx + ww - 4, wy + 40),
            (wx + 40, wy + wh - 4),
            (wx + ww - 2, wy + wh - 2),
        ):
            px, py = place_tooltip(win, cursor, tip)
            assert _rect_contains(win, (px, py, tip[0], tip[1]))


def test_place_tooltip_avoids_covering_anchor_widget_when_possible():
    win = (0, 0, 520, 620)
    # Combobox under Presets: cursor near top of combo so default +20px would overlap it
    combo_rect = (40, 120, 260, 34)
    cursor = (100, 126)
    tip = (240, 42)

    px, py = place_tooltip(win, cursor, tip, anchor_rect=combo_rect)
    tip_rect = (px, py, tip[0], tip[1])
    assert _rect_contains(win, tip_rect)
    assert not _rects_overlap(tip_rect, combo_rect)

    # Combobox near bottom of window: places above the combobox without overlapping it
    combo_bottom = (40, 560, 260, 34)
    cursor_bottom = (100, 575)
    bx, by = place_tooltip(win, cursor_bottom, tip, anchor_rect=combo_bottom)
    tip_rect_b = (bx, by, tip[0], tip[1])
    assert _rect_contains(win, tip_rect_b)
    assert not _rects_overlap(tip_rect_b, combo_bottom)

    # Fallback when anchor fills almost the entire window: still clamped inside window
    huge_anchor = (4, 4, 512, 612)
    fx, fy = place_tooltip(win, (500, 600), tip, anchor_rect=huge_anchor)
    assert _rect_contains(win, (fx, fy, tip[0], tip[1]))


def test_place_tooltip_secondary_screen_bounds_and_fallback():
    screen = (0, 0, 1920, 1040)
    # Window partially dragged beyond the right/bottom edge of the monitor
    win = (1500, 700, 600, 500)
    tip = (220, 50)
    cursor = (1890, 1020)

    px, py = place_tooltip(win, cursor, tip, screen_rect=screen)
    tip_rect = (px, py, tip[0], tip[1])
    assert _rect_contains(win, tip_rect)
    assert _rect_contains(screen, tip_rect)

    # Fallback when window_rect is None: uses screen_rect
    nx, ny = place_tooltip(None, (1910, 1030), tip, screen_rect=screen)
    assert _rect_contains(screen, (nx, ny, tip[0], tip[1]))


def test_offscreen_qt_tooltip_in_main_mini_and_theme_dialog():
    if pytest is not None:
        QtCore = pytest.importorskip("PySide6.QtCore")
        QtGui = pytest.importorskip("PySide6.QtGui")
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    else:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except ImportError:
            return

    QPoint = QtCore.QPoint
    QRect = QtCore.QRect
    QHelpEvent = QtGui.QHelpEvent
    QEvent = QtCore.QEvent
    QApplication = QtWidgets.QApplication

    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    from hdr2sdr.ui.mini_window import MiniModeWindow
    from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog

    def pump(n=10):
        for _ in range(n):
            app.processEvents()

    def trigger_tooltip(main_win, widget, global_pt):
        local_pt = widget.mapFromGlobal(global_pt)
        ev = QHelpEvent(QEvent.Type.ToolTip, local_pt, global_pt)
        main_win.eventFilter(widget, ev)
        pump(3)
        return QRect(main_win._tooltip.pos(), main_win._tooltip.size())

    w = MainWindow()
    w.resize(1080, 720)
    w.show()
    pump(10)

    target_btn = w.go_btn
    target_btn.setToolTip(
        "Long tooltip text for verifying wrapping and window edge clamping across all window modes."
    )

    try:
        # 1. MainWindow normal size: right, bottom, and left edges
        win_rect = QRect(w.mapToGlobal(QPoint(0, 0)), w.size())
        for gx, gy in (
            (win_rect.left() + 2, win_rect.top() + 80),
            (win_rect.right() - 2, win_rect.top() + 80),
            (win_rect.left() + 120, win_rect.bottom() - 2),
            (win_rect.right() - 2, win_rect.bottom() - 2),
        ):
            tip_rect = trigger_tooltip(w, target_btn, QPoint(gx, gy))
            assert win_rect.contains(tip_rect), f"{tip_rect} not inside {win_rect}"

        # 2. MainWindow at minimum size
        min_sz = w.minimumSizeHint().expandedTo(w.minimumSize())
        w.resize(max(320, min_sz.width()), max(240, min_sz.height()))
        pump(5)
        min_win_rect = QRect(w.mapToGlobal(QPoint(0, 0)), w.size())
        for gx, gy in (
            (min_win_rect.left() + 4, min_win_rect.top() + 40),
            (min_win_rect.right() - 4, min_win_rect.top() + 40),
            (min_win_rect.left() + 60, min_win_rect.bottom() - 4),
        ):
            tip_rect = trigger_tooltip(w, target_btn, QPoint(gx, gy))
            assert min_win_rect.contains(tip_rect)

        # 3. MiniModeWindow (narrow window wrapping + edge clamping)
        mini = MiniModeWindow(w)
        mini.resize(420, 380)
        mini.show()
        pump(10)
        try:
            mini_rect = QRect(mini.mapToGlobal(QPoint(0, 0)), mini.size())
            for gx, gy in (
                (mini_rect.left() + 3, mini_rect.top() + 50),
                (mini_rect.right() - 3, mini_rect.top() + 50),
                (mini_rect.left() + 80, mini_rect.bottom() - 3),
            ):
                tip_rect = trigger_tooltip(w, mini.mini_best_btn, QPoint(gx, gy))
                assert mini_rect.contains(tip_rect)
                assert w._tooltip.maximumWidth() == min(420, mini_rect.width() - 16)
        finally:
            mini.close()
            pump(5)

        # 4. ThemeCustomizeDialog: constrained to dialog (obj.window()), avoids covering _preset_combo
        dlg = ThemeCustomizeDialog(w, w.theme_name)
        dlg.show()
        pump(10)
        try:
            dlg._preset_combo.setToolTip("Select a built-in look preset or custom slot.")
            dlg_rect = QRect(dlg.mapToGlobal(QPoint(0, 0)), dlg.size())
            combo_rect = QRect(
                dlg._preset_combo.mapToGlobal(QPoint(0, 0)),
                dlg._preset_combo.size(),
            )
            tip_rect = trigger_tooltip(w, dlg._preset_combo, combo_rect.center())
            assert dlg_rect.contains(tip_rect)
            assert not tip_rect.intersects(combo_rect)
        finally:
            dlg.reject()
            pump(5)
    finally:
        w._tooltip.hide()
        w.close()
        pump(5)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
