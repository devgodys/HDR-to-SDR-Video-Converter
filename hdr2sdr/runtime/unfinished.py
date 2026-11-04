"""Persistence for interrupted conversion metadata and lookup records."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Callable


class UnfinishedStore:
    """Read and write unfinished conversion sidecars and their JSON registry.

    Settings and source details are supplied by the caller so this module stays
    independent of the UI and conversion settings model.
    """

    def __init__(self, registry_path: os.PathLike | str):
        self.registry_path = Path(registry_path)

    @staticmethod
    def source_fingerprint(source: os.PathLike | str) -> list[Any]:
        source = Path(source)
        normalized = os.path.normcase(os.path.abspath(str(source)))
        try:
            stat = source.stat()
            return [normalized, stat.st_size, stat.st_mtime_ns]
        except OSError:
            return [normalized, 0, 0]

    @staticmethod
    def sidecar_path(part: os.PathLike | str) -> Path:
        part = Path(part)
        return part.with_name(part.name + ".json")

    def _record_is_recoverable(self, record: dict[str, Any]) -> bool:
        try:
            part = Path(record["part"])
            if part.is_file():
                return True
            sidecar = self.read_sidecar(part)
            chain = sidecar.get("chain") if isinstance(sidecar, dict) else None
            if isinstance(chain, dict):
                parts = chain.get("parts")
                if isinstance(parts, list) and parts and all(Path(p).is_file() for p in parts):
                    return True
        except (OSError, TypeError, ValueError):
            return False
        return False

    def load(self) -> list[dict[str, Any]]:
        try:
            with self.registry_path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        valid = []
        for record in data:
            if not isinstance(record, dict) or not record.get("part"):
                continue
            if self._record_is_recoverable(record):
                valid.append(record)
        return valid

    def save(self, records: list[dict[str, Any]]) -> None:
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.registry_path.with_name(self.registry_path.name + ".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(records[-50:], stream, indent=2)
            os.replace(temporary, self.registry_path)
        except OSError:
            pass

    @staticmethod
    def _path_key(path: os.PathLike | str) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def remember(self, part: os.PathLike | str, destination: os.PathLike | str,
                 source_fingerprint: list[Any], at: str | None = None) -> None:
        key = self._path_key(part)
        records = [record for record in self.load()
                   if self._path_key(record["part"]) != key]
        records.append({
            "part": str(part),
            "dst": str(destination),
            "source": source_fingerprint,
            "at": at or datetime.datetime.now().isoformat(timespec="seconds"),
        })
        self.save(records)

    def forget(self, part: os.PathLike | str) -> None:
        key = self._path_key(part)
        self.save([record for record in self.load()
                   if self._path_key(record["part"]) != key])

    def discard(self, part: os.PathLike | str) -> None:
        """Delete an unfinished output, any chained segments, and their metadata."""
        part = Path(part)
        sidecar = self.read_sidecar(part)
        chain = sidecar.get("chain") if isinstance(sidecar, dict) else None
        chained_parts: list[Path] = []
        if isinstance(chain, dict) and isinstance(chain.get("parts"), list):
            for item in chain["parts"]:
                try:
                    chained_parts.append(Path(item))
                except (TypeError, ValueError):
                    continue
        for target in (*chained_parts, part):
            for path in (target, self.sidecar_path(target)):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.forget(target)

    def publish(self, part: os.PathLike | str, destination: os.PathLike | str,
                publisher: Callable) -> OSError | None:
        """Publish a completed part and clear its unfinished-run metadata."""
        error = publisher(part, destination)
        if error is not None:
            return error
        try:
            self.sidecar_path(part).unlink(missing_ok=True)
        except OSError:
            pass
        self.forget(part)
        return None

    def for_source(self, source_fingerprint: list[Any], suffix: str | None = None) -> list[Path]:
        found = []
        for record in self.load():
            part = Path(record["part"])
            if record.get("source") != source_fingerprint:
                continue
            if suffix is not None and part.suffix.lower() != suffix.lower():
                continue
            found.append(part)
        return found

    def write_sidecar(self, part: os.PathLike | str, settings_signature: str,
                      source_fingerprint: list[Any],
                      chain: dict[str, Any] | None = None) -> None:
        sidecar = self.sidecar_path(part)
        temporary = sidecar.with_name(sidecar.name + ".tmp")
        payload: dict[str, Any] = {
            "settings": settings_signature,
            "source": source_fingerprint,
        }
        if chain:
            payload["chain"] = chain
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream)
            os.replace(temporary, sidecar)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def read_sidecar(self, part: os.PathLike | str) -> dict[str, Any]:
        try:
            data = json.loads(self.sidecar_path(part).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}
