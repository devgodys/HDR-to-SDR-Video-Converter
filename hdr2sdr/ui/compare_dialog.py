from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QSize, QEvent, QObject, QProcess, QUrl, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hdr2sdr.core.utils import clock_time, exe
from hdr2sdr.core.progress import LineAssembler, make_progress_parser
from hdr2sdr.core.commands import build_test_clip_command
from hdr2sdr.runtime.clip_runs import ClipRunManager
from hdr2sdr.runtime import jobobject
from hdr2sdr.theme.palettes import _neon_active, _theme_is_dark
from hdr2sdr.theme.theme_tokens import build_button_states_qss, build_progress_bar_qss, rgba_str
from hdr2sdr.ui.icons import load_app_icon
from hdr2sdr.ui.tonemap_comparison import (
    TonemapCompareItem,
    TonemapComparator,
    build_comparison_variants,
)
from hdr2sdr.ui.widgets.buttons import GlassButton, TitleBarButton
from hdr2sdr.ui.widgets.containers import _TitleBarDragArea, paint_frameless_surface, window_radius
from hdr2sdr.ui.widgets.frame_strip import FrameStrip, FrameStripLoader
from hdr2sdr.ui.widgets.param_controls import ScrubSpinBox, TimelineSlider, make_value_chip
from hdr2sdr.ui.widgets.sliders import PreviewScrubSlider
from hdr2sdr.ui.widgets.toggles import ToggleSwitch
from hdr2sdr.ui.style_helpers import set_role

if TYPE_CHECKING:
    from hdr2sdr.ui.main_window import MainWindow


class ElidedLabel(QLabel):
    """QLabel that elides long text with an ellipsis (...) to fit available width without pushing layouts."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(20)
        if text:
            self.setToolTip(text)
            self._update_elided()

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_elided()

    def text(self) -> str:
        return self._full_text

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        m = self.contentsMargins()
        return QSize(
            fm.horizontalAdvance(self._full_text) + m.left() + m.right() + 2,
            fm.height() + m.top() + m.bottom(),
        )

    def minimumSizeHint(self) -> QSize:
        m = self.contentsMargins()
        return QSize(20, self.fontMetrics().height() + m.top() + m.bottom())

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.updateGeometry()
            self._update_elided()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self) -> None:
        w = max(0, self.width())
        fm = self.fontMetrics()
        m = self.contentsMargins()
        avail_w = max(0, w - m.left() - m.right())
        full_w = fm.horizontalAdvance(self._full_text)
        if avail_w > 0 and avail_w < full_w:
            elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, avail_w)
            super().setText(elided)
        else:
            super().setText(self._full_text)


class CompactProgressBar(QProgressBar):
    """QProgressBar with a compact minimum size hint to prevent layout expansion on narrow viewports."""

    def minimumSizeHint(self) -> QSize:
        return QSize(24, 8)


class RenderOverlayBadge(QFrame):
    """Floating semi-transparent status badge over the comparison workspace during rendering."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("renderOverlayBadge")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_lbl = QLabel()
        f = self.title_lbl.font()
        f.setWeight(QFont.Weight.Bold)
        f.setPointSize(f.pointSize() + 1)
        self.title_lbl.setFont(f)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet("color: #F4F4F5; background: transparent; border: none;")
        layout.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel()
        self.subtitle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_lbl.setStyleSheet("color: #A1A1AA; background: transparent; border: none;")
        layout.addWidget(self.subtitle_lbl)

        self.setStyleSheet(
            "QFrame#renderOverlayBadge {"
            "  background: rgba(18, 18, 22, 0.92);"
            "  border: 1px solid rgba(56, 189, 248, 0.6);"
            "  border-radius: 8px;"
            "}"
        )


class VariantListItemWidget(QWidget):
    """Compact two-row card for each tonemap + gamut variant in the sidebar."""

    def __init__(
        self,
        item: TonemapCompareItem,
        is_current: bool = False,
        tokens: dict[str, str] | None = None,
        dialog: TonemapComparisonDialog | None = None,
        index: int = 0,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.item = item
        self.dialog = dialog
        self.index = index
        self._hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        t = tokens or {}
        accent = t.get("accent", "#38BDF8")
        text_primary = t.get("text", "#F4F4F5")
        text_secondary = t.get("text_secondary", "#A1A1AA")
        border = t.get("border", "#3F3F46")
        bg_field = t.get("bg_field", "#18181B")
        accent_soft = rgba_str(accent, 0.14)
        accent_border = rgba_str(accent, 0.35)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(4)

        # Row 1: Method name + Engine tag + Current indicator + Status
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        title_text = item.method.split("\u00b7")[0].strip() if "\u00b7" in item.method else item.method
        self.name_lbl = ElidedLabel(title_text)
        f = self.name_lbl.font()
        f.setWeight(QFont.Weight.DemiBold)
        self.name_lbl.setFont(f)
        self.name_lbl.setStyleSheet(f"background: transparent; border: none; color: {text_primary}; font-size: 12px;")
        row1.addWidget(self.name_lbl)

        badge_text = item.engine.upper()
        self.badge_lbl = QLabel(badge_text)
        self.badge_lbl.setObjectName("variantEngineBadge")
        self.badge_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.badge_lbl.setStyleSheet(
            f"QLabel#variantEngineBadge {{ background: {accent_soft}; border: 1px solid {accent_border}; "
            f"border-radius: 4px; padding: 1px 5px; color: {accent}; font-size: 9.5px; font-weight: 600; }}"
        )
        row1.addWidget(self.badge_lbl)

        if is_current:
            cur_lbl = QLabel(self.tr("(current)"))
            cur_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            cur_lbl.setStyleSheet(
                f"background: transparent; border: none; color: {accent}; font-size: 10.5px; font-weight: 600;"
            )
            row1.addWidget(cur_lbl)

        row1.addStretch(1)
        self.status_lbl = QLabel(f"○ {self.tr('Pending')}")
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status_lbl.setStyleSheet(f"background: transparent; border: none; color: {text_secondary}; font-size: 11px;")
        row1.addWidget(self.status_lbl)
        lay.addLayout(row1)

        # Row 2: Gamut mapping on left + single-variant Render button on right (under status_lbl)
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        gamut_str = self.tr(item.gamut) if item.gamut != "N/A" else "—"
        self.gamut_lbl = QLabel(self.tr("Gamut: {gamut}", gamut=gamut_str))
        self.gamut_lbl.setStyleSheet(f"background: transparent; border: none; color: {text_secondary}; font-size: 11px;")
        row2.addWidget(self.gamut_lbl)
        row2.addStretch(1)

        self.render_one_btn = GlassButton(self.tr("Render"))
        self.render_one_btn.setObjectName("variantRenderOneBtn")
        self.render_one_btn.setFixedHeight(20)
        self.render_one_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.render_one_btn.setToolTip(self.tr("Render only this tone-mapping variant."))
        self.render_one_btn.setStyleSheet(
            f"QPushButton#variantRenderOneBtn {{ padding: 1px 8px; font-size: 10px; border-radius: 5px; "
            f"background: {bg_field}; border: 1px solid {border}; color: {text_secondary}; }}\n"
            f"QPushButton#variantRenderOneBtn:hover {{ border-color: {accent}; color: {text_primary}; }}"
        )
        self.render_one_btn.clicked.connect(self._on_render_one_clicked)
        row2.addWidget(self.render_one_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(row2)

        self.update_status()

    def enterEvent(self, event: Any) -> None:
        self._hovered = True
        self._sync_render_btn_visibility()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self._hovered = False
        self._sync_render_btn_visibility()
        super().leaveEvent(event)

    def _sync_render_btn_visibility(self) -> None:
        if not hasattr(self, "render_one_btn"):
            return
        if self.item.status == "ready":
            self.render_one_btn.setVisible(self._hovered)
        elif self.item.status == "rendering":
            self.render_one_btn.setVisible(False)
        else:
            is_sel = self.dialog is not None and getattr(self.dialog, "_selected_index", -1) == self.index
            self.render_one_btn.setVisible(self._hovered or is_sel)

    def _on_render_one_clicked(self) -> None:
        if self.dialog is not None and hasattr(self.dialog, "render_single_variant"):
            self.dialog.render_single_variant(self.index)

    def tr(self, source: str, **kwargs) -> str:
        if self.dialog is not None and hasattr(self.dialog, "tr"):
            return self.dialog.tr(source, **kwargs)
        return source.format(**kwargs) if kwargs else source

    def update_status(self) -> None:
        t = getattr(self.dialog, "tokens", {}) if self.dialog is not None else {}
        is_dark = _theme_is_dark(t) if t else True
        ready_col = "#4ADE80" if is_dark else "#16A34A"
        render_col = t.get("accent", "#38BDF8")
        err_col = "#F87171" if is_dark else "#DC2626"
        muted_col = t.get("text_secondary", "#9CA3AF")

        if self.item.status == "ready":
            self.status_lbl.setText(f"● {self.tr('Ready')}")
            self.status_lbl.setStyleSheet(
                f"background: transparent; border: none; color: {ready_col}; font-size: 11px; font-weight: 600;"
            )
            if hasattr(self, "render_one_btn"):
                self.render_one_btn.setEnabled(True)
                self.render_one_btn.setText(self.tr("Render"))
        elif self.item.status == "rendering":
            self.status_lbl.setText(f"⏳ {self.tr('Rendering…')}")
            self.status_lbl.setStyleSheet(
                f"background: transparent; border: none; color: {render_col}; font-size: 11px; font-weight: 600;"
            )
            if hasattr(self, "render_one_btn"):
                self.render_one_btn.setEnabled(False)
                self.render_one_btn.setText(self.tr("Rendering…"))
        elif self.item.status == "error":
            self.status_lbl.setText(f"⚠ {self.tr('Error')}")
            self.status_lbl.setStyleSheet(
                f"background: transparent; border: none; color: {err_col}; font-size: 11px; font-weight: 600;"
            )
            if self.dialog is not None:
                self.setToolTip(self.dialog._format_item_error(self.item))
            if hasattr(self, "render_one_btn"):
                self.render_one_btn.setEnabled(True)
                self.render_one_btn.setText(self.tr("Render"))
        elif self.item.status == "cancelled":
            self.status_lbl.setText(f"⏹ {self.tr('Cancelled')}")
            self.status_lbl.setStyleSheet(f"background: transparent; border: none; color: {muted_col}; font-size: 11px;")
            if hasattr(self, "render_one_btn"):
                self.render_one_btn.setEnabled(True)
                self.render_one_btn.setText(self.tr("Render"))
        else:
            self.status_lbl.setText(f"○ {self.tr('Pending')}")
            self.status_lbl.setStyleSheet(f"background: transparent; border: none; color: {muted_col}; font-size: 11px;")
            if hasattr(self, "render_one_btn"):
                self.render_one_btn.setEnabled(True)
                self.render_one_btn.setText(self.tr("Render"))
        self._sync_render_btn_visibility()


class FullscreenFrameDialog(QDialog):
    """Fullscreen viewer for comparison frames preserving aspect ratio and allowing variant switching."""

    def __init__(
        self,
        pixmap: QPixmap,
        title: str = "",
        tokens: dict[str, str] | None = None,
        parent: QWidget | None = None,
        variants: list[TonemapCompareItem] | None = None,
        current_index: int = 0,
        filter_mode: str = "all",
        on_index_changed: Any = None,
    ):
        super().__init__(parent)
        self.pixmap = pixmap
        self._variants = variants or []
        self.current_index = current_index
        self._filter_mode = filter_mode
        self._on_index_changed = on_index_changed
        t = tokens or {}
        bg = t.get("bg_window", "#09090B")
        text_primary = t.get("text", "#F4F4F5")
        text_secondary = t.get("text_secondary", "#A1A1AA")
        accent = t.get("accent", "#38BDF8")

        self.setWindowTitle(title or self.tr("Full size preview"))
        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {text_primary}; }}\n"
            f"QPushButton {{ padding: 4px 12px; }}"
        )
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Top bar with navigation buttons, title, shortcut tip, and Return/Back button
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(8)

        self.prev_btn = GlassButton("◀ " + self.tr("Previous"))
        self.prev_btn.setFixedHeight(30)
        self.prev_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.prev_btn.clicked.connect(lambda: self.step_variant(-1))
        top_bar.addWidget(self.prev_btn)

        self.variant_pos_lbl = QLabel("")
        self.variant_pos_lbl.setStyleSheet(
            f"background: transparent; border: none; color: {accent}; font-weight: 600;"
        )
        top_bar.addWidget(self.variant_pos_lbl)

        self.next_btn = GlassButton(self.tr("Next") + " ▶")
        self.next_btn.setFixedHeight(30)
        self.next_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.next_btn.clicked.connect(lambda: self.step_variant(1))
        top_bar.addWidget(self.next_btn)

        self.title_lbl = QLabel(title)
        f = self.title_lbl.font()
        f.setWeight(QFont.Weight.DemiBold)
        self.title_lbl.setFont(f)
        self.title_lbl.setStyleSheet(f"background: transparent; border: none; color: {text_primary};")
        top_bar.addWidget(self.title_lbl)

        self.hint_lbl = QLabel(self.tr("Press Esc to return"))
        self.hint_lbl.setStyleSheet(f"background: transparent; border: none; color: {text_secondary}; font-size: 11px;")
        top_bar.addWidget(self.hint_lbl)

        top_bar.addStretch(1)

        self.back_btn = GlassButton(self.tr("Back"))
        self.back_btn.setFixedHeight(30)
        self.back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.back_btn.clicked.connect(self.accept)
        top_bar.addWidget(self.back_btn)

        layout.addLayout(top_bar)

        # Centered image label
        self.image_lbl = QLabel()
        self.image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_lbl.setStyleSheet("QLabel { background: transparent; border: none; }")
        layout.addWidget(self.image_lbl, 1)

        self.refresh_navigation()

        screen = self.screen() if hasattr(self, "screen") else None
        avail = screen.availableGeometry() if screen else None
        max_w = int(avail.width() * 0.95) if avail else 1600
        max_h = int(avail.height() * 0.95) if avail else 900
        init_w = min(max_w, max(800, pixmap.width()))
        init_h = min(max_h, max(600, pixmap.height()))
        self.resize(init_w, init_h)

    def tr(self, source: str, **kwargs) -> str:
        if self.parent() is not None and hasattr(self.parent(), "tr"):
            return self.parent().tr(source, **kwargs)
        return source.format(**kwargs) if kwargs else source

    def _navigable_indices(self) -> list[int]:
        if not self._variants:
            return []
        filtered = [
            i
            for i, v in enumerate(self._variants)
            if v.status == "ready"
            and v.pixmap is not None
            and not v.pixmap.isNull()
            and (
                self._filter_mode == "all"
                or (self._filter_mode == "gpu" and v.engine == "gpu")
                or (self._filter_mode == "cpu" and v.engine == "cpu")
            )
        ]
        if filtered:
            return filtered
        return [
            i
            for i, v in enumerate(self._variants)
            if v.status == "ready" and v.pixmap is not None and not v.pixmap.isNull()
        ]

    def _format_variant_title(self, item: TonemapCompareItem) -> str:
        if item.engine == "gpu":
            gamut_text = self.tr("Gamut: {gamut}", gamut=self.tr(item.gamut))
        else:
            gamut_text = self.tr("Gamut: N/A (CPU tonemap)")
        return f"{item.method}  ·  {gamut_text}"

    def refresh_navigation(self) -> None:
        has_variants = bool(self._variants)
        self.prev_btn.setVisible(has_variants)
        self.next_btn.setVisible(has_variants)
        self.variant_pos_lbl.setVisible(has_variants)
        if not has_variants:
            return
        nav = self._navigable_indices()
        can_step = len(nav) > 1
        self.prev_btn.setEnabled(can_step)
        self.next_btn.setEnabled(can_step)
        if self.current_index in nav:
            pos = nav.index(self.current_index) + 1
            self.variant_pos_lbl.setText(f"{pos} / {len(nav)}")
        elif nav:
            self.variant_pos_lbl.setText(f"1 / {len(nav)}")
        else:
            self.variant_pos_lbl.setText("0 / 0")

    def set_variant_index(self, index: int) -> None:
        if not (0 <= index < len(self._variants)):
            return
        item = self._variants[index]
        if item.pixmap is None or item.pixmap.isNull():
            return
        self.current_index = index
        self.pixmap = item.pixmap
        new_title = self._format_variant_title(item)
        self.title_lbl.setText(new_title)
        self.setWindowTitle(new_title)
        self.refresh_navigation()
        self._update_scaled_pixmap()
        if callable(self._on_index_changed):
            self._on_index_changed(index)

    def step_variant(self, delta: int) -> None:
        nav = self._navigable_indices()
        if not nav:
            return
        if self.current_index in nav:
            cur_pos = nav.index(self.current_index)
            next_idx = nav[(cur_pos + delta) % len(nav)]
        else:
            next_idx = nav[0]
        self.set_variant_index(next_idx)

    def keyPressEvent(self, event: Any) -> None:
        if hasattr(event, "key"):
            k = event.key()
            if k == Qt.Key.Key_Escape:
                self.reject()
                return
            if self._variants and k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
                self.step_variant(-1)
                return
            if self._variants and k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
                self.step_variant(1)
                return
        super().keyPressEvent(event)

    def wheelEvent(self, event: Any) -> None:
        if self._variants and hasattr(event, "angleDelta"):
            dy = event.angleDelta().y()
            if dy > 0:
                self.step_variant(-1)
                event.accept()
                return
            if dy < 0:
                self.step_variant(1)
                event.accept()
                return
        super().wheelEvent(event)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self) -> None:
        if self.pixmap and not self.pixmap.isNull():
            w = max(100, self.image_lbl.width())
            h = max(100, self.image_lbl.height())
            scaled = self.pixmap.scaled(
                w,
                h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_lbl.setPixmap(scaled)


class TonemapComparisonDialog(QDialog):
    """Modal visual comparison dialog for all applicable tone-mapping methods on a single frame."""

    def __init__(self, parent_window: MainWindow):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self.tokens = getattr(parent_window, "tokens", {})
        self.theme_name = getattr(parent_window, "theme_name", "dark")
        self.ui_style = getattr(parent_window, "ui_style", "classic")
        self.neon_effect = getattr(parent_window, "neon_effect", False)
        self._frameless = True
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.src_path = str(parent_window.src_edit.text())
        self.seek = float(parent_window._frame_preview_target_seconds())
        self.duration = float(getattr(parent_window, "duration", 0.0) or 0.0)
        if not self.duration and hasattr(parent_window, "preview_slider"):
            self.duration = float(parent_window.preview_slider.maximum())
        self.base_settings = parent_window.collect_settings()

        has_bt2390 = getattr(parent_window, "has_bt2390", True)
        has_st2094 = getattr(parent_window, "has_st2094", False)
        dv_profile = getattr(parent_window, "_dv_profile", None)

        self.variants = build_comparison_variants(has_bt2390, has_st2094, dv_profile)
        self.comparator = TonemapComparator(
            ffmpeg_exe=exe("ffmpeg") or "ffmpeg",
            src_path=self.src_path,
            seek=self.seek,
            base_settings=self.base_settings,
            variants=self.variants,
            parent=self,
        )

        self.clip_manager = getattr(parent_window, "_test_clip_runs", None) or ClipRunManager()
        self._test_clip_proc: QProcess | None = None
        self._test_clip_out: Path | None = None
        self._test_clip_assembler = LineAssembler()
        self._test_clip_parser = make_progress_parser(False)
        self._last_test_clip_error: str = ""

        self._seek_debounce_timer = QTimer(self)
        self._seek_debounce_timer.setSingleShot(True)
        self._seek_debounce_timer.timeout.connect(self._on_seek_debounce_timeout)

        # Placeholder image tracking (source frame or last ready variant)
        self._placeholder_pixmap: QPixmap | None = getattr(parent_window, "_current_preview_pixmap", None)
        if self._placeholder_pixmap is not None and self._placeholder_pixmap.isNull():
            self._placeholder_pixmap = None
        self._last_ready_pixmap: QPixmap | None = None
        self._custom_duration: int = 5

        self.setWindowTitle(self.tr("Compare tone-mapping methods"))
        self.resize(1080, 720)
        self.setMinimumSize(920, 600)
        self._active_filter = "all"
        self._selected_index = 0
        self._expected_comparator_gen: int | None = None

        settings = getattr(getattr(parent_window, "i18n", None), "settings", None)
        raw_auto = settings.value("compare_auto_render", False) if settings is not None else False
        if isinstance(raw_auto, str):
            self._auto_render = raw_auto.strip().lower() in ("1", "true", "yes", "on")
        else:
            self._auto_render = bool(raw_auto)

        self._build_ui()
        self._connect_signals()

        # Start sequential rendering if auto-render is enabled; otherwise load cached frames only
        if self._auto_render:
            self._render_all_frames()
        else:
            self._expected_comparator_gen = getattr(self.comparator, "_gen", 0)
            self.comparator.prepare_cache()
            for v in self.variants:
                if v.status == "ready" and v.pixmap is not None and not v.pixmap.isNull():
                    self._last_ready_pixmap = v.pixmap
                    break
            self._populate_list()
            ready_count = sum(1 for v in self.variants if v.status == "ready")
            self.progress_bar.setValue(ready_count)
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.hide()
            self._sync_render_frames_btn(running=False)
            self.status_lbl.setText(
                self.tr("{ready} of {total} variants ready (cancelled)", ready=ready_count, total=len(self.variants))
            )
            first_ready_idx = next((i for i, v in enumerate(self.variants) if v.status == "ready"), 0)
            if 0 <= first_ready_idx < len(self.variants):
                self._selected_index = first_ready_idx
                for row in range(self.list_widget.count()):
                    l_item = self.list_widget.item(row)
                    if l_item.data(Qt.ItemDataRole.UserRole) == first_ready_idx:
                        self.list_widget.setCurrentRow(row)
                        break
                self._select_variant(first_ready_idx)

    def tr(self, source: str, **kwargs) -> str:
        if hasattr(self.parent_window, "tr"):
            return self.parent_window.tr(source, **kwargs)
        return super().tr(source)

    def paintEvent(self, event: Any) -> None:
        t = self.tokens
        ring = None
        if _neon_active(self):
            ring = QColor(t.get("accent", "#38BDF8"))
            ring.setAlpha(140)
        paint_frameless_surface(
            self,
            window_radius(self),
            t.get("border", "#3F3F46"),
            self._paint_dialog_backdrop,
            ring,
        )
        super().paintEvent(event)

    def _paint_dialog_backdrop(self, painter: QPainter) -> None:
        bg_layer = getattr(self.parent_window, "_bg_layer", None)
        if bg_layer is not None and hasattr(bg_layer, "_paint_contents"):
            painter.fillRect(self.rect(), QColor(self.tokens.get("bg_window", "#18181B")))
            if hasattr(bg_layer, "_paint_backdrop"):
                bg_layer._paint_backdrop(
                    painter,
                    self.tokens,
                    getattr(self, "theme_name", "dark"),
                    _theme_is_dark(self.tokens),
                )
            return
        painter.fillRect(self.rect(), QColor(self.tokens.get("bg_window", "#18181B")))

    def _build_ui(self) -> None:
        t = self.tokens
        bg = t.get("bg_window", "#18181B")
        card_bg = t.get("bg_card", "#27272A")
        card_alt = t.get("bg_card_alt", "#303036")
        bg_field = t.get("bg_field", "#18181B")
        border = t.get("border", "#3F3F46")
        text_primary = t.get("text", "#F4F4F5")
        text_secondary = t.get("text_secondary", "#A1A1AA")
        accent = t.get("accent", "#38BDF8")
        accent_soft = rgba_str(accent, 0.14)
        btn_states_qss = build_button_states_qss(t, int(t.get("radius_button", 8)))
        pbar_qss = build_progress_bar_qss(t, getattr(self, "theme_name", "dark"), 8)

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {text_primary}; }}\n"
            f"{btn_states_qss}\n"
            f"{pbar_qss}\n"
            f"QListWidget {{ background: {card_bg}; border: 1px solid {border}; border-radius: 10px; padding: 5px; outline: none; }}\n"
            f"QListWidget::item {{ background: {bg_field}; border: 1px solid {border}; border-radius: 8px; margin: 0px; }}\n"
            f"QListWidget::item:hover {{ background: {card_alt}; border: 1px solid {border}; border-radius: 8px; }}\n"
            f"QListWidget::item:selected {{ background: {accent_soft}; border: 1.5px solid {accent}; border-radius: 8px; }}\n"
            f"QListWidget QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px 1px 2px 1px; border: none; }}\n"
            f"QListWidget QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 24px; }}\n"
            f"QListWidget QScrollBar::handle:vertical:hover {{ background: {text_secondary}; }}\n"
            f"QListWidget QScrollBar::add-line:vertical, QListWidget QScrollBar::sub-line:vertical {{ height: 0px; border: none; background: transparent; }}\n"
            f"QListWidget QScrollBar::add-page:vertical, QListWidget QScrollBar::sub-page:vertical {{ background: transparent; }}\n"
            f"QFrame#compareHeaderCard {{ background: transparent; border: none; }}\n"
            f"QFrame#compareHeaderCard QLabel {{ background: transparent; border: none; border-radius: 0px; padding: 0px; }}\n"
            f"QFrame#compareImageContainer {{ background: #09090B; border: 1px solid {border}; border-radius: 10px; }}\n"
            f"QFrame#compareImageContainer QLabel {{ background: transparent; border: none; }}\n"
            f"QFrame#compareFrameBar {{ background: #18181B; border-top: 1px solid {border}; border-bottom-left-radius: 7px; border-bottom-right-radius: 7px; }}\n"
            f"QFrame#compareFrameBar QLabel {{ background: transparent; border: none; color: {text_primary}; }}\n"
            f"QFrame#compareFrameBar QLabel:disabled {{ color: {text_secondary}; }}\n"
            f"QLabel#compareFrameSectionLbl {{ color: {text_secondary}; font-weight: 600; }}\n"
            f"QLabel#compareFrameSectionLbl:disabled {{ color: #71717A; }}\n"
            f"QLabel#comparePositionLbl {{ color: {text_primary}; font-weight: 600; }}\n"
            f"QLabel#comparePositionLbl:disabled {{ color: {text_secondary}; }}\n"
            f"QFrame#compareActionsCard {{ background: {card_bg}; border: 1px solid {border}; border-radius: 10px; }}\n"
            f"QFrame#compareActionsCard QLabel {{ background: transparent; border: none; border-radius: 0px; padding: 0px; }}\n"
            f"QPushButton {{ padding: 4px 10px; font-size: 11px; }}\n"
            f"QPushButton[role=\"preset-segment-btn\"] {{ padding: 3px 8px; font-size: 11px; }}\n"
            f"QPushButton[compact_action=\"true\"] {{ padding: 2px 7px 2px 21px; font-size: 11px; border-radius: 6px; background: {bg_field}; border: 1px solid {border}; color: {text_primary}; }}\n"
            f"QPushButton[compact_action=\"true\"]:hover {{ background: {card_alt}; border-color: {accent}; }}\n"
            f"QPushButton[compact_action=\"true\"]:disabled {{ background: transparent; border: 1px solid {border}; color: {text_secondary}; }}\n"
            f"QPushButton[compact_action=\"true\"][role=\"icon-btn-ready\"] {{ background: {t.get('success', '#10B981')}; color: white; border: 1px solid {t.get('success', '#10B981')}; padding: 2px 7px 2px 21px; font-size: 11px; }}\n"
            f"QPushButton#compareCancelBtn {{ padding: 2px 8px; font-size: 11px; border-radius: 6px; }}\n"
            f"QPushButton#compareHeaderApplyBtn {{ padding: 4px 14px 4px 28px; font-size: 11.5px; font-weight: 600; border-radius: 6px; }}\n"
            f"QPushButton#compareHeaderCloseBtn {{ padding: 4px 12px; font-size: 11.5px; border-radius: 6px; }}\n"
            f"QSpinBox {{ background: {bg_field}; color: {text_primary}; border: 1px solid {border}; border-radius: 6px; padding: 2px 6px; }}"
        )

        outer_lay = QVBoxLayout(self)
        outer_lay.setContentsMargins(14, 10, 14, 14)
        outer_lay.setSpacing(8)

        # ===== FRAMELESS MODERN TITLE BAR =====
        self.titlebar = _TitleBarDragArea(self)
        tb_lay = QHBoxLayout(self.titlebar)
        tb_lay.setContentsMargins(2, 0, 0, 2)
        tb_lay.setSpacing(8)

        app_icon = load_app_icon()
        if not app_icon.isNull():
            icon_lbl = QLabel()
            icon_lbl.setPixmap(app_icon.pixmap(20, 20))
            icon_lbl.setStyleSheet("QLabel { background: transparent; border: none; }")
            tb_lay.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.window_title_lbl = QLabel(self.tr("Compare tone-mapping methods"))
        f_wtitle = self.window_title_lbl.font()
        f_wtitle.setPointSize(max(9, f_wtitle.pointSize()))
        f_wtitle.setWeight(QFont.Weight.DemiBold)
        self.window_title_lbl.setFont(f_wtitle)
        self.window_title_lbl.setStyleSheet(f"QLabel {{ background: transparent; border: none; color: {text_primary}; }}")
        tb_lay.addWidget(self.window_title_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        tb_lay.addStretch(1)

        self._minimize_btn = TitleBarButton("–", parent=self.titlebar)
        self._minimize_btn.setToolTip(self.tr("Minimize"))
        self._minimize_btn.clicked.connect(self.showMinimized)
        tb_lay.addWidget(self._minimize_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._title_close_btn = TitleBarButton("×", danger=True, parent=self.titlebar)
        self._title_close_btn.setToolTip(self.tr("Close"))
        self._title_close_btn.clicked.connect(self.reject)
        tb_lay.addWidget(self._title_close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        outer_lay.addWidget(self.titlebar)

        main_lay = QHBoxLayout()
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(14)
        outer_lay.addLayout(main_lay, 1)

        # ===== LEFT SIDEBAR: Methods list =====
        left_widget = QWidget()
        left_widget.setFixedWidth(316)
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        # Header + Auto-render toggle
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(2, 0, 2, 0)
        hdr_row.setSpacing(8)
        top_lbl = QLabel(self.tr("Methods & Gamut"))
        f_top = top_lbl.font()
        f_top.setPointSize(max(9, f_top.pointSize() + 1))
        f_top.setWeight(QFont.Weight.Bold)
        top_lbl.setFont(f_top)
        top_lbl.setStyleSheet(f"background: transparent; border: none; color: {text_primary};")
        hdr_row.addWidget(top_lbl)
        hdr_row.addStretch(1)

        self.auto_render_chk = ToggleSwitch(self.tr("Auto-render"), label_left=True)
        self.auto_render_chk.setProperty("label_left", True)
        self.auto_render_chk.setProperty("text_color", text_primary)
        self.auto_render_chk.setProperty("on_color", accent)
        self.auto_render_chk.setProperty("off_color", border)
        self.auto_render_chk.setStyleSheet(f"color: {text_primary}; background: transparent;")
        self.auto_render_chk.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.auto_render_chk.setChecked(self._auto_render)
        self.auto_render_chk._pos = 1.0 if self._auto_render else 0.0
        self.auto_render_chk.setToolTip(
            self.tr("Automatically render all comparison variants when opening or scrubbing.")
        )
        self.auto_render_chk.toggled.connect(self._on_auto_render_toggled)
        hdr_row.addWidget(self.auto_render_chk, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        left_lay.addLayout(hdr_row)

        # Filter buttons row inside a segmented pill container
        self.filter_segment_frame = QFrame()
        set_role(self.filter_segment_frame, "preset-segment")
        filter_row = QHBoxLayout(self.filter_segment_frame)
        filter_row.setContentsMargins(2, 2, 2, 2)
        filter_row.setSpacing(4)

        self.btn_filter_all = GlassButton(f"{self.tr('All')} ({len(self.variants)})")
        self.btn_filter_gpu = GlassButton(
            f"{self.tr('GPU')} ({sum(1 for v in self.variants if v.engine == 'gpu')})"
        )
        self.btn_filter_cpu = GlassButton(
            f"{self.tr('CPU')} ({sum(1 for v in self.variants if v.engine == 'cpu')})"
        )

        for b in (self.btn_filter_all, self.btn_filter_gpu, self.btn_filter_cpu):
            b.setCheckable(True)
            set_role(b, "preset-segment-btn")
            b.setFixedHeight(26)
            filter_row.addWidget(b, 1)
        self.btn_filter_all.setChecked(True)
        left_lay.addWidget(self.filter_segment_frame)

        self.btn_filter_all.clicked.connect(lambda: self._set_filter("all"))
        self.btn_filter_gpu.clicked.connect(lambda: self._set_filter("gpu"))
        self.btn_filter_cpu.clicked.connect(lambda: self._set_filter("cpu"))

        # Variants List
        self.list_widget = QListWidget()
        self.list_widget.setSpacing(4)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_lay.addWidget(self.list_widget, 1)

        self._populate_list()
        main_lay.addWidget(left_widget)

        # ===== RIGHT SIDE: Large frame preview + info + actions =====
        right_widget = QWidget()
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)

        # Info Header area (Left: active variant title + meta + disclaimer; Right: Apply to conversion + Close)
        self.header_card = QFrame()
        self.header_card.setObjectName("compareHeaderCard")
        self.header_card.setStyleSheet(
            f"QFrame#compareHeaderCard {{ background: transparent; border: none; padding: 0px; }}\n"
            f"QFrame#compareHeaderCard QLabel {{ background: transparent; border: none; border-radius: 0px; padding: 0px; }}"
        )
        hdr_outer = QHBoxLayout(self.header_card)
        hdr_outer.setContentsMargins(2, 0, 2, 2)
        hdr_outer.setSpacing(10)

        h_lay = QVBoxLayout()
        h_lay.setContentsMargins(0, 0, 0, 0)
        h_lay.setSpacing(2)

        self.active_variant_title = ElidedLabel("")
        f_title = self.active_variant_title.font()
        f_title.setPointSize(f_title.pointSize() + 2)
        f_title.setWeight(QFont.Weight.Bold)
        self.active_variant_title.setFont(f_title)
        self.active_variant_title.setStyleSheet(f"background: transparent; border: none; color: {text_primary};")
        h_lay.addWidget(self.active_variant_title)

        self.active_variant_meta = ElidedLabel("")
        self.active_variant_meta.setStyleSheet(
            f"background: transparent; border: none; color: {text_secondary}; font-size: 11.5px;"
        )
        h_lay.addWidget(self.active_variant_meta)

        self.disclaimer_lbl = QLabel(
            self.tr(
                "Visual comparison of tone-mapping methods on the current frame. Not an objective quality score."
            )
        )
        self.disclaimer_lbl.setStyleSheet(
            f"background: transparent; border: none; color: {text_secondary}; font-size: 10.5px;"
        )
        self.disclaimer_lbl.setWordWrap(True)
        h_lay.addWidget(self.disclaimer_lbl)

        hdr_outer.addLayout(h_lay, 1)

        # Primary dialog actions right above the preview (Apply to conversion + Close)
        self.b_lay = QHBoxLayout()
        self.b_lay.setContentsMargins(0, 0, 0, 0)
        self.b_lay.setSpacing(6)

        self.apply_btn = GlassButton(self.tr("Apply to conversion"))
        self.apply_btn.setObjectName("compareHeaderApplyBtn")
        self.apply_btn.setProperty("lead_icon", "check")
        self.apply_btn.setFixedHeight(30)
        set_role(self.apply_btn, "go")
        self.apply_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.apply_btn.setToolTip(self.tr("Apply the selected tone-mapping method and frame position to the main window."))
        self.apply_btn.clicked.connect(self._apply_active_variant)

        self.close_btn = GlassButton(self.tr("Close"), self)
        self.close_btn.setObjectName("compareHeaderCloseBtn")
        self.close_btn.setFixedHeight(30)
        self.close_btn.clicked.connect(self.reject)
        self.close_btn.hide()

        self.b_lay.addWidget(self.apply_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        hdr_outer.addLayout(self.b_lay, 0)

        right_lay.addWidget(self.header_card)

        # Central Image Display
        self.image_container = QFrame()
        self.image_container.setObjectName("compareImageContainer")
        self.image_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_container.setStyleSheet(
            f"QFrame#compareImageContainer {{ background: #09090B; border: 1px solid {border}; border-radius: 10px; }}\n"
            f"QFrame#compareImageContainer QLabel {{ background: transparent; border: none; }}"
        )
        img_lay = QVBoxLayout(self.image_container)
        img_lay.setContentsMargins(4, 4, 4, 4)
        img_lay.setSpacing(0)

        self.image_lbl = QLabel()
        self.image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.image_lbl.setStyleSheet("QLabel { background: transparent; border: none; }")
        self.image_lbl.installEventFilter(self)
        img_lay.addWidget(self.image_lbl, 1)

        self.render_overlay_badge = RenderOverlayBadge(self.image_lbl)
        self.render_overlay_badge.hide()

        right_lay.addWidget(self.image_container, 1)

        # ---- Timeline zone directly above the Test frame / Test video clip card ----
        # Uses FrameStrip (matching Live preview) when video thumbnails are available,
        # and keeps preview_slider / preview_position_label synced (and visible as fallback).
        self.scrub_bar = QFrame()
        self.scrub_bar.setObjectName("compareScrubBar")
        self.scrub_bar.setStyleSheet(
            f"QFrame#compareScrubBar {{ background: transparent; border: none; }}\n"
            f"QFrame#compareScrubBar QLabel {{ background: transparent; border: none; }}"
        )
        scrub_outer = QVBoxLayout(self.scrub_bar)
        scrub_outer.setContentsMargins(0, 0, 0, 0)
        scrub_outer.setSpacing(0)

        self.scrub_slider_row = QWidget()
        scrub_lay = QHBoxLayout(self.scrub_slider_row)
        scrub_lay.setContentsMargins(4, 0, 4, 0)
        scrub_lay.setSpacing(10)

        self.preview_position_label = QLabel(clock_time(self.seek))
        self.preview_position_label.setObjectName("comparePositionLbl")
        scrub_lay.addWidget(self.preview_position_label)

        self.preview_slider = TimelineSlider(formatter=clock_time)
        set_role(self.preview_slider, "compact-slider")
        self.preview_slider.setFixedHeight(26)
        max_dur = max(1, int(self.duration)) if self.duration > 0 else 0
        self.preview_slider.setRange(0, max_dur)
        self.preview_slider.setValue(int(self.seek))
        is_scrub_enabled = self.duration > 0 or self.seek > 0
        self.preview_slider.setEnabled(is_scrub_enabled)
        self.preview_position_label.setEnabled(is_scrub_enabled)
        self.preview_slider.setToolTip(self.tr("Scrub frame — drag to preview any position in the video."))
        self.preview_slider.valueChanged.connect(self._on_seek_slider_changed)
        scrub_lay.addWidget(self.preview_slider, 1)
        scrub_outer.addWidget(self.scrub_slider_row)

        self.frame_strip = FrameStrip()
        self.frame_strip.hide()
        self.frame_strip_loader = FrameStripLoader(self)
        self.frame_strip_loader.frameReady.connect(self._on_compare_frame_strip_frame)
        self.frame_strip.seekRequested.connect(self._on_compare_strip_seek)
        scrub_outer.addWidget(self.frame_strip)

        right_lay.addWidget(self.scrub_bar)

        # ---- Unified Single-Row Studio Bar: Left = Frames status + Render, Right = Test clip (5 s + Render) ----
        self.actions_card = QFrame()
        self.actions_card.setObjectName("compareActionsCard")
        self.actions_card.setStyleSheet(
            f"QFrame#compareActionsCard {{ background: {card_bg}; border: 1px solid {border}; border-radius: 10px; }}\n"
            f"QFrame#compareActionsCard QLabel {{ background: transparent; border: none; border-radius: 0px; padding: 0px; }}\n"
            f"QFrame#compareFrameBar {{ background: transparent; border: none; }}"
        )
        act_lay = QHBoxLayout(self.actions_card)
        act_lay.setContentsMargins(10, 7, 10, 7)
        act_lay.setSpacing(8)

        # ---- Left section: Frames status + progress + Render test frames ----
        self.frame_bar = QFrame()
        self.frame_bar.setObjectName("compareFrameBar")
        fb_lay = QHBoxLayout(self.frame_bar)
        fb_lay.setContentsMargins(0, 0, 0, 0)
        fb_lay.setSpacing(6)

        self.frame_section_lbl = QLabel(self.tr("Test frame:"))
        self.frame_section_lbl.setObjectName("compareFrameSectionLbl")
        f_fsec = self.frame_section_lbl.font()
        f_fsec.setWeight(QFont.Weight.DemiBold)
        self.frame_section_lbl.setFont(f_fsec)
        self.frame_section_lbl.setStyleSheet(f"color: {accent}; font-size: 11.5px;")
        self.frame_section_lbl.setEnabled(is_scrub_enabled)
        self.frame_section_lbl.hide()
        fb_lay.addWidget(self.frame_section_lbl)

        self.progress_bar = CompactProgressBar()
        self.progress_bar.setRange(0, len(self.variants))
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setMinimumWidth(28)
        self.progress_bar.setMaximumWidth(64)
        self.progress_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        fb_lay.addWidget(self.progress_bar, 0)

        self.status_lbl = ElidedLabel(
            self.tr("Rendering {current} of {total}…", current=1, total=len(self.variants))
        )
        self.status_lbl.setStyleSheet(
            f"background: transparent; border: none; color: {text_secondary}; font-size: 11px;"
        )
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        fb_lay.addWidget(self.status_lbl, 1)

        self.cancel_btn = GlassButton(self.tr("Cancel"))
        self.cancel_btn.setObjectName("compareCancelBtn")
        self.cancel_btn.setFixedHeight(24)
        self.cancel_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        fb_lay.addWidget(self.cancel_btn, 0)

        self.render_frames_btn = GlassButton(self.tr("Render test frames"))
        self.render_frames_btn.setFixedHeight(26)
        self.render_frames_btn.setMinimumWidth(112)
        set_role(self.render_frames_btn, "go")
        self.render_frames_btn.setToolTip(self.tr("Render all tone-mapping comparison frames for the current position."))
        self.render_frames_btn.clicked.connect(self._on_render_frames_clicked)
        fb_lay.addWidget(self.render_frames_btn, 0)

        act_lay.addWidget(self.frame_bar, 1)

        # Vertical divider between Frames section and Test clip section
        row_div = QFrame()
        row_div.setFrameShape(QFrame.Shape.VLine)
        row_div.setFrameShadow(QFrame.Shadow.Plain)
        row_div.setStyleSheet(f"background: {border}; max-width: 1px; min-height: 18px;")
        act_lay.addWidget(row_div)

        # ---- Right section: Test video clip: [ 5 s ] [▶ Open] [Render test clip] ----
        row_clip = QHBoxLayout()
        row_clip.setContentsMargins(0, 0, 0, 0)
        row_clip.setSpacing(6)

        self.clip_section_lbl = QLabel(self.tr("Test video clip:"))
        f_sec = self.clip_section_lbl.font()
        f_sec.setWeight(QFont.Weight.DemiBold)
        self.clip_section_lbl.setFont(f_sec)
        self.clip_section_lbl.setStyleSheet(f"color: {accent}; font-size: 11.5px;")
        row_clip.addWidget(self.clip_section_lbl)

        self.clip_dur_lbl = QLabel(self.tr("Duration:"), self)
        self.clip_dur_lbl.setStyleSheet(f"color: {text_secondary};")
        self.clip_dur_lbl.hide()

        sec_sfx = self.tr(" s").strip()
        self.btn_dur_3s = GlassButton(f"3 {sec_sfx}", self)
        self.btn_dur_5s = GlassButton(f"5 {sec_sfx}", self)
        self.btn_dur_10s = GlassButton(f"10 {sec_sfx}", self)
        self.btn_dur_custom = GlassButton(self.tr("Custom…"), self)

        for b, dur in ((self.btn_dur_3s, 3), (self.btn_dur_5s, 5), (self.btn_dur_10s, 10)):
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.setToolTip(self.tr("Set test clip duration to {seconds} seconds.", seconds=dur))
            b.clicked.connect(lambda _, d=dur: self._set_duration_preset(d))
            b.hide()
        self.btn_dur_custom.setCheckable(True)
        self.btn_dur_custom.setFixedHeight(26)
        self.btn_dur_custom.clicked.connect(self._toggle_custom_duration)
        self.btn_dur_custom.hide()

        # Single CRF-18-style ScrubSpinBox value chip ("5 s" by default: drag horizontally or click to type)
        self.test_clip_dur_spin = ScrubSpinBox("", self.tr(" s"), px_per_step=6.0)
        self.test_clip_dur_spin.setRange(1, 120)
        self.test_clip_dur_spin.setValue(5)
        self.test_clip_dur_spin.setToolTip(
            self.tr("Test clip duration in seconds.")
        )
        self.test_clip_dur_spin.valueChanged.connect(self._on_spin_value_changed)
        self.test_clip_dur_chip = make_value_chip(self.test_clip_dur_spin, 80, 26)
        row_clip.addWidget(self.test_clip_dur_chip)

        self.test_clip_status_lbl = ElidedLabel("")
        self.test_clip_status_lbl.setObjectName("testClipStatusLbl")
        self.test_clip_status_lbl.setFixedHeight(20)
        self.test_clip_status_lbl.setStyleSheet(f"color: {text_secondary}; font-size: 11px;")
        self.test_clip_status_lbl.hide()
        row_clip.addWidget(self.test_clip_status_lbl, 0)

        self.test_clip_open_btn = GlassButton(self.tr("Open test clip"))
        self.test_clip_open_btn.setProperty("compact_action", True)
        self.test_clip_open_btn.setProperty("lead_icon", "play")
        self.test_clip_open_btn.setFixedHeight(26)
        self.test_clip_open_btn.setEnabled(False)
        self.test_clip_open_btn.setToolTip(self.tr("Open the rendered test clip in your default media player."))
        self.test_clip_open_btn.clicked.connect(self._open_test_clip)
        self.test_clip_open_btn.hide()
        row_clip.addWidget(self.test_clip_open_btn, 0)

        self.test_clip_btn = GlassButton(self.tr("Render test clip"))
        self.test_clip_btn.setFixedHeight(26)
        self.test_clip_btn.setMinimumWidth(114)
        set_role(self.test_clip_btn, "go")
        self.test_clip_btn.clicked.connect(self._run_test_clip)
        row_clip.addWidget(self.test_clip_btn, 0)

        act_lay.addLayout(row_clip, 0)

        # ---- Floating mini-toolbar over top-right of preview image (Full size, Save frame, Save all) ----
        self.image_tools_overlay = QFrame(self.image_container)
        self.image_tools_overlay.setObjectName("compareImageToolsOverlay")
        self.image_tools_overlay.setStyleSheet(
            "QFrame#compareImageToolsOverlay {"
            "  background: rgba(18, 18, 22, 0.78);"
            "  border: 1px solid rgba(255, 255, 255, 0.14);"
            "  border-radius: 8px;"
            "}\n"
            "QFrame#compareImageToolsOverlay QPushButton {"
            "  background: transparent; border: none; border-radius: 6px; padding: 0px;"
            "}\n"
            f"QFrame#compareImageToolsOverlay QPushButton:hover {{ background: rgba(255, 255, 255, 0.14); }}"
        )
        it_lay = QHBoxLayout(self.image_tools_overlay)
        it_lay.setContentsMargins(4, 3, 4, 3)
        it_lay.setSpacing(3)

        self.fullscreen_btn = GlassButton("")
        self.fullscreen_btn.setProperty("lead_icon", "maximize")
        self.fullscreen_btn.setProperty("icon_kind", "maximize")
        self.fullscreen_btn.setProperty("icon_override_color", "#F4F4F5")
        self.fullscreen_btn.setIconSize(QSize(15, 15))
        self.fullscreen_btn.setFixedSize(26, 24)
        self.fullscreen_btn.setEnabled(False)
        self.fullscreen_btn.setToolTip(self.tr("View full size — open the current preview frame in a larger window."))
        self.fullscreen_btn.clicked.connect(self._show_fullscreen)
        it_lay.addWidget(self.fullscreen_btn)

        self.save_btn = GlassButton("")
        self.save_btn.setProperty("lead_icon", "save_frame")
        self.save_btn.setProperty("icon_kind", "save_frame")
        self.save_btn.setProperty("icon_override_color", "#F4F4F5")
        self.save_btn.setIconSize(QSize(15, 15))
        self.save_btn.setFixedSize(26, 24)
        self.save_btn.setEnabled(False)
        self.save_btn.setToolTip(self.tr("Save frame — save the current preview frame to disk as an image."))
        self.save_btn.clicked.connect(self._save_active_frame)
        it_lay.addWidget(self.save_btn)

        self.save_all_btn = GlassButton("")
        self.save_all_btn.setProperty("lead_icon", "save_all")
        self.save_all_btn.setProperty("icon_kind", "save_all")
        self.save_all_btn.setProperty("icon_override_color", "#F4F4F5")
        self.save_all_btn.setIconSize(QSize(15, 15))
        self.save_all_btn.setFixedSize(26, 24)
        self.save_all_btn.setEnabled(False)
        self.save_all_btn.setToolTip(self.tr("Save all frames — save all rendered comparison frames into a folder with mode names."))
        self.save_all_btn.clicked.connect(lambda _checked=False: self._save_all_frames())
        it_lay.addWidget(self.save_all_btn)
        self.image_tools_overlay.adjustSize()

        self._set_duration_preset(5)

        right_lay.addWidget(self.actions_card)
        self._init_compare_frame_strip()
        main_lay.addWidget(right_widget, 1)

        # Update initial display
        if self.variants:
            self._select_variant(0)

    def _populate_list(self) -> None:
        self.list_widget.clear()
        cur_method = self.parent_window.method_combo.currentText()
        cur_gamut = self.parent_window.gamut_combo.currentText()

        for i, item in enumerate(self.variants):
            if self._active_filter == "gpu" and item.engine != "gpu":
                continue
            if self._active_filter == "cpu" and item.engine != "cpu":
                continue

            is_cur = item.method == cur_method and (
                item.engine == "cpu" or item.gamut == cur_gamut
            )
            w = VariantListItemWidget(item, is_current=is_cur, tokens=self.tokens, dialog=self, index=i)
            list_item = QListWidgetItem()
            list_item.setSizeHint(QSize(0, 56))
            list_item.setData(Qt.ItemDataRole.UserRole, i)
            self.list_widget.addItem(list_item)
            self.list_widget.setItemWidget(list_item, w)

    def _set_filter(self, filter_mode: str) -> None:
        self._active_filter = filter_mode
        if hasattr(self, "btn_filter_all"):
            self.btn_filter_all.setChecked(filter_mode == "all")
            self.btn_filter_gpu.setChecked(filter_mode == "gpu")
            self.btn_filter_cpu.setChecked(filter_mode == "cpu")
        self._populate_list()
        # Reselect first visible item
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
            self._on_item_clicked(self.list_widget.item(0))

    def _connect_signals(self) -> None:
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.comparator.variant_started.connect(self._on_variant_started)
        self.comparator.variant_finished.connect(self._on_variant_finished)
        self.comparator.all_finished.connect(self._on_all_finished)

    def _on_item_clicked(self, list_item: QListWidgetItem) -> None:
        idx = list_item.data(Qt.ItemDataRole.UserRole)
        self._select_variant(idx)

    def _select_variant(self, index: int) -> None:
        if 0 <= index < len(self.variants):
            self._selected_index = index
            item = self.variants[index]

            title_name = item.method
            if item.engine == "gpu":
                gamut_text = self.tr("Gamut: {gamut}", gamut=self.tr(item.gamut))
            else:
                gamut_text = self.tr("Gamut: N/A (CPU tonemap)")
            self.active_variant_title.setText(f"{title_name}  ·  {gamut_text}")

            file_stem = Path(self.src_path).name if self.src_path else ""
            time_str = clock_time(self.seek)
            self.active_variant_meta.setText(
                self.tr("Source: {file} · Frame position: {time}", file=file_stem, time=time_str)
            )
            self._update_position_label()

            self._update_preview_display(item)
            self._sync_all_render_btn_visibility()
            self._test_clip_out = None
            if hasattr(self, "test_clip_open_btn"):
                self.test_clip_open_btn.setEnabled(False)
                self.test_clip_open_btn.hide()
                set_role(self.test_clip_open_btn, "")
            if hasattr(self, "test_clip_status_lbl") and self._test_clip_proc is None:
                self._update_test_clip_idle_status()

    def _sync_all_render_btn_visibility(self) -> None:
        if not hasattr(self, "list_widget"):
            return
        for row in range(self.list_widget.count()):
            l_item = self.list_widget.item(row)
            w = self.list_widget.itemWidget(l_item)
            if isinstance(w, VariantListItemWidget):
                w._sync_render_btn_visibility()

    def _set_duration_preset(self, dur: int) -> None:
        if hasattr(self, "btn_dur_custom"):
            self.btn_dur_custom.setChecked(False)
        if hasattr(self, "test_clip_dur_spin"):
            self.test_clip_dur_spin.blockSignals(True)
            self.test_clip_dur_spin.setValue(dur)
            self.test_clip_dur_spin.blockSignals(False)
        self._sync_duration_presets(dur)
        self._update_test_clip_idle_status()

    def _toggle_custom_duration(self) -> None:
        if hasattr(self, "btn_dur_3s"):
            self.btn_dur_3s.setChecked(False)
        if hasattr(self, "btn_dur_5s"):
            self.btn_dur_5s.setChecked(False)
        if hasattr(self, "btn_dur_10s"):
            self.btn_dur_10s.setChecked(False)
        if hasattr(self, "btn_dur_custom"):
            self.btn_dur_custom.setChecked(True)
        if hasattr(self, "test_clip_dur_spin"):
            self.test_clip_dur_spin.show()
            custom_val = getattr(self, "_custom_duration", 5)
            self.test_clip_dur_spin.blockSignals(True)
            self.test_clip_dur_spin.setValue(custom_val)
            self.test_clip_dur_spin.blockSignals(False)
        self._update_test_clip_idle_status()

    def _on_spin_value_changed(self, val: int) -> None:
        self._custom_duration = val
        if hasattr(self, "btn_dur_custom"):
            self.btn_dur_custom.setChecked(val not in (5, 10))
        self._sync_duration_presets(val)
        self._update_test_clip_idle_status()

    def _sync_duration_presets(self, val: int) -> None:
        if hasattr(self, "btn_dur_3s"):
            self.btn_dur_3s.setChecked(val == 3)
        if hasattr(self, "btn_dur_5s"):
            self.btn_dur_5s.setChecked(val == 5)
        if hasattr(self, "btn_dur_10s"):
            self.btn_dur_10s.setChecked(val == 10)

    def _update_test_clip_idle_status(self) -> None:
        if not hasattr(self, "test_clip_status_lbl") or self._test_clip_proc is not None:
            return
        if self._test_clip_out is not None and self._test_clip_out.is_file():
            return
        dur = self.test_clip_dur_spin.value() if hasattr(self, "test_clip_dur_spin") else 5
        self.test_clip_status_lbl.setStyleSheet(
            f"color: {self.tokens.get('text_secondary', '#A1A1AA')}; font-size: 11px;"
        )
        self.test_clip_status_lbl.setText(
            self.tr("Ready to render test clip ({seconds} s)", seconds=dur)
        )

    def _init_compare_frame_strip(self) -> None:
        """Seed FrameStrip from MainWindow's loaded thumbnails immediately if present,
        and start background FrameStripLoader if ffmpeg + valid source are available."""
        if not hasattr(self, "frame_strip"):
            return
        self.frame_strip.set_duration(self.duration)
        self._sync_compare_frame_strip_position()
        parent_fs = getattr(self.parent_window, "frame_strip", None)
        if parent_fs is not None and parent_fs.has_frames():
            self.frame_strip._count = parent_fs._count
            self.frame_strip._frames = dict(parent_fs._frames)
            self.frame_strip.show()
            if hasattr(self, "scrub_slider_row"):
                self.scrub_slider_row.hide()
            self.frame_strip.update()
            return
        f = exe("ffmpeg")
        if f and self.src_path and Path(self.src_path).is_file() and self.duration > 0:
            count = self.frame_strip.tile_count_for_width(680)
            self.frame_strip.reset(count)
            kind = getattr(self.parent_window, "kind", "hdr")
            self.frame_strip_loader.start(f, self.src_path, self.duration, count, kind in ("hdr", "dolby"))

    def _on_compare_frame_strip_frame(self, idx: int, image) -> None:
        self.frame_strip.set_frame(idx, image)
        if self.preview_slider.isEnabled():
            if not self.frame_strip.isVisible():
                self.frame_strip.show()
            if hasattr(self, "scrub_slider_row") and self.scrub_slider_row.isVisible():
                self.scrub_slider_row.hide()

    def _on_compare_strip_seek(self, frac: float) -> None:
        sl = self.preview_slider
        if sl.isEnabled() and sl.maximum() > sl.minimum():
            sl.setValue(int(round(sl.minimum() + frac * (sl.maximum() - sl.minimum()))))

    def _sync_compare_frame_strip_position(self) -> None:
        if not hasattr(self, "frame_strip") or not hasattr(self, "preview_slider"):
            return
        sl = self.preview_slider
        span = sl.maximum() - sl.minimum()
        frac = (self.seek - sl.minimum()) / span if span > 0 else 0.0
        self.frame_strip.set_position(frac)
        cur_s = int(round(self.seek))
        tot_s = int(round(self.duration)) if self.duration > 0 else 0
        txt = f"{clock_time(cur_s)} / {clock_time(tot_s)}" if tot_s > 0 else clock_time(cur_s)
        self.frame_strip.set_time_info(cur_s, tot_s, txt)

    def _update_position_label(self) -> None:
        if not hasattr(self, "preview_position_label"):
            return
        cur_str = clock_time(int(round(self.seek)))
        if self.duration > 0:
            tot_str = clock_time(int(round(self.duration)))
            self.preview_position_label.setText(f"{cur_str} / {tot_str}")
        else:
            self.preview_position_label.setText(cur_str)
        is_enabled = hasattr(self, "preview_slider") and self.preview_slider.isEnabled()
        self.preview_position_label.setEnabled(is_enabled)
        if hasattr(self, "frame_section_lbl"):
            self.frame_section_lbl.setEnabled(is_enabled)
        self._sync_compare_frame_strip_position()

    def _on_seek_slider_changed(self, value: int) -> None:
        self.seek = float(value)
        self._update_position_label()
        file_stem = Path(self.src_path).name if self.src_path else ""
        time_str = clock_time(self.seek)
        if hasattr(self, "active_variant_meta"):
            self.active_variant_meta.setText(
                self.tr("Source: {file} · Frame position: {time}", file=file_stem, time=time_str)
            )
        self._seek_debounce_timer.start(300)

    def _sync_render_frames_btn(self, running: bool) -> None:
        if not hasattr(self, "render_frames_btn"):
            return
        if running:
            self.render_frames_btn.setText(self.tr("Cancel"))
            set_role(self.render_frames_btn, "stop-ready")
        else:
            self.render_frames_btn.setText(self.tr("Render test frames"))
            set_role(self.render_frames_btn, "go")

    def _on_auto_render_toggled(self, checked: bool) -> None:
        self._auto_render = bool(checked)
        settings = getattr(getattr(self.parent_window, "i18n", None), "settings", None)
        if settings is not None:
            settings.setValue("compare_auto_render", "1" if self._auto_render else "0")
        if not self._auto_render and self.comparator.active_proc is not None and self.comparator._single_target_index is None:
            self._on_cancel_clicked()
        elif self._auto_render and any(v.status in ("pending", "cancelled") for v in self.variants):
            self._render_all_frames()

    def _on_render_frames_clicked(self) -> None:
        if self.comparator.active_proc is not None:
            self._on_cancel_clicked()
            return
        self._render_all_frames()

    def _render_all_frames(self) -> None:
        self._seek_debounce_timer.stop()
        self.comparator.seek = self.seek
        for item in self.variants:
            if item.status == "cancelled":
                item.status = "pending"
        self.progress_bar.setValue(sum(1 for v in self.variants if v.status == "ready"))
        self.status_lbl.setText(
            self.tr("Rendering {current} of {total}…", current=1, total=len(self.variants))
        )
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        self._sync_render_frames_btn(running=True)
        self.comparator.start()
        self._expected_comparator_gen = getattr(self.comparator, "_gen", None)
        self._populate_list()
        if 0 <= self._selected_index < len(self.variants):
            self._update_preview_display(self.variants[self._selected_index])

    def render_single_variant(self, index: int) -> None:
        if not (0 <= index < len(self.variants)):
            return
        self._seek_debounce_timer.stop()
        self._select_variant(index)
        for row in range(self.list_widget.count()):
            l_item = self.list_widget.item(row)
            if l_item.data(Qt.ItemDataRole.UserRole) == index:
                self.list_widget.setCurrentRow(row)
                break
        self.comparator.seek = self.seek
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        self._sync_render_frames_btn(running=True)
        self.comparator.render_single(index)
        self._expected_comparator_gen = getattr(self.comparator, "_gen", None)
        self._populate_list()
        self._update_preview_display(self.variants[index])

    def _on_seek_debounce_timeout(self) -> None:
        self._seek_debounce_timer.stop()
        if self._test_clip_proc is not None:
            self._cancel_test_clip()
        self._expected_comparator_gen = None
        self.comparator.cancel()
        self.comparator.seek = self.seek
        self._last_ready_pixmap = None
        for item in self.variants:
            item.status = "pending"
            item.pixmap = None
            item.file_path = None
        if hasattr(self, "save_all_btn"):
            self.save_all_btn.setEnabled(False)
        if getattr(self, "_auto_render", False):
            self._render_all_frames()
        else:
            self.comparator.prepare_cache()
            self._populate_list()
            ready_count = sum(1 for v in self.variants if v.status == "ready")
            self.progress_bar.setValue(ready_count)
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.hide()
            self._sync_render_frames_btn(running=False)
            self.status_lbl.setText(
                self.tr("{ready} of {total} variants ready (cancelled)", ready=ready_count, total=len(self.variants))
            )
            if 0 <= self._selected_index < len(self.variants):
                self._update_preview_display(self.variants[self._selected_index])

    def _update_overlay_geometry(self) -> None:
        if hasattr(self, "image_tools_overlay") and hasattr(self, "image_container"):
            tb = self.image_tools_overlay
            tb.adjustSize()
            ic_w = self.image_container.width()
            if ic_w > tb.width() + 16:
                tb.move(ic_w - tb.width() - 10, 10)
                tb.raise_()
        if not hasattr(self, "render_overlay_badge") or not hasattr(self, "image_lbl"):
            return
        badge = self.render_overlay_badge
        lbl = self.image_lbl
        if not badge.isVisible() or lbl.width() <= 10 or lbl.height() <= 10:
            return
        bw = min(420, max(280, lbl.width() - 40))
        bh = 62
        bx = max(0, (lbl.width() - bw) // 2)
        by = max(0, (lbl.height() - bh) // 2)
        badge.setGeometry(bx, by, bw, bh)
        badge.raise_()

    def _update_preview_display(self, item: TonemapCompareItem) -> None:
        has_pixmap = item.status == "ready" and item.pixmap is not None
        if hasattr(self, "save_btn"):
            self.save_btn.setEnabled(has_pixmap)
        if hasattr(self, "save_all_btn"):
            any_ready = any(v.status == "ready" and v.pixmap is not None and not v.pixmap.isNull() for v in self.variants)
            self.save_all_btn.setEnabled(any_ready)
        if hasattr(self, "fullscreen_btn"):
            self.fullscreen_btn.setEnabled(has_pixmap)

        lbl_w = max(200, self.image_lbl.width() if self.image_lbl.width() > 10 else (self.image_container.width() - 8))
        lbl_h = max(150, self.image_lbl.height() if self.image_lbl.height() > 10 else (self.image_container.height() - 48))

        if has_pixmap:
            if hasattr(self, "render_overlay_badge"):
                self.render_overlay_badge.hide()
            self._last_ready_pixmap = item.pixmap
            scaled = item.pixmap.scaled(
                lbl_w,
                lbl_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_lbl.setPixmap(scaled)
            self.image_lbl.setText("")
        elif item.status in ("rendering", "pending"):
            temp_pixmap = self._last_ready_pixmap or self._placeholder_pixmap
            if temp_pixmap is None or temp_pixmap.isNull():
                for v in self.variants:
                    if v.status == "ready" and v.pixmap is not None and not v.pixmap.isNull():
                        temp_pixmap = v.pixmap
                        self._last_ready_pixmap = v.pixmap
                        break
            if temp_pixmap is not None and not temp_pixmap.isNull():
                scaled = temp_pixmap.scaled(
                    lbl_w,
                    lbl_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.image_lbl.setPixmap(scaled)
                self.image_lbl.setText("")
            else:
                self.image_lbl.setPixmap(QPixmap())
                self.image_lbl.setText("")

            if hasattr(self, "render_overlay_badge"):
                variant_num = self._selected_index + 1
                self.render_overlay_badge.title_lbl.setText(
                    f"{self.tr('Rendering…')} (#{variant_num})"
                )
                self.render_overlay_badge.subtitle_lbl.setText(
                    self.tr("Temporary preview — final result will appear when ready")
                )
                self.render_overlay_badge.show()
                self._update_overlay_geometry()
        elif item.status == "error":
            if hasattr(self, "render_overlay_badge"):
                self.render_overlay_badge.hide()
            self.image_lbl.setPixmap(QPixmap())
            err_msg = self._format_item_error(item)
            self.image_lbl.setStyleSheet("QLabel { background: transparent; border: none; color: #F87171; font-size: 12px; }")
            self.image_lbl.setText(f"⚠ {err_msg}")
        elif item.status == "cancelled":
            if hasattr(self, "render_overlay_badge"):
                self.render_overlay_badge.hide()
            self.image_lbl.setPixmap(QPixmap())
            self.image_lbl.setStyleSheet("QLabel { background: transparent; border: none; color: #A1A1AA; font-size: 12px; }")
            self.image_lbl.setText(f"⏹ {self.tr('Cancelled')}")
        else:
            if hasattr(self, "render_overlay_badge"):
                self.render_overlay_badge.hide()
            self.image_lbl.setPixmap(QPixmap())
            self.image_lbl.setStyleSheet("QLabel { background: transparent; border: none; color: #A1A1AA; font-size: 12px; }")
            self.image_lbl.setText(f"○ {self.tr('Waiting in queue…')}")

    def _format_item_error(self, item: TonemapCompareItem) -> str:
        if item.error_code == "failed_to_start":
            base = self.tr("Failed to start FFmpeg")
        elif item.error_code == "corrupt_image":
            base = self.tr("Corrupt or unreadable preview frame")
        elif item.error_code == "process_failed":
            base = self.tr("FFmpeg failed with exit code {code}", code=item.exit_code)
        else:
            base = item.error_message or self.tr("Rendering failed for this variant")

        if item.error_detail:
            return f"{base}: {item.error_detail}"
        return base

    def _refresh_list_item_status(self, index: int) -> None:
        for row in range(self.list_widget.count()):
            l_item = self.list_widget.item(row)
            if l_item.data(Qt.ItemDataRole.UserRole) == index:
                w = self.list_widget.itemWidget(l_item)
                if isinstance(w, VariantListItemWidget):
                    w.update_status()
                break

    def _on_variant_started(self, index: int, item: TonemapCompareItem) -> None:
        if self._expected_comparator_gen is None:
            return
        comp_gen = getattr(self.comparator, "_gen", None)
        if comp_gen is not None and comp_gen != self._expected_comparator_gen:
            return
        self._refresh_list_item_status(index)
        self.status_lbl.setText(
            self.tr("Rendering {current} of {total}…", current=index + 1, total=len(self.variants))
        )
        if index == self._selected_index:
            self._update_preview_display(item)

    def _on_variant_finished(self, index: int, item: TonemapCompareItem, success: bool) -> None:
        if self._expected_comparator_gen is None:
            return
        comp_gen = getattr(self.comparator, "_gen", None)
        if comp_gen is not None and comp_gen != self._expected_comparator_gen:
            return
        self._refresh_list_item_status(index)
        if success and item.pixmap is not None and not item.pixmap.isNull():
            self._last_ready_pixmap = item.pixmap
        ready_count = sum(1 for v in self.variants if v.status == "ready")
        self.progress_bar.setValue(ready_count)
        if hasattr(self, "save_all_btn"):
            self.save_all_btn.setEnabled(ready_count > 0)
        fs_dlg = getattr(self, "_active_fullscreen_dlg", None)
        if fs_dlg is not None:
            fs_dlg.refresh_navigation()
        if index == self._selected_index:
            self._update_preview_display(item)

    def _on_all_finished(self) -> None:
        if self._expected_comparator_gen is None:
            return
        comp_gen = getattr(self.comparator, "_gen", None)
        if comp_gen is not None and comp_gen != self._expected_comparator_gen:
            if comp_gen != self._expected_comparator_gen + 1:
                return
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.hide()
        self._sync_render_frames_btn(running=False)
        ready_count = sum(1 for v in self.variants if v.status == "ready")
        error_count = sum(1 for v in self.variants if v.status == "error")
        total_count = len(self.variants)

        if any(v.status == "cancelled" for v in self.variants) or getattr(self.comparator, "_cancelled", False):
            self.progress_bar.setValue(ready_count)
            self.status_lbl.setText(
                self.tr(
                    "{ready} of {total} variants ready (cancelled)",
                    ready=ready_count,
                    total=total_count,
                )
            )
        elif error_count > 0:
            self.progress_bar.setValue(ready_count)
            self.status_lbl.setText(
                self.tr(
                    "Ready: {ready} of {total} · Errors: {errors}",
                    ready=ready_count,
                    total=total_count,
                    errors=error_count,
                )
            )
        else:
            self.progress_bar.setValue(total_count)
            self.status_lbl.setText(self.tr("All variants ready"))

    def _on_cancel_clicked(self) -> None:
        self._seek_debounce_timer.stop()
        ready_count = sum(1 for v in self.variants if v.status == "ready")
        self._expected_comparator_gen = None
        self.comparator.cancel()
        self.progress_bar.setValue(ready_count)
        self.status_lbl.setText(
            self.tr(
                "{ready} of {total} variants ready (cancelled)",
                ready=ready_count,
                total=len(self.variants),
            )
        )
        self._populate_list()
        if 0 <= self._selected_index < len(self.variants):
            self._update_preview_display(self.variants[self._selected_index])
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.hide()
        self._sync_render_frames_btn(running=False)

    def _apply_active_variant(self) -> None:
        if 0 <= self._selected_index < len(self.variants):
            item = self.variants[self._selected_index]
            self.parent_window.method_combo.setCurrentText(item.method)
            if item.engine == "gpu" and item.gamut != "N/A":
                self.parent_window.gamut_combo.setCurrentText(item.gamut)
            if hasattr(self.parent_window, "preview_slider") and self.parent_window.preview_slider:
                if self.parent_window.preview_slider.maximum() < int(self.seek):
                    self.parent_window.preview_slider.setRange(0, max(int(self.duration), int(self.seek)))
                self.parent_window.preview_slider.setValue(int(self.seek))
            elif hasattr(self.parent_window, "_schedule_frame_preview"):
                self.parent_window._schedule_frame_preview()
            self.accept()

    @staticmethod
    def _sanitize_filename_part(text: str) -> str:
        bad = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in bad else ch for ch in text).strip()
        return cleaned or "variant"

    def _format_variant_filename(self, index: int, item: TonemapCompareItem, ext: str = ".png") -> str:
        default_stem = self._sanitize_filename_part(Path(self.src_path).stem if self.src_path else "compare")
        method_part = item.method.split("·")[0].strip() if "·" in item.method else item.method.strip()
        if item.engine == "gpu" and item.gamut != "N/A":
            mode_label = f"{method_part} [GPU - Gamut {item.gamut}]"
        else:
            mode_label = f"{method_part} [CPU]"
        safe_mode = self._sanitize_filename_part(mode_label)
        return f"{default_stem} - {index + 1:02d} - {safe_mode}{ext}"

    def _default_save_dir(self) -> str:
        initdir = str(Path.home())
        if hasattr(self.parent_window, "output_folder") and self.parent_window.output_folder:
            initdir = str(self.parent_window.output_folder)
        elif (
            has_dest := getattr(self.parent_window, "dst_edit", None)
        ) and has_dest.text() and Path(has_dest.text()).parent.is_dir():
            initdir = str(Path(has_dest.text()).parent)
        return initdir

    def _save_active_frame(self) -> None:
        if 0 <= self._selected_index < len(self.variants):
            item = self.variants[self._selected_index]
            if item.pixmap is None or item.pixmap.isNull():
                return
            default_name = self._format_variant_filename(self._selected_index, item, ".png")
            initdir = self._default_save_dir()

            save_path, _ = QFileDialog.getSaveFileName(
                self,
                self.tr("Save comparison frame"),
                str(Path(initdir) / default_name),
                "PNG image (*.png);;JPEG image (*.jpg)",
            )
            if save_path:
                if not item.pixmap.save(save_path):
                    QMessageBox.critical(
                        self,
                        self.tr("Save comparison frame"),
                        self.tr("Couldn't save the image to that location."),
                    )

    def _save_all_frames(self, target_dir: str | Path | bool | None = None) -> int:
        if isinstance(target_dir, bool) or not isinstance(target_dir, (str, Path)):
            target_dir = None

        has_any_ready = any(
            v.status == "ready" and v.pixmap is not None and not v.pixmap.isNull()
            for v in self.variants
        )
        if not has_any_ready:
            return 0

        if target_dir is None:
            initdir = self._default_save_dir()
            chosen = QFileDialog.getExistingDirectory(
                self,
                self.tr("Choose SDR output folder"),
                initdir,
            )
            if not chosen:
                return 0
            out_dir = Path(chosen)
        else:
            out_dir = Path(target_dir)

        # Re-collect ready variants after folder dialog closes so only actually
        # rendered frames (status == "ready" with valid pixmap or cached file)
        # are saved, skipping any pending/rendering/error/cancelled frames.
        ready_items = [
            (idx, v)
            for idx, v in enumerate(self.variants)
            if v.status == "ready"
            and (
                (v.pixmap is not None and not getattr(v.pixmap, "isNull", lambda: False)())
                or (v.file_path is not None and Path(v.file_path).is_file())
            )
        ]
        if not ready_items:
            return 0

        out_dir.mkdir(parents=True, exist_ok=True)
        saved_count = 0
        import shutil as _shutil
        for idx, item in ready_items:
            if item.status != "ready":
                continue
            fname = self._format_variant_filename(idx, item, ".png")
            dest_file = out_dir / fname
            saved_ok = False
            if item.file_path is not None and Path(item.file_path).is_file() and Path(item.file_path).stat().st_size > 0:
                try:
                    _shutil.copy2(item.file_path, dest_file)
                    saved_ok = dest_file.is_file() and dest_file.stat().st_size > 0
                except OSError:
                    saved_ok = False
            if not saved_ok and item.pixmap is not None and not getattr(item.pixmap, "isNull", lambda: False)():
                saved_ok = bool(item.pixmap.save(str(dest_file)))
            if saved_ok:
                saved_count += 1

        if saved_count == 0 and target_dir is None:
            QMessageBox.critical(
                self,
                self.tr("Save comparison frame"),
                self.tr("Couldn't save the image to that location."),
            )
        return saved_count

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is getattr(self, "image_lbl", None):
            if event.type() == QEvent.Type.Resize:
                self._update_overlay_geometry()
            elif event.type() == QEvent.Type.MouseButtonDblClick:
                if hasattr(self, "fullscreen_btn") and self.fullscreen_btn.isEnabled():
                    self._show_fullscreen()
                    return True
        return super().eventFilter(watched, event)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._update_overlay_geometry()

    def _on_fullscreen_variant_changed(self, index: int) -> None:
        self._select_variant(index)
        for row in range(self.list_widget.count()):
            l_item = self.list_widget.item(row)
            if l_item.data(Qt.ItemDataRole.UserRole) == index:
                self.list_widget.setCurrentRow(row)
                break

    def _show_fullscreen(self) -> None:
        if 0 <= self._selected_index < len(self.variants):
            item = self.variants[self._selected_index]
            if item.pixmap is None or item.pixmap.isNull():
                return
            title = self.active_variant_title.text() or self.tr("Full size preview")
            dlg = FullscreenFrameDialog(
                pixmap=item.pixmap,
                title=title,
                tokens=self.tokens,
                parent=self,
                variants=self.variants,
                current_index=self._selected_index,
                filter_mode=self._active_filter,
                on_index_changed=self._on_fullscreen_variant_changed,
            )
            self._active_fullscreen_dlg = dlg
            try:
                dlg.exec()
            finally:
                self._active_fullscreen_dlg = None

    def _run_test_clip(self) -> None:
        if self._test_clip_proc is not None:
            self._cancel_test_clip()
            return

        if not self.src_path or not Path(self.src_path).is_file():
            QMessageBox.information(
                self, self.tr("Render test clip"), self.tr("Choose a source video to compare methods.")
            )
            return
        if not exe("ffmpeg"):
            QMessageBox.warning(
                self,
                self.tr("Render test clip"),
                self.tr("Install FFmpeg to see an instant preview of the current settings here."),
            )
            return

        if not (0 <= self._selected_index < len(self.variants)):
            return

        item = self.variants[self._selected_index]
        dur = self.test_clip_dur_spin.value()
        seek = self.seek

        ext = Path(self.src_path).suffix.lower() if self.src_path else ".mkv"
        if not ext:
            ext = ".mkv"

        generation, out_dir = self.clip_manager.begin()
        out = out_dir / f"test_clip{ext}"

        base_s = self.parent_window._settings_with_tracks(out)
        s = replace(base_s, tonemap_method=item.method)
        if item.engine == "gpu" and item.gamut != "N/A":
            s = replace(s, gamut_mode=item.gamut)

        cmd, warnings = build_test_clip_command(s, out, seek, dur)
        if not cmd:
            self.clip_manager.cleanup(out_dir)
            return

        using_hb = s.using_handbrake
        self._test_clip_assembler = LineAssembler()
        self._test_clip_parser = make_progress_parser(using_hb)
        self._test_clip_parser.configure(dur)

        proc = QProcess(self)
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        proc.started.connect(lambda p=proc, gen=generation: self._on_test_clip_started(p, gen))
        proc.errorOccurred.connect(
            lambda err, p=proc, gen=generation, o=out: self._on_test_clip_error(err, p, gen, o)
        )
        proc.readyReadStandardOutput.connect(lambda p=proc: self._on_test_clip_output(p))
        proc.readyReadStandardError.connect(lambda p=proc: self._on_test_clip_output(p))
        proc.finished.connect(lambda *_args, out=out, gen=generation, p=proc: self._on_test_clip_finished(out, gen, p))

        self._test_clip_proc = proc
        self._test_clip_out = None
        self._last_test_clip_error = ""
        self.test_clip_open_btn.setEnabled(False)
        self.test_clip_open_btn.hide()
        set_role(self.test_clip_open_btn, "")
        self.test_clip_btn.setText(self.tr("Cancel clip"))
        set_role(self.test_clip_btn, "stop-ready")
        self.test_clip_status_lbl.setStyleSheet(f"color: {self.tokens.get('text_secondary', '#A1A1AA')}; font-size: 11px;")
        self.test_clip_status_lbl.setText(
            self.tr("Rendering test clip: {pct}%", pct=0)
        )
        self.test_clip_status_lbl.show()
        proc.start()

    def _on_test_clip_started(self, proc: QProcess, generation: int) -> None:
        if proc is self._test_clip_proc and self.clip_manager.is_current(generation):
            try:
                pid = proc.processId()
                if pid:
                    jobobject.bind(pid)
            except Exception:
                pass

    def _on_test_clip_error(
        self, error: QProcess.ProcessError, proc: QProcess, generation: int, out: Path
    ) -> None:
        if not self.clip_manager.is_current(generation):
            self.clip_manager.cleanup(out.parent)
            try:
                proc.deleteLater()
            except Exception:
                pass
            return

        if error == QProcess.ProcessError.FailedToStart:
            if self._test_clip_proc is proc:
                self._test_clip_proc = None
            self.clip_manager.cleanup(out.parent)
            try:
                proc.deleteLater()
            except Exception:
                pass
            self.test_clip_btn.setText(self.tr("Render test clip"))
            set_role(self.test_clip_btn, "go")
            self.test_clip_open_btn.setEnabled(False)
            self.test_clip_open_btn.hide()
            set_role(self.test_clip_open_btn, "")
            self.test_clip_status_lbl.setStyleSheet(
                f"color: {self.tokens.get('danger', '#F87171')}; font-size: 11px;"
            )
            self.test_clip_status_lbl.setText(self.tr("Failed to start FFmpeg"))
            self.test_clip_status_lbl.show()

    def _cancel_test_clip(self) -> None:
        if self._test_clip_proc is not None:
            proc = self._test_clip_proc
            self._test_clip_proc = None
            self.clip_manager.cancel()
            try:
                if proc.state() != QProcess.ProcessState.NotRunning:
                    proc.kill()
                proc.deleteLater()
            except (RuntimeError, AttributeError):
                pass
        self.test_clip_btn.setText(self.tr("Render test clip"))
        set_role(self.test_clip_btn, "go")
        self.test_clip_open_btn.setEnabled(False)
        self.test_clip_open_btn.hide()
        set_role(self.test_clip_open_btn, "")
        self.test_clip_status_lbl.setStyleSheet(f"color: {self.tokens.get('text_secondary', '#A1A1AA')}; font-size: 11px;")
        self.test_clip_status_lbl.setText(self.tr("Test clip render cancelled."))
        self.test_clip_status_lbl.hide()

    def _on_test_clip_output(self, proc: QProcess) -> None:
        if proc is not self._test_clip_proc:
            return
        data = bytes(proc.readAllStandardOutput()) + bytes(proc.readAllStandardError())
        if not data:
            return
        for line in self._test_clip_assembler.feed(data):
            if any(w in line.lower() for w in ("error", "failed", "invalid", "could not")):
                self._last_test_clip_error = line.strip()
            stats, consumed = self._test_clip_parser.feed(line)
            if stats.pct > 0:
                self.test_clip_status_lbl.setStyleSheet(f"color: {self.tokens.get('text_secondary', '#A1A1AA')}; font-size: 11px;")
                self.test_clip_status_lbl.setText(
                    self.tr("Rendering test clip: {pct}%", pct=int(stats.pct))
                )

    def _on_test_clip_finished(
        self, out: Path, generation: int, proc: QProcess | None = None
    ) -> None:
        if not self.clip_manager.is_current(generation):
            self.clip_manager.cleanup(out.parent)
            return
        if proc is not None and proc.error() == QProcess.ProcessError.FailedToStart:
            return
        active_p = self._test_clip_proc or proc
        self._test_clip_proc = None
        if active_p is not None:
            try:
                active_p.deleteLater()
            except Exception:
                pass
        self.test_clip_btn.setText(self.tr("Render test clip"))
        set_role(self.test_clip_btn, "go")

        if not out.is_file() or out.stat().st_size == 0:
            err_detail = getattr(self, "_last_test_clip_error", "")
            if err_detail:
                msg = self.tr("Test clip render failed: {detail}", detail=err_detail)
            else:
                msg = self.tr("Test clip render failed.")
            self.test_clip_status_lbl.setStyleSheet(f"color: {self.tokens.get('danger', '#F87171')}; font-size: 11px;")
            self.test_clip_status_lbl.setText(msg)
            self.test_clip_status_lbl.show()
            self.test_clip_open_btn.setEnabled(False)
            self.test_clip_open_btn.hide()
            set_role(self.test_clip_open_btn, "")
            self.clip_manager.cleanup(out.parent)
            return

        self._test_clip_out = out
        self.test_clip_open_btn.setEnabled(True)
        self.test_clip_open_btn.show()
        set_role(self.test_clip_open_btn, "icon-btn-ready")
        mb = round(out.stat().st_size / 1024 / 1024, 1)
        self.test_clip_status_lbl.setStyleSheet(f"color: {self.tokens.get('text_secondary', '#A1A1AA')}; font-size: 11px;")
        self.test_clip_status_lbl.setText(self.tr("Test clip ready ({size} MB)", size=mb))
        self.test_clip_status_lbl.hide()

    def _open_test_clip(self) -> None:
        if self._test_clip_out and self._test_clip_out.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._test_clip_out)))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._update_overlay_geometry()
        if hasattr(self, "b_lay"):
            avail = self.width()
            if avail < 980:
                self.b_lay.setSpacing(4 if avail < 940 else 6)
            else:
                self.b_lay.setSpacing(8)
        if 0 <= self._selected_index < len(self.variants):
            self._update_preview_display(self.variants[self._selected_index])

    def reject(self) -> None:
        self.comparator.cancel()
        self._cancel_test_clip()
        super().reject()

    def closeEvent(self, event: Any) -> None:
        if hasattr(self, "frame_strip_loader"):
            self.frame_strip_loader.cancel()
        self.comparator.cancel()
        self._cancel_test_clip()
        super().closeEvent(event)
