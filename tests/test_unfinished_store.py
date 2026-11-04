import json

from hdr2sdr.runtime.unfinished import UnfinishedStore


def test_store_writes_and_reads_sidecar_and_registry(tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    part = tmp_path / "movie.part.mkv"
    part.write_bytes(b"partial")
    registry = tmp_path / "state" / "unfinished_runs.json"
    store = UnfinishedStore(registry)
    fingerprint = store.source_fingerprint(source)

    store.write_sidecar(part, "settings-hash", fingerprint)
    store.remember(part, tmp_path / "movie.mkv", fingerprint, at="2026-10-16T12:00:00")

    assert store.read_sidecar(part) == {"settings": "settings-hash", "source": fingerprint}
    assert store.load() == [{
        "part": str(part), "dst": str(tmp_path / "movie.mkv"),
        "source": fingerprint, "at": "2026-10-16T12:00:00",
    }]
    assert store.for_source(fingerprint, ".MKV") == [part]
    assert store.for_source(fingerprint, ".mp4") == []


def test_store_replaces_duplicate_records_and_caps_registry(tmp_path):
    store = UnfinishedStore(tmp_path / "unfinished_runs.json")
    source = tmp_path / "source.mkv"
    fingerprint = store.source_fingerprint(source)
    parts = []
    for number in range(52):
        part = tmp_path / f"{number}.part.mkv"
        part.touch()
        parts.append(part)
        store.remember(part, part.with_suffix(".mkv"), fingerprint, at=str(number))

    store.remember(parts[-1], "new-destination.mkv", fingerprint, at="new")
    records = store.load()
    assert len(records) == 50
    assert records[-1]["at"] == "new"
    assert len([r for r in records if r["part"] == str(parts[-1])]) == 1


def test_store_forget_and_corrupt_json_are_safe(tmp_path):
    registry = tmp_path / "unfinished_runs.json"
    part = tmp_path / "partial.mkv"
    part.touch()
    store = UnfinishedStore(registry)
    fp = store.source_fingerprint(tmp_path / "source.mkv")
    store.remember(part, "destination.mkv", fp)
    store.forget(part)
    assert store.load() == []

    registry.write_text("{broken", encoding="utf-8")
    assert store.load() == []
    assert store.read_sidecar(part) == {}


def test_store_ignores_malformed_entries_and_missing_parts(tmp_path):
    missing = tmp_path / "missing.part.mkv"
    valid = tmp_path / "valid.part.mkv"
    valid.touch()
    registry = tmp_path / "unfinished_runs.json"
    registry.write_text(json.dumps([
        1, {}, {"part": 4}, {"part": str(missing)}, {"part": str(valid)},
    ]), encoding="utf-8")

    assert UnfinishedStore(registry).load() == [{"part": str(valid)}]


def test_publish_clears_sidecar_and_registry_only_after_success(tmp_path):
    part = tmp_path / "movie.part.mkv"
    destination = tmp_path / "movie.mkv"
    part.write_bytes(b"partial")
    store = UnfinishedStore(tmp_path / "unfinished.json")
    store.write_sidecar(part, "settings", ["source"])
    store.remember(part, destination, ["source"])

    def publish(src, dst):
        src.replace(dst)
        return None

    error = store.publish(part, destination, publish)

    assert error is None
    assert destination.read_bytes() == b"partial"
    assert not store.sidecar_path(part).exists()
    assert store.load() == []


def test_failed_publish_preserves_unfinished_record(tmp_path):
    part = tmp_path / "movie.part.mkv"
    part.touch()
    store = UnfinishedStore(tmp_path / "unfinished.json")
    store.remember(part, tmp_path / "movie.mkv", ["source"])
    error = OSError("rename failed")

    result = store.publish(part, tmp_path / "movie.mkv", lambda *_: error)

    assert result is error
    assert store.load()[0]["part"] == str(part)


def test_chain_sidecar_keeps_registry_recoverable_and_discard_removes_all_segments(tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    part1 = tmp_path / "movie.resume-part1.mkv"
    part2 = tmp_path / "movie.resume-part2.mkv"
    part1.write_bytes(b"segment-1")
    store = UnfinishedStore(tmp_path / "unfinished_runs.json")
    fingerprint = store.source_fingerprint(source)
    chain = {
        "version": 1,
        "final_destination": str(tmp_path / "movie.mkv"),
        "parts": [str(part1)],
        "outpoints": [36.0],
        "seek_seconds": 36.0,
        "progress_offset": 36.0,
        "active_output": str(part2),
    }

    # Even if the process died before part2 was created by ffmpeg, the chain
    # sidecar references part1 on disk so the registry entry stays recoverable.
    store.write_sidecar(part2, "settings-hash", fingerprint, chain=chain)
    store.remember(part2, tmp_path / "movie.mkv", fingerprint)
    assert store.for_source(fingerprint, ".mkv") == [part2]
    assert store.read_sidecar(part2)["chain"] == chain

    part2.write_bytes(b"segment-2")
    store.discard(part2)
    assert not part1.exists()
    assert not part2.exists()
    assert not store.sidecar_path(part2).exists()
    assert store.load() == []
