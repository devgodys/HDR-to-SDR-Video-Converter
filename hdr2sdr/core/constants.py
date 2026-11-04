from __future__ import annotations

import re



# Input containers/streams the app accepts for drag & drop and recognises in
# the "Video" file-dialog filter. Anything FFmpeg can demux is fine here -
# the list only decides what a drop is allowed to add silently, so keep it to
# things that are really video. (Files picked by hand in the file dialog are
# accepted whatever their extension, see MainWindow._add_files_to_queue.)
VIDEO_EXTENSIONS = frozenset({
    # common
    ".mkv", ".mp4", ".m4v", ".mov", ".qt", ".webm",
    # MPEG transport / program streams, camera & broadcast captures
    ".ts", ".m2ts", ".mts", ".m2t", ".tp", ".trp", ".mpg", ".mpeg", ".mpe", ".vob", ".mod", ".tod",
    # Microsoft / legacy
    ".avi", ".wmv", ".asf", ".divx",
    # Flash, Ogg, mobile, pro formats
    ".flv", ".f4v", ".ogv", ".ogm", ".3gp", ".3g2", ".mxf", ".dv",
    # raw elementary streams (HEVC/H.264/VC-1/AV1), e.g. from capture cards
    ".hevc", ".h265", ".265", ".h264", ".264", ".avc", ".vc1", ".ivf", ".obu",
})

# Qt file-dialog filter string: "Video (*.mkv *.mp4 ...);;All files (*.*)"
VIDEO_DIALOG_FILTER = (
    "Video (" + " ".join("*" + e for e in sorted(VIDEO_EXTENSIONS)) + ");;All files (*.*)"
)


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

# Ko-fi link shown as a small button in the header - edit to match your
# own page.
KOFI_URL = "https://ko-fi.com/devgodys"

# ffmpeg -progress emits key=value lines in blocks terminated by a
# "progress=continue"/"progress=end" line.
PROGRESS_KEYS = {"frame", "fps", "bitrate", "total_size", "out_time_us", "out_time_ms",
                  "out_time", "dup_frames", "drop_frames", "speed", "progress"}

_NUM = re.compile(r"[-+]?\d*\.?\d+")
# HandBrakeCLI prints e.g.: "Encoding: task 1 of 1, 45.23 % (123.4 fps, avg 110.3 fps, ETA 00h05m32s)"
_HB_PROGRESS = re.compile(
    r"Encoding:(?:\s*task\s+(\d+)\s+of\s+(\d+),)?.*?(\d+\.\d+)\s*%(?:\s*\(([\d.]+)\s*fps,\s*avg\s*([\d.]+)\s*fps"
    r"(?:,\s*ETA\s*(\d+)h(\d+)m(\d+)s)?\))?")
# HandBrakeCLI --help prints its "-e, --encoder <string>  Select video
# encoder: <space-separated ids>" block, then the next "--flag" line. Anchored
# on "Select video encoder:" rather than the "-e," prefix since that phrase
# has stayed stable across HandBrakeCLI versions/platforms; used to
# cross-check chosen encoder ids (including *_10bit/*_12bit ones) against
# what this actual HandBrakeCLI build really ships, the same way -encoders
# already does for FFmpeg in check() below.
_HB_ENCODER_LIST = re.compile(r"Select video encoder:\s*(.*?)(?:\n\s*-{1,2}\S|\Z)", re.S)

# Substrings FFmpeg prints when a copied (-c:a copy / -c:s copy) track can't
# actually be stored in an MP4/MOV container as-is - TrueHD/Atmos audio and
# SubRip/PGS subtitles are the common real-world cases (see the "Container"
# tooltip above). Matched case-insensitively against the raw log text so it
# doesn't depend on FFmpeg's exact wording/punctuation across versions.
_MP4_INCOMPATIBLE_TRACK_HINTS = (
    "in mp4 support is experimental",
    "add '-strict -2'",
    "codec not currently supported in container",
    "could not find tag for codec",
)

# Substrings FFmpeg/the vendor SDK print when a *hardware* encoder (NVENC,
# AMF, QSV, VideoToolbox, ...) fails to open - almost always because the
# installed GPU/driver doesn't actually support that codec (e.g. AV1 NVENC
# needs an RTX 40-series+ card; older NVIDIA GPUs list av1_nvenc as a
# compiled-in encoder but can't open it), not because anything about the
# conversion settings is wrong.
_HW_ENCODER_UNAVAILABLE_HINTS = (
    "no capable devices found",
    "opencodesessionex failed",
    "driver does not support the required nvenc api version",
    "error while opening encoder",
    "cannot load nvcuda.dll",
    "cannot load libcuda.so",
)

# Substrings printed when hardware *decode* (-hwaccel auto in FFmpeg, or
# --enable-hw-decoding nvdec in HandBrakeCLI) fails to initialise for this
# particular source/GPU/driver combination - distinct from the encode-side
# hints above. The fix here is different too: turn the "Hardware-accelerated
# decode" toggle off rather than switch encoders.
_HW_DECODE_UNAVAILABLE_HINTS = (
    "hwaccel initialisation returned error",
    "failed to create decoder",
    "failed setup for format",
    "hardware accelerated decoding of this stream is unsupported",
    "failed to get hw surface format",
    "no decoder surfaces left",
    "cannot open the hardware device",
    "cuvidcreatedecoder failed",
    "cuviddecodepicture failed",
)

# Hardware decode that initialised fine and then broke *mid-run*. Seen on a
# long 4K HDR10 -> SDR job with "-hwaccel auto" (which picked DXVA2): after
# ~24 minutes the decoder could no longer copy a finished frame out of GPU
# memory ("Failed to transfer data to output frame: -12"), FFmpeg exited with
# -12 (ENOMEM) although system RAM was fine. Unlike the init failures above,
# the work already encoded is good, so the right reaction is not "turn it off
# and start over" but "carry on from where it stopped with software decode"
# (see MainWindow._offer_resume). Only meaningful while hardware decode is on -
# the scanner gates it on hwaccel_decode_on, since with software decode an
# ENOMEM really is out-of-memory.
_HW_DECODE_RUNTIME_HINTS = (
    "failed to transfer data to output frame",
    "error processing packet in decoder: cannot allocate memory",
)

# Libplacebo prints these when its GPU (Vulkan) tone-mapping path runs out
# of VRAM mid-render. FFmpeg still exits 0 - it's a per-frame render
# failure inside libplacebo, not a crash - so it just fills the frame with
# a solid colour (usually green) instead of erroring out. Needs its own
# check since the encoder/decoder hints above all assume a nonzero exit
# code. Only affects the GPU curves (BT.2390/Spline/BT.2446A/ST2094-*);
# the CPU ones (Hable/Reinhard/Mobius/Linear) don't touch Vulkan at all.
_GPU_TONEMAP_VRAM_HINTS = (
    "vk_error_out_of_device_memory",
    "failed creating lut texture",
    "failed generating tone-mapping lut",
    "failed initializing plane texture",
    "no slab to serve request",
    "failed mapping frame id",
    "failed rendering image",
    "failed dispatching intermediate pass",
)


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
#
# Labels intentionally follow the same short "<Name> \u00b7 <engine> tonemap"
# shape for both GPU and CPU curves - the GPU ones used to carry a long
# "GPU libplacebo/Vulkan (FFmpeg only)" suffix inline in the dropdown text,
# which made every GPU entry dominate the combo box's natural width and
# didn't match the terse CPU labels. That caveat now lives in TONEMAP_INFO's
# tooltip text instead (same place every other curve's fine print lives),
# shown on hover exactly like Hable/Reinhard/Mobius already are.
TONEMAP_BASE = {
    "BT.2390 \u00b7 GPU tonemap": ("gpu", "bt.2390"),
    "Spline \u00b7 GPU tonemap": ("gpu", "spline"),
    "Hable \u00b7 CPU tonemap": ("cpu", "hable"),
    "Reinhard \u00b7 CPU tonemap": ("cpu", "reinhard"),
    "Mobius \u00b7 CPU tonemap": ("cpu", "mobius"),
}
TONEMAP_PRO_GPU = {
    "BT.2446A \u00b7 GPU tonemap": ("gpu", "bt.2446a"),
    "Auto \u00b7 GPU tonemap": ("gpu", "auto"),
}
TONEMAP_PRO_GPU_ST2094 = {
    "ST2094-40 (HDR10+) \u00b7 GPU tonemap": ("gpu", "st2094-40"),
    "ST2094-10 \u00b7 GPU tonemap": ("gpu", "st2094-10"),
}
TONEMAP_PRO_CPU = {
    "Linear \u00b7 CPU tonemap": ("cpu", "linear"),
    "Gamma \u00b7 CPU tonemap": ("cpu", "gamma"),
    "Clip \u00b7 CPU tonemap": ("cpu", "clip"),
    "None (direct, no tonemap) \u00b7 CPU tonemap": ("cpu", "none"),
}

# Experimental: short tooltip per curve, shown on hover in the Tone mapping
# dropdown. Kept short and technical: what the curve does, and the one
# tradeoff/requirement that actually changes the decision - not marketing
# copy, not a restatement of the label.
TONEMAP_INFO = {
    "BT.2390 \u00b7 GPU tonemap":
        "ITU-R reference curve: smooth highlight rolloff, preserves midtone contrast (recommended for GPU).",
    "Spline \u00b7 GPU tonemap":
        "libplacebo default: adapts pivot to scene brightness for consistent contrast.",
    "Hable \u00b7 CPU tonemap":
        "Filmic curve (Uncharted 2): smooth rolloff, punchy midtones (recommended for CPU).",
    "Reinhard \u00b7 CPU tonemap":
        "Classic photographic operator: flatter, lower-contrast curve.",
    "Mobius \u00b7 CPU tonemap":
        "Gentler highlight shoulder: retains more detail in bright areas.",
    "BT.2446A \u00b7 GPU tonemap":
        "ITU-R broadcast method: alternative if BT.2390 appears too contrasty.",
    "Auto \u00b7 GPU tonemap":
        "Automatically adapts tone mapping curve per scene.",
    "ST2094-40 (HDR10+) \u00b7 GPU tonemap":
        "Uses source HDR10+ dynamic metadata when present.",
    "ST2094-10 \u00b7 GPU tonemap":
        "Uses source ST2094-10 dynamic metadata when present.",
    "Linear \u00b7 CPU tonemap":
        "Linear scaling without compression (for testing/comparison).",
    "Gamma \u00b7 CPU tonemap":
        "Simple gamma remap (for testing/comparison).",
    "Clip \u00b7 CPU tonemap":
        "Hard-clips luminance above SDR white (diagnostic use).",
    "None (direct, no tonemap) \u00b7 CPU tonemap":
        "Direct pass-through without tone mapping (colors will appear washed out).",
}

# Toggles for the two libplacebo (GPU tonemap) behaviours that have
# been seen to occasionally corrupt output into solid-colour frames - see
# build_vf_chain() for the fuller history. Off by default because that's the
# safe/known-working setting; each can be switched back on for sources where
# the extra accuracy is worth the (small, source-dependent) risk. Written in
# the same plain-tradeoffs style as TONEMAP_INFO above, and only ever shown
# next to a GPU curve - CPU curves don't go near libplacebo at all.
LIBPLACEBO_DOVI_INFO = (
    "Applies Dolby Vision dynamic metadata (RPU) before tone mapping. "
    "Disable if output displays incorrect colors or tinting."
)
LIBPLACEBO_PEAK_DETECT_INFO = (
    "Dynamically measures peak brightness per scene on GPU. "
    "Improves contrast in scenes with large brightness swings."
)


# Shown as a tooltip on the Quality row, the same way TONEMAP_INFO explains
# each tone-mapping curve on hover.
QUALITY_SLIDER_INFO = (
    "Lower CRF/CQ = higher quality (less compression, larger file). Drag right for higher quality."
)

PRIORITY_LEVELS = ["Efficiency (low)", "Balanced", "Performance (high)"]
PRIORITY_NICE = {"Efficiency (low)": 10, "Balanced": 0, "Performance (high)": -5}

# Shared fixed width for the numeric spin boxes that sit next to a slider
# (Quality, Brightness boost, Saturation trim, Bitrate, CPU cores) - so all
# rows line up instead of each spin box sizing itself to its own digit
# count. Wide enough for Bitrate's up to 5 digits (kbps, max 50000) - the
# widest of the group by far, everything else has 2-3 digits at most and
# just ends up with a little extra breathing room either side.
SPIN_WIDTH = 72  # spin box alone, now flanked by round -/+ stepper buttons
TOUCH_SLIDER_HEIGHT = 40  # touch-friendly drag zone height; the visible groove/handle inside it are drawn smaller (handle matches the round -/+ stepper buttons' size, 26px)
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
ENCODER_EFFICIENCY = {"CPU": 1.0, "NVIDIA": 1.35, "AMD": 1.35, "Intel": 1.3, "Apple": 1.25}

ENCODER_MAP = {
    "CPU \u00b7 H.264": "libx264", "CPU \u00b7 H.265": "libx265",
    "NVIDIA NVENC \u00b7 H.264": "h264_nvenc", "NVIDIA NVENC \u00b7 H.265": "hevc_nvenc",
    "AMD AMF \u00b7 H.264": "h264_amf", "AMD AMF \u00b7 H.265": "hevc_amf",
    "Apple VideoToolbox \u00b7 H.264": "h264_videotoolbox",
    "Apple VideoToolbox \u00b7 H.265": "hevc_videotoolbox",
    # Intel Quick Sync Video (QSV) - hardware encoding on Intel iGPUs
    # (6th-gen/Skylake or newer) and Arc dGPUs. Very common on laptops that
    # have neither a discrete NVIDIA nor AMD GPU, so this was a real gap:
    # those machines previously had no hardware-encode option at all here
    # and fell back to slow CPU x264/x265. FFmpeg encoder ids confirmed
    # against `ffmpeg -encoders`; QSV needs Intel Media SDK/oneVPL runtime
    # present, which self.encoders detection (see check()) already handles
    # the same way it does for the other hardware encoders above - absent
    # if the runtime isn't installed, present if it is.
    "Intel QSV \u00b7 H.264": "h264_qsv", "Intel QSV \u00b7 H.265": "hevc_qsv",
    # AV1 is royalty-free (Alliance for Open Media Patent License) so it
    # carries none of HEVC's patent-pool baggage - no licensing blocker for
    # an MIT-licensed app. SVT-AV1 (libsvtav1) is BSD-2-Clause + Patent and
    # already ships in the winget FFmpeg build. No Apple VideoToolbox AV1
    # entry: as of 2026 VideoToolbox can decode AV1 (M3+) but still has no
    # AV1 *encoder* - Apple's hardware encoder side remains HEVC-first.
    "CPU \u00b7 AV1": "libsvtav1",
    "NVIDIA NVENC \u00b7 AV1": "av1_nvenc",
    "AMD AMF \u00b7 AV1": "av1_amf",
    # Intel Xe/Arc QSV AV1 encoder, added in HandBrake 1.6.0 / has existed
    # in FFmpeg's qsv wrapper since roughly the same era - needs an Xe- or
    # Arc-generation Intel GPU (not older integrated graphics).
    "Intel QSV \u00b7 AV1": "av1_qsv",
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
        "Intel QSV \u00b7 H.265",
        # All four AV1 encoders here handle 10-bit natively (AV1 itself
        # requires at least the encoder support 8/10-bit; none of these
        # builds does AV1 12-bit in practice, so - like H.264 above - AV1
        # simply has no 12-bit entry below).
        "CPU \u00b7 AV1", "NVIDIA NVENC \u00b7 AV1", "AMD AMF \u00b7 AV1", "Intel QSV \u00b7 AV1",
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
    # Intel Quick Sync Video ids, added HandBrake 1.6.0 (H.264/H.265) - the
    # 10-bit H.265 id shipped alongside it, confirmed in HandBrakeCLI
    # --help output and multiple HandBrake issue-tracker logs. Needs
    # 6th-gen/Skylake-or-newer Intel graphics (older iGPUs are explicitly
    # unsupported per HandBrake's own release notes) or an Arc dGPU.
    "Intel QSV \u00b7 H.264": {"8-bit (SDR standard)": "qsv_h264"},
    "Intel QSV \u00b7 H.265": {"8-bit (SDR standard)": "qsv_h265", "10-bit": "qsv_h265_10bit"},
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
    # rather than erroring. vce_av1_10bit specifically is now confirmed by
    # HandBrake 1.11's release notes ("AMD VCN AV1 10-bit encoder").
    "CPU \u00b7 AV1": {"8-bit (SDR standard)": "svt_av1", "10-bit": "svt_av1_10bit"},
    "NVIDIA NVENC \u00b7 AV1": {"8-bit (SDR standard)": "nvenc_av1", "10-bit": "nvenc_av1_10bit"},
    "AMD AMF \u00b7 AV1": {"8-bit (SDR standard)": "vce_av1", "10-bit": "vce_av1_10bit"},
    # Intel Xe/Arc QSV AV1, added HandBrake 1.6.0 alongside the H.264/H.265
    # QSV ids above.
    "Intel QSV \u00b7 AV1": {"8-bit (SDR standard)": "qsv_av1", "10-bit": "qsv_av1_10bit"},
}
# HandBrakeCLI's --encoder-profile isn't required to get 10/12-bit output -
# the *_10bit/*_12bit encoder id alone already forces it - but setting it
# explicitly keeps the stream's signalled profile honest for players/
# muxers that check it rather than the actual sample depth.
HB_PROFILE_FOR_DEPTH = {"10-bit": "main10", "12-bit": "main12"}

# Quality-spin/-slider range per encoder label (full range + a sane
# default value). Every encoder except CPU AV1 shares the same familiar
# 0-51 CRF/CQ/QP scale (x264/x265's CRF, and NVENC/AMF's -cq/-qp happen to
# use that same 0-51 range regardless of codec). SVT-AV1's CRF instead
# runs 0-63 with a much higher "sane default" (~30 vs ~18-23), so CPU AV1
# gets its own range; every other label (including the NVIDIA/AMD AV1
# hardware encoders) falls back to DEFAULT_QUALITY_RANGE unchanged.
QUALITY_RANGES = {
    "CPU \u00b7 AV1": {"range": (0, 63), "default": 30},
}
DEFAULT_QUALITY_RANGE = {"range": (0, 51), "default": 18}


# ---- Quality tier boundaries & preset matching (Stage 3) --------------------
QUALITY_TIER_BOUNDS_51 = (
    (0, 16, "Maximum"),
    (17, 21, "High"),
    (22, 27, "Medium"),
    (28, 51, "Low"),
)

QUALITY_TIER_BOUNDS_AV1 = (
    (0, 23, "Maximum"),
    (24, 32, "High"),
    (33, 40, "Medium"),
    (41, 63, "Low"),
)

QUALITY_TIER_BOUNDS_BY_ENCODER = {
    "CPU \u00b7 AV1": QUALITY_TIER_BOUNDS_AV1,
}

# Value a click on a quality-band caption jumps to (each lies inside its band;
# "Maximum"/"High" match the Best-quality preset and the encoder default).
QUALITY_TIER_REPS_51 = {"Maximum": 16, "High": 18, "Medium": 24, "Low": 30}
QUALITY_TIER_REPS_AV1 = {"Maximum": 22, "High": 30, "Medium": 36, "Low": 45}
QUALITY_TIER_REPS_BY_ENCODER = {
    "CPU \u00b7 AV1": QUALITY_TIER_REPS_AV1,
}

PROGRESS_BAR_HEIGHT = 18


def quality_tier_label(encoder_label: str, value: int) -> str:
    """Return the English source key ('Maximum' / 'High' / 'Medium' / 'Low')
    for a CRF/CQ ``value`` under ``encoder_label``.
    """
    bounds = QUALITY_TIER_BOUNDS_BY_ENCODER.get(encoder_label, QUALITY_TIER_BOUNDS_51)
    for lo, hi, label in bounds:
        if lo <= value <= hi:
            return label
    return bounds[0][2] if value < bounds[0][0] else bounds[-1][2]


def should_reset_quality_on_range_change(
    old_value: int,
    old_default: int,
    new_lo: int,
    new_hi: int,
    new_default: int | None = None,
) -> bool:
    """Return True if changing encoder should reset ``quality_spin`` to the
    new encoder's default: ONLY when ``old_value == old_default`` (and the
    default actually changed) or when ``old_value`` is outside ``[new_lo, new_hi]``.
    """
    if not (new_lo <= old_value <= new_hi):
        return True
    if old_value == old_default:
        if new_default is not None and new_default == old_default:
            return False
        return True
    return False


def match_active_preset(
    *,
    kind: str,
    encoder: str,
    method: str,
    quality: int,
    quality_mode: str = "CRF/CQ",
    optimal_curve: str = "BT.2390 \u00b7 GPU tonemap",
    fast_encoder: str = "CPU \u00b7 H.264",
    fast_curve: str = "BT.2390 \u00b7 GPU tonemap",
    default_quality: int = 18,
    crf_tolerance: int = 1,
) -> str | None:
    """Pure function: determine which preset ('optimal', 'best', 'fast', or None)
    matches the current conversion settings within ``crf_tolerance``.

    Returns None when ``kind == 'unknown'``, when ``quality_mode != 'CRF/CQ'``,
    or when the active settings do not match any of the 3 presets (Variant A:
    all 3 buttons unchecked -> 'Custom settings').
    """
    if kind == "unknown" or quality_mode != "CRF/CQ":
        return None

    # Best quality: CPU · H.265, optimal_curve, CRF ~16 (±crf_tolerance)
    if (
        encoder == "CPU \u00b7 H.265"
        and method == optimal_curve
        and abs(quality - 16) <= crf_tolerance
    ):
        return "best"

    # Fast: fast_encoder (GPU H.265 if available else CPU · H.264), fast_curve,
    # and if CPU encoder then CRF ~23 (±crf_tolerance), else default/hardware CRF
    if encoder == fast_encoder and method == fast_curve:
        if fast_encoder.startswith("CPU"):
            if abs(quality - 23) <= crf_tolerance:
                return "fast"
        else:
            if abs(quality - default_quality) <= max(crf_tolerance, 2):
                return "fast"

    # Optimal: optimal_curve and default quality (±crf_tolerance)
    if method == optimal_curve and abs(quality - default_quality) <= crf_tolerance:
        return "optimal"

    return None


GAMUT_MAPPING_LABELS = (
    "Perceptual",
    "Relative",
    "Clip",
)

GAMUT_MAPPING_MODES = {
    "Perceptual": "perceptual",
    "Relative": "relative",
    "Clip": "clip",
}

GAMUT_MAPPING_INFO = (
    "Gamut mapping (GPU only) \u2014 maps wide-gamut colors (BT.2020 / DCI-P3) to SDR (BT.709):\n"
    "\u2022 Perceptual: smooth perceptual compression (recommended).\n"
    "\u2022 Relative: colorimetric mapping preserving in-gamut hues.\n"
    "\u2022 Clip: hard clipping of out-of-gamut highlights."
)
