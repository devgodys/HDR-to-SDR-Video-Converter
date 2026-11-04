"""Tests for hdr2sdr.core.commands - the PURE CORE command builders.

None of these import or start Qt. That is the point: before the original
migration, getting a command out of the app required constructing a
QApplication and a full MainWindow first. If any test below needs Qt, the
pure-core boundary has been broken somewhere and that is itself the bug
to fix. This is the same test suite as tests/test_command_builders.py in
the single-file layout, re-pointed at the package.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from hdr2sdr.core.commands import (
    ConversionSettings,
    build_brightness_args,
    build_colortemperature_args,
    build_command,
    build_container_args,
    build_encode_args,
    build_ffmpeg_command,
    build_handbrake_command,
    build_scale_args,
    build_test_clip_command,
    build_vf_chain,
    crop_sample_offsets,
    resolve_handbrake_encoder,
    select_crop_filter,
    settings_signature,
    tonemap_lookup,
)


def settings(**overrides):
    base = ConversionSettings(
        src="/videos/movie.mkv",
        dst="/videos/movie.sdr.mkv",
        cpu_count=8,
        handbrake_tool="/usr/bin/HandBrakeCLI",
    )
    return ConversionSettings(**{**base.__dict__, **overrides})


# ---------------------------------------------------------------------
# encode(): quality/bitrate mode and per-encoder-family option selection
# ---------------------------------------------------------------------

class TestBuildEncodeArgs:
    def test_cpu_h265_crf(self):
        e, opts = build_encode_args(settings(encoder_label="CPU \u00b7 H.265", quality=20))
        assert e == "libx265"
        assert opts == ["-crf", "20", "-preset", "medium"]

    def test_cpu_av1_uses_numeric_preset_not_named(self):
        e, opts = build_encode_args(settings(encoder_label="CPU \u00b7 AV1", quality=30))
        assert e == "libsvtav1"
        assert opts == ["-crf", "30", "-preset", "6"]

    def test_nvenc_uses_constqp_not_vbr_cq(self):
        """Regression guard for the documented nvenc vbr+cq flakiness: this
        must stay -rc constqp -qp, never -rc vbr -cq."""
        e, opts = build_encode_args(settings(encoder_label="NVIDIA NVENC \u00b7 H.265", quality=23))
        assert e == "hevc_nvenc"
        assert opts == ["-rc", "constqp", "-qp", "23"]

    def test_nvenc_qp_zero_is_not_remapped(self):
        """Unlike QSV, 0 is nvenc's real "automatic" sentinel is -1, so 0
        is already a legitimate value here and must pass through as-is."""
        e, opts = build_encode_args(settings(encoder_label="NVIDIA NVENC \u00b7 H.265", quality=0))
        assert opts == ["-rc", "constqp", "-qp", "0"]

    def test_amd_uses_cqp_with_both_i_and_p_qp(self):
        e, opts = build_encode_args(settings(encoder_label="AMD AMF \u00b7 H.265", quality=25))
        assert opts == ["-rc", "cqp", "-qp_i", "25", "-qp_p", "25"]

    def test_amf_av1_maps_ui_quality_to_av1_qpindex_range(self):
        cases = ((0, "1"), (25, "126"), (51, "255"))
        for quality, expected in cases:
            _encoder, opts = build_encode_args(settings(
                encoder_label="AMD AMF \u00b7 AV1", quality=quality))
            assert opts == ["-rc", "cqp", "-qp_i", expected, "-qp_p", expected]

    def test_qsv_quality_zero_is_remapped_to_one(self):
        """0 is out of QSV's valid 1-51 range and risks the option being
        silently ignored - must be remapped, unlike nvenc's qp."""
        e, opts = build_encode_args(settings(encoder_label="Intel QSV \u00b7 H.265", quality=0))
        assert opts == ["-q:v", "1"]

    def test_qsv_nonzero_quality_passes_through(self):
        e, opts = build_encode_args(settings(encoder_label="Intel QSV \u00b7 H.265", quality=25))
        assert opts == ["-q:v", "25"]

    def test_qsv_av1_uses_the_1_to_51_quality_scale(self):
        _encoder, opts = build_encode_args(settings(
            encoder_label="Intel QSV \u00b7 AV1", quality=25))
        assert opts == ["-q:v", "25"]

    def test_bitrate_mode_sets_cap_and_bufsize(self):
        e, opts = build_encode_args(settings(
            encoder_label="CPU \u00b7 H.265", quality_mode="Bitrate", target_bitrate_kbps=4000))
        assert opts[:6] == ["-b:v", "4000k", "-maxrate", "6000k", "-bufsize", "8000k"]

    def test_bitrate_mode_av1_gets_numeric_preset(self):
        e, opts = build_encode_args(settings(
            encoder_label="CPU \u00b7 AV1", quality_mode="Bitrate", target_bitrate_kbps=4000))
        assert "-preset" in opts and opts[opts.index("-preset") + 1] == "6"

    def test_threads_added_for_cpu_encoder_below_core_count(self):
        e, opts = build_encode_args(settings(encoder_label="CPU \u00b7 H.265", cores=4, cpu_count=8))
        assert opts[-2:] == ["-threads", "4"]

    def test_threads_omitted_at_zero_cores(self):
        e, opts = build_encode_args(settings(encoder_label="CPU \u00b7 H.265", cores=0, cpu_count=8))
        assert "-threads" not in opts

    def test_threads_omitted_when_cores_equals_cpu_count(self):
        """0 < cores < cpu_count is a strict upper bound: asking for
        every core is the same as not capping at all."""
        e, opts = build_encode_args(settings(encoder_label="CPU \u00b7 H.265", cores=8, cpu_count=8))
        assert "-threads" not in opts

    def test_threads_not_added_for_hardware_encoder(self):
        e, opts = build_encode_args(settings(encoder_label="NVIDIA NVENC \u00b7 H.265", cores=4, cpu_count=8))
        assert "-threads" not in opts


# ---------------------------------------------------------------------
# build_vf_chain(): filter ordering and the libplacebo/zscale split
# ---------------------------------------------------------------------

class TestBuildVfChain:
    def test_gpu_engine_sets_dv_and_peak_detect_flags_explicitly(self):
        vf = build_vf_chain(settings(tonemap_method="BT.2390 \u00b7 GPU tonemap",
                                     dv_rpu=False, peak_detect=True))
        assert "apply_dolbyvision=0" in vf
        assert "peak_detect=1" in vf

    def test_cpu_engine_uses_zscale_tonemap_chain(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap"))
        assert vf.startswith("zscale=t=linear")
        assert "tonemap=tonemap=hable" in vf

    def test_denoise_applied_before_tonemap(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap", denoise="Medium"))
        assert vf.index("hqdn3d") < vf.index("zscale")

    def test_crop_applied_first(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap",
                                     autocrop_value="crop=1920:800:0:140"))
        assert vf.startswith("crop=1920:800:0:140,")

    def test_resharpen_applied_after_scale(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap",
                                     resolution_label="1080p", resharpen=True))
        assert vf.index("scale=") < vf.index("unsharp")

    def test_pix_fmt_propagates_into_filter(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap"), fmt="yuv420p10le")
        assert "format=yuv420p10le" in vf
        assert vf.count("format=yuv420p10le") == 1

    def test_brightness_and_saturation_appended_last(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap",
                                     brightness=5, saturation=10))
        assert vf.endswith(build_brightness_args(settings(brightness=5, saturation=10)))

    def test_default_desat_matches_previous_hardcoded_value(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap"))
        assert "tonemap=tonemap=hable:desat=0" in vf

    def test_desat_passed_through_on_cpu_engine(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap", desat=0.5))
        assert "tonemap=tonemap=hable:desat=0.5" in vf

    def test_colortemperature_default_appends_nothing(self):
        assert build_colortemperature_args(settings()) == ""
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap"))
        assert "colortemperature" not in vf

    def test_colortemperature_appended_after_brightness(self):
        vf = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap",
                                     brightness=5, colortemperature=5800))
        assert vf.endswith(",colortemperature=temperature=5800")

    def test_colortemperature_works_on_both_engines(self):
        for method in ("BT.2390 \u00b7 GPU tonemap", "Hable \u00b7 CPU tonemap"):
            vf = build_vf_chain(settings(tonemap_method=method, colortemperature=5000))
            assert "colortemperature=temperature=5000" in vf


# ---------------------------------------------------------------------
# ffmpeg command assembly
# ---------------------------------------------------------------------

class TestBuildFfmpegCommand:
    def test_no_preview_uses_plain_single_output_form(self):
        cmd = build_ffmpeg_command(settings())
        assert "-filter_complex" not in cmd
        assert "-map" in cmd and "0:v:0" in cmd

    def test_preview_splits_into_filter_complex_with_two_outputs(self):
        cmd = build_ffmpeg_command(settings(preview_path="/tmp/preview.jpg"))
        assert "-filter_complex" in cmd
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "split=2[enc][pv]" in fc
        assert "format=yuvj420p[pvout]" in fc

    def test_resume_seek_placed_before_input(self):
        cmd = build_ffmpeg_command(settings(resume_seek_sec=123.456))
        i_idx = cmd.index("-i")
        ss_idx = cmd.index("-ss")
        assert ss_idx < i_idx
        assert cmd[ss_idx + 1] == "123.456"

    def test_def_0011_resume_segment_resets_output_timestamps(self):
        cmd = build_ffmpeg_command(settings(src="/tmp/src.mkv", resume_seek_sec=20.0, resume_output_override="/tmp/part2.mkv"))
        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == "/tmp/src.mkv"
        assert cmd[i_idx + 2] == "-ss"
        assert cmd[i_idx + 3] == "0"

    def test_resume_output_override_used_as_destination(self):
        cmd = build_ffmpeg_command(settings(dst="/out/full.mkv",
                                            resume_output_override="/out/part2.mkv"))
        assert cmd[-1] == "/out/part2.mkv"

    def test_mp4_gets_faststart_mkv_does_not(self):
        mp4 = build_ffmpeg_command(settings(dst="/out/movie.mp4"))
        mkv = build_ffmpeg_command(settings(dst="/out/movie.mkv"))
        assert "+faststart" in mp4
        assert "+faststart" not in mkv

    def test_track_and_codec_args_present(self):
        cmd = build_ffmpeg_command(settings())
        assert "-map" in cmd and "0:a?" in cmd and "0:s?" in cmd
        assert "-max_interleave_delta" in cmd
        assert cmd[cmd.index("-max_interleave_delta") + 1] == "0"


class TestBuildContainerArgs:
    @pytest.mark.parametrize("ext", [".mp4", ".mov", ".m4v", ".MP4"])
    def test_faststart_for_mp4_family(self, ext):
        assert build_container_args(f"/out/movie{ext}") == ["-movflags", "+faststart"]

    @pytest.mark.parametrize("ext", [".mkv", ".webm", ".avi"])
    def test_no_faststart_elsewhere(self, ext):
        assert build_container_args(f"/out/movie{ext}") == []


class TestBuildScaleArgs:
    def test_source_resolution_no_scale(self):
        assert build_scale_args(settings(resolution_label="Source (no scaling)")) == ""

    def test_scale_forces_even_height_and_lanczos(self):
        args = build_scale_args(settings(resolution_label="1080p"))
        assert args == ",scale=-2:1080:flags=lanczos"


# ---------------------------------------------------------------------
# HandBrake command assembly
# ---------------------------------------------------------------------

class TestBuildHandbrakeCommand:
    def test_bitrate_mode_uses_multi_pass_turbo(self):
        cmd, warnings = build_handbrake_command(settings(
            backend="HandBrake", quality_mode="Bitrate", target_bitrate_kbps=3000))
        assert "--multi-pass" in cmd and "--turbo" in cmd
        assert "-b" in cmd and cmd[cmd.index("-b") + 1] == "3000"
        assert not warnings

    def test_quality_mode_uses_q_flag(self):
        cmd, warnings = build_handbrake_command(settings(backend="HandBrake", quality=22))
        assert "-q" in cmd and cmd[cmd.index("-q") + 1] == "22"

    def test_crop_mode_reflects_autocrop_toggle(self):
        on, _ = build_handbrake_command(settings(backend="HandBrake", autocrop=True))
        off, _ = build_handbrake_command(settings(backend="HandBrake", autocrop=False))
        assert on[on.index("--crop-mode") + 1] == "auto"
        assert off[off.index("--crop-mode") + 1] == "none"

    def test_hwaccel_decode_only_added_for_nvenc(self):
        nvenc, _ = build_handbrake_command(settings(
            backend="HandBrake", encoder_label="NVIDIA NVENC \u00b7 H.265", hwaccel=True))
        cpu, _ = build_handbrake_command(settings(
            backend="HandBrake", encoder_label="CPU \u00b7 H.265", hwaccel=True))
        assert "--enable-hw-decoding" in nvenc
        assert "--enable-hw-decoding" not in cpu

    def test_resolution_uses_dash_y_only(self):
        cmd, _ = build_handbrake_command(settings(backend="HandBrake", resolution_label="1080p"))
        assert "-Y" in cmd and cmd[cmd.index("-Y") + 1] == "1080"
        assert "-X" not in cmd

    def test_def_0012_handbrake_x264_10bit_uses_high10_profile(self):
        cmd, _ = build_handbrake_command(settings(
            backend="HandBrake", encoder_label="CPU \u00b7 H.264", bit_depth_label="10-bit"))
        assert "--encoder-profile" in cmd
        assert cmd[cmd.index("--encoder-profile") + 1] == "high10"

    def test_handbrake_x265_10bit_uses_main10_profile(self):
        cmd, _ = build_handbrake_command(settings(
            backend="HandBrake", encoder_label="CPU \u00b7 H.265", bit_depth_label="10-bit"))
        assert "--encoder-profile" in cmd
        assert cmd[cmd.index("--encoder-profile") + 1] == "main10"


class TestResolveHandbrakeEncoder:
    def test_known_combo_has_no_warning(self):
        e, ids, warnings = resolve_handbrake_encoder("CPU \u00b7 H.265", "10-bit")
        assert e is not None
        assert not warnings

    def test_unknown_depth_falls_back_to_8bit_with_warning(self):
        e, ids, warnings = resolve_handbrake_encoder("CPU \u00b7 H.265", "not-a-real-depth")
        assert e == ids.get("8-bit (SDR standard)")
        assert warnings
        assert "falling back to 8-bit" in warnings[0]

    def test_no_configured_encoder_fails_explicitly(self):
        with pytest.raises(ValueError, match="No HandBrake encoder is configured"):
            resolve_handbrake_encoder("unknown encoder", "12-bit")


# ---------------------------------------------------------------------
# build_command(): backend dispatch
# ---------------------------------------------------------------------

class TestBuildCommand:
    def test_ffmpeg_backend_dispatches_to_ffmpeg_builder(self):
        cmd, warnings = build_command(settings(backend="FFmpeg"))
        assert Path(cmd[0]).name.lower() in ("ffmpeg", "ffmpeg.exe")

    def test_handbrake_backend_dispatches_to_handbrake_builder(self):
        cmd, warnings = build_command(settings(backend="HandBrake"))
        assert cmd[0] == "/usr/bin/HandBrakeCLI"


# ---------------------------------------------------------------------
# Test-clip command: must track the real command, not drift from it
# ---------------------------------------------------------------------

class TestBuildTestClipCommand:
    def test_ffmpeg_clip_uses_ss_and_t_for_trim(self):
        cmd, _ = build_test_clip_command(settings(), Path("/tmp/clip.mkv"), seek=12.5, dur=5)
        assert cmd[cmd.index("-ss") + 1] == "12.50"
        assert cmd[cmd.index("-t") + 1] == "5"

    def test_def_0005_test_clip_resets_output_timestamps(self):
        cmd, _ = build_test_clip_command(settings(src="/tmp/in.mkv"), Path("/tmp/clip.mkv"), seek=10.0, dur=4)
        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == "/tmp/in.mkv"
        assert cmd[i_idx + 2] == "-ss"
        assert cmd[i_idx + 3] == "0"
        assert cmd[cmd.index("-t") + 1] == "4"

    def test_handbrake_clip_drops_two_pass_but_keeps_filters(self):
        real, _ = build_handbrake_command(settings(
            backend="HandBrake", deband=True, resharpen=True, autocrop=True,
            denoise="Medium", quality_mode="Bitrate", target_bitrate_kbps=3000))
        clip, _ = build_test_clip_command(
            settings(backend="HandBrake", deband=True, resharpen=True, autocrop=True,
                    denoise="Medium", quality_mode="Bitrate", target_bitrate_kbps=3000),
            Path("/tmp/clip.mkv"), seek=12.0, dur=7)
        assert "--filter" not in clip and "deband" not in clip
        assert "--unsharp=light" in clip
        assert clip[clip.index("--crop-mode") + 1] == "auto"
        assert "--two-pass" not in clip and "--multi-pass" not in clip
        assert "--turbo" not in clip
        assert "--start-at" in clip and "--stop-at" in clip

    def test_handbrake_clip_writes_to_clip_path_not_real_dst(self):
        cmd, _ = build_test_clip_command(
            settings(backend="HandBrake", dst="/out/real.mkv"),
            Path("/tmp/clip.mkv"), seek=1.0, dur=5)
        assert str(Path("/tmp/clip.mkv")) in cmd
        assert "/out/real.mkv" not in cmd


def test_tonemap_lookup_unknown_falls_back_to_hable_cpu():
    assert tonemap_lookup("not a real method") == ("cpu", "hable")


def test_tonemap_lookup_known_gpu_curve():
    assert tonemap_lookup("BT.2390 \u00b7 GPU tonemap") == ("gpu", "bt.2390")


def test_settings_signature_uses_versioned_whitelist_and_ignores_runtime_fields():
    base = settings(cores=2, cpu_count=8, hwaccel=True, handbrake_tool="/a/HandBrakeCLI")
    runtime_changed = settings(
        src="/other/in.mkv", dst="/other/out.mkv", preview_path="/tmp/pv.jpg",
        resume_seek_sec=42.0, resume_output_override="/tmp/part2.mkv",
        autocrop_value="crop=1920:800:0:140", cores=6, cpu_count=16,
        hwaccel=False, handbrake_tool="/b/HandBrakeCLI",
        src_streams=((1, "audio", "aac"),),
    )
    assert settings_signature(base).startswith("v2:")
    assert settings_signature(base) == settings_signature(runtime_changed)
    assert settings_signature(base) != settings_signature(settings(quality=24))


def test_crop_sample_offsets_and_bounding_union_selection():
    assert crop_sample_offsets(10.0) == [(2.5, 5.0)]
    assert len(crop_sample_offsets(100.0)) == 3
    # A dark middle sample with a narrow box must not crop out content seen at 20%/80%.
    samples = [
        "[Parsed_cropdetect_0] x1:0 x2:1919 y1:140 y2:939 w:1920 h:800 x:0 y:140 crop=1920:800:0:140\n",
        "[Parsed_cropdetect_0] crop=-2:-2:0:0\ncrop=600:400:660:340\n",
        "[Parsed_cropdetect_0] crop=1920:800:0:140\n",
    ]
    assert select_crop_filter(samples, 1920, 1080) == "crop=1920:800:0:140"
    # Full-frame detection is a no-op and returns empty string when source dimensions match.
    assert select_crop_filter(["crop=1920:1080:0:0"], 1920, 1080) == ""



def test_gamut_mapping_modes_in_gpu_and_cpu_chains_and_signature_v2():
    """P1-2: Gamut mapping (Perceptual, Relative, Clip) in libplacebo vf chain,
    omitted for Perceptual and CPU chains, included in v2 settings_signature,
    and v1 unfinished sidecars do not auto-resume silently."""
    from hdr2sdr.core.commands import SETTINGS_SIGNATURE_VERSION, gamut_mode_lookup
    from hdr2sdr.runtime.resume import ResumeAction, ResumeCoordinator

    assert SETTINGS_SIGNATURE_VERSION == 2
    assert gamut_mode_lookup("Perceptual") == "perceptual"
    assert gamut_mode_lookup("Relative") == "relative"
    assert gamut_mode_lookup("Clip") == "clip"
    assert gamut_mode_lookup("") == "perceptual"

    # 1. GPU tonemap: Perceptual omits gamut_mode; Relative & Clip include it
    vf_perc = build_vf_chain(settings(tonemap_method="BT.2390 \u00b7 GPU tonemap", gamut_mode="Perceptual"))
    assert "gamut_mode=" not in vf_perc

    vf_rel = build_vf_chain(settings(tonemap_method="BT.2390 \u00b7 GPU tonemap", gamut_mode="Relative"))
    assert ":gamut_mode=relative" in vf_rel

    vf_clip = build_vf_chain(settings(tonemap_method="BT.2390 \u00b7 GPU tonemap", gamut_mode="Clip"))
    assert ":gamut_mode=clip" in vf_clip

    # 2. CPU tonemap: never includes gamut_mode even if gamut_mode is Relative or Clip
    for mode in ("Perceptual", "Relative", "Clip"):
        vf_cpu = build_vf_chain(settings(tonemap_method="Hable \u00b7 CPU tonemap", gamut_mode=mode))
        assert "gamut_mode" not in vf_cpu

    # 3. Signature v2 includes gamut_mode and normalizes case
    s_perc = settings(gamut_mode="Perceptual")
    s_perc_low = settings(gamut_mode="perceptual")
    s_rel = settings(gamut_mode="Relative")
    s_clip = settings(gamut_mode="Clip")
    sig_perc = settings_signature(s_perc)
    assert sig_perc.startswith("v2:")
    assert sig_perc == settings_signature(s_perc_low)
    assert sig_perc != settings_signature(s_rel)
    assert settings_signature(s_rel) != settings_signature(s_clip)

    # 4. Legacy v1 sidecar does not crash and never silently auto-resumes
    fp = ["/in/video.mkv", 1000, 12345]
    v1_meta = {"settings": "v1:0123456789abcdef", "source": fp}
    assessment = ResumeCoordinator.assess_orphan(
        "/out/video.part.mkv", 180.0, 600.0, v1_meta, sig_perc, fp
    )
    assert assessment.same_source is True
    assert assessment.same_settings is False
    assert assessment.matches is False
    assert ResumeCoordinator.orphan_action(assessment, unattended=True) == ResumeAction.ORPHAN_START_OVER
    assert ResumeCoordinator.orphan_action(assessment, unattended=False) == ResumeAction.ORPHAN_ASK


def test_def_0002_svtav1_bitrate_mode_no_maxrate():
    from hdr2sdr.core.commands import build_encode_args, ConversionSettings
    s = ConversionSettings(encoder_label="CPU · AV1", quality_mode="Bitrate", target_bitrate_kbps=2000)
    enc, opts = build_encode_args(s)
    assert enc == "libsvtav1"
    assert "-b:v" in opts and "2000k" in opts
    assert "-maxrate" not in opts
    assert "-bufsize" not in opts


def test_def_0003_cropdetect_filter_normalizes_to_8bit_yuv420p():
    from hdr2sdr.core.commands import CROPDETECT_FILTER
    assert CROPDETECT_FILTER.startswith("format=yuv420p,cropdetect=")


def test_def_0004_handbrake_bitrate_mode_multi_pass():
    from hdr2sdr.core.commands import build_handbrake_command
    cmd, warnings = build_handbrake_command(settings(
        backend="HandBrake", quality_mode="Bitrate", target_bitrate_kbps=3000))
    assert "--multi-pass" in cmd
    assert "--two-pass" not in cmd
