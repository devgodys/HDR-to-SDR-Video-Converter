"""Step 0 of the migration: characterisation harness.

Instantiates the REAL MainWindow in an offscreen QApplication, drives the
REAL widgets across a matrix of configurations, and snapshots what the
REAL command builders produce.

This does not assert anything about correctness. Its only job is to pin
down *current* behaviour byte-for-byte so the refactor that follows can be
proven to change nothing.

Usage:
    QT_QPA_PLATFORM=offscreen python3 tools/golden_capture.py out.json
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

import hdr2sdr.core.commands as C
import hdr2sdr.ui.main_window as app

SRC = "/tmp/src video.mkv"
DST_MKV = "/tmp/out.mkv"
DST_MP4 = "/tmp/out.mp4"

# Normalised in the snapshot so it doesn't depend on where ffmpeg happens
# to live on the machine that captured it.
TOOL_PLACEHOLDERS = {
    "ffmpeg": "<FFMPEG>",
    "ffprobe": "<FFPROBE>",
    "HandBrakeCLI": "<HANDBRAKE>",
}

BASE = {
    "backend": "FFmpeg",
    "encoder": "CPU \u00b7 H.265",
    "bit_depth": "8-bit (SDR standard)",
    "quality_mode": "CRF/CQ",
    "quality": 20,
    "bitrate": 8000,
    "method": "BT.2390 \u00b7 GPU tonemap",
    "res": "Source (no scaling)",
    "denoise": "Off",
    "dst": DST_MKV,
    "deband": False,
    "resharpen": False,
    "autocrop": False,
    "hwaccel": False,
    "dv_rpu": False,
    "peak_detect": False,
    "brightness": 0,
    "saturation": 0,
    "cores": 0,
    "cpu_count": 8,
    "preview": None,
    "resume_seek": 0.0,
    "resume_out": None,
    "autocrop_value": "",
}


def variations():
    """Base config plus one-variable-at-a-time deviations, plus a handful
    of deliberately tangled combinations."""
    yield "base", {}

    for enc in ["CPU \u00b7 H.264", "CPU \u00b7 AV1", "NVIDIA NVENC \u00b7 H.264",
                "NVIDIA NVENC \u00b7 H.265", "NVIDIA NVENC \u00b7 AV1",
                "Intel QSV \u00b7 H.264", "Intel QSV \u00b7 H.265", "Intel QSV \u00b7 AV1"]:
        yield f"encoder={enc}", {"encoder": enc}

    for depth in ["10-bit", "12-bit"]:
        yield f"depth={depth}", {"bit_depth": depth}

    # q=0 exercises the QSV "0 is out of range, remap to 1" branch and the
    # nvenc constqp path where 0 is already legitimate.
    for q in [0, 51]:
        yield f"quality={q}", {"quality": q}
        yield f"quality={q},qsv", {"quality": q, "encoder": "Intel QSV \u00b7 H.265"}
        yield f"quality={q},nvenc", {"quality": q, "encoder": "NVIDIA NVENC \u00b7 H.265"}

    yield "bitrate-mode", {"quality_mode": "Bitrate"}
    yield "bitrate-mode,av1", {"quality_mode": "Bitrate", "encoder": "CPU \u00b7 AV1"}
    yield "bitrate-mode,nvenc", {"quality_mode": "Bitrate", "encoder": "NVIDIA NVENC \u00b7 H.265"}
    yield "bitrate=1500", {"quality_mode": "Bitrate", "bitrate": 1500}

    for m in ["Spline \u00b7 GPU tonemap", "ST2094-40 (HDR10+) \u00b7 GPU tonemap",
              "ST2094-10 \u00b7 GPU tonemap", "Auto \u00b7 GPU tonemap",
              "Hable \u00b7 CPU tonemap", "Reinhard \u00b7 CPU tonemap",
              "Mobius \u00b7 CPU tonemap", "Linear \u00b7 CPU tonemap",
              "Gamma \u00b7 CPU tonemap", "Clip \u00b7 CPU tonemap",
              "None (direct, no tonemap) \u00b7 CPU tonemap"]:
        yield f"method={m}", {"method": m}

    for r in ["2160p (4K)", "1440p", "1080p", "720p", "480p"]:
        yield f"res={r}", {"res": r}

    for d in ["Light", "Medium"]:
        yield f"denoise={d}", {"denoise": d}

    yield "dst=mp4", {"dst": DST_MP4}
    yield "dst=mov", {"dst": "/tmp/out.mov"}
    yield "dst=m4v", {"dst": "/tmp/out.m4v"}
    yield "dst=webm", {"dst": "/tmp/out.webm"}

    for flag in ["deband", "resharpen", "hwaccel", "dv_rpu", "peak_detect"]:
        yield f"{flag}=on", {flag: True}

    yield "autocrop=on", {"autocrop": True, "autocrop_value": "crop=1920:800:0:140"}
    yield "brightness=+5", {"brightness": 5}
    yield "brightness=-7", {"brightness": -7}
    yield "saturation=+10", {"saturation": 10}
    yield "brightness+saturation", {"brightness": 3, "saturation": -4}

    yield "cores=4", {"cores": 4}
    yield "cores=8-of-8", {"cores": 8}  # equal to CPU_COUNT -> no -threads
    yield "cores=4,bitrate", {"cores": 4, "quality_mode": "Bitrate"}
    yield "cores=4,nvenc", {"cores": 4, "encoder": "NVIDIA NVENC \u00b7 H.265"}

    yield "preview-on", {"preview": "/tmp/preview.jpg"}
    yield "preview-on,10bit", {"preview": "/tmp/preview.jpg", "bit_depth": "10-bit"}
    yield "preview-on,mp4", {"preview": "/tmp/preview.jpg", "dst": DST_MP4}

    yield "resume-seek", {"resume_seek": 1234.5}
    yield "resume-seek+override", {"resume_seek": 1234.5, "resume_out": "/tmp/part2.mkv"}
    yield "resume-override-mp4", {"resume_seek": 60.0, "resume_out": "/tmp/part2.mp4"}

    # HandBrake backend
    hb = {"backend": "HandBrake"}
    yield "hb:base", hb
    for enc in ["CPU \u00b7 H.264", "CPU \u00b7 AV1", "NVIDIA NVENC \u00b7 H.265",
                "Intel QSV \u00b7 H.265"]:
        yield f"hb:encoder={enc}", {**hb, "encoder": enc}
    for depth in ["10-bit", "12-bit"]:
        yield f"hb:depth={depth}", {**hb, "bit_depth": depth}
    yield "hb:bitrate", {**hb, "quality_mode": "Bitrate"}
    yield "hb:res=1080p", {**hb, "res": "1080p"}
    yield "hb:denoise=Medium", {**hb, "denoise": "Medium"}
    yield "hb:deband+sharpen", {**hb, "deband": True, "resharpen": True}
    yield "hb:autocrop", {**hb, "autocrop": True}
    yield "hb:hwaccel+nvenc", {**hb, "hwaccel": True, "encoder": "NVIDIA NVENC \u00b7 H.265"}
    yield "hb:cpu-tonemap", {**hb, "method": "Mobius \u00b7 CPU tonemap"}
    yield "hb:mp4", {**hb, "dst": DST_MP4}

    # Deliberately tangled: several interacting options at once.
    yield "combo:everything-ffmpeg", {
        "encoder": "NVIDIA NVENC \u00b7 H.265", "bit_depth": "10-bit", "quality": 26,
        "method": "ST2094-40 (HDR10+) \u00b7 GPU tonemap", "res": "1080p",
        "denoise": "Medium", "dst": DST_MP4, "deband": True, "resharpen": True,
        "autocrop": True, "autocrop_value": "crop=3840:1600:0:280", "hwaccel": True,
        "dv_rpu": True, "peak_detect": True, "brightness": 4, "saturation": 6,
        "preview": "/tmp/preview.jpg",
    }
    yield "combo:everything-cpu", {
        "encoder": "CPU \u00b7 AV1", "bit_depth": "10-bit", "quality_mode": "Bitrate",
        "bitrate": 4200, "method": "Hable \u00b7 CPU tonemap", "res": "720p",
        "denoise": "Light", "deband": True, "resharpen": True, "cores": 3,
        "brightness": -2, "saturation": 9, "resume_seek": 900.0,
    }
    yield "combo:everything-hb", {
        **hb, "encoder": "CPU \u00b7 H.265", "bit_depth": "10-bit",
        "quality_mode": "Bitrate", "bitrate": 3000, "method": "Reinhard \u00b7 CPU tonemap",
        "res": "1440p", "denoise": "Light", "deband": True, "resharpen": True,
        "autocrop": True, "dst": DST_MP4,
    }


def apply_config(w, cfg):
    """Drive the real widgets. Returns the config as actually realised -
    a combo may refuse a value (e.g. an encoder not offered at 12-bit),
    and that refusal is itself behaviour worth pinning down."""
    c = {**BASE, **cfg}
    app.CPU_COUNT = c["cpu_count"]
    # MainWindow's spinbox ranges are set at construction using the host's
    # real CPU count. Refresh them too, otherwise scenarios above a small
    # machine's core count are silently clamped before command generation.
    w.cores_spin.setRange(1, c["cpu_count"])
    w.cores_slider.setRange(1, c["cpu_count"])

    w.src_edit.setText(SRC)
    w.dst_edit.setText(c["dst"])
    # Bit depth first: it re-filters the encoder list.
    w.bit_depth_combo.setCurrentText(c["bit_depth"])
    w.backend_combo.setCurrentText(c["backend"])
    w.encoder_combo.setCurrentText(c["encoder"])
    w.quality_mode_combo.setCurrentText(c["quality_mode"])
    w.quality_spin.setValue(c["quality"])
    w.target_bitrate_spin.setValue(c["bitrate"])
    w.method_combo.setCurrentText(c["method"])
    w.res_combo.setCurrentText(c["res"])
    w.denoise_combo.setCurrentText(c["denoise"])
    w.deband_chk.setChecked(c["deband"])
    w.resharpen_chk.setChecked(c["resharpen"])
    w.autocrop_chk.setChecked(c["autocrop"])
    w.hwaccel_chk.setChecked(c["hwaccel"])
    w.dv_rpu_chk.setChecked(c["dv_rpu"])
    w.peak_detect_chk.setChecked(c["peak_detect"])
    w.brightness_spin.setValue(c["brightness"])
    w.saturation_spin.setValue(c["saturation"])
    w.cores_spin.setMaximum(max(1, c["cpu_count"]))
    w.cores_spin.setValue(c["cores"] if c["cores"] else w.cores_spin.minimum())
    w._autocrop_value = c["autocrop_value"] or None
    w.preview_path = Path(c["preview"]) if c["preview"] else None
    w._resume_coordinator.seek_seconds = float(c["resume_seek"] or 0.0)
    w._resume_coordinator.output_override = Path(c["resume_out"]) if c["resume_out"] else None
    w.handbrake_tool = "HandBrakeCLI"
    w.duration = 3600.0
    # Trim bar drives the test-clip command's -ss/-t; pin it so that path
    # is captured deterministically too.
    w.preview_timeline.setRange(0, 3600)
    w.preview_timeline.setValues(120, 127)

    return {
        "encoder_actual": w.encoder_combo.currentText(),
        "method_actual": w.method_combo.currentText(),
        "backend_actual": w.backend_combo.currentText(),
        "quality_actual": w.quality_spin.value(),
        "cores_actual": w.cores_spin.value(),
    }


def normalise(cmd):
    out = []
    for part in cmd:
        part = "" if part is None else str(part)
        lower_part = part.lower()
        for tool, placeholder in TOOL_PLACEHOLDERS.items():
            if lower_part.endswith(tool.lower()) or lower_part.endswith(tool.lower() + ".exe"):
                part = placeholder
                break
        # The snapshot uses POSIX-style paths by convention. pathlib turns
        # the deliberately synthetic /tmp test-clip path into \tmp\... on
        # Windows, so normalize that fixture path across host platforms.
        if part.startswith("\\tmp\\"):
            part = part.replace("\\", "/")
        out.append(part)
    return out


def capture():
    qapp = QApplication.instance() or QApplication(sys.argv)
    w = app.MainWindow()
    # The snapshot records command-builder behaviour, not the capabilities of
    # the GPU/FFmpeg/HandBrake installation running the test. Empty encoder
    # sets intentionally select the UI's structural fallback lists.
    w.encoders = set()
    w.hb_encoders = set()
    w.has_bt2390 = True
    w.has_st2094 = True
    w.refresh_method_choices(force_default=True)
    w._on_bit_depth_changed(w.bit_depth_combo.currentText())
    snapshot = {}
    for name, cfg in variations():
        realised = apply_config(w, cfg)
        fmt, _ = app.FFMPEG_BIT_DEPTH[w.bit_depth_combo.currentText()]
        entry = {
            "realised": realised,
            "command": normalise(app.build_command(w._settings_with_tracks(announce=True))[0]),
            "vf_chain": C.build_vf_chain(w.collect_settings(), fmt),
            "encode": [C.build_encode_args(w.collect_settings())[0], list(C.build_encode_args(w.collect_settings())[1])],
            "scale_args": C.build_scale_args(w.collect_settings()),
            "brightness_args": C.build_brightness_args(w.collect_settings()),
            "container_args": C.build_container_args(w.dst_edit.text()),
            "tonemap": list(C.tonemap_lookup(w.method_combo.currentText())),
            "test_clip_command": normalise(w._test_clip_command(Path("/tmp/testclip.mkv"))),
        }
        if name in snapshot:
            raise SystemExit(f"duplicate variation name: {name}")
        snapshot[name] = entry
    del qapp
    return snapshot


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("golden/commands.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    data = capture()
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"captured {len(data)} variations -> {out}")
