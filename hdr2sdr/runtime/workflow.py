"""UI-independent decisions for starting and finishing conversion runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from hdr2sdr.core.dolby_vision import profile5_rpu_supported


class FinishAction(str, Enum):
    STOPPED = "stopped"
    FAILED = "failed"
    RESUME = "resume"
    CONCAT = "concat"
    PUBLISH = "publish"
    DONE = "done"


class StartAction(str, Enum):
    WAIT_PROCESS = "wait_process"
    WAIT_SETUP = "wait_setup"
    MISSING_BACKEND = "missing_backend"
    MISSING_FILES = "missing_files"
    ANALYZE_SOURCE = "analyze_source"
    SKIP_SDR = "skip_sdr"
    CONTINUE = "continue"


class DolbyVisionAction(str, Enum):
    CONTINUE = "continue"
    BLOCK_PROFILE5 = "block_profile5"
    ENABLE_PROFILE5_RPU = "enable_profile5_rpu"
    WARN_QUEUE_BASE_LAYER = "warn_queue_base_layer"
    CONFIRM_BASE_LAYER = "confirm_base_layer"


class RunWorkflow:
    """Decide run workflow steps; callers perform the resulting UI effects."""

    @staticmethod
    def start_blocker(process_busy: bool, setup_busy: bool) -> StartAction | None:
        if process_busy:
            return StartAction.WAIT_PROCESS
        if setup_busy:
            return StartAction.WAIT_SETUP
        return None

    @staticmethod
    def input_action(handbrake_selected: bool, handbrake_available: bool,
                     source_exists: bool, destination_selected: bool) -> StartAction:
        if handbrake_selected and not handbrake_available:
            return StartAction.MISSING_BACKEND
        if not source_exists or not destination_selected:
            return StartAction.MISSING_FILES
        return StartAction.CONTINUE

    @staticmethod
    def source_action(kind: str) -> StartAction:
        if kind == "unknown":
            return StartAction.ANALYZE_SOURCE
        if kind == "sdr":
            return StartAction.SKIP_SDR
        return StartAction.CONTINUE

    @staticmethod
    def dolby_vision_action(profile, kind, *, using_handbrake, tonemap_engine,
                            has_libplacebo, unattended) -> DolbyVisionAction:
        if profile == 5:
            if not profile5_rpu_supported(
                    profile, using_handbrake, tonemap_engine, has_libplacebo):
                return DolbyVisionAction.BLOCK_PROFILE5
            return DolbyVisionAction.ENABLE_PROFILE5_RPU
        if kind == "dolby":
            return (DolbyVisionAction.WARN_QUEUE_BASE_LAYER if unattended
                    else DolbyVisionAction.CONFIRM_BASE_LAYER)
        return DolbyVisionAction.CONTINUE

    @staticmethod
    def start_action(start_result: str, process_running: bool, queue_running: bool) -> str:
        """Classify a start attempt for the Start handler's follow-up work."""
        if start_result == "busy":
            return "wait"
        if queue_running and not process_running:
            return "skip_queue_item"
        return "none"

    @staticmethod
    def finish_action(process_succeeded: bool, stopped: bool,
                      resume_segment: bool, part_file: bool,
                      part_published: bool | None = None) -> FinishAction:
        """Choose the next step after process exit and optional part publish.

        ``part_published=None`` means publication still needs to be attempted.
        A failed publication follows the same resume-or-fail path as an encoder
        failure, preserving the existing opportunity to resume a usable part.
        """
        if stopped:
            return FinishAction.STOPPED
        if not process_succeeded:
            return FinishAction.RESUME
        if resume_segment:
            return FinishAction.CONCAT
        if part_file and part_published is None:
            return FinishAction.PUBLISH
        if part_file and not part_published:
            return FinishAction.RESUME
        return FinishAction.DONE

    @staticmethod
    def after_resume_offer(stopped: bool, resume_started: bool) -> FinishAction:
        if resume_started:
            return FinishAction.RESUME
        return FinishAction.STOPPED if stopped else FinishAction.FAILED

    @staticmethod
    def concat_action(succeeded: bool, stopped: bool) -> FinishAction:
        if succeeded:
            return FinishAction.DONE
        return FinishAction.STOPPED if stopped else FinishAction.FAILED


@dataclass
class RunContext:
    """Source, destination, kind, and settings snapshot for the active run."""

    source: Path | None = None
    destination: Path | None = None
    kind: str | None = None
    settings: dict = field(default_factory=dict)

    def capture(self, source, destination, kind, settings) -> None:
        self.source = Path(source)
        self.destination = Path(destination)
        self.kind = kind
        snapshot = dict(settings)
        for transient in ("src", "dst", "preview_path", "resume_seek_sec",
                          "resume_output_override", "handbrake_tool"):
            snapshot.pop(transient, None)
        self.settings = snapshot
