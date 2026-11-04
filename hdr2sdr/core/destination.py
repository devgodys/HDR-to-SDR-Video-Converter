"""Pure path planning for conversion outputs."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


def _same_path(a: os.PathLike | str, b: os.PathLike | str) -> bool:
    """Compare paths robustly, including aliases to existing files."""
    a, b = Path(a), Path(b)
    try:
        if a.exists() and b.exists():
            return a.samefile(b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


@dataclass(frozen=True)
class DestinationPlan:
    """All output-side paths for one final destination."""

    final: Path

    def __init__(self, final: os.PathLike | str):
        object.__setattr__(self, "final", Path(final))

    @property
    def part(self) -> Path:
        return self.final.with_name(f"{self.final.stem}.part{self.final.suffix}")

    @property
    def sidecar(self) -> Path:
        return self.sidecar_for(self.part)

    @staticmethod
    def is_part_file(path: os.PathLike | str | None) -> bool:
        """Whether a path uses the app's ``name.part.ext`` output naming."""
        return path is not None and Path(path).stem.endswith(".part")

    @staticmethod
    def sidecar_for(part: os.PathLike | str) -> Path:
        part = Path(part)
        return part.with_name(part.name + ".json")

    @property
    def concat_list(self) -> Path:
        return self.concat_list_for(self.final)

    @staticmethod
    def concat_list_for(destination: os.PathLike | str) -> Path:
        destination = Path(destination)
        return destination.with_name(f"{destination.stem}.resume-concat.txt")

    def resume_part(self, number: int) -> Path:
        if number < 1:
            raise ValueError("resume part number must be positive")
        return self.final.with_name(f"{self.final.stem}.resume-part{number}{self.final.suffix}")

    def collides_with(self, source: os.PathLike | str) -> bool:
        """Whether a source path is any file this conversion may write or remove."""
        source = Path(source)
        fixed = (self.final, self.part, self.sidecar,
                 self.concat_list_for(self.final), self.concat_list_for(self.part))
        if any(_same_path(source, path) for path in fixed):
            return True
        pattern = re.compile(
            rf"^{re.escape(self.final.stem)}\.resume-part\d+{re.escape(self.final.suffix)}$",
            re.IGNORECASE,
        )
        return (
            pattern.match(source.name) is not None
            and os.path.normcase(os.path.abspath(str(source.parent)))
            == os.path.normcase(os.path.abspath(str(self.final.parent)))
        )
