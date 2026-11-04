import json

from hdr2sdr.runtime.history import HistoryStore


def test_history_store_records_settings_and_matches_same_source(tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    store = HistoryStore(tmp_path / "config" / "conversion_history.json")

    store.append(source, tmp_path / "output.mkv", "done", "hdr",
                 settings={"backend": "FFmpeg", "encoder_label": "H.265 CPU"},
                 timestamp="2026-10-16T12:00:00")

    records = store.for_source(source)
    assert records == [{
        "timestamp": "2026-10-16T12:00:00",
        "source": str(source),
        "source_fingerprint": HistoryStore.source_fingerprint(source),
        "destination": str(tmp_path / "output.mkv"),
        "status": "done",
        "kind": "hdr",
        "settings": {"backend": "FFmpeg", "encoder_label": "H.265 CPU"},
    }]


def test_history_store_distinguishes_changed_source_and_caps_entries(tmp_path):
    source = tmp_path / "source.mkv"
    source.write_bytes(b"before")
    store = HistoryStore(tmp_path / "history.json", max_records=2)
    store.append(source, "one.mkv", "done", "hdr")
    source.write_bytes(b"changed source")
    assert store.for_source(source) == []

    store.append(source, "two.mkv", "failed", "hdr")
    store.append(source, "three.mkv", "stopped", "hdr")
    assert [item["destination"] for item in store.load()] == ["two.mkv", "three.mkv"]


def test_history_store_reads_legacy_records_and_handles_corrupt_file(tmp_path):
    source = tmp_path / "source.mkv"
    source.touch()
    path = tmp_path / "history.json"
    path.write_text(json.dumps([{"source": str(source), "status": "done"}]), encoding="utf-8")
    store = HistoryStore(path)
    assert store.for_source(source)[0]["status"] == "done"

    path.write_text("{broken", encoding="utf-8")
    assert store.load() == []
