# Open HDR to SDR Converter

A desktop tone-mapping tool for people who care which curve they're using. Standard FFmpeg mapping for speed, true BT.2390 on the GPU for accuracy, and an experimental HandBrakeCLI path — all in one window, with the actual command being run always visible.

![platform](https://img.shields.io/badge/platform-Windows-informational)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![grandma approved](https://img.shields.io/badge/grandma-approved-ff69b4)

**[Website](https://godysdev.github.io/Open-HDR-to-SDR-convertor/)** · **[Latest release](https://github.com/godysdev/Open-HDR-to-SDR-convertor/releases/latest)**

![Light mode](webassets/app-screenshot-light.png)

## Why

Good HDR→SDR tone mapping shouldn't cost money, and it shouldn't require learning FFmpeg first.

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
- **Full curve library** — Hable, Reinhard, Mobius, BT.2446A, ST2094-10/40, Linear, Gamma, Clip, None
- **Hardware encoding** — CPU (x264/x265), NVIDIA NVENC, AMD AMF, Apple VideoToolbox
- **Resolution presets** — Source, 4K, 1440p, 1080p, 720p, 480p, or custom width/height
- **Hardware-accelerated decode** — CUDA / DXVA2 / VideoToolbox (FFmpeg backend)
- **Live run controls** — pause/resume, live CPU-core affinity, and process priority, all adjustable mid-conversion
- **Live resource monitoring** — CPU and GPU usage shown while converting
- **Activity & Capabilities panels** — a live log of the actual FFmpeg/HandBrake output, and a report of what's detected on your system (FFmpeg, Vulkan, HandBrakeCLI) before you convert
- **One-click Windows setup** — missing FFmpeg/HandBrakeCLI installed via `winget` from inside the app
- **Transparent by design** — the exact command line is always visible, never hidden behind "auto"
- Light and dark themes

## Requirements

| Component | Needed for |
|---|---|
| Python 3.10+ | Running the app itself |
| PySide6 | The Qt-based UI |
| FFmpeg | Standard and GPU tone-mapping backends |
| Vulkan-capable GPU + drivers | The libplacebo/BT.2390 backend specifically |
| HandBrakeCLI *(optional)* | The experimental HandBrake backend |
| psutil *(optional)* | Live pause/resume, CPU-core control, resource stats |

On Windows, missing FFmpeg/HandBrakeCLI can be installed automatically from inside the app via `winget` — see the Capabilities panel.

## Install & run

```bash
git clone https://github.com/godysdev/Open-HDR-to-SDR-convertor.git
cd Open-HDR-to-SDR-convertor
pip install -r requirements.txt
python hdr_to_sdr_gui_qt.py
```

Or grab a prebuilt Windows `.exe` from the [latest release](https://github.com/godysdev/Open-HDR-to-SDR-convertor/releases/latest) — no Python required, though FFmpeg is still needed separately.

## Basic workflow

1. Pick a **Source** HDR file and an **SDR output** path, then click **Analyze**.
2. Choose a **backend** and **curve** — BT.2390 on GPU is the sane default; fall back to Standard FFmpeg without a Vulkan-capable GPU.
3. Set **resolution** and **encoder**. Leave resolution on "Source" to keep the original frame size.
4. Adjust **quality**, or enable **Pro mode** for the full CRF/CQ range, extra curves, and a brightness trim.
5. Click **Start** — progress, speed, ETA, and live FFmpeg/HandBrake output are all shown as it runs.

## Building a standalone executable

```bash
pip install pyinstaller
pyinstaller --windowed --onefile --icon=icon.ico hdr_to_sdr_gui_qt.py
```

Drop `icon.ico` next to the script beforehand and it's picked up automatically, both in-app and as the exe's file icon.

## Troubleshooting

- **BT.2390 (GPU) option greyed out** — no Vulkan-capable device was detected; use a Standard FFmpeg curve instead.
- **HandBrakeCLI backend missing** — it's optional and only appears once HandBrakeCLI is found on your PATH.
- **Conversion feels like it's taking over the machine** — drop Process priority to Efficiency, or lower the CPU-core count live during a run.

## Support

If this saved you a re-encode: [☕ Ko-fi](https://ko-fi.com/devgodys)

## License

[MIT](LICENSE)
