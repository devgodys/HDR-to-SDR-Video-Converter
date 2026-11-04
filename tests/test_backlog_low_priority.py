import pytest


pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit
from hdr2sdr.core.bitrate import estimate_output_bytes


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow

    win = MainWindow()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    yield win
    win.close()


def test_activity_log_is_bounded_and_keeps_position_when_reading_history(window):
    assert isinstance(window.log, QPlainTextEdit)
    assert window.log.document().maximumBlockCount() == 5000
    for line in range(80):
        window.write(f"line {line}\n")
    scrollbar = window.log.verticalScrollBar()
    scrollbar.setValue(0)
    old_value = scrollbar.value()
    old_max = scrollbar.maximum()
    window.write("new activity\n")
    assert scrollbar.value() == old_value
    assert scrollbar.maximum() >= old_max


def test_disk_space_estimate_uses_duration_and_selected_bitrate():
    # 10 Mbps for one hour is about 5.6 GB plus 25% headroom, even if the
    # source file itself is tiny.
    expected = estimate_output_bytes(3600, 10_000, source_size_bytes=5)
    assert 5_600_000_000 < expected < 5_700_000_000
    assert expected > 2 * 1024**3


def test_main_window_no_longer_exposes_compatibility_delegates(window):
    removed = (
        "_source_collides_with_outputs", "_unfinished_for_source", "_last_run_ok",
        "_temp_part_path", "_is_temp_part", "command_ffmpeg", "command_handbrake",
        "_resume_parts", "_resume_outpoints", "_hw_encoder_error_seen",
    )
    assert not [name for name in removed if hasattr(window, name)]
