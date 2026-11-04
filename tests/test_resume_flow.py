"""Resume after a failed long conversion: hardware decode dying mid-run is
handled by an automatic software-decode resume, and a chained resume (second
failure) no longer deletes the file it is about to keep (WinError 2)."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

RUNTIME_LINE = "[hevc @ 000001dd4a6dbcc0] Failed to transfer data to output frame: -12."


@pytest.fixture
def win(monkeypatch):
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    for _ in range(4):
        app.processEvents()
    # no dialog may ever block a test: any attempt to show one is a failure
    def no_dialog(*a, **k):
        raise AssertionError("a dialog was shown")
    monkeypatch.setattr(QMessageBox, "exec", no_dialog)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(no_dialog))
    w.using_hb = False
    yield w
    w.close()


def failed_run(w, tmp_path, progress=400.0, duration=1000.0, hw=True):
    active = tmp_path / "movie.sdr.mkv"
    active.write_bytes(b"x" * 100)
    w.duration = duration
    w.current_out_sec = progress
    w._resume_coordinator.progress_offset = 0.0
    w.hwaccel_chk.setChecked(hw)
    w._hints.reset()
    w._hints.configure(encoder_label=None, hwaccel_decode_on=hw)
    w._hints.feed(RUNTIME_LINE)
    return active


def test_hw_decode_failure_mid_run_resumes_automatically_with_software_decode(win, tmp_path, monkeypatch):
    active = failed_run(win, tmp_path)
    started = []
    monkeypatch.setattr(win, "_begin_resume", lambda p: started.append(p))
    assert win._offer_resume(active) is True                # resumed without asking
    assert started == [active]
    assert win._hwaccel_forced_off is True
    assert win.hwaccel_chk.isChecked()                       # the person's toggle is untouched...
    assert win.collect_settings().hwaccel is False           # ...but the next segment decodes in software
    assert win._hwaccel_wanted() is False
    win._hwaccel_forced_off = False
    win._resume_coordinator.reset()                         # a new job starts clean
    assert win._hwaccel_forced_off is False and win.collect_settings().hwaccel is True


def test_no_endless_loop_second_failure_goes_the_normal_way(win, tmp_path, monkeypatch):
    active = failed_run(win, tmp_path)
    win._hwaccel_forced_off = True                           # already switched once
    monkeypatch.setattr(win, "_begin_resume", lambda p: pytest.fail("must not auto-resume again"))
    with pytest.raises(AssertionError, match="a dialog was shown"):
        win._offer_resume(active)                            # the ordinary "Resume?" dialog


def test_failure_too_early_only_hints(win, tmp_path, monkeypatch):
    active = failed_run(win, tmp_path, progress=5.0)         # below the resume threshold
    logs = []
    monkeypatch.setattr(win, "write", logs.append)
    monkeypatch.setattr(win, "_begin_resume", lambda p: pytest.fail("too early to resume"))
    assert win._offer_resume(active) is False
    assert any("Turn off" in l for l in logs) and not win._hwaccel_forced_off


def test_runtime_error_with_hw_decode_off_is_not_auto_handled(win, tmp_path, monkeypatch):
    active = failed_run(win, tmp_path, hw=False)             # software decode: ENOMEM is real
    assert win._hints.hw_decode_runtime is False
    with pytest.raises(AssertionError, match="a dialog was shown"):
        win._offer_resume(active)


def test_chained_resume_keeps_the_file_that_just_failed(win, tmp_path, monkeypatch):
    final = tmp_path / "movie_SDR.mkv"
    win.dst_edit.setText(str(final))
    win._hwaccel_forced_off = False
    win._resume_coordinator.reset()
    monkeypatch.setattr(win, "_probe_duration", lambda p: 600.0)
    monkeypatch.setattr(win, "_start_resume_segment", lambda: None)
    # 1st failure: the output itself is renamed to ...resume-part1
    final.write_bytes(b"first-segment")
    win._begin_resume(final)
    part1 = tmp_path / "movie_SDR.resume-part1.mkv"
    assert part1.read_bytes() == b"first-segment" and not final.exists()
    assert win._resume_coordinator.output_override == tmp_path / "movie_SDR.resume-part2.mkv"
    # 2nd failure: the resumed segment (already called ...resume-part2) fails too.
    part2 = win._resume_coordinator.output_override
    part2.write_bytes(b"second-segment")
    win._begin_resume(part2)                                 # used to: unlink part2, then WinError 2
    assert part2.read_bytes() == b"second-segment"           # kept
    assert win._resume_coordinator.parts == [part1, part2]
    assert win._resume_coordinator.output_override == tmp_path / "movie_SDR.resume-part3.mkv"
    assert part1.exists()                                    # earlier parts untouched

