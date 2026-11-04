# Dependencies: two channels

Both for the Python packages the app is built from and for the portable tools
(FFmpeg, HandBrakeCLI) it downloads, there are two channels. **Stable** targets
Python 3.15, is the default and what releases use; **Latest** exists to find problems with new
versions early.

## Python packages

| | file | what it is |
|---|---|---|
| **stable** | `requirements-stable.txt` | exact versions **+ SHA-256 hashes** for every package and sub-dependency, locked for Python 3.15. pip refuses anything that doesn't match. |
| stable (build) | `requirements-build-stable.txt` | the same plus PyInstaller, also hash-locked |
| **latest** | `requirements-latest.txt` | no pins: whatever is newest on PyPI right now |
| latest (build) | `requirements-build-latest.txt` | plus the newest PyInstaller |

Sources of truth are the `.in` files (`requirements.in` = what the code
needs, loosely; `requirements-stable.in` / `requirements-build-stable.in` =
the exact stable versions, locked for Python 3.15). `requirements.txt` just points
at the stable lock, so a plain `pip install -r requirements.txt` is the safe one.

```
python tools/deps.py test --channel stable      # venv + install (hash-checked) + run the tests
python tools/deps.py test --channel latest      # same with the newest of everything
python tools/deps.py install --channel stable --build   # venv ready for PyInstaller
python tools/deps.py outdated                   # which pins have newer releases on PyPI
python tools/deps.py lock                       # regenerate the stable locks (needs `uv`)
python tools/deps.py lock --check               # verify the locks match the .in files
Compilation.bat stable|latest                   # build the exe from a channel (default stable)
```

Routine: run `test --channel latest` every now and then (e.g. weekly or in
CI). If it fails, that is an early warning - pin stays where it is, fix the
code, then bump `requirements-stable.in` and run `lock`.

The stable install, test, and build commands require Python 3.15. Use the
Python 3.15 interpreter explicitly when more than one Python version is
installed. The stable Qt binding is PySide6 6.12.0, which adds initial Python
3.15 support.

## Portable tools (FFmpeg, HandBrakeCLI)

The "Tools: stable / latest" selector in the Conversion panel chooses what
the portable downloader installs (it only matters where winget isn't used, or
isn't available). Everything is SHA-256 verified before it is installed; a
mismatch deletes the download and installs nothing. What's installed is
recorded in `tools/installed.json` next to the executables.

* **stable** - builds pinned in `hdr2sdr/runtime/tools.py` together with their
  hashes: FFmpeg `n8.1.3` (BtbN autobuild `autobuild-2026-10-05-13-07`) and
  HandBrakeCLI `1.11.2`. Same bytes for everyone. If upstream has since
  deleted the pinned FFmpeg build (BtbN prunes old ones) the newest build of
  the same release branch is used instead, verified against upstream's
  published checksums, and the log says so.
* **latest** - FFmpeg `master` and the newest HandBrake release, verified
  against the checksum upstream publishes next to them (BtbN's
  `checksums.sha256`, GitHub's per-asset digest). This catches corruption or
  tampering in transit; it cannot protect against a compromised upstream
  release - only the pinned stable hash can.

Refreshing the pins (with a release of this app): take the newest
`autobuild-*` tag and the hash from its `checksums.sha256`, the newest
HandBrake tag and its asset digest, put them into `runtime/tools.py`, run the
tests.
