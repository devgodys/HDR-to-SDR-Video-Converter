from __future__ import annotations

import time

from PySide6.QtCore import (
    Qt, QRectF, QRect, QPoint, QEvent, Signal, QSize,
)
from PySide6.QtGui import (
    QColor, QFontMetrics, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QWidget, QComboBox, QVBoxLayout, QListWidget, QListWidgetItem, QProxyStyle, QStyle,
)

from hdr2sdr.theme.theme_tokens import LIGHT_BASE_TOKENS, ON_ACCENT_DARK, ON_ACCENT_LIGHT, pick_on_accent
from hdr2sdr.ui.widgets.buttons import GlassButton




class NoMenuStylePopupStyle(QProxyStyle):
    """QComboBox popups default to Qt's "menu style" list (SH_ComboBox_Popup),
    which shows a scrollable list via its own up/down scroller buttons instead
    of a normal QScrollBar when the list doesn't fit the screen. Those
    scroller buttons are painted straight through QStyle primitives rather
    than as a styleable child widget/class, so no QSS selector can reach them
    - they always show up in the platform's default (opaque light) colour
    regardless of theme. Turning this style hint off makes Qt fall back to
    an ordinary list with a normal, themeable QScrollBar instead."""
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0
        return super().styleHint(hint, option, widget, returnData)


class RoundedComboPopup(QWidget):
    """Stand-in for QComboBox's native popup (an internal
    QComboBoxPrivateContainer), used by RoundedComboBox below instead of
    the real one - because the native one can't be made to have rounded
    corners. border-radius in QSS only clips what a widget's own
    paintEvent draws *inside* the rounded path; the square corners
    outside it still need to be transparent or they show as solid
    wedges (same issue Card's WA_TranslucentBackground already solves -
    see that class). That fix doesn't reach the native combo popup
    because it's a platform-specific internal class we don't own the
    paintEvent of; setting WA_TranslucentBackground directly on it was
    tried and made it render as an opaque solid-black box instead
    (that window apparently isn't set up for real alpha compositing the
    way an ordinary in-tree widget is). This is a plain QWidget instead,
    fully our own paintEvent, so the exact same technique that works for
    Card works here too."""
    RADIUS = 10
    ROW_HEIGHT = 34
    MAX_HEIGHT = 320

    def __init__(self, combo):
        # An overlay *inside* the owning window, not a separate top-level
        # Qt::Popup window. As a real OS window it was wrapped by Windows
        # in its own shadow/rounded-corner/border chrome, which showed up
        # on screen as a second outline around the dropdown ("a window
        # inside a window") no matter what this widget painted or what
        # DWM attributes were requested - the outline belongs to the OS
        # window, not to us. A child widget has no OS window, so there is
        # nothing for Windows to decorate. Outside-click / Escape /
        # window-deactivate dismissal, which Qt::Popup used to provide for
        # free, is done by the application event filter below.
        super().__init__(combo.window())
        self.combo = combo
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.hide()
        self._bg = QColor(LIGHT_BASE_TOKENS["bg_card"])
        self._border = QColor(LIGHT_BASE_TOKENS["border"])
        outer = QVBoxLayout(self)
        # 10px, matching RADIUS below - not the 5px this started as. A
        # child's own corner needs to be at least RADIUS in from each
        # edge, or its square corner sits inside the curved sliver near
        # our corner instead of behind the straight part of it, and pokes
        # past the rounded silhouette (this is what the reported
        # "triangles, worse at the bottom, only on the right where the
        # scrollbar sits" bug actually was). A pixel mask was tried
        # instead of this - clipping every pixel painted here, us and our
        # children both, to the exact rounded shape, which is the more
        # bulletproof fix in principle - but building that mask from
        # QPainterPath.toFillPolygon().toPolygon() rounds the smooth
        # curve to whole pixels, and on a real Windows compositor that
        # produced its own new (and stranger) single-pixel seam artifact
        # right at the corner instead. This margin approach is a plainer
        # fix for the same problem - keep every square-cornered child far
        # enough from a corner that there's nothing there to clip in the
        # first place - and doesn't touch window-level masking at all, so
        # it can't have that class of side effect.
        outer.setContentsMargins(self.RADIUS, self.RADIUS, self.RADIUS, self.RADIUS)
        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        # Belt-and-suspenders alongside the width calc in open_below():
        # never show a horizontal scrollbar even if that calc is ever off
        # for some item - it's a much uglier failure mode than just
        # letting Qt elide the odd overlong entry with "...".
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        # role="combo-popup-list" so this doesn't pick up the app-wide
        # QListWidget{...} rule (used by things like the history dialog's
        # list) meant for a list that draws its own visible card - this
        # one needs to stay fully transparent since paintEvent below
        # already draws the one and only rounded background/border for
        # the whole popup; a second one from the list itself would show
        # as a redundant nested box inside it.
        self.list.setProperty("role", "combo-popup-list")
        outer.addWidget(self.list)
        self.list.itemClicked.connect(self._pick)
        self.list.itemActivated.connect(self._pick)
        # apply_theme() calls set_theme() below with real theme colours
        # before this ever gets shown for the first time; these are just
        # a safe fallback in case it's somehow opened before that.
        self.set_theme(
            LIGHT_BASE_TOKENS["bg_card"],
            LIGHT_BASE_TOKENS["border"],
            LIGHT_BASE_TOKENS["text"],
            LIGHT_BASE_TOKENS["bg_window"],
            LIGHT_BASE_TOKENS["accent"],
        )

    def set_theme(self, bg, border, txt, hover, selected):
        self._bg = QColor(bg)
        self._border = QColor(border)
        # White text is unreadable on a pale accent (custom themes can pick
        # one); choose by the accent's perceived luminance instead.
        sel_txt = pick_on_accent(QColor(selected).name())
        self.list.setStyleSheet(f"""
            QListWidget[role="combo-popup-list"] {{
                background: transparent; border: none; padding: 0px;
                outline: none; color: {txt}; font-size: 10pt;
            }}
            QListWidget[role="combo-popup-list"]::item {{
                padding: 7px 10px; border-radius: 6px; min-height: 20px;
                background: transparent;
            }}
            QListWidget[role="combo-popup-list"]::item:focus {{
                background: transparent; border: none; outline: none;
            }}
            QListWidget[role="combo-popup-list"]::item:hover {{ background: {hover}; }}
            QListWidget[role="combo-popup-list"]::item:selected {{ background: {selected}; color: {sel_txt}; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px 4px 0px; border: none; }}
            QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; border: none; background: transparent; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)

    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            try:
                gp = event.globalPosition().toPoint()
            except Exception:
                return False
            inside = QRect(self.mapToGlobal(QPoint(0, 0)), self.size()).contains(gp)
            if not inside:
                # Don't swallow the click: it still has to reach whatever was
                # clicked (and RoundedComboBox.mousePressEvent uses the
                # close-timestamp this stamps to tell "this click just
                # closed the popup" from "open it again").
                self.hide()
        elif et == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.hide()
            return True
        elif obj is self.window() and et in (QEvent.Type.WindowDeactivate, QEvent.Type.Resize,
                                             QEvent.Type.Move):
            self.hide()
        return False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.fillPath(path, self._bg)
        p.setPen(QPen(self._border, 1))
        p.drawPath(path)

    def _pick(self, item):
        self.combo.setCurrentIndex(self.list.row(item))
        if hasattr(self.combo, "activated"):          # RoundedComboBox only (RoundedMenuButton has its own signal)
            self.combo.activated.emit(self.list.row(item))
        self.hide()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.list.currentItem()
            if item is not None:
                self._pick(item)
            return
        # Up/Down/Home/End/PageUp/PageDown navigation and letter-key
        # type-ahead search are all QAbstractItemView built-ins - just
        # hand the event to the real list instead of reimplementing them.
        self.list.keyPressEvent(event)

    def hideEvent(self, event):
        super().hideEvent(event)
        # Covers every way this can close - a pick, Escape, or Qt::Popup's
        # own automatic outside-click dismissal - since hideEvent fires
        # regardless of which one triggered it.
        self.combo.setProperty("popupOpen", False)
        self.combo.style().unpolish(self.combo)
        self.combo.style().polish(self.combo)
        # Stamp the moment this closed - see the mousePressEvent guards on
        # RoundedComboBox/RoundedMenuButton below for what this is for.
        self.combo._popup_closed_at = time.monotonic()
        QApplication.instance().removeEventFilter(self)

    def open_below(self):
        self.list.clear()
        fm = self.list.fontMetrics()
        content_w = 0
        for i in range(self.combo.count()):
            text = (
                self.combo.display_item_text(i)
                if hasattr(self.combo, "display_item_text")
                else self.combo.itemText(i)
            )
            self.list.addItem(QListWidgetItem(text))
            content_w = max(content_w, fm.horizontalAdvance(text))
        self.list.setCurrentRow(self.combo.currentIndex())

        # Popup width has to fit the *widest item's full text*, not just
        # combo.width() - loosen_combo() deliberately narrows the combo
        # itself (eliding its own current-selection display with "...")
        # so long entries don't force the whole panel wider, but that same
        # narrow width applied to the popup just moved the eliding problem
        # into the dropdown instead, as a horizontal scrollbar cutting off
        # every item's text (seen with HDR tone mapping methods like
        # "ST2094-40 (HDR10+) . GPU tonemap"). Padding below matches the
        # item padding (10px each side) + a little room for the scrollbar
        # + the popup's own outer margins on each side (self.RADIUS now,
        # see __init__).
        item_w = content_w + 20 + 16 + self.RADIUS * 2
        w = max(self.combo.width(), min(item_w, 480), 220)
        # +4 beyond the top/bottom margins (self.RADIUS each, see
        # __init__) is a little slack for QVBoxLayout's own spacing -
        # matches the +14 this was (5px old margin * 2 + 4) before that
        # margin became RADIUS.
        # Use the row height Qt actually computes for this list (QSS padding
        # + font metrics + DPI scale) instead of the fixed ROW_HEIGHT guess:
        # with fractional Windows scaling (125%/150%) or a different font the
        # real row can differ from 34px, which made the popup come out
        # slightly too tall (empty strip, covers more of the fields below) or
        # too short (clipped rows / scrollbar) from one open to the next.
        row_h = self.list.sizeHintForRow(0)
        if row_h <= 0:
            row_h = self.ROW_HEIGHT
        # Height is a whole number of rows plus the outer margins, with no
        # extra slack: the old "+4" left a visible empty strip under the
        # last row. For long lists the visible row count is capped to a
        # whole number of rows (a viewport that isn't a multiple of the row
        # height leaves a blank gap below the last row once the list is
        # scrolled to the end, because QListWidget scrolls per item by
        # default) and scrolling is per pixel so the end is always flush.
        max_rows = max(1, (self.MAX_HEIGHT - self.RADIUS * 2) // row_h)
        visible_rows = min(max(1, self.combo.count()), max_rows)
        h = visible_rows * row_h + self.RADIUS * 2
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        win = self.combo.window()
        if self.parentWidget() is not win:
            self.setParent(win)
        bounds = win.rect()
        margin = 6
        w = min(w, max(1, bounds.width() - 2 * margin))
        self.resize(w, h)
        x = self.combo.mapTo(win, QPoint(0, 0)).x()
        if getattr(self.combo, "popup_align_right", False):
            # Header buttons sit at the window's right edge - line the
            # popup's right edge up with the button's.
            x = self.combo.mapTo(win, QPoint(self.combo.width(), 0)).x() - w
        y = self.combo.mapTo(win, QPoint(0, self.combo.height() + 4)).y()
        if y + h > bounds.bottom() - margin:
            above = self.combo.mapTo(win, QPoint(0, 0)).y() - h - 4
            y = above if above >= margin else max(margin, bounds.bottom() - margin - h)
        x = max(margin, min(x, bounds.right() - margin - w))
        self.move(x, y)

        self.combo.setProperty("popupOpen", True)
        self.combo.style().unpolish(self.combo)
        self.combo.style().polish(self.combo)

        self.show()
        self.raise_()
        self.list.setFocus()
        self.list.scrollToItem(self.list.currentItem())


class RoundedComboBox(QComboBox):
    """Drop-in QComboBox replacement that shows a RoundedComboPopup
    instead of the native popup - see that class's docstring for why.
    Everything else (addItem/currentIndex/currentTextChanged/loosen_combo/
    etc.) is untouched, ordinary QComboBox behaviour; only how the popup
    itself is shown changes. apply_theme() calls set_popup_theme() on
    every one of these (via findChildren) the same way it themes
    everything else."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._popup = None
        self._popup_colors = None
        self._display_prefix: str = ""
        self._item_translator = None

    def set_display_prefix(self, prefix: str = "", item_translator=None) -> None:
        """Display ``<prefix><item>`` inside both the closed chip and dropdown popup
        (e.g. ``Denoise: Off``, ``Denoise: Light``, ``Denoise: Medium``) while keeping
        ``currentText()`` / ``itemText(i)`` unchanged for backend command builders."""
        self._display_prefix = str(prefix or "")
        self._item_translator = item_translator
        self.updateGeometry()
        self.update()

    def display_item_text(self, index: int) -> str:
        raw = self.itemText(index)
        if callable(self._item_translator) and raw:
            try:
                raw = str(self._item_translator(raw))
            except Exception:
                pass
        return f"{self._display_prefix}{raw}" if self._display_prefix else raw

    def display_current_text(self) -> str:
        idx = self.currentIndex()
        if 0 <= idx < self.count():
            return self.display_item_text(idx)
        raw = self.currentText()
        if callable(self._item_translator) and raw:
            try:
                raw = str(self._item_translator(raw))
            except Exception:
                pass
        return f"{self._display_prefix}{raw}" if self._display_prefix else raw

    def initStyleOption(self, option):
        super().initStyleOption(option)
        if self._display_prefix or callable(self._item_translator):
            option.currentText = self.display_current_text()

    def findText(self, text, flags=Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchCaseSensitive) -> int:
        idx = super().findText(text, flags)
        if idx >= 0 or not self._display_prefix:
            return idx
        s = str(text or "")
        if s.lower().startswith(self._display_prefix.lower()):
            stripped = s[len(self._display_prefix):].strip()
            idx = super().findText(stripped, flags)
            if idx >= 0:
                return idx
        for i in range(self.count()):
            if self.display_item_text(i).lower() == s.lower() or self.itemText(i).lower() == s.lower():
                return i
        return -1

    def setCurrentText(self, text: str) -> None:
        if self._display_prefix:
            idx = self.findText(text)
            if idx >= 0:
                self.setCurrentIndex(idx)
                return
        super().setCurrentText(text)

    def sizeHint(self) -> QSize:
        sh = super().sizeHint()
        if not self._display_prefix and not callable(self._item_translator):
            return sh
        fm = QFontMetrics(self.font())
        max_w = max((fm.horizontalAdvance(self.display_item_text(i)) for i in range(self.count())), default=0)
        return QSize(max(sh.width(), min(max_w + 26, 118)), sh.height())

    def minimumSizeHint(self) -> QSize:
        msh = super().minimumSizeHint()
        if not self._display_prefix and not callable(self._item_translator):
            return msh
        fm = QFontMetrics(self.font())
        cur_w = fm.horizontalAdvance(self.display_current_text()) if self.count() else 0
        return QSize(max(msh.width(), min(cur_w + 22, 102)), msh.height())

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        win = self.window()
        if getattr(win, "chevron_icon", None) is not None:
            return
        from PySide6.QtCore import QPointF
        from hdr2sdr.theme.theme_tokens import window_tokens
        t = window_tokens(win)
        col = QColor(t["text_secondary"] if self.isEnabled() else t["border"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(col, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx = self.width() - 13.0
        cy = self.height() / 2.0
        p.drawPolyline([QPointF(cx - 4.0, cy - 1.5), QPointF(cx, cy + 2.5), QPointF(cx + 4.0, cy - 1.5)])
        p.end()

    def set_popup_theme(self, bg, border, txt, hover, selected):
        self._popup_colors = (bg, border, txt, hover, selected)
        if self._popup is not None:
            self._popup.set_theme(*self._popup_colors)

    def showPopup(self):
        if self._popup is None:
            self._popup = RoundedComboPopup(self)
            if self._popup_colors is not None:
                self._popup.set_theme(*self._popup_colors)
        self._popup.open_below()

    def hidePopup(self):
        # Deliberately doesn't call super().hidePopup() - the native popup
        # this would otherwise hide was never shown in the first place, so
        # there's nothing there for it to do, and no code in this app ever
        # calls .hidePopup() directly (checked); RoundedComboPopup closes
        # itself (see _pick/keyPressEvent/Qt::Popup's own outside-click
        # dismissal).
        if self._popup is not None:
            self._popup.hide()

    def mousePressEvent(self, event):
        # QComboBox's own mousePressEvent unconditionally calls
        # showPopup() on a click - it has no idea a popup is already
        # open, since RoundedComboPopup isn't the native popup it
        # tracks internally (that's exactly why showPopup()/hidePopup()
        # above are overridden in the first place). Left unhandled, a
        # second click on the box while the dropdown was already open
        # closed and immediately reopened it within that same click
        # instead of toggling it shut - this is what actually makes
        # that a toggle.
        if self._popup is not None and self._popup.isVisible():
            self._popup.hide()
            return
        # The isVisible() check above only catches a popup that's *still*
        # open at the moment of this press. But RoundedComboPopup is a
        # Qt::Popup, and Qt::Popup's own outside-click dismissal fires
        # while the press is being delivered, *before* it reaches this
        # widget's mousePressEvent - so by the time this runs, a click
        # meant to just close the popup has already closed it, isVisible()
        # is already False, and the guard above does nothing; execution
        # falls through to super().mousePressEvent(), which calls
        # showPopup() and reopens it in the same click ("click to close
        # instead re-closes-then-reopens" bug). Guard against that second
        # case with a short time window instead of a visibility check:
        # if the popup closed within the last instant, this press is the
        # one that closed it, not a fresh request to open it again.
        just_closed = getattr(self, "_popup_closed_at", None)
        if just_closed is not None and (time.monotonic() - just_closed) < 0.2:
            return
        super().mousePressEvent(event)


class RoundedMenuButton(GlassButton):
    """Icon-only header button that opens the same RoundedComboPopup the
    RoundedComboBox pickers use (rounded corners, themed list, themed
    scrollbar) instead of a native QMenu - used for the interface-language
    picker, so it matches the encoder/backend dropdowns rather than being
    the one plain OS-style menu left in the app.

    Subclasses GlassButton (not QToolButton) purely so this button paints
    itself with the exact same routine as every other button in the app
    (Add videos, Remove, Output, ...) - square instead of pill-shaped
    (radius=8.0, same as those), rather than a second, QSS-driven paint
    path of its own. QSS border-radius on a small (34x38) QToolButton
    turned out to still show visibly stair-stepped corners on real
    Windows even after antialiasing the *mask* painted on top of it (an
    earlier fix, since removed - it rendered the QSS background into an
    offscreen buffer and cropped that with an antialiased rounded-rect
    mask) - the underlying QSS-painted fill itself was what's aliased,
    not just its edge, so re-cropping an already-aliased fill with a
    clean mask didn't retroactively smooth it. GlassButton
    sidesteps the whole question: in glass mode its background is 100%
    hand-painted (no QSS involved at all), which is the actual reason
    the app's other buttons render cleanly.

    RoundedComboPopup only ever talks to its owner through a handful of
    QComboBox-shaped calls (count/itemText/currentIndex/setCurrentIndex
    plus ordinary QWidget geometry), so this implements just those over a
    plain list of labels; picking a row emits activatedIndex(int) rather
    than changing any selection state of its own beyond remembering it
    for the next open (which pre-highlights the current row)."""
    activatedIndex = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._current = -1
        self._popup = None
        self._popup_colors = None
        self.popup_align_right = True
        self.clicked.connect(self.showPopup)

    def minimumSizeHint(self) -> QSize:
        return QSize(60, 32)

    # ---- QComboBox-shaped surface RoundedComboPopup relies on --------
    def set_items(self, texts, current=-1):
        self._items = list(texts)
        self._current = current

    def count(self):
        return len(self._items)

    def itemText(self, i):
        return self._items[i]

    def currentIndex(self):
        return self._current

    def setCurrentIndex(self, i):
        self._current = i
        self.activatedIndex.emit(i)

    # ---- popup plumbing (same shape as RoundedComboBox's) -------------
    def set_popup_theme(self, bg, border, txt, hover, selected):
        self._popup_colors = (bg, border, txt, hover, selected)
        if self._popup is not None:
            self._popup.set_theme(*self._popup_colors)

    def showPopup(self):
        if self._popup is None:
            self._popup = RoundedComboPopup(self)
            if self._popup_colors is not None:
                self._popup.set_theme(*self._popup_colors)
        self._popup.open_below()

    def hidePopup(self):
        if self._popup is not None:
            self._popup.hide()

    def mousePressEvent(self, event):
        # Same toggle-shut-on-second-click guard as RoundedComboBox,
        # including the same Qt::Popup timing gap (see the long comment
        # in RoundedComboBox.mousePressEvent above for why both checks
        # are needed).
        if self._popup is not None and self._popup.isVisible():
            self._popup.hide()
            event.accept()
            return
        just_closed = getattr(self, "_popup_closed_at", None)
        if just_closed is not None and (time.monotonic() - just_closed) < 0.2:
            event.accept()
            return
        super().mousePressEvent(event)
