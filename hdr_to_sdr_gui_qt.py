"""HDR -> SDR with standard FFmpeg tone mapping, true BT.2390 via libplacebo/Vulkan,
and an experimental HandBrakeCLI backend.

Qt for Python (PySide6) port of the original Tkinter app. Behaviour, command
lines, and detection logic are kept identical; only the UI toolkit changed.
Process I/O now arrives via Qt signals (QProcess) instead of a background
thread + queue, since QProcess already delivers its signals on the GUI
thread - the thread/queue plumbing the Tkinter version needed is gone.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QProcess, QUrl
from PySide6.QtGui import QFontDatabase, QFont, QTextCursor, QIcon, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QSpinBox, QSlider, QProgressBar, QTextEdit, QGroupBox, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFileDialog, QMessageBox, QFrame, QSplitter
)

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

# ---- palette ---------------------------------------------------------------
# Same two "compact utility card" themes as the Tkinter version, tuned
# separately rather than a literal colour-invert of each other. Blue is
# reserved for the primary action, progress fill, and in-progress status
# text in both themes.
THEMES = {
    "light": dict(
        BG="#EDEDEA", CARD="#FFFFFF", CARD2="#F1EFE9", FIELD="#FFFFFF",
        DISABLED="#E7E4DC", BORDER="#DDDBD3", TXT="#1C1C1A", MUTED="#8A8883",
        INDIGO="#1968E0", INDIGO2="#124FAF",
        GREEN="#1E9E5A", GREEN2="#167C46",
        RED="#D64545", RED2="#B33636",
        AMBER="#BF7A1E", AMBER2="#9C6418",
        LOG_BG="#F8F7F4", LOG_FG="#33322E",
    ),
    "dark": dict(
        BG="#14161B", CARD="#1C1F26", CARD2="#262A33", FIELD="#20242D",
        DISABLED="#2B2F38", BORDER="#30343E", TXT="#F0F1F4", MUTED="#8D93A0",
        INDIGO="#5B8DEF", INDIGO2="#4573D6",
        GREEN="#3FCB7E", GREEN2="#2FAE68",
        RED="#FF6B6B", RED2="#E85454",
        AMBER="#F0B429", AMBER2="#D69A1E",
        LOG_BG="#12141A", LOG_FG="#D7DCE6",
    ),
}

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
CPU_COUNT = os.cpu_count() or 4

# Project links shown as small buttons in the header - edit these to match
# your actual repo/Ko-fi.
GITHUB_URL = "https://github.com/godysdev/Open-HDR-to-SDR-convertor"
KOFI_URL = "https://ko-fi.com/devgodys"

# ffmpeg -progress emits key=value lines in blocks terminated by a
# "progress=continue"/"progress=end" line.
PROGRESS_KEYS = {"frame", "fps", "bitrate", "total_size", "out_time_us", "out_time_ms",
                  "out_time", "dup_frames", "drop_frames", "speed", "progress"}

_NUM = re.compile(r"[-+]?\d*\.?\d+")
# HandBrakeCLI prints e.g.: "Encoding: task 1 of 1, 45.23 % (123.4 fps, avg 110.3 fps, ETA 00h05m32s)"
_HB_PROGRESS = re.compile(
    r"Encoding:.*?(\d+\.\d+)\s*%(?:\s*\(([\d.]+)\s*fps,\s*avg\s*([\d.]+)\s*fps"
    r"(?:,\s*ETA\s*(\d+)h(\d+)m(\d+)s)?\))?")

RESOLUTIONS = [
    ("Source (no scaling)", "", ""),
    ("3840\u00d72160 (4K)", "3840", "2160"),
    ("2560\u00d71440 (1440p)", "2560", "1440"),
    ("1920\u00d71080 (1080p)", "1920", "1080"),
    ("1280\u00d7720 (720p)", "1280", "720"),
    ("854\u00d7480 (480p)", "854", "480"),
    ("Custom (edit fields)", None, None),
]

# Tone-mapping curves. label -> ("gpu"/"cpu", <exact ffmpeg/libplacebo enum
# string>). GPU codes are libplacebo's `tonemapping=` values; CPU codes are
# the classic `tonemap` filter's values - both are real, documented ffmpeg
# options, not invented ones.
TONEMAP_BASE = {
    "BT.2390 \u00b7 GPU libplacebo/Vulkan": ("gpu", "bt.2390"),
    "Hable \u00b7 Standard FFmpeg": ("cpu", "hable"),
    "Reinhard \u00b7 Standard FFmpeg": ("cpu", "reinhard"),
    "Mobius \u00b7 Standard FFmpeg": ("cpu", "mobius"),
}
TONEMAP_PRO_GPU = {
    "BT.2446A \u00b7 GPU libplacebo/Vulkan": ("gpu", "bt.2446a"),
    "Auto \u00b7 GPU libplacebo/Vulkan": ("gpu", "auto"),
}
TONEMAP_PRO_GPU_ST2094 = {
    "ST2094-40 (HDR10+) \u00b7 GPU libplacebo/Vulkan": ("gpu", "st2094-40"),
    "ST2094-10 \u00b7 GPU libplacebo/Vulkan": ("gpu", "st2094-10"),
}
TONEMAP_PRO_CPU = {
    "Linear \u00b7 Standard FFmpeg": ("cpu", "linear"),
    "Gamma \u00b7 Standard FFmpeg": ("cpu", "gamma"),
    "Clip \u00b7 Standard FFmpeg": ("cpu", "clip"),
    "None (direct, no tonemap) \u00b7 Standard FFmpeg": ("cpu", "none"),
}

PRIORITY_LEVELS = ["Efficiency (low)", "Balanced", "Performance (high)"]
PRIORITY_NICE = {"Efficiency (low)": 10, "Balanced": 0, "Performance (high)": -5}

# ---- bitrate estimation (rough, content-dependent) -------------------
# This is a ballpark only - CRF/CQ/QP rate control lets the encoder use
# however many bits a given frame needs, so actual bitrate depends on
# content complexity (grain, motion, detail) far more than on any of the
# inputs below. The estimate exists to catch surprises (e.g. "CRF 14 on
# 4K H.264 is going to be huge"), not to predict an exact number.
#
# Anchor: x264 "medium", CRF 23, 1080p, typical live-action content
# averages roughly 3-4 Mbps - a widely cited x264 rule of thumb. From
# there:
#   - every -6 CRF steps roughly doubles the bitrate, and every +6 CRF
#     roughly halves it - CRF's quantizer scale is logarithmic and a
#     6-point step is specifically "twice/half the bits" by design.
#   - bitrate is assumed to scale ~linearly with pixel count (constant
#     bits-per-pixel).
#   - H.265/HEVC needs roughly 35-45% fewer bits than H.264 for
#     comparable quality at the same CRF value.
#   - Hardware encoders (NVENC/AMF/VideoToolbox) are less bit-efficient
#     than x264/x265 software encoding at a given quality setting, so
#     they're nudged upward.
BITRATE_ANCHOR_1080P_H264_CRF23_KBPS = 3500
CODEC_EFFICIENCY = {"H.264": 1.0, "H.265": 0.60}
ENCODER_EFFICIENCY = {"CPU": 1.0, "NVIDIA": 1.35, "AMD": 1.35, "Apple": 1.25}


def estimate_bitrate_kbps(encoder_label, quality, width, height):
    """Rough estimated output bitrate in kbps for the given encoder combo
    label (an ENCODER_MAP key), CRF/CQ/QP value, and output resolution."""
    codec = "H.265" if encoder_label.endswith("H.265") else "H.264"
    if encoder_label.startswith("CPU"):
        enc_type = "CPU"
    elif encoder_label.startswith("NVIDIA"):
        enc_type = "NVIDIA"
    elif encoder_label.startswith("AMD"):
        enc_type = "AMD"
    else:
        enc_type = "Apple"
    w = width or 1920
    h = height or 1080
    scale = max(1, w * h) / (1920 * 1080)
    crf_factor = 2 ** ((23 - quality) / 6.0)
    kbps = (BITRATE_ANCHOR_1080P_H264_CRF23_KBPS * scale * crf_factor
            * CODEC_EFFICIENCY[codec] * ENCODER_EFFICIENCY[enc_type])
    return max(150.0, kbps)

ENCODER_MAP = {
    "CPU \u00b7 H.264": "libx264", "CPU \u00b7 H.265": "libx265",
    "NVIDIA NVENC \u00b7 H.264": "h264_nvenc", "NVIDIA NVENC \u00b7 H.265": "hevc_nvenc",
    "AMD AMF \u00b7 H.264": "h264_amf", "AMD AMF \u00b7 H.265": "hevc_amf",
    "Apple VideoToolbox \u00b7 H.264": "h264_videotoolbox",
    "Apple VideoToolbox \u00b7 H.265": "hevc_videotoolbox",
}
HB_ENCODER_MAP = {
    "CPU \u00b7 H.264": "x264", "CPU \u00b7 H.265": "x265",
    "NVIDIA NVENC \u00b7 H.264": "nvenc_h264", "NVIDIA NVENC \u00b7 H.265": "nvenc_h265",
    "AMD AMF \u00b7 H.264": "vce_h264", "AMD AMF \u00b7 H.265": "vce_h265",
    "Apple VideoToolbox \u00b7 H.264": "vt_h264", "Apple VideoToolbox \u00b7 H.265": "vt_h265",
}


# ---- small helpers -----------------------------------------------------

def exe(n):
    names = (n + ".exe", n) if sys.platform.startswith("win") else (n,)
    return next((str(ROOT / x) for x in names if (ROOT / x).is_file()), shutil.which(n))


def pick_font(candidates, default):
    fams = {f.lower() for f in QFontDatabase.families()}
    for c in candidates:
        if c.lower() in fams:
            return c
    return default


def parse_num(s):
    if not s:
        return None
    m = _NUM.match(s.strip())
    return float(m.group()) if m else None


def format_time(seconds):
    seconds = max(0, int(seconds))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def even(n):
    n = int(n)
    return max(2, n - (n % 2))


def no_window_kwargs():
    # A PyInstaller --windowed build has no console, so sys.stdin/stdout/
    # stderr don't exist - subprocess normally tries to inherit those
    # handles, and on Windows that fails with "OSError: [WinError 6] The
    # handle is invalid" the moment anything calls subprocess.check_output.
    # Giving stdin an explicit target (rather than "inherit") sidesteps it;
    # stdout/stderr are already redirected explicitly by every caller below.
    return dict(creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdin=subprocess.DEVNULL)


def refresh_windows_path():
    """After winget installs something, its own installer updates the
    registry's PATH - but this already-running process won't see that
    until we re-read it, same idea as the PowerShell setup script's
    Update-SessionPath. Without this, a tool installed a moment ago still
    looks missing to shutil.which()/exe() for the rest of this session."""
    if not sys.platform.startswith("win"):
        return
    try:
        import winreg
        parts = []
        for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                           (winreg.HKEY_CURRENT_USER, r"Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    parts.append(val)
            except OSError:
                pass
        if parts:
            os.environ["PATH"] = ";".join(parts)
    except Exception:
        pass


def find_icon():
    """Look next to the script (or, when frozen, next to the unpacked exe
    contents) for an icon file, so dropping one in with a recognized name
    is enough - no code change needed. .ico works cross-platform for the
    in-app QIcon; only the exe's own file icon needs a real .ico/.icns via
    PyInstaller's --icon flag (see the build instructions)."""
    for name in ("icon.ico", "icon.png", "app.ico", "app.png"):
        p = ROOT / name
        if p.is_file():
            return str(p)
    return None


# ---- small widget helpers -----------------------------------------------

def set_role(widget, role):
    """Attach a QSS-selectable role (e.g. label colour, button colour) and
    force a restyle - Qt caches style output per-widget, so a property
    change alone doesn't repaint until you unpolish/polish."""
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def loosen_combo(combo, min_chars=12):
    """QComboBox defaults to sizing itself so its *widest item* always fits
    without eliding - with entries like "ST2094-40 (HDR10+) . GPU
    libplacebo/Vulkan" that alone sets a large floor under how narrow the
    whole panel can ever get, no matter how far a splitter is dragged.
    Switching to a fixed content-length budget lets the widget (and
    everything containing it) shrink freely; the current selection just
    elides with "..." instead, which is fine since the full text is still
    visible in the dropdown and via hover tooltip."""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(min_chars)
    combo.currentTextChanged.connect(combo.setToolTip)
    combo.setToolTip(combo.currentText())


class Card(QGroupBox):
    """A titled card matching the compact-utility-card look: thin border,
    flat card-colour fill, bold small-caps-ish header."""

    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.setProperty("role", "card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 20, 16, 16)
        self.body.setSpacing(8)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Open HDR to SDR Converter")

        self.FONT = pick_font(["Segoe UI", "SF Pro Text", "Inter", "Ubuntu", "Helvetica Neue"], "")
        self.FONT_SEMI = pick_font(["Segoe UI Semibold", "SF Pro Display", "Inter SemiBold", "Ubuntu"], self.FONT)
        self.MONO = pick_font(["Cascadia Mono", "Consolas", "Menlo", "SF Mono", "Ubuntu Mono", "Courier New"], "monospace")
        if self.FONT:
            self.setFont(QFont(self.FONT, 10))

        self.duration = 0.0
        self.kind = "unknown"
        self.src_width = None
        self.src_height = None
        self.has_bt2390 = False
        self.has_st2094 = False
        self.encoders = set()
        self.proc = None
        self.proc_psutil = None
        self._setup_proc = None
        self.stopping = False
        self.paused = False
        self.out_buf = ""
        self.block = {}
        self.using_hb = False
        self.gpu_tool = shutil.which("nvidia-smi")
        self.handbrake_tool = exe("HandBrakeCLI")
        self.theme_name = "light"

        self.build()
        self.apply_theme()
        self.set_state("idle", "Checking FFmpeg\u2026")
        self.update_run_controls()
        QTimer.singleShot(0, self.check)

    # ---- theming ---------------------------------------------------
    def apply_theme(self):
        t = THEMES[self.theme_name]
        qss = f"""
        QWidget {{ background: {t['BG']}; color: {t['TXT']}; font-family: '{self.FONT}'; }}
        QGroupBox[role="card"], QFrame[role="card"] {{
            background: {t['CARD']}; border: 1px solid {t['BORDER']}; border-radius: 6px;
            margin-top: 10px; font-family: '{self.FONT_SEMI}'; font-size: 9pt; font-weight: 600;
        }}
        QGroupBox[role="card"]::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t['TXT']}; }}
        QLabel {{ background: transparent; }}
        QLabel[role="muted"] {{ color: {t['MUTED']}; font-size: 9pt; }}
        QLabel[role="info"] {{ color: {t['INDIGO']}; font-weight: 600; font-size: 9pt; }}
        QLabel[role="good"] {{ color: {t['GREEN']}; font-weight: 600; font-size: 9pt; }}
        QLabel[role="bad"] {{ color: {t['RED']}; font-weight: 600; font-size: 9pt; }}
        QLabel[role="warn"] {{ color: {t['AMBER']}; font-weight: 600; font-size: 9pt; }}
        QLabel[role="title"] {{ font-family: '{self.FONT_SEMI}'; font-size: 20pt; font-weight: 600; }}
        QLabel[role="subtitle"] {{ color: {t['MUTED']}; }}
        QLabel[role="mono"] {{ font-family: '{self.MONO}'; font-size: 9pt; color: {t['MUTED']}; }}
        QLineEdit, QComboBox, QSpinBox {{
            background: {t['FIELD']}; border: 1px solid {t['BORDER']}; border-radius: 4px; padding: 5px 7px;
        }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {t['MUTED']}; background: {t['DISABLED']}; }}
        QPushButton {{
            background: {t['CARD']}; border: 1px solid {t['BORDER']}; border-radius: 4px; padding: 7px 12px;
        }}
        QPushButton:hover {{ background: {t['CARD2']}; }}
        QPushButton:disabled {{ color: {t['MUTED']}; background: {t['DISABLED']}; border-color: {t['BORDER']}; }}
        QPushButton[role="go"] {{ background: {t['INDIGO']}; color: white; font-weight: 600; border: none; }}
        QPushButton[role="go"]:hover {{ background: {t['INDIGO2']}; }}
        QPushButton[role="stop-ready"] {{ background: {t['RED']}; color: white; border: none; }}
        QPushButton[role="stop-ready"]:hover {{ background: {t['RED2']}; }}
        QPushButton[role="pause-ready"] {{ background: {t['AMBER']}; color: white; border: none; }}
        QPushButton[role="pause-ready"]:hover {{ background: {t['AMBER2']}; }}
        QPushButton[role="resume-ready"] {{ background: {t['GREEN']}; color: white; border: none; }}
        QPushButton[role="resume-ready"]:hover {{ background: {t['GREEN2']}; }}
        QPushButton[role="theme"] {{ background: {t['CARD']}; border: 1px solid {t['BORDER']}; }}
        QPushButton[role="kofi"] {{ background: #FF5E5B; color: white; border: none; font-weight: 600; }}
        QPushButton[role="kofi"]:hover {{ background: #F04642; }}
        QPushButton[role="toggle"] {{
            background: transparent; border: none; color: {t['MUTED']}; font-family: '{self.FONT_SEMI}'; text-align: left;
        }}
        QPushButton[role="toggle"]:hover {{ color: {t['INDIGO']}; }}
        QCheckBox {{ background: transparent; }}
        QSlider::groove:horizontal {{ height: 4px; background: {t['FIELD']}; border: 1px solid {t['BORDER']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; background: {t['INDIGO']}; border-radius: 7px; }}
        QProgressBar {{
            background: {t['DISABLED']}; border: none; border-radius: 5px; height: 10px; text-align: center; color: transparent;
        }}
        QProgressBar::chunk {{ background: {t['INDIGO']}; border-radius: 5px; }}
        QProgressBar[state="warn"]::chunk {{ background: {t['AMBER']}; }}
        QProgressBar[state="good"]::chunk {{ background: {t['GREEN']}; }}
        QProgressBar[state="bad"]::chunk {{ background: {t['RED']}; }}
        QTextEdit[role="log"] {{
            background: {t['LOG_BG']}; color: {t['LOG_FG']}; border: 1px solid {t['BORDER']}; border-radius: 4px;
            font-family: '{self.MONO}'; font-size: 9pt;
        }}
        QSplitter::handle {{ background: transparent; margin: 0 2px; }}
        QSplitter::handle:hover {{ background: {t['INDIGO']}; }}
        """
        self.setStyleSheet(qss)
        self.theme_btn.setText("\u2600 Light mode" if self.theme_name == "dark" else "\U0001F319 Dark mode")

    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.apply_theme()

    # ---- build -------------------------------------------------------
    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # header
        header = QHBoxLayout()
        htitle = QVBoxLayout()
        title = QLabel("Open HDR to SDR Converter")
        set_role(title, "title")
        subtitle = QLabel("GPU BT.2390 when libplacebo + Vulkan are available")
        set_role(subtitle, "subtitle")
        htitle.addWidget(title)
        htitle.addWidget(subtitle)
        header.addLayout(htitle)
        header.addStretch(1)

        self.github_btn = QPushButton("GitHub")
        set_role(self.github_btn, "theme")
        self.github_btn.setToolTip(GITHUB_URL)
        self.github_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))
        header.addWidget(self.github_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.kofi_btn = QPushButton("\u2665 Support on Ko-fi")
        set_role(self.kofi_btn, "kofi")
        self.kofi_btn.setToolTip(KOFI_URL)
        self.kofi_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KOFI_URL)))
        header.addWidget(self.kofi_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.theme_btn = QPushButton("\U0001F319 Dark mode")
        set_role(self.theme_btn, "theme")
        self.theme_btn.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(10)
        root.addWidget(self.splitter, 1)

        left_panel = QWidget()
        right_panel = QWidget()
        left = QVBoxLayout(left_panel)
        right = QVBoxLayout(right_panel)
        left.setContentsMargins(0, 0, 0, 0)
        right.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)

        # ---- FILES ----
        files = Card("FILES")
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.src_edit = QLineEdit()
        self.dst_edit = QLineEdit()
        for r, (label, edit, fn) in enumerate((
                ("Source", self.src_edit, self.browse_source),
                ("SDR output", self.dst_edit, self.browse_output))):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(edit, r, 1)
            btn = QPushButton("Browse")
            btn.clicked.connect(fn)
            grid.addWidget(btn, r, 2)
        files.body.addLayout(grid)
        left.addWidget(files)

        # ---- SOURCE ANALYSIS ----
        analysis = Card("SOURCE ANALYSIS")
        self.analysis_label = QLabel("Select a video and click Analyze.")
        self.analysis_label.setWordWrap(True)
        set_role(self.analysis_label, "muted")
        analyze_btn = QPushButton("Analyze")
        analyze_btn.clicked.connect(self.analyze)
        analysis.body.addWidget(self.analysis_label)
        analysis.body.addWidget(analyze_btn, 0, Qt.AlignmentFlag.AlignLeft)
        left.addWidget(analysis)

        # ---- PROGRESS ----
        progress = Card("PROGRESS")
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 1000)  # 0.1% resolution
        self.status_label = QLabel("")
        set_role(self.status_label, "muted")
        self.speed_label = QLabel("")
        self.speed_label.setWordWrap(True)
        set_role(self.speed_label, "muted")
        self.speed_label.hide()
        self.resource_label = QLabel(
            "" if HAVE_PSUTIL else
            "psutil not installed: Pause/Resume, live CPU-core control, and CPU/GPU stats are "
            "unavailable. Install with: pip install psutil (then restart this app).")
        self.resource_label.setWordWrap(True)
        set_role(self.resource_label, "muted")
        if not self.resource_label.text():
            self.resource_label.hide()
        progress.body.addWidget(self.pbar)
        progress.body.addWidget(self.status_label)
        progress.body.addWidget(self.speed_label)
        progress.body.addWidget(self.resource_label)
        left.addWidget(progress)

        # ---- CONTROLS ----
        controls = Card("CONTROLS")
        controls.body.addWidget(QLabel("CPU cores (launch + live)"))
        cf = QHBoxLayout()
        self.cores_spin = QSpinBox()
        self.cores_spin.setRange(1, CPU_COUNT)
        self.cores_spin.setValue(CPU_COUNT)
        self.cores_slider = QSlider(Qt.Orientation.Horizontal)
        self.cores_slider.setRange(1, CPU_COUNT)
        self.cores_slider.setValue(CPU_COUNT)
        self.affinity_btn = QPushButton("Apply cores now")
        self.affinity_btn.clicked.connect(self.apply_live_cores)
        self.cores_spin.valueChanged.connect(self.cores_slider.setValue)
        self.cores_slider.valueChanged.connect(self.cores_spin.setValue)
        cf.addWidget(self.cores_spin)
        cf.addWidget(self.cores_slider, 1)
        cf.addWidget(self.affinity_btn)
        controls.body.addLayout(cf)

        run_row = QHBoxLayout()
        self.go_btn = QPushButton("Start")
        set_role(self.go_btn, "go")
        self.go_btn.clicked.connect(self.start)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        run_row.addWidget(self.go_btn)
        run_row.addWidget(self.pause_btn)
        run_row.addWidget(self.stop_btn)
        controls.body.addLayout(run_row)

        controls.body.addWidget(QLabel("Priority (launch + live)"))
        pf = QHBoxLayout()
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(PRIORITY_LEVELS)
        self.priority_combo.setCurrentText("Balanced")
        loosen_combo(self.priority_combo, 12)
        self.priority_slider = QSlider(Qt.Orientation.Horizontal)
        self.priority_slider.setRange(0, 2)
        self.priority_slider.setValue(1)
        self.priority_btn = QPushButton("Apply priority now")
        self.priority_btn.clicked.connect(self.apply_live_priority)
        self.priority_combo.currentIndexChanged.connect(self.priority_slider.setValue)
        self.priority_slider.valueChanged.connect(self.priority_combo.setCurrentIndex)
        pf.addWidget(self.priority_combo)
        pf.addWidget(self.priority_slider, 1)
        pf.addWidget(self.priority_btn)
        controls.body.addLayout(pf)

        note = QLabel("Both cores and priority apply at Start, and can be changed live with the Apply "
                       "buttons above (ffmpeg can't resize its own thread pool or change its own priority "
                       "mid-run, but the OS can be told which cores it's allowed to use and how it's "
                       "scheduled, which has the same effect).")
        note.setWordWrap(True)
        set_role(note, "muted")
        controls.body.addWidget(note)
        controls.body.addStretch(1)
        left.addWidget(controls, 1)

        # ---- CONVERSION ----
        # Exposed as self.conv_card (not just a local var) so a subclass -
        # e.g. the Pro build's bit-depth selector - can insert into this
        # card without depending on fragile layout-index guessing.
        self.conv_card = conv = Card("CONVERSION")
        conv.body.addWidget(QLabel("Backend"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["FFmpeg", "HandBrakeCLI (experimental)"])
        loosen_combo(self.backend_combo, 14)
        conv.body.addWidget(self.backend_combo)
        self.backend_note = QLabel("")
        self.backend_note.setWordWrap(True)
        set_role(self.backend_note, "muted")
        conv.body.addWidget(self.backend_note)

        conv.body.addWidget(QLabel("Tone mapping (FFmpeg only)"))
        self.method_combo = QComboBox()
        loosen_combo(self.method_combo, 14)
        conv.body.addWidget(self.method_combo)

        conv.body.addWidget(QLabel("Encoder"))
        self.encoder_combo = QComboBox()
        self.encoder_combo.addItems(list(ENCODER_MAP.keys()))
        loosen_combo(self.encoder_combo, 14)
        conv.body.addWidget(self.encoder_combo)

        self.pro_mode_chk = QCheckBox("Pro mode")
        self.pro_mode_chk.toggled.connect(self.toggle_pro_mode)
        conv.body.addWidget(self.pro_mode_chk)
        pro_note = QLabel("Full CRF range, extra tone-mapping curves, brightness trim.")
        pro_note.setWordWrap(True)
        set_role(pro_note, "muted")
        conv.body.addWidget(pro_note)

        self.hwaccel_chk = QCheckBox("Hardware-accelerated decode")
        conv.body.addWidget(self.hwaccel_chk)
        hwaccel_note = QLabel("CUDA / DXVA2 / VideoToolbox \u2014 FFmpeg only.")
        hwaccel_note.setWordWrap(True)
        set_role(hwaccel_note, "muted")
        conv.body.addWidget(hwaccel_note)

        conv.body.addWidget(QLabel("Quality (CRF/CQ)"))
        qf = QHBoxLayout()
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(14, 28)
        self.quality_spin.setValue(18)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(14, 28)
        self.quality_slider.setValue(18)
        self.quality_spin.valueChanged.connect(self.quality_slider.setValue)
        self.quality_slider.valueChanged.connect(self.quality_spin.setValue)
        qf.addWidget(self.quality_spin)
        qf.addWidget(self.quality_slider, 1)
        conv.body.addLayout(qf)
        self.cap_label = QLabel("")
        self.cap_label.setWordWrap(True)
        set_role(self.cap_label, "muted")
        conv.body.addWidget(self.cap_label)

        self.bitrate_label = QLabel("")
        self.bitrate_label.setWordWrap(True)
        set_role(self.bitrate_label, "muted")
        conv.body.addWidget(self.bitrate_label)

        self.brightness_label = QLabel("Brightness boost (-20 to +20, 0 = off)")
        conv.body.addWidget(self.brightness_label)
        self.brightness_spin = QSpinBox()
        self.brightness_spin.setRange(-20, 20)
        self.brightness_spin.setValue(0)
        self.brightness_spin.setFixedWidth(90)
        self.brightness_label.setEnabled(False)
        self.brightness_spin.setEnabled(False)
        conv.body.addWidget(self.brightness_spin, 0, Qt.AlignmentFlag.AlignLeft)

        conv.body.addWidget(QLabel("Output resolution"))
        self.res_combo = QComboBox()
        self.res_combo.addItems([r[0] for r in RESOLUTIONS])
        loosen_combo(self.res_combo, 14)
        self.res_combo.currentTextChanged.connect(self.apply_res_preset)
        conv.body.addWidget(self.res_combo)
        wh = QHBoxLayout()
        wcol = QVBoxLayout()
        wcol.addWidget(QLabel("Width"))
        self.res_w_edit = QLineEdit()
        self.res_w_edit.setFixedWidth(120)
        # Fixes a Qt rendering glitch (seen in frozen/PyInstaller builds) where
        # typed characters leave ghosted/overlapping pixels behind instead of
        # a clean repaint - force a full repaint on every keystroke.
        self.res_w_edit.textChanged.connect(lambda _=None: self.res_w_edit.repaint())
        wcol.addWidget(self.res_w_edit)
        hcol = QVBoxLayout()
        hcol.addWidget(QLabel("Height"))
        self.res_h_edit = QLineEdit()
        self.res_h_edit.setFixedWidth(120)
        self.res_h_edit.textChanged.connect(lambda _=None: self.res_h_edit.repaint())
        hcol.addWidget(self.res_h_edit)
        wh.addLayout(wcol)
        wh.addLayout(hcol)
        wh.addStretch(1)
        conv.body.addLayout(wh)
        res_note = QLabel("Leave one blank to keep aspect ratio; leave both blank for no scaling.")
        res_note.setWordWrap(True)
        set_role(res_note, "muted")
        conv.body.addWidget(res_note)
        conv.body.addStretch(1)

        right.addWidget(conv, 1)

        # ---- Activity / Capabilities toggles ----
        headers = QHBoxLayout()
        self.activity_btn = QPushButton("\u25b8 Activity")
        set_role(self.activity_btn, "toggle")
        self.activity_btn.clicked.connect(self.toggle_activity)
        self.caps_btn = QPushButton("\u25b8 Capabilities")
        set_role(self.caps_btn, "toggle")
        self.caps_btn.clicked.connect(self.toggle_caps)
        headers.addWidget(self.activity_btn)
        headers.addWidget(self.caps_btn)
        headers.addStretch(1)
        root.addLayout(headers)

        self.activity_card = QFrame()
        self.activity_card.setProperty("role", "card")
        acard_layout = QVBoxLayout(self.activity_card)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        set_role(self.log, "log")
        self.log.setFixedHeight(150)
        acard_layout.addWidget(self.log)
        root.addWidget(self.activity_card)
        self.activity_card.hide()

        self.caps_card = QFrame()
        self.caps_card.setProperty("role", "card")
        ccard_layout = QVBoxLayout(self.caps_card)
        self.caps_label = QLabel("")
        set_role(self.caps_label, "mono")
        self.caps_label.setWordWrap(True)
        ccard_layout.addWidget(self.caps_label)
        if sys.platform.startswith("win"):
            setup_row = QHBoxLayout()
            self.setup_btn = QPushButton("Install missing (winget)")
            self.setup_btn.clicked.connect(self.start_dependency_setup)
            setup_row.addWidget(self.setup_btn)
            setup_row.addStretch(1)
            ccard_layout.addLayout(setup_row)
            setup_note = QLabel("Installs FFmpeg and/or HandBrakeCLI via winget if missing. Progress "
                                 "streams into Activity. Windows may show a permission (UAC) prompt "
                                 "during install \u2014 that's normal.")
            setup_note.setWordWrap(True)
            set_role(setup_note, "muted")
            ccard_layout.addWidget(setup_note)
        root.addWidget(self.caps_card)
        self.caps_card.hide()

        # ---- bitrate estimate: keep it live as the relevant settings change --
        self.quality_spin.valueChanged.connect(self.update_bitrate_estimate)
        self.encoder_combo.currentTextChanged.connect(self.update_bitrate_estimate)
        self.backend_combo.currentTextChanged.connect(self.update_bitrate_estimate)
        self.res_w_edit.textChanged.connect(self.update_bitrate_estimate)
        self.res_h_edit.textChanged.connect(self.update_bitrate_estimate)
        self.update_bitrate_estimate()

        # Size to actual content rather than a fixed guess - both Activity
        # and Capabilities are collapsed at this point, so this naturally
        # produces a compact window that fits low-resolution screens; it
        # grows on demand when either panel is expanded (see refit_window).
        self.layout().activate()
        self.splitter.setSizes([730, 350])
        # A bit of extra headroom beyond the raw sizeHint - on some
        # platforms/DPI settings the hint comes in a touch short of what's
        # actually needed once the window is realized on screen, which
        # clips the bottom-most row(s) on first open.
        self.resize(1080, max(560, self.sizeHint().height() + 24))
        self._did_first_show_relayout = False

    # ---- one-time layout fix on first real show ------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if not self._did_first_show_relayout:
            self._did_first_show_relayout = True
            # Some nested rows (e.g. the Width/Height fields) only get their
            # correct pixel widths after a layout pass that runs with real,
            # on-screen geometry. Toggling Activity/Capabilities visible
            # forced that pass, but it also permanently grew the window to
            # fit their content (a fixed-height log box) and never shrank
            # back after hiding them again. A trivial resize nudge forces
            # the same kind of relayout without touching those panels at
            # all, so the window itself stays at its intended compact size.
            QTimer.singleShot(0, self._force_full_relayout)

    def _force_full_relayout(self):
        size = self.size()
        self.resize(size.width(), size.height() + 1)
        self.resize(size.width(), max(size.height(), self.sizeHint().height() + 24))
        # The Width/Height fields are fixed-size now, so the window resize
        # above never actually touches their geometry and can't fix their
        # first-paint glitch on its own - repaint them directly too.
        self.res_w_edit.style().unpolish(self.res_w_edit)
        self.res_w_edit.style().polish(self.res_w_edit)
        self.res_w_edit.repaint()
        self.res_h_edit.style().unpolish(self.res_h_edit)
        self.res_h_edit.style().polish(self.res_h_edit)
        self.res_h_edit.repaint()

    # ---- resolution presets -------------------------------------------
    def apply_res_preset(self, text):
        for label, w, h in RESOLUTIONS:
            if label == text:
                if w is not None:
                    self.res_w_edit.setText(w)
                    self.res_h_edit.setText(h)
                return

    # ---- bitrate estimate ----------------------------------------------
    def update_bitrate_estimate(self):
        v = self.encoder_combo.currentText()
        if v not in ENCODER_MAP:
            self.bitrate_label.setText("")
            return
        q = self.quality_spin.value()
        w_txt, h_txt = self.res_w_edit.text().strip(), self.res_h_edit.text().strip()
        w = int(w_txt) if w_txt.isdigit() else self.src_width
        h = int(h_txt) if h_txt.isdigit() else self.src_height
        kbps = estimate_bitrate_kbps(v, q, w, h)
        mbps = kbps / 1000.0
        text = f"Approx. output bitrate: ~{mbps:.1f} Mbps"
        if self.duration:
            size_gb = (kbps * 1000.0 / 8.0) * self.duration / (1024 ** 3)
            unit = "GB" if size_gb >= 0.1 else "MB"
            size_val = size_gb if unit == "GB" else size_gb * 1024
            text += f" \u2192 ~{size_val:.1f} {unit} for this source"
        is_hb_hw = (self.backend_combo.currentText().startswith("HandBrake")
                    and not v.startswith("CPU"))
        caveat = ("HandBrake's hardware-encoder quality scale isn't directly "
                  "comparable to CRF, so this is even rougher than usual. " if is_hb_hw else "")
        text += f". {caveat}Rough guide only \u2014 actual bitrate depends heavily on content complexity."
        self.bitrate_label.setText(text)

    # ---- pro mode --------------------------------------------------
    def toggle_pro_mode(self, checked):
        if checked:
            self.quality_spin.setRange(0, 51)
            self.quality_slider.setRange(0, 51)
            self.brightness_label.setEnabled(True)
            self.brightness_spin.setEnabled(True)
        else:
            self.quality_spin.setRange(14, 28)
            self.quality_slider.setRange(14, 28)
            if not (14 <= self.quality_spin.value() <= 28):
                self.quality_spin.setValue(18)
            self.brightness_spin.setValue(0)
            self.brightness_label.setEnabled(False)
            self.brightness_spin.setEnabled(False)
        self.refresh_method_choices()
        self.update_bitrate_estimate()

    # ---- collapse toggles -----------------------------------------
    def toggle_activity(self):
        self.setUpdatesEnabled(False)
        if self.activity_card.isVisible():
            self.activity_card.hide()
            self.activity_btn.setText("\u25b8 Activity")
        else:
            self.activity_card.show()
            self.activity_btn.setText("\u25be Activity")
        self.refit_window()

    def toggle_caps(self):
        # Freeze before check() runs, not just before the resize: check()
        # updates several labels outside the Capabilities panel itself
        # (cap_label, backend_note, the status label), and each of those
        # setText() calls reflows the always-visible Conversion card while
        # repaints are still enabled - that's what caused the window to
        # visibly creep upward in a couple of small steps before the
        # deferred resize even ran.
        self.setUpdatesEnabled(False)
        if self.caps_card.isVisible():
            self.caps_card.hide()
            self.caps_btn.setText("\u25b8 Capabilities")
        else:
            self.check()
            self.caps_label.setText(self.caps_summary)
            self.caps_card.show()
            self.caps_btn.setText("\u25be Capabilities")
        self.refit_window()

    def refit_window(self):
        """Re-fit the window to its current content after a panel is
        shown/hidden. hide()/show() alone frees or claims layout space, but
        a top-level widget that's already been explicitly resize()d doesn't
        shrink or grow on its own just because a child layout item changed -
        without this, collapsing a panel leaves its old blank space behind
        instead of reclaiming it. The singleShot(0, ...) lets the layout
        actually process the visibility change first; calling adjustSize()
        immediately would still measure the old (pre-change) geometry.

        Repaints are already frozen by the caller (toggle_activity/
        toggle_caps) before any content changed, so nothing paints at an
        intermediate size/position until _do_refit re-enables updates."""
        QTimer.singleShot(0, self._do_refit)

    def _do_refit(self):
        pos = self.pos()
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height() + 24)
        # Growing the window can push its bottom edge off-screen, and some
        # window managers respond by repositioning the window upward to
        # keep it visible - resize() itself doesn't move it, but the WM's
        # own "keep on screen" nudge does, and it doesn't undo itself when
        # the window shrinks back down. Re-pinning the top-left corner
        # after every resize stops that nudge from accumulating across
        # repeated open/close toggles.
        self.move(pos)
        self.setUpdatesEnabled(True)

    # ---- state / status helper --------------------------------------
    def set_state(self, kind, text):
        self.status_label.setText(text)
        role = {"idle": "muted", "run": "info", "paused": "warn", "done": "good", "error": "bad"}[kind]
        bar_state = {"idle": "", "run": "", "paused": "warn", "done": "good", "error": "bad"}[kind]
        set_role(self.status_label, role)
        self.pbar.setProperty("state", bar_state)
        self.pbar.style().unpolish(self.pbar)
        self.pbar.style().polish(self.pbar)

    def update_run_controls(self):
        running = self.proc is not None
        self.go_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        set_role(self.stop_btn, "stop-ready" if running else "")
        if self.paused:
            self.pause_btn.setText("Resume")
            set_role(self.pause_btn, "resume-ready" if running else "")
        else:
            self.pause_btn.setText("Pause")
            set_role(self.pause_btn, "pause-ready" if running else "")
        self.pause_btn.setEnabled(running)
        self.affinity_btn.setEnabled(running)
        self.priority_btn.setEnabled(running)

    # ---- ffmpeg capability check -------------------------------------
    def check(self):
        self.encoders = set()
        self.has_bt2390 = False
        try:
            note = []
            if self.handbrake_tool:
                # Insert invisible break points after path separators - a raw
                # path like "C:\Users\...\HandBrakeCLI.EXE" has no spaces, so
                # QLabel word-wrap treats it as one unbreakable word and
                # forces the whole panel to be at least that wide. A
                # zero-width space is invisible but gives the wrapper
                # somewhere to break the line.
                wrappable_path = self.handbrake_tool.replace("\\", "\\\u200b")
                note.append(f"HandBrakeCLI detected: {wrappable_path}")
            else:
                note.append("HandBrakeCLI not found on PATH (backend disabled until installed).")
            self.backend_note.setText("\n".join(note))

            f = exe("ffmpeg")
            if not f:
                self.set_state("error", "FFmpeg not found.")
                return
            filters = subprocess.check_output(
                [f, "-hide_banner", "-filters"], text=True, encoding="utf-8", errors="replace",
                stderr=subprocess.STDOUT, **no_window_kwargs()).lower()
            helptext = subprocess.check_output(
                [f, "-hide_banner", "-h", "filter=libplacebo"], text=True, encoding="utf-8", errors="replace",
                stderr=subprocess.STDOUT, **no_window_kwargs()).lower() if "libplacebo" in filters else ""
            self.has_bt2390 = "libplacebo" in filters and "bt.2390" in helptext
            self.has_st2094 = self.has_bt2390 and "st2094-40" in helptext
            enc = subprocess.check_output(
                [f, "-hide_banner", "-encoders"], text=True, encoding="utf-8", errors="replace",
                stderr=subprocess.STDOUT, **no_window_kwargs())
            self.encoders = set(enc.split())
            self.refresh_method_choices()
            if self.has_bt2390:
                self.cap_label.setText("\u2713 True BT.2390 available via libplacebo/Vulkan.\nIt tone-maps and converts color on the GPU.")
                self.set_state("idle", "FFmpeg ready \u00b7 libplacebo/Vulkan BT.2390 enabled")
            else:
                self.cap_label.setText("BT.2390 unavailable: this FFmpeg has no usable libplacebo filter.\nHable, Reinhard, and Mobius remain available.")
                self.set_state("idle", "FFmpeg ready \u00b7 standard tone mapping only")
        except (OSError, subprocess.CalledProcessError) as e:
            self.set_state("error", f"FFmpeg capability check failed: {e}")
        finally:
            self.refresh_caps_summary()

    def refresh_method_choices(self):
        values = []
        if self.has_bt2390:
            values.append("BT.2390 \u00b7 GPU libplacebo/Vulkan")
            if self.pro_mode_chk.isChecked():
                values += list(TONEMAP_PRO_GPU)
                if self.has_st2094:
                    values += list(TONEMAP_PRO_GPU_ST2094)
        values += ["Hable \u00b7 Standard FFmpeg", "Reinhard \u00b7 Standard FFmpeg", "Mobius \u00b7 Standard FFmpeg"]
        if self.pro_mode_chk.isChecked():
            values += list(TONEMAP_PRO_CPU)
        current = self.method_combo.currentText()
        self.method_combo.blockSignals(True)
        self.method_combo.clear()
        self.method_combo.addItems(values)
        self.method_combo.blockSignals(False)
        if current in values:
            self.method_combo.setCurrentText(current)
        elif values:
            self.method_combo.setCurrentIndex(0)

    def refresh_caps_summary(self):
        def yn(ok):
            return "\u2713" if ok else "\u2717"
        lines = [
            f"{yn(bool(exe('ffmpeg')))} FFmpeg \u2014 {exe('ffmpeg') or 'not found, required for the FFmpeg backend'}",
            f"{yn(self.has_bt2390)} BT.2390 GPU tonemap (libplacebo/Vulkan)"
            + ("" if self.has_bt2390 else " \u2014 unavailable, Hable/Reinhard/Mobius still work"),
            "",
            "FFmpeg encoders:",
        ]
        for label, name in (("CPU H.264", "libx264"), ("CPU H.265", "libx265"),
                             ("NVIDIA NVENC H.264", "h264_nvenc"), ("NVIDIA NVENC H.265", "hevc_nvenc"),
                             ("AMD AMF H.264", "h264_amf"), ("AMD AMF H.265", "hevc_amf"),
                             ("Apple VideoToolbox H.264", "h264_videotoolbox"),
                             ("Apple VideoToolbox H.265", "hevc_videotoolbox")):
            lines.append(f"   {yn(name in self.encoders)} {label}")
        lines += [
            "",
            f"{yn(bool(self.handbrake_tool))} HandBrakeCLI (experimental backend)"
            + (f" \u2014 {self.handbrake_tool}" if self.handbrake_tool else " \u2014 not found on PATH"),
            f"{yn(HAVE_PSUTIL)} psutil (Pause/Resume, live CPU-core/priority control)"
            + ("" if HAVE_PSUTIL else " \u2014 install with: pip install psutil"),
            f"{yn(bool(self.gpu_tool))} nvidia-smi (live GPU utilization stats)"
            + ("" if self.gpu_tool else " \u2014 not found, GPU stats will be unavailable"),
        ]
        self.caps_summary = "\n".join(lines)
        if self.caps_card.isVisible():
            self.caps_label.setText(self.caps_summary)

    # ---- one-click dependency setup (Windows / winget) -----------------
    def start_dependency_setup(self):
        if self.proc is not None or getattr(self, "_setup_proc", None) is not None:
            QMessageBox.information(self, "Busy", "Finish or stop the current conversion/setup first.")
            return
        winget = shutil.which("winget")
        if not winget:
            QMessageBox.critical(
                self, "winget not found",
                "Windows Package Manager (winget) isn't available.\n\n"
                "Install 'App Installer' from the Microsoft Store, then try again:\n"
                "https://apps.microsoft.com/detail/9nblggh4nns1")
            return
        todo = []
        if not exe("ffmpeg"):
            todo.append(("Gyan.FFmpeg", "FFmpeg"))
        if not self.handbrake_tool:
            todo.append(("HandBrake.HandBrake.CLI", "HandBrakeCLI"))
        if not todo:
            QMessageBox.information(self, "Nothing to install", "FFmpeg and HandBrakeCLI are already installed.")
            return
        self._setup_queue = todo
        self.setup_btn.setEnabled(False)
        if not self.activity_card.isVisible():
            self.toggle_activity()
        self.write(f"\n[setup] Installing {len(todo)} package(s) via winget \u2014 a Windows permission "
                    "prompt may appear; approve it to continue.\n")
        self._run_next_setup_item()

    def _run_next_setup_item(self):
        if not self._setup_queue:
            self.write("[setup] Done. Re-checking capabilities\u2026\n")
            refresh_windows_path()
            self.handbrake_tool = exe("HandBrakeCLI")
            self.check()
            self.setup_btn.setEnabled(True)
            QMessageBox.information(self, "Setup complete", "Dependency installation finished \u2014 see Activity log for details.")
            return
        pkg_id, name = self._setup_queue.pop(0)
        self.write(f"[setup] Installing {name} ({pkg_id})\u2026\n")
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.readyReadStandardOutput.connect(lambda p=p: self.write(
            bytes(p.readAllStandardOutput()).decode("utf-8", errors="replace")))
        p.finished.connect(lambda code, status, name=name: self._on_setup_item_finished(code, name))
        p.errorOccurred.connect(lambda err, name=name: self._on_setup_item_error(name))
        p.setProgram("winget")
        p.setArguments(["install", "--id", pkg_id, "-e", "--silent",
                         "--accept-package-agreements", "--accept-source-agreements"])
        self._setup_proc = p
        p.start()

    def _on_setup_item_finished(self, code, name):
        if code == 0:
            self.write(f"[setup] {name} installed.\n")
        else:
            self.write(f"[setup] {name} install exited with code {code} \u2014 see output above.\n")
        refresh_windows_path()
        self._setup_proc = None
        self._run_next_setup_item()

    def _on_setup_item_error(self, name):
        self.write(f"[setup] Failed to launch winget for {name}.\n")
        self._setup_proc = None
        self.setup_btn.setEnabled(True)

    # ---- file pickers ---------------------------------------------
    def browse_source(self):
        x, _ = QFileDialog.getOpenFileName(
            self, "Choose source video", "",
            "Video (*.mkv *.mp4 *.mov *.m4v *.ts *.webm);;All files (*.*)")
        if x:
            self.src_edit.setText(x)
            p = Path(x)
            self.dst_edit.setText(str(p.with_name(p.stem + "_SDR.mp4")))
            self.kind = "unknown"
            self.duration = 0.0
            self.src_width = None
            self.src_height = None
            self.analysis_label.setText("Ready to analyze this source.")
            self.update_bitrate_estimate()

    def browse_output(self):
        cur = self.dst_edit.text()
        if cur:
            p = Path(cur)
            initdir = str(p.parent) if p.parent.is_dir() else str(Path.home())
            initfile = p.name
        elif self.src_edit.text():
            sp = Path(self.src_edit.text())
            initdir = str(sp.parent) if sp.parent.is_dir() else str(Path.home())
            initfile = sp.stem + "_SDR.mp4"
        else:
            initdir, initfile = str(Path.home()), "output_SDR.mp4"
        x, _ = QFileDialog.getSaveFileName(
            self, "Choose SDR output", str(Path(initdir) / initfile), "MP4 (*.mp4);;MKV (*.mkv)")
        if x:
            self.dst_edit.setText(x)

    # ---- analyze ----------------------------------------------------
    def analyze(self):
        if not Path(self.src_edit.text()).is_file():
            QMessageBox.critical(self, "Source", "Choose a source video first.")
            return
        p = exe("ffprobe")
        if not p:
            QMessageBox.critical(self, "FFprobe missing", "Bundle ffprobe with FFmpeg or install FFmpeg.")
            return
        try:
            d = json.loads(subprocess.check_output(
                [p, "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json",
                 self.src_edit.text()],
                text=True, encoding="utf-8", errors="replace", **no_window_kwargs()))
            s = d["streams"][0]
            fmt_dur = d.get("format", {}).get("duration")
            self.duration = float(fmt_dur or s.get("duration") or 0)
            self.src_width = s.get("width")
            self.src_height = s.get("height")
            t = (s.get("color_transfer") or "unknown").lower()
            raw = json.dumps(s).lower()
            if "dovi" in raw or "dolby vision" in raw:
                self.kind = "dolby"
                note = "Dolby Vision detected. A compatible HDR10 base layer is required for dependable SDR output."
            elif t in ("smpte2084", "pq", "arib-std-b67", "hlg"):
                self.kind = "hdr"
                note = "HDR detected. Tone mapping is appropriate."
            else:
                self.kind = "sdr"
                note = "Likely SDR/Rec.709. Tone mapping is normally unnecessary."
            dur_note = f"{format_time(self.duration)} ({self.duration:.0f}s)" if self.duration else "unknown (progress/ETA will be limited)"
            self.analysis_label.setText(f"{note} Transfer: {t}; duration: {dur_note}.")
            self.update_bitrate_estimate()
        except UnicodeDecodeError as e:
            self.analysis_label.setText(f"Analysis failed reading ffprobe's output ({e}). "
                                         "This is usually a corrupted/unusual metadata tag in the file itself, "
                                         "not a problem with the video streams - conversion can often still proceed.")
        except Exception as e:
            self.analysis_label.setText(f"Analysis failed: {e}")

    # ---- command builders --------------------------------------------
    def scale_args_ffmpeg(self):
        w, h = self.res_w_edit.text().strip(), self.res_h_edit.text().strip()
        if not w and not h:
            return ""
        ws = str(even(w)) if w else "-2"
        hs = str(even(h)) if h else "-2"
        return f",scale={ws}:{hs}:flags=lanczos"

    def brightness_args_ffmpeg(self):
        val = self.brightness_spin.value() if self.pro_mode_chk.isChecked() else 0
        if val == 0:
            return ""
        return f",eq=brightness={val / 20.0:.4f}"

    def tonemap_lookup(self):
        all_curves = {**TONEMAP_BASE, **TONEMAP_PRO_GPU, **TONEMAP_PRO_GPU_ST2094, **TONEMAP_PRO_CPU}
        return all_curves.get(self.method_combo.currentText(), ("cpu", "hable"))

    def encode(self):
        v = self.encoder_combo.currentText()
        e = ENCODER_MAP[v]
        q = str(self.quality_spin.value())
        if v.startswith("CPU"):
            opts = ["-crf", q, "-preset", "medium"]
        elif v.startswith("NVIDIA"):
            # ffmpeg's nvenc "-cq" option treats 0 as "automatic" (i.e. NOT
            # "best quality") - it silently drops out of constant-quality
            # mode and falls back to a default bitrate-based rate control,
            # which is what caused the 50GB -> 6GB / worse-quality surprise.
            # Remap 0 to 1, the actual best usable -cq value, and force true
            # constant-quality mode with -rc vbr -b:v 0 so -cq is honored
            # instead of being capped by a hidden default bitrate.
            cq = "1" if self.quality_spin.value() == 0 else q
            opts = ["-rc", "vbr", "-cq", cq, "-b:v", "0"]
        elif v.startswith("AMD"):
            opts = ["-rc", "cqp", "-qp_i", q, "-qp_p", q]
        else:
            opts = ["-q:v", q]
        cores = self.cores_spin.value()
        if v.startswith("CPU") and 0 < cores < CPU_COUNT:
            opts = opts + ["-threads", str(cores)]
        return e, opts

    def command_ffmpeg(self):
        e, opt = self.encode()
        base = [exe("ffmpeg"), "-hide_banner", "-y"]
        if self.hwaccel_chk.isChecked():
            base += ["-hwaccel", "auto"]
        base += ["-progress", "pipe:1", "-nostats", "-i", self.src_edit.text(), "-map", "0:v:0", "-map", "0:a?"]
        engine, code = self.tonemap_lookup()
        if engine == "gpu":
            vf = f"libplacebo=tonemapping={code}:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p"
        else:
            vf = f"zscale=t=linear:npl=100,format=gbrpf32le,tonemap=tonemap={code}:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p"
        vf += self.scale_args_ffmpeg()
        vf += self.brightness_args_ffmpeg()
        return base + ["-vf", vf, "-c:v", e, *opt, "-c:a", "copy", "-movflags", "+faststart", self.dst_edit.text()]

    def command_handbrake(self):
        e = HB_ENCODER_MAP[self.encoder_combo.currentText()]
        cmd = [self.handbrake_tool, "-i", self.src_edit.text(), "-o", self.dst_edit.text(),
               "-e", e, "-q", str(self.quality_spin.value()), "-M", "709", "-E", "copy"]
        w, h = self.res_w_edit.text().strip(), self.res_h_edit.text().strip()
        if w:
            cmd += ["-X", str(even(w))]
        if h:
            cmd += ["-Y", str(even(h))]
        if self.hwaccel_chk.isChecked() and e.startswith("nvenc"):
            cmd += ["--enable-hw-decoding", "nvdec"]
        return cmd

    def command(self):
        return self.command_handbrake() if self.backend_combo.currentText().startswith("HandBrake") else self.command_ffmpeg()

    # ---- run / control -------------------------------------------------
    def start(self):
        if self.proc is not None:
            QMessageBox.information(self, "Already running", "A conversion is already in progress.")
            return
        if getattr(self, "_setup_proc", None) is not None:
            QMessageBox.information(self, "Setup in progress", "Wait for dependency installation to finish first.")
            return
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        if using_hb and not self.handbrake_tool:
            QMessageBox.critical(self, "HandBrakeCLI not found",
                                  "Install HandBrakeCLI and ensure it's on PATH, or switch the backend to FFmpeg.")
            return
        if not Path(self.src_edit.text()).is_file() or not self.dst_edit.text():
            QMessageBox.critical(self, "Files", "Choose source and output files.")
            return
        if not using_hb and not exe("ffmpeg"):
            QMessageBox.critical(self, "FFmpeg", "FFmpeg is required.")
            return
        if self.kind == "unknown":
            self.analyze()
        if self.kind == "sdr":
            QMessageBox.information(self, "Already SDR", "This source appears to be SDR; conversion was not started.")
            return
        if self.kind == "dolby":
            if QMessageBox.question(self, "Dolby Vision",
                                     "Continue only if it contains an HDR10-compatible base layer?") != QMessageBox.StandardButton.Yes:
                return
        if not using_hb:
            engine, code = self.tonemap_lookup()
            if engine == "gpu" and not self.has_bt2390:
                QMessageBox.critical(self, "GPU tonemap unavailable",
                                      "This FFmpeg lacks libplacebo/Vulkan support. Use a Standard FFmpeg curve "
                                      "or install a libplacebo-enabled FFmpeg build.")
                return
            if code in ("st2094-40", "st2094-10") and not self.has_st2094:
                QMessageBox.critical(self, "Curve unavailable",
                                      f"This libplacebo build doesn't support {code} (needs a newer FFmpeg/libplacebo). "
                                      "Try BT.2390 or BT.2446A instead.")
                return
            e, _ = self.encode()
            if e not in self.encoders:
                QMessageBox.critical(self, "Encoder unavailable", f"{e} is not available in this FFmpeg build or GPU driver.")
                return
        for label, val in (("Width", self.res_w_edit.text().strip()), ("Height", self.res_h_edit.text().strip())):
            if val:
                try:
                    assert int(val) > 0
                except (ValueError, AssertionError):
                    QMessageBox.critical(self, "Output resolution", f"{label} must be a positive number, or leave it blank.")
                    return

        self.stopping = False
        self.paused = False
        self.pbar.setValue(0)
        self.speed_label.setText("")
        self.speed_label.hide()
        self.using_hb = using_hb
        self.out_buf = ""
        self.block = {}

        cmd = self.command()
        if not self.activity_card.isVisible():
            self.toggle_activity()
        self.write("$ " + subprocess.list2cmdline(cmd) + "\n")
        self.set_state("run", "Converting\u2026 0%")

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_ready_read)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_process_error)
        self.proc.setProgram(cmd[0])
        self.proc.setArguments(cmd[1:])
        self.proc.start()
        if not self.proc.waitForStarted(3000):
            QMessageBox.critical(self, "Could not start", "Failed to start the encoder process.")
            self.set_state("error", "Failed to start")
            self.proc = None
            return

        self.proc_psutil = None
        if HAVE_PSUTIL:
            try:
                self.proc_psutil = psutil.Process(self.proc.processId())
                self.proc_psutil.cpu_percent(interval=None)  # first call is meaningless by design; primes it
                # Apply launch priority now that we have a real pid (QProcess has no
                # creationflags/preexec_fn hook the way subprocess.Popen does).
                self._set_priority(self.proc_psutil, self.priority_combo.currentText(), quiet=True)
            except Exception:
                self.proc_psutil = None

        self.update_run_controls()
        QTimer.singleShot(1000, self.sample_resources)

    def _set_priority(self, pp, level, quiet=False):
        try:
            if sys.platform.startswith("win"):
                m = {"Efficiency (low)": getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", None),
                     "Balanced": getattr(psutil, "NORMAL_PRIORITY_CLASS", None),
                     "Performance (high)": getattr(psutil, "HIGH_PRIORITY_CLASS", None)}
                val = m.get(level)
                if val is not None:
                    pp.nice(val)
            else:
                pp.nice(PRIORITY_NICE.get(level, 0))
            if not quiet:
                self.write(f"[live] Priority set to {level}.\n")
        except Exception as e:
            if not quiet:
                self.write(f"[live] Priority apply failed: {e}\n")
                QMessageBox.critical(self, "Live priority", str(e))

    # ---- QProcess signal handlers --------------------------------------
    def _on_ready_read(self):
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.out_buf += data
        parts = re.split(r"[\r\n]", self.out_buf)
        self.out_buf = parts.pop()  # keep the trailing incomplete line buffered
        for line in parts:
            if line:
                self._handle_line(line)

    def _handle_line(self, line):
        if self.using_hb:
            m = _HB_PROGRESS.search(line)
            if m:
                pct = min(99.9, float(m.group(1)))
                fps = parse_num(m.group(2))
                eta_sec = int(m.group(4)) * 3600 + int(m.group(5)) * 60 + int(m.group(6)) if m.group(4) else None
                self.apply_stats({"pct": pct, "speed_x": None, "mbps": None, "fps": fps, "eta_sec": eta_sec})
            elif line.strip():
                self.write(line + "\n")
        else:
            key = line.split("=", 1)[0] if "=" in line else ""
            if key in PROGRESS_KEYS or key.startswith("stream_"):
                self.block[key] = line.split("=", 1)[1] if "=" in line else ""
                if key == "progress":
                    self.apply_stats(self.ffmpeg_block_to_stats(self.block))
                    self.block = {}
            elif line.strip():
                self.write(line + "\n")

    def ffmpeg_block_to_stats(self, block):
        out_ms = parse_num(block.get("out_time_ms", ""))
        pct = None
        if self.duration and out_ms is not None:
            pct = min(99.9, out_ms / 1e6 / self.duration * 100)
        speed_x = parse_num(block.get("speed", ""))
        kbps = parse_num(block.get("bitrate", ""))
        mbps = kbps / 8000 if kbps is not None else None  # kbit/s -> MB/s
        eta_sec = None
        if pct is not None and speed_x and self.duration:
            eta_sec = self.duration * (100 - pct) / 100 / speed_x
        return {"pct": pct, "speed_x": speed_x, "mbps": mbps, "fps": parse_num(block.get("fps", "")), "eta_sec": eta_sec}

    def apply_stats(self, stats):
        pct = stats.get("pct")
        if pct is not None:
            self.pbar.setValue(int(pct * 10))
            self.set_state("paused" if self.paused else "run", f"Converting\u2026 {pct:.0f}%")
        parts = []
        if stats.get("speed_x") is not None:
            parts.append(f"{stats['speed_x']:.2f}x realtime")
        if stats.get("fps") is not None:
            parts.append(f"{stats['fps']:.0f} fps")
        if stats.get("mbps") is not None:
            parts.append(f"{stats['mbps']:.1f} MB/s")
        if stats.get("eta_sec") is not None:
            parts.append(f"ETA {format_time(stats['eta_sec'])}")
        self.speed_label.setText(" \u00b7 ".join(parts))
        self.speed_label.setVisible(bool(parts))

    def _on_process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.proc is not None:
            self.write("\nCould not run the encoder.\n")
            self.set_state("error", "Failed to start")
            self.proc = None
            self.update_run_controls()

    def _on_finished(self, exit_code, exit_status):
        was_stopping = self.stopping
        if was_stopping:
            self.write("\nConversion stopped by user.\n")
            ok = False
        elif exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self.write("\nFinished successfully.\n")
            ok = True
        else:
            self.write(f"\nConversion failed (exit code {exit_code}); see log.\n")
            ok = False
        self.proc = None
        self.proc_psutil = None
        self.stopping = False
        self.paused = False
        if ok:
            self.pbar.setValue(1000)
            self.set_state("done", "Done \u00b7 saved to output")
        elif was_stopping:
            self.set_state("idle", "Stopped")
        else:
            self.set_state("error", "Failed \u2014 see activity log")
        self.update_run_controls()

    def sample_resources(self):
        if not (self.proc and self.proc.state() == QProcess.ProcessState.Running):
            return
        parts = []
        if HAVE_PSUTIL and self.proc_psutil is not None:
            try:
                parts.append(f"This process: {self.proc_psutil.cpu_percent(interval=None):.0f}% CPU")
                parts.append(f"System: {psutil.cpu_percent(interval=None):.0f}% CPU")
            except Exception:
                pass
        if self.gpu_tool:
            try:
                out = subprocess.check_output(
                    [self.gpu_tool, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                    text=True, encoding="utf-8", errors="replace", timeout=1, **no_window_kwargs()).strip().splitlines()
                if out:
                    parts.append(f"GPU: {out[0]}%")
            except Exception:
                pass
        if parts:
            self.resource_label.setText(" \u00b7 ".join(parts))
            self.resource_label.show()
        QTimer.singleShot(1000, self.sample_resources)

    # ---- live controls --------------------------------------------------
    def require_psutil(self, feature):
        if HAVE_PSUTIL:
            return True
        msg = f"{feature} requires the psutil package, which isn't installed.\n\nInstall it with:\n\n    pip install psutil\n\nthen restart this app."
        self.write(f"[live] {feature} unavailable: psutil is not installed.\n")
        QMessageBox.information(self, "psutil required", msg)
        return False

    def apply_live_cores(self):
        if not (self.proc and self.proc.state() == QProcess.ProcessState.Running):
            return
        if not self.require_psutil("Live CPU-core control"):
            return
        try:
            n = max(1, min(CPU_COUNT, self.cores_spin.value()))
            psutil.Process(self.proc.processId()).cpu_affinity(list(range(n)))
            self.write(f"[live] CPU cores limited to {n} of {CPU_COUNT} (affinity).\n")
        except (AttributeError, NotImplementedError):
            QMessageBox.information(self, "Unavailable", "CPU affinity control isn't supported on this OS (e.g. macOS).")
        except Exception as e:
            self.write(f"[live] CPU core apply failed: {e}\n")
            QMessageBox.critical(self, "Live CPU cores", str(e))

    def apply_live_priority(self):
        if not (self.proc and self.proc.state() == QProcess.ProcessState.Running):
            return
        if not self.require_psutil("Live priority control"):
            return
        try:
            pp = psutil.Process(self.proc.processId())
            self._set_priority(pp, self.priority_combo.currentText())
        except Exception as e:
            self.write(f"[live] Priority apply failed: {e}\n")
            QMessageBox.critical(self, "Live priority", str(e))

    def toggle_pause(self):
        if not (self.proc and self.proc.state() == QProcess.ProcessState.Running):
            return
        if not self.require_psutil("Pause/Resume"):
            return
        try:
            pp = psutil.Process(self.proc.processId())
            if self.paused:
                pp.resume()
                self.paused = False
                self.set_state("run", self.status_label.text().replace("Paused", "Converting\u2026"))
                self.write("[live] Resumed.\n")
            else:
                pp.suspend()
                self.paused = True
                self.set_state("paused", "Paused")
                self.write("[live] Paused.\n")
            self.update_run_controls()
        except Exception as e:
            self.write(f"[live] Pause/Resume failed: {e}\n")
            QMessageBox.critical(self, "Pause/Resume", str(e))

    def write(self, x):
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.insertPlainText(x)
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def stop(self):
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            if self.paused and HAVE_PSUTIL:
                try:
                    psutil.Process(self.proc.processId()).resume()
                except Exception:
                    pass
                self.paused = False
            self.stopping = True
            self.proc.terminate()
            # ffmpeg/HandBrakeCLI are console apps; terminate() (WM_CLOSE on
            # Windows, SIGTERM on POSIX) can be ignored, so force a kill a
            # few seconds later if it's still alive.
            QTimer.singleShot(4000, self._force_kill_if_stuck)
            self.set_state("run", "Stopping\u2026")
            self.update_run_controls()

    def _force_kill_if_stuck(self):
        if self.proc and self.stopping and self.proc.state() == QProcess.ProcessState.Running:
            self.proc.kill()

    def closeEvent(self, event):
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            self.proc.kill()
            self.proc.waitForFinished(1000)
        event.accept()


def install_excepthook(win):
    """A --windowed PyInstaller build has no console, so sys.stderr is None
    and any exception raised inside a Qt slot (button click, timer, signal
    handler, ...) that Python's default excepthook would normally print
    just gets silently discarded instead - the app looks like it's doing
    nothing. This routes uncaught exceptions to a visible dialog and the
    Activity log instead, so a genuine bug shows itself instead of hiding
    as an unresponsive button."""
    import traceback

    def hook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            win.write("\n[error] Unhandled exception:\n" + msg + "\n")
        except Exception:
            pass
        try:
            QMessageBox.critical(win, "Unexpected error", msg[-2000:])
        except Exception:
            pass

    sys.excepthook = hook


def main():
    app = QApplication(sys.argv)
    icon_path = find_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    win = MainWindow()
    if icon_path:
        win.setWindowIcon(QIcon(icon_path))
    install_excepthook(win)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
