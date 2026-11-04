from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from dataclasses import asdict, replace

from PySide6.QtCore import (
    QVariantAnimation, QEasingCurve,
    Qt, QTimer, QProcess, QEventLoop, QUrl, QSize, QEvent, QSettings, QPoint, QByteArray, QRect, QRectF, QPointF,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QTextCursor, QDesktopServices, QPixmap, QImage, QKeySequence, QShortcut, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QGridLayout, QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QProgressBar, QPlainTextEdit, QGroupBox, QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox, QFrame, QSplitter, QScrollArea, QDialog, QMenu, QListWidget, QListWidgetItem, QAbstractItemView, QStyledItemDelegate, QStyleOptionViewItem, QStyleOptionToolButton, QToolButton, QSizePolicy, QStackedWidget, QStyle, QSystemTrayIcon, QGraphicsDropShadowEffect, QLayout,
)

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

from hdr2sdr.core.bitrate import estimate_bitrate_kbps, estimate_output_bytes
from hdr2sdr.core.destination import DestinationPlan, _same_path
from hdr2sdr.core.dolby_vision import (
    dovi_configuration, dovi_profile,
)
from hdr2sdr.core.commands import (
    ConversionSettings, _ffmpeg_exe, crop_sample_offsets, gamut_mode_lookup, plan_tracks, build_command, build_container_args, build_ffmpeg_command, build_test_clip_command, build_vf_chain, select_crop_filter, tonemap_lookup, settings_signature, CROPDETECT_FILTER,
)
from hdr2sdr.core.constants import (
    BIT_DEPTH_LABELS, GAMUT_MAPPING_INFO, GAMUT_MAPPING_LABELS, COLOR_RANGE_NAMES, VIDEO_DIALOG_FILTER, VIDEO_EXTENSIONS, DEFAULT_QUALITY_RANGE, ENCODER_MAP, FFMPEG_BIT_DEPTH, HB_ENCODER_IDS, KOFI_URL, LANGUAGE_NAMES, LIBPLACEBO_DOVI_INFO, LIBPLACEBO_PEAK_DETECT_INFO, MATRIX_NAMES, PRIMARIES_NAMES, PRIORITY_LEVELS, PRIORITY_NICE, PROGRESS_BAR_HEIGHT, QUALITY_RANGES, QUALITY_SLIDER_INFO, QUALITY_TIER_BOUNDS_51, QUALITY_TIER_BOUNDS_BY_ENCODER, QUALITY_TIER_REPS_51, QUALITY_TIER_REPS_BY_ENCODER, RESOLUTIONS, SPIN_WIDTH, match_active_preset, quality_tier_label, should_reset_quality_on_range_change, TONEMAP_INFO, TONEMAP_PRO_CPU, TONEMAP_PRO_GPU, TONEMAP_PRO_GPU_ST2094, TOUCH_SLIDER_HEIGHT, TRANSFER_NAMES, _HB_ENCODER_LIST,
)
from hdr2sdr.core.progress import (
    ErrorHintScanner, LineAssembler, make_progress_parser, select_progress_status_text,
)
from hdr2sdr.core.utils import (
    probe_streams,
    clock_time, detect_chroma_subsampling, even, exe, format_time, log_suppressed, refresh_windows_path, wrappable,
)
from hdr2sdr.i18n.localizer import Localizer
from hdr2sdr.paths import CPU_COUNT, PORTABLE_TOOLS_DIR
from hdr2sdr.runtime import after_action, jobobject, power
from hdr2sdr.runtime.conversion import ConversionRunner, is_process_active, is_process_running, is_valid_qobject
from hdr2sdr.runtime.lifecycle import RunState, RunStateMachine, transition_safely
from hdr2sdr.runtime.queue import QueueController
from hdr2sdr.runtime.resume import ResumeAction, ResumeCoordinator
from hdr2sdr.runtime.workflow import (
    DolbyVisionAction, FinishAction, RunContext, RunWorkflow, StartAction,
)
from hdr2sdr.runtime.clip_runs import ClipRunManager
from hdr2sdr.runtime.unfinished import UnfinishedStore
from hdr2sdr.runtime.history import HistoryStore
from hdr2sdr.runtime import tools as tools_runtime
from hdr2sdr.runtime.capabilities import check_start_capabilities, invalidate_transient_libplacebo_cache
from hdr2sdr.ui.tool_installer import ToolInstallThread
from hdr2sdr.ui.probe import capture_text, run_capture
from hdr2sdr.ui.after_action import AfterActionCountdown
from hdr2sdr.ui.history_dialog import ConversionHistoryDialog
from hdr2sdr.ui.conversion_preferences import ConversionPreferences
from hdr2sdr.ui.analyze_worker import AnalyzeWorker
from hdr2sdr.runtime.updates import (
    APP_VERSION, UPDATE_CHECK_REPO, _tag_is_newer, fetch_latest_github_tag,
)
from hdr2sdr.theme.look import copy_look, load_slots, load_look, palette_name, save_look
from hdr2sdr.theme.metrics import button_radius, load_radii
from hdr2sdr.theme.palettes import (
    _neon_active,
)
from hdr2sdr.theme.theme_tokens import (
    FONT_BODY_CANDIDATES,
    FONT_MONO_CANDIDATES,
    FONT_SEMI_CANDIDATES,
    build_combo_view_qss,
    build_glass_button_qss,
    build_main_window_qss,
    build_message_box_qss,
    build_scroll_area_qss,
    build_tokens,
    build_tooltip_qss,
    build_tray_menu_qss,
)
from hdr2sdr.ui.dialogs.theme_customize import ThemeCustomizeDialog
from hdr2sdr.ui.glass import (
    _mix_hex, blurred_backdrop,
)
from hdr2sdr.ui.icons import (
    load_app_icon, video_drop_glyph,
)
from hdr2sdr.ui.imaging import clipping_pct
from hdr2sdr.ui.style_helpers import (
    chevron_icon_path, loosen_combo, pick_font, set_role, style_progress_bar,
)
from hdr2sdr.ui.widgets.buttons import GlassButton, TitleBarButton
from hdr2sdr.ui.win_native import NativeChromeFilter, _is_live_qobject
from hdr2sdr.ui.widgets.combo import RoundedComboBox, RoundedMenuButton
from hdr2sdr.ui.widgets.overlay import OverlayPanel
from hdr2sdr.ui.widgets.containers import (
    Card, _BgLayer, _TitleBarDragArea,
)
from hdr2sdr.ui.widgets.progress import ProgressShimmer
from hdr2sdr.ui.widgets.sliders import (
    PreviewScrubSlider, RangeSlider, RoundHandleSlider,
)
from hdr2sdr.ui.widgets.param_controls import (
    ParamSlider, ColorPad2D, TierSlider, TimelineSlider, ScrubSpinBox, LinkButton, make_value_chip, TEMPERATURE_GRADIENT, DEFAULT_CONTROL_STYLE,
)
from hdr2sdr.ui.widgets.frame_strip import FrameStrip, FrameStripLoader
from hdr2sdr.ui.widgets.stepper import make_stepper, StepperRowLayout, LeftRoomLayout, STEPPER_GLOW_ROOM
from hdr2sdr.ui.widgets.toggles import ToggleSwitch
from hdr2sdr.ui.tooltip_layout import place_tooltip, tooltip_max_width
from hdr2sdr.ui.widgets.tooltip import _AppToolTip




class _AdaptiveFlowLayout(QLayout):
    """Places child widgets (e.g. tone-mapping ToggleSwitches) on a single row when
    their combined sizeHints fit within the available width, and wraps onto a second
    line without horizontal clipping when narrower or translated."""

    def __init__(self, parent=None, h_spacing: int = 8, v_spacing: int = 6):
        super().__init__(parent)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, max(1, width), 0), test_only=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        max_w = 0
        max_h = 0
        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            hint = wid.minimumSizeHint() if wid is not None else item.minimumSize()
            max_w = max(max_w, hint.width())
            max_h = max(max_h, hint.height())
        m = self.contentsMargins()
        if max_w == 0 and max_h == 0:
            return QSize(0, 0)
        return QSize(max_w + m.left() + m.right(), max_h + m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        avail_w = max(1, eff.width())
        visible = []
        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            hint = item.sizeHint()
            visible.append((item, hint.width(), hint.height()))
        if not visible:
            return 0
        x = eff.x()
        y = eff.y()
        row_h = 0
        for item, w, h in visible:
            if x > eff.x() and x + w > eff.x() + avail_w:
                x = eff.x()
                y += row_h + self._v_space
                row_h = 0
            item_w = min(w, avail_w)
            if not test_only:
                item.setGeometry(QRect(x, y, item_w, h))
            x += item_w + self._h_space
            row_h = max(row_h, h)
        return (y - eff.y()) + row_h + m.top() + m.bottom()


class _QueueItemDelegate(QStyledItemDelegate):
    """Paints row selection/hover background while suppressing delegate text,
    letting _QueueItemWidget render the filename and compact remove (×) button."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)


class _QueueRemoveButton(QToolButton):
    """Compact remove button for queue items with vector-drawn cross.

    Uses QPainter to draw the cross centered exactly on QRectF(self.rect()).center()
    so the glyph is immune to font baseline and font metric variations across
    operating systems and DPI scaling factors.
    """

    def __init__(self, item_widget: "_QueueItemWidget", parent=None):
        super().__init__(parent)
        self.item_widget = item_widget
        self._hover = False
        self.setObjectName("queueItemRemoveBtn")
        self.setText("×")
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def initStyleOption(self, option: QStyleOptionToolButton):
        super().initStyleOption(option)
        option.text = ""

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        win = getattr(self.item_widget, "main_window", None)
        t = getattr(win, "tokens", {}) if win else {}
        selected = getattr(self.item_widget, "_selected", False)

        if self._hover:
            color = QColor("#ffffff") if selected else QColor(t.get("danger", "#ef4444"))
        else:
            color = QColor(t.get("on_accent", "#ffffff")) if selected else QColor(t.get("text_secondary", "#9ca3af"))

        c = QRectF(self.rect()).center()
        h = 3.5
        pen = QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPointF(c.x() - h, c.y() - h), QPointF(c.x() + h, c.y() + h))
        painter.drawLine(QPointF(c.x() - h, c.y() + h), QPointF(c.x() + h, c.y() - h))
        painter.end()


class _QueueItemWidget(QWidget):
    """Row widget for items in MainWindow's queue_list, featuring filename and a compact remove (×) button."""

    def __init__(self, item: QListWidgetItem, window: "MainWindow", parent=None):
        super().__init__(parent)
        self.item = item
        self.main_window = window
        self._selected = False
        self.setFixedHeight(24)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)

        text = item.text() or (Path(item.toolTip()).name if item.toolTip() else "")
        self.label = QLabel(text)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.label.setStyleSheet("QLabel { background: transparent; }")
        layout.addWidget(self.label, 1)

        self.remove_btn = _QueueRemoveButton(self)
        self.remove_btn.setToolTip(self.main_window.tr("Remove"))
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        layout.addWidget(self.remove_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _on_remove_clicked(self):
        if hasattr(self.main_window, "_on_queue_item_remove_clicked"):
            self.main_window._on_queue_item_remove_clicked(self.item)

    def update_text(self, text: str):
        self.label.setText(text)

    def set_selected(self, selected: bool):
        self._selected = selected
        t = getattr(self.main_window, "tokens", {})
        on_accent = t.get("on_accent", "#ffffff")
        text_color = t.get("text", "#e5e7eb")
        if selected:
            self.label.setStyleSheet(f"background: transparent; color: {on_accent}; font-weight: 500;")
            self.remove_btn.setStyleSheet("""
                QToolButton#queueItemRemoveBtn {
                    border: none;
                    border-radius: 9px;
                    background: transparent;
                    padding: 0;
                    margin: 0;
                }
                QToolButton#queueItemRemoveBtn:hover {
                    background: rgba(255, 255, 255, 0.25);
                }
            """)
        else:
            self.label.setStyleSheet(f"background: transparent; color: {text_color};")
            self.remove_btn.setStyleSheet("")
        self.remove_btn.update()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.i18n = Localizer()
        load_radii(self.i18n.settings)      # saved Card / Button rounding (theme panel)
        self.setObjectName("mainWindowRoot")
        self.setWindowTitle(self.tr("HDR to SDR Video Converter"))
        self.setAcceptDrops(True)

        # Frameless: no native title bar/border - see self.titlebar in
        # build() for the drag/maximize replacement, and
        # mousePressEvent/mouseMoveEvent below for the edge-resize
        # replacement (the native frame's resize-by-dragging-any-edge is
        # gone along with everything else about it, and MiniModeWindow's
        # single-corner QSizeGrip isn't enough for a window this size -
        # people resize a settings-heavy window from whichever edge is
        # convenient, not just one corner). _BgLayer reads this flag to
        # know whether to clip/border itself to a rounded window shape
        # (unchanged for anything that doesn't set it, so
        # ThemeCustomizeDialog and every other still-framed window keep
        # painting a plain square fill exactly as before).
        self._frameless = True
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Needed for mouseMoveEvent to fire (and update the resize
        # cursor) while hovering near an edge with no button held - by
        # default a plain QWidget only gets move events during a drag.
        self.setMouseTracking(True)

        self.FONT = pick_font(list(FONT_BODY_CANDIDATES), "Segoe UI")
        self.FONT_SEMI = pick_font(list(FONT_SEMI_CANDIDATES), self.FONT or "Segoe UI")
        self.MONO = pick_font(list(FONT_MONO_CANDIDATES), "monospace")
        if self.FONT:
            self.setFont(QFont(self.FONT, 10))

        self.duration = 0.0
        self.kind = "unknown"
        self._dv_profile = None
        self._dv_rpu_auto_enabled = False
        self._dv_rpu_user_override = False
        self._src_color_transfer = ""
        self._src_color_primaries = ""
        self._src_color_matrix = ""
        self._src_color_range = ""
        self._job_tonemap_override = None
        self._job_dv_rpu_override = None
        self._job_tonemap_fallback_attempts = 0
        self._gpu_tonemap_fallback_used = False
        self._fallback_retry_pending = False
        self._tonemap_fallback_declined_for_current = False
        self.show_detailed_conversion_info = (
            str(self.i18n.settings.value("show_detailed_conversion_info", "false")).lower() == "true"
        )
        self._enc_is_two_col = None
        self._q_is_beside = None
        self.src_width = None
        self.src_height = None
        self.has_bt2390 = False
        self.has_st2094 = False
        self.ffmpeg_version = None
        self.encoders = set()
        self.hb_encoders = set()
        self.proc = None
        self._conversion_runner = ConversionRunner(self)
        self._conversion_runner.parsed_line.connect(self._handle_line)
        self._conversion_runner.finished.connect(self._on_finished)
        self._conversion_runner.process_error.connect(self._on_process_error)
        self._run_lifecycle = RunStateMachine()
        self.proc_psutil = None
        self._setup_proc = None
        self.current_out_sec = 0.0
        self.preview_path = None
        self.preview_mtime = 0
        self._frame_preview_path = Path(tempfile.gettempdir()) / f"hdr2sdr_frame_preview_{os.getpid()}.png"
        self._frame_preview_proc = None
        self._frame_preview_gen = 0
        self._frame_preview_seek = 0.0
        self._frame_preview_is_end = False
        self._current_preview_pixmap = None
        self._test_clip_proc = None
        self._test_clip_runs = ClipRunManager()
        self._test_clip_seek = 0.0
        self._test_clip_using_hb = False
        self._reset_test_clip_parsers()
        # The preview card shows one status line under the frame, not two:
        # normal state is the "Preview frame at ..." meta text, but a test
        # clip render/cancel/failure message takes over that same line
        # while it's relevant, then hands it back. Two separate labels
        # used to stack here, which left an awkward double gap once the
        # test-clip line had something to say.
        self._preview_meta_text = ""
        self._test_clip_status_text = ""
        self._queue_controller = QueueController()
        self._queue_controller.paths = []
        self._queue_controller.running = False
        self._after_countdown = None      # the live "PC will ... in N s" countdown, if any
        self._queue_controller.batch_clean = True          # every item since the batch began finished cleanly
        self.output_folder = None
        self._test_clip_out = None
        self._last_analysis_raw = None
        self._last_analysis_doc = None
        self._analysis_generation = 0
        self._analysis_workers = {}
        self._analysis_start_pending = False
        self._start_in_progress = False
        self.side_tab = None
        self.stopping = False
        self.paused = False
        self.using_hb = False
        self._autocrop_value = None
        self._reset_run_parsers()
        self._current_encoder_label = None
        self._hwaccel_decode_on = False
        # Set when hardware decode died mid-run and the job was resumed with
        # software decode instead; overrides the toggle for the rest of this
        # resume chain only (cleared when the ResumeCoordinator resets).
        self._hwaccel_forced_off = False
        # ---- resume-after-failure state (FFmpeg backend only) ----
        # current_out_sec is already tracked separately below (used for the
        # UI's position readout too) and is kept as an *absolute* position
        # in source time even across resume segments - see the coordinator's
        # progress_offset and FfmpegProgressParser._block_to_stats.
        self._resume_coordinator = ResumeCoordinator()
        self._run_context = RunContext()     # snapshot of the active job
        self.gpu_tool = shutil.which("nvidia-smi")
        self.handbrake_tool = exe("HandBrakeCLI")
        # ONE shared look (base, gradient, glass, neon glow ...) restored from
        # QSettings; Light / Dark / Neon are just built-in presets of it
        # (see hdr2sdr/theme/look.py). The first run after the upgrade
        # migrates the old three per-theme profiles.
        self.look = load_look(self.i18n.settings)
        self._apply_look_state()

        self._tooltip = _AppToolTip()
        # Themes the native title bar of every framed dialog/message box
        # (Customize theme, Check for updates, setup results, ...) so no
        # stock-Windows strip is left across the top of them.
        self._native_chrome = NativeChromeFilter(self)
        self._app_filters_installed = False


        self._unfinished_registry = UnfinishedStore(
            self._history_file_path().with_name("unfinished_runs.json"))
        self._history_store = HistoryStore(self._history_file_path())
        self.build()
        # ---- completion notifications (tray balloon) ------------------
        # A tray icon is required for QSystemTrayIcon.showMessage() to
        # actually surface a balloon/toast on every platform (Windows in
        # particular ignores showMessage() from a hidden tray icon), so
        # one is created and shown here rather than only lazily on first
        # use. isSystemTrayAvailable() covers headless/minimal Linux
        # setups where there's no tray to attach to at all.
        self._tray = None
        self._tray_restore_target = None  # which hidden window (self or the
        # Mini launcher) "minimize to tray" should bring back - see
        # _minimize_to_tray()/_on_tray_activated() below.
        if QSystemTrayIcon.isSystemTrayAvailable():
            tray_icon = load_app_icon()
            self._tray = QSystemTrayIcon(tray_icon if not tray_icon.isNull() else self.style().standardIcon(
                QStyle.StandardPixmap.SP_ComputerIcon), self)
            self._tray.setToolTip(self.tr("HDR to SDR Video Converter"))
            # Clicking the balloon itself opens the output folder - the
            # cheapest way to give the notification a useful action
            # without a second custom-button API that showMessage()
            # doesn't actually expose cross-platform.
            self._tray.messageClicked.connect(self._open_output_folder)
            # Clicking (or double-clicking, platform-dependent) the tray
            # icon itself - as opposed to a balloon - is the "bring the
            # window back" gesture, for whichever window "minimize to
            # tray" hid (see _minimize_to_tray()).
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.setContextMenu(self._build_tray_menu())
            self._style_tray_menu()
            self._tray.show()
        # Installed only now, after every widget referenced anywhere in
        # eventFilter() (queue_list, preview_label, ...) actually exists -
        # doing this earlier meant Qt's synchronous events fired *during*
        # widget construction (ChildAdded/ParentChange etc. don't wait for
        # an event loop) were already reaching eventFilter() and hitting
        # self.queue_list before build() had created it, raising inside a
        # C++-invoked virtual method - which PySide6 doesn't recover from
        # cleanly and instead surfaces as an unrelated-looking
        # "QVBoxLayout returned NULL" crash a few widget constructions later.
        self._install_app_filters()
        # Taken off again before the QApplication is torn down: left installed, the two app-wide
        # Python filters call into each other during interpreter shutdown.
        QApplication.instance().aboutToQuit.connect(self._remove_app_filters)
        self.retranslate_static_ui()
        self.apply_theme()
        # Second call, same theme, is deliberate: a user's first manual
        # theme switch after launch permanently fixes an oversized-font
        # look that's otherwise stuck until they do that (confirmed by
        # testing - toggling the theme cycle button, or changing it from
        # the customize panel, both do it; switching back and forth
        # afterward never brings the oversized look back). That points at
        # setStyleSheet()'s first-ever application on this window leaving
        # something short of fully settled no matter how much the flush
        # below chases it, while a second, later application - identical
        # stylesheet, identical everything - finishes the job. Rather than
        # keep chasing whichever specific metric is still stale after one
        # pass (the "TEST BUILD" comment below is the history of exactly
        # that chase), just do the second pass ourselves before the window
        # is ever shown, instead of waiting for the person to trigger one
        # by hand.
        self.apply_theme()
        # ---- TEST BUILD: synchronous startup, one-shot sizing --------
        # Every previous fix here (seeding method_combo before check(),
        # flushing pending style-polish events, forcing every nested
        # layout to invalidate/recompute) closed one specific gap between
        # the window's first computed height and its "true" one - and the
        # jump kept coming back from a different cause each time. The
        # common thread: any height computed before the window has been
        # shown and laid out for real once is a guess, and exactly which
        # widget metrics have "settled" by that point isn't something
        # this code can fully pin down from outside Qt's own scheduling.
        #
        # Different approach: don't guess, don't size twice. Run the
        # capability probe (self.check() - shells out to ffmpeg and
        # HandBrakeCLI) synchronously right here, before the window is
        # shown at all, so every combo/label it touches already holds its
        # final content, then size the window exactly once from that.
        # There's nothing left to catch up to after showing, so there's
        # nothing left to jump. The tradeoff: the window's first
        # appearance is delayed by however long check() takes (typically
        # well under a second) instead of appearing instantly and
        # settling a moment later - worth testing against the old
        # behaviour to see which one actually reads better.
        self.check()
        self._conversion_preferences = ConversionPreferences(
            self.i18n.settings, self._conversion_preference_controls(),
            self._restore_output_folder, lambda: self.output_folder,
            lambda: self._on_bit_depth_changed(self.bit_depth_combo.currentText()), self)
        self._conversion_preferences.restore()
        self._conversion_preferences.bind()
        self._fit_height_to_content(width=1080)
        self._fit_height_to_content()
        self._has_saved_geometry = self._restore_saved_window_geometry()
        self.update_run_controls()
        if hasattr(self, "frame_strip"):
            self.frame_strip.set_scrub_enabled(False)
        self._update_frame_strip_during_conversion()

    def tr(self, source, **values):
        """Translate an English source string while keeping English as a safe fallback."""
        return self.i18n.text(source, **values)

    def _translate_widget_property(self, widget, getter, setter, property_name):
        source = widget.property(property_name)
        if source is None:
            source = getter()
            widget.setProperty(property_name, source)
        if source:
            translated = self.tr(source)
            if property_name == "i18n_text" and (widget.property("role") or "") == "section-title":
                translated = translated.upper()
            setter(translated)

    def retranslate_static_ui(self):
        """Translate visible static captions without altering internal combo-box values."""
        self.setWindowTitle(self.tr("HDR to SDR Video Converter"))
        for widget in self.findChildren(QGroupBox):
            self._translate_widget_property(widget, widget.title, widget.setTitle, "i18n_title")
        dynamic_labels = {
            getattr(self, "preview_label", None),
            getattr(self, "analysis_label", None),
            getattr(self, "preview_position_label", None),
            getattr(self, "preview_meta_label", None),
            getattr(self, "status_label", None),
            getattr(self, "speed_label", None),
            getattr(self, "bitrate_label", None),
            getattr(self, "summary_params_label", None),
            getattr(self, "preset_hint_label", None),
        }
        for widget in self.findChildren(QLabel):
            if widget in dynamic_labels:
                continue
            self._translate_widget_property(widget, widget.text, widget.setText, "i18n_text")

        if getattr(self, "_last_analysis_doc", None) is not None:
            self._apply_analysis_document(self._last_analysis_doc, is_retranslate=True)
        elif not self.src_edit.text() or not Path(self.src_edit.text()).is_file():
            self.analysis_label.setText(self.tr("Select a video to analyze it automatically."))

        if getattr(self, "_current_preview_pixmap", None) is not None and not self._current_preview_pixmap.isNull():
            self.preview_label.setPixmap(self._current_preview_pixmap.scaled(
                max(self.preview_label.width(), 320), max(self.preview_label.height(), 200),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        elif getattr(self, "duration", 0) > 0 and self.src_edit.text() and Path(self.src_edit.text()).is_file():
            self._schedule_frame_preview()
        elif not self.src_edit.text() or not Path(self.src_edit.text()).is_file():
            self.preview_label.setText(self.tr("Choose a source video to see an instant preview of the current settings."))

        if hasattr(self, "preview_position_label") and hasattr(self, "preview_slider"):
            if getattr(self, "duration", 0) > 0:
                self._update_preview_position_label(self.preview_slider.value())
            else:
                self.preview_position_label.setText("0:00")
        for widget in self.findChildren(QPushButton):
            self._translate_widget_property(widget, widget.text, widget.setText, "i18n_text")
            self._translate_widget_property(widget, widget.toolTip, widget.setToolTip, "i18n_tooltip")
        for widget in self.findChildren(ToggleSwitch):
            self._translate_widget_property(widget, widget.text, widget.setText, "i18n_text")
            self._translate_widget_property(widget, widget.toolTip, widget.setToolTip, "i18n_tooltip")
        self.language_button.setText("")
        self.mode_btn.setText("")
        self.language_button.setToolTip(self.tr("Interface language"))
        self._retranslate_tools_channel()
        self._retranslate_after_action()
        self._retranslate_controls()
        if hasattr(self, "_retranslate_advanced_filters"):
            self._retranslate_advanced_filters()
        if hasattr(self, "_update_adv_filters_header"):
            self._update_adv_filters_header()
        if hasattr(self, "_update_adv_encoding_header"):
            self._update_adv_encoding_header()
        if hasattr(self, "_update_quality_tier_label"):
            self._update_quality_tier_label()
        if hasattr(self, "update_bitrate_estimate"):
            self.update_bitrate_estimate()
        if hasattr(self, "_sync_preset_segment_state"):
            self._sync_preset_segment_state()
        if hasattr(self, "_retranslate_section_headers"):
            self._retranslate_section_headers()
        if hasattr(self, "tm_toggles_layout"):
            self.tm_toggles_layout.invalidate()
        self._run_status_min_w = 0
        if hasattr(self, "_update_inline_progress_status"):
            self._update_inline_progress_status()
        if hasattr(self, "_update_output_folder_btn"):
            self._update_output_folder_btn()
        if hasattr(self, "_retranslate_queue_items"):
            self._retranslate_queue_items()
        if hasattr(self, "preview_duration_label") and hasattr(self, "preview_timeline"):
            span = max(1, self.preview_timeline.high() - self.preview_timeline.low()) if self.preview_timeline.isEnabled() else 0
            self.preview_duration_label.setText(self.tr("Step: {span} s", span=span))
        if hasattr(self, "_update_conversion_adaptive_layout"):
            self._update_conversion_adaptive_layout()
        if hasattr(self, "_ensure_right_column_width"):
            self._ensure_right_column_width()
        if hasattr(self, "_invalidate_all_layouts"):
            self._invalidate_all_layouts()
        if hasattr(self, "conv_scroll_content") and self.conv_scroll_content.layout() is not None:
            self.conv_scroll_content.layout().activate()
        if self.layout() is not None:
            self.layout().activate()

    def rebuild_language_menu(self):
        saved_language = self.i18n.settings.value("interface_language", "system")
        choices = [("system", "System default (Windows)"), *LANGUAGE_NAMES.items()]
        self._language_codes = [code for code, _name in choices]
        current = self._language_codes.index(saved_language) if saved_language in self._language_codes else 0
        self.language_button.set_items([name for _code, name in choices], current)

    def _on_language_picked(self, index):
        if 0 <= index < len(self._language_codes):
            self.change_language(self._language_codes[index])

    def change_language(self, code):
        self.i18n.set_language(code)
        self.rebuild_language_menu()
        self.retranslate_static_ui()

    # ---- theming ---------------------------------------------------
    def _glass_button_qss(self, t):
        return build_glass_button_qss(t)

    def apply_theme(self):
        if hasattr(self, "look") and self.theme_name == palette_name(self.look):
            self.tokens = build_tokens(self.look)
        elif getattr(self, "tokens", None) is None or self.tokens.get("theme_name") != self.theme_name:
            self.tokens = build_tokens(theme_name=self.theme_name)
        t = self.tokens
        br = min(button_radius(), 13)
        chevron_icon = chevron_icon_path(t['text_secondary'])
        qss = build_main_window_qss(
            t,
            button_radius_px=button_radius(),
            chevron_icon=chevron_icon,
            font=self.FONT,
            font_semi=self.FONT_SEMI,
            mono=self.MONO,
            touch_slider_height=TOUCH_SLIDER_HEIGHT,
            ui_style=self.ui_style,
        )
        self.setStyleSheet(qss)
        self._style_tray_menu()
        if hasattr(self, "log"):
            self.log.viewport().setAutoFillBackground(False)
            self.log.viewport().setObjectName("logViewport")
            self.log.viewport().setStyleSheet("QWidget#logViewport { background: transparent; }")
        if hasattr(self, "conv_scroll"):
            self.conv_scroll.setStyleSheet(build_scroll_area_qss(t, "convScroll"))
        if hasattr(self, "analysis_scroll"):
            self.analysis_scroll.setStyleSheet(build_scroll_area_qss(t, "analysisScroll"))
        self._tooltip.setStyleSheet(build_tooltip_qss(t, button_radius_px=button_radius()))
        for tw in self.findChildren(ToggleSwitch):
            tw.setProperty("on_color", t["accent"])
            tw.setProperty("off_color", t["border"])
            tw.setProperty("text_color", t["text"])
            tw.update()
        # A QComboBox's popup (its .view(), an internal QAbstractItemView)
        # is its own top-level window, not a plain child widget - the
        # "QComboBox QAbstractItemView { ... }" descendant-selector rule
        # in qss above normally reaches it fine, but any widget anywhere
        # in that combo's ancestor chain that has its *own* local
        # setStyleSheet() call (e.g. adv_filters_panel's "background:
        # transparent") can interrupt that cascade for just that popup,
        # same underlying "local stylesheet breaks inherited theming down
        # the tree" issue as the tooltip workaround above. Setting the
        # popup's stylesheet directly on itself, per combo, every theme
        # switch, sidesteps the cascade entirely instead of hoping it
        # reaches every popup unbroken.
        combo_view_qss = build_combo_view_qss(t)
        for combo in self.findChildren(QComboBox):
            combo.view().setStyleSheet(combo_view_qss)
        # RoundedComboBox (backend/encoder/bit depth/quality mode/method/
        # resolution/container/denoise/priority) doesn't use that native
        # .view() popup at all - see RoundedComboPopup's docstring for why
        # (rounded corners + WA_TranslucentBackground don't work on it).
        # findChildren(QComboBox) above still finds these too since
        # RoundedComboBox is a QComboBox subclass, and still harmlessly
        # themes their real .view() - it's just never shown - so this loop
        # only needs to additionally push colours into the custom popup
        # each one actually displays.
        for combo in self.findChildren(RoundedComboBox):
            combo.set_popup_theme(t['bg_card'], t['border'], t['text'], t['bg_card_alt'], t['accent'])
        self.language_button.set_popup_theme(t['bg_card'], t['border'], t['text'], t['bg_card_alt'], t['accent'])
        for panel_ in (getattr(self, "power_dialog", None), getattr(self, "update_panel", None)):
            if panel_ is not None:
                try:
                    panel_.set_theme(t['bg_card'], t['border'])
                except RuntimeError:
                    pass
        tools_btn = getattr(self, "tools_channel_btn", None)
        if tools_btn is not None:
            try:
                tools_btn.set_popup_theme(t['bg_card'], t['border'], t['text'], t['bg_card_alt'], t['accent'])
            except RuntimeError:
                self.tools_channel_btn = None
        if getattr(self, "pbar", None) is not None:
            style_progress_bar(self.pbar, t, self.theme_name)
        # setStyleSheet() queues a style-change/polish for every affected
        # widget instead of applying it synchronously. Card.body's own
        # margins are set in code (setContentsMargins), so they're
        # unaffected, but every QLineEdit/QPushButton (FILES' Source/SDR
        # output fields and Browse buttons, for one) is sized purely from
        # its QSS "padding: 5px 7px"/"padding: 7px 12px" - and immediately
        # after this call, in __init__ before the window is ever shown,
        # minimumSizeHint() can still see each widget's *unpolished*
        # (smaller, native-style) size. Flushing the queued polish here
        # means minimumSizeHint() below already reflects the final, padded
        # sizes rather than a stale pre-polish guess.
        #
        # Only do this before the window's first show(), though - once
        # it's actually on screen, pumping the event loop mid-apply_theme()
        # (e.g. from the mode button switching themes at runtime) forces
        # Qt to paint a real frame using whichever widgets happened to have
        # repolished so far, not the finished result - some already on the
        # new theme's font size, some still on the old one. That's a real
        # flash of oversized-then-shrinking text, not just a hypothetical:
        # reproduced by switching themes while the window is open. Nothing
        # downstream of this point actually needs the flush once shown -
        # minimumSizeHint() only mattered for the pre-show sizing above.
        if not self.isVisible():
            QApplication.processEvents()
        # GlassButton's own drop shadow should only show in glass mode
        # (classic mode stays flat, as before) - toggled here rather
        # than per-paint to avoid re-entrant repaints from flipping a
        # graphics effect inside paintEvent(). Neon reuses the same
        # QGraphicsDropShadowEffect but re-tuned into a soft coloured
        # glow (accent colour per role, INDIGO for anything neutral)
        # instead of glass's neutral black elevation shadow. Neon is
        # checked first (not glass) so a neon+glass combo still gets its
        # own coloured glow rather than being flattened into a plain
        # neutral glass shadow - the two are no longer mutually exclusive.
        is_neon = _neon_active(self)
        for gb in self.findChildren(GlassButton):
            is_step = (gb.property("role") or "") == "stepper"
            role = gb.property("role") or ""
            is_neon_target = role == "go" or (
                role in ("panel-toggle", "preset-segment-btn") and gb.isCheckable() and gb.isChecked()
            )
            if is_neon and is_neon_target and gb.isEnabled():
                glow_key = GlassButton._TINT_ROLES.get(role, "accent")
                glow = QColor(t[glow_key])
                glow.setAlpha(190)
                gb._shadow.setBlurRadius(10 if is_step else 24)
                gb._shadow.setOffset(0, 0)
                gb._shadow.setColor(glow)
                gb._shadow.setEnabled(True)
            elif self.ui_style == "glass" and not gb.property("drawer_header"):
                gb._shadow.setBlurRadius(8 if is_step else 10)
                gb._shadow.setOffset(0, 1 if is_step else 2)
                gb._shadow.setColor(QColor(0, 0, 0, 32))
                gb._shadow.setEnabled(True)
            else:
                gb._shadow.setEnabled(False)
            gb.update()
        if hasattr(self, "_pbar_shadow"):
            if is_neon:
                p_glow = QColor(t["accent"])
                p_glow.setAlpha(180)
                self._pbar_shadow.setColor(p_glow)
                self._pbar_shadow.setEnabled(True)
            else:
                self._pbar_shadow.setEnabled(False)
        for card in self.findChildren(Card):
            card._shadow.setEnabled(False)
            card.update()
        for cls in (ParamSlider, TierSlider, ColorPad2D, RoundHandleSlider):
            for ctrl in self.findChildren(cls):
                if hasattr(ctrl, "_bg_cache_key"):
                    ctrl._bg_cache_key = None
                ctrl.update()
        if hasattr(self, "cores_panel_widget") and self.cores_panel_widget is not None:
            self.cores_panel_widget.update()
        if hasattr(self, "controls_card") and self.controls_card is not None:
            self.controls_card.update()
        # language_button/mode_btn/theme_customize_btn are GlassButton now
        # - their icon colour (including mode_btn's glass/neon-only accent)
        # is computed live in GlassButton.paintEvent() via icon_accent
        # (see _icon_tint_hex()), so there's no QIcon to rebake here on
        # every theme change any more. mode_btn's icon_kind *shape* still
        # changes with the theme (sun/moon/drop/drop_filled), so that
        # still needs updating by hand.
        self.mode_btn.setProperty("icon_kind", self._mode_icon_kind())
        self.mode_btn.update()
        self.language_button.update()
        self.mode_btn.setToolTip({
            "light": self.tr("Switch to dark"), "dark": self.tr("Switch to neon"),
            "neon": self.tr("Switch to light"),
        }[self._look_key()])
        self.theme_customize_btn.setVisible(True)
        self.theme_customize_btn.update()
        if getattr(self, "_last_analysis_doc", None) is not None:
            self._apply_analysis_document(self._last_analysis_doc, is_retranslate=True)
        self.theme_customize_btn.setToolTip(self.tr("Settings"))
        # Sized here (not at widget-creation time) because the "muted"
        # role's real 9pt font only exists after the stylesheet above is
        # applied and polished - reading fontMetrics() any earlier sees the
        # window's pre-stylesheet default font and reserves noticeably more
        # height than the text actually needs once themed.

        if hasattr(self, "_style_pro_drop_zone"):
            self._style_pro_drop_zone()
        if hasattr(self, "tm_toggles_layout"):
            self.tm_toggles_layout.invalidate()
        if hasattr(self, "_sync_analysis_scroll_height"):
            self._sync_analysis_scroll_height()
        self._run_status_min_w = 0
        if hasattr(self, "_update_inline_progress_status"):
            self._update_inline_progress_status()
        self._bg_layer.setGeometry(self.rect())
        if hasattr(self, "_update_queue_items_selection_style"):
            self._update_queue_items_selection_style()
        QTimer.singleShot(0, self.refresh_blur)

    # ---- the one shared "look" ---------------------------------------
    def _apply_look_state(self):
        """Derives everything painting reads from self.look via pure
        build_tokens(look) without mutating global THEMES."""
        self.look["ui_style"] = "classic"
        self.tokens = build_tokens(self.look)
        self.theme_name = self.tokens["theme_name"]
        self.ui_style = "classic"
        self.neon_effect = self.tokens["neon_effect"]

    def _set_look(self, look, persist=True):
        """Makes `look` the current look and repaints everything. persist=
        False is the dialog's live preview (it saves once, on Apply)."""
        self.look = copy_look(look)
        self._apply_look_state()
        if persist:
            save_look(self.i18n.settings, self.look)
        self._sync_style_visuals()

    def _get_theme_custom(self, theme_key=None):
        """The shared look (kept under the old name for painters/mini window);
        the argument is ignored - there is one set of settings now."""
        return self.look

    THEME_CYCLE = ("light", "dark", "neon")

    def _look_key(self):
        """Which built-in preset the current look is based on."""
        return "neon" if self.look["neon_effect"] else self.look["base"]

    def _mode_icon_kind(self):
        key = self._look_key()
        return "bolt_filled" if key == "neon" else ("moon" if key == "dark" else "sun")

    def toggle_mode(self):
        """Header button: light -> dark -> neon -> light. Each of the three is a built-in
        preset that remembers whatever was applied to it in the customize window."""
        idx = self.THEME_CYCLE.index(self._look_key())
        slots = load_slots(self.i18n.settings)
        self._set_look(slots[self.THEME_CYCLE[(idx + 1) % len(self.THEME_CYCLE)]])

    def _sync_style_visuals(self):
        """Repaints everything that depends on the look."""
        self.apply_theme()
        self._bg_layer.update()
        for w in self.findChildren(Card):
            w.update()
        QTimer.singleShot(0, self.refresh_blur)
        mini = getattr(self, "_mini_window", None)
        if mini is not None:
            mini.apply_theme()

    def _open_theme_customizer(self, modal: bool = True):
        """Opens the Settings / Customize your theme dialog (live preview;
        Ctrl+, or the gear button in the header)."""
        dlg = ThemeCustomizeDialog(self)
        self._last_theme_dialog = dlg
        if modal and not getattr(self, "_non_modal_settings_for_test", False):
            return dlg.exec()
        dlg.show()
        return dlg

    def _on_settings_shortcut(self):
        is_offscreen = QApplication.platformName().lower() == "offscreen"
        return self._open_theme_customizer(modal=not is_offscreen)

    # ---- liquid glass backdrop ---------------------------------------
    def get_blurred_bg(self):
        return self._blurred_bg

    def _repaint_conversion_card(self):
        if hasattr(self, "conv_scroll_content") and self.conv_scroll_content is not None:
            if self.conv_scroll_content.layout() is not None:
                self.conv_scroll_content.layout().activate()
            self.conv_scroll_content.updateGeometry()
            self.conv_scroll_content.update()
        if hasattr(self, "conv_scroll") and self.conv_scroll is not None:
            if self.conv_scroll.viewport() is not None:
                self.conv_scroll.viewport().update()
            self.conv_scroll.update()
        if hasattr(self, "conv_card") and self.conv_card is not None:
            self.conv_card.update()
        self.update()

    def refresh_blur(self):
        self._repaint_conversion_card()
        self._blurred_bg = None
        return
        pixmap = self._bg_layer.grab()
        if pixmap.isNull() or pixmap.width() < 1 or pixmap.height() < 1:
            return
        # Blurring at full window resolution every tick (previously ~450ms)
        # was the actual cause of the stutter reported after adding the
        # ambient drift - QGraphicsBlurEffect's quality blur is expensive,
        # and its cost scales with pixel count, so a full 1080p+ window
        # every tick is real, repeated work on the UI thread.
        #
        # Standard fix: blur destroys fine detail anyway, so downscaling
        # first, blurring the *small* copy, then scaling the (already
        # blurred) result back up looks the same as blurring at full res -
        # but a quarter-linear-size copy is ~16x fewer pixels to process.
        self._blurred_bg = blurred_backdrop(pixmap)  # downscale/blur/upscale + dither (glass.py)
        for cls in (Card, GlassButton):
            for w in self.findChildren(cls):
                w.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_update_conversion_adaptive_layout"):
            self._update_conversion_adaptive_layout()
        if hasattr(self, "_style_pro_drop_zone"):
            self._style_pro_drop_zone()
        self._bg_layer.setGeometry(self.rect())
        # refresh_blur() calls QWidget.grab(), which forces an immediate
        # repaint/layout pass - doing that synchronously from inside
        # resizeEvent can re-enter Qt's own resize handling and recurse
        # (hit this exact bug prototyping the glass cards separately).
        # Deferring one tick avoids it.
        QTimer.singleShot(0, self.refresh_blur)

    # ---- frameless window chrome: edge-resize + maximize/restore -------
    # Everything the native frame used to give for free (Qt.WindowType.
    # FramelessWindowHint above turns all of it off at once): dragging
    # any edge or corner to resize, and the maximize/restore half of the
    # title bar buttons built in build() (self.titlebar handles the
    # drag-to-move and double-click-to-maximize half - see
    # _TitleBarDragArea). MiniModeWindow uses a plain QSizeGrip instead
    # of this, which is enough for a small fixed-purpose window resized
    # from one corner; this window is resized from whichever edge is
    # convenient, the same as it could be with its native frame, so it
    # gets the fuller startSystemResize() treatment instead.
    #
    # In practice this only ever fires for the left/right/bottom edges
    # and the two bottom corners: self.titlebar (see build()) covers the
    # entire top edge, is drag-only, and reliable dragging across the
    # whole row won this app's actual tradeoff against also supporting
    # resize-by-the-top-pixel (see _TitleBarDragArea's own comment for
    # why - the two turned out to conflict, not stack). _resize_edge_at
    # still computes Top/TopLeft/TopRight correctly if asked - nothing
    # currently asks it about a point up there, since titlebar's own
    # rect always catches those clicks first.
    _RESIZE_MARGIN = 6

    def _resize_edge_at(self, pos):
        """Which edge(s) `pos` (widget-local coordinates) is within
        _RESIZE_MARGIN of, as a Qt.Edge combination - a corner returns
        two flags at once (e.g. Top|Left), a plain edge returns one,
        and the interior (or a maximized window, which can't be resized
        this way) returns None."""
        if self.isMaximized():
            return None
        m = self._RESIZE_MARGIN
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        if x < -m or y < -m or x > w + m or y > h + m:
            return None  # can land here mid-drag; not a resize start
        edges = Qt.Edge(0)
        if y <= m:
            edges |= Qt.Edge.TopEdge
        elif y >= h - m:
            edges |= Qt.Edge.BottomEdge
        if x <= m:
            edges |= Qt.Edge.LeftEdge
        elif x >= w - m:
            edges |= Qt.Edge.RightEdge
        return edges or None

    _EDGE_CURSORS = {
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.BottomEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.TopEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeBDiagCursor,
        Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeBDiagCursor,
    }

    def mouseMoveEvent(self, event):
        # Only reaches here for moves over self's own bare background -
        # a move over any child widget (a Card, the splitter, titlebar)
        # is delivered to that widget instead and never reaches this
        # override, which is exactly the area (the thin margin band
        # around everything) where a resize cursor actually belongs.
        edge = self._resize_edge_at(event.position().toPoint())
        self.setCursor(self._EDGE_CURSORS.get(edge, Qt.CursorShape.ArrowCursor) if edge
                        else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._resize_edge_at(event.position().toPoint())
            if edge:
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemResize(edge)
                    return
        super().mousePressEvent(event)

    def toggle_maximize_restore(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        if hasattr(self, "maximize_btn"):
            self.maximize_btn.setText("\u2752" if self.isMaximized() else "\u25a1")
            self.maximize_btn.setToolTip(
                self.tr("Restore") if self.isMaximized() else self.tr("Maximize"))

    # ---- build -------------------------------------------------------
    def build(self):
        # Sits behind every other child, repainted with a flat fill in
        # this theme's BG colour (see _BgLayer) - Card and every other
        # Glass* widget crops its own region out of a blurred snapshot
        # of *this* widget, not of self, so the snapshot never
        # recursively contains any card/control that's already glass.
        self._bg_layer = _BgLayer(self)
        self._bg_layer.lower()
        self._blurred_bg = QPixmap()

        root = QVBoxLayout(self)
        # Only side/bottom margins on root - the top margin lives on the
        # titlebar widget's own layout instead (see the comment right
        # before self.titlebar below for why: a bare margin on root, with
        # no widget covering it, is dead space nothing can drag or
        # resize from - this app's own MiniModeWindow had exactly that
        # bug the first time it went frameless).
        root.setContentsMargins(16, 0, 16, 12)
        # Titlebar gets HEADER_GLOW_ROOM of empty room *below* its buttons
        # (header margins below) so a GlassButton's glow/shadow isn't cut
        # off in a hard straight line at the titlebar's bottom edge - a
        # child's graphics effect can't paint outside its parent widget.
        # That room replaces the old 6px root spacing (now 0) and is 6px
        # taller than it, so the content starts 6px lower than before.
        # (QBoxLayout treats a negative spacing as "style default", so the
        # strip can't be made to overlap the content to keep it at 0.)
        HEADER_GLOW_ROOM = 12
        root.setSpacing(0)

        # header / titlebar: this window is frameless (see self._frameless
        # a few lines up in __init__) - no native title bar, so this row
        # is it. _TitleBarDragArea makes the row itself draggable
        # (windowHandle().startSystemMove()) and double-click-to-maximize
        # (toggle_maximize_restore() below); minimize/maximize/close
        # replace the native buttons that disappeared along with the
        # rest of the native frame. The whole row is drag-only (see
        # _TitleBarDragArea's own comment for why resize-by-the-top-edge
        # specifically isn't worth carving out of it) - root's top
        # margin is 0, so the titlebar covers the whole top edge with
        # nothing left outside it to turn into a dead zone.
        self.titlebar = _TitleBarDragArea()
        header = QHBoxLayout(self.titlebar)
        header.setContentsMargins(0, 16, 0, HEADER_GLOW_ROOM)
        header.setSpacing(8)

        app_icon = load_app_icon()
        if not app_icon.isNull():
            icon_label = QLabel()
            icon_label.setPixmap(app_icon.pixmap(32, 32))
            header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        htitle = QVBoxLayout()
        title = QLabel("HDR → SDR")
        set_role(title, "title")
        htitle.addWidget(title)
        header.addLayout(htitle)
        header.addStretch(1)

        self.kofi_btn = GlassButton("\u2665 Support on Ko-fi")
        set_role(self.kofi_btn, "kofi")
        self.kofi_btn.setToolTip(KOFI_URL)
        self.kofi_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KOFI_URL)))
        header.addWidget(self.kofi_btn, 0, Qt.AlignmentFlag.AlignTop)

        # Theme-customize swatch - visible in neon mode (accent gradient)
        # or light-glass mode (accent colour/effect/glow intensity), see
        # apply_theme()'s is_neon/is_glass visibility toggle. Sits right
        # after Ko-fi and before the mode toggle it modifies (see
        # ThemeCustomizeDialog / _open_theme_customizer).
        # These three are GlassButton, not a QToolButton subclass - same
        # paint routine (square, radius=8.0) as every other button in the
        # app (Add videos, Remove, Output, ...), rather than a QSS-driven
        # background of their own. QSS border-radius at this small a size
        # (38x34) turned out to still read as visibly stair-stepped
        # corners on real Windows, even after wrapping it in an
        # antialiased mask - the QSS fill itself is what's aliased, not
        # just its edge, so cropping it cleanly doesn't retroactively
        # smooth it. GlassButton's glass-mode background is hand-painted
        # with no QSS involved at all, which is the actual reason the
        # rest of the app's buttons render cleanly - see RoundedMenuButton
        # (widgets/combo.py) for the fuller version of this note.
        self.mode_btn = GlassButton("")
        self.mode_btn.setToolTip(self.tr("Appearance"))
        self.mode_btn.setFixedSize(38, 34)
        self.mode_btn.setIconSize(QSize(18, 18))
        self.mode_btn.setProperty("icon_kind", self._mode_icon_kind())
        # "auto": accented only in glass/neon, same condition
        # apply_theme() used to compute by hand for this button's old
        # colour.
        self.mode_btn.setProperty("icon_accent", "auto")
        set_role(self.mode_btn, "theme")
        self.mode_btn.clicked.connect(self.toggle_mode)
        header.addWidget(self.mode_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.theme_customize_btn = GlassButton("")
        self.theme_customize_btn.setFixedSize(38, 34)
        self.theme_customize_btn.setIconSize(QSize(18, 18))
        self.theme_customize_btn.setProperty("icon_kind", "gear")
        # "always": this is a colour swatch, it should read as one in
        # every theme mode, not just glass/neon - see
        # GlassButton._icon_tint_hex()'s docstring.
        self.theme_customize_btn.setProperty("icon_accent", "always")
        set_role(self.theme_customize_btn, "theme")
        self.theme_customize_btn.clicked.connect(lambda: self._open_theme_customizer(modal=True))
        self.theme_customize_btn.setVisible(False)
        header.addWidget(self.theme_customize_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        self.settings_shortcut.activated.connect(self._on_settings_shortcut)

        # RoundedMenuButton (opens the same RoundedComboPopup the encoder/
        # backend pickers use) instead of a native QToolButton+QMenu, so
        # the language list matches the rest of the app's dropdowns.
        self.language_button = RoundedMenuButton()
        self.language_button.setToolTip(self.tr("Interface language"))
        self._language_codes = []
        self.language_button.activatedIndex.connect(self._on_language_picked)
        self.rebuild_language_menu()
        self.language_button.setFixedSize(38, 34)
        self.language_button.setIconSize(QSize(18, 18))
        self.language_button.setProperty("icon_kind", "globe")
        set_role(self.language_button, "theme")
        # The language picker lives in the Customize window now (the gear button); this
        # object is kept only so the language code/menu helpers keep working.
        self.language_button.setParent(self)
        self.language_button.hide()

        # Window controls, replacing the native minimize/maximize/close
        # that disappeared along with the native frame.
        self.minimize_btn = TitleBarButton("\u2013")
        self.minimize_btn.setToolTip(self.tr("Minimize"))
        self.minimize_btn.clicked.connect(self.showMinimized)
        header.addWidget(self.minimize_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.maximize_btn = TitleBarButton("\u25a1")
        self.maximize_btn.setToolTip(self.tr("Maximize"))
        self.maximize_btn.clicked.connect(self.toggle_maximize_restore)
        header.addWidget(self.maximize_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.close_btn = TitleBarButton("\u00d7", danger=True)
        self.close_btn.setToolTip(self.tr("Close"))
        self.close_btn.clicked.connect(self.close)
        header.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        # Same height as the 34px header buttons next to them (they are all
        # top-aligned), so the three window controls sit on the same centre
        # line instead of 4px higher.
        for b_ in (self.minimize_btn, self.maximize_btn, self.close_btn):
            b_.setFixedSize(34, 34)

        root.addWidget(self.titlebar)

        # Everything below the titlebar lives in its own sub-layout so the
        # 6px gap *between the splitter and the Activity card* is owned by
        # that layout (BODY_GAP) and not by `root`'s spacing - root's
        # spacing is 0 now (the titlebar's own bottom margin plays that
        # role, see HEADER_GLOW_ROOM). Hidden items add no spacing, so with
        # the Activity card collapsed nothing extra shows up.
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        root.addLayout(body, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(10)
        body.addWidget(self.splitter, 1)

        self.left_panel = left_panel = QWidget()
        self.right_panel = right_panel = QWidget()
        left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        right_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        left_panel.setObjectName("glassPanel")
        right_panel.setObjectName("glassPanel")
        left = QVBoxLayout(left_panel)
        right = QVBoxLayout(right_panel)
        left.setContentsMargins(0, 0, 0, 0)
        right.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        right.setSpacing(8)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)

        # ---- FILES ----
        self.files_card = files = Card("Files")
        files.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.src_edit = QLineEdit()
        self.dst_edit = QLineEdit()

        # Empty-queue compact drop zone: exact same fixed 96px height as queue_list
        # so the Files card and main window never change height on add/remove.
        self.files_empty_drop_widget = QFrame()
        self.files_empty_drop_widget.setObjectName("filesEmptyDropWidget")
        self.files_empty_drop_widget.setStyleSheet("QFrame#filesEmptyDropWidget { background: transparent; }")
        self.files_empty_drop_widget.setFixedHeight(96)
        self.files_empty_drop_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.files_empty_drop_widget.setAcceptDrops(False)
        self.files_empty_drop_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.files_empty_drop_widget.installEventFilter(self)
        empty_drop_layout = QHBoxLayout(self.files_empty_drop_widget)
        empty_drop_layout.setContentsMargins(12, 6, 12, 6)
        empty_drop_layout.setSpacing(10)
        empty_drop_layout.addStretch(1)
        self.files_drop_icon = QLabel()
        self.files_drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.files_drop_icon.setFixedSize(28, 28)
        self.files_drop_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_drop_layout.addWidget(self.files_drop_icon)
        self.files_drop_label = QLabel("Drop a video here")
        self.files_drop_label.setObjectName("filesDropLabel")
        self.files_drop_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.files_drop_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_drop_layout.addWidget(self.files_drop_label)
        self.files_browse_btn = GlassButton("Browse File")
        self.files_browse_btn.setMinimumWidth(116)
        self.files_browse_btn.setFixedHeight(30)
        self.files_browse_btn.clicked.connect(self.browse_source)
        empty_drop_layout.addWidget(self.files_browse_btn)
        empty_drop_layout.addStretch(1)
        files.body.addWidget(self.files_empty_drop_widget)

        self.queue_list = QListWidget()
        self.queue_list.setFixedHeight(96)
        self.queue_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.queue_list.setAcceptDrops(False)
        self.queue_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.queue_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self._show_queue_context_menu)
        self.queue_list.setItemDelegate(_QueueItemDelegate(self.queue_list))
        self.queue_list.setToolTip(self.tr("Videos waiting to be converted with the current settings."))
        self.queue_list.installEventFilter(self)
        files.body.addWidget(self.queue_list)

        # "Drop video files here..." used to be its own QLabel sitting
        # above the queue list, permanently costing a row of vertical
        # space whether the queue was empty or full. It only actually
        # means something while the queue *is* empty, so instead it's a
        # transparent overlay painted directly on top of the (then-empty)
        # list viewport - free real estate that costs nothing once real
        # queue rows exist. WA_TransparentForMouseEvents lets clicks/drags
        # pass straight through to the list/window underneath instead of
        # being swallowed by the label.
        self.queue_hint_label = QLabel("Drop video files here, or add them to the conversion queue.", self.queue_list.viewport())
        self.queue_hint_label.setWordWrap(True)
        self.queue_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(self.queue_hint_label, "muted")
        self.queue_hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.queue_list.viewport().installEventFilter(self)
        # QListWidget's model emits these on every addItem()/takeItem(),
        # covering every current and future call site in one place rather
        # than needing an explicit _update_queue_hint() call added at each
        # of them.
        self.queue_list.model().rowsInserted.connect(self._on_queue_rows_inserted)
        self.queue_list.model().rowsInserted.connect(self._update_queue_hint)
        self.queue_list.model().rowsRemoved.connect(self._update_queue_hint)
        self.queue_list.currentRowChanged.connect(self._on_queue_row_selected)
        self.queue_list.itemSelectionChanged.connect(self._update_queue_items_selection_style)
        self._update_queue_hint()

        # The bottom row of buttons ("Add videos", "Remove", "Output folder") has
        # been removed from the Files card:
        # - Adding files is handled via "Browse File" in the empty drop zone, drag & drop,
        #   and the right-click context menu.
        # - Removing files is handled via the compact "×" button on each row, the right-click
        #   context menu, and Delete/Backspace keys.
        # - Output folder selection is relocated to the "Output" section as a compact button.
        self.queue_add_btn = self.files_browse_btn
        self.queue_remove_btn = None
        left.addWidget(files)

        # ---- SOURCE ANALYSIS ----
        self.analysis_card = analysis = Card("Source analysis")
        # Same reasoning as FILES above.
        analysis.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.analysis_label = QLabel("Select a video to analyze it automatically.")
        self.analysis_label.setWordWrap(True)
        # Left+Top, not the QLabel default of Left+VCenter - vertical
        # centering was making any gap between the actual text height and a
        # reserved minimum height look like blank padding above *and* below.
        self.analysis_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.analysis_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.analysis_label.setOpenExternalLinks(False)
        self.analysis_label.linkActivated.connect(self._on_preview_action_clicked)
        set_role(self.analysis_label, "muted")
        # Pinned to a fixed height via a QScrollArea rather than left to
        # size itself off the label's own sizeHint(). analyze() swaps this
        # label's text between a 1-line placeholder and a variable-length
        # HTML block (headline + source line + 3 recommendation lines +
        # an optional primaries warning, 5-7 lines depending on the
        # source) - letting that reflow the card's height used to change
        # self.minimumSizeHint() for the whole window on every Analyze
        # click. Because QSplitter always forces the left and right
        # columns to the same height, that change didn't just resize
        # SOURCE ANALYSIS - it changed how much filler space landed in
        # CONVERSION's own addStretch(1) too (see the comment there),
        # which is what made that empty area grow/shrink on every click.
        # Fixing the height here removes that variable at the source: the
        # left column's total height is now constant regardless of what
        # Analyze produces, and the rare case that doesn't fit the
        # reserved height scrolls internally instead of resizing anything.
        self.analysis_scroll = QScrollArea()
        self.analysis_scroll.setObjectName("analysisScroll")
        self.analysis_scroll.setWidgetResizable(True)
        # Height comes from real font metrics for the 9pt "muted" role QSS
        # sets on this label (see `QLabel[role="muted"]` below), not a
        # guessed constant. analyze() fills this label with up to ~10
        # lines in the common HDR case - headline, "Source file", the
        # source-details line, "HDR metadata" plus 1-3 detail lines
        # (mastering range/MaxCLL-MaxFALL/DOVI profile - only shown for
        # HDR/Dolby sources), "Recommended settings", and the three
        # preset lines - so that's what's reserved here. The rare extra
        # line (a primaries mismatch warning) or an unusually long
        # source-details line that wraps isn't worth reserving space for
        # on every single analysis; it just scrolls instead, via the
        # QScrollArea already wrapping this label.
        self.analysis_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.analysis_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.analysis_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.analysis_scroll.setStyleSheet("QScrollArea#analysisScroll { background: transparent; border: none; }")
        self.analysis_scroll.viewport().setObjectName("analysisScrollViewport")
        self.analysis_scroll.viewport().setStyleSheet("QWidget#analysisScrollViewport { background: transparent; }")
        self.analysis_scroll.setWidget(self.analysis_label)
        analysis.body.addWidget(self.analysis_scroll)

        orig_analysis_set_text = self.analysis_label.setText

        def _analysis_set_text_and_sync(text):
            orig_analysis_set_text(text)
            self._sync_analysis_scroll_height()

        self.analysis_label.setText = _analysis_set_text_and_sync



        self.analyze_btn = GlassButton("Analyze selected video")
        set_role(self.analyze_btn, "panel-toggle")
        self.analyze_btn.setToolTip(self.tr("Analyze the selected queue item before converting it."))
        self.analyze_btn.clicked.connect(self.analyze_selected_queue_video)
        analysis.body.addWidget(self.analyze_btn)
        # Analysis is automatic when a queued source is selected. Keep the
        # compatibility action object for older callers, but remove it from
        # the visible card.
        analysis.body.removeWidget(self.analyze_btn)
        self.analyze_btn.hide()

        # One-click "just do it" versions of the three lines above: apply
        # that tier's tone-mapping curve/encoder/quality settings without
        # the person having to translate the recommendation into dropdown
        # picks themselves. Disabled until a source has actually been
        # analyzed (self.kind starts "unknown").
        self._applying_preset = False
        self._last_quality_default = DEFAULT_QUALITY_RANGE["default"]
        class _PresetSegmentFrame(QFrame):
            def minimumSizeHint(self_frame) -> QSize:
                return QSize(180, max(26, super().minimumSizeHint().height()))

        self.preset_segment_frame = _PresetSegmentFrame()
        set_role(self.preset_segment_frame, "preset-segment")
        preset_row = QHBoxLayout(self.preset_segment_frame)
        preset_row.setContentsMargins(2, 2, 2, 2)
        preset_row.setSpacing(4)
        self.preset_optimal_btn = GlassButton("Optimal")
        self.preset_best_btn = GlassButton("Best quality")
        self.preset_fast_btn = GlassButton("Fast")
        for b, fn, tip in (
            (self.preset_optimal_btn, self._apply_optimal_preset,
             "Apply the Optimal tone-mapping curve for this source (matches the app defaults in most cases)."),
            (self.preset_best_btn, self._apply_best_quality_preset,
             "Apply the Best quality settings: CPU \u00b7 H.265, low CRF, best available curve. Slower."),
            (self.preset_fast_btn, self._apply_fast_preset,
             "Apply the Fast settings: hardware encoder if this machine has one (else CPU \u00b7 H.264), simple curve."),
        ):
            b.setCheckable(True)
            set_role(b, "preset-segment-btn")
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setMinimumWidth(0)
            b.setEnabled(False)
            b.setToolTip(self.tr(tip))
            b.clicked.connect(fn)
            preset_row.addWidget(b, 1)
        self.preset_hint_label = QLabel("")
        self.preset_hint_label.setWordWrap(True)
        set_role(self.preset_hint_label, "muted")
        self.preset_hint_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.preset_hint_label.hide()
        left.addWidget(analysis)


        # ---- LIVE PREVIEW ----
        # Two sources feed the same label at different times: an instant
        # single-frame render of the current tone-map/brightness/resolution
        # settings while idle (see _schedule_frame_preview below), and once
        # a real conversion is running, a real frame from the actual
        # tone-mapped/encoded output (ffmpeg splits the stream into the
        # encode + preview branches - see command_ffmpeg) takes over.
        preview = Card("Live preview")
        self.preview_card = preview
        preview.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        preview.layout().setContentsMargins(16, 8, 16, 14)
        preview.layout().setSpacing(3)
        preview.body.setSpacing(2)
        self.preview_label = QLabel("Choose a source video to see an instant preview of the current settings.")
        self.preview_label.setWordWrap(True)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setContentsMargins(0, 0, 0, 0)
        self.preview_label.setMinimumHeight(200)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        set_role(self.preview_label, "muted")
        self.preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.preview_label.setOpenExternalLinks(False)
        self.preview_label.linkActivated.connect(self._on_preview_action_clicked)
        self.preview_label.installEventFilter(self)
        self.preview_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.preview_label.customContextMenuRequested.connect(self._show_preview_context_menu)
        preview.body.addWidget(self.preview_label, 1)

        self.preview_meta_label = QLabel("")
        self.preview_meta_label.setWordWrap(True)
        self.preview_meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_meta_label.setContentsMargins(0, 0, 0, 0)
        set_role(self.preview_meta_label, "muted")
        meta_line_h = QFontMetrics(self.preview_meta_label.font()).lineSpacing() + 2
        self.preview_meta_label.setMinimumHeight(meta_line_h)
        self.preview_meta_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        preview.body.addWidget(self.preview_meta_label, 0)

        # Live preview scrub controls: position label and single scrub slider only.
        self.preview_controls_widget = QWidget()
        self.preview_controls_widget.setObjectName("previewControlsWidget")
        self.preview_controls_widget.setStyleSheet("QWidget#previewControlsWidget { background: transparent; }")
        self.preview_controls_widget.setFixedHeight(32)
        self.preview_controls_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        timeline_row = QHBoxLayout(self.preview_controls_widget)
        timeline_row.setContentsMargins(0, 0, 0, 4)
        timeline_row.setSpacing(8)

        self.preview_position_label = QLabel("0:00")
        set_role(self.preview_position_label, "muted")
        timeline_row.addWidget(self.preview_position_label)

        self.preview_slider = TimelineSlider(formatter=clock_time)
        set_role(self.preview_slider, "compact-slider")
        self.preview_slider.setFixedHeight(28)
        self.preview_slider.setRange(0, 0)
        self.preview_slider.setEnabled(False)
        self.preview_slider.setToolTip(self.tr("Scrub frame — drag to preview any position in the video."))
        self.preview_slider.valueChanged.connect(self._on_preview_slider_changed)
        timeline_row.addWidget(self.preview_slider, 1)

        self.preview_timeline = self.preview_slider

        # Thumbnails above the timeline (filled in the background after analysis; hidden until the first arrives)
        self.frame_strip = FrameStrip()
        self.frame_strip.hide()
        self.frame_strip_loader = FrameStripLoader(self)
        self.frame_strip_loader.frameReady.connect(self._on_frame_strip_frame)
        self.frame_strip.seekRequested.connect(self._seek_preview_fraction)
        self.preview_slider.valueChanged.connect(self._update_frame_strip_position)
        preview.body.addWidget(self.frame_strip, 0)
        preview.body.addWidget(self.preview_controls_widget, 0)
        self.preview_controls_widget.hide()

        left.addWidget(preview, 1)

        # ---- PROGRESS ----
        self.progress_card = progress = Card("Progress")
        progress.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 1000)  # 0.1% resolution
        self.pbar.setTextVisible(False)
        self.pbar.setFormat("%p%")
        self.pbar.setFixedHeight(PROGRESS_BAR_HEIGHT)
        self.pbar.setMinimumWidth(160)
        self._pbar_shimmer = ProgressShimmer(self.pbar)
        self._pbar_shadow = QGraphicsDropShadowEffect(self.pbar)
        self._pbar_shadow.setBlurRadius(20)
        self._pbar_shadow.setOffset(0, 0)
        self._pbar_shadow.setEnabled(False)
        self.pbar.setGraphicsEffect(self._pbar_shadow)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(False)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        set_role(self.status_label, "muted")
        self.status_label.setFixedHeight(
            QFontMetrics(QFont(self.FONT, 9, QFont.Weight.DemiBold)).lineSpacing() + 4)

        self._base_status_text = ""
        self._raw_speed_stats_text = ""
        self._latest_progress_stats = None
        self._latest_resource_text = ""

        # Off-layout proxy QLabel kept for backward compatibility with callers/tests
        # that inspect or set self.speed_label; NOT added to progress.body (P0-4).
        self.speed_label = QLabel("")
        self.speed_label.hide()
        orig_speed_set_text = self.speed_label.setText

        def _speed_proxy_set_text(text):
            orig_speed_set_text(text)
            self._raw_speed_stats_text = str(text or "")
            self._latest_progress_stats = None
            self._update_inline_progress_status()

        self.speed_label.setText = _speed_proxy_set_text

        self.resource_label = QLabel(
            "" if HAVE_PSUTIL else
            "psutil not installed: Pause/Resume, live CPU-core control, and CPU/GPU stats are "
            "unavailable. Install with: pip install psutil (then restart this app).")
        set_role(self.resource_label, "muted")
        if HAVE_PSUTIL:
            self.resource_label.setWordWrap(False)
            self.resource_label.hide()
        else:
            self.resource_label.setWordWrap(True)
        if not self.resource_label.text():
            self.resource_label.hide()

        self.pbar_row_widget = QWidget()
        self.pbar_row_widget.setObjectName("pbarRowWidget")
        self.pbar_row_widget.setStyleSheet("QWidget#pbarRowWidget { background: transparent; }")
        self.pbar_row = pbar_row = QHBoxLayout(self.pbar_row_widget)
        pbar_row.setContentsMargins(0, 0, 0, 0)
        pbar_row.setSpacing(8)
        pbar_row.addWidget(self.pbar, 1)
        pbar_row.addWidget(self.status_label, 0)
        progress.body.addWidget(self.pbar_row_widget)
        progress.body.addWidget(self.resource_label)
        left.addWidget(progress)

        # ---- CONTROLS ----
        self.controls_card = controls = Card("Controls")

        self.run_stack = QStackedWidget()
        self.run_stack.setObjectName("runStack")
        self.run_stack.setStyleSheet("QStackedWidget#runStack { background: transparent; }")
        self.run_idle_page = QWidget()
        self.run_idle_page.setObjectName("runIdlePage")
        self.run_idle_page.setStyleSheet("QWidget#runIdlePage { background: transparent; }")
        idle_layout = QHBoxLayout(self.run_idle_page)
        idle_layout.setContentsMargins(0, 0, 0, 0)
        idle_layout.setSpacing(8)
        self.go_btn = GlassButton("Start")
        set_role(self.go_btn, "go")
        self.go_btn.setFixedHeight(36)
        self.go_btn.clicked.connect(self._on_start_clicked)
        idle_layout.addWidget(self.go_btn, 1)

        self.run_active_page = QWidget()
        self.run_active_page.setObjectName("runActivePage")
        self.run_active_page.setStyleSheet("QWidget#runActivePage { background: transparent; }")
        run_row = QHBoxLayout(self.run_active_page)
        run_row.setContentsMargins(0, 0, 0, 0)
        run_row.setSpacing(8)
        self.pause_btn = GlassButton("Pause")
        self.pause_btn.setFixedHeight(36)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = GlassButton("Stop")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.clicked.connect(self.stop)
        run_row.addWidget(self.pause_btn, 1)
        run_row.addWidget(self.stop_btn, 1)

        self.run_stack.addWidget(self.run_idle_page)
        self.run_stack.addWidget(self.run_active_page)
        self.run_stack.setFixedHeight(36)
        self.run_stack.setCurrentIndex(0)
        controls.body.addWidget(self.run_stack)

        self._live_apply_timer = QTimer(self)
        self._live_apply_timer.setSingleShot(True)
        self._live_apply_timer.setInterval(200)
        self._live_apply_timer.timeout.connect(lambda: self.apply_live_settings(quiet=True))

        self._frame_preview_timer = QTimer(self)
        self._frame_preview_timer.setSingleShot(True)
        self._frame_preview_timer.setInterval(400)
        self._frame_preview_timer.timeout.connect(self._run_frame_preview)

        ParamSlider.set_global_style(str(self.i18n.settings.value("control_style", DEFAULT_CONTROL_STYLE) or DEFAULT_CONTROL_STYLE))

        # P1-1: CPU cores & priority placed inside CONTROLS card under Start/Pause-Stop
        self.cores_panel_widget = QWidget()
        self.cores_panel_widget.setObjectName("coresPanelWidget")
        self.cores_panel_widget.setStyleSheet("QWidget#coresPanelWidget { background: transparent; }")
        cores_panel_layout = QVBoxLayout(self.cores_panel_widget)
        cores_panel_layout.setContentsMargins(STEPPER_GLOW_ROOM, 2, 0, 0)
        cores_panel_layout.setSpacing(2)
        cores_hdr_row = QHBoxLayout()
        cores_hdr_row.setContentsMargins(0, 0, 0, 0)
        cores_hdr_row.setSpacing(6)
        self.cores_title_lbl = QLabel(self.tr("CPU cores & priority"))
        self.cores_title_lbl.setProperty("i18n_text", "CPU cores & priority")
        self.cores_title_lbl.setWordWrap(False)
        set_role(self.cores_title_lbl, "field-label")
        self.cores_note_lbl = QLabel("")
        self.cores_note_lbl.hide()
        cores_tip = self.tr("Limits CPU core count and process priority. Applies at launch and adjusts live while encoding.")
        self.cores_title_lbl.setToolTip(cores_tip)
        self.cores_panel_widget.setToolTip(cores_tip)
        cores_hdr_row.addWidget(self.cores_title_lbl)
        cores_hdr_row.addStretch(1)
        cores_hdr_row.addWidget(self.cores_note_lbl)
        cores_panel_layout.addLayout(cores_hdr_row)

        # Two compact notched ParamSliders side-by-side: Cores and Priority (no dropdown needed).
        cf = QHBoxLayout()
        cf.setContentsMargins(0, 0, 0, 0)
        cf.setSpacing(8)
        self.cores_slider = ParamSlider(
            self.tr("Cores"), 1, CPU_COUNT, CPU_COUNT, default=CPU_COUNT, ticks=True)
        self.cores_spin = self.cores_stepper = self.cores_slider
        self.cores_slider.setMinimumWidth(140)
        self.cores_slider.setToolTip(cores_tip)
        self.cores_spin.valueChanged.connect(self._schedule_live_apply)

        self.priority_slider = ParamSlider(
            self.tr("Priority"), 0, len(PRIORITY_LEVELS) - 1, 1, default=1,
            choices=PRIORITY_LEVELS, display_choices=("Low", "Balanced", "High"),
            ticks=True, segmented=True)
        self.priority_combo = self.priority_slider
        self.priority_slider.setMinimumWidth(128)
        self.priority_slider.setMaximumWidth(184)
        self.priority_slider.setToolTip(cores_tip)
        self.priority_combo.currentIndexChanged.connect(self._schedule_live_apply)

        # Golden-ratio ~62.5% : 37.5% (5 : 3) split so Cores gets a long track for 16-32+ core CPUs
        # while the 3-step Priority control stays compact and proportional.
        cf.addWidget(self.cores_slider, 5)
        cf.addWidget(self.priority_slider, 3)
        self.power_more_btn = GlassButton("")
        self.power_more_btn.setFixedWidth(30)
        self.power_more_btn.setFixedHeight(26)
        self.power_more_btn.setIconSize(QSize(15, 15))
        self.power_more_btn.setToolTip(self.tr("More options"))
        self.power_more_btn.setProperty("icon_kind", "dots")
        self.power_more_btn.setProperty("icon_accent", "auto")
        set_role(self.power_more_btn, "theme")
        self.power_more_btn.clicked.connect(self._open_power_dialog)
        cf.addWidget(self.power_more_btn)
        cores_panel_layout.addLayout(cf)
        controls.body.addLayout(LeftRoomLayout(self.cores_panel_widget))

        self._build_power_dialog()
        self.keep_awake_chk.toggled.connect(self._on_power_pref_changed)
        self.keep_display_chk.toggled.connect(self._on_power_pref_changed)
        controls.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        left.addWidget(controls)

        # ---- CONVERSION ----
        def _section_header(text):
            lbl = QLabel(text.upper())
            lbl.setProperty("i18n_text", text)
            f = lbl.font()
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
            lbl.setFont(f)
            set_role(lbl, "section-title")
            return lbl

        def _field_label(text):
            lbl = QLabel(text)
            set_role(lbl, "field-label")
            return lbl

        self.conv_card = conv = Card("Conversion")
        self.conv_scroll = QScrollArea()
        self.conv_scroll.setObjectName("convScroll")
        self.conv_scroll.setWidgetResizable(True)
        self.conv_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.conv_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Always reserve the 8px vertical scrollbar lane so opening/closing Advanced filters
        # never changes conv_scroll.viewport().width() or shifts Conversion controls horizontally.
        self.conv_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        _conv_vbar = self.conv_scroll.verticalScrollBar()
        _conv_vbar.setProperty("inactive", True)
        def _sync_conv_vbar_active(_min: int = 0, max_val: int = 0) -> None:
            inactive = max_val <= 0
            if _conv_vbar.property("inactive") != inactive:
                _conv_vbar.setProperty("inactive", inactive)
                _conv_vbar.style().unpolish(_conv_vbar)
                _conv_vbar.style().polish(_conv_vbar)
                _conv_vbar.update()
        _conv_vbar.rangeChanged.connect(_sync_conv_vbar_active)
        self.conv_scroll.setMinimumHeight(320)
        self.conv_scroll.setStyleSheet("QScrollArea#convScroll { background: transparent; border: none; }")
        self.conv_scroll.viewport().setObjectName("convScrollViewport")
        self.conv_scroll.viewport().setStyleSheet("QWidget#convScrollViewport { background: transparent; }")
        class _ConvScrollContent(QWidget):
            def minimumSizeHint(self_inner):
                sh = super().minimumSizeHint()
                return QSize(min(340, sh.width()), sh.height())

        self.conv_scroll_content = _ConvScrollContent()
        self.conv_scroll_content.setObjectName("convScrollContent")
        self.conv_scroll_content.setStyleSheet("QWidget#convScrollContent { background: transparent; }")
        conv_inner = QVBoxLayout(self.conv_scroll_content)
        conv_inner.setContentsMargins(0, 2, 4, 10)
        conv_inner.setSpacing(8)
        self.conv_scroll.setWidget(self.conv_scroll_content)
        conv.body.addWidget(self.conv_scroll, 1)

        self._section_headers = {}
        self._section_bodies = {}
        self._section_expanded = {}

        def _make_collapsible_section(sec_key, title_src, default_expanded=True):
            hdr = _section_header(title_src)
            hdr.setCursor(Qt.CursorShape.PointingHandCursor)
            saved_raw = self.i18n.settings.value(f"section_expanded_{sec_key}", None)
            if saved_raw is None:
                expanded = bool(default_expanded)
            else:
                expanded = str(saved_raw).strip().lower() in ("1", "true", "yes", "on")
            body_w = QWidget()
            body_obj_name = f"sectionBody_{sec_key}"
            body_w.setObjectName(body_obj_name)
            body_w.setStyleSheet(f"QWidget#{body_obj_name} {{ background: transparent; }}")
            body_l = QVBoxLayout(body_w)
            body_l.setContentsMargins(STEPPER_GLOW_ROOM, 2, 2, 8)
            body_l.setSpacing(8)
            body_w.setVisible(expanded)
            self._section_headers[sec_key] = (hdr, title_src)
            self._section_bodies[sec_key] = body_w
            self._section_expanded[sec_key] = expanded

            arrow = "\u25be " if expanded else "\u25b8 "
            hdr.setProperty("i18n_text", None)
            hdr.setText(arrow + self.tr(title_src).upper())

            def _on_hdr_press(event):
                if event.button() == Qt.MouseButton.LeftButton:
                    self.toggle_conversion_section(sec_key)
                    event.accept()

            hdr.mousePressEvent = _on_hdr_press
            conv_inner.addWidget(hdr)
            conv_inner.addWidget(body_w)
            return body_l

        # ---- Presets bar at the very top of Conversion zone (above Encoding) ----
        conv_inner.addWidget(self.preset_segment_frame)
        conv_inner.addWidget(self.preset_hint_label)

        # ---- ENCODING group: backend/encoder/bit depth/decode/quality ----
        enc_body = _make_collapsible_section("encoding", "Encoding", True)
        enc_body.setSpacing(6)
        backend_row = QHBoxLayout()
        backend_row.setContentsMargins(0, 0, 0, 0)
        backend_row.setSpacing(8)
        backend_lbl = _field_label("Backend")
        backend_lbl.setMinimumWidth(0)
        self.backend_combo = RoundedComboBox()
        self.backend_combo.addItems(["FFmpeg", "HandBrake"])
        loosen_combo(self.backend_combo, 10)
        backend_row.addWidget(backend_lbl)
        backend_row.addWidget(self.backend_combo, 1)
        enc_body.addLayout(backend_row)

        self.ffmpeg_version_note = QLabel("")
        self.ffmpeg_version_note.setWordWrap(True)
        set_role(self.ffmpeg_version_note, "warn")
        self.ffmpeg_version_note.hide()
        enc_body.addWidget(self.ffmpeg_version_note)

        self.enc_grid = enc_grid = QGridLayout()
        enc_grid.setContentsMargins(0, 0, 0, 0)
        enc_grid.setHorizontalSpacing(10)
        enc_grid.setVerticalSpacing(4)
        enc_grid.setColumnStretch(0, 1)
        enc_grid.setColumnStretch(1, 1)
        self.enc_lbl = enc_lbl = _field_label("Encoder")
        enc_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        enc_grid.addWidget(enc_lbl, 0, 0)
        self.encoder_combo = RoundedComboBox()
        self.encoder_combo.addItems(list(ENCODER_MAP.keys()))
        loosen_combo(self.encoder_combo, 8)
        enc_grid.addWidget(self.encoder_combo, 1, 0)

        self.bd_lbl = bd_lbl = _field_label("Bit depth")
        bd_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        enc_grid.addWidget(bd_lbl, 0, 1)
        self.bit_depth_combo = RoundedComboBox()
        self.bit_depth_combo.addItems(BIT_DEPTH_LABELS)
        loosen_combo(self.bit_depth_combo, 8)
        self.bit_depth_combo.setCurrentText("8-bit (SDR standard)")
        self.bit_depth_combo.currentTextChanged.connect(self._on_bit_depth_changed)
        enc_grid.addWidget(self.bit_depth_combo, 1, 1)
        enc_body.addLayout(enc_grid)
        # Which encoder ids exist - and which of those this machine's
        # FFmpeg/HandBrakeCLI actually has - differs by backend, so a
        # backend switch needs the same re-filter a bit-depth change does.
        self.backend_combo.currentTextChanged.connect(
            lambda _text: self._on_bit_depth_changed(self.bit_depth_combo.currentText()))
        self._on_bit_depth_changed(self.bit_depth_combo.currentText())
        # _on_bit_depth_changed() repopulates encoder_combo with
        # blockSignals(True) (see there), so a switch into/out of CPU AV1
        # triggered purely by a bit-depth change wouldn't otherwise reach
        # currentTextChanged - hook it directly instead of relying on that
        # signal for this one thing.
        self.encoder_combo.currentTextChanged.connect(lambda _t: self._update_quality_range())

        # Hardware-accelerated decode - on by default. It only speeds up
        # reading the source (see _update_hwaccel_note below for exactly
        # what it does per backend/encoder); it never changes output
        # quality, so there's no real reason to make people opt in.
        self.hwaccel_chk = ToggleSwitch("Hardware-accelerated decode")
        self.hwaccel_chk.setChecked(True)

        self.q_box = q_box = QVBoxLayout()
        q_box.setContentsMargins(0, 0, 0, 0)
        q_box.setSpacing(4)
        self.q_head_row = q_head_row = QHBoxLayout()
        q_head_row.setContentsMargins(0, 0, 0, 0)
        q_head_row.setSpacing(4)
        self.quality_head_label = quality_label = _field_label(self.tr("Quality"))
        quality_label.setProperty("i18n_text", "Quality")
        quality_label.setToolTip(self.tr(QUALITY_SLIDER_INFO))
        self.quality_tier_lbl = QLabel("")
        set_role(self.quality_tier_lbl, "muted")
        self.quality_tier_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.quality_tier_lbl.setToolTip(self.tr("Approximate quality tier (hardware encoder scales are not identical)."))
        self.quality_tier_lbl.hide()

        self.quality_mode_combo = RoundedComboBox()
        self.quality_mode_combo.addItems(["CRF/CQ", "Bitrate"])
        loosen_combo(self.quality_mode_combo, 6)
        self.quality_mode_combo.setToolTip(self.tr(
            "CRF/CQ: constant quality (variable file size, default).\n"
            "Bitrate: target average bitrate (fixed bandwidth budget)."))
        self.quality_mode_combo.hide()

        q_head_row.addWidget(quality_label)
        q_head_row.addWidget(self.quality_tier_lbl)
        q_head_row.addStretch(1)
        q_head_row.addWidget(self.quality_mode_combo)

        # The number is now a small drag-or-type chip ("CRF 18"); the slider
        # below carries the quality bands, so there are no -/+ buttons.
        self.quality_spin = ScrubSpinBox("CRF ")
        self.quality_spin.setRange(0, 51)
        self.quality_spin.setValue(18)
        self.quality_spin.setToolTip(self.tr(QUALITY_SLIDER_INFO))
        self.quality_stepper = make_value_chip(self.quality_spin, 92)
        self.quality_stepper.setMinimumWidth(max(92, self.quality_stepper.minimumSizeHint().width()))

        self.target_bitrate_spin = ScrubSpinBox("", " kbps", px_per_step=5.0)
        self.target_bitrate_spin.setRange(500, 50000)
        self.target_bitrate_spin.setSingleStep(500)
        self.target_bitrate_spin.setValue(8000)
        self.target_bitrate_spin.setToolTip(self.tr("Target bitrate, in kbps."))
        self.bitrate_stepper = make_value_chip(self.target_bitrate_spin, 116)
        self.bitrate_stepper.setMinimumWidth(max(116, self.bitrate_stepper.minimumSizeHint().width()))
        self.bitrate_stepper.hide()

        q_head_row.addWidget(self.quality_stepper)
        q_head_row.addWidget(self.bitrate_stepper)
        q_box.addLayout(q_head_row)

        # Responsive wrapper if card width is squeezed under 340px
        self.q_sub_widget = QWidget()
        self.q_sub_widget.setObjectName("qSubWidget")
        self.q_sub_widget.setStyleSheet("QWidget#qSubWidget { background: transparent; }")
        self.q_sub_row = QHBoxLayout(self.q_sub_widget)
        self.q_sub_row.setContentsMargins(0, 0, 0, 0)
        self.q_sub_row.setSpacing(6)
        self.q_sub_row.addStretch(1)
        self.q_sub_widget.hide()
        q_box.addWidget(self.q_sub_widget)

        # Row 2: Full-width sliders
        self.quality_slider = TierSlider()
        self.quality_slider.setMinimumWidth(80)
        self.quality_slider.setRange(0, 51)
        self.quality_slider.setValue(18)
        self.quality_slider.setInvertedAppearance(True)
        self.quality_slider.setInvertedControls(True)
        self.quality_slider.setToolTip(self.tr(QUALITY_SLIDER_INFO))
        self.quality_spin.valueChanged.connect(self.quality_slider.setValue)
        self.quality_slider.valueChanged.connect(self.quality_spin.setValue)
        self.quality_spin.valueChanged.connect(lambda _v: self._update_quality_tier_label())
        self.quality_spin.valueChanged.connect(lambda _v: self._sync_preset_segment_state())
        q_box.addWidget(self.quality_slider)

        self.bitrate_slider = RoundHandleSlider(Qt.Orientation.Horizontal)
        set_role(self.bitrate_slider, "compact-slider")
        self.bitrate_slider.setRange(500, 50000)
        self.bitrate_slider.setValue(8000)
        self.bitrate_slider.setToolTip(self.tr("Target bitrate, in kbps."))
        self.target_bitrate_spin.valueChanged.connect(self.bitrate_slider.setValue)
        self.bitrate_slider.valueChanged.connect(self.target_bitrate_spin.setValue)
        self.bitrate_slider.setMinimumWidth(80)
        self.bitrate_slider.hide()
        q_box.addWidget(self.bitrate_slider)

        self.qf = q_head_row
        self._update_quality_range()  # correct range/default if the encoder combo's default is CPU AV1

        def _update_quality_mode_visibility():
            bitrate_mode = self.quality_mode_combo.currentText() == "Bitrate"
            self.quality_stepper.setVisible(not bitrate_mode)
            self.quality_tier_lbl.setVisible(False)
            self.quality_slider.setVisible(not bitrate_mode)
            self.bitrate_stepper.setVisible(bitrate_mode)
            self.bitrate_slider.setVisible(bitrate_mode)
            quality_label.setText(self.tr("Quality"))
            self.update_bitrate_estimate()
            self._sync_preset_segment_state()
            if hasattr(self, "_update_adv_encoding_header"):
                self._update_adv_encoding_header()
            if hasattr(self, "_update_conversion_adaptive_layout") and not getattr(self, "_in_adaptive_layout", False):
                self._update_conversion_adaptive_layout()
        self._update_quality_mode_visibility = _update_quality_mode_visibility
        self.quality_mode_combo.currentTextChanged.connect(lambda _t: _update_quality_mode_visibility())
        self.target_bitrate_spin.valueChanged.connect(self.update_bitrate_estimate)

        self.bitrate_label = QLabel("")
        self.bitrate_label.setWordWrap(True)
        set_role(self.bitrate_label, "muted")
        q_box.addWidget(self.bitrate_label)
        self.summary_params_label = QLabel("")
        self.summary_params_label.setWordWrap(True)
        set_role(self.summary_params_label, "muted")
        self.summary_params_label.hide()
        q_box.addWidget(self.summary_params_label)
        enc_body.addLayout(q_box)
        enc_body.addWidget(self.hwaccel_chk)

        # ---- Advanced encoding button removed from visible Encoding UI ----
        self.adv_encoding_btn = GlassButton("\u25b8 " + self.tr("Advanced encoding"), self)
        self.adv_encoding_btn.setProperty("i18n_text", "Advanced encoding")
        self.adv_encoding_btn.setProperty("drawer_header", True)
        self.adv_encoding_btn.setCheckable(True)
        set_role(self.adv_encoding_btn, "panel-toggle")
        self.adv_encoding_btn.hide()

        self.adv_encoding_panel = QWidget(self)
        self.adv_encoding_panel.setObjectName("advEncodingPanel")
        self.adv_encoding_panel.setStyleSheet("QWidget#advEncodingPanel { background: transparent; }")
        adv_enc_layout = QVBoxLayout(self.adv_encoding_panel)
        adv_enc_layout.setContentsMargins(STEPPER_GLOW_ROOM, 4, 0, 0)
        adv_enc_layout.setSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(8)
        self.rate_control_lbl = self.encoding_mode_lbl = _field_label(self.tr("Encoding mode:"))
        self.rate_control_lbl.setProperty("i18n_text", "Encoding mode:")
        mode_row.addWidget(self.rate_control_lbl)
        adv_enc_layout.addLayout(mode_row)

        self.adv_encoding_panel.hide()

        def _toggle_adv_encoding():
            expanded = self.adv_encoding_btn.isChecked()
            self.setUpdatesEnabled(False)
            old_height = self.height()
            self.adv_encoding_panel.setVisible(expanded)
            self._update_adv_encoding_header()
            self.layout().activate()
            target_h = max(old_height, self.minimumSizeHint().height())
            if self.height() != target_h:
                self.resize(self.width(), target_h)
            self.setUpdatesEnabled(True)
            if expanded and hasattr(self, "conv_scroll"):
                QTimer.singleShot(0, lambda: self.conv_scroll.ensureWidgetVisible(self.quality_mode_combo, 0, 32))
            QTimer.singleShot(0, self.refresh_blur)

        self.adv_encoding_btn.clicked.connect(_toggle_adv_encoding)

        self.backend_combo.currentTextChanged.connect(lambda _t: self._update_hwaccel_note())
        self.encoder_combo.currentTextChanged.connect(lambda _t: self._update_hwaccel_note())
        self._update_hwaccel_note()

        # End of the "encoding" group. Its own top padding/divider line
        # (see role="section-title" QSS) is the separator now - a bare
        # addSpacing(14) here on top of that just doubled the gap, which
        # is exactly the wasted vertical space that left no room for
        # Advanced filters to expand without the card overflowing.
        tm_body = _make_collapsible_section("tonemap", "HDR tone mapping", True)
        tm_body.setSpacing(6)
        tm_body.addWidget(_field_label("Method"))
        self.method_combo = RoundedComboBox()
        loosen_combo(self.method_combo, 10)
        self.method_combo.currentTextChanged.connect(
            lambda text: self.method_combo.setToolTip(self.tr(TONEMAP_INFO.get(text, ""))))
        self.method_combo.currentTextChanged.connect(self._schedule_frame_preview)
        self.method_combo.currentTextChanged.connect(lambda _t: self._sync_preset_segment_state())
        self.method_combo.currentIndexChanged.connect(lambda _idx: self._clear_job_tonemap_override())
        self.encoder_combo.currentTextChanged.connect(lambda _t: self._sync_preset_segment_state())
        tm_body.addWidget(self.method_combo)
        self.backend_combo.currentTextChanged.connect(
            lambda _text: self.refresh_method_choices())

        # Gamut mapping (GPU / libplacebo only: Perceptual, Relative, Clip)
        # OR Desaturate overbright highlights (CPU tonemap only) on the left,
        # + Compare methods button always anchored on the right of the same row.
        class _BoundedRowWidget(QWidget):
            def minimumSizeHint(self_inner):
                sh = super().minimumSizeHint()
                return QSize(min(260, sh.width()), sh.height())

        self.gamut_mapping_row_widget = _BoundedRowWidget()
        self.gamut_mapping_row_widget.setObjectName("gamutMappingRowWidget")
        self.gamut_mapping_row_widget.setStyleSheet("QWidget#gamutMappingRowWidget { background: transparent; }")
        gamut_row = QHBoxLayout(self.gamut_mapping_row_widget)
        gamut_row.setContentsMargins(0, 1, 0, 1)
        gamut_row.setSpacing(8)
        self.gamut_mapping_label = _field_label("Gamut mapping:")
        self.gamut_mapping_label.setWordWrap(False)
        self.gamut_mapping_label.setMinimumWidth(0)
        self.gamut_combo = RoundedComboBox()
        self.gamut_combo.addItems(list(GAMUT_MAPPING_LABELS))
        self.gamut_combo.setCurrentText("Perceptual")
        loosen_combo(self.gamut_combo, 6)
        gamut_tip = self.tr(GAMUT_MAPPING_INFO)
        self.gamut_mapping_label.setToolTip(gamut_tip)
        self.gamut_combo.setToolTip(gamut_tip)
        self.gamut_combo.currentTextChanged.connect(self._schedule_frame_preview)

        # desat: CPU (zscale+tonemap) path option placed on the left of the
        # same row as Compare methods when a CPU curve is selected.
        self.desat_chk = ToggleSwitch("Desaturate overbright highlights")
        self.desat_chk.setToolTip(self.tr(
            "Desaturates extreme highlights toward white to avoid tinting in blown-out areas."))
        self.desat_chk.toggled.connect(self._update_desat_availability)
        self.desat_chk.toggled.connect(self._schedule_frame_preview)
        self.desat_spin = QDoubleSpinBox()
        self.desat_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        _orig_desat_wheel = self.desat_spin.wheelEvent
        self.desat_spin.wheelEvent = lambda ev: (_orig_desat_wheel(ev) if self.desat_spin.hasFocus() else ev.ignore())
        self.desat_spin.setRange(0.0, 5.0)
        self.desat_spin.setSingleStep(0.1)
        self.desat_spin.setDecimals(2)
        self.desat_spin.setValue(2.0)
        self.desat_spin.setFixedWidth(SPIN_WIDTH)
        self.desat_spin.valueChanged.connect(self._schedule_frame_preview)

        self.compare_methods_btn = GlassButton(self.tr("Compare methods"))
        self.compare_methods_btn.setProperty("i18n_text", "Compare methods")
        set_role(self.compare_methods_btn, "panel-toggle")
        self.compare_methods_btn.setFixedHeight(30)
        self.compare_methods_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.compare_methods_btn.setMinimumWidth(0)
        self.compare_methods_btn.minimumSizeHint = lambda: QSize(90, 30)
        self.compare_methods_btn.setEnabled(False)
        self.compare_methods_btn.setToolTip(self.tr(
            "Compare tone-mapping methods side-by-side on the current frame."))
        self.compare_methods_btn.clicked.connect(self.open_tonemap_comparison)

        gamut_row.addWidget(self.gamut_mapping_label, 0)
        gamut_row.addWidget(self.gamut_combo, 1)
        gamut_row.addWidget(self.desat_chk, 1)
        gamut_row.addWidget(self.desat_spin, 0)
        gamut_row.addWidget(self.compare_methods_btn, 0)
        tm_body.addWidget(self.gamut_mapping_row_widget)

        # Fine-tuning for the two libplacebo (GPU tonemap) behaviours that
        # can corrupt output into solid-colour frames on some sources - see
        # LIBPLACEBO_DOVI_INFO/LIBPLACEBO_PEAK_DETECT_INFO above and
        # build_vf_chain() below for the history. Both default off (the
        # known-reliable setting) and are only enabled (not just greyed
        # out) once a GPU/libplacebo curve is actually selected - CPU
        # curves never touch libplacebo at all. Placed in an adaptive
        # flow layout so they share a single row when labels fit and wrap
        # cleanly onto a second line without clipping when narrow/translated.
        self.tm_toggles_layout = _AdaptiveFlowLayout(h_spacing=8, v_spacing=6)
        self.dv_rpu_chk = ToggleSwitch("Apply Dolby Vision RPU")
        self.dv_rpu_chk.setToolTip(self.tr(LIBPLACEBO_DOVI_INFO))
        self.dv_rpu_chk.toggled.connect(self._schedule_frame_preview)
        self.dv_rpu_chk.clicked.connect(self._on_dv_rpu_clicked)
        self.tm_toggles_layout.addWidget(self.dv_rpu_chk)

        self.peak_detect_chk = ToggleSwitch("Dynamic peak detection")
        self.peak_detect_chk.setToolTip(self.tr(LIBPLACEBO_PEAK_DETECT_INFO))
        self.peak_detect_chk.toggled.connect(self._schedule_frame_preview)
        self.tm_toggles_layout.addWidget(self.peak_detect_chk)
        tm_body.addLayout(self.tm_toggles_layout)

        self.method_combo.currentTextChanged.connect(lambda _t: self._update_libplacebo_toggle_availability())
        self.backend_combo.currentTextChanged.connect(lambda _t: self._update_libplacebo_toggle_availability())

        # desat_chk/desat_spin exist now, so this is safe to run for the
        # first time - but _update_libplacebo_toggle_availability() isn't
        # called yet: its first call is deferred until the Advanced filters
        # drawer below has been built.
        self._update_desat_availability()

        # End of the "tone mapping" group, before the "output" group below.
        # No addSpacing() here - the section header's own top padding/
        # divider (role="section-title") already separates the groups;
        # stacking both doubled the gap and was exactly what left no room
        # for Advanced filters to expand without the card overflowing.
        # Brightness/Saturation used to live here too; they're built
        # further down now, inside the Advanced filters drawer (most
        # people never touch them, same as Deband/Denoise), but the
        # widgets themselves are unchanged.

        # ---- OUTPUT group: resolution, container, then the optional
        # Advanced filters drawer (which itself only ever affects the
        # output, so it belongs at the end of this group rather than
        # floating on its own below it).
        out_body = _make_collapsible_section("output", "Output", True)
        # Resolution and Container share one two-column row (labels on top,
        # combos below) instead of two stacked label+combo pairs - that
        # single change gives back ~60px of height in the Conversion
        # card, which is what lets the Advanced filters drawer open
        # without the window having to grow (its content used to push
        # this column's minimum height past the left column's, so Qt
        # quietly made the whole window taller on every expand). Labels
        # use an Ignored horizontal policy so a long translation can only
        # be clipped, never widen the column's minimum width - the same
        # width budget the toggles rows below are careful about.
        self.out_grid = out_grid = QGridLayout()
        out_grid.setContentsMargins(0, 0, 0, 0)
        out_grid.setHorizontalSpacing(8)
        out_grid.setVerticalSpacing(6)
        out_grid.setColumnStretch(0, 3)
        out_grid.setColumnStretch(1, 2)
        out_grid.setColumnStretch(2, 3)
        res_lbl = _field_label("Output resolution")
        res_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        out_grid.addWidget(res_lbl, 0, 0)
        self.res_combo = RoundedComboBox()
        self.res_combo.addItems(list(RESOLUTIONS.keys()))
        loosen_combo(self.res_combo, 6)
        self.res_combo.currentTextChanged.connect(self.update_bitrate_estimate)
        self.res_combo.currentTextChanged.connect(self._schedule_frame_preview)
        self.res_combo.setToolTip(self.tr(
            "Width is computed automatically to keep the source's aspect ratio."))
        out_grid.addWidget(self.res_combo, 1, 0)

        cont_lbl = _field_label("Container")
        cont_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        out_grid.addWidget(cont_lbl, 0, 1)
        self.container_combo = RoundedComboBox()
        # MKV first, so it's the default selection: it accepts every track
        # type this app might copy through unchanged (TrueHD/Atmos, DTS,
        # PGS, SRT, ...), while MP4 silently rejects several of them at mux
        # time (see _MP4_INCOMPATIBLE_TRACK_HINTS below) - safer default for
        # people who don't already know which tracks their source has.
        self.container_combo.addItems(["MKV", "MP4"])
        loosen_combo(self.container_combo, 4)
        self.container_combo.currentTextChanged.connect(self._on_container_changed)
        self.container_combo.setToolTip(self.tr(
            "MKV: recommended default (supports all audio and subtitle tracks).\n"
            "MP4: maximum device compatibility (may reject some audio/subtitle formats)."
        ))
        out_grid.addWidget(self.container_combo, 1, 1)

        # Output folder button placed on the same row (row 1, col 2) as Output resolution and Container:
        self.output_folder_btn = GlassButton(self.tr("Output folder"))
        self.output_folder_btn.setProperty("i18n_text", "Output folder")
        set_role(self.output_folder_btn, "panel-toggle")
        self.output_folder_btn.setFixedHeight(30)
        self.output_folder_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.output_folder_btn.setMinimumWidth(0)
        self.output_folder_btn.minimumSizeHint = lambda: QSize(90, 30)
        self.output_folder_btn.clicked.connect(self._output_or_reveal)
        self.output_folder_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.output_folder_btn.customContextMenuRequested.connect(
            lambda pos: self._show_history_dialog())
        self._update_output_folder_btn()
        out_grid.addWidget(self.output_folder_btn, 1, 2)
        out_body.addLayout(out_grid)

        # Migrate legacy section_expanded_additional key without losing user state
        if self.i18n.settings.value("section_expanded_additional", None) is not None:
            self.i18n.settings.remove("section_expanded_additional")

        # ---- Advanced filters at the end of Output section (P1-1) ----
        self.adv_filters_btn = GlassButton("\u25b8 Advanced filters")
        self.adv_filters_btn.setProperty("i18n_text", "Advanced filters")
        self.adv_filters_btn.setProperty("drawer_header", True)
        self.adv_filters_btn.setCheckable(True)
        set_role(self.adv_filters_btn, "panel-toggle")
        self.adv_filters_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.adv_filters_btn.setMinimumWidth(0)
        self.adv_filters_btn.setMinimumHeight(30)
        self.adv_filters_btn.minimumSizeHint = lambda: QSize(160, 30)
        out_body.addWidget(self.adv_filters_btn)

        self.adv_filters_panel = QWidget()
        self.adv_filters_panel.setObjectName("advFiltersPanel")
        # This panel sits *inside* conv's Card, not beside it - a plain
        # QWidget gets the app-wide "QWidget { background: {BG} }" flat
        # fill by default, which is exactly right for a bare card body but
        # visibly wrong here: Card's own background (gradient/tint in
        # Glass ui_style, a flat CARD-colour fill in classic - see Card's
        # docstring) is what should show through, the same as every other
        # plain content widget already sitting in conv.body. Forcing this
        # one transparent is what makes it blend in instead of painting a
        # mismatched solid rectangle over Card's own background - visible
        # as a flat beige/grey box that didn't track theme changes at all,
        # since it was a flat colour rather than transparent to begin with.
        self.adv_filters_panel.setStyleSheet("QWidget#advFiltersPanel { background: transparent; }")
        adv_layout = QVBoxLayout(self.adv_filters_panel)
        adv_layout.setContentsMargins(STEPPER_GLOW_ROOM, 4, 0, 0)
        adv_layout.setSpacing(4)

        # Colour adjustments: ColorPad2D (for Brightness boost x Saturation trim) +
        # ParamSliders (value shown inside the control, no -/+ buttons) with one Reset
        # for the whole group. Each slider is also the object the rest of the app
        # already knew as *_spin, *_slider, *_label and *_stepper.
        self.colour_group_label = _field_label(self.tr("Colour adjustments"))
        self.colour_group_label.setProperty("i18n_text", "Colour adjustments")
        self.colour_group_label.setMinimumWidth(0)
        self.colour_reset_btn = LinkButton(self.tr("Reset"))
        self.colour_reset_btn.setProperty("i18n_text", "Reset")
        self.colour_reset_btn.clicked.connect(self._reset_colour_adjustments)
        colour_hdr = QHBoxLayout()
        colour_hdr.setContentsMargins(0, 0, 0, 0)
        colour_hdr.addWidget(self.colour_group_label)
        colour_hdr.addStretch(1)
        colour_hdr.addWidget(self.colour_reset_btn)
        adv_layout.addLayout(colour_hdr)

        self.brightness_slider = ParamSlider(
            "Brightness", -20, 20, 0, bidirectional=True, origin=0, pad_capable=True)
        self.brightness_spin = self.brightness_label = self.brightness_stepper = self.brightness_slider
        adv_layout.addWidget(self.brightness_slider)
        self.brightness_spin.valueChanged.connect(self._schedule_frame_preview)

        self.brightness_backend_note = QLabel("")
        self.brightness_backend_note.setWordWrap(True)
        set_role(self.brightness_backend_note, "muted")
        adv_layout.addWidget(self.brightness_backend_note)

        self.saturation_slider = ParamSlider(
            "Saturation", -20, 20, 0, bidirectional=True, origin=0, pad_capable=True)
        self.saturation_spin = self.saturation_label = self.saturation_stepper = self.saturation_slider
        adv_layout.addWidget(self.saturation_slider)
        self.saturation_spin.valueChanged.connect(self._schedule_frame_preview)

        self.saturation_backend_note = QLabel("")
        self.saturation_backend_note.setWordWrap(True)
        set_role(self.saturation_backend_note, "muted")
        adv_layout.addWidget(self.saturation_backend_note)

        # 2-D pad for Brightness boost (vertical) x Saturation trim (horizontal) ("Colour pad" style, default).
        self.colour_pad = ColorPad2D(self.saturation_slider, self.brightness_slider)
        adv_layout.addWidget(self.colour_pad)

        self.colortemperature_slider = ParamSlider(
            "Colour temperature", 3000, 10000, 6500, step=100, bidirectional=True, origin=6500,
            formatter=lambda v: f"{v} K", gradient=TEMPERATURE_GRADIENT, pad_capable=True)
        self.colortemperature_spin = self.colortemperature_label = self.colortemperature_stepper = self.colortemperature_slider
        self.colortemperature_spin.valueChanged.connect(self._schedule_frame_preview)
        adv_layout.addWidget(self.colortemperature_slider)

        for _w in (self.brightness_slider, self.saturation_slider, self.colortemperature_slider, self.colour_pad):
            _w.styleChosen.connect(self._on_control_style_chosen)
        self.brightness_slider.styleChanged.connect(lambda _s: self._apply_colour_group_style())
        for _w in (self.brightness_slider, self.saturation_slider, self.colortemperature_slider):
            _w.valueChanged.connect(lambda _v: self._update_colour_reset_state())
        self._apply_colour_group_style()

        self._retranslate_advanced_filters()

        self.backend_combo.currentTextChanged.connect(lambda _t: self._update_brightness_availability())
        self._update_brightness_availability()

        self._update_libplacebo_toggle_availability()

        # Adaptive grid for Deband, Auto-crop, Resharpen (collapses to 1 column on narrow width)
        self.adv_grid_widget = QWidget()
        self.adv_grid_widget.setObjectName("advGridWidget")
        self.adv_grid_widget.setStyleSheet("QWidget#advGridWidget { background: transparent; }")
        self.adv_grid_layout = QGridLayout(self.adv_grid_widget)
        self.adv_grid_layout.setContentsMargins(0, 4, 0, 0)
        self.adv_grid_layout.setHorizontalSpacing(10)
        self.adv_grid_layout.setVerticalSpacing(6)

        self.deband_chk = ToggleSwitch("Deband")
        self.deband_chk.setToolTip(self.tr(
            "Smooths color banding artifacts in gradients (skies, shadows)."))
        self.autocrop_chk = ToggleSwitch("Auto-crop")
        self.autocrop_chk.setToolTip(self.tr(
            "Detects and crops black letterbox bars before encoding."))
        self.resharpen_chk = ToggleSwitch("Resharpen")
        self.resharpen_chk.setToolTip(self.tr(
            "Applies a light sharpening pass after downscaling to preserve edge detail."))

        # Denoise compact unified chip dropdown beside Resharpen ("Denoise: Off / Light / Medium")
        self.denoise_container = QWidget()
        self.denoise_container.setObjectName("denoiseContainer")
        self.denoise_container.setStyleSheet("QWidget#denoiseContainer { background: transparent; }")
        denoise_row = QHBoxLayout(self.denoise_container)
        denoise_row.setContentsMargins(0, 0, 0, 0)
        denoise_row.setSpacing(0)
        denoise_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.denoise_label = _field_label(self.tr("Denoise:"))
        self.denoise_label.setProperty("i18n_text", "Denoise:")
        self.denoise_label.setFixedSize(0, 0)
        self.denoise_label.hide()
        denoise_row.addWidget(self.denoise_label)
        self.denoise_combo = RoundedComboBox()
        self.denoise_combo.setObjectName("denoiseChipCombo")
        self.denoise_combo.addItems(["Off", "Light", "Medium"])
        self.denoise_combo.set_display_prefix(f"{self.tr('Denoise:').rstrip(':').strip()}: ", self.tr)
        self.denoise_combo.setFixedHeight(26)
        self.denoise_combo.setMinimumWidth(120)
        self.denoise_combo.setMaximumWidth(150)
        self.denoise_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        loosen_combo(self.denoise_combo, 5)
        self.denoise_combo.setToolTip(self.tr(
            "Reduces noise and grain before tone mapping."))
        denoise_row.addWidget(self.denoise_combo, 1)

        self.adv_grid_layout.addWidget(self.deband_chk, 0, 0)
        self.adv_grid_layout.addWidget(self.autocrop_chk, 0, 1)
        self.adv_grid_layout.addWidget(self.resharpen_chk, 1, 0)
        self.adv_grid_layout.addWidget(self.denoise_container, 1, 1)
        self.adv_grid_layout.setColumnStretch(0, 1)
        self.adv_grid_layout.setColumnStretch(1, 1)
        adv_layout.addWidget(self.adv_grid_widget)

        self.backend_combo.currentTextChanged.connect(
            lambda _text: self._update_deband_availability())
        self._update_deband_availability()

        self.deband_chk.toggled.connect(self._schedule_frame_preview)
        self.autocrop_chk.toggled.connect(self._schedule_frame_preview)
        self.resharpen_chk.toggled.connect(self._schedule_frame_preview)
        self.denoise_combo.currentTextChanged.connect(self._schedule_frame_preview)
        self.backend_combo.currentTextChanged.connect(self._schedule_frame_preview)

        for sig in (
            self.brightness_spin.valueChanged,
            self.saturation_spin.valueChanged,
            self.colortemperature_spin.valueChanged,
            self.deband_chk.toggled,
            self.autocrop_chk.toggled,
            self.resharpen_chk.toggled,
            self.denoise_combo.currentTextChanged,
            self.backend_combo.currentTextChanged,
        ):
            sig.connect(lambda *_a: self._update_adv_filters_header())
            sig.connect(lambda *_a: self._sync_preset_segment_state())

        self.adv_filters_panel.setVisible(False)
        out_body.addWidget(self.adv_filters_panel)

        def _toggle_adv_filters():
            expanded = self.adv_filters_btn.isChecked()
            self.setUpdatesEnabled(False)
            old_height = self.height()
            self.adv_filters_panel.setVisible(expanded)
            self._update_adv_filters_header()
            self.layout().activate()
            target_h = max(old_height, self.minimumSizeHint().height())
            if self.height() != target_h:
                self.resize(self.width(), target_h)
            self.setUpdatesEnabled(True)
            self._repaint_conversion_card()
            if expanded and hasattr(self, "conv_scroll") and False:
                QTimer.singleShot(0, lambda: self.conv_scroll.ensureWidgetVisible(self.saturation_slider, 0, 32))
            QTimer.singleShot(0, self.refresh_blur)
        self.adv_filters_btn.clicked.connect(_toggle_adv_filters)
        self._update_adv_filters_header()


        # Stretch goes here, between the settings fields and the two
        # bottom buttons, instead of after the buttons - that way the
        # buttons are pinned to the bottom edge of the CONVERSION card.
        # CONTROLS (left column) now does the exact same thing with its
        # own trailing content - see the comment on controls.body.addStretch(1)
        # above - so both cards grow to fill the shared splitter height
        # with their own background, and their bottom edges stay level
        # with each other regardless of which side's natural content is
        # currently taller.
        conv_inner.addStretch(1)

        self.activity_btn = GlassButton("Activity")
        self.activity_btn.setCheckable(True)
        set_role(self.activity_btn, "panel-toggle")
        self.activity_btn.clicked.connect(self.toggle_activity)
        conv.body.addWidget(self.activity_btn)

        right.addWidget(conv, 1)

        # ---- ACTIVITY: a collapsed log drawer below the splitter --------
        # A real Card, not a bare QFrame(role="card") - the liquid-glass
        # backdrop/tint/rim-border is painted in Card.paintEvent() now (see
        # its docstring), NOT through QSS, so a plain QFrame with the same
        # "card" property never actually gets that painting and instead
        # falls through to the generic flat QWidget{background} fill. Only
        # noticeable as a bug in Glass ui_style, where every neighbouring
        # real Card gets the frosted-glass treatment and this drawer stood
        # out as a flat black box instead - classic ui_style's own flat
        # fill happened to look fine by coincidence, same mechanism either
        # way. No title wanted here (there's already an "Activity" toggle
        # button elsewhere) - Card always creates one, so it's hidden right
        # after construction instead.
        #
        # Added straight to root, with no extra QWidget wrapper for the
        # min/max-height constraint - Card already sets WA_TranslucentBackground
        # on itself specifically so its four rounded corners show whatever's
        # *behind* it instead of defaulting to opaque black there (see the
        # comment on that attribute in Card.__init__), but that only works
        # one layer deep: a plain QWidget wrapper in between is itself
        # opaque, so the corners were revealing *its* black fill instead of
        # reaching all the way through to the window behind that. Every
        # other Card in this window sits directly in a layout with no such
        # wrapper - this is the one place that had one, and the one place
        # with the black-corner bug.
        self.side_card = Card("")
        self.side_card.setMinimumHeight(160)
        self.side_card.setMaximumHeight(260)
        self.side_card.title_label.setVisible(False)
        card_layout = self.side_card.body
        body.addWidget(self.side_card, 1)
        self.side_card.hide()

        activity_page = QWidget()
        activity_page.setObjectName("activityPage")
        # This plain QWidget has no "role" property, so it falls under the
        # app-wide `QWidget { background: {t['bg_window']} }` catch-all rule and
        # gets painted opaque in the theme's base colour - which sits
        # directly between the Card's own painted background and the (now
        # transparent) log, and was the actual source of the solid black/
        # near-black box: the log itself was already transparent, but this
        # widget behind it wasn't, so its opaque fill is what showed
        # through instead of the Card underneath.
        activity_page.setStyleSheet("QWidget#activityPage { background: transparent; }")
        ap_layout = QVBoxLayout(activity_page)
        # Card rounds its corners at RADIUS=16px, but its own outer layout
        # only reserves 10px/12px top/bottom margin - plenty when a title
        # label sits there, but this card hides its title, so the log's
        # square corners used to poke slightly into the rounded-off wedge
        # at the top. 8px here (on top of Card's existing 10/12px) clears
        # RADIUS on both edges at every corner.
        ap_layout.setContentsMargins(0, 8, 0, 8)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(5000)
        set_role(self.log, "log")
        # QTextEdit's viewport auto-fills with the opaque QPalette::Base
        # colour by default, which paints over the "background: transparent"
        # set via QSS above (a well-known Qt gotcha - the QSS rule alone
        # isn't always enough for QAbstractScrollArea subclasses, and even
        # this gets reset every time setStyleSheet() runs again - see the
        # matching reassertion in apply_theme()). Turning this off, plus
        # dropping the native frame, is what actually lets the Card/glass
        # panel behind it show through so the log reads as plain text
        # floating over the current theme instead of its own boxed-in
        # panel.
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.log.setAutoFillBackground(False)
        self.log.viewport().setAutoFillBackground(False)
        self.log.viewport().setObjectName("logViewport")
        self.log.viewport().setStyleSheet("QWidget#logViewport { background: transparent; }")
        ap_layout.addWidget(self.log)
        card_layout.addWidget(activity_page)

        # ---- bitrate estimate: keep it live as the relevant settings change --
        self.quality_spin.valueChanged.connect(self.update_bitrate_estimate)
        self.encoder_combo.currentTextChanged.connect(self.update_bitrate_estimate)
        self.backend_combo.currentTextChanged.connect(self.update_bitrate_estimate)
        # _update_quality_mode_visibility() (defined earlier, by the Quality
        # row) also needs res_combo/bitrate_label to exist - safe to call
        # for the first time only now that they do.
        _update_quality_mode_visibility()
        self._update_queue_hint()

        # Collapsed layout - both Activity and Capabilities are collapsed at
        # this point, so sizing to this content naturally produces a compact
        # window that fits low-resolution screens; the splitter gives up
        # some of its height on demand when either panel is expanded (see
        # _select_tab). The actual resize-to-fit happens later, in
        # __init__, once apply_theme() has run too - see
        # _fit_height_to_content().
        if hasattr(self, "conv_scroll") and self.conv_scroll is not None:
            self.conv_scroll.installEventFilter(self)
            self.conv_scroll.viewport().installEventFilter(self)
        if hasattr(self, "conv_scroll_content") and self.conv_scroll_content is not None:
            self.conv_scroll_content.installEventFilter(self)
        self._ensure_right_column_width()
        self.setMinimumSize(920, 960)
        self.layout().activate()
        self.splitter.setSizes([632, 448])

    # ---- sizing ----------------------------------------------------------
    DEFAULT_WINDOW_WIDTH = 1080
    DEFAULT_WINDOW_HEIGHT = 1020

    def _compute_minimum_window_height(self) -> int:
        left_min_h = self.left_panel.minimumSizeHint().height() if hasattr(self, "left_panel") else 0
        right_min_h = self.right_panel.minimumSizeHint().height() if hasattr(self, "right_panel") else 0
        splitter_min_h = self.splitter.minimumSizeHint().height() if hasattr(self, "splitter") else 0
        chrome_h = max(56, self.minimumSizeHint().height() - splitter_min_h)
        return max(960, self.minimumSizeHint().height(), chrome_h + max(left_min_h, right_min_h))

    def _fit_height_to_content(self, width=None):
        """Size the window to the default ~1050px height (or taller if the
        layout requires more, e.g. when Activity is expanded), capped by the
        available desktop screen height minus margins, and keep minimumHeight
        high enough that Live preview's viewport stays >= 200px."""
        if hasattr(self, "_ensure_right_column_width"):
            self._ensure_right_column_width()
        if hasattr(self, "_sync_analysis_scroll_height"):
            self._sync_analysis_scroll_height()
        self._invalidate_all_layouts()
        self.layout().activate()
        min_content_h = self._compute_minimum_window_height()
        target_h = max(self.DEFAULT_WINDOW_HEIGHT, min_content_h)
        screen = self.screen() if hasattr(self, "screen") else None
        if screen is not None:
            avail_h = screen.availableGeometry().height()
            # In offscreen QPA, Qt's default virtual screen is 800px high, which
            # previously clamped target_h to 760px (800 - 40). Only apply the
            # desktop screen cap on a real display or when avail_h >= 960.
            is_offscreen = QApplication.platformName().lower() == "offscreen"
            if (not is_offscreen and avail_h > 0) or avail_h >= 960:
                target_h = min(target_h, max(720, avail_h - 32))
        min_win_h = min(min_content_h, target_h)
        self.setMinimumSize(920, min_win_h)
        self.resize(width if width is not None else self.width(), target_h)

    def _restore_saved_window_geometry(self) -> bool:
        """Restore saved window geometry/size from QSettings if available."""
        settings = getattr(getattr(self, "i18n", None), "settings", None)
        if settings is None:
            return False
        raw_geo = settings.value("window_geometry", None)
        if isinstance(raw_geo, QByteArray) and not raw_geo.isEmpty():
            if self.restoreGeometry(raw_geo):
                if self.width() < self.minimumWidth() or self.height() < self.minimumHeight():
                    self.resize(max(self.width(), self.minimumWidth()), max(self.height(), self.minimumHeight()))
                return True
        raw_size = settings.value("window_size", None)
        if isinstance(raw_size, QSize) and raw_size.isValid():
            self.resize(max(self.minimumWidth(), raw_size.width()), max(self.minimumHeight(), raw_size.height()))
            return True
        if isinstance(raw_size, str) and "x" in raw_size.lower():
            try:
                w_str, h_str = raw_size.lower().split("x", 1)
                w_val, h_val = int(w_str.strip()), int(h_str.strip())
                if w_val > 0 and h_val > 0:
                    self.resize(max(self.minimumWidth(), w_val), max(self.minimumHeight(), h_val))
                    return True
            except ValueError:
                pass
        return False

    def _save_window_geometry(self) -> None:
        settings = getattr(getattr(self, "i18n", None), "settings", None)
        if settings is None:
            return
        if not self.isMinimized() and not self.isMaximized() and not self.isFullScreen():
            settings.setValue("window_geometry", self.saveGeometry())
            settings.setValue("window_size", f"{self.width()}x{self.height()}")
        # NOTE: this used to also call self.setMaximumHeight(target_h + 12)
        # to stop the compact layout leaving dead grey space when dragged
        # taller by hand. That worked, but a QWidget with a maximumHeight
        # below the screen's height can't be maximized on Windows/most
        # window managers - Qt just disables (or silently no-ops) the
        # maximize button once maximumHeight() is capped, which is why the
        # window couldn't be put fullscreen. Dropping the cap fixes that;
        # the mild dead-space-when-manually-resized tradeoff is worth being
        # able to maximize at all.

    def _invalidate_all_layouts(self):
        """self.layout().activate() alone only recomputes the top-level
        root layout's geometry from whatever its children's *cached*
        sizeHint()/minimumSize() currently say - it doesn't itself force
        every nested layout (grid inside FILES' Card.body inside left_panel
        inside splitter, and so on down) to recompute its own cache first.
        Normally a style/font change propagates upward on its own via
        QEvent::LayoutRequest, but that's one hop of the cascade per
        event-loop turn - a widget whose own sizeHint() is already correct
        can still sit inside a parent QGridLayout/QVBoxLayout whose cached
        size hasn't caught up yet, which is exactly what showed up as the
        FILES card's fields being ~3px short of their own sizeHint() when
        measured immediately after a style/font change. Invalidating every
        layout in the tree here forces a fresh bottom-up recompute in one
        go, so this always produces the same height it eventually would
        anyway - no cascade timing to get unlucky with."""
        if self.layout():
            self.layout().invalidate()
        for w in self.findChildren(QWidget):
            if w.layout():
                w.layout().invalidate()

    # ---- resolution presets -------------------------------------------
    # ---- bitrate estimate ----------------------------------------------
    def update_bitrate_estimate(self):
        v = self.encoder_combo.currentText()
        if v not in ENCODER_MAP:
            self.bitrate_label.setText("")
            if hasattr(self, "summary_params_label"):
                self.summary_params_label.setText("")
                self.summary_params_label.hide()
            return
        if self.quality_mode_combo.currentText() == "Bitrate":
            kbps = self.target_bitrate_spin.value()
        else:
            q = self.quality_spin.value()
            target_h = RESOLUTIONS.get(self.res_combo.currentText())
            if target_h and self.src_width and self.src_height:
                w = even(round(self.src_width * target_h / self.src_height))
                h = target_h
            else:
                w, h = self.src_width, self.src_height
            kbps = estimate_bitrate_kbps(v, q, w, h)
        mbps = kbps / 1000.0
        self.bitrate_label.setText(self.tr("Approx. output bitrate: ~{mbps:.1f} Mbps").format(mbps=mbps))
        has_source = bool(self.src_edit.text() and Path(self.src_edit.text()).is_file())
        reliable_size = bool(has_source and self.duration and self.duration > 0 and (
            self.quality_mode_combo.currentText() == "Bitrate" or (self.src_width and self.src_height)
        ))
        if hasattr(self, "summary_params_label"):
            res_text = self.res_combo.currentText() if hasattr(self, "res_combo") else ""
            enc_text = self.encoder_combo.currentText() if hasattr(self, "encoder_combo") else ""
            short_codec = enc_text.split("\u00b7")[-1].strip() if "\u00b7" in enc_text else enc_text
            if has_source and res_text and enc_text:
                approx_str = ""
                if reliable_size:
                    size_gb = (kbps * 1000.0 / 8.0) * self.duration / (1024 ** 3)
                    unit = "GB" if size_gb >= 0.1 else "MB"
                    size_val = size_gb if unit == "GB" else size_gb * 1024
                    approx_str = f" \u00b7 ~{size_val:.1f} {unit} ({self.tr('approx.')})"
                self.summary_params_label.setText(f"{self.tr('Output summary')}: {res_text} \u00b7 {short_codec}{approx_str}")
                self.summary_params_label.show()
            else:
                self.summary_params_label.setText("")
                self.summary_params_label.hide()

    # ---- quality range / advanced-toggle availability ---------------
    def _available_quality_tier_keys(self) -> list[str]:
        enc = self.encoder_combo.currentText() if hasattr(self, "encoder_combo") else ""
        bounds = QUALITY_TIER_BOUNDS_BY_ENCODER.get(enc, QUALITY_TIER_BOUNDS_51)
        return list(dict.fromkeys(label for _, _, label in bounds))

    def _update_quality_tier_reserved_width(self):
        lbl = getattr(self, "quality_tier_lbl", None)
        if lbl is None:
            return
        lbl.setFixedWidth(0)
        lbl.hide()

    def _update_quality_tier_label(self):
        lbl = getattr(self, "quality_tier_lbl", None)
        if lbl is None:
            return
        self._update_quality_tier_reserved_width()
        tier_src = quality_tier_label(self.encoder_combo.currentText(), self.quality_spin.value())
        lbl.setText(self.tr(tier_src))
        lbl.hide()
        if hasattr(self, "quality_slider"):
            self.quality_slider.set_labeler(self.tr)      # band captions follow the interface language
        lbl.setToolTip(self.tr("Approximate quality tier (hardware encoder scales are not identical)."))

    def _update_quality_range(self):
        """Applies the widest CRF/CQ range for whichever encoder is
        currently selected - see QUALITY_RANGES above. Resets to the new
        encoder's default ONLY if the old value equalled the previous
        encoder's default (or fell outside the new [lo, hi] bounds)."""
        ranges = QUALITY_RANGES.get(self.encoder_combo.currentText(), DEFAULT_QUALITY_RANGE)
        lo, hi = ranges["range"]
        new_default = ranges["default"]
        old_default = getattr(self, "_last_quality_default", DEFAULT_QUALITY_RANGE["default"])
        old_value = self.quality_spin.value()
        reset = should_reset_quality_on_range_change(old_value, old_default, lo, hi, new_default)
        self._last_quality_default = new_default
        self.quality_spin.setRange(lo, hi)
        self.quality_slider.setRange(lo, hi)
        enc_label = self.encoder_combo.currentText()
        self.quality_slider.set_tiers(
            QUALITY_TIER_BOUNDS_BY_ENCODER.get(enc_label, QUALITY_TIER_BOUNDS_51),
            QUALITY_TIER_REPS_BY_ENCODER.get(enc_label, QUALITY_TIER_REPS_51),
            self.tr)
        if reset and self.quality_spin.value() != new_default:
            self.quality_spin.setValue(new_default)
        self._update_quality_tier_label()

    def _update_libplacebo_toggle_availability(self):
        """The Dolby Vision RPU / dynamic peak detection toggles only mean
        anything for a GPU (libplacebo) tone-mapping curve - CPU curves
        (zscale+tonemap) never touch libplacebo at all - so they're fully
        hidden (not just disabled) unless a GPU curve is actually
        selected, appearing right where they're relevant (under Tone
        mapping) instead of sitting there greyed out or getting buried in
        the generic Advanced filters drawer. The CPU-only desaturation
        toggle is the mirror image of that."""
        engine, _code = tonemap_lookup(self.method_combo.currentText())
        gpu = engine == "gpu"
        self.dv_rpu_chk.setVisible(gpu)
        self.peak_detect_chk.setVisible(gpu)
        if hasattr(self, "gamut_mapping_row_widget"):
            self.gamut_mapping_row_widget.setVisible(True)
        if hasattr(self, "gamut_combo"):
            self.gamut_combo.setVisible(gpu)
        if hasattr(self, "gamut_mapping_label"):
            self.gamut_mapping_label.setVisible(gpu)
        self.desat_chk.setVisible(not gpu)
        self.desat_spin.setVisible(not gpu and self.desat_chk.isChecked())
        if hasattr(self, "tm_toggles_layout"):
            self.tm_toggles_layout.invalidate()

    def _retranslate_controls(self):
        if hasattr(self, "cores_slider"):
            self.cores_slider.setText(self.tr("Cores"))
        if hasattr(self, "priority_slider"):
            self.priority_slider.setText(self.tr("Priority"))
            self.priority_slider.update()
        if hasattr(self, "quality_slider"):
            self.quality_slider.set_labeler(self.tr)

    def _reset_colour_adjustments(self):
        for w in (self.brightness_slider, self.saturation_slider, self.colortemperature_slider):
            if w.isEnabled() or w is self.colortemperature_slider:
                w.reset()

    def _update_colour_reset_state(self):
        if hasattr(self, "colour_reset_btn"):
            self.colour_reset_btn.setEnabled(any(
                w.isModified() for w in (self.brightness_slider, self.saturation_slider, self.colortemperature_slider)))

    def _apply_colour_group_style(self):
        """The "Colour pad" style swaps the brightness + saturation sliders for one 2-D pad."""
        pad = ParamSlider.global_style() == "pad"
        self.brightness_slider.setVisible(not pad)
        self.saturation_slider.setVisible(not pad)
        self.colour_pad.setVisible(pad)
        self._update_brightness_availability()
        self._update_colour_reset_state()

    def _on_control_style_chosen(self, key):
        self.i18n.settings.setValue("control_style", key)

    def _retranslate_advanced_filters(self):
        if not hasattr(self, "brightness_label"):
            return
        self.brightness_label.setText(self.tr("Brightness"))
        bright_tip = self.tr("Brightness boost (-20 to +20, 0 = off)")
        self.brightness_label.setToolTip(bright_tip)
        self.brightness_spin.setToolTip(bright_tip)
        self.brightness_slider.setToolTip(bright_tip)

        self.saturation_label.setText(self.tr("Saturation"))
        sat_tip = self.tr("Saturation trim (-20 to +20, 0 = off)")
        self.saturation_label.setToolTip(sat_tip)
        self.saturation_spin.setToolTip(sat_tip)
        self.saturation_slider.setToolTip(sat_tip)

        self.colortemperature_label.setText(self.tr("Colour temperature"))
        ct_tip = self.tr("Colour temperature (3000K-10000K, 6500K = off)")
        self.colortemperature_label.setToolTip(ct_tip)
        self.colortemperature_spin.setToolTip(ct_tip)
        self.colortemperature_slider.setToolTip(ct_tip)
        self.colour_pad.setToolTip(self.tr("Drag to set brightness (up/down) and saturation (left/right). Double-click to reset."))
        self.colour_group_label.setText(self.tr("Colour adjustments"))
        self.colour_reset_btn.setText(self.tr("Reset"))
        self.colour_reset_btn.updateGeometry()
        if hasattr(self, "colour_pad"):
            self.colour_pad.update()

        if hasattr(self, "denoise_label"):
            self.denoise_label.setText(self.tr("Denoise:"))
        if hasattr(self, "denoise_combo") and hasattr(self.denoise_combo, "set_display_prefix"):
            self.denoise_combo.set_display_prefix(f"{self.tr('Denoise:').rstrip(':').strip()}: ", self.tr)
            lang = getattr(getattr(self, "i18n", None), "language", "en")
            if lang in ("ru", "uk"):
                self.denoise_combo.setMaximumWidth(185)
            else:
                self.denoise_combo.setMaximumWidth(150)

    def _active_advanced_filters_count(self) -> int:
        if not hasattr(self, "brightness_spin"):
            return 0
        count = 0
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        if not using_hb:
            if self.brightness_spin.value() != 0:
                count += 1
            if self.saturation_spin.value() != 0:
                count += 1
            if self.deband_chk.isChecked():
                count += 1
        if self.colortemperature_spin.value() != 6500:
            count += 1
        if self.autocrop_chk.isChecked():
            count += 1
        if self.resharpen_chk.isChecked():
            count += 1
        if self.denoise_combo.currentText() != "Off":
            count += 1
        return count

    def _update_adv_filters_header(self):
        if not hasattr(self, "adv_filters_btn"):
            return
        expanded = getattr(self.adv_filters_btn, "isChecked", lambda: False)()
        arrow = "\u25be " if expanded else "\u25b8 "
        count = self._active_advanced_filters_count()
        badge = f" ({count})" if count > 0 else ""
        self.adv_filters_btn.setProperty("i18n_text", "Advanced filters")
        self.adv_filters_btn.setText(arrow + self.tr("Advanced filters") + badge)

    def _update_adv_encoding_header(self):
        if not hasattr(self, "adv_encoding_btn"):
            return
        expanded = getattr(self.adv_encoding_btn, "isChecked", lambda: False)()
        arrow = "\u25be " if expanded else "\u25b8 "
        bitrate_active = hasattr(self, "quality_mode_combo") and self.quality_mode_combo.currentText() == "Bitrate"
        badge = f" ({self.tr('Bitrate')})" if bitrate_active else ""
        self.adv_encoding_btn.setProperty("i18n_text", "Advanced encoding")
        self.adv_encoding_btn.setText(arrow + self.tr("Advanced encoding") + badge)

    def _update_desat_availability(self):
        self.desat_spin.setEnabled(self.desat_chk.isChecked())
        self.desat_spin.setVisible(self.desat_chk.isVisible() and self.desat_chk.isChecked())

    def _update_hwaccel_note(self):
        # command_ffmpeg() always adds "-hwaccel auto" when checked, regardless
        # of encoder. command_handbrake() only adds "--enable-hw-decoding nvdec"
        # when checked *and* the selected encoder is NVIDIA NVENC - it's a
        # no-op with every other HandBrake encoder. Used to be a QLabel note
        # printed under the checkbox; now the same per-backend explanation
        # lives in the checkbox's own tooltip instead, so nothing prints
        # below it.
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        label = self.encoder_combo.currentText()
        if using_hb:
            if label.startswith("NVIDIA NVENC"):
                text = "HandBrakeCLI: enables NVDEC decoding for this NVENC encoder."
            else:
                text = "HandBrakeCLI: only has an effect with an NVIDIA NVENC encoder \u2014 no effect with the current encoder."
        else:
            text = "FFmpeg: requests CUDA / DXVA2 / VideoToolbox decoding automatically."
        self.hwaccel_chk.setToolTip(self.tr(text))
        self.keep_awake_chk.setToolTip(self.tr(
            "Stops Windows from going to sleep while a conversion is running. Turn it off if you want the PC "
            "to sleep on its own, for example when you plan to pause and shut down."))
        self.keep_display_chk.setToolTip(self.tr(
            "Prevents the display from turning off while conversion is in progress."))

    def _update_brightness_availability(self):
        # HandBrakeCLI has no equivalent of ffmpeg's eq=brightness/saturation
        # filter, so the settings are silently ignored by command_handbrake().
        # Rather than let the live preview (always rendered via ffmpeg) show
        # an effect that never makes it into the actual HandBrake output,
        # disable both controls for that backend and explain why.
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        enabled = not using_hb
        if hasattr(self, "colour_pad"):
            self.colour_pad.setEnabled(enabled)
        self.brightness_label.setEnabled(enabled)
        self.brightness_spin.setEnabled(enabled)
        self.brightness_slider.setEnabled(enabled)
        self.saturation_label.setEnabled(enabled)
        self.saturation_spin.setEnabled(enabled)
        self.saturation_slider.setEnabled(enabled)
        if using_hb:
            self.brightness_spin.setValue(0)
            self.saturation_spin.setValue(0)
            self.brightness_backend_note.setText(
                "Not available on the HandBrake backend (no equivalent filter) "
                "\u2014 switch to FFmpeg to use it.")
            self.saturation_backend_note.setText(
                "Not available on the HandBrake backend (no equivalent filter) "
                "\u2014 switch to FFmpeg to use it.")
        else:
            self.brightness_backend_note.setText("")
            self.saturation_backend_note.setText("")
        # An empty QLabel still reserves a line's worth of height (plus
        # the layout spacing on both sides of it) even with nothing to
        # show - hide it outright on the (usual, FFmpeg) path where
        # there's no note, instead of leaving a blank gap before the
        # next section.
        self.brightness_backend_note.setVisible(using_hb)
        self.saturation_backend_note.setVisible(using_hb)

    def _update_deband_availability(self):
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        self.deband_chk.setEnabled(not using_hb)
        if using_hb:
            self.deband_chk.setChecked(False)
            self.deband_chk.setToolTip(
                "Deband is unavailable in this HandBrakeCLI version. Switch to FFmpeg to use it.")
        else:
            self.deband_chk.setToolTip(self.tr(
                "Smooths color banding artifacts in gradients (skies, shadows)."))

    # ---- collapse toggles -----------------------------------------
    def toggle_activity(self):
        # Freeze the whole layout while changing visibility and height. This
        # prevents Qt from painting intermediate geometry during the resize.
        self.setUpdatesEnabled(False)
        if self.side_tab == "activity":
            self.side_tab = None
            self.side_card.hide()
        else:
            self.side_tab = "activity"
            self.side_card.show()
        self.activity_btn.setChecked(self.side_tab == "activity")
        self._fit_height_to_content()
        self.setUpdatesEnabled(True)

    # ---- state / status helper --------------------------------------
    def _sync_analysis_scroll_height(self):
        """Size analysis_scroll to the wrapped height of analysis_label's text,
        capped at ~45% of left_panel height so 13-line Dolby Vision output fits
        without scrolling at 1080x1050 while Live preview absorbs the delta."""
        if not hasattr(self, "analysis_scroll") or not hasattr(self, "analysis_label"):
            return
        vp_w = self.analysis_scroll.viewport().width()
        if vp_w <= 60:
            left_w = self.left_panel.width() if hasattr(self, "left_panel") and self.left_panel.width() > 100 else 620
            vp_w = max(280, left_w - 36)
        line_h = QFontMetrics(QFont(self.FONT, 9)).lineSpacing()
        hfw = self.analysis_label.heightForWidth(vp_w)
        sh_h = self.analysis_label.sizeHint().height()
        content_h = max(line_h + 6, (hfw if hfw > 0 else sh_h) + 8)
        left_h = self.left_panel.height() if hasattr(self, "left_panel") and self.left_panel.height() > 400 else max(960, self.height() - 72)
        chrome = (
            (self.analysis_card.title_label.sizeHint().height() if hasattr(self, "analysis_card") else 22)
            + (self.analyze_btn.sizeHint().height() if hasattr(self, "analyze_btn") else 34)
            + 36
        )
        cap_h = max(372, line_h * 13 + 12, int(left_h * 0.45) - chrome)
        target_h = min(content_h, cap_h)
        if self.analysis_scroll.minimumHeight() != target_h or self.analysis_scroll.maximumHeight() != target_h:
            self.analysis_scroll.setMinimumHeight(target_h)
            self.analysis_scroll.setMaximumHeight(target_h)

    def _status_text_font_metrics(self) -> QFontMetrics:
        """Return QFontMetrics for the actual weight used by status_label roles
        (DemiBold / 600 for info/warn/good/bad in QSS, or the polished widget font)."""
        f = QFont(self.status_label.font() if hasattr(self, "status_label") else QFont(self.FONT, 9))
        if f.pointSize() <= 0:
            f.setPointSize(9)
        role = (self.status_label.property("role") or "muted") if hasattr(self, "status_label") else "muted"
        if role in ("info", "warn", "good", "bad"):
            f.setWeight(QFont.Weight.DemiBold)
        return QFontMetrics(f)

    def _measure_status_text_px(self, text: str) -> int:
        """Measure status text width using the role's real font weight (DemiBold/600)
        plus a 6px safety margin so right-aligned text never clips on the left."""
        if not text:
            return 0
        fm_role = self._status_text_font_metrics()
        f_semi = QFont(self.FONT, 9, QFont.Weight.DemiBold)
        fm_semi = QFontMetrics(f_semi)
        return max(fm_role.horizontalAdvance(text), fm_semi.horizontalAdvance(text)) + 6

    def _available_pbar_row_status_width(self) -> int:
        """Compute available width for status_label from the actual pbar_row geometry
        while reserving >= 35% of the row (and never less than 160px) for pbar."""
        row_w = 0
        if hasattr(self, "pbar_row_widget") and self.pbar_row_widget.width() > 180:
            row_w = self.pbar_row_widget.width()
        elif hasattr(self, "pbar_row") and self.pbar_row.geometry().width() > 180:
            row_w = self.pbar_row.geometry().width()
        elif hasattr(self, "progress_card") and self.progress_card.width() > 220:
            m = self.progress_card.layout().contentsMargins() if self.progress_card.layout() else None
            pad = (m.left() + m.right()) if m is not None else 32
            row_w = self.progress_card.width() - pad
        else:
            row_w = 560
        base_min = max(160, self.pbar.minimumWidth() if hasattr(self, "pbar") else 160)
        pbar_min_w = max(base_min, int(row_w * 0.35)) if row_w >= 460 else base_min
        spacing = self.pbar_row.spacing() if hasattr(self, "pbar_row") else 8
        return max(80, row_w - pbar_min_w - spacing)

    def _update_inline_progress_status(self):
        if not hasattr(self, "status_label"):
            return
        base = getattr(self, "_base_status_text", "") or ""
        stats = getattr(self, "_latest_progress_stats", None)
        raw_stats = getattr(self, "_raw_speed_stats_text", "") or ""
        max_status_px = self._available_pbar_row_status_width()
        if getattr(self, "_last_max_status_px", None) != max_status_px:
            self._last_max_status_px = max_status_px
            self._run_status_min_w = 0

        # Build candidate full stats for tooltip via select_progress_status_text
        if stats is not None:
            eta_txt = self.tr("ETA {time}", time=format_time(stats.eta_sec)) if stats.eta_sec is not None else None
            _, full_tip = select_progress_status_text(
                base,
                speed_x=stats.speed_x,
                fps=stats.fps,
                mbps=stats.mbps,
                eta_text=eta_txt,
                max_width=max_status_px,
                measure_fn=self._measure_status_text_px,
            )
        elif raw_stats:
            _, full_tip = select_progress_status_text(
                base,
                raw_stats_text=raw_stats,
                max_width=max_status_px,
                measure_fn=self._measure_status_text_px,
            )
        else:
            full_tip = base

        role = self.status_label.property("role") or "muted"
        if role in ("info", "warn") and (stats is not None or raw_stats or "converting" in base.lower() or "test clip" in base.lower() or role == "warn"):
            # Extract percentage: from stats, base, or pbar
            pct_val = None
            if stats is not None and stats.pct is not None:
                pct_val = round(stats.pct)
            else:
                m_pct = re.search(r"(\d+(?:\.\d+)?)\s*%", base)
                if m_pct:
                    try:
                        pct_val = round(float(m_pct.group(1)))
                    except ValueError:
                        pass
                elif hasattr(self, "pbar") and self.pbar.value() > 0:
                    pct_val = round(self.pbar.value() / 10.0)

            pct_part = f"{pct_val}%" if pct_val is not None else ""

            # Extract ETA: from stats or raw_stats
            eta_part = ""
            if stats is not None and stats.eta_sec is not None:
                eta_part = self.tr("ETA {time}", time=format_time(stats.eta_sec))
            elif raw_stats:
                m_eta = re.search(r"(?:ETA\s*)([0-9]+:[0-9]+(?::[0-9]+)?|[0-9]+[hmsd](?:\s*[0-9]+[hmsd])*)", raw_stats, re.IGNORECASE)
                if m_eta:
                    eta_part = self.tr("ETA {time}", time=m_eta.group(1))

            parts = []
            if role == "warn":
                parts.append(self.tr("Paused"))
            if pct_part:
                parts.append(pct_part)
            if eta_part:
                parts.append(eta_part)

            disp = " \u00b7 ".join(parts)
            if not disp:
                disp = base
            if eta_part and len(parts) > 1 and self._measure_status_text_px(disp) > max_status_px:
                parts_no_eta = [p for p in parts if p != eta_part]
                disp = " \u00b7 ".join(parts_no_eta) if parts_no_eta else disp
        else:
            disp = base

        real_w = self._status_text_font_metrics().horizontalAdvance(disp) if disp else 0
        raw_target_w = min(max_status_px, real_w + 4) if disp else 0
        if role in ("info", "warn") and (stats is not None or raw_stats or "converting" in base.lower()):
            prev_hi = min(max_status_px, getattr(self, "_run_status_min_w", 0))
            target_label_w = min(max_status_px, max(raw_target_w, min(prev_hi, raw_target_w + 16)))
            self._run_status_min_w = target_label_w
        else:
            target_label_w = raw_target_w

        self.status_label.setMinimumWidth(target_label_w)
        self.status_label.setText(disp)

        latest_res = getattr(self, "_latest_resource_text", "")
        combined_tip = f"{full_tip}\n{latest_res}".strip() if latest_res else full_tip
        tip_text = combined_tip if (combined_tip != disp or bool(stats or raw_stats or latest_res)) else ""
        self.status_label.setToolTip(tip_text)
        if hasattr(self, "pbar"):
            self.pbar.setToolTip(tip_text)
        if hasattr(self, "pbar_row"):
            self.pbar_row.activate()

    def set_state(self, kind, text):
        old_height = self.height()
        role = {"idle": "muted", "run": "info", "paused": "warn", "done": "good", "error": "bad"}[kind]
        role_changed = self.status_label.property("role") != role
        self._base_status_text = str(text or "")
        if kind in ("idle", "done", "error"):
            self._latest_progress_stats = None
            self._raw_speed_stats_text = ""
            self._latest_resource_text = ""
            self._run_status_min_w = 0
            if hasattr(self, "pbar"):
                self.pbar.setToolTip("")
        if role_changed:
            set_role(self.status_label, role)
        self._update_inline_progress_status()
        if role_changed and getattr(self, "preview_card", None) is not None:
            self._clamp_preview_layout(old_height, self.preview_card.height())

    # ---- output folder / completion notifications ----------------------
    def _current_output_path(self):
        """Best-guess path to the file conversion is writing (or last
        wrote) to - the resumed-run final destination if a resume chain
        is active, otherwise whatever's in the Destination field. Used
        by both the "Open output folder" button and the tray
        notification's click action, so they always agree."""
        p = self._resume_coordinator.final_destination or (self._run_context.destination if self.proc is not None and self._run_context.destination
                                       else (Path(self.dst_edit.text()) if self.dst_edit.text() else None))
        return p

    def _open_output_folder(self):
        p = self._current_output_path()
        folder = p.parent if p else self.output_folder
        if not folder or not Path(folder).is_dir():
            QMessageBox.information(self, "Output folder", "No output folder to open yet \u2014 choose a destination first.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _notify_completion(self, ok, message):
        """Cross-platform "conversion finished" notification via the
        system tray balloon/toast. Silently does nothing when no tray is
        available (self._tray is None) - the on-screen status label and
        activity log already cover that case."""
        if self._tray is None:
            return
        icon = QSystemTrayIcon.MessageIcon.Information if ok else QSystemTrayIcon.MessageIcon.Warning
        self._tray.showMessage("HDR to SDR Video Converter", message, icon, 6000)

    # ---- minimize to tray / close-while-converting guard ----------------
    def _themed_message_box(self, icon, title, text, parent=None):
        """A QMessageBox pre-styled to match the app's current theme,
        instead of falling back to the OS's plain default dialog look.
        QMessageBox is a genuinely separate top-level window - it doesn't
        automatically inherit self's own stylesheet the way the app's own
        child widgets do, which is why these were showing up as plain
        white Windows dialogs regardless of the selected theme."""
        box = QMessageBox(parent or self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStyleSheet(build_message_box_qss(self.tokens))
        return box

    # Shared between MainWindow's own closeEvent() and MiniModeWindow's
    # (self.engine is the same MainWindow instance either way, and
    # self.proc is the one shared conversion process regardless of which
    # of the two windows started it) - so both windows warn the same way
    # instead of one silently killing an in-progress conversion.
    def _confirm_close_while_converting(self, parent_window):
        """Returns 'quit', 'tray', or 'cancel'. Skips the dialog (returns
        'quit' immediately) when nothing is actually converting."""
        if not self._is_proc_running():
            return "quit"
        box = self._themed_message_box(
            QMessageBox.Icon.Warning, self.tr("Conversion in progress"),
            self.tr(
                "A conversion is still running. Closing now stops it right where it is - "
                "the output file will be incomplete, and (unlike Stop mid-session) there's "
                "no resuming it once the app has fully quit.\n\n"
                "Minimize to the system tray instead to let it keep converting in the "
                "background."),
            parent=parent_window)
        tray_btn = box.addButton(self.tr("Minimize to tray"), QMessageBox.ButtonRole.ActionRole)
        quit_btn = box.addButton(self.tr("Stop and quit"), QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        if self._tray is None:
            tray_btn.setEnabled(False)
            tray_btn.setToolTip(self.tr("No system tray available on this system."))
        box.setDefaultButton(cancel_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is tray_btn:
            return "tray"
        if clicked is quit_btn:
            return "quit"
        return "cancel"

    def _minimize_to_tray(self, window):
        window.hide()
        self._tray_restore_target = window
        if self._tray is not None:
            self._tray.showMessage(
                self.tr("HDR to SDR Video Converter"),
                self.tr("Still converting in the background \u2014 click the tray icon to reopen."),
                QSystemTrayIcon.MessageIcon.Information, 4000)

    def _build_tray_menu(self):
        menu = QMenu()
        # Frameless + translucent so the rounded corners the stylesheet
        # draws aren't sitting on a square opaque window (same idea as
        # the dropdown popups).
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._tray_status_action = menu.addAction("")
        self._tray_status_action.setEnabled(False)
        menu.addSeparator()
        menu.addAction(self.tr("Show window"), self._restore_from_tray)
        self._tray_mini_action = menu.addAction(self.tr("Mini mode"), self._mini_from_tray)
        menu.addSeparator()
        self._tray_pause_action = menu.addAction(self.tr("Pause"), self.toggle_pause)
        self._tray_stop_action = menu.addAction(self.tr("Stop"), self.stop)
        menu.addSeparator()
        menu.addAction(self.tr("Open output folder"), self._open_output_folder)
        menu.addAction(self.tr("Check for updates"), self.check_for_updates)
        menu.addSeparator()
        menu.addAction(self.tr("Quit"), self._quit_from_tray)
        menu.aboutToShow.connect(self._refresh_tray_menu)
        self._tray_menu = menu
        return menu

    def _refresh_tray_menu(self):
        running = self._is_proc_running()
        status = self.status_label.text().strip() or self.tr("Ready")
        if len(status) > 38:
            status = status[:37] + "\u2026"
        self._tray_status_action.setText(status)
        self._tray_pause_action.setText(self.tr("Resume") if self.paused else self.tr("Pause"))
        self._tray_pause_action.setEnabled(running)
        self._tray_stop_action.setEnabled(running)
        mini = getattr(self, "_mini_window", None)
        self._tray_mini_action.setEnabled(mini is None or not mini.isVisible())   # already in Mini mode

    def _style_tray_menu(self):
        menu = getattr(self, "_tray_menu", None)
        if menu is None:
            return
        menu.setStyleSheet(build_tray_menu_qss(self.tokens))

    def _restore_from_tray(self):
        window = self._tray_restore_target or self
        self._tray_restore_target = None
        window.show()
        window.raise_()
        window.activateWindow()

    def _mini_from_tray(self):
        """Tray -> "Mini mode": works whether the full window is on screen or
        was hidden to the tray (a running conversion carries on either way)."""
        self._tray_restore_target = None
        self._switch_to_mini()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _quit_from_tray(self):
        # The tray menu's "Quit" is a deliberate, explicit exit request -
        # skip the "still converting" prompt's tray option (there's no
        # point offering to minimize to tray from the tray menu itself)
        # but still warn if something's running, so this isn't a silent
        # way to kill a conversion the close-dialog would have warned
        # about.
        if self._is_proc_running():
            box = self._themed_message_box(
                QMessageBox.Icon.Warning, self.tr("Conversion in progress"),
                self.tr("A conversion is still running - quitting now stops it "
                        "with an incomplete output file. Quit anyway?"))
            yes_btn = box.addButton(QMessageBox.StandardButton.Yes)
            box.addButton(QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            box.exec()
            if box.clickedButton() is not yes_btn:
                return
            self.stopping = True
        # Route explicit tray exit through closeEvent so settings, child
        # processes, temporary clips, and power requests receive the same
        # cleanup as a normal window close. The confirmation above already
        # handled the running-process prompt, so closeEvent must not repeat it.
        self._tray_quit_confirmed = True
        self.close()

    # ---- conversion history (JSON log next to the QSettings file) ------
    def _history_file_path(self):
        """Directory next to where this app's settings live, as a plain
        JSON array file, one entry per finished conversion. Explicitly
        asks QSettings for IniFormat rather than reusing the app's real
        QSettings(...) instance as-is: on Windows that instance defaults
        to the registry (NativeFormat), whose fileName() is a registry
        key path, not anything on disk - IniFormat guarantees an actual
        per-user config file/directory to sit next to on every OS, even
        though the app's own settings are stored in the registry there."""
        settings_path = Path(QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope,
            "DevGodys", "HDR to SDR Video Converter").fileName())
        return settings_path.parent / "conversion_history.json"

    def _append_history_entry(self, source, destination, status, kind):
        self._history_store.append(
            source, destination, status, kind,
            settings=dict(self._run_context.settings))

    def _show_history_dialog(self):
        dialog = ConversionHistoryDialog(
            self._history_store.load(), self.tr, self._history_store.clear, self)
        dialog.exec()

    def _is_proc_running(self) -> bool:
        proc = getattr(self, "proc", None)
        if proc is None:
            return False
        if not is_valid_qobject(proc):
            self.proc = None
            return False
        try:
            return proc.state() == QProcess.ProcessState.Running
        except RuntimeError:
            self.proc = None
            return False
        except AttributeError:
            return True

    def _clear_loaded_source_when_idle(self):
        """Reset loaded source/destination fields and placeholders when the visible queue is empty and idle."""
        if self._is_proc_running() or getattr(self._queue_controller, "running", False):
            return
        for worker in tuple(getattr(self, "_analysis_workers", {}).values()):
            try:
                worker.requestInterruption()
            except RuntimeError:
                pass
        self._analysis_generation = getattr(self, "_analysis_generation", 0) + 1
        self._analysis_start_pending = False
        if hasattr(self, "_frame_preview_timer"):
            self._frame_preview_timer.stop()
        old_pv, self._frame_preview_proc = getattr(self, "_frame_preview_proc", None), None
        self._frame_preview_gen = getattr(self, "_frame_preview_gen", 0) + 1
        if is_valid_qobject(old_pv):
            try:
                if old_pv.state() != QProcess.ProcessState.NotRunning:
                    old_pv.kill()
                old_pv.deleteLater()
            except RuntimeError:
                pass
        self.src_edit.clear()
        self.dst_edit.clear()
        self.kind = "unknown"
        self.duration = 0.0
        self.src_width = None
        self.src_height = None
        self._autocrop_value = None
        self._dv_profile = None
        self._src_color_transfer = ""
        self._src_color_primaries = ""
        self._src_color_matrix = ""
        self._src_color_range = ""
        self._job_tonemap_override = None
        self._job_dv_rpu_override = None
        self._job_tonemap_fallback_attempts = 0
        self._gpu_tonemap_fallback_used = False
        self._fallback_retry_pending = False
        self._tonemap_fallback_declined_for_current = False
        self._dv_rpu_user_override = False
        self._dv_rpu_auto_enabled = False
        if hasattr(self, "dv_rpu_chk"):
            self.dv_rpu_chk.setChecked(False)
        self._last_analysis_raw = None
        self._last_analysis_doc = None
        self._run_context = RunContext()
        for button in (self.preset_optimal_btn, self.preset_best_btn, self.preset_fast_btn):
            if button is not None:
                button.setEnabled(False)
        self._sync_preset_segment_state()
        self.analysis_label.setToolTip("")
        self.analysis_label.setText(self.tr("Select a video to analyze it automatically."))
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(self.tr("Choose a source video to see an instant preview of the current settings."))
        self._set_preview_meta("")
        if hasattr(self, "preview_slider"):
            self.preview_slider.setEnabled(False)
            self.preview_slider.setRange(0, 0)
            self.preview_slider.setValue(0)
        elif hasattr(self, "preview_timeline"):
            self.preview_timeline.setEnabled(False)
            self.preview_timeline.setRange(0, 0)
        if hasattr(self, "preview_position_label"):
            self.preview_position_label.setText("0:00")
        is_same_file = False
        try:
            if x and self.src_edit.text() and Path(x).is_file() and Path(self.src_edit.text()).is_file():
                is_same_file = os.path.normcase(os.path.abspath(x)) == os.path.normcase(os.path.abspath(self.src_edit.text()))
        except Exception:
            pass
        if not is_same_file:
            self._stop_frame_strip()
        if hasattr(self, "preview_duration_label"):
            self.preview_duration_label.setText(self.tr("Step: {span} s", span=0))
        if hasattr(self, "update_bitrate_estimate"):
            self.update_bitrate_estimate()

    def update_run_controls(self):
        if self.proc is not None and not is_valid_qobject(self.proc):
            self.proc = None
        running = self.proc is not None
        self.go_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        set_role(self.stop_btn, "stop-ready" if running else "panel-toggle")
        if self.paused:
            self.pause_btn.setProperty("i18n_text", "Resume")
            self.pause_btn.setText(self.tr("Resume"))
            set_role(self.pause_btn, "resume-ready" if running else "panel-toggle")
        else:
            self.pause_btn.setProperty("i18n_text", "Pause")
            self.pause_btn.setText(self.tr("Pause"))
            set_role(self.pause_btn, "pause-ready" if running else "panel-toggle")
        self.pause_btn.setEnabled(running)
        if hasattr(self, "run_stack"):
            self.run_stack.setCurrentIndex(1 if running else 0)
        if hasattr(self, "queue_list") and self.queue_list.count() == 0:
            self._update_queue_hint()

    # ---- ffmpeg capability check -------------------------------------
    def check(self):
        self.encoders = set()
        self.has_bt2390 = False
        self.has_st2094 = False
        self.ffmpeg_version = None
        try:
            f = exe("ffmpeg")
            if not f:
                self.set_state("error", "FFmpeg not found.")
                return
            # (not subprocess.check_output: that freezes the window - see ui/probe.py)
            version_output = capture_text([f, "-hide_banner", "-version"], timeout=30, merge_stderr=True)
            version_match = re.search(r"ffmpeg version n?(\d+)\.(\d+)", version_output, re.IGNORECASE)
            if version_match:
                self.ffmpeg_version = tuple(map(int, version_match.groups()))
            filters = capture_text([f, "-hide_banner", "-filters"], timeout=30, merge_stderr=True).lower()
            helptext = capture_text([f, "-hide_banner", "-h", "filter=libplacebo"], timeout=30,
                                    merge_stderr=True).lower() if "libplacebo" in filters else ""
            self.has_bt2390 = "libplacebo" in filters and "bt.2390" in helptext
            self.has_st2094 = self.has_bt2390 and "st2094-40" in helptext
            enc = capture_text([f, "-hide_banner", "-encoders"], timeout=30, merge_stderr=True)
            self.encoders = set(enc.split())
            self.refresh_method_choices(force_default=True)
            self.set_state("idle", "Ready")
        except (OSError, subprocess.CalledProcessError) as e:       # ProbeError is an OSError
            self.set_state("error", f"FFmpeg capability check failed: {e}")
        finally:
            self._update_ffmpeg_version_note()
            self._check_handbrake_encoders()
            self.refresh_caps_summary()
        # Re-run bit-depth filtering now that self.encoders/self.hb_encoders
        # reflect this machine's real capabilities, not the build()-time
        # "nothing detected yet" fallback - same reasoning as
        # refresh_method_choices(force_default=True) above, applied to the
        # Encoder combo's bit-depth-filtered list instead of Tone mapping.
        self._on_bit_depth_changed(self.bit_depth_combo.currentText())

    def _update_ffmpeg_version_note(self):
        """Show a concise, actionable warning only for versions with the
        older colour-range handling.  A missing/unparseable version should
        not look like a failure: the ordinary capability check already
        reports a genuinely unusable FFmpeg."""
        if not hasattr(self, "ffmpeg_version_note"):
            return
        if self.ffmpeg_version and self.ffmpeg_version < (7, 1):
            version = ".".join(map(str, self.ffmpeg_version))
            self.ffmpeg_version_note.setText(
                f"FFmpeg {version} detected — update to 7.1 or newer for more reliable full/limited colour-range handling.")
            self.ffmpeg_version_note.show()
        else:
            self.ffmpeg_version_note.clear()
            self.ffmpeg_version_note.hide()

    def _check_handbrake_encoders(self):
        """Parse `HandBrakeCLI --help`'s encoder list the same way `check()`
        parses `ffmpeg -encoders` above. self.hb_encoders is left empty (not
        populated with a guess) if HandBrakeCLI is missing or the parse
        fails - callers treat an empty set as "detection unavailable" and
        fall back to their static compatibility tables rather than treating
        it as "nothing is supported"."""
        self.hb_encoders = set()
        if not self.handbrake_tool:
            return
        try:
            out = capture_text([self.handbrake_tool, "--help"], timeout=15, merge_stderr=True)
            m = _HB_ENCODER_LIST.search(out)
            if m:
                self.hb_encoders = set(m.group(1).split())
        except Exception:
            log_suppressed("main_window")

    def refresh_method_choices(self, force_default=False):
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        values = []
        # GPU/libplacebo curves only exist on the FFmpeg backend - HandBrake's
        # own --colorspace tonemap= option wraps FFmpeg's classic CPU `tonemap`
        # avfilter, not libplacebo, so it can never offer these regardless of
        # what this machine's FFmpeg build supports.
        if not using_hb and self.has_bt2390:
            values.append("BT.2390 \u00b7 GPU tonemap")
            values.append("Spline \u00b7 GPU tonemap")
            values += list(TONEMAP_PRO_GPU)
            if self.has_st2094:
                values += list(TONEMAP_PRO_GPU_ST2094)
        values += ["Hable \u00b7 CPU tonemap", "Reinhard \u00b7 CPU tonemap", "Mobius \u00b7 CPU tonemap"]
        values += list(TONEMAP_PRO_CPU)
        # force_default=True is for the one call in check() right after real
        # capability detection finishes: the combo was seeded with the CPU-
        # only fallback list before FFmpeg was even probed (see __init__, to
        # avoid the startup resize jump), so its "current" selection at that
        # point is just that placeholder default, not something the user
        # actually chose - BT.2390 becoming available should promote it to
        # selected rather than being silently skipped because the fallback
        # happens to still be a valid item. Every other call site (backend
        # switch) leaves this False, so an actual user
        # selection is preserved whenever it's still valid.
        current = None if force_default else self.method_combo.currentText()
        self.method_combo.blockSignals(True)
        self.method_combo.clear()
        self.method_combo.addItems(values)
        for i, v in enumerate(values):
            info = TONEMAP_INFO.get(v)
            if info:
                self.method_combo.setItemData(i, self.tr(info), Qt.ItemDataRole.ToolTipRole)
        self.method_combo.blockSignals(False)
        self.method_combo.setToolTip(self.tr(TONEMAP_INFO.get(self.method_combo.currentText(), "")))
        if current in values:
            self.method_combo.setCurrentText(current)
        elif values:
            self.method_combo.setCurrentIndex(0)
        # setCurrentText()/setCurrentIndex() above are no-ops - and emit no
        # currentTextChanged/currentIndexChanged - whenever the combo
        # already happens to land on that same text/index after clear()+
        # addItems() (e.g. the very first real population, right after
        # check() detects capabilities: the combo was empty, so index 0
        # is both the "before" and "after" state). dv_rpu_chk/peak_detect_chk
        # availability is wired to that signal (see below), so relying on
        # it alone left them stuck on whatever they were computed as at
        # widget-construction time (combo still empty -> always disabled)
        # until the user's *next* real selection change happened to fire
        # it - which is exactly what looked like "just touching Tone
        # mapping turns them on". Calling this directly, unconditionally,
        # keeps it in sync with the combo's actual final state regardless
        # of whether Qt considered anything to have "changed".
        if hasattr(self, "dv_rpu_chk"):
            self._update_libplacebo_toggle_availability()

    # ---- which encoder labels can actually produce a given bit depth ----
    def _allowed_encoder_labels(self, depth_label):
        """Encoder labels usable at `depth_label` with whichever backend is
        currently selected: structurally correct (right encoder family for
        that depth) AND, once capability detection has run, actually present
        in this machine's FFmpeg/HandBrakeCLI build. Falls back to the
        structural table alone if detection hasn't completed yet or came
        back empty, so a detection hiccup doesn't empty the dropdown."""
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        if using_hb:
            structural = {lbl for lbl, depths in HB_ENCODER_IDS.items() if depth_label in depths}
            if not self.hb_encoders:
                return structural
            return {lbl for lbl in structural if HB_ENCODER_IDS[lbl][depth_label] in self.hb_encoders}
        else:
            _, structural = FFMPEG_BIT_DEPTH.get(depth_label, (None, set()))
            if not self.encoders:
                return structural
            return {lbl for lbl in structural if ENCODER_MAP[lbl] in self.encoders}

    def _on_bit_depth_changed(self, label: str):
        allowed = self._allowed_encoder_labels(label)
        current = self.encoder_combo.currentText()
        self.encoder_combo.blockSignals(True)
        self.encoder_combo.clear()
        self.encoder_combo.addItems([e for e in ENCODER_MAP if e in allowed])
        if current in allowed:
            self.encoder_combo.setCurrentText(current)
        self.encoder_combo.blockSignals(False)
        # blockSignals above means a switch into/out of CPU AV1 caused by
        # this repopulation (e.g. the previous encoder isn't valid at the
        # new bit depth, so Qt silently lands on a different one) wouldn't
        # otherwise reach _update_quality_range via currentTextChanged.
        if hasattr(self, "quality_spin"):
            self._update_quality_range()

    def refresh_caps_summary(self):
        def yn(ok):
            return "\u2713" if ok else "\u2717"
        lines = [
            f"{yn(bool(exe('ffmpeg')))} FFmpeg \u2014 {wrappable(exe('ffmpeg')) if exe('ffmpeg') else 'not found, required for the FFmpeg backend'}",
            f"{yn(self.has_bt2390)} BT.2390 GPU tonemap (libplacebo/Vulkan)"
            + ("" if self.has_bt2390 else " \u2014 unavailable, Hable/Reinhard/Mobius still work"),
            "",
            "FFmpeg encoders:",
        ]
        for label, name in (("CPU H.264", "libx264"), ("CPU H.265", "libx265"),
                             ("Intel QSV H.264", "h264_qsv"), ("Intel QSV H.265", "hevc_qsv"),
                             ("NVIDIA NVENC H.264", "h264_nvenc"), ("NVIDIA NVENC H.265", "hevc_nvenc"),
                             ("AMD AMF H.264", "h264_amf"), ("AMD AMF H.265", "hevc_amf"),
                             ("Apple VideoToolbox H.264", "h264_videotoolbox"),
                             ("Apple VideoToolbox H.265", "hevc_videotoolbox"),
                             ("CPU AV1 (SVT-AV1)", "libsvtav1"),
                             ("NVIDIA NVENC AV1", "av1_nvenc"), ("AMD AMF AV1", "av1_amf")):
            lines.append(f"   {yn(name in self.encoders)} {label}")
        if self.handbrake_tool:
            hb_note = (f" \u2014 {len(self.hb_encoders)} encoder(s) detected" if self.hb_encoders
                       else " \u2014 encoder list could not be detected; availability isn't pre-checked")
        else:
            hb_note = " \u2014 not found on PATH"
        lines += [
            "",
            f"{yn(bool(self.handbrake_tool))} HandBrakeCLI"
            + (f" \u2014 {wrappable(self.handbrake_tool)}{hb_note}" if self.handbrake_tool else hb_note),
            f"{yn(HAVE_PSUTIL)} psutil (Pause/Resume, live CPU-core/priority control)"
            + ("" if HAVE_PSUTIL else " \u2014 install with: pip install psutil"),
            f"{yn(bool(self.gpu_tool))} nvidia-smi (live GPU utilization stats)"
            + ("" if self.gpu_tool else " \u2014 not found, GPU stats will be unavailable"),
        ]
        self.caps_summary = "\n".join(lines)

    # ---- one-click dependency setup (Windows / winget) -----------------
    def check_for_updates(self):
        """Single entry point for the "Check for updates" button: reports
        whether a newer app release exists, installs FFmpeg/HandBrakeCLI
        if either is missing, or - if both are already present - offers
        to update either or both to their latest portable build. Folding
        all three checks (app, FFmpeg, HandBrakeCLI) into one click means
        a Windows user only has one button to remember to press."""
        if self.proc is not None or getattr(self, "_setup_proc", None) is not None:
            QMessageBox.information(self, "Busy", "Finish or stop the current conversion/setup first.")
            return
        self._report_app_update_status()
        if not exe("ffmpeg") or not self.handbrake_tool:
            self.start_dependency_setup()
            return
        self._show_update_choice()

    def _set_install_btn_enabled(self, enabled: bool):
        btn = getattr(self, "install_btn", None)
        if btn is not None:
            try:
                btn.setEnabled(bool(enabled))
            except RuntimeError:
                self.install_btn = None

    def _show_update_choice(self):
        """"Already installed" question as a card inside the window (dimmed
        backdrop, rounded like the dropdowns) instead of a native message
        box with the OS title bar. A portable copy is deliberately updated in
        the app's own folder: it never overwrites a user-managed system
        FFmpeg/HandBrakeCLI installation, whether that install originally
        came from winget or from this same portable-download path."""
        old = getattr(self, "update_panel", None)
        if old is not None:
            try:
                old.hide()
                old.setParent(None)
                old.deleteLater()
            except RuntimeError:
                pass
        dlg = getattr(self, "_last_theme_dialog", None)
        host = dlg if (dlg is not None and dlg.isVisible()) else self
        panel = OverlayPanel(host, modal=True)
        t = self.tokens
        panel.set_theme(t['bg_card'], t['border'])
        panel.add_title(self.tr("Already installed"))
        text = QLabel(self.tr("FFmpeg and HandBrakeCLI are already installed. Update either one?"))
        text.setWordWrap(True)
        set_role(text, "muted")
        panel.body.addWidget(text)
        panel.body.addSpacing(4)

        def add(label, role, action):
            b = GlassButton(label)
            if role:
                set_role(b, role)
            b.setMinimumHeight(34)
            b.clicked.connect(lambda: (panel.hide(), action()))
            panel.body.addWidget(b)
            return b

        add(self.tr("Update both"), "go",
            lambda: self._start_portable_dependency_setup(force_ffmpeg=True, force_handbrake=True))
        add(self.tr("Update FFmpeg"), None, lambda: self._start_portable_dependency_setup(force_ffmpeg=True))
        add(self.tr("Update HandBrakeCLI"), None,
            lambda: self._start_portable_dependency_setup(force_handbrake=True))
        add(self.tr("Not now"), "panel-toggle", lambda: None)
        self.update_panel = panel
        panel.open_centered(width=380)

    def _report_app_update_status(self):
        """Checks the app's own GitHub releases page (see
        fetch_latest_github_tag - a plain redirect, not the rate-limited
        API) and writes the result to the Activity log; pops a dialog
        only when there's actually a newer tagged release to point at,
        so routine "you're current" / "can't reach GitHub" checks don't
        interrupt anything. This app has no auto-update mechanism - this
        is purely informational, linking to the Releases page to update
        by hand."""
        releases_url = f"https://github.com/{UPDATE_CHECK_REPO}/releases/latest"
        tag = fetch_latest_github_tag(UPDATE_CHECK_REPO)
        if tag is None:
            self.write("[update check] Could not reach GitHub to check for app updates.\n")
        elif APP_VERSION is None:
            self.write(f"[update check] Running an unreleased development build \u2014 "
                       f"latest published release is {tag}: {releases_url}\n")
        elif _tag_is_newer(tag, APP_VERSION):
            self.write(f"[update check] A newer version is available: {tag} "
                       f"(you have {APP_VERSION}). {releases_url}\n")
            QMessageBox.information(
                self, "Update available",
                f"Version {tag} is available \u2014 you're on {APP_VERSION}.\n\n"
                f"Download it from the Releases page:\n{releases_url}")
        else:
            self.write(f"[update check] The app is up to date ({APP_VERSION}).\n")

    def start_dependency_setup(self):
        if self.proc is not None or getattr(self, "_setup_proc", None) is not None:
            QMessageBox.information(self, "Busy", "Finish or stop the current conversion/setup first.")
            return
        winget = self._find_winget()
        if not winget:
            self._start_portable_dependency_setup(winget_missing=True)
            return
        todo = []
        if not exe("ffmpeg"):
            todo.append(("Gyan.FFmpeg", "FFmpeg"))
        if not self.handbrake_tool:
            todo.append(("HandBrake.HandBrake.CLI", "HandBrakeCLI"))
        if not todo:
            QMessageBox.information(self, "Nothing to install", "FFmpeg and HandBrakeCLI are already installed.")
            return
        self._setup_queue = todo
        self._set_install_btn_enabled(False)
        if self.side_tab != "activity":
            self.toggle_activity()
        self.write(f"\n[setup] Installing {len(todo)} package(s) via winget \u2014 a Windows permission "
                    "prompt may appear; approve it to continue.\n")
        self._run_next_setup_item()

    def _run_next_setup_item(self):
        if not self._setup_queue:
            self.write("[setup] Done. Re-checking capabilities\u2026\n")
            refresh_windows_path()
            self.handbrake_tool = exe("HandBrakeCLI")
            self.check()
            self._set_install_btn_enabled(True)
            QMessageBox.information(self, "Setup complete", "Dependency installation finished \u2014 see Activity log for details.")
            return
        pkg_id, name = self._setup_queue.pop(0)
        self.write(f"[setup] Installing {name} ({pkg_id})\u2026\n")
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.readyReadStandardOutput.connect(lambda p=p: self.write(
            bytes(p.readAllStandardOutput()).decode("utf-8", errors="replace")))
        p.finished.connect(lambda code, status, name=name: self._on_setup_item_finished(code, name))
        p.errorOccurred.connect(lambda err, name=name: self._on_setup_item_error(name))
        p.setProgram(self._find_winget() or "winget")
        p.setArguments(["install", "--id", pkg_id, "-e", "--silent",
                         "--accept-package-agreements", "--accept-source-agreements"])
        self._setup_proc = p
        p.start()

    def _on_setup_item_finished(self, code, name):
        if code == 0:
            self.write(f"[setup] {name} installed.\n")
        else:
            self.write(f"[setup] {name} install exited with code {code} \u2014 see output above.\n")
        refresh_windows_path()
        self._setup_proc = None
        self._run_next_setup_item()

    def _on_setup_item_error(self, name):
        self.write(f"[setup] Failed to launch winget for {name}.\n")
        self._setup_proc = None
        self._set_install_btn_enabled(True)

    def _find_winget(self):
        """Find App Installer's executable even when WindowsApps is absent from PATH."""
        candidates = [shutil.which("winget")]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(str(Path(local_app_data) / "Microsoft" / "WindowsApps" / "winget.exe"))
        return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)

    def _start_portable_dependency_setup(self, force_ffmpeg=False, force_handbrake=False, winget_missing=False):
        """Download official portable tools when Windows Package Manager is unavailable."""
        if self.proc is not None or getattr(self, "_setup_proc", None) is not None:
            QMessageBox.information(
                self, "Busy", "Finish or stop the current conversion/setup first.")
            return
        todo = []
        if force_ffmpeg or not exe("ffmpeg"):
            todo.append("FFmpeg")
        if force_handbrake or not self.handbrake_tool:
            todo.append("HandBrakeCLI")
        if not todo:
            QMessageBox.information(self, "Nothing to install", "FFmpeg and HandBrakeCLI are already installed.")
            return

        self._set_install_btn_enabled(False)
        if self.side_tab != "activity":
            self.toggle_activity()
        # The portable build is the deliberate choice, not just a fallback:
        # it's the one compiled with libplacebo/Vulkan (GPU tone mapping),
        # which winget's FFmpeg package lacks. So only blame winget when
        # it was actually looked for and not found.
        channel = self._tools_channel()
        if winget_missing:
            self.write("\n[setup] winget is unavailable. Downloading portable " + " and ".join(todo)
                       + f" ({channel} channel, SHA-256 verified) to your local app-data folder\u2026\n")
        else:
            self.write("\n[setup] Downloading portable " + " and ".join(todo)
                       + f" ({channel} channel, SHA-256 verified) to your local app-data folder\u2026\n")
        thread = ToolInstallThread(todo, channel, PORTABLE_TOOLS_DIR, self)
        thread.log.connect(self.write)
        thread.done.connect(self._on_portable_setup_finished)
        self._setup_proc = thread
        thread.start()

    def _on_portable_setup_finished(self, ok):
        thread, self._setup_proc = self._setup_proc, None
        if thread is not None:
            thread.wait(2000)
            thread.deleteLater()
        self._set_install_btn_enabled(True)
        if not ok:
            self.write("[setup] Portable setup did not complete - see the messages above. "
                       "Nothing unverified was installed.\n")
            return
        self.handbrake_tool = exe("HandBrakeCLI")
        self.check()
        QMessageBox.information(self, "Setup complete", "Portable dependency setup finished \u2014 see Activity log for details.")

    # ---- tool channel (stable / latest) ---------------------------
    def _tools_channel(self):
        value = self.i18n.settings.value("tools_channel", tools_runtime.STABLE)
        return value if value in tools_runtime.CHANNELS else tools_runtime.STABLE

    def _on_tools_channel_picked(self, index):
        channels = (tools_runtime.STABLE, tools_runtime.LATEST)
        if 0 <= index < len(channels):
            self.i18n.settings.setValue("tools_channel", channels[index])
            self._retranslate_tools_channel()

    def _retranslate_tools_channel(self):
        btn = getattr(self, "tools_channel_btn", None)
        if btn is None:
            return
        try:
            labels = [self.tr("Tools: stable (pinned, verified)"), self.tr("Tools: latest (newest builds)")]
            current = (tools_runtime.STABLE, tools_runtime.LATEST).index(self._tools_channel())
            btn.set_items(labels, current)
            btn.setText(labels[current])
            btn.setToolTip(self.tr(
                "Download channel for FFmpeg / HandBrakeCLI:\n"
                "• Stable: verified release.\n"
                "• Latest: newest upstream build."))
        except RuntimeError:
            self.tools_channel_btn = None

    # ---- workflow helpers: profiles, history, queue -----------------
    def _add_files_to_queue(self, paths, trust_extension=False):
        """`trust_extension=True` for files the person picked by hand in the
        file dialog: whatever extension they have, they meant to add them
        (the analysis step reports it if FFmpeg can't read one). Drag &
        drop keeps filtering by VIDEO_EXTS so a stray non-video file
        dropped on the window isn't queued."""
        accepted = QueueController.accepted_files(paths, self.VIDEO_EXTS, trust_extension)
        added = 0
        history_notices = []
        for path in accepted:
            if self._queue_controller.add(path):
                self.queue_list.addItem(Path(path).name)
                it = self.queue_list.item(self.queue_list.count() - 1)
                it.setToolTip(path)
                w = self.queue_list.itemWidget(it)
                if isinstance(w, _QueueItemWidget):
                    w.setToolTip(path)
                    w.update_text(Path(path).name)
                added += 1
                previous = self._history_store.for_source(path)
                if previous:
                    history_notices.append((path, previous[-1]))
        if added and not Path(self.src_edit.text()).is_file():
            # Adding files must be instantaneous. ffprobe analysis is useful
            # only for the video that is about to encode, so defer it until
            # Start instead of blocking the UI on every Add videos click.
            self._set_source(self._queue_controller.paths[0], analyze=True)
        if added:
            self.write(f"[queue] Added {added} video(s).\n")
        for path, previous in history_notices:
            old_settings = previous.get("settings") or {}
            encoder = old_settings.get("encoder_label", "")
            backend = old_settings.get("backend", "")
            bit_depth = old_settings.get("bit_depth_label", "")
            settings_note = " using " + " / ".join(
                value for value in (backend, encoder, bit_depth) if value) if any(
                    (backend, encoder, bit_depth)) else ""
            destination = previous.get("destination")
            result_note = f"; output: {destination}" if destination else ""
            self.write(
                f"[history] {Path(path).name} was previously {previous.get('status', 'processed')} "
                f"on {previous.get('timestamp', 'an earlier date')}{settings_note}{result_note}. "
                "Right-click Output to review conversion history.\n")

    def analyze_selected_queue_video(self):
        if self.proc is not None:            # the run reads duration/kind while it works
            self.status_label.setText("Wait for the running conversion to finish first")
            return
        row = self.queue_list.currentRow()
        if row >= 0 and row < len(self._queue_controller.paths):
            self._set_source(self._queue_controller.paths[row], analyze=False)
        if not Path(self.src_edit.text()).is_file():
            QMessageBox.information(self, "Analyze", "Add a video to the queue, then select it first.")
            return
        self.analyze()

    def _on_queue_row_selected(self, row):
        """Load and analyze a newly selected queue item without racing a run."""
        if (row < 0
                or getattr(self, "_suppress_queue_selection_analysis", False)
                or getattr(self._queue_controller, "running", False)
                or getattr(self, "_start_in_progress", False)
                or self._is_proc_running()):
            return
        self._queue_controller.sync_with_visible(self.queue_list.count())
        if row >= len(self._queue_controller.paths):
            return
        path = self._queue_controller.paths[row]
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.src_edit.text())):
            if self.kind == "unknown" and not any(
                    worker.generation == self._analysis_generation and worker.isRunning()
                    for worker in self._analysis_workers.values()):
                self.analyze()
            return
        self._set_source(path, analyze=True)

    def _create_queue_item_widget(self, item: QListWidgetItem) -> _QueueItemWidget:
        return _QueueItemWidget(item, self)

    def _on_queue_rows_inserted(self, parent, first, last):
        for row in range(first, last + 1):
            item = self.queue_list.item(row)
            if item is None or self.queue_list.itemWidget(item) is not None:
                continue
            item.setSizeHint(QSize(0, 24))
            widget = self._create_queue_item_widget(item)
            if item.toolTip():
                widget.setToolTip(item.toolTip())
            self.queue_list.setItemWidget(item, widget)
        self._update_queue_items_selection_style()

    def _update_queue_items_selection_style(self):
        for i in range(self.queue_list.count()):
            it = self.queue_list.item(i)
            if it is None:
                continue
            w = self.queue_list.itemWidget(it)
            if isinstance(w, _QueueItemWidget):
                w.set_selected(it.isSelected())

    def _retranslate_queue_items(self):
        for i in range(self.queue_list.count()):
            it = self.queue_list.item(i)
            if it is None:
                continue
            w = self.queue_list.itemWidget(it)
            if isinstance(w, _QueueItemWidget):
                w.remove_btn.setToolTip(self.tr("Remove"))

    def _on_queue_item_remove_clicked(self, item: QListWidgetItem):
        row = self.queue_list.row(item)
        if row < 0:
            return
        selected_items = self.queue_list.selectedItems()
        if len(selected_items) > 1 and item in selected_items:
            rows = [self.queue_list.row(it) for it in selected_items if self.queue_list.row(it) >= 0]
        else:
            rows = [row]
        self._remove_queue_items_at(rows)

    def _remove_selected_queue_items(self):
        selected_items = self.queue_list.selectedItems()
        if selected_items:
            rows = [self.queue_list.row(it) for it in selected_items if self.queue_list.row(it) >= 0]
        else:
            row = self.queue_list.currentRow()
            if row < 0 and self.queue_list.count() > 0:
                row = 0
            rows = [row] if row >= 0 else []
        if rows:
            self._remove_queue_items_at(rows)

    def _remove_queue_items_at(self, rows: list[int]):
        if not rows:
            return
        sorted_rows = sorted(set(rows), reverse=True)
        min_deleted = min(sorted_rows)
        for r in sorted_rows:
            if 0 <= r < len(self._queue_controller.paths):
                self._queue_controller.remove_at(r)
            if 0 <= r < self.queue_list.count():
                self.queue_list.takeItem(r)

        if self.queue_list.count() > 0:
            next_row = min(min_deleted, self.queue_list.count() - 1)
            self.queue_list.setCurrentRow(next_row)
            if not self._is_proc_running() and not getattr(self._queue_controller, "running", False):
                self._on_queue_row_selected(next_row)
        else:
            self._queue_controller.clear()
            if not self._is_proc_running() and not getattr(self._queue_controller, "running", False):
                self._clear_loaded_source_when_idle()

    def _remove_queue_item(self):
        self._remove_selected_queue_items()

    def _show_queue_context_menu(self, pos: QPoint):
        item = self.queue_list.itemAt(pos)
        selected_items = self.queue_list.selectedItems()
        if item is not None and item not in selected_items:
            self.queue_list.clearSelection()
            item.setSelected(True)
            self.queue_list.setCurrentItem(item)
            selected_items = [item]

        menu = QMenu(self)
        remove_action = menu.addAction(self.tr("Remove"))
        remove_action.setEnabled(len(selected_items) > 0 or self.queue_list.count() > 0)
        remove_action.triggered.connect(self._remove_selected_queue_items)

        menu.addSeparator()
        add_action = menu.addAction(self.tr("Add videos"))
        add_action.triggered.connect(self.browse_source)

        menu.exec(self.queue_list.mapToGlobal(pos))

    def _on_start_clicked(self):
        """CONTROLS' single Start button. With zero or one file queued
        there's nothing to choose between, so this behaves exactly like
        the old "Start queue" button (removed from FILES - see the
        queue_row comment above) always did: run the whole queue in
        order. With two or more files queued it's genuinely ambiguous
        whether "Start" means just the file currently selected in FILES
        or the whole queue, so it asks instead of silently guessing."""
        if self.proc is not None and not is_valid_qobject(self.proc):
            self.proc = None
        self._queue_controller.sync_with_visible(self.queue_list.count())
        if self.queue_list.count() == 0 or not self._queue_controller.paths:
            self._clear_loaded_source_when_idle()
            QMessageBox.information(self, "Queue", "Add one or more source videos first.")
            return
        if len(self._queue_controller.paths) > 1:
            box = QMessageBox(self)
            box.setWindowTitle(self.tr("Start conversion"))
            box.setText(self.tr(
                "Convert only the file selected in the queue, or the whole queue in order?"))
            selected_btn = box.addButton(self.tr("Selected file"), QMessageBox.ButtonRole.AcceptRole)
            queue_btn = box.addButton(self.tr("Whole queue"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(queue_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is selected_btn:
                self._start_selected_file_only()
                return
            if clicked is not queue_btn:
                return  # Cancel, or the dialog was closed without a choice
        self.start_queue()

    def _start_selected_file_only(self):
        """Converts just the row currently selected in the FILES queue
        list, leaving every other queued file untouched - the "Selected
        file" choice from _on_start_clicked() above. Pulled out of
        queue_paths/queue_list the same way _start_next_queue_item() does,
        but for one specific row instead of always index 0, and without
        starting the controller - a single-file run shouldn't auto-advance
        into the rest of the queue afterwards."""
        if self.proc is not None and not is_valid_qobject(self.proc):
            self.proc = None
        if self.proc is not None:
            QMessageBox.information(self, "Conversion running", "Wait for the current conversion to finish first.")
            return
        self._queue_controller.sync_with_visible(self.queue_list.count())
        row = self.queue_list.currentRow()
        if row < 0 or row >= len(self._queue_controller.paths) or row >= self.queue_list.count():
            QMessageBox.information(self, "Queue", "Select a file in the queue first.")
            return
        path = self._queue_controller.take_selected(row)
        self._suppress_queue_selection_analysis = True
        try:
            self.queue_list.takeItem(row)
        finally:
            self._suppress_queue_selection_analysis = False
        self._set_source(path, analyze=False)
        self.write(f"[queue] Starting {Path(path).name} (selected file only).\n")
        QTimer.singleShot(0, self.start)

    def start_queue(self):
        if self.proc is not None and not is_valid_qobject(self.proc):
            self.proc = None
        if self.proc is not None:
            QMessageBox.information(self, "Conversion running", "Wait for the current conversion to finish first.")
            return
        self._queue_controller.sync_with_visible(self.queue_list.count())
        if self.queue_list.count() == 0 or not self._queue_controller.paths:
            self._clear_loaded_source_when_idle()
            QMessageBox.information(self, "Queue", "Add one or more source videos first.")
            return
        self._queue_controller.start()
        self._start_next_queue_item()

    def _start_next_queue_item(self):
        advance = self._queue_controller.advance()
        if advance.action == "finished":
            self.write("[queue] Finished.\n")
            if advance.was_running:
                # Reached only when the queue actually drained on its own
                # (every item succeeded) - a failed item sets
                # stopping the controller itself, in _on_finished/
                # _on_concat_finished, and notifies from there instead,
                # so this branch only ever fires for the success case.
                self._notify_completion(True, "Queue finished \u2014 all files converted.")
                self._all_done()
            return
        self.queue_list.takeItem(0)
        path = advance.path
        if advance.action == "skipped":    # moved/deleted since it was queued
            self.write(f"[queue] Source not found, skipped: {path}\n")
            QTimer.singleShot(0, self._start_next_queue_item)
            return
        self._set_source(path, analyze=False)
        self.write(f"[queue] Starting {Path(path).name}.\n")
        QTimer.singleShot(0, self.start)

    def _confirm_destination(self):
        """Never silently overwrite an existing file: in a queue the output gets a free
        "_1", "_2" ... name (two sources with the same name must not clobber each other); a
        single run asks first."""
        dst = Path(self.dst_edit.text())
        if not dst.exists():
            return True
        try:
            same = dst.samefile(Path(self.src_edit.text()))
        except OSError:
            same = os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(self.src_edit.text()))
        if same:
            self.write("[error] The output file is the same as the source - choose another output name.\n")
            self.status_label.setText("The output file is the same as the source")
            return False
        if self._queue_controller.running:
            candidate = self._queue_controller.free_destination(dst)
            self.write(f"[queue] {dst.name} already exists - writing {candidate.name} instead.\n")
            self.dst_edit.setText(str(candidate))
            return True
        return QMessageBox.question(
            self, "File exists", f"{dst.name} already exists. Overwrite it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _enough_disk_space(self):
        destination = Path(self.dst_edit.text())
        try:
            free = shutil.disk_usage(destination.parent).free
        except OSError:
            return True
        # Prefer duration and estimated bitrate: an input-size heuristic
        # greatly overstates space for remuxes and low-bitrate outputs.
        try:
            source_size = Path(self.src_edit.text()).stat().st_size
        except OSError:
            source_size = 0
        if self.quality_mode_combo.currentText() == "Bitrate":
            bitrate_kbps = self.target_bitrate_spin.value()
        else:
            target_h = RESOLUTIONS.get(self.res_combo.currentText())
            if target_h and self.src_width and self.src_height:
                width = even(round(self.src_width * target_h / self.src_height))
                height = target_h
            else:
                width, height = self.src_width, self.src_height
            bitrate_kbps = estimate_bitrate_kbps(
                self.encoder_combo.currentText(), self.quality_spin.value(), width, height
            )
        expected = estimate_output_bytes(self.duration, bitrate_kbps, source_size)
        if free >= expected:
            return True
        return QMessageBox.question(
            self, "Low disk space",
            f"Only {free / 1024 ** 3:.1f} GB is free; the output may need about {expected / 1024 ** 3:.1f} GB. Continue?") == QMessageBox.StandardButton.Yes

    # ---- file pickers ---------------------------------------------
    def browse_source(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add videos to queue", "", VIDEO_DIALOG_FILTER)
        if paths:
            self._add_files_to_queue(paths, trust_extension=True)

    def _on_container_changed(self, label):
        """Swaps the destination path's extension to match the chosen
        container, but leaves everything else about the path alone -
        including a name the person edited by hand - so switching MP4/MKV
        never silently resets a custom output filename or folder."""
        new_ext = ".mkv" if label == "MKV" else ".mp4"
        current = Path(self.dst_edit.text()) if self.dst_edit.text() else None
        if current is not None and current.suffix.lower() != new_ext:
            self.dst_edit.setText(str(current.with_suffix(new_ext)))

    def _set_source(self, x, analyze=True):
        for worker in tuple(self._analysis_workers.values()):
            worker.requestInterruption()
        self._analysis_generation += 1
        self._analysis_start_pending = False
        self.src_edit.setText(x)
        self._dv_rpu_user_override = False
        self._dv_rpu_auto_enabled = False
        if hasattr(self, "dv_rpu_chk"):
            self.dv_rpu_chk.setChecked(False)
        self._dv_profile = None
        self._src_color_transfer = ""
        self._src_color_primaries = ""
        self._src_color_matrix = ""
        self._src_color_range = ""
        self._job_tonemap_override = None
        self._job_dv_rpu_override = None
        self._job_tonemap_fallback_attempts = 0
        self._gpu_tonemap_fallback_used = False
        self._fallback_retry_pending = False
        self._tonemap_fallback_declined_for_current = False
        self._frame_preview_cpu_fallback = False
        self._last_analysis_doc = None

        # Cancel any in-flight preview and clear stale preview frames
        if hasattr(self, "_frame_preview_timer"):
            self._frame_preview_timer.stop()
        old_pv, self._frame_preview_proc = getattr(self, "_frame_preview_proc", None), None
        if is_valid_qobject(old_pv):
            try:
                if old_pv.state() != QProcess.ProcessState.NotRunning:
                    old_pv.kill()
                old_pv.deleteLater()
            except RuntimeError:
                pass
        self._current_preview_pixmap = None
        if hasattr(self, "preview_label"):
            self.preview_label.setPixmap(QPixmap())
            if x and Path(x).is_file():
                self.preview_label.setText(self.tr("Analyzing source video\u2026"))
            else:
                self.preview_label.setText(self.tr("Choose a source video to see an instant preview of the current settings."))
        if hasattr(self, "preview_meta_label"):
            self._set_preview_meta("")
        if hasattr(self, "preview_expand_btn"):
            self.preview_expand_btn.setEnabled(False)
        if hasattr(self, "preview_save_btn"):
            self.preview_save_btn.setEnabled(False)
        if hasattr(self, "compare_methods_btn"):
            self.compare_methods_btn.setEnabled(bool(x and Path(x).is_file()))

        p = Path(x)
        folder = self.output_folder if self.output_folder and self.output_folder.is_dir() else p.parent
        ext = ".mkv" if getattr(self, "container_combo", None) and self.container_combo.currentText() == "MKV" else ".mp4"
        self.dst_edit.setText(str(folder / (p.stem + "_SDR" + ext)))
        self.kind = "unknown"
        self.duration = 0.0
        self.src_width = None
        self.src_height = None
        self._autocrop_value = None
        # A previous file's scrub position doesn't mean anything on a new,
        # differently-timed source - disable until analyze() knows the new
        # duration and can set a sensible range/default again.
        if hasattr(self, "preview_slider"):
            self.preview_slider.setEnabled(False)
            self.preview_slider.setRange(0, 0)
            self.preview_slider.setValue(0)
        elif hasattr(self, "preview_timeline"):
            self.preview_timeline.setEnabled(False)
            self.preview_timeline.setRange(0, 0)
        if hasattr(self, "preview_position_label"):
            self.preview_position_label.setText("0:00")
        self._stop_frame_strip()
        if hasattr(self, "preview_duration_label"):
            self.preview_duration_label.setText(self.tr("Step: {span} s", span=0))
        self._update_output_folder_btn()
        if hasattr(self, "update_bitrate_estimate"):
            self.update_bitrate_estimate()
        # A test clip already rendered belongs to the previous file too -
        # don't leave "Open test clip" green/enabled for a clip that's no
        # longer the one currently loaded.
        self._test_clip_out = None
        if hasattr(self, "test_clip_open_btn") and self.test_clip_open_btn:
            self.test_clip_open_btn.setEnabled(False)
            set_role(self.test_clip_open_btn, "panel-toggle")
            self.test_clip_open_btn.setToolTip(self.tr(
                "Open test clip (available once rendered)."))
        if analyze:
            self.analyze()

    def _update_output_folder_btn(self):
        btn = getattr(self, "output_folder_btn", None)
        if btn is None:
            return
        btn.setText(self.tr("Output folder"))
        if self.output_folder and self.output_folder.is_dir():
            tip = (f"{self.tr('Current output folder')}: {self.output_folder}\n\n" +
                   self.tr("Choose one output folder for all queued files. Right-click for conversion history."))
        else:
            tip = (f"{self.tr('Current output folder')}: {self.tr('Next to each source video')}\n\n" +
                   self.tr("Output folder for converted files (default: next to source). Right-click for history."))
        btn.setToolTip(tip)

    def browse_output(self):
        initial = str(self.output_folder or (Path(self.src_edit.text()).parent if self.src_edit.text() else Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Choose SDR output folder", initial)
        if folder:
            self.output_folder = Path(folder)
            self._update_output_folder_btn()
            self._conversion_preferences.schedule_save()
            if self.src_edit.text():
                self._set_source(self.src_edit.text(), analyze=False)

    def _output_or_reveal(self):
        """Click handler for the merged Output/History button. Setting an
        output folder ahead of time is still always available (clicking
        just opens the picker) - but if the user never bothered to set
        one and a conversion has already written a real file to the
        default location, jump straight to that file's folder instead of
        making them go looking for it."""
        if not self.output_folder:
            p = self._current_output_path()
            if p and p.is_file():
                self._open_output_folder()
                return
        self.browse_output()

    # ---- HDR metadata extraction --------------------------------------
    @staticmethod
    def _ratio_to_float(value):
        """ffprobe reports several fields (mastering-display luminance,
        chromaticity coordinates) as "num/den" rational strings rather
        than plain numbers - for the ones this app displays (min/max
        mastering luminance) the ratio *is* directly the value in
        cd/m\u00b2 (nits), no extra scaling needed. Returns None on
        anything unparsable rather than raising, since a single odd
        field shouldn't take down the whole analysis."""
        try:
            num, den = str(value).split("/")
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError, AttributeError):
            return None

    def _hdr_metadata_details(self, s, raw):
        """Pull the embedded static/dynamic HDR metadata - mastering
        display luminance range, MaxCLL/MaxFALL, Dolby Vision profile,
        HDR10+ dynamic metadata presence - out of the same ffprobe
        stream dict/raw dump analyze() already has, entirely locally
        (no network, no extra ffprobe call). This is a separate concern
        from self.kind/note above: kind only looks at the transfer
        function (PQ/HLG) to decide *whether* to tone-map, whereas this
        answers "what HDR flavour does the file actually carry" for
        display next to the preview. Returns a list of short plain-text
        (no HTML) lines, always non-empty when called for an HDR/Dolby
        source."""
        side_data = s.get("side_data_list") or []
        mastering = next((sd for sd in side_data
                           if sd.get("side_data_type") == "Mastering display metadata"), None)
        cll = next((sd for sd in side_data
                    if sd.get("side_data_type") == "Content light level metadata"), None)
        dovi = dovi_configuration(s)
        # HDR10+'s SMPTE2094-40 dynamic metadata lives in per-frame SEI,
        # not this stream-level side_data_list (ffprobe only surfaces it
        # there via -show_frames, which this app doesn't request) - so,
        # same as the has_hdr10plus check in _optimal_note, presence is
        # inferred from the lowercased raw stream dump instead.
        has_hdr10plus = any(m in raw for m in ("smpte2094-40", "hdr10+", "hdr dynamic metadata"))

        lines = []
        if dovi:
            profile = dovi.get("dv_profile")
            level = dovi.get("dv_level")
            layers = [name for name, flag in (("BL", dovi.get("bl_present_flag")),
                                               ("EL", dovi.get("el_present_flag")),
                                               ("RPU", dovi.get("rpu_present_flag"))) if flag]
            if profile is not None and level is not None:
                profile_txt = f"Profile {profile} \u00b7 Level {level}"
            elif profile is not None:
                profile_txt = f"Profile {profile}"
            else:
                profile_txt = f"Profile {self.tr('Unknown')}"
            compat = dovi.get("dv_bl_signal_compatibility_id")
            compat_txt = " \u00b7 HDR10-compatible base layer" if compat else ""
            layers_txt = (" \u00b7 " + "/".join(layers)) if layers else ""
            lines.append(f"Dolby Vision {profile_txt}{compat_txt}{layers_txt}")
        if has_hdr10plus:
            lines.append("HDR10+ dynamic metadata (SMPTE 2094-40) present")
        if mastering:
            min_n = self._ratio_to_float(mastering.get("min_luminance"))
            max_n = self._ratio_to_float(mastering.get("max_luminance"))
            if max_n is not None:
                if min_n is not None:
                    lines.append(f"Mastering display: {min_n:.4g}\u2013{max_n:.0f} nits")
                else:
                    lines.append(f"Mastering display: up to {max_n:.0f} nits")
        if cll:
            max_content = cll.get("max_content")
            max_average = cll.get("max_average")
            parts = []
            if max_content:
                parts.append(f"MaxCLL {max_content} nits")
            if max_average:
                parts.append(f"MaxFALL {max_average} nits")
            if parts:
                lines.append(" \u00b7 ".join(parts))
        if not lines:
            lines.append("No embedded static/dynamic metadata \u2014 tone mapping relies on the transfer function only")
        return lines

    # ---- analyze ----------------------------------------------------
    def analyze(self):
        source = self.src_edit.text()
        if not Path(source).is_file():
            QMessageBox.critical(self, "Source", "Choose a source video first.")
            self._analysis_start_pending = False
            return False
        probe = exe("ffprobe")
        if not probe:
            QMessageBox.critical(self, "FFprobe missing", "Bundle ffprobe with FFmpeg or install FFmpeg.")
            self._analysis_start_pending = False
            return False
        if any(worker.generation == self._analysis_generation
               and worker.source == source and worker.isRunning()
               for worker in self._analysis_workers.values()):
            return True
        self._analysis_generation += 1
        generation = self._analysis_generation
        worker = AnalyzeWorker(generation, str(probe), source)
        self._analysis_workers[generation] = worker
        worker.completed.connect(self._on_analysis_complete)
        worker.finished.connect(self._forget_analysis_worker)
        worker.start()
        return True

    def _forget_analysis_worker(self):
        worker = self.sender()
        for generation, candidate in tuple(self._analysis_workers.items()):
            if candidate is worker:
                self._analysis_workers.pop(generation, None)
                break

    def _on_analysis_complete(self, generation, source, document, error):
        current = self.src_edit.text()
        if (generation != self._analysis_generation
                or os.path.normcase(os.path.abspath(source))
                != os.path.normcase(os.path.abspath(current))):
            return
        if error is not None:
            self._show_analysis_error(error)
            succeeded = False
        else:
            succeeded = self._apply_analysis_document(document)
        mini = getattr(self, "_mini_window", None)
        if mini is not None and hasattr(mini, "on_analysis_complete"):
            mini.on_analysis_complete(succeeded)
        if not self._analysis_start_pending:
            return
        self._analysis_start_pending = False
        if succeeded:
            QTimer.singleShot(0, self.start)
        elif self._queue_controller.running:
            self.write(f"[queue] Analysis failed for {Path(source).name}; skipping it.\n")
            self._queue_controller.batch_clean = False
            QTimer.singleShot(0, self._start_next_queue_item)

    def _show_analysis_error(self, error):
        self._last_analysis_raw = None
        self._last_analysis_doc = None
        for button in (self.preset_optimal_btn, self.preset_best_btn, self.preset_fast_btn):
            if button is not None:
                button.setEnabled(False)
        self._sync_preset_segment_state()
        if hasattr(self, "update_bitrate_estimate"):
            self.update_bitrate_estimate()
        self.analysis_label.setToolTip("")
        if isinstance(error, UnicodeDecodeError):
            self.analysis_label.setText(
                f"Analysis failed reading ffprobe's output ({html.escape(str(error))}). "
                "This is usually a corrupted/unusual metadata tag in the file itself, "
                "not a problem with the video streams - conversion can often still proceed.")
        else:
            self.analysis_label.setText(f"Analysis failed: {html.escape(str(error))}")

    def _apply_analysis_document(self, d, *, is_retranslate: bool = False):
        self._last_analysis_doc = d
        success = True
        try:
            s = d["streams"][0]
            self._dv_profile = dovi_profile(s)
            fmt_dur = d.get("format", {}).get("duration")
            self.duration = float(fmt_dur or s.get("duration") or 0)
            # Trim bar range in whole seconds, with a default 5s selection
            # centered on the midpoint - the same starting point the old
            # fixed "middle of the source, 5s" defaults used, but now
            # both ends are draggable instead of position/duration being
            # separate controls.
            if self.duration and not is_retranslate:
                total = int(self.duration)
                lo = int(total / 2)
                if hasattr(self, "preview_slider"):
                    self.preview_slider.setRange(0, total)
                    self.preview_slider.setEnabled(True)
                    self.preview_slider.setValue(lo)
                elif hasattr(self, "preview_timeline"):
                    self.preview_timeline.setRange(0, total)
                    self.preview_timeline.setEnabled(True)
                    if hasattr(self.preview_timeline, "setValue"):
                        self.preview_timeline.setValue(lo)
                    elif hasattr(self.preview_timeline, "setValues"):
                        self.preview_timeline.setValues(lo, lo)
                self._update_preview_position_label(lo)
                QTimer.singleShot(0, self._start_frame_strip)
            self.src_width = s.get("width")
            self.src_height = s.get("height")
            t = (s.get("color_transfer") or "unknown").lower()
            primaries = (s.get("color_primaries") or "unknown").lower()
            # ffprobe's stream field is (confusingly) named "color_space" for
            # the matrix coefficients, not a colour space in the everyday
            # sense - it's the bt709/bt2020nc/etc. value used to convert
            # YUV<->RGB, distinct from both transfer (PQ/HLG/gamma curve)
            # and primaries (gamut).
            matrix = (s.get("color_space") or "unknown").lower()
            color_range = (s.get("color_range") or "unknown").lower()
            self._src_color_transfer = t
            self._src_color_primaries = primaries
            self._src_color_matrix = matrix
            self._src_color_range = color_range
            range_name = COLOR_RANGE_NAMES.get(color_range, color_range)
            bit_depth = s.get("bits_per_raw_sample")
            if not bit_depth:
                # Fall back to reading it out of the pix_fmt name (e.g.
                # "yuv420p10le" -> 10) when the container doesn't report
                # bits_per_raw_sample directly.
                pf = (s.get("pix_fmt") or "")
                bit_depth = next((n for n in ("12", "10") if n in pf), "8")
            raw = json.dumps(s).lower()
            self._last_analysis_raw = raw
            has_hdr_side_data = any(
                m in raw for m in (
                    "mastering display metadata",
                    "content light level metadata",
                    "smpte2094-40",
                    "hdr10+",
                    "hdr dynamic metadata",
                )
            )
            untagged_high_depth_hdr = (
                str(bit_depth) in ("10", "12")
                and t in ("unknown", "unspecified")
                and primaries not in ("bt709", "bt470bg", "smpte170m")
                and matrix not in ("bt709", "bt470bg", "smpte170m")
            )
            if "dovi" in raw or "dolby vision" in raw:
                self.kind = "dolby"
                note = "Dolby Vision detected. A compatible HDR10 base layer is required for dependable SDR output."
            elif t in ("smpte2084", "pq", "arib-std-b67", "hlg") or has_hdr_side_data or untagged_high_depth_hdr:
                self.kind = "hdr"
                note = "HDR detected. Tone mapping is appropriate."
            else:
                self.kind = "sdr"
                note = "Likely SDR/Rec.709. Tone mapping is normally unnecessary."
            dur_note = f"{format_time(self.duration)} ({self.duration:.0f}s)" if self.duration else "unknown (progress/ETA will be limited)"
            rec = self._recommend(self.kind, raw)
            src_cs = (f"{html.escape(str(TRANSFER_NAMES.get(t, t)))} \u00b7 {html.escape(str(PRIMARIES_NAMES.get(primaries, primaries)))} primaries "
                      f"\u00b7 {html.escape(str(MATRIX_NAMES.get(matrix, matrix)))} matrix \u00b7 {range_name} \u00b7 {bit_depth}-bit")
            # This app always tone-maps *to* BT.709 primaries/transfer/matrix
            # - that fixed combination is what "SDR" means here, so there's
            # no target picker to show; this line just states that fixed
            # destination next to whatever the source actually is.
            cs_line = f"Source: {src_cs} \u2192 Target: BT.709 \u00b7 BT.709 \u00b7 BT.709 (SDR)"
            warn = ""
            if self.kind == "hdr" and primaries not in ("bt2020", "unknown"):
                warn = (f" Note: source primaries are {html.escape(str(PRIMARIES_NAMES.get(primaries, primaries)))}, not the usual "
                        "BT.2020 - tone mapping assumes a BT.2020 gamut, so colours may be off.")
            # Full explanation - kept as a tooltip rather than shown inline;
            # in practice almost nobody reads a 4-sentence paragraph in a
            # side-panel card, so the label itself shows a compact,
            # scannable summary instead (headline + two labelled sections)
            # and this goes on hover for anyone who wants the whole story,
            # the same show-compact/hover-for-detail pattern already used
            # for the tone-mapping dropdown.
            # Embedded HDR metadata (MaxCLL/MaxFALL, mastering display
            # luminance range, Dolby Vision profile/level, HDR10+ dynamic
            # metadata presence) - computed once here from the same
            # ffprobe stream dict/raw dump already parsed above, and
            # reused for both the hover tooltip and the on-screen lines
            # below so the two never drift out of sync with each other.
            hdr_meta_lines = self._hdr_metadata_details(s, raw) if self.kind in ("hdr", "dolby") else []
            tip_parts = []
            if self.kind == "dolby":
                tip_parts.append(self.tr("Dolby Vision source — tone mapped to SDR (BT.709)."))
            elif self.kind == "hdr":
                tip_parts.append(self.tr("HDR source — tone mapped to SDR (BT.709)."))
            else:
                tip_parts.append(self.tr("SDR / Rec.709 source — tone mapping is normally unnecessary."))

            cs_details = []
            raw_t = TRANSFER_NAMES.get(t, t)
            if raw_t and str(raw_t).lower() not in ("unknown", "unspecified"):
                cs_details.append(f"Transfer: {raw_t}")
            raw_p = PRIMARIES_NAMES.get(primaries, primaries)
            if raw_p and str(raw_p).lower() not in ("unknown", "unspecified"):
                cs_details.append(f"Primaries: {raw_p}")
            raw_m = MATRIX_NAMES.get(matrix, matrix)
            if raw_m and str(raw_m).lower() not in ("unknown", "unspecified"):
                cs_details.append(f"Matrix: {raw_m}")
            if cs_details:
                tip_parts.append(" · ".join(cs_details))

            if warn:
                tip_parts.append(warn.strip())

            self.analysis_label.setToolTip("\n".join(tip_parts))
            t_col = self.tokens
            headline = {"hdr": "HDR detected", "sdr": "SDR / Rec.709 detected", "dolby": "Dolby Vision detected"}[self.kind]
            headline_color = t_col["warning"] if self.kind == "dolby" else t_col["accent"]
            # Extract bitrate of source video
            raw_br = s.get("bit_rate")
            if not raw_br or str(raw_br).lower() in ("n/a", "none", "0"):
                tags = s.get("tags") or {}
                for k, v in tags.items():
                    if k.upper().startswith("BPS"):
                        raw_br = v
                        break
            if not raw_br or str(raw_br).lower() in ("n/a", "none", "0"):
                raw_br = d.get("format", {}).get("bit_rate")
            if not raw_br or str(raw_br).lower() in ("n/a", "none", "0"):
                fmt_tags = d.get("format", {}).get("tags") or {}
                for k, v in fmt_tags.items():
                    if k.upper().startswith("BPS"):
                        raw_br = v
                        break

            src_bitrate = None
            if raw_br:
                try:
                    src_bitrate = float(raw_br)
                except (ValueError, TypeError):
                    src_bitrate = None

            if (not src_bitrate or src_bitrate <= 0) and self.duration and self.duration > 0:
                tags = s.get("tags") or {}
                for k, v in tags.items():
                    if "NUMBER_OF_BYTES" in k.upper():
                        try:
                            src_bitrate = (float(v) * 8.0) / self.duration
                            break
                        except Exception:
                            pass

            if (not src_bitrate or src_bitrate <= 0) and self.duration and self.duration > 0:
                try:
                    fsize = d.get("format", {}).get("size")
                    if not fsize and hasattr(self, "src_edit"):
                        src_path = self.src_edit.text().strip()
                        if src_path and os.path.exists(src_path):
                            fsize = os.path.getsize(src_path)
                    if not fsize and getattr(self, "_run_context", None) and self._run_context.source:
                        if os.path.exists(self._run_context.source):
                            fsize = os.path.getsize(self._run_context.source)
                    if fsize:
                        src_bitrate = (float(fsize) * 8.0) / self.duration
                except Exception:
                    pass
            self.src_bitrate = src_bitrate

            short_items = []
            if self.src_width and self.src_height:
                short_items.append(f"{self.src_width}\u00d7{self.src_height}")
            raw_t = TRANSFER_NAMES.get(t, t)
            if raw_t and raw_t != "unknown":
                short_items.append(html.escape(str(raw_t)))
            raw_p = PRIMARIES_NAMES.get(primaries, primaries)
            if raw_p and raw_p != "unknown":
                short_items.append(html.escape(str(raw_p)))
            if range_name and range_name not in ("unknown", "range not signalled"):
                short_items.append(html.escape(str(range_name)))
            if bit_depth and bit_depth != "unknown":
                short_items.append(f"{bit_depth}-bit")
            chroma = detect_chroma_subsampling(s)
            if chroma:
                short_items.append(chroma)
            if self.duration:
                short_items.append(format_time(self.duration))
            if self.src_bitrate and self.src_bitrate > 0:
                if self.src_bitrate >= 1_000_000:
                    short_items.append(f"{self.src_bitrate / 1_000_000.0:.1f} Mbps")
                else:
                    short_items.append(f"{self.src_bitrate / 1_000.0:.0f} kbps")
            src_short = " \u00b7 ".join(short_items) if short_items else self.tr("Unknown source")
            muted = t_col["text_secondary"]
            lines = [f"<b style='color:{headline_color}'>{headline}</b>"]
            lines.append(f"<b style='color:{muted}'>{self.tr('Source file')}</b>")
            lines.append(f"&nbsp;&nbsp;{src_short}")
            if hdr_meta_lines:
                lines.append(f"<b style='color:{muted}'>{self.tr('HDR metadata')}</b>")
                for ml in hdr_meta_lines:
                    lines.append(f"&nbsp;&nbsp;{ml}")
            if self._dv_profile == 5:
                using_hb = self.backend_combo.currentText().startswith("HandBrake")
                engine, _ = tonemap_lookup(self.method_combo.currentText())
                if using_hb or engine != "gpu" or not self.has_bt2390:
                    lines.append(f"<span style='color:{t_col['warning']}'>\u26a0 {self.tr('Dolby Vision Profile 5 requires GPU tone mapping (libplacebo). CPU tonemap and HandBrake are not supported.')}</span>")
                    if self.has_bt2390:
                        lines.append(f"&nbsp;&nbsp;<a href='action:switch_gpu' style='color:{t_col['accent']}; text-decoration:underline;'>{self.tr('Switch to GPU tone mapping (BT.2390)')}</a>")
            if warn:
                lines.append(f"<span style='color:{t_col['warning']}'>\u26a0 primaries are "
                              f"{html.escape(str(PRIMARIES_NAMES.get(primaries, primaries)))}, not BT.2020 \u2014 colours may be off</span>")
            self.analysis_label.setText("<br>".join(lines))
            dovi = dovi_configuration(s)
            rpu_present = bool(dovi.get("rpu_present_flag"))
            dv_prof = dovi_profile(s)
            compatible_dovi_rpu = bool(
                self.kind == "dolby"
                and rpu_present
                and (dv_prof in (4, 5, 7, 8, 9) or (dv_prof is None and dovi))
            )
            if not is_retranslate and not getattr(self, "_dv_rpu_user_override", False) and hasattr(self, "dv_rpu_chk"):
                if compatible_dovi_rpu:
                    self.dv_rpu_chk.setChecked(True)
                    self._dv_rpu_auto_enabled = True
                else:
                    self.dv_rpu_chk.setChecked(False)
                    self._dv_rpu_auto_enabled = False
            for b in (self.preset_optimal_btn, self.preset_best_btn, self.preset_fast_btn):
                if b is not None:
                    b.setEnabled(True)
            self._sync_preset_segment_state()
            self.update_bitrate_estimate()
            if not is_retranslate:
                self._schedule_frame_preview()
        except UnicodeDecodeError as e:
            success = False
            self._last_analysis_raw = None
            for b in (self.preset_optimal_btn, self.preset_best_btn, self.preset_fast_btn):
                if b is not None:
                    b.setEnabled(False)
            self.analysis_label.setToolTip("")
            self.analysis_label.setText(f"Analysis failed reading ffprobe's output ({html.escape(str(e))}). "
                                         "This is usually a corrupted/unusual metadata tag in the file itself, "
                                         "not a problem with the video streams - conversion can often still proceed.")
        except Exception as e:
            success = False
            self._last_analysis_raw = None
            for b in (self.preset_optimal_btn, self.preset_best_btn, self.preset_fast_btn):
                if b is not None:
                    b.setEnabled(False)
            self.analysis_label.setToolTip("")
            self.analysis_label.setText(f"Analysis failed: {html.escape(str(e))}")
        # analysis_label just went from its short placeholder text to a
        # multi-line HTML block (or, in the except branches, to a wrapped
        # sentence) - but it lives inside analysis_scroll, a fixed-height
        # QScrollArea (see build()), so that no longer changes SOURCE
        # ANALYSIS's or the window's minimum height; any overflow just
        # scrolls. This call is kept as a harmless no-op safety net for
        # any other height-affecting change analyze() might make (e.g.
        # ffmpeg_version_note or another label toggling visible/hidden)
        # rather than because analysis_label's own reflow needs it now.
        return success

    def _optimal_note(self, kind, raw):
        """What 'Optimal' means for this source - almost always just a
        confirmation that the app's own defaults already fit, since that's
        what those defaults are chosen for."""
        if kind == "sdr":
            return "tone mapping off \u2014 already the default for non-HDR sources"
        if kind == "dolby":
            return "BT.2390 \u00b7 GPU, same as HDR \u2014 needs a usable HDR10 base layer"
        has_hdr10plus = any(m in raw for m in ("smpte2094-40", "hdr10+", "hdr dynamic metadata"))
        if has_hdr10plus and self.has_bt2390 and self.has_st2094:
            return "ST2094-40 \u2014 matches this source's HDR10+ metadata"
        if self.has_bt2390:
            return "BT.2390 \u00b7 GPU \u2014 already the app default"
        return "Hable \u00b7 CPU \u2014 BT.2390/libplacebo unavailable in this FFmpeg build"

    def _best_quality_note(self):
        return "CPU \u00b7 H.265, low CRF \u2014 slower, highest fidelity"

    def _fast_note(self):
        """Same hardware-encoder probe as _perf_recommendation(), phrased as
        the 'trade some quality for speed' tier rather than a full sentence."""
        gpu_options = [
            ("NVIDIA NVENC \u00b7 H.265", "hevc_nvenc"),
            ("AMD AMF \u00b7 H.265", "hevc_amf"),
            ("Apple VideoToolbox \u00b7 H.265", "hevc_videotoolbox"),
            ("Intel QSV \u00b7 H.265", "hevc_qsv"),
        ]
        gpu_hit = next((label for label, enc_id in gpu_options if enc_id in self.encoders), None)
        if gpu_hit:
            return f"{gpu_hit} \u2014 hardware, much faster, still solid quality"
        return f"CPU \u00b7 H.264 or a higher CRF \u2014 no hardware encoder detected ({CPU_COUNT} cores)"

    # ---- one-click preset application --------------------------------
    def _optimal_curve(self, kind, raw):
        """The method_combo label _optimal_note() describes in words -
        kept as the one place that decides this, so the note text and the
        "Optimal" button can't drift out of sync with each other."""
        if kind == "sdr":
            return "None (direct, no tonemap) \u00b7 CPU tonemap"
        if kind != "dolby":
            has_hdr10plus = any(m in raw for m in ("smpte2094-40", "hdr10+", "hdr dynamic metadata"))
            if has_hdr10plus and self.has_bt2390 and self.has_st2094:
                return "ST2094-40 (HDR10+) \u00b7 GPU tonemap"
        if self.has_bt2390:
            return "BT.2390 \u00b7 GPU tonemap"
        return "Hable \u00b7 CPU tonemap"

    def _select_curve(self, label):
        """Applies a method_combo selection. Falls back to Hable - always
        present regardless of backend - if the requested label isn't
        actually a valid option right now (e.g. a GPU curve while the
        HandBrake backend is selected, which never offers libplacebo)."""
        if self.method_combo.findText(label) < 0:
            label = "Hable \u00b7 CPU tonemap"
        self.method_combo.setCurrentText(label)

    def _fast_preset_targets(self):
        gpu_options = [
            ("NVIDIA NVENC \u00b7 H.265", "hevc_nvenc"),
            ("AMD AMF \u00b7 H.265", "hevc_amf"),
            ("Apple VideoToolbox \u00b7 H.265", "hevc_videotoolbox"),
            ("Intel QSV \u00b7 H.265", "hevc_qsv"),
        ]
        gpu_hit = next((label for label, enc_id in gpu_options if enc_id in self.encoders), None)
        target_enc = gpu_hit if gpu_hit and self.encoder_combo.findText(gpu_hit) >= 0 else "CPU \u00b7 H.264"
        raw_curve = "BT.2390 \u00b7 GPU tonemap" if self.has_bt2390 else "Hable \u00b7 CPU tonemap"
        target_curve = raw_curve if self.method_combo.findText(raw_curve) >= 0 else "Hable \u00b7 CPU tonemap"
        return target_enc, target_curve

    def _resolved_optimal_curve(self):
        if self.kind == "unknown" or self._last_analysis_raw is None:
            return "Hable \u00b7 CPU tonemap"
        raw = self._optimal_curve(self.kind, self._last_analysis_raw)
        return raw if self.method_combo.findText(raw) >= 0 else "Hable \u00b7 CPU tonemap"

    def _sync_preset_segment_state(self):
        if getattr(self, "_applying_preset", False):
            return
        btns = (
            ("optimal", getattr(self, "preset_optimal_btn", None)),
            ("best", getattr(self, "preset_best_btn", None)),
            ("fast", getattr(self, "preset_fast_btn", None)),
        )
        if any(b is None for _, b in btns):
            return
        if self.kind == "unknown" or self._last_analysis_raw is None or not self.preset_optimal_btn.isEnabled():
            for _, b in btns:
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)
            if hasattr(self, "preset_hint_label"):
                self.preset_hint_label.setText("")
                self.preset_hint_label.hide()
            return

        opt_curve = self._resolved_optimal_curve()
        fast_enc, fast_curve = self._fast_preset_targets()
        enc_text = self.encoder_combo.currentText()
        def_q = QUALITY_RANGES.get(enc_text, DEFAULT_QUALITY_RANGE)["default"]
        active = match_active_preset(
            kind=self.kind,
            encoder=enc_text,
            method=self.method_combo.currentText(),
            quality=self.quality_spin.value(),
            quality_mode=self.quality_mode_combo.currentText() if hasattr(self, "quality_mode_combo") else "CRF/CQ",
            optimal_curve=opt_curve,
            fast_encoder=fast_enc,
            fast_curve=fast_curve,
            default_quality=def_q,
            crf_tolerance=1,
        )
        if active is not None and getattr(self, "_active_advanced_filters_count", lambda: 0)() > 0:
            active = None
        for key, b in btns:
            b.blockSignals(True)
            b.setChecked(active == key)
            b.style().unpolish(b)
            b.style().polish(b)
            b.update()
            b.blockSignals(False)
            if _neon_active(self):
                if active == key and b.isEnabled():
                    glow = QColor(self.tokens["accent"])
                    glow.setAlpha(190)
                    b._shadow.setBlurRadius(24)
                    b._shadow.setOffset(0, 0)
                    b._shadow.setColor(glow)
                    b._shadow.setEnabled(True)
                elif self.ui_style != "glass":
                    b._shadow.setEnabled(False)

        if hasattr(self, "preset_hint_label"):
            if active == "optimal":
                detailed_desc = self._optimal_note(self.kind, self._last_analysis_raw)
            elif active == "best":
                detailed_desc = self._best_quality_note()
            elif active == "fast":
                detailed_desc = self._fast_note()
            else:
                detailed_desc = ""
            self.preset_hint_label.setText(detailed_desc)
            self.preset_hint_label.setToolTip(detailed_desc)
            self.preset_hint_label.setVisible(bool(detailed_desc and getattr(self, "show_detailed_conversion_info", False)))
        if hasattr(self, "preset_optimal_btn") and self.preset_optimal_btn is not None:
            self.preset_optimal_btn.setToolTip(self._optimal_note(self.kind, self._last_analysis_raw))
        if hasattr(self, "preset_best_btn") and self.preset_best_btn is not None:
            self.preset_best_btn.setToolTip(self._best_quality_note())
        if hasattr(self, "preset_fast_btn") and self.preset_fast_btn is not None:
            self.preset_fast_btn.setToolTip(self._fast_note())

    def _apply_optimal_preset(self):
        if self.kind == "unknown" or self._last_analysis_raw is None:
            return
        self._applying_preset = True
        try:
            if hasattr(self, "quality_mode_combo"):
                self.quality_mode_combo.setCurrentText("CRF/CQ")
            label = self._optimal_curve(self.kind, self._last_analysis_raw)
            self._select_curve(label)
            def_q = QUALITY_RANGES.get(self.encoder_combo.currentText(), DEFAULT_QUALITY_RANGE)["default"]
            self.quality_spin.setValue(def_q)
        finally:
            self._applying_preset = False
        self._sync_preset_segment_state()
        self.write(f"[preset] Optimal applied: {self.method_combo.currentText()}\n")

    def _apply_best_quality_preset(self):
        if self.kind == "unknown" or self._last_analysis_raw is None:
            return
        self._applying_preset = True
        try:
            if hasattr(self, "quality_mode_combo"):
                self.quality_mode_combo.setCurrentText("CRF/CQ")
            label = self._optimal_curve(self.kind, self._last_analysis_raw)
            self._select_curve(label)
            if self.encoder_combo.findText("CPU \u00b7 H.265") >= 0:
                self.encoder_combo.setCurrentText("CPU \u00b7 H.265")
            self.quality_spin.setValue(16)  # low CRF - matches _best_quality_note()'s promise
        finally:
            self._applying_preset = False
        self._sync_preset_segment_state()
        self.write(f"[preset] Best quality applied: {self.encoder_combo.currentText()}, "
                   f"{self.method_combo.currentText()}, CRF {self.quality_spin.value()}\n")

    def _apply_fast_preset(self):
        if self.kind == "unknown" or self._last_analysis_raw is None:
            return
        self._applying_preset = True
        try:
            if hasattr(self, "quality_mode_combo"):
                self.quality_mode_combo.setCurrentText("CRF/CQ")
            target, label = self._fast_preset_targets()
            if self.encoder_combo.findText(target) >= 0:
                self.encoder_combo.setCurrentText(target)
            if target.startswith("CPU"):
                self.quality_spin.setValue(23)
            else:
                def_q = QUALITY_RANGES.get(self.encoder_combo.currentText(), DEFAULT_QUALITY_RANGE)["default"]
                self.quality_spin.setValue(def_q)
            self._select_curve(label)
        finally:
            self._applying_preset = False
        self._sync_preset_segment_state()
        self.write(f"[preset] Fast applied: {self.encoder_combo.currentText()}, {self.method_combo.currentText()}\n")

    def _recommend(self, kind, raw):
        """A short, honest steer toward the settings most likely to fit this
        specific source and this machine - not a hard rule, just what the
        analysis and the capability probe already gathered pointing
        somewhere useful instead of sitting unused."""
        perf = self._perf_recommendation()
        if kind == "sdr":
            return ("Tone mapping isn't really needed here - if you convert anyway, a gentle CPU tonemap "
                    f"curve (or None) is safer than forcing BT.2390 on non-HDR footage. {perf}")
        if kind == "dolby":
            return ("Results depend on this file having a usable HDR10 base layer - if the output looks "
                    f"off, that's the likely reason. {perf}")
        # kind == "hdr"
        has_hdr10plus = any(m in raw for m in ("smpte2094-40", "hdr10+", "hdr dynamic metadata"))
        if has_hdr10plus and self.has_bt2390 and self.has_st2094:
            curve = "This source carries HDR10+ dynamic metadata - pick ST2094-40 for the most accurate result."
        elif self.has_bt2390:
            curve = "BT.2390 on GPU (the default) is a good fit for this source."
        else:
            curve = "This FFmpeg build has no usable BT.2390/libplacebo - Hable (CPU tonemap) is the best curve available here."
        return f"{curve} {perf}"

    def _perf_recommendation(self):
        """Speed-vs-quality steer built from the same self.encoders
        capability probe the encoder dropdown filtering already uses, so
        it's specific to what this machine's FFmpeg build can actually
        reach rather than a generic tip that may not apply."""
        gpu_options = [
            ("NVIDIA NVENC \u00b7 H.265", "hevc_nvenc"),
            ("AMD AMF \u00b7 H.265", "hevc_amf"),
            ("Apple VideoToolbox \u00b7 H.265", "hevc_videotoolbox"),
            ("Intel QSV \u00b7 H.265", "hevc_qsv"),
        ]
        gpu_hit = next((label for label, enc_id in gpu_options if enc_id in self.encoders), None)
        if gpu_hit:
            return (f"For speed with still-solid quality, this machine has {gpu_hit} available \u2014 much "
                     "faster than CPU encoding. For the best achievable quality (at the cost of encode "
                     "time), use CPU \u00b7 H.265 with a low CRF instead.")
        return (f"No hardware encoder was detected here, so encoding runs on the CPU ({CPU_COUNT} cores "
                 "available) \u2014 CPU \u00b7 H.265 gives the best quality; CPU \u00b7 H.264, a higher CRF, "
                 "or limiting cores less aggressively trades some quality for shorter encode times.")

    # ---- command builders --------------------------------------------
    def _run_crop_detect(self):
        """Runs cropdetect over sample windows across the source and
        returns an ffmpeg "crop=W:H:X:Y" filter string, or "" if nothing
        was detected/the probe failed. Cached in self._autocrop_value per
        source (cleared in _set_source) so this only runs once even if
        Start is pressed more than once."""
        ff = exe("ffmpeg")
        if not ff:
            return ""
        samples = []
        for start_at, sample_dur in crop_sample_offsets(self.duration):
            try:
                proc = run_capture(
                    [ff, "-hide_banner", "-ss", f"{start_at:.2f}", "-i", self.src_edit.text(),
                     "-t", f"{sample_dur:g}", "-vf", CROPDETECT_FILTER, "-f", "null", "-"],
                    timeout=30)
            except Exception:
                continue
            samples.append(proc.text("stderr"))
        return select_crop_filter(samples, self.src_width, self.src_height)

    # ---- settings snapshot ------------------------------------------------
    # This is the whole UI side of the PURE CORE boundary: one place that
    # reads the widgets, and nothing below it that does. Anything that used
    # to reach into self.encoder_combo/self.deband_chk/... from inside a
    # command builder now goes through here instead.
    #
    # Deliberately a plain read, with no validation and no defaulting: the
    # widgets are already constrained to valid values by the UI itself
    # (bit depth re-filters the encoder list, backend re-filters the
    # tonemap list), and re-checking here would just create a second,
    # divergent idea of what is valid.
    def collect_settings(self, **overrides):
        """Snapshot every setting the command builders need.

        `overrides` exists for the callers that need a variant of the
        current settings without touching the UI - the frame preview
        renders at a fixed 8-bit pixel format, a resume segment writes to
        a part file. Previously each of those poked a temporary value into
        a widget or an attribute and relied on putting it back afterwards.
        """
        s = ConversionSettings(
            src=self.src_edit.text(),
            dst=self.dst_edit.text(),
            backend=self.backend_combo.currentText(),
            encoder_label=self.encoder_combo.currentText(),
            bit_depth_label=self.bit_depth_combo.currentText(),
            quality_mode=self.quality_mode_combo.currentText(),
            quality=self.quality_spin.value(),
            target_bitrate_kbps=self.target_bitrate_spin.value(),
            cores=self.cores_spin.value(),
            cpu_count=CPU_COUNT,
            tonemap_method=(getattr(self, "_job_tonemap_override", None) or self.method_combo.currentText()),
            resolution_label=self.res_combo.currentText(),
            denoise=self.denoise_combo.currentText(),
            deband=self.deband_chk.isChecked(),
            resharpen=self.resharpen_chk.isChecked(),
            autocrop=self.autocrop_chk.isChecked(),
            autocrop_value=(getattr(self, "_autocrop_value", None) or "") if self.autocrop_chk.isChecked() else "",
            hwaccel=self._hwaccel_wanted(),
            dv_rpu=(self._job_dv_rpu_override if getattr(self, "_job_dv_rpu_override", None) is not None else self.dv_rpu_chk.isChecked()),
            peak_detect=self.peak_detect_chk.isChecked(),
            brightness=self.brightness_spin.value(),
            saturation=self.saturation_spin.value(),
            desat=(self.desat_spin.value() if self.desat_chk.isChecked() else 0.0),
            colortemperature=self.colortemperature_spin.value(),
            gamut_mode=(self.gamut_combo.currentText() if hasattr(self, "gamut_combo") else "Perceptual"),
            preview_path=str(self.preview_path) if self.preview_path else "",
            resume_seek_sec=self._resume_coordinator.seek_seconds,
            resume_output_override=(str(self._resume_coordinator.output_override)
                                    if self._resume_coordinator.output_override else ""),
            handbrake_tool=self.handbrake_tool or "",
            src_color_transfer=(getattr(self, "_src_color_transfer", "") or ""),
            src_color_primaries=(getattr(self, "_src_color_primaries", "") or ""),
            src_color_matrix=(getattr(self, "_src_color_matrix", "") or ""),
            src_color_range=(getattr(self, "_src_color_range", "") or ""),
        )
        return replace(s, **overrides) if overrides else s

    def _conversion_preference_controls(self):
        """Persistent conversion choices; source paths and queue are session-only."""
        return {
            "backend": self.backend_combo,
            "encoder": self.encoder_combo,
            "bit_depth": self.bit_depth_combo,
            "quality_mode": self.quality_mode_combo,
            "quality": self.quality_spin,
            "target_bitrate": self.target_bitrate_spin,
            "cores": self.cores_spin,
            "priority": self.priority_combo,
            "tonemap": self.method_combo,
            "resolution": self.res_combo,
            "container": self.container_combo,
            "denoise": self.denoise_combo,
            "deband": self.deband_chk,
            "resharpen": self.resharpen_chk,
            "autocrop": self.autocrop_chk,
            "hwaccel": self.hwaccel_chk,
            "peak_detect": self.peak_detect_chk,
            "desat_enabled": self.desat_chk,
            "desat": self.desat_spin,
            "brightness": self.brightness_spin,
            "saturation": self.saturation_spin,
            "colortemperature": self.colortemperature_spin,
            "gamut_mode": self.gamut_combo,
        }

    def _restore_output_folder(self, folder):
        self.output_folder = folder
        self._update_output_folder_btn()

    def _settings_with_tracks(self, out_path=None, announce=False):
        """collect_settings() plus the source's audio/subtitle streams, so
        the command builders can leave out tracks an MP4/MOV output can't
        hold. Probed lazily (only when a command is actually built) and
        cached per file, since collect_settings() itself is called a lot
        and must stay instant. `announce` writes the "left out" notes to
        the log once per source/destination pair."""
        s = self.collect_settings()
        if s.using_handbrake or not s.src:
            return s
        try:
            st = Path(s.src).stat()
            key = (s.src, st.st_mtime_ns, st.st_size)
        except OSError:
            return s
        cache = self.__dict__.setdefault("_stream_cache", {})
        if key not in cache:
            cache[key] = probe_streams(s.src)
            if len(cache) > 32:
                cache.pop(next(iter(cache)))
        s = replace(s, src_streams=cache[key])
        if announce:
            plan = plan_tracks(s, out_path)
            lines = plan.describe(tr=self.tr)
            note_key = (key, str(out_path or s.effective_dst))
            if lines and getattr(self, "_track_note_key", None) != note_key:
                self._track_note_key = note_key
                for line in lines:
                    self.write(f"[tracks] {line}\n")
        return s

    # ---- run / control -------------------------------------------------
    def _confirm_tonemap_fallback(self, fallback_label: str, reason: str) -> bool:
        """Notify the user when GPU tone mapping fails and offer to fall back to
        ``fallback_label`` for THIS file only (or decline and skip the file).
        Never mutates ``method_combo``, ``dv_rpu_chk``, or saved ``QSettings``."""
        fname = Path(self.src_edit.text()).name if self.src_edit.text() else "this file"
        banner_msg = f"Tonemap fallback for {fname}: {fallback_label} (your setting is unchanged)"
        self._tonemap_fallback_banner_text = banner_msg
        if hasattr(self, "_set_test_clip_status"):
            self._set_test_clip_status(banner_msg)
        if getattr(self._queue_controller, "running", False) and not getattr(self, "_prompt_tonemap_fallback_in_queue", False):
            # In unattended queue mode, avoid blocking overnight runs with a modal
            # unless _prompt_tonemap_fallback_in_queue is set, while keeping the
            # prominent banner and marking batch_clean = False.
            self._queue_controller.batch_clean = False
            return True
        ans = QMessageBox.question(
            self,
            self.tr("GPU tone mapping unavailable"),
            self.tr(
                "{reason}\n\n"
                "Use {fallback} for this file only?\n"
                "(Your selected tone-mapping method in Settings will stay unchanged "
                "and will be tried again on the next file. Choose No to skip this file.)"
            ).format(reason=reason, fallback=fallback_label),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            self._tonemap_fallback_banner_text = ""
            if hasattr(self, "_set_test_clip_status"):
                self._set_test_clip_status("")
            self.write(f"[tonemap] Fallback to {fallback_label} declined by user; skipping {fname}.\n")
            return False
        return True

    def _clear_job_tonemap_override(self):
        self._job_tonemap_override = None
        self._job_dv_rpu_override = None
        self._job_tonemap_fallback_attempts = 0
        self._gpu_tonemap_fallback_used = False
        self._fallback_retry_pending = False
        self._tonemap_fallback_declined_for_current = False
        if getattr(self, "_tonemap_fallback_banner_text", ""):
            self._tonemap_fallback_banner_text = ""
            if hasattr(self, "_set_test_clip_status"):
                self._set_test_clip_status("")

    def _report_start_capability_problem(self, title, message):
        """Report a failed preflight without blocking unattended queue runs."""
        if self._queue_controller.running:
            self.write(f"[queue] {Path(self.src_edit.text()).name}: {message} Skipped.\n")
            self._queue_controller.batch_clean = False
        else:
            QMessageBox.warning(self, title, message)

    def start(self):
        """Starts converting the current source. If it cannot start (already SDR, no encoder,
        low disk space, ...) while a queue is running, the item is skipped and the queue
        carries on - it must never sit "running" with nothing running."""
        # Preflight may enter nested Qt event loops (analysis, crop detection,
        # or confirmation dialogs). A timer or a second click can therefore
        # re-enter start() before QProcess exists. Keep the guard around the
        # whole preflight, not just around process startup.
        if getattr(self, "_start_in_progress", False):
            return
        self._start_in_progress = True
        if hasattr(self, "frame_strip_loader"):
            self.frame_strip_loader.cancel()      # thumbnails must not compete with the encode (they stay shown)
        if hasattr(self, "frame_strip"):
            self.frame_strip.set_scrub_enabled(False)
            self.frame_strip.show()
        if hasattr(self, "preview_controls_widget"):
            self.preview_controls_widget.hide()
        self._update_frame_strip_during_conversion()
        try:
            result = self._start_impl()
        finally:
            self._start_in_progress = False
        action = RunWorkflow.start_action(
            result, self.proc is not None, self._queue_controller.running)
        if action == "wait":
            return
        if action == "skip_queue_item":
            self.write("[queue] This file was skipped - it could not be started.\n")
            self._queue_controller.batch_clean = False                  # something needs a look: no sleep/shutdown after
            QTimer.singleShot(0, self._start_next_queue_item)

    def _start_impl(self):
        """The body of start(). Returns "busy" when a run/setup is already in progress (the
        queue must not move on then), anything else when it either launched or gave up."""
        blocker = RunWorkflow.start_blocker(
            self.proc is not None, getattr(self, "_setup_proc", None) is not None)
        if blocker == StartAction.WAIT_PROCESS:
            QMessageBox.information(self, "Already running", "A conversion is already in progress.")
            return "busy"
        self._cancel_after_action_countdown()      # starting something new means "not done yet"
        if not self._queue_controller.running:
            self._queue_controller.batch_clean = True               # (queue items continue the batch's flag)
        if blocker == StartAction.WAIT_SETUP:
            QMessageBox.information(self, "Setup in progress", "Wait for dependency installation to finish first.")
            return "busy"
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        input_action = RunWorkflow.input_action(
            using_hb, bool(self.handbrake_tool), Path(self.src_edit.text()).is_file(),
            bool(self.dst_edit.text()))
        if input_action == StartAction.MISSING_BACKEND:
            if self._queue_controller.running:                      # unattended: log it, no modal dialog
                self.write("[queue] HandBrakeCLI not found - install it or switch the backend to FFmpeg.\n")
            else:
                QMessageBox.critical(self, "HandBrakeCLI not found",
                                      "Install HandBrakeCLI and ensure it's on PATH, or switch the backend to FFmpeg.")
            return
        if input_action == StartAction.MISSING_FILES:
            if self._queue_controller.running:                      # a vanished/renamed file must not stall the queue
                self.write(f"[queue] Source not found, skipped: {self.src_edit.text()}\n")
            else:
                QMessageBox.critical(self, "Files", "Choose source and output files.")
            return
        if self._refuse_source_collision():
            return
        if not self._confirm_destination():
            return
        if self._refuse_source_collision():      # the queue may have picked a new free name
            return
        if not self._enough_disk_space():
            return
        if not using_hb and not exe("ffmpeg"):
            if self._queue_controller.running:
                self.write("[queue] FFmpeg not found - install FFmpeg first.\n")
            else:
                QMessageBox.critical(self, "FFmpeg", "FFmpeg is required.")
            return
        source_action = RunWorkflow.source_action(self.kind)
        if source_action == StartAction.ANALYZE_SOURCE:
            self._analysis_start_pending = True
            if self.analyze():
                return "busy"
            self._analysis_start_pending = False
            return "analysis_failed"
        if source_action == StartAction.SKIP_SDR:
            if self._queue_controller.running:                      # unattended: log it, no modal dialog
                self.write(f"[queue] {Path(self.src_edit.text()).name} looks like SDR already - skipped.\n")
            else:
                QMessageBox.information(self, "Already SDR", "This source appears to be SDR; conversion was not started.")
            return
        if not getattr(self, "_fallback_retry_pending", False):
            self._job_tonemap_override = None
            self._gpu_tonemap_fallback_used = False
        else:
            self._fallback_retry_pending = False
            self._job_dv_rpu_override = None
            self._job_tonemap_fallback_attempts = 0
        active_method = getattr(self, "_job_tonemap_override", None) or self.method_combo.currentText()
        engine, _curve = tonemap_lookup(active_method)
        dovi_action = RunWorkflow.dolby_vision_action(
            self._dv_profile, self.kind, using_handbrake=using_hb,
            tonemap_engine=engine, has_libplacebo=self.has_bt2390,
            unattended=self._queue_controller.running)
        if dovi_action == DolbyVisionAction.BLOCK_PROFILE5:
            message = (
                "Dolby Vision Profile 5 has no HDR10-compatible base layer. "
                "It requires FFmpeg with libplacebo and a GPU tone-mapping curve "
                "so its Dolby Vision RPU can be applied. Select a GPU curve and "
                "the FFmpeg backend, or use another source."
            )
            if self._queue_controller.running:
                self.write(f"[queue] {Path(self.src_edit.text()).name}: {message} Skipped.\n")
                self._queue_controller.batch_clean = False
            else:
                QMessageBox.warning(self, "Dolby Vision Profile 5", message)
            return
        if dovi_action == DolbyVisionAction.ENABLE_PROFILE5_RPU:
            if not self.dv_rpu_chk.isChecked():
                self.dv_rpu_chk.setChecked(True)
                self._dv_rpu_auto_enabled = True
            self.write("[dovi] Profile 5 detected; Dolby Vision RPU is enabled for this FFmpeg GPU run. "
                       "Inspect the output colours before relying on it.\n")
            if self._queue_controller.running:
                self._queue_controller.batch_clean = False
        elif dovi_action == DolbyVisionAction.WARN_QUEUE_BASE_LAYER:
            self.write("[queue] Dolby Vision source - converting it assuming an HDR10-compatible "
                       "base layer; check the colours of the result.\n")
            self._queue_controller.batch_clean = False
        elif dovi_action == DolbyVisionAction.CONFIRM_BASE_LAYER:
            if QMessageBox.question(
                    self, "Dolby Vision",
                    "Continue only if it contains an HDR10-compatible base layer?") != QMessageBox.StandardButton.Yes:
                return
        # Stop any in-flight still-frame preview before running FFmpeg preflight
        # probes or crop detection so a 4K libplacebo preview process does not
        # hold Vulkan/BAR1 device memory while probe_libplacebo() runs.
        self._stop_frame_preview_process()
        if not using_hb and self.autocrop_chk.isChecked() and self._autocrop_value is None:
            self.write("Detecting crop...\n")
            self._autocrop_value = self._run_crop_detect()
            if self._autocrop_value:
                self.write(f"Auto-crop: {self._autocrop_value}\n")
            else:
                self.write("Auto-crop: nothing detected, skipping.\n")
        if not using_hb:
            active_method = getattr(self, "_job_tonemap_override", None) or self.method_combo.currentText()
            engine, code = tonemap_lookup(active_method)
            issue = check_start_capabilities(
                self.collect_settings(), ffmpeg=exe("ffmpeg"),
                tonemap_engine=engine, tonemap_code=code,
                has_libplacebo=self.has_bt2390,
                has_st2094=self.has_st2094, encoders=self.encoders)
            if (issue is not None
                    and engine == "gpu"
                    and issue.title in ("GPU tonemap could not start", "GPU tonemap unavailable", "Curve unavailable")
                    and self._dv_profile != 5
                    and getattr(self, "_job_tonemap_fallback_attempts", 0) < 1):
                fallback_label = "Hable \u00b7 CPU tonemap"
                first_line = (issue.message or "").splitlines()[0].strip()
                self._job_tonemap_fallback_attempts = getattr(self, "_job_tonemap_fallback_attempts", 0) + 1
                if not self._confirm_tonemap_fallback(fallback_label, first_line):
                    self._tonemap_fallback_declined_for_current = True
                    return
                self.write(
                    f"[tonemap] {first_line} "
                    f"Using {fallback_label} for this file only (user setting preserved).\n"
                )
                self._job_tonemap_override = fallback_label
                if getattr(self, "_dv_rpu_auto_enabled", False):
                    self._job_dv_rpu_override = False
                self._gpu_tonemap_fallback_used = True
                engine, code = tonemap_lookup(self._job_tonemap_override)
                issue = check_start_capabilities(
                    self.collect_settings(), ffmpeg=exe("ffmpeg"),
                    tonemap_engine=engine, tonemap_code=code,
                    has_libplacebo=self.has_bt2390,
                    has_st2094=self.has_st2094, encoders=self.encoders)
            if issue is not None:
                self._report_start_capability_problem(issue.title, issue.message)
                return
        self.stopping = False
        self.paused = False
        self.pbar.setValue(0)
        self.speed_label.setText("")
        self.speed_label.hide()
        self.using_hb = using_hb
        self._reset_run_parsers(using_hb)
        # encoder_combo is shared between the FFmpeg and HandBrakeCLI
        # backends, so this is meaningful (and worth capturing) either way.
        self._current_encoder_label = self.encoder_combo.currentText()
        self._hwaccel_decode_on = self._hwaccel_wanted()
        # A new run discards leftovers from an abandoned resume chain.
        self._hwaccel_forced_off = False
        self._resume_coordinator.begin_fresh_run(self.dst_edit.text())
        # Snapshot of what THIS run is about: the fields on screen can change while it runs
        # (the user picks another queue row, edits the destination), but history, validation
        # and resume must keep talking about the file that was actually converted.
        transition_safely(self._run_lifecycle, RunState.STARTING, log_suppressed)
        self._run_context.capture(
            self.src_edit.text(), self.dst_edit.text(), self.kind,
            asdict(self.collect_settings()))
        if using_hb:
            self.write("[resume] Resuming an unfinished output is only available with the FFmpeg backend.\n")
        if not using_hb and self._offer_orphan_resume(self._resume_coordinator.output_override):
            return                                  # continuing an earlier, interrupted run
        self._write_part_sidecar(self._resume_coordinator.output_override)

        try:
            if using_hb:
                self.preview_path = None
                self.preview_label.setText(self.tr("Live preview isn't available with the HandBrakeCLI backend."))
            else:
                self.preview_path = Path(tempfile.gettempdir()) / f"hdr2sdr_live_preview_{os.getpid()}.jpg"
                self.preview_mtime = 0
                self.preview_path.unlink(missing_ok=True)
                self.preview_label.setText(self.tr("Waiting for the first preview frame\u2026"))
            self._set_preview_meta("")
            cmd, warnings = build_command(self._settings_with_tracks(announce=True))
        except (OSError, ValueError) as error:
            self._finalize_run_error(f"Could not prepare the conversion: {error}")
            return
        for warning in warnings:
            self.write(warning)
        if using_hb and self.hb_encoders:
            # Only enforced when detection actually found something (see
            # _check_handbrake_encoders) - an empty self.hb_encoders means
            # detection didn't run/parse, not that nothing is supported, and
            # blocking every HandBrake start on that would be a regression.
            try:
                hb_enc_id = cmd[cmd.index("-e") + 1]
            except ValueError:
                hb_enc_id = None
            if hb_enc_id and hb_enc_id not in self.hb_encoders:
                transition_safely(self._run_lifecycle, RunState.FAILED, log_suppressed)
                self._report_start_capability_problem(
                    "Encoder unavailable",
                    f"This HandBrakeCLI build doesn't support the '{hb_enc_id}' encoder/profile.")
                return
        self._launch(cmd)

    def _release_proc(self):
        """Forgets the finished QProcess and lets Qt free it (they used to pile up, one per run)."""
        old, self.proc = self.proc, None
        self.proc_psutil = None
        runner = getattr(self, "_conversion_runner", None)
        if runner is not None:
            if runner.process is old:
                runner.process = None
            if is_valid_qobject(old):
                QTimer.singleShot(0, lambda p=old, r=runner: r.release(p))
        elif is_valid_qobject(old) and hasattr(old, "deleteLater"):
            QTimer.singleShot(0, old.deleteLater)

    def _refuse_source_collision(self):
        """True (after telling the user / logging) when the source would be overwritten or deleted
        by the output actually chosen. Checked again after _confirm_destination(), because the
        queue may pick another free name (_1, _2 ...) there."""
        if not DestinationPlan(self.dst_edit.text()).collides_with(self.src_edit.text()):
            return False
        self.write("[error] The source file has the same name as a file this conversion would write "
                   "or delete (the output, its .part file or a resume segment) - choose another "
                   "output name.\n")
        if self._queue_controller.running:
            self.write("[queue] This file was skipped.\n")
        else:
            QMessageBox.critical(self, "Output name", "The output name would overwrite or delete the "
                                 "source video (or one of its temporary files). Choose a different output.")
        return True

    # ---- unfinished output from an earlier run --------------------------------
    # ---- registry of unfinished runs (found again even if the output name/folder changed) ----
    def _write_part_sidecar(self, part):
        fingerprint = UnfinishedStore.source_fingerprint((self._run_context.source or Path(self.src_edit.text())))
        destination = (self._resume_coordinator.final_destination
                       or self._run_context.destination
                       or Path(self.dst_edit.text()))
        self._unfinished_registry.write_sidecar(
            part, settings_signature(self.collect_settings()), fingerprint,
            chain=self._resume_coordinator.chain_metadata())
        self._unfinished_registry.remember(part, destination, fingerprint)

    def _offer_orphan_resume(self, part):
        """A "<name>.part<ext>" or chained resume segment left by an earlier run that was
        killed / crashed / closed. Offers to continue it when it is long enough to be
        worth it. Returns True when a resume segment was launched."""
        part = Path(part)
        self.write(f"[resume] Looking for an unfinished output: {part} - "
                   f"{'found' if part.is_file() else 'not there'}.\n")
        if not part.is_file():
            # the output name / folder may have changed since (Mini mode, another container...),
            # or an active resume chain moved the first segment to .resume-part1:
            # look the source up in the registry of unfinished runs
            others = self._unfinished_registry.for_source(
                UnfinishedStore.source_fingerprint((self._run_context.source or Path(self.src_edit.text()))), part.suffix)
            if not others:
                return False
            part = others[-1]
            if _same_path(part, (self._run_context.source or Path(self.src_edit.text()))):
                return False
            self.write(f"[resume] The same source has an unfinished output elsewhere: {part}\n")
        meta = self._unfinished_registry.read_sidecar(part)
        if self._resume_coordinator.has_chain_metadata(meta):
            chain = self._resume_coordinator.load_chain_metadata(meta)
            if chain is None:
                done = 0.0
            else:
                segment_done = (self._probe_duration(part) or 0.0) if part.is_file() else 0.0
                done = chain["progress_offset"] + segment_done
        else:
            done = (self._probe_duration(part) or 0.0) if part.is_file() else 0.0
        threshold = self._resume_coordinator.minimum_useful_duration(self.duration)
        assessment = self._resume_coordinator.assess_orphan(
            part, done, self.duration, meta, settings_signature(self.collect_settings()),
            UnfinishedStore.source_fingerprint((self._run_context.source or Path(self.src_edit.text()))))
        same_settings = assessment.same_settings
        same_source = assessment.same_source
        same = assessment.matches
        if not meta:
            why = "no settings record next to it (made by an older version)"
        elif not same_source:
            why = "the source file differs from the one it was made from"
        elif not same_settings:
            why = "the conversion settings changed"
        else:
            why = "same settings"
        def start_over():
            self._unfinished_registry.discard(part)
            return False
        unattended = self._queue_controller.unattended(
            self.after_action_combo.currentData(), after_action.NONE)
        orphan_action = self._resume_coordinator.orphan_action(assessment, unattended)
        if orphan_action == ResumeAction.ORPHAN_TOO_SHORT:
            self.write(f"[resume] Found {part.name}, but only {format_time(done)} of it could be read "
                       f"(less than {format_time(threshold)}) - not worth resuming, starting over.\n")
            return start_over()
        self.write(f"[resume] Found an unfinished {part.name}: {format_time(done)} encoded, "
                   f"{why}.\n")
        if orphan_action == ResumeAction.ORPHAN_AUTO_RESUME:
            self.write(f"[resume] Found an unfinished {part.name} ({format_time(done)} encoded) "
                       "- continuing it.\n")
            self._queue_controller.batch_clean = False
            self._begin_resume(part)
            return True
        if orphan_action == ResumeAction.ORPHAN_START_OVER:
            self.write(f"[queue] An unfinished {part.name} from different settings was found - "
                       "starting over.\n")
            return start_over()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unfinished conversion found")
        total = format_time(self.duration) if self.duration else "?"
        note = ("The settings and the source are the same as before." if same else
                "The settings (or the source file) are NOT known to be the same as before, so the "
                "joined result may not play correctly.")
        box.setText(f"{part.name}\n\n" + f"A previous run of this file stopped after about "
                    f"{format_time(done)} of {total}.\n\n{note}")
        resume_btn = box.addButton("Resume from here" if same else "Resume anyway",
                                   QMessageBox.ButtonRole.AcceptRole)
        over_btn = box.addButton("Start over", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(resume_btn if same else over_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is resume_btn:
            self._begin_resume(part)
            return True
        if clicked is over_btn:
            return start_over()
        self._resume_coordinator.output_override = None        # Cancel: touch nothing, run nothing
        self._run_context.destination = None
        transition_safely(self._run_lifecycle, RunState.STOPPED, log_suppressed)
        return True

    def _warn(self, title, text):
        """A dialog when someone is watching; just a log line in an unattended queue (a modal
        box would hold the whole night's queue until somebody clicks it)."""
        if self._queue_controller.running:
            self.write(f"[warning] {title}: {text}\n")
            self._queue_controller.batch_clean = False
            return
        QMessageBox.warning(self, title, text)

    def _finalize_run_error(self, message, *, process_start_failed=False):
        """Send setup/start errors through the same terminal path as a failed run.

        In queue mode this records a failed item and stops the batch without
        leaving the queue marked active or opening a modal dialog overnight.
        """
        if process_start_failed and self.proc is None:
            return  # FailedToStart and waitForStarted(False) can report the same failure.
        self.write(f"[error] {message}\n")
        if self._resume_coordinator.concat_phase:
            self._on_concat_finished(1, QProcess.ExitStatus.CrashExit, message)
            return
        self._warn("Could not continue conversion", message)
        self._on_finished(1, QProcess.ExitStatus.CrashExit, failure_message=message)

    def _launch(self, cmd):
        """Shared QProcess launch tail for the main conversion, resume
        segments, and the resume part-concat step - anything that needs to
        run a command through the Activity log / progress machinery.
        Deliberately does NOT auto-switch to the Activity tab anymore -
        starting a conversion shouldn't yank the user's view away from
        whatever panel they were on; they can still open Activity
        themselves whenever they want to check the log."""
        self.write("$ " + subprocess.list2cmdline(cmd) + "\n")
        if self._run_lifecycle.state in (RunState.STARTING, RunState.RESUMING):
            transition_safely(self._run_lifecycle, RunState.ENCODING, log_suppressed)
        self.set_state("run", "Converting\u2026 0%")

        try:
            self.proc, started, job_bound = self._conversion_runner.start(
                cmd, using_handbrake=self.using_hb, duration=self.duration,
                resume_offset=self._resume_coordinator.progress_offset)
        except TypeError:
            self.proc, started, job_bound = self._conversion_runner.start(
                cmd, using_handbrake=self.using_hb, duration=self.duration,
                resume_offset=self._resume_coordinator.progress_offset)
        except OSError as error:
            self.proc = self._conversion_runner.process
            self._finalize_run_error(f"Could not start encoder: {error}")
            return
        if not started:
            self._finalize_run_error(
                "Failed to start the encoder process.", process_start_failed=True)
            return
        if not job_bound and sys.platform.startswith("win"):
            if not getattr(self, "_job_warned", False):         # the OS ends it with the app - or not
                self._job_warned = True
                self.write("[warn] Could not bind the encoder to the app (job object): if the app is "
                           "killed, ffmpeg may keep running in the background.\n")
        # Idempotent: a resume segment / concat step / next queue item just
        # renews it. Released again in _on_finished / _on_concat_finished.
        # (Honours the "Keep the PC awake" toggles - see _apply_power_state.)
        self._apply_power_state()

        self.proc_psutil = None
        if HAVE_PSUTIL:
            try:
                self.proc_psutil = psutil.Process(self.proc.processId())
                self.proc_psutil.cpu_percent(interval=None)  # first call is meaningless by design; primes it
                # Apply launch priority now that we have a real pid (QProcess has no
                # creationflags/preexec_fn hook the way subprocess.Popen does).
                self._set_priority(self.proc_psutil, self.priority_combo.currentText(), quiet=True)
            except Exception:
                self.proc_psutil = None

        self.update_run_controls()
        QTimer.singleShot(1000, self.sample_resources)
        if self.preview_path is not None:
            QTimer.singleShot(1500, self.poll_preview)

    def _set_priority(self, pp, level, quiet=False):
        try:
            if sys.platform.startswith("win"):
                m = {"Efficiency (low)": getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", None),
                     "Balanced": getattr(psutil, "NORMAL_PRIORITY_CLASS", None),
                     "Performance (high)": getattr(psutil, "HIGH_PRIORITY_CLASS", None)}
                val = m.get(level)
                if val is not None:
                    pp.nice(val)
            else:
                pp.nice(PRIORITY_NICE.get(level, 0))
            if not quiet:
                self.write(f"[live] Priority set to {level}.\n")
        except Exception as e:
            if not quiet:
                self.write(f"[live] Priority apply failed: {e}\n")
                QMessageBox.critical(self, "Live priority", str(e))

    # ---- QProcess signal handlers --------------------------------------
    # ---- parser lifecycle ------------------------------------------------
    def _reset_run_parsers(self, using_hb=False):
        """Fresh line assembler, progress parser and error-hint scanner for
        the encoder process that is about to take over the progress bar.

        Called from all three places a new process starts: a fresh run, a
        queue item, and a resume segment. The resume one is the easy one to
        forget, because it looks like a continuation of the same job - but
        it is a brand-new ffmpeg with its own out_time restarting at zero,
        and a stale half-line left in the assembler from the previous
        segment would be prepended to its first line of output.
        """
        self._hints = ErrorHintScanner(using_handbrake=using_hb)

    def _reset_test_clip_parsers(self, using_hb=False):
        """Test-clip renders get their own assembler and parser, separate
        from the real conversion's, because a clip can be rendered while a
        conversion is running. They deliberately share the error-hint
        scanner - see ErrorHintScanner.feed_gpu_tonemap_only()."""
        self._test_clip_assembler = LineAssembler()
        self._test_clip_parser = make_progress_parser(using_hb)

    def _handle_line(self, line, stats=None, consumed=False):
        self._hints.configure(self._current_encoder_label, self._hwaccel_decode_on)
        self._hints.feed(line)
        if stats is not None:
            self.apply_stats(stats)
        elif not consumed and line.strip():
            self.write(line + "\n")

    def apply_stats(self, stats):
        if stats.out_sec is not None:
            self.current_out_sec = stats.out_sec
        pct = stats.pct
        self._latest_progress_stats = stats
        if pct is not None:
            self.pbar.setValue(int(pct * 10))
            self.set_state("paused" if self.paused else "run", f"Converting\u2026 {pct:.0f}%")
        else:
            self._update_inline_progress_status()
        self._update_frame_strip_during_conversion()

    def _on_process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.proc is not None:
            self._finalize_run_error(
                "The encoder process could not be started.", process_start_failed=True)

    def _on_finished(self, exit_code, exit_status, failure_message=None):
        power.release()
        if hasattr(self, "frame_strip"):
            self.frame_strip.set_scrub_enabled(True)
            if exit_code == 0 and self.duration > 0:
                self.frame_strip.set_position(1.0)
                self.frame_strip.set_time_info(self.duration, self.duration)
        if self._resume_coordinator.concat_phase:
            self._on_concat_finished(exit_code, exit_status, failure_message)
            return
        was_stopping = self.stopping
        self._conversion_runner.record_result(False)
        final_dst = self._resume_coordinator.final_destination or (self._run_context.destination or Path(self.dst_edit.text()))
        active_path = self._resume_coordinator.output_override or final_dst
        if was_stopping:
            self.write("\nConversion stopped by user.\n")
            ok = False
        elif exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self.write("\nFinished successfully.\n")
            ok = True
        else:
            if failure_message:
                self.write(f"\n[error] {failure_message}\n")
            else:
                self.write(f"\nConversion failed (exit code {exit_code}); see log.\n")
            ok = False
            if (not self.using_hb and self._hints.mp4_track
                    and active_path.suffix.lower() in (".mp4", ".mov", ".m4v")):
                self.write(
                    "[hint] This looks like an MP4/MOV container limitation, not a "
                    "conversion error: some copied audio or subtitle track (e.g. "
                    "TrueHD/Atmos, PGS, SRT) can't be muxed into MP4 unchanged. "
                    "Switch the Container option to MKV and try again.\n")
                self._warn(
                    "MP4 can't hold one of these tracks",
                    "The conversion failed while writing the output file, not while "
                    "tone-mapping the video.\n\n"
                    "One of the audio or subtitle tracks being copied through "
                    "unchanged (for example TrueHD/Atmos audio, or PGS/SRT "
                    "subtitles) isn't something the MP4/MOV container can store "
                    "as-is.\n\n"
                    "Switch the Container option to MKV and run the conversion "
                    "again \u2014 MKV accepts all of these track types unmodified.")
            elif self._hints.hw_encoder:
                label = self._current_encoder_label or "the selected hardware encoder"
                backend_note = " (HandBrakeCLI)" if self.using_hb else ""
                self.write(
                    f"[hint] This looks like a GPU/driver limitation, not a conversion "
                    f"error: '{label}'{backend_note} failed to open, which usually means "
                    f"this GPU or its driver doesn't actually support that codec (e.g. AV1 "
                    f"NVENC needs an RTX 40-series or newer card; AV1 AMF needs RDNA3). "
                    f"Switch to a different encoder (a CPU encoder always works) and try "
                    f"again.\n")
                self._warn(
                    "Hardware encoder unavailable",
                    f"The conversion failed while opening '{label}'{backend_note}, not "
                    "while tone-mapping the video.\n\n"
                    "This usually means the installed GPU or its driver doesn't "
                    "actually support that codec, even though it's listed as available "
                    "\u2014 for example, AV1 encoding over NVENC needs an RTX 40-series "
                    "(or newer) card, and AV1 over AMD's AMF needs an RDNA3 card; older "
                    "GPUs can't open those encoders even though they're offered in the "
                    "dropdown.\n\n"
                    "Pick a different encoder in the Encoder dropdown (a CPU encoder "
                    "such as CPU \u00b7 H.265 always works, or try HEVC/H.264 on this "
                    "GPU's own hardware encoder if it supports those) and run the "
                    "conversion again.")
            elif self._hints.hw_decode:
                backend_note = "HandBrakeCLI's NVDEC decoding" if self.using_hb else "FFmpeg's hardware decode"
                self.write(
                    f"[hint] This looks like a GPU/driver limitation on the decode side, "
                    f"not a conversion error: {backend_note} failed to initialise for "
                    f"this source. Turn off \"Hardware-accelerated decode\" and try again "
                    f"\u2014 software decoding always works, just slower.\n")
                self._warn(
                    "Hardware decode unavailable",
                    f"The conversion failed while setting up {backend_note} for this "
                    "video, not while tone-mapping it.\n\n"
                    "This usually means the GPU or its driver can't hardware-decode "
                    "this particular stream (for example, some GPUs or drivers don't "
                    "handle 10-bit HEVC with Dolby Vision layers over hardware decode "
                    "even though they handle plain HEVC fine).\n\n"
                    "Turn off the \"Hardware-accelerated decode\" toggle and run the "
                    "conversion again \u2014 software decoding is slower but doesn't "
                    "depend on GPU driver support.")
        if self._hints.gpu_tonemap:
            # Outside the exit-code branch on purpose - this doesn't make
            # FFmpeg exit non-zero, it just quietly writes broken frames
            # (see _GPU_TONEMAP_VRAM_HINTS), so it can fire on the ok path.
            self.write(
                "[hint] Libplacebo (GPU tone mapping) ran out of GPU memory partway "
                "through and substituted solid-colour frames for the ones it couldn't "
                "render - that's very likely what any green (or otherwise flat-"
                "coloured) stretches in the output are. Try closing other GPU-heavy "
                "apps (browser tabs, games) to free VRAM, or switch Tone mapping to a "
                "CPU curve (Hable/Reinhard/Mobius/Linear) \u2014 those never touch the "
                "GPU and aren't affected.\n")
            self._warn(
                "GPU tone mapping ran low on GPU memory",
                "Libplacebo's GPU tone-mapping path (BT.2390/Spline/BT.2446A/ST2094-*) "
                "ran out of GPU memory while rendering \u2014 the log shows "
                "VK_ERROR_OUT_OF_DEVICE_MEMORY. FFmpeg doesn't treat this as fatal, so "
                "the run finished, but every frame it couldn't render was replaced with "
                "a solid colour (usually green) instead of erroring out.\n\n"
                "Try closing other GPU-heavy apps to free video memory, or switch Tone "
                "mapping to a CPU curve \u2014 Hable, Reinhard, Mobius, or Linear all use "
                "the classic zscale+tonemap path instead of the GPU/Vulkan one, so "
                "they're unaffected by this.")
        was_resume_segment = bool(self._resume_coordinator.parts) or self._resume_coordinator.final_destination is not None
        if (not ok
                and not was_stopping
                and not self.using_hb
                and self._hints.gpu_tonemap
                and self._dv_profile != 5
                and not was_resume_segment
                and getattr(self, "current_out_sec", 0.0) < 5.0
                and not getattr(self, "_gpu_tonemap_fallback_used", False)
                and getattr(self, "_job_tonemap_fallback_attempts", 0) < 1):
            fallback_label = "Hable \u00b7 CPU tonemap"
            self._job_tonemap_fallback_attempts = getattr(self, "_job_tonemap_fallback_attempts", 0) + 1
            self._gpu_tonemap_fallback_used = True
            reason = "Libplacebo (GPU tone mapping) ran out of GPU/Vulkan memory at the start of encoding."
            if self._confirm_tonemap_fallback(fallback_label, reason):
                self._job_tonemap_override = fallback_label
                if getattr(self, "_dv_rpu_auto_enabled", False):
                    self._job_dv_rpu_override = False
                self.write(
                    f"[tonemap] Libplacebo (GPU tone mapping) ran out of GPU/Vulkan memory "
                    f"\u2014 retrying this file with {fallback_label} (user setting preserved).\n"
                )
                self._release_proc()
                self.proc_psutil = None
                self.stopping = False
                self.paused = False
                if DestinationPlan.is_part_file(active_path):
                    self._unfinished_registry.discard(active_path)
                self._resume_coordinator.reset()
                transition_safely(self._run_lifecycle, RunState.IDLE, log_suppressed)
                self.update_run_controls()
                QTimer.singleShot(0, self.start)
                return
            self._tonemap_fallback_declined_for_current = True
            self._release_proc()
            self.proc_psutil = None
            self.stopping = False
            self.paused = False
            if DestinationPlan.is_part_file(active_path):
                self._unfinished_registry.discard(active_path)
            self._resume_coordinator.reset()
            transition_safely(self._run_lifecycle, RunState.STOPPED, log_suppressed)
            self.set_state("idle", "Skipped")
            self._clear_job_tonemap_override()
            self.update_run_controls()
            if self._queue_controller.running:
                self._queue_controller.batch_clean = False
                QTimer.singleShot(0, self._start_next_queue_item)
            return
        self._release_proc()
        self.proc_psutil = None
        self.stopping = False
        self.paused = False

        is_part_file = DestinationPlan.is_part_file(active_path)
        finish_action = RunWorkflow.finish_action(
            ok, was_stopping, was_resume_segment, is_part_file)
        if finish_action == FinishAction.CONCAT:
            # This process only encoded one segment of the source (from a
            # previous resume point onward), not the whole file - stitch it
            # onto the earlier part(s) instead of treating the item as done.
            self._resume_coordinator.parts.append(active_path)
            self._resume_coordinator.output_override = None
            self._start_concat_phase()
            return

        if finish_action == FinishAction.PUBLISH:
            publish_error = self._unfinished_registry.publish(
                active_path, final_dst, self._conversion_runner.publish_temp)
            published = publish_error is None
            if publish_error is not None:
                self.write(f"[error] Could not rename {Path(active_path).name} to "
                           f"{Path(final_dst).name}: {publish_error}\n")
            ok = published
            finish_action = RunWorkflow.finish_action(
                ok, was_stopping, was_resume_segment, is_part_file,
                part_published=published)
        if finish_action == FinishAction.RESUME:
            if failure_message is None and self._offer_resume(active_path):
                return  # a new segment (or a fresh restart) was already launched
            finish_action = RunWorkflow.after_resume_offer(was_stopping, False)
        ok = finish_action == FinishAction.DONE
        if finish_action == FinishAction.DONE:
            self._conversion_runner.record_result(True)
            self.pbar.setValue(1000)
            transition_safely(self._run_lifecycle, RunState.DONE, log_suppressed)
            self.set_state("done", "Done \u00b7 saved to output")
            self._note_item_result(self._validate_output(final_dst))
        elif finish_action == FinishAction.STOPPED:
            transition_safely(self._run_lifecycle, RunState.STOPPED, log_suppressed)
            self.set_state("idle", "Stopped")
        else:
            transition_safely(self._run_lifecycle, RunState.FAILED, log_suppressed)
            self.set_state("error", "Failed \u2014 see activity log")
            # A failed attempt may have useful encoded data on disk. Clear
            # only the in-memory chain so the next Start cannot interpret
            # those kept files as stale files to delete.
            self._resume_coordinator.reset()
        if was_stopping:
            if DestinationPlan.is_part_file(active_path):
                self._unfinished_registry.discard(active_path)  # Stop = cancel: remove partial output
        elif not ok and DestinationPlan.is_part_file(active_path):
            self.write(f"[info] The unfinished output was kept as {active_path.name}.\n")
        self._append_history_entry(
            str((self._run_context.source or Path(self.src_edit.text()))), final_dst if ok else active_path,
            "done" if ok else ("stopped" if was_stopping else "failed"), (self._run_context.kind or self.kind))
        self._clear_job_tonemap_override()
        self.update_run_controls()
        completion = self._queue_controller.complete_item(ok, was_stopping)
        if completion.action == "advance":
            QTimer.singleShot(250, self._start_next_queue_item)
        elif completion.action == "stopped":
            # User pressed Stop mid-queue - not a failure, so don't notify
            # or continue to the next item.
            self.write("[queue] Stopped by user.\n")
        elif completion.action == "failed":
            self.write("[queue] Stopped because one item failed.\n")
            self._notify_completion(False, f"Queue stopped: {active_path.name} failed to convert.")
        elif completion.action == "standalone":
            # Standalone (non-queue) run - this is the only place its
            # completion is ever reported, so notify right here rather
            # than waiting for a "queue finished" point that doesn't
            # exist for it. Queue completions are notified once, from
            # _start_next_queue_item(), when the queue actually drains.
            self._notify_completion(ok, f"{'Finished converting' if ok else 'Conversion failed for'} {active_path.name}.")
            if ok:
                self._all_done()

    # ---- after-conversion action (sleep / hibernate / shut down) ------------
    def _note_item_result(self, verified):
        """Called for every item that finished OK: a questionable result
        (output check not happy, or the GPU tone-mapper ran out of memory and
        wrote flat frames) means the person should look at it before the PC
        is put to sleep or switched off."""
        if not verified or self._hints.gpu_tonemap:
            self._queue_controller.batch_clean = False

    def _after_action_label(self, action):
        return {after_action.SLEEP: self.tr("Sleep"), after_action.HIBERNATE: self.tr("Hibernate"),
                after_action.SHUTDOWN: self.tr("Shut down")}.get(action, "")

    def _retranslate_after_action(self):
        combo = getattr(self, "after_action_combo", None)
        if combo is None:
            return
        self.after_action_label.setText(self.tr("After conversion:"))
        combo.blockSignals(True)
        for i, text in enumerate((self.tr("Do nothing"), self.tr("Sleep"),
                                  self.tr("Hibernate"), self.tr("Shut down"))):
            combo.setItemText(i, text)
        combo.blockSignals(False)
        tip = self.tr("Action to perform after all conversions in the queue complete successfully.")
        self.after_action_label.setToolTip(tip)
        combo.setToolTip(tip)

    def _all_done(self):
        """Everything the person asked for finished successfully."""
        action = self.after_action_combo.currentData()
        if action in (None, after_action.NONE) or not after_action.supported():
            return
        if not self._queue_controller.batch_clean:
            self.write("[after] Not %s: a result needs a look first (see the messages above).\n"
                       % {after_action.SLEEP: "going to sleep", after_action.HIBERNATE: "hibernating",
                          after_action.SHUTDOWN: "shutting down"}[action])
            return
        self._start_after_action_countdown(action)

    def _start_after_action_countdown(self, action):
        if self._after_countdown is not None:
            return
        seconds = after_action.COUNTDOWN_SECONDS
        if not after_action.begin(action, seconds):
            self.write("[after] Could not schedule the action - the PC is left as it is.\n")
            return
        box = self._themed_message_box(QMessageBox.Icon.Information, "HDR to SDR Video Converter", "")
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        template = self.tr("{action} in {seconds} s. Click Cancel to stop it.")
        ctrl = AfterActionCountdown(box, template, self._after_action_label(action), seconds, parent=self)
        ctrl.expired.connect(lambda: self._after_countdown_done(action, True))
        ctrl.cancelled.connect(lambda: self._after_countdown_done(action, False))
        self._after_countdown = ctrl
        self.write(f"[after] All done - {action} in {seconds} s unless cancelled.\n")
        ctrl.start()

    def _after_countdown_done(self, action, expired):
        self._after_countdown = None
        if expired:
            self.write(f"[after] Countdown finished - {action}.\n")
            if not after_action.execute(action):
                self.write("[after] Windows did not accept the request; the PC is left as it is.\n")
        else:
            after_action.abort(action)
            self.write("[after] Cancelled - the PC stays on.\n")

    def _cancel_after_action_countdown(self):
        ctrl = self._after_countdown
        if ctrl is not None:
            ctrl.cancel()

    # ---- Pro -> Mini ---------------------------------------------------------
    def _switch_to_mini(self):
        """Hand over to the compact Mini window and take this one off screen.
        Everything keeps running - this window is the conversion engine Mini
        drives, so a running conversion just carries on and Mini shows its
        progress. The Mini window is created on demand (when the app was
        started with "Always open Pro mode" there isn't one yet)."""
        from hdr2sdr.ui.mini_window import MiniModeWindow    # circular at import time
        mini = getattr(self, "_mini_window", None)
        if mini is None:
            mini = MiniModeWindow(self)
            icon = self.windowIcon()
            if not icon.isNull():
                mini.setWindowIcon(icon)
        mini.sync_from_engine()
        mini.show()
        mini.raise_()
        mini.activateWindow()
        # Same two-step take-down MiniModeWindow.open_pro() uses, for the same
        # reason (a window hidden in the same event-loop turn another one is
        # shown can be left painted on screen on Windows).
        QTimer.singleShot(0, self._dismiss_for_mini)
        QTimer.singleShot(120, self._dismiss_for_mini)

    def _dismiss_for_mini(self):
        mini = getattr(self, "_mini_window", None)
        if mini is None or not mini.isVisible():
            return                      # Mini isn't actually up: never leave the person with no window
        self.setWindowOpacity(0.0)
        self.hide()

    # ---- secondary power options (behind the "..." button) ----------------
    def _build_power_dialog(self):
        """Card with the two less-used options, shown over the "..." button
        (same rounded look as the Tools dropdown). Built once; the widgets
        keep the attribute names they always had (keep_display_chk,
        after_action_label, after_action_combo)."""
        panel = OverlayPanel(self)
        panel.add_title(self.tr("More options"))

        self.keep_awake_chk = ToggleSwitch("Keep the PC awake while converting")
        self.keep_awake_chk.setChecked(self._power_pref("keep_awake", True))
        panel.body.addWidget(self.keep_awake_chk)
        # setVisible() only AFTER the widget has a parent: on a parentless
        # widget setVisible(True) opens it as its own tiny top-level window
        # (the brief window that used to flash at startup).
        self.keep_awake_chk.setVisible(sys.platform.startswith("win"))

        self.keep_display_chk = ToggleSwitch("Also keep the screen on")
        self.keep_display_chk.setChecked(self._power_pref("keep_display_on", False))
        self.keep_display_chk.setEnabled(self.keep_awake_chk.isChecked())
        panel.body.addWidget(self.keep_display_chk)
        self.keep_display_chk.setVisible(sys.platform.startswith("win"))

        # What the PC does when EVERYTHING has converted successfully (a whole
        # queue, resumed parts joined). Deliberately NOT remembered between
        # launches: a "Shut down" chosen once must not silently apply to some
        # unrelated conversion next week. Windows only (runtime/after_action.py).
        after_row = QHBoxLayout()
        self.after_action_label = QLabel("After conversion:")
        after_row.addWidget(self.after_action_label)
        self.after_action_combo = RoundedComboBox()
        for act in after_action.ACTIONS:
            self.after_action_combo.addItem("", act)
        loosen_combo(self.after_action_combo, 8)
        after_row.addWidget(self.after_action_combo, 1)
        panel.body.addLayout(after_row)
        for w_ in (self.after_action_label, self.after_action_combo):
            w_.setVisible(after_action.supported())
        self.after_action_combo.currentIndexChanged.connect(self._refresh_power_more_btn)

        # Back to the compact Mini window - also while a conversion is
        # running; Mini then just shows that conversion's progress.
        self.mini_mode_btn = GlassButton(self.tr("Mini mode"))
        set_role(self.mini_mode_btn, "panel-toggle")
        self.mini_mode_btn.setMinimumHeight(32)
        self.mini_mode_btn.clicked.connect(lambda: (panel.hide(), QTimer.singleShot(0, self._switch_to_mini)))
        panel.body.addWidget(self.mini_mode_btn)
        self.power_dialog = panel

    def _open_power_dialog(self):
        panel = self.power_dialog
        if panel.isVisible():
            panel.hide()
            return
        # The press on this very button already closed the open panel (outside-
        # click dismissal); don't let the same click's "clicked" reopen it.
        if time.monotonic() - panel.closed_at < 0.25:
            return
        t = self.tokens
        panel.set_theme(t['bg_card'], t['border'])
        panel.open_above(self.power_more_btn, width=390)
        self._refresh_power_more_btn()

    def _refresh_power_more_btn(self, *_):
        """The "..." button lights up when something inside it differs from the
        defaults, so a non-default choice (above all a planned shutdown) is
        never completely out of sight."""
        btn = getattr(self, "power_more_btn", None)
        if btn is None:
            return
        non_default = (self.keep_display_chk.isChecked() or not self.keep_awake_chk.isChecked()
                       or self.after_action_combo.currentData() not in (None, after_action.NONE))
        btn.setProperty("icon_accent", "always" if non_default else "auto")
        btn.update()

    # ---- keep-awake preferences -----------------------------------------
    def _power_pref(self, key, default):
        value = self.i18n.settings.value(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _on_power_pref_changed(self, _checked=False):
        awake = self.keep_awake_chk.isChecked()
        self.keep_display_chk.setEnabled(awake)
        self._refresh_power_more_btn()
        self.i18n.settings.setValue("keep_awake", awake)
        self.i18n.settings.setValue("keep_display_on", self.keep_display_chk.isChecked())
        if self._is_proc_running():
            self._apply_power_state()          # takes effect on the running conversion at once

    def _apply_power_state(self):
        """Hold (or drop) the Windows keep-awake request according to the two
        toggles. Idempotent; GUI thread only (SetThreadExecutionState is per
        thread)."""
        if self.keep_awake_chk.isChecked():
            power.keep_awake(display=self.keep_display_chk.isChecked())
        else:
            power.release()

    # ---- resume-after-failure (FFmpeg backend only) ---------------------
    def _hwaccel_wanted(self):
        """The Hardware-accelerated decode toggle, unless a mid-run decode
        failure already switched this resume chain over to software."""
        return self.hwaccel_chk.isChecked() and not self._hwaccel_forced_off

    def _responsive_capture(self, cmd, timeout):
        """capture_text() on a worker thread while the window keeps repainting (user input is
        held back), so a long scan of a big file does not look like a frozen app."""
        import threading
        box = {}

        def work():
            try:
                box["out"] = capture_text(cmd, timeout=timeout)
            except Exception as e:
                box["err"] = e
        t = threading.Thread(target=work, daemon=True)
        t.start()
        app = QApplication.instance()
        while t.is_alive():
            if app is not None:
                app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 40)
            time.sleep(0.03)
        if "err" in box:
            raise box["err"]
        return box.get("out", "")

    def _probe_duration(self, path):
        key = self._file_key(path)
        cache = self.__dict__.setdefault("_probe_cache", {})
        if key is not None and key in cache:
            return cache[key]
        value = self._probe_duration_uncached(path)
        if key is not None:
            cache[key] = value
        return value

    @staticmethod
    def _file_key(path):
        try:
            st = Path(path).stat()
            return (str(path), st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    def _probe_duration_uncached(self, path):
        """Real muxed duration of an existing file, via ffprobe - used in
        preference to the -progress estimate when deciding where to resume
        from, since -progress can be a little ahead of what actually made
        it into the container before the process died."""
        probe = exe("ffprobe")
        if not probe:
            return None
        # The container header cannot be trusted for a file whose writer was killed (MKV may carry a
        # planned duration, MP4 has no index at all), so read the last real packet instead.
        try:
            self.status_label.setText("Checking the unfinished file\u2026")
            out = self._responsive_capture([probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                            "packet=pts_time", "-of", "csv=p=0", str(path)], 300)
            last = 0.0
            for line in out.splitlines():
                try:
                    last = max(last, float(line.strip().split(",")[0]))
                except ValueError:
                    continue
            if last > 0:
                return last
        except Exception:
            pass
        try:
            info = json.loads(capture_text(
                [probe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(path)], timeout=30))
            d = float(info.get("format", {}).get("duration") or 0)
            return d if d > 0 else None
        except Exception:
            return None

    def _offer_resume(self, active_path):
        """Called from _on_finished() on a failed run. Returns True if a
        resume segment (or a fresh restart) was launched in response, in
        which case the caller should treat this _on_finished() call as
        superseded rather than a final failure."""
        threshold = self._resume_coordinator.minimum_useful_duration(self.duration)
        segment_progress = self.current_out_sec - self._resume_coordinator.progress_offset
        hw_decode_died = (self._hints.hw_decode_runtime and self.hwaccel_chk.isChecked()
                          and not self._hwaccel_forced_off)
        unattended = self._queue_controller.running
        action = self._resume_coordinator.failure_action(
            backend_available=not self.using_hb,
            has_specific_hint=bool(self._hints.mp4_track or self._hints.hw_encoder or self._hints.hw_decode),
            hardware_decode_failed=hw_decode_died,
            enough_progress=segment_progress >= threshold,
            partial_exists=active_path.is_file(), unattended=unattended)
        if action == ResumeAction.UNAVAILABLE:
            return False
        if action == ResumeAction.SOFTWARE_DECODE:
            # Hardware *decode* broke mid-run (e.g. DXVA2 unable to copy a
            # frame out of GPU memory, exit code -12). What was encoded so
            # far is fine and encoding stays on the GPU - only the decoder
            # is swapped for the software one. No dialog: asking "resume?"
            # when the answer is always yes, hours into an unattended job,
            # just stalls it until someone walks past the screen.
            self._hwaccel_forced_off = True
            self.write(
                "[resume] Hardware decode failed mid-run (the decoder could not "
                "copy a frame out of GPU memory). Continuing from the last good "
                "point with software decode; encoding stays on the GPU.\n")
            self._begin_resume(active_path)
            return True
        if action == ResumeAction.DECODE_FAILED_TOO_EARLY:
            self.write(
                "[hint] Hardware decode failed before enough was encoded to resume "
                "from. Turn off \"Hardware-accelerated decode\" and start again.\n")
        if action in (ResumeAction.TOO_SHORT, ResumeAction.DECODE_FAILED_TOO_EARLY):
            self.write(f"[resume] Not offered: only {format_time(max(0.0, segment_progress))} was encoded "
                       f"(needs at least {format_time(threshold)}).\n")
            if self._resume_coordinator.parts:
                # A chained resume attempt that failed almost immediately -
                # resuming again is unlikely to help, but the earlier,
                # successfully-encoded part(s) are still good and shouldn't
                # be thrown away silently.
                parts_list = "\n".join(f"  {p}" for p in self._resume_coordinator.parts)
                self.write(
                    "[resume] This segment failed almost immediately, so resuming "
                    "again probably won't help. The earlier part(s) were kept, not "
                    f"deleted:\n{parts_list}\n")
            return False
        if action == ResumeAction.AUTOMATIC:
            # unattended: resuming is what the person would pick anyway (same as the decode case)
            self.write("[resume] The conversion failed part-way; resuming from the last good point.\n")
            self._queue_controller.batch_clean = False
            self._begin_resume(active_path)
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Resume conversion?"))
        dur_text = format_time(self.duration) if self.duration else self.tr("the end")
        box.setText(
            self.tr(
                "The conversion got to about {done} of {total} before failing.\n\n"
                "Resume picks up from close to that point instead of re-encoding "
                "the whole file again from the start. The parts get joined into "
                "the same output file once the rest finishes."
            ).format(done=format_time(self.current_out_sec), total=dur_text))
        resume_btn = box.addButton(self.tr("Resume from here"), QMessageBox.ButtonRole.AcceptRole)
        restart_btn = box.addButton(self.tr("Start over"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(resume_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is resume_btn:
            self._begin_resume(active_path)
            return True
        if clicked is restart_btn:
            # Get rid of this attempt's leftovers so start() doesn't have
            # to guess whether a stray file next to the real output is safe
            # to delete - it always is at this point, nothing else refers
            # to it.
            self._unfinished_registry.discard(active_path)
            for p in self._resume_coordinator.parts:
                self._unfinished_registry.discard(p)
            self._hwaccel_forced_off = False
            self._resume_coordinator.reset()
            QTimer.singleShot(0, self.start)
            return True
        return False

    def _begin_resume(self, active_path):
        transition_safely(self._run_lifecycle, RunState.RESUMING, log_suppressed)
        final_dst = self._resume_coordinator.final_destination or (self._run_context.destination or Path(self.dst_edit.text()))
        self._resume_coordinator.final_destination = final_dst
        active_path = Path(active_path)
        if not self._resume_coordinator.parts:
            meta = self._unfinished_registry.read_sidecar(active_path)
            chain = self._resume_coordinator.load_chain_metadata(meta)
            if chain is not None:
                self._resume_coordinator.restore_chain(chain, final_dst)
                part_duration = self._probe_duration(active_path) if active_path.is_file() else 0.0
                if not active_path.is_file() or (
                        part_duration is not None
                        and part_duration <= self._resume_coordinator.SAFETY_MARGIN_SECONDS
                        and self.current_out_sec <= self._resume_coordinator.progress_offset + self._resume_coordinator.SAFETY_MARGIN_SECONDS):
                    try:
                        active_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    if active_path != self._resume_coordinator.output_override:
                        try:
                            DestinationPlan.sidecar_for(active_path).unlink(missing_ok=True)
                        except OSError:
                            pass
                        self._unfinished_registry.forget(active_path)
                    self._write_part_sidecar(self._resume_coordinator.output_override)
                    self.write(
                        f"[resume] Restored {len(self._resume_coordinator.parts)} earlier part(s); "
                        f"continuing from {format_time(self._resume_coordinator.seek_seconds)}.\n")
                    self._start_resume_segment()
                    return
        try:
            part_duration = self._probe_duration(active_path)
            segment = self._resume_coordinator.prepare_segment(
                active_path, final_dst, part_duration, self.current_out_sec,
                self._resume_coordinator.progress_offset)
        except OSError as e:
            self._hwaccel_forced_off = False
            self._finalize_run_error(f"Could not prepare the partial file for resuming: {e}")
            return
        for retired in (segment.moved_from, segment.kept_part):
            if retired is not None and retired != segment.next_output:
                try:
                    DestinationPlan.sidecar_for(retired).unlink(missing_ok=True)
                except OSError:
                    pass
                self._unfinished_registry.forget(retired)
        self._write_part_sidecar(segment.next_output)
        self.write(f"[resume] Kept {segment.kept_part.name} as part {len(self._resume_coordinator.parts)}; "
                   f"continuing from {format_time(segment.seek_seconds)}.\n")
        self._start_resume_segment()

    def _start_resume_segment(self):
        self.stopping = False
        self.paused = False
        self.pbar.setValue(0)
        self.speed_label.setText("")
        self.speed_label.hide()
        self._reset_run_parsers(self.using_hb)
        self._current_encoder_label = self.encoder_combo.currentText()
        self._hwaccel_decode_on = self._hwaccel_wanted()
        # Live preview works the same for a resume segment as a fresh
        # start - same vf chain, -ss just moves the start point. Reset
        # preview_mtime too so we don't compare against a stale file.
        try:
            self.preview_path = Path(tempfile.gettempdir()) / f"hdr2sdr_live_preview_{os.getpid()}.jpg"
            self.preview_mtime = 0
            self.preview_path.unlink(missing_ok=True)
            self.preview_label.setText(self.tr("Waiting for the first preview frame\u2026"))
            self._set_preview_meta("")
            cmd, warnings = build_command(self._settings_with_tracks(announce=True))
        except OSError as error:
            self._finalize_run_error(f"Could not prepare the resumed segment: {error}")
            return
        for warning in warnings:
            self.write(warning)
        self._launch(cmd)


    def _start_concat_phase(self):
        transition_safely(self._run_lifecycle, RunState.CONCATENATING, log_suppressed)
        self._resume_coordinator.concat_phase = True
        final_dst = self._resume_coordinator.final_destination
        parts = list(self._resume_coordinator.parts)
        self.write(f"[resume] All segments encoded \u2014 joining {len(parts)} part(s) into "
                   f"{final_dst.name}\u2026\n")
        concat_target = DestinationPlan(final_dst).part
        self._resume_coordinator.concat_list_path = DestinationPlan.concat_list_for(concat_target)
        try:
            cmd, list_path = self._resume_coordinator.concat_command(
                parts, concat_target, _ffmpeg_exe(),
                build_container_args, list(self._resume_coordinator.outpoints))
        except OSError as error:
            self._finalize_run_error(f"Could not prepare the concat list: {error}")
            return
        self._resume_coordinator.concat_list_path = list_path
        self._launch(cmd)

    def _on_concat_finished(self, exit_code, exit_status, failure_message=None):
        power.release()
        self._resume_coordinator.concat_phase = False
        self._release_proc()
        was_stopping = self.stopping
        self.stopping = False
        self.paused = False
        if self._resume_coordinator.concat_list_path is not None:
            try:
                self._resume_coordinator.concat_list_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._resume_coordinator.concat_list_path = None
        final_dst = self._resume_coordinator.final_destination or (self._run_context.destination or Path(self.dst_edit.text()))
        result = self._resume_coordinator.finalize_concat(
            was_stopping, exit_code, exit_status == QProcess.ExitStatus.NormalExit,
            DestinationPlan(final_dst).part, final_dst,
            lambda part, destination: self._unfinished_registry.publish(
                part, destination, self._conversion_runner.publish_temp),
            self._unfinished_registry.discard)
        concat_action = RunWorkflow.concat_action(result.succeeded, was_stopping)
        ok = concat_action == FinishAction.DONE
        parts = list(result.parts)
        self._conversion_runner.record_result(ok)
        if ok:
            for part in parts:
                self._unfinished_registry.forget(part)
            self.write("\nFinished successfully (resumed).\n")
            self._hwaccel_forced_off = False
            self._resume_coordinator.reset()
            self.pbar.setValue(1000)
            transition_safely(self._run_lifecycle, RunState.DONE, log_suppressed)
            self.set_state("done", "Done \u00b7 saved to output")
            self._note_item_result(self._validate_output(final_dst))
            self._append_history_entry(str((self._run_context.source or Path(self.src_edit.text()))), final_dst, "done", (self._run_context.kind or self.kind))
            self.update_run_controls()
            completion = self._queue_controller.complete_item(True, False)
            if completion.action == "advance":
                QTimer.singleShot(250, self._start_next_queue_item)
            elif completion.action == "standalone":
                self._notify_completion(True, f"Finished converting {final_dst.name}.")
                self._all_done()
        else:
            reason = ("Join cancelled by user.\n" if was_stopping else
                      (f"Could not prepare/run the join: {failure_message}\n" if failure_message else
                       "Failed to join the resumed segments; see log.\n"))
            self.write("\n" + reason)
            parts_list = "\n".join(f"  {p}" for p in parts)
            self.write(f"[resume] The already-encoded segment(s) were kept so nothing is lost:\n"
                       f"{parts_list}\n"
                       f"They use identical codec settings, so they can be joined manually "
                       f"(e.g. with FFmpeg's concat demuxer or mkvmerge) if needed.\n")
            self._warn(
                "Could not join resumed segments",
                (failure_message or "Joining the segments into the final file failed.")
                + "\n\nNothing was lost \u2014 the encoded part files are still on disk:\n\n"
                + parts_list +
                "\n\nYou can join them manually (they share identical codec settings), or "
                "delete them and start the conversion over.")
            self._hwaccel_forced_off = False
            self._resume_coordinator.reset()
            terminal = (RunState.STOPPED if concat_action == FinishAction.STOPPED
                        else RunState.FAILED)
            transition_safely(self._run_lifecycle, terminal, log_suppressed)
            self.set_state("error", "Failed \u2014 see activity log")
            self._append_history_entry(
                str((self._run_context.source or Path(self.src_edit.text()))), final_dst, "stopped" if was_stopping else "failed", (self._run_context.kind or self.kind))
            self.update_run_controls()
            completion = self._queue_controller.complete_item(False, was_stopping)
            if completion.action == "stopped":
                self.write("[queue] Stopped by user.\n")
            elif completion.action == "failed":
                self.write("[queue] Stopped because one item failed.\n")
                self._notify_completion(False, f"Queue stopped: joining {final_dst.name}'s segments failed.")
            elif completion.action == "standalone":
                self._notify_completion(False, f"Failed to join the resumed segments for {final_dst.name}.")

    def _validate_output(self, output):
        """Fast post-flight check: verify the muxed file exists, has a video
        stream and remains close to the source duration.  It does not decode
        every frame, so it is safe to run automatically after each queue item."""
        probe = exe("ffprobe")
        if not output.is_file():
            self.write("[verify] Output file is missing.\n")
            return False
        if not probe:
            self.write(f"[verify] Output exists ({output.stat().st_size / 1024 ** 2:.1f} MB); ffprobe unavailable for a deeper check.\n")
            return True
        try:
            info = json.loads(capture_text(
                [probe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                 "-of", "json", str(output)], timeout=30))
            video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            duration = float(info.get("format", {}).get("duration") or 0)
            if not video:
                self.write("[verify] Warning: no video stream found in output.\n")
                return False
            elif self.duration and abs(duration - self.duration) > max(5, self.duration * .02):
                self.write(f"[verify] Warning: output duration {format_time(duration)} differs from source {format_time(self.duration)}.\n")
                return False
            else:
                self.write(f"[verify] OK · {video.get('width')}×{video.get('height')} · {format_time(duration)} · {output.stat().st_size / 1024 ** 2:.1f} MB\n")
            return True
        except Exception as e:
            self.write(f"[verify] Could not inspect output: {e}\n")
            return False

    def sample_resources(self):
        if not (self._is_proc_running()):
            return
        parts = []
        if HAVE_PSUTIL and self.proc_psutil is not None:
            try:
                parts.append(f"This process: {self.proc_psutil.cpu_percent(interval=None):.0f}% CPU")
                parts.append(f"System: {psutil.cpu_percent(interval=None):.0f}% CPU")
            except Exception:
                log_suppressed("main_window")
        if self.gpu_tool:
            # nvidia-smi blocks the GUI thread while it runs: ask every 4th tick, show the last value between
            self._gpu_tick = getattr(self, "_gpu_tick", 0) + 1
            if self._gpu_tick % 4 == 1:
                try:
                    out = capture_text(
                        [self.gpu_tool, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                        timeout=1).strip().splitlines()
                    self._gpu_last = out[0] if out else None
                except Exception:
                    self._gpu_last = None
            if getattr(self, "_gpu_last", None):
                parts.append(f"GPU: {self._gpu_last}%")
        self._latest_resource_text = " \u00b7 ".join(parts) if parts else ""
        self._update_inline_progress_status()
        QTimer.singleShot(1000, self.sample_resources)

    # ---- instant settings preview ------------------------------------
    # Renders a single real frame through build_vf_chain() - the exact same
    # tone-map/scale/brightness pipeline the real encode uses - so this is
    # a preview of the actual output, not a lookalike approximation. Only
    # available with FFmpeg present (regardless of which backend is chosen
    # for the real encode itself; HandBrake has no equivalent way to grab
    # one filtered frame without doing a full pass).
    def _stop_frame_preview_process(self, wait_ms=500):
        if hasattr(self, "_frame_preview_timer"):
            self._frame_preview_timer.stop()
        old_pv, self._frame_preview_proc = getattr(self, "_frame_preview_proc", None), None
        if old_pv is not None:
            self._frame_preview_gen = getattr(self, "_frame_preview_gen", 0) + 1
            if is_valid_qobject(old_pv):
                try:
                    if old_pv.state() != QProcess.ProcessState.NotRunning:
                        old_pv.kill()
                        if wait_ms > 0 and hasattr(old_pv, "waitForFinished"):
                            old_pv.waitForFinished(wait_ms)
                    if hasattr(old_pv, "deleteLater"):
                        old_pv.deleteLater()
                except RuntimeError:
                    pass
        invalidate_transient_libplacebo_cache()

    def _schedule_frame_preview(self, *_args):
        if (self._is_proc_running()
                or getattr(self, "_analysis_start_pending", False)
                or getattr(self, "_start_in_progress", False)
                or getattr(self._queue_controller, "running", False)):
            return  # a real conversion or queue start owns preview_label right now
        if not Path(self.src_edit.text()).is_file():
            return
        self._frame_preview_timer.start()

    def _preview_seek_seconds(self):
        """Timestamp to render the still preview from - the scrub slider position."""
        if hasattr(self, "preview_slider"):
            return float(self.preview_slider.value())
        if hasattr(self, "preview_timeline"):
            if hasattr(self.preview_timeline, "value"):
                return float(self.preview_timeline.value())
            if hasattr(self.preview_timeline, "values"):
                return float(self.preview_timeline.values()[0])
        return 0.0

    def _frame_preview_target_seconds(self):
        """Which timestamp the instant preview should actually show right now."""
        return self._preview_seek_seconds()

    def _test_clip_duration(self):
        """Test clip length in seconds."""
        if hasattr(self, "preview_timeline") and hasattr(self.preview_timeline, "values"):
            lo, hi = self.preview_timeline.values()
            if hi != lo:
                return max(1, hi - lo)
        return 5

    def _on_preview_action_clicked(self, url: str):
        if url == "action:switch_gpu":
            if self.backend_combo.currentText().startswith("HandBrake"):
                self.backend_combo.setCurrentText("FFmpeg")
            self._select_curve("BT.2390 \u00b7 GPU tonemap")
            self._schedule_frame_preview()
        elif url == "action:switch_ffmpeg":
            self.backend_combo.setCurrentText("FFmpeg")
            self._schedule_frame_preview()
        elif url == "action:retry":
            self._run_frame_preview()

    def _scrub_preview_by_drag(self, event, et) -> bool:
        """Drag sideways on the preview picture to move through the video (the full picture width is
        about 5 % of the video; hold Shift for 10x finer). A plain click or a right-click is left alone."""
        sl = getattr(self, "preview_slider", None)
        if sl is None or not sl.isEnabled() or sl.maximum() <= sl.minimum():
            return False
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._preview_drag = (event.position().x(), float(sl.value()), False)
            return False
        drag = getattr(self, "_preview_drag", None)
        if drag is None:
            return False
        if et == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
            x0, v0, active = drag
            dx = event.position().x() - x0
            if not active and abs(dx) < 6:
                return False
            self._preview_drag = (x0, v0, True)
            width = max(200.0, float(self.preview_label.width()))
            gain = 0.05 * (0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0)
            sl.setValue(int(round(v0 + dx / width * gain * (sl.maximum() - sl.minimum()))))
            self.preview_label.setCursor(Qt.CursorShape.SizeHorCursor)
            return True
        if et == QEvent.Type.MouseButtonRelease:
            self._preview_drag = None
            self.preview_label.unsetCursor()
            return drag[2]
        return False

    def _on_frame_strip_frame(self, idx, image):
        self.frame_strip.set_frame(idx, image)
        if self.preview_slider.isEnabled() or self._is_proc_running():
            if not self.frame_strip.isVisible():
                self.frame_strip.show()
            if hasattr(self, "preview_controls_widget") and self.preview_controls_widget.isVisible():
                self.preview_controls_widget.hide()

    def _seek_preview_fraction(self, frac):
        if self._is_proc_running():
            return
        sl = self.preview_slider
        if sl.isEnabled() and sl.maximum() > sl.minimum():
            sl.setValue(int(round(sl.minimum() + frac * (sl.maximum() - sl.minimum()))))

    def _update_frame_strip_position(self, value):
        sl = self.preview_slider
        span = sl.maximum() - sl.minimum()
        self.frame_strip.set_position((value - sl.minimum()) / span if span > 0 else 0.0)
        total = sl.maximum()
        text = f"{clock_time(value)} / {clock_time(total)}" if total > 0 else clock_time(value)
        self.frame_strip.set_time_info(value, total, text)

    def _stop_frame_strip(self):
        if hasattr(self, "frame_strip_loader"):
            self.frame_strip_loader.cancel()
        if not self._is_proc_running():
            if hasattr(self, "frame_strip"):
                self.frame_strip.reset()
                self.frame_strip.hide()
            if hasattr(self, "preview_controls_widget"):
                self.preview_controls_widget.hide()

    def _update_frame_strip_during_conversion(self):
        if not hasattr(self, "frame_strip") or self.duration <= 0:
            return
        if not self.frame_strip.isVisible():
            self.frame_strip.show()
        if hasattr(self, "preview_controls_widget") and self.preview_controls_widget.isVisible():
            self.preview_controls_widget.hide()

        count = self.frame_strip.tile_count_for_width(self.preview_label.width() or 560)
        if self.frame_strip._count == 0:
            self.frame_strip._count = count
        self.frame_strip.set_duration(self.duration)

        frac = max(0.0, min(1.0, self.current_out_sec / self.duration))
        self.frame_strip.set_position(frac)
        self.frame_strip.set_time_info(self.current_out_sec, self.duration)

    def _start_frame_strip(self):
        """Fetch the thumbnails for the loaded source."""
        path = self.src_edit.text()
        f = exe("ffmpeg")
        if not f or not path or not Path(path).is_file() or self.duration <= 0:
            return
        count = self.frame_strip.tile_count_for_width(self.preview_label.width() or 560)
        if self.frame_strip._count != count:
            self.frame_strip.reset(count)
        self.frame_strip.set_duration(self.duration)
        if self._is_proc_running():
            self._update_frame_strip_during_conversion()
        else:
            self._update_frame_strip_position(self.preview_slider.value())
        self.frame_strip.show()
        if hasattr(self, "preview_controls_widget"):
            self.preview_controls_widget.hide()
        if not self._is_proc_running() and not getattr(self._queue_controller, "running", False):
            self.frame_strip_loader.start(f, path, self.duration, count, self.kind in ("hdr", "dolby"))
        else:
            try:
                mtime = int(os.path.getmtime(path))
                key = (path, mtime, count)
                cached = FrameStripLoader._cache.get(key)
                if cached:
                    for i in range(min(count, len(cached))):
                        self.frame_strip.set_frame(i, cached[i])
            except OSError:
                pass

    def _update_preview_position_label(self, value):
        """"14:14 / 28:28" - where the preview is, and how long the video is."""
        lbl = getattr(self, "preview_position_label", None)
        total = self.preview_slider.maximum() if hasattr(self, "preview_slider") else 0
        text = f"{clock_time(value)} / {clock_time(total)}" if total > 0 else clock_time(value)
        if lbl is not None:
            lbl.setText(text)
            # digits share one width, so reserving the width of the all-zero text keeps the slider from jittering
            lbl.setMinimumWidth(QFontMetrics(lbl.font()).horizontalAdvance(re.sub(r"\d", "0", text)) + 2)
        if hasattr(self, "frame_strip"):
            self.frame_strip.set_time_info(value, total, text)

    def _on_preview_slider_changed(self, value):
        self._update_preview_position_label(value)
        self._schedule_frame_preview()

    def _on_preview_timeline_changed(self, low, high=None):
        if high is None:
            high = low
        self._on_preview_slider_changed(low)

    def _run_frame_preview(self, *, force_cpu_tonemap: bool = False):
        if (self._is_proc_running()
                or getattr(self, "_analysis_start_pending", False)
                or getattr(self, "_start_in_progress", False)
                or getattr(self._queue_controller, "running", False)):
            return
        if not Path(self.src_edit.text()).is_file():
            return
        f = exe("ffmpeg")
        if not f:
            self.preview_label.setText(self.tr("Install FFmpeg to see an instant preview of the current settings here."))
            return

        # Check source and settings compatibility before launching ffmpeg
        if self._dv_profile == 5:
            using_hb = self.backend_combo.currentText().startswith("HandBrake")
            engine, _code = tonemap_lookup(self.method_combo.currentText())
            accent = self.tokens.get("accent", "#38BDF8")
            if using_hb:
                if self.has_bt2390:
                    reason = self.tr("Dolby Vision Profile 5 requires FFmpeg with GPU tone mapping.")
                    action = f"<a href='action:switch_gpu' style='color:{accent}; text-decoration:underline;'>{self.tr('Switch to FFmpeg & GPU tone mapping')}</a>"
                    self.preview_label.setText(f"{reason}<br><br>{action}")
                else:
                    self.preview_label.setText(self.tr("Dolby Vision Profile 5 requires GPU tone mapping (libplacebo). CPU tonemap and HandBrake are not supported."))
                self._set_preview_meta("")
                return
            if engine != "gpu":
                if self.has_bt2390:
                    reason = self.tr("Dolby Vision Profile 5 has no HDR10 base layer and is incompatible with CPU tone mapping.")
                    action = f"<a href='action:switch_gpu' style='color:{accent}; text-decoration:underline;'>{self.tr('Switch to GPU tone mapping (BT.2390)')}</a>"
                    self.preview_label.setText(f"{reason}<br><br>{action}")
                else:
                    self.preview_label.setText(self.tr("Dolby Vision Profile 5 requires GPU tone mapping (libplacebo). CPU tonemap and HandBrake are not supported."))
                self._set_preview_meta("")
                return
            if not self.has_bt2390:
                self.preview_label.setText(self.tr("Dolby Vision Profile 5 requires FFmpeg built with libplacebo."))
                self._set_preview_meta("")
                return

        # A rapid string of setting changes (e.g. dragging the brightness
        # slider) can restart the debounce timer before the previous render
        # finished - cancel that stale render rather than letting two
        # ffmpeg processes race to write the same file.
        old_pv, self._frame_preview_proc = self._frame_preview_proc, None
        if is_valid_qobject(old_pv):
            try:
                if old_pv.state() != QProcess.ProcessState.NotRunning:
                    old_pv.kill()
                old_pv.deleteLater()
            except RuntimeError:
                pass
        seek = self._frame_preview_target_seconds()
        self._frame_preview_seek = seek
        self._frame_preview_is_end = False
        settings = self.collect_settings()
        if force_cpu_tonemap:
            settings = replace(settings, tonemap_method="Hable \u00b7 CPU tonemap", dv_rpu=False)
            self._frame_preview_cpu_fallback = True
        else:
            self._frame_preview_cpu_fallback = False
        vf = build_vf_chain(settings)
        self._frame_preview_path.unlink(missing_ok=True)
        cmd = [f, "-hide_banner", "-y", "-ss", f"{seek:.2f}", "-i", self.src_edit.text(),
               "-frames:v", "1", "-vf", vf, "-c:v", "png", str(self._frame_preview_path)]
        # A generation token, not just "clear self._frame_preview_proc before
        # starting": kill() is async, so the just-killed process's finished
        # signal can still arrive *after* this new one is already running -
        # without this, that stale signal would read (or race writing) the
        # same output file a newer render just produced/is producing.
        self._frame_preview_gen += 1
        gen = self._frame_preview_gen
        proc = QProcess(self)
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        proc.finished.connect(lambda *_args, gen=gen: self._on_frame_preview_finished(gen))
        self._frame_preview_proc = proc
        self._current_preview_pixmap = None
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(self.tr("Rendering preview frame\u2026"))
        self._set_preview_meta("")
        if hasattr(self, "preview_expand_btn") and self.preview_expand_btn:
            self.preview_expand_btn.setEnabled(False)
        if hasattr(self, "preview_save_btn") and self.preview_save_btn:
            self.preview_save_btn.setEnabled(False)
        proc.start()
        if proc.waitForStarted(2000):
            jobobject.bind(proc.processId())

    def _on_frame_preview_finished(self, gen):
        if not is_valid_qobject(self) or not is_valid_qobject(getattr(self, "method_combo", None)):
            return
        if gen != self._frame_preview_gen:
            return  # a killed/stale render finishing late - a newer one already owns the output file
        old_pv, self._frame_preview_proc = self._frame_preview_proc, None
        if is_valid_qobject(old_pv) and hasattr(old_pv, "deleteLater"):
            QTimer.singleShot(0, old_pv.deleteLater)
        # A real conversion may have started while this render was in
        # flight - don't stomp its live preview with a now-stale frame.
        if self._is_proc_running():
            return
        if not self._frame_preview_path.exists():
            engine, _code = tonemap_lookup(self.method_combo.currentText())
            if (engine == "gpu"
                    and self._dv_profile != 5
                    and not getattr(self, "_frame_preview_cpu_fallback", False)):
                self._run_frame_preview(force_cpu_tonemap=True)
                return
            accent = self.tokens.get("accent", "#38BDF8")
            if self._dv_profile == 5 and engine != "gpu" and self.has_bt2390:
                reason = self.tr("Dolby Vision Profile 5 requires GPU tone mapping.")
                action = f"<a href='action:switch_gpu' style='color:{accent}; text-decoration:underline;'>{self.tr('Switch to GPU tone mapping (BT.2390)')}</a>"
                self.preview_label.setText(f"{reason}<br><br>{action}")
            else:
                reason = self.tr("Could not render preview frame for the current settings.")
                action = f"<a href='action:retry' style='color:{accent}; text-decoration:underline;'>{self.tr('Retry')}</a>"
                self.preview_label.setText(f"{reason}<br><br>{action}")
            return
        img = QImage(str(self._frame_preview_path))
        if img.isNull():
            accent = self.tokens.get("accent", "#38BDF8")
            reason = self.tr("Could not render preview frame for the current settings.")
            action = f"<a href='action:retry' style='color:{accent}; text-decoration:underline;'>{self.tr('Retry')}</a>"
            self.preview_label.setText(f"{reason}<br><br>{action}")
            return
        pix = QPixmap.fromImage(img)
        self._current_preview_pixmap = pix
        self.preview_label.setPixmap(pix.scaled(
            max(self.preview_label.width(), 320), max(self.preview_label.height(), 200),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        # Use the seek this render actually started with, not a fresh
        # _frame_preview_target_seconds() read - the trim handles could
        # have moved again while this render was in flight.
        seek = self._frame_preview_seek
        shadow_pct, highlight_pct = clipping_pct(img)
        clip_bits = []
        if shadow_pct >= 0.5:
            clip_bits.append(f"{shadow_pct:.0f}% shadows clipped")
        if highlight_pct >= 0.5:
            clip_bits.append(f"{highlight_pct:.0f}% highlights clipped")
        clip_note = " \u00b7 " + " \u00b7 ".join(clip_bits) if clip_bits else " \u00b7 no clipping detected"
        # Flag which end of the trim this frame is, so dragging the right
        # handle doesn't read as "the clip still starts here" - the label
        # only ever showed the start position before trims had two ends.
        end_note = self.tr(" (clip end)") if self._frame_preview_is_end else ""
        self._set_preview_meta(f"Preview frame at {clock_time(seek)}{end_note}" + clip_note)
        if hasattr(self, "preview_expand_btn") and self.preview_expand_btn:
            self.preview_expand_btn.setEnabled(True)
        if hasattr(self, "preview_save_btn") and self.preview_save_btn:
            self.preview_save_btn.setEnabled(True)
        if hasattr(self, "compare_methods_btn"):
            self.compare_methods_btn.setEnabled(True)

    # ---- short test-clip render -----------------------------------
    def _test_clip_command(self, out_path):
        """Thin wrapper over build_test_clip_command() - see the PURE CORE
        section for what the command contains, and why the HandBrake
        branch is now derived from the real conversion command instead of
        being assembled separately."""
        seek = self._preview_seek_seconds()
        dur = self._test_clip_duration()
        self._test_clip_seek = seek
        cmd, warnings = build_test_clip_command(self._settings_with_tracks(out_path, announce=True), out_path, seek, dur)
        for w in warnings:
            self.write(w)
        return cmd

    def _clamp_preview_layout(self, old_height, old_card_h):
        """Force preview_card and the top-level window back to specific,
        already-known-good height values, undoing whatever the layout
        pass in between did to them.

        Guards against Qt's own top-level-window habit of nudging the
        whole window towards the layout's latest sizeHint() on basically
        any child content change, even when the window already had
        plenty of room (first noticed and fixed this way for the
        Advanced filters toggle). The actual root cause chased down for
        Live Preview specifically was test_clip_btn's role switching
        (see _set_test_clip_btn_role) leaving it briefly in a
        minimumHeight/maximumHeight mismatch, which is fixed at the
        source now - this is what's left as a cheap, harmless safety net
        for the same class of problem elsewhere, not a targeted patch.

        Takes the target heights as arguments rather than reading
        self.height()/self.preview_card.height() itself so callers can
        capture the "before" baseline up front, before whatever change
        they're about to make.
        """
        self.layout().activate()
        if old_card_h > 0 and getattr(self, "preview_card", None) is not None \
                and self.preview_card.height() != old_card_h:
            self.preview_card.resize(self.preview_card.width(), old_card_h)
        target_h = max(old_height, self.minimumSizeHint().height())
        if self.height() != target_h:
            self.resize(self.width(), target_h)

    def _refresh_preview_meta_display(self):
        """Single line under the preview frame: a test-clip status message
        takes priority over the frame's own "Preview frame at ..." meta
        text while one is set, then the meta text reappears once the
        status clears - instead of two labels stacked on top of each
        other.

        Also the natural place to guard against Live Preview and the
        window itself drifting in size - see _clamp_preview_layout().
        """
        old_height = self.height()
        old_card_h = self.preview_card.height()
        self.preview_meta_label.setText(self._test_clip_status_text or self._preview_meta_text)
        self._clamp_preview_layout(old_height, old_card_h)

    def _set_preview_meta(self, text):
        self._preview_meta_text = text
        self._refresh_preview_meta_display()

    def _set_test_clip_status(self, text):
        self._test_clip_status_text = text
        self._refresh_preview_meta_display()

    def _set_test_clip_btn_role(self, role):
        """set_role(self.test_clip_btn, role) alone isn't enough: this
        button is setFixedSize(34, 34) at construction (see
        _preview_icon_btn) because it's an icon-only control sitting in a
        tight row, not a normal flow-sized button - but "stop-ready" (and
        "pause-ready"/"resume-ready", shared with the main Controls'
        Pause/Stop buttons where the difference doesn't matter, since
        those size normally) has no explicit padding of its own, unlike
        "panel-toggle"'s "padding: 6px 14px". Qt's stylesheet engine
        recomputes this button's minimumSizeHint from scratch on
        re-polish when a role with a QSS min-height is involved,
        falling back to the native style's default content margins for
        the padding it wasn't told - and that recomputed minimum (36 in
        testing) doesn't agree with the maximumHeight setFixedSize
        already locked in (34), leaving the widget in a min>max
        inconsistent state that leaks into the whole window's
        minimumSizeHint() and is exactly what was nudging Live Preview
        for the entire time a test clip or conversion was running (see
        _clamp_preview_layout's docstring for the rest of that story).
        Re-asserting setFixedSize() right after the repolish forces the
        min/max constraint back to what it's actually supposed to be.
        """
        if hasattr(self, "test_clip_btn") and self.test_clip_btn:
            set_role(self.test_clip_btn, role)
            self.test_clip_btn.setFixedSize(34, 34)

    def _run_test_clip(self):
        if self._test_clip_proc is not None and not is_valid_qobject(self._test_clip_proc):
            self._test_clip_proc = None
        if self._test_clip_proc is not None:
            # Second click while one is running: treat it as cancel.
            old_process = self._test_clip_proc
            self._test_clip_proc = None
            self._test_clip_runs.cancel()
            if is_valid_qobject(old_process):
                try:
                    if old_process.state() != QProcess.ProcessState.NotRunning:
                        old_process.kill()
                    old_process.deleteLater()
                except RuntimeError:
                    pass
            if hasattr(self, "test_clip_btn") and self.test_clip_btn:
                self.test_clip_btn.setProperty("icon_kind", "scissors")
                self.test_clip_btn.update()
                self._set_test_clip_btn_role("panel-toggle")
                self.test_clip_btn.setToolTip(self.tr(
                    "Render a short test clip to preview quality and motion before converting."))
            self._set_test_clip_status("Test clip render cancelled.")
            if self.proc is None:
                self.set_state("idle", "Ready")
            return
        if self._is_proc_running():
            QMessageBox.information(self, "Conversion running",
                                     "Wait for the current conversion to finish first.")
            return
        if not Path(self.src_edit.text()).is_file():
            QMessageBox.critical(self, "Files", "Choose a source file first.")
            return
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        if using_hb:
            if not self.handbrake_tool:
                QMessageBox.critical(self, "HandBrakeCLI not found",
                                      "Install HandBrakeCLI and ensure it's on PATH, or switch the backend to FFmpeg.")
                return
            ext = Path(self.dst_edit.text()).suffix.lower()
            ext = ext if ext in (".mp4", ".mkv") else ".mp4"
        else:
            if not exe("ffmpeg"):
                QMessageBox.critical(self, "FFmpeg", "FFmpeg is required.")
                return
            ext = Path(self.dst_edit.text()).suffix or ".mp4"
        generation, out_dir = self._test_clip_runs.begin()
        out = out_dir / f"test_clip{ext}"
        cmd = self._test_clip_command(out)
        proc = QProcess(self)
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._hints.gpu_tonemap = False
        self._test_clip_using_hb = using_hb
        self._reset_test_clip_parsers(using_hb)
        proc.readyReadStandardOutput.connect(lambda p=proc: self._on_test_clip_output(p))
        proc.finished.connect(
            lambda *_args, out=out, generation=generation:
            self._on_test_clip_finished(out, generation))
        self._test_clip_proc = proc
        self._test_clip_out = None
        if hasattr(self, "test_clip_open_btn") and self.test_clip_open_btn:
            self.test_clip_open_btn.setEnabled(False)
            set_role(self.test_clip_open_btn, "panel-toggle")
            self.test_clip_open_btn.setToolTip(self.tr(
                "Open test clip (available once rendered)."))
        if hasattr(self, "test_clip_btn") and self.test_clip_btn:
            self.test_clip_btn.setProperty("icon_kind", "stop")
            self.test_clip_btn.update()
            self._set_test_clip_btn_role("stop-ready")
            self.test_clip_btn.setToolTip(self.tr("Cancel the test clip render currently in progress."))
        dur = self._test_clip_duration()
        self._set_test_clip_status(f"Rendering a {dur}s test clip with the current settings\u2026")
        if self.proc is None:
            self.pbar.setValue(0)
            self.set_state("run", "Test clip\u2026 0%")
        proc.start()
        if proc.waitForStarted(2000):
            jobobject.bind(proc.processId())

    def _on_test_clip_output(self, proc):
        if not is_valid_qobject(proc):
            return
        try:
            data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        except RuntimeError:
            return
        for line in self._test_clip_assembler.feed(data):
            self._handle_test_clip_line(line)

    def _handle_test_clip_line(self, line):
        # Separate assembler/parser from _handle_line()'s, but a test clip
        # runs the same filter chain and can hit the same libplacebo VRAM
        # failure, so the scanner checks for it here too (and only that -
        # see ErrorHintScanner.feed_gpu_tonemap_only()).
        self._hints.feed_gpu_tonemap_only(line)
        dur = self._test_clip_duration()
        self._test_clip_parser.configure(dur)
        stats, consumed = self._test_clip_parser.feed(line)
        if stats is not None and stats.pct is not None:
            self._update_test_clip_progress(stats.pct)
        elif not consumed and line.strip():
            self.write(line + "\n")

    def _update_test_clip_progress(self, pct):
        # Only drive the shared pbar/status_label if a real conversion
        # isn't the one currently "owning" them - see _run_test_clip()'s
        # matching guard when the render starts.
        if self.proc is not None:
            return
        self.pbar.setValue(int(pct * 10))
        self.set_state("run", f"Test clip\u2026 {pct:.0f}%")

    def _on_test_clip_finished(self, out, generation=None):
        if generation is not None and not self._test_clip_runs.is_current(generation):
            # An older cancelled process may finish after the next render starts.
            self._test_clip_runs.cleanup(Path(out).parent)
            return
        proc = self._test_clip_proc
        was_current = proc is not None and is_valid_qobject(proc)
        self._test_clip_proc = None
        if was_current:
            self._on_test_clip_output(proc)
            for line in self._test_clip_assembler.flush():
                self._handle_test_clip_line(line)
            if hasattr(proc, "deleteLater"):
                QTimer.singleShot(0, proc.deleteLater)
        self._test_clip_assembler.reset()
        if hasattr(self, "test_clip_btn") and self.test_clip_btn:
            self.test_clip_btn.setProperty("icon_kind", "scissors")
            self.test_clip_btn.update()
            self._set_test_clip_btn_role("panel-toggle")
            self.test_clip_btn.setToolTip(self.tr(
                "Render a short test clip to preview quality and motion before converting."))
        if self.proc is None:
            self.set_state("idle", "Ready")
        if not was_current:
            return  # cancelled - out may be a half-written/deleted file
        if not out.exists() or out.stat().st_size == 0:
            self._set_test_clip_status("Test clip render failed \u2014 check the Activity log for details.")
            self._test_clip_runs.cleanup(Path(out).parent)
            return
        self._test_clip_out = out
        if hasattr(self, "test_clip_open_btn") and self.test_clip_open_btn:
            self.test_clip_open_btn.setEnabled(True)
            # Green tint so "ready to play" is visible at a glance, not just in
            # the tooltip - previously promised by the tooltip text below but
            # never actually applied (role stayed "panel-toggle" either way).
            set_role(self.test_clip_open_btn, "icon-btn-ready")
            self.test_clip_open_btn.setToolTip(self.tr("Open test clip \u2014 ready: {out}", out=out))
        self._set_test_clip_status("")
        if self._hints.gpu_tonemap:
            self._set_test_clip_status(
                "Rendered, but GPU tone mapping ran out of GPU memory partway through \u2014 "
                "expect solid-colour (often green) stretches. See the warning dialog.")
            QMessageBox.warning(
                self, "GPU tone mapping ran low on GPU memory",
                "Libplacebo's GPU tone-mapping path (BT.2390/Spline/BT.2446A/ST2094-*) "
                "ran out of GPU memory while rendering this test clip \u2014 the log shows "
                "VK_ERROR_OUT_OF_DEVICE_MEMORY. The clip still finished, but any frame it "
                "couldn't render was replaced with a solid colour (usually green) instead "
                "of erroring out, and the full conversion would hit the same problem.\n\n"
                "Try closing other GPU-heavy apps to free video memory, or switch Tone "
                "mapping to a CPU curve \u2014 Hable, Reinhard, Mobius, or Linear all use "
                "the classic zscale+tonemap path instead of the GPU/Vulkan one, so "
                "they're unaffected by this.")

    def _open_test_clip(self):
        if self._test_clip_out and self._test_clip_out.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._test_clip_out)))

    def show_preview_fullscreen(self):
        pix = self._current_preview_pixmap
        if pix is None or pix.isNull():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Preview frame")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        screen = self.screen() if hasattr(self, "screen") else None
        avail = screen.availableGeometry() if screen else None
        max_w = int(avail.width() * 0.9) if avail else 1600
        max_h = int(avail.height() * 0.9) if avail else 900
        scaled = pix.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        lbl.setPixmap(scaled)
        lay.addWidget(lbl)
        dlg.resize(scaled.size())
        dlg.exec()

    def save_preview_frame(self):
        pix = self._current_preview_pixmap
        if pix is None or pix.isNull():
            return
        default_name = "preview_frame.png"
        if self.src_edit.text():
            default_name = Path(self.src_edit.text()).stem + "_preview.png"
        initdir = str(Path.home())
        if self.dst_edit.text() and Path(self.dst_edit.text()).parent.is_dir():
            initdir = str(Path(self.dst_edit.text()).parent)
        x, _ = QFileDialog.getSaveFileName(
            self, "Save preview frame", str(Path(initdir) / default_name),
            "PNG image (*.png);;JPEG image (*.jpg)")
        if x:
            if not pix.save(x):
                QMessageBox.critical(self, "Save preview frame", "Couldn't save the image to that location.")

    def _show_preview_context_menu(self, pos):
        if not self.src_edit.text() or not Path(self.src_edit.text()).is_file():
            return
        menu = QMenu(self)
        compare_act = menu.addAction(self.tr("Compare methods"))
        compare_act.triggered.connect(self.open_tonemap_comparison)
        if self._current_preview_pixmap and not self._current_preview_pixmap.isNull():
            save_act = menu.addAction(self.tr("Save frame\u2026"))
            save_act.triggered.connect(self.save_preview_frame)
            view_act = menu.addAction(self.tr("View full size"))
            view_act.triggered.connect(self.show_preview_fullscreen)
        menu.exec(self.preview_label.mapToGlobal(pos))

    def open_tonemap_comparison(self):
        if not self.src_edit.text() or not Path(self.src_edit.text()).is_file():
            QMessageBox.information(self, self.tr("Compare methods"), self.tr("Choose a source video to compare methods."))
            return
        if not exe("ffmpeg"):
            QMessageBox.warning(self, self.tr("Compare methods"), self.tr("Install FFmpeg to see an instant preview of the current settings here."))
            return
        from hdr2sdr.ui.compare_dialog import TonemapComparisonDialog
        dlg = TonemapComparisonDialog(self)
        dlg.exec()

    # ---- live preview -----------------------------------------------
    # Reads a JPEG that ffmpeg itself continuously overwrites (see
    # command_ffmpeg's filter_complex split) - this is a real frame from
    # the actual tone-mapped/encoded output, not a separate re-read of the
    # HDR source. Only available on the FFmpeg backend; HandBrakeCLI has no
    # equivalent split-output mechanism we can drive from the CLI.
    def poll_preview(self):
        if not (self._is_proc_running()):
            return
        if self.preview_path and self.preview_path.exists():
            try:
                mtime = self.preview_path.stat().st_mtime
            except OSError:
                mtime = None
            if mtime and mtime != self.preview_mtime:
                self.preview_mtime = mtime
                img = QImage(str(self.preview_path))
                if not img.isNull():
                    pix = QPixmap.fromImage(img)
                    self._current_preview_pixmap = pix
                    self.preview_label.setPixmap(pix.scaled(
                        max(self.preview_label.width(), 320), max(self.preview_label.height(), 200),
                        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    self._update_preview_meta(img)
                    self._update_frame_strip_during_conversion()
                    if hasattr(self, "preview_expand_btn") and self.preview_expand_btn:
                        self.preview_expand_btn.setEnabled(True)
                    if hasattr(self, "preview_save_btn") and self.preview_save_btn:
                        self.preview_save_btn.setEnabled(True)
                    if hasattr(self, "compare_methods_btn"):
                        self.compare_methods_btn.setEnabled(True)
        QTimer.singleShot(1500, self.poll_preview)

    def _update_preview_meta(self, img):
        pos = f"{clock_time(self.current_out_sec)} / {clock_time(self.duration)}" if self.duration else clock_time(self.current_out_sec)
        shadow_pct, highlight_pct = clipping_pct(img)
        clip_bits = []
        if shadow_pct >= 0.5:
            clip_bits.append(f"{shadow_pct:.0f}% shadows clipped")
        if highlight_pct >= 0.5:
            clip_bits.append(f"{highlight_pct:.0f}% highlights clipped")
        clip_note = " \u00b7 " + " \u00b7 ".join(clip_bits) if clip_bits else " \u00b7 no clipping detected"
        self._set_preview_meta(pos + clip_note)

    # ---- live controls --------------------------------------------------
    def require_psutil(self, feature):
        if HAVE_PSUTIL:
            return True
        msg = f"{feature} requires the psutil package, which isn't installed.\n\nInstall it with:\n\n    pip install psutil\n\nthen restart this app."
        self.write(f"[live] {feature} unavailable: psutil is not installed.\n")
        QMessageBox.information(self, "psutil required", msg)
        return False

    def _schedule_live_apply(self, *_args):
        """Hooked to the cores/priority controls so dragging them applies
        live automatically - no separate 'Apply now' button. Restarts the
        debounce timer on every change; only the value the user settles on
        (after the drag stops for _live_apply_timer's interval) gets sent
        to cpu_affinity()/nice(). No-op while nothing is running or psutil
        is unavailable, so it's harmless to leave connected at all times."""
        if not (self._is_proc_running()):
            return
        if not HAVE_PSUTIL:
            return
        self._live_apply_timer.start()

    def apply_live_settings(self, quiet=False):
        if not (self._is_proc_running()):
            return
        if not HAVE_PSUTIL:
            if not quiet:
                self.require_psutil("Live core/priority control")
            return
        try:
            n = max(1, min(CPU_COUNT, self.cores_spin.value()))
            psutil.Process(self.proc.processId()).cpu_affinity(list(range(n)))
            self.write(f"[live] CPU cores limited to {n} of {CPU_COUNT} (affinity).\n")
        except (AttributeError, NotImplementedError):
            if not quiet:
                QMessageBox.information(self, "Unavailable", "CPU affinity control isn't supported on this OS (e.g. macOS).")
        except Exception as e:
            self.write(f"[live] CPU core apply failed: {e}\n")
            if not quiet:
                QMessageBox.critical(self, "Live CPU cores", str(e))
        try:
            pp = psutil.Process(self.proc.processId())
            self._set_priority(pp, self.priority_combo.currentText(), quiet=quiet)
        except Exception as e:
            self.write(f"[live] Priority apply failed: {e}\n")
            if not quiet:
                QMessageBox.critical(self, "Live priority", str(e))

    def toggle_pause(self):
        if not (self._is_proc_running()):
            return
        if not self.require_psutil("Pause/Resume"):
            return
        try:
            pp = psutil.Process(self.proc.processId())
            if self.paused:
                pp.resume()
                self.paused = False
                pct = round(self.pbar.value() / 10.0) if hasattr(self, "pbar") else 0
                self.set_state("run", f"Converting\u2026 {pct}%")
                self.write("[live] Resumed.\n")
            else:
                pp.suspend()
                self.paused = True
                self.set_state("paused", "Paused")
                self.write("[live] Paused.\n")
            self.update_run_controls()
        except Exception as e:
            self.write(f"[live] Pause/Resume failed: {e}\n")
            QMessageBox.critical(self, "Pause/Resume", str(e))

    def write(self, x):
        scrollbar = self.log.verticalScrollBar()
        follow_tail = scrollbar.value() >= scrollbar.maximum() - 1
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(str(x))
        if follow_tail:
            scrollbar.setValue(scrollbar.maximum())

    def stop(self):
        if self._is_proc_running():
            if self.paused and HAVE_PSUTIL:
                try:
                    psutil.Process(self.proc.processId()).resume()
                except Exception:
                    log_suppressed("main_window")
                self.paused = False
            self.stopping = True
            if not self.using_hb and not self._resume_coordinator.concat_phase:
                # ffmpeg ignores WM_CLOSE on Windows and a kill leaves an unreadable file with no
                # trailer; "q" on stdin makes it finish the file properly (exit 0, playable).
                try:
                    self.proc.write(b"q")
                except Exception:
                    log_suppressed("main_window")
                QTimer.singleShot(5000, self._terminate_if_stuck)
                QTimer.singleShot(10000, self._force_kill_if_stuck)
            else:
                self.proc.terminate()
                # HandBrakeCLI / the concat step are console apps; terminate() (WM_CLOSE on
                # Windows, SIGTERM on POSIX) can be ignored, so force a kill a few seconds
                # later if it's still alive.
                QTimer.singleShot(4000, self._force_kill_if_stuck)
            self.set_state("run", "Stopping\u2026")
            self.update_run_controls()

    def _install_app_filters(self):
        if getattr(self, "_app_filters_installed", False):
            return
        app = QApplication.instance()
        if app is None or not _is_live_qobject(app):
            return
        nc = getattr(self, "_native_chrome", None)
        if nc is not None and _is_live_qobject(nc):
            app.installEventFilter(nc)
        if _is_live_qobject(self):
            app.installEventFilter(self)
        self._app_filters_installed = True

    def _remove_app_filters(self):
        self._app_filters_installed = False
        app = QApplication.instance()
        if app is None or not _is_live_qobject(app):
            return
        for f in (self, getattr(self, "_native_chrome", None)):
            if f is not None and _is_live_qobject(f):
                try:
                    app.removeEventFilter(f)
                except RuntimeError:
                    pass

    def _terminate_if_stuck(self):
        if self.stopping and self._is_proc_running():
            self.proc.terminate()

    def _force_kill_if_stuck(self):
        if self.stopping and self._is_proc_running():
            self.proc.kill()

    def eventFilter(self, obj, event):
        if not _is_live_qobject(self) or not _is_live_qobject(obj) or event is None:
            return False
        et = event.type()
        # Custom tooltip (see _AppToolTip) - installed on the whole
        # QApplication so it catches every widget's existing setToolTip()
        # text without touching those call sites. Swallowing QEvent.ToolTip
        # here is what stops Qt's own (black-on-this-machine) tooltip from
        # ever appearing.
        if et == QEvent.Type.ToolTip:
            tooltip = getattr(self, "_tooltip", None)
            if not _is_live_qobject(tooltip):
                return False
            text = obj.toolTip() if isinstance(obj, QWidget) else ""
            if text:
                cursor_pt = event.globalPos()
                win_attr = getattr(obj, "window", None) if isinstance(obj, QWidget) else None
                win = win_attr() if callable(win_attr) else (win_attr if isinstance(win_attr, QWidget) else None)
                if win is not None and (win.width() <= 0 or win.height() <= 0):
                    win = None
                screen = (
                    QApplication.screenAt(cursor_pt)
                    or (win.screen() if win is not None else None)
                    or QApplication.primaryScreen()
                )
                scr_geo = screen.availableGeometry() if screen is not None else None
                screen_rect = (
                    (scr_geo.x(), scr_geo.y(), scr_geo.width(), scr_geo.height())
                    if scr_geo is not None
                    else None
                )
                if win is not None:
                    win_tl = win.mapToGlobal(QPoint(0, 0))
                    window_rect = (win_tl.x(), win_tl.y(), win.width(), win.height())
                else:
                    window_rect = screen_rect
                max_w = (
                    tooltip_max_width(window_rect[2])
                    if window_rect is not None and window_rect[2] > 0
                    else 420
                )
                tooltip.setMaximumWidth(max_w)
                tooltip.setText(text)
                tooltip.adjustSize()
                w = min(tooltip.width(), max_w)
                if tooltip.wordWrap() and w > 0:
                    hfw = tooltip.heightForWidth(w)
                    if hfw > tooltip.height():
                        tooltip.resize(w, hfw)
                if (
                    window_rect is not None
                    and window_rect[3] > 0
                    and tooltip.height() > window_rect[3]
                ):
                    tooltip.resize(tooltip.width(), window_rect[3])
                if isinstance(obj, QWidget) and obj is not win and obj.width() > 0 and obj.height() > 0:
                    anc_tl = obj.mapToGlobal(QPoint(0, 0))
                    anchor_rect = (anc_tl.x(), anc_tl.y(), obj.width(), obj.height())
                else:
                    anchor_rect = None
                tx, ty = place_tooltip(
                    window_rect,
                    (cursor_pt.x(), cursor_pt.y()),
                    (tooltip.width(), tooltip.height()),
                    anchor_rect=anchor_rect,
                    screen_rect=screen_rect,
                )
                tooltip.move(QPoint(tx, ty))
                tooltip.show()
            else:
                tooltip.hide()
            return True
        if et in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            tooltip = getattr(self, "_tooltip", None)
            if _is_live_qobject(tooltip):
                tooltip.hide()
        # Keeps the displayed preview frame scaled to whatever space it
        # currently has - not just on the next poll/render tick, but
        # immediately on any resize. This matters more than a plain
        # QWidget.resizeEvent override would catch: dragging the
        # left/right QSplitter handle (the everyday way this panel gets
        # narrower) resizes preview_label directly without the top-level
        # window itself being resized, so a MainWindow-level resizeEvent
        # would miss it entirely.
        if obj is getattr(self, "preview_label", None) and self._scrub_preview_by_drag(event, et):
            return True
        if obj is getattr(self, "preview_label", None) and et == QEvent.Type.Resize:
            cur = self.preview_label.pixmap()
            if cur is not None and not cur.isNull() and self._current_preview_pixmap is not None:
                self.preview_label.setPixmap(self._current_preview_pixmap.scaled(
                    max(self.preview_label.width(), 320), max(self.preview_label.height(), 200),
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        # The hint label isn't in any layout (it's painted directly on the
        # queue list's viewport, see _update_queue_hint below), so nothing
        # resizes it automatically when the splitter is dragged or the
        # window resized - it has to be matched to the viewport's size by
        # hand on every viewport resize.
        if obj is getattr(self, "files_empty_drop_widget", None) and et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self.browse_source()
                return True
        queue_list = getattr(self, "queue_list", None)
        if _is_live_qobject(queue_list) and (obj is queue_list or obj is queue_list.viewport()):
            if et == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                    self._remove_selected_queue_items()
                    return True
            if obj is queue_list.viewport() and et == QEvent.Type.Resize:
                hint_lbl = getattr(self, "queue_hint_label", None)
                if _is_live_qobject(hint_lbl):
                    hint_lbl.setGeometry(queue_list.viewport().rect())
        conv_scroll = getattr(self, "conv_scroll", None)
        conv_content = getattr(self, "conv_scroll_content", None)
        if _is_live_qobject(conv_scroll) and (
            obj is conv_scroll or (_is_live_qobject(conv_content) and obj is conv_content)
            or (_is_live_qobject(conv_scroll.viewport()) and obj is conv_scroll.viewport())
        ) and et == QEvent.Type.Resize:
            if hasattr(self, "_update_conversion_adaptive_layout") and not getattr(self, "_in_adaptive_layout", False):
                self._update_conversion_adaptive_layout()
        if not _is_live_qobject(self) or not _is_live_qobject(obj):
            return False
        return super().eventFilter(obj, event)

    def _on_dv_rpu_clicked(self, checked: bool = False):
        self._dv_rpu_user_override = True
        self._dv_rpu_auto_enabled = False

    def _update_conversion_adaptive_layout(self, width: int | None = None):
        if getattr(self, "_in_adaptive_layout", False):
            return
        self._in_adaptive_layout = True
        try:
            if not hasattr(self, "conv_scroll") or not hasattr(self, "encoder_combo") or not hasattr(self, "enc_grid"):
                return
            if width is None:
                vp = self.conv_scroll.viewport() if hasattr(self, "conv_scroll") else None
                width = (
                    vp.width()
                    if vp and vp.width() > 0
                    else (self.conv_scroll_content.width() if hasattr(self, "conv_scroll_content") and self.conv_scroll_content.width() > 0 else 0)
                )
            if width <= 0:
                return
    
            fm = QFontMetrics(self.encoder_combo.font())
            scale = max(1.0, fm.height() / 16.0)
            min_enc_w = max(self.encoder_combo.minimumSizeHint().width(), int(145 * scale))
            min_bd_w = max(self.bit_depth_combo.minimumSizeHint().width(), int(145 * scale))
            grid_sp = max(0, self.enc_grid.horizontalSpacing() if hasattr(self.enc_grid, "horizontalSpacing") else 10)
            grid_margins = self.enc_grid.contentsMargins().left() + self.enc_grid.contentsMargins().right()
            content_margins = (
                self.conv_scroll_content.layout().contentsMargins().left()
                + self.conv_scroll_content.layout().contentsMargins().right()
            ) if hasattr(self, "conv_scroll_content") and self.conv_scroll_content.layout() else 4
            enc_threshold = min_enc_w + min_bd_w + grid_sp + grid_margins + content_margins
            is_two_col = width >= enc_threshold
    
            if getattr(self, "_enc_is_two_col", None) != is_two_col:
                self._enc_is_two_col = is_two_col
                self.enc_grid.removeWidget(self.enc_lbl)
                self.enc_grid.removeWidget(self.encoder_combo)
                self.enc_grid.removeWidget(self.bd_lbl)
                self.enc_grid.removeWidget(self.bit_depth_combo)
                if is_two_col:
                    self.enc_grid.addWidget(self.enc_lbl, 0, 0)
                    self.enc_grid.addWidget(self.encoder_combo, 1, 0)
                    self.enc_grid.addWidget(self.bd_lbl, 0, 1)
                    self.enc_grid.addWidget(self.bit_depth_combo, 1, 1)
                    self.enc_grid.setColumnStretch(0, 1)
                    self.enc_grid.setColumnStretch(1, 1)
                else:
                    self.enc_grid.addWidget(self.enc_lbl, 0, 0)
                    self.enc_grid.addWidget(self.encoder_combo, 1, 0)
                    self.enc_grid.addWidget(self.bd_lbl, 2, 0)
                    self.enc_grid.addWidget(self.bit_depth_combo, 3, 0)
                    self.enc_grid.setColumnStretch(0, 1)
                    self.enc_grid.setColumnStretch(1, 0)
    
            is_bitrate = getattr(self, "quality_mode_combo", None) and self.quality_mode_combo.currentText() == "Bitrate"
            stepper = self.bitrate_stepper if is_bitrate else self.quality_stepper
            stepper_w = 116 if is_bitrate else 92
            raw_label_w = max(self.quality_head_label.minimumSizeHint().width(), self.quality_head_label.sizeHint().width()) if hasattr(self, "quality_head_label") else 38
            label_w = max(raw_label_w, int(38 * scale))
            enc = self.encoder_combo.currentText() if hasattr(self, "encoder_combo") else ""
            bounds = QUALITY_TIER_BOUNDS_BY_ENCODER.get(enc, QUALITY_TIER_BOUNDS_51)
            tier_keys = list(dict.fromkeys(label for _, _, label in bounds))
            max_tier_w = max(fm.horizontalAdvance(self.tr(k)) for k in tier_keys) if tier_keys else 0
            if hasattr(self, "quality_tier_lbl") and getattr(self, "_quality_tier_reserved_w", None) != max_tier_w:
                self._quality_tier_reserved_w = max_tier_w
                self.quality_tier_lbl.setFixedWidth(max_tier_w)
    
            tier_w = 0
            combo_w = max(self.quality_mode_combo.minimumSizeHint().width(), self.quality_mode_combo.sizeHint().width()) if (hasattr(self, "quality_mode_combo") and self.quality_mode_combo.isVisible()) else 0
            sp = int(11 * (scale**2) + 56 * scale - 42) if scale > 1.0 else (25 if tier_w > 0 else 19)
            q_row_threshold = label_w + tier_w + combo_w + stepper_w + sp
            self._q_row_threshold = q_row_threshold
            is_q_beside = width >= q_row_threshold
    
            if getattr(self, "_q_is_beside", None) != is_q_beside:
                self._q_is_beside = is_q_beside
                if hasattr(self, "q_head_row") and hasattr(self, "q_sub_row") and hasattr(self, "q_sub_widget"):
                    if is_q_beside:
                        enc_parent = self.q_head_row.parentWidget() if self.q_head_row.parentWidget() else None
                        self.q_sub_row.removeWidget(self.quality_stepper)
                        self.q_sub_row.removeWidget(self.bitrate_stepper)
                        self.q_head_row.removeWidget(self.quality_stepper)
                        self.q_head_row.removeWidget(self.bitrate_stepper)
                        if enc_parent is not None:
                            self.quality_stepper.setParent(enc_parent)
                            self.bitrate_stepper.setParent(enc_parent)
                        self.q_head_row.addWidget(self.quality_stepper)
                        self.q_head_row.addWidget(self.bitrate_stepper)
                        self.q_sub_widget.hide()
                        if hasattr(self, "_update_quality_mode_visibility"):
                            self._update_quality_mode_visibility()
                    else:
                        self.q_head_row.removeWidget(self.quality_stepper)
                        self.q_head_row.removeWidget(self.bitrate_stepper)
                        self.q_sub_row.addWidget(self.quality_stepper)
                        self.q_sub_row.addWidget(self.bitrate_stepper)
                        self.q_sub_widget.show()
                        if hasattr(self, "_update_quality_mode_visibility"):
                            self._update_quality_mode_visibility()
    
            if hasattr(self, "adv_grid_layout") and hasattr(self, "deband_chk"):
                is_adv_two_col = width >= int(300 * scale)
                if getattr(self, "_adv_grid_is_two_col", None) != is_adv_two_col:
                    self._adv_grid_is_two_col = is_adv_two_col
                    self.adv_grid_layout.removeWidget(self.deband_chk)
                    self.adv_grid_layout.removeWidget(self.autocrop_chk)
                    self.adv_grid_layout.removeWidget(self.resharpen_chk)
                    if hasattr(self, "denoise_container"):
                        self.adv_grid_layout.removeWidget(self.denoise_container)
                    if is_adv_two_col:
                        self.adv_grid_layout.addWidget(self.deband_chk, 0, 0)
                        self.adv_grid_layout.addWidget(self.autocrop_chk, 0, 1)
                        self.adv_grid_layout.addWidget(self.resharpen_chk, 1, 0)
                        if hasattr(self, "denoise_container"):
                            self.adv_grid_layout.addWidget(self.denoise_container, 1, 1)
                        self.adv_grid_layout.setColumnStretch(0, 1)
                        self.adv_grid_layout.setColumnStretch(1, 1)
                    else:
                        self.adv_grid_layout.addWidget(self.deband_chk, 0, 0)
                        self.adv_grid_layout.addWidget(self.autocrop_chk, 1, 0)
                        self.adv_grid_layout.addWidget(self.resharpen_chk, 2, 0)
                        if hasattr(self, "denoise_container"):
                            self.adv_grid_layout.addWidget(self.denoise_container, 3, 0)
                        self.adv_grid_layout.setColumnStretch(0, 1)
                        self.adv_grid_layout.setColumnStretch(1, 0)
            if hasattr(self, "colortemperature_slider") and hasattr(self, "brightness_slider") and hasattr(self, "saturation_slider"):
                if hasattr(self, "adv_filters_panel") and self.adv_filters_panel.layout() is not None:
                    self.adv_filters_panel.layout().activate()
                ct_geo = self.colortemperature_slider.geometry()
                if ct_geo.width() > 0:
                    if not self.brightness_slider.isVisible():
                        self.brightness_slider.setGeometry(ct_geo)
                    if not self.saturation_slider.isVisible():
                        self.saturation_slider.setGeometry(ct_geo)
        finally:
            self._in_adaptive_layout = False

    def _ensure_right_column_width(self):
        """Ensure right_panel minimumWidth is at least conv_scroll_content's
        minimumSizeHint().width() + scrollbar width + card margins so no child
        control is ever clipped horizontally inside conv_scroll.viewport()."""
        if not hasattr(self, "conv_scroll_content") or not hasattr(self, "right_panel"):
            return
        if hasattr(self, "_update_conversion_adaptive_layout"):
            self._update_conversion_adaptive_layout()
        if self.conv_scroll_content.layout() is not None:
            self.conv_scroll_content.layout().activate()
        content_w = self.conv_scroll_content.minimumSizeHint().width()
        sb_w = max(12, self.conv_scroll.verticalScrollBar().sizeHint().width() or 12)
        card_margins = 32  # Card left(16) + right(16)
        needed = min(448, max(350, content_w + sb_w + card_margins + 8))
        # Never shrink right_panel minimumWidth when sections are collapsed so collapsing
        # a section cannot trigger a horizontal QSplitter resize stutter.
        prev_min = getattr(self, "_right_panel_min_w_floor", 350)
        if all(getattr(self, "_section_expanded", {}).values()):
            prev_min = needed
        else:
            prev_min = max(prev_min, needed)
        self._right_panel_min_w_floor = prev_min
        if self.right_panel.minimumWidth() != prev_min:
            self.right_panel.setMinimumWidth(prev_min)

    def toggle_conversion_section(self, sec_key: str):
        body_w = getattr(self, "_section_bodies", {}).get(sec_key)
        hdr_pair = getattr(self, "_section_headers", {}).get(sec_key)
        if body_w is None or hdr_pair is None:
            return
        hdr, title_src = hdr_pair
        expanded = body_w.isHidden()
        self._section_expanded[sec_key] = expanded
        self.setUpdatesEnabled(False)
        try:
            body_w.setVisible(expanded)
            self.i18n.settings.setValue(f"section_expanded_{sec_key}", "true" if expanded else "false")
            arrow = "\u25be " if expanded else "\u25b8 "
            hdr.setProperty("i18n_text", None)
            hdr.setText(arrow + self.tr(title_src).upper())
            if expanded:
                self._ensure_right_column_width()
        finally:
            self.setUpdatesEnabled(True)
        self._repaint_conversion_card()
        QTimer.singleShot(0, self.refresh_blur)

    def _retranslate_section_headers(self):
        for sec_key, (hdr, title_src) in getattr(self, "_section_headers", {}).items():
            body_w = self._section_bodies.get(sec_key)
            expanded = (not body_w.isHidden()) if body_w is not None else self._section_expanded.get(sec_key, True)
            arrow = "\u25be " if expanded else "\u25b8 "
            hdr.setProperty("i18n_text", None)
            hdr.setText(arrow + self.tr(title_src).upper())

    def _style_pro_drop_zone(self):
        if not hasattr(self, "files_drop_icon"):
            return
        t = getattr(self, "tokens", None) or build_tokens(theme_name="light")
        r = int(t["radius_button"])
        self.files_empty_drop_widget.setStyleSheet(
            f"QFrame#filesEmptyDropWidget {{ background: transparent; border: 1.5px dashed {t['border_strong']}; border-radius: {r}px; }}"
        )
        self.files_drop_icon.setPixmap(video_drop_glyph(t["text_secondary"], 24))
        self.files_drop_label.setStyleSheet(
            f"QLabel#filesDropLabel {{ font-size: 13px; color: {t['text']}; border: none; background: transparent; }}"
        )

    def _update_queue_hint(self, *args):
        """While queue is empty: show the compact drop zone in FILES (same 64px
        fixed height as queue_list). Source analysis & Live preview stay visible
        with their placeholders at all times, so neither the Files card nor the
        window changes height when files are added or removed."""
        empty = self.queue_list.count() == 0
        self.queue_hint_label.setGeometry(self.queue_list.viewport().rect())
        self.queue_hint_label.setVisible(False)
        if hasattr(self, "files_empty_drop_widget"):
            self.files_empty_drop_widget.setVisible(empty)
            self.queue_list.setVisible(not empty)
            if hasattr(self, "files_drop_label"):
                active_src = (getattr(self, "_run_context", None) and self._run_context.source) or self.src_edit.text()
                if empty and self._is_proc_running() and active_src:
                    fname = Path(active_src).name
                    if len(fname) > 36:
                        fname = fname[:33] + "\u2026"
                    self.files_drop_label.setText(f"\u25b6 {fname}")
                    self.files_drop_label.setToolTip(str(active_src))
                else:
                    self.files_drop_label.setText(self.tr("Drop a video here"))
                    self.files_drop_label.setToolTip("")
            self._style_pro_drop_zone()
        if hasattr(self, "queue_remove_btn") and self.queue_remove_btn is not None:
            self.queue_remove_btn.setEnabled(not empty)
        if hasattr(self, "analyze_btn"):
            self.analyze_btn.setEnabled(not empty)

    def showEvent(self, event):
        self._install_app_filters()
        super().showEvent(event)
        if self.windowOpacity() < 1.0:       # was taken off screen by _switch_to_mini()
            self.setWindowOpacity(1.0)
        # One-time, not on every show()/hide()/show() cycle (Mini <-> Pro
        # switching re-shows this window repeatedly without needing this
        # again once it's already settled).
        #
        # This used to schedule the refit via QTimer.singleShot(0, ...)
        # instead of doing it here directly - which turned out to fix
        # nothing visible: a re-tested recording showed the exact same
        # jump, because a deferred call still lets Qt paint one frame at
        # the wrong geometry first and repaint at the corrected one a
        # tick later - the jump doesn't care whether the second pass was
        # Qt's own doing or ours, only that there were two visibly
        # different frames. So this instead suppresses repainting for the
        # entire span the fix needs, then applies it synchronously,
        # before a single pixel of the wrong geometry ever reaches the
        # screen - going straight from "not shown yet" to "already
        # settled" with nothing in between to see.
        if not getattr(self, "_post_show_refit_done", False):
            self._post_show_refit_done = True
            self.setUpdatesEnabled(False)
            QApplication.processEvents()
            if getattr(self, "_has_saved_geometry", False):
                self._ensure_right_column_width()
                self._invalidate_all_layouts()
                self.layout().activate()
                min_h = self._compute_minimum_window_height()
                self.setMinimumSize(920, min(min_h, max(960, self.height())))
                self._restore_saved_window_geometry()
            else:
                self._fit_height_to_content()
            QApplication.processEvents()
            self.setUpdatesEnabled(True)

    def closeEvent(self, event):
        self._cancel_after_action_countdown()
        self._conversion_preferences.save()
        self._save_window_geometry()
        if getattr(self, "_tray_quit_confirmed", False):
            self._tray_quit_confirmed = False
            choice = "quit"
        else:
            choice = self._confirm_close_while_converting(self)
        if choice == "cancel":
            event.ignore()
            return
        if choice == "tray":
            event.ignore()
            self._minimize_to_tray(self)
            return
        # choice == "quit": a conversion in progress (if any) was already
        # explicitly confirmed by the dialog above - mark it as a
        # deliberate stop rather than a failure so _on_finished() doesn't
        # also try to pop up its own "resume?" prompt on top of this
        # (there's nothing to resume into once the app has actually
        # quit - see _confirm_close_while_converting()'s own explanation).
        self.stopping = True
        power.release()
        for timer_name in ("_frame_preview_timer", "_live_apply_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        for worker in tuple(self._analysis_workers.values()):
            worker.requestInterruption()
        for worker in tuple(self._analysis_workers.values()):
            worker.wait(1000)
        self._analysis_workers.clear()
        for p in (self.proc, self._frame_preview_proc, self._test_clip_proc):
            if is_process_running(p):
                try:
                    p.kill()
                    p.waitForFinished(1000)
                except RuntimeError:
                    pass
        self._test_clip_runs.cleanup_all()
        setup = getattr(self, "_setup_proc", None)
        if setup is not None and is_valid_qobject(setup):
            if hasattr(setup, "cancel"):   # portable download thread: stops at the next chunk
                setup.cancel()
                setup.wait(3000)
            elif is_process_running(setup):   # winget QProcess
                try:
                    setup.kill()
                    setup.waitForFinished(1000)
                except RuntimeError:
                    pass
        self._remove_app_filters()
        event.accept()
        # Closing the Pro window's [X] quits the whole application, same
        # as closing Mini mode's [X] - previously this instead reshowed
        # the Mini launcher window ("go back" rather than "exit"), which
        # left that window lingering as a second thing to close even
        # after the person was done with the app entirely. Straightforward
        # "closing the window you're using exits the app" beats a hidden
        # "actually there's another window behind this one" surprise.
        QApplication.instance().quit()


    # ---- drag-and-drop (whole window accepts a dropped video) -----------
    # Deliberately no dedicated drop zone/panel - that would cost vertical
    # space for something used maybe once per file. Instead the whole
    # window accepts the drop and just fills in the existing Source field,
    # exactly as if you'd used Browse.
    VIDEO_EXTS = VIDEO_EXTENSIONS   # one shared list, see core/constants.py

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        paths = [url.toLocalFile() for url in urls]
        if any(Path(path).suffix.lower() in self.VIDEO_EXTS for path in paths) and not (
                self._is_proc_running()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        paths = [url.toLocalFile() for url in urls]
        paths = [path for path in paths if Path(path).suffix.lower() in self.VIDEO_EXTS]
        if not paths:
            event.ignore()
            return
        if self._is_proc_running():
            event.ignore()
            return
        self._add_files_to_queue(paths)
        event.acceptProposedAction()
