"""QA tests for QUE area: queue operations, selected file conversion, and deletion handling."""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.main_window import MainWindow


def get_qapp():
    return QApplication.instance() or QApplication([])


def test_que_04_start_selected_file_only(tmp_path):
    """QUE-04: _start_selected_file_only converts only the selected item without starting the full queue."""
    qapp = get_qapp()
    w = MainWindow()

    src1 = tmp_path / "item1.mkv"
    src1.write_bytes(b"content1")
    src2 = tmp_path / "item2.mkv"
    src2.write_bytes(b"content2")
    src3 = tmp_path / "item3.mkv"
    src3.write_bytes(b"content3")

    w.queue_list.clear()
    for s in (src1, src2, src3):
        w.queue_list.addItem(str(s))
    w._queue_controller.paths = [str(src1), str(src2), str(src3)]

    # Select the second item (row 1)
    w.queue_list.setCurrentRow(1)

    log_messages = []
    w.write = lambda msg: log_messages.append(msg)

    with patch.object(w, "start") as mock_start:
        w._start_selected_file_only()
        mock_start.assert_not_called()  # It uses QTimer.singleShot(0, self.start)
        qapp.processEvents()
        mock_start.assert_called_once()

    # The selected item (src2) was removed from queue to be processed
    assert len(w._queue_controller.paths) == 2
    assert str(src2) not in w._queue_controller.paths
    assert w.queue_list.count() == 2
    # Full queue was not started
    assert not w._queue_controller.running
    assert any("item2.mkv (selected file only)" in m for m in log_messages)


def test_que_06_item_deletion_during_encode(tmp_path, monkeypatch):
    """QUE-06: Deleting pending items while an encode is in progress does not disrupt the running encode."""
    qapp = get_qapp()
    w = MainWindow()

    src1 = tmp_path / "running.mkv"
    src1.write_bytes(b"data1")
    src2 = tmp_path / "pending1.mkv"
    src2.write_bytes(b"data2")
    src3 = tmp_path / "pending2.mkv"
    src3.write_bytes(b"data3")

    w.queue_list.clear()
    for s in (src1, src2, src3):
        w.queue_list.addItem(str(s))
    w._queue_controller.paths = [str(src1), str(src2), str(src3)]
    w._queue_controller.running = True
    w._queue_controller.current_index = 0

    from PySide6.QtCore import QProcess
    w.proc = QProcess(w)
    monkeypatch.setattr(w, "_is_proc_running", lambda: True)

    # Remove the second item (row 1: pending1.mkv)
    w.queue_list.setCurrentRow(1)
    w._remove_selected_queue_items()

    # Running process is undisturbed
    assert w.proc is not None
    assert w._queue_controller.running
    # Queue controller paths updated
    assert len(w._queue_controller.paths) == 2
    assert str(src2) not in w._queue_controller.paths
    assert w._queue_controller.paths[1] == str(src3)
    assert w.queue_list.count() == 2
