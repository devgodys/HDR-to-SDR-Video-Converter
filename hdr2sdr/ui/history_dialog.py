"""Conversion history dialog, kept separate from MainWindow orchestration."""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QVBoxLayout,
)

from hdr2sdr.ui.widgets.buttons import GlassButton


class ConversionHistoryDialog(QDialog):
    def __init__(self, history, translate, clear_history, parent=None):
        super().__init__(parent)
        self._history = list(reversed(history))
        self._tr = translate
        self._clear_history = clear_history
        self.setWindowTitle(self._tr("Conversion history"))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self._list = None
        if not self._history:
            layout.addWidget(QLabel(self._tr("No conversions recorded yet.")))
        else:
            self._list = QListWidget()
            status_labels = {
                "done": "✓ " + self._tr("Done"),
                "failed": "✗ " + self._tr("Failed"),
                "stopped": "■ " + self._tr("Stopped"),
            }
            for entry in self._history:
                name = Path(entry.get("source", "")).name or self._tr("(unknown source)")
                when = entry.get("timestamp", "")
                status = status_labels.get(entry.get("status"), entry.get("status", ""))
                kind = {"hdr": "HDR10", "dolby": self._tr("Dolby Vision"),
                        "sdr": "SDR"}.get(entry.get("kind"), "")
                settings = entry.get("settings") or {}
                encoder = settings.get("encoder_label") or settings.get("encoder") or ""
                label = f"{status} · {when} · {name}"
                if kind:
                    label += f" · {kind}"
                quality_mode = settings.get("quality_mode", "")
                quality = settings.get("quality")
                bitrate = settings.get("target_bitrate_kbps")
                quality_value = (f"{quality_mode} {bitrate} kb/s" if quality_mode == "Bitrate" and bitrate
                                 else f"{quality_mode} {quality}" if quality_mode and quality is not None
                                 else "")
                details = [settings.get("backend", ""), encoder,
                           settings.get("bit_depth_label", ""), quality_value,
                           settings.get("tonemap_method", "")]
                summary = " / ".join(str(value) for value in details if value)
                if summary:
                    label += f" · {summary}"
                self._list.addItem(label)
            layout.addWidget(self._list)
            self._list.itemDoubleClicked.connect(lambda _item: self._open_selected_folder())
            row = QHBoxLayout()
            open_button = GlassButton(self._tr("Open output folder"))
            open_button.clicked.connect(self._open_selected_folder)
            clear_button = GlassButton(self._tr("Clear history"))
            clear_button.clicked.connect(self._confirm_clear)
            row.addWidget(open_button)
            row.addStretch(1)
            row.addWidget(clear_button)
            layout.addLayout(row)
        close_button = GlassButton(self._tr("Close"))
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def _open_selected_folder(self):
        row = self._list.currentRow() if self._list is not None else -1
        if row < 0:
            return
        destination = self._history[row].get("destination")
        folder = Path(destination).parent if destination else None
        if folder and folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            QMessageBox.information(
                self, self._tr("Output folder"),
                self._tr("That entry's output folder isn't available anymore."))

    def _confirm_clear(self):
        answer = QMessageBox.question(
            self, self._tr("Clear history"),
            self._tr("Remove all recorded conversion history? This can't be undone."))
        if answer == QMessageBox.StandardButton.Yes:
            self._clear_history()
            self.close()
