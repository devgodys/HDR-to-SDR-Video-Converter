from pathlib import Path

import pytest

from hdr2sdr.runtime.resume import ResumeAction, ResumeCoordinator


def test_begin_fresh_run_removes_previous_chain_files_and_resets_state(tmp_path):
    coordinator = ResumeCoordinator()
    destination = tmp_path / "movie.mkv"
    old_part = tmp_path / "movie.resume-part1.mkv"
    old_output = tmp_path / "movie.resume-part2.mkv"
    old_part.touch()
    old_output.touch()
    coordinator.parts = [old_part]
    coordinator.outpoints = [10.0]
    coordinator.output_override = old_output
    coordinator.progress_offset = 10.0

    new_output = coordinator.begin_fresh_run(destination)

    assert not old_part.exists() and not old_output.exists()
    assert coordinator.parts == [] and coordinator.outpoints == []
    assert coordinator.progress_offset == 0
    assert coordinator.output_override == destination.with_name("movie.part.mkv")
    assert new_output == coordinator.output_override


@pytest.mark.parametrize(
    ("unattended", "matches", "duration", "threshold", "expected"),
    [(False, True, 90, 20, ResumeAction.ORPHAN_ASK),
     (True, True, 90, 20, ResumeAction.ORPHAN_AUTO_RESUME),
     (True, False, 90, 20, ResumeAction.ORPHAN_START_OVER),
     (True, True, 10, 20, ResumeAction.ORPHAN_TOO_SHORT)],
)
def test_orphan_action_decision(unattended, matches, duration, threshold, expected):
    from hdr2sdr.runtime.resume import OrphanAssessment

    assessment = OrphanAssessment(Path("part.mkv"), duration, threshold,
                                  matches, matches)
    assert ResumeCoordinator.orphan_action(assessment, unattended) == expected


@pytest.mark.parametrize(
    ("backend", "hint", "decode", "progress", "exists", "unattended", "expected"),
    [(False, False, False, True, True, False, ResumeAction.UNAVAILABLE),
     (True, True, False, True, True, False, ResumeAction.UNAVAILABLE),
     (True, False, True, True, True, False, ResumeAction.SOFTWARE_DECODE),
     (True, False, True, False, True, False, ResumeAction.DECODE_FAILED_TOO_EARLY),
     (True, False, False, False, True, False, ResumeAction.TOO_SHORT),
     (True, False, False, True, True, True, ResumeAction.AUTOMATIC),
     (True, False, False, True, True, False, ResumeAction.ASK)],
)
def test_failure_resume_action_decision(backend, hint, decode, progress, exists,
                                       unattended, expected):
    assert ResumeCoordinator.failure_action(
        backend_available=backend, has_specific_hint=hint,
        hardware_decode_failed=decode, enough_progress=progress,
        partial_exists=exists, unattended=unattended) == expected


def test_prepare_segment_uses_muxed_duration_and_keeps_chained_part(tmp_path):
    coordinator = ResumeCoordinator()
    destination = tmp_path / "movie_SDR.mkv"
    active = tmp_path / "movie_SDR.part.mkv"
    active.write_bytes(b"segment one")

    first = coordinator.prepare_segment(active, destination, 41.5, 45.0)
    assert first.kept_part.name == "movie_SDR.resume-part1.mkv"
    assert first.seek_seconds == 37.5
    assert first.outpoint_seconds == 37.5
    assert not active.exists()

    # The next failed segment already has its target name; it must not unlink itself.
    first.next_output.write_bytes(b"segment two")
    second = coordinator.prepare_segment(first.next_output, destination, 22.0, 60.0,
                                         progress_offset=37.5)
    assert second.kept_part == first.next_output
    assert second.moved_from is None
    assert second.seek_seconds == 55.5
    assert second.outpoint_seconds == 18.0
    assert second.kept_part.read_bytes() == b"segment two"


def test_prepare_segment_falls_back_to_progress_when_duration_unavailable(tmp_path):
    coordinator = ResumeCoordinator()
    destination = tmp_path / "movie.mkv"
    active = tmp_path / "movie.part.mkv"
    active.touch()
    segment = coordinator.prepare_segment(active, destination, None, 50.0, 10.0)
    assert segment.seek_seconds == 46.0
    assert coordinator.progress_offset == 46.0
    assert coordinator.parts == [segment.kept_part]


def test_orphan_assessment_and_duration_threshold():
    assessment = ResumeCoordinator.assess_orphan(
        "file.part.mkv", 90, 3600,
        {"settings": "sig", "source": ["source"]}, "sig", ["source"])
    assert assessment.matches
    assert assessment.threshold == 108
    changed = ResumeCoordinator.assess_orphan(
        "file.part.mkv", 90, 100,
        {"settings": "old", "source": ["source"]}, "new", ["source"])
    assert not changed.matches
    assert changed.threshold == 20


def test_concat_command_writes_cutpoints_and_container_arguments(tmp_path):
    coordinator = ResumeCoordinator()
    first = tmp_path / "part1.mkv"
    second = tmp_path / "part2.mkv"
    destination = tmp_path / "joined.mkv"
    command, list_path = coordinator.concat_command(
        [first, second], destination, "ffmpeg.exe", lambda _path: ["-f", "matroska"], [12.345])
    content = list_path.read_text(encoding="utf-8")
    assert f"file '{first.resolve()}'\noutpoint 12.345\n" in content
    assert f"file '{second.resolve()}'\n" in content
    assert command[-3:] == ["-f", "matroska", str(destination)]


def test_concat_result_requires_successful_uncancelled_process():
    assert ResumeCoordinator.concat_succeeded(False, 0, True)
    assert not ResumeCoordinator.concat_succeeded(True, 0, True)
    assert not ResumeCoordinator.concat_succeeded(False, 1, True)
    assert not ResumeCoordinator.concat_succeeded(False, 0, False)


def test_finalize_concat_removes_parts_only_after_publication(tmp_path):
    coordinator = ResumeCoordinator()
    destination = tmp_path / "joined.mkv"
    part = tmp_path / "joined.resume-part1.mkv"
    temporary = tmp_path / "joined.part.mkv"
    part.write_bytes(b"encoded segment")
    temporary.write_bytes(b"joined output")
    coordinator.parts.append(part)
    calls = []

    result = coordinator.finalize_concat(
        False, 0, True, temporary, destination,
        lambda src, dst: calls.append((src, dst)) or True,
        lambda path: calls.append(("discard", path)))
    assert result.succeeded
    assert result.parts == (part,)
    assert calls == [(temporary, destination)]
    assert not part.exists()


def test_finalize_concat_keeps_parts_after_failure(tmp_path):
    coordinator = ResumeCoordinator()
    destination = tmp_path / "joined.mkv"
    part = tmp_path / "joined.resume-part1.mkv"
    temporary = tmp_path / "joined.part.mkv"
    part.touch()
    temporary.touch()
    coordinator.parts.append(part)
    discarded = []
    result = coordinator.finalize_concat(
        False, 1, True, temporary, destination,
        lambda *_: pytest.fail("must not publish a failed concat"), discarded.append)
    assert not result.succeeded and not result.stopped
    assert part.exists()
    assert discarded == [temporary]


def test_chain_metadata_roundtrip_and_missing_part_rejection(tmp_path):
    coordinator = ResumeCoordinator()
    destination = tmp_path / "movie_SDR.mkv"
    active = tmp_path / "movie_SDR.part.mkv"
    active.write_bytes(b"segment-one")
    first = coordinator.prepare_segment(active, destination, 44.0, 44.0)
    manifest = coordinator.chain_metadata()
    assert manifest is not None

    # Simulate a relaunched process restoring the chain from sidecar metadata
    # and then resuming after the second segment also failed mid-way.
    first.next_output.write_bytes(b"segment-two")
    restarted = ResumeCoordinator()
    loaded = restarted.load_chain_metadata({"chain": manifest})
    assert loaded is not None
    restarted.restore_chain(loaded, destination)
    second = restarted.prepare_segment(
        first.next_output, destination, 30.0, 70.0, restarted.progress_offset)
    assert restarted.parts == [first.kept_part, first.next_output]
    assert restarted.outpoints == [40.0, 26.0]
    assert second.next_output == tmp_path / "movie_SDR.resume-part3.mkv"
    assert second.seek_seconds == 66.0

    # If an earlier kept part was deleted on disk, the chain is rejected.
    first.kept_part.unlink()
    assert ResumeCoordinator.load_chain_metadata({"chain": manifest}) is None
