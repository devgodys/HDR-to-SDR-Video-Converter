from __future__ import annotations

import re
from dataclasses import dataclass

from hdr2sdr.core.constants import (
    PROGRESS_KEYS, _GPU_TONEMAP_VRAM_HINTS, _HB_PROGRESS, _HW_DECODE_RUNTIME_HINTS, _HW_DECODE_UNAVAILABLE_HINTS, _HW_ENCODER_UNAVAILABLE_HINTS, _MP4_INCOMPATIBLE_TRACK_HINTS,
)
from hdr2sdr.core.utils import parse_num


# ======================================================================
# PURE CORE - process output parsing
# ======================================================================
# Same rule as above: no `self`, no widgets, no Qt.
#
# This existed in three places before, with small differences each time:
# the real conversion (_handle_line), the test clip
# (_handle_test_clip_line), and a resume segment - which is the real
# conversion again, but with a source-time offset applied, because a
# resumed ffmpeg run seeks with -ss and therefore restarts its own
# out_time at zero. The differences were real, so they are parameters
# here (duration, offset), not variants. What was genuinely duplicated -
# the key=value absorption, the \r/\n line splitting, the error-hint
# substring scan - is now written once.
# ======================================================================

@dataclass(frozen=True)
class ProgressStats:
    """One decoded progress update. Every field is optional because both
    backends omit fields early in a run, before they have anything to
    report."""

    pct: float | None = None
    speed_x: float | None = None
    mbps: float | None = None
    fps: float | None = None
    eta_sec: float | None = None
    out_sec: float | None = None


class LineAssembler:
    """Splits a byte-stream-turned-text into complete lines.

    Both tools write progress with carriage returns rather than newlines,
    and a read can land mid-line, so the trailing fragment has to be held
    back until the rest of it arrives. Getting this wrong shows up as a
    progress bar that stutters or a log line cut in half.
    """

    def __init__(self):
        self._buf = ""

    def feed(self, text):
        self._buf += text
        parts = re.split(r"[\r\n]", self._buf)
        self._buf = parts.pop()  # keep the trailing incomplete line buffered
        return [p for p in parts if p]

    def flush(self):
        """Return any trailing partial line left in the buffer and clear it."""
        if not self._buf:
            return []
        parts = re.split(r"[\r\n]", self._buf)
        self._buf = ""
        return [p for p in parts if p]

    def reset(self):
        self._buf = ""


class ErrorHintScanner:
    """Watches the log for the handful of failures that are worth turning
    into a specific, actionable message at the end of a run instead of
    letting the user hunt for them in the raw output.

    Each flag latches on first sight: the message is shown once when the
    process finishes, and a failure that repeats every frame should not
    re-trigger anything.
    """

    def __init__(self, using_handbrake=False, encoder_label=None, hwaccel_decode_on=False):
        self.using_handbrake = using_handbrake
        self.encoder_label = encoder_label
        self.hwaccel_decode_on = hwaccel_decode_on
        self.mp4_track = False
        self.hw_encoder = False
        self.hw_decode = False
        self.hw_decode_runtime = False
        self.gpu_tonemap = False

    def configure(self, encoder_label, hwaccel_decode_on):
        """`encoder_label`/`hwaccel_decode_on` are re-read on every line
        rather than frozen at construction, because they can change after
        the scanner is created: _start_resume_segment() re-reads the
        encoder combo and hwaccel checkbox for the new segment without
        recreating the scanner."""
        self.encoder_label = encoder_label
        self.hwaccel_decode_on = hwaccel_decode_on

    def feed(self, line):
        low = line.lower()
        # Container/track-copy failures are FFmpeg-specific (HandBrakeCLI
        # transcodes/falls back instead of erroring the same way), so keep
        # that check FFmpeg-only. The HW encode/decode hints below come from
        # the underlying vendor libraries (NVENC/NVDEC/AMF/VideoToolbox)
        # that both backends call into, so check those regardless of backend.
        if not self.using_handbrake and not self.mp4_track:
            if any(hint in low for hint in _MP4_INCOMPATIBLE_TRACK_HINTS):
                self.mp4_track = True
        if (not self.hw_encoder
                and self.encoder_label
                and self.encoder_label.split(None, 1)[0] in ("NVIDIA", "AMD", "Intel", "Apple")
                and any(hint in low for hint in _HW_ENCODER_UNAVAILABLE_HINTS)):
            self.hw_encoder = True
        if (not self.hw_decode
                and self.hwaccel_decode_on
                and any(hint in low for hint in _HW_DECODE_UNAVAILABLE_HINTS)):
            self.hw_decode = True
        if (not self.hw_decode_runtime
                and self.hwaccel_decode_on
                and any(hint in low for hint in _HW_DECODE_RUNTIME_HINTS)):
            self.hw_decode_runtime = True
        self.feed_gpu_tonemap_only(line)

    def feed_gpu_tonemap_only(self, line):
        """The subset a test-clip render checks.

        A test clip runs the same filter chain, so it can hit the same
        libplacebo VRAM failure - but it is not the real conversion, and
        letting it latch the hardware encoder/decoder or MP4 track flags
        would make the *next* real run report a problem that belongs to
        the clip. Kept deliberately narrow for that reason.
        """
        if not self.gpu_tonemap:
            low = line.lower()
            if any(hint in low for hint in _GPU_TONEMAP_VRAM_HINTS):
                self.gpu_tonemap = True

    def reset(self):
        self.mp4_track = False
        self.hw_encoder = False
        self.hw_decode = False
        self.hw_decode_runtime = False
        self.gpu_tonemap = False


class FfmpegProgressParser:
    """Decodes ffmpeg's "-progress pipe:1" key=value stream.

    `duration` is the source length in seconds; `offset` shifts the result
    back into absolute source time for a resumed segment, whose own
    out_time restarts at zero because of the -ss before -i. Both default
    to the plain non-resumed case.
    """

    def __init__(self, duration=0.0, offset=0.0):
        self.duration = duration
        self.offset = offset
        self._block = {}

    def configure(self, duration, offset=0.0):
        """Both values are re-read on every block rather than frozen at
        construction: duration can arrive late (a source analysed while
        the run is already going) and offset changes when a resume
        segment starts."""
        self.duration = duration
        self.offset = offset

    def feed(self, line):
        """Returns (stats, consumed).

        consumed is False when the line was not part of a progress block
        at all - the caller writes those to the activity log. stats is
        None until a block completes.
        """
        key = line.split("=", 1)[0] if "=" in line else ""
        if key not in PROGRESS_KEYS and not key.startswith("stream_"):
            return None, False
        self._block[key] = line.split("=", 1)[1] if "=" in line else ""
        if key != "progress":
            return None, True
        stats = self._block_to_stats(self._block)
        self._block = {}
        return stats, True

    def _block_to_stats(self, block):
        out_ms = parse_num(block.get("out_time_ms", ""))
        # FFmpeg uses INT64_MIN as an "unknown" timestamp in early progress
        # blocks. Treat it as missing rather than producing a huge negative
        # percentage/ETA or moving resume bookkeeping backwards.
        if out_ms is not None and out_ms < 0:
            out_ms = None
        # out_time_ms is relative to *this* ffmpeg process's own input
        # timeline, which restarts at 0 whenever a resume segment applies
        # "-ss" before "-i". `offset` (0.0 for a normal, non-resumed run)
        # shifts it back to an absolute position in the source so the
        # progress bar/ETA/current_out_sec stay correct across a resume
        # instead of restarting from 0%.
        out_sec = out_ms / 1e6 + self.offset if out_ms is not None else None
        pct = None
        if self.duration and out_sec is not None:
            pct = max(0.0, min(99.9, out_sec / self.duration * 100))
        speed_x = parse_num(block.get("speed", ""))
        kbps = parse_num(block.get("bitrate", ""))
        mbps = kbps / 8000 if kbps is not None else None  # kbit/s -> MB/s
        eta_sec = None
        if pct is not None and speed_x and self.duration:
            eta_sec = self.duration * (100 - pct) / 100 / speed_x
        return ProgressStats(pct=pct, speed_x=speed_x, mbps=mbps,
                             fps=parse_num(block.get("fps", "")),
                             eta_sec=eta_sec, out_sec=out_sec)

    def reset(self):
        self._block = {}


class HandbrakeProgressParser:
    """Decodes HandBrakeCLI's human-readable "Encoding: ... 45.23 % (...)"
    line. Unlike ffmpeg there is no machine-readable feed to ask for, so
    this is a regex over the same text the user sees.

    HandBrake reports a percentage rather than a position, so out_sec is
    derived from `duration` instead of the other way round - which also
    means a run with no known duration simply has no out_sec, rather than
    a wrong one.
    """

    def __init__(self, duration=0.0):
        self.duration = duration

    def configure(self, duration, offset=0.0):
        """`offset` is accepted and ignored, so the two parsers stay
        interchangeable. It is meaningless here: HandBrake reports a
        percentage of the whole job, which is already absolute, whereas
        ffmpeg reports a position in its own -ss-shifted timeline. Resume
        is FFmpeg-only for that reason among others."""
        self.duration = duration

    def feed(self, line):
        """Returns (stats, consumed) with the same contract as
        FfmpegProgressParser.feed()."""
        m = _HB_PROGRESS.search(line)
        if not m:
            return None, False
        pct = float(m.group(3))
        # Bitrate mode runs "task 1 of 2" then "task 2 of 2", each 0-100 %: fold them into one
        # monotonic overall percentage instead of letting the bar run 0->100->0->100.
        if m.group(1) and m.group(2) and int(m.group(2)) > 1:
            task, tasks = int(m.group(1)), int(m.group(2))
            pct = ((task - 1) + pct / 100.0) / tasks * 100.0
        pct = min(99.9, pct)
        fps = parse_num(m.group(4))
        eta_sec = (int(m.group(6)) * 3600 + int(m.group(7)) * 60 + int(m.group(8))
                   if m.group(6) else None)
        out_sec = self.duration * pct / 100 if self.duration else None
        return ProgressStats(pct=pct, speed_x=None, mbps=None, fps=fps,
                             eta_sec=eta_sec, out_sec=out_sec), True

    def reset(self):
        pass


def make_progress_parser(using_handbrake, duration=0.0, offset=0.0):
    if using_handbrake:
        return HandbrakeProgressParser(duration=duration)
    return FfmpegProgressParser(duration=duration, offset=offset)


# ==================== END PURE CORE - process output parsing ==========



def _extract_stat_parts(
    *,
    speed_x: float | None = None,
    fps: float | None = None,
    mbps: float | None = None,
    eta_text: str | None = None,
    raw_stats_text: str = "",
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (x_part, fps_part, mbps_part, eta_part) from structured values or raw stats string."""
    x_part = f"{speed_x:.2f}x realtime" if speed_x is not None else None
    fps_part = f"{fps:.0f} fps" if fps is not None else None
    mbps_part = f"{mbps:.1f} MB/s" if mbps is not None else None
    eta_part = eta_text if eta_text else None

    if raw_stats_text:
        for token in (p.strip() for p in raw_stats_text.split("·") if p.strip()):
            low = token.lower()
            if "realtime" in low and x_part is None:
                x_part = token
            elif low.endswith("fps") and fps_part is None:
                fps_part = token
            elif "mb/s" in low and mbps_part is None:
                mbps_part = token
            elif low.startswith("eta") and eta_part is None:
                eta_part = token
    return x_part, fps_part, mbps_part, eta_part


def build_progress_stat_candidates(
    base_status: str,
    *,
    speed_x: float | None = None,
    fps: float | None = None,
    mbps: float | None = None,
    eta_text: str | None = None,
    raw_stats_text: str = "",
) -> list[str]:
    """Build candidate status strings in P0-4 drop priority order:
    Full (all parts) -> drop MB/s -> drop x realtime (keep fps + ETA) -> ETA only -> base_status.
    """
    base = (base_status or "").strip()
    x_part, fps_part, mbps_part, eta_part = _extract_stat_parts(
        speed_x=speed_x,
        fps=fps,
        mbps=mbps,
        eta_text=eta_text,
        raw_stats_text=raw_stats_text,
    )
    if not any((x_part, fps_part, mbps_part, eta_part)):
        return [base]

    tiers = [
        [p for p in (x_part, fps_part, mbps_part, eta_part) if p],  # 0: full
        [p for p in (x_part, fps_part, eta_part) if p],             # 1: drop MB/s
        [p for p in (fps_part, eta_part) if p],                     # 2: drop x realtime (keep fps + ETA)
        [p for p in (eta_part,) if p],                              # 3: ETA only
        [],                                                         # 4: base status only
    ]
    candidates: list[str] = []
    for parts in tiers:
        if base and parts:
            s = " · ".join([base, *parts])
        elif parts:
            s = " · ".join(parts)
        else:
            s = base
        if s not in candidates:
            candidates.append(s)
    return candidates


def select_progress_status_text(
    base_status: str,
    *,
    speed_x: float | None = None,
    fps: float | None = None,
    mbps: float | None = None,
    eta_text: str | None = None,
    raw_stats_text: str = "",
    max_width: int | None = None,
    measure_fn=None,
) -> tuple[str, str]:
    """Select the richest status string that fits within ``max_width`` according to
    ``measure_fn`` (defaulting to ``len``), and return ``(display_text, full_tooltip_text)``.
    """
    candidates = build_progress_stat_candidates(
        base_status,
        speed_x=speed_x,
        fps=fps,
        mbps=mbps,
        eta_text=eta_text,
        raw_stats_text=raw_stats_text,
    )
    full_text = candidates[0] if candidates else (base_status or "")
    if max_width is None or max_width <= 0:
        return full_text, full_text
    measure = measure_fn if callable(measure_fn) else len
    for cand in candidates:
        if measure(cand) <= max_width:
            return cand, full_text
    return candidates[-1], full_text
