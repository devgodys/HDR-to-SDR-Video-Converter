"""Which files the app accepts: drag & drop filters by a broad extension list,
files picked by hand in the file dialog are accepted whatever they're called."""
from pathlib import Path

from hdr2sdr.core.constants import VIDEO_DIALOG_FILTER, VIDEO_EXTENSIONS
from hdr2sdr.runtime.queue import QueueController


def test_blu_ray_and_camera_formats_are_known():
    for ext in (".ts", ".m2ts", ".mts", ".m2t", ".avi", ".mpg", ".vob", ".wmv", ".flv", ".3gp", ".mxf", ".hevc"):
        assert ext in VIDEO_EXTENSIONS
    assert ".m2ts" in VIDEO_DIALOG_FILTER and VIDEO_DIALOG_FILTER.endswith("All files (*.*)")


def test_drop_filters_but_dialog_trusts(tmp_path):
    names = ["a.TS", "b.m2ts", "c.avi", "readme.txt", "photo.jpg", "weird.xyz"]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    paths = [str(tmp_path / n) for n in names]
    accepted = QueueController.accepted_files(paths, VIDEO_EXTENSIONS)
    assert sorted(Path(p).name for p in accepted) == ["a.TS", "b.m2ts", "c.avi"]
    trusted = QueueController.accepted_files(paths, VIDEO_EXTENSIONS, trust_extension=True)
    assert len(trusted) == len(names)
