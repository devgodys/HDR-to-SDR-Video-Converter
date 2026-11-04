from hdr2sdr.ui import win_native as wn


class _W:
    def winId(self):
        return 1234


def test_colorref_is_bgr_and_rejects_non_hex():
    assert wn.colorref("#112233") == 0x332211
    assert wn.colorref("rgba(1,2,3,0.5)") is None
    assert wn.colorref("") is None


def test_noop_off_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(wn, "_is_windows", lambda: False)
    monkeypatch.setattr(wn, "_set_attr", lambda *a: calls.append(a) or True)
    wn.theme_titlebar(_W(), "dark")
    wn.no_native_chrome(_W())
    assert calls == []


def test_titlebar_attrs_on_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(wn, "_is_windows", lambda: True)
    monkeypatch.setattr(wn, "_set_attr", lambda h, a, v: calls.append((h, a, v)) or True)
    wn.theme_titlebar(_W(), "dark")
    attrs = [a for _h, a, _v in calls]
    assert wn.DWMWA_USE_IMMERSIVE_DARK_MODE in attrs
    assert wn.DWMWA_CAPTION_COLOR in attrs and wn.DWMWA_TEXT_COLOR in attrs
    assert (1234, wn.DWMWA_USE_IMMERSIVE_DARK_MODE, 1) in calls


def test_no_native_chrome_attrs(monkeypatch):
    calls = []
    monkeypatch.setattr(wn, "_is_windows", lambda: True)
    monkeypatch.setattr(wn, "_set_attr", lambda h, a, v: calls.append((a, v)) or True)
    wn.no_native_chrome(_W())
    assert (wn.DWMWA_WINDOW_CORNER_PREFERENCE, wn.DWMWCP_DONOTROUND) in calls
    assert (wn.DWMWA_BORDER_COLOR, wn.DWMWA_COLOR_NONE) in calls
