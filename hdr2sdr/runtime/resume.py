"""Resume-segment planning and concat command construction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from hdr2sdr.core.destination import DestinationPlan


@dataclass(frozen=True)
class ResumeSegment:
    """Paths and timing for the next segment in a resumed conversion."""

    kept_part: Path
    next_output: Path
    seek_seconds: float
    progress_offset: float
    outpoint_seconds: float
    moved_from: Path | None


@dataclass(frozen=True)
class OrphanAssessment:
    path: Path
    duration: float
    threshold: float
    same_settings: bool
    same_source: bool

    @property
    def matches(self) -> bool:
        return self.same_settings and self.same_source


@dataclass(frozen=True)
class ConcatResult:
    succeeded: bool
    stopped: bool
    destination: Path
    parts: tuple[Path, ...]
    publish_error: bool = False


class ResumeAction(str, Enum):
    UNAVAILABLE = "unavailable"
    SOFTWARE_DECODE = "software_decode"
    DECODE_FAILED_TOO_EARLY = "decode_failed_too_early"
    TOO_SHORT = "too_short"
    AUTOMATIC = "automatic"
    ASK = "ask"
    ORPHAN_TOO_SHORT = "orphan_too_short"
    ORPHAN_START_OVER = "orphan_start_over"
    ORPHAN_AUTO_RESUME = "orphan_auto_resume"
    ORPHAN_ASK = "orphan_ask"


class ResumeCoordinator:
    """Own resume-chain bookkeeping and operations that do not need Qt."""

    SAFETY_MARGIN_SECONDS = 4.0

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.parts: list[Path] = []
        self.outpoints: list[float] = []
        self.final_destination: Path | None = None
        self.output_override: Path | None = None
        self.seek_seconds = 0.0
        self.progress_offset = 0.0
        self.concat_phase = False
        self.concat_list_path: Path | None = None

    def chain_metadata(self) -> dict[str, Any] | None:
        """Serialize the active multi-segment chain for crash-safe sidecars."""
        if not self.parts:
            return None
        return {
            "version": 1,
            "final_destination": str(self.final_destination) if self.final_destination else "",
            "parts": [str(part) for part in self.parts],
            "outpoints": [float(point) for point in self.outpoints],
            "seek_seconds": float(self.seek_seconds),
            "progress_offset": float(self.progress_offset),
            "active_output": str(self.output_override) if self.output_override else "",
        }

    @staticmethod
    def has_chain_metadata(metadata: dict[str, Any] | None) -> bool:
        return isinstance(metadata, dict) and isinstance(metadata.get("chain"), dict)

    @staticmethod
    def load_chain_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        """Validate and normalize a persisted chain manifest from a sidecar."""
        if not ResumeCoordinator.has_chain_metadata(metadata):
            return None
        raw = metadata["chain"]
        raw_parts = raw.get("parts")
        if not isinstance(raw_parts, list) or not raw_parts:
            return None
        parts: list[Path] = []
        for item in raw_parts:
            if not isinstance(item, str) or not item:
                return None
            part_path = Path(item)
            if not part_path.is_file():
                return None
            parts.append(part_path)
        raw_outpoints = raw.get("outpoints")
        if not isinstance(raw_outpoints, list) or len(raw_outpoints) != len(parts):
            return None
        try:
            outpoints = [max(0.0, float(point)) for point in raw_outpoints]
            seek_seconds = max(0.0, float(raw.get("seek_seconds", 0.0) or 0.0))
            progress_offset = max(0.0, float(raw.get("progress_offset", seek_seconds) or 0.0))
        except (TypeError, ValueError):
            return None
        final_dst = raw.get("final_destination")
        active_out = raw.get("active_output")
        return {
            "final_destination": Path(final_dst) if isinstance(final_dst, str) and final_dst else None,
            "parts": parts,
            "outpoints": outpoints,
            "seek_seconds": seek_seconds,
            "progress_offset": progress_offset,
            "active_output": Path(active_out) if isinstance(active_out, str) and active_out else None,
        }

    def restore_chain(self, chain: dict[str, Any],
                      final_destination: Path | str | None = None) -> None:
        """Restore coordinator state from a validated chain manifest."""
        destination = (Path(final_destination) if final_destination is not None
                       else chain.get("final_destination"))
        self.final_destination = destination
        self.parts = [Path(p) for p in chain["parts"]]
        self.outpoints = [float(x) for x in chain["outpoints"]]
        self.seek_seconds = float(chain["seek_seconds"])
        self.progress_offset = float(chain["progress_offset"])
        active_output = chain.get("active_output")
        if active_output is not None:
            self.output_override = Path(active_output)
        elif destination is not None:
            self.output_override = DestinationPlan(destination).resume_part(len(self.parts) + 1)
        else:
            self.output_override = None

    def begin_fresh_run(self, destination) -> Path:
        """Discard files from an abandoned chain and prepare the initial part."""
        stale_paths = [*self.parts]
        if self.output_override is not None:
            stale_paths.append(self.output_override)
        for stale_path in stale_paths:
            try:
                Path(stale_path).unlink(missing_ok=True)
            except OSError:
                pass
        self.reset()
        self.output_override = DestinationPlan(destination).part
        return self.output_override

    @staticmethod
    def minimum_useful_duration(total_duration: float | None) -> float:
        return min(120.0, max(20.0, (total_duration or 0.0) * 0.03))

    @staticmethod
    def assess_orphan(path, duration, total_duration, metadata,
                      settings_signature, source_fingerprint) -> OrphanAssessment:
        threshold = ResumeCoordinator.minimum_useful_duration(total_duration)
        metadata = metadata if isinstance(metadata, dict) else {}
        return OrphanAssessment(
            Path(path), float(duration or 0.0), threshold,
            bool(metadata) and metadata.get("settings") == settings_signature,
            bool(metadata) and metadata.get("source") == source_fingerprint,
        )

    @staticmethod
    def orphan_action(assessment: OrphanAssessment, unattended: bool) -> ResumeAction:
        if assessment.duration < assessment.threshold:
            return ResumeAction.ORPHAN_TOO_SHORT
        if unattended:
            return (ResumeAction.ORPHAN_AUTO_RESUME if assessment.matches
                    else ResumeAction.ORPHAN_START_OVER)
        return ResumeAction.ORPHAN_ASK

    @staticmethod
    def failure_action(*, backend_available: bool, has_specific_hint: bool,
                       hardware_decode_failed: bool, enough_progress: bool,
                       partial_exists: bool, unattended: bool) -> ResumeAction:
        """Choose whether a failed FFmpeg run can or should be resumed."""
        if not backend_available or has_specific_hint:
            return ResumeAction.UNAVAILABLE
        if hardware_decode_failed:
            if enough_progress and partial_exists:
                return ResumeAction.SOFTWARE_DECODE
            return ResumeAction.DECODE_FAILED_TOO_EARLY
        if not enough_progress or not partial_exists:
            return ResumeAction.TOO_SHORT
        return ResumeAction.AUTOMATIC if unattended else ResumeAction.ASK

    def prepare_segment(self, active_path, final_destination, part_duration,
                        current_progress, progress_offset=0.0) -> ResumeSegment:
        """Preserve the completed segment and calculate a safe next seek point."""
        active_path = Path(active_path)
        final_destination = Path(final_destination)
        plan = DestinationPlan(final_destination)
        number = len(self.parts) + 1
        kept_part = plan.resume_part(number)
        moved_from = None
        if active_path != kept_part:
            if kept_part.exists():
                kept_part.unlink()
            active_path.replace(kept_part)
            moved_from = active_path

        base_offset = float(progress_offset or 0.0)
        if part_duration is None:
            segment_duration = max(0.0, float(current_progress or 0.0) - base_offset)
        else:
            segment_duration = max(0.0, float(part_duration))
        next_seek = max(0.0, base_offset + segment_duration - self.SAFETY_MARGIN_SECONDS)
        next_output = plan.resume_part(number + 1)

        self.final_destination = final_destination
        self.parts.append(kept_part)
        self.outpoints.append(max(0.0, next_seek - base_offset))
        self.output_override = next_output
        self.seek_seconds = next_seek
        self.progress_offset = next_seek
        return ResumeSegment(kept_part, next_output, next_seek, next_seek,
                             self.outpoints[-1], moved_from)

    @staticmethod
    def write_concat_list(parts, destination, outpoints=None) -> Path:
        """Write an FFmpeg concat-demuxer list, including overlap cut points."""
        destination = Path(destination)
        list_path = DestinationPlan.concat_list_for(destination)
        outpoints = list(outpoints or [])
        with list_path.open("w", encoding="utf-8") as stream:
            for index, part in enumerate(parts):
                escaped = str(Path(part).resolve()).replace("'", r"'\''")
                stream.write(f"file '{escaped}'\n")
                if (index < len(outpoints) and index < len(parts) - 1
                        and outpoints[index] is not None):
                    stream.write(f"outpoint {max(0.0, outpoints[index]):.3f}\n")
        return list_path

    @staticmethod
    def concat_command(parts, destination, ffmpeg, container_args, outpoints=None):
        destination = Path(destination)
        list_path = ResumeCoordinator.write_concat_list(parts, destination, outpoints)
        command = [ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
                   "-i", str(list_path), "-map", "0", "-c", "copy",
                   *container_args(str(destination)), str(destination)]
        return command, list_path

    @staticmethod
    def concat_succeeded(was_stopping, exit_code, normal_exit) -> bool:
        return not was_stopping and exit_code == 0 and bool(normal_exit)

    def finalize_concat(self, was_stopping, exit_code, normal_exit, temporary,
                        destination, publish: Callable, discard: Callable) -> ConcatResult:
        """Publish a successful join and remove segments only after publication."""
        destination = Path(destination)
        temporary = Path(temporary)
        parts = tuple(self.parts)
        process_ok = self.concat_succeeded(was_stopping, exit_code, normal_exit)
        publication = publish(temporary, destination) if process_ok else False
        # Accept bool callbacks used by callers/tests and the error-or-None
        # contract returned by UnfinishedStore.publish.
        published = (publication if isinstance(publication, bool)
                     else publication is None) if process_ok else False
        succeeded = process_ok and published
        if not succeeded:
            discard(temporary)
        else:
            for part in parts:
                for path in (part, DestinationPlan.sidecar_for(part)):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
        return ConcatResult(succeeded, bool(was_stopping), destination, parts,
                            publish_error=process_ok and not published)
