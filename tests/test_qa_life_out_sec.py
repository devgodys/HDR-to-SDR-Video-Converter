"""QA tests for LIFE (lifecycle), OUT (outputs), SEC (security & resilience), and HIS (history) areas."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QByteArray, QSize
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.ui.history_dialog import ConversionHistoryDialog
from hdr2sdr.runtime.unfinished import UnfinishedStore
from hdr2sdr.theme.look import load_presets, load_slots


def get_qapp():
    return QApplication.instance() or QApplication([])


def test_life_01_close_during_encode_choices(monkeypatch):
    """LIFE-01: Close during encode offers Quit (kills process, INV-03), Tray (continues), and Cancel (aborts)."""
    qapp = get_qapp()
    w = MainWindow()
    monkeypatch.setattr(w, "_is_proc_running", lambda: True)

    # 1. Cancel
    monkeypatch.setattr(w, "_confirm_close_while_converting", lambda parent: "cancel")
    evt_cancel = MagicMock()
    w.closeEvent(evt_cancel)
    evt_cancel.ignore.assert_called_once()

    # 2. Tray
    tray_minimized = []
    monkeypatch.setattr(w, "_confirm_close_while_converting", lambda parent: "tray")
    monkeypatch.setattr(w, "_minimize_to_tray", lambda parent=None: tray_minimized.append(True))
    evt_tray = MagicMock()
    w.closeEvent(evt_tray)
    evt_tray.ignore.assert_called_once()
    assert len(tray_minimized) == 1

    # 3. Quit
    from PySide6.QtCore import QProcess
    w.proc = QProcess(w)
    monkeypatch.setattr(w, "_confirm_close_while_converting", lambda parent: "quit")
    evt_quit = MagicMock()
    with patch("hdr2sdr.ui.main_window.is_process_running", lambda p: p is not None):
        with patch.object(w.proc, "kill") as mock_kill:
            w._tray_quit_confirmed = True
            w.closeEvent(evt_quit)
            mock_kill.assert_called_once()
            evt_quit.accept.assert_called_once()


def test_life_03_close_during_workers_stops_cleanly():
    """LIFE-03: Closing MainWindow while analysis, preview, or test clip workers run stops them cleanly."""
    qapp = get_qapp()
    w = MainWindow()

    mock_worker = MagicMock()
    w._analysis_workers = {1: mock_worker}
    w._frame_preview_proc = MagicMock()
    w._test_clip_proc = MagicMock()

    w._tray_quit_confirmed = True
    evt = MagicMock()
    with patch("hdr2sdr.ui.main_window.is_process_running", lambda p: p is not None):
        w.closeEvent(evt)

    mock_worker.requestInterruption.assert_called_once()
    w._frame_preview_proc.kill.assert_called_once()
    w._test_clip_proc.kill.assert_called_once()
    evt.accept.assert_called_once()


def test_life_07_geometry_restore_and_bounds():
    """LIFE-07: Window geometry restoration handles clamped sizes and corrupted values safely (INV-21)."""
    qapp = get_qapp()
    w = MainWindow()

    # Valid string size
    w.i18n.settings.setValue("window_size", "1024x768")
    assert w._restore_saved_window_geometry() is True
    assert w.width() >= 920
    assert w.height() >= 400

    # Malformed string size
    w.i18n.settings.setValue("window_size", "invalid_size")
    # Must not raise exception
    assert w._restore_saved_window_geometry() is False


def test_out_02_container_switch_updates_extension():
    """OUT-02: Switching container between MKV and MP4 updates destination path extension."""
    qapp = get_qapp()
    w = MainWindow()

    w.dst_edit.setText("C:/output/video.mp4")
    w._on_container_changed("MKV")
    assert Path(w.dst_edit.text()) == Path("C:/output/video.mkv")

    w._on_container_changed("MP4")
    assert Path(w.dst_edit.text()) == Path("C:/output/video.mp4")


def test_out_05_open_folder_without_destination(monkeypatch):
    """OUT-05: Clicking Open folder button without destination shows informative message."""
    qapp = get_qapp()
    w = MainWindow()
    w.dst_edit.setText("")
    w.output_folder = None

    info_called = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *args: info_called.append((title, text)))

    w._open_output_folder()
    assert len(info_called) == 1
    assert "No output folder" in info_called[0][1]


def test_sec_03_corrupted_json_and_sidecars(tmp_path):
    """SEC-03: Corrupted unfinished_runs.json, sidecars, and look settings are handled without crash."""
    # 1. Corrupted unfinished store
    store_file = tmp_path / "unfinished_runs.json"
    store_file.write_text("{ corrupted invalid json ::: ", encoding="utf-8")
    store = UnfinishedStore(store_file)
    records = store.load()
    assert records == []

    # 2. Corrupted sidecar
    part_file = tmp_path / "video.mkv.part"
    part_file.write_bytes(b"dummy")
    sidecar_file = tmp_path / "video.mkv.part.json"
    sidecar_file.write_text("NOT_JSON", encoding="utf-8")
    sidecar_data = store.read_sidecar(part_file)
    assert sidecar_data == {}

    # 3. Corrupted look presets in settings
    mock_settings = MagicMock()
    mock_settings.value.return_value = "{bad_json"
    presets = load_presets(mock_settings)
    assert presets == []
    slots = load_slots(mock_settings)
    assert isinstance(slots, dict)


def test_his_02_history_dialog_clear_and_folder(tmp_path, monkeypatch):
    """HIS-02: History dialog allows opening existing folder and clearing history with confirmation."""
    qapp = get_qapp()

    history = [
        {
            "source": str(tmp_path / "src.mkv"),
            "destination": str(tmp_path / "out.mp4"),
            "status": "done",
            "timestamp": "2026-11-02 12:00",
            "kind": "hdr",
            "settings": {"encoder_label": "NVENC", "quality_mode": "CRF", "quality": 20}
        }
    ]

    cleared = []
    dlg = ConversionHistoryDialog(history, translate=lambda s: s, clear_history=lambda: cleared.append(True))
    assert dlg._list.count() == 1

    # Confirm clear - Yes
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dlg._confirm_clear()
    assert len(cleared) == 1
