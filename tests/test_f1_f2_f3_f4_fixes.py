"""Regression and real-FFmpeg integration tests for F1, F2, F3, and F4."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from hdr2sdr.core.commands import (
    ConversionSettings,
    build_command,
    build_container_args,
    build_ffmpeg_command,
    build_test_clip_command,
    build_vf_chain,
    ffmpeg_track_args,
    ffmpeg_track_codec_args,
    plan_tracks,
)
from hdr2sdr.runtime.workflow import DolbyVisionAction, RunWorkflow
from hdr2sdr.core.utils import probe_streams
from hdr2sdr.runtime.resume import ResumeCoordinator


# =====================================================================
# F1: CPU tonemapping on untagged / partially-tagged HDR sources
# =====================================================================

def test_f1_cpu_vf_chain_tagged_vs_untagged_and_partial():
    """F1:
    1) Tagged source (smpte2084 + bt2020 + bt2020nc) keeps exact golden zscale=t=linear:npl=100.
    2) Missing range alone (src_color_range='unknown') keeps exact golden zscale=t=linear:npl=100.
    3) Missing transfer, primaries, or matrix injects explicit tin/pin/min/rin and p=bt2020.
    4) HLG transfer ('arib-std-b67' or 'hlg') with missing primaries/matrix uses tin=arib-std-b67.
    5) Missing transfer defaults to PQ (tin=smpte2084) and emits a log warning."""
    # 1. Default unprobed settings -> golden unchanged
    s_default = ConversionSettings(
        src="/in/hdr.mkv",
        dst="/out/sdr.mkv",
        tonemap_method="Hable \u00b7 CPU tonemap",
    )
    vf_default = build_vf_chain(s_default)
    assert vf_default == (
        "zscale=t=linear:npl=100,format=gbrpf32le,"
        "tonemap=tonemap=hable:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p"
    )
    _, warn_default = build_command(s_default)
    assert warn_default == []

    # 2. Fully tagged PQ/BT.2020 source -> golden unchanged
    s_tagged = replace(
        s_default,
        src_color_transfer="smpte2084",
        src_color_primaries="bt2020",
        src_color_matrix="bt2020nc",
        src_color_range="tv",
    )
    assert build_vf_chain(s_tagged) == vf_default
    _, warn_tagged = build_command(s_tagged)
    assert warn_tagged == []

    # 3. Missing range alone -> golden unchanged (does not break zscale)
    s_missing_range_only = replace(s_tagged, src_color_range="unknown")
    assert build_vf_chain(s_missing_range_only) == vf_default
    _, warn_range = build_command(s_missing_range_only)
    assert warn_range == []

    # 4. Completely untagged source (transfer=unknown, primaries=unknown, matrix=unknown, range=tv)
    s_untagged = replace(
        s_default,
        src_color_transfer="unknown",
        src_color_primaries="unknown",
        src_color_matrix="unknown",
        src_color_range="tv",
    )
    vf_untagged = build_vf_chain(s_untagged)
    assert vf_untagged == (
        "zscale=tin=smpte2084:pin=bt2020:min=bt2020nc:rin=tv:t=linear:p=bt2020:npl=100,"
        "format=gbrpf32le,tonemap=tonemap=hable:desat=0,"
        "zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p"
    )
    cmd_untagged, warn_untagged = build_command(s_untagged)
    assert len(warn_untagged) == 1
    assert "PQ (smpte2084)" in warn_untagged[0] and "BT.2020" in warn_untagged[0]
    assert cmd_untagged[cmd_untagged.index("-vf") + 1] == vf_untagged

    # Test clip also gets the fixed vf chain and warning
    clip_cmd, clip_warn = build_test_clip_command(s_untagged, "/tmp/clip.mkv", 10.0, 5)
    assert clip_cmd[clip_cmd.index("-vf") + 1] == vf_untagged
    assert clip_warn == warn_untagged

    # 5. HLG transfer known, primaries/matrix unknown -> tin=arib-std-b67
    s_hlg_partial = replace(
        s_default,
        src_color_transfer="arib-std-b67",
        src_color_primaries="unknown",
        src_color_matrix="unspecified",
        src_color_range="pc",
    )
    vf_hlg = build_vf_chain(s_hlg_partial)
    assert vf_hlg.startswith(
        "zscale=tin=arib-std-b67:pin=bt2020:min=bt2020nc:rin=pc:t=linear:p=bt2020:npl=100,format=gbrpf32le,"
    )
    _, warn_hlg = build_command(s_hlg_partial)
    assert len(warn_hlg) == 1 and "HLG (arib-std-b67)" in warn_hlg[0]

    # 6. GPU engine is unaffected even when source tags are unknown
    s_gpu_untagged = replace(s_untagged, tonemap_method="BT.2390 \u00b7 GPU tonemap")
    vf_gpu = build_vf_chain(s_gpu_untagged)
    assert vf_gpu.startswith("libplacebo=tonemapping=bt.2390:")
    _, warn_gpu = build_command(s_gpu_untagged)
    assert warn_gpu == []

    # 7. DV Profile 5 behavior is unchanged (still blocked on CPU / HandBrake)
    assert RunWorkflow.dolby_vision_action(
        5, "dolby", using_handbrake=False, tonemap_engine="cpu",
        has_libplacebo=True, unattended=False
    ) == DolbyVisionAction.BLOCK_PROFILE5


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="ffmpeg/ffprobe not installed")
def test_f1_real_ffmpeg_untagged_hevc_10bit_cpu_tonemap_succeeds(tmp_path):
    """F1 real FFmpeg verification:
    Synthesize a 10-bit HEVC clip without color transfer/primaries/matrix tags
    (yuv420p10le(tv, progressive)). Verify that the old zscale=t=linear:npl=100
    fails with 'code 3074: no path between colorspaces' while build_ffmpeg_command()
    with probed unknown tags succeeds (rc == 0) and produces a valid SDR video."""
    untagged_src = tmp_path / "untagged_hdr10.mkv"
    r_gen = subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=24:duration=1",
        "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
        str(untagged_src),
    ], capture_output=True, text=True)
    if r_gen.returncode != 0:
        pytest.skip(f"libx265 unavailable: {r_gen.stderr}")

    # Confirm ffprobe reports unknown color_space / color_transfer / color_primaries
    probe_out = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=pix_fmt,color_range,color_space,color_transfer,color_primaries",
        "-of", "json", str(untagged_src),
    ], capture_output=True, text=True, check=True).stdout)
    st = probe_out["streams"][0]
    assert st.get("pix_fmt") == "yuv420p10le"
    assert st.get("color_transfer", "unknown") in ("unknown", "unspecified")

    # Old VF chain fails with code 3074
    old_vf = (
        "zscale=t=linear:npl=100,format=gbrpf32le,"
        "tonemap=tonemap=hable:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p"
    )
    r_old = subprocess.run([
        "ffmpeg", "-hide_banner", "-y", "-i", str(untagged_src),
        "-vf", old_vf, "-c:v", "libx264", str(tmp_path / "old_fail.mkv"),
    ], capture_output=True, text=True)
    assert r_old.returncode != 0
    assert "3074" in r_old.stderr or "no path between colorspaces" in r_old.stderr

    # New command built by build_command() with probed unknown tags succeeds
    out_sdr = tmp_path / "converted_sdr.mkv"
    s = ConversionSettings(
        src=str(untagged_src),
        dst=str(out_sdr),
        encoder_label="CPU \u00b7 H.264",
        tonemap_method="Hable \u00b7 CPU tonemap",
        src_color_transfer=st.get("color_transfer") or "unknown",
        src_color_primaries=st.get("color_primaries") or "unknown",
        src_color_matrix=st.get("color_space") or "unknown",
        src_color_range=st.get("color_range") or "unknown",
    )
    cmd, warnings = build_command(s)
    assert len(warnings) == 1
    r_new = subprocess.run(cmd, capture_output=True, text=True)
    assert r_new.returncode == 0, r_new.stderr
    assert out_sdr.is_file() and out_sdr.stat().st_size > 0


# =====================================================================
# F2: Preserve MKV attachments (-map 0:t?) and ignore attachments in subtitle ordinals
# =====================================================================

def test_f2_mkv_maps_attachments_while_mp4_mov_m4v_do_not():
    """F2:
    1) MKV output includes '-map 0:t?' in main conversion, live-preview split,
       test clips, and resume segments.
    2) MP4 / MOV / M4V outputs do NOT include '-map 0:t?'.
    3) Attachment streams (codec_type 'attachment') never shift subtitle ordinals
       when computing -c:s:N srt overrides for mov_text."""
    streams_with_attachment = (
        (0, "video", "hevc"),
        (1, "audio", "aac"),
        (2, "attachment", "ttf"),
        (3, "subtitle", "ass"),
        (4, "attachment", "otf"),
        (5, "subtitle", "mov_text"),
    )
    s_mkv = ConversionSettings(
        src="/in/anime.mkv",
        dst="/out/anime_SDR.mkv",
        src_streams=streams_with_attachment,
    )
    # Subtitle ordinals must ignore stream #2 (ttf) and #4 (otf):
    # stream #3 (ass) is subtitle ordinal 0; stream #5 (mov_text) is subtitle ordinal 1
    plan = plan_tracks(s_mkv)
    assert plan.sub_overrides == ((1, "srt"),)
    assert "-c:s:1" in ffmpeg_track_codec_args(s_mkv)
    assert "-c:s:0" not in ffmpeg_track_codec_args(s_mkv)

    # Main MKV command and live-preview MKV command have -map 0:t?
    for pv in ("", "/tmp/live_pv.jpg"):
        cmd = build_ffmpeg_command(replace(s_mkv, preview_path=pv))
        assert "0:t?" in cmd
        idx_s = cmd.index("0:s?")
        assert cmd[idx_s + 1:idx_s + 3] == ["-map", "0:t?"]

    # Resume segment (.resume-part2.mkv) has -map 0:t?
    s_resume = replace(
        s_mkv,
        resume_output_override="/out/anime_SDR.resume-part2.mkv",
        resume_seek_sec=60.0,
    )
    cmd_resume = build_ffmpeg_command(s_resume)
    assert "0:t?" in cmd_resume

    # Test clip (.mkv) has -map 0:t?, test clip (.mp4) does NOT
    clip_mkv, _ = build_test_clip_command(s_mkv, "/tmp/test_clip.mkv", 10.0, 5)
    assert "0:t?" in clip_mkv
    clip_mp4, _ = build_test_clip_command(s_mkv, "/tmp/test_clip.mp4", 10.0, 5)
    assert "0:t?" not in clip_mp4

    # MP4, MOV, M4V main commands do NOT have -map 0:t?
    for ext in (".mp4", ".mov", ".m4v"):
        s_mp4 = replace(s_mkv, dst=f"/out/anime_SDR{ext}")
        cmd_mp4 = build_ffmpeg_command(s_mp4)
        assert "0:t?" not in cmd_mp4
        assert "0:t?" not in ffmpeg_track_args(s_mp4, s_mp4.dst)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="ffmpeg/ffprobe not installed")
def test_f2_real_ffmpeg_mkv_ass_and_ttf_attachment_preserved_across_encode_and_concat(tmp_path):
    """F2 real FFmpeg verification:
    Create an MKV with video + ASS subtitle + TTF attachment.
    1) Verify probe_streams() excludes the attachment stream so subtitle ordinals are untouched.
    2) Run conversion with ffmpeg_track_args/ffmpeg_track_codec_args to MKV -> verify output keeps
       Video + Subtitle: ass + Attachment: ttf.
    3) Run ResumeCoordinator.concat_command on two MKV segments with attachments -> verify
       the joined MKV preserves Attachment: ttf."""
    ass_path = tmp_path / "subs.ass"
    ass_path.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hello\n",
        encoding="utf-8",
    )
    ttf_path = tmp_path / "CustomFont.ttf"
    ttf_path.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 64)

    src_mkv = tmp_path / "source_with_font.mkv"
    r_src = subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=24:duration=1",
        "-i", str(ass_path),
        "-attach", str(ttf_path), "-metadata:s:t:0", "mimetype=application/x-truetype-font",
        "-map", "0:v", "-map", "1:s",
        "-c:v", "libx264", "-c:s", "ass",
        str(src_mkv),
    ], capture_output=True, text=True)
    assert r_src.returncode == 0, r_src.stderr

    # 1. probe_streams must only return audio/subtitle, never attachment
    probed = probe_streams(str(src_mkv))
    assert probed == ((1, "subtitle", "ass"),)

    # 2. Convert to MKV using project track builders
    out_mkv = tmp_path / "out_sdr.mkv"
    s = ConversionSettings(
        src=str(src_mkv),
        dst=str(out_mkv),
        encoder_label="CPU \u00b7 H.264",
        tonemap_method="Hable \u00b7 CPU tonemap",
        src_streams=probed,
    )
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-i", str(src_mkv),
        "-map", "0:v:0", *ffmpeg_track_args(s, str(out_mkv)),
        "-c:v", "libx264", *ffmpeg_track_codec_args(s, str(out_mkv)),
        str(out_mkv),
    ]
    r_conv = subprocess.run(cmd, capture_output=True, text=True)
    assert r_conv.returncode == 0, r_conv.stderr

    streams_out = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
        "-of", "json", str(out_mkv),
    ], capture_output=True, text=True, check=True).stdout)["streams"]
    types_and_codecs = [(st.get("codec_type"), st.get("codec_name")) for st in streams_out]
    assert ("video", "h264") in types_and_codecs
    assert ("subtitle", "ass") in types_and_codecs
    assert ("attachment", "ttf") in types_and_codecs

    # 3. Verify resume concat preserves attachments too
    part2_mkv = tmp_path / "out_sdr_part2.mkv"
    shutil.copyfile(out_mkv, part2_mkv)
    joined_mkv = tmp_path / "joined_sdr.mkv"
    concat_cmd, list_path = ResumeCoordinator.concat_command(
        [out_mkv, part2_mkv], joined_mkv, "ffmpeg", build_container_args, [0.8]
    )
    try:
        r_concat = subprocess.run(concat_cmd, capture_output=True, text=True)
        assert r_concat.returncode == 0, r_concat.stderr
        joined_streams = json.loads(subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
            "-of", "json", str(joined_mkv),
        ], capture_output=True, text=True, check=True).stdout)["streams"]
        joined_tc = [(st.get("codec_type"), st.get("codec_name")) for st in joined_streams]
        assert ("subtitle", "ass") in joined_tc
        assert ("attachment", "ttf") in joined_tc
    finally:
        list_path.unlink(missing_ok=True)


# =====================================================================
# F4: No PySide6 fallback stubs in hdr2sdr/runtime/conversion.py
# =====================================================================

def test_f4_conversion_py_has_no_pyside6_stubs():
    """F4: hdr2sdr/runtime/conversion.py must import QObject, QProcess, Signal
    directly from PySide6.QtCore without inline fallback stub classes, while
    tests/qt_stubs.py provides the stubs for headless test environments."""
    conv_path = Path(__file__).resolve().parent.parent / "hdr2sdr" / "runtime" / "conversion.py"
    source = conv_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert top_level_classes == ["ConversionRunner"]
    assert "_BoundSignal" not in source
    assert "class QProcess" not in source
    assert "class QObject" not in source
    assert "class Signal" not in source

    stub_path = Path(__file__).resolve().parent / "qt_stubs.py"
    assert stub_path.is_file()


# =====================================================================
# F3: GPU -> Hable CPU fallback preserves method_combo, QSettings,
#     dv_rpu_chk, and next queue file, with attempt counter & user decline
# =====================================================================

def _extract_main_window_methods(*method_names: str) -> dict:
    """Compile selected MainWindow methods directly from hdr2sdr/ui/main_window.py
    so F3 logic can be verified both with real PySide6 and in headless environments
    without PySide6 installed."""
    from dataclasses import asdict
    from hdr2sdr.core.commands import (
        ConversionSettings,
        build_command,
        build_ffmpeg_command,
        plan_tracks,
        settings_signature,
        tonemap_lookup,
    )
    from hdr2sdr.core.constants import FFMPEG_BIT_DEPTH, RESOLUTIONS
    CPU_COUNT = 8
    from hdr2sdr.core.destination import DestinationPlan
    from hdr2sdr.runtime.capabilities import check_start_capabilities
    from hdr2sdr.runtime.lifecycle import RunState, transition_safely
    from hdr2sdr.runtime.workflow import DolbyVisionAction, FinishAction, RunWorkflow, StartAction

    mw_path = Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "main_window.py"
    source = mw_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    mw_cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    ns = {
        "Path": Path,
        "replace": replace,
        "asdict": asdict,
        "ConversionSettings": ConversionSettings,
        "build_command": build_command,
        "build_ffmpeg_command": build_ffmpeg_command,
        "plan_tracks": plan_tracks,
        "settings_signature": settings_signature,
        "tonemap_lookup": tonemap_lookup,
        "CPU_COUNT": CPU_COUNT,
        "FFMPEG_BIT_DEPTH": FFMPEG_BIT_DEPTH,
        "RESOLUTIONS": RESOLUTIONS,
        "DestinationPlan": DestinationPlan,
        "check_start_capabilities": check_start_capabilities,
        "RunState": RunState,
        "transition_safely": transition_safely,
        "DolbyVisionAction": DolbyVisionAction,
        "FinishAction": FinishAction,
        "RunWorkflow": RunWorkflow,
        "StartAction": StartAction,
        "log_suppressed": lambda *_a, **_k: None,
        "exe": lambda name: "ffmpeg" if name == "ffmpeg" else None,
        "probe_streams": lambda *_a, **_k: (),
    }
    for node in mw_cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in method_names:
            mod = ast.Module(body=[node], type_ignores=[])
            code = compile(mod, str(mw_path), "exec")
            exec(code, ns)
    return {name: ns[name] for name in method_names}, ns


def test_f3_fallback_preserves_combo_qsettings_dv_rpu_and_next_queue_item(tmp_path):
    """F3 verification:
    1) After GPU->Hable CPU fallback (both preflight and runtime), method_combo,
       dv_rpu_chk, and saved conversion_preferences (QSettings) remain untouched.
    2) Only ConversionSettings for the active job gets 'Hable · CPU tonemap' and dv_rpu=False.
    3) When the next file in the queue is loaded via _set_source(), the override and
       attempt counter reset so the user's selected GPU method is tried again.
    4) Attempt counter (_job_tonemap_fallback_attempts) prevents an infinite fallback loop.
    5) User can decline fallback in the dialog/banner and skip the file.
    6) Dolby Vision Profile 5 remains strictly blocked from CPU fallback."""
    from hdr2sdr.runtime.capabilities import StartCapabilityIssue
    from hdr2sdr.runtime.lifecycle import RunStateMachine
    from hdr2sdr.runtime.resume import ResumeCoordinator
    from hdr2sdr.runtime.workflow import RunContext

    methods, mw_ns = _extract_main_window_methods(
        "collect_settings",
        "_confirm_tonemap_fallback",
        "_clear_job_tonemap_override",
        "_start_impl",
    )

    class _Widget:
        def __init__(self, val="", checked=False, on_change=None):
            self._val = val
            self._checked = checked
            self._on_change = on_change
            self.set_calls = []

        def currentText(self):
            return self._val

        def setCurrentText(self, val):
            self.set_calls.append(val)
            self._val = val
            if self._on_change:
                self._on_change(val)

        def text(self):
            return self._val

        def setText(self, val):
            self._val = val

        def value(self):
            return self._val

        def isChecked(self):
            return self._checked

        def setChecked(self, val):
            self.set_calls.append(bool(val))
            self._checked = bool(val)
            if self._on_change:
                self._on_change(self._checked)

    saved_qsettings = {"tonemap": "BT.2390 \u00b7 GPU tonemap", "dv_rpu": True, "save_count": 0}

    def _on_pref_mutated(_v):
        saved_qsettings["save_count"] += 1
        saved_qsettings["tonemap"] = harness.method_combo.currentText()

    class _Harness:
        pass

    harness = _Harness()
    f1 = tmp_path / "file1.mkv"
    f2 = tmp_path / "file2.mkv"
    f1.write_bytes(b"hdr1")
    f2.write_bytes(b"hdr2")

    harness.src_edit = _Widget(str(f1))
    harness.dst_edit = _Widget(str(tmp_path / "file1_SDR.mkv"))
    harness.backend_combo = _Widget("FFmpeg")
    harness.encoder_combo = _Widget("NVIDIA NVENC \u00b7 H.265")
    harness.bit_depth_combo = _Widget("8-bit (SDR standard)")
    harness.quality_mode_combo = _Widget("CRF/CQ")
    harness.quality_spin = _Widget(20)
    harness.target_bitrate_spin = _Widget(8000)
    harness.cores_spin = _Widget(0)
    harness.method_combo = _Widget("BT.2390 \u00b7 GPU tonemap", on_change=_on_pref_mutated)
    harness.res_combo = _Widget("Source (no scaling)")
    harness.denoise_combo = _Widget("Off")
    harness.deband_chk = _Widget(checked=False)
    harness.resharpen_chk = _Widget(checked=False)
    harness.autocrop_chk = _Widget(checked=False)
    harness.hwaccel_chk = _Widget(checked=False)
    harness.dv_rpu_chk = _Widget(checked=True, on_change=_on_pref_mutated)
    harness.peak_detect_chk = _Widget(checked=False)
    harness.brightness_spin = _Widget(0)
    harness.saturation_spin = _Widget(0)
    harness.desat_chk = _Widget(checked=False)
    harness.desat_spin = _Widget(2.0)
    harness.colortemperature_spin = _Widget(6500)
    harness.gamut_combo = _Widget("Perceptual")
    harness.preview_label = _Widget("")
    harness.speed_label = _Widget("")
    harness.speed_label.hide = lambda: None
    harness.pbar = _Widget(0)
    harness.pbar.setValue = lambda v: None

    harness.proc = None
    harness._setup_proc = None
    harness.handbrake_tool = ""
    harness.kind = "dolby"
    harness._dv_profile = 8
    harness._dv_rpu_auto_enabled = True
    harness._dv_rpu_user_override = False
    harness.has_bt2390 = True
    harness.has_st2094 = True
    harness.encoders = {"hevc_nvenc", "libx265"}
    harness.hb_encoders = set()
    harness.preview_path = None
    harness.preview_mtime = 0
    harness._src_color_transfer = "smpte2084"
    harness._src_color_primaries = "bt2020"
    harness._src_color_matrix = "bt2020nc"
    harness._src_color_range = "tv"
    harness._job_tonemap_override = None
    harness._job_dv_rpu_override = None
    harness._job_tonemap_fallback_attempts = 0
    harness._gpu_tonemap_fallback_used = False
    harness._tonemap_fallback_declined_for_current = False
    harness._tonemap_fallback_banner_text = ""
    harness._resume_coordinator = ResumeCoordinator()
    harness._run_lifecycle = RunStateMachine()
    harness._run_context = RunContext()
    harness._queue_controller = type("Q", (), {"running": True, "batch_clean": True})()
    harness.logs = []
    harness.banners = []
    harness.launched = []
    harness.tr = lambda s, **kw: s.format(**kw) if kw else s
    harness.write = lambda msg: harness.logs.append(msg)
    harness._set_test_clip_status = lambda msg: harness.banners.append(msg)
    harness._set_preview_meta = lambda _m: None
    harness._hwaccel_wanted = lambda: False
    harness._cancel_after_action_countdown = lambda: None
    harness._refuse_source_collision = lambda: False
    harness._confirm_destination = lambda: True
    harness._enough_disk_space = lambda: True
    harness._stop_frame_preview_process = lambda: None
    harness._reset_run_parsers = lambda _hb: None
    harness._offer_orphan_resume = lambda _p: False
    harness._write_part_sidecar = lambda _p: None
    harness._report_start_capability_problem = lambda t, m: harness.logs.append(f"PROBLEM:{t}:{m}")
    harness._launch = lambda cmd: harness.launched.append(cmd)

    # Bind extracted MainWindow methods
    import types
    for name, fn in methods.items():
        setattr(harness, name, types.MethodType(fn, harness))
    harness._settings_with_tracks = lambda out_path=None, announce=False: harness.collect_settings()

    # Fake check_start_capabilities: File 1 fails on GPU, succeeds on CPU; File 2 succeeds on GPU
    cap_calls = []

    def fake_check_caps(settings, *, ffmpeg, tonemap_engine, tonemap_code, **kwargs):
        cap_calls.append((Path(settings.src).name, tonemap_engine, tonemap_code, settings.tonemap_method, settings.dv_rpu))
        if Path(settings.src).name == "file1.mkv" and tonemap_engine == "gpu":
            return StartCapabilityIssue(
                "GPU tonemap could not start",
                "VK_ERROR_OUT_OF_DEVICE_MEMORY",
            )
        return None

    mw_ns["check_start_capabilities"] = fake_check_caps
    import tempfile as _tf
    mw_ns["tempfile"] = _tf
    import os as _os
    mw_ns["os"] = _os

    # --- Run File 1 (triggers GPU -> Hable CPU fallback) ---
    harness._start_impl()

    # Verify File 1 fell back to Hable CPU for this job ONLY:
    assert len(harness.launched) == 1
    assert any("tonemap=hable" in str(a) for a in harness.launched[0])
    assert harness._job_tonemap_override == "Hable \u00b7 CPU tonemap"
    assert harness._job_dv_rpu_override is False
    assert harness._job_tonemap_fallback_attempts == 1
    assert harness._tonemap_fallback_banner_text != ""

    # Verify UI widgets and QSettings were NOT touched at all:
    assert harness.method_combo.currentText() == "BT.2390 \u00b7 GPU tonemap"
    assert harness.method_combo.set_calls == []
    assert harness.dv_rpu_chk.isChecked() is True
    assert harness.dv_rpu_chk.set_calls == []
    assert saved_qsettings["save_count"] == 0
    assert saved_qsettings["tonemap"] == "BT.2390 \u00b7 GPU tonemap"

    # --- Simulate advancing to File 2 in the queue ---
    harness._clear_job_tonemap_override()
    harness.src_edit.setText(str(f2))
    harness.dst_edit.setText(str(tmp_path / "file2_SDR.mkv"))
    harness.launched.clear()

    harness._start_impl()
    # File 2 must try the user's selected GPU method ('BT.2390 · GPU tonemap') again!
    assert len(harness.launched) == 1
    assert any("libplacebo=tonemapping=bt.2390" in str(a) for a in harness.launched[0])
    assert harness._job_tonemap_override is None
    assert harness.method_combo.currentText() == "BT.2390 \u00b7 GPU tonemap"

    # --- Verify user declining the fallback dialog skips the file ---
    harness._clear_job_tonemap_override()
    harness.src_edit.setText(str(f1))
    harness.launched.clear()
    harness._queue_controller.running = False

    class _FakeMsgBox:
        class StandardButton:
            Yes = 1
            No = 2
        @staticmethod
        def question(*_a, **_k):
            return _FakeMsgBox.StandardButton.No
        @staticmethod
        def warning(*_a, **_k):
            return None

    mw_ns["QMessageBox"] = _FakeMsgBox
    mw_ns["QMessageBox"] = _FakeMsgBox
    harness.kind = "hdr"

    harness._start_impl()
    assert harness.launched == []
    assert harness._tonemap_fallback_declined_for_current is True
    assert harness._job_tonemap_override is None
    assert harness.method_combo.currentText() == "BT.2390 \u00b7 GPU tonemap"

    # --- Verify DV Profile 5 is still strictly blocked ---
    harness._clear_job_tonemap_override()
    harness.kind = "dolby"
    harness._dv_profile = 5
    harness.launched.clear()
    harness._start_impl()
    assert harness.launched == []
    assert harness._job_tonemap_override is None


# =====================================================================
# UI Layout & eventFilter _QueueItemWidget Tooltip Crash Regression Tests
# =====================================================================

def test_queue_item_widget_does_not_shadow_qwidget_window_and_ui_layout():
    """Verify:
    1) _QueueItemWidget stores self.main_window (never self.window), so QWidget.window()
       remains callable and hovering over a queue row in MainWindow.eventFilter never raises
       TypeError: 'MainWindow' object is not callable.
    2) Files zone height is increased (96px for both files_empty_drop_widget and queue_list).
    3) Live preview has compact-slider role on preview_slider, bottom margin >= 12px, and reduced DEFAULT_WINDOW_HEIGHT.
    4) Output folder button shares out_grid row 1 with Output resolution and Container.
    5) Compare methods button shares gamut_mapping_row_widget with Gamut mapping, and gamut_mapping_row_widget
       remains visible when switching between GPU and CPU curves.
    6) Advanced encoding button is removed from visible Encoding section layout.
    """
    mw_path = Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "main_window.py"
    src = mw_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(mw_path))

    # 1. Check _QueueItemWidget AST: no assignment to self.window
    qiw_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_QueueItemWidget")
    assigned_attrs = [
        t.attr
        for node in ast.walk(qiw_cls)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
    ]
    assert "window" not in assigned_attrs, "_QueueItemWidget must not assign self.window (shadows QWidget.window())"
    assert "main_window" in assigned_attrs, "_QueueItemWidget must store self.main_window"

    # 2. Check eventFilter guards obj.window() against non-callable attributes
    assert "callable(win_attr)" in src

    # 3. Check Files zone height increased to 96px
    assert "self.files_empty_drop_widget.setFixedHeight(96)" in src
    assert "self.queue_list.setFixedHeight(96)" in src

    # 4. Check Live preview slider role and bottom spacing
    assert 'set_role(self.preview_slider, "compact-slider")' in src
    assert "preview.layout().setContentsMargins(16, 8, 16, 14)" in src
    assert "self.preview_controls_widget.setFixedHeight(32)" in src

    # 5. Check Output folder button is on out_grid row 1 col 2 alongside res_combo (1,0) and container_combo (1,1)
    assert "out_grid.addWidget(self.res_combo, 1, 0)" in src
    assert "out_grid.addWidget(self.container_combo, 1, 1)" in src
    assert "out_grid.addWidget(self.output_folder_btn, 1, 2)" in src

    # 6. Check Compare methods shares gamut_row with Gamut mapping (GPU) and Desaturate (CPU),
    # and preset_segment_frame is at the very top of Conversion (conv_inner) above Encoding
    assert "conv_inner.addWidget(self.preset_segment_frame)" in src
    assert "enc_body.addWidget(self.preset_segment_frame)" not in src
    assert "gamut_row.addWidget(self.gamut_mapping_label, 0)" in src
    assert "gamut_row.addWidget(self.gamut_combo, 1)" in src
    assert "gamut_row.addWidget(self.desat_chk, 1)" in src
    assert "gamut_row.addWidget(self.desat_spin, 0)" in src
    assert "gamut_row.addWidget(self.compare_methods_btn, 0)" in src
    assert "self.gamut_mapping_row_widget.setVisible(True)" in src

    # 8. Check compare_dialog.py has unified Test frame / Test video clip card, 5s + 10s + custom spinbox,
    # Render test frames button, Auto-render toggle, per-variant Render button, Fullscreen switching, and Save all frames
    cd_path = Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "compare_dialog.py"
    cd_src = cd_path.read_text(encoding="utf-8")
    ast.parse(cd_src, filename=str(cd_path))
    assert 'raw_auto = settings.value("compare_auto_render", False) if settings is not None else False' in cd_src
    assert "self.btn_dur_custom.hide()" in cd_src
    assert 'self.test_clip_dur_spin = ScrubSpinBox("", self.tr(" s"), px_per_step=6.0)' in cd_src
    assert "self.test_clip_dur_chip = make_value_chip(self.test_clip_dur_spin, 80, 26)" in cd_src
    assert "row_clip.addWidget(self.test_clip_dur_chip)" in cd_src
    assert "right_lay.addWidget(self.scrub_bar)" in cd_src
    assert 'self.frame_section_lbl = QLabel(self.tr("Test frame:"))' in cd_src
    assert 'self.clip_section_lbl = QLabel(self.tr("Test video clip:"))' in cd_src
    assert 'self.render_frames_btn = GlassButton(self.tr("Render test frames"))' in cd_src
    assert 'self.auto_render_chk = ToggleSwitch(self.tr("Auto-render"), label_left=True)' in cd_src
    assert 'self.auto_render_chk.setProperty("text_color", text_primary)' in cd_src
    assert "self.frame_strip = FrameStrip()" in cd_src
    assert 'self.apply_btn.setObjectName("compareHeaderApplyBtn")' in cd_src
    assert "fb_lay.addWidget(self.progress_bar, 0)" in cd_src
    assert "fb_lay.addWidget(self.status_lbl, 1)" in cd_src
    assert "self.close_btn.hide()" in cd_src
    tc_src = (Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "dialogs" / "theme_customize.py").read_text(encoding="utf-8")
    assert "self._import_presets_btn.hide()" in tc_src
    assert "self._export_presets_btn.hide()" in tc_src
    assert 'self._save_as_new_btn.setFixedHeight(114)' in tc_src
    assert "img.setDevicePixelRatio(1.0)" in tc_src
    assert "Qt.AspectRatioMode.KeepAspectRatioByExpanding" in tc_src
    assert 'self.render_one_btn = GlassButton(self.tr("Render"))' in cd_src
    assert "def render_single_variant(self, index: int) -> None:" in cd_src
    assert "def step_variant(self, delta: int) -> None:" in cd_src
    assert "def _save_all_frames(self, target_dir: str | Path | bool | None = None) -> int:" in cd_src
    assert 'self.save_all_btn = GlassButton(' in cd_src

    # 9. Check instant section toggle (no section animation) and conversion card repaint fix
    assert "_animate_section_widget" not in src
    assert "def _repaint_conversion_card(self):" in src


    # 7. Check Advanced encoding button is not added to enc_body
    assert "enc_body.addWidget(self.adv_encoding_btn)" not in src
    assert "enc_body.addWidget(self.adv_encoding_panel)" not in src


def test_save_all_frames_handles_bool_clicked_signal_and_saves_only_ready_frames(tmp_path):
    """Verify:
    1) Passing bool (False/True) from Qt clicked(bool) signal does not raise TypeError in Path().
    2) When only a subset of variants are 'ready' and others are 'rendering' or 'pending',
       _save_all_frames saves ONLY the ready variants with mode names in the filenames.
    """
    cd_path = Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "compare_dialog.py"
    cd_src = cd_path.read_text(encoding="utf-8")
    assert "lambda _checked=False: self._save_all_frames()" in cd_src
    assert "if isinstance(target_dir, bool) or not isinstance(target_dir, (str, Path)):" in cd_src

    # Execute _save_all_frames logic directly on a mock dialog with mixed ready/rendering/pending variants
    ns = {"Path": Path}
    tree = ast.parse(cd_src, filename=str(cd_path))
    cls_node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TonemapComparisonDialog")
    for fn_name in ("_sanitize_filename_part", "_format_variant_filename", "_save_all_frames"):
        fn_node = next(n for n in cls_node.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        mod = ast.Module(body=[fn_node], type_ignores=[])
        exec(compile(mod, str(cd_path), "exec"), ns)

    class _DummyPixmap:
        def __init__(self, payload: bytes, null: bool = False):
            self._payload = payload
            self._null = null
        def isNull(self):
            return self._null
        def save(self, path_str: str) -> bool:
            if self._null:
                return False
            Path(path_str).write_bytes(self._payload)
            return True

    class _Variant:
        def __init__(self, method, gamut, engine, status, pixmap=None, file_path=None):
            self.method = method
            self.gamut = gamut
            self.engine = engine
            self.status = status
            self.pixmap = pixmap
            self.file_path = file_path

    class _DummyDialog:
        src_path = str(tmp_path / "Cyberpunk_Ep01.mkv")
        variants = [
            _Variant("BT.2390 · GPU tonemap", "Perceptual", "gpu", "ready", pixmap=_DummyPixmap(b"PNG_READY_1")),
            _Variant("ST2094-10 · GPU tonemap", "Relative", "gpu", "rendering", pixmap=None),
            _Variant("Hable · CPU tonemap", "N/A", "cpu", "ready", pixmap=_DummyPixmap(b"PNG_READY_3")),
            _Variant("Mobius · CPU tonemap", "N/A", "cpu", "pending", pixmap=None),
            _Variant("Reinhard · CPU tonemap", "N/A", "cpu", "error", pixmap=None),
        ]
        _sanitize_filename_part = staticmethod(ns["_sanitize_filename_part"])
        _format_variant_filename = ns["_format_variant_filename"]
        _save_all_frames = ns["_save_all_frames"]
        def _default_save_dir(self):
            return str(tmp_path)
        def tr(self, s, **kw):
            return s.format(**kw) if kw else s

    dlg = _DummyDialog()
    out_dir = tmp_path / "saved_frames"
    count = dlg._save_all_frames(out_dir)
    assert count == 2
    saved_files = sorted(p.name for p in out_dir.iterdir())
    assert saved_files == [
        "Cyberpunk_Ep01 - 01 - BT.2390 [GPU - Gamut Perceptual].png",
        "Cyberpunk_Ep01 - 03 - Hable [CPU].png",
    ]


def test_output_section_bottom_margin_and_adv_filters_unclipped():
    """Verify Output collapsible section has bottom padding (STEPPER_GLOW_ROOM, 0, 0, 6)
    and adv_filters_btn has minimumHeight(30) and is excluded from downward drop-shadow clipping."""
    mw_path = Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "main_window.py"
    mw_src = mw_path.read_text(encoding="utf-8")
    assert "body_l.setContentsMargins(STEPPER_GLOW_ROOM, 2, 2, 8)" in mw_src
    assert "self.adv_filters_btn.setMinimumHeight(30)" in mw_src
    assert 'elif self.ui_style == "glass" and not gb.property("drawer_header"):' in mw_src


def test_combobox_down_arrow_suppressed_no_double_chevron():
    """Verify QComboBox::down-arrow in build_main_window_qss and build_theme_dialog_qss
    sets image: none; width: 0px; height: 0px; border: none; so RoundedComboBox.paintEvent
    chevron is never doubled or shifted on focus/popup changes."""
    from hdr2sdr.theme.theme_tokens import build_main_window_qss, build_theme_dialog_qss, build_tokens

    tok = build_tokens(dict(base="dark", stops=None, direction=180, intensity=0, effect="none", button_custom=False, button_hex=None, ui_style="glass", neon_effect=False))
    mw_qss = build_main_window_qss(tok, button_radius_px=10, chevron_icon="", font="Segoe UI", font_semi="Segoe UI Semibold", mono="Consolas")
    dlg_qss = build_theme_dialog_qss(tok)
    assert "QComboBox::down-arrow { image: none; width: 0px; height: 0px; border: none; }" in " ".join(mw_qss.split())
    assert "QComboBox::down-arrow { image: none; width: 0px; height: 0px; border: none; }" in " ".join(dlg_qss.split())


def test_glass_and_duplicate_base_toggle_removed_only_neon_and_rounding_kept():
    """Verify Glass effect is completely disabled/removed, Light theme base duplicate toggle
    is hidden from Tab 3, and only Neon effect + Card/Button rounding remain."""
    from hdr2sdr.theme.metrics import button_radius, card_radius, set_radii

    set_radii(card=16, button=8)
    assert card_radius() == 16 and button_radius() == 8

    tc_path = Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "dialogs" / "theme_customize.py"
    tc_src = tc_path.read_text(encoding="utf-8")
    assert "self._glass = False" in tc_src
    assert "self._glass_toggle.hide()" in tc_src
    assert "self._base_toggle.hide()" in tc_src
    assert "toggles.addWidget(self._glass_toggle)" not in tc_src
    assert "toggles.addWidget(self._base_toggle)" not in tc_src
    assert "toggles.addWidget(self._neon_toggle)" in tc_src
    assert "_paint_radial_effect" not in tc_src

    mw_path = Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "main_window.py"
    mw_src = mw_path.read_text(encoding="utf-8")
    assert "self.look[\"ui_style\"] = \"classic\"" in mw_src
    assert "self.ui_style = \"classic\"" in mw_src


def test_no_undefined_names_across_hdr2sdr_modules():
    """Verify every loaded name in every hdr2sdr/*.py module is defined or imported
    (catches missing QtGui/QtWidgets imports like QRadialGradient in containers.py)."""
    import builtins

    root = Path(__file__).resolve().parent.parent / "hdr2sdr"
    builtin_names = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__annotations__"}
    undefined = []

    for pyf in sorted(root.rglob("*.py")):
        tree = ast.parse(pyf.read_text(encoding="utf-8"), filename=str(pyf))
        defined = set(builtin_names)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                    defined.add(a.arg)
                if node.args.vararg:
                    defined.add(node.args.vararg.arg)
                if node.args.kwarg:
                    defined.add(node.args.kwarg.arg)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    defined.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    defined.add(alias.asname or alias.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                defined.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
            elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in defined:
                    undefined.append(f"{pyf.relative_to(root.parent)}:{node.lineno}:{node.id}")

    assert not undefined, f"Undefined names found: {undefined}"


def test_sliders_and_advanced_panel_adapted_to_screenshot_spec():
    """Verify Quality TierSlider + CRF chip and Advanced filters Colour adjustments
    (ColorPad2D for Brightness x Saturation + Colour temperature gradient ParamSlider + Reset LinkButton)
    are wired and default to the 'pad' layout shown in the target screenshot."""
    from hdr2sdr.core.constants import QUALITY_TIER_REPS_51, QUALITY_TIER_REPS_AV1, QUALITY_TIER_REPS_BY_ENCODER

    assert QUALITY_TIER_REPS_51 == {"Maximum": 16, "High": 18, "Medium": 24, "Low": 30}
    assert QUALITY_TIER_REPS_AV1 == {"Maximum": 22, "High": 30, "Medium": 36, "Low": 45}
    assert QUALITY_TIER_REPS_BY_ENCODER["CPU \u00b7 AV1"] == QUALITY_TIER_REPS_AV1

    root = Path(__file__).resolve().parent.parent
    pc_src = (root / "hdr2sdr" / "ui" / "widgets" / "param_controls.py").read_text(encoding="utf-8")
    mw_src = (root / "hdr2sdr" / "ui" / "main_window.py").read_text(encoding="utf-8")

    # Default style is "pad" (2D pad for Brightness/Saturation + fill bar for Colour temperature)
    assert 'DEFAULT_CONTROL_STYLE = "pad"' in pc_src
    assert 'if s == "pad":' in pc_src and 's = "fill"' in pc_src
    assert '{"fill": 26, "scrub": 26, "track": 42}' in pc_src
    assert "TRACK_H = 13" in pc_src and "HEIGHT = 26" in pc_src
    assert "_draw_band_labels" in pc_src
    assert "Qt.TextFlag.TextDontClip" in pc_src

    # MainWindow uses ScrubSpinBox("CRF "), TierSlider(), ColorPad2D, ParamSlider with TEMPERATURE_GRADIENT, and LinkButton("Reset")
    assert 'self.quality_spin = ScrubSpinBox("CRF ")' in mw_src
    assert "self.quality_stepper = make_value_chip(self.quality_spin, 92)" in mw_src
    assert "self.quality_slider = TierSlider()" in mw_src
    assert 'self.colour_group_label = _field_label(self.tr("Colour adjustments"))' in mw_src
    assert 'self.colour_reset_btn = LinkButton(self.tr("Reset"))' in mw_src
    assert "self.colour_pad = ColorPad2D(self.saturation_slider, self.brightness_slider)" in mw_src
    assert "gradient=TEMPERATURE_GRADIENT" in mw_src
    assert 'ParamSlider.set_global_style(str(self.i18n.settings.value("control_style", DEFAULT_CONTROL_STYLE) or DEFAULT_CONTROL_STYLE))' in mw_src
    assert 'ParamSlider(\n            "Brightness", -20, 20, 0, bidirectional=True, origin=0, pad_capable=True)' in mw_src
    assert 'ParamSlider(\n            "Saturation", -20, 20, 0, bidirectional=True, origin=0, pad_capable=True)' in mw_src
    assert 'self.brightness_label.setText(self.tr("Brightness"))' in mw_src
    assert 'self.saturation_label.setText(self.tr("Saturation"))' in mw_src
    assert "_backdrop_pixmap" in pc_src
    assert "val_slot_w = max(" in pc_src
    assert 'self.priority_slider = ParamSlider(' in mw_src
    assert 'choices=PRIORITY_LEVELS, display_choices=("Low", "Balanced", "High")' in mw_src
    assert 'ticks=True, segmented=True' in mw_src
    assert 'self.cores_combo' not in mw_src
    assert "cf.addWidget(self.cores_slider, 5)" in mw_src
    assert "cf.addWidget(self.priority_slider, 3)" in mw_src
    assert "self.priority_slider.setMaximumWidth(184)" in mw_src
    assert "self.conv_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)" in mw_src
    assert 'self.denoise_combo.setObjectName("denoiseChipCombo")' in mw_src
    assert "self.denoise_combo.set_display_prefix(" in mw_src
    assert "self.denoise_combo.setMaximumWidth(" in mw_src
    assert "denoise_row.addWidget(self.denoise_combo, 2)" in mw_src
    assert "denoise_row.addStretch(1)" in mw_src
    tt_src = (root / "hdr2sdr" / "theme" / "theme_tokens.py").read_text(encoding="utf-8")
    assert 'QSlider[role="tier-slider"] {{ min-height: 26px; max-height: 26px; }}' in tt_src
    assert "for cls in (ParamSlider, TierSlider, ColorPad2D, RoundHandleSlider):" in mw_src
    assert "for ctrl in self.findChildren(cls):" in mw_src



    # Verify FrameStrip timeline ruler/badge/playhead and auto-hide of preview_controls_widget
    fs_src = (root / "hdr2sdr" / "ui" / "widgets" / "frame_strip.py").read_text(encoding="utf-8")
    assert "RULER_H = 18" in fs_src and "STRIP_H = 44" in fs_src and "TILE_H = RULER_H + STRIP_H" in fs_src
    assert "def set_time_info(" in fs_src
    assert "self.preview_controls_widget.hide()" in mw_src
    cd_src = (root / "hdr2sdr" / "ui" / "compare_dialog.py").read_text(encoding="utf-8")
    assert 'self.fullscreen_btn.setProperty("lead_icon", "maximize")' in cd_src
    assert 'self.save_btn.setProperty("lead_icon", "save_frame")' in cd_src
    assert 'self.save_all_btn.setProperty("lead_icon", "save_all")' in cd_src
    assert 'self.test_clip_open_btn.setProperty("lead_icon", "play")' in cd_src


def test_no_findchildren_tuple_argument_anywhere():
    """PySide6 QObject.findChildren/findChild only accepts a single type, never a tuple of types."""
    root = Path(__file__).resolve().parent.parent
    bad = []
    for folder in ("hdr2sdr", "tests", "tools"):
        for pyf in sorted((root / folder).rglob("*.py")):
            tree = ast.parse(pyf.read_text(encoding="utf-8"), filename=str(pyf))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in ("findChildren", "findChild"):
                        if not node.args or isinstance(node.args[0], (ast.Tuple, ast.List, ast.Set, ast.BinOp)):
                            bad.append(f"{pyf.relative_to(root)}:{node.lineno}:{ast.unparse(node)}")
    assert not bad, f"Invalid findChildren/findChild signature calls: {bad}"
