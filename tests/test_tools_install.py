"""The portable-tools downloader: pinned/stable and latest channels, and above
all that nothing unverified ever gets installed. Runs against a throwaway
local HTTP server - no network."""
import hashlib
import http.server
import io
import json
import threading
import zipfile

import pytest

from hdr2sdr.runtime import tools as T


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Serves tmp_path/www; URLs in tools.py are redirected to it."""
    www = tmp_path / "www"
    www.mkdir()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(www), **k)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(T, "BTBN_RELEASES", f"{base}/btbn")
    monkeypatch.setattr(T, "HANDBRAKE_RELEASES", f"{base}/hb")
    monkeypatch.setattr(T, "HANDBRAKE_LATEST_API", f"{base}/hb-api.json")
    yield www, base
    httpd.shutdown()


FFMPEG_ZIP = make_zip({"ffmpeg-x/bin/ffmpeg.exe": b"MZ-ffmpeg", "ffmpeg-x/bin/ffprobe.exe": b"MZ-ffprobe",
                       "ffmpeg-x/bin/ffplay.exe": b"MZ-play"})
HB_ZIP = make_zip({"HandBrakeCLI.exe": b"MZ-hb"})


def put(www, rel, data):
    p = www / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())


def test_parse_checksums_formats():
    text = f"{'a'*64}  one.zip\n{'b'*64} *two.zip\nnot a checksum line\n"
    assert T.parse_checksums(text) == {"one.zip": "a" * 64, "two.zip": "b" * 64}


def test_stable_installs_pinned_build_and_records_it(server, tmp_path, monkeypatch):
    www, _ = server
    monkeypatch.setattr(T, "STABLE_FFMPEG_SHA256", sha(FFMPEG_ZIP))
    put(www, f"btbn/download/{T.STABLE_FFMPEG_TAG}/{T.STABLE_FFMPEG_ASSET}", FFMPEG_ZIP)
    tools = tmp_path / "tools"
    logs = []
    assert T.install(["FFmpeg"], "stable", tools, log=logs.append) is True
    assert (tools / "ffmpeg.exe").read_bytes() == b"MZ-ffmpeg"
    assert (tools / "ffprobe.exe").read_bytes() == b"MZ-ffprobe"
    assert not (tools / "ffplay.exe").exists()                 # only what we asked for
    rec = json.loads((tools / "installed.json").read_text())["FFmpeg"]
    assert rec["channel"] == "stable" and rec["sha256"] == sha(FFMPEG_ZIP)
    assert any("SHA-256 verified" in l for l in logs)


def test_hash_mismatch_is_refused_and_nothing_is_installed(server, tmp_path):
    www, _ = server        # pinned hash is the real one; the server serves different bytes
    put(www, f"btbn/download/{T.STABLE_FFMPEG_TAG}/{T.STABLE_FFMPEG_ASSET}", FFMPEG_ZIP)
    tools = tmp_path / "tools"
    logs = []
    assert T.install(["FFmpeg"], "stable", tools, log=logs.append) is False
    assert not (tools / "ffmpeg.exe").exists() and not (tools / "installed.json").exists()
    assert any("Checksum mismatch" in l for l in logs)


def test_existing_install_survives_a_failed_update(server, tmp_path):
    www, _ = server
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "ffmpeg.exe").write_bytes(b"old-good-ffmpeg")
    put(www, f"btbn/download/{T.STABLE_FFMPEG_TAG}/{T.STABLE_FFMPEG_ASSET}", FFMPEG_ZIP)   # wrong hash
    assert T.install(["FFmpeg"], "stable", tools, log=lambda s: None) is False
    assert (tools / "ffmpeg.exe").read_bytes() == b"old-good-ffmpeg"


def test_stable_falls_back_to_branch_build_when_pin_was_pruned(server, tmp_path):
    www, _ = server        # no pinned file on the server -> 404 -> branch build via upstream checksums
    put(www, f"btbn/download/latest/{T.STABLE_FFMPEG_BRANCH_ASSET}", FFMPEG_ZIP)
    put(www, "btbn/download/latest/checksums.sha256", f"{sha(FFMPEG_ZIP)}  {T.STABLE_FFMPEG_BRANCH_ASSET}\n")
    logs = []
    assert T.install(["FFmpeg"], "stable", tmp_path / "t", log=logs.append) is True
    assert any("no longer available" in l for l in logs)


def test_latest_ffmpeg_verified_against_upstream_checksums(server, tmp_path):
    www, _ = server
    put(www, f"btbn/download/latest/{T.LATEST_FFMPEG_ASSET}", FFMPEG_ZIP)
    put(www, "btbn/download/latest/checksums.sha256", f"{sha(FFMPEG_ZIP)}  {T.LATEST_FFMPEG_ASSET}\n")
    assert T.install(["FFmpeg"], "latest", tmp_path / "t", log=lambda s: None) is True
    # ...and a tampered download is refused
    put(www, f"btbn/download/latest/{T.LATEST_FFMPEG_ASSET}", FFMPEG_ZIP + b"tampered")
    assert T.install(["FFmpeg"], "latest", tmp_path / "t2", log=lambda s: None) is False


def test_latest_handbrake_uses_github_asset_digest(server, tmp_path):
    www, base = server
    put(www, "hb/dl/HandBrakeCLI-9.9-win-x86_64.zip", HB_ZIP)
    api = {"tag_name": "9.9", "assets": [
        {"name": "HandBrakeCLI-9.9-win-aarch64.zip", "browser_download_url": f"{base}/nope", "digest": "sha256:" + "0" * 64},
        {"name": "HandBrakeCLI-9.9-win-x86_64.zip", "browser_download_url": f"{base}/hb/dl/HandBrakeCLI-9.9-win-x86_64.zip",
         "digest": "sha256:" + sha(HB_ZIP)}]}
    put(www, "hb-api.json", json.dumps(api))
    assert T.install(["HandBrakeCLI"], "latest", tmp_path / "t", log=lambda s: None) is True
    assert (tmp_path / "t" / "HandBrakeCLI.exe").read_bytes() == b"MZ-hb"


def test_latest_handbrake_without_digest_is_refused(server, tmp_path):
    www, base = server
    put(www, "hb/dl/HandBrakeCLI-9.9-win-x86_64.zip", HB_ZIP)
    put(www, "hb-api.json", json.dumps({"tag_name": "9.9", "assets": [
        {"name": "HandBrakeCLI-9.9-win-x86_64.zip", "browser_download_url": f"{base}/hb/dl/HandBrakeCLI-9.9-win-x86_64.zip"}]}))
    logs = []
    assert T.install(["HandBrakeCLI"], "latest", tmp_path / "t", log=logs.append) is False
    assert not (tmp_path / "t" / "HandBrakeCLI.exe").exists()
    assert any("no SHA-256" in l for l in logs)


def test_archive_cannot_write_outside_the_tools_folder(tmp_path):
    z = tmp_path / "evil.zip"
    z.write_bytes(make_zip({"../../escape.txt": b"x", "a/../../ffmpeg.exe": b"MZ", "ok/ffprobe.exe": b"MZ2"}))
    out = tmp_path / "tools"
    T.extract_executables(z, ("ffmpeg.exe", "ffprobe.exe"), ("ffmpeg.exe",), out)
    assert sorted(p.name for p in out.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path.parent / "escape.txt").exists()


def test_required_executable_missing_in_archive(tmp_path):
    z = tmp_path / "x.zip"
    z.write_bytes(make_zip({"readme.txt": b"hi"}))
    with pytest.raises(T.ToolInstallError):
        T.extract_executables(z, ("ffmpeg.exe",), ("ffmpeg.exe",), tmp_path / "t")


def test_source_without_a_valid_hash_cannot_exist():
    with pytest.raises(T.ToolInstallError):
        T.ToolSource("X", "http://x", "", "x", ("x.exe",))
    with pytest.raises(T.ToolInstallError):
        T.ToolSource("X", "http://x", "abc", "x", ("x.exe",))


def test_pins_are_wellformed():
    assert len(T.STABLE_FFMPEG_SHA256) == 64 and len(T.STABLE_HANDBRAKE_SHA256) == 64
    int(T.STABLE_FFMPEG_SHA256, 16), int(T.STABLE_HANDBRAKE_SHA256, 16)


def test_cancel_stops_the_download(server, tmp_path, monkeypatch):
    www, _ = server
    monkeypatch.setattr(T, "STABLE_FFMPEG_SHA256", sha(FFMPEG_ZIP))
    put(www, f"btbn/download/{T.STABLE_FFMPEG_TAG}/{T.STABLE_FFMPEG_ASSET}", FFMPEG_ZIP)
    logs = []
    assert T.install(["FFmpeg", "HandBrakeCLI"], "stable", tmp_path / "t", log=logs.append, cancel=lambda: True) is False
    assert not (tmp_path / "t" / "ffmpeg.exe").exists()
    assert not any("HandBrakeCLI" in l and "Downloading" in l for l in logs)   # stopped, didn't go on


def test_download_size_limit_removes_partial_file(server, tmp_path):
    www, base = server
    payload = b"0123456789"
    put(www, "large.zip", payload)
    destination = tmp_path / "large.zip"
    with pytest.raises(T.ToolInstallError, match="size limit"):
        T.download_file(f"{base}/large.zip", destination, max_bytes=5)
    assert not destination.exists()
    assert not destination.with_suffix(".zip.part").exists()


def test_download_size_limit_also_checks_streamed_bytes_when_length_is_unknown(
        server, tmp_path, monkeypatch):
    www, base = server
    payload = b"0123456789"
    put(www, "large.zip", payload)
    real_request = T._request

    class UnknownLength:
        def __init__(self, response):
            self.response = response
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.response.close()

        def read(self, size):
            return self.response.read(size)

    monkeypatch.setattr(T, "_request", lambda *a, **k: UnknownLength(real_request(*a, **k)))
    destination = tmp_path / "large.zip"
    with pytest.raises(T.ToolInstallError, match="size limit"):
        T.download_file(f"{base}/large.zip", destination, max_bytes=5)
    assert not destination.exists()
    assert not destination.with_suffix(".zip.part").exists()


def test_extraction_limit_rejects_expanded_archive_before_writing(tmp_path):
    archive = tmp_path / "expanded.zip"
    archive.write_bytes(make_zip({"bin/ffmpeg.exe": b"12345", "bin/ffprobe.exe": b"67890"}))
    out = tmp_path / "tools"
    with pytest.raises(T.ToolInstallError, match="extraction limit"):
        T.extract_executables(archive, ("ffmpeg.exe", "ffprobe.exe"),
                              ("ffmpeg.exe",), out, max_extracted_bytes=9)
    assert not list(out.glob("*.new"))
    assert not (out / "ffmpeg.exe").exists()


def test_failed_extraction_of_a_later_member_leaves_the_old_tools_untouched(tmp_path, monkeypatch):
    out = tmp_path / "tools"
    out.mkdir()
    (out / "ffmpeg.exe").write_bytes(b"OLD-FFMPEG")
    (out / "ffprobe.exe").write_bytes(b"OLD-FFPROBE")
    z = tmp_path / "new.zip"
    z.write_bytes(make_zip({"bin/ffmpeg.exe": b"NEW-FFMPEG", "bin/ffprobe.exe": b"NEW-FFPROBE"}))
    real_open = zipfile.ZipFile.open
    calls = []

    def flaky(self, *a, **k):
        calls.append(1)
        if len(calls) == 2:                       # the second member fails while extracting
            raise OSError("disk full")
        return real_open(self, *a, **k)
    monkeypatch.setattr(zipfile.ZipFile, "open", flaky)
    with pytest.raises(T.ToolInstallError):
        T.extract_executables(z, ("ffmpeg.exe", "ffprobe.exe"), ("ffmpeg.exe",), out)
    assert (out / "ffmpeg.exe").read_bytes() == b"OLD-FFMPEG"
    assert (out / "ffprobe.exe").read_bytes() == b"OLD-FFPROBE"
    assert sorted(p.name for p in out.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]      # no *.new left


def test_failed_publish_of_a_later_file_restores_the_old_tools(tmp_path, monkeypatch):
    """The first exe is swapped in, the second swap fails: the old working set must come back."""
    out = tmp_path / "tools"
    out.mkdir()
    (out / "ffmpeg.exe").write_bytes(b"OLD-FFMPEG")
    (out / "ffprobe.exe").write_bytes(b"OLD-FFPROBE")
    z = tmp_path / "new.zip"
    z.write_bytes(make_zip({"bin/ffmpeg.exe": b"NEW-FFMPEG", "bin/ffprobe.exe": b"NEW-FFPROBE"}))
    real = T.os.replace
    n = []

    def flaky(a, b):
        n.append(1)
        if str(b).endswith("ffprobe.exe") and str(a).endswith(".new"):    # publishing the 2nd file
            raise PermissionError("file is in use")
        return real(a, b)
    monkeypatch.setattr(T.os, "replace", flaky)
    with pytest.raises(T.ToolInstallError):
        T.extract_executables(z, ("ffmpeg.exe", "ffprobe.exe"), ("ffmpeg.exe",), out)
    monkeypatch.setattr(T.os, "replace", real)
    assert (out / "ffmpeg.exe").read_bytes() == b"OLD-FFMPEG"
    assert (out / "ffprobe.exe").read_bytes() == b"OLD-FFPROBE"
    assert sorted(p.name for p in out.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]


def test_failed_installed_record_publish_restores_tools_and_metadata(tmp_path, monkeypatch):
    out = tmp_path / "tools"
    out.mkdir()
    (out / "ffmpeg.exe").write_bytes(b"OLD-FFMPEG")
    (out / "installed.json").write_text('{"FFmpeg": {"label": "old"}}', encoding="utf-8")
    archive = tmp_path / "new.zip"
    archive.write_bytes(make_zip({"bin/ffmpeg.exe": b"NEW-FFMPEG"}))
    real = T.os.replace

    def flaky(source, destination):
        if str(destination).endswith("installed.json") and str(source).endswith("installed.json.new"):
            raise PermissionError("metadata is in use")
        return real(source, destination)

    monkeypatch.setattr(T.os, "replace", flaky)
    with pytest.raises(T.ToolInstallError):
        T.extract_executables(
            archive, ("ffmpeg.exe",), ("ffmpeg.exe",), out,
            record={"FFmpeg": {"label": "new"}},
        )

    assert (out / "ffmpeg.exe").read_bytes() == b"OLD-FFMPEG"
    assert json.loads((out / "installed.json").read_text(encoding="utf-8")) == {
        "FFmpeg": {"label": "old"}
    }
    assert sorted(p.name for p in out.iterdir()) == ["ffmpeg.exe", "installed.json"]
