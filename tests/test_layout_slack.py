"""Starting a conversion shows the speed / CPU rows: the window grows by exactly what they need,
and the CONTROLS card must not end up with an empty band at the bottom."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_no_empty_band_in_controls_when_stats_rows_appear():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from hdr2sdr.ui.main_window import MainWindow
    w = MainWindow()
    w.show()

    def pump():
        for _ in range(8):
            app.processEvents()
    pump()
    card = w.controls_card
    before = card.height() - card.minimumSizeHint().height()
    w.speed_label.setText("1.17x realtime · 2 fps · 4.6 MB/s · ETA 1h 56m 26s")
    w.speed_label.show()
    w.resource_label.setText("This process: 739% CPU · System: 66% CPU · GPU: 5%")
    w.resource_label.show()
    pump()
    after = card.height() - card.minimumSizeHint().height()
    assert after <= before + 1, (before, after)
    w.close()
