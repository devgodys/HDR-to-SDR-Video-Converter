# HDR to SDR Video Converter

A desktop tone-mapping tool for people who care which curve they're using — standard FFmpeg, true BT.2390 on the GPU, or HandBrakeCLI, with the exact command always visible. H.264, H.265, and AV1 output, 10-bit and 12-bit included, free.

![platform](https://img.shields.io/badge/platform-Windows-informational)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![grandma approved](https://img.shields.io/badge/grandma-approved-ff69b4)

**[Website](https://openhdrtosdr.com/)** · **[Latest release](https://github.com/devgodys/HDR-to-SDR-Video-Converter/releases/latest)**

![Light mode](webassets/app-screenshot-light.png)

## Why

If you're just a user who wants to convert HDR to SDR, you shouldn't have to learn FFmpeg first.

Right now the choice is usually one of two extremes: paid software with a clean interface, or FFmpeg/HandBrake — free, powerful, and correct, but built for people who already know what BT.2390 means and are comfortable on a command line. There's not much in between.

This app is built to be that middle ground. It does exactly one job — HDR to SDR — and does it using nothing but your own hardware and open-source tools already capable of doing it right (FFmpeg, libplacebo/Vulkan, optionally HandBrakeCLI). No subscription, no cloud processing, no black-box "auto" button standing in for a real answer. Just a normal window: pick a curve, pick an encoder, hit start, and see the exact command that ran.

## Backends

Three genuinely different pipelines, not one "auto" button:

| Backend | Type | Curves | Notes |
|---|---|---|---|
| **Standard FFmpeg** | CPU | Hable, Reinhard, Mobius (+ Linear, Gamma, Clip, None in Pro mode) | Fast, works everywhere FFmpeg does, no GPU required |
| **libplacebo / Vulkan** | GPU | BT.2390 by default; BT.2446A, ST2094-10, ST2094-40 (HDR10+), Auto in Pro mode | Runs the real ITU-R BT.2390 EETF — the same curve most HDR-to-SDR grades are built around |
| **HandBrakeCLI** | Experimental | Same resolution/encoder controls | Alternate encode path for sources where HandBrake's own pipeline behaves better |

## Features

- **True BT.2390 tone mapping** via libplacebo/Vulkan — not a CPU approximation
- **H.264, H.265 and AV1 output** — CPU (x264/x265/SVT-AV1) plus hardware encoders (NVIDIA NVENC, AMD AMF/AMF AV1, Apple VideoToolbox for H.264/H.265). AV1 is royalty-free and typically needs 40-50% fewer bits than H.264 for comparable quality
- **10-bit & 12-bit output** — free, no license, no separate build. 10-bit works with any H.265/AV1 encoder (CPU or hardware); 12-bit is CPU libx265 only, since no common hardware encoder does 12-bit HEVC
- **Full curve library** — Hable, Reinhard, Mobius, BT.2446A, ST2094-10/40, Linear, Gamma, Clip, None, each with a hover tooltip explaining its actual tradeoffs
- **Batch queue** — add any number of source videos (via file picker or drag-and-drop) and hit Start queue to convert them one after another with the current settings, either into a single chosen output folder or, by default, next to each source file
- **One-click presets** — once a queued video's been analyzed, apply **Optimal** (the recommended curve for that source), **Best quality** (CPU · H.265, Pro mode, low CRF, best available curve), or **Fast** (a hardware encoder if one's detected, else CPU · H.264, simple curve) without hand-translating a recommendation into dropdown picks
- **Analyzes on demand** — analysis (transfer characteristics, color range, HDR10+/Dolby Vision metadata, a curve recommendation) runs when you click Analyze on a queued file, or automatically right before that file starts converting — kept out of the Add videos step so queuing up a batch stays instant
- **Audio & subtitle passthrough** — all audio and subtitle tracks are copied through untouched (never re-encoded or dropped) on both the FFmpeg and HandBrake backends
- **MP4 or MKV output container** — MP4 for the widest device/player support, or MKV when the source has subtitle or audio tracks (e.g. PGS, DTS) that MP4 can't hold when copied as-is
- **Low disk space warning** — checks free space against an estimate of the output size before starting, and asks for confirmation rather than failing partway through
- **Live Preview** — a real decoded-and-tone-mapped frame from your source, refreshed as you change settings, with a shadow/highlight clipping check so you can catch a curve crushing detail before committing to a full encode. View it full size or save the frame straight to disk
- **Live bitrate estimate** — updates automatically as you change quality, resolution, or encoder (each codec's own CRF scale is accounted for), so a surprising CRF/resolution combo shows up before you hit Start, not after
- **Hardware encoding** — CPU (x264/x265/SVT-AV1), NVIDIA NVENC, AMD AMF, Apple VideoToolbox
- **Resolution presets** — Source, 4K, 1440p, 1080p, 720p, 480p, or custom; scaling is height-driven with width computed to match, so non-16:9 sources aren't stretched
- **Hardware-accelerated decode** — CUDA / DXVA2 / VideoToolbox (FFmpeg backend)
- **Interface language picker** — English plus a globe menu of other languages (translations load from an optional `i18n/` folder of JSON files; the app runs in English automatically if it isn't present), with a "System default" option that follows Windows' language
- **Drag and drop** — drop one or more video files anywhere on the window to add them straight to the queue
- **Live run controls** — pause/resume, live CPU-core affinity, and process priority, all adjustable mid-conversion
- **Live resource monitoring** — CPU and GPU usage shown while converting
- **Live activity log** — a live log of the actual FFmpeg/HandBrake output as it runs
- **One-click Windows setup** — missing FFmpeg/HandBrakeCLI installed via `winget` from inside the app, with a "Quick install" shortcut right in the header so you don't need to open a panel first. If `winget` itself isn't available, the app falls back to downloading the same official portable builds directly (no admin rights needed)
- **Transparent by design** — the exact command line is always visible, never hidden behind "auto"
- Light and dark themes

## Requirements

| Component | Needed for |
|---|---|
| Python 3.10+ | Running the app itself |
| PySide6 | The Qt-based UI |
| FFmpeg *(full/GPL build, with libx264 + libx265 + libsvtav1)* | Standard and GPU tone-mapping backends, plus AV1 output |
| Vulkan-capable GPU + drivers | The libplacebo/BT.2390 backend specifically |
| HandBrakeCLI *(optional)* | The experimental HandBrake backend |
| psutil *(optional)* | Live pause/resume, CPU-core control, resource stats |
| `i18n/` folder *(optional)* | Non-English interface languages — drop in `<code>.json` files; without it the app runs in English |

On Windows, missing FFmpeg/HandBrakeCLI can be installed automatically from inside the app via `winget` — see the System panel, or the "Quick install" shortcut in the header. If `winget` isn't available, the app downloads the same official portable builds directly instead. This pulls the right build automatically, so you don't need to hunt for the correct FFmpeg variant yourself; see [Dependencies & licensing](#dependencies--licensing) below for exactly what gets installed and from where.

## Dependencies & licensing

This app doesn't bundle FFmpeg or HandBrakeCLI — they're installed on your
own machine, straight from their authors' official channels, via `winget`
(or downloaded directly as a portable copy when `winget` isn't available):

| Tool | Source | winget package ID | License |
|---|---|---|---|
| FFmpeg | [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) full build | `Gyan.FFmpeg` | GPLv3 |
| HandBrakeCLI | [HandBrake](https://handbrake.fr) official release | `HandBrake.HandBrake.CLI` | GPLv2 |

AV1 encoding uses SVT-AV1 (`libsvtav1`), which already ships in the same
FFmpeg full build above — BSD-2-Clause + Patent, part of the Alliance for
Open Media's royalty-free AV1 ecosystem.

Both FFmpeg and HandBrake are excellent, independent open-source projects —
full credit to their authors and contributors. This app is a GUI front end
that shells out to them as separate processes; it doesn't modify, link
against, or redistribute their code, and each keeps its own license
regardless of this project's MIT license (see [License](#license) below).

Prefer to install them yourself instead of using the in-app installer? That
works too — the app just looks for `ffmpeg`, `ffprobe`, and `HandBrakeCLI`
on your `PATH`.

## Install & run

```bash
git clone https://github.com/devgodys/HDR-to-SDR-Video-Converter.git
cd HDR-to-SDR-Video-Converter
pip install -r requirements.txt
python hdr_to_sdr_gui_qt.py
```

Or grab a prebuilt Windows `.exe` from the [latest release](https://github.com/devgodys/HDR-to-SDR-Video-Converter/releases/latest) — no Python required, though FFmpeg is still needed separately.

### 10-bit / 12-bit output

10-bit and 12-bit are built into the CONVERSION card as a bit-depth selector — no separate script or build. Picking a depth filters the encoder list down to whatever your FFmpeg/HandBrakeCLI build can actually produce at that depth (e.g. only libx265 for 12-bit), rather than offering combinations that don't exist. Free, no license needed.

## Basic workflow

1. **Add videos** to the queue — via the file picker or by dragging one or more files onto the window — and the **SDR output** path is filled in for each (next to the source by default, or in a single output folder you choose). Adding is instant; nothing is analyzed yet.
2. Select a queued file and click **Analyze selected video** (or just start the queue — each file is analyzed automatically right before its turn) to read its transfer function, bit depth, color range, duration, and HDR10+/Dolby Vision metadata, with a curve suggested based on what it finds.
3. Once analyzed, either use **Optimal** / **Best quality** / **Fast** to one-click apply a recommended curve/encoder/quality combo, or set things manually: choose a **backend** and **curve** — BT.2390 on GPU is the sane default; fall back to Standard FFmpeg without a Vulkan-capable GPU (hover a curve for a plain-language rundown of what it trades off) — plus **resolution**, **encoder** (H.264, H.265, or AV1), **bit depth**, and **container** (MP4, or MKV if the source has subtitle/audio tracks MP4 can't hold). Leave resolution on "Source" to keep the original frame size and aspect ratio. Audio and subtitle tracks are always copied through untouched.
4. Adjust **quality**, or enable **Pro mode** for the full CRF/CQ range, extra curves, and a brightness trim. Check the **Live Preview** panel — it shows an actual tone-mapped frame and flags shadow/highlight clipping, and you can view it full size or save the frame to disk — plus the live bitrate estimate, before committing.
5. Click **Start queue** — each file converts in turn, with progress, speed, ETA, and live FFmpeg/HandBrake output all shown as it runs (a low-disk-space check runs first). Pause/resume, CPU-core limits, and process priority can all be adjusted mid-conversion.

## Building a standalone executable

Windows only (this builds a `.exe`, so it has to run on Windows). Drop
`icon.ico` *and* an `icons/` folder (containing `icon-<size>.png` files,
e.g. `icon-16.png`, `icon-32.png`, `icon-256.png`) next to the script
beforehand — both get bundled and are picked up automatically, in-app and
as the exe's file icon. If you're using the interface-language picker,
also include your `i18n/` folder so translations ship with the build.

```bat
pip install pyinstaller
```

Then run this as **one single line** in `cmd.exe` or PowerShell
(copy-pasting a multi-line `\`-continued Unix-style command into `cmd.exe`
will silently break it into separate, broken commands — `cmd` doesn't
understand `\` as a line continuation):

```bat
python -m PyInstaller --noconfirm --windowed --onefile --name "HDR-to-SDR-Video-Converter" --icon icon.ico --add-data "icon.ico;." --add-data "icons;icons" --add-data "i18n;i18n" hdr_to_sdr_gui_qt.py
```

`python -m PyInstaller` (rather than bare `pyinstaller`) is deliberate: pip
often installs the `pyinstaller` console script into a `Scripts\` folder
that isn't on your `PATH`, which gives a `'pyinstaller' is not recognized`
error even though the package installed fine. `python -m PyInstaller` runs
it as a module instead, so it works as long as `python` itself is on `PATH`
— no separate PATH fix needed. `pyinstaller` on its own works fine too as
long as its console script actually is on your `PATH`.

If you'd rather split it across multiple lines for readability, use `^` (not
`\`) as the line-continuation character in `cmd.exe`:

```bat
python -m PyInstaller --noconfirm --windowed --onefile --name "HDR-to-SDR-Video-Converter" ^
  --icon icon.ico ^
  --add-data "icon.ico;." ^
  --add-data "icons;icons" ^
  --add-data "i18n;i18n" ^
  hdr_to_sdr_gui_qt.py
```

There's only one entry point — 10-bit/12-bit output and AV1 support are part of the same build, not a separate exe.

## Troubleshooting

- **BT.2390 (GPU) option greyed out** — no Vulkan-capable device was detected; use a Standard FFmpeg curve instead.
- **"Encoder unavailable" for CPU · H.264/H.265/AV1** — you likely have an LGPL/"essentials" FFmpeg build without `libx264`/`libx265`/`libsvtav1`. Use the in-app **Quick install** (installs the full GPL build automatically), or grab the "full" build yourself from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/).
- **HandBrakeCLI backend missing** — it's optional and only appears once HandBrakeCLI is found on your PATH.
- **Interface language not changing** — the app falls back to English for any language without a matching `i18n/<code>.json` file next to the script.
- **Conversion feels like it's taking over the machine** — drop Process priority to Efficiency, or lower the CPU-core count live during a run.

## Support

If this saved you a re-encode: [♥ Support on Ko-fi](https://ko-fi.com/devgodys)

## License

[MIT](LICENSE)
