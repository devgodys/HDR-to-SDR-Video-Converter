from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Qt, QTimer, QProcess, QRectF,
)
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QProgressBar, QVBoxLayout, QHBoxLayout, QFileDialog, QSizeGrip, QGraphicsDropShadowEffect,
)

from hdr2sdr.runtime.updates import APP_VERSION
from hdr2sdr.runtime.unfinished import UnfinishedStore
from hdr2sdr.theme.palettes import (
    THEMES, _neon_active,
)
from hdr2sdr.theme.theme_tokens import (
    build_mini_window_qss, build_tokens,
)
from hdr2sdr.core.constants import PROGRESS_BAR_HEIGHT, VIDEO_DIALOG_FILTER
from hdr2sdr.ui.glass import blurred_backdrop
from hdr2sdr.ui.icons import load_app_icon
from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.style_helpers import (
    set_role, style_progress_bar,
)
from hdr2sdr.ui.widgets.buttons import GlassButton, TitleBarButton
from hdr2sdr.ui.widgets.containers import (
    Card, _BgLayer, _TitleBarDragArea,
)
from hdr2sdr.ui.widgets.progress import ProgressShimmer
from hdr2sdr.ui.widgets.toggles import ToggleSwitch




def _mini_video_glyph(color, size=40):
    """Small line-art "video file" glyph for the Mini mode drop zone -
    same reasoning as the shared drawn icon glyphs (not an emoji glyph, so
    it renders identically regardless of what fonts are installed): a
    rounded rectangle with a play triangle inside, echoing the generic
    "media file" icon most drag-and-drop tools use for their empty state."""
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


class MiniModeWindow(QWidget):
    """A minimal drag-and-drop launcher for people who don't want to touch
    Analyze/tone-mapping curves/encoder pickers at all: drop a file, wait
    for a one-line verdict, tap one of three quality tiers, done. It has
    no conversion logic of its own - it drives a hidden, fully set-up
    MainWindow ("the engine") using the exact same analyze()/
    _apply_fast_preset()/_apply_optimal_preset()/_apply_best_quality_preset()/
    start() the Pro UI's own preset row already calls, so a Mini-mode
    conversion produces byte-identical output to doing the same thing by
    hand in Pro mode. "Pro" just reveals that same engine's real window.

    Visually this borrows the app's own Card/GlassButton "liquid glass"
    material rather than inventing a second look - both read ui_style/
    theme_name off self.window(), which for this standalone window is
    just itself, so setting those two attributes is enough. It also
    carries its own _BgLayer/get_blurred_bg()/refresh_blur() (same as
    MainWindow, and just as cheap - _BgLayer only repaints on change),
    so gradients, background effects, and glass mode's blur all render
    the same as Pro mode instead of only the base palette carrying over.

    theme_name/ui_style mirror whatever Pro mode last used (see
    _resolve_theme()) so this launcher feels like the same app rather
    than a differently-skinned one; apply_theme() below keeps it that
    way both at startup and live, since MainWindow.toggle_mode() calls
    it directly on self._mini_window when this window exists."""

    VIDEO_EXTS = MainWindow.VIDEO_EXTS

    def __init__(self, engine: "MainWindow"):
        super().__init__()
        self.engine = engine
        engine._mini_window = self
        # Card/GlassButton read these two off self.window() - see the
        # class docstring above for why this alone is enough to get real
        # themed rendering in this standalone window. Set to *something*
        # valid here so early paint events (before apply_theme() runs at
        # the end of __init__) never see an unset attribute; apply_theme()
        # immediately resolves this from the engine's actual theme.
        self.theme_name = "light"
        self.ui_style = "classic"
        self.neon_effect = False
        self.tokens = build_tokens(theme_name="light")
        self._theme = self.tokens
        self.setAcceptDrops(True)
        self.setWindowTitle(engine.windowTitle())
        if engine.FONT:
            self.setFont(QFont(engine.FONT, 10))
        icon = engine.windowIcon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.setObjectName("miniRoot")
        self.resize(460, 400)
        self.setMinimumSize(420, 380)

        # Frameless: no native title bar/border, own drag handle
        # (_TitleBarDragArea below) and resize grip in its place - see
        # the "Liquid Glass" write-up this was ported from for why (the
        # native Windows frame otherwise always draws its own square
        # title bar on top of this app's own rounded "glass" look,
        # regardless of how the content underneath is styled).
        # _BgLayer reads this flag to know whether to clip/border itself
        # to a rounded window shape (frameless) or paint a plain square
        # fill (MainWindow, still framed - unchanged) - see its
        # paintEvent for that branch.
        self._frameless = True
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Same live gradient/effect backdrop MainWindow uses - cheap since
        # _BgLayer only repaints on resize/theme change, no timer.
        self._bg_layer = _BgLayer(self)
        self._bg_layer.lower()
        self._blurred_bg = QPixmap()

        root = QVBoxLayout(self)
        # Only side/bottom margins here now - the top margin used to sit
        # on `root` itself, above self.titlebar, which left it as dead
        # space no widget actually covers: nothing there to catch a
        # press and call startSystemMove(), just the bare window
        # background underneath (see _TitleBarDragArea). Moved that same
        # 18px down into the titlebar's own top margin instead, so the
        # draggable widget's rect actually extends into the corner all
        # the way to y=0 and the whole band is part of one widget that
        # can react to it.
        root.setContentsMargins(16, 0, 16, 16)
        HEADER_GLOW_ROOM = 12
        root.setSpacing(4)

        self.titlebar = _TitleBarDragArea()
        top_row = QHBoxLayout(self.titlebar)
        top_row.setContentsMargins(0, 16, 0, HEADER_GLOW_ROOM)
        top_row.setSpacing(8)
        app_icon = load_app_icon()
        if not app_icon.isNull():
            icon_label = QLabel()
            icon_label.setPixmap(app_icon.pixmap(24, 24))
            self.icon_label = icon_label
            top_row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        title = QLabel(engine.tr("HDR to SDR Video Converter"))
        title.setObjectName("miniTitleLabel")
        self.title_label = title
        top_row.addWidget(title)
        top_row.addStretch(1)
        self.pro_btn = GlassButton(engine.tr("Pro \u2192"))
        set_role(self.pro_btn, "panel-toggle")
        self.pro_btn.setFixedHeight(30)
        self.pro_btn.setToolTip(engine.tr("Open the full interface (all settings, queue, live preview)."))
        self.pro_btn.clicked.connect(self.open_pro)
        top_row.addWidget(self.pro_btn)
        self.minimize_btn = TitleBarButton("\u2013")
        self.minimize_btn.setToolTip(engine.tr("Minimize"))
        self.minimize_btn.clicked.connect(self.showMinimized)
        top_row.addWidget(self.minimize_btn)
        self.close_btn = TitleBarButton("\u00d7", danger=True)
        self.close_btn.setToolTip(engine.tr("Close"))
        self.close_btn.clicked.connect(self.close)
        top_row.addWidget(self.close_btn)
        root.addWidget(self.titlebar)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        root.addLayout(body, 1)

        # Resize grip: the native frame's resize-by-edge-dragging is also
        # gone, so this is the replacement. QSizeGrip already knows how
        # to resize *this* top-level window on its own - nothing else to
        # wire up beyond positioning it (see resizeEvent below).
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)

        # ---- drop zone: a real Card (glass panel), not a QFrame + QSS -
        # dashed border, so it reads as one material with everything else
        # instead of a flat placeholder box glued on top. -----------------
        self.drop_card = Card("")
        self.drop_card.title_label.hide()
        self.drop_card.body.setContentsMargins(0, 8, 0, 8)
        self.drop_card.setMinimumHeight(150)
        self.drop_card.setCursor(Qt.CursorShape.PointingHandCursor)

        self.drop_icon = QLabel()
        self.drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_card.body.addWidget(self.drop_icon)

        self.drop_label = QLabel(engine.tr("Drop a video here"))
        self.drop_label.setObjectName("miniDropLabel")
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setWordWrap(True)
        self.drop_card.body.addWidget(self.drop_label)

        self.browse_btn = GlassButton(engine.tr("Browse File"))
        self.browse_btn.setFixedWidth(140)
        self.browse_btn.setFixedHeight(32)
        self.browse_btn.clicked.connect(self.browse)
        browse_row = QHBoxLayout()
        browse_row.addStretch(1)
        browse_row.addWidget(self.browse_btn)
        browse_row.addStretch(1)
        self.drop_card.body.addSpacing(6)
        self.drop_card.body.addLayout(browse_row)
        body.addWidget(self.drop_card, 1)

        # ---- one-line status once a file is loaded/analyzed ------------
        self.status_label = QLabel("")
        self.status_label.setObjectName("miniStatusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(self.status_label, "muted")
        body.addWidget(self.status_label)

        # ---- the three one-tap quality tiers - same presets/handlers as
        # Pro mode's own preset row (_apply_*_preset + start()) --------
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self.mini_fast_btn = GlassButton(engine.tr("Fast"))
        self.mini_optimal_btn = GlassButton(engine.tr("Optimal"))
        self.mini_best_btn = GlassButton(engine.tr("Best quality"))
        # "Optimal" gets the accent tint (GlassButton role "go" -> INDIGO)
        # so it reads as the recommended/primary tier at a glance, the
        # way vidcord highlights its default "Compress" tab in blue.
        for b, role, tip in (
            (self.mini_fast_btn, "panel-toggle", "Hardware encoder if available, simple curve. Quickest."),
            (self.mini_optimal_btn, "go", "Matches this source's recommended settings - a safe default."),
            (self.mini_best_btn, "panel-toggle", "CPU \u00b7 H.265, low CRF, best available curve. Slower."),
        ):
            set_role(b, role)
            b.setEnabled(False)
            b.setFixedHeight(38)
            b.setToolTip(engine.tr(tip))
            preset_row.addWidget(b, 1)
        self.mini_fast_btn.clicked.connect(lambda: self._start_with(self.engine._apply_fast_preset))
        self.mini_optimal_btn.clicked.connect(lambda: self._start_with(self.engine._apply_optimal_preset))
        self.mini_best_btn.clicked.connect(lambda: self._start_with(self.engine._apply_best_quality_preset))
        body.addLayout(preset_row)

        # Plain QProgressBar, themed via style_progress_bar() below (same
        # helper the Pro mode PROGRESS card uses).
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 1000)
        self.pbar.setTextVisible(True)
        self.pbar.setFormat("%p%")
        self.pbar.setFixedHeight(PROGRESS_BAR_HEIGHT)
        self._pbar_shimmer = ProgressShimmer(self.pbar)
        self._pbar_shadow = QGraphicsDropShadowEffect(self.pbar)
        self._pbar_shadow.setBlurRadius(20)
        self._pbar_shadow.setOffset(0, 0)
        self._pbar_shadow.setEnabled(False)
        self.pbar.setGraphicsEffect(self._pbar_shadow)
        self.pbar.hide()
        body.addWidget(self.pbar)

        self.footer_label = QLabel(f"v{APP_VERSION} \u00b7 " + engine.tr("Mini mode"))
        self.footer_label.setObjectName("miniFooterLabel")
        footer_row = QHBoxLayout()
        footer_row.addWidget(self.footer_label)
        footer_row.addStretch(1)
        # ---- skip-mini-window toggle ------------------------------------
        # Persisted straight to QSettings on toggle (not tied to any
        # Apply/Back flow, unlike ThemeCustomizeDialog's controls - there's
        # nothing to preview here, it only decides what main() shows on
        # the *next* launch). A mirror of this same toggle also lives in
        # ThemeCustomizeDialog (see its "skip_mini_row"/_on_skip_mini_
        # toggled), since once this is turned on there's no other route
        # back to Mini mode's own settings - Pro mode's customize panel is
        # the only surface left to turn it back off from.
        self.skip_mini_toggle = ToggleSwitch(engine.tr("Always open Pro mode"))
        self.skip_mini_toggle.setChecked(
            str(engine.i18n.settings.value("skip_mini_window", "false")).lower() == "true"
        )
        self.skip_mini_toggle.setToolTip(
            engine.tr("Skip this launcher next time and start straight in the full interface."))
        self.skip_mini_toggle.toggled.connect(self._on_skip_mini_toggled)
        footer_row.addWidget(self.skip_mini_toggle)
        body.addLayout(footer_row)

        self._preset_buttons = (self.mini_fast_btn, self.mini_optimal_btn, self.mini_best_btn)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._poll_progress)
        self._drop_active = False

        # Resolves theme_name/ui_style from the engine and paints
        # everything above in that theme - see the class docstring and
        # apply_theme() itself for why this is a full method rather than
        # one-off setup, and how it's kept in sync afterwards.
        self.apply_theme()

    def _resolve_theme(self):
        """Mirrors whatever theme+style combo Pro mode is currently on
        (which itself was loaded from QSettings at MainWindow startup -
        see MainWindow.__init__ - and updated live by toggle_mode() and
        the theme customizer), instead of a fixed look, so this launcher feels
        like the same app rather than a differently-skinned one.
        theme_name and ui_style are independent, so each is validated on
        its own; falls back to light/classic if the engine ever has
        something invalid (shouldn't normally happen)."""
        name = getattr(self.engine, "theme_name", "light")
        neon = bool(getattr(self.engine, "neon_effect", False))
        name = name if name in THEMES else "light"
        style = "classic"
        return name, style, neon

    def apply_theme(self):
        """(Re)paints this whole window in the engine's current theme.
        Called once at the end of __init__ (so the window opens already
        matching last run's saved theme) and again by
        MainWindow.toggle_mode() whenever the user changes theme in Pro
        mode while this window exists, so switching back to Mini mode
        never shows a stale look."""
        self.theme_name, self.ui_style, self.neon_effect = self._resolve_theme()
        engine_look = getattr(self.engine, "look", None)
        self.tokens = build_tokens(engine_look, theme_name=self.theme_name)
        t = self.tokens
        self._theme = t
        style_progress_bar(self.pbar, t, self.theme_name)
        self.setStyleSheet(build_mini_window_qss(t))
        self.title_label.setStyleSheet(f"QLabel#miniTitleLabel {{ font-size: 15px; font-weight: 600; color: {t['text']}; }}")
        self.status_label.setStyleSheet(f"QLabel#miniStatusLabel {{ color: {t['text_secondary']}; font-size: 11.5px; }}")
        self.footer_label.setStyleSheet(f"QLabel#miniFooterLabel {{ color: {t['text_secondary']}; font-size: 9.5px; }}")
        self.skip_mini_toggle.setProperty("on_color", t["accent"])
        self.skip_mini_toggle.setProperty("off_color", t["border"])
        self.skip_mini_toggle.setProperty("text_color", t["text_secondary"])
        self.skip_mini_toggle.update()
        self._style_drop_zone(self._drop_active)

        buttons = (self.pro_btn, self.browse_btn, *getattr(self, "_preset_buttons", ()))
        is_neon = _neon_active(self)
        for b in buttons:
            role = b.property("role") or ""
            is_neon_target = role == "go" or (role in ("panel-toggle", "preset-segment-btn") and b.isCheckable() and b.isChecked())
            if is_neon and is_neon_target and b.isEnabled():
                glow_key = GlassButton._TINT_ROLES.get(role, "accent")
                glow = QColor(t[glow_key])
                glow.setAlpha(190)
                b._shadow.setBlurRadius(24)
                b._shadow.setOffset(0, 0)
                b._shadow.setColor(glow)
                b._shadow.setEnabled(True)
            elif self.ui_style == "glass":
                b._shadow.setBlurRadius(16)
                b._shadow.setOffset(0, 4)
                b._shadow.setColor(QColor(0, 0, 0, 25))
                b._shadow.setEnabled(True)
            else:
                b._shadow.setEnabled(False)
            b.update()
        if hasattr(self, "_pbar_shadow"):
            if is_neon:
                p_glow = QColor(t["accent"])
                p_glow.setAlpha(180)
                self._pbar_shadow.setColor(p_glow)
                self._pbar_shadow.setEnabled(True)
            else:
                self._pbar_shadow.setEnabled(False)
        self.drop_card.update()

        # Push the gradient/effect backdrop and (in glass mode) the
        # cropped-blur snapshot every Card/GlassButton reads from - same
        # two calls MainWindow.apply_theme()/resizeEvent() make, just
        # scoped to this window's own _bg_layer.
        self._bg_layer.update()
        QTimer.singleShot(0, self.refresh_blur)

    # ---- gradient/effect backdrop + liquid-glass crop (mirrors
    # MainWindow's own _bg_layer/get_blurred_bg/refresh_blur) -----------
    def _get_theme_custom(self, theme_key):
        return self.engine._get_theme_custom(theme_key)

    def get_blurred_bg(self):
        return self._blurred_bg

    def refresh_blur(self):
        if self.ui_style != "glass":
            return
        pixmap = self._bg_layer.grab()
        if pixmap.isNull() or pixmap.width() < 1 or pixmap.height() < 1:
            return
        self._blurred_bg = blurred_backdrop(pixmap)  # downscale/blur/upscale + dither (glass.py)
        for cls in (Card, GlassButton):
            for w in self.findChildren(cls):
                w.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg_layer.setGeometry(self.rect())
        self._grip.move(self.width() - self._grip.width() - 4, self.height() - self._grip.height() - 4)
        self._grip.raise_()
        QTimer.singleShot(0, self.refresh_blur)


    # ---- drop-zone active/idle look - since Card paints itself from
    # THEMES/ui_style only (no per-instance tint), the "a valid file is
    # being dragged over this" cue is done by retinting the icon/label
    # instead of trying to retint the panel material itself. ------------
    def _style_drop_zone(self, active):
        self._drop_active = active
        t = self._theme
        if active:
            self.drop_icon.setPixmap(_mini_video_glyph(t["accent"], 40))
            self.drop_label.setText(self.engine.tr("Release to add"))
            self.drop_label.setStyleSheet(f"QLabel#miniDropLabel {{ font-size: 13px; color: {t['accent']}; font-weight: 600; padding-top: 4px; }}")
        else:
            has_file = Path(self.engine.src_edit.text()).is_file() if self.engine.src_edit.text() else False
            icon_color = t["accent"] if has_file else t["text_secondary"]
            self.drop_icon.setPixmap(_mini_video_glyph(icon_color, 40))
            self.drop_label.setText(Path(self.engine.src_edit.text()).name if has_file
                                     else self.engine.tr("Drop a video here"))
            self.drop_label.setStyleSheet(f"QLabel#miniDropLabel {{ font-size: 13px; color: {t['text']}; padding-top: 4px; }}")

    # ---- drag & drop (matches MainWindow's own VIDEO_EXTS/behaviour) ---
    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        paths = [url.toLocalFile() for url in urls]
        if any(Path(p).suffix.lower() in self.VIDEO_EXTS for p in paths):
            self._style_drop_zone(active=True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._style_drop_zone(active=False)

    def dropEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        paths = [url.toLocalFile() for url in urls]
        paths = [p for p in paths if Path(p).suffix.lower() in self.VIDEO_EXTS]
        self._style_drop_zone(active=False)
        if not paths:
            event.ignore()
            return
        self._load(paths[0])
        event.acceptProposedAction()

    def mousePressEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if self.drop_card.geometry().contains(pos):
            self.browse()
        super().mousePressEvent(event)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self.engine.tr("Choose a video"), "",
            VIDEO_DIALOG_FILTER)
        if path:
            self._load(path)

    # ---- load + analyze (reuses the engine's real ffprobe analysis) ----
    def _load(self, path):
        self.pbar.hide()
        self.status_label.setText(self.engine.tr("Analyzing\u2026"))
        self.drop_label.setText(Path(path).name)
        t = self._theme
        self.drop_icon.setPixmap(_mini_video_glyph(t["accent"], 40))
        for b in self._preset_buttons:
            b.setEnabled(False)
        QApplication.processEvents()
        self.engine.output_folder = None
        self.engine._set_source(path, analyze=True)
        if self.engine.kind != "unknown":
            self.on_analysis_complete(True)
        elif not any(w.isRunning() for w in getattr(self.engine, "_analysis_workers", {}).values()):
            self.on_analysis_complete(False)

    def on_analysis_complete(self, succeeded: bool):
        if not succeeded or self.engine.kind == "unknown":
            self.status_label.setText(
                self.engine.tr("Couldn't read this file - open Pro mode for more detail."))
            for b in self._preset_buttons:
                b.setEnabled(False)
            return
        headline = {
            "hdr": self.engine.tr("HDR detected \u2014 ready to convert."),
            "sdr": self.engine.tr("This already looks like SDR \u2014 conversion isn't needed."),
            "dolby": self.engine.tr("Dolby Vision detected \u2014 needs an HDR10-compatible base layer."),
        }[self.engine.kind]
        try:
            path = self.engine.src_edit.text()
            fingerprint = UnfinishedStore.source_fingerprint(Path(path))
            if self.engine._unfinished_registry.for_source(fingerprint):
                headline += "  An unfinished conversion of this file was found - Start will offer to resume it."
        except Exception:
            pass
        self.status_label.setText(headline)
        for b in self._preset_buttons:
            b.setEnabled(True)

    # ---- one-click convert: apply a preset, then start exactly like the
    # Pro UI's own preset buttons would (see _apply_*_preset/start() on
    # MainWindow) ------------------------------------------------------
    def _start_with(self, preset_fn):
        if self.engine.kind == "unknown":
            return
        preset_fn()
        self.engine.start()
        if self.engine.proc is not None:
            for b in self._preset_buttons:
                b.setEnabled(False)
            self.status_label.setText(self.engine.tr("Converting\u2026"))
            self.pbar.setValue(0)
            self.pbar.show()
            self._poll_timer.start()

    def _poll_progress(self):
        self.pbar.setValue(self.engine.pbar.value())
        if self.engine.proc is None:
            self._poll_timer.stop()
            self.pbar.hide()
            last_run_ok = self.engine._conversion_runner.last_run_ok
            ok = bool(last_run_ok if last_run_ok is not None else self.engine.pbar.value() >= 990)
            self.status_label.setText(
                self.engine.tr("Done \u2014 saved next to the source.") if ok
                else self.engine.tr("Stopped. Drop the file again to retry, or open Pro mode."))
            for b in self._preset_buttons:
                b.setEnabled(True)

    # ---- coming back from the full interface -----------------------------
    def sync_from_engine(self):
        """Show what the engine (the Pro window) is doing right now - used
        when the person switches back to Mini from Pro, possibly in the
        middle of a conversion they set up there."""
        engine = self.engine
        self.title_label.setText(engine.tr("HDR to SDR Video Converter"))
        self.pro_btn.setText(engine.tr("Pro \u2192"))
        self.pro_btn.setToolTip(engine.tr("Open the full interface (all settings, queue, live preview)."))
        self.minimize_btn.setToolTip(engine.tr("Minimize"))
        self.close_btn.setToolTip(engine.tr("Close"))
        self.browse_btn.setText(engine.tr("Browse File"))
        self.mini_fast_btn.setText(engine.tr("Fast"))
        self.mini_optimal_btn.setText(engine.tr("Optimal"))
        self.mini_best_btn.setText(engine.tr("Best quality"))
        self.footer_label.setText(f"v{APP_VERSION} \u00b7 " + engine.tr("Mini mode"))
        self.skip_mini_toggle.setText(engine.tr("Always open Pro mode"))
        self.skip_mini_toggle.setToolTip(
            engine.tr("Skip this launcher next time and start straight in the full interface."))
        src = engine.src_edit.text().strip() if hasattr(engine, "src_edit") else ""
        if src:
            self.drop_label.setText(Path(src).name)
            self.drop_icon.setPixmap(_mini_video_glyph(self._theme["accent"], 40))
        else:
            self._style_drop_zone(self._drop_active)
        converting = engine.proc is not None
        if converting:
            self.status_label.setText(engine.tr("Converting\u2026"))
            self.pbar.setValue(engine.pbar.value())
            self.pbar.show()
            for b in self._preset_buttons:
                b.setEnabled(False)
            self._poll_timer.start()
        else:
            self._poll_timer.stop()
            self.pbar.hide()
            ready = bool(src) and engine.kind != "unknown"
            for b in self._preset_buttons:
                b.setEnabled(ready)

    # ---- switch to the full interface ----------------------------------
    def open_pro(self):
        """Hand over to the full interface and get this launcher fully off
        screen. Deliberately hide(), never close() - close() is this
        window's real quit path (see closeEvent below).

        Why this isn't just hide()+engine.show(): this window is frameless
        + translucent, and on Windows such a layered window can be left
        painted on screen (a "ghost" that doesn't go away) when it's hidden
        from inside its own child button's click handler, especially while
        another top-level is being shown in the same event-loop turn
        (MainWindow.showEvent even pumps processEvents() on its first show).
        So: bring Pro up first, then take this window down in a *separate*
        event-loop turn - fully transparent first (so even a stale frame is
        invisible), then hidden, then re-checked once more shortly after in
        case the compositor hadn't let go yet. showEvent below restores
        opacity so a later re-show (tray restore, etc.) isn't invisible."""
        self._poll_timer.stop()
        engine = self.engine
        engine.show()
        engine.raise_()
        engine.activateWindow()
        QTimer.singleShot(0, self._dismiss_launcher)
        QTimer.singleShot(120, self._dismiss_launcher)

    def _dismiss_launcher(self):
        if not self.isVisible() and self.windowOpacity() == 0.0:
            return
        if not self.engine.isVisible():
            # Pro isn't actually up (e.g. it was closed/hidden in the
            # meantime) - leave the launcher alone rather than leaving
            # the user with no window at all.
            return
        self.setWindowOpacity(0.0)
        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        if self.windowOpacity() < 1.0:
            self.setWindowOpacity(1.0)

    def _on_skip_mini_toggled(self, checked):
        """Persists the "Always open Pro mode" preference immediately -
        see the toggle's own comment in __init__ for why this isn't part
        of any Apply/Back flow. Turning it on also jumps straight to Pro
        mode right now (not just "from next launch onward"), since
        there'd otherwise be no visible effect of flipping the switch
        until the app is restarted."""
        self.engine.i18n.settings.setValue("skip_mini_window", "true" if checked else "false")
        if checked:
            self.open_pro()

    def closeEvent(self, event):
        # Mini mode is the app's actual entry point/lifecycle owner (see
        # QApplication.setQuitOnLastWindowClosed(False) in main()) - its
        # own [X] is the one that really exits, unlike the Pro window's
        # (see MainWindow.closeEvent), which just comes back here.
        choice = self.engine._confirm_close_while_converting(self)
        if choice == "cancel":
            event.ignore()
            return
        if choice == "tray":
            event.ignore()
            self.engine._minimize_to_tray(self)
            return
        self.engine._tray_quit_confirmed = True
        self.engine.close()
        event.accept()
        QApplication.instance().quit()
