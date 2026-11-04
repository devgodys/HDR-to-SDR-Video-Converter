from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request



# ---- app version / update checking -----------------------------------------
# GitHub repo this app checks for updates against.
UPDATE_CHECK_REPO = "devgodys/HDR-to-SDR-Video-Converter"

# The version of this build (e.g. "25.0" for the "v25.0" release). Bump it to match the
# tag right before cutting a release. "Check for updates" compares it against the newest
# GitHub tag, and _tag_is_newer() only fires on a strictly newer one.
APP_VERSION = "25.0"


def _parse_version_tag(tag):
    """Turns a GitHub release tag into a tuple of ints for comparison.
    Handles the repo's actual tag style ("v.2.1" - note the dot right
    after "v", not just "v2.1") as well as the more common "v2.1.3" or
    a bare "2.1", and ignores any non-numeric pre-release suffix like
    "-beta". Returns an empty tuple for anything unparsable, which
    always compares as "not newer" rather than raising."""
    s = tag.strip()
    if s[:1].lower() == "v":
        s = s[1:]
    s = s.lstrip(".")
    s = re.split(r"[^0-9.]", s, maxsplit=1)[0]
    parts = [p for p in s.split(".") if p != ""]
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return ()


def _tag_is_newer(remote_tag, local_version):
    remote, local = _parse_version_tag(remote_tag), _parse_version_tag(local_version)
    if not remote:
        return False
    size = max(len(remote), len(local))              # 2.1.0 is the same release as 2.1
    pad = lambda v: v + (0,) * (size - len(v))
    return pad(remote) > pad(local)


def fetch_latest_github_tag(owner_repo, timeout=5):
    """Resolves owner/repo's "latest release" tag from the plain HTTP
    redirect GitHub's web UI itself issues for .../releases/latest ->
    .../releases/tag/<tag> - never touching api.github.com. Unlike an
    api.github.com call, a plain github.com page redirect is not subject
    to the 60-requests/hour unauthenticated API rate limit, so this can
    be re-run as often as the user clicks "Check for updates" without
    ever risking a 403. Returns None on any network hiccup, unexpected
    response, or parse failure - a failed update check should never be
    treated as "no update available", nor should it raise and interrupt
    whatever else the button is doing."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None  # stop right at the redirect; don't follow it

    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(
        f"https://github.com/{owner_repo}/releases/latest",
        headers={"User-Agent": "HDR-to-SDR-Converter-update-check"})
    try:
        opener.open(req, timeout=timeout)
        return None  # a 200 here means no redirect happened - unexpected
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            return None
        location = e.headers.get("Location", "")
        _, sep, tag = location.rstrip("/").rpartition("/tag/")
        return urllib.parse.unquote(tag) if sep else None
    except (urllib.error.URLError, OSError, ValueError):
        return None
