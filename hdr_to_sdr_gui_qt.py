"""HDR -> SDR with standard FFmpeg tone mapping, true BT.2390 via libplacebo/Vulkan,
and a HandBrakeCLI backend.

Qt for Python (PySide6) port of the original Tkinter app. Behaviour, command
lines, and detection logic are kept identical; only the UI toolkit changed.
Process I/O now arrives via Qt signals (QProcess) instead of a background
thread + queue, since QProcess already delivers its signals on the GUI
thread - the thread/queue plumbing the Tkinter version needed is gone.

Includes 10/12-bit output for both the FFmpeg and HandBrakeCLI backends
(bit-depth selector in the CONVERSION card). This used to live in a separate
hdr_to_sdr_pro_gui_qt.py subclass so it could be shipped as its own "10/12-bit
build" executable - now folded directly in, since nothing here is
license-gated and there's no real reason to maintain two files just to
produce two downloads.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import (
    Qt, QTimer, QProcess, QUrl, QSize, QEvent, QLocale, QSettings,
    QPropertyAnimation, QEasingCurve, Property, QRectF, QPoint
)
from PySide6.QtGui import QAction, QColor, QFontDatabase, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QTextCursor, QIcon, QDesktopServices, QPixmap, QImage, QPalette
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QSpinBox, QSlider, QProgressBar, QTextEdit, QGroupBox, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFileDialog, QMessageBox, QFrame, QSplitter, QStackedWidget, QScrollArea,
    QDialog, QMenu, QToolButton, QListWidget, QSizePolicy, QGraphicsDropShadowEffect,
    QAbstractButton, QProxyStyle, QStyle
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
I18N_DIR = ROOT / "i18n"
PORTABLE_TOOLS_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "HDR-to-SDR-Converter" / "tools"
CPU_COUNT = os.cpu_count() or 4

# ffprobe uses the historical names "tv" and "pc" for video range.  Make
# the distinction explicit in the analysis panel: it is particularly useful
# for HDR-to-SDR work, where an unintended full/limited conversion can make
# blacks look raised or crushed.
COLOR_RANGE_NAMES = {
    "tv": "limited range (TV)",
    "mpeg": "limited range (TV)",
    "pc": "full range (PC)",
    "jpeg": "full range (PC)",
    "unknown": "range not signalled",
}


LANGUAGE_NAMES = {
    "ar": "العربية", "bg": "Български", "bn": "বাংলা", "cs": "Čeština",
    "de": "Deutsch", "el": "Ελληνικά", "es": "Español", "fa": "فارسی",
    "fr": "Français", "he": "עברית", "hi": "हिन्दी", "id": "Bahasa Indonesia",
    "it": "Italiano", "ja": "日本語", "ko": "한국어", "ms": "Bahasa Melayu",
    "nl": "Nederlands", "pl": "Polski", "pt_br": "Português (Brasil)",
    "ro": "Română", "ru": "Русский", "sr": "Српски", "th": "ไทย",
    "tl": "Filipino", "tr": "Türkçe", "uk": "Українська", "ur": "اردو",
    "vi": "Tiếng Việt", "zh_cn": "简体中文", "zh_tw": "繁體中文",
}


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
    def _system_language():
        system_code = QLocale.system().name().lower().replace("-", "_")
        if system_code.startswith("pt"):
            return "pt_br"
        if system_code.startswith("zh"):
            # Traditional-script regions/variants map to zh_tw; everything
            # else (zh_cn, zh_sg, bare "zh", ...) falls back to Simplified.
            return "zh_tw" if system_code.split("_")[-1] in ("tw", "hk", "mo", "hant") else "zh_cn"
        base = system_code.split("_", 1)[0]
        return base if base in LANGUAGE_NAMES else "en"

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

# Project links shown as small buttons in the header - edit these to match
# your actual repo/Ko-fi.
GITHUB_URL = "https://github.com/devgodys/HDR-to-SDR-Video-Converter"
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
# HandBrakeCLI --help prints its "-e, --encoder <string>  Select video
# encoder: <space-separated ids>" block, then the next "--flag" line. Anchored
# on "Select video encoder:" rather than the "-e," prefix since that phrase
# has stayed stable across HandBrakeCLI versions/platforms; used to
# cross-check chosen encoder ids (including *_10bit/*_12bit ones) against
# what this actual HandBrakeCLI build really ships, the same way -encoders
# already does for FFmpeg in check() below.
_HB_ENCODER_LIST = re.compile(r"Select video encoder:\s*(.*?)(?:\n\s*-{1,2}\S|\Z)", re.S)

# label -> target output height, or None for "don't scale". Deliberately
# height-only, not a fixed W*H pair: forcing an exact width AND height (the
# old behavior) stretches/distorts any source that isn't 16:9. Scaling to a
# target height and letting width be computed from it (-2 in the ffmpeg
# filter, HandBrakeCLI's own -Y-only aspect handling) keeps the source's
# aspect ratio correct for any input, not just 16:9 ones.
RESOLUTIONS = {
    "Source (no scaling)": None,
    "2160p (4K)": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
}

# Tone-mapping curves. label -> ("gpu"/"cpu", <exact ffmpeg/libplacebo enum
# string>). GPU codes are libplacebo's `tonemapping=` values - FFmpeg-only,
# HandBrake has no libplacebo path at all. CPU codes are the classic
# `tonemap` avfilter's values, and - confirmed against HandBrake's own
# libhb/colorspace.c - HandBrake's `--colorspace ...:tonemap=` runs that
# exact same avfilter under the hood, so these work on both backends.
TONEMAP_BASE = {
    "BT.2390 \u00b7 GPU libplacebo/Vulkan (FFmpeg only)": ("gpu", "bt.2390"),
    "Hable \u00b7 CPU tonemap": ("cpu", "hable"),
    "Reinhard \u00b7 CPU tonemap": ("cpu", "reinhard"),
    "Mobius \u00b7 CPU tonemap": ("cpu", "mobius"),
}
TONEMAP_PRO_GPU = {
    "BT.2446A \u00b7 GPU libplacebo/Vulkan (FFmpeg only)": ("gpu", "bt.2446a"),
    "Auto \u00b7 GPU libplacebo/Vulkan (FFmpeg only)": ("gpu", "auto"),
}
TONEMAP_PRO_GPU_ST2094 = {
    "ST2094-40 (HDR10+) \u00b7 GPU libplacebo/Vulkan (FFmpeg only)": ("gpu", "st2094-40"),
    "ST2094-10 \u00b7 GPU libplacebo/Vulkan (FFmpeg only)": ("gpu", "st2094-10"),
}
TONEMAP_PRO_CPU = {
    "Linear \u00b7 CPU tonemap": ("cpu", "linear"),
    "Gamma \u00b7 CPU tonemap": ("cpu", "gamma"),
    "Clip \u00b7 CPU tonemap": ("cpu", "clip"),
    "None (direct, no tonemap) \u00b7 CPU tonemap": ("cpu", "none"),
}

# Experimental: short tooltip per curve, shown on hover in the Tone mapping
# dropdown. Plain-language tradeoffs, not marketing copy - if a curve has a
# real downside (crushes highlights, needs metadata this source may lack),
# say so rather than only listing what it's good at.
TONEMAP_INFO = {
    "BT.2390 \u00b7 GPU libplacebo/Vulkan (FFmpeg only)":
        "The reference curve most professional HDR-to-SDR grades are built around. Good default - "
        "balances highlight rolloff and midtone contrast without extra setup. Needs a Vulkan GPU.",
    "Hable \u00b7 CPU tonemap":
        "A filmic curve (originally from Uncharted 2) - smooth highlight rolloff, punchy midtones. "
        "Solid default when BT.2390 isn't available, and the default HandBrake also uses internally.",
    "Reinhard \u00b7 CPU tonemap":
        "Simple and fast, but tends to look flatter/lower-contrast than Hable or BT.2390 - a reasonable "
        "fallback, not usually the first choice.",
    "Mobius \u00b7 CPU tonemap":
        "Similar territory to Hable with a gentler shoulder into highlights - can hold onto slightly more "
        "detail in bright areas at the cost of a flatter overall look.",
    "BT.2446A \u00b7 GPU libplacebo/Vulkan (FFmpeg only)":
        "ITU's method aimed at broadcast/TV mastering conventions rather than BT.2390's cinema-leaning "
        "target - worth trying if BT.2390 looks a bit too contrasty for your source.",
    "Auto \u00b7 GPU libplacebo/Vulkan (FFmpeg only)":
        "Lets libplacebo pick per-scene rather than applying one fixed curve throughout. Can adapt better "
        "to sources with wildly inconsistent brightness, at the cost of predictability.",
    "ST2094-40 (HDR10+) \u00b7 GPU libplacebo/Vulkan (FFmpeg only)":
        "Uses the source's own HDR10+ dynamic metadata if present - most accurate option when that "
        "metadata actually exists, but does nothing extra on a plain HDR10 source that lacks it.",
    "ST2094-10 \u00b7 GPU libplacebo/Vulkan (FFmpeg only)":
        "Same idea as ST2094-40 but for the less common ST2094-10 metadata standard - only useful if your "
        "source actually carries it.",
    "Linear \u00b7 CPU tonemap":
        "No highlight rolloff at all - bright areas clip hard rather than compress gracefully. Mainly "
        "useful for testing/comparison, rarely what you want for a final export.",
    "Gamma \u00b7 CPU tonemap":
        "A flat power-curve remap rather than a perceptual tone-mapping curve - simple, but doesn't handle "
        "extreme highlights as gracefully as Hable/BT.2390.",
    "Clip \u00b7 CPU tonemap":
        "Hard-clips anything above SDR white instead of compressing it - fast, but loses all detail in "
        "blown-out highlights. Mostly a diagnostic/testing option.",
    "None (direct, no tonemap) \u00b7 CPU tonemap":
        "Passes values through with no tone mapping at all - output will look washed out or broken on an "
        "SDR display. Only useful for troubleshooting the pipeline itself.",
}

PRIORITY_LEVELS = ["Efficiency (low)", "Balanced", "Performance (high)"]
PRIORITY_NICE = {"Efficiency (low)": 10, "Balanced": 0, "Performance (high)": -5}

# Shared fixed width for the numeric spin boxes that sit next to a slider
# (Quality, Brightness boost, CPU cores) - so all three rows line up instead
# of each spin box sizing itself to its own digit count.
SPIN_WIDTH = 56  # spin box alone, now flanked by round -/+ stepper buttons
# (was 90px when the spin box's own native up/down arrows had to fit inside it)

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
# AV1 needs roughly 40-50% fewer bits than H.264 for comparable quality -
# similar to or a bit better than H.265, per the usual AV1-vs-H.264/H.265
# efficiency comparisons (e.g. NVIDIA's own NVENC AV1 benchmarks).
CODEC_EFFICIENCY = {"H.264": 1.0, "H.265": 0.60, "AV1": 0.50}
# CRF "center" (this codec's own typical/default value) and how many CRF
# steps double/halve the bitrate - both differ per codec's own quantizer
# scale, not just its bit-efficiency (that's CODEC_EFFICIENCY, above).
# H.264/H.265 share the classic x264-derived "6 steps = 2x bitrate" rule
# of thumb around CRF 23. SVT-AV1's CRF instead runs a much wider 0-63
# range centered further out (CRF ~30 is the commonly-cited "default"
# quality point - see e.g. "SVT-AV1 CRF 30 \u2248 x265 CRF 21 \u2248 x264 CRF 16"),
# so it gets its own center/step rather than being forced onto x264's.
CRF_CENTER = {"H.264": 23, "H.265": 23, "AV1": 30}
CRF_HALVING_STEP = {"H.264": 6, "H.265": 6, "AV1": 9}
ENCODER_EFFICIENCY = {"CPU": 1.0, "NVIDIA": 1.35, "AMD": 1.35, "Apple": 1.25}


def estimate_bitrate_kbps(encoder_label, quality, width, height):
    """Rough estimated output bitrate in kbps for the given encoder combo
    label (an ENCODER_MAP key), CRF/CQ/QP value, and output resolution."""
    if encoder_label.endswith("AV1"):
        codec = "AV1"
    elif encoder_label.endswith("H.265"):
        codec = "H.265"
    else:
        codec = "H.264"
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
    crf_factor = 2 ** ((CRF_CENTER[codec] - quality) / CRF_HALVING_STEP[codec])
    kbps = (BITRATE_ANCHOR_1080P_H264_CRF23_KBPS * scale * crf_factor
            * CODEC_EFFICIENCY[codec] * ENCODER_EFFICIENCY[enc_type])
    return max(150.0, kbps)

ENCODER_MAP = {
    "CPU \u00b7 H.264": "libx264", "CPU \u00b7 H.265": "libx265",
    "NVIDIA NVENC \u00b7 H.264": "h264_nvenc", "NVIDIA NVENC \u00b7 H.265": "hevc_nvenc",
    "AMD AMF \u00b7 H.264": "h264_amf", "AMD AMF \u00b7 H.265": "hevc_amf",
    "Apple VideoToolbox \u00b7 H.264": "h264_videotoolbox",
    "Apple VideoToolbox \u00b7 H.265": "hevc_videotoolbox",
    # AV1 is royalty-free (Alliance for Open Media Patent License) so it
    # carries none of HEVC's patent-pool baggage - no licensing blocker for
    # an MIT-licensed app. SVT-AV1 (libsvtav1) is BSD-2-Clause + Patent and
    # already ships in the winget FFmpeg build. No Apple VideoToolbox AV1
    # entry: as of 2026 VideoToolbox can decode AV1 (M3+) but still has no
    # AV1 *encoder* - Apple's hardware encoder side remains HEVC-first.
    "CPU \u00b7 AV1": "libsvtav1",
    "NVIDIA NVENC \u00b7 AV1": "av1_nvenc",
    "AMD AMF \u00b7 AV1": "av1_amf",
}
BIT_DEPTH_LABELS = ["8-bit (SDR standard)", "10-bit", "12-bit"]

# ---- FFmpeg side: bit depth -> (pix_fmt for the zscale/libplacebo
# `format=` param, encoder labels that can *structurally* produce it).
# NVENC / AMD AMF / VideoToolbox cap out at 10-bit HEVC; only libx265 (CPU)
# goes to 12-bit, and H.264 doesn't do either in any practical sense.
# Cross-checked against self.encoders (the exact "libx265"/"hevc_nvenc"/...
# names `ffmpeg -encoders` reports) in _allowed_encoder_labels() below,
# since being the right *kind* of encoder for a depth doesn't mean this
# particular FFmpeg build actually has it.
FFMPEG_BIT_DEPTH = {
    "8-bit (SDR standard)": ("yuv420p", set(ENCODER_MAP.keys())),
    "10-bit": ("yuv420p10le", {
        "CPU \u00b7 H.265", "NVIDIA NVENC \u00b7 H.265",
        "AMD AMF \u00b7 H.265", "Apple VideoToolbox \u00b7 H.265",
        # All three AV1 encoders here handle 10-bit natively (AV1 itself
        # requires at least the encoder support 8/10-bit; none of these
        # three builds does AV1 12-bit in practice, so - like H.264 above -
        # AV1 simply has no 12-bit entry below).
        "CPU \u00b7 AV1", "NVIDIA NVENC \u00b7 AV1", "AMD AMF \u00b7 AV1",
    }),
    "12-bit": ("yuv420p12le", {"CPU \u00b7 H.265"}),
}

# ffprobe color_transfer/color_primaries/color_space(matrix) values -> short
# human-readable labels for the SOURCE ANALYSIS card. Only covers what
# actually turns up in real-world files; anything else falls back to the
# raw ffprobe string as-is, so an unrecognised value still shows *something*
# instead of a blank or a KeyError.
TRANSFER_NAMES = {
    "smpte2084": "PQ (SMPTE ST 2084)", "pq": "PQ (SMPTE ST 2084)",
    "arib-std-b67": "HLG (ARIB STD-B67)", "hlg": "HLG (ARIB STD-B67)",
    "bt709": "BT.709", "bt470bg": "BT.601 (PAL)", "smpte170m": "BT.601 (NTSC)",
    "linear": "Linear", "unknown": "unknown",
}
PRIMARIES_NAMES = {
    "bt2020": "BT.2020", "bt709": "BT.709",
    "smpte432": "Display P3", "smpte431": "DCI-P3",
    "bt470bg": "BT.601 (PAL)", "smpte170m": "BT.601 (NTSC)", "unknown": "unknown",
}
MATRIX_NAMES = {
    "bt2020nc": "BT.2020 non-constant luminance", "bt2020c": "BT.2020 constant luminance",
    "bt709": "BT.709", "smpte170m": "BT.601", "bt470bg": "BT.601 (PAL)", "unknown": "unknown",
}

# ---- HandBrakeCLI side: encoder label -> {bit depth label: -e id}.
# A depth missing from a label's dict means that combo genuinely doesn't
# exist as a HandBrakeCLI encoder id, not just "untested" - confirmed
# against `HandBrakeCLI --help` / handbrake.fr/docs/.../technical/video-bit-depth.html:
# only x264/x265/NVEnc-H.265/AMF-H.265/VideoToolbox-H.265 ship *_10bit ids,
# and only x265 ships a *_12bit id - hardware encoders top out at 10-bit,
# and none of the H.264 hardware encoders have a >8-bit id at all.
# Cross-checked against self.hb_encoders (ids parsed out of `HandBrakeCLI
# --help`) the same way, for the same reason.
HB_ENCODER_IDS = {
    "CPU \u00b7 H.264": {"8-bit (SDR standard)": "x264", "10-bit": "x264_10bit"},
    "CPU \u00b7 H.265": {"8-bit (SDR standard)": "x265", "10-bit": "x265_10bit", "12-bit": "x265_12bit"},
    "NVIDIA NVENC \u00b7 H.264": {"8-bit (SDR standard)": "nvenc_h264"},
    "NVIDIA NVENC \u00b7 H.265": {"8-bit (SDR standard)": "nvenc_h265", "10-bit": "nvenc_h265_10bit"},
    "AMD AMF \u00b7 H.264": {"8-bit (SDR standard)": "vce_h264"},
    "AMD AMF \u00b7 H.265": {"8-bit (SDR standard)": "vce_h265", "10-bit": "vce_h265_10bit"},
    "Apple VideoToolbox \u00b7 H.264": {"8-bit (SDR standard)": "vt_h264"},
    "Apple VideoToolbox \u00b7 H.265": {"8-bit (SDR standard)": "vt_h265", "10-bit": "vt_h265_10bit"},
    # AV1 encoder ids added in HandBrake 1.6.0 (svt_av1/svt_av1_10bit, CPU)
    # and 1.7.0 (nvenc_av1 on RTX 40xx+, vce_av1 on RX 7000+/RDNA3),
    # confirmed present in `HandBrakeCLI --help` and HandBrake's release
    # notes. The *_10bit ids for the two hardware ones follow the same
    # naming convention as nvenc_h265/nvenc_h265_10bit and
    # vce_h265/vce_h265_10bit above, but - unlike those - aren't directly
    # confirmed from a HandBrakeCLI build with that exact hardware; if a
    # given id turns out not to exist on a user's HandBrakeCLI, it's simply
    # absent from self.hb_encoders and this dict already fails safe the
    # same way any other detected-but-missing id does (see
    # _allowed_encoder_labels below): that combo is just not offered,
    # rather than erroring.
    "CPU \u00b7 AV1": {"8-bit (SDR standard)": "svt_av1", "10-bit": "svt_av1_10bit"},
    "NVIDIA NVENC \u00b7 AV1": {"8-bit (SDR standard)": "nvenc_av1", "10-bit": "nvenc_av1_10bit"},
    "AMD AMF \u00b7 AV1": {"8-bit (SDR standard)": "vce_av1", "10-bit": "vce_av1_10bit"},
}
# HandBrakeCLI's --encoder-profile isn't required to get 10/12-bit output -
# the *_10bit/*_12bit encoder id alone already forces it - but setting it
# explicitly keeps the stream's signalled profile honest for players/
# muxers that check it rather than the actual sample depth.
HB_PROFILE_FOR_DEPTH = {"10-bit": "main10", "12-bit": "main12"}

# Quality-spin/-slider range per encoder label ("standard"/"pro" pair +
# a sane default value). Every encoder except CPU AV1 shares the same
# familiar 0-51 CRF/CQ/QP scale (x264/x265's CRF, and NVENC/AMF's -cq/-qp
# happen to use that same 0-51 range regardless of codec) - that's why
# toggle_pro_mode() could hard-code (14,28)/(0,51) before AV1 existed here.
# SVT-AV1's CRF instead runs 0-63 with a much higher "sane default" (~30
# vs ~18-23), so CPU AV1 gets its own pair of ranges; every other label
# (including the new NVIDIA/AMD AV1 hardware encoders) falls back to
# DEFAULT_QUALITY_RANGE unchanged.
QUALITY_RANGES = {
    "CPU \u00b7 AV1": {"standard": (24, 38), "pro": (0, 63), "default": 30},
}
DEFAULT_QUALITY_RANGE = {"standard": (14, 28), "pro": (0, 51), "default": 18}


# ---- small helpers -----------------------------------------------------

def exe(n):
    names = (n + ".exe", n) if sys.platform.startswith("win") else (n,)
    locations = (ROOT, PORTABLE_TOOLS_DIR)
    for location in locations:
        found = next((str(location / x) for x in names if (location / x).is_file()), None)
        if found:
            return found
    return shutil.which(n)


def wrappable(path):
    """A raw path like "C:\\Users\\...\\HandBrakeCLI.EXE" has no spaces, so
    Qt's word-wrap treats it as one unbreakable word and stretches the
    whole panel to fit it. A zero-width space is invisible but gives the
    wrapper somewhere to break the line - used anywhere a full path gets
    displayed in a label/log that needs to stay narrow."""
    return path.replace("\\", "\\\u200b").replace("/", "/\u200b")


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


def clock_time(seconds):
    """Compact clock-style timecode (1:23:45 or 12:34) for the Live Preview
    position readout - format_time()'s "1h 19m 59s" style reads better for
    an ETA, but a "position / total" line wants the shorter clock form."""
    seconds = max(0, int(seconds))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def clipping_pct(img, step=3, near=5):
    """Rough percentage of the frame that's crushed to near-black or blown
    to near-white after tone mapping - a quick visual sanity check that the
    chosen curve/brightness isn't destroying shadow or highlight detail.
    Samples every `step`th pixel (not every pixel) since this runs on the
    GUI thread every ~1.5s and only needs to be roughly right, not exact."""
    gray = img.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h = gray.width(), gray.height()
    if w == 0 or h == 0:
        return 0.0, 0.0
    stride = gray.bytesPerLine()
    ptr = gray.constBits()
    try:
        ptr.setsize(stride * h)
    except AttributeError:
        pass  # some PySide6 versions size constBits() automatically
    buf = bytes(ptr)
    shadow = highlight = total = 0
    for y in range(0, h, step):
        row = y * stride
        for x in range(0, w, step):
            v = buf[row + x]
            total += 1
            if v <= near:
                shadow += 1
            elif v >= 255 - near:
                highlight += 1
    if not total:
        return 0.0, 0.0
    return shadow / total * 100, highlight / total * 100


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


def header_icon(kind, color):
    """Draw small header controls without relying on emoji-font availability."""
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "globe":
        painter.drawEllipse(3.5, 3.5, 15, 15)
        painter.drawEllipse(7.2, 3.5, 7.6, 15)
        painter.drawLine(3.8, 11, 18.2, 11)
    elif kind == "sun":
        painter.drawEllipse(7, 7, 8, 8)
        for x1, y1, x2, y2 in ((11, 2, 11, 4.5), (11, 17.5, 11, 20), (2, 11, 4.5, 11), (17.5, 11, 20, 11),
                                (4.6, 4.6, 6.4, 6.4), (15.6, 15.6, 17.4, 17.4),
                                (17.4, 4.6, 15.6, 6.4), (6.4, 15.6, 4.6, 17.4)):
            painter.drawLine(x1, y1, x2, y2)
    else:  # moon
        path = QPainterPath()
        path.addEllipse(4, 3, 15, 16)
        cutout = QPainterPath()
        cutout.addEllipse(9, 2, 15, 16)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path.subtracted(cutout))

    painter.end()
    return QIcon(pixmap)


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


# Sizes Windows actually requests for a top-level window/taskbar icon across
# its scale-factor table (100%-400%): 16/20/24/30/32/36/40/48/60/64/72/80/96,
# plus 128/256 for Alt+Tab and jump-list thumbnails on very high DPI.
ICON_SIZES = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 128, 256)


def load_app_icon():
    """Build the runtime QIcon (title bar / taskbar button / Alt+Tab) from
    the icons/icon-<size>.png set, registering every size explicitly via
    addFile(..., QSize(...)).

    QIcon(path) on a single file - even a multi-resolution .ico - only ever
    reads that file's first embedded frame; it does NOT auto-select the
    matching size the way Windows' own shell icon extraction does. Loading
    every PNG explicitly is the documented way to get a crisp, exact match
    at each size instead of Qt scaling one fixed pixmap up or down.

    icon.ico (via find_icon()) is kept as the fallback for a plain
    Python-from-source run where the icons/ folder hasn't been unpacked
    next to the script, and is still what --icon embeds as the .exe's own
    file icon at compile time - Explorer/the shell extracts the matching
    size from that file natively, so it doesn't need this treatment."""
    icon = QIcon()
    icons_dir = ROOT / "icons"
    for size in ICON_SIZES:
        p = icons_dir / f"icon-{size}.png"
        if p.is_file():
            icon.addFile(str(p), QSize(size, size))
    if not icon.isNull():
        return icon
    p = find_icon()
    return QIcon(p) if p else QIcon()


# ---- small widget helpers -----------------------------------------------

def set_role(widget, role):
    """Attach a QSS-selectable role (e.g. label colour, button colour) and
    force a restyle - Qt caches style output per-widget, so a property
    change alone doesn't repaint until you unpolish/polish."""
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def make_stepper(spin: QSpinBox) -> QWidget:
    """Wrap a QSpinBox with round -/+ buttons and hide its native
    up/down buttons (boxy and, on some native Windows styles, prone to
    QSS-vs-native-chrome mismatches - see QSlider groove note in main()).
    spin itself is untouched: same widget, same .value()/.setValue()/
    .valueChanged/.setEnabled() any existing code already uses - only its
    container widget is new, so call sites just need to add that
    container to a layout instead of the bare spin box."""
    minus_btn = QPushButton("\u2212")
    plus_btn = QPushButton("+")
    for b in (minus_btn, plus_btn):
        set_role(b, "stepper")
        b.setFixedSize(26, 26)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    set_role(spin, "stepper-spin")
    spin.setFixedHeight(26)  # match the round buttons exactly so the row
    # doesn't rely on QHBoxLayout's cross-axis centering (which left the
    # number a few px off from the +/- buttons when their sizeHints differed)
    minus_btn.clicked.connect(spin.stepDown)
    plus_btn.clicked.connect(spin.stepUp)

    def sync_enabled():
        minus_btn.setEnabled(spin.isEnabled() and spin.value() > spin.minimum())
        plus_btn.setEnabled(spin.isEnabled() and spin.value() < spin.maximum())

    spin.valueChanged.connect(lambda _v: sync_enabled())
    sync_enabled()

    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(minus_btn)
    row.addWidget(spin)
    row.addWidget(plus_btn)

    # Existing code toggles spin.setEnabled(...) directly (e.g. brightness
    # controls). Piggyback on that instead of requiring every call site to
    # also remember to enable/disable the two new buttons: wrap setEnabled
    # so the buttons always mirror the spin box's state without touching
    # any of the ~6 existing .setEnabled(...) call sites.
    original_set_enabled = spin.setEnabled

    def set_enabled(enabled):
        original_set_enabled(enabled)
        sync_enabled()

    spin.setEnabled = set_enabled
    return wrap


def chevron_icon_path(hex_color, size=20):
    """Render (and cache on disk) a small down-chevron PNG in the given
    colour, for use as QComboBox::down-arrow - QSS can't draw a shape
    itself, and the native OS triangle sits inside its own boxed-off
    ::drop-down section with a divider line next to it. One flat icon,
    one fixed colour, no per-state variants: that's what caused the
    earlier "doubled arrow" glitch (a second, differently-styled image
    swapped in on hover and briefly rendered on top of the first)."""
    cache_dir = Path(tempfile.gettempdir()) / "hdr2sdr_ui_icons"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"chevron_{hex_color.lstrip('#')}_{size}.png"
    if not path.exists():
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(hex_color))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        w, h = size * 0.5, size * 0.26
        cx, cy = size / 2, size / 2
        arrow = QPainterPath()
        arrow.moveTo(cx - w / 2, cy - h / 2)
        arrow.lineTo(cx, cy + h / 2)
        arrow.lineTo(cx + w / 2, cy - h / 2)
        painter.drawPath(arrow)
        painter.end()
        pix.save(str(path), "PNG")
    return str(path).replace("\\", "/")


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


class _AppToolTip(QLabel):
    """Replacement for the native QToolTip, installed as an app-wide event
    filter (see MainWindow.eventFilter). Needed because the native tooltip
    stays solid black on some Windows/Qt combinations no matter what
    palette or style-sheet is set on it - both QApplication.setPalette()
    and the dedicated QToolTip.setPalette() were tried and neither stuck,
    so this sidesteps native tooltip rendering entirely and draws a plain
    QLabel of our own instead."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWordWrap(True)
        self.setMaximumWidth(420)


class Card(QFrame):
    """A titled card: soft-rounded flat fill, subtle drop shadow, and a
    plain in-flow title label instead of QGroupBox's native "title cut
    into the border" chrome - reads as a modern raised surface rather
    than a legacy Windows group box. The title is a regular QLabel, so
    the existing generic QLabel i18n pass in retranslate_static_ui()
    picks it up automatically - no separate translation wiring needed."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 10, 18, 12)
        outer.setSpacing(6)

        self.title_label = QLabel(title)
        set_role(self.title_label, "card-title")
        outer.addWidget(self.title_label)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        outer.addLayout(self.body)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 45))
        self.setGraphicsEffect(shadow)


class ToggleSwitch(QAbstractButton):
    """iOS-style animated toggle switch with a trailing text label -
    drop-in replacement for QCheckBox(text): same isChecked() /
    setChecked() / toggled API, so call sites and translation logic
    (which just calls .text()/.setText()/.toolTip()) don't need to
    change. Unlike a QSS-only checkbox indicator, the knob position is
    a real animated QPropertyAnimation, not an instant state swap.

    Colours come from the current theme via Qt properties (on_color/
    off_color/text_color) set in apply_theme() - paintEvent() reads
    them fresh each frame instead of hardcoding a palette, so light/
    dark switching and the animation share the same code path."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._track_w = 40
        self._track_h = 22
        self._pos = 0.0  # 0 = off, 1 = on
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_to)

    def _get_knob_pos(self):
        return self._pos

    def _set_knob_pos(self, value):
        self._pos = value
        self.update()

    knobPos = Property(float, _get_knob_pos, _set_knob_pos)

    def _animate_to(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        extra = (8 + fm.horizontalAdvance(self.text())) if self.text() else 0
        return QSize(self._track_w + extra, max(self._track_h, fm.height()) + 6)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        off = QColor(self.property("off_color") or "#B4B2A9")
        on = QColor(self.property("on_color") or "#1968E0")
        mix = QColor(
            round(off.red() + (on.red() - off.red()) * self._pos),
            round(off.green() + (on.green() - off.green()) * self._pos),
            round(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        if not self.isEnabled():
            mix = QColor(self.property("off_color") or "#B4B2A9")

        track = QRectF(0, (self.height() - self._track_h) / 2, self._track_w, self._track_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mix)
        painter.drawRoundedRect(track, self._track_h / 2, self._track_h / 2)

        knob_d = self._track_h - 4
        knob_x = track.x() + 2 + self._pos * (self._track_w - knob_d - 4)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(knob_x, track.y() + 2, knob_d, knob_d))

        if self.text():
            painter.setPen(QColor(self.property("text_color") or "#1C1C1A"))
            painter.setFont(self.font())
            text_rect = QRectF(self._track_w + 8, 0, self.width() - self._track_w - 8, self.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        painter.end()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.i18n = Localizer()
        self.setWindowTitle(self.tr("HDR to SDR Movie Converter"))
        self.setAcceptDrops(True)

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
        self.ffmpeg_version = None
        self.encoders = set()
        self.hb_encoders = set()
        self.proc = None
        self.proc_psutil = None
        self._setup_proc = None
        self.current_out_sec = 0.0
        self.preview_path = None
        self.preview_mtime = 0
        self._frame_preview_path = Path(tempfile.gettempdir()) / "hdr2sdr_frame_preview.png"
        self._frame_preview_proc = None
        self._frame_preview_gen = 0
        self._current_preview_pixmap = None
        self._test_clip_proc = None
        # The preview card shows one status line under the frame, not two:
        # normal state is the "Preview frame at ..." meta text, but a test
        # clip render/cancel/failure message takes over that same line
        # while it's relevant, then hands it back. Two separate labels
        # used to stack here, which left an awkward double gap once the
        # test-clip line had something to say.
        self._preview_meta_text = ""
        self._test_clip_status_text = ""
        self.queue_paths = []
        self.queue_running = False
        self.output_folder = None
        self._test_clip_out = None
        self._last_analysis_raw = None
        self.side_tab = None
        self.stopping = False
        self.paused = False
        self.out_buf = ""
        self.block = {}
        self.using_hb = False
        self.gpu_tool = shutil.which("nvidia-smi")
        self.handbrake_tool = exe("HandBrakeCLI")
        self.theme_name = "light"
        self._tooltip = _AppToolTip()

        self.build()
        # Installed only now, after every widget referenced anywhere in
        # eventFilter() (queue_list, preview_label, ...) actually exists -
        # doing this earlier meant Qt's synchronous events fired *during*
        # widget construction (ChildAdded/ParentChange etc. don't wait for
        # an event loop) were already reaching eventFilter() and hitting
        # self.queue_list before build() had created it, raising inside a
        # C++-invoked virtual method - which PySide6 doesn't recover from
        # cleanly and instead surfaces as an unrelated-looking
        # "QVBoxLayout returned NULL" crash a few widget constructions later.
        QApplication.instance().installEventFilter(self)
        self.retranslate_static_ui()
        self.apply_theme()
        # ---- TEST BUILD: synchronous startup, one-shot sizing --------
        # Every previous fix here (seeding method_combo before check(),
        # flushing pending style-polish events, forcing every nested
        # layout to invalidate/recompute) closed one specific gap between
        # the window's first computed height and its "true" one - and the
        # jump kept coming back from a different cause each time. The
        # common thread: any height computed before the window has been
        # shown and laid out for real once is a guess, and exactly which
        # widget metrics have "settled" by that point isn't something
        # this code can fully pin down from outside Qt's own scheduling.
        #
        # Different approach: don't guess, don't size twice. Run the
        # capability probe (self.check() - shells out to ffmpeg and
        # HandBrakeCLI) synchronously right here, before the window is
        # shown at all, so every combo/label it touches already holds its
        # final content, then size the window exactly once from that.
        # There's nothing left to catch up to after showing, so there's
        # nothing left to jump. The tradeoff: the window's first
        # appearance is delayed by however long check() takes (typically
        # well under a second) instead of appearing instantly and
        # settling a moment later - worth testing against the old
        # behaviour to see which one actually reads better.
        self.check()
        self._fit_height_to_content(width=1080)
        self.update_run_controls()

    def tr(self, source, **values):
        """Translate an English source string while keeping English as a safe fallback."""
        return self.i18n.text(source, **values)

    def _translate_widget_property(self, widget, getter, setter, property_name):
        source = widget.property(property_name)
        if source is None:
            source = getter()
            widget.setProperty(property_name, source)
        if source:
            setter(self.tr(source))

    def retranslate_static_ui(self):
        """Translate visible static captions without altering internal combo-box values."""
        self.setWindowTitle(self.tr("HDR to SDR Movie Converter"))
        for widget in self.findChildren(QGroupBox):
            self._translate_widget_property(widget, widget.title, widget.setTitle, "i18n_title")
        for widget in self.findChildren(QLabel):
            self._translate_widget_property(widget, widget.text, widget.setText, "i18n_text")
        for widget in self.findChildren(QPushButton):
            self._translate_widget_property(widget, widget.text, widget.setText, "i18n_text")
            self._translate_widget_property(widget, widget.toolTip, widget.setToolTip, "i18n_tooltip")
        for widget in self.findChildren(ToggleSwitch):
            self._translate_widget_property(widget, widget.text, widget.setText, "i18n_text")
            self._translate_widget_property(widget, widget.toolTip, widget.setToolTip, "i18n_tooltip")
        self.language_button.setText("")
        self.theme_btn.setText("")
        self.theme_btn.setToolTip(
            self.tr("Light mode") if self.theme_name == "dark" else self.tr("Dark mode"))
        self.language_button.setToolTip(self.tr("Interface language"))

    def rebuild_language_menu(self):
        self.language_menu.clear()
        saved_language = self.i18n.settings.value("interface_language", "system")
        choices = [("system", "System default (Windows)"), *LANGUAGE_NAMES.items()]
        for code, name in choices:
            action = QAction(name, self.language_menu)
            action.setCheckable(True)
            action.setChecked(code == saved_language)
            action.triggered.connect(lambda checked=False, selected=code: self.change_language(selected))
            self.language_menu.addAction(action)

    def change_language(self, code):
        self.i18n.set_language(code)
        self.rebuild_language_menu()
        self.retranslate_static_ui()

    # ---- theming ---------------------------------------------------
    def apply_theme(self):
        t = THEMES[self.theme_name]
        chevron_icon = chevron_icon_path(t['MUTED'])
        qss = f"""
        QWidget {{ background: {t['BG']}; color: {t['TXT']}; font-family: '{self.FONT}'; }}
        QFrame[role="card"] {{
            background: {t['CARD']}; border: 1px solid {t['BORDER']}; border-radius: 12px;
        }}
        QLabel[role="card-title"] {{
            font-family: '{self.FONT_SEMI}'; font-size: 10pt; font-weight: 600; color: {t['TXT']};
        }}
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
            background: {t['FIELD']}; border: 1px solid {t['BORDER']}; border-radius: 8px; padding: 6px 8px;
        }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {t['INDIGO']}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1.5px solid {t['INDIGO']}; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {t['MUTED']}; background: {t['DISABLED']}; }}
        QComboBox:on {{ border: 1.5px solid {t['INDIGO']}; }}
        QComboBox::drop-down {{
            subcontrol-origin: padding; subcontrol-position: top right;
            width: 22px; border: none; background: transparent;
        }}
        QComboBox::down-arrow {{
            image: url({chevron_icon}); width: 10px; height: 10px;
        }}
        QComboBox QAbstractItemView {{
            background: {t['CARD']}; border: 1px solid {t['BORDER']}; border-radius: 0px;
            padding: 4px; outline: none;
        }}
        QComboBoxPrivateScroller {{
            background: {t['CARD']}; border: none;
        }}
        QComboBox QAbstractItemView QScrollBar:vertical {{
            background: transparent; width: 10px; margin: 4px 2px 4px 0px; border: none;
        }}
        QComboBox QAbstractItemView QScrollBar::handle:vertical {{
            background: {t['BORDER']}; border-radius: 4px; min-height: 24px;
        }}
        QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {{
            background: {t['MUTED']};
        }}
        QComboBox QAbstractItemView QScrollBar::add-line:vertical,
        QComboBox QAbstractItemView QScrollBar::sub-line:vertical {{
            height: 0px; border: none; background: transparent;
        }}
        QComboBox QAbstractItemView QScrollBar::add-page:vertical,
        QComboBox QAbstractItemView QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 7px 10px; border-radius: 6px; min-height: 20px; color: {t['TXT']}; background: transparent;
        }}
        QComboBox QAbstractItemView::item:focus {{ border: none; outline: none; }}
        QComboBox QAbstractItemView::item:hover {{ background: {t['CARD2']}; }}
        QComboBox QAbstractItemView::item:selected {{ background: {t['INDIGO']}; color: white; }}
        QListWidget {{
            background: {t['CARD']}; border: 1px solid {t['BORDER']}; border-radius: 8px;
            padding: 4px; outline: none; color: {t['TXT']};
        }}
        QListWidget::item {{
            padding: 6px 8px; border-radius: 6px; background: transparent;
        }}
        QListWidget::item:hover {{ background: {t['CARD2']}; }}
        QListWidget::item:selected {{ background: {t['INDIGO']}; color: white; }}
        QListWidget::item:selected:!active {{ background: {t['INDIGO']}; color: white; }}
        QSpinBox[role="stepper-spin"] {{
            padding: 0px 2px; text-align: center; qproperty-alignment: AlignCenter;
        }}
        QSpinBox[role="stepper-spin"]::up-button, QSpinBox[role="stepper-spin"]::down-button {{
            width: 0px; border: none;
        }}
        QPushButton[role="stepper"] {{
            background: transparent; border: 1px solid {t['BORDER']}; border-radius: 13px;
            padding: 0px 0px 2px 0px; font-size: 11pt; font-weight: 600; color: {t['TXT']};
        }}
        QPushButton[role="stepper"]:hover {{ background: {t['INDIGO']}; color: white; border-color: {t['INDIGO']}; }}
        QPushButton[role="stepper"]:pressed {{ background: {t['INDIGO2']}; color: white; }}
        QPushButton[role="stepper"]:disabled {{ color: {t['MUTED']}; background: transparent; border-color: {t['BORDER']}; }}
        QPushButton {{
            background: {t['CARD']}; border: 1px solid {t['BORDER']}; border-radius: 8px; padding: 8px 14px;
        }}
        QPushButton:hover {{ background: {t['CARD2']}; }}
        QPushButton:disabled {{ color: {t['MUTED']}; background: {t['DISABLED']}; border-color: {t['BORDER']}; }}
        QPushButton[role="go"] {{
            background: {t['INDIGO']}; color: white; font-weight: 600; border: none; min-height: 20px;
        }}
        QPushButton[role="go"]:hover {{ background: {t['INDIGO2']}; }}
        QPushButton[role="stop-ready"] {{ background: {t['RED']}; color: white; border: none; min-height: 20px; }}
        QPushButton[role="stop-ready"]:hover {{ background: {t['RED2']}; }}
        QPushButton[role="pause-ready"] {{ background: {t['AMBER']}; color: white; border: none; min-height: 20px; }}
        QPushButton[role="pause-ready"]:hover {{ background: {t['AMBER2']}; }}
        QPushButton[role="resume-ready"] {{ background: {t['GREEN']}; color: white; border: none; min-height: 20px; }}
        QPushButton[role="resume-ready"]:hover {{ background: {t['GREEN2']}; }}
        QPushButton[role="theme"], QToolButton[role="theme"] {{
            background: {t['CARD']}; border: 1px solid {t['BORDER']}; border-radius: 8px; padding: 7px 10px;
        }}
        QToolButton[role="theme"]::menu-indicator {{ image: none; width: 0px; }}
        QPushButton[role="kofi"] {{ background: #FF5E5B; color: white; border: none; font-weight: 600; }}
        QPushButton[role="kofi"]:hover {{ background: #F04642; }}
        QPushButton[role="toggle"] {{
            background: transparent; border: none; color: {t['MUTED']}; font-family: '{self.FONT_SEMI}'; text-align: left;
        }}
        QPushButton[role="toggle"]:hover {{ color: {t['INDIGO']}; }}
        QPushButton[role="panel-toggle"] {{
            background: {t['CARD2']}; border: 1px solid {t['BORDER']}; border-radius: 8px;
            padding: 6px 14px; color: {t['MUTED']}; font-weight: 600;
        }}
        QPushButton[role="panel-toggle"]:hover {{
            background: {t['CARD']}; color: {t['TXT']}; border-color: {t['INDIGO']};
        }}
        QPushButton[role="panel-toggle"]:checked {{
            background: {t['INDIGO']}; color: white; border-color: {t['INDIGO']};
        }}
        QPushButton[role="panel-toggle"]:checked:hover {{ background: {t['INDIGO2']}; }}
        QFrame[role="toolbar"] {{
            background: {t['BG']}; border: 1px solid {t['BORDER']}; border-radius: 8px;
        }}
        QPushButton[role="preview-view"] {{
            background: transparent; color: {t['INDIGO']}; border: 1.5px solid {t['INDIGO']};
            border-radius: 8px; font-weight: 600; padding: 5px 16px;
        }}
        QPushButton[role="preview-view"]:hover {{ background: {t['INDIGO']}; color: white; }}
        QPushButton[role="preview-save"] {{
            background: transparent; color: {t['GREEN']}; border: 1.5px solid {t['GREEN']};
            border-radius: 8px; font-weight: 600; padding: 5px 16px;
        }}
        QPushButton[role="preview-save"]:hover {{ background: {t['GREEN']}; color: white; }}
        QPushButton[role="preview-view"]:disabled, QPushButton[role="preview-save"]:disabled {{
            background: transparent; color: {t['MUTED']}; border-color: {t['BORDER']};
        }}
        QPushButton[role="icon-btn"] {{
            background: transparent; color: {t['MUTED']}; border: 1.5px solid {t['BORDER']};
            border-radius: 8px; font-size: 13pt; padding: 0px;
        }}
        QPushButton[role="icon-btn"]:hover {{ background: {t['CARD2']}; color: {t['INDIGO']}; border-color: {t['INDIGO']}; }}
        QPushButton[role="icon-btn"]:disabled {{ color: {t['BORDER']}; border-color: {t['BORDER']}; background: transparent; }}
        QPushButton[role="icon-btn-ready"] {{
            background: {t['GREEN']}; color: white; border: 1.5px solid {t['GREEN']};
            border-radius: 8px; font-size: 13pt; padding: 0px; font-weight: 600;
        }}
        QPushButton[role="icon-btn-ready"]:hover {{ background: {t['GREEN2']}; border-color: {t['GREEN2']}; }}
        QPushButton[role="icon-btn-busy"] {{
            background: {t['AMBER']}; color: white; border: 1.5px solid {t['AMBER']};
            border-radius: 8px; font-size: 13pt; padding: 0px; font-weight: 600;
        }}
        QPushButton[role="icon-btn-busy"]:hover {{ background: {t['AMBER2']}; border-color: {t['AMBER2']}; }}
        QSlider {{ min-height: 26px; background: transparent; }}
        QSlider::groove:horizontal {{
            height: 4px; border-radius: 2px; background: {t['BORDER']}; border: none; margin: 0px;
        }}
        QSlider::sub-page:horizontal {{
            height: 4px; border-radius: 2px; background: {t['INDIGO']}; border: none; margin: 0px;
        }}
        QSlider::add-page:horizontal {{
            height: 4px; border-radius: 2px; background: {t['BORDER']}; border: none; margin: 0px;
        }}
        QSlider::handle:horizontal {{
            width: 20px; height: 20px; margin: -9px 0; border-radius: 10px;
            background: #FFFFFF; border: 1px solid {t['BORDER']};
        }}
        QSlider::handle:horizontal:hover {{ border: 1.5px solid {t['INDIGO']}; }}
        QSlider::handle:horizontal:pressed {{ border: 1.5px solid {t['INDIGO2']}; }}
        QProgressBar {{
            background: {t['DISABLED']}; border: none; border-radius: 6px;
            min-height: 12px; max-height: 12px; text-align: center; color: transparent;
        }}
        QProgressBar::chunk {{
            border-radius: 6px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['INDIGO']}, stop:1 {t['INDIGO2']});
        }}
        QProgressBar[state="warn"]::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['AMBER']}, stop:1 {t['AMBER2']});
        }}
        QProgressBar[state="good"]::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['GREEN']}, stop:1 {t['GREEN2']});
        }}
        QProgressBar[state="bad"]::chunk {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['RED']}, stop:1 {t['RED2']});
        }}
        QTextEdit[role="log"] {{
            background: {t['LOG_BG']}; color: {t['LOG_FG']}; border: 1px solid {t['BORDER']}; border-radius: 8px;
            font-family: '{self.MONO}'; font-size: 9pt;
        }}
        QSplitter::handle {{ background: transparent; margin: 0 2px; }}
        QSplitter::handle:hover {{ background: transparent; }}
        """
        self.setStyleSheet(qss)
        # Neither QApplication.setPalette() nor the dedicated
        # QToolTip.setPalette() kept the native tooltip from rendering
        # solid black on this machine - some Windows/Qt combinations force
        # their own tooltip chrome regardless. Styling our own _AppToolTip
        # QLabel instead (shown via the app-wide event filter below,
        # swallowing the real QEvent.ToolTip) sidesteps native tooltip
        # rendering entirely, so this is the one place that actually needs
        # to reflect the current theme's colours.
        self._tooltip.setStyleSheet(f"""
            QLabel {{
                background: {t['CARD']}; color: {t['TXT']}; border: 1px solid {t['BORDER']};
                border-radius: 8px; padding: 8px 10px; font-size: 9pt;
            }}
        """)
        for tw in (getattr(self, "pro_mode_chk", None), getattr(self, "hwaccel_chk", None)):
            if tw is not None:
                tw.setProperty("on_color", t["INDIGO"])
                tw.setProperty("off_color", t["BORDER"])
                tw.setProperty("text_color", t["TXT"])
                tw.update()
        # setStyleSheet() queues a style-change/polish for every affected
        # widget instead of applying it synchronously. Card.body's own
        # margins are set in code (setContentsMargins), so they're
        # unaffected, but every QLineEdit/QPushButton (FILES' Source/SDR
        # output fields and Browse buttons, for one) is sized purely from
        # its QSS "padding: 5px 7px"/"padding: 7px 12px" - and immediately
        # after this call, in __init__ before the window is ever shown,
        # minimumSizeHint() can still see each widget's *unpolished*
        # (smaller, native-style) size. Flushing the queued polish here
        # means minimumSizeHint() below already reflects the final, padded
        # sizes rather than a stale pre-polish guess.
        QApplication.processEvents()
        self.language_button.setIcon(header_icon("globe", t["TXT"]))
        self.theme_btn.setIcon(header_icon("sun" if self.theme_name == "dark" else "moon", t["TXT"]))
        self.language_button.setText("")
        self.theme_btn.setText("")
        self.theme_btn.setToolTip(
            self.tr("Light mode") if self.theme_name == "dark" else self.tr("Dark mode"))
        # Sized here (not at widget-creation time) because the "muted"
        # role's real 9pt font only exists after the stylesheet above is
        # applied and polished - reading fontMetrics() any earlier sees the
        # window's pre-stylesheet default font and reserves noticeably more
        # height than the text actually needs once themed.


    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.apply_theme()

    # ---- build -------------------------------------------------------
    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 10)
        root.setSpacing(6)

        # header
        header = QHBoxLayout()
        htitle = QVBoxLayout()
        title = QLabel("HDR to SDR Video Converter")
        set_role(title, "title")
        htitle.addWidget(title)
        header.addLayout(htitle)
        header.addStretch(1)

        self.kofi_btn = QPushButton("\u2665 Support on Ko-fi")
        set_role(self.kofi_btn, "kofi")
        self.kofi_btn.setToolTip(KOFI_URL)
        self.kofi_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KOFI_URL)))
        header.addWidget(self.kofi_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.language_button = QToolButton()
        self.language_button.setText("")
        self.language_button.setToolTip("Interface language")
        self.language_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.language_menu = QMenu(self.language_button)
        self.language_button.setMenu(self.language_menu)
        self.rebuild_language_menu()
        self.language_button.setFixedSize(38, 34)
        set_role(self.language_button, "theme")
        header.addWidget(self.language_button, 0, Qt.AlignmentFlag.AlignTop)

        self.theme_btn = QToolButton()
        self.theme_btn.setText("")
        self.theme_btn.setToolTip("Dark mode")
        self.theme_btn.setFixedSize(38, 34)
        set_role(self.theme_btn, "theme")
        self.theme_btn.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(10)
        root.addWidget(self.splitter, 1)

        self.left_panel = left_panel = QWidget()
        self.right_panel = right_panel = QWidget()
        left = QVBoxLayout(left_panel)
        right = QVBoxLayout(right_panel)
        left.setContentsMargins(0, 0, 0, 0)
        right.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        right.setSpacing(8)
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)

        # ---- FILES ----
        self.files_card = files = Card("Files")
        # Without this, Qt's box layout lets this card float up toward its
        # *preferred* size (the queue list can grow from 64px to its 90px
        # cap) before the cards below it even get their guaranteed minimum
        # - that stolen space is exactly what was landing as the LIVE
        # PREVIEW button row overlapping the image. Minimum vertical policy
        # means this card only ever claims its own true minimum; any real
        # leftover space still flows to CONTROLS (stretch=1) as before.
        files.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        # Source/output line edits remain as internal state used by the
        # encoder. The visible workflow is intentionally queue-first.
        self.src_edit = QLineEdit()
        self.dst_edit = QLineEdit()

        self.queue_list = QListWidget()
        # Kept small on purpose: this list scrolls internally once it has
        # more entries than fit, so a tall minimum height here only ever
        # buys "see more rows without scrolling" at the cost of pushing
        # every card below FILES further down the window - which is the
        # opposite of what we want when vertical space is tight.
        self.queue_list.setMinimumHeight(64)
        self.queue_list.setMaximumHeight(90)
        # The list's own height is capped above, so once more items are
        # queued than fit in that fixed viewport, they must scroll rather
        # than pushing the card taller. QAbstractItemView already defaults
        # to an as-needed vertical scrollbar, but that default is set
        # explicitly here so it doesn't silently depend on it.
        self.queue_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_list.setToolTip("Videos waiting to be converted with the current settings.")
        files.body.addWidget(self.queue_list)

        # "Drop video files here..." used to be its own QLabel sitting
        # above the queue list, permanently costing a row of vertical
        # space whether the queue was empty or full. It only actually
        # means something while the queue *is* empty, so instead it's a
        # transparent overlay painted directly on top of the (then-empty)
        # list viewport - free real estate that costs nothing once real
        # queue rows exist. WA_TransparentForMouseEvents lets clicks/drags
        # pass straight through to the list/window underneath instead of
        # being swallowed by the label.
        self.queue_hint_label = QLabel("Drop video files here, or add them to the conversion queue.", self.queue_list.viewport())
        self.queue_hint_label.setWordWrap(True)
        self.queue_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(self.queue_hint_label, "muted")
        self.queue_hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.queue_list.viewport().installEventFilter(self)
        # QListWidget's model emits these on every addItem()/takeItem(),
        # covering every current and future call site in one place rather
        # than needing an explicit _update_queue_hint() call added at each
        # of them.
        self.queue_list.model().rowsInserted.connect(self._update_queue_hint)
        self.queue_list.model().rowsRemoved.connect(self._update_queue_hint)
        self._update_queue_hint()
        queue_row = QHBoxLayout()
        self.queue_add_btn = QPushButton("Add videos")
        self.queue_remove_btn = QPushButton("Remove")
        self.queue_start_btn = QPushButton("Start queue")
        self.output_folder_btn = QPushButton("SDR output")
        self.queue_add_btn.clicked.connect(self.browse_source)
        self.queue_remove_btn.clicked.connect(self._remove_queue_item)
        self.queue_start_btn.clicked.connect(self.start_queue)
        self.output_folder_btn.clicked.connect(self.browse_output)
        self.output_folder_btn.setToolTip("Choose one output folder for all queued files. Default: next to each source video.")
        for button in (self.queue_add_btn, self.queue_remove_btn, self.queue_start_btn, self.output_folder_btn):
            set_role(button, "panel-toggle")
            queue_row.addWidget(button)
        files.body.addLayout(queue_row)
        left.addWidget(files)

        # ---- SOURCE ANALYSIS ----
        analysis = Card("Source analysis")
        # Same reasoning as FILES above.
        analysis.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.analysis_label = QLabel("Select a video to analyze it automatically.")
        self.analysis_label.setWordWrap(True)
        # Left+Top, not the QLabel default of Left+VCenter - vertical
        # centering was making any gap between the actual text height and a
        # reserved minimum height look like blank padding above *and* below.
        self.analysis_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        set_role(self.analysis_label, "muted")
        # Pinned to a fixed height via a QScrollArea rather than left to
        # size itself off the label's own sizeHint(). analyze() swaps this
        # label's text between a 1-line placeholder and a variable-length
        # HTML block (headline + source line + 3 recommendation lines +
        # an optional primaries warning, 5-7 lines depending on the
        # source) - letting that reflow the card's height used to change
        # self.minimumSizeHint() for the whole window on every Analyze
        # click. Because QSplitter always forces the left and right
        # columns to the same height, that change didn't just resize
        # SOURCE ANALYSIS - it changed how much filler space landed in
        # CONVERSION's own addStretch(1) too (see the comment there),
        # which is what made that empty area grow/shrink on every click.
        # Fixing the height here removes that variable at the source: the
        # left column's total height is now constant regardless of what
        # Analyze produces, and the rare case that doesn't fit the
        # reserved height scrolls internally instead of resizing anything.
        self.analysis_scroll = QScrollArea()
        self.analysis_scroll.setWidgetResizable(True)
        # Height comes from real font metrics for the 9pt "muted" role QSS
        # sets on this label (see `QLabel[role="muted"]` below), not a
        # guessed constant. analyze() fills this label with up to 7 lines
        # in the common case - headline, "Source file", the source-details
        # line, "Recommended settings", and the three preset lines - so
        # that's what's reserved here. The rare 8th line (a primaries
        # mismatch warning) or an unusually long source-details line that
        # wraps isn't worth reserving space for on every single analysis;
        # it just scrolls instead, via the QScrollArea already wrapping
        # this label.
        analysis_fm = QFontMetrics(QFont(self.FONT, 9))
        analysis_visible_lines = 7
        self.analysis_scroll.setFixedHeight(analysis_fm.lineSpacing() * analysis_visible_lines + 8)
        self.analysis_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.analysis_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.analysis_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Transparent so the scroll area/viewport don't paint a second,
        # slightly-different background on top of the card's own - without
        # this it reads as a nested box rather than part of the card.
        self.analysis_scroll.setStyleSheet("background: transparent; border: none;")
        self.analysis_scroll.viewport().setStyleSheet("background: transparent;")
        self.analysis_scroll.setWidget(self.analysis_label)
        analysis.body.addWidget(self.analysis_scroll)



        self.analyze_btn = QPushButton("Analyze selected video")
        set_role(self.analyze_btn, "panel-toggle")
        self.analyze_btn.setToolTip("Analyze the selected queue item before converting it.")
        self.analyze_btn.clicked.connect(self.analyze_selected_queue_video)
        analysis.body.addWidget(self.analyze_btn)

        # One-click "just do it" versions of the three lines above: apply
        # that tier's tone-mapping curve/encoder/quality settings without
        # the person having to translate the recommendation into dropdown
        # picks themselves. Disabled until a source has actually been
        # analyzed (self.kind starts "unknown").
        preset_row = QHBoxLayout()
        self.preset_optimal_btn = QPushButton("Optimal")
        self.preset_best_btn = QPushButton("Best quality")
        self.preset_fast_btn = QPushButton("Fast")
        for b, fn, tip in (
            (self.preset_optimal_btn, self._apply_optimal_preset,
             "Apply the Optimal tone-mapping curve for this source (matches the app defaults in most cases)."),
            (self.preset_best_btn, self._apply_best_quality_preset,
             "Apply the Best quality settings: CPU \u00b7 H.265, Pro mode, low CRF, best available curve. Slower."),
            (self.preset_fast_btn, self._apply_fast_preset,
             "Apply the Fast settings: hardware encoder if this machine has one (else CPU \u00b7 H.264), simple curve."),
        ):
            set_role(b, "panel-toggle")
            b.setEnabled(False)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            preset_row.addWidget(b)
        left.addWidget(analysis)


        # ---- LIVE PREVIEW ----
        # Two sources feed the same label at different times: an instant
        # single-frame render of the current tone-map/brightness/resolution
        # settings while idle (see _schedule_frame_preview below), and once
        # a real conversion is running, a real frame from the actual
        # tone-mapped/encoded output (ffmpeg splits the stream into the
        # encode + preview branches - see command_ffmpeg) takes over.
        preview = Card("Live preview")
        # The card needs room for the fixed viewport, caption and the action
        # row. A hand-typed setMinimumHeight() here previously understated
        # the real content height (fixed 220px viewport + caption + button
        # row + status line + margins comes to well over 330px), and since
        # an explicit minimumHeight overrides the layout's own computed
        # minimum for how much space the *parent* layout gives this card,
        # that mismatch is exactly what let the button row get squeezed on
        # top of the preview image. Preferred/Minimum lets Qt compute the
        # real minimum from the children instead, and Minimum (no shrink
        # flag) stops the parent VBox from compressing it below that even
        # when the window is short.
        preview.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.preview_label = QLabel("Choose a source video to see an instant preview of the current settings.")
        self.preview_label.setWordWrap(True)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # A fixed viewport makes the layout reserve space for the image,
        # metadata and action row separately. Without it QLabel's pixmap
        # size can arrive after the card's first geometry pass and visually
        # meet the buttons at the bottom edge.
        self.preview_label.setFixedHeight(220)
        set_role(self.preview_label, "muted")
        self.preview_label.installEventFilter(self)
        preview.body.addWidget(self.preview_label)
        self.preview_meta_label = QLabel("")
        self.preview_meta_label.setWordWrap(True)
        self.preview_meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(self.preview_meta_label, "muted")
        preview.body.addWidget(self.preview_meta_label)

        # Keep actions under the frame. Overlay controls hide the details
        # that the preview is meant to show.
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        self.preview_expand_btn = QPushButton("View full size")
        set_role(self.preview_expand_btn, "panel-toggle")
        self.preview_expand_btn.setEnabled(False)
        self.preview_expand_btn.setToolTip("View full size \u2014 open the current preview frame in a larger window.")
        self.preview_expand_btn.clicked.connect(self.show_preview_fullscreen)

        self.preview_save_btn = QPushButton("Save frame")
        set_role(self.preview_save_btn, "panel-toggle")
        self.preview_save_btn.setEnabled(False)
        self.preview_save_btn.setToolTip("Save frame \u2014 save the current preview frame to disk as an image.")
        self.preview_save_btn.clicked.connect(self.save_preview_frame)

        # The instant still-frame preview above is always rendered through
        # ffmpeg regardless of the chosen backend. It can't show motion,
        # real bitrate/quality at the actual encoder settings, or (for the
        # HandBrake backend) exactly what HandBrakeCLI will produce.
        # Rendering a full live preview during the real encode would mean
        # running ffmpeg in parallel with HandBrakeCLI just for that
        # purpose, which is a lot of moving parts for a preview - so
        # instead, offer a cheap one-off: encode a short real clip with
        # today's settings through the actual backend that will be used,
        # and let the person play it back with their own player.
        self.test_clip_btn = QPushButton("Render 5s test")
        set_role(self.test_clip_btn, "panel-toggle")
        self.test_clip_btn.setToolTip(
            "Render 5s test clip \u2014 encode a short real clip from the middle of the "
            "source with the current settings, through the backend that will be used "
            "for the full conversion, so you can check quality and motion first.")
        self.test_clip_btn.clicked.connect(self._run_test_clip)

        self.test_clip_open_btn = QPushButton("Open test clip")
        set_role(self.test_clip_open_btn, "panel-toggle")
        self.test_clip_open_btn.setEnabled(False)
        self.test_clip_open_btn.setToolTip(
            "Open test clip \u2014 no test clip rendered yet. Turns green once one is ready to play.")
        self.test_clip_open_btn.clicked.connect(self._open_test_clip)

        for b in (self.preview_expand_btn, self.preview_save_btn, self.test_clip_btn, self.test_clip_open_btn):
            btn_row.addWidget(b)
        preview.body.addLayout(btn_row)

        left.addWidget(preview)

        # ---- PROGRESS ----
        progress = Card("Progress")
        # Same reasoning as LIVE PREVIEW above: don't let this card be
        # compressed below its real content height.
        progress.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 1000)  # 0.1% resolution
        self.status_label = QLabel("")
        self.status_label.setWordWrap(False)
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
        # The bar and its status text ("Ready", "Converting... 42%", "Done -
        # saved to output"...) used to be stacked on separate lines, costing
        # a full extra row for what's essentially a short caption. Putting
        # them side by side instead - bar stretching to fill the row, text
        # right after it - keeps the same information in half the height,
        # without the contrast headaches of actually painting text on top
        # of the (colour-coded, quite thin) bar itself.
        pbar_row = QHBoxLayout()
        pbar_row.setSpacing(10)
        pbar_row.addWidget(self.pbar, 1)
        pbar_row.addWidget(self.status_label)
        progress.body.addLayout(pbar_row)
        progress.body.addWidget(self.speed_label)
        progress.body.addWidget(self.resource_label)
        left.addWidget(progress)

        # ---- CONTROLS ----
        self.controls_card = controls = Card("Controls")

        run_row = QHBoxLayout()
        self.go_btn = QPushButton("Start")
        set_role(self.go_btn, "go")
        self.go_btn.clicked.connect(self.start_queue)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        run_row.addWidget(self.pause_btn)
        run_row.addWidget(self.go_btn)
        run_row.addWidget(self.stop_btn)
        controls.body.addLayout(run_row)

        # Debounced live-apply timer: sliders fire valueChanged on every
        # intermediate tick while being dragged, so we don't want to call
        # cpu_affinity()/nice() on each of those - only on the value the
        # user actually settles on. _schedule_live_apply() (re)starts this;
        # only the last restart before it fires actually applies anything.
        self._live_apply_timer = QTimer(self)
        self._live_apply_timer.setSingleShot(True)
        self._live_apply_timer.setInterval(200)
        self._live_apply_timer.timeout.connect(lambda: self.apply_live_settings(quiet=True))

        # Same idea for the instant settings-preview frame: rendering a
        # frame takes real time (a seek + decode + filter + PNG encode),
        # so debounce it too rather than launching an ffmpeg process on
        # every intermediate slider tick.
        self._frame_preview_timer = QTimer(self)
        self._frame_preview_timer.setSingleShot(True)
        self._frame_preview_timer.setInterval(400)
        self._frame_preview_timer.timeout.connect(self._run_frame_preview)

        controls.body.addWidget(QLabel("CPU cores & priority (launch + live)"))
        cf = QHBoxLayout()
        self.cores_spin = QSpinBox()
        self.cores_spin.setRange(1, CPU_COUNT)
        self.cores_spin.setValue(CPU_COUNT)
        self.cores_spin.setFixedWidth(SPIN_WIDTH)
        self.cores_slider = QSlider(Qt.Orientation.Horizontal)
        self.cores_slider.setFixedHeight(26)
        self.cores_slider.setRange(1, CPU_COUNT)
        self.cores_slider.setValue(CPU_COUNT)
        self.cores_spin.valueChanged.connect(self.cores_slider.setValue)
        self.cores_slider.valueChanged.connect(self.cores_spin.setValue)
        self.cores_spin.valueChanged.connect(self._schedule_live_apply)
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(PRIORITY_LEVELS)
        self.priority_combo.setCurrentText("Balanced")
        loosen_combo(self.priority_combo, 10)
        self.priority_combo.currentIndexChanged.connect(self._schedule_live_apply)
        cf.addWidget(make_stepper(self.cores_spin))
        cf.addWidget(self.cores_slider, 1)
        cf.addWidget(self.priority_combo)
        controls.body.addLayout(cf)

        note = QLabel("Cores and priority apply at Start, and live while running.")
        note.setWordWrap(True)
        set_role(note, "muted")
        controls.body.addWidget(note)
        # CONTROLS now grows to fill leftover height the same way CONVERSION
        # does (see conv.body.addStretch(1) further down): the stretch lives
        # *inside* the card's own body layout, and the card itself gets
        # stretch factor 1 in left.addWidget() below. That way its white
        # background - not plain page background - is what fills any gap
        # between its natural content and the shared splitter height, so
        # CONTROLS' bottom edge tracks CONVERSION's bottom edge exactly
        # instead of drifting apart whenever one side's natural content
        # height changes relative to the other's (which is what used to
        # happen here: CONTROLS was fixed-height with an external spacer
        # absorbing the gap as bare background, so any growth on the
        # CONVERSION side - e.g. adding new settings there - just made that
        # gap bigger and more visible instead of both cards staying level).
        controls.body.addStretch(1)
        left.addWidget(controls, 1)

        # ---- CONVERSION ----
        self.conv_card = conv = Card("Conversion")
        conv.body.addWidget(QLabel("Backend"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["FFmpeg", "HandBrake"])
        loosen_combo(self.backend_combo, 14)
        conv.body.addWidget(self.backend_combo)

        conv.body.addLayout(preset_row)

        self.ffmpeg_version_note = QLabel("")
        self.ffmpeg_version_note.setWordWrap(True)
        set_role(self.ffmpeg_version_note, "warn")
        self.ffmpeg_version_note.hide()
        conv.body.addWidget(self.ffmpeg_version_note)

        conv.body.addWidget(QLabel("Tone mapping"))
        self.method_combo = QComboBox()
        loosen_combo(self.method_combo, 14)
        self.method_combo.currentTextChanged.connect(
            lambda text: self.method_combo.setToolTip(TONEMAP_INFO.get(text, "")))
        self.method_combo.currentTextChanged.connect(self._schedule_frame_preview)
        conv.body.addWidget(self.method_combo)
        self.backend_combo.currentTextChanged.connect(self.refresh_method_choices)

        conv.body.addWidget(QLabel("Encoder"))
        self.encoder_combo = QComboBox()
        self.encoder_combo.addItems(list(ENCODER_MAP.keys()))
        loosen_combo(self.encoder_combo, 14)
        conv.body.addWidget(self.encoder_combo)

        conv.body.addWidget(QLabel("Bit depth"))
        self.bit_depth_combo = QComboBox()
        self.bit_depth_combo.addItems(BIT_DEPTH_LABELS)
        self.bit_depth_combo.setCurrentText("8-bit (SDR standard)")
        self.bit_depth_combo.currentTextChanged.connect(self._on_bit_depth_changed)
        conv.body.addWidget(self.bit_depth_combo)
        # Which encoder ids exist - and which of those this machine's
        # FFmpeg/HandBrakeCLI actually has - differs by backend, so a
        # backend switch needs the same re-filter a bit-depth change does.
        self.backend_combo.currentTextChanged.connect(
            lambda _text: self._on_bit_depth_changed(self.bit_depth_combo.currentText()))
        self._on_bit_depth_changed(self.bit_depth_combo.currentText())
        # _on_bit_depth_changed() repopulates encoder_combo with
        # blockSignals(True) (see there), so a switch into/out of CPU AV1
        # triggered purely by a bit-depth change wouldn't otherwise reach
        # currentTextChanged - hook it directly instead of relying on that
        # signal for this one thing.
        self.encoder_combo.currentTextChanged.connect(lambda _t: self._update_quality_range())

        self.pro_mode_chk = ToggleSwitch("Pro mode")
        self.pro_mode_chk.toggled.connect(self.toggle_pro_mode)
        conv.body.addWidget(self.pro_mode_chk)
        pro_note = QLabel("Full CRF range, extra tone-mapping curves, brightness trim.")
        pro_note.setWordWrap(True)
        set_role(pro_note, "muted")
        conv.body.addWidget(pro_note)

        self.hwaccel_chk = ToggleSwitch("Hardware-accelerated decode")
        conv.body.addWidget(self.hwaccel_chk)
        self.hwaccel_note = QLabel("")
        self.hwaccel_note.setWordWrap(True)
        set_role(self.hwaccel_note, "muted")
        conv.body.addWidget(self.hwaccel_note)

        conv.body.addWidget(QLabel("Quality (CRF/CQ)"))
        qf = QHBoxLayout()
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(14, 28)
        self.quality_spin.setValue(18)
        self.quality_spin.setFixedWidth(SPIN_WIDTH)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setFixedHeight(26)
        self.quality_slider.setRange(14, 28)
        self.quality_slider.setValue(18)
        self.quality_spin.valueChanged.connect(self.quality_slider.setValue)
        self.quality_slider.valueChanged.connect(self.quality_spin.setValue)
        qf.addWidget(make_stepper(self.quality_spin))
        qf.addWidget(self.quality_slider, 1)
        conv.body.addLayout(qf)

        self.bitrate_label = QLabel("")
        self.bitrate_label.setWordWrap(True)
        set_role(self.bitrate_label, "muted")
        conv.body.addWidget(self.bitrate_label)

        self.brightness_label = QLabel("Brightness boost (-20 to +20, 0 = off)")
        conv.body.addWidget(self.brightness_label)
        bf = QHBoxLayout()
        self.brightness_spin = QSpinBox()
        self.brightness_spin.setRange(-20, 20)
        self.brightness_spin.setValue(0)
        self.brightness_spin.setFixedWidth(SPIN_WIDTH)
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setFixedHeight(26)
        self.brightness_slider.setRange(-20, 20)
        self.brightness_slider.setValue(0)
        self.brightness_spin.valueChanged.connect(self.brightness_slider.setValue)
        self.brightness_slider.valueChanged.connect(self.brightness_spin.setValue)
        self.brightness_spin.valueChanged.connect(self._schedule_frame_preview)
        self.brightness_label.setEnabled(False)
        self.brightness_spin.setEnabled(False)
        self.brightness_slider.setEnabled(False)
        bf.addWidget(make_stepper(self.brightness_spin))
        bf.addWidget(self.brightness_slider, 1)
        conv.body.addLayout(bf)

        self.brightness_backend_note = QLabel("")
        self.brightness_backend_note.setWordWrap(True)
        set_role(self.brightness_backend_note, "muted")
        conv.body.addWidget(self.brightness_backend_note)

        self.saturation_label = QLabel("Saturation trim (-20 to +20, 0 = off)")
        conv.body.addWidget(self.saturation_label)
        sf = QHBoxLayout()
        self.saturation_spin = QSpinBox()
        self.saturation_spin.setRange(-20, 20)
        self.saturation_spin.setValue(0)
        self.saturation_spin.setFixedWidth(SPIN_WIDTH)
        self.saturation_slider = QSlider(Qt.Orientation.Horizontal)
        self.saturation_slider.setFixedHeight(26)
        self.saturation_slider.setRange(-20, 20)
        self.saturation_slider.setValue(0)
        self.saturation_spin.valueChanged.connect(self.saturation_slider.setValue)
        self.saturation_slider.valueChanged.connect(self.saturation_spin.setValue)
        self.saturation_spin.valueChanged.connect(self._schedule_frame_preview)
        self.saturation_label.setEnabled(False)
        self.saturation_spin.setEnabled(False)
        self.saturation_slider.setEnabled(False)
        sf.addWidget(make_stepper(self.saturation_spin))
        sf.addWidget(self.saturation_slider, 1)
        conv.body.addLayout(sf)

        self.saturation_backend_note = QLabel("")
        self.saturation_backend_note.setWordWrap(True)
        set_role(self.saturation_backend_note, "muted")
        conv.body.addWidget(self.saturation_backend_note)

        self.backend_combo.currentTextChanged.connect(lambda _t: self._update_brightness_availability())
        self._update_brightness_availability()

        self.backend_combo.currentTextChanged.connect(lambda _t: self._update_hwaccel_note())
        self.encoder_combo.currentTextChanged.connect(lambda _t: self._update_hwaccel_note())
        self._update_hwaccel_note()

        conv.body.addWidget(QLabel("Output resolution"))
        self.res_combo = QComboBox()
        self.res_combo.addItems(list(RESOLUTIONS.keys()))
        loosen_combo(self.res_combo, 14)
        self.res_combo.currentTextChanged.connect(self.update_bitrate_estimate)
        self.res_combo.currentTextChanged.connect(self._schedule_frame_preview)
        conv.body.addWidget(self.res_combo)
        res_note = QLabel("Width is computed automatically to keep the source's aspect ratio.")
        res_note.setWordWrap(True)
        set_role(res_note, "muted")
        conv.body.addWidget(res_note)

        conv.body.addWidget(QLabel("Container"))
        self.container_combo = QComboBox()
        self.container_combo.addItems(["MP4", "MKV"])
        self.container_combo.currentTextChanged.connect(self._on_container_changed)
        self.container_combo.setToolTip(
            "MP4 \u2014 widest device/player support.\n"
            "MKV \u2014 needed if the source has subtitle or audio tracks "
            "(e.g. PGS, DTS) that MP4 can't hold when copied as-is."
        )
        conv.body.addWidget(self.container_combo)

        # Stretch goes here, between the settings fields and the two
        # bottom buttons, instead of after the buttons - that way the
        # buttons are pinned to the bottom edge of the CONVERSION card.
        # CONTROLS (left column) now does the exact same thing with its
        # own trailing content - see the comment on controls.body.addStretch(1)
        # above - so both cards grow to fill the shared splitter height
        # with their own background, and their bottom edges stay level
        # with each other regardless of which side's natural content is
        # currently taller.
        conv.body.addStretch(1)

        # Activity now lives right above the dependency button instead of
        # in PROGRESS - it's used far more often when something's going
        # wrong with a dependency/setup step than during normal encoding,
        # so it reads better as a neighbour of "Install missing
        # dependencies" than of the progress bar.
        self.activity_btn = QPushButton("Activity")
        self.activity_btn.setCheckable(True)
        set_role(self.activity_btn, "panel-toggle")
        self.activity_btn.clicked.connect(self.toggle_activity)
        conv.body.addWidget(self.activity_btn)

        # "Install missing dependencies" and "Update FFmpeg" used to be two
        # separate buttons. Both are Windows-only (winget / a PowerShell
        # portable-download script), so they're merged into the single
        # button Windows actually needs: install whatever's missing, or -
        # if FFmpeg and HandBrakeCLI are both already present - offer to
        # update FFmpeg instead of just saying "nothing to do".
        if sys.platform.startswith("win"):
            self.install_btn = QPushButton("Install missing dependencies")
            set_role(self.install_btn, "panel-toggle")
            self.install_btn.setToolTip(
                "Installs FFmpeg/HandBrakeCLI if either is missing. If both are "
                "already installed, offers to update FFmpeg instead.")
            self.install_btn.clicked.connect(self.install_or_update_dependencies)
            conv.body.addWidget(self.install_btn)

        right.addWidget(conv, 1)

        # ---- ACTIVITY: a collapsed log drawer below the splitter --------
        self.side_panel = QWidget()
        self.side_panel.setMinimumHeight(160)
        self.side_panel.setMaximumHeight(260)
        side = QVBoxLayout(self.side_panel)
        side.setContentsMargins(0, 0, 0, 0)

        self.side_card = QFrame()
        self.side_card.setProperty("role", "card")
        card_layout = QVBoxLayout(self.side_card)
        side.addWidget(self.side_card, 1)
        root.addWidget(self.side_panel)
        self.side_panel.hide()

        activity_page = QWidget()
        ap_layout = QVBoxLayout(activity_page)
        ap_layout.setContentsMargins(0, 0, 0, 0)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        set_role(self.log, "log")
        ap_layout.addWidget(self.log)
        card_layout.addWidget(activity_page)

        # ---- bitrate estimate: keep it live as the relevant settings change --
        self.quality_spin.valueChanged.connect(self.update_bitrate_estimate)
        self.encoder_combo.currentTextChanged.connect(self.update_bitrate_estimate)
        self.backend_combo.currentTextChanged.connect(self.update_bitrate_estimate)
        self.update_bitrate_estimate()

        # Collapsed layout - both Activity and Capabilities are collapsed at
        # this point, so sizing to this content naturally produces a compact
        # window that fits low-resolution screens; the splitter gives up
        # some of its height on demand when either panel is expanded (see
        # _select_tab). The actual resize-to-fit happens later, in
        # __init__, once apply_theme() has run too - see
        # _fit_height_to_content().
        self.layout().activate()
        self.splitter.setSizes([730, 350])

    # ---- sizing ----------------------------------------------------------
    def _fit_height_to_content(self, width=None):
        """Resize the window to the layout's real minimum height (keeping
        the current width, unless one is given). Safe to call any time
        content that affects vertical size has changed - e.g. once theming
        has been applied, or after the capability check fills in the
        Capabilities tab.

        Uses minimumSizeHint(), not sizeHint(): sizeHint() is each widget's
        own idea of a "comfortable" size and tends to run looser than what
        the layout actually needs, which is exactly the dead grey space
        below the window's content that manually shrinking the window
        reclaimed with no content loss - minimumSizeHint() is the tighter,
        layout-computed floor that avoids clipping without adding slack.
        Read it straight off self, not recombined from
        left/right_panel.minimumSizeHint() + a hand-rolled "chrome_h" +
        a flat +24 fudge factor: that reconstruction routinely overshoots
        the real minimum by 20-40px, and every one of those extra pixels
        lands as visible dead space at the bottom of the CONTROLS/
        CONVERSION cards (both end in addStretch(1) and both columns give
        them stretch factor 1, so any slack the splitter is given flows
        into these two cards and keeps their bottom edges level with each
        other - QSplitter forces both columns to the same height
        regardless of content either way; the stretch just decides
        whether that shared height shows up as growth inside the cards or
        as bare background below them). self.minimumSizeHint() is exactly
        the same bottom-up computation Qt already does for us, with no
        need to re-add a margin on top of it.
        """
        self._invalidate_all_layouts()
        self.layout().activate()
        target_h = max(560, self.minimumSizeHint().height())
        # Never resize past the visible desktop: without this cap, a
        # tall minimum height (e.g. from SOURCE ANALYSIS's multi-line
        # summary right after Analyze) can push the window's bottom edge
        # below the screen, which reads as "the buttons disappeared" even
        # though they're still in the layout. Leave a little headroom for
        # the OS taskbar/dock rather than using the full screen height.
        screen = self.screen() if hasattr(self, "screen") else None
        if screen is not None:
            avail_h = screen.availableGeometry().height()
            target_h = min(target_h, max(480, avail_h - 40))
        self.resize(width if width is not None else self.width(), target_h)
        # Also cap how tall the window can be dragged: CONTROLS and
        # CONVERSION both use stretch=1 to keep their bottom edges level
        # (see the addWidget(..., 1) comments), which means any extra
        # height beyond what the content needs doesn't go anywhere useful
        # - it just piles up as dead space under the CPU-cores note and
        # above the Activity/Install buttons. Since target_h above is
        # already the tightest height the layout needs right now, simply
        # not allowing the window past that (plus a little slack so it
        # doesn't feel clamped) removes the dead space at the source
        # instead of trying to absorb it after the fact.
        self.setMaximumHeight(target_h + 12)

    def _invalidate_all_layouts(self):
        """self.layout().activate() alone only recomputes the top-level
        root layout's geometry from whatever its children's *cached*
        sizeHint()/minimumSize() currently say - it doesn't itself force
        every nested layout (grid inside FILES' Card.body inside left_panel
        inside splitter, and so on down) to recompute its own cache first.
        Normally a style/font change propagates upward on its own via
        QEvent::LayoutRequest, but that's one hop of the cascade per
        event-loop turn - a widget whose own sizeHint() is already correct
        can still sit inside a parent QGridLayout/QVBoxLayout whose cached
        size hasn't caught up yet, which is exactly what showed up as the
        FILES card's fields being ~3px short of their own sizeHint() when
        measured immediately after a style/font change. Invalidating every
        layout in the tree here forces a fresh bottom-up recompute in one
        go, so this always produces the same height it eventually would
        anyway - no cascade timing to get unlucky with."""
        if self.layout():
            self.layout().invalidate()
        for w in self.findChildren(QWidget):
            if w.layout():
                w.layout().invalidate()

    # ---- resolution presets -------------------------------------------
    # ---- bitrate estimate ----------------------------------------------
    def update_bitrate_estimate(self):
        v = self.encoder_combo.currentText()
        if v not in ENCODER_MAP:
            self.bitrate_label.setText("")
            return
        q = self.quality_spin.value()
        target_h = RESOLUTIONS.get(self.res_combo.currentText())
        if target_h and self.src_width and self.src_height:
            w = even(round(self.src_width * target_h / self.src_height))
            h = target_h
        else:
            w, h = self.src_width, self.src_height
        kbps = estimate_bitrate_kbps(v, q, w, h)
        mbps = kbps / 1000.0
        text = f"Approx. output bitrate: ~{mbps:.1f} Mbps"
        if self.duration:
            size_gb = (kbps * 1000.0 / 8.0) * self.duration / (1024 ** 3)
            unit = "GB" if size_gb >= 0.1 else "MB"
            size_val = size_gb if unit == "GB" else size_gb * 1024
            text += f" \u2192 ~{size_val:.1f} {unit} for this source"
        self.bitrate_label.setText(text)

    # ---- pro mode --------------------------------------------------
    def _update_quality_range(self):
        """Applies the (standard, pro) range for whichever encoder is
        currently selected - see QUALITY_RANGES above. Called both on Pro
        mode toggle and on any encoder change, since CPU AV1 needs a
        different CRF range than everything else regardless of which of
        those two changed."""
        ranges = QUALITY_RANGES.get(self.encoder_combo.currentText(), DEFAULT_QUALITY_RANGE)
        lo, hi = ranges["pro"] if self.pro_mode_chk.isChecked() else ranges["standard"]
        # Read the value before touching the range: QSpinBox/QSlider clamp
        # their current value into the new range as a side effect of
        # setRange() itself, so checking "is it still in range" afterwards
        # would always say yes (it was just forced into range) and this
        # would never fall through to the real default below.
        old_value = self.quality_spin.value()
        self.quality_spin.setRange(lo, hi)
        self.quality_slider.setRange(lo, hi)
        if not (lo <= old_value <= hi):
            self.quality_spin.setValue(ranges["default"])

    def toggle_pro_mode(self, checked):
        self._update_quality_range()
        self._update_brightness_availability()
        self.refresh_method_choices()
        self.update_bitrate_estimate()
        self._schedule_frame_preview()

    def _update_hwaccel_note(self):
        # command_ffmpeg() always adds "-hwaccel auto" when checked, regardless
        # of encoder. command_handbrake() only adds "--enable-hw-decoding nvdec"
        # when checked *and* the selected encoder is NVIDIA NVENC - it's a
        # no-op with every other HandBrake encoder. The note used to just say
        # "FFmpeg only", which was wrong (it does affect the HandBrake/NVENC
        # command too) - reflect the real, per-backend behaviour instead.
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        label = self.encoder_combo.currentText()
        if using_hb:
            if label.startswith("NVIDIA NVENC"):
                text = "HandBrakeCLI: enables NVDEC decoding for this NVENC encoder."
            else:
                text = "HandBrakeCLI: only has an effect with an NVIDIA NVENC encoder \u2014 no effect with the current encoder."
        else:
            text = "FFmpeg: requests CUDA / DXVA2 / VideoToolbox decoding automatically."
        self.hwaccel_note.setText(text)

    def _update_brightness_availability(self):
        # HandBrakeCLI has no equivalent of ffmpeg's eq=brightness/saturation
        # filter, so the settings are silently ignored by command_handbrake().
        # Rather than let the live preview (always rendered via ffmpeg) show
        # an effect that never makes it into the actual HandBrake output,
        # disable both controls for that backend and explain why.
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        enabled = self.pro_mode_chk.isChecked() and not using_hb
        self.brightness_label.setEnabled(enabled)
        self.brightness_spin.setEnabled(enabled)
        self.brightness_slider.setEnabled(enabled)
        self.saturation_label.setEnabled(enabled)
        self.saturation_spin.setEnabled(enabled)
        self.saturation_slider.setEnabled(enabled)
        if using_hb:
            self.brightness_spin.setValue(0)
            self.saturation_spin.setValue(0)
            self.brightness_backend_note.setText(
                "Not available on the HandBrake backend (no equivalent filter) "
                "\u2014 switch to FFmpeg to use it.")
            self.saturation_backend_note.setText(
                "Not available on the HandBrake backend (no equivalent filter) "
                "\u2014 switch to FFmpeg to use it.")
        else:
            self.brightness_backend_note.setText("")
            self.saturation_backend_note.setText("")

    # ---- collapse toggles -----------------------------------------
    def toggle_activity(self):
        # Freeze the whole layout while changing visibility and height. This
        # prevents Qt from painting intermediate geometry during the resize.
        self.setUpdatesEnabled(False)
        if self.side_tab == "activity":
            self.side_tab = None
            self.side_panel.hide()
        else:
            self.side_tab = "activity"
            self.side_panel.show()
        self.activity_btn.setChecked(self.side_tab == "activity")
        self._fit_height_to_content()
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

    # ---- ffmpeg capability check -------------------------------------
    def check(self):
        self.encoders = set()
        self.has_bt2390 = False
        self.has_st2094 = False
        self.ffmpeg_version = None
        try:
            f = exe("ffmpeg")
            if not f:
                self.set_state("error", "FFmpeg not found.")
                return
            version_output = subprocess.check_output(
                [f, "-hide_banner", "-version"], text=True, encoding="utf-8", errors="replace",
                stderr=subprocess.STDOUT, **no_window_kwargs())
            version_match = re.search(r"ffmpeg version (\d+)\.(\d+)", version_output, re.IGNORECASE)
            if version_match:
                self.ffmpeg_version = tuple(map(int, version_match.groups()))
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
            self.refresh_method_choices(force_default=True)
            self.set_state("idle", "Ready")
        except (OSError, subprocess.CalledProcessError) as e:
            self.set_state("error", f"FFmpeg capability check failed: {e}")
        finally:
            self._update_ffmpeg_version_note()
            self._check_handbrake_encoders()
            self.refresh_caps_summary()
        # Re-run bit-depth filtering now that self.encoders/self.hb_encoders
        # reflect this machine's real capabilities, not the build()-time
        # "nothing detected yet" fallback - same reasoning as
        # refresh_method_choices(force_default=True) above, applied to the
        # Encoder combo's bit-depth-filtered list instead of Tone mapping.
        self._on_bit_depth_changed(self.bit_depth_combo.currentText())

    def _update_ffmpeg_version_note(self):
        """Show a concise, actionable warning only for versions with the
        older colour-range handling.  A missing/unparseable version should
        not look like a failure: the ordinary capability check already
        reports a genuinely unusable FFmpeg."""
        if not hasattr(self, "ffmpeg_version_note"):
            return
        if self.ffmpeg_version and self.ffmpeg_version < (7, 1):
            version = ".".join(map(str, self.ffmpeg_version))
            self.ffmpeg_version_note.setText(
                f"FFmpeg {version} detected — update to 7.1 or newer for more reliable full/limited colour-range handling.")
            self.ffmpeg_version_note.show()
        else:
            self.ffmpeg_version_note.clear()
            self.ffmpeg_version_note.hide()

    def _check_handbrake_encoders(self):
        """Parse `HandBrakeCLI --help`'s encoder list the same way `check()`
        parses `ffmpeg -encoders` above. self.hb_encoders is left empty (not
        populated with a guess) if HandBrakeCLI is missing or the parse
        fails - callers treat an empty set as "detection unavailable" and
        fall back to their static compatibility tables rather than treating
        it as "nothing is supported"."""
        self.hb_encoders = set()
        if not self.handbrake_tool:
            return
        try:
            out = subprocess.check_output(
                [self.handbrake_tool, "--help"], text=True, encoding="utf-8",
                errors="replace", stderr=subprocess.STDOUT, timeout=10,
                **no_window_kwargs())
            m = _HB_ENCODER_LIST.search(out)
            if m:
                self.hb_encoders = set(m.group(1).split())
        except Exception:
            pass

    def refresh_method_choices(self, force_default=False):
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        values = []
        # GPU/libplacebo curves only exist on the FFmpeg backend - HandBrake's
        # own --colorspace tonemap= option wraps FFmpeg's classic CPU `tonemap`
        # avfilter, not libplacebo, so it can never offer these regardless of
        # what this machine's FFmpeg build supports.
        if not using_hb and self.has_bt2390:
            values.append("BT.2390 \u00b7 GPU libplacebo/Vulkan (FFmpeg only)")
            if self.pro_mode_chk.isChecked():
                values += list(TONEMAP_PRO_GPU)
                if self.has_st2094:
                    values += list(TONEMAP_PRO_GPU_ST2094)
        values += ["Hable \u00b7 CPU tonemap", "Reinhard \u00b7 CPU tonemap", "Mobius \u00b7 CPU tonemap"]
        if self.pro_mode_chk.isChecked():
            values += list(TONEMAP_PRO_CPU)
        # force_default=True is for the one call in check() right after real
        # capability detection finishes: the combo was seeded with the CPU-
        # only fallback list before FFmpeg was even probed (see __init__, to
        # avoid the startup resize jump), so its "current" selection at that
        # point is just that placeholder default, not something the user
        # actually chose - BT.2390 becoming available should promote it to
        # selected rather than being silently skipped because the fallback
        # happens to still be a valid item. Every other call site (backend
        # switch, Pro mode toggle) leaves this False, so an actual user
        # selection is preserved whenever it's still valid.
        current = None if force_default else self.method_combo.currentText()
        self.method_combo.blockSignals(True)
        self.method_combo.clear()
        self.method_combo.addItems(values)
        for i, v in enumerate(values):
            info = TONEMAP_INFO.get(v)
            if info:
                self.method_combo.setItemData(i, info, Qt.ItemDataRole.ToolTipRole)
        self.method_combo.blockSignals(False)
        self.method_combo.setToolTip(TONEMAP_INFO.get(self.method_combo.currentText(), ""))
        if current in values:
            self.method_combo.setCurrentText(current)
        elif values:
            self.method_combo.setCurrentIndex(0)

    # ---- which encoder labels can actually produce a given bit depth ----
    def _allowed_encoder_labels(self, depth_label):
        """Encoder labels usable at `depth_label` with whichever backend is
        currently selected: structurally correct (right encoder family for
        that depth) AND, once capability detection has run, actually present
        in this machine's FFmpeg/HandBrakeCLI build. Falls back to the
        structural table alone if detection hasn't completed yet or came
        back empty, so a detection hiccup doesn't empty the dropdown."""
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        if using_hb:
            structural = {lbl for lbl, depths in HB_ENCODER_IDS.items() if depth_label in depths}
            if not self.hb_encoders:
                return structural
            return {lbl for lbl in structural if HB_ENCODER_IDS[lbl][depth_label] in self.hb_encoders}
        else:
            _, structural = FFMPEG_BIT_DEPTH.get(depth_label, (None, set()))
            if not self.encoders:
                return structural
            return {lbl for lbl in structural if ENCODER_MAP[lbl] in self.encoders}

    def _on_bit_depth_changed(self, label: str):
        allowed = self._allowed_encoder_labels(label)
        current = self.encoder_combo.currentText()
        self.encoder_combo.blockSignals(True)
        self.encoder_combo.clear()
        self.encoder_combo.addItems([e for e in ENCODER_MAP if e in allowed])
        if current in allowed:
            self.encoder_combo.setCurrentText(current)
        self.encoder_combo.blockSignals(False)
        # blockSignals above means a switch into/out of CPU AV1 caused by
        # this repopulation (e.g. the previous encoder isn't valid at the
        # new bit depth, so Qt silently lands on a different one) wouldn't
        # otherwise reach _update_quality_range via currentTextChanged.
        if hasattr(self, "quality_spin"):
            self._update_quality_range()

    def refresh_caps_summary(self):
        def yn(ok):
            return "\u2713" if ok else "\u2717"
        lines = [
            f"{yn(bool(exe('ffmpeg')))} FFmpeg \u2014 {wrappable(exe('ffmpeg')) if exe('ffmpeg') else 'not found, required for the FFmpeg backend'}",
            f"{yn(self.has_bt2390)} BT.2390 GPU tonemap (libplacebo/Vulkan)"
            + ("" if self.has_bt2390 else " \u2014 unavailable, Hable/Reinhard/Mobius still work"),
            "",
            "FFmpeg encoders:",
        ]
        for label, name in (("CPU H.264", "libx264"), ("CPU H.265", "libx265"),
                             ("NVIDIA NVENC H.264", "h264_nvenc"), ("NVIDIA NVENC H.265", "hevc_nvenc"),
                             ("AMD AMF H.264", "h264_amf"), ("AMD AMF H.265", "hevc_amf"),
                             ("Apple VideoToolbox H.264", "h264_videotoolbox"),
                             ("Apple VideoToolbox H.265", "hevc_videotoolbox"),
                             ("CPU AV1 (SVT-AV1)", "libsvtav1"),
                             ("NVIDIA NVENC AV1", "av1_nvenc"), ("AMD AMF AV1", "av1_amf")):
            lines.append(f"   {yn(name in self.encoders)} {label}")
        if self.handbrake_tool:
            hb_note = (f" \u2014 {len(self.hb_encoders)} encoder(s) detected" if self.hb_encoders
                       else " \u2014 encoder list could not be detected; availability isn't pre-checked")
        else:
            hb_note = " \u2014 not found on PATH"
        lines += [
            "",
            f"{yn(bool(self.handbrake_tool))} HandBrakeCLI"
            + (f" \u2014 {wrappable(self.handbrake_tool)}{hb_note}" if self.handbrake_tool else hb_note),
            f"{yn(HAVE_PSUTIL)} psutil (Pause/Resume, live CPU-core/priority control)"
            + ("" if HAVE_PSUTIL else " \u2014 install with: pip install psutil"),
            f"{yn(bool(self.gpu_tool))} nvidia-smi (live GPU utilization stats)"
            + ("" if self.gpu_tool else " \u2014 not found, GPU stats will be unavailable"),
        ]
        self.caps_summary = "\n".join(lines)

    # ---- one-click dependency setup (Windows / winget) -----------------
    def install_or_update_dependencies(self):
        """Single entry point for the merged "Install missing dependencies"
        button: install whatever's missing, or - if FFmpeg and HandBrakeCLI
        are both already present - offer to update FFmpeg instead of just
        telling the user there's nothing to install."""
        if self.proc is not None or getattr(self, "_setup_proc", None) is not None:
            QMessageBox.information(self, "Busy", "Finish or stop the current conversion/setup first.")
            return
        if not exe("ffmpeg") or not self.handbrake_tool:
            self.start_dependency_setup()
            return
        if QMessageBox.question(
                self, "Already installed",
                "FFmpeg and HandBrakeCLI are already installed. Update FFmpeg to the "
                "latest version now?") == QMessageBox.StandardButton.Yes:
            # A portable copy is deliberately updated in the app's own
            # folder: it never overwrites a user-managed system FFmpeg
            # installation.
            self._start_portable_dependency_setup(force_ffmpeg=True)

    def start_dependency_setup(self):
        if self.proc is not None or getattr(self, "_setup_proc", None) is not None:
            QMessageBox.information(self, "Busy", "Finish or stop the current conversion/setup first.")
            return
        winget = self._find_winget()
        if not winget:
            self._start_portable_dependency_setup()
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
        self.install_btn.setEnabled(False)
        if self.side_tab != "activity":
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
            self.install_btn.setEnabled(True)
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
        p.setProgram(self._find_winget() or "winget")
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
        self.install_btn.setEnabled(True)

    def _find_winget(self):
        """Find App Installer's executable even when WindowsApps is absent from PATH."""
        candidates = [shutil.which("winget")]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(str(Path(local_app_data) / "Microsoft" / "WindowsApps" / "winget.exe"))
        return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)

    def _start_portable_dependency_setup(self, force_ffmpeg=False):
        """Download official portable tools when Windows Package Manager is unavailable."""
        todo = []
        if force_ffmpeg or not exe("ffmpeg"):
            todo.append("FFmpeg")
        if not self.handbrake_tool:
            todo.append("HandBrakeCLI")
        if not todo:
            QMessageBox.information(self, "Nothing to install", "FFmpeg and HandBrakeCLI are already installed.")
            return

        self.install_btn.setEnabled(False)
        if self.side_tab != "activity":
            self.toggle_activity()
        self.write("\n[setup] winget is unavailable. Downloading portable " + " and ".join(todo)
                   + " to your local app-data folder…\n")

        # FFmpeg is downloaded from Gyan's Windows builds. HandBrake's URL is
        # resolved from its official GitHub release API so the app does not pin
        # users to an obsolete CLI version. The files are unpacked only under
        # %LOCALAPPDATA%, never into Program Files or system PATH.
        script = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$tools = Join-Path $env:LOCALAPPDATA 'HDR-to-SDR-Converter\tools'
$work = Join-Path $env:TEMP 'hdr-to-sdr-portable-setup'
New-Item -ItemType Directory -Force -Path $tools, $work | Out-Null

function Install-ZipTool($url, $label, $exeName, $destinationName) {
    $zip = Join-Path $work ($destinationName + '.zip')
    $expanded = Join-Path $work ($destinationName + '-expanded')
    Remove-Item -Recurse -Force $expanded -ErrorAction SilentlyContinue
    Write-Output "[setup] Downloading $label…"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-Archive -LiteralPath $zip -DestinationPath $expanded -Force
    $exe = Get-ChildItem -LiteralPath $expanded -Recurse -Filter $exeName | Select-Object -First 1
    if (-not $exe) { throw "$label archive did not contain $exeName" }
    Copy-Item -LiteralPath $exe.FullName -Destination (Join-Path $tools $exeName) -Force
}

if ($FORCE_FFMPEG -or -not (Test-Path (Join-Path $tools 'ffmpeg.exe'))) {
    Install-ZipTool 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' 'FFmpeg' 'ffmpeg.exe' 'ffmpeg'
    $probe = Get-ChildItem -LiteralPath (Join-Path $work 'ffmpeg-expanded') -Recurse -Filter 'ffprobe.exe' | Select-Object -First 1
    if ($probe) { Copy-Item -LiteralPath $probe.FullName -Destination (Join-Path $tools 'ffprobe.exe') -Force }
}

if (-not (Test-Path (Join-Path $tools 'HandBrakeCLI.exe'))) {
    $headers = @{ 'User-Agent' = 'HDR-to-SDR-Converter portable setup' }
    $release = Invoke-RestMethod -Headers $headers -Uri 'https://api.github.com/repos/HandBrake/HandBrake/releases/latest'
    $asset = $release.assets | Where-Object { $_.name -match '^HandBrakeCLI-.*-win-x86_64\.zip$' } | Select-Object -First 1
    if (-not $asset) { throw 'The latest HandBrake release has no Windows x64 CLI archive.' }
    Install-ZipTool $asset.browser_download_url 'HandBrakeCLI' 'HandBrakeCLI.exe' 'handbrake'
}

Write-Output '[setup] Portable tools are ready.'
'''.replace("$FORCE_FFMPEG", "$true" if force_ffmpeg else "$false")
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.readyReadStandardOutput.connect(lambda p=p: self.write(
            bytes(p.readAllStandardOutput()).decode("utf-8", errors="replace")))
        p.finished.connect(self._on_portable_setup_finished)
        p.errorOccurred.connect(self._on_portable_setup_error)
        p.setProgram("powershell.exe")
        p.setArguments(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
        self._setup_proc = p
        p.start()

    def _on_portable_setup_finished(self, code, status):
        self._setup_proc = None
        self.install_btn.setEnabled(True)
        if code != 0:
            self.write(f"[setup] Portable setup exited with code {code} — see output above.\n")
            return
        self.handbrake_tool = exe("HandBrakeCLI")
        self.check()
        QMessageBox.information(self, "Setup complete", "Portable dependency setup finished — see Activity log for details.")

    def _on_portable_setup_error(self, error):
        self.write("[setup] Failed to launch portable dependency setup.\n")
        self._setup_proc = None
        self.install_btn.setEnabled(True)

    # ---- workflow helpers: profiles, history, queue -----------------
    def _add_files_to_queue(self, paths):
        accepted = [str(Path(path)) for path in paths
                    if Path(path).is_file() and Path(path).suffix.lower() in self.VIDEO_EXTS]
        added = 0
        for path in accepted:
            if path not in self.queue_paths:
                self.queue_paths.append(path)
                self.queue_list.addItem(Path(path).name)
                self.queue_list.item(self.queue_list.count() - 1).setToolTip(path)
                added += 1
        if added and not Path(self.src_edit.text()).is_file():
            # Adding files must be instantaneous. ffprobe analysis is useful
            # only for the video that is about to encode, so defer it until
            # Start instead of blocking the UI on every Add videos click.
            self._set_source(self.queue_paths[0], analyze=False)
        if added:
            self.write(f"[queue] Added {added} video(s).\n")

    def analyze_selected_queue_video(self):
        row = self.queue_list.currentRow()
        if row >= 0 and row < len(self.queue_paths):
            self._set_source(self.queue_paths[row], analyze=False)
        if not Path(self.src_edit.text()).is_file():
            QMessageBox.information(self, "Analyze", "Add a video to the queue, then select it first.")
            return
        self.analyze()

    def _remove_queue_item(self):
        row = self.queue_list.currentRow()
        if row >= 0:
            self.queue_paths.pop(row)
            self.queue_list.takeItem(row)

    def start_queue(self):
        if self.proc is not None:
            QMessageBox.information(self, "Conversion running", "Wait for the current conversion to finish first.")
            return
        if not self.queue_paths:
            QMessageBox.information(self, "Queue", "Add one or more source videos first.")
            return
        self.queue_running = True
        self._start_next_queue_item()

    def _start_next_queue_item(self):
        if not self.queue_running or not self.queue_paths:
            self.queue_running = False
            self.write("[queue] Finished.\n")
            return
        path = self.queue_paths.pop(0)
        self.queue_list.takeItem(0)
        self._set_source(path, analyze=True)
        self.write(f"[queue] Starting {Path(path).name}.\n")
        QTimer.singleShot(0, self.start)

    def _enough_disk_space(self):
        destination = Path(self.dst_edit.text())
        try:
            free = shutil.disk_usage(destination.parent).free
        except OSError:
            return True
        # Estimate from input size when bitrate metadata is unavailable; the
        # 1.5x margin makes this a warning, not a false promise of exact size.
        try:
            expected = max(Path(self.src_edit.text()).stat().st_size, 512 * 1024 * 1024) * 1.5
        except OSError:
            expected = 512 * 1024 * 1024
        if free >= expected:
            return True
        return QMessageBox.question(
            self, "Low disk space",
            f"Only {free / 1024 ** 3:.1f} GB is free; the output may need about {expected / 1024 ** 3:.1f} GB. Continue?") == QMessageBox.StandardButton.Yes

    # ---- file pickers ---------------------------------------------
    def browse_source(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add videos to queue", "",
            "Video (*.mkv *.mp4 *.mov *.m4v *.ts *.webm);;All files (*.*)")
        if paths:
            self._add_files_to_queue(paths)

    def _on_container_changed(self, label):
        """Swaps the destination path's extension to match the chosen
        container, but leaves everything else about the path alone -
        including a name the person edited by hand - so switching MP4/MKV
        never silently resets a custom output filename or folder."""
        new_ext = ".mkv" if label == "MKV" else ".mp4"
        current = Path(self.dst_edit.text()) if self.dst_edit.text() else None
        if current is not None and current.suffix.lower() != new_ext:
            self.dst_edit.setText(str(current.with_suffix(new_ext)))

    def _set_source(self, x, analyze=True):
        self.src_edit.setText(x)
        p = Path(x)
        folder = self.output_folder if self.output_folder and self.output_folder.is_dir() else p.parent
        ext = ".mkv" if getattr(self, "container_combo", None) and self.container_combo.currentText() == "MKV" else ".mp4"
        self.dst_edit.setText(str(folder / (p.stem + "_SDR" + ext)))
        self.kind = "unknown"
        self.duration = 0.0
        self.src_width = None
        self.src_height = None
        if analyze:
            self.analyze()

    def browse_output(self):
        initial = str(self.output_folder or (Path(self.src_edit.text()).parent if self.src_edit.text() else Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Choose SDR output folder", initial)
        if folder:
            self.output_folder = Path(folder)
            self.output_folder_btn.setText("SDR output: custom folder")
            if self.src_edit.text():
                self._set_source(self.src_edit.text(), analyze=False)

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
                # errors="strict" (not "replace") is deliberate here: this is the one call site
                # with a dedicated except UnicodeDecodeError below that gives a specific,
                # friendlier message for corrupted/unusual metadata tags. "replace" would silently
                # swallow the decode error, making that except branch unreachable dead code.
                text=True, encoding="utf-8", errors="strict", **no_window_kwargs()))
            s = d["streams"][0]
            fmt_dur = d.get("format", {}).get("duration")
            self.duration = float(fmt_dur or s.get("duration") or 0)
            self.src_width = s.get("width")
            self.src_height = s.get("height")
            t = (s.get("color_transfer") or "unknown").lower()
            primaries = (s.get("color_primaries") or "unknown").lower()
            # ffprobe's stream field is (confusingly) named "color_space" for
            # the matrix coefficients, not a colour space in the everyday
            # sense - it's the bt709/bt2020nc/etc. value used to convert
            # YUV<->RGB, distinct from both transfer (PQ/HLG/gamma curve)
            # and primaries (gamut).
            matrix = (s.get("color_space") or "unknown").lower()
            color_range = (s.get("color_range") or "unknown").lower()
            range_name = COLOR_RANGE_NAMES.get(color_range, color_range)
            bit_depth = s.get("bits_per_raw_sample")
            if not bit_depth:
                # Fall back to reading it out of the pix_fmt name (e.g.
                # "yuv420p10le" -> 10) when the container doesn't report
                # bits_per_raw_sample directly.
                pf = (s.get("pix_fmt") or "")
                bit_depth = next((n for n in ("12", "10") if n in pf), "8")
            raw = json.dumps(s).lower()
            self._last_analysis_raw = raw
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
            rec = self._recommend(self.kind, raw)
            src_cs = (f"{TRANSFER_NAMES.get(t, t)} \u00b7 {PRIMARIES_NAMES.get(primaries, primaries)} primaries "
                      f"\u00b7 {MATRIX_NAMES.get(matrix, matrix)} matrix \u00b7 {range_name} \u00b7 {bit_depth}-bit")
            # This app always tone-maps *to* BT.709 primaries/transfer/matrix
            # - that fixed combination is what "SDR" means here, so there's
            # no target picker to show; this line just states that fixed
            # destination next to whatever the source actually is.
            cs_line = f"Source: {src_cs} \u2192 Target: BT.709 \u00b7 BT.709 \u00b7 BT.709 (SDR)"
            warn = ""
            if self.kind == "hdr" and primaries not in ("bt2020", "unknown"):
                warn = (f" Note: source primaries are {PRIMARIES_NAMES.get(primaries, primaries)}, not the usual "
                        "BT.2020 - tone mapping assumes a BT.2020 gamut, so colours may be off.")
            # Full explanation - kept as a tooltip rather than shown inline;
            # in practice almost nobody reads a 4-sentence paragraph in a
            # side-panel card, so the label itself shows a compact,
            # scannable summary instead (headline + two labelled sections)
            # and this goes on hover for anyone who wants the whole story,
            # the same show-compact/hover-for-detail pattern already used
            # for the tone-mapping dropdown.
            self.analysis_label.setToolTip(f"{note} {cs_line}. Duration: {dur_note}. {rec}{warn}")
            t_col = THEMES[self.theme_name]
            headline = {"hdr": "HDR detected", "sdr": "SDR / Rec.709 detected", "dolby": "Dolby Vision detected"}[self.kind]
            headline_color = t_col["AMBER"] if self.kind == "dolby" else t_col["INDIGO"]
            res = f"{self.src_width}\u00d7{self.src_height} \u00b7 " if self.src_width and self.src_height else ""
            src_short = (f"{res}{TRANSFER_NAMES.get(t, t)} \u00b7 {PRIMARIES_NAMES.get(primaries, primaries)} "
                         f"\u00b7 {range_name} \u00b7 {bit_depth}-bit")
            dur_short = format_time(self.duration) if self.duration else "unknown duration"
            muted = t_col["MUTED"]
            lines = [f"<b style='color:{headline_color}'>{headline}</b>"]
            lines.append(f"<b style='color:{muted}'>Source file</b>")
            lines.append(f"&nbsp;&nbsp;{src_short} \u00b7 {dur_short}")
            lines.append(f"<b style='color:{muted}'>Recommended settings</b>")
            lines.append(f"&nbsp;&nbsp;<b>Optimal</b> \u2014 {self._optimal_note(self.kind, raw)}")
            lines.append(f"&nbsp;&nbsp;<b>Best quality</b> \u2014 {self._best_quality_note()}")
            lines.append(f"&nbsp;&nbsp;<b>Fast</b> \u2014 {self._fast_note()}")
            if warn:
                lines.append(f"<span style='color:{t_col['AMBER']}'>\u26a0 primaries are "
                              f"{PRIMARIES_NAMES.get(primaries, primaries)}, not BT.2020 \u2014 colours may be off</span>")
            self.analysis_label.setText("<br>".join(lines))
            for b in (self.preset_optimal_btn, self.preset_best_btn, self.preset_fast_btn):
                b.setEnabled(True)
            self.update_bitrate_estimate()
            self._schedule_frame_preview()
        except UnicodeDecodeError as e:
            self._last_analysis_raw = None
            for b in (self.preset_optimal_btn, self.preset_best_btn, self.preset_fast_btn):
                b.setEnabled(False)
            self.analysis_label.setToolTip("")
            self.analysis_label.setText(f"Analysis failed reading ffprobe's output ({e}). "
                                         "This is usually a corrupted/unusual metadata tag in the file itself, "
                                         "not a problem with the video streams - conversion can often still proceed.")
        except Exception as e:
            self._last_analysis_raw = None
            for b in (self.preset_optimal_btn, self.preset_best_btn, self.preset_fast_btn):
                b.setEnabled(False)
            self.analysis_label.setToolTip("")
            self.analysis_label.setText(f"Analysis failed: {e}")
        # analysis_label just went from its short placeholder text to a
        # multi-line HTML block (or, in the except branches, to a wrapped
        # sentence) - but it lives inside analysis_scroll, a fixed-height
        # QScrollArea (see build()), so that no longer changes SOURCE
        # ANALYSIS's or the window's minimum height; any overflow just
        # scrolls. This call is kept as a harmless no-op safety net for
        # any other height-affecting change analyze() might make (e.g.
        # ffmpeg_version_note or another label toggling visible/hidden)
        # rather than because analysis_label's own reflow needs it now.
        self._fit_height_to_content()

    def _optimal_note(self, kind, raw):
        """What 'Optimal' means for this source - almost always just a
        confirmation that the app's own defaults already fit, since that's
        what those defaults are chosen for."""
        if kind == "sdr":
            return "tone mapping off \u2014 already the default for non-HDR sources"
        if kind == "dolby":
            return "BT.2390 \u00b7 GPU, same as HDR \u2014 needs a usable HDR10 base layer"
        has_hdr10plus = any(m in raw for m in ("smpte2094-40", "hdr10+", "hdr dynamic metadata"))
        if has_hdr10plus and self.has_bt2390 and self.has_st2094:
            return "ST2094-40 \u00b7 Pro mode \u2014 matches this source's HDR10+ metadata"
        if self.has_bt2390:
            return "BT.2390 \u00b7 GPU \u2014 already the app default"
        return "Hable \u00b7 CPU \u2014 BT.2390/libplacebo unavailable in this FFmpeg build"

    def _best_quality_note(self):
        return "CPU \u00b7 H.265, Pro mode, low CRF \u2014 slower, highest fidelity"

    def _fast_note(self):
        """Same hardware-encoder probe as _perf_recommendation(), phrased as
        the 'trade some quality for speed' tier rather than a full sentence."""
        gpu_options = [
            ("NVIDIA NVENC \u00b7 H.265", "hevc_nvenc"),
            ("AMD AMF \u00b7 H.265", "hevc_amf"),
            ("Apple VideoToolbox \u00b7 H.265", "hevc_videotoolbox"),
        ]
        gpu_hit = next((label for label, enc_id in gpu_options if enc_id in self.encoders), None)
        if gpu_hit:
            return f"{gpu_hit} \u2014 hardware, much faster, still solid quality"
        return f"CPU \u00b7 H.264 or a higher CRF \u2014 no hardware encoder detected ({CPU_COUNT} cores)"

    # ---- one-click preset application --------------------------------
    def _optimal_curve(self, kind, raw):
        """(method_combo label, needs_pro_mode) for exactly what
        _optimal_note() describes in words - kept as the one place that
        decides this, so the note text and the "Optimal" button can't
        drift out of sync with each other."""
        if kind == "sdr":
            return "None (direct, no tonemap) \u00b7 CPU tonemap", True
        if kind != "dolby":
            has_hdr10plus = any(m in raw for m in ("smpte2094-40", "hdr10+", "hdr dynamic metadata"))
            if has_hdr10plus and self.has_bt2390 and self.has_st2094:
                return "ST2094-40 (HDR10+) \u00b7 GPU libplacebo/Vulkan (FFmpeg only)", True
        if self.has_bt2390:
            return "BT.2390 \u00b7 GPU libplacebo/Vulkan (FFmpeg only)", False
        return "Hable \u00b7 CPU tonemap", False

    def _select_curve(self, label, need_pro):
        """Applies a method_combo selection, turning Pro mode on first if
        the curve needs it (toggle_pro_mode's own signal handler already
        repopulates method_combo's item list synchronously, so it's ready
        by the time we get to setCurrentText). Falls back to Hable - always
        present regardless of backend/Pro mode - if the requested label
        isn't actually a valid option right now (e.g. a GPU curve while the
        HandBrake backend is selected, which never offers libplacebo)."""
        if need_pro and not self.pro_mode_chk.isChecked():
            self.pro_mode_chk.setChecked(True)
        if self.method_combo.findText(label) < 0:
            label = "Hable \u00b7 CPU tonemap"
        self.method_combo.setCurrentText(label)

    def _apply_optimal_preset(self):
        if self.kind == "unknown" or self._last_analysis_raw is None:
            return
        label, need_pro = self._optimal_curve(self.kind, self._last_analysis_raw)
        self._select_curve(label, need_pro)
        self.write(f"[preset] Optimal applied: {self.method_combo.currentText()}\n")

    def _apply_best_quality_preset(self):
        if self.kind == "unknown" or self._last_analysis_raw is None:
            return
        label, _ = self._optimal_curve(self.kind, self._last_analysis_raw)
        self._select_curve(label, True)  # Best quality always wants Pro mode + the best available curve
        if self.encoder_combo.findText("CPU \u00b7 H.265") >= 0:
            self.encoder_combo.setCurrentText("CPU \u00b7 H.265")
        self.quality_spin.setValue(16)  # low CRF - matches _best_quality_note()'s promise
        self.write(f"[preset] Best quality applied: {self.encoder_combo.currentText()}, "
                   f"{self.method_combo.currentText()}, CRF {self.quality_spin.value()}\n")

    def _apply_fast_preset(self):
        if self.kind == "unknown" or self._last_analysis_raw is None:
            return
        gpu_options = [
            ("NVIDIA NVENC \u00b7 H.265", "hevc_nvenc"),
            ("AMD AMF \u00b7 H.265", "hevc_amf"),
            ("Apple VideoToolbox \u00b7 H.265", "hevc_videotoolbox"),
        ]
        gpu_hit = next((label for label, enc_id in gpu_options if enc_id in self.encoders), None)
        target = gpu_hit if gpu_hit and self.encoder_combo.findText(gpu_hit) >= 0 else "CPU \u00b7 H.264"
        if self.encoder_combo.findText(target) >= 0:
            self.encoder_combo.setCurrentText(target)
        if self.pro_mode_chk.isChecked():
            self.pro_mode_chk.setChecked(False)  # also resets quality_spin's range/value to the simple default
        if target.startswith("CPU"):
            self.quality_spin.setValue(23)  # a bit higher than the default 18 - "or a higher CRF" per _fast_note()
        # GPU tone-mapping is itself hardware-accelerated and cheap even
        # though it's not required for speed, so prefer it when available;
        # Hable is the lightest CPU curve otherwise.
        label = "BT.2390 \u00b7 GPU libplacebo/Vulkan (FFmpeg only)" if self.has_bt2390 else "Hable \u00b7 CPU tonemap"
        self._select_curve(label, False)
        self.write(f"[preset] Fast applied: {self.encoder_combo.currentText()}, {self.method_combo.currentText()}\n")

    def _recommend(self, kind, raw):
        """A short, honest steer toward the settings most likely to fit this
        specific source and this machine - not a hard rule, just what the
        analysis and the capability probe already gathered pointing
        somewhere useful instead of sitting unused."""
        perf = self._perf_recommendation()
        if kind == "sdr":
            return ("Tone mapping isn't really needed here - if you convert anyway, a gentle CPU tonemap "
                    f"curve (or Pro mode \u2192 None) is safer than forcing BT.2390 on non-HDR footage. {perf}")
        if kind == "dolby":
            return ("Results depend on this file having a usable HDR10 base layer - if the output looks "
                    f"off, that's the likely reason. {perf}")
        # kind == "hdr"
        has_hdr10plus = any(m in raw for m in ("smpte2094-40", "hdr10+", "hdr dynamic metadata"))
        if has_hdr10plus and self.has_bt2390 and self.has_st2094:
            curve = "This source carries HDR10+ dynamic metadata - enable Pro mode and pick ST2094-40 for the most accurate result."
        elif self.has_bt2390:
            curve = "BT.2390 on GPU (the default) is a good fit for this source."
        else:
            curve = "This FFmpeg build has no usable BT.2390/libplacebo - Hable (CPU tonemap) is the best curve available here."
        return f"{curve} {perf}"

    def _perf_recommendation(self):
        """Speed-vs-quality steer built from the same self.encoders
        capability probe the encoder dropdown filtering already uses, so
        it's specific to what this machine's FFmpeg build can actually
        reach rather than a generic tip that may not apply."""
        gpu_options = [
            ("NVIDIA NVENC \u00b7 H.265", "hevc_nvenc"),
            ("AMD AMF \u00b7 H.265", "hevc_amf"),
            ("Apple VideoToolbox \u00b7 H.265", "hevc_videotoolbox"),
        ]
        gpu_hit = next((label for label, enc_id in gpu_options if enc_id in self.encoders), None)
        if gpu_hit:
            return (f"For speed with still-solid quality, this machine has {gpu_hit} available \u2014 much "
                     "faster than CPU encoding. For the best achievable quality (at the cost of encode "
                     "time), use CPU \u00b7 H.265 with Pro mode and a low CRF instead.")
        return (f"No hardware encoder was detected here, so encoding runs on the CPU ({CPU_COUNT} cores "
                 "available) \u2014 CPU \u00b7 H.265 gives the best quality; CPU \u00b7 H.264, a higher CRF, "
                 "or limiting cores less aggressively trades some quality for shorter encode times.")

    # ---- command builders --------------------------------------------
    def scale_args_ffmpeg(self):
        h = RESOLUTIONS.get(self.res_combo.currentText())
        if not h:
            return ""
        # -2 computes width from height and forces it even, so this always
        # matches the source's real aspect ratio instead of stretching it.
        return f",scale=-2:{even(h)}:flags=lanczos"

    def brightness_args_ffmpeg(self):
        b_val = self.brightness_spin.value() if self.pro_mode_chk.isChecked() else 0
        s_val = self.saturation_spin.value() if self.pro_mode_chk.isChecked() else 0
        if b_val == 0 and s_val == 0:
            return ""
        parts = []
        if b_val != 0:
            parts.append(f"brightness={b_val / 20.0:.4f}")
        if s_val != 0:
            # eq=saturation is a multiplier around 1.0, not an offset like
            # brightness - map the -20..+20 slider onto roughly 0.5x..1.5x
            # so the low/high ends land on a clearly-visible but still
            # believable de-tint / boost instead of clipping to grayscale
            # or cartoonish oversaturation.
            parts.append(f"saturation={1.0 + s_val / 40.0:.4f}")
        return "," + "eq=" + ":".join(parts)

    def tonemap_lookup(self):
        all_curves = {**TONEMAP_BASE, **TONEMAP_PRO_GPU, **TONEMAP_PRO_GPU_ST2094, **TONEMAP_PRO_CPU}
        return all_curves.get(self.method_combo.currentText(), ("cpu", "hable"))

    def encode(self):
        v = self.encoder_combo.currentText()
        e = ENCODER_MAP[v]
        q = str(self.quality_spin.value())
        if v == "CPU \u00b7 AV1":
            # SVT-AV1's -preset is a numeric 0-13 speed/efficiency dial,
            # not x264/x265's named presets - "medium" means nothing to
            # it. 6 is SVT-AV1's own documented middle-of-the-road speed,
            # the rough equivalent of x264/x265's "medium" here.
            opts = ["-crf", q, "-preset", "6"]
        elif v.startswith("CPU"):
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

    def build_vf_chain(self, fmt="yuv420p"):
        """The exact tonemap + scale + brightness filter chain used for the
        real encode. Shared with the instant frame-preview render below so
        the preview is provably the same pipeline, not a lookalike.

        `fmt` is the pixel format the tonemap/format filter converts into -
        pass the selected bit depth's pix_fmt here directly rather than
        building with a hardcoded "yuv420p" and patching the string
        afterwards (the previous approach relied on "format=yuv420p"
        appearing exactly once in the chain, which is easy to silently
        break if the chain is ever edited)."""
        engine, code = self.tonemap_lookup()
        if engine == "gpu":
            vf = f"libplacebo=tonemapping={code}:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format={fmt}"
        else:
            vf = f"zscale=t=linear:npl=100,format=gbrpf32le,tonemap=tonemap={code}:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,format={fmt}"
        vf += self.scale_args_ffmpeg()
        vf += self.brightness_args_ffmpeg()
        return vf

    def container_args_ffmpeg(self, path=None):
        # -movflags +faststart moves the moov atom to the front for instant
        # web/streaming playback start - an MP4/MOV muxer option only.
        # FFmpeg errors out ("Unrecognized option") if it's passed while
        # writing to an MKV (Matroska) container, so it has to be left off
        # entirely rather than just being a harmless no-op there.
        ext = Path(path if path is not None else self.dst_edit.text()).suffix.lower()
        if ext in (".mp4", ".mov", ".m4v"):
            return ["-movflags", "+faststart"]
        return []

    def command_ffmpeg(self):
        e, opt = self.encode()
        fmt, _ = FFMPEG_BIT_DEPTH[self.bit_depth_combo.currentText()]
        base = [exe("ffmpeg"), "-hide_banner", "-y"]
        if self.hwaccel_chk.isChecked():
            base += ["-hwaccel", "auto"]
        base += ["-progress", "pipe:1", "-nostats", "-i", self.src_edit.text()]
        vf = self.build_vf_chain(fmt)
        container_args = self.container_args_ffmpeg()
        if self.preview_path is None:
            # No live preview requested (shouldn't normally happen for the
            # FFmpeg backend, but fall back to the plain single-output form).
            return base + ["-map", "0:v:0", *self._ffmpeg_track_args(), "-vf", vf, "-c:v", e, *opt,
                            "-pix_fmt", fmt, *self._ffmpeg_track_codec_args(),
                            *container_args, self.dst_edit.text()]
        main_out = [
            "-map", "[enc]", *self._ffmpeg_track_args(), "-c:v", e, *opt, "-pix_fmt", fmt,
            *self._ffmpeg_track_codec_args(),
            *container_args, self.dst_edit.text(),
        ]
        # Split the tone-mapped output: full-res branch goes to the encoder,
        # a low-fps branch continuously overwrites a JPEG the GUI polls for
        # the Live Preview panel - so the preview is a real frame from the
        # same pipeline, not a separate re-read of the HDR source. Capped at
        # 1600px wide rather than left at the full output resolution: at
        # only 1fps the cap barely matters for cost, but an uncapped source
        # (e.g. 8K "Source, no scaling") would otherwise write a huge JPEG
        # once a second for the whole encode. 1600 is still a large jump up
        # from a plain thumbnail, so "View full size"/"Save frame" hold up
        # against a 4K source instead of just upscaling a postage stamp.
        # The extra "format=yuvj420p" on the preview branch is deliberate:
        # the main branch's format= may be 10/12-bit when that's selected,
        # and MJPEG can only encode 8-bit - without forcing it back down
        # here, ffmpeg crashes outright (access violation) instead of
        # erroring cleanly when the preview branch hits the mjpeg encoder
        # with a pixel format it can't handle.
        fc = (f"[0:v]{vf}[tm];[tm]split=2[enc][pv];"
              f"[pv]fps=1,scale=w='min(iw\\,1600)':h=-2:flags=lanczos,format=yuvj420p[pvout]")
        preview_out = ["-map", "[pvout]", "-f", "image2", "-update", "1",
                        "-q:v", "2", "-c:v", "mjpeg", str(self.preview_path)]
        return base + ["-filter_complex", fc] + main_out + preview_out

    def command_handbrake(self):
        label = self.encoder_combo.currentText()
        depth = self.bit_depth_combo.currentText()
        ids = HB_ENCODER_IDS.get(label, {})
        # Should always be a hit given _on_bit_depth_changed already limits
        # encoder_combo to labels valid at this depth - the 8-bit id is a
        # defensive fallback only, not an expected path. If it's ever hit
        # anyway, say so in the log instead of silently swapping in 8-bit
        # output for what the UI showed as a 10/12-bit selection.
        e = ids.get(depth)
        if e is None:
            e = ids.get("8-bit (SDR standard)")
            self.write(f"[warn] {label} has no {depth} encoder id in this build \u2014 "
                       f"falling back to 8-bit output.\n")
        cmd = [self.handbrake_tool, "-i", self.src_edit.text(), "-o", self.dst_edit.text(),
               "-e", e, "-q", str(self.quality_spin.value()), "-M", "709", "-E", "copy"]
        cmd += ["--all-audio", "--all-subtitles"]
        # HandBrake's --colorspace filter only runs its zscale+tonemap chain
        # when asked to change the *transfer* away from PQ/HLG (see
        # libhb/colorspace.c) - transfer=bt709 is what triggers it, tonemap=
        # picks the curve, desat=0 matches the constant already used on the
        # FFmpeg side. engine is always "cpu" here in practice, since
        # refresh_method_choices() hides the GPU/libplacebo curves once the
        # backend is HandBrake - the isinstance-style check is just a safe
        # fallback for a stale selection caught mid-switch.
        engine, code = self.tonemap_lookup()
        if engine == "cpu":
            cmd += ["--colorspace", f"transfer=bt709:tonemap={code}:desat=0"]
        target_h = RESOLUTIONS.get(self.res_combo.currentText())
        if target_h:
            # -Y alone: HandBrakeCLI's default (loose) anamorphic mode fits
            # to this height and computes width itself, keeping the
            # source's aspect ratio - no need to also pass -X.
            cmd += ["-Y", str(even(target_h))]
        if self.hwaccel_chk.isChecked() and e.startswith("nvenc"):
            cmd += ["--enable-hw-decoding", "nvdec"]
        # HandBrakeCLI's --encoder-profile isn't required to get 10/12-bit
        # output (the *_10bit/*_12bit encoder id alone already forces it),
        # but setting it explicitly keeps the signalled profile honest for
        # players/muxers that check it rather than the actual sample depth.
        profile = HB_PROFILE_FOR_DEPTH.get(depth)
        if profile and depth in ids:
            cmd += ["--encoder-profile", profile]
        return cmd

    def command(self):
        return self.command_handbrake() if self.backend_combo.currentText().startswith("HandBrake") else self.command_ffmpeg()

    def _ffmpeg_track_args(self):
        return ["-map", "0:a?", "-map", "0:s?"]

    def _ffmpeg_track_codec_args(self):
        return ["-c:a", "copy", "-c:s", "copy"]

    # ---- run / control -------------------------------------------------
    def start(self):
        if self.proc is not None:
            QMessageBox.information(self, "Already running", "A conversion is already in progress.")
            return
        if getattr(self, "_setup_proc", None) is not None:
            QMessageBox.information(self, "Setup in progress", "Wait for dependency installation to finish first.")
            return
        self._frame_preview_timer.stop()
        if self._frame_preview_proc is not None:
            self._frame_preview_proc.kill()
            self._frame_preview_proc = None
            self._frame_preview_gen += 1
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        if using_hb and not self.handbrake_tool:
            QMessageBox.critical(self, "HandBrakeCLI not found",
                                  "Install HandBrakeCLI and ensure it's on PATH, or switch the backend to FFmpeg.")
            return
        if not Path(self.src_edit.text()).is_file() or not self.dst_edit.text():
            QMessageBox.critical(self, "Files", "Choose source and output files.")
            return
        if not self._enough_disk_space():
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
                                      "This FFmpeg lacks libplacebo/Vulkan support. Use a CPU tonemap curve "
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
        self.stopping = False
        self.paused = False
        self.pbar.setValue(0)
        self.speed_label.setText("")
        self.speed_label.hide()
        self.using_hb = using_hb
        self.out_buf = ""
        self.block = {}

        if using_hb:
            self.preview_path = None
            self.preview_label.setText("Live preview isn't available with the HandBrakeCLI backend.")
        else:
            self.preview_path = Path(tempfile.gettempdir()) / "hdr2sdr_live_preview.jpg"
            self.preview_mtime = 0
            self.preview_path.unlink(missing_ok=True)
            self.preview_label.setText("Waiting for the first preview frame\u2026")
        self._set_preview_meta("")

        cmd = self.command()
        if using_hb and self.hb_encoders:
            # Only enforced when detection actually found something (see
            # _check_handbrake_encoders) - an empty self.hb_encoders means
            # detection didn't run/parse, not that nothing is supported, and
            # blocking every HandBrake start on that would be a regression.
            try:
                hb_enc_id = cmd[cmd.index("-e") + 1]
            except ValueError:
                hb_enc_id = None
            if hb_enc_id and hb_enc_id not in self.hb_encoders:
                QMessageBox.critical(
                    self, "Encoder unavailable",
                    f"This HandBrakeCLI build doesn't support the '{hb_enc_id}' encoder/profile.")
                return
        if self.side_tab != "activity":
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
        if self.preview_path is not None:
            QTimer.singleShot(1500, self.poll_preview)

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
                out_sec = self.duration * pct / 100 if self.duration else None
                self.apply_stats({"pct": pct, "speed_x": None, "mbps": None, "fps": fps, "eta_sec": eta_sec, "out_sec": out_sec})
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
        out_sec = out_ms / 1e6 if out_ms is not None else None
        return {"pct": pct, "speed_x": speed_x, "mbps": mbps, "fps": parse_num(block.get("fps", "")),
                "eta_sec": eta_sec, "out_sec": out_sec}

    def apply_stats(self, stats):
        if stats.get("out_sec") is not None:
            self.current_out_sec = stats["out_sec"]
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
            self._validate_output(Path(self.dst_edit.text()))
        elif was_stopping:
            self.set_state("idle", "Stopped")
        else:
            self.set_state("error", "Failed \u2014 see activity log")
        self.update_run_controls()
        if self.queue_running:
            if ok:
                QTimer.singleShot(250, self._start_next_queue_item)
            else:
                self.queue_running = False
                self.write("[queue] Stopped because one item failed.\n")

    def _validate_output(self, output):
        """Fast post-flight check: verify the muxed file exists, has a video
        stream and remains close to the source duration.  It does not decode
        every frame, so it is safe to run automatically after each queue item."""
        probe = exe("ffprobe")
        if not output.is_file():
            self.write("[verify] Output file is missing.\n")
            return
        if not probe:
            self.write(f"[verify] Output exists ({output.stat().st_size / 1024 ** 2:.1f} MB); ffprobe unavailable for a deeper check.\n")
            return
        try:
            info = json.loads(subprocess.check_output(
                [probe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                 "-of", "json", str(output)], text=True, encoding="utf-8", errors="replace", **no_window_kwargs()))
            video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            duration = float(info.get("format", {}).get("duration") or 0)
            if not video:
                self.write("[verify] Warning: no video stream found in output.\n")
            elif self.duration and abs(duration - self.duration) > max(5, self.duration * .02):
                self.write(f"[verify] Warning: output duration {format_time(duration)} differs from source {format_time(self.duration)}.\n")
            else:
                self.write(f"[verify] OK · {video.get('width')}×{video.get('height')} · {format_time(duration)} · {output.stat().st_size / 1024 ** 2:.1f} MB\n")
        except Exception as e:
            self.write(f"[verify] Could not inspect output: {e}\n")

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

    # ---- instant settings preview ------------------------------------
    # Renders a single real frame through build_vf_chain() - the exact same
    # tone-map/scale/brightness pipeline the real encode uses - so this is
    # a preview of the actual output, not a lookalike approximation. Only
    # available with FFmpeg present (regardless of which backend is chosen
    # for the real encode itself; HandBrake has no equivalent way to grab
    # one filtered frame without doing a full pass).
    def _schedule_frame_preview(self, *_args):
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            return  # a real conversion owns preview_label right now
        if not Path(self.src_edit.text()).is_file():
            return
        self._frame_preview_timer.start()

    def _run_frame_preview(self):
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            return
        if not Path(self.src_edit.text()).is_file():
            return
        f = exe("ffmpeg")
        if not f:
            self.preview_label.setText("Install FFmpeg to see an instant preview of the current settings here.")
            return
        # A rapid string of setting changes (e.g. dragging the brightness
        # slider) can restart the debounce timer before the previous render
        # finished - cancel that stale render rather than letting two
        # ffmpeg processes race to write the same file.
        if self._frame_preview_proc is not None:
            self._frame_preview_proc.kill()
            self._frame_preview_proc = None
        # Middle of the source as a representative frame: cheap (no need to
        # scan for peak brightness) and, unlike frame 0, unlikely to land on
        # a black title card or logo.
        seek = max(0.0, self.duration / 2) if self.duration else 0.0
        vf = self.build_vf_chain()
        cmd = [f, "-hide_banner", "-y", "-ss", f"{seek:.2f}", "-i", self.src_edit.text(),
               "-frames:v", "1", "-vf", vf, "-c:v", "png", str(self._frame_preview_path)]
        # A generation token, not just "clear self._frame_preview_proc before
        # starting": kill() is async, so the just-killed process's finished
        # signal can still arrive *after* this new one is already running -
        # without this, that stale signal would read (or race writing) the
        # same output file a newer render just produced/is producing.
        self._frame_preview_gen += 1
        gen = self._frame_preview_gen
        proc = QProcess(self)
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        proc.finished.connect(lambda *_args, gen=gen: self._on_frame_preview_finished(gen))
        self._frame_preview_proc = proc
        self.preview_label.setText("Rendering preview frame\u2026")
        self._set_preview_meta("")
        proc.start()

    def _on_frame_preview_finished(self, gen):
        if gen != self._frame_preview_gen:
            return  # a killed/stale render finishing late - a newer one already owns the output file
        self._frame_preview_proc = None
        # A real conversion may have started while this render was in
        # flight - don't stomp its live preview with a now-stale frame.
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            return
        if not self._frame_preview_path.exists():
            self.preview_label.setText("Couldn't render a preview frame for the current settings.")
            return
        img = QImage(str(self._frame_preview_path))
        if img.isNull():
            self.preview_label.setText("Couldn't render a preview frame for the current settings.")
            return
        pix = QPixmap.fromImage(img)
        self._current_preview_pixmap = pix
        self.preview_label.setPixmap(pix.scaled(
            max(self.preview_label.width(), 320), 220,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        seek = max(0.0, self.duration / 2) if self.duration else 0.0
        shadow_pct, highlight_pct = clipping_pct(img)
        clip_bits = []
        if shadow_pct >= 0.5:
            clip_bits.append(f"{shadow_pct:.0f}% shadows clipped")
        if highlight_pct >= 0.5:
            clip_bits.append(f"{highlight_pct:.0f}% highlights clipped")
        clip_note = " \u00b7 " + " \u00b7 ".join(clip_bits) if clip_bits else " \u00b7 no clipping detected"
        self._set_preview_meta(f"Preview frame at {clock_time(seek)}" + clip_note)
        self.preview_expand_btn.setEnabled(True)
        self.preview_save_btn.setEnabled(True)

    # ---- short test-clip render -----------------------------------
    def _test_clip_command(self, out_path):
        """A ~5s real encode through the exact backend/settings that will
        be used for the real conversion, so - unlike the instant still
        frame above - it also stands in for a live preview on the
        HandBrake backend and shows motion/real encoded quality."""
        seek = max(0.0, (self.duration or 10.0) / 2 - 2.5)
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        src = self.src_edit.text()
        if using_hb:
            label = self.encoder_combo.currentText()
            depth = self.bit_depth_combo.currentText()
            ids = HB_ENCODER_IDS.get(label, {})
            e = ids.get(depth) or ids.get("8-bit (SDR standard)")
            cmd = [self.handbrake_tool, "-i", src, "-o", str(out_path),
                   "-e", e, "-q", str(self.quality_spin.value()), "-M", "709", "-E", "copy",
                   "--start-at", f"duration:{seek:.1f}", "--stop-at", "duration:5"]
            engine, code = self.tonemap_lookup()
            if engine == "cpu":
                cmd += ["--colorspace", f"transfer=bt709:tonemap={code}:desat=0"]
            target_h = RESOLUTIONS.get(self.res_combo.currentText())
            if target_h:
                cmd += ["-Y", str(even(target_h))]
            if self.hwaccel_chk.isChecked() and e.startswith("nvenc"):
                cmd += ["--enable-hw-decoding", "nvdec"]
            profile = HB_PROFILE_FOR_DEPTH.get(depth)
            if profile and depth in ids:
                cmd += ["--encoder-profile", profile]
            return cmd
        e, opt = self.encode()
        fmt, _ = FFMPEG_BIT_DEPTH[self.bit_depth_combo.currentText()]
        vf = self.build_vf_chain(fmt)
        cmd = [exe("ffmpeg"), "-hide_banner", "-y"]
        if self.hwaccel_chk.isChecked():
            cmd += ["-hwaccel", "auto"]
        cmd += ["-ss", f"{seek:.2f}", "-i", src, "-t", "5", "-vf", vf,
                "-c:v", e, *opt, "-pix_fmt", fmt, "-c:a", "copy",
                *self.container_args_ffmpeg(out_path), str(out_path)]
        return cmd

    def _refresh_preview_meta_display(self):
        """Single line under the preview frame: a test-clip status message
        takes priority over the frame's own "Preview frame at ..." meta
        text while one is set, then the meta text reappears once the
        status clears - instead of two labels stacked on top of each
        other."""
        self.preview_meta_label.setText(self._test_clip_status_text or self._preview_meta_text)

    def _set_preview_meta(self, text):
        self._preview_meta_text = text
        self._refresh_preview_meta_display()

    def _set_test_clip_status(self, text):
        self._test_clip_status_text = text
        self._refresh_preview_meta_display()

    def _run_test_clip(self):
        if self._test_clip_proc is not None:
            # Second click while one is running: treat it as cancel.
            self._test_clip_proc.kill()
            self._test_clip_proc = None
            self.test_clip_btn.setText("Render 5s test")
            set_role(self.test_clip_btn, "panel-toggle")
            self.test_clip_btn.setToolTip(
                "Render 5s test clip \u2014 encode a short real clip from the middle of the "
                "source with the current settings, through the backend that will be used "
                "for the full conversion, so you can check quality and motion first.")
            self._set_test_clip_status("Test clip render cancelled.")
            return
        if self.proc is not None and self.proc.state() == QProcess.ProcessState.Running:
            QMessageBox.information(self, "Conversion running",
                                     "Wait for the current conversion to finish first.")
            return
        if not Path(self.src_edit.text()).is_file():
            QMessageBox.critical(self, "Files", "Choose a source file first.")
            return
        using_hb = self.backend_combo.currentText().startswith("HandBrake")
        if using_hb:
            if not self.handbrake_tool:
                QMessageBox.critical(self, "HandBrakeCLI not found",
                                      "Install HandBrakeCLI and ensure it's on PATH, or switch the backend to FFmpeg.")
                return
            ext = Path(self.dst_edit.text()).suffix.lower()
            ext = ext if ext in (".mp4", ".mkv") else ".mp4"
        else:
            if not exe("ffmpeg"):
                QMessageBox.critical(self, "FFmpeg", "FFmpeg is required.")
                return
            ext = Path(self.dst_edit.text()).suffix or ".mp4"
        src_path = Path(self.src_edit.text())
        # Next to the source by default, like a "quick look" export would -
        # falls back to the system temp dir only if that folder genuinely
        # isn't writable (e.g. a read-only media mount).
        out_dir = src_path.parent
        if not os.access(out_dir, os.W_OK):
            out_dir = Path(tempfile.gettempdir())
        out = out_dir / f"{src_path.stem}_test_clip{ext}"
        out.unlink(missing_ok=True)
        cmd = self._test_clip_command(out)
        proc = QProcess(self)
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda p=proc: self.write(
            bytes(p.readAllStandardOutput()).decode(errors="replace")))
        proc.finished.connect(lambda *_args, out=out: self._on_test_clip_finished(out))
        self._test_clip_proc = proc
        self._test_clip_out = None
        self.test_clip_open_btn.setEnabled(False)
        set_role(self.test_clip_open_btn, "panel-toggle")
        self.test_clip_open_btn.setToolTip(
            "Open test clip \u2014 no test clip rendered yet. Turns green once one is ready to play.")
        self.test_clip_btn.setText("Cancel test")
        set_role(self.test_clip_btn, "stop-ready")
        self.test_clip_btn.setToolTip("Cancel the test clip render currently in progress.")
        self._set_test_clip_status("Rendering a 5s test clip with the current settings\u2026")
        proc.start()

    def _on_test_clip_finished(self, out):
        was_current = self._test_clip_proc is not None
        self._test_clip_proc = None
        self.test_clip_btn.setText("Render 5s test")
        set_role(self.test_clip_btn, "panel-toggle")
        self.test_clip_btn.setToolTip(
            "Render 5s test clip \u2014 encode a short real clip from the middle of the "
            "source with the current settings, through the backend that will be used "
            "for the full conversion, so you can check quality and motion first.")
        if not was_current:
            return  # cancelled - out may be a half-written/deleted file
        if not out.exists() or out.stat().st_size == 0:
            self._set_test_clip_status("Test clip render failed \u2014 check the Activity log for details.")
            return
        self._test_clip_out = out
        self.test_clip_open_btn.setEnabled(True)
        set_role(self.test_clip_open_btn, "panel-toggle")
        self.test_clip_open_btn.setToolTip(f"Open test clip \u2014 ready: {out}")
        self._set_test_clip_status("")

    def _open_test_clip(self):
        if self._test_clip_out and self._test_clip_out.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._test_clip_out)))

    def show_preview_fullscreen(self):
        pix = self._current_preview_pixmap
        if pix is None or pix.isNull():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Preview frame")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        screen = self.screen() if hasattr(self, "screen") else None
        avail = screen.availableGeometry() if screen else None
        max_w = int(avail.width() * 0.9) if avail else 1600
        max_h = int(avail.height() * 0.9) if avail else 900
        scaled = pix.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        lbl.setPixmap(scaled)
        lay.addWidget(lbl)
        dlg.resize(scaled.size())
        dlg.exec()

    def save_preview_frame(self):
        pix = self._current_preview_pixmap
        if pix is None or pix.isNull():
            return
        default_name = "preview_frame.png"
        if self.src_edit.text():
            default_name = Path(self.src_edit.text()).stem + "_preview.png"
        initdir = str(Path.home())
        if self.dst_edit.text() and Path(self.dst_edit.text()).parent.is_dir():
            initdir = str(Path(self.dst_edit.text()).parent)
        x, _ = QFileDialog.getSaveFileName(
            self, "Save preview frame", str(Path(initdir) / default_name),
            "PNG image (*.png);;JPEG image (*.jpg)")
        if x:
            if not pix.save(x):
                QMessageBox.critical(self, "Save preview frame", "Couldn't save the image to that location.")

    # ---- live preview -----------------------------------------------
    # Reads a JPEG that ffmpeg itself continuously overwrites (see
    # command_ffmpeg's filter_complex split) - this is a real frame from
    # the actual tone-mapped/encoded output, not a separate re-read of the
    # HDR source. Only available on the FFmpeg backend; HandBrakeCLI has no
    # equivalent split-output mechanism we can drive from the CLI.
    def poll_preview(self):
        if not (self.proc and self.proc.state() == QProcess.ProcessState.Running):
            return
        if self.preview_path and self.preview_path.exists():
            try:
                mtime = self.preview_path.stat().st_mtime
            except OSError:
                mtime = None
            if mtime and mtime != self.preview_mtime:
                self.preview_mtime = mtime
                img = QImage(str(self.preview_path))
                if not img.isNull():
                    pix = QPixmap.fromImage(img)
                    self._current_preview_pixmap = pix
                    self.preview_label.setPixmap(pix.scaled(
                        max(self.preview_label.width(), 320), 220,
                        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    self._update_preview_meta(img)
                    self.preview_expand_btn.setEnabled(True)
                    self.preview_save_btn.setEnabled(True)
        QTimer.singleShot(1500, self.poll_preview)

    def _update_preview_meta(self, img):
        pos = f"{clock_time(self.current_out_sec)} / {clock_time(self.duration)}" if self.duration else clock_time(self.current_out_sec)
        shadow_pct, highlight_pct = clipping_pct(img)
        clip_bits = []
        if shadow_pct >= 0.5:
            clip_bits.append(f"{shadow_pct:.0f}% shadows clipped")
        if highlight_pct >= 0.5:
            clip_bits.append(f"{highlight_pct:.0f}% highlights clipped")
        clip_note = " \u00b7 " + " \u00b7 ".join(clip_bits) if clip_bits else " \u00b7 no clipping detected"
        self._set_preview_meta(pos + clip_note)

    # ---- live controls --------------------------------------------------
    def require_psutil(self, feature):
        if HAVE_PSUTIL:
            return True
        msg = f"{feature} requires the psutil package, which isn't installed.\n\nInstall it with:\n\n    pip install psutil\n\nthen restart this app."
        self.write(f"[live] {feature} unavailable: psutil is not installed.\n")
        QMessageBox.information(self, "psutil required", msg)
        return False

    def _schedule_live_apply(self, *_args):
        """Hooked to the cores/priority controls so dragging them applies
        live automatically - no separate 'Apply now' button. Restarts the
        debounce timer on every change; only the value the user settles on
        (after the drag stops for _live_apply_timer's interval) gets sent
        to cpu_affinity()/nice(). No-op while nothing is running or psutil
        is unavailable, so it's harmless to leave connected at all times."""
        if not (self.proc and self.proc.state() == QProcess.ProcessState.Running):
            return
        if not HAVE_PSUTIL:
            return
        self._live_apply_timer.start()

    def apply_live_settings(self, quiet=False):
        if not (self.proc and self.proc.state() == QProcess.ProcessState.Running):
            return
        if not HAVE_PSUTIL:
            if not quiet:
                self.require_psutil("Live core/priority control")
            return
        try:
            n = max(1, min(CPU_COUNT, self.cores_spin.value()))
            psutil.Process(self.proc.processId()).cpu_affinity(list(range(n)))
            self.write(f"[live] CPU cores limited to {n} of {CPU_COUNT} (affinity).\n")
        except (AttributeError, NotImplementedError):
            if not quiet:
                QMessageBox.information(self, "Unavailable", "CPU affinity control isn't supported on this OS (e.g. macOS).")
        except Exception as e:
            self.write(f"[live] CPU core apply failed: {e}\n")
            if not quiet:
                QMessageBox.critical(self, "Live CPU cores", str(e))
        try:
            pp = psutil.Process(self.proc.processId())
            self._set_priority(pp, self.priority_combo.currentText(), quiet=quiet)
        except Exception as e:
            self.write(f"[live] Priority apply failed: {e}\n")
            if not quiet:
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

    def eventFilter(self, obj, event):
        et = event.type()
        # Custom tooltip (see _AppToolTip) - installed on the whole
        # QApplication so it catches every widget's existing setToolTip()
        # text without touching those call sites. Swallowing QEvent.ToolTip
        # here is what stops Qt's own (black-on-this-machine) tooltip from
        # ever appearing.
        if et == QEvent.Type.ToolTip:
            text = obj.toolTip() if isinstance(obj, QWidget) else ""
            if text:
                self._tooltip.setText(text)
                self._tooltip.adjustSize()
                self._tooltip.move(event.globalPos() + QPoint(14, 20))
                self._tooltip.show()
            else:
                self._tooltip.hide()
            return True
        if et in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            self._tooltip.hide()
        # Keeps the displayed preview frame scaled to whatever space it
        # currently has - not just on the next poll/render tick, but
        # immediately on any resize. This matters more than a plain
        # QWidget.resizeEvent override would catch: dragging the
        # left/right QSplitter handle (the everyday way this panel gets
        # narrower) resizes preview_label directly without the top-level
        # window itself being resized, so a MainWindow-level resizeEvent
        # would miss it entirely.
        if obj is getattr(self, "preview_label", None) and event.type() == QEvent.Type.Resize:
            cur = self.preview_label.pixmap()
            if cur is not None and not cur.isNull() and self._current_preview_pixmap is not None:
                self.preview_label.setPixmap(self._current_preview_pixmap.scaled(
                    max(self.preview_label.width(), 320), 220,
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        # The hint label isn't in any layout (it's painted directly on the
        # queue list's viewport, see _update_queue_hint below), so nothing
        # resizes it automatically when the splitter is dragged or the
        # window resized - it has to be matched to the viewport's size by
        # hand on every viewport resize.
        queue_list = getattr(self, "queue_list", None)
        if queue_list is not None and obj is queue_list.viewport() and event.type() == QEvent.Type.Resize:
            self.queue_hint_label.setGeometry(queue_list.viewport().rect())
        return super().eventFilter(obj, event)

    def _update_queue_hint(self, *args):
        """Show the "Drop video files here..." placeholder only while the
        queue is empty, and keep it sized to the current viewport - called
        once at setup and again on every add/remove via the queue list's
        model signals (see queue_list.model().rowsInserted/rowsRemoved
        above)."""
        self.queue_hint_label.setGeometry(self.queue_list.viewport().rect())
        self.queue_hint_label.setVisible(self.queue_list.count() == 0)

    def closeEvent(self, event):
        for p in (self.proc, self._frame_preview_proc, self._test_clip_proc,
                  getattr(self, "_setup_proc", None)):
            if p is not None and p.state() == QProcess.ProcessState.Running:
                p.kill()
                p.waitForFinished(1000)
        event.accept()

    # ---- drag-and-drop (whole window accepts a dropped video) -----------
    # Deliberately no dedicated drop zone/panel - that would cost vertical
    # space for something used maybe once per file. Instead the whole
    # window accepts the drop and just fills in the existing Source field,
    # exactly as if you'd used Browse.
    VIDEO_EXTS = {".mkv", ".mp4", ".mov", ".m4v", ".ts", ".webm"}

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        paths = [url.toLocalFile() for url in urls]
        if any(Path(path).suffix.lower() in self.VIDEO_EXTS for path in paths) and not (
                self.proc and self.proc.state() == QProcess.ProcessState.Running):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        paths = [url.toLocalFile() for url in urls]
        paths = [path for path in paths if Path(path).suffix.lower() in self.VIDEO_EXTS]
        if not paths:
            event.ignore()
            return
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            event.ignore()
            return
        self._add_files_to_queue(paths)
        event.acceptProposedAction()


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
    # Force Fusion instead of leaving Qt to pick whatever native style is
    # available (windowsvista/windows11 on Windows, etc.). Fusion is a
    # QStyle Qt fully implements itself, so custom QSS - especially the
    # QSlider groove/sub-page/add-page split used for the quality/brightness
    # sliders - paints exactly as designed. Native styles partially ignore
    # that QSS and fall back to their own slider chrome, which is what
    # produced the misaligned/overlapping slider look on Windows.
    app.setStyle("Fusion")
    app.setStyle(NoMenuStylePopupStyle(app.style()))
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    win = MainWindow()
    if not icon.isNull():
        win.setWindowIcon(icon)
    install_excepthook(win)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
