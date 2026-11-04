# Changelog

## v25.0 (2026-11-04)

### Architecture & Modular Package
- Modularized the codebase into the `hdr2sdr/` package (`core`, `runtime`, `services`, `theme`, `ui`) with a strict pure-Python `hdr2sdr/core/` boundary (zero Qt dependencies in command builders, stream probing, Dolby Vision analysis, and progress parsers).
- Added backward-compatible entry points: `python -m hdr2sdr` and `python hdr_to_sdr_gui_qt.py`.

### New Features
- **Compare Tone-Mapping Methods Dialog**: Visual side-by-side comparison of up to 18 GPU (`libplacebo`) and CPU (`zscale` + `tonemap`) method × gamut combinations on any frame from the source video, complete with an interactive timeline frame strip, 5-second test video clip rendering, and one-click **Apply to conversion**.
- **Dolby Vision & HDR10+ Pipeline**: Added Dolby Vision Profile 5 / 7 / 8 detection and `libplacebo` reshaping support, plus `ST2094-40` / `ST2094-10` dynamic tone-mapping curves.
- **Advanced Pro Colour & Filter Controls**: Added an interactive **2D Brightness × Saturation pad**, **Colour temperature** slider, **Denoise** (`hqdn3d` / `nlmeans`), **Deband** (`gradfun`), **Sharpen** (`unsharp`), **10-bit HDR Auto-crop**, and **Subtitle burn-in**.
- **Crash-Safe Resume**: Interrupted conversions can now be resumed from the last completed keyframe segment with automatic segment splicing and output verification.
- **Mini Mode & Theme Customizer**: Added a compact floating **Mini Mode** window and a **Theme Customizer** supporting **Light**, **Dark**, and **Neon** palettes with custom gradient stops and adjustable card/button rounding.
- **Expanded Hardware & Track Support**: Added **Intel QSV** hardware encoding (`h264_qsv`, `hevc_qsv`, `av1_qsv`), automatic MP4 `mov_text` → `SRT` subtitle conversion when outputting to `.mkv`, and 31 interface languages in `i18n/`.

### Fixes & Polish
- Centered the **✓ Apply to conversion** button and unified high-contrast `on_accent` icon rendering in the Compare dialog header.
- Fixed SVT-AV1 Bitrate mode CLI construction by removing unsupported `-maxrate` and `-bufsize` flags.
- Corrected 10-bit HDR auto-crop black threshold in `hdr2sdr/core/crop.py` (`format=yuv420p,cropdetect=24:2:0`).
- Updated HandBrake multi-pass flag (`--multi-pass`) and set `--encoder-profile high10` for HandBrake `x264_10bit`.
- Fixed timestamp synchronization (`-ss 0` after `-i`) for test clips and resumed segments.
- Probed hardware encoders with a `256x256` frame during preflight to prevent false NVENC minimum-dimension rejections.
