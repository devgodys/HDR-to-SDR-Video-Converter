"""Interactive video thumbnail timeline for Live preview (and Compare dialog).

``FrameStripLoader`` grabs one small frame per tile with ffmpeg, one process at
a time (never more than one, low priority, never while a conversion runs), and
hands each to ``FrameStrip`` as it arrives, so the strip fills in left to
right instead of appearing all at once. Results are cached per file, so
re-selecting a video is instant.

``FrameStrip`` acts as the complete, self-contained timeline once thumbnails
are available (replacing the separate bottom scrub slider):
- Top time ruler with major/minor tick marks and timestamp scale labels
- Edge-to-edge video thumbnails with played/unplayed contrast
- Prominent draggable playhead with a top shield handle, vertical bar, and
  bottom anchor
- Live hover scrub line with a floating timestamp pill under the cursor
- High-contrast current/total time badge (e.g. ``19:26 / 28:28``) that
  automatically dodges the playhead
"""
from __future__ import annotations

import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QPointF, QProcess, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from hdr2sdr.core.utils import clock_time
from hdr2sdr.theme.theme_tokens import window_tokens

RULER_H = 18
STRIP_H = 44
TILE_H = RULER_H + STRIP_H   # 62 px total timeline height
TILE_ASPECT = 16 / 9
THUMB_H = 92                 # decoded height; drawn at STRIP_H (2x for crisp scaled displays)

# Cheap tone-map for the HDR thumbnails only (frames are tiny, so cost is nil).
# If this build of ffmpeg has no zscale the loader retries with a plain scale.
_VF_HDR = ("scale=-2:{h},zscale=t=linear:npl=100,format=gbrpf32le,tonemap=hable:desat=0,"
           "zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p")
_VF_PLAIN = "scale=-2:{h}"


class FrameStripLoader(QObject):
    frameReady = Signal(int, QImage)     # tile index, image
    finished = Signal()

    _CACHE_MAX = 6
    _cache: "OrderedDict[tuple, dict[int, QImage]]" = OrderedDict()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._proc.finished.connect(self._on_proc_finished)
        self._proc.errorOccurred.connect(self._on_proc_error)
        self._current: tuple[int, int, bool] | None = None
        self._killing = False
        self._gen = 0
        self._queue: list[tuple[int, float]] = []
        self._ffmpeg = ""
        self._path = ""
        self._hdr = False
        self._tonemap_ok = True
        self._key: tuple | None = None
        self._got: dict[int, QImage] = {}

    # -- public ------------------------------------------------------------
    def start(self, ffmpeg: str, path: str, duration: float, count: int, hdr: bool) -> None:
        self.cancel()
        if not ffmpeg or not path or duration <= 0 or count <= 0:
            return
        try:
            mtime = int(os.path.getmtime(path))
        except OSError:
            return
        self._ffmpeg, self._path, self._hdr = ffmpeg, path, hdr
        self._tonemap_ok = True
        self._key = (path, mtime, count)
        cached = FrameStripLoader._cache.get(self._key)
        if cached and len(cached) >= count:
            FrameStripLoader._cache.move_to_end(self._key)
            for i in range(count):
                self.frameReady.emit(i, cached[i])
            self.finished.emit()
            return
        self._got = dict(cached or {})
        for i, img in self._got.items():
            self.frameReady.emit(i, img)
        # Middle of each tile's time span; a spot clear of the very first/last frame (often black).
        self._queue = [(i, duration * (i + 0.5) / count) for i in range(count) if i not in self._got]
        self._gen += 1
        self._next(self._gen)

    def cancel(self) -> None:
        self._gen += 1
        self._queue = []
        self._current = None
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            self._killing = True
            self._proc.kill()
            self._proc.waitForFinished(500)
            self._killing = False
            self._proc.readAllStandardOutput()          # drop whatever the killed run printed

    def is_running(self) -> bool:
        return self._current is not None or bool(self._queue)

    # -- internals ---------------------------------------------------------
    def _args(self, t: float, tonemap: bool) -> list[str]:
        vf = (_VF_HDR if tonemap else _VF_PLAIN).format(h=THUMB_H)
        return ["-hide_banner", "-loglevel", "error", "-threads", "1", "-ss", f"{t:.2f}", "-i", self._path,
                "-frames:v", "1", "-an", "-sn", "-dn", "-vf", vf,
                "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "6", "-"]

    def _next(self, gen: int) -> None:
        if gen != self._gen:
            return
        if not self._queue:
            self._current = None
            self._remember()
            self.finished.emit()
            return
        idx, t = self._queue[0]
        use_tm = self._hdr and self._tonemap_ok
        self._current = (gen, idx, use_tm)
        self._proc.setProgram(self._ffmpeg)
        self._proc.setArguments(self._args(t, use_tm))
        self._proc.start()

    def _on_proc_finished(self, code, _status) -> None:
        if self._killing or self._current is None:
            return
        self._done(code)

    def _on_proc_error(self, err) -> None:
        # FailedToStart never emits finished(); treat it as a failed tile. (Crashed/timeouts do emit finished.)
        if (err == QProcess.ProcessError.FailedToStart
                and self._current is not None and not self._killing):
            self._done(-1, defer=True)

    def _done(self, code: int, defer: bool = False) -> None:
        gen, idx, used_tonemap = self._current
        self._current = None
        # QProcess still reports "Starting" inside errorOccurred(FailedToStart), and start() is refused
        # in that state - so from there the next tile is launched on the following event-loop turn.
        step = (lambda g: QTimer.singleShot(0, lambda: self._next(g))) if defer else self._next
        if gen != self._gen:
            self._proc.readAllStandardOutput()
            return
        data = bytes(self._proc.readAllStandardOutput())
        img = QImage()
        if code == 0 and data:
            img.loadFromData(data, "JPG")
        if img.isNull():
            if used_tonemap:                    # no zscale/tonemap in this build: redo this tile plain
                self._tonemap_ok = False
                step(gen)
                return
            self._queue = [q for q in self._queue if q[0] != idx]     # give up on this tile only
        else:
            self._queue = [q for q in self._queue if q[0] != idx]
            self._got[idx] = img
            self.frameReady.emit(idx, img)
        step(gen)

    def _remember(self) -> None:
        if self._key is not None and self._got:
            FrameStripLoader._cache[self._key] = dict(self._got)
            while len(FrameStripLoader._cache) > self._CACHE_MAX:
                FrameStripLoader._cache.popitem(last=False)


class FrameStrip(QWidget):
    """Self-contained video thumbnail timeline with top time ruler, ticks,
    prominent playhead handle, hover time bubble, and position/duration badge."""

    seekRequested = Signal(float)      # fraction 0..1 of the video

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: dict[int, QPixmap] = {}
        self._count = 0
        self._pos = 0.0               # playhead, fraction 0..1
        self._duration = 0.0          # total video duration in seconds
        self._cur_sec = 0             # current playhead second
        self._time_text = ""          # formatted "19:26 / 28:28" badge text
        self._hover: float | None = None
        self._dragging = False
        self._scrub_enabled = True
        self.setFixedHeight(TILE_H)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def tile_count_for_width(self, width: int | None = None) -> int:
        w = width if width is not None else self.width()
        return max(3, min(16, int(w / (STRIP_H * TILE_ASPECT))))

    def set_scrub_enabled(self, enabled: bool) -> None:
        self._scrub_enabled = bool(enabled)
        if not self._scrub_enabled:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()

    def scrub_enabled(self) -> bool:
        return self._scrub_enabled

    def reset(self, count: int = 0) -> None:
        self._frames.clear()
        self._count = count
        self.update()

    def set_duration(self, duration: float) -> None:
        self._duration = max(0.0, float(duration or 0.0))
        self.update()

    def set_time_info(self, current_sec: int | float, total_sec: int | float, text: str = "") -> None:
        self._cur_sec = max(0, int(round(current_sec)))
        if total_sec and total_sec > 0:
            self._duration = float(total_sec)
        self._time_text = str(text or (
            f"{clock_time(self._cur_sec)} / {clock_time(int(round(self._duration)))}"
            if self._duration > 0 else clock_time(self._cur_sec)
        ))
        self.update()

    def set_frame(self, idx: int, image: QImage) -> None:
        self._frames[idx] = QPixmap.fromImage(image)
        self._count = max(self._count, idx + 1)
        self.update()

    def has_frames(self) -> bool:
        return bool(self._frames)

    def set_position(self, fraction: float) -> None:
        self._pos = max(0.0, min(1.0, fraction))
        if self._duration > 0:
            self._cur_sec = int(round(self._pos * self._duration))
            self._time_text = f"{clock_time(self._cur_sec)} / {clock_time(int(round(self._duration)))}"
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(300, TILE_H)

    def paintEvent(self, _e) -> None:
        t = window_tokens(self.window())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.fillPath(clip, QColor(t["bg_field"]))

        accent = QColor(t["accent"])
        px = r.left() + self._pos * r.width()
        ruler_rect = QRectF(r.left(), r.top(), r.width(), RULER_H)
        strip_rect = QRectF(r.left(), r.top() + RULER_H, r.width(), max(1.0, r.height() - RULER_H))

        p.save()
        p.setClipPath(clip)

        # ---- 1. Filmstrip thumbnails (bottom band) -------------------------
        n = max(1, self._count)
        tw = strip_rect.width() / n
        for i in range(n):
            tile = QRectF(strip_rect.left() + i * tw, strip_rect.top(), tw, strip_rect.height())
            pm = self._frames.get(i)
            if pm is not None and not pm.isNull():
                s = max(tile.width() / pm.width(), tile.height() / pm.height())
                sw, sh = pm.width() * s, pm.height() * s
                p.save()
                p.setClipRect(tile)
                p.drawPixmap(QRectF(tile.center().x() - sw / 2, tile.center().y() - sh / 2, sw, sh),
                             pm, QRectF(pm.rect()))
                p.restore()
            else:
                # Subtle alternating placeholder cells while background ffmpeg tiles decode
                alt = QColor(255, 255, 255, 10 if (i % 2 == 0) else 4)
                p.fillRect(tile, alt)
            if i:
                p.fillRect(QRectF(tile.left() - 0.5, strip_rect.top(), 1.0, strip_rect.height()),
                           QColor(0, 0, 0, 110))

        # Gently dim the unplayed region to the right of the playhead so progress reads at a glance
        if px < strip_rect.right():
            p.fillRect(QRectF(px, strip_rect.top(), strip_rect.right() - px, strip_rect.height()),
                       QColor(0, 0, 0, 58))

        # ---- 2. Top time ruler band (ticks + timestamps + played bar) ------
        p.fillRect(ruler_rect, QColor(12, 14, 18, 225))
        # Subtle played tint across the ruler up to the playhead
        if px > ruler_rect.left():
            acc_fill = QColor(accent)
            acc_fill.setAlpha(42)
            p.fillRect(QRectF(ruler_rect.left(), ruler_rect.top(), px - ruler_rect.left(), ruler_rect.height()),
                       acc_fill)

        # Major and minor ruler ticks + time scale labels
        n_major = max(3, min(6, int(r.width() // 84)))
        n_minor_per_major = 4
        total_minor = n_major * n_minor_per_major
        tick_lbl_font = QFont(self.font())
        tick_lbl_font.setPointSizeF(7.2)
        tick_lbl_font.setWeight(QFont.Weight.DemiBold)
        p.setFont(tick_lbl_font)
        fm_tick = QFontMetricsF(tick_lbl_font)

        for m in range(total_minor + 1):
            frac_m = m / total_minor
            tx = r.left() + frac_m * r.width()
            is_major = (m % n_minor_per_major) == 0
            th = 4.5 if is_major else 2.2
            tc = QColor(255, 255, 255, 135 if is_major else 65)
            p.fillRect(QRectF(tx - 0.5, ruler_rect.bottom() - th, 1.0, th), tc)

            if is_major and self._duration > 0:
                sec_m = int(round(frac_m * self._duration))
                lbl_s = clock_time(sec_m)
                lw = fm_tick.horizontalAdvance(lbl_s)
                if m == 0:
                    lx = r.left() + 5.0
                elif m == total_minor:
                    lx = r.right() - lw - 5.0
                else:
                    lx = tx - lw / 2.0
                p.setPen(QColor(228, 228, 234, 210))
                p.drawText(QRectF(lx, ruler_rect.top() + 1.0, lw + 4.0, ruler_rect.height() - 4.0),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextDontClip,
                           lbl_s)

        # 2px accent progress bar along the ruler/filmstrip seam up to playhead
        p.fillRect(QRectF(r.left(), ruler_rect.bottom() - 1.5, r.width(), 1.5), QColor(255, 255, 255, 35))
        if px > r.left():
            p.fillRect(QRectF(r.left(), ruler_rect.bottom() - 2.0, px - r.left(), 2.0), accent)

        # ---- 3. Hover guide line + floating timestamp pill -----------------
        if self._hover is not None and not self._dragging:
            hx = r.left() + self._hover * r.width()
            p.setPen(QPen(QColor(255, 255, 255, 185), 1.4, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(hx, strip_rect.top() + 1.0), QPointF(hx, strip_rect.bottom() - 1.0))
            if self._duration > 0:
                h_sec = int(round(self._hover * self._duration))
                h_txt = clock_time(h_sec)
                hw = fm_tick.horizontalAdvance(h_txt) + 10.0
                hx0 = max(r.left() + 2.0, min(hx - hw / 2.0, r.right() - hw - 2.0))
                h_pill = QRectF(hx0, ruler_rect.top() + 1.5, hw, ruler_rect.height() - 3.0)
                hp_path = QPainterPath()
                hp_path.addRoundedRect(h_pill, 4.0, 4.0)
                p.fillPath(hp_path, QColor(18, 20, 26, 242))
                p.setPen(QPen(accent, 1.0))
                p.drawPath(hp_path)
                p.setPen(QColor("#FFFFFF"))
                p.drawText(h_pill, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextDontClip, h_txt)

        # ---- 4. Playhead line + top handle shield + bottom anchor ----------
        # Dark contrast halo behind the vertical playhead line so it stands out on bright scenes
        p.fillRect(QRectF(px - 2.0, strip_rect.top(), 4.0, strip_rect.height()), QColor(0, 0, 0, 155))
        p.setPen(QPen(accent, 2.4))
        p.drawLine(QPointF(px, ruler_rect.top() + 9.0), QPointF(px, r.bottom() - 1.0))

        # Top draggable playhead shield / pointer cap
        cap_x = max(r.left() + 5.5, min(px, r.right() - 5.5))
        cap = QPainterPath()
        cap.moveTo(cap_x - 5.0, ruler_rect.top() + 1.5)
        cap.lineTo(cap_x + 5.0, ruler_rect.top() + 1.5)
        cap.lineTo(cap_x + 5.0, ruler_rect.top() + 8.0)
        cap.lineTo(cap_x, ruler_rect.top() + 13.5)
        cap.lineTo(cap_x - 5.0, ruler_rect.top() + 8.0)
        cap.closeSubpath()
        p.setPen(QPen(QColor("#FFFFFF"), 1.1))
        p.setBrush(accent)
        p.drawPath(cap)

        # Small bottom anchor triangle
        bot_tri = QPainterPath()
        bot_tri.moveTo(cap_x - 3.5, r.bottom() - 0.5)
        bot_tri.lineTo(cap_x + 3.5, r.bottom() - 0.5)
        bot_tri.lineTo(cap_x, r.bottom() - 4.5)
        bot_tri.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawPath(bot_tri)

        # ---- 5. High-contrast Current / Total Time Badge ("19:26 / 28:28") -
        badge_text = self._time_text or (
            f"{clock_time(self._cur_sec)} / {clock_time(int(round(self._duration)))}"
            if self._duration > 0 else ""
        )
        if badge_text:
            b_font = QFont(self.font())
            b_font.setPointSizeF(7.8)
            b_font.setWeight(QFont.Weight.DemiBold)
            p.setFont(b_font)
            fm_b = QFontMetricsF(b_font)
            bw = fm_b.horizontalAdvance(badge_text) + 12.0
            bh = 17.0
            # Place in bottom-left by default; flip to bottom-right when playhead is in the left 28%
            bx = (r.right() - bw - 5.0) if self._pos < 0.28 else (r.left() + 5.0)
            by = r.bottom() - bh - 4.0
            b_rect = QRectF(bx, by, bw, bh)
            b_path = QPainterPath()
            b_path.addRoundedRect(b_rect, 5.0, 5.0)
            p.fillPath(b_path, QColor(10, 12, 16, 222))
            p.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
            p.drawPath(b_path)
            p.setPen(QColor("#F4F4F5"))
            p.drawText(b_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextDontClip, badge_text)

        p.restore()

        # Outer border (accent highlight on hover/drag)
        border_col = accent if (self._dragging or self._hover is not None) else QColor(t["border"])
        p.setPen(QPen(border_col, 1.2 if self._dragging else 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(clip)
        p.end()

    def _frac(self, x: float) -> float:
        return max(0.0, min(1.0, x / max(1, self.width())))

    def mousePressEvent(self, e):
        if not self._scrub_enabled:
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.seekRequested.emit(self._frac(e.position().x()))
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self._hover = self._frac(e.position().x())
        if self._scrub_enabled and (e.buttons() & Qt.MouseButton.LeftButton) and self.isEnabled():
            self.seekRequested.emit(self._hover)
        self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._scrub_enabled else Qt.CursorShape.ArrowCursor)
            self.update()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if not self._scrub_enabled or not self.isEnabled() or self._duration <= 0:
            e.ignore()
            return
        notches = e.angleDelta().y() / 120.0
        if notches:
            step_sec = 5.0 if (e.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)) else 1.0
            new_sec = max(0.0, min(self._duration, self._pos * self._duration + notches * step_sec))
            self.seekRequested.emit(new_sec / self._duration)
        e.accept()

    def keyPressEvent(self, e):
        if not self._scrub_enabled or not self.isEnabled() or self._duration <= 0:
            super().keyPressEvent(e)
            return
            return
        step_sec = 5.0 if (e.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)) else 1.0
        if e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.seekRequested.emit(min(1.0, (self._pos * self._duration + step_sec) / self._duration))
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self.seekRequested.emit(max(0.0, (self._pos * self._duration - step_sec) / self._duration))
            e.accept()
            return
        super().keyPressEvent(e)

    def leaveEvent(self, e):
        self._hover = None
        self.update()
        super().leaveEvent(e)
