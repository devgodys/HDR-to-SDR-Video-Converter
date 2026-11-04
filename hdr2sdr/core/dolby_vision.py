"""Small, Qt-free helpers for Dolby Vision stream metadata."""

from __future__ import annotations


def dovi_configuration(stream: dict) -> dict:
    """Return the Dolby Vision configuration side data, or an empty mapping."""
    for item in stream.get("side_data_list") or ():
        if isinstance(item, dict) and item.get("side_data_type") == "DOVI configuration record":
            return item
    return {}


def dovi_profile(stream: dict) -> int | None:
    """Read the numeric DV profile reported by ffprobe without trusting input types."""
    value = dovi_configuration(stream).get("dv_profile")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def profile5_rpu_supported(profile, using_handbrake, tonemap_engine, has_libplacebo) -> bool:
    """Profile 5 needs RPU-aware libplacebo processing; other profiles are unaffected."""
    return profile != 5 or (
        not using_handbrake and tonemap_engine == "gpu" and bool(has_libplacebo)
    )
