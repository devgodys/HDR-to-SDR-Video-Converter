import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_system_language_uses_display_language_not_region(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QLocale
    from hdr2sdr.i18n.localizer import Localizer

    class Fake:
        def uiLanguages(self):
            return ["en-US", "uk-UA"]

        def name(self):
            return "uk_UA"

    monkeypatch.setattr(QLocale, "system", staticmethod(lambda: Fake()))
    assert Localizer._system_language() == "en"


def test_system_language_falls_back_to_locale_name(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QLocale
    from hdr2sdr.i18n.localizer import Localizer

    class Fake:
        def uiLanguages(self):
            return []

        def name(self):
            return "uk_UA"

    monkeypatch.setattr(QLocale, "system", staticmethod(lambda: Fake()))
    assert Localizer._system_language() == "uk"
