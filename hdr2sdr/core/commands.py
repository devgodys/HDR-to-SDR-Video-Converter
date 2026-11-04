from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from dataclasses import dataclass, replace

from hdr2sdr.core.constants import (
    ENCODER_MAP, FFMPEG_BIT_DEPTH, HB_ENCODER_IDS, HB_PROFILE_FOR_DEPTH, RESOLUTIONS, TONEMAP_BASE, TONEMAP_PRO_CPU, TONEMAP_PRO_GPU, TONEMAP_PRO_GPU_ST2094,
)
from hdr2sdr.core.utils import (
    even, exe,
)


# ======================================================================
# PURE CORE - command building
# ======================================================================
# Everything from here to the end of the "PURE CORE" marker reads only its
# arguments. No `self`, no widgets, no QSettings, no globals that change at
# runtime except the module-level constant tables above.
#
# Why this boundary exists: before it, the command builders read the 25
# Qt widgets directly (self.encoder_combo.currentText() and friends, 119
# such reads across 43 methods). That made the encoder logic impossible to
# test without constructing the whole 1100-line UI first, and it forced
# Mini mode to keep a hidden MainWindow around purely to own the widgets
# that held the settings.
#
# The rule that keeps this honest: a function below may not gain a
# parameter named `self`, and may not read a widget. If it needs a new
# input, that input becomes a field on ConversionSettings.
#
# The UI side of the boundary is MainWindow.collect_settings(), which takes
# one snapshot of the widgets and hands it over. The snapshot is one-way on
# purpose: there is no binding back from ConversionSettings to the widgets,
# because two-way synchronisation is a class of bug this app currently does
# not have and does not need to acquire. Presets already push values into
# the widgets directly, which is the only reverse direction required.
# ======================================================================

@dataclass(frozen=True)
class ConversionSettings:
    """An immutable snapshot of every setting the command builders need.

    Taken once, immediately before a command is built. Frozen so a builder
    cannot quietly mutate shared state that a later builder then reads -
    the exact failure mode that made the widget-as-model arrangement hard
    to reason about.
    """

    src: str = ""
    dst: str = ""
    backend: str = "FFmpeg"
    encoder_label: str = "CPU \u00b7 H.265"
    bit_depth_label: str = "8-bit (SDR standard)"
    quality_mode: str = "CRF/CQ"
    quality: int = 20
    target_bitrate_kbps: int = 8000
    cores: int = 0
    cpu_count: int = 1
    tonemap_method: str = "BT.2390 \u00b7 GPU tonemap"
    resolution_label: str = "Source (no scaling)"
    denoise: str = "Off"
    deband: bool = False
    resharpen: bool = False
    autocrop: bool = False
    autocrop_value: str = ""
    hwaccel: bool = False
    dv_rpu: bool = False
    peak_detect: bool = False
    brightness: int = 0
    saturation: int = 0
    desat: float = 0.0
    colortemperature: int = 6500
    gamut_mode: str = "Perceptual"
    preview_path: str = ""
    resume_seek_sec: float = 0.0
    resume_output_override: str = ""
    handbrake_tool: str = ""
    # The source's audio/subtitle streams as (index, codec_type, codec_name)
    # tuples, from ffprobe - lets the builders leave out tracks the chosen
    # output container can't hold (see plan_tracks). Empty = unknown, in
    # which case every track is mapped and copied like before.
    src_streams: tuple = ()
    # Probed source color tags from ffprobe ("unknown"/"unspecified" when
    # missing in the stream). Empty string means not probed / fully tagged,
    # keeping golden snapshots unchanged.
    src_color_transfer: str = ""
    src_color_primaries: str = ""
    src_color_matrix: str = ""
    src_color_range: str = ""

    @property
    def using_handbrake(self):
        return self.backend.startswith("HandBrake")

    @property
    def pix_fmt(self):
        return FFMPEG_BIT_DEPTH[self.bit_depth_label][0]

    @property
    def effective_dst(self):
        """Where FFmpeg actually writes. A resume run encodes its second
        segment to a separate part file that is concatenated afterwards,
        so the override wins over the destination shown in the UI."""
        return self.resume_output_override or self.dst


SETTINGS_SIGNATURE_VERSION = 2
_RESUME_SIGNATURE_FIELDS = (
    "backend",
    "encoder_label",
    "bit_depth_label",
    "quality_mode",
    "quality",
    "target_bitrate_kbps",
    "tonemap_method",
    "resolution_label",
    "denoise",
    "deband",
    "resharpen",
    "autocrop",
    "dv_rpu",
    "peak_detect",
    "brightness",
    "saturation",
    "desat",
    "colortemperature",
    "gamut_mode",
)


def gamut_mode_lookup(value: str | None) -> str:
    """Normalize UI gamut mapping label/code to libplacebo gamut_mode ('perceptual', 'relative', 'clip')."""
    norm = str(value or "").strip().lower()
    if norm.startswith("relative"):
        return "relative"
    if norm.startswith("clip"):
        return "clip"
    return "perceptual"


def settings_signature(settings: ConversionSettings) -> str:
    """Hash a versioned whitelist of output-affecting settings for safe resume checks."""
    snap = {field: getattr(settings, field) for field in _RESUME_SIGNATURE_FIELDS}
    snap["gamut_mode"] = gamut_mode_lookup(snap.get("gamut_mode"))
    payload = {
        "version": SETTINGS_SIGNATURE_VERSION,
        "settings": snap,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"v{SETTINGS_SIGNATURE_VERSION}:{digest}"


CROPDETECT_FILTER = "format=yuv420p,cropdetect=24:2:0"
_CROP_RE = re.compile(r"crop=(-?\d+):(-?\d+):(-?\d+):(-?\d+)")


def crop_sample_offsets(duration: float | None) -> list[tuple[float, float]]:
    """Return (start_sec, duration_sec) sample windows for cropdetect."""
    total = float(duration or 0.0)
    if total < 15.0:
        return [(max(0.0, total / 2.0 - 2.5), 5.0)]
    return [(max(0.0, total * frac - 1.0), 2.0) for frac in (0.2, 0.5, 0.8)]


def select_crop_filter(outputs, src_width: int | None = None,
                       src_height: int | None = None) -> str:
    """Combine cropdetect outputs across sample windows into a safe crop=W:H:X:Y filter."""
    if isinstance(outputs, str):
        chunks = [outputs]
    else:
        chunks = [str(item) for item in (outputs or ()) if item]
    settled: list[tuple[int, int, int, int]] = []
    for text in chunks:
        valid_in_chunk: list[tuple[int, int, int, int]] = []
        for w_s, h_s, x_s, y_s in _CROP_RE.findall(text):
            w, h, x, y = int(w_s), int(h_s), int(x_s), int(y_s)
            if w <= 0 or h <= 0 or x < 0 or y < 0:
                continue
            if src_width and x + w > src_width:
                continue
            if src_height and y + h > src_height:
                continue
            valid_in_chunk.append((w, h, x, y))
        if valid_in_chunk:
            settled.append(valid_in_chunk[-1])
    if not settled:
        return ""
    x1 = min(x for _w, _h, x, _y in settled)
    y1 = min(y for _w, _h, _x, y in settled)
    x2 = max(x + w for w, _h, x, _y in settled)
    y2 = max(y + h for _w, h, _x, y in settled)
    width = even(x2 - x1)
    height = even(y2 - y1)
    if src_width and src_height and x1 == 0 and y1 == 0 and width >= src_width and height >= src_height:
        return ""
    return f"crop={width}:{height}:{x1}:{y1}"


def tonemap_lookup(method_label):
    all_curves = {**TONEMAP_BASE, **TONEMAP_PRO_GPU, **TONEMAP_PRO_GPU_ST2094, **TONEMAP_PRO_CPU}
    return all_curves.get(method_label, ("cpu", "hable"))


def build_scale_args(s):
    h = RESOLUTIONS.get(s.resolution_label)
    if not h:
        return ""
    # -2 computes width from height and forces it even, so this always
    # matches the source's real aspect ratio instead of stretching it.
    return f",scale=-2:{even(h)}:flags=lanczos"


def build_brightness_args(s):
    b_val = s.brightness
    s_val = s.saturation
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


def build_colortemperature_args(s):
    """A separate FFmpeg filter (not part of eq=), applied after
    brightness/saturation: a direct warmer/cooler dial that works
    identically regardless of GPU (libplacebo) or CPU (zscale) tonemap
    path. 6500K is neutral (daylight reference white) - the
    filter is only appended when set to something else, so the default
    ConversionSettings() still produces the exact filter chain this
    produced before this option existed. Requires FFmpeg 4.4+
    (colortemperature filter); older builds will fail at encode time
    with an "unknown filter" error rather than being silently ignored,
    same as any other filter this app assumes is present."""
    if s.colortemperature == 6500:
        return ""
    return f",colortemperature=temperature={s.colortemperature}"


class ToolMissing(RuntimeError):
    """A required external tool (FFmpeg / HandBrakeCLI) could not be found."""


def _ffmpeg_exe():
    path = exe("ffmpeg")
    if not path:
        raise ToolMissing("FFmpeg not found")
    return path


def build_container_args(path, encoder=None):
    # -movflags +faststart moves the moov atom to the front for instant
    # web/streaming playback start - an MP4/MOV muxer option only.
    # FFmpeg errors out ("Unrecognized option") if it's passed while
    # writing to an MKV (Matroska) container, so it has to be left off
    # entirely rather than just being a harmless no-op there.
    ext = Path(path).suffix.lower()
    if ext in (".mp4", ".mov", ".m4v"):
        args = ["-movflags", "+faststart"]
        # libx265 / *_hevc into MP4 is tagged "hev1" by default, which QuickTime, Safari and
        # iOS refuse to play; "hvc1" plays everywhere.
        if encoder and ("hevc" in encoder or encoder == "libx265"):
            args = ["-tag:v", "hvc1", *args]
        return args
    return []


def build_encode_args(s):
    v = s.encoder_label
    e = ENCODER_MAP[v]
    bitrate_mode = s.quality_mode == "Bitrate"
    if bitrate_mode:
        # Single-pass VBR with a capped peak, not a true two-pass encode
        # (that would need to run ffmpeg twice - a bigger change to the
        # run pipeline than this warrants right now). Good enough to hit
        # roughly the requested average size; not frame-accurate.
        br = s.target_bitrate_kbps
        if v == "CPU \u00b7 AV1":
            opts = ["-b:v", f"{br}k", "-preset", "6"]
        else:
            opts = ["-b:v", f"{br}k", "-maxrate", f"{int(br * 1.5)}k", "-bufsize", f"{br * 2}k"]
            if v.startswith("CPU"):
                opts += ["-preset", "medium"]
        cores = s.cores
        if v.startswith("CPU") and 0 < cores < s.cpu_count:
            opts += ["-threads", str(cores)]
        return e, opts
    q = str(s.quality)
    if v == "CPU \u00b7 AV1":
        # SVT-AV1's -preset is a numeric 0-13 speed/efficiency dial,
        # not x264/x265's named presets - "medium" means nothing to
        # it. 6 is SVT-AV1's own documented middle-of-the-road speed,
        # the rough equivalent of x264/x265's "medium" here.
        opts = ["-crf", q, "-preset", "6"]
    elif v.startswith("CPU"):
        opts = ["-crf", q, "-preset", "medium"]
    elif v.startswith("NVIDIA"):
        # Previously "-rc vbr -cq Q -b:v 0". That combination is a
        # known-flaky corner of ffmpeg's nvenc wrapper: depending on
        # ffmpeg/driver/SDK version, vbr+cq doesn't reliably drop the
        # rate controller's internal bitrate ceiling even with
        # "-b:v 0" explicitly set, so the requested -cq can end up
        # silently overridden by a much lower effective bitrate cap -
        # exactly the "CQ says best quality, output is a fraction of
        # the CPU CRF equivalent's size" surprise. "-rc constqp -qp Q"
        # is the reliable alternative: a direct constant-quantizer
        # mode with no separate bitrate/rate-control layer to conflict
        # with the requested quality, the nvenc equivalent of x264/
        # x265's -crf. Its "-qp" option's own "automatic" sentinel is
        # -1, not 0 (unlike -cq's 0-means-automatic), so - unlike the
        # old -cq path - 0 here is already a real, honored value and
        # needs no remapping.
        if v.endswith("AV1"):
            # av1_nvenc's -qp runs 0-255 (hevc/h264_nvenc: 0-51); the UI scale is 0-51 for all
            # hardware encoders, so stretch it - passing 18 straight through would be almost no
            # compression at all.
            q = str(min(255, round(s.quality * 255 / 51)))
        opts = ["-rc", "constqp", "-qp", q]
    elif v.startswith("AMD"):
        if e == "av1_amf":
            # AMF AV1 uses QPIndex 1-255 (zero is reserved), unlike the
            # app's 0-51 UI scale and unlike AMF H.264/HEVC's 0-51 QP.
            q = str(min(255, max(1, round(1 + s.quality * 254 / 51))))
        opts = ["-rc", "cqp", "-qp_i", q, "-qp_p", q]
    else:
        # Intel QSV's "-q:v"/global_quality (and Apple VideoToolbox's
        # equivalent) are documented as valid over 1-51 only, unlike
        # x264/x265 CRF where 0 is a legitimate (lossless) value - 0
        # here is out of range and risks the quality option being
        # silently ignored/falling back to a default rate-control mode
        # instead of actually applying "best quality" (the same class
        # of 0-is-not-really-best footgun the old nvenc "-cq" path
        # used to have, before that was switched to "-rc constqp -qp"
        # above). Remap 0 to 1, the real best usable value.
        qv = "1" if s.quality == 0 else q
        opts = ["-q:v", qv]
    cores = s.cores
    if v.startswith("CPU") and 0 < cores < s.cpu_count:
        opts = opts + ["-threads", str(cores)]
    return e, opts


_UNKNOWN_COLOR_TAGS = ("unknown", "unspecified")


def _cpu_zscale_color_args(s) -> tuple[str, list[str]]:
    """Return (first_zscale_expr, warnings) for the CPU (zscale+tonemap) path.

    When transfer, primaries, or matrix are unknown/unspecified in the probed
    source, zscale fails with 'code 3074: no path between colorspaces'.
    Explicitly pass tin/pin/min/rin and output p=bt2020 on the first zscale in
    that case, while keeping fully-tagged sources (and unprobed defaults) on the
    exact 'zscale=t=linear:npl=100' string required by golden snapshots.
    """
    raw_t = str(getattr(s, "src_color_transfer", "") or "").strip().lower()
    raw_p = str(getattr(s, "src_color_primaries", "") or "").strip().lower()
    raw_m = str(getattr(s, "src_color_matrix", "") or "").strip().lower()
    raw_r = str(getattr(s, "src_color_range", "") or "").strip().lower()

    any_probed = any((raw_t, raw_p, raw_m, raw_r))
    if not any_probed:
        return "zscale=t=linear:npl=100", []

    t_unknown = (not raw_t) or (raw_t in _UNKNOWN_COLOR_TAGS)
    p_unknown = (not raw_p) or (raw_p in _UNKNOWN_COLOR_TAGS)
    m_unknown = (not raw_m) or (raw_m in _UNKNOWN_COLOR_TAGS)

    # Absence of range alone does not break zscale and requires no changes.
    if not (t_unknown or p_unknown or m_unknown):
        return "zscale=t=linear:npl=100", []

    if t_unknown:
        tin = "smpte2084"
    elif raw_t in ("pq", "smpte2084"):
        tin = "smpte2084"
    elif raw_t in ("hlg", "arib-std-b67"):
        tin = "arib-std-b67"
    else:
        tin = raw_t

    pin = "bt2020" if p_unknown else raw_p
    min_val = "bt2020nc" if m_unknown else raw_m

    if raw_r in ("pc", "full", "jpeg"):
        rin = "pc"
    else:
        rin = "tv"

    missing = []
    if t_unknown:
        missing.append("transfer")
    if p_unknown:
        missing.append("primaries")
    if m_unknown:
        missing.append("matrix")
    assumed_trc = "HLG (arib-std-b67)" if tin == "arib-std-b67" else "PQ (smpte2084)"
    warn = (
        f"[color] Source color tags ({', '.join(missing)}) not found; "
        f"assuming {assumed_trc} / BT.2020 (tin={tin}:pin={pin}:min={min_val}:rin={rin}).\n"
    )
    return f"zscale=tin={tin}:pin={pin}:min={min_val}:rin={rin}:t=linear:p=bt2020:npl=100", [warn]


def build_vf_chain(s, fmt="yuv420p"):
    """The exact tonemap + scale + brightness filter chain used for the
    real encode. Shared with the instant frame-preview render below so
    the preview is provably the same pipeline, not a lookalike.

    `fmt` is the pixel format the tonemap/format filter converts into -
    pass the selected bit depth's pix_fmt here directly rather than
    building with a hardcoded "yuv420p" and patching the string
    afterwards (the previous approach relied on "format=yuv420p"
    appearing exactly once in the chain, which is easy to silently
    break if the chain is ever edited)."""
    engine, code = tonemap_lookup(s.tonemap_method)
    denoise_filters = {"Light": "hqdn3d=1:1:2:2", "Medium": "hqdn3d=4:3:6:4.5"}
    denoise = denoise_filters.get(s.denoise)
    crop = s.autocrop_value or ""
    if engine == "gpu":
        dovi = f":apply_dolbyvision={int(s.dv_rpu)}"
        peak = f":peak_detect={int(s.peak_detect)}"
        gmode = gamut_mode_lookup(getattr(s, "gamut_mode", "Perceptual"))
        gamut = f":gamut_mode={gmode}" if gmode in ("relative", "clip") else ""
        vf = f"libplacebo=tonemapping={code}:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format={fmt}{dovi}{peak}{gamut}"
    else:
        zscale_in, _ = _cpu_zscale_color_args(s)
        vf = f"{zscale_in},format=gbrpf32le,tonemap=tonemap={code}:desat={s.desat:g},zscale=p=bt709:t=bt709:m=bt709:r=tv,format={fmt}"
    if denoise:
        vf = f"{denoise},{vf}"
    if crop:
        vf = f"{crop},{vf}"
    vf += build_scale_args(s)
    if s.resharpen:
        vf += ",unsharp=5:5:0.8:5:5:0.4"
    if s.deband:
        vf += ",deband=1thr=0.02:2thr=0.02:3thr=0.02:4thr=0.02"
    vf += build_brightness_args(s)
    vf += build_colortemperature_args(s)
    return vf


# ---- MP4-family output: tracks the container can't hold ----------------
MP4_FAMILY = (".mp4", ".mov", ".m4v")
_MP4_AUDIO_COPYABLE = {"aac", "ac3", "eac3", "mp3", "mp2", "alac", "opus", "flac", "dts"}
_TEXT_SUBTITLES = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}


@dataclass(frozen=True)
class TrackPlan:
    skip: tuple = ()                  # ((index, codec_type, codec_name), ...) left out
    subtitle_codec: str = "copy"      # "mov_text" when text subtitles go into an MP4
    sub_overrides: tuple = ()         # ((ordinal, "srt"), ...) per-subtitle codec overrides (e.g. MKV mov_text -> srt)
    sub_override_streams: tuple = ()  # ((stream_index, src_codec), ...) for human-readable log lines

    def describe(self, tr=None):
        """One human-readable line per left-out or converted track, for the log."""
        def _t(source, **kwargs):
            if tr is not None:
                return tr(source, **kwargs)
            return source.format(**kwargs) if kwargs else source

        kind = {"audio": "audio", "subtitle": "subtitle"}
        lines = [
            _t(
                "stream #{index} ({kind}, {codec}) isn't supported in an MP4/MOV container "
                "and was left out - choose MKV to keep it",
                index=i,
                kind=kind.get(t, t),
                codec=c,
            )
            for i, t, c in self.skip
        ]
        if self.sub_override_streams:
            for i, src_codec in self.sub_override_streams:
                lines.append(
                    _t(
                        "subtitle stream #{index} ({codec}) converted to SRT for Matroska",
                        index=i,
                        codec=src_codec,
                    )
                )
        elif self.sub_overrides:
            for ordinal, _dst_codec in self.sub_overrides:
                lines.append(
                    _t(
                        "subtitle stream #{index} ({codec}) converted to SRT for Matroska",
                        index=ordinal,
                        codec="mov_text",
                    )
                )
        return lines


def plan_tracks(s, out_path=None):
    """Decide which audio/subtitle tracks go into `out_path` (default: the
    settings' own output). Pure: depends only on s.src_streams and the
    output file's extension."""
    out = out_path if out_path is not None else s.effective_dst
    if not s.src_streams:
        return TrackPlan()
    ext = Path(str(out)).suffix.lower()
    if ext == ".mkv":
        sub_overrides = []
        sub_override_streams = []
        sub_ordinal = 0
        for index, ctype, codec in sorted(s.src_streams, key=lambda item: item[0]):
            codec_norm = (codec or "").lower()
            if ctype == "subtitle":
                if codec_norm == "mov_text":
                    sub_overrides.append((sub_ordinal, "srt"))
                    sub_override_streams.append((index, codec_norm))
                sub_ordinal += 1
        return TrackPlan(
            skip=(),
            subtitle_codec="copy",
            sub_overrides=tuple(sub_overrides),
            sub_override_streams=tuple(sub_override_streams),
        )
    if ext not in MP4_FAMILY:
        return TrackPlan()
    skip, text_subs = [], False
    for index, ctype, codec in s.src_streams:
        codec = (codec or "").lower()
        if ctype == "audio" and codec not in _MP4_AUDIO_COPYABLE:
            skip.append((index, ctype, codec))
        elif ctype == "subtitle":
            if codec in _TEXT_SUBTITLES:
                text_subs = True
            else:
                skip.append((index, ctype, codec))
    return TrackPlan(tuple(skip), "mov_text" if text_subs else "copy")


def ffmpeg_track_args(s=None, out_path=None):
    out = out_path if out_path is not None else (s.effective_dst if s is not None else "")
    ext = Path(str(out)).suffix.lower() if out else ""
    args = ["-map", "0:a?", "-map", "0:s?"]
    if ext == ".mkv":
        args += ["-map", "0:t?"]
    if s is not None:
        # negative maps must come after the positive ones they subtract from
        for index, _t, _c in plan_tracks(s, out_path).skip:
            args += ["-map", f"-0:{index}"]
    return args


def ffmpeg_track_codec_args(s=None, out_path=None):
    plan = plan_tracks(s, out_path) if s is not None else TrackPlan()
    args = ["-c:a", "copy", "-c:s", plan.subtitle_codec]
    for ordinal, sub_codec in plan.sub_overrides:
        args += [f"-c:s:{ordinal}", sub_codec]
    args += ["-max_interleave_delta", "0"]
    return args


def build_ffmpeg_command(s):
    e, opt = build_encode_args(s)
    fmt = s.pix_fmt
    dst = s.effective_dst
    base = [_ffmpeg_exe(), "-hide_banner", "-y"]
    if s.hwaccel:
        base += ["-hwaccel", "auto"]
    if s.resume_seek_sec > 0:
        base += ["-ss", f"{s.resume_seek_sec:.3f}"]
    base += ["-progress", "pipe:1", "-nostats", "-i", s.src]
    if s.resume_seek_sec > 0:
        base += ["-ss", "0"]
    vf = build_vf_chain(s, fmt)
    container_args = build_container_args(dst, e)
    if not s.preview_path:
        return base + ["-map", "0:v:0", *ffmpeg_track_args(s, dst), "-vf", vf, "-c:v", e, *opt,
                       "-pix_fmt", fmt, *ffmpeg_track_codec_args(s, dst),
                       *container_args, dst]
    main_out = [
        "-map", "[enc]", *ffmpeg_track_args(s, dst), "-c:v", e, *opt, "-pix_fmt", fmt,
        *ffmpeg_track_codec_args(s, dst),
        *container_args, dst,
    ]
    fc = (f"[0:v]{vf}[tm];[tm]split=2[enc][pv];"
          f"[pv]fps=1,format=yuvj420p[pvout]")
    preview_out = ["-map", "[pvout]", "-f", "image2", "-update", "1",
                   "-q:v", "2", "-c:v", "mjpeg", str(s.preview_path)]
    return base + ["-filter_complex", fc] + main_out + preview_out


def resolve_handbrake_encoder(encoder_label, bit_depth_label):
    ids = HB_ENCODER_IDS.get(encoder_label, {})
    e = ids.get(bit_depth_label)
    warnings = []
    if e is None:
        e = ids.get("8-bit (SDR standard)")
        if e is None:
            raise ValueError(
                f"No HandBrake encoder is configured for {encoder_label} at {bit_depth_label}."
            )
        warnings.append(f"[warn] {encoder_label} has no {bit_depth_label} encoder id in this "
                        f"build \u2014 falling back to 8-bit output.\n")
    return e, ids, warnings


def build_handbrake_command(s):
    """Returns (argv, warnings). Warnings are returned rather than logged
    so this stays callable without a window attached."""
    label = s.encoder_label
    depth = s.bit_depth_label
    e, ids, warnings = resolve_handbrake_encoder(label, depth)
    cmd = [s.handbrake_tool, "-i", s.src, "-o", s.effective_dst,
           "-e", e, "-M", "709"]
    if s.quality_mode == "Bitrate":
        cmd += ["-b", str(s.target_bitrate_kbps), "--multi-pass", "--turbo"]
    else:
        cmd += ["-q", str(s.quality)]
    cmd += ["-E", "copy", "--all-audio", "--all-subtitles"]
    if s.denoise != "Off":
        cmd += ["--denoise", s.denoise.lower()]
    if s.resharpen:
        cmd += ["--unsharp=light"]
    cmd += ["--crop-mode", "auto" if s.autocrop else "none"]
    engine, code = tonemap_lookup(s.tonemap_method)
    if engine == "cpu":
        cmd += ["--colorspace", f"transfer=bt709:tonemap={code}:desat=0"]
    target_h = RESOLUTIONS.get(s.resolution_label)
    if target_h:
        cmd += ["-Y", str(even(target_h))]
    if s.hwaccel and e.startswith("nvenc"):
        cmd += ["--enable-hw-decoding", "nvdec"]
    profile = "high10" if e == "x264_10bit" else HB_PROFILE_FOR_DEPTH.get(depth)
    if profile and depth in ids:
        cmd += ["--encoder-profile", profile]
    return cmd, warnings


def build_command(s):
    """Returns (argv, warnings) for whichever backend is selected."""
    if s.using_handbrake:
        return build_handbrake_command(s)
    engine, _code = tonemap_lookup(s.tonemap_method)
    warnings = _cpu_zscale_color_args(s)[1] if engine != "gpu" else []
    return build_ffmpeg_command(s), warnings


def build_test_clip_command(s, out_path, seek, dur):
    if s.using_handbrake:
        cmd, warnings = build_handbrake_command(
            replace(s, dst=str(out_path), resume_output_override=""))
        cmd = _hb_insert_trim(cmd, seek, dur)
        return cmd, warnings
    e, opt = build_encode_args(s)
    fmt = s.pix_fmt
    vf = build_vf_chain(s, fmt)
    engine, _code = tonemap_lookup(s.tonemap_method)
    warnings = _cpu_zscale_color_args(s)[1] if engine != "gpu" else []
    cmd = [_ffmpeg_exe(), "-hide_banner", "-y"]
    if s.hwaccel:
        cmd += ["-hwaccel", "auto"]
    cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += ["-ss", f"{seek:.2f}", "-i", s.src, "-ss", "0", "-t", str(dur),
            "-map", "0:v:0", *ffmpeg_track_args(s, out_path), "-vf", vf,
            "-c:v", e, *opt, "-pix_fmt", fmt, *ffmpeg_track_codec_args(s, out_path),
            *build_container_args(out_path, e), str(out_path)]
    return cmd, warnings


_HB_TEST_CLIP_DROP = {"--two-pass", "--multi-pass", "--turbo"}


def _hb_insert_trim(cmd, seek, dur):
    out = [a for a in cmd if a not in _HB_TEST_CLIP_DROP]
    return out + ["--start-at", f"duration:{seek:.1f}", "--stop-at", f"duration:{dur}"]


# ====================== END PURE CORE - command building ==============
