from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

from PySide6.QtCore import (
    Qt, QTimer, QSize, QRect, QRectF, QPoint, QUrl,
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QLinearGradient, QPixmap, QDesktopServices, QFont, QFontMetrics,
)
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QLabel, QWidget, QLineEdit, QComboBox,
    QVBoxLayout, QHBoxLayout, QMessageBox, QDialog, QMenu, QToolButton,
    QInputDialog, QFrame, QScrollArea, QStackedWidget, QSizePolicy, QFileDialog, QLayout, QPushButton,
)

from hdr2sdr.core.constants import KOFI_URL, LANGUAGE_NAMES
from hdr2sdr.runtime.updates import APP_VERSION
from hdr2sdr.theme.look import (
    BASES, builtin_preset, clean_preset, copy_look, load_presets, load_slots, palette_name,
    save_look, save_presets, save_slot,
)
from hdr2sdr.theme.palettes import (
    NEON_BG_EFFECTS, _THEME_DEFAULT_INTENSITY, _THEME_DEFAULT_STOPS, _neon_active, _theme_is_dark,
)
from hdr2sdr.theme.theme_tokens import (
    build_theme_dialog_qss, build_tokens, is_accent_contrast_low, pick_on_accent,
)
from hdr2sdr.ui.glass import blurred_backdrop
from hdr2sdr.ui.icons import _swatch_icon, _pipette_icon
from hdr2sdr.theme.metrics import (
    MAX_BUTTON_RADIUS, MAX_CARD_RADIUS,
    button_radius, card_radius, set_radii,
)
from hdr2sdr.ui.style_helpers import _attach_press_bounce, set_role
from hdr2sdr.ui.widgets.buttons import GlassButton, TitleBarButton
from hdr2sdr.ui.widgets.combo import RoundedComboBox, RoundedMenuButton
from hdr2sdr.ui.widgets.containers import (
    Card, _BgLayer, paint_frameless_surface, window_radius,
)
from hdr2sdr.ui.widgets.sliders import (
    RoundHandleSlider, _EyedropperOverlay, _GradientPreviewBar, _HueSlider, _SVPicker,
)
from hdr2sdr.ui.widgets.toggles import ToggleSwitch


def _escape_mnemonic(text: str) -> str:
    """Escape ampersands as && so QPushButton does not consume & as a mnemonic."""
    return str(text or "").replace("&&", "&").replace("&", "&&")


def _detect_windows_system_base() -> str:
    """Detect whether the OS prefers light or dark app mode (defaults to 'dark')."""
    try:
        app = QApplication.instance()
        if app is not None and hasattr(app, "styleHints"):
            hints = app.styleHints()
            if hasattr(hints, "colorScheme"):
                scheme = hints.colorScheme()
                if str(scheme).lower().endswith("light"):
                    return "light"
                if str(scheme).lower().endswith("dark"):
                    return "dark"
    except Exception:
        pass
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(val) == 1 else "dark"
    except Exception:
        return "dark"


def _render_preset_preview_pixmap(preset_dict: dict, width: int = 160, height: int = 76) -> QPixmap:
    """Render a realistic miniature window snapshot (backdrop gradient/effect,
    two cards, accent CTA button, and progress bar) for a preset card."""
    pix = QPixmap(max(32, width), max(24, height))
    pix.fill(Qt.GlobalColor.transparent)
    tok = build_tokens(preset_dict)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    rect = QRectF(0.5, 0.5, width - 1.0, height - 1.0)
    path = QPainterPath()
    path.addRoundedRect(rect, 8.0, 8.0)
    p.setClipPath(path)

    p.fillRect(rect, QColor(tok["bg_window"]))
    stops = preset_dict.get("stops")
    intensity = int(preset_dict.get("intensity", 0) or 0)
    direction = int(preset_dict.get("direction", 180) or 180)
    if stops and intensity > 0:
        angle = math.radians(direction)
        diag = math.hypot(width, height) / 2.0
        cx, cy = width / 2.0, height / 2.0
        dx, dy = math.cos(angle) * diag, math.sin(angle) * diag
        grad = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
        alpha = round(255 * (intensity / 100.0))
        n = max(1, len(stops) - 1)
        for i, hex_c in enumerate(stops):
            c = QColor(hex_c)
            c.setAlpha(alpha)
            grad.setColorAt(i / n if n else 0.0, c)
        p.fillRect(rect, grad)

    # Miniature left & right cards
    card_col = QColor(tok["bg_card"])
    if preset_dict.get("ui_style") == "glass":
        card_col.setAlpha(205)
    p.setPen(QPen(QColor(tok["border"]), 1.0))
    p.setBrush(card_col)
    left_card = QRectF(8, 10, width * 0.52, height - 20)
    right_card = QRectF(width * 0.56 + 4, 10, width * 0.44 - 12, height - 20)
    p.drawRoundedRect(left_card, 5.0, 5.0)
    p.drawRoundedRect(right_card, 5.0, 5.0)

    # Miniature lines & accent button inside cards
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tok["text"]))
    p.drawRoundedRect(QRectF(14, 16, left_card.width() * 0.55, 4), 2.0, 2.0)
    p.setBrush(QColor(tok["text_secondary"]))
    p.drawRoundedRect(QRectF(14, 24, left_card.width() * 0.72, 3), 1.5, 1.5)
    p.setBrush(QColor(tok["bg_field"]))
    p.drawRoundedRect(QRectF(right_card.x() + 5, 17, right_card.width() - 10, 8), 3.0, 3.0)
    p.drawRoundedRect(QRectF(right_card.x() + 5, 29, right_card.width() - 10, 8), 3.0, 3.0)

    p.setBrush(QColor(tok["accent"]))
    p.drawRoundedRect(QRectF(14, height - 22, left_card.width() - 12, 8), 3.0, 3.0)

    p.setClipping(False)
    border_col = QColor(tok["accent"] if preset_dict.get("neon_effect") else tok["border"])
    p.setPen(QPen(border_col, 1.2 if preset_dict.get("neon_effect") else 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(rect, 8.0, 8.0)
    p.end()
    return pix



class _FlowLayout(QLayout):
    """Responsive flow layout that wraps child widgets onto new rows within available width
    and updates its parent widget minimumHeight so wrapped rows are never clipped vertically."""

    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 8, v_spacing: int = 8, distribute_rows: bool = False):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._distribute_rows = distribute_rows
        self._items: list = []
        self._cached_h: int = 32

    def addItem(self, item):
        self._items.append(item)

    def horizontalSpacing(self) -> int:
        return self._h_space

    def verticalSpacing(self) -> int:
        return self._v_space

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, max(1, width), 0), test_only=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        h = self._do_layout(rect, test_only=False)
        if h > 0 and h != self._cached_h:
            self._cached_h = h
            pw = self.parentWidget()
            if pw is not None and pw.minimumHeight() != h:
                pw.setMinimumHeight(h)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        max_w = 0
        max_item_h = 0
        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            hint = self._item_effective_hint(item)
            max_w = max(max_w, hint.width())
            max_item_h = max(max_item_h, hint.height())
        m = self.contentsMargins()
        h = max(self._cached_h, max_item_h + m.top() + m.bottom())
        return QSize(max_w + m.left() + m.right(), h)

    @staticmethod
    def _item_effective_hint(item) -> QSize:
        hint = item.sizeHint()
        wid = item.widget()
        if wid is not None:
            if isinstance(wid, QPushButton) and wid.text():
                fm = QFontMetrics(wid.font())
                txt_w = fm.horizontalAdvance(wid.text().replace("&&", "&"))
                extra = 28 if wid.property("lead_icon") else 0
                min_btn_w = txt_w + 24 + extra
                hint = QSize(max(hint.width(), min_btn_w), max(hint.height(), 32))
            elif isinstance(wid, QLabel) and wid.text() and not wid.wordWrap():
                fm = QFontMetrics(wid.font())
                txt_w = fm.horizontalAdvance(wid.text())
                hint = QSize(max(hint.width(), txt_w + 8), max(hint.height(), 24))
        return hint

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        avail_w = max(1, effective.width())

        visible_entries = []
        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            hint = self._item_effective_hint(item)
            visible_entries.append((item, min(hint.width(), avail_w), hint.height()))

        if not visible_entries:
            return m.top() + m.bottom()

        rows: list[list[tuple]] = []
        cur_row: list[tuple] = []
        cur_w = 0
        for entry in visible_entries:
            _item, w, _h = entry
            needed = w if not cur_row else (cur_w + self._h_space + w)
            if cur_row and needed > avail_w:
                rows.append(cur_row)
                cur_row = [entry]
                cur_w = w
            else:
                cur_row.append(entry)
                cur_w = needed
        if cur_row:
            rows.append(cur_row)

        # Balance 2-row button toolbars (e.g. 4+1 -> 3+2) when distribute_rows is enabled
        if self._distribute_rows and len(rows) == 2 and len(rows[0]) - len(rows[1]) >= 2:
            split_idx = (len(visible_entries) + 1) // 2
            cand_r0 = visible_entries[:split_idx]
            cand_r1 = visible_entries[split_idx:]
            w0 = sum(e[1] for e in cand_r0) + self._h_space * max(0, len(cand_r0) - 1)
            w1 = sum(e[1] for e in cand_r1) + self._h_space * max(0, len(cand_r1) - 1)
            if w0 <= avail_w and w1 <= avail_w:
                rows = [cand_r0, cand_r1]

        y = effective.y()
        for r_idx, row in enumerate(rows):
            row_h = max((e[2] for e in row), default=0)
            total_base_w = sum(e[1] for e in row) + self._h_space * max(0, len(row) - 1)
            spare = max(0, avail_w - total_base_w) if self._distribute_rows else 0
            x = effective.x()
            for i, (item, base_w, h) in enumerate(row):
                if self._distribute_rows and len(row) > 0:
                    add_w = (spare // len(row)) + (1 if i < (spare % len(row)) else 0)
                else:
                    add_w = 0
                final_w = min(avail_w - (x - effective.x()), base_w + add_w)
                if not test_only:
                    vy = y + max(0, (row_h - h) // 2)
                    item.setGeometry(QRect(QPoint(x, vy), QSize(final_w, h)))
                x += final_w + self._h_space
            y += row_h
            if r_idx + 1 < len(rows):
                y += self._v_space

        return y - rect.y() + m.bottom()


class _PresetCard(QFrame):
    """Gallery card for a built-in or user preset on the Appearance -> Themes tab."""

    def __init__(
        self,
        dialog: ThemeCustomizeDialog,
        data_key: str,
        title: str,
        preset_dict: dict,
        *,
        is_builtin: bool,
        is_selected: bool,
        is_modified: bool,
        card_idx: int,
    ):
        super().__init__(dialog)
        self.dialog = dialog
        self.data_key = data_key
        self.preset_title = title
        self.is_builtin = is_builtin
        self.is_selected = is_selected
        self.is_modified = is_modified
        obj_name = f"presetGalleryCard_{card_idx}"
        self.setObjectName(obj_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(114)

        t = dialog.tokens
        r = max(6, min(14, int(t.get("radius_card", 12)) - 2))
        border_c = t["accent"] if is_selected else t["border"]
        border_w = "2px" if is_selected else "1px"
        bg_c = t["bg_card_alt"] if is_selected else t["bg_card"]
        self.setStyleSheet(
            f"QFrame#{obj_name} {{ background: {bg_c}; border: {border_w} solid {border_c}; border-radius: {r}px; }}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        self.thumb_label = QLabel()
        self.thumb_label.setObjectName(f"{obj_name}_thumb")
        self.thumb_label.setStyleSheet(f"QLabel#{obj_name}_thumb {{ background: transparent; border: none; }}")
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setFixedHeight(64)
        self.thumb_label.setPixmap(_render_preset_preview_pixmap(preset_dict, 156, 62))
        self.thumb_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.thumb_label)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(2, 0, 2, 0)
        bottom.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setObjectName(f"{obj_name}_title")
        self.title_label.setStyleSheet(
            f"QLabel#{obj_name}_title {{ color: {t['text']}; font-weight: 600; font-size: 9pt; background: transparent; border: none; }}"
        )
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        f_title = QFont(self.title_label.font())
        f_title.setPointSizeF(9.0)
        f_title.setWeight(QFont.Weight.DemiBold)
        title_w = max(QFontMetrics(f_title).horizontalAdvance(title), QFontMetrics(self.title_label.font()).horizontalAdvance(title))
        self.title_label.setMinimumWidth(title_w + 6)
        bottom.addWidget(self.title_label, 1)

        if is_selected and is_modified:
            mod_fg = pick_on_accent(t["accent"])
            self.badge_label = QLabel(dialog.win.tr("Modified"))
            self.badge_label.setObjectName(f"{obj_name}_modBadge")
            self.badge_label.setStyleSheet(
                f"QLabel#{obj_name}_modBadge {{ background: {t['accent']}; color: {mod_fg}; "
                f"border-radius: 4px; padding: 1px 5px; font-size: 7.5pt; font-weight: 600; }}"
            )
            self.badge_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        else:
            kind_txt = dialog.win.tr("Built-in") if is_builtin else dialog.win.tr("Custom")
            self.badge_label = QLabel(kind_txt)
            self.badge_label.setObjectName(f"{obj_name}_kindBadge")
            self.badge_label.setStyleSheet(
                f"QLabel#{obj_name}_kindBadge {{ color: {t['text_secondary']}; font-size: 7.5pt; background: transparent; border: none; }}"
            )
            self.badge_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        badge_w = QFontMetrics(self.badge_label.font()).horizontalAdvance(self.badge_label.text()) + 12
        self.badge_label.setMinimumWidth(badge_w)
        bottom.addWidget(self.badge_label)

        self.menu_btn = GlassButton("")
        set_role(self.menu_btn, "panel-toggle")
        self.menu_btn.setProperty("icon_kind", "dots")
        self.menu_btn.setIconSize(QSize(16, 16))
        self.menu_btn.setFixedSize(24, 22)
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_btn.setToolTip(
            dialog.win.tr("Duplicate") if is_builtin else dialog.win.tr("Rename / Duplicate / Delete")
        )
        self.menu_btn.clicked.connect(self._open_menu)
        bottom.addWidget(self.menu_btn)

        self.preferred_min_width = min(180, max(160, title_w + badge_w + 24 + 24))
        self.setMinimumWidth(self.preferred_min_width)

        lay.addLayout(bottom)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _pos: self._open_menu())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dialog._select_preset_by_data(self.data_key)
            event.accept()
            return
        super().mousePressEvent(event)

    def _open_menu(self):
        menu = QMenu(self)
        tr = self.dialog.win.tr
        rename_act = None
        del_act = None
        if self.is_builtin:
            dup_act = menu.addAction(tr("Duplicate"))
        else:
            rename_act = menu.addAction(tr("Rename"))
            dup_act = menu.addAction(tr("Duplicate"))
            del_act = menu.addAction(tr("Delete"))
        chosen = menu.exec(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft()))
        if chosen is None:
            return
        if chosen == dup_act:
            self.dialog._duplicate_preset(self.data_key)
        elif rename_act is not None and chosen == rename_act:
            idx = int(self.data_key.split(":", 1)[1])
            self.dialog._rename_user_preset(idx)
        elif del_act is not None and chosen == del_act:
            idx = int(self.data_key.split(":", 1)[1])
            self.dialog._delete_user_preset(idx)


class ThemeCustomizeDialog(QDialog):
    """Settings & Appearance dialog (landscape layout with left navigation
    General | Appearance | About, Appearance sub-tabs Themes | Colors | Glass & Shape,
    debounced live window preview with Hold to compare, and draft confirmation
    Save / Discard / Cancel on close)."""

    DEFAULT_WIDTH = 820
    DEFAULT_HEIGHT = 660
    MIN_WIDTH = 760
    MIN_HEIGHT = 600

    _BUILTIN_NAMES = (("light", "Light"), ("dark", "Dark"), ("neon", "Neon"))

    def __init__(self, win, target_theme=None):
        super().__init__(win)
        self.win = win
        look = copy_look(win.look)
        look["ui_style"] = "classic"
        self._orig_look = copy_look(look)
        self._orig_radii = (card_radius(), button_radius())
        self._orig_tokens = copy_look(win.tokens) if isinstance(getattr(win, "tokens", None), dict) else build_tokens(look)

        self.tokens = build_tokens(look)
        self.theme_name = palette_name(look)
        self.ui_style = look["ui_style"]
        self.neon_effect = look["neon_effect"]
        self._blurred_bg = None
        self._blur_timer = QTimer(self)
        self._blur_timer.setSingleShot(True)
        self._blur_timer.setInterval(30)
        self._blur_timer.timeout.connect(self.refresh_blur)

        self.setWindowTitle(win.tr("Customize your theme"))
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._style_key = None

        # Working state
        self._base = look["base"]
        self._follow_system_base = str(win.i18n.settings.value("look_follow_system_base", "false")).lower() == "true"
        self._glass = False
        self._neon = bool(look["neon_effect"])
        self._stops_set = bool(look["stops"])
        self._stops = list(look["stops"] or self._default_stops())
        self._direction = look["direction"]
        self._intensity = look["intensity"]
        self._effect = look["effect"] if look["effect"] in NEON_BG_EFFECTS else "none"
        self._button_custom = bool(look["button_custom"])
        self._editing_button = False
        self._button_hex = look["button_hex"] or self._stops[0]
        self._active = 0
        self._user_presets = []
        self._slots = {}
        self._selected_preset_data: str | None = None
        self._preset_cards: list[_PresetCard] = []
        self._preset_cards_by_data: dict[str, _PresetCard] = {}

        # Close-confirmation state
        self._closing_confirmed = False
        self._confirm_on_close_in_offscreen = False
        self._unsaved_close_handler = None
        self._last_unsaved_close_prompt = None

        # Live window preview state (debounced ~120ms + Hold to compare)
        self._holding_compare = False
        self._applied_snapshot_pixmap = self._capture_win_pixmap()
        self._live_snapshot_pixmap = self._applied_snapshot_pixmap
        self._preview_grab_timer = QTimer(self)
        self._preview_grab_timer.setSingleShot(True)
        self._preview_grab_timer.setInterval(120)
        self._preview_grab_timer.timeout.connect(self._update_live_window_preview)

        self._apply_stylesheet()
        self._build_ui()

        self._refresh_preset_combo()
        self._theme_popups()
        self._select_stop(0)
        self._update_contrast_warning()
        self._update_undo_state()
        self._sync_column_widths()
        self._render_window_preview_label()
        self._stamp_sources()
        self._center_over_parent()

    def _center_over_parent(self):
        if self.win is not None and self.win.width() > 100 and self.win.height() > 100:
            geo = self.win.geometry()
            cx = geo.x() + (geo.width() - self.width()) // 2
            cy = geo.y() + (geo.height() - self.height()) // 2
            self.move(max(0, cx), max(0, cy))

    def _build_ui(self):
        win = self.win
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        # ---- Top window controls bar (frameless: minimize & close + optional preview toggle on narrow) ----
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(4, 0, 0, 0)
        top_bar.setSpacing(6)
        top_bar.addStretch(1)
        self._toggle_preview_btn = GlassButton(_escape_mnemonic(win.tr("Live window preview")))
        self._toggle_preview_btn.setCheckable(True)
        self._toggle_preview_btn.setChecked(False)
        set_role(self._toggle_preview_btn, "panel-toggle")
        self._toggle_preview_btn.setFixedHeight(26)
        self._toggle_preview_btn.hide()
        self._toggle_preview_btn.clicked.connect(self._on_toggle_narrow_preview)
        top_bar.addWidget(self._toggle_preview_btn)
        self._minimize_btn = TitleBarButton("–")
        self._minimize_btn.setToolTip(win.tr("Minimize"))
        self._minimize_btn.clicked.connect(self.showMinimized)
        top_bar.addWidget(self._minimize_btn)
        self._close_btn = TitleBarButton("×", danger=True)
        self._close_btn.setToolTip(win.tr("Close"))
        self._close_btn.clicked.connect(self._request_close)
        top_bar.addWidget(self._close_btn)
        root.addLayout(top_bar)

        # ---- Main body: [Left vertical nav (~144px)] + [Center full-height content] + [Right live preview (260-288px)] ----
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(10)
        self._body_row = body_row

        # 1. Left navigation card (~144 px, inner margins 8,10,8,10 so Appearance never clips)
        self._nav_card = Card("")
        self._nav_card.title_label.hide()
        self._nav_card.layout().setContentsMargins(8, 10, 8, 10)
        self._nav_card.setFixedWidth(144)
        self._nav_card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        nav_lay = self._nav_card.body
        nav_lay.setSpacing(6)

        self._tools_available = sys.platform.startswith("win")
        self._nav_general_btn = GlassButton(_escape_mnemonic(win.tr("General")))
        self._nav_appearance_btn = GlassButton(_escape_mnemonic(win.tr("Appearance")))
        self._nav_tools_btn = GlassButton(_escape_mnemonic(win.tr("Tools")))
        self._nav_about_btn = GlassButton(_escape_mnemonic(win.tr("About")))
        self._nav_buttons = {
            "general": self._nav_general_btn,
            "appearance": self._nav_appearance_btn,
            "tools": self._nav_tools_btn,
            "about": self._nav_about_btn,
        }
        for sec_key, btn in self._nav_buttons.items():
            btn.setCheckable(True)
            set_role(btn, "preset-segment-btn")
            btn.setFixedHeight(34)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _checked=False, k=sec_key: self.select_section(k))
            nav_lay.addWidget(btn)
        self._nav_tools_btn.setVisible(self._tools_available)
        nav_lay.addStretch(1)
        body_row.addWidget(self._nav_card)

        # 2. Center content column (full height, vertical scroll only)
        self._content_card = Card("")
        self._content_card.title_label.hide()
        self._content_card.layout().setContentsMargins(10, 10, 10, 10)
        self._content_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_scroll = QScrollArea()
        self._content_scroll.setObjectName("settingsContentScroll")
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content_scroll.setStyleSheet("QScrollArea#settingsContentScroll { background: transparent; border: none; }")
        self._content_scroll.viewport().setObjectName("settingsContentViewport")
        self._content_scroll.viewport().setStyleSheet("QWidget#settingsContentViewport { background: transparent; }")

        self._content_scroll_widget = QWidget()
        self._content_scroll_widget.setObjectName("settingsContentWidget")
        self._content_scroll_widget.setStyleSheet("QWidget#settingsContentWidget { background: transparent; }")
        scroll_lay = QVBoxLayout(self._content_scroll_widget)
        scroll_lay.setContentsMargins(0, 0, 2, 0)
        scroll_lay.setSpacing(8)

        self._section_stack = QStackedWidget()
        self._section_stack.setObjectName("settingsSectionStack")
        self._section_stack.setStyleSheet("QStackedWidget#settingsSectionStack { background: transparent; }")
        scroll_lay.addWidget(self._section_stack)
        self._content_scroll.setWidget(self._content_scroll_widget)
        self._content_card.body.addWidget(self._content_scroll, 1)
        body_row.addWidget(self._content_card, 1)

        # 3. Right column: Live window preview (260-288 px, 16:11 thumbnail) + Hold to compare underneath
        self._live_preview_card = Card("")
        self._live_preview_card.title_label.hide()
        self._live_preview_card.layout().setContentsMargins(10, 10, 10, 10)
        self._live_preview_card.setFixedWidth(280)
        self._live_preview_card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        pv_col = self._live_preview_card.body
        pv_col.setSpacing(8)

        pv_head_row = QHBoxLayout()
        pv_head_row.setContentsMargins(0, 0, 0, 0)
        pv_head_row.setSpacing(6)
        self._preview_title_label = QLabel(win.tr("Live window preview"))
        set_role(self._preview_title_label, "field-label")
        pv_head_row.addWidget(self._preview_title_label)

        self._modified_badge = QLabel(win.tr("Modified"))
        self._modified_badge.setObjectName("presetModifiedBadge")
        self._modified_badge.hide()
        pv_head_row.addWidget(self._modified_badge)
        pv_head_row.addStretch(1)
        pv_col.addLayout(pv_head_row)

        self._preview_mode_label = QLabel("")
        set_role(self._preview_mode_label, "muted")
        self._preview_mode_label.setWordWrap(True)
        pv_col.addWidget(self._preview_mode_label)

        self._window_preview_label = QLabel()
        self._window_preview_label.setObjectName("windowLivePreviewLabel")
        self._window_preview_label.setFixedSize(256, 176)  # 16:11 aspect ratio
        self._window_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv_col.addWidget(self._window_preview_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self._hold_compare_btn = GlassButton(_escape_mnemonic(win.tr("Hold to compare")))
        set_role(self._hold_compare_btn, "panel-toggle")
        self._hold_compare_btn.setFixedHeight(32)
        self._hold_compare_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hold_compare_btn.pressed.connect(self._on_hold_compare_pressed)
        self._hold_compare_btn.released.connect(self._on_hold_compare_released)
        pv_col.addWidget(self._hold_compare_btn)

        self._preview_details_frame = QFrame()
        self._preview_details_frame.setObjectName("previewDetailsFrame")
        det_lay = QVBoxLayout(self._preview_details_frame)
        det_lay.setContentsMargins(10, 8, 10, 8)
        det_lay.setSpacing(4)
        self._preview_details_label = QLabel("")
        self._preview_details_label.setObjectName("previewDetailsLabel")
        self._preview_details_label.setWordWrap(True)
        set_role(self._preview_details_label, "muted")
        det_lay.addWidget(self._preview_details_label)
        pv_col.addWidget(self._preview_details_frame)
        pv_col.addStretch(1)

        body_row.addWidget(self._live_preview_card)
        self._live_preview_card.hide()
        root.addLayout(body_row, 1)

        # Build the 4 navigation pages: General (0), Appearance (1), Tools (2), About (3)
        self._build_general_page()
        self._build_appearance_page()
        self._build_tools_page()
        self._build_about_page()

        # ---- Bottom shared footer: [status] ... [Revert] [Save changes] ----
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 2, 0, 0)
        footer.setSpacing(10)
        self._dirty_status_label = QLabel(win.tr("No unsaved changes"))
        set_role(self._dirty_status_label, "muted")
        footer.addWidget(self._dirty_status_label)
        footer.addStretch(1)

        self._revert_btn = GlassButton(win.tr("Revert"))
        self._revert_btn.setProperty("lead_icon", "undo")
        self._revert_btn.setToolTip(win.tr("Go back to the theme that was applied when you opened this window."))
        self._revert_btn.setFixedWidth(135)
        self._revert_btn.setFixedHeight(36)
        self._revert_btn.clicked.connect(self._revert_to_applied)
        self._undo_btn = self._revert_btn  # alias kept for tests/test_theme_dialog.py
        footer.addWidget(self._revert_btn)

        self._save_btn = GlassButton(win.tr("Save changes"))
        set_role(self._save_btn, "go")
        self._save_btn.setDefault(True)
        self._save_btn.setFixedWidth(135)
        self._save_btn.setFixedHeight(36)
        self._save_btn.clicked.connect(self._apply)
        self._apply_btn = self._save_btn
        footer.addWidget(self._save_btn)
        root.addLayout(footer)

        self.select_section("appearance")
        self.select_appearance_tab("themes")

    def _build_general_page(self):
        win = self.win
        self._general_page = QWidget()
        self._general_page.setObjectName("settingsGeneralPage")
        self._general_page.setStyleSheet("QWidget#settingsGeneralPage { background: transparent; }")
        lay = QVBoxLayout(self._general_page)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(12)

        lang_row = QHBoxLayout()
        self._lang_label = QLabel(win.tr("Language"))
        set_role(self._lang_label, "field-label")
        lang_row.addWidget(self._lang_label)
        lang_row.addStretch(1)
        self._lang_combo = RoundedComboBox()
        self._lang_codes = ["system", *LANGUAGE_NAMES.keys()]
        self._lang_combo.addItem("System default (Windows)", "system")
        for code, name in LANGUAGE_NAMES.items():
            self._lang_combo.addItem(name, code)
        saved_lang = str(win.i18n.settings.value("interface_language", "system"))
        self._saved_lang = saved_lang if saved_lang in self._lang_codes else "system"
        self._lang_combo.setCurrentIndex(self._lang_codes.index(self._saved_lang))
        self._lang_combo.setMinimumWidth(150)
        self._lang_combo.activated.connect(self._on_language_picked)
        lang_row.addWidget(self._lang_combo)
        lay.addLayout(lang_row)

        self._skip_mini_toggle = self._make_toggle(win.tr("Always open Pro mode (skip mini window)"))
        self._skip_mini_toggle.setChecked(
            str(win.i18n.settings.value("skip_mini_window", "false")).lower() == "true"
        )
        self._skip_mini_toggle.toggled.connect(self._on_skip_mini_toggled)
        lay.addWidget(self._skip_mini_toggle)

        self._detailed_conversion_toggle = self._make_toggle(win.tr("Show detailed conversion info"))
        self._detailed_conversion_toggle.setChecked(
            str(win.i18n.settings.value("show_detailed_conversion_info", "false")).lower() == "true"
        )
        self._detailed_conversion_toggle.toggled.connect(self._on_detailed_conversion_toggled)
        lay.addWidget(self._detailed_conversion_toggle)

        if hasattr(win, "keep_awake_chk"):
            self._keep_awake_toggle = self._make_toggle(win.tr("Keep PC awake during conversion"))
            self._keep_awake_toggle.setChecked(win.keep_awake_chk.isChecked())
            self._keep_awake_toggle.toggled.connect(win.keep_awake_chk.setChecked)
            lay.addWidget(self._keep_awake_toggle)
        if hasattr(win, "keep_display_chk"):
            self._keep_display_toggle = self._make_toggle(win.tr("Keep display on during conversion"))
            self._keep_display_toggle.setChecked(win.keep_display_chk.isChecked())
            self._keep_display_toggle.toggled.connect(win.keep_display_chk.setChecked)
            lay.addWidget(self._keep_display_toggle)

        lay.addStretch(1)
        self._section_stack.addWidget(self._general_page)

    def _build_appearance_page(self):
        win = self.win
        self._appearance_page = QWidget()
        self._appearance_page.setObjectName("settingsAppearancePage")
        self._appearance_page.setStyleSheet("QWidget#settingsAppearancePage { background: transparent; }")
        lay = QVBoxLayout(self._appearance_page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # Sub-tabs bar: Themes | Colors | Glass & Shape
        tab_frame = QFrame()
        set_role(tab_frame, "preset-segment")
        tab_row = QHBoxLayout(tab_frame)
        tab_row.setContentsMargins(2, 2, 2, 2)
        tab_row.setSpacing(4)
        self._tab_themes_btn = GlassButton(_escape_mnemonic(win.tr("Themes")))
        self._tab_colors_btn = GlassButton(_escape_mnemonic(win.tr("Colors")))
        self._tab_glass_shape_btn = GlassButton(_escape_mnemonic(win.tr("Shape")))
        self._appearance_tab_btns = {
            "themes": self._tab_themes_btn,
            "colors": self._tab_colors_btn,
            "glass_shape": self._tab_glass_shape_btn,
        }
        self._subtab_buttons = self._appearance_tab_btns
        for tkey, btn in self._appearance_tab_btns.items():
            btn.setCheckable(True)
            set_role(btn, "preset-segment-btn")
            btn.setFixedHeight(32)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _c=False, k=tkey: self.select_appearance_tab(k))
            tab_row.addWidget(btn, 1)
        lay.addWidget(tab_frame)

        self._appearance_stack = QStackedWidget()
        self._appearance_stack.setObjectName("appearanceSubStack")
        self._appearance_stack.setStyleSheet("QStackedWidget#appearanceSubStack { background: transparent; }")
        lay.addWidget(self._appearance_stack, 1)

        # ---- Tab 1: Themes (Gallery header with +/- + Gallery + Actions, hidden _preset_combo) ----
        self._themes_tab = QWidget()
        self._themes_tab.setObjectName("appearanceThemesTab")
        self._themes_tab.setStyleSheet("QWidget#appearanceThemesTab { background: transparent; }")
        t_lay = QVBoxLayout(self._themes_tab)
        t_lay.setContentsMargins(2, 2, 2, 2)
        t_lay.setSpacing(8)

        # Hidden _preset_combo kept for full compatibility with existing tests
        self._preset_combo = RoundedComboBox(self._themes_tab)
        self._preset_combo.activated.connect(self._on_preset_selected)
        self._preset_combo.hide()

        # Gallery panel header bar: Presets title + "+" (Save as new) / "−" (Delete)
        gallery_header_row = QHBoxLayout()
        gallery_header_row.setContentsMargins(0, 0, 0, 0)
        gallery_header_row.setSpacing(8)
        self._presets_label = QLabel(win.tr("Presets"))
        set_role(self._presets_label, "field-label")
        gallery_header_row.addWidget(self._presets_label)
        gallery_header_row.addStretch(1)
        self._save_preset_btn = GlassButton("+")
        set_role(self._save_preset_btn, "stepper")
        self._save_preset_btn.setFixedSize(26, 26)
        self._save_preset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _attach_press_bounce(self._save_preset_btn)
        self._save_preset_btn.setToolTip(win.tr("Save current colours as a new preset"))
        self._save_preset_btn.clicked.connect(self._save_current_as_preset)
        self._save_preset_btn.hide()
        gallery_header_row.addWidget(self._save_preset_btn)
        self._delete_preset_btn = GlassButton("−")
        set_role(self._delete_preset_btn, "stepper")
        self._delete_preset_btn.setFixedSize(26, 26)
        self._delete_preset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _attach_press_bounce(self._delete_preset_btn)
        self._delete_preset_btn.setToolTip(win.tr("Delete selected preset"))
        self._delete_preset_btn.clicked.connect(self._delete_selected_preset)
        self._delete_preset_btn.hide()
        gallery_header_row.addWidget(self._delete_preset_btn)
        t_lay.addLayout(gallery_header_row)

        # Gallery container
        self._preset_gallery_widget = QWidget()
        self._preset_gallery_widget.setObjectName("presetGalleryWidget")
        self._preset_gallery_widget.setStyleSheet("QWidget#presetGalleryWidget { background: transparent; }")
        self._preset_gallery_layout = QGridLayout(self._preset_gallery_widget)
        self._preset_gallery_layout.setContentsMargins(0, 0, 0, 0)
        self._preset_gallery_layout.setHorizontalSpacing(8)
        self._preset_gallery_layout.setVerticalSpacing(8)
        t_lay.addWidget(self._preset_gallery_widget)

        # Action buttons row: Surprise me and Reset to preset (equal size and style)
        self._themes_actions_widget = QWidget()
        self._themes_actions_widget.setObjectName("themesActionsFlowWidget")
        self._themes_actions_widget.setStyleSheet("QWidget#themesActionsFlowWidget { background: transparent; }")
        actions_lay = QHBoxLayout(self._themes_actions_widget)
        actions_lay.setContentsMargins(0, 0, 0, 0)
        actions_lay.setSpacing(8)
        self._themes_actions_flow = actions_lay  # keep attribute for backward compatibility

        # "Save as new" is styled and placed as a card-sized tile (114px tall)
        # directly inside _preset_gallery_layout right next to the theme cards.
        self._save_as_new_btn = GlassButton("+  " + _escape_mnemonic(win.tr("Save as new")), self._preset_gallery_widget)
        self._save_as_new_btn.setObjectName("saveAsNewPresetCardBtn")
        set_role(self._save_as_new_btn, "panel-toggle")
        self._save_as_new_btn.setFixedHeight(114)
        self._save_as_new_btn.setMinimumWidth(168)
        self._save_as_new_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._save_as_new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_as_new_btn.setToolTip(win.tr("Save current colours as a new preset"))
        self._save_as_new_btn.clicked.connect(self._save_current_as_preset)
        self._save_as_new_btn.hide()

        self._surprise_btn = GlassButton(_escape_mnemonic(win.tr("Surprise me")))
        self._surprise_btn.setProperty("lead_icon", "sparkles")
        self._surprise_btn.setFixedHeight(34)
        self._surprise_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._surprise_btn.clicked.connect(self._surprise_me)
        actions_lay.addWidget(self._surprise_btn, 1)

        self._reset_btn = GlassButton(_escape_mnemonic(win.tr("Reset to preset")))
        self._reset_btn.setToolTip(win.tr("Reset this theme to its original default look."))
        self._reset_btn.setFixedHeight(34)
        self._reset_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._reset_btn.clicked.connect(self._reset)
        actions_lay.addWidget(self._reset_btn, 1)

        # Hidden Import/Export JSON buttons kept only for programmatic compatibility
        self._import_presets_btn = GlassButton(_escape_mnemonic(win.tr("Import JSON")), self._themes_tab)
        set_role(self._import_presets_btn, "panel-toggle")
        self._import_presets_btn.clicked.connect(self._import_presets_json)
        self._import_presets_btn.hide()

        self._export_presets_btn = GlassButton(_escape_mnemonic(win.tr("Export JSON")), self._themes_tab)
        set_role(self._export_presets_btn, "panel-toggle")
        self._export_presets_btn.clicked.connect(self._export_presets_json)
        self._export_presets_btn.hide()

        t_lay.addWidget(self._themes_actions_widget)
        t_lay.addStretch(1)
        self._appearance_stack.addWidget(self._themes_tab)

        # ---- Tab 2: Colors ----
        self._colors_tab = QWidget()
        self._colors_tab.setObjectName("appearanceColorsTab")
        self._colors_tab.setStyleSheet("QWidget#appearanceColorsTab { background: transparent; }")
        c_lay = QVBoxLayout(self._colors_tab)
        c_lay.setContentsMargins(2, 2, 2, 2)
        c_lay.setSpacing(8)

        base_row = QHBoxLayout()
        base_row.setSpacing(8)
        base_row.addWidget(QLabel(win.tr("Base theme")))
        base_seg = QFrame()
        set_role(base_seg, "preset-segment")
        base_seg_lay = QHBoxLayout(base_seg)
        base_seg_lay.setContentsMargins(2, 2, 2, 2)
        base_seg_lay.setSpacing(4)
        self._base_dark_btn = GlassButton(win.tr("Dark"))
        self._base_light_btn = GlassButton(win.tr("Light"))
        self._base_system_btn = GlassButton(win.tr("System"))
        for b_key, b_btn in (("dark", self._base_dark_btn), ("light", self._base_light_btn), ("system", self._base_system_btn)):
            b_btn.setCheckable(True)
            set_role(b_btn, "preset-segment-btn")
            b_btn.setFixedHeight(30)
            b_btn.setMinimumWidth(0)
            b_btn.clicked.connect(lambda _c=False, k=b_key: self._on_base_segment_clicked(k))
            base_seg_lay.addWidget(b_btn, 1)
        base_row.addWidget(base_seg, 1)
        c_lay.addLayout(base_row)

        self._preview = _GradientPreviewBar()
        self._preview.setFixedHeight(26)
        c_lay.addWidget(self._preview)

        self._swatch_bar_widget = QWidget()
        self._swatch_bar_widget.setObjectName("swatchFlowWidget")
        self._swatch_bar_widget.setStyleSheet("QWidget#swatchFlowWidget { background: transparent; }")
        self._swatch_row_layout = _FlowLayout(self._swatch_bar_widget, margin=0, h_spacing=8, v_spacing=6)
        self._swatch_buttons = []
        self._add_btn = GlassButton(_escape_mnemonic(win.tr("+ Add Color")))
        self._add_btn.clicked.connect(self._add_stop)
        c_lay.addWidget(self._swatch_bar_widget)

        self._contrast_warn_label = QLabel(win.tr("Low contrast with background or button text (< 3:1)"))
        self._contrast_warn_label.setObjectName("contrastWarnLabel")
        self._contrast_warn_label.setWordWrap(True)
        self._contrast_warn_label.hide()
        c_lay.addWidget(self._contrast_warn_label)

        # Collapsible advanced color picker block ("Customize colors" — collapsed by default per U4)
        self._customize_colors_btn = GlassButton("\u25b8 " + _escape_mnemonic(win.tr("Customize colors")))
        self._customize_colors_btn.setProperty("drawer_header", True)
        self._customize_colors_btn.setCheckable(True)
        self._customize_colors_btn.setChecked(False)
        set_role(self._customize_colors_btn, "panel-toggle")
        self._customize_colors_btn.clicked.connect(self._toggle_customize_colors_panel)
        c_lay.addWidget(self._customize_colors_btn)

        self._customize_colors_panel = QWidget()
        self._customize_colors_panel.setObjectName("customizeColorsPanel")
        self._customize_colors_panel.setStyleSheet("QWidget#customizeColorsPanel { background: transparent; }")
        picker_lay = QVBoxLayout(self._customize_colors_panel)
        picker_lay.setContentsMargins(0, 2, 0, 2)
        picker_lay.setSpacing(6)

        self._sv = _SVPicker()
        self._sv.changed.connect(self._on_sv_changed)
        picker_lay.addWidget(self._sv)

        self._hue = _HueSlider()
        self._hue.valueChanged.connect(self._on_hue_changed)
        picker_lay.addWidget(self._hue)

        hex_row = QHBoxLayout()
        hex_row.setSpacing(10)
        self._eyedrop_btn = QToolButton()
        self._eyedrop_btn.setObjectName("eyedropBtn")
        self._eyedrop_btn.setFixedSize(36, 36)
        self._eyedrop_btn.setIconSize(QSize(36, 36))
        self._eyedrop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eyedrop_btn.setStyleSheet(
            "QToolButton#eyedropBtn { border: none; background: transparent; padding: 0px; }"
        )
        self._eyedrop_btn.setToolTip(win.tr("Pick colour from screen"))
        self._eyedrop_btn.clicked.connect(self._start_eyedrop)
        hex_row.addWidget(self._eyedrop_btn)
        self._hex_edit = QLineEdit()
        self._hex_edit.setMaxLength(7)
        self._hex_edit.editingFinished.connect(self._on_hex_edited)
        self._hex_edit.textChanged.connect(self._update_eyedrop_icon)
        hex_row.addWidget(self._hex_edit, 1)
        self._remove_btn = GlassButton("−")
        set_role(self._remove_btn, "stepper")
        self._remove_btn.setFixedSize(26, 26)
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _attach_press_bounce(self._remove_btn)
        self._remove_btn.setToolTip(win.tr("Remove this colour"))
        self._remove_btn.clicked.connect(self._remove_stop)
        hex_row.addWidget(self._remove_btn)
        picker_lay.addLayout(hex_row)
        self._customize_colors_panel.setVisible(False)
        c_lay.addWidget(self._customize_colors_panel)

        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel(win.tr("Background Effect")))
        effect_row.addStretch(1)
        self._effect_combo = RoundedComboBox()
        self._effect_labels = {
            "none": win.tr("None"), "grid": win.tr("Grid"), "dots": win.tr("Dots"),
            "diagonal": win.tr("Diagonal"), "waves": win.tr("Waves"), "rings": win.tr("Rings"),
            "crosshatch": win.tr("Crosshatch"), "stars": win.tr("Stars"),
        }
        for key in NEON_BG_EFFECTS:
            self._effect_combo.addItem(self._effect_labels[key], key)
        self._effect_combo.setCurrentIndex(NEON_BG_EFFECTS.index(self._effect))
        self._effect_combo.currentIndexChanged.connect(self._on_effect_changed)
        effect_row.addWidget(self._effect_combo)
        c_lay.addLayout(effect_row)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel(win.tr("Gradient Direction")))
        dir_row.addStretch(1)
        self._dir_value_label = QLabel(f"{self._direction}°")
        dir_row.addWidget(self._dir_value_label)
        c_lay.addLayout(dir_row)
        self._dir_slider = RoundHandleSlider(Qt.Orientation.Horizontal)
        self._dir_slider.setRange(0, 360)
        self._dir_slider.setValue(self._direction)
        self._dir_slider.valueChanged.connect(self._on_direction_changed)
        c_lay.addWidget(self._dir_slider)

        int_row = QHBoxLayout()
        int_row.addWidget(QLabel(win.tr("Color Intensity")))
        int_row.addStretch(1)
        self._int_value_label = QLabel(f"{self._intensity}%")
        int_row.addWidget(self._int_value_label)
        c_lay.addLayout(int_row)
        self._int_slider = RoundHandleSlider(Qt.Orientation.Horizontal)
        self._int_slider.setRange(0, 100)
        self._int_slider.setValue(self._intensity)
        self._int_slider.valueChanged.connect(self._on_intensity_changed)
        c_lay.addWidget(self._int_slider)
        c_lay.addStretch(1)
        self._appearance_stack.addWidget(self._colors_tab)

        # ---- Tab 3: Glass & Shape ----
        self._glass_shape_tab = QWidget()
        self._glass_shape_tab.setObjectName("appearanceGlassShapeTab")
        self._glass_shape_tab.setStyleSheet("QWidget#appearanceGlassShapeTab { background: transparent; }")
        g_lay = QVBoxLayout(self._glass_shape_tab)
        g_lay.setContentsMargins(2, 4, 2, 4)
        g_lay.setSpacing(12)

        toggles = QVBoxLayout()
        toggles.setSpacing(10)
        self._glass_toggle = self._make_toggle(win.tr("Glass effect"))
        self._glass_toggle.setChecked(False)
        self._glass_toggle.hide()
        self._neon_toggle = self._make_toggle(win.tr("Neon effect"))
        self._neon_toggle.setChecked(self._neon)
        self._neon_toggle.toggled.connect(self._on_neon_toggled)
        self._base_toggle = self._make_toggle(win.tr("Light theme base"))
        self._base_toggle.setChecked(self._base == "light")
        self._base_toggle.toggled.connect(self._on_base_toggled)
        self._base_toggle.hide()
        toggles.addWidget(self._neon_toggle)
        g_lay.addLayout(toggles)

        radius_row = QVBoxLayout()
        radius_row.setSpacing(10)
        self._radius_sliders = {}
        self._radius_labels = {}
        for key, title, maximum, value in (
            ("card", win.tr("Card rounding"), MAX_CARD_RADIUS, card_radius()),
            ("button", win.tr("Button rounding"), MAX_BUTTON_RADIUS, button_radius()),
        ):
            colw = QVBoxLayout()
            colw.setSpacing(4)
            head = QHBoxLayout()
            head.addWidget(QLabel(title))
            head.addStretch(1)
            lbl = QLabel(f"{value} px")
            head.addWidget(lbl)
            colw.addLayout(head)
            sl = RoundHandleSlider(Qt.Orientation.Horizontal)
            sl.setRange(0, maximum)
            sl.setValue(value)
            sl.valueChanged.connect(lambda v, k=key: self._on_radius_changed(k, v))
            colw.addWidget(sl)
            radius_row.addLayout(colw)
            self._radius_sliders[key] = sl
            self._radius_labels[key] = lbl
        self._card_radius_slider = self._radius_sliders["card"]
        self._btn_radius_slider = self._radius_sliders["button"]
        g_lay.addLayout(radius_row)
        g_lay.addStretch(1)
        self._appearance_stack.addWidget(self._glass_shape_tab)

        self._section_stack.addWidget(self._appearance_page)
        self._sync_base_segment_buttons()

    def _build_tools_page(self):
        win = self.win
        self._tools_page = QWidget()
        self._tools_page.setObjectName("settingsToolsPage")
        self._tools_page.setStyleSheet("QWidget#settingsToolsPage { background: transparent; }")
        lay = QVBoxLayout(self._tools_page)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(12)

        tools_available = bool(getattr(self, "_tools_available", sys.platform.startswith("win")))

        self.tools_channel_btn = RoundedMenuButton()
        self._tools_channel_btn = self.tools_channel_btn
        self.tools_channel_btn.popup_align_right = False
        set_role(self.tools_channel_btn, "panel-toggle")
        self.tools_channel_btn.setFixedHeight(36)
        self.tools_channel_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if hasattr(win, "_on_tools_channel_picked"):
            self.tools_channel_btn.activatedIndex.connect(win._on_tools_channel_picked)
        lay.addWidget(self.tools_channel_btn)
        self.tools_channel_btn.setVisible(tools_available)

        self._tools_channel_desc = QLabel(win.tr(
            "Download channel for FFmpeg / HandBrakeCLI:\n"
            "• Stable: verified release.\n"
            "• Latest: newest upstream build."
        ))
        self._tools_channel_desc.setWordWrap(True)
        set_role(self._tools_channel_desc, "muted")
        lay.addWidget(self._tools_channel_desc)
        self._tools_channel_desc.setVisible(tools_available)

        self.install_btn = GlassButton(_escape_mnemonic(win.tr("Check for updates")))
        self._install_btn = self.install_btn
        self._check_updates_btn = self.install_btn
        set_role(self.install_btn, "panel-toggle")
        self.install_btn.setFixedHeight(36)
        self.install_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.install_btn.setToolTip(win.tr(
            "Check for app and tool (FFmpeg / HandBrakeCLI) updates."
        ))
        self.install_btn.setEnabled(getattr(win, "_setup_proc", None) is None)
        if hasattr(win, "check_for_updates"):
            self.install_btn.clicked.connect(win.check_for_updates)
        lay.addWidget(self.install_btn)
        self.install_btn.setVisible(tools_available)

        self._check_updates_desc = QLabel(win.tr(
            "Check for app and tool (FFmpeg / HandBrakeCLI) updates."
        ))
        self._check_updates_desc.setWordWrap(True)
        set_role(self._check_updates_desc, "muted")
        lay.addWidget(self._check_updates_desc)
        self._check_updates_desc.setVisible(tools_available)

        if tools_available:
            self._attach_tools_refs()
        else:
            self._detach_tools_refs()

        lay.addStretch(1)
        self._section_stack.addWidget(self._tools_page)

    def _attach_tools_refs(self):
        win = getattr(self, "win", None)
        if win is None or not getattr(self, "_tools_available", False):
            return
        win.tools_channel_btn = self.tools_channel_btn
        win.install_btn = self.install_btn
        self.install_btn.setEnabled(getattr(win, "_setup_proc", None) is None)
        if hasattr(win, "_retranslate_tools_channel"):
            win._retranslate_tools_channel()

    def _detach_tools_refs(self):
        win = getattr(self, "win", None)
        if win is None:
            return
        if getattr(win, "tools_channel_btn", None) is getattr(self, "tools_channel_btn", None):
            win.tools_channel_btn = None
        if getattr(win, "install_btn", None) is getattr(self, "install_btn", None):
            win.install_btn = None

    def _build_about_page(self):
        win = self.win
        self._about_page = QWidget()
        self._about_page.setObjectName("settingsAboutPage")
        self._about_page.setStyleSheet("QWidget#settingsAboutPage { background: transparent; }")
        lay = QVBoxLayout(self._about_page)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(12)

        self._about_app_label = QLabel(win.tr("HDR to SDR Video Converter"))
        set_role(self._about_app_label, "card-title")
        lay.addWidget(self._about_app_label)

        self._about_version_label = QLabel(win.tr("Version {version}", version=APP_VERSION))
        set_role(self._about_version_label, "muted")
        lay.addWidget(self._about_version_label)

        self._about_btn_widget = QWidget()
        self._about_btn_widget.setObjectName("aboutActionsFlowWidget")
        self._about_btn_widget.setStyleSheet("QWidget#aboutActionsFlowWidget { background: transparent; }")
        btn_flow = _FlowLayout(self._about_btn_widget, margin=0, h_spacing=10, v_spacing=8)
        self._about_kofi_btn = GlassButton("\u2665 Support on Ko-fi")
        set_role(self._about_kofi_btn, "kofi")
        self._about_kofi_btn.setToolTip(KOFI_URL)
        self._about_kofi_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KOFI_URL)))
        btn_flow.addWidget(self._about_kofi_btn)

        self._about_activity_btn = GlassButton(_escape_mnemonic(win.tr("Open Activity log")))
        set_role(self._about_activity_btn, "panel-toggle")
        self._about_activity_btn.clicked.connect(self._open_activity_from_about)
        btn_flow.addWidget(self._about_activity_btn)
        lay.addWidget(self._about_btn_widget)
        lay.addStretch(1)
        self._section_stack.addWidget(self._about_page)

    def _open_activity_from_about(self):
        if hasattr(self.win, "toggle_activity"):
            if getattr(self.win, "side_tab", None) != "activity":
                self.win.toggle_activity()

    # ---- Navigation & Sub-tabs ----------------------------------------------
    def select_section(self, sec_key: str):
        order = ("general", "appearance", "tools", "about")
        if sec_key not in order:
            return
        if sec_key == "tools" and not getattr(self, "_tools_available", sys.platform.startswith("win")):
            return
        self._current_section = sec_key
        self._section_stack.setCurrentIndex(order.index(sec_key))
        for k, btn in self._nav_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(k == sec_key)
            btn.blockSignals(False)
        self._sync_column_widths()

    def select_appearance_tab(self, tab_key: str):
        order = ("themes", "colors", "glass_shape")
        if tab_key not in order:
            return
        self._current_appearance_tab = tab_key
        self._appearance_stack.setCurrentIndex(order.index(tab_key))
        for k, btn in self._appearance_tab_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == tab_key)
            btn.blockSignals(False)
        self._sync_column_widths()

    @property
    def current_section(self) -> str:
        return getattr(self, "_current_section", "appearance")

    @property
    def current_appearance_tab(self) -> str:
        return getattr(self, "_current_appearance_tab", "themes")

    @property
    def _preview_Showing_applied(self) -> bool:
        return bool(getattr(self, "_holding_compare", False))

    def _capture_live_snapshot(self):
        self._update_live_window_preview()

    def _set_customize_colors_expanded(self, expanded: bool):
        self._customize_colors_btn.setChecked(bool(expanded))
        self._toggle_customize_colors_panel()

    def _toggle_customize_colors_panel(self):
        expanded = self._customize_colors_btn.isChecked()
        self._customize_colors_panel.setVisible(expanded)
        arrow = "\u25be " if expanded else "\u25b8 "
        self._customize_colors_btn.setText(arrow + _escape_mnemonic(self.win.tr("Customize colors")))
        self._sync_column_widths()

    def _on_base_segment_clicked(self, key: str):
        if key == "system":
            self._follow_system_base = True
            self.win.i18n.settings.setValue("look_follow_system_base", "true")
            resolved = _detect_windows_system_base()
            self._base = resolved
        else:
            self._follow_system_base = False
            self.win.i18n.settings.setValue("look_follow_system_base", "false")
            self._base = "light" if key == "light" else "dark"
        self._base_toggle.blockSignals(True)
        self._base_toggle.setChecked(self._base == "light")
        self._base_toggle.blockSignals(False)
        self._sync_base_segment_buttons()
        self._follow_default_stops()
        self._live_apply()

    def _sync_base_segment_buttons(self):
        if not hasattr(self, "_base_dark_btn"):
            return
        active = "system" if self._follow_system_base else self._base
        for k, btn in (("dark", self._base_dark_btn), ("light", self._base_light_btn), ("system", self._base_system_btn)):
            btn.blockSignals(True)
            btn.setChecked(k == active)
            btn.blockSignals(False)

    # ---- Live Window Preview & Hold to Compare ------------------------------
    def _capture_win_pixmap(self) -> QPixmap:
        if self.win is not None and hasattr(self.win, "grab"):
            try:
                pm = self.win.grab()
                if not pm.isNull():
                    # Normalize devicePixelRatio to 1.0 so High-DPI Windows scaling (125%/150%/200%)
                    # never causes QPainter.drawPixmap to shrink the grabbed window into the top-left
                    # fraction of the preview frame.
                    img = pm.toImage()
                    img.setDevicePixelRatio(1.0)
                    return QPixmap.fromImage(img)
            except Exception:
                pass
        return _render_preset_preview_pixmap(self._current_full(), 320, 180)

    def _schedule_live_window_preview(self):
        if hasattr(self, "_preview_grab_timer"):
            self._preview_grab_timer.start()

    def _update_live_window_preview(self):
        self._live_snapshot_pixmap = self._capture_win_pixmap()
        self._render_window_preview_label()

    def _on_hold_compare_pressed(self):
        self._holding_compare = True
        self._render_window_preview_label()

    def _on_hold_compare_released(self):
        self._holding_compare = False
        self._render_window_preview_label()

    def _render_window_preview_label(self):
        lbl = getattr(self, "_window_preview_label", None)
        if lbl is None or (hasattr(self, "_live_preview_card") and self._live_preview_card.isHidden()):
            return
        t = self.tokens
        lbl.setStyleSheet(
            f"QLabel#windowLivePreviewLabel {{ background: {t['bg_field']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 0px; }}"
        )
        src = self._applied_snapshot_pixmap if self._holding_compare else self._live_snapshot_pixmap
        if src is not None and not src.isNull():
            cw, ch = max(32, lbl.width() - 4), max(24, lbl.height() - 4)
            canvas = QPixmap(cw, ch)
            canvas.fill(Qt.GlobalColor.transparent)
            p = QPainter(canvas)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            clip_p = QPainterPath()
            clip_p.addRoundedRect(QRectF(0.5, 0.5, cw - 1.0, ch - 1.0), 6.0, 6.0)
            p.setClipPath(clip_p)
            p.fillRect(0, 0, cw, ch, QColor(t["bg_window"]))
            if self._stops and self._intensity > 0:
                angle = math.radians(self._direction)
                diag = math.hypot(cw, ch) / 2.0
                cx, cy = cw / 2.0, ch / 2.0
                dx, dy = math.cos(angle) * diag, math.sin(angle) * diag
                grad = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
                alpha = round(255 * (self._intensity / 100.0))
                n = max(1, len(self._stops) - 1)
                for i, hex_c in enumerate(self._stops):
                    c = QColor(hex_c)
                    c.setAlpha(alpha)
                    grad.setColorAt(i / n if n else 0.0, c)
                p.fillRect(0, 0, cw, ch, grad)

            # Fill the inner preview area (with a 5px ambient gradient rim around it)
            pad = 5
            inner_w = max(16, cw - 2 * pad)
            inner_h = max(16, ch - 2 * pad)
            src_img = src.toImage()
            src_img.setDevicePixelRatio(1.0)
            scaled_img = src_img.scaled(
                inner_w,
                inner_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled_img.setDevicePixelRatio(1.0)
            # Crop from the top-center of the scaled window so both columns and header fill the preview frame 100%
            crop_x = max(0, (scaled_img.width() - inner_w) // 2)
            cropped = scaled_img.copy(crop_x, 0, inner_w, min(inner_h, scaled_img.height()))
            inner_rect = QRectF(pad, pad, inner_w, inner_h)
            inner_clip = QPainterPath()
            inner_clip.addRoundedRect(inner_rect, 4.5, 4.5)
            p.save()
            p.setClipPath(inner_clip)
            p.drawImage(inner_rect, cropped)
            p.restore()
            p.setPen(QPen(QColor(t["accent"] if self._neon else t["border"]), 1.1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(inner_rect, 4.5, 4.5)
            p.end()
            lbl.setPixmap(canvas)
        if hasattr(self, "_preview_mode_label"):
            if self._holding_compare:
                self._preview_mode_label.setText(self.win.tr("Showing applied look (before edits)"))
            else:
                preset_name = self._preset_combo.currentText() if hasattr(self, "_preset_combo") else ""
                self._preview_mode_label.setText(preset_name)
        if hasattr(self, "_preview_details_frame") and hasattr(self, "_preview_details_label"):
            r_c = max(6, min(12, int(t.get("radius_card", 10)) - 2))
            self._preview_details_frame.setStyleSheet(
                f"QFrame#previewDetailsFrame {{ background: {t['bg_field']}; border: 1px solid {t['border']}; border-radius: {r_c}px; }}"
            )
            base_str = "Light" if self._base == "light" else "Dark"
            if self._follow_system_base:
                base_str += " (System)"
            mat_parts = ["Neon"] if self._neon else ["Classic"]
            eff_str = self._effect_labels.get(self._effect, "None") if hasattr(self, "_effect_labels") else self._effect
            accent_hex = (self._button_hex if self._button_custom else (self._stops[0] if self._stops else t["accent"])).upper()
            lines = [
                f"<b>{self.win.tr('Base theme')}:</b> {base_str} · {' + '.join(mat_parts)}",
                f"<b>{self.win.tr('Colors')}:</b> {accent_hex} · {self._intensity}% ({self._direction}°)",
                f"<b>{self.win.tr('Background Effect')}:</b> {eff_str}",
                f"<b>{self.win.tr('Card rounding')}:</b> {card_radius()} px · {button_radius()} px",
            ]
            self._preview_details_label.setText("<br>".join(lines))

    def _update_contrast_warning(self):
        lbl = getattr(self, "_contrast_warn_label", None)
        if lbl is None:
            return
        t = self.tokens
        low, _bg_r, _txt_r = is_accent_contrast_low(t["accent"], t["bg_card"], t.get("on_accent"))
        lbl.setStyleSheet(f"QLabel#contrastWarnLabel {{ color: {t['warning']}; font-size: 8.5pt; padding: 1px 0px; }}")
        lbl.setVisible(bool(low))

    # ---- i18n stamping & retranslation --------------------------------------
    def _stamp_sources(self):
        i18n = self.win.i18n
        reverse = {i18n.strings.get(key, eng): eng for eng, key in i18n.keys_by_english.items()}
        for w in self.findChildren(QWidget):
            if isinstance(w, (QLineEdit, QComboBox)) or w is getattr(self, "tools_channel_btn", None):
                continue
            if hasattr(w, "setText") and hasattr(w, "text") and w.property("i18n_src") is None:
                raw_txt = str(w.text() or "").replace("&&", "&")
                src = reverse.get(raw_txt)
                if src is not None:
                    w.setProperty("i18n_src", src)
            if w.property("i18n_tip_src") is None:
                src = reverse.get(w.toolTip())
                if src is not None:
                    w.setProperty("i18n_tip_src", src)

    def _on_language_picked(self, index):
        code = self._lang_combo.itemData(index)
        if not code or code == self._saved_lang:
            return
        self._saved_lang = code
        self.win.change_language(code)
        self._retranslate()

    def _retranslate(self, reverse=None):
        tr = self.win.tr
        self.setWindowTitle(tr("Customize your theme"))
        for w in self.findChildren(QWidget):
            src = w.property("i18n_src")
            if src is not None and hasattr(w, "setText"):
                txt = tr(src)
                if isinstance(w, QPushButton):
                    txt = _escape_mnemonic(txt)
                w.setText(txt)
            src = w.property("i18n_tip_src")
            if src is not None:
                w.setToolTip(tr(src))
        self._effect_labels = {
            "none": tr("None"), "grid": tr("Grid"), "dots": tr("Dots"),
            "diagonal": tr("Diagonal"), "waves": tr("Waves"), "rings": tr("Rings"),
            "crosshatch": tr("Crosshatch"), "stars": tr("Stars"),
        }
        for i, key in enumerate(NEON_BG_EFFECTS):
            self._effect_combo.setItemText(i, self._effect_labels[key])
        if hasattr(self, "_about_version_label"):
            self._about_version_label.setText(tr("Version {version}", version=APP_VERSION))
        if hasattr(self, "_customize_colors_btn"):
            arrow = "\u25be " if self._customize_colors_btn.isChecked() else "\u25b8 "
            self._customize_colors_btn.setText(arrow + _escape_mnemonic(tr("Customize colors")))
        if hasattr(self.win, "_retranslate_tools_channel"):
            self.win._retranslate_tools_channel()
        self._refresh_preset_combo()
        self._rebuild_swatches()
        self._update_undo_state()
        self._sync_column_widths()
        self._stamp_sources()

    def _default_stops(self):
        return list(_THEME_DEFAULT_STOPS["neon" if self._neon else self._base])

    def _apply_stylesheet(self):
        self.setStyleSheet(build_theme_dialog_qss(self.tokens, button_radius_px=button_radius()))

    def _make_toggle(self, text):
        tg = ToggleSwitch(text)
        self._style_toggle(tg)
        return tg

    def _style_toggle(self, tg):
        t = self.tokens
        tg.setProperty("on_color", t["accent"])
        tg.setProperty("off_color", t["border"])
        tg.setProperty("text_color", t["text"])
        tg.update()

    def _restyle(self):
        toggles = [self._skip_mini_toggle, self._glass_toggle, self._neon_toggle, self._base_toggle]
        for extra in ("_keep_awake_toggle", "_keep_display_toggle", "_detailed_conversion_toggle"):
            if hasattr(self, extra):
                toggles.append(getattr(self, extra))
        for tg in toggles:
            self._style_toggle(tg)
        self._update_contrast_warning()
        key = (self.theme_name, self._glass, self._neon, self.tokens["accent"])
        if key != self._style_key:
            self._style_key = key
            self._apply_stylesheet()
            self._rebuild_swatches()
            for w in self.findChildren(GlassButton):
                w.update()

    def _build_look(self):
        return dict(
            base=self._base,
            stops=list(self._stops) if self._stops_set else None,
            direction=self._direction,
            intensity=self._intensity,
            effect=self._effect,
            button_custom=bool(self._button_custom),
            button_hex=self._button_hex if self._button_custom else None,
            ui_style="classic",
            neon_effect=bool(self._neon),
        )

    # ---- Panel background: mirrors the live theme being edited --------------
    def paintEvent(self, event):
        t = self.tokens
        ring = None
        if _neon_active(self):
            ring = QColor(t["accent"])
            ring.setAlpha(140)
        paint_frameless_surface(
            self, window_radius(self), t["border"], self._paint_dialog_backdrop, ring
        )
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def get_blurred_bg(self):
        return self._blurred_bg

    def _theme_popups(self):
        t = self.tokens
        combos = [self._preset_combo, self._effect_combo, self._lang_combo]
        if getattr(self, "tools_channel_btn", None) is not None:
            combos.append(self.tools_channel_btn)
        for combo in combos:
            combo.set_popup_theme(t["bg_card"], t["border"], t["text"], t["bg_card_alt"], t["accent"])

    def _schedule_blur(self):
        if self._glass:
            self._blur_timer.start()

    def refresh_blur(self):
        self._blurred_bg = None
        return
        w, h = self.width(), self.height()
        if w < 2 or h < 2:
            return
        pixmap = QPixmap(w, h)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_dialog_backdrop(painter, veil=False)
        painter.end()
        self._blurred_bg = blurred_backdrop(pixmap)
        for card in self.findChildren(Card):
            card.update()
        for btn in self.findChildren(GlassButton):
            btn.update()

    def _on_toggle_narrow_preview(self, checked: bool = False):
        self._sync_column_widths()

    def _sync_column_widths(self):
        if hasattr(self, "_nav_buttons") and hasattr(self, "_nav_card"):
            max_nav_txt = max(
                (QFontMetrics(b.font()).horizontalAdvance(b.text().replace("&&", "&")) for b in self._nav_buttons.values()),
                default=76,
            )
            self._nav_card.setFixedWidth(max(144, max_nav_txt + 38))

        if hasattr(self, "_appearance_tab_btns"):
            for btn in (*self._appearance_tab_btns.values(), self._base_dark_btn, self._base_light_btn, self._base_system_btn):
                txt_w = QFontMetrics(btn.font()).horizontalAdvance(btn.text().replace("&&", "&"))
                btn.setMinimumWidth(txt_w + 18)

        for lbl in self.findChildren(QLabel):
            if not lbl.wordWrap() and lbl.text():
                tw = QFontMetrics(lbl.font()).horizontalAdvance(lbl.text())
                lbl.setMinimumWidth(tw + 10)

        if hasattr(self, "_live_preview_card"):
            self._live_preview_card.hide()
        if hasattr(self, "_toggle_preview_btn"):
            self._toggle_preview_btn.hide()

        if hasattr(self, "_revert_btn") and hasattr(self, "_save_btn"):
            fm = QFontMetrics(self._save_btn.font())
            w_revert = fm.horizontalAdvance(self._revert_btn.text().replace("&&", "&")) + 38
            w_save = fm.horizontalAdvance(self._save_btn.text().replace("&&", "&")) + 38
            btn_w = max(135, w_revert, w_save)
            self._revert_btn.setFixedWidth(btn_w)
            self._save_btn.setFixedWidth(btn_w)

        self._relayout_preset_gallery()
        for flow_attr in ("_themes_actions_flow", "_swatch_row_layout"):
            fl = getattr(self, flow_attr, None)
            if fl is not None and hasattr(fl, "invalidate"):
                fl.invalidate()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_column_widths()
        QTimer.singleShot(0, self.refresh_blur)

    def showEvent(self, event):
        super().showEvent(event)
        self._attach_tools_refs()
        self._sync_column_widths()
        QTimer.singleShot(0, self.refresh_blur)

    def _paint_dialog_backdrop(self, painter, veil=True):
        rect = self.rect()
        w, h = rect.width(), rect.height()
        t = self.tokens
        is_dark = _theme_is_dark(t)
        painter.fillRect(rect, QColor(t["bg_window"]))

        angle = math.radians(self._direction)
        diag = math.hypot(w, h) / 2.0
        cx, cy = w / 2.0, h / 2.0
        dx, dy = math.cos(angle) * diag, math.sin(angle) * diag
        grad = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
        n = max(1, len(self._stops) - 1)
        alpha = round(255 * (self._intensity / 100.0))
        for i, hex_color in enumerate(self._stops):
            color = QColor(hex_color)
            color.setAlpha(alpha)
            grad.setColorAt(i / n if n else 0.0, color)
        if self._stops_set:
            painter.fillRect(rect, grad)

        effect = self._effect
        if effect == "grid":
            _BgLayer._paint_grid_effect(painter, t, is_dark, w, h)
        elif effect == "dots":
            _BgLayer._paint_dots_effect(painter, t, is_dark, w, h)
        elif effect == "diagonal":
            _BgLayer._paint_diagonal_effect(painter, t, is_dark, w, h)
        elif effect == "waves":
            _BgLayer._paint_waves_effect(painter, t, is_dark, w, h)
        elif effect == "rings":
            _BgLayer._paint_rings_effect(painter, t, is_dark, w, h)
        elif effect == "crosshatch":
            _BgLayer._paint_crosshatch_effect(painter, t, is_dark, w, h)
        elif effect == "stars":
            _BgLayer._paint_stars_effect(painter, t, is_dark, w, h)

        if veil and self._glass:
            tint_top, tint_bottom = (58, 18) if is_dark else (65, 20)
            tint = QLinearGradient(0, 0, 0, h)
            tint.setColorAt(0.0, QColor(255, 255, 255, tint_top))
            tint.setColorAt(1.0, QColor(255, 255, 255, tint_bottom))
            painter.fillRect(rect, tint)
            if is_dark:
                painter.fillRect(rect, QColor(0, 0, 0, 60))

    # ---- Stop selection / inline colour editor ------------------------------
    def _select_stop(self, index):
        self._active = index
        self._editing_button = False
        h, s, v, _a = QColor(self._stops[index]).getHsvF()
        h = 0.0 if h < 0 else h
        self._hue.blockSignals(True)
        self._hue.setValue(round(h * 359))
        self._hue.blockSignals(False)
        self._sv.set_hue(round(h * 359))
        self._sv.set_sv(s, v)
        self._hex_edit.setText(self._stops[index].upper())
        self._remove_btn.setEnabled(len(self._stops) > 2)
        self._add_btn.setEnabled(len(self._stops) < 3)
        self._rebuild_swatches()

    def _rebuild_swatches(self):
        while self._swatch_row_layout.count():
            item = self._swatch_row_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not getattr(self, "_add_btn", None):
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._swatch_buttons = []
        for i, hex_color in enumerate(self._stops):
            btn = QToolButton()
            obj_n = f"swatchBtn_{i}"
            btn.setObjectName(obj_n)
            btn.setIcon(_swatch_icon(hex_color, size=26))
            btn.setIconSize(QSize(26, 26))
            btn.setFixedSize(30, 30)
            btn.setCheckable(True)
            btn.setChecked(i == self._active and not self._editing_button)
            btn.setStyleSheet(
                f"QToolButton#{obj_n} {{ border: 2px solid transparent; background: transparent; padding: 0px; }}"
                f"QToolButton#{obj_n}:checked {{ border: 2px solid {self.tokens['accent']}; }}"
            )
            btn.clicked.connect(lambda _checked=False, idx=i: self._select_stop(idx))
            self._swatch_row_layout.addWidget(btn)
            self._swatch_buttons.append(btn)

        if hasattr(self, "_add_btn"):
            self._swatch_row_layout.addWidget(self._add_btn)

        sep = QFrame()
        sep.setObjectName("swatchCustomDivider")
        sep.setFixedSize(1, 22)
        sep.setStyleSheet(f"QFrame#swatchCustomDivider {{ background: {self.tokens['border']}; border: none; }}")
        self._swatch_row_layout.addWidget(sep)

        button_label = QLabel(self.win.tr("Custom button"))
        button_label.setObjectName("customButtonLabel")
        button_label.setStyleSheet(f"QLabel#customButtonLabel {{ color: {self.tokens['text']}; font-size: 11px; }}")
        button_label.setMinimumWidth(QFontMetrics(button_label.font()).horizontalAdvance(button_label.text()) + 10)
        self._swatch_row_layout.addWidget(button_label)

        effective_button_hex = self._button_hex if self._button_custom else self._stops[0]
        self._button_swatch_btn = QToolButton()
        self._button_swatch_btn.setObjectName("buttonSwatchBtn")
        self._button_swatch_btn.setIcon(_swatch_icon(effective_button_hex, size=26))
        self._button_swatch_btn.setIconSize(QSize(26, 26))
        self._button_swatch_btn.setFixedSize(30, 30)
        self._button_swatch_btn.setCheckable(True)
        self._button_swatch_btn.setChecked(self._editing_button)
        self._button_swatch_btn.setStyleSheet(
            "QToolButton#buttonSwatchBtn { border: 2px solid transparent; background: transparent; padding: 0px; }"
            "QToolButton#buttonSwatchBtn:checked { border: 2px solid " + self.tokens["accent"] + "; }"
        )
        self._button_swatch_btn.setToolTip(
            self.win.tr("Button colour \u2014 click to {action}").format(
                action=self.win.tr("change it") if self._button_custom else self.win.tr("customize it")
            )
        )
        self._button_swatch_btn.clicked.connect(self._select_button)
        self._button_swatch_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._button_swatch_btn.customContextMenuRequested.connect(self._on_button_swatch_context_menu)
        self._swatch_row_layout.addWidget(self._button_swatch_btn)

        self._button_reset_btn = QToolButton()
        self._button_reset_btn.setObjectName("buttonResetBtn")
        self._button_reset_btn.setText("\u2715")
        self._button_reset_btn.setFixedSize(16, 16)
        self._button_reset_btn.setStyleSheet(
            "QToolButton#buttonResetBtn {"
            f"  color: {self.tokens['text']}; background: transparent;"
            "  border: none; font-size: 10px; padding: 0px;"
            "}"
            f"QToolButton#buttonResetBtn:hover {{ color: {self.tokens['accent']}; }}"
        )
        self._button_reset_btn.setToolTip(self.win.tr("Follow first gradient colour again"))
        self._button_reset_btn.setVisible(self._button_custom)
        self._button_reset_btn.clicked.connect(self._reset_button_colour)
        self._swatch_row_layout.addWidget(self._button_reset_btn)

        self._swatch_row_layout.invalidate()
        if hasattr(self, "_swatch_bar_widget"):
            self._swatch_bar_widget.updateGeometry()
        self._preview.set_stops(self._stops)

    def _active_color_hex(self):
        if self._editing_button:
            return self._button_hex if self._button_custom else self._stops[0]
        return self._stops[self._active]

    def _set_active_color(self, hex_color):
        if self._editing_button:
            self._button_custom = True
            self._button_hex = hex_color
            self._button_swatch_btn.setIcon(_swatch_icon(hex_color, size=26))
            self._button_reset_btn.setVisible(True)
            self._live_apply()
        else:
            self._set_active_stop(hex_color)

    def _select_button(self):
        self._editing_button = True
        hex_color = self._active_color_hex()
        h, s, v, _a = QColor(hex_color).getHsvF()
        h = 0.0 if h < 0 else h
        self._hue.blockSignals(True)
        self._hue.setValue(round(h * 359))
        self._hue.blockSignals(False)
        self._sv.set_hue(round(h * 359))
        self._sv.set_sv(s, v)
        self._hex_edit.setText(hex_color.upper())
        self._remove_btn.setEnabled(False)
        self._add_btn.setEnabled(len(self._stops) < 3)
        self._rebuild_swatches()

    def _touch_gradient(self):
        self._stops_set = True
        if self._intensity == 0:
            self._set_intensity_ui(_THEME_DEFAULT_INTENSITY["neon" if self._neon else self._base])

    def _set_intensity_ui(self, value):
        self._intensity = value
        self._int_slider.blockSignals(True)
        self._int_slider.setValue(value)
        self._int_slider.blockSignals(False)
        self._int_value_label.setText(f"{value}%")

    def _set_active_stop(self, hex_color):
        self._touch_gradient()
        self._stops[self._active] = hex_color
        self._preview.set_stops(self._stops)
        if self._active < len(self._swatch_buttons):
            self._swatch_buttons[self._active].setIcon(_swatch_icon(hex_color, size=26))
        self._live_apply()

    def _on_sv_changed(self, s, v):
        color = QColor.fromHsvF(self._hue.value() / 359.0, s, v)
        self._set_active_color(color.name())
        self._hex_edit.setText(color.name().upper())

    def _on_hue_changed(self, hue):
        self._sv.set_hue(hue)
        _h, s, v, _a = QColor(self._active_color_hex()).getHsvF()
        color = QColor.fromHsvF(hue / 359.0, s, v)
        self._set_active_color(color.name())
        self._hex_edit.setText(color.name().upper())

    def _on_hex_edited(self):
        text = self._hex_edit.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        color = QColor(text)
        if not color.isValid():
            self._hex_edit.setText(self._active_color_hex().upper())
            return
        self._set_active_color(color.name())
        h, s, v, _a = color.getHsvF()
        h = 0.0 if h < 0 else h
        self._hue.blockSignals(True)
        self._hue.setValue(round(h * 359))
        self._hue.blockSignals(False)
        self._sv.set_hue(self._hue.value())
        self._sv.set_sv(s, v)

    def _add_stop(self):
        if len(self._stops) >= 3:
            return
        base = QColor(self._stops[-1])
        h, s, v, _a = base.getHsvF()
        h = 0.0 if h < 0 else h
        new_color = QColor.fromHsvF((h + 0.35) % 1.0, max(s, 0.6), max(v, 0.6))
        self._touch_gradient()
        self._stops.append(new_color.name())
        self._select_stop(len(self._stops) - 1)
        self._live_apply()

    def _remove_stop(self):
        if len(self._stops) <= 2:
            return
        self._touch_gradient()
        del self._stops[self._active]
        self._select_stop(min(self._active, len(self._stops) - 1))
        self._live_apply()

    def _on_direction_changed(self, value):
        self._direction = value
        self._dir_value_label.setText(f"{value}\u00b0")
        self._live_apply()

    def _on_intensity_changed(self, value):
        self._intensity = value
        if value > 0:
            self._stops_set = True
        self._int_value_label.setText(f"{value}%")
        self._live_apply()

    def _on_radius_changed(self, key, value):
        self._radius_labels[key].setText(f"{value} px")
        self._radius_labels[key].setMinimumWidth(QFontMetrics(self._radius_labels[key].font()).horizontalAdvance(self._radius_labels[key].text()) + 10)
        set_radii(**{key: value})
        if not hasattr(self, "_radius_timer"):
            self._radius_timer = QTimer(self)
            self._radius_timer.setSingleShot(True)
            self._radius_timer.setInterval(40)
            self._radius_timer.timeout.connect(self._refresh_radii)
        self._radius_timer.start()
        self._sync_preset_combo()
        self._update_undo_state()
        self._schedule_live_window_preview()

    def _refresh_radii(self):
        win = self.win
        win.apply_theme()
        mini = getattr(win, "_mini_window", None)
        if mini is not None:
            mini.apply_theme()
        for top in (win, mini, self):
            if top is None:
                continue
            for w in top.findChildren(Card):
                w.update()
            for w in top.findChildren(GlassButton):
                w.update()
            for w in top.findChildren(QWidget):
                if w.__class__.__name__ in ("ParamSlider", "TierSlider", "ColorPad2D", "RoundHandleSlider"):
                    if hasattr(w, "_bg_cache_key"):
                        w._bg_cache_key = None
                    w.update()
            top.update()
        self._schedule_blur()
        self._schedule_live_window_preview()

    def _on_effect_changed(self, index):
        self._effect = self._effect_combo.itemData(index)
        self._live_apply()

    def _on_button_swatch_context_menu(self, pos):
        if not self._button_custom:
            return
        menu = QMenu(self)
        reset_action = menu.addAction(self.win.tr("Follow first gradient colour"))
        chosen = menu.exec(self._button_swatch_btn.mapToGlobal(pos))
        if chosen == reset_action:
            self._reset_button_colour()

    def _reset_button_colour(self):
        self._button_custom = False
        self._select_stop(self._active)
        self._live_apply()

    def _on_glass_toggled(self, checked):
        self._glass = False

    def _on_neon_toggled(self, checked):
        self._neon = bool(checked)
        self._follow_default_stops()
        self._live_apply()

    def _on_base_toggled(self, checked):
        self._follow_system_base = False
        self._base = "light" if checked else "dark"
        self._sync_base_segment_buttons()
        self._follow_default_stops()
        self._live_apply()

    def _follow_default_stops(self):
        if not self._stops_set:
            self._stops = self._default_stops()
            self._button_hex = self._stops[0] if not self._button_custom else self._button_hex
            self._select_stop(0)

    def _on_skip_mini_toggled(self, checked):
        self.win.i18n.settings.setValue("skip_mini_window", "true" if checked else "false")

    def _on_detailed_conversion_toggled(self, checked):
        self.win.i18n.settings.setValue("show_detailed_conversion_info", "true" if checked else "false")
        self.win.show_detailed_conversion_info = bool(checked)
        if hasattr(self.win, "_sync_preset_segment_state"):
            self.win._sync_preset_segment_state()

    def _surprise_me(self):
        base_hue = random.randint(0, 359)
        second_hue = (base_hue + random.choice([-140, -110, 110, 140])) % 360
        third_hue = (base_hue + random.choice([-70, -50, 50, 70])) % 360
        self._stops = [
            QColor.fromHsv(base_hue, random.randint(180, 255), random.randint(210, 255)).name(),
            QColor.fromHsv(third_hue, random.randint(180, 255), random.randint(210, 255)).name(),
            QColor.fromHsv(second_hue, random.randint(180, 255), random.randint(210, 255)).name(),
        ]
        self._stops_set = True
        self._direction = random.choice([0, 45, 90, 135, 180, 225, 270, 315])
        self._dir_slider.blockSignals(True)
        self._dir_slider.setValue(self._direction)
        self._dir_slider.blockSignals(False)
        self._dir_value_label.setText(f"{self._direction}\u00b0")
        self._dir_value_label.setMinimumWidth(QFontMetrics(self._dir_value_label.font()).horizontalAdvance(self._dir_value_label.text()) + 10)
        self._set_intensity_ui(random.randint(40, 75))
        self._select_stop(0)
        self._live_apply()

    def _reset(self):
        """Reset to the factory look of the preset this one is based on (Light, Dark or Neon)."""
        self._load_state(builtin_preset("neon" if self._neon else self._base))

    def _load_state(self, d):
        if d.get("base") in BASES:
            self._base = d["base"]
        if "neon_effect" in d:
            self._neon = bool(d["neon_effect"])
        self._glass = False
        if "stops" in d:
            if d["stops"]:
                self._stops = list(d["stops"])
                self._stops_set = True
            else:
                self._stops_set = False
                self._stops = self._default_stops()
        if "direction" in d:
            self._direction = int(d["direction"])
        if "intensity" in d:
            self._intensity = int(d["intensity"])
        if d.get("effect") in NEON_BG_EFFECTS:
            self._effect = d["effect"]
        if "button_custom" in d:
            self._button_custom = bool(d["button_custom"] and d.get("button_hex"))
        self._button_hex = d.get("button_hex") or (self._button_hex if self._button_custom else self._stops[0])
        radii = {}
        if "radius_card" in d:
            radii["card"] = int(d["radius_card"])
        if "radius_button" in d:
            radii["button"] = int(d["radius_button"])
        if radii:
            set_radii(**radii)
        self.tokens = build_tokens(self._build_look())
        self._sync_controls()
        self._select_stop(0)
        self._live_apply()
        if radii:
            self._refresh_radii()

    def _sync_controls(self):
        pairs = (
            (self._glass_toggle, self._glass),
            (self._neon_toggle, self._neon),
            (self._base_toggle, self._base == "light"),
        )
        for tg, value in pairs:
            tg.blockSignals(True)
            tg.setChecked(value)
            tg.blockSignals(False)
        self._sync_base_segment_buttons()
        self._effect_combo.blockSignals(True)
        self._effect_combo.setCurrentIndex(NEON_BG_EFFECTS.index(self._effect))
        self._effect_combo.blockSignals(False)
        self._dir_slider.blockSignals(True)
        self._dir_slider.setValue(self._direction)
        self._dir_slider.blockSignals(False)
        self._dir_value_label.setText(f"{self._direction}\u00b0")
        self._dir_value_label.setMinimumWidth(QFontMetrics(self._dir_value_label.font()).horizontalAdvance(self._dir_value_label.text()) + 10)
        self._set_intensity_ui(self._intensity)
        for key, value in (("card", card_radius()), ("button", button_radius())):
            self._radius_sliders[key].blockSignals(True)
            self._radius_sliders[key].setValue(value)
            self._radius_sliders[key].blockSignals(False)
            self._radius_labels[key].setText(f"{value} px")
        self._radius_labels[key].setMinimumWidth(QFontMetrics(self._radius_labels[key].font()).horizontalAdvance(self._radius_labels[key].text()) + 10)

    # ---- Presets gallery + combo synchronization ---------------------------
    @staticmethod
    def _norm(d):
        out = {}
        flat = ("intensity" in d and d["intensity"] == 0) or ("stops" in d and not d["stops"])
        for key in ("base", "effect", "ui_style"):
            if key in d:
                out[key] = d[key]
        if "neon_effect" in d:
            out["neon_effect"] = bool(d["neon_effect"])
        if "stops" in d:
            out["stops"] = None if flat else tuple(c.lower() for c in d["stops"])
        if "intensity" in d:
            out["intensity"] = 0 if flat else d["intensity"]
        if "direction" in d:
            out["direction"] = 180 if flat else d["direction"]
        if "button_custom" in d:
            out["button"] = (d.get("button_hex") or "").lower() if d["button_custom"] else None
        for key in ("radius_card", "radius_button"):
            if key in d:
                out[key] = d[key]
        return out

    def _current_full(self):
        d = self._build_look()
        d["radius_card"], d["radius_button"] = card_radius(), button_radius()
        return d

    def _refresh_preset_combo(self, preferred_data: str | None = None):
        if preferred_data is None and self._preset_combo.count() > 0:
            preferred_data = self._selected_preset_data or str(
                self._preset_combo.itemData(self._preset_combo.currentIndex()) or ""
            )
        self._user_presets = load_presets(self.win.i18n.settings)
        self._slots = load_slots(self.win.i18n.settings)
        c = self._preset_combo
        c.blockSignals(True)
        c.clear()
        for key, name in self._BUILTIN_NAMES:
            c.addItem(self.win.tr(name), f"b:{key}")
        for i, p in enumerate(self._user_presets):
            c.addItem(p["name"], f"u:{i}")
        c.blockSignals(False)
        self._sync_preset_combo(preferred_data=preferred_data)

    def _is_preset_modified(self) -> bool:
        return not getattr(self, "_preset_exact_match", True)

    def _sync_preset_combo(self, preferred_data: str | None = None):
        cur = self._norm(self._current_full())
        key = "neon" if self._neon else self._base
        index = -1
        fallback = 0
        for i in range(self._preset_combo.count()):
            data = self._preset_combo.itemData(i)
            if data == f"b:{key}":
                fallback = i
            ref = self._preset_for(data)
            if index < 0 and ref is not None and all(cur.get(k) == v for k, v in self._norm(ref).items()):
                index = i

        if preferred_data is not None:
            pref_idx = self._preset_combo.findData(preferred_data)
            if pref_idx >= 0:
                index = pref_idx
                self._selected_preset_data = preferred_data
                ref = self._preset_for(preferred_data)
                exact = bool(ref is not None and all(cur.get(k) == v for k, v in self._norm(ref).items()))
            else:
                exact = index >= 0
                if not exact:
                    index = fallback
                self._selected_preset_data = str(self._preset_combo.itemData(index) or "")
        else:
            exact = index >= 0
            if exact:
                if self._selected_preset_data:
                    sel_idx = self._preset_combo.findData(self._selected_preset_data)
                    if sel_idx >= 0:
                        sel_ref = self._preset_for(self._selected_preset_data)
                        if sel_ref is not None and all(cur.get(k) == v for k, v in self._norm(sel_ref).items()):
                            index = sel_idx
                self._selected_preset_data = str(self._preset_combo.itemData(index) or "")
            else:
                sel_data = str(self._selected_preset_data or "")
                sel_idx = self._preset_combo.findData(sel_data) if sel_data.startswith("u:") else -1
                if sel_idx >= 0:
                    index = sel_idx
                else:
                    index = fallback
                    self._selected_preset_data = str(self._preset_combo.itemData(index) or "")

        self._preset_exact_match = exact
        self._preset_combo.blockSignals(True)
        self._preset_combo.setCurrentIndex(index)
        self._preset_combo.blockSignals(False)
        active_data = str(self._preset_combo.itemData(index) or "")
        self._delete_preset_btn.setEnabled(active_data.startswith("u:"))
        if hasattr(self, "_modified_badge"):
            t = self.tokens
            fg = pick_on_accent(t["accent"])
            self._modified_badge.setStyleSheet(
                f"QLabel#presetModifiedBadge {{ background: {t['accent']}; color: {fg}; "
                f"border-radius: 4px; padding: 1px 6px; font-size: 8pt; font-weight: 600; }}"
            )
            self._modified_badge.setVisible(not exact)
        self._rebuild_preset_gallery()
        self._render_window_preview_label()

    def _rebuild_preset_gallery(self):
        if not hasattr(self, "_preset_gallery_layout"):
            return
        save_btn = getattr(self, "_save_as_new_btn", None)
        while self._preset_gallery_layout.count():
            item = self._preset_gallery_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not save_btn:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._preset_cards = []
        self._preset_cards_by_data = {}
        active_idx = self._preset_combo.currentIndex()
        is_mod = not getattr(self, "_preset_exact_match", True)
        for i in range(self._preset_combo.count()):
            data = str(self._preset_combo.itemData(i) or "")
            title = self._preset_combo.itemText(i)
            p_dict = self._preset_for(data) or self._current_full()
            card = _PresetCard(
                self,
                data,
                title,
                p_dict,
                is_builtin=data.startswith("b:"),
                is_selected=(i == active_idx),
                is_modified=(i == active_idx and is_mod),
                card_idx=i,
            )
            self._preset_cards.append(card)
            self._preset_cards_by_data[data] = card
        if save_btn is not None:
            t = self.tokens
            r = max(6, min(14, int(t.get("radius_card", 12)) - 2))
            save_btn.setText("+  " + _escape_mnemonic(self.win.tr("Save as new")))
            save_btn.setStyleSheet(
                f"QPushButton#saveAsNewPresetCardBtn {{ background: {t['bg_card']}; color: {t['text_secondary']}; "
                f"border: 1.5px dashed {t['border_strong']}; border-radius: {r}px; font-weight: 600; font-size: 9.5pt; padding: 8px; }}"
                f"QPushButton#saveAsNewPresetCardBtn:hover {{ background: {t['bg_card_alt']}; color: {t['accent']}; border-color: {t['accent']}; }}"
            )
            save_btn.hide()
        self._gallery_cols = None
        self._relayout_preset_gallery()

    def _relayout_preset_gallery(self):
        if not hasattr(self, "_preset_gallery_layout") or not getattr(self, "_preset_cards", None):
            return
        max_card_w = max((getattr(c, "preferred_min_width", 168) for c in self._preset_cards), default=168)
        vp_w = self._content_scroll.viewport().width() if hasattr(self, "_content_scroll") else 0
        if vp_w <= 100:
            nav_w = self._nav_card.width() if hasattr(self, "_nav_card") else 144
            pv_w = (self._live_preview_card.width() + 10) if (hasattr(self, "_live_preview_card") and not self._live_preview_card.isHidden()) else 0
            vp_w = max(360, self.width() - nav_w - pv_w - 58)
        if vp_w >= (3 * max_card_w + 24):
            cols = 3
        elif vp_w >= (2 * max_card_w + 18):
            cols = 2
        else:
            cols = 1
        tiles = [*self._preset_cards]
        if getattr(self, "_gallery_cols", None) == cols and self._preset_gallery_layout.count() == len(tiles):
            for c in range(cols):
                self._preset_gallery_layout.setColumnStretch(c, 1)
            for c in range(cols, 4):
                self._preset_gallery_layout.setColumnStretch(c, 0)
            return
        while self._preset_gallery_layout.count():
            self._preset_gallery_layout.takeAt(0)
        self._gallery_cols = cols
        for i, tile in enumerate(tiles):
            self._preset_gallery_layout.addWidget(tile, i // cols, i % cols)
        for c in range(cols):
            self._preset_gallery_layout.setColumnStretch(c, 1)
        for c in range(cols, 4):
            self._preset_gallery_layout.setColumnStretch(c, 0)

    def _preset_for(self, data):
        if not data:
            return None
        kind, _, ref = str(data).partition(":")
        if kind == "b":
            return self._slots.get(ref)
        if kind == "u" and ref.isdigit() and int(ref) < len(self._user_presets):
            return self._user_presets[int(ref)]
        return None

    def _select_preset_by_data(self, data: str):
        for i in range(self._preset_combo.count()):
            if str(self._preset_combo.itemData(i) or "") == data:
                self._preset_combo.setCurrentIndex(i)
                self._on_preset_selected(i)
                return

    def _on_preset_selected(self, index):
        self._slots = load_slots(self.win.i18n.settings)
        data = str(self._preset_combo.itemData(index) or "")
        self._selected_preset_data = data
        preset = self._preset_for(data)
        self._delete_preset_btn.setEnabled(data.startswith("u:"))
        if preset is not None:
            self._load_state(preset)

    def _save_preset_as(self, name: str | None = None):
        return self._save_current_as_preset(name=name)

    def _save_current_as_preset(self, name: str | None = None):
        if name is None:
            dlg = QInputDialog(self)
            dlg.setWindowTitle(self.win.tr("Save preset"))
            dlg.setLabelText(self.win.tr("Preset name:"))
            dlg.setTextValue(self.win.tr("My look"))
            dlg.setStyleSheet(self.styleSheet())
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            name = dlg.textValue()
        name = str(name or "").strip()[:40]
        if not name:
            return
        look = self._current_full()
        preset = clean_preset(
            dict(look, name=name, stops=list(self._stops), intensity=self._intensity if self._stops_set else 0)
        )
        if preset is None:
            return
        presets = [p for p in load_presets(self.win.i18n.settings) if p["name"] != name]
        presets.append(preset)
        save_presets(self.win.i18n.settings, presets)
        new_key = f"u:{len(presets) - 1}"
        self._selected_preset_data = new_key
        self._refresh_preset_combo(preferred_data=new_key)

    def _rename_user_preset(self, user_idx: int | str | None = None, new_name: str | None = None, *, old_name: str | None = None):
        presets = load_presets(self.win.i18n.settings)
        if old_name is not None or isinstance(user_idx, str):
            target_name = old_name if old_name is not None else str(user_idx)
            matched = [i for i, p in enumerate(presets) if p.get("name") == target_name]
            if not matched:
                return
            user_idx = matched[0]
        if user_idx is None or not (0 <= int(user_idx) < len(presets)):
            return
        user_idx = int(user_idx)
        old_name = presets[user_idx]["name"]
        if new_name is None:
            dlg = QInputDialog(self)
            dlg.setWindowTitle(self.win.tr("Rename preset"))
            dlg.setLabelText(self.win.tr("New preset name:"))
            dlg.setTextValue(old_name)
            dlg.setStyleSheet(self.styleSheet())
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            new_name = dlg.textValue()
        new_name = str(new_name or "").strip()[:40]
        if not new_name or new_name == old_name:
            return
        presets[user_idx]["name"] = new_name
        save_presets(self.win.i18n.settings, presets)
        target_key = f"u:{user_idx}"
        self._selected_preset_data = target_key
        self._refresh_preset_combo(preferred_data=target_key)

    def _duplicate_preset(self, data_key: str, new_name: str | None = None):
        src_preset = self._preset_for(data_key)
        resolved_key = str(data_key)
        if src_preset is None:
            for i, p in enumerate(load_presets(self.win.i18n.settings)):
                if p.get("name") == data_key:
                    src_preset = dict(p)
                    resolved_key = f"u:{i}"
                    break
            if src_preset is None and str(data_key).lower() in ("light", "dark", "neon"):
                resolved_key = f"b:{str(data_key).lower()}"
                src_preset = self._preset_for(resolved_key)
        if src_preset is None:
            return
        presets = load_presets(self.win.i18n.settings)
        existing_names = {p["name"] for p in presets}
        base_name = new_name or f"{src_preset.get('name', 'Preset')} (Copy)"
        cand = str(base_name).strip()[:40]
        n = 2
        while cand in existing_names:
            cand = f"{base_name[:34]} {n}"
            n += 1
        stops = src_preset.get("stops") or list(_THEME_DEFAULT_STOPS["neon" if src_preset.get("neon_effect") else src_preset.get("base", "light")])
        dup = clean_preset(dict(src_preset, name=cand, stops=list(stops)))
        if dup is None:
            return
        presets.append(dup)
        save_presets(self.win.i18n.settings, presets)
        keep_key = self._selected_preset_data or resolved_key
        self._refresh_preset_combo(preferred_data=keep_key)

    def _delete_user_preset(self, user_idx: int | str, confirm: bool | None = None):
        presets = load_presets(self.win.i18n.settings)
        if isinstance(user_idx, str):
            matched = [i for i, p in enumerate(presets) if p.get("name") == user_idx]
            if not matched:
                return
            user_idx = matched[0]
            if confirm is None:
                confirm = False
        if confirm is None:
            confirm = QApplication.platformName().lower() != "offscreen"
        if not (0 <= int(user_idx) < len(presets)):
            return
        user_idx = int(user_idx)
        name = presets[user_idx]["name"]
        if confirm:
            box = self.win._themed_message_box(
                QMessageBox.Icon.Question,
                self.win.tr("Delete preset"),
                self.win.tr("Delete the preset \u201c{name}\u201d? This can't be undone.").format(name=name),
                parent=self,
            )
            yes_btn = box.addButton(QMessageBox.StandardButton.Yes)
            box.addButton(QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            box.exec()
            if box.clickedButton() != yes_btn:
                return
        prev_sel = self._selected_preset_data or str(self._preset_combo.itemData(self._preset_combo.currentIndex()) or "")
        del presets[user_idx]
        save_presets(self.win.i18n.settings, presets)
        if prev_sel == f"u:{user_idx}":
            if presets:
                neighbor = min(user_idx, len(presets) - 1)
                next_sel = f"u:{neighbor}"
            else:
                next_sel = f"b:{'neon' if self._neon else self._base}"
        elif prev_sel.startswith("u:"):
            old_i = int(prev_sel.split(":", 1)[1])
            next_sel = f"u:{old_i - 1}" if old_i > user_idx else prev_sel
        else:
            next_sel = prev_sel
        self._selected_preset_data = next_sel
        self._refresh_preset_combo(preferred_data=next_sel)

    def _delete_selected_preset(self):
        data = str(self._preset_combo.itemData(self._preset_combo.currentIndex()) or "")
        if not data.startswith("u:"):
            return
        idx = int(data.split(":", 1)[1])
        self._delete_user_preset(idx, confirm=True)

    def _export_presets_json(self, file_path: str | Path | None = None) -> int:
        if file_path is None:
            file_path, _ = QFileDialog.getSaveFileName(
                self, self.win.tr("Export JSON"), "hdr2sdr_presets.json", "JSON (*.json)"
            )
        if not file_path:
            return 0
        presets = load_presets(self.win.i18n.settings)
        try:
            Path(file_path).write_text(json.dumps(presets, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return len(presets)
        except OSError:
            return 0

    def _import_presets_json(self, file_path: str | Path | None = None) -> int:
        if file_path is None:
            file_path, _ = QFileDialog.getOpenFileName(
                self, self.win.tr("Import JSON"), "", "JSON (*.json)"
            )
        if not file_path:
            return 0
        try:
            raw = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return 0
        items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        presets = load_presets(self.win.i18n.settings)
        by_name = {p["name"]: i for i, p in enumerate(presets)}
        added = 0
        for item in items:
            cp = clean_preset(item)
            if cp is None:
                continue
            if cp["name"] in by_name:
                presets[by_name[cp["name"]]] = cp
            else:
                by_name[cp["name"]] = len(presets)
                presets.append(cp)
            added += 1
        if added:
            save_presets(self.win.i18n.settings, presets)
            self._refresh_preset_combo()
        return added

    # ---- Eyedropper ---------------------------------------------------------
    def _start_eyedrop(self):
        screens = QApplication.screens()
        if not screens:
            return
        snapshots = [(screen.geometry(), screen.grabWindow(0).toImage()) for screen in screens]
        overlay = _EyedropperOverlay(snapshots, self)
        overlay.picked.connect(self._on_eyedrop_picked)
        overlay.start()

    def _on_eyedrop_picked(self, hex_color):
        self._hex_edit.setText(hex_color.upper())
        self._on_hex_edited()

    def _update_eyedrop_icon(self, text=None):
        c = QColor((text if text is not None else self._hex_edit.text()).strip())
        if c.isValid():
            self._eyedrop_btn.setIcon(_pipette_icon(c.name(), 36))

    # ---- Live preview / Revert / Save / Close confirmation ------------------
    def _live_apply(self):
        look = self._build_look()
        self.win._set_look(look, persist=False)
        self.tokens = build_tokens(look)
        self.theme_name = palette_name(look)
        self.ui_style = look["ui_style"]
        self.neon_effect = look["neon_effect"]
        self._restyle()
        self._theme_popups()
        self.update()
        self._schedule_blur()
        self._sync_preset_combo()
        self._update_undo_state()
        self._schedule_live_window_preview()

    def _restore_original(self):
        """Restore look, radii (set_radii), and window tokens to exact pre-open values."""
        set_radii(card=self._orig_radii[0], button=self._orig_radii[1])
        self.win._set_look(copy_look(self._orig_look), persist=False)
        self.win.tokens = build_tokens(
            self.win.look, radius_card=self._orig_radii[0], radius_button=self._orig_radii[1]
        )
        self._refresh_radii()

    def is_dirty(self) -> bool:
        return self._is_dirty()

    def _is_dirty(self):
        cur = self._norm(self._current_full())
        orig = dict(self._orig_look, radius_card=self._orig_radii[0], radius_button=self._orig_radii[1])
        return cur != self._norm(orig)

    def _update_undo_state(self):
        dirty = self._is_dirty()
        btn = getattr(self, "_undo_btn", None)
        if btn is not None:
            btn.setEnabled(dirty)
        status_lbl = getattr(self, "_dirty_status_label", None)
        if status_lbl is not None:
            status_lbl.setObjectName("dirtyStatusPill")
            status_lbl.setText(self.win.tr("Unsaved changes" if dirty else "No unsaved changes"))
            set_role(status_lbl, "warn" if dirty else "muted")
            t = self.tokens
            fg = t["warning"] if dirty else t["text_secondary"]
            status_lbl.setStyleSheet(
                f"QLabel#dirtyStatusPill {{ background: transparent; color: {fg}; "
                f"border: none; font-size: 9pt; padding: 4px 2px; }}"
            )

    def _revert_to_initial(self):
        self._revert_to_applied()

    def _revert_to_applied(self):
        """Revert changes to the look and rounding from when the dialog opened; stays open."""
        set_radii(card=self._orig_radii[0], button=self._orig_radii[1])
        self._load_state(
            dict(
                copy_look(self._orig_look),
                radius_card=self._orig_radii[0],
                radius_button=self._orig_radii[1],
            )
        )
        self.win.tokens = build_tokens(
            self.win.look, radius_card=self._orig_radii[0], radius_button=self._orig_radii[1]
        )
        self._update_live_window_preview()

    def _apply(self):
        win = self.win
        self._live_apply()
        save_look(win.i18n.settings, win.look)
        save_slot(win.i18n.settings, win.look)
        win.i18n.settings.setValue("radius_card", card_radius())
        win.i18n.settings.setValue("radius_button", button_radius())
        self._orig_look = copy_look(win.look)
        self._orig_radii = (card_radius(), button_radius())
        self._applied_snapshot_pixmap = self._capture_win_pixmap()
        self._closing_confirmed = True
        self._update_undo_state()
        self._detach_tools_refs()
        self.accept()

    def _prompt_unsaved_close(self) -> str:
        """Prompt user with Save / Discard / Cancel when closing with unsaved changes.
        Returns 'save', 'discard', or 'cancel'."""
        self._last_unsaved_close_prompt = ("save", "discard", "cancel")
        if callable(self._unsaved_close_handler):
            try:
                res = self._unsaved_close_handler(self)
            except TypeError:
                res = self._unsaved_close_handler()
            return str(res or "discard").lower()
        is_offscreen = QApplication.platformName().lower() == "offscreen"
        if is_offscreen and not self._confirm_on_close_in_offscreen:
            return "discard"
        tr = self.win.tr
        box = self.win._themed_message_box(
            QMessageBox.Icon.Question,
            tr("Unsaved appearance changes"),
            tr("You have unsaved appearance changes. Save them before closing?"),
            parent=self,
        )
        save_b = box.addButton(tr("Save"), QMessageBox.ButtonRole.AcceptRole)
        discard_b = box.addButton(tr("Discard"), QMessageBox.ButtonRole.DestructiveRole)
        cancel_b = box.addButton(tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_b)
        self._active_close_box = box
        box.exec()
        clicked = box.clickedButton()
        self._active_close_box = None
        if clicked == save_b:
            return "save"
        if clicked == discard_b:
            return "discard"
        if clicked == cancel_b:
            return "cancel"
        return "cancel"

    def _request_close(self):
        if not self._is_dirty():
            self._closing_confirmed = True
            self._restore_original()
            self._detach_tools_refs()
            super().reject()
            return
        choice = self._prompt_unsaved_close()
        if choice == "save":
            self._apply()
        elif choice == "discard":
            self._closing_confirmed = True
            self._restore_original()
            self._detach_tools_refs()
            super().reject()
        else:
            # Cancel: stay open with draft intact
            return

    def reject(self):
        if getattr(self, "_closing_confirmed", False) or not self._is_dirty():
            self._closing_confirmed = False
            self._restore_original()
            self._detach_tools_refs()
            super().reject()
            return
        self._request_close()

    def closeEvent(self, event):
        if getattr(self, "_closing_confirmed", False) or not self._is_dirty():
            self._closing_confirmed = False
            self._restore_original()
            self._detach_tools_refs()
            event.accept()
            return
        choice = self._prompt_unsaved_close()
        if choice == "save":
            self._apply()
            event.accept()
        elif choice == "discard":
            self._closing_confirmed = True
            self._restore_original()
            self._detach_tools_refs()
            event.accept()
            super().reject()
        else:
            event.ignore()
