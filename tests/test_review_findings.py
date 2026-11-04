"""Regression tests for the findings in REVIEW.md (ids R-xx match its headings).

They were written as strict xfails while each bug existed; every fixed finding has since become
an ordinary test (no xfail markers are applied any more). If a new finding needs one, add
``pytest.mark.xfail(strict=True, reason=...)`` directly on that test.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hdr2sdr.core import commands
from hdr2sdr.core.bitrate import estimate_bitrate_kbps
from hdr2sdr.core.commands import (
    ConversionSettings, build_container_args, build_encode_args, build_ffmpeg_command,
)
from hdr2sdr.core.progress import ErrorHintScanner, HandbrakeProgressParser
from hdr2sdr.runtime.resume import ResumeCoordinator
from hdr2sdr.runtime.updates import _tag_is_newer

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))



# ---------------------------------------------------------------- core ----

def test_qsv_has_its_own_bitrate_efficiency():
    qsv = estimate_bitrate_kbps("Intel QSV \u00b7 H.265", 20, 1920, 1080)
    apple = estimate_bitrate_kbps("Apple VideoToolbox \u00b7 H.265", 20, 1920, 1080)
    assert qsv != apple


def test_intel_qsv_open_failure_is_reported_as_hardware_encoder_problem():
    scanner = ErrorHintScanner(encoder_label="Intel QSV \u00b7 H.265")
    scanner.feed("[hevc_qsv @ 0x1] Error while opening encoder for output stream #0:0")
    assert scanner.hw_encoder


def test_handbrake_two_pass_progress_never_goes_backwards():
    parser = HandbrakeProgressParser(duration=100)
    first, _ = parser.feed("Encoding: task 1 of 2, 99.80 % (200.00 fps, avg 190.00 fps, ETA 00h00m01s)")
    second, _ = parser.feed("Encoding: task 2 of 2, 0.50 % (30.00 fps, avg 30.00 fps, ETA 00h10m00s)")
    assert second.pct >= first.pct


def test_trailing_zero_in_release_tag_is_not_an_update():
    assert not _tag_is_newer("v.2.1.0", "2.1")


def test_hevc_in_mp4_is_tagged_hvc1():
    s = ConversionSettings(src="in.mkv", dst="out.mp4", encoder_label="CPU \u00b7 H.265",
                           tonemap_method="Hable \u00b7 CPU tonemap")
    cmd = build_ffmpeg_command(s)
    assert "-tag:v" in cmd and cmd[cmd.index("-tag:v") + 1] == "hvc1"


def test_nvenc_av1_quality_is_not_passed_through_on_the_h265_scale():
    s = ConversionSettings(encoder_label="NVIDIA NVENC \u00b7 AV1", quality=18)
    _enc, opts = build_encode_args(s)
    assert dict(zip(opts[::2], opts[1::2]))["-qp"] != "18"


def test_command_builder_never_puts_none_into_argv(monkeypatch):
    monkeypatch.setattr(commands, "exe", lambda name: None)
    with pytest.raises(commands.ToolMissing):
        build_ffmpeg_command(ConversionSettings(src="in.mkv", dst="out.mp4"))


# --------------------------------------------------------------- Qt side ----

def test_clipping_pct_budget_at_4k():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from hdr2sdr.ui.imaging import clipping_pct

    app = QApplication.instance() or QApplication([])  # noqa: F841 (never a bare QGuiApplication: it would clash with `win`)
    img = QImage(3840, 2160, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.gray)
    started = time.perf_counter()
    clipping_pct(img)
    # It runs every 1.5 s while converting; anything above ~50 ms is visible jank.
    assert time.perf_counter() - started < 0.05


@pytest.fixture
def win(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    for _ in range(4):
        app.processEvents()
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    yield w
    w.close()


def test_queue_does_not_stall_silently_on_an_sdr_item(win, tmp_path):
    clip = tmp_path / "already_sdr.mkv"
    clip.write_bytes(b"x")
    win._set_source(str(clip), analyze=False)
    win.kind = "sdr"
    win._queue_controller.running = True
    win._queue_controller.paths.append(str(tmp_path / "next.mkv"))
    win.start()
    from PySide6.QtWidgets import QApplication
    for _ in range(20):                                  # the skip hands over to the next item asynchronously
        QApplication.instance().processEvents()
    # Either the queue moved on or it was cleanly marked as not running; what it
    # must not do is stay "running" with nothing running and no way to finish.
    assert not win._queue_controller.running or win.proc is not None


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg/ffprobe")
def test_resume_join_does_not_replay_the_overlap(tmp_path):
    def run(*args):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)

    def duration(path):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True).stdout
        return float(out)

    src = tmp_path / "src.mkv"
    run("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=20", "-c:v", "libx264",
        "-preset", "ultrafast", str(src))
    part1, part2 = tmp_path / "p1.mkv", tmp_path / "p2.mkv"
    run("-i", str(src), "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", str(part1))
    # exactly what _begin_resume() does: restart 4 s before the end of part 1
    run("-ss", "6", "-i", str(src), "-c:v", "libx264", "-preset", "ultrafast", str(part2))

    final = tmp_path / "joined.mkv"
    cmd, _list = ResumeCoordinator.concat_command(
        [part1, part2], final, "ffmpeg", build_container_args, [6.0])
    cmd[0] = shutil.which("ffmpeg")
    subprocess.run(cmd, check=True, capture_output=True)
    assert abs(duration(final) - duration(src)) < 1.0   # today: ~24 s for a 20 s source
