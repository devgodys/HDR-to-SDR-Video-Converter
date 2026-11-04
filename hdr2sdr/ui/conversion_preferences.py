"""Restore and persist conversion controls independently of MainWindow."""

import json
from pathlib import Path

from PySide6.QtCore import QTimer


class ConversionPreferences:
    def __init__(self, settings, controls, output_folder_changed, output_folder_getter,
                 bit_depth_changed, parent=None):
        self.settings = settings
        self.controls = dict(controls)
        self.output_folder_changed = output_folder_changed
        self.output_folder_getter = output_folder_getter
        self.bit_depth_changed = bit_depth_changed
        self.restoring = False
        self.timer = QTimer(parent)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.save)

    def restore(self):
        self.restoring = True
        try:
            raw = self.settings.value("conversion_preferences", "{}")
            try:
                values = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
            except (TypeError, ValueError):
                values = {}
            folder = values.get("output_folder")
            if folder and Path(folder).is_dir():
                self.output_folder_changed(Path(folder))
            # These selections determine which values the dependent controls
            # expose, so restore them before the remaining fields.
            for key in ("backend", "bit_depth"):
                value = values.get(key)
                widget = self.controls.get(key)
                if widget is None:
                    continue
                if value is not None and widget.findText(str(value)) >= 0:
                    widget.setCurrentText(str(value))
            self.bit_depth_changed()
            # Restore encoder before numeric fields (quality) so encoder-range
            # updates run before the saved quality is applied.
            ordered_keys = ["encoder", *[k for k in self.controls if k not in ("backend", "bit_depth", "encoder")]]
            for key in ordered_keys:
                if key not in values:
                    continue
                widget = self.controls[key]
                value = values[key]
                if hasattr(widget, "isChecked"):
                    widget.setChecked(bool(value))
                elif hasattr(widget, "findText") and getattr(widget, "_choices", True):
                    if widget.findText(str(value)) >= 0:
                        widget.setCurrentText(str(value))
                else:
                    try:
                        iv = float(value) if key == "desat" else int(value)
                    except (TypeError, ValueError):
                        continue
                    if key == "quality" and iv <= 0:
                        # Guard against accidental 0 saved from pre-Stage-4 wheel scroll
                        iv = 18
                    try:
                        widget.setValue(iv)
                    except (TypeError, ValueError):
                        continue
        finally:
            self.restoring = False

    def bind(self):
        for widget in self.controls.values():
            is_choice = bool(getattr(widget, "_choices", True))
            signal = ((getattr(widget, "currentTextChanged", None) if is_choice else None)
                      or getattr(widget, "valueChanged", None)
                      or getattr(widget, "toggled", None))
            if signal is not None:
                signal.connect(self.schedule_save)

    def schedule_save(self, *_args):
        if not self.restoring:
            self.timer.start()

    def save(self):
        values = {}
        for key, widget in self.controls.items():
            if hasattr(widget, "isChecked"):
                values[key] = widget.isChecked()
            elif hasattr(widget, "currentText") and getattr(widget, "_choices", True):
                values[key] = widget.currentText()
            else:
                values[key] = widget.value()
        folder = self.output_folder_getter()
        values["output_folder"] = str(folder) if folder else ""
        self.settings.setValue("conversion_preferences", json.dumps(values, sort_keys=True))
