"""Tests for qa/tools/verify_output.py V11 full decode error filtering."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import qa.tools.verify_output as V


def test_v11_ignores_benign_dts_rounding_and_repetition(tmp_path):
    dummy_out = tmp_path / "out.mkv"
    dummy_out.write_bytes(b"dummy mkv data")

    # Mock ffprobe probes returning valid video stream and duration
    mock_probe = {
        "format": {"duration": "10.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "color_space": "bt709",
                "color_range": "tv",
            }
        ],
    }

    # Simulate FFmpeg output containing only Matroska 1ms DTS rounding warnings and repetition
    mock_run_result = MagicMock()
    mock_run_result.returncode = 0
    mock_run_result.stderr = (
        "[matroska,webm @ 0000029b] non monotonically increasing dts to muxer in stream 0: 1000 >= 1000\n"
        "Last message repeated 4 times\n"
        "[matroska,webm @ 0000029b] non monotonous DTS in output stream 0:1\n"
    )

    with patch.object(V, "probe", return_value=(mock_probe, "")), \
         patch.object(V, "_run", return_value=mock_run_result), \
         patch.object(V, "frame_stats", return_value={"YMIN": 16, "YMAX": 235, "UMIN": 128, "UMAX": 128, "VMIN": 128, "VMAX": 128}):
        res = V.verify(dummy_out, ffmpeg="ffmpeg", full_decode=True)

    v11_check = next((c for c in res["checks"] if c["id"] == "V11"), None)
    assert v11_check is not None, "V11 check must be present"
    assert v11_check["ok"] is True, f"V11 should PASS when only benign warnings occur: {v11_check['detail']}"
    assert "benign DTS-rounding warnings ignored" in v11_check["detail"]


def test_v11_fails_on_genuine_decode_errors(tmp_path):
    dummy_out = tmp_path / "out.mkv"
    dummy_out.write_bytes(b"dummy mkv data")

    mock_probe = {
        "format": {"duration": "10.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "color_space": "bt709",
                "color_range": "tv",
            }
        ],
    }

    # Simulate FFmpeg output containing real corruption alongside benign warnings
    mock_run_result = MagicMock()
    mock_run_result.returncode = 0
    mock_run_result.stderr = (
        "[matroska,webm @ 0000029b] non monotonically increasing dts to muxer in stream 0: 1000 >= 1000\n"
        "Last message repeated 2 times\n"
        "[hevc @ 0000029b] corrupted macroblock at 42 18\n"
        "[hevc @ 0000029b] slice header corrupted\n"
    )

    with patch.object(V, "probe", return_value=(mock_probe, "")), \
         patch.object(V, "_run", return_value=mock_run_result), \
         patch.object(V, "frame_stats", return_value={"YMIN": 16, "YMAX": 235, "UMIN": 128, "UMAX": 128, "VMIN": 128, "VMAX": 128}):
        res = V.verify(dummy_out, ffmpeg="ffmpeg", full_decode=True)

    v11_check = next((c for c in res["checks"] if c["id"] == "V11"), None)
    assert v11_check is not None, "V11 check must be present"
    assert v11_check["ok"] is False, "V11 must FAIL when genuine decode errors are present"
    assert "corrupted macroblock" in v11_check["detail"]
