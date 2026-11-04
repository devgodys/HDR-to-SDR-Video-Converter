"""Queue state and path decisions for conversion batches."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QueueAdvance:
    """One workflow decision for advancing a batch queue."""

    action: str
    path: str | None = None
    was_running: bool = False


@dataclass(frozen=True)
class QueueCompletion:
    """Batch-level action after one conversion reaches a terminal state."""

    action: str


class QueueController:
    """Own the ordered queue and batch flags without depending on Qt widgets."""

    def __init__(self):
        self.paths: list[str] = []
        self.running = False
        self.batch_clean = True

    def add(self, path) -> bool:
        path = str(Path(path))
        if path in self.paths:
            return False
        self.paths.append(path)
        return True

    @staticmethod
    def accepted_files(paths, allowed_extensions, trust_extension=False) -> list[str]:
        """Filter candidate inputs for drag/drop or an explicit file-picker choice."""
        extensions = {str(ext).lower() for ext in allowed_extensions}
        return [str(Path(path)) for path in paths
                if Path(path).is_file()
                and (trust_extension or Path(path).suffix.lower() in extensions)]

    def remove_at(self, index: int) -> str | None:
        if index < 0 or index >= len(self.paths):
            return None
        return self.paths.pop(index)

    def take_next(self) -> str | None:
        return self.remove_at(0)

    def advance(self) -> QueueAdvance:
        """Consume the next path and decide whether to start, skip, or finish.

        UI effects (widgets, timers, logs and notifications) remain the
        responsibility of the caller.
        """
        if not self.running or not self.paths:
            was_running = self.running
            self.running = False
            return QueueAdvance("finished", was_running=was_running)

        path = self.take_next()
        if self.is_missing(path):
            self.batch_clean = False
            return QueueAdvance("skipped", path=path)
        return QueueAdvance("start", path=path)

    def take_selected(self, index: int) -> str | None:
        """Remove one selected item without enabling batch auto-advance."""
        self.running = False
        return self.remove_at(index)

    def complete_item(self, succeeded: bool, stopped: bool) -> QueueCompletion:
        """Choose the next workflow step after an individual run completes."""
        if self.running:
            if succeeded:
                return QueueCompletion("advance")
            self.running = False
            return QueueCompletion("stopped" if stopped else "failed")
        if not stopped:
            return QueueCompletion("standalone")
        return QueueCompletion("none")

    def start(self) -> None:
        self.running = True
        self.batch_clean = True

    def stop(self) -> None:
        self.running = False

    def clear(self) -> None:
        """Remove all queued paths and reset batch state."""
        self.paths.clear()
        self.running = False
        self.batch_clean = True

    def sync_with_visible(self, visible_count: int) -> None:
        """Ensure an empty visible queue list never retains hidden runnable paths."""
        if visible_count <= 0:
            self.paths.clear()
            self.running = False
        elif len(self.paths) > visible_count:
            del self.paths[visible_count:]

    def mark_unreviewed(self) -> None:
        self.batch_clean = False

    def unattended(self, after_action, no_action) -> bool:
        return self.running and (bool(self.paths) or after_action not in (None, no_action))

    @staticmethod
    def is_missing(path) -> bool:
        return not Path(path).is_file()

    @staticmethod
    def is_sdr(kind) -> bool:
        return kind == "sdr"

    @staticmethod
    def free_destination(destination) -> Path:
        """Return an unoccupied auto-numbered destination beside the requested one."""
        destination = Path(destination)
        if not destination.exists():
            return destination
        number = 1
        while True:
            candidate = destination.with_name(f"{destination.stem}_{number}{destination.suffix}")
            if not candidate.exists():
                return candidate
            number += 1
