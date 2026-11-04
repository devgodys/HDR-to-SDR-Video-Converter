"""Bounded, best-effort persistence for completed conversion history."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any


class HistoryStore:
    """Keep recent conversion outcomes and settings outside the UI layer."""

    def __init__(self, path: os.PathLike | str, max_records: int = 200):
        self.path = Path(path)
        self.max_records = max(1, int(max_records))

    @staticmethod
    def source_fingerprint(source: os.PathLike | str) -> list[Any]:
        source = Path(source)
        normalized = os.path.normcase(os.path.abspath(str(source)))
        try:
            stat = source.stat()
            return [normalized, stat.st_size, stat.st_mtime_ns]
        except OSError:
            return [normalized, 0, 0]

    def load(self) -> list[dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict) and item.get("source")]

    def append(self, source, destination, status, kind, settings=None,
               timestamp: str | None = None) -> None:
        if not source:
            return
        entry = {
            "timestamp": timestamp or datetime.datetime.now().isoformat(timespec="seconds"),
            "source": str(source),
            "source_fingerprint": self.source_fingerprint(source),
            "destination": str(destination) if destination else "",
            "status": str(status),
            "kind": str(kind or "unknown"),
            "settings": settings if isinstance(settings, dict) else {},
        }
        records = self.load()
        records.append(entry)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(records[-self.max_records:], stream, indent=2)
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def for_source(self, source: os.PathLike | str) -> list[dict[str, Any]]:
        target = self.source_fingerprint(source)
        normalized_path = target[0]
        matches = []
        for record in self.load():
            fingerprint = record.get("source_fingerprint")
            if isinstance(fingerprint, list) and len(fingerprint) == 3:
                if fingerprint == target:
                    matches.append(record)
            elif os.path.normcase(os.path.abspath(str(record["source"]))) == normalized_path:
                # Older history entries recorded path only.
                matches.append(record)
        return matches

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
