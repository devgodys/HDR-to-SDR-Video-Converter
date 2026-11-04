from __future__ import annotations

TOOLTIP_MAX_WIDTH = 420
TOOLTIP_WINDOW_MARGIN = 8
TOOLTIP_OFFSET_X = 14
TOOLTIP_OFFSET_Y_BELOW = 20
TOOLTIP_OFFSET_Y_ABOVE = 12
TOOLTIP_ANCHOR_GAP = 6

RectTuple = tuple[int, int, int, int]
PointTuple = tuple[int, int]
SizeTuple = tuple[int, int]


def tooltip_max_width(
    window_width: int,
    cap: int = TOOLTIP_MAX_WIDTH,
    margin: int = TOOLTIP_WINDOW_MARGIN,
) -> int:
    """Maximum tooltip width inside a window of `window_width`: min(420, window_width - 16)."""
    return max(1, min(int(cap), int(window_width) - 2 * int(margin)))


def place_tooltip(
    window_rect: RectTuple | None,
    cursor_pos: PointTuple,
    tip_size: SizeTuple,
    anchor_rect: RectTuple | None = None,
    screen_rect: RectTuple | None = None,
) -> PointTuple:
    """Pure geometry calculation for positioning _AppToolTip in global coordinates.

    Accepts (x, y, w, h) rect tuples, (x, y) cursor tuple, and (w, h) size tuple,
    and returns (x, y) top-left position tuple.
    """
    cx, cy = int(cursor_pos[0]), int(cursor_pos[1])
    tw, th = int(tip_size[0]), int(tip_size[1])
    win_r = tuple(int(v) for v in window_rect) if window_rect is not None else None
    scr_r = tuple(int(v) for v in screen_rect) if screen_rect is not None else None
    anc_r = tuple(int(v) for v in anchor_rect) if anchor_rect is not None else None

    if win_r is None or win_r[2] <= 0 or win_r[3] <= 0:
        win_r = scr_r
    if win_r is None or win_r[2] <= 0 or win_r[3] <= 0:
        return (cx + TOOLTIP_OFFSET_X, cy + TOOLTIP_OFFSET_Y_BELOW)

    wx, wy, ww, wh = win_r
    bx, by, bw, bh = wx, wy, ww, wh

    if scr_r is not None and scr_r[2] > 0 and scr_r[3] > 0:
        sx, sy, sw, sh = scr_r
        ix1, iy1 = max(wx, sx), max(wy, sy)
        ix2, iy2 = min(wx + ww, sx + sw), min(wy + wh, sy + sh)
        if (ix2 - ix1) >= tw and (iy2 - iy1) >= th:
            bx, by, bw, bh = ix1, iy1, ix2 - ix1, iy2 - iy1

    m = TOOLTIP_WINDOW_MARGIN
    if bw >= tw + 2 * m:
        min_x = bx + m
        max_x = bx + bw - m - tw
    else:
        min_x = bx
        max_x = max(bx, bx + bw - tw)

    if bh >= th + 2 * m:
        min_y = by + m
        max_y = by + bh - m - th
    else:
        min_y = by
        max_y = max(by, by + bh - th)

    base_x = max(min_x, min(cx + TOOLTIP_OFFSET_X, max_x))

    y_below = cy + TOOLTIP_OFFSET_Y_BELOW
    if y_below <= max_y:
        base_y = max(min_y, y_below)
    else:
        y_above = cy - th - TOOLTIP_OFFSET_Y_ABOVE
        base_y = max(min_y, min(y_above, max_y))

    if anc_r is not None and anc_r[2] > 0 and anc_r[3] > 0:
        ax, ay, aw, ah = anc_r

        def _overlaps(px: int, py: int) -> bool:
            return (px < ax + aw) and (px + tw > ax) and (py < ay + ah) and (py + th > ay)

        def _in_bounds(px: int, py: int) -> bool:
            return (min_x <= px <= max_x) and (min_y <= py <= max_y)

        if _overlaps(base_x, base_y):
            candidates = (
                (base_x, max(cy + TOOLTIP_OFFSET_Y_BELOW, ay + ah + TOOLTIP_ANCHOR_GAP)),
                (base_x, min(cy - th - TOOLTIP_OFFSET_Y_ABOVE, ay - th - TOOLTIP_ANCHOR_GAP)),
                (base_x, ay - th - TOOLTIP_ANCHOR_GAP),
                (max(cx + TOOLTIP_OFFSET_X, ax + aw + TOOLTIP_ANCHOR_GAP), base_y),
                (ax + aw + TOOLTIP_ANCHOR_GAP, base_y),
                (min(cx - tw - TOOLTIP_OFFSET_X, ax - tw - TOOLTIP_ANCHOR_GAP), base_y),
                (ax - tw - TOOLTIP_ANCHOR_GAP, base_y),
            )
            for cand_x, cand_y in candidates:
                if _in_bounds(cand_x, cand_y) and not _overlaps(cand_x, cand_y):
                    return (cand_x, cand_y)

    return (base_x, base_y)
