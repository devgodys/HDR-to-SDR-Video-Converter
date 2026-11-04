"""Pure-Python QtCore stubs for headless test runners when PySide6 is not installed."""

from __future__ import annotations

import importlib.machinery
import inspect
import sys
import types


class _BoundSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, slot=None):
        if slot is None:
            self._slots.clear()
        elif slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class Signal:
    def __init__(self, *_types):
        self._name = ""

    def __set_name__(self, owner, name):
        self._name = f"__sig_{name}"

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        sig = instance.__dict__.get(self._name)
        if sig is None:
            sig = _BoundSignal()
            instance.__dict__[self._name] = sig
        return sig


class QObject:
    def __init__(self, parent=None):
        self._parent = parent


class QProcess(QObject):
    class ProcessState:
        NotRunning = 0
        Starting = 1
        Running = 2

    class ProcessChannelMode:
        SeparateChannels = 0
        MergedChannels = 1

    class ExitStatus:
        NormalExit = 0
        CrashExit = 1

    class ProcessError:
        FailedToStart = 0
        Crashed = 1


def install_qtcore_stubs_if_missing() -> bool:
    """Install minimal PySide6.QtCore stubs when PySide6 is not installed."""
    try:
        import PySide6.QtCore  # noqa: F401
        return False
    except ImportError:
        pass
    for finder in sys.meta_path:
        if getattr(finder, "_is_hdr2sdr_qt_stub_finder", False):
            return True

    class _QtCoreStubLoader:
        @staticmethod
        def create_module(spec):
            if spec.name == "PySide6":
                pkg = types.ModuleType("PySide6")
                pkg.__path__ = []
                pkg._is_stub = True
                return pkg
            mod = types.ModuleType("PySide6.QtCore")
            mod.QObject = QObject
            mod.QProcess = QProcess
            mod.Signal = Signal
            mod._is_stub = True
            return mod

        @staticmethod
        def exec_module(module):
            if module.__name__ == "PySide6.QtCore":
                pkg = sys.modules.get("PySide6")
                if pkg is not None:
                    pkg.QtCore = module

    class _QtCoreStubFinder:
        _is_hdr2sdr_qt_stub_finder = True

        @staticmethod
        def find_spec(fullname, path=None, target=None):
            if fullname in ("PySide6", "PySide6.QtCore"):
                frame = inspect.currentframe()
                caller_globals = ""
                try:
                    f = frame.f_back
                    while f is not None:
                        mod_name = f.f_globals.get("__name__", "")
                        if mod_name and not mod_name.startswith("importlib"):
                            caller_globals = mod_name
                            break
                        f = f.f_back
                finally:
                    del frame
                if caller_globals == "hdr2sdr.runtime.conversion" or caller_globals.startswith("tests."):
                    return importlib.machinery.ModuleSpec(
                        fullname, _QtCoreStubLoader, is_package=(fullname == "PySide6")
                    )
            return None

    sys.meta_path.append(_QtCoreStubFinder)
    return True
