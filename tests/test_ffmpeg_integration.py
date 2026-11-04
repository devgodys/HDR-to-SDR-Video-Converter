"""Real FFmpeg: an MP4 output no longer dies on tracks MP4 can't hold."""
import shutil
import subprocess
from dataclasses import replace

import pytest

from hdr2sdr.core.commands import ConversionSettings, ffmpeg_track_args, ffmpeg_track_codec_args
from hdr2sdr.core.utils import probe_streams

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg/ffprobe not installed")


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    mkv = d / "src.mkv"
    (d / "s.srt").write_text("1\n00:00:00,000 --> 00:00:01,500\nHello\n")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=2",
                        "-f", "lavfi", "-i", "sine=duration=2", "-f", "lavfi", "-i", "sine=f=880:duration=2",
                        "-i", str(d / "s.srt"), "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a:0", "truehd", "-strict", "-2", "-ac", "2",
                        "-c:a:1", "ac3", "-c:s", "srt", str(mkv)], capture_output=True, text=True)
    if r.returncode:
        pytest.skip("this ffmpeg can't make the TrueHD test file: " + r.stderr[-200:])
    return mkv


def convert(src, dst, s):
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", *ffmpeg_track_args(s, str(dst)),
           "-c:v", "libx264", *ffmpeg_track_codec_args(s, str(dst)), str(dst)]
    return subprocess.run(cmd, capture_output=True, text=True)


def kinds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True).stdout.split()
    return out


def test_probe_finds_the_tracks(source):
    assert [c for _i, _t, c in probe_streams(str(source))] == ["truehd", "ac3", "subrip"]


def test_mp4_drops_truehd_and_converts_subtitles_while_old_behaviour_failed(source, tmp_path):
    base = ConversionSettings(src=str(source), dst=str(tmp_path / "o.mp4"), src_streams=probe_streams(str(source)))
    assert convert(source, tmp_path / "old.mp4", None).returncode != 0          # the bug this fixes
    r = convert(source, tmp_path / "new.mp4", base)
    assert r.returncode == 0, r.stderr
    assert kinds(tmp_path / "new.mp4") == ["h264", "ac3", "mov_text"]


def test_mkv_keeps_everything(source, tmp_path):
    base = ConversionSettings(src=str(source), dst=str(tmp_path / "o.mkv"), src_streams=probe_streams(str(source)))
    assert convert(source, tmp_path / "new.mkv", replace(base, dst=str(tmp_path / "new.mkv"))).returncode == 0
    assert kinds(tmp_path / "new.mkv") == ["h264", "truehd", "ac3", "subrip"]
