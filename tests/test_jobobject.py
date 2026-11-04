import sys

from hdr2sdr.runtime import jobobject


def test_bind_is_a_noop_off_windows():
    if sys.platform.startswith("win"):
        return
    assert jobobject.bind(1234) is False
    assert jobobject.bind(0) is False
