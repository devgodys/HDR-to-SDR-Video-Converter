"""Cached one-frame checks for FFmpeg features that need a working device."""

from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from hdr2sdr.core.commands import build_encode_args

_CACHE: dict[tuple, tuple[bool, str]] = {}
_LOCK = threading.Lock()
_HARDWARE_ENCODER_SUFFIXES = ("_nvenc", "_amf", "_qsv", "_videotoolbox")
_BACKTRACE_FRAME_RE = re.compile(r"\[libplacebo\b[^\]]*\]\s+#\d+\s+0x[0-9a-fA-F]+")


@dataclass(frozen=True)
class StartCapabilityIssue:
    title: str
    message: str


def _strip_libplacebo_backtrace(stderr: str) -> str:
    """Remove raw C stack trace frames from libplacebo stderr while preserving diagnostic lines."""
    if not stderr:
        return ""
    cleaned: list[str] = []
    for line in stderr.splitlines():
        stripped = line.strip()
        if "[libplacebo" in stripped and stripped.endswith("Backtrace:"):
            continue
        if _BACKTRACE_FRAME_RE.search(stripped):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def check_start_capabilities(settings, *, ffmpeg, tonemap_engine, tonemap_code,
                             has_libplacebo, has_st2094, encoders,
                             libplacebo_probe=None, encoder_probe=None):
    """Return the first actionable FFmpeg start issue, or ``None`` if ready."""
    libplacebo_probe = libplacebo_probe or probe_libplacebo
    encoder_probe = encoder_probe or probe_encoder
    if tonemap_engine == "gpu" and not has_libplacebo:
        return StartCapabilityIssue(
            "GPU tonemap unavailable",
            "This FFmpeg lacks libplacebo/Vulkan support. Use a CPU tonemap curve "
            "or install a libplacebo-enabled FFmpeg build.")
    if tonemap_engine == "gpu":
        ok, detail = libplacebo_probe(ffmpeg, timeout=8)
        if not ok:
            detail = _strip_libplacebo_backtrace(detail)
            message = ("FFmpeg lists libplacebo, but a one-frame GPU check failed. "
                       "Check the Vulkan driver or select a CPU curve.")
            if detail:
                message += f"\n\n{detail}"
            return StartCapabilityIssue("GPU tonemap could not start", message)
    if tonemap_code in ("st2094-40", "st2094-10") and not has_st2094:
        return StartCapabilityIssue(
            "Curve unavailable",
            f"This libplacebo build doesn't support {tonemap_code} (needs a newer FFmpeg/libplacebo). "
            "Try BT.2390 or BT.2446A instead.")
    encoder, _args = build_encode_args(settings)
    if encoder not in encoders:
        return StartCapabilityIssue(
            "Encoder unavailable",
            f"{encoder} is not available in this FFmpeg build or GPU driver.")
    if is_hardware_encoder(encoder):
        ok, detail = encoder_probe(
            ffmpeg, encoder, pix_fmt=settings.pix_fmt, timeout=8)
        if not ok:
            message = (f"{encoder} is listed by FFmpeg but could not encode a test frame. "
                       "Check the GPU driver or select another encoder.")
            if detail:
                message += f"\n\n{detail}"
            return StartCapabilityIssue("Hardware encoder unavailable", message)
    return None


def is_hardware_encoder(encoder: str) -> bool:
    return encoder.endswith(_HARDWARE_ENCODER_SUFFIXES)


def _binary_identity(ffmpeg) -> tuple:
    path = os.path.normcase(os.path.abspath(str(ffmpeg)))
    try:
        stat = Path(path).stat()
        return path, stat.st_mtime_ns, stat.st_size
    except OSError:
        return path, None, None


def _probe(ffmpeg, probe_key, arguments, timeout=12) -> tuple[bool, str]:
    key = (*_binary_identity(ffmpeg), probe_key)
    with _LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached

    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", *arguments]
    run_kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    }
    try:
        result = subprocess.run(command, **run_kwargs)
        stderr_clean = _strip_libplacebo_backtrace((result.stderr or "").strip())
        if (result.returncode != 0
                and isinstance(probe_key, tuple)
                and probe_key[:1] == ("libplacebo",)
                and "vk_error_out_of_device_memory" in stderr_clean.lower()):
            # Windows WDDM/Vulkan may take a moment to reclaim BAR1 device memory
            # after a background 4K preview process is killed; retry once briefly.
            import time
            time.sleep(0.15)
            result = subprocess.run(command, **run_kwargs)
            stderr_clean = _strip_libplacebo_backtrace((result.stderr or "").strip())
        outcome = result.returncode == 0, stderr_clean
    except (OSError, subprocess.SubprocessError) as error:
        outcome = False, str(error)

    with _LOCK:
        _CACHE[key] = outcome
    return outcome


def probe_encoder(ffmpeg, encoder, pix_fmt="yuv420p", timeout=12) -> tuple[bool, str]:
    """Check that an encoder can open and emit one synthetic frame."""
    args = [
        "-f", "lavfi", "-i", "color=c=black:s=256x256:r=1:d=1",
        "-frames:v", "1", "-an", "-pix_fmt", str(pix_fmt),
        "-c:v", str(encoder), "-f", "null", "-",
    ]
    return _probe(ffmpeg, ("encoder", encoder, pix_fmt), args, timeout)


def probe_libplacebo(ffmpeg, timeout=12) -> tuple[bool, str]:
    """Check that Vulkan/libplacebo actually processes a frame on this device."""
    args = [
        "-f", "lavfi", "-i", "color=c=gray:s=128x128:r=1:d=1,format=yuv420p10le",
        "-vf", ("libplacebo=tonemapping=bt.2390:colorspace=bt709:"
                "color_primaries=bt709:color_trc=bt709:range=tv:"
                "format=yuv420p:apply_dolbyvision=0:peak_detect=0"),
        "-frames:v", "1", "-an", "-f", "null", "-",
    ]
    return _probe(ffmpeg, ("libplacebo", "bt.2390"), args, timeout)


def invalidate_transient_libplacebo_cache() -> None:
    """Drop cached libplacebo probe failures caused by transient GPU/BAR1 memory exhaustion."""
    with _LOCK:
        to_remove = [
            k for k, (ok, detail) in _CACHE.items()
            if not ok and len(k) >= 4 and isinstance(k[3], tuple) and k[3][:1] == ("libplacebo",)
            and ("vk_error_out_of_device_memory" in (detail or "").lower()
                 or "out of device memory" in (detail or "").lower()
                 or "failed initializing plane texture" in (detail or "").lower()
                 or "no slab to serve request" in (detail or "").lower())
        ]
        for k in to_remove:
            _CACHE.pop(k, None)


def clear_probe_cache() -> None:
    """Clear cached device checks (primarily useful after changing drivers)."""
    with _LOCK:
        _CACHE.clear()
