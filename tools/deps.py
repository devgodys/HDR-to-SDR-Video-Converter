#!/usr/bin/env python3
"""Dependency channels for HDR-to-SDR Converter (see DEPENDENCIES.md).

  stable  requirements-stable.txt: exact versions + SHA-256 hashes (pip refuses
          anything that doesn't match). What releases are built from.
  latest  requirements-latest.txt: newest PyPI versions, no pins. Run the tests
          against it regularly to catch problems with new releases early.

Commands
  install [--channel stable|latest] [--build] [--venv DIR]
          Create/refresh a virtual environment (default .venv-<channel>) and
          install the channel into it (--build adds PyInstaller).
  test    [--channel stable|latest] [--include-golden]
          install + run the test-suite headless in that environment
          (excludes tests/test_golden_snapshot.py by default per project rule).
  lock    [--check]
          Regenerate the stable lock files from the .in files with `uv`;
          --check only verifies they are up to date (exit 1 if not).
  outdated
          Show which pinned stable versions have newer releases on PyPI.
Add --print to any command to show what would run without running it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON_FOR_LOCK = "3.15"          # stable locks target this Python minor release


def require_stable_python():
    if sys.version_info[:2] != (3, 15):
        sys.exit(
            "The stable dependency channel targets Python 3.15. "
            f"Current interpreter: {sys.version_info.major}.{sys.version_info.minor}."
        )


def requirements_file(channel, build):
    return ROOT / (f"requirements-build-{channel}.txt" if build else f"requirements-{channel}.txt")


def venv_python(venv):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_matches_current_python(py: Path) -> bool:
    if not py.exists():
        return False
    try:
        out = subprocess.check_output(
            [str(py), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
        return out == f"{sys.version_info.major}.{sys.version_info.minor}"
    except (OSError, subprocess.SubprocessError):
        return False


def run(cmd, show_only=False, **kw):
    print("$", " ".join(str(c) for c in cmd))
    if show_only:
        return 0
    return subprocess.call([str(c) for c in cmd], **kw)


def cmd_install(args):
    if args.channel == "stable" and not args.print:
        require_stable_python()
    venv = Path(args.venv) if args.venv else ROOT / f".venv-{args.channel}"
    req = requirements_file(args.channel, args.build)
    if not req.exists():
        sys.exit(f"missing {req.name}")
    py = venv_python(venv)
    if not py.exists():
        if run([sys.executable, "-m", "venv", venv], args.print):
            return 1
    elif not args.print and not _venv_matches_current_python(py):
        if run([sys.executable, "-m", "venv", "--clear", venv], args.print):
            return 1
    pip = [py, "-m", "pip", "install", "--upgrade" if args.channel == "latest" else "--no-deps"]
    if args.channel == "stable":
        # hash-locked: every package (and its dependencies) is in the file with hashes
        pip = [py, "-m", "pip", "install", "--require-hashes"]
    return run(pip + ["-r", req], args.print)


def cmd_test(args):
    if args.channel == "stable" and not args.print:
        require_stable_python()
    rc = cmd_install(args)
    if rc:
        return rc
    venv = Path(args.venv) if args.venv else ROOT / f".venv-{args.channel}"
    py = venv_python(venv)
    if run([py, "-m", "pip", "install", "pytest"], args.print):
        return 1
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    print(f"== running the tests against the {args.channel} channel ==")
    pytest_cmd = [py, "-m", "pytest", "-q"]
    if not getattr(args, "include_golden", False):
        pytest_cmd.append(f"--ignore={ROOT / 'tests' / 'test_golden_snapshot.py'}")
    pytest_cmd.append(ROOT / "tests")
    return run(pytest_cmd, args.print, env=env, cwd=ROOT)


def cmd_lock(args):
    pairs = [("requirements-stable.in", "requirements-stable.txt"),
             ("requirements-build-stable.in", "requirements-build-stable.txt")]
    rc = 0
    for src, dst in pairs:
        base = ["uv", "pip", "compile", src, "--universal", "--generate-hashes",
                "--python-version", PYTHON_FOR_LOCK, "-q"]
        if args.check:
            tmp = ROOT / (dst + ".check")
            if run(base + ["-o", tmp], args.print, cwd=ROOT):
                return 1
            if args.print:
                continue
            body = lambda p: re.sub(r"^#.*\n", "", p.read_text(encoding="utf-8"), flags=re.M)
            same = body(tmp) == body(ROOT / dst)
            tmp.unlink(missing_ok=True)
            print(f"{dst}: {'up to date' if same else 'OUT OF DATE - run: python tools/deps.py lock'}")
            rc |= 0 if same else 1
        else:
            rc |= run(base + ["-o", dst], args.print, cwd=ROOT)
    return rc


def cmd_outdated(args):
    pins = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", (ROOT / "requirements-build-stable.txt").read_text(), re.M)
    print(f"{'package':24s} {'pinned':12s} newest on PyPI")
    behind = 0
    for name, pinned in pins:
        try:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=20) as r:
                newest = json.load(r)["info"]["version"]
        except Exception as e:
            newest = f"? ({type(e).__name__})"
        flag = "" if newest == pinned else "  <- newer"
        behind += bool(flag) and not newest.startswith("?")
        print(f"{name:24s} {pinned:12s} {newest}{flag}")
    print(f"\n{behind} package(s) have newer releases. To try them: python tools/deps.py test --channel latest")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("install", "test"):
        p = sub.add_parser(name)
        p.add_argument("--channel", choices=("stable", "latest"), default="stable")
        p.add_argument("--venv")
        p.add_argument("--print", action="store_true")
        if name == "install":
            p.add_argument("--build", action="store_true", help="also install PyInstaller")
        else:
            p.set_defaults(build=False)
            p.add_argument("--include-golden", action="store_true",
                           help="also run tests/test_golden_snapshot.py")
    p = sub.add_parser("lock")
    p.add_argument("--check", action="store_true")
    p.add_argument("--print", action="store_true")
    p = sub.add_parser("outdated")
    p.add_argument("--print", action="store_true")
    args = ap.parse_args()
    sys.exit({"install": cmd_install, "test": cmd_test, "lock": cmd_lock, "outdated": cmd_outdated}[args.cmd](args))


if __name__ == "__main__":
    main()
