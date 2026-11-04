"""Output is written to <name>.part<ext> and renamed on success; Stop removes it; the run
snapshot (not the on-screen fields) decides what history/validation talk about."""
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from hdr2sdr.core.destination import DestinationPlan
from hdr2sdr.core.commands import ConversionSettings, settings_signature
from hdr2sdr.runtime.resume import ResumeCoordinator
from hdr2sdr.runtime.unfinished import UnfinishedStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def win(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    for _ in range(3):
        app.processEvents()
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    monkeypatch.setattr(w, "_validate_output", lambda p: True)
    monkeypatch.setattr(w, "_notify_completion", lambda *a, **k: None)
    monkeypatch.setattr(w, "_all_done", lambda: None)
    hist = []
    monkeypatch.setattr(w, "_append_history_entry", lambda *a, **k: hist.append(a))
    w._hist = hist
    yield w
    w.close()


def _arm(w, tmp_path):
    from PySide6.QtCore import QProcess  # noqa: F401
    dst = tmp_path / "movie_SDR.mp4"
    part = DestinationPlan(dst).part
    part.write_bytes(b"data")
    w._run_context.source, w._run_context.destination, w._run_context.kind = tmp_path / "movie.mkv", dst, "hdr10"
    w._resume_coordinator.output_override = part
    w.dst_edit.setText(str(tmp_path / "somewhere_else.mp4"))     # the user edits the field mid-run
    w.src_edit.setText(str(tmp_path / "other.mkv"))
    return dst, part


def test_part_name():
    assert DestinationPlan(Path("/x/a_SDR.mp4")).part.name == "a_SDR.part.mp4"
    assert DestinationPlan.is_part_file(Path("/x/a_SDR.part.mp4"))
    assert not DestinationPlan.is_part_file(Path("/x/a_SDR.resume-part2.mp4"))


def test_success_renames_part_to_snapshot_destination(win, tmp_path):
    from PySide6.QtCore import QProcess
    dst, part = _arm(win, tmp_path)
    win._on_finished(0, QProcess.ExitStatus.NormalExit)
    assert dst.is_file() and not part.exists()
    assert not (tmp_path / "somewhere_else.mp4").exists()
    assert win._hist[-1][0] == str(tmp_path / "movie.mkv")


def test_stop_removes_the_part_file(win, tmp_path):
    from PySide6.QtCore import QProcess
    dst, part = _arm(win, tmp_path)
    win.stopping = True
    win._on_finished(0, QProcess.ExitStatus.NormalExit)
    assert not dst.exists() and not part.exists()


def test_failure_keeps_part_but_never_the_real_name(win, tmp_path):
    from PySide6.QtCore import QProcess
    dst, part = _arm(win, tmp_path)
    win.duration = 100
    win._on_finished(1, QProcess.ExitStatus.NormalExit)
    assert not dst.exists()
    assert part.exists()

    # A later Start must not treat the kept partial as stale coordinator
    # state and delete it before orphan-resume discovery can inspect it.
    win._resume_coordinator.begin_fresh_run(dst)
    assert part.exists()


def test_backend_change_preserves_tonemap_selection(win):
    selected = "Reinhard \u00b7 CPU tonemap"
    win.method_combo.setCurrentText(selected)
    win.backend_combo.setCurrentText("HandBrake")
    assert win.method_combo.currentText() == selected


def test_selecting_queue_item_starts_analysis_automatically(win, tmp_path, monkeypatch):
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"video")
    second.write_bytes(b"video")
    win._queue_controller.add(first)
    win._queue_controller.add(second)
    for path in (first, second):
        win.queue_list.addItem(path.name)
        win.queue_list.item(win.queue_list.count() - 1).setToolTip(str(path))
    win._set_source(str(first), analyze=False)
    calls = []
    monkeypatch.setattr(win, "analyze", lambda: calls.append(win.src_edit.text()) or True)

    win._on_queue_row_selected(1)

    assert win.src_edit.text() == str(second)
    assert calls == [str(second)]
    assert not win.analyze_btn.isVisible()


def test_queue_item_start_defers_analysis_until_start_impl(win):
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "main_window.py"
    text = source.read_text(encoding="utf-8")
    selected = text.split("def _start_selected_file_only(self):", 1)[1].split("def start_queue(self):", 1)[0]
    next_item = text.split("def _start_next_queue_item(self):", 1)[1].split("def _confirm_destination(self):", 1)[0]
    assert "self._set_source(path, analyze=False)" in selected
    assert "self._set_source(path, analyze=False)" in next_item


def test_resume_prepare_error_finishes_queue_item(win, tmp_path, monkeypatch):
    from hdr2sdr.runtime.lifecycle import RunState

    dst, part = _arm(win, tmp_path)
    part.write_bytes(b"partial")
    win._queue_controller.running = True
    win._queue_controller.paths = ["next.mkv"]

    def fail_prepare(*_args, **_kwargs):
        raise OSError("file is locked")

    monkeypatch.setattr(win._resume_coordinator, "prepare_segment", fail_prepare)
    win._begin_resume(part)
    assert win._run_lifecycle.state == RunState.FAILED
    assert not win._queue_controller.running
    assert part.exists()


def test_concat_list_error_finishes_queue_item_and_keeps_segments(win, tmp_path, monkeypatch):
    from hdr2sdr.runtime.lifecycle import RunState

    destination = tmp_path / "joined.mkv"
    segment = tmp_path / "joined.resume-part1.mkv"
    segment.write_bytes(b"encoded segment")
    coordinator = win._resume_coordinator
    coordinator.final_destination = destination
    coordinator.parts = [segment]
    win._queue_controller.running = True
    win._queue_controller.paths = ["next.mkv"]

    def fail_concat(*_args, **_kwargs):
        raise OSError("directory is read-only")

    monkeypatch.setattr(coordinator, "concat_command", fail_concat)
    win._start_concat_phase()
    assert win._run_lifecycle.state == RunState.FAILED
    assert not win._queue_controller.running
    assert segment.exists()


def test_process_start_failure_finishes_queue_item(win, tmp_path, monkeypatch):
    from PySide6.QtCore import QProcess
    from hdr2sdr.runtime.lifecycle import RunState

    dst, part = _arm(win, tmp_path)
    win._queue_controller.running = True
    win._queue_controller.paths = ["next.mkv"]
    monkeypatch.setattr(win._conversion_runner, "start",
                        lambda *_args, **_kwargs: (QProcess(), False, False))
    win._launch(["missing-encoder.exe"])
    assert win._run_lifecycle.state == RunState.FAILED
    assert not win._queue_controller.running
    assert win.proc is None
    assert part.exists()


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_orphan_part_is_resumed_in_a_queue_when_settings_match(win, tmp_path, monkeypatch):
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    dst = tmp_path / "movie_SDR.mkv"
    part = DestinationPlan(dst).part
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                          "testsrc2=size=640x360:rate=25", "-c:v", "libx264", "-preset", "ultrafast",
                          "-b:v", "4M", "-t", "3000", str(part)])
    time.sleep(5)
    p.kill()
    p.wait()
    win._run_context.source, win._run_context.destination = src, dst
    win.src_edit.setText(str(src))
    win.dst_edit.setText(str(dst))
    win._queue_controller.running = True
    win._queue_controller.paths = ["another.mkv"]            # more items follow: nobody is watching
    started = []
    monkeypatch.setattr(win, "_begin_resume", lambda path: started.append(path))
    win._write_part_sidecar(part)
    assert 20 <= win._probe_duration(part) < 3000    # the real end of the data, not the planned length
    assert win._offer_orphan_resume(part) is True and started == [part]
    # same file, but the settings changed since: it must not be joined to a different encode
    started.clear()
    win.brightness_spin.setValue(win.brightness_spin.value() + 5)
    assert win._offer_orphan_resume(part) is False
    assert not part.exists() and started == []


def test_signature_ignores_source_and_destination_path_spelling():
    windows = ConversionSettings(src=r"C:\video\source.mkv", dst=r"C:\out\source_SDR.mkv")
    posix = ConversionSettings(src="C:/video/source.mkv", dst="C:/out/source_SDR.mkv")
    assert settings_signature(windows) == settings_signature(posix)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_unfinished_part_is_found_even_when_output_name_and_folder_changed(win, tmp_path, monkeypatch):
    win._unfinished_registry = UnfinishedStore(tmp_path / "unfinished_runs.json")
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x")
    first = tmp_path / "A"
    first.mkdir()
    part = DestinationPlan(first / "movie_SDR.mkv").part
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                          "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "4M", "-t", "3000", str(part)])
    time.sleep(5)
    p.kill()
    p.wait()
    win.src_edit.setText(str(src))
    win.dst_edit.setText(str(first / "movie_SDR.mkv"))
    win._run_context.source, win._run_context.destination = src, first / "movie_SDR.mkv"
    win._write_part_sidecar(part)                    # what the first run did when it started
    # second run: another folder / name (e.g. Mini mode "next to the source")
    other = tmp_path / "B" / "movie_SDR.mkv"
    other.parent.mkdir()
    win._run_context.destination = other
    win.dst_edit.setText(str(other))
    win._queue_controller.running = True
    win._queue_controller.paths = ["more.mkv"]
    started = []
    monkeypatch.setattr(win, "_begin_resume", lambda path: started.append(path))
    assert win._offer_orphan_resume(DestinationPlan(other).part) is True
    assert started == [part]
    # once it is published/discarded the record goes away
    win._unfinished_registry.discard(part)
    assert win._unfinished_registry.for_source(UnfinishedStore.source_fingerprint(src)) == []


def test_resumed_chain_survives_crash_and_relaunch(win, tmp_path, monkeypatch):
    """After segment 1 is moved to resume-part1 and segment 2 crashes/closes the app,
    a fresh launch must find resume-part2 in the registry, restore resume-part1 + outpoints
    from the sidecar chain manifest, keep resume-part2, and start resume-part3."""
    win._unfinished_registry = UnfinishedStore(tmp_path / "unfinished_runs.json")
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"source-video")
    dst = tmp_path / "movie_SDR.mkv"
    win.src_edit.setText(str(src))
    win.dst_edit.setText(str(dst))
    win.duration = 1000.0
    win._run_context.source, win._run_context.destination = src, dst

    durations = {
        "movie_SDR.part.mkv": 100.0,
        "movie_SDR.resume-part1.mkv": 100.0,
        "movie_SDR.resume-part2.mkv": 50.0,
    }
    monkeypatch.setattr(win, "_probe_duration", lambda p: durations.get(Path(p).name, 0.0))
    launched = []
    monkeypatch.setattr(win, "_start_resume_segment",
                        lambda: launched.append(win._resume_coordinator.output_override))

    # Session 1: initial part fails at 100s -> _begin_resume moves it to resume-part1
    part = win._resume_coordinator.begin_fresh_run(dst)
    part.write_bytes(b"segment-1")
    win._write_part_sidecar(part)
    win._begin_resume(part)

    part1 = tmp_path / "movie_SDR.resume-part1.mkv"
    part2 = tmp_path / "movie_SDR.resume-part2.mkv"
    part3 = tmp_path / "movie_SDR.resume-part3.mkv"
    assert part1.read_bytes() == b"segment-1"
    assert win._resume_coordinator.output_override == part2
    assert win._unfinished_registry.read_sidecar(part2)["chain"]["parts"] == [str(part1)]

    # Segment 2 writes 50s of data and then the process crashes / app is closed.
    part2.write_bytes(b"segment-2")

    # Session 2: fresh coordinator (simulating app relaunch), unattended resume.
    win._resume_coordinator = ResumeCoordinator()
    win._queue_controller.running = True
    win._queue_controller.paths = ["next.mkv"]
    initial_part = win._resume_coordinator.begin_fresh_run(dst)
    assert not initial_part.exists()  # movie_SDR.part.mkv was already moved to resume-part1

    assert win._offer_orphan_resume(initial_part) is True
    assert win._resume_coordinator.parts == [part1, part2]
    assert win._resume_coordinator.outpoints == [96.0, 46.0]
    assert win._resume_coordinator.seek_seconds == 142.0
    assert win._resume_coordinator.output_override == part3
    assert launched[-1] == part3
    assert win._unfinished_registry.read_sidecar(part3)["chain"]["parts"] == [
        str(part1), str(part2),
    ]


def test_source_named_like_the_temp_part_is_never_touched(win, tmp_path):
    src = tmp_path / "movie_SDR.part.mp4"
    src.write_bytes(b"precious source")
    dst = tmp_path / "movie_SDR.mp4"                     # does not exist, so the old check passed it
    win.src_edit.setText(str(src))
    win.dst_edit.setText(str(dst))
    win._queue_controller.running = False
    assert DestinationPlan(dst).collides_with(src)
    win.kind = "hdr"
    win._start_impl()
    assert src.read_bytes() == b"precious source"
    assert win.proc is None
    # a segment name and the settings record collide as well; unrelated names do not
    assert DestinationPlan(dst).collides_with(tmp_path / "movie_SDR.resume-part2.mp4")
    assert not DestinationPlan(dst).collides_with(tmp_path / "movie.mp4")


def test_queue_picked_name_is_checked_against_the_source_again(win, tmp_path):
    """movie_SDR.mp4 is taken, so the queue moves to movie_SDR_1.mp4 - and the source happens to be
    movie_SDR_1.part.mp4, i.e. the temp file of that new name. It must be skipped, not overwritten."""
    src = tmp_path / "movie_SDR_1.part.mp4"
    src.write_bytes(b"precious source")
    dst = tmp_path / "movie_SDR.mp4"
    dst.write_bytes(b"existing")
    win.src_edit.setText(str(src))
    win.dst_edit.setText(str(dst))
    win._queue_controller.running = True
    win.kind = "hdr"
    win._confirm_destination = lambda: (win.dst_edit.setText(str(tmp_path / "movie_SDR_1.mp4")) or True)
    win._start_impl()
    assert src.read_bytes() == b"precious source"
    assert win.proc is None


def test_delayed_release_proc_does_not_delete_newly_started_process(win, tmp_path):
    """FIX_QPROCESS_ALREADY_DELETED_2026-10-24:
    When _release_proc() schedules QTimer.singleShot(0, ...) at the end of segment 1 and
    segment 2 (or concat) starts synchronously before singleShot(0) fires, the delayed
    release must NOT delete the newly started QProcess."""
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication
    from hdr2sdr.runtime.conversion import is_valid_qobject

    old_proc = QProcess(win)
    new_proc = QProcess(win)
    win.proc = old_proc
    win._conversion_runner.process = old_proc

    # 1. _release_proc() is called when old_proc finishes
    win._release_proc()
    assert win.proc is None
    assert win._conversion_runner.process is None

    # 2. Synchronously (before singleShot(0) fires), a new segment/concat process starts
    win.proc = new_proc
    win._conversion_runner.process = new_proc

    # 3. Pump events so the queued QTimer.singleShot(0, ...) executes
    app = QApplication.instance()
    for _ in range(10):
        app.processEvents()

    assert win.proc is new_proc
    assert win._conversion_runner.process is new_proc
    assert is_valid_qobject(new_proc)
    win._release_proc()
    for _ in range(6):
        app.processEvents()


def test_deleted_qprocess_wrapper_does_not_raise_runtime_error(win):
    """FIX_QPROCESS_ALREADY_DELETED_2026-10-24:
    If a QProcess C++ object was destroyed while a Python attribute still referenced the wrapper,
    MainWindow methods (_is_proc_running, update_run_controls, sample_resources, poll_preview)
    must not raise RuntimeError and must clear the stale reference."""
    class DeletedQProcessWrapper:
        def objectName(self):
            raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")

        def state(self):
            raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")

    win.proc = DeletedQProcessWrapper()
    assert win._is_proc_running() is False
    assert win.proc is None

    win.proc = DeletedQProcessWrapper()
    win.update_run_controls()
    assert win.proc is None
    assert win.go_btn.isEnabled()

    win.proc = DeletedQProcessWrapper()
    win.sample_resources()
    win.poll_preview()
    assert win.proc is None


def test_empty_visible_queue_start_and_remove_never_launch_stale_source(win, tmp_path, monkeypatch):
    """FIX_QPROCESS_ALREADY_DELETED_2026-10-24:
    1) Removing the last file from the queue clears src_edit, dst_edit, kind, and _analysis_start_pending.
    2) Pressing Start (_on_start_clicked / start_queue) when the visible queue_list is empty
       never launches a previous/stale file even if src_edit or _queue_controller.paths had leftover data."""
    src = tmp_path / "previous_file.mkv"
    src.write_bytes(b"video")
    launched = []
    monkeypatch.setattr(win, "_launch", lambda cmd: launched.append(cmd))

    # Add file and then remove it via Remove button
    win._add_files_to_queue([str(src)], trust_extension=True)
    assert win.queue_list.count() == 1
    assert win.src_edit.text() == str(src)

    win._remove_queue_item()
    assert win.queue_list.count() == 0
    assert win._queue_controller.paths == []
    assert win.src_edit.text() == ""
    assert win.dst_edit.text() == ""
    assert win.kind == "unknown"

    # Simulate stale state after an error where visible queue_list is empty
    win.src_edit.setText(str(src))
    win.dst_edit.setText(str(tmp_path / "previous_file_SDR.mp4"))
    win.kind = "hdr"
    win._queue_controller.paths = [str(src)]

    # Pressing Start when visible queue_list is empty must purge stale state and NOT launch
    win._on_start_clicked()
    assert launched == []
    assert win.proc is None
    assert win._queue_controller.paths == []
    assert win.src_edit.text() == ""
    assert win.dst_edit.text() == ""


def test_libplacebo_preflight_oom_falls_back_to_cpu_tonemap_except_profile5(win, tmp_path, monkeypatch):
    """When libplacebo preflight fails with VK_ERROR_OUT_OF_DEVICE_MEMORY on an HDR10 or
    Dolby Vision Profile 7/8 source (which has an HDR10-compatible base layer), _start_impl()
    automatically switches to 'Hable · CPU tonemap' and launches conversion instead of skipping.
    For Dolby Vision Profile 5 (no HDR10 base layer), it must NOT fall back to CPU tonemap."""
    import hdr2sdr.ui.main_window as mw_mod
    from hdr2sdr.runtime.capabilities import StartCapabilityIssue

    src = tmp_path / "Cyberpunk Edgerunners 2 - 01 [2160p WEB-DL DV HDR10].mkv"
    src.write_bytes(b"video")
    dst = tmp_path / "Cyberpunk_SDR.mkv"
    win.src_edit.setText(str(src))
    win.dst_edit.setText(str(dst))
    win.kind = "dolby"
    win._dv_profile = 8
    win._queue_controller.running = True
    win.has_bt2390 = True
    win.encoders = {"hevc_nvenc", "libx265", "libx264"}
    win.refresh_method_choices(force_default=True)
    win.method_combo.setCurrentText("BT.2390 \u00b7 GPU tonemap")

    monkeypatch.setattr(mw_mod, "exe", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(win, "_enough_disk_space", lambda: True)
    monkeypatch.setattr(win, "_offer_orphan_resume", lambda *_a, **_k: False)
    monkeypatch.setattr(win, "_write_part_sidecar", lambda *_a, **_k: None)

    cap_calls = []

    def fake_check_caps(settings, *, ffmpeg, tonemap_engine, tonemap_code, **kwargs):
        cap_calls.append((tonemap_engine, tonemap_code))
        if tonemap_engine == "gpu":
            return StartCapabilityIssue(
                "GPU tonemap could not start",
                "FFmpeg lists libplacebo, but a one-frame GPU check failed. Check the Vulkan driver or select a CPU curve.\n\n"
                "[libplacebo] Allocation of size 256K failed: VK_ERROR_OUT_OF_DEVICE_MEMORY!",
            )
        return None

    launched = []
    monkeypatch.setattr(mw_mod, "check_start_capabilities", fake_check_caps)
    monkeypatch.setattr(win, "_launch", lambda cmd: launched.append(cmd))

    win._start_impl()
    assert cap_calls == [("gpu", "bt.2390"), ("cpu", "hable")]
    # F3: method_combo remains unchanged ("BT.2390 · GPU tonemap"); override is per-job only
    assert win.method_combo.currentText() == "BT.2390 \u00b7 GPU tonemap"
    assert win._job_tonemap_override == "Hable \u00b7 CPU tonemap"
    assert win.collect_settings().tonemap_method == "Hable \u00b7 CPU tonemap"
    assert len(launched) == 1
    assert any("tonemap=hable" in str(arg) for arg in launched[0])

    # Profile 5 has no HDR10 base layer -> must NOT fall back to CPU tonemapping
    launched.clear()
    cap_calls.clear()
    win._dv_profile = 5
    win.method_combo.setCurrentText("BT.2390 \u00b7 GPU tonemap")
    win._start_impl()
    assert cap_calls == [("gpu", "bt.2390")]
    assert launched == []


def test_libplacebo_runtime_oom_on_frame_zero_retries_with_cpu_tonemap(win, tmp_path, monkeypatch):
    """If 128x128 preflight passes but 4K libplacebo fails on frame 0 during encoding with
    VK_ERROR_OUT_OF_DEVICE_MEMORY, _on_finished switches to Hable · CPU tonemap and retries."""
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    dst, part = _arm(win, tmp_path)
    win.duration = 120.0
    win.current_out_sec = 0.0
    win.using_hb = False
    win._dv_profile = 8
    win._gpu_tonemap_fallback_used = False
    win.has_bt2390 = True
    win.refresh_method_choices(force_default=True)
    win.method_combo.setCurrentText("BT.2390 \u00b7 GPU tonemap")
    win._hints.gpu_tonemap = True

    retried = []
    monkeypatch.setattr(win, "_confirm_tonemap_fallback", lambda *_a, **_k: True)
    monkeypatch.setattr(win, "start", lambda: retried.append(win.collect_settings().tonemap_method))

    win._on_finished(1, QProcess.ExitStatus.NormalExit)
    app = QApplication.instance()
    for _ in range(6):
        app.processEvents()

    # F3: method_combo stays on the user selection; only the job snapshot switches to Hable
    assert win.method_combo.currentText() == "BT.2390 \u00b7 GPU tonemap"
    assert win._job_tonemap_override == "Hable \u00b7 CPU tonemap"
    assert retried == ["Hable \u00b7 CPU tonemap"]
    assert not part.exists()


def test_frame_preview_suppressed_during_queue_start(win, tmp_path):
    """_schedule_frame_preview must not start the 400ms still-frame preview timer while
    _analysis_start_pending, _start_in_progress, or _queue_controller.running is True."""
    src = tmp_path / "video.mkv"
    src.write_bytes(b"video")
    win.src_edit.setText(str(src))
    win._frame_preview_timer.stop()

    win._analysis_start_pending = True
    win._schedule_frame_preview()
    assert not win._frame_preview_timer.isActive()
    win._analysis_start_pending = False

    win._queue_controller.running = True
    win._schedule_frame_preview()
    assert not win._frame_preview_timer.isActive()
    win._queue_controller.running = False

    win._schedule_frame_preview()
    assert win._frame_preview_timer.isActive()
    win._frame_preview_timer.stop()


def test_stop_frame_preview_runs_before_preflight_and_keeps_gpu_tonemap_when_transient_oom_clears(win, tmp_path, monkeypatch):
    """Case 2a in MainWindow._start_impl():
    When a 4K preview process was holding Vulkan BAR1 memory and caused a cached transient
    VK_ERROR_OUT_OF_DEVICE_MEMORY in capabilities._CACHE, _start_impl() stops the preview
    process and invalidates the transient OOM cache BEFORE check_start_capabilities() runs,
    so GPU tone mapping ('BT.2390 · GPU tonemap') succeeds without needing CPU fallback."""
    import hdr2sdr.ui.main_window as mw_mod
    from hdr2sdr.runtime import capabilities

    src = tmp_path / "Cyberpunk Edgerunners 2 - 01 [2160p WEB-DL DV HDR10].mkv"
    src.write_bytes(b"video")
    dst = tmp_path / "Cyberpunk_SDR.mkv"
    win.src_edit.setText(str(src))
    win.dst_edit.setText(str(dst))
    win.kind = "dolby"
    win._dv_profile = 8
    win._queue_controller.running = True
    win.has_bt2390 = True
    win.encoders = {"hevc_nvenc", "libx265", "libx264"}
    win.refresh_method_choices(force_default=True)
    win.method_combo.setCurrentText("BT.2390 \u00b7 GPU tonemap")

    fake_ff = tmp_path / "ffmpeg.exe"
    fake_ff.write_bytes(b"ffmpeg")
    monkeypatch.setattr(mw_mod, "exe", lambda name: str(fake_ff) if name == "ffmpeg" else None)
    monkeypatch.setattr(win, "_enough_disk_space", lambda: True)
    monkeypatch.setattr(win, "_offer_orphan_resume", lambda *_a, **_k: False)
    monkeypatch.setattr(win, "_write_part_sidecar", lambda *_a, **_k: None)

    class FakeResultOk:
        returncode = 0
        stderr = ""

    capabilities.clear_probe_cache()
    try:
        # Seed a stale transient OOM entry in _CACHE (as if probed while preview was holding BAR1)
        key = (*capabilities._binary_identity(fake_ff), ("libplacebo", "bt.2390"))
        capabilities._CACHE[key] = (
            False,
            "[libplacebo] Allocation of size 256K failed: VK_ERROR_OUT_OF_DEVICE_MEMORY!",
        )
        monkeypatch.setattr(capabilities.subprocess, "run", lambda *a, **k: FakeResultOk())

        launched = []
        monkeypatch.setattr(win, "_launch", lambda cmd: launched.append(cmd))

        win._start_impl()
        assert win.method_combo.currentText() == "BT.2390 \u00b7 GPU tonemap"
        assert len(launched) == 1
        assert any("libplacebo=" in str(arg) for arg in launched[0])
    finally:
        capabilities.clear_probe_cache()
