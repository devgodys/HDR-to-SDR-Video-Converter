"""MP4/MOV outputs leave out tracks the container can't hold (Blu-ray
TrueHD / PGS, ...) instead of failing the whole conversion; MKV keeps all."""
from hdr2sdr.core.commands import (
    ConversionSettings, build_ffmpeg_command, build_test_clip_command, plan_tracks,
)

# a typical Blu-ray .m2ts: video 0, TrueHD 1, AC-3 2, PGS 3, PGS 4, DTS 5
BLURAY = (
    (1, "audio", "truehd"), (2, "audio", "ac3"),
    (3, "subtitle", "hdmv_pgs_subtitle"), (4, "subtitle", "hdmv_pgs_subtitle"),
    (5, "audio", "dts"),
)


def settings(**kw):
    base = dict(src="/in/disc.m2ts", dst="/out/disc.sdr.mp4", src_streams=BLURAY)
    base.update(kw)
    return ConversionSettings(**base)


def test_mp4_skips_unsupported_tracks_and_keeps_the_rest():
    plan = plan_tracks(settings())
    assert [i for i, _t, _c in plan.skip] == [1, 3, 4]      # TrueHD + both PGS
    cmd = build_ffmpeg_command(settings())
    assert cmd.count("-0:1") == 1 and "-0:3" in cmd and "-0:4" in cmd
    assert "-0:2" not in cmd and "-0:5" not in cmd           # AC-3 and DTS stay
    # negative maps come after the positive ones they subtract from
    assert cmd.index("-0:1") > cmd.index("0:s?")


def test_mkv_keeps_everything():
    s = settings(dst="/out/disc.sdr.mkv")
    assert plan_tracks(s).skip == ()
    cmd = build_ffmpeg_command(s)
    assert not any(a.startswith("-0:") for a in cmd)
    assert cmd[cmd.index("-c:s") + 1] == "copy"


def test_text_subtitles_become_mov_text_in_mp4():
    s = settings(src_streams=((1, "audio", "aac"), (2, "subtitle", "subrip"), (3, "subtitle", "ass")))
    plan = plan_tracks(s)
    assert plan.skip == () and plan.subtitle_codec == "mov_text"
    cmd = build_ffmpeg_command(s)
    assert cmd[cmd.index("-c:s") + 1] == "mov_text"


def test_unknown_streams_keeps_old_behaviour():
    s = settings(src_streams=())
    assert plan_tracks(s).skip == ()
    cmd = build_ffmpeg_command(s)
    assert not any(a.startswith("-0:") for a in cmd)


def test_test_clip_follows_its_own_output_extension():
    s = settings(dst="/out/disc.sdr.mkv")                    # real output MKV...
    cmd, _w = build_test_clip_command(s, "/tmp/clip.mp4", 10.0, 5)   # ...clip is MP4
    assert "-0:1" in cmd and "-0:3" in cmd
    cmd, _w = build_test_clip_command(s, "/tmp/clip.mkv", 10.0, 5)
    assert not any(a.startswith("-0:") for a in cmd)



def test_mkv_mov_text_subtitles_overridden_to_srt_by_ordinal():
    """FIX_MKV_MOV_TEXT_SUBTITLES_2026-10-23:
    MKV with [video, audio, mov_text, subrip, hdmv_pgs_subtitle, mov_text]
    keeps global -c:s copy and adds -c:s:0 srt and -c:s:3 srt (no overrides
    for ordinal 1 and 2)."""
    from hdr2sdr.core.commands import ffmpeg_track_codec_args
    streams = (
        (0, "video", "hevc"),
        (1, "audio", "aac"),
        (2, "subtitle", "mov_text"),
        (3, "subtitle", "subrip"),
        (4, "subtitle", "hdmv_pgs_subtitle"),
        (5, "subtitle", "mov_text"),
    )
    s = settings(dst="/out/video.sdr.mkv", src_streams=streams)
    plan = plan_tracks(s)
    assert plan.skip == ()
    assert plan.subtitle_codec == "copy"
    assert plan.sub_overrides == ((0, "srt"), (3, "srt"))
    assert plan.describe() == [
        "subtitle stream #2 (mov_text) converted to SRT for Matroska",
        "subtitle stream #5 (mov_text) converted to SRT for Matroska",
    ]

    codec_args = ffmpeg_track_codec_args(s)
    assert codec_args == [
        "-c:a", "copy",
        "-c:s", "copy",
        "-c:s:0", "srt",
        "-c:s:3", "srt",
        "-max_interleave_delta", "0",
    ]
    assert "-c:s:1" not in codec_args
    assert "-c:s:2" not in codec_args

    # Main conversion, live-preview branch, and test clip all include the overrides
    for preview_path in (None, "/tmp/pv.jpg"):
        cmd = build_ffmpeg_command(settings(dst="/out/video.sdr.mkv", src_streams=streams, preview_path=preview_path))
        i_cs = cmd.index("-c:s")
        assert cmd[i_cs:i_cs + 6] == ["-c:s", "copy", "-c:s:0", "srt", "-c:s:3", "srt"]
        assert "-c:s:1" not in cmd and "-c:s:2" not in cmd

    clip_cmd, _ = build_test_clip_command(s, "/tmp/clip.mkv", 10.0, 5)
    i_cs_clip = clip_cmd.index("-c:s")
    assert clip_cmd[i_cs_clip:i_cs_clip + 6] == ["-c:s", "copy", "-c:s:0", "srt", "-c:s:3", "srt"]


def test_mkv_non_mov_text_and_mp4_mov_unchanged():
    """Non-mov_text MKV (subrip+ass), MP4/MOV, WebM, and empty src_streams stay unchanged."""
    from hdr2sdr.core.commands import ffmpeg_track_codec_args
    # 1. MKV with subrip + ass -> no overrides
    s_mkv_text = settings(
        dst="/out/video.sdr.mkv",
        src_streams=((1, "audio", "aac"), (2, "subtitle", "subrip"), (3, "subtitle", "ass")),
    )
    plan_mkv = plan_tracks(s_mkv_text)
    assert plan_mkv.sub_overrides == ()
    assert plan_mkv.describe() == []
    assert ffmpeg_track_codec_args(s_mkv_text) == ["-c:a", "copy", "-c:s", "copy", "-max_interleave_delta", "0"]

    # 2. MP4 and MOV with mov_text + PGS -> text_subs -> mov_text, PGS skipped, no sub_overrides
    mixed = ((1, "audio", "aac"), (2, "subtitle", "mov_text"), (3, "subtitle", "hdmv_pgs_subtitle"))
    for ext in (".mp4", ".mov"):
        s_mp4 = settings(dst=f"/out/video.sdr{ext}", src_streams=mixed)
        plan_mp4 = plan_tracks(s_mp4)
        assert plan_mp4.skip == ((3, "subtitle", "hdmv_pgs_subtitle"),)
        assert plan_mp4.subtitle_codec == "mov_text"
        assert plan_mp4.sub_overrides == ()
        assert ffmpeg_track_codec_args(s_mp4) == ["-c:a", "copy", "-c:s", "mov_text", "-max_interleave_delta", "0"]

    # 3. Empty src_streams on MKV -> no overrides
    s_empty = settings(dst="/out/video.sdr.mkv", src_streams=())
    assert plan_tracks(s_empty).sub_overrides == ()
    assert ffmpeg_track_codec_args(s_empty) == ["-c:a", "copy", "-c:s", "copy", "-max_interleave_delta", "0"]


def test_mkv_six_mov_text_streams_main_preview_test_clip_and_resume_segment():
    """FIX_MKV_MOV_TEXT_SUBTITLES_2026-10-23:
    Exact reported scenario: MP4 source with 6 mov_text subtitle streams converted to MKV
    must keep global '-c:s copy' and override all 6 subtitle ordinals (-c:s:0 srt .. -c:s:5 srt)
    across main encode, live-preview encode, test clips, and resume segments, and expose
    the conversion in the activity log (including _test_clip_command(announce=True))."""
    from pathlib import Path
    from hdr2sdr.core.commands import ffmpeg_track_codec_args

    six_mov_text_streams = (
        (0, "video", "hevc"),
        (1, "audio", "eac3"),
        (2, "subtitle", "mov_text"),
        (3, "subtitle", "mov_text"),
        (4, "subtitle", "mov_text"),
        (5, "subtitle", "mov_text"),
        (6, "subtitle", "mov_text"),
        (7, "subtitle", "mov_text"),
    )
    expected_overrides = tuple((i, "srt") for i in range(6))
    expected_codec_tail = [
        "-c:a", "copy",
        "-c:s", "copy",
        "-c:s:0", "srt",
        "-c:s:1", "srt",
        "-c:s:2", "srt",
        "-c:s:3", "srt",
        "-c:s:4", "srt",
        "-c:s:5", "srt",
        "-max_interleave_delta", "0",
    ]

    # 1. Plan & log description
    s_main = settings(src="/in/movie.mp4", dst="/out/movie_SDR.mkv", src_streams=six_mov_text_streams)
    plan = plan_tracks(s_main)
    assert plan.skip == ()
    assert plan.subtitle_codec == "copy"
    assert plan.sub_overrides == expected_overrides
    desc = plan.describe()
    assert len(desc) == 6
    assert desc[0] == "subtitle stream #2 (mov_text) converted to SRT for Matroska"
    assert desc[5] == "subtitle stream #7 (mov_text) converted to SRT for Matroska"
    assert ffmpeg_track_codec_args(s_main) == expected_codec_tail

    # 2. Main encode and live-preview split encode
    for pv in (None, "/tmp/live_pv.jpg"):
        cmd = build_ffmpeg_command(
            settings(src="/in/movie.mp4", dst="/out/movie_SDR.mkv", src_streams=six_mov_text_streams, preview_path=pv)
        )
        idx = cmd.index("-c:a")
        assert cmd[idx:idx + len(expected_codec_tail)] == expected_codec_tail

    # 3. Resume segment (.resume-part2.mkv with resume_seek_sec > 0)
    s_resume = settings(
        src="/in/movie.mp4",
        dst="/out/movie_SDR.mkv",
        resume_output_override="/out/movie_SDR.resume-part2.mkv",
        resume_seek_sec=42.5,
        src_streams=six_mov_text_streams,
    )
    resume_cmd = build_ffmpeg_command(s_resume)
    assert "-ss" in resume_cmd and resume_cmd[resume_cmd.index("-ss") + 1] == "42.500"
    idx_res = resume_cmd.index("-c:a")
    assert resume_cmd[idx_res:idx_res + len(expected_codec_tail)] == expected_codec_tail
    assert resume_cmd[-1] == "/out/movie_SDR.resume-part2.mkv"

    # 4. Test clip (.mkv) and main_window._test_clip_command announce=True check
    clip_cmd, _ = build_test_clip_command(s_main, "/tmp/test_clip.mkv", 15.0, 5)
    idx_clip = clip_cmd.index("-c:a")
    assert clip_cmd[idx_clip:idx_clip + len(expected_codec_tail)] == expected_codec_tail

    mw_src = (Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "build_test_clip_command(self._settings_with_tracks(out_path, announce=True)" in mw_src
