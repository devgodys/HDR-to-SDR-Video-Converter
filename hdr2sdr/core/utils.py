from __future__ import annotations

import os
import shutil
import subprocess
import sys

from hdr2sdr.core.constants import _NUM
from hdr2sdr.paths import (
    PORTABLE_TOOLS_DIR, ROOT,
)




# ---- small helpers -----------------------------------------------------

def exe(n):
    names = (n + ".exe", n) if sys.platform.startswith("win") else (n,)
    locations = (ROOT, PORTABLE_TOOLS_DIR)
    for location in locations:
        found = next((str(location / x) for x in names if (location / x).is_file()), None)
        if found:
            return found
    return shutil.which(n)


def probe_streams(src, timeout=20):
    """Audio/subtitle streams of `src` as ((index, codec_type, codec_name), ...)
    via a quick ffprobe call, or () if ffprobe is missing / fails / times out
    - the caller then simply keeps the old "map and copy everything"
    behaviour. Never raises."""
    import json
    probe = exe("ffprobe")
    if not probe or not src:
        return ()
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "stream=index,codec_type,codec_name",
             "-of", "json", src],
            capture_output=True, text=True, timeout=timeout, **no_window_kwargs())
        streams = json.loads(out.stdout or "{}").get("streams", [])
        return tuple((int(st["index"]), st.get("codec_type", ""), st.get("codec_name", ""))
                     for st in streams if st.get("codec_type") in ("audio", "subtitle"))
    except Exception:
        return ()


def wrappable(path):
    """A raw path like "C:\\Users\\...\\HandBrakeCLI.EXE" has no spaces, so
    Qt's word-wrap treats it as one unbreakable word and stretches the
    whole panel to fit it. A zero-width space is invisible but gives the
    wrapper somewhere to break the line - used anywhere a full path gets
    displayed in a label/log that needs to stay narrow."""
    return path.replace("\\", "\\\u200b").replace("/", "/\u200b")


def parse_num(s):
    if not s:
        return None
    m = _NUM.match(s.strip())
    return float(m.group()) if m else None


def format_time(seconds):
    seconds = max(0, int(seconds))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def clock_time(seconds):
    """Compact clock-style timecode (1:23:45 or 12:34) for the Live Preview
    position readout - format_time()'s "1h 19m 59s" style reads better for
    an ETA, but a "position / total" line wants the shorter clock form."""
    seconds = max(0, int(seconds))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def even(n):
    n = int(n)
    return max(2, n - (n % 2))


def detect_chroma_subsampling(val: dict | str | None) -> str | None:
    """Detect chroma subsampling (e.g. '4:4:4', '4:2:2', '4:2:0') from a stream dict or pix_fmt string."""
    if not val:
        return None
    if isinstance(val, dict):
        pf = str(val.get("pix_fmt") or "").lower()
        if not pf:
            raw = str(val).lower()
            if "4:4:4" in raw or "yuv444" in raw:
                return "4:4:4"
            if "4:2:2" in raw or "yuv422" in raw:
                return "4:2:2"
            if "4:2:0" in raw or "yuv420" in raw:
                return "4:2:0"
            return None
    else:
        pf = str(val).lower()

    if "444" in pf or pf.startswith(("rgb", "bgr", "gbr")) or pf in ("nv24", "p410le", "p416le", "ayuv64le"):
        return "4:4:4"
    if "422" in pf or pf in ("nv16", "nv20le", "p210le", "p216le", "yuyv422", "uyvy422"):
        return "4:2:2"
    if "420" in pf or pf in ("nv12", "nv21", "p010le", "p016le"):
        return "4:2:0"
    if "411" in pf:
        return "4:1:1"
    if "410" in pf:
        return "4:1:0"
    if "440" in pf:
        return "4:4:0"
    return None


# ==================== END PURE CORE - process output parsing ==========


def no_window_kwargs():
    # A PyInstaller --windowed build has no console, so sys.stdin/stdout/
    # stderr don't exist - subprocess normally tries to inherit those
    # handles, and on Windows that fails with "OSError: [WinError 6] The
    # handle is invalid" the moment anything calls subprocess.check_output.
    # Giving stdin an explicit target (rather than "inherit") sidesteps it;
    # stdout/stderr are already redirected explicitly by every caller below.
    return dict(creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdin=subprocess.DEVNULL)


def refresh_windows_path():
    """After winget installs something, its own installer updates the
    registry's PATH - but this already-running process won't see that
    until we re-read it, same idea as the PowerShell setup script's
    Update-SessionPath. Without this, a tool installed a moment ago still
    looks missing to shutil.which()/exe() for the rest of this session."""
    if not sys.platform.startswith("win"):
        return
    try:
        import winreg
        parts = []
        for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                           (winreg.HKEY_CURRENT_USER, r"Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    parts.append(val)
            except OSError:
                pass
        if parts:
            # REG_EXPAND_SZ values hold %SystemRoot% etc.; keep whatever this process already had
            merged, seen = [], set()
            for chunk in (*parts, os.environ.get("PATH", "")):
                for entry in os.path.expandvars(chunk).split(";"):
                    key = os.path.normcase(entry.strip().rstrip("\\"))
                    if key and key not in seen:
                        seen.add(key)
                        merged.append(entry.strip())
            os.environ["PATH"] = ";".join(merged)
    except Exception:
        pass


_SEEN_SUPPRESSED = set()


def log_suppressed(where=""):
    """For `except Exception:` blocks that deliberately carry on: records the error (once per
    distinct place/message) in errors.log instead of dropping it, so silent failures are findable."""
    import time
    import traceback
    try:
        msg = traceback.format_exc()
        key = (where, msg.strip().splitlines()[-1] if msg.strip() else "")
        if key in _SEEN_SUPPRESSED or len(_SEEN_SUPPRESSED) > 200:
            return
        _SEEN_SUPPRESSED.add(key)
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
        folder = os.path.join(base, "HDR-to-SDR-Converter")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "errors.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + f"[suppressed {where}] " + msg + "\n")
    except Exception:
        pass
