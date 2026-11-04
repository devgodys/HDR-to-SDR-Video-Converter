"""Download the portable FFmpeg / HandBrakeCLI builds, verified.

Two channels, chosen by the person in the app (default: stable):

  stable  A specific build pinned in this file together with its SHA-256.
          Same bytes for everyone, every time, and the download is refused
          if it doesn't hash to the pinned value. If upstream has since
          deleted the pinned FFmpeg build (BtbN prunes old autobuilds), the
          newest build of the same release branch is used instead - still
          verified, against upstream's own published checksums, and the
          log says so.
  latest  The newest upstream build (FFmpeg master, newest HandBrake
          release), verified against the checksum upstream publishes next
          to it (BtbN's checksums.sha256, GitHub's per-asset digest). For
          catching problems with new FFmpeg/HandBrake versions early.

Verification protects against corrupted / tampered downloads. For "latest"
the checksum comes from the same upstream, so it can't protect against a
compromised upstream release - only "stable" (hash baked into this app
version) can, for the pinned build.

No Qt in here: the GUI runs install() on a worker thread, and the tests
run it against a local HTTP server.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STABLE = "stable"
LATEST = "latest"
CHANNELS = (STABLE, LATEST)

# ---- pins (refresh together with a release of this app) -------------------
# Taken from upstream's published checksums on 2026-10-06 and re-verified by
# downloading the files and hashing them.
BTBN_RELEASES = "https://github.com/BtbN/FFmpeg-Builds/releases"
STABLE_FFMPEG_TAG = "autobuild-2026-10-05-13-07"
STABLE_FFMPEG_ASSET = "ffmpeg-n8.1.3-14-g330caae0c1-win64-gpl-8.1.zip"
STABLE_FFMPEG_SHA256 = "ce8f117aa804906c4a190367babfeeb0b9c8b2ea85ac128fa381b407f964af85"
# Fallback when the pinned build was pruned upstream: rolling build of the same branch.
STABLE_FFMPEG_BRANCH_ASSET = "ffmpeg-n8.1-latest-win64-gpl-8.1.zip"
LATEST_FFMPEG_ASSET = "ffmpeg-master-latest-win64-gpl.zip"

HANDBRAKE_RELEASES = "https://github.com/HandBrake/HandBrake/releases"
HANDBRAKE_LATEST_API = "https://api.github.com/repos/HandBrake/HandBrake/releases/latest"
STABLE_HANDBRAKE_TAG = "1.11.2"
STABLE_HANDBRAKE_ASSET = "HandBrakeCLI-1.11.2-win-x86_64.zip"
STABLE_HANDBRAKE_SHA256 = "80bfe8d5f5d11cc3ef76b834add3ed4e82dee6523ffeb435c283f88b1a21f09d"
_HANDBRAKE_ASSET_RE = re.compile(r"^HandBrakeCLI-.*-win-x86_64\.zip$")

_USER_AGENT = "HDR-to-SDR-Converter portable setup"
_CHUNK = 1 << 20
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
# Generous headroom for current Windows builds while bounding disk use when
# an upstream URL returns an unexpectedly large or unbounded response.
MAX_DOWNLOAD_BYTES = 1 << 30          # 1 GiB compressed archive
MAX_EXTRACTED_BYTES = 1 << 30         # 1 GiB total selected executables


class ToolInstallError(Exception):
    """A tool couldn't be installed (message is meant to be shown to the person)."""


@dataclass(frozen=True)
class ToolSource:
    name: str            # "FFmpeg" / "HandBrakeCLI"
    url: str
    sha256: str
    label: str           # what to show in the log, e.g. "FFmpeg n8.1.3 (pinned)"
    exes: tuple          # executables to pull out of the archive; first one is required
    required: tuple = ()

    def __post_init__(self):
        if not _SHA256_RE.match(self.sha256 or ""):
            raise ToolInstallError(f"No valid SHA-256 for {self.name}; refusing to download it unverified.")


# ---- small helpers ---------------------------------------------------------
def parse_checksums(text):
    """Parse `sha256sum`-style text ("<hash>  <name>" or "<hash> *<name>") into {name: hash}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _request(url, method="GET", timeout=30):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def _get_bytes(url, timeout=30):
    try:
        with _request(url, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise ToolInstallError(f"GitHub refused the request ({e.code}) - probably its rate limit; try again later.") from e
        raise ToolInstallError(f"Could not fetch {url} (HTTP {e.code}).") from e
    except (urllib.error.URLError, OSError) as e:
        raise ToolInstallError(f"Could not reach {url}: {e}") from e


def _exists(url, timeout=30):
    """True unless the server says 404/410. Any other trouble counts as 'exists': the real download will report it."""
    try:
        with _request(url, method="HEAD", timeout=timeout):
            return True
    except urllib.error.HTTPError as e:
        return e.code not in (404, 410)
    except (urllib.error.URLError, OSError):
        return True


# ---- resolving what to download -------------------------------------------
def resolve_ffmpeg(channel, log=lambda s: None):
    exes = ("ffmpeg.exe", "ffprobe.exe")
    if channel == STABLE:
        url = f"{BTBN_RELEASES}/download/{STABLE_FFMPEG_TAG}/{STABLE_FFMPEG_ASSET}"
        if _exists(url):
            return ToolSource("FFmpeg", url, STABLE_FFMPEG_SHA256,
                              f"FFmpeg {_ffmpeg_version_of(STABLE_FFMPEG_ASSET)} (stable, pinned)", exes, ("ffmpeg.exe",))
        log("[setup] The pinned FFmpeg build is no longer available upstream; using the newest build of the same "
            "release branch instead (verified against upstream's checksums).")
        asset = STABLE_FFMPEG_BRANCH_ASSET
        label = f"FFmpeg {_ffmpeg_version_of(asset)} (stable branch, newest build)"
    else:
        asset = LATEST_FFMPEG_ASSET
        label = "FFmpeg master (latest build)"
    base = f"{BTBN_RELEASES}/download/latest"
    sums = parse_checksums(_get_bytes(f"{base}/checksums.sha256").decode("utf-8", "replace"))
    if asset not in sums:
        raise ToolInstallError(f"Upstream's checksum list has no entry for {asset}; refusing to install it unverified.")
    return ToolSource("FFmpeg", f"{base}/{asset}", sums[asset], label, exes, ("ffmpeg.exe",))


def _ffmpeg_version_of(asset):
    m = re.search(r"ffmpeg-(n?[\d.]+|master)", asset)
    return m.group(1) if m else asset


def resolve_handbrake(channel, log=lambda s: None):
    exes = ("HandBrakeCLI.exe",)
    if channel == STABLE:
        url = f"{HANDBRAKE_RELEASES}/download/{STABLE_HANDBRAKE_TAG}/{STABLE_HANDBRAKE_ASSET}"
        return ToolSource("HandBrakeCLI", url, STABLE_HANDBRAKE_SHA256,
                          f"HandBrakeCLI {STABLE_HANDBRAKE_TAG} (stable, pinned)", exes, exes)
    try:
        release = json.loads(_get_bytes(HANDBRAKE_LATEST_API).decode("utf-8"))
    except ValueError as e:
        raise ToolInstallError("GitHub returned something that isn't valid release data.") from e
    asset = next((a for a in release.get("assets", []) if _HANDBRAKE_ASSET_RE.match(a.get("name", ""))), None)
    if not asset:
        raise ToolInstallError("The latest HandBrake release has no Windows x64 CLI archive.")
    digest = str(asset.get("digest") or "")
    if not digest.lower().startswith("sha256:"):
        raise ToolInstallError("GitHub published no SHA-256 for the latest HandBrakeCLI archive; "
                               "refusing to install it unverified (use the stable channel).")
    return ToolSource("HandBrakeCLI", asset["browser_download_url"], digest.split(":", 1)[1],
                      f"HandBrakeCLI {release.get('tag_name', '?')} (latest release)", exes, exes)


# ---- download / verify / extract -------------------------------------------
def download_file(url, dest, progress=None, cancel=lambda: False, timeout=60,
                  max_bytes=MAX_DOWNLOAD_BYTES):
    """Stream `url` to `dest` (via dest.part). `progress(percent|None)`."""
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with _request(url, timeout=timeout) as r, open(part, "wb") as f:
            try:
                total = int(r.headers.get("Content-Length") or 0)
            except (TypeError, ValueError) as e:
                raise ToolInstallError("Download returned an invalid Content-Length.") from e
            if total > max_bytes:
                raise ToolInstallError(
                    f"Download exceeds the {max_bytes // (1 << 20)} MiB size limit.")
            done = 0
            while True:
                if cancel():
                    raise ToolInstallError("Cancelled.")
                chunk = r.read(_CHUNK)
                if not chunk:
                    break
                if done + len(chunk) > max_bytes:
                    raise ToolInstallError(
                        f"Download exceeds the {max_bytes // (1 << 20)} MiB size limit.")
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done * 100 // total if total else None)
        os.replace(part, dest)
    except ToolInstallError:
        part.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as e:
        part.unlink(missing_ok=True)
        raise ToolInstallError(f"Download failed (HTTP {e.code}): {url}") from e
    except (urllib.error.URLError, OSError) as e:
        part.unlink(missing_ok=True)
        raise ToolInstallError(f"Download failed: {e}") from e


def verify_sha256(path, expected):
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        Path(path).unlink(missing_ok=True)
        raise ToolInstallError(
            f"Checksum mismatch - the downloaded file was deleted and NOT installed.\n"
            f"  expected {expected.lower()}\n  got      {actual}")
    return actual


def extract_executables(zip_path, wanted, required, dest_dir, record=None,
                        max_extracted_bytes=MAX_EXTRACTED_BYTES):
    """Copy the named executables out of the archive into dest_dir. Only the exact file names
    asked for are ever written, and always directly into dest_dir, so the archive can't place
    files anywhere else (no path traversal).

    Two phases, so a failure half-way cannot leave a mixed old/new tool folder: every file is first
    extracted to "<name>.new" and checked; only when ALL of them are fine are they swapped in."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    staged = []                                   # (name, tmp path)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            by_name = {}
            for info in zf.infolist():
                if not info.is_dir():
                    by_name.setdefault(Path(info.filename).name.lower(), info)
            selected_total = 0
            written_total = 0
            for name in wanted:
                info = by_name.get(name.lower())
                if info is None:
                    if name in required:
                        raise ToolInstallError(f"The archive did not contain {name}.")
                    continue
                if info.file_size < 0 or info.file_size > max_extracted_bytes:
                    raise ToolInstallError(
                        f"{name} exceeds the {max_extracted_bytes // (1 << 20)} MiB extraction limit.")
                if selected_total + info.file_size > max_extracted_bytes:
                    raise ToolInstallError(
                        f"Selected files exceed the {max_extracted_bytes // (1 << 20)} MiB extraction limit.")
                selected_total += info.file_size
                tmp = dest_dir / (name + ".new")
                staged.append((name, tmp))
                written = 0
                with zf.open(info) as src, open(tmp, "wb") as out:
                    while True:
                        chunk = src.read(_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        written_total += len(chunk)
                        if (written > info.file_size or written_total > max_extracted_bytes):
                            raise ToolInstallError(f"{name} expanded beyond its declared size.")
                        out.write(chunk)
                if written != info.file_size:
                    raise ToolInstallError(f"{name} size does not match the archive directory.")
                if tmp.stat().st_size == 0:
                    raise ToolInstallError(f"{name} in the archive is empty.")
        if record is not None:
            record_path = dest_dir / "installed.json"
            try:
                metadata = json.loads(record_path.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    metadata = {}
            except (OSError, ValueError):
                metadata = {}
            metadata.update(record)
            record_tmp = record_path.with_suffix(".json.new")
            record_tmp.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            staged.append((record_path.name, record_tmp))
        found = []
        backups = []                              # (target, backup) of files already replaced
        published = []
        try:
            for name, tmp in staged:              # everything extracted fine: publish
                target = dest_dir / name
                if target.exists():
                    bak = dest_dir / (name + ".bak")
                    os.replace(target, bak)
                    backups.append((target, bak))
                os.replace(tmp, target)
                if name != "installed.json":
                    found.append(name)
                published.append(target)
        except OSError:
            for target in reversed(published):
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            for target, bak in reversed(backups):  # put the old working set back
                try:
                    os.replace(bak, target)
                except OSError:
                    pass
            raise
        for _target, bak in backups:
            try:
                bak.unlink(missing_ok=True)
            except OSError:
                pass
        return found
    except (zipfile.BadZipFile, OSError, EOFError) as e:
        raise ToolInstallError(f"Could not extract the archive: {e}") from e
    finally:
        for _name, tmp in staged:                 # nothing half-written is left behind
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _record_entry(source, sha, channel):
    return {source.name: {
        "channel": channel, "label": source.label, "url": source.url, "sha256": sha,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
    }}


def install(names, channel, tools_dir, log=print, cancel=lambda: False):
    """Install the tools in `names` ("FFmpeg", "HandBrakeCLI") from `channel`.
    Returns True only if every one installed and verified. Never raises."""
    if channel not in CHANNELS:
        channel = STABLE
    work = Path(tempfile.mkdtemp(prefix="hdr2sdr-setup-"))
    resolvers = {"FFmpeg": resolve_ffmpeg, "HandBrakeCLI": resolve_handbrake}
    ok = True
    try:
        for name in names:
            try:
                source = resolvers[name](channel, log)
                log(f"[setup] {source.label}")
                log(f"[setup] Downloading {source.url}")
                zip_path = work / (name + ".zip")
                last = [-10]

                def progress(pct, last=last):
                    if pct is not None and pct >= last[0] + 10:
                        last[0] = pct // 10 * 10
                        log(f"[setup]   {last[0]}%")

                download_file(source.url, zip_path, progress, cancel)
                sha = verify_sha256(zip_path, source.sha256)
                log(f"[setup] SHA-256 verified: {sha}")
                installed = extract_executables(
                    zip_path, source.exes, source.required, tools_dir,
                    record=_record_entry(source, sha, channel))
                log(f"[setup] {name} installed ({', '.join(installed)}).")
            except ToolInstallError as e:
                ok = False
                log(f"[setup] {name}: {e}")
                if str(e) == "Cancelled.":
                    break
            except Exception as e:                      # never let a worker thread die silently
                ok = False
                log(f"[setup] {name}: unexpected error: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if ok:
        log("[setup] Portable tools are ready.")
    return ok
