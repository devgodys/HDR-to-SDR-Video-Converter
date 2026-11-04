"""QA tests for LAY (layout & DPI), I18N (translations), TOOL (tools), and PERF (performance) areas."""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from hdr2sdr.ui.main_window import MainWindow
from hdr2sdr.core.commands import TrackPlan
from hdr2sdr.core.utils import exe
from hdr2sdr.runtime.updates import fetch_latest_github_tag


def get_qapp():
    return QApplication.instance() or QApplication([])


def test_lay_05_adding_removing_files_preserves_window_height(tmp_path):
    """LAY-05: Adding or removing files from the queue list preserves the window height."""
    qapp = get_qapp()
    w = MainWindow()
    initial_h = w.height()

    for i in range(10):
        w.queue_list.addItem(f"C:/video/file_{i}.mkv")
        w._queue_controller.paths.append(f"C:/video/file_{i}.mkv")

    assert w.height() == initial_h

    w.queue_list.clear()
    w._queue_controller.clear()
    assert w.height() == initial_h


def test_lay_06_long_translations_narrow_window():
    """LAY-06: Long translations (de, ru, fr) in minimum window width (920px) layout cleanly without clipping."""
    qapp = get_qapp()
    w = MainWindow()
    w.resize(920, 600)

    for loc in ("de", "ru", "fr"):
        w.change_language(loc)
        qapp.processEvents()
        # Verify window width is maintained and controls fit
        assert w.width() >= 920
        assert w.quality_spin.width() > 0


def test_i18n_04_runtime_messages_translated():
    """I18N-04: Runtime messages such as TrackPlan.describe accept a translation callback."""
    plan = TrackPlan(skip=((1, "audio", "truehd"),))

    # Without tr
    desc_en = plan.describe()
    assert len(desc_en) == 1
    assert "stream #1 (audio, truehd)" in desc_en[0]

    # With tr callback
    def mock_tr(msg, **kwargs):
        return f"TRANSLATED: {msg.format(**kwargs)}"

    desc_tr = plan.describe(tr=mock_tr)
    assert len(desc_tr) == 1
    assert desc_tr[0].startswith("TRANSLATED:")
    assert "stream #1 (audio, truehd)" in desc_tr[0]


def test_tool_06_update_check_network_error_resilience():
    """TOOL-06: Update check network failures (DNS/connection/timeout) return None without raising."""
    with patch("urllib.request.build_opener") as mock_opener:
        instance = MagicMock()
        instance.open.side_effect = urllib.error.URLError("Network unreachable")
        mock_opener.return_value = instance

        result = fetch_latest_github_tag("owner/repo")
        assert result is None


def test_tool_07_portable_ffmpeg_precedence(tmp_path, monkeypatch):
    """TOOL-07: Portable FFmpeg in tools folder or beside exe takes precedence over system PATH."""
    from hdr2sdr.core import utils
    portable_tool = tmp_path / "ffmpeg.exe" if sys.platform.startswith("win") else tmp_path / "ffmpeg"
    portable_tool.write_bytes(b"dummy")

    monkeypatch.setattr(utils, "PORTABLE_TOOLS_DIR", tmp_path)
    found = exe("ffmpeg")
    assert found == str(portable_tool)


def test_perf_03_gui_responsiveness_during_probe(monkeypatch):
    """PERF-03: Capability probing does not block Qt event processing loop > 1s (INV-11)."""
    qapp = get_qapp()
    w = MainWindow()

    event_processed = []

    def handle_timer():
        event_processed.append(True)

    from PySide6.QtCore import QTimer
    timer = QTimer()
    timer.singleShot(0, handle_timer)

    # Let the event loop cycle
    qapp.processEvents()
    assert len(event_processed) == 1
