from __future__ import annotations

import json

from PySide6.QtCore import (
    QLocale, QSettings,
)

from hdr2sdr.core.constants import LANGUAGE_NAMES
from hdr2sdr.paths import I18N_DIR




class Localizer:
    """Loads the bundled Python dictionaries and translates known UI source text."""

    def __init__(self):
        self.settings = QSettings("DevGodys", "HDR to SDR Video Converter")
        self.english = self._load("en")
        self.keys_by_english = {value: key for key, value in self.english.items()}
        self.language = self._resolve(self.settings.value("interface_language", "system"))
        self.strings = self._load(self.language) if self.language != "en" else self.english

    @staticmethod
    def _load(code):
        path = I18N_DIR / f"{code}.json"
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _map_code(system_code):
        system_code = system_code.lower().replace("-", "_")
        if system_code.startswith("pt"):
            return "pt_br"
        if system_code.startswith("zh"):
            # Traditional-script regions/variants map to zh_tw; everything
            # else (zh_cn, zh_sg, bare "zh", ...) falls back to Simplified.
            return "zh_tw" if system_code.split("_")[-1] in ("tw", "hk", "mo", "hant") else "zh_cn"
        base = system_code.split("_", 1)[0]
        return base if base == "en" or base in LANGUAGE_NAMES else None

    @staticmethod
    def _system_language():
        """The OS *display* language (uiLanguages), not the regional-format locale: a user with
        English Windows but Ukrainian regional formats must get English."""
        locale = QLocale.system()
        for code in locale.uiLanguages():
            mapped = Localizer._map_code(code)
            if mapped:
                return mapped
        return Localizer._map_code(locale.name()) or "en"

    def _resolve(self, code):
        return self._system_language() if code == "system" else code if code in LANGUAGE_NAMES else "en"

    def set_language(self, code):
        self.settings.setValue("interface_language", code)
        self.language = self._resolve(code)
        self.strings = self._load(self.language) if self.language != "en" else self.english

    def text(self, source, **values):
        key = self.keys_by_english.get(source, source)
        translated = self.strings.get(key, source)
        try:
            return translated.format(**values) if values else translated
        except (KeyError, ValueError):
            return translated
