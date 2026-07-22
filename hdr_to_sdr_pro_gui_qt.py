"""HDR -> SDR Converter \u2014 10/12-bit build.

Adds 10-bit and 12-bit output on top of the free Open-HDR-to-SDR-Converter
engine. No license, no paywall - this exists purely because most HDR
converters (including several paid ones) hard-lock higher bit depths
behind a purchase, and there's no real technical reason to.

Deliberately a thin subclass of the free app's MainWindow, not a fork:
bug fixes and new tone-mapping curves added to hdr_to_sdr_gui_qt.py apply
here automatically. This file only adds the bit-depth selector and a
non-blocking Ko-fi mention - nothing here gates functionality.

Packaging: build this as its own executable/download if you want it
listed as a distinct "10/12-bit build" on the site, or just fold
_add_bit_depth_control() straight into the main app's build() - now that
nothing here requires a license, there's no real reason to ship two
executables. Keeping it a separate file for now in case that changes.
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QComboBox, QLabel

from hdr_to_sdr_gui_qt import MainWindow, ENCODER_MAP, install_excepthook, find_icon

# Bit depth -> (pixel format for the zscale/libplacebo `format=` param,
# subset of ENCODER_MAP that can actually produce it).
# NVENC / AMD AMF / VideoToolbox cap out at 10-bit HEVC; only libx265
# (CPU) goes to 12-bit, and H.264 doesn't do either in any practical sense.
BIT_DEPTH_ENCODERS = {
    "8-bit (SDR standard)": ("yuv420p", set(ENCODER_MAP.keys())),
    "10-bit": ("yuv420p10le", {
        "CPU \u00b7 H.265", "NVIDIA NVENC \u00b7 H.265",
        "AMD AMF \u00b7 H.265", "Apple VideoToolbox \u00b7 H.265",
    }),
    "12-bit": ("yuv420p12le", {"CPU \u00b7 H.265"}),
}


class BitDepthMainWindow(MainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Open HDR to SDR Converter \u2014 10/12-bit")

    # ---- extra UI -----------------------------------------------------
    def build(self):
        super().build()
        self._add_bit_depth_control()

    def _add_bit_depth_control(self):
        label = QLabel("Bit depth")
        self.bit_depth_combo = QComboBox()
        self.bit_depth_combo.addItems(list(BIT_DEPTH_ENCODERS.keys()))
        self.bit_depth_combo.setCurrentText("10-bit")
        self.bit_depth_combo.currentTextChanged.connect(self._on_bit_depth_changed)
        # Insert right after the Encoder combo in the CONVERSION card.
        self.conv_card.body.insertWidget(6, self.bit_depth_combo)
        self.conv_card.body.insertWidget(6, label)

        self._on_bit_depth_changed(self.bit_depth_combo.currentText())

    def _on_bit_depth_changed(self, label: str):
        _, allowed = BIT_DEPTH_ENCODERS[label]
        current = self.encoder_combo.currentText()
        self.encoder_combo.blockSignals(True)
        self.encoder_combo.clear()
        self.encoder_combo.addItems([e for e in ENCODER_MAP if e in allowed])
        if current in allowed:
            self.encoder_combo.setCurrentText(current)
        self.encoder_combo.blockSignals(False)

    # ---- command building: inject the chosen bit depth ------------------
    def command_ffmpeg(self):
        cmd = super().command_ffmpeg()
        fmt, _ = BIT_DEPTH_ENCODERS[self.bit_depth_combo.currentText()]
        vf_index = cmd.index("-vf") + 1
        cmd[vf_index] = cmd[vf_index].replace("format=yuv420p", f"format={fmt}")
        insert_at = cmd.index("-c:a")
        cmd[insert_at:insert_at] = ["-pix_fmt", fmt]
        return cmd


def main():
    app = QApplication(sys.argv)
    icon_path = find_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    win = BitDepthMainWindow()
    if icon_path:
        win.setWindowIcon(QIcon(icon_path))
    install_excepthook(win)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
