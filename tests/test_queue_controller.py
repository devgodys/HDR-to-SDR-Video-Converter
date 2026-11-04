from hdr2sdr.runtime.queue import QueueController


def test_queue_state_add_take_and_start(tmp_path):
    controller = QueueController()
    first = tmp_path / "one.mkv"
    second = tmp_path / "two.mkv"
    assert controller.add(first)
    assert not controller.add(first)
    assert controller.add(second)
    controller.start()
    assert controller.running and controller.batch_clean
    assert controller.take_next() == str(first)
    assert controller.paths == [str(second)]
    controller.mark_unreviewed()
    assert not controller.batch_clean
    controller.stop()
    assert not controller.running


def test_queue_unattended_depends_on_remaining_items_or_after_action(tmp_path):
    controller = QueueController()
    controller.start()
    assert not controller.unattended("none", "none")
    controller.add(tmp_path / "next.mkv")
    assert controller.unattended("none", "none")
    controller.paths.clear()
    assert controller.unattended("shutdown", "none")


def test_queue_skips_missing_files_and_detects_sdr():
    assert QueueController.is_missing("path-that-does-not-exist.mkv")
    assert QueueController.is_sdr("sdr")
    assert not QueueController.is_sdr("hdr")


def test_queue_advance_owns_start_skip_and_finish_decisions(tmp_path):
    controller = QueueController()
    valid = tmp_path / "valid.mkv"
    valid.touch()
    missing = tmp_path / "missing.mkv"
    controller.add(missing)
    controller.add(valid)
    controller.start()

    skipped = controller.advance()
    assert (skipped.action, skipped.path) == ("skipped", str(missing))
    assert not controller.batch_clean
    started = controller.advance()
    assert (started.action, started.path) == ("start", str(valid))
    finished = controller.advance()
    assert finished.action == "finished"
    assert finished.was_running
    assert not controller.running


def test_selected_queue_item_does_not_start_batch(tmp_path):
    controller = QueueController()
    controller.add(tmp_path / "one.mkv")
    controller.add(tmp_path / "two.mkv")
    controller.start()

    assert controller.take_selected(1) == str(tmp_path / "two.mkv")
    assert controller.paths == [str(tmp_path / "one.mkv")]
    assert not controller.running


def test_queue_completion_decides_advance_stop_or_standalone():
    controller = QueueController()
    controller.start()
    assert controller.complete_item(True, False).action == "advance"
    assert controller.complete_item(False, True).action == "stopped"
    assert not controller.running
    assert controller.complete_item(False, False).action == "standalone"
    assert controller.complete_item(False, True).action == "none"


def test_auto_destination_checks_collisions(tmp_path):
    original = tmp_path / "movie_SDR.mkv"
    original.touch()
    (tmp_path / "movie_SDR_1.mkv").touch()
    assert QueueController.free_destination(original) == tmp_path / "movie_SDR_2.mkv"


def test_qprocess_already_deleted_guard_and_delayed_release_does_not_clobber_new_process():
    """FIX_QPROCESS_ALREADY_DELETED_2026-10-24:
    1) is_valid_qobject / is_process_running return False (never raising RuntimeError) when
       a QProcess wrapper raises 'Internal C++ object (PySide6.QtCore.QProcess) already deleted'.
    2) ConversionRunner.release(expected_process=old_proc) deletes only old_proc when
       runner.process has already advanced to new_proc (e.g., resume segment / concat / next item).
    3) Stale callbacks (_forward_output, flush_output, _forward_finished, _forward_error)
       on a deleted or superseded process wrapper are safely ignored."""
    from hdr2sdr.runtime.conversion import (
        ConversionRunner,
        QProcess,
        is_process_active,
        is_process_running,
        is_valid_qobject,
    )

    class DeletedProcessStub:
        def objectName(self):
            raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")

        def state(self):
            raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")

        def readAllStandardOutput(self):
            raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")

        def deleteLater(self):
            raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")

    class LiveProcessStub:
        def __init__(self, chunk=b"frame=10\n"):
            self._chunk = chunk
            self.deleted_later = False
            self._state = QProcess.ProcessState.Running

        def objectName(self):
            if self.deleted_later:
                raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")
            return "live"

        def state(self):
            if self.deleted_later:
                raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")
            return self._state

        def readAllStandardOutput(self):
            if self.deleted_later:
                raise RuntimeError("Internal C++ object (PySide6.QtCore.QProcess) already deleted.")
            c, self._chunk = self._chunk, b""
            return c

        def deleteLater(self):
            self.deleted_later = True

    dead = DeletedProcessStub()
    assert not is_valid_qobject(dead)
    assert not is_process_running(dead)
    assert not is_process_active(dead)
    assert not is_valid_qobject(None)

    live1 = LiveProcessStub(b"old-out\n")
    live2 = LiveProcessStub(b"new-out\n")
    assert is_valid_qobject(live1) and is_process_running(live1)

    runner = ConversionRunner()
    runner.process = live1

    # Simulate _release_proc() clearing runner.process and scheduling runner.release(live1),
    # while a new process (live2) is started synchronously before the singleShot(0) fires:
    runner.process = live2
    runner.release(expected_process=live1)
    assert live1.deleted_later is True
    assert not is_valid_qobject(live1)
    assert runner.process is live2
    assert live2.deleted_later is False
    assert is_valid_qobject(live2) and is_process_running(live2)

    # Stale callbacks from live1 or dead must be ignored and never touch live2
    parsed = []
    runner.parsed_line.connect(lambda line, _st, _c: parsed.append(line))
    runner._forward_output(live1)
    runner._forward_output(dead)
    runner._forward_finished(1, 0, proc=live1)
    assert parsed == []
    assert runner.process is live2

    # Current process output still works normally
    runner._forward_output(live2)
    assert parsed == ["new-out"]

    # Normal release of current process
    runner.release(expected_process=live2)
    assert runner.process is None
    assert live2.deleted_later is True


def test_queue_clear_and_sync_with_visible_prevents_stale_relaunch(tmp_path):
    """FIX_QPROCESS_ALREADY_DELETED_2026-10-24:
    QueueController.clear() and sync_with_visible(0) ensure that an empty visible queue
    never retains stale paths or running state, and MainWindow wires _clear_loaded_source_when_idle()
    into _remove_queue_item(), _on_start_clicked(), and start_queue()."""
    from pathlib import Path

    controller = QueueController()
    f1 = tmp_path / "old_movie.mkv"
    f1.write_bytes(b"video")
    controller.add(f1)
    controller.start()
    assert controller.running and len(controller.paths) == 1

    # If the visible queue list is empty (0 rows), sync_with_visible(0) must purge stale state
    controller.sync_with_visible(0)
    assert controller.paths == []
    assert not controller.running

    # Explicit clear() also resets paths, running, and batch_clean
    controller.add(f1)
    controller.start()
    controller.mark_unreviewed()
    controller.clear()
    assert controller.paths == []
    assert not controller.running
    assert controller.batch_clean

    mw_src = (Path(__file__).resolve().parent.parent / "hdr2sdr" / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert "def _clear_loaded_source_when_idle(self):" in mw_src
    assert "self._queue_controller.sync_with_visible(self.queue_list.count())" in mw_src
    assert "lambda p=old, r=runner: r.release(p)" in mw_src
