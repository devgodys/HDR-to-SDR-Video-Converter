from __future__ import annotations

from hdr2sdr.core.constants import (
    BITRATE_ANCHOR_1080P_H264_CRF23_KBPS, CODEC_EFFICIENCY, CRF_CENTER, CRF_HALVING_STEP, ENCODER_EFFICIENCY,
)




def estimate_bitrate_kbps(encoder_label, quality, width, height):
    """Rough estimated output bitrate in kbps for the given encoder combo
    label (an ENCODER_MAP key), CRF/CQ/QP value, and output resolution."""
    if encoder_label.endswith("AV1"):
        codec = "AV1"
    elif encoder_label.endswith("H.265"):
        codec = "H.265"
    else:
        codec = "H.264"
    if encoder_label.startswith("CPU"):
        enc_type = "CPU"
    elif encoder_label.startswith("NVIDIA"):
        enc_type = "NVIDIA"
    elif encoder_label.startswith("AMD"):
        enc_type = "AMD"
    elif encoder_label.startswith("Intel"):
        enc_type = "Intel"
    else:
        enc_type = "Apple"
    w = width or 1920
    h = height or 1080
    scale = max(1, w * h) / (1920 * 1080)
    crf_factor = 2 ** ((CRF_CENTER[codec] - quality) / CRF_HALVING_STEP[codec])
    kbps = (BITRATE_ANCHOR_1080P_H264_CRF23_KBPS * scale * crf_factor
            * CODEC_EFFICIENCY[codec] * ENCODER_EFFICIENCY[enc_type])
    return max(150.0, kbps)


def estimate_output_bytes(duration_seconds, bitrate_kbps, source_size_bytes=0,
                          minimum_bytes=512 * 1024 * 1024, headroom=1.25):
    """Estimate output storage needs using duration when it is available."""
    minimum_bytes = max(0, int(minimum_bytes))
    source_size_bytes = max(0, int(source_size_bytes or 0))
    if duration_seconds and duration_seconds > 0:
        estimate = max(0.0, float(bitrate_kbps or 0)) * 1000 / 8 * float(duration_seconds) * float(headroom)
        return max(minimum_bytes, int(estimate))
    return int(max(source_size_bytes, minimum_bytes) * 1.5)
