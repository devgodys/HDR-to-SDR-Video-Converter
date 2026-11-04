import json

import pytest


pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QComboBox, QSpinBox


def test_conversion_preferences_restore_without_persisting_queue(tmp_path):
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.conversion_preferences import ConversionPreferences

    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    encoder = QComboBox()
    encoder.addItems(["CPU · H.265", "CPU · H.264"])
    container = QComboBox()
    container.addItems(["MP4", "MKV"])
    quality = QSpinBox()
    quality.setRange(0, 51)
    controls = {"encoder": encoder, "container": container, "quality": quality}
    folder_state = [tmp_path]
    dependencies = {"depth": QComboBox()}
    dependencies["depth"].addItems(["8-bit", "10-bit"])
    controls["bit_depth"] = dependencies["depth"]
    binder = ConversionPreferences(
        settings, controls, lambda folder: folder_state.__setitem__(0, folder),
        lambda: folder_state[0], lambda: None)
    encoder.setCurrentText("CPU · H.265")
    container.setCurrentText("MP4")
    quality.setValue(23)
    binder.save()

    saved = json.loads(settings.value("conversion_preferences"))
    assert saved["encoder"] == "CPU · H.265"
    assert saved["container"] == "MP4"
    assert saved["quality"] == 23
    assert "queue" not in saved

    encoder.setCurrentText("CPU · H.264")
    container.setCurrentText("MKV")
    quality.setValue(18)
    binder.restore()
    assert encoder.currentText() == "CPU · H.265"
    assert container.currentText() == "MP4"
    assert quality.value() == 23
    assert folder_state[0] == tmp_path
