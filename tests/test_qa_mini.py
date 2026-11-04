"""QA tests for MINI area: Mini mode window behaviour, presets, switching, and settings parity."""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QMimeData, QUrl, QPoint
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.mini_window import MiniModeWindow


def get_qapp():
    return QApplication.instance() or QApplication([])


def test_mini_01_launch_drop_and_start(tmp_path):
    """MINI-01: MiniModeWindow launches, handles drag and drop of video file, and sets up analysis."""
    qapp = get_qapp()
    engine = MainWindow()
    mini = MiniModeWindow(engine)

    src = tmp_path / "test_hdr.mkv"
    src.write_bytes(b"dummy_hdr_content")

    # Simulate drop event
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(src))])
    event = QDropEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime_data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    def fake_set_source(p, analyze=True):
        engine.src_edit.setText(str(p))
        # simulate an active worker during drop
        worker = MagicMock()
        worker.isRunning.return_value = True
        engine._analysis_workers = {1: worker}

    with patch.object(engine, "_set_source", side_effect=fake_set_source) as mock_set_source:
        mini.dropEvent(event)
        assert mock_set_source.called
        assert Path(mock_set_source.call_args[0][0]) == src
        assert mock_set_source.call_args[1].get("analyze") is True

    assert mini.drop_label.text() == "test_hdr.mkv"
    assert "Analyzing" in mini.status_label.text()


def test_mini_02_sync_from_engine_during_encode():
    """MINI-02: Switching between Mini and Pro during active encode reflects state accurately."""
    qapp = get_qapp()
    engine = MainWindow()
    mini = MiniModeWindow(engine)

    # Simulate active conversion
    mock_proc = MagicMock()
    engine.proc = mock_proc
    engine.pbar.setValue(450)
    engine.src_edit.setText("c:/videos/sample.mkv")

    mini.sync_from_engine()

    assert "sample.mkv" in mini.drop_label.text()
    assert "Converting" in mini.status_label.text()
    assert not mini.pbar.isHidden()
    assert mini.pbar.value() == 450
    assert not mini.mini_fast_btn.isEnabled()
    assert mini._poll_timer.isActive()

    # Simulate conversion completion
    engine.proc = None
    engine.pbar.setValue(1000)
    engine._conversion_runner.last_run_ok = True
    mini._poll_progress()

    assert mini.pbar.isHidden()
    assert "Done" in mini.status_label.text()
    assert mini.mini_fast_btn.isEnabled()


def test_mini_03_clean_close(monkeypatch):
    """MINI-03: MiniModeWindow closeEvent handles cancel and quit paths cleanly."""
    qapp = get_qapp()
    engine = MainWindow()
    mini = MiniModeWindow(engine)

    # Cancel close
    monkeypatch.setattr(engine, "_confirm_close_while_converting", lambda parent: "cancel")
    mock_event = MagicMock()
    mini.closeEvent(mock_event)
    mock_event.ignore.assert_called_once()

    # Quit path
    monkeypatch.setattr(engine, "_confirm_close_while_converting", lambda parent: "quit")
    mock_event_quit = MagicMock()
    with patch.object(engine, "close") as mock_eng_close:
        with patch.object(QApplication.instance(), "quit") as mock_qapp_quit:
            mini.closeEvent(mock_event_quit)
            mock_eng_close.assert_called_once()
            mock_event_quit.accept.assert_called_once()
            mock_qapp_quit.assert_called_once()


def test_mini_04_skip_mini_window_toggle():
    """MINI-04: skip_mini_window toggle persists preference and triggers open_pro when checked."""
    qapp = get_qapp()
    engine = MainWindow()
    mini = MiniModeWindow(engine)

    with patch.object(mini, "open_pro") as mock_open_pro:
        mini._on_skip_mini_toggled(True)
        assert engine.i18n.settings.value("skip_mini_window") == "true"
        mock_open_pro.assert_called_once()

    mini._on_skip_mini_toggled(False)
    assert engine.i18n.settings.value("skip_mini_window") == "false"


def test_mini_05_analysis_feedback_messages():
    """MINI-05: on_analysis_complete gives distinct messages for HDR, SDR, Dolby Vision, and failed probe."""
    qapp = get_qapp()
    engine = MainWindow()
    mini = MiniModeWindow(engine)

    # Corrupted / unknown
    engine.kind = "unknown"
    mini.on_analysis_complete(False)
    assert "Couldn't read this file" in mini.status_label.text()
    assert not mini.mini_optimal_btn.isEnabled()

    # HDR
    engine.kind = "hdr"
    mini.on_analysis_complete(True)
    assert "HDR detected" in mini.status_label.text()
    assert mini.mini_optimal_btn.isEnabled()

    # SDR
    engine.kind = "sdr"
    mini.on_analysis_complete(True)
    assert "already looks like SDR" in mini.status_label.text()
    assert mini.mini_optimal_btn.isEnabled()

    # Dolby Vision
    engine.kind = "dolby"
    mini.on_analysis_complete(True)
    assert "Dolby Vision detected" in mini.status_label.text()
    assert mini.mini_optimal_btn.isEnabled()


def test_mini_06_preset_settings_parity(tmp_path):
    """MINI-06: Mini preset buttons and Pro preset methods produce identical ConversionSettings."""
    qapp = get_qapp()
    engine = MainWindow()
    mini = MiniModeWindow(engine)

    src = tmp_path / "hdr_source.mkv"
    src.write_bytes(b"data")
    engine.src_edit.setText(str(src))
    engine.dst_edit.setText(str(tmp_path / "out.mp4"))
    engine.kind = "hdr"

    # Compare Fast preset
    engine._apply_fast_preset()
    pro_fast = engine.collect_settings()

    # Reset and call through mini
    with patch.object(engine, "start"):
        mini._start_with(engine._apply_fast_preset)
        mini_fast = engine.collect_settings()
    assert pro_fast == mini_fast

    # Compare Optimal preset
    engine._apply_optimal_preset()
    pro_opt = engine.collect_settings()

    with patch.object(engine, "start"):
        mini._start_with(engine._apply_optimal_preset)
        mini_opt = engine.collect_settings()
    assert pro_opt == mini_opt

    # Compare Best Quality preset
    engine._apply_best_quality_preset()
    pro_best = engine.collect_settings()

    with patch.object(engine, "start"):
        mini._start_with(engine._apply_best_quality_preset)
        mini_best = engine.collect_settings()
    assert pro_best == mini_best
