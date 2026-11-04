"""Qt process owner for a running conversion command."""

from __future__ import annotations

import codecs
import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from . import jobobject
from hdr2sdr.core.progress import LineAssembler, make_progress_parser


def is_valid_qobject(obj) -> bool:
    """Return True if ``obj`` is a non-None wrapper whose underlying C++ QObject is still alive.

    Uses ``shiboken6.isValid`` when available and also probes the wrapper for
    ``RuntimeError`` ('Internal C++ object ... already deleted'). Plain Python
    test doubles return True unless they explicitly set ``_deleted = True`` or
    raise ``RuntimeError`` on access.
    """
    if obj is None:
        return False
    if getattr(obj, "_deleted", False):
        return False
    try:
        from shiboken6 import isValid  # type: ignore[import-not-found]
        if not isValid(obj):
            return False
    except ImportError:
        pass
    except TypeError:
        pass
    try:
        if hasattr(obj, "objectName"):
            obj.objectName()
    except RuntimeError:
        return False
    return True


def is_process_running(proc) -> bool:
    """Return True if ``proc`` is a valid QProcess currently in the ``Running`` state."""
    if not is_valid_qobject(proc):
        return False
    try:
        return proc.state() == QProcess.ProcessState.Running
    except RuntimeError:
        return False


def is_process_active(proc) -> bool:
    """Return True if ``proc`` is a valid QProcess in ``Starting`` or ``Running`` state."""
    if not is_valid_qobject(proc):
        return False
    try:
        return proc.state() != QProcess.ProcessState.NotRunning
    except RuntimeError:
        return False


class ConversionRunner(QObject):
    """Owns the conversion QProcess and forwards bytes and lifecycle signals."""

    output = Signal(bytes)
    parsed_line = Signal(str, object, bool)
    finished = Signal(int, object)
    process_error = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process: QProcess | None = None
        self.last_run_ok: bool | None = None
        self._line_assembler = LineAssembler()
        self._log_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._progress_parser = make_progress_parser(False)
        self._duration = 0.0
        self._resume_offset = 0.0

    def _on_process_destroyed(self, destroyed_proc=None) -> None:
        if destroyed_proc is None or self.process is destroyed_proc:
            self.process = None

    def start(self, command: list[str], start_timeout_ms: int = 3000, *,
              using_handbrake: bool = False, duration: float = 0.0,
              resume_offset: float = 0.0, on_created=None):
        if self.process is not None and not is_valid_qobject(self.process):
            self.process = None
        if is_process_active(self.process):
            raise RuntimeError("A conversion process is already running.")
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda p=process: self._forward_output(p))
        process.finished.connect(lambda code, status, p=process: self._forward_finished(code, status, p))
        process.errorOccurred.connect(lambda err, p=process: self._forward_error(err, p))
        if hasattr(process, "destroyed"):
            try:
                process.destroyed.connect(lambda *_args, p=process: self._on_process_destroyed(p))
            except Exception:
                pass
        process.setProgram(command[0])
        process.setArguments(command[1:])
        self.process = process
        if callable(on_created):
            on_created(process)
        self._line_assembler.reset()
        self._log_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._progress_parser = make_progress_parser(using_handbrake)
        self._duration = duration or 0.0
        self._resume_offset = resume_offset or 0.0
        process.start()
        started = False
        if is_valid_qobject(process):
            try:
                started = bool(process.waitForStarted(start_timeout_ms))
            except RuntimeError:
                started = False
        bound = False
        if started and is_valid_qobject(process):
            try:
                bound = jobobject.bind(process.processId())
            except RuntimeError:
                bound = False
        return process, started, bound

    def _emit_parsed_lines(self, lines) -> None:
        for line in lines:
            self._progress_parser.configure(self._duration, self._resume_offset)
            stats, consumed = self._progress_parser.feed(line)
            self.parsed_line.emit(line, stats, consumed)

    def _forward_output(self, proc=None):
        process = proc if proc is not None else self.process
        if process is None or (proc is not None and self.process is not None and proc is not self.process):
            return
        if not is_valid_qobject(process):
            if self.process is process:
                self.process = None
            return
        try:
            data = bytes(process.readAllStandardOutput())
        except RuntimeError:
            if self.process is process:
                self.process = None
            return
        if data:
            self.output.emit(data)
            self.consume_output(data)

    def consume_output(self, data: bytes) -> None:
        text = self._log_decoder.decode(bytes(data))
        self._emit_parsed_lines(self._line_assembler.feed(text))

    def flush_output(self, proc=None) -> None:
        """Drain remaining process stdout, finalise UTF-8 decoding, and flush buffered lines."""
        process = proc if proc is not None else self.process
        if process is not None and is_valid_qobject(process):
            try:
                data = bytes(process.readAllStandardOutput())
            except Exception:
                data = b""
            if data:
                self.output.emit(data)
                self.consume_output(data)
        tail = self._log_decoder.decode(b"", final=True)
        lines = self._line_assembler.feed(tail) if tail else []
        lines.extend(self._line_assembler.flush())
        self._emit_parsed_lines(lines)

    def record_result(self, succeeded: bool) -> None:
        self.last_run_ok = bool(succeeded)

    def _forward_error(self, error, proc=None):
        if proc is not None and self.process is not None and proc is not self.process:
            return
        self.process_error.emit(error)

    def _forward_finished(self, exit_code, exit_status, proc=None):
        if proc is not None and self.process is not None and proc is not self.process:
            return
        self.flush_output(proc)
        self.finished.emit(int(exit_code), exit_status)

    def release(self, expected_process=None):
        """Release and schedule deletion of the finished QProcess.

        If ``expected_process`` is provided and ``self.process`` has already moved on
        to a new QProcess (e.g. a resume segment, concat step, or next queue item),
        only ``expected_process`` is cleaned up and the active ``self.process`` is
        left untouched.
        """
        if expected_process is not None and self.process is not expected_process:
            target = expected_process
        else:
            target, self.process = self.process, None
        if target is not None and is_valid_qobject(target):
            for sig_name in ("readyReadStandardOutput", "finished", "errorOccurred"):
                sig = getattr(target, sig_name, None)
                if sig is not None and hasattr(sig, "disconnect"):
                    try:
                        sig.disconnect()
                    except Exception:
                        pass
            if hasattr(target, "deleteLater"):
                try:
                    target.deleteLater()
                except RuntimeError:
                    pass

    @staticmethod
    def publish_temp(part: Path | str, final: Path | str) -> OSError | None:
        try:
            os.replace(part, final)
            return None
        except OSError as error:
            return error
