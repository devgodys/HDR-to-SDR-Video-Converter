"""Tests for hdr2sdr.core.progress - the PURE CORE progress parsers.

Same rule as test_command_builders.py: no QApplication, no widgets. Same
suite as tests/test_progress.py in the single-file layout, re-pointed at
the package.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hdr2sdr.core.progress import (
    ErrorHintScanner,
    FfmpegProgressParser,
    HandbrakeProgressParser,
    LineAssembler,
    make_progress_parser,
)


class TestLineAssembler:
    def test_splits_on_newline_and_carriage_return(self):
        a = LineAssembler()
        assert a.feed("frame=1\r\nfps=25\r") == ["frame=1", "fps=25"]

    def test_holds_back_incomplete_trailing_line(self):
        a = LineAssembler()
        assert a.feed("frame=1\nfp") == ["frame=1"]
        assert a.feed("s=25\n") == ["fps=25"]

    def test_line_split_across_arbitrary_chunk_boundary(self):
        """The real failure mode this guards against: a QProcess read can
        land in the middle of any line, not just at a key=value
        boundary."""
        a = LineAssembler()
        raw = "frame=100\nout_time_ms=20000000\nprogress=continue\n"
        out = []
        for i in range(0, len(raw), 7):
            out += a.feed(raw[i:i + 7])
        assert out == ["frame=100", "out_time_ms=20000000", "progress=continue"]

    def test_empty_lines_dropped(self):
        a = LineAssembler()
        assert a.feed("frame=1\n\nfps=25\n") == ["frame=1", "fps=25"]

    def test_reset_clears_buffer(self):
        a = LineAssembler()
        a.feed("incomplete-line-no-terminator")
        a.reset()
        assert a.feed("frame=1\n") == ["frame=1"]

    def test_flush_emits_trailing_line_without_newline(self):
        a = LineAssembler()
        assert a.feed("frame=1\n[hevc_nvenc] No capable devices found") == ["frame=1"]
        assert a.flush() == ["[hevc_nvenc] No capable devices found"]
        assert a.flush() == []


class TestFfmpegProgressParser:
    def _feed_block(self, parser, out_time_ms, extra=None, key_order=None):
        block = {"out_time_ms": str(out_time_ms), **(extra or {})}
        last = None
        for k in (key_order or list(block)):
            last = parser.feed(f"{k}={block[k]}")
        return parser.feed("progress=continue")

    def test_incomplete_block_returns_no_stats(self):
        p = FfmpegProgressParser(duration=100.0)
        stats, consumed = p.feed("out_time_ms=5000000")
        assert stats is None
        assert consumed is True

    def test_complete_block_computes_percentage(self):
        p = FfmpegProgressParser(duration=100.0)
        p.feed("out_time_ms=20000000")
        stats, consumed = p.feed("progress=continue")
        assert consumed is True
        assert stats.pct == 20.0
        assert stats.out_sec == 20.0

    def test_percentage_clamped_at_99_9(self):
        p = FfmpegProgressParser(duration=100.0)
        p.feed("out_time_ms=999000000")  # 999s of a 100s source
        stats, _ = p.feed("progress=continue")
        assert stats.pct == 99.9

    def test_negative_ffmpeg_timestamp_is_treated_as_missing(self):
        p = FfmpegProgressParser(duration=100.0)
        p.feed("out_time_ms=-9223372036854775808")
        stats, _ = p.feed("progress=continue")
        assert stats.pct is None
        assert stats.out_sec is None
        assert stats.eta_sec is None

    def test_resume_offset_shifts_absolute_position(self):
        """out_time_ms is relative to the resumed process's own -ss-shifted
        timeline; offset must shift it back to an absolute source position."""
        p = FfmpegProgressParser(duration=100.0, offset=40.0)
        p.feed("out_time_ms=10000000")  # 10s into *this* segment
        stats, _ = p.feed("progress=continue")
        assert stats.out_sec == 50.0
        assert stats.pct == 50.0

    def test_configure_updates_duration_and_offset_live(self):
        """duration can arrive late (source analysed mid-run) and offset
        changes when a resume segment starts - both must be re-readable
        without recreating the parser."""
        p = FfmpegProgressParser()
        p.feed("out_time_ms=10000000")
        stats, _ = p.feed("progress=continue")
        assert stats.pct is None  # no duration yet
        p.configure(duration=50.0, offset=0.0)
        p.feed("out_time_ms=25000000")
        stats, _ = p.feed("progress=continue")
        assert stats.pct == 50.0

    def test_speed_and_bitrate_conversion(self):
        p = FfmpegProgressParser(duration=100.0)
        p.feed("out_time_ms=10000000")
        p.feed("speed=2.5x")
        p.feed("bitrate=8000.0kbits/s")
        stats, _ = p.feed("progress=continue")
        assert stats.speed_x == 2.5
        assert stats.mbps == 1.0  # 8000 kbit/s -> 1.0 MB/s

    def test_eta_uses_speed_and_remaining_percentage(self):
        p = FfmpegProgressParser(duration=100.0)
        p.feed("out_time_ms=50000000")  # 50%
        p.feed("speed=2.0x")
        stats, _ = p.feed("progress=continue")
        assert stats.eta_sec == 25.0  # 50s remaining / 2.0x

    def test_no_eta_without_speed(self):
        p = FfmpegProgressParser(duration=100.0)
        p.feed("out_time_ms=50000000")
        stats, _ = p.feed("progress=continue")
        assert stats.eta_sec is None

    def test_block_resets_after_completion(self):
        """A stale field from a previous block must not leak into the
        next one - each progress=continue starts a fresh dict."""
        p = FfmpegProgressParser(duration=100.0)
        p.feed("out_time_ms=10000000")
        p.feed("speed=2.0x")
        p.feed("progress=continue")
        p.feed("out_time_ms=20000000")
        stats, _ = p.feed("progress=continue")
        assert stats.speed_x is None

    def test_non_progress_line_not_consumed(self):
        p = FfmpegProgressParser(duration=100.0)
        stats, consumed = p.feed("some ffmpeg banner or warning text")
        assert stats is None
        assert consumed is False

    def test_stream_prefixed_keys_are_consumed_but_ignored(self):
        p = FfmpegProgressParser(duration=100.0)
        stats, consumed = p.feed("stream_0_0_q=23.0")
        assert consumed is True
        assert stats is None

    def test_reset_clears_partial_block(self):
        p = FfmpegProgressParser(duration=100.0)
        p.feed("out_time_ms=10000000")
        p.reset()
        p.feed("progress=continue")
        stats, _ = p.feed("progress=continue")
        # after reset, the dangling "progress=continue" alone forms a
        # complete (if empty) block on its own - out_time_ms is gone.
        assert stats.out_sec is None


class TestHandbrakeProgressParser:
    def test_parses_percentage_fps_and_eta(self):
        p = HandbrakeProgressParser(duration=200.0)
        line = "Encoding: task 1 of 1, 45.23 % (123.4 fps, avg 110.3 fps, ETA 00h05m32s)"
        stats, consumed = p.feed(line)
        assert consumed is True
        assert stats.pct == 45.23
        assert stats.fps == 123.4

    def test_eta_hms_conversion(self):
        p = HandbrakeProgressParser(duration=200.0)
        line = "Encoding: task 1 of 1, 45.23 % (123.4 fps, avg 110.3 fps, ETA 00h05m32s)"
        stats, _ = p.feed(line)
        assert stats.eta_sec == 5 * 60 + 32

    def test_out_sec_derived_from_duration_and_percentage(self):
        p = HandbrakeProgressParser(duration=200.0)
        stats, _ = p.feed("Encoding: task 1 of 1, 50.00 %")
        assert stats.out_sec == 100.0

    def test_no_duration_means_no_out_sec(self):
        p = HandbrakeProgressParser(duration=0.0)
        stats, _ = p.feed("Encoding: task 1 of 1, 50.00 %")
        assert stats.out_sec is None

    def test_percentage_clamped_at_99_9(self):
        p = HandbrakeProgressParser(duration=200.0)
        stats, _ = p.feed("Encoding: task 1 of 1, 100.00 %")
        assert stats.pct == 99.9

    def test_line_without_percentage_not_consumed(self):
        p = HandbrakeProgressParser(duration=200.0)
        stats, consumed = p.feed("Starting encode...")
        assert stats is None
        assert consumed is False

    def test_progress_without_fps_block_still_parses(self):
        p = HandbrakeProgressParser(duration=200.0)
        stats, consumed = p.feed("Encoding: task 1 of 1, 10.00 %")
        assert consumed is True
        assert stats.pct == 10.0
        assert stats.fps is None
        assert stats.eta_sec is None

    def test_configure_ignores_offset(self):
        """HandBrake reports a percentage of the whole job, which is
        already absolute - offset is accepted for interface parity with
        FfmpegProgressParser and does nothing."""
        p = HandbrakeProgressParser(duration=100.0)
        p.configure(duration=50.0, offset=999.0)
        stats, _ = p.feed("Encoding: task 1 of 1, 50.00 %")
        assert stats.out_sec == 25.0  # uses the new duration, ignores offset


class TestMakeProgressParser:
    def test_selects_ffmpeg_parser(self):
        assert isinstance(make_progress_parser(using_handbrake=False), FfmpegProgressParser)

    def test_selects_handbrake_parser(self):
        assert isinstance(make_progress_parser(using_handbrake=True), HandbrakeProgressParser)


class TestErrorHintScanner:
    def test_mp4_track_hint_only_checked_for_ffmpeg(self):
        ffmpeg_scanner = ErrorHintScanner(using_handbrake=False)
        hb_scanner = ErrorHintScanner(using_handbrake=True)
        line = "Could not find tag for codec truehd in stream #0"
        ffmpeg_scanner.feed(line)
        hb_scanner.feed(line)
        assert ffmpeg_scanner.mp4_track is True
        assert hb_scanner.mp4_track is False

    def test_hw_encoder_hint_requires_matching_vendor_label(self):
        s = ErrorHintScanner(using_handbrake=False)
        s.configure(encoder_label="CPU \u00b7 H.265", hwaccel_decode_on=False)
        s.feed("nvenc: no capable devices found for encoding")
        # CPU encoder selected - an nvenc-shaped error is irrelevant noise.
        assert s.hw_encoder is False

    def test_hw_encoder_hint_latches_for_hardware_vendor(self):
        s = ErrorHintScanner(using_handbrake=False)
        s.configure(encoder_label="NVIDIA NVENC \u00b7 H.265", hwaccel_decode_on=False)
        s.feed("nvenc: no capable devices found for encoding")
        assert s.hw_encoder is True

    def test_hw_decode_hint_requires_hwaccel_on(self):
        s = ErrorHintScanner(using_handbrake=False)
        s.configure(encoder_label=None, hwaccel_decode_on=False)
        s.feed("hwaccel initialisation returned error: -1")
        assert s.hw_decode is False
        s.configure(encoder_label=None, hwaccel_decode_on=True)
        s.feed("hwaccel initialisation returned error: -1")
        assert s.hw_decode is True

    def test_flags_latch_and_do_not_unlatch(self):
        s = ErrorHintScanner(using_handbrake=False)
        s.configure(encoder_label="NVIDIA NVENC \u00b7 H.265", hwaccel_decode_on=False)
        s.feed("nvenc: no capable devices found for encoding")
        s.feed("a perfectly normal log line")
        assert s.hw_encoder is True

    def test_gpu_tonemap_hint_checked_regardless_of_backend(self):
        hb_scanner = ErrorHintScanner(using_handbrake=True)
        hb_scanner.feed("VK_ERROR_OUT_OF_DEVICE_MEMORY")
        assert hb_scanner.gpu_tonemap is True

    def test_feed_gpu_tonemap_only_does_not_touch_other_flags(self):
        """The test-clip render path must not be able to latch the
        hardware-encoder/decoder or MP4-track flags - those belong to the
        real conversion only, see the docstring on this method."""
        s = ErrorHintScanner(using_handbrake=False)
        s.configure(encoder_label="NVIDIA NVENC \u00b7 H.265", hwaccel_decode_on=True)
        s.feed_gpu_tonemap_only("nvenc: no capable devices found for encoding")
        s.feed_gpu_tonemap_only("hwaccel initialisation returned error: -1")
        s.feed_gpu_tonemap_only("Could not find tag for codec truehd in stream #0")
        assert s.hw_encoder is False
        assert s.hw_decode is False
        assert s.mp4_track is False

    def test_reset_clears_all_flags(self):
        s = ErrorHintScanner(using_handbrake=False)
        s.configure(encoder_label="NVIDIA NVENC \u00b7 H.265", hwaccel_decode_on=True)
        s.feed("nvenc: no capable devices found for encoding")
        s.reset()
        assert s.hw_encoder is False
        assert s.mp4_track is False
        assert s.hw_decode is False
        assert s.gpu_tonemap is False


class TestHwDecodeRuntimeFailure:
    """Hardware decode that dies mid-run (DXVA2 can't copy a frame out of
    GPU memory, exit -12) is a different animal from an init failure: it
    must NOT latch the init flag (that one suppresses the resume offer)."""

    LINES = [
        "[hevc @ 000001dd4a6dbcc0] Failed to transfer data to output frame: -12.",
        "[dec:hevc @ 000001dcfeb367c0] Error processing packet in decoder: Cannot allocate memory",
    ]

    def test_latches_runtime_flag_only(self):
        for line in self.LINES:
            s = ErrorHintScanner()
            s.configure(encoder_label=None, hwaccel_decode_on=True)
            s.feed(line)
            assert s.hw_decode_runtime is True
            assert s.hw_decode is False

    def test_requires_hwaccel_on(self):
        """With software decode an ENOMEM is a genuine out-of-memory."""
        for line in self.LINES:
            s = ErrorHintScanner()
            s.configure(encoder_label=None, hwaccel_decode_on=False)
            s.feed(line)
            assert s.hw_decode_runtime is False

    def test_reset_clears_it(self):
        s = ErrorHintScanner()
        s.configure(encoder_label=None, hwaccel_decode_on=True)
        s.feed(self.LINES[0])
        s.reset()
        assert s.hw_decode_runtime is False



def test_inline_progress_status_text_and_drop_priority():
    """P0-4: Speed statistics combine into the progress status string on the right
    of the progress bar and drop parts in priority order:
    MB/s -> x realtime -> (keep fps and ETA), while keeping full text for tooltip."""
    from hdr2sdr.core.progress import build_progress_stat_candidates, select_progress_status_text

    cands = build_progress_stat_candidates(
        "Converting\u2026 42%",
        speed_x=1.45,
        fps=18.0,
        mbps=6.2,
        eta_text="ETA 12:30",
    )
    assert cands[0] == "Converting\u2026 42% \u00b7 1.45x realtime \u00b7 18 fps \u00b7 6.2 MB/s \u00b7 ETA 12:30"
    assert cands[1] == "Converting\u2026 42% \u00b7 1.45x realtime \u00b7 18 fps \u00b7 ETA 12:30"
    assert cands[2] == "Converting\u2026 42% \u00b7 18 fps \u00b7 ETA 12:30"
    assert cands[3] == "Converting\u2026 42% \u00b7 ETA 12:30"
    assert cands[4] == "Converting\u2026 42%"

    # Wide enough for everything
    disp_full, tip = select_progress_status_text(
        "Converting\u2026 42%",
        speed_x=1.45,
        fps=18.0,
        mbps=6.2,
        eta_text="ETA 12:30",
        max_width=len(cands[0]),
    )
    assert disp_full == cands[0]
    assert tip == cands[0]

    # Drop MB/s first
    disp_no_mbps, tip2 = select_progress_status_text(
        "Converting\u2026 42%",
        speed_x=1.45,
        fps=18.0,
        mbps=6.2,
        eta_text="ETA 12:30",
        max_width=len(cands[1]),
    )
    assert disp_no_mbps == cands[1]
    assert "MB/s" not in disp_no_mbps
    assert "1.45x realtime" in disp_no_mbps
    assert tip2 == cands[0]

    # Drop x realtime next, keeping fps and ETA
    disp_fps_eta, tip3 = select_progress_status_text(
        "Converting\u2026 42%",
        speed_x=1.45,
        fps=18.0,
        mbps=6.2,
        eta_text="ETA 12:30",
        max_width=len(cands[2]),
    )
    assert disp_fps_eta == "Converting\u2026 42% \u00b7 18 fps \u00b7 ETA 12:30"
    assert tip3 == cands[0]
