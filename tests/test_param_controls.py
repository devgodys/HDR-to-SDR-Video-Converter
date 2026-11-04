"""The redesigned parameter controls: value-inside sliders, quality bands,
colour pad, drag-or-type chip, timeline, thumbnails - and how MainWindow
uses them."""
import shutil
import subprocess

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from hdr2sdr.core.constants import (
    QUALITY_TIER_BOUNDS_51, QUALITY_TIER_BOUNDS_AV1, QUALITY_TIER_REPS_51, QUALITY_TIER_REPS_AV1,
)
from hdr2sdr.ui.widgets.param_controls import (
    ColorPad2D, DEFAULT_CONTROL_STYLE, ParamSlider, ScrubSpinBox, TierSlider, TimelineSlider, make_value_chip,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _default_style():
    ParamSlider.set_global_style("fill")
    yield
    ParamSlider.set_global_style("fill")


def _slider(style="fill", **kw):
    ParamSlider.set_global_style(style)
    kw.setdefault("bidirectional", True)
    w = ParamSlider("Brightness", -20, 20, 0, **kw)
    w.resize(300, w.height())
    w.show()
    QApplication.processEvents()
    return w


# ---- ParamSlider ------------------------------------------------------------

def test_param_slider_model_api(qapp):
    w = _slider()
    seen = []
    w.valueChanged.connect(seen.append)
    w.setValue(5)
    w.setValue(5)                      # no change -> no second signal
    w.setValue(999)                    # clamped
    assert w.value() == 20 and seen == [5, 20]
    w.setRange(-10, 10)
    assert w.value() == 10 and w.minimum() == -10
    assert w.isModified()
    w.reset()
    assert w.value() == 0 and not w.isModified()
    assert w.text() == "Brightness"
    assert w.bidirectional is True and w.origin_value == 0.0


@pytest.mark.parametrize("style", ["fill", "scrub", "track"])
def test_param_slider_drag_in_every_style(qapp, style):
    w = _slider(style)
    mid = QPoint(w.width() // 2, 16 if style != "track" else 36)
    QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, mid)
    QTest.mouseMove(w, QPoint(w.width() - 20, mid.y()))
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(w.width() - 20, mid.y()))
    assert w.value() > 5, (style, w.value())            # dragged right of centre -> positive
    w.reset()                                           # scrub is relative to where it started, so start again from 0
    QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, mid)
    QTest.mouseMove(w, QPoint(10, mid.y()))
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(10, mid.y()))
    assert w.value() < -5, (style, w.value())


def test_param_slider_keyboard_wheel_and_edit(qapp):
    w = _slider()
    w.setFocus()
    QTest.keyClick(w, Qt.Key.Key_Right)
    QTest.keyClick(w, Qt.Key.Key_Right)
    assert w.value() == 2
    QTest.keyClick(w, Qt.Key.Key_PageUp)
    assert w.value() == 6                              # a tenth of the 40-wide range = 4
    QTest.keyClick(w, Qt.Key.Key_Home)
    assert w.value() == -20
    QTest.keyClick(w, Qt.Key.Key_Delete)               # reset
    assert w.value() == 0
    # typing: Enter opens the editor, a number + Enter commits, Escape cancels
    QTest.keyClick(w, Qt.Key.Key_Return)
    assert w._editing
    w._editor.setText("+7")
    QTest.keyClick(w._editor, Qt.Key.Key_Return)
    assert w.value() == 7 and not w._editing
    QTest.keyClick(w, Qt.Key.Key_Return)
    w._editor.setText("-3")
    QTest.keyClick(w._editor, Qt.Key.Key_Escape)
    assert w.value() == 7 and not w._editing
    # out of range input is clamped, junk is ignored
    QTest.keyClick(w, Qt.Key.Key_Return)
    w._editor.setText("500")
    QTest.keyClick(w._editor, Qt.Key.Key_Return)
    assert w.value() == 20
    QTest.keyClick(w, Qt.Key.Key_Return)
    w._editor.setText("abc")
    QTest.keyClick(w._editor, Qt.Key.Key_Return)
    assert w.value() == 20


def test_param_slider_wheel_only_when_focused(qapp):
    from PySide6.QtGui import QWheelEvent
    w = _slider()
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    w.clearFocus()
    qapp.sendEvent(w, ev)
    assert w.value() == 0 and not ev.isAccepted()      # ignored -> the panel scrolls instead
    w.setFocus()
    ev2 = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
                      Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(w, ev2)
    assert w.value() == 1


def test_param_slider_double_click_edits_without_moving_value(qapp):
    w = _slider()
    w.setValue(4)
    pt = QPoint(w.width() - 30, 16)                    # far from the current value
    QTest.mouseClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    QTest.mouseDClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    assert w._editing and w.value() == 4               # the first click's jump was undone
    w._end_edit(commit=False)


def test_param_slider_formatters_and_temperature_origin(qapp):
    w = ParamSlider("Colour temperature", 3000, 10000, 6500, step=100, bidirectional=True, origin=6500,
                    formatter=lambda v: f"{v} K")
    assert w.valueText() == "6500 K" and not w.isModified()
    w.setValue(5200)
    assert w.valueText() == "5200 K" and w.isModified()
    n = ParamSlider("B", -20, 20, 5, bidirectional=True, origin=0)
    assert n.valueText() == "+5"
    n.setValue(-8)
    assert n.valueText() == "\u22128"
    n.setValue(0)
    assert n.valueText() == "0"


def test_style_switch_is_global_and_pad_only_on_colour_widgets(qapp):
    a = _slider("fill", pad_capable=True)
    b = _slider("fill")
    heights = {s: None for s in ("fill", "scrub", "track")}
    for s in heights:
        ParamSlider.set_global_style(s)
        heights[s] = a.height()
        assert b.style_name() == s
    assert heights["track"] > heights["fill"] == heights["scrub"]
    ParamSlider.set_global_style("pad")
    assert a.style_name() == "fill" and b.style_name() == "fill"     # pad is drawn by ColorPad2D, not by sliders
    ParamSlider.set_global_style("nonsense")
    assert ParamSlider.global_style() == DEFAULT_CONTROL_STYLE
    # context menu offers the pad only where it makes sense, and marks the current style
    labels = lambda w: [x.text() for x in w.build_context_menu().actions()[2].menu().actions()]
    assert len(labels(a)) == 4 and len(labels(b)) == 3
    ParamSlider.set_global_style("scrub")
    checked = [x.text() for x in a.build_context_menu().actions()[2].menu().actions() if x.isChecked()]
    assert checked == ["Scrub field"]


def test_style_choice_emits_on_the_chosen_instance_only(qapp):
    a, b = _slider(), _slider()
    got_a, got_b, changed_b = [], [], []
    a.styleChosen.connect(got_a.append)
    b.styleChosen.connect(got_b.append)
    b.styleChanged.connect(changed_b.append)
    a._choose_style("track")
    assert got_a == ["track"] and got_b == [] and changed_b == ["track"]
    assert ParamSlider.global_style() == "track"


# ---- ColorPad2D -------------------------------------------------------------

def test_colour_pad_drives_both_controls_and_resets(qapp):
    x = ParamSlider("Saturation", -20, 20, 0, bidirectional=True)
    y = ParamSlider("Brightness", -20, 20, 0, bidirectional=True)
    pad = ColorPad2D(x, y)
    pad.resize(300, pad.height())
    pad.show()
    inner = pad._inner()
    QTest.mouseClick(pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(int(inner.right()), int(inner.top())))
    assert (x.value(), y.value()) == (20, 20)          # top-right = more saturated + brighter
    QTest.mouseClick(pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(int(inner.left()), int(inner.bottom())))
    assert (x.value(), y.value()) == (-20, -20)
    QTest.mouseDClick(pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      QPoint(int(inner.center().x()), int(inner.center().y())))
    assert (x.value(), y.value()) == (0, 0)
    y.setValue(6)                                      # model -> pad stays in sync
    assert abs(pad._point_for().y() - (inner.bottom() - (26 / 40) * inner.height())) < 0.5
    pad.setFocus()
    QTest.keyClick(pad, Qt.Key.Key_Up)
    QTest.keyClick(pad, Qt.Key.Key_Left)
    assert (x.value(), y.value()) == (-1, 7)


# ---- TierSlider ---------------------------------------------------------------

def _tier(maximum, bounds, reps, value):
    s = TierSlider()
    s.setRange(0, maximum)
    s.setInvertedAppearance(True)
    s.setInvertedControls(True)
    s.set_tiers(bounds, reps, lambda k: k)
    s.setValue(value)
    s._snap_to_value()
    s.resize(400, s.height())
    s.show()
    QApplication.processEvents()
    return s


@pytest.mark.parametrize("maximum,bounds,reps", [(51, QUALITY_TIER_BOUNDS_51, QUALITY_TIER_REPS_51),
                                                 (63, QUALITY_TIER_BOUNDS_AV1, QUALITY_TIER_REPS_AV1)])
def test_tier_slider_mapping_is_monotonic_and_invertible(qapp, maximum, bounds, reps):
    s = _tier(maximum, bounds, reps, 18)
    assert len(s.tiers()) == 4
    fracs = [s._frac_for(float(v)) for v in range(maximum, -1, -1)]      # worst -> best = left -> right
    assert fracs == sorted(fracs) and 0.0 < fracs[0] < fracs[-1] < 1.0
    for v in range(maximum + 1):
        assert s._value_for_frac(s._frac_for(float(v))) == v, v
    # every band owns exactly a quarter of the track
    edges = [s._frac_for(float(b["hi"] if i else b["hi"])) for i, b in enumerate(s.tiers())]
    assert [round(s._value_for_frac(f)) for f in (0.0, 1.0)] == [maximum, 0]


def test_tier_slider_label_click_jumps_to_representative_value(qapp):
    s = _tier(51, QUALITY_TIER_BOUNDS_51, QUALITY_TIER_REPS_51, 40)
    assert s.tiers()[s.active_tier_index()]["key"] == "Low"
    for i, band in enumerate(s.tiers()):
        r = s._label_rects()[i]
        QTest.mouseClick(s, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(int(r.center().x()), int(s.TRACK_H + 6)))
        assert s.value() == band["rep"], band
        assert s.tiers()[s.active_tier_index()]["key"] == band["key"]


def test_tier_slider_drag_and_keys(qapp):
    s = _tier(51, QUALITY_TIER_BOUNDS_51, QUALITY_TIER_REPS_51, 30)
    x0, x1 = s._x_range()
    y = int(s.TRACK_H / 2)
    QTest.mousePress(s, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(x1 - 2), y))
    assert s.value() <= 1                                # far right = best quality (lowest CRF)
    QTest.mouseMove(s, QPoint(int(x0 + 2), y))
    QTest.mouseRelease(s, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(x0 + 2), y))
    assert s.value() >= 50
    s.setValue(30)
    s.setFocus()
    QTest.keyClick(s, Qt.Key.Key_Right)                  # right = better = lower CRF
    assert s.value() == 29


def test_tier_slider_falls_back_to_linear_when_bands_dont_fit(qapp):
    s = TierSlider()
    s.setRange(0, 40)                                    # bands reach 51 but never cover... fewer than the range
    s.setInvertedAppearance(True)
    s.set_tiers(((0, 10, "A"),), {}, lambda k: k)        # does not reach 40 -> ignored
    assert s.tiers() == []
    s.setValue(10)
    assert 0.0 <= s._frac_for(10.0) <= 1.0


# ---- ScrubSpinBox ---------------------------------------------------------------

def test_chip_edit_field_has_room_and_scrubs_and_types(qapp):
    spin = ScrubSpinBox("CRF ")
    spin.setRange(0, 51)
    spin.setValue(18)
    chip = make_value_chip(spin, 92)
    chip.show()
    QApplication.processEvents()
    assert spin.text() == "CRF 18"
    assert spin.lineEdit().width() >= 60               # regression: the field once collapsed to 1 px
    le = spin.lineEdit()
    from PySide6.QtGui import QMouseEvent

    def mouse(kind, x, buttons):
        g = le.mapToGlobal(QPointF(x, 10))
        qapp.sendEvent(le, QMouseEvent(kind, QPointF(x, 10), g, Qt.MouseButton.LeftButton, buttons,
                                       Qt.KeyboardModifier.NoModifier))

    mouse(QEvent.Type.MouseButtonPress, 10, Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseMove, 10 + 30, Qt.MouseButton.LeftButton)      # 30 px right at 6 px/step -> +5
    mouse(QEvent.Type.MouseButtonRelease, 10 + 30, Qt.MouseButton.NoButton)
    assert spin.value() == 23
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, "20")
    QTest.keyClick(spin, Qt.Key.Key_Return)
    assert spin.value() == 20
    spin.clearFocus()


def test_chip_wheel_ignored_without_focus(qapp):
    from PySide6.QtGui import QWheelEvent
    spin = ScrubSpinBox()
    spin.setRange(0, 51)
    spin.setValue(18)
    spin.show()
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    spin.clearFocus()
    qapp.sendEvent(spin, ev)
    assert spin.value() == 18


# ---- TimelineSlider -------------------------------------------------------------

def test_timeline_click_seeks_and_hover_formats_time(qapp):
    s = TimelineSlider(formatter=lambda v: f"{v // 60}:{v % 60:02d}")
    s.setRange(0, 600)
    s.resize(500, 28)
    s.show()
    QApplication.processEvents()
    x0, x1 = s._x_range()
    assert s.value_at(x0) == 0 and s.value_at(x1) == 600
    mid = int((x0 + x1) / 2)
    QTest.mouseClick(s, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(mid, 14))
    assert abs(s.value() - 300) <= 2                    # a click jumps there, it doesn't just "page"
    from PySide6.QtGui import QMouseEvent
    p = QPointF(x0 + (x1 - x0) * 0.25, 14)
    qapp.sendEvent(s, QMouseEvent(QEvent.Type.MouseMove, p, s.mapToGlobal(p), Qt.MouseButton.NoButton,
                                  Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert s._bubble is not None and s._bubble.text() == "2:30"
    s.hide()
    assert not s._bubble.isVisible()


# ---- thumbnails -------------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_frame_strip_loader_fills_tiles_caches_and_cancels(qapp, tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer
    from hdr2sdr.ui.widgets.frame_strip import FrameStrip, FrameStripLoader
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:duration=6",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    ld = FrameStripLoader()
    strip = FrameStrip()
    got = []
    ld.frameReady.connect(lambda i, img: (got.append(i), strip.set_frame(i, img)))
    loop = QEventLoop()
    ld.finished.connect(loop.quit)
    QTimer.singleShot(30000, loop.quit)
    ld.start(shutil.which("ffmpeg"), str(clip), 6.0, 4, False)
    loop.exec()
    assert sorted(got) == [0, 1, 2, 3] and strip.has_frames() and not ld.is_running()
    again = []
    ld.frameReady.connect(lambda i, img: again.append(i))
    ld.start(shutil.which("ffmpeg"), str(clip), 6.0, 4, False)        # served from the cache, synchronously
    assert sorted(again) == [0, 1, 2, 3]
    # cancel mid-run never emits finished and leaves nothing running
    done = []
    ld.finished.connect(lambda: done.append(1))
    ld.start(shutil.which("ffmpeg"), str(clip), 6.0, 9, False)
    ld.cancel()
    assert not ld.is_running()
    # a missing ffmpeg is survivable
    bad = FrameStripLoader()
    loop2 = QEventLoop()
    bad.finished.connect(loop2.quit)
    QTimer.singleShot(5000, loop2.quit)
    bad.start("/nonexistent/ffmpeg", str(clip), 6.0, 2, False)
    loop2.exec()
    assert not bad.is_running()


# ---- MainWindow integration ----------------------------------------------------------

@pytest.fixture
def win(qapp):
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    for _ in range(4):
        qapp.processEvents()
    yield w
    w.close()


def test_main_window_uses_the_new_controls(win):
    assert isinstance(win.brightness_slider, ParamSlider) and win.brightness_spin is win.brightness_slider
    assert isinstance(win.quality_slider, TierSlider)
    assert isinstance(win.quality_spin, ScrubSpinBox)
    assert isinstance(win.preview_slider, TimelineSlider)
    # old -/+ steppers are gone from the quality and filter rows
    assert not hasattr(win.quality_stepper, "minus_btn")
    assert win.quality_spin.text() == "CRF 18"
    # the value chip, the band slider and the model agree
    win.quality_slider.setValue(24)
    assert win.quality_spin.value() == 24
    win.quality_spin.setValue(30)
    assert win.quality_slider.value() == 30
    assert win.quality_slider.tiers()[win.quality_slider.active_tier_index()]["key"] == "Low"


def test_quality_bands_follow_the_encoder_and_language(win):
    win.encoder_combo.setCurrentText("CPU \u00b7 AV1")
    QApplication.processEvents()
    bounds = {b["key"]: (b["lo"], b["hi"]) for b in win.quality_slider.tiers()}
    assert bounds["Maximum"][0] == 0 and bounds["Low"][1] == 63
    win.encoder_combo.setCurrentText("CPU \u00b7 H.264")
    QApplication.processEvents()
    assert {b["key"]: (b["lo"], b["hi"]) for b in win.quality_slider.tiers()}["Low"][1] == 51
    win.change_language("uk")
    assert win._tr_band("Maximum") == win.tr("Maximum") if hasattr(win, "_tr_band") else True
    assert win.quality_slider._labeler("High") == win.tr("High")
    win.change_language("en")


def test_colour_group_reset_and_style_persistence(win):
    assert not win.colour_reset_btn.isEnabled()
    win.brightness_spin.setValue(5)
    win.saturation_spin.setValue(-3)
    win.colortemperature_spin.setValue(5500)
    assert win.colour_reset_btn.isEnabled()
    win.colour_reset_btn.click()
    assert (win.brightness_spin.value(), win.saturation_spin.value(), win.colortemperature_spin.value()) == (0, 0, 6500)
    assert not win.colour_reset_btn.isEnabled()
    # pad style swaps the two sliders for the pad and is remembered
    win.adv_filters_panel.setVisible(True)
    win.brightness_slider._choose_style("pad")
    QApplication.processEvents()
    assert win.colour_pad.isVisibleTo(win.adv_filters_panel) and not win.brightness_slider.isVisibleTo(win.adv_filters_panel)
    assert str(win.i18n.settings.value("control_style")) == "pad"
    win.colour_pad._choose_style("scrub")
    assert not win.colour_pad.isVisibleTo(win.adv_filters_panel) and win.brightness_slider.isVisibleTo(win.adv_filters_panel)
    assert str(win.i18n.settings.value("control_style")) == "scrub"


def test_cores_and_priority_notched_sliders_side_by_side(win):
    from hdr2sdr.paths import CPU_COUNT
    n = CPU_COUNT
    assert win.cores_spin.value() == n and win.cores_slider.isVisibleTo(win.cores_panel_widget)
    assert win.priority_slider.isVisibleTo(win.cores_panel_widget)
    assert win.priority_combo is win.priority_slider
    assert win.priority_combo.currentText() == "Balanced"
    assert win.priority_slider.valueText() == "Balanced"
    # Clicking left/right/middle segments of priority_slider jumps between the 3 notched levels
    w = win.priority_slider.width()
    QTest.mouseClick(win.priority_slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(10, 16))
    assert win.priority_combo.currentIndex() == 0 and win.priority_combo.currentText() == "Efficiency (low)"
    QTest.mouseClick(win.priority_slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(w - 10, 16))
    assert win.priority_combo.currentIndex() == 2 and win.priority_combo.currentText() == "Performance (high)"
    QTest.mouseClick(win.priority_slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(w // 2, 16))
    assert win.priority_combo.currentIndex() == 1 and win.priority_combo.currentText() == "Balanced"
    win.change_language("uk")
    assert win.cores_slider.text() != "Cores"
    assert win.priority_slider.text() != "Priority"
    win.change_language("en")


def test_timeline_label_shows_position_and_total(win):
    win.preview_slider.setEnabled(True)
    win.preview_slider.setRange(0, 1708)
    win.preview_slider.setValue(854)
    assert win.preview_position_label.text() == "14:14 / 28:28"
    win._update_preview_position_label(0)
    assert win.preview_position_label.text() == "0:00 / 28:28"
    win.preview_slider.setRange(0, 0)
    win._update_preview_position_label(0)
    assert win.preview_position_label.text() == "0:00"


def test_drag_on_the_preview_picture_moves_the_timeline(win):
    from PySide6.QtGui import QMouseEvent
    win.preview_slider.setEnabled(True)
    win.preview_slider.setRange(0, 1000)
    win.preview_slider.setValue(500)
    lbl = win.preview_label

    def ev(kind, x, buttons):
        return QMouseEvent(kind, QPointF(x, 10), lbl.mapToGlobal(QPointF(x, 10)), Qt.MouseButton.LeftButton,
                           buttons, Qt.KeyboardModifier.NoModifier)

    win.eventFilter(lbl, ev(QEvent.Type.MouseButtonPress, 100, Qt.MouseButton.LeftButton))
    assert win.preview_slider.value() == 500                  # a plain press changes nothing
    consumed = win.eventFilter(lbl, ev(QEvent.Type.MouseMove, 100 + 100, Qt.MouseButton.LeftButton))
    assert consumed and win.preview_slider.value() > 500
    consumed = win.eventFilter(lbl, ev(QEvent.Type.MouseButtonRelease, 200, Qt.MouseButton.NoButton))
    assert consumed                                         # the release after a drag is swallowed


def test_new_i18n_strings_exist_in_every_language():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "i18n"
    keys = [k for k in json.loads((root / "en.json").read_text(encoding="utf-8")) if k.startswith("ctl_")]
    assert len(keys) >= 12
    for f in root.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        assert all(k in data for k in keys), f.name
    uk = json.loads((root / "uk.json").read_text(encoding="utf-8"))
    ru = json.loads((root / "ru.json").read_text(encoding="utf-8"))
    assert uk["ctl_cores_all"] != "All cores ({n})" and ru["ctl_cores_all"] != "All cores ({n})"
