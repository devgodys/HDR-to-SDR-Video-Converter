#!/usr/bin/env python3
"""Backward-compatible launcher for HDR to SDR Video Converter.

You can run the application either via:
    python -m hdr2sdr
or:
    python hdr_to_sdr_gui_qt.py
"""
from __future__ import annotations

from hdr2sdr.__main__ import main

if __name__ == "__main__":
    main()
