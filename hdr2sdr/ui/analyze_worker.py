"""Background ffprobe worker for source analysis."""

from __future__ import annotations

import json

from PySide6.QtCore import QEventLoop, QProcess, QThread, QTimer, Signal

from hdr2sdr.ui.probe import ProbeError


class AnalyzeWorker(QThread):
    completed = Signal(int, str, object, object)

    def __init__(self, generation: int, executable: str, source: str, arguments=None):
        super().__init__()
        self.generation = generation
        self.executable = executable
        self.source = source
        self.arguments = list(arguments) if arguments is not None else [
            "-v", "error", "-select_streams", "v:0", "-show_streams",
            "-show_format", "-of", "json", self.source]

    def run(self):
        if self.isInterruptionRequested():
            return
        process = QProcess()
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        loop = QEventLoop()
        state = {"error": None, "timed_out": False, "cancelled": False}

        def stop_if_needed():
            if self.isInterruptionRequested():
                state["cancelled"] = True
            if state["cancelled"] or state["timed_out"]:
                process.kill()
                loop.quit()

        def on_timeout():
            state["timed_out"] = True
            process.kill()
            loop.quit()

        def on_error(error):
            if error == QProcess.ProcessError.FailedToStart:
                state["error"] = ProbeError(f"could not start {self.executable}")
                loop.quit()

        process.finished.connect(lambda *_: loop.quit())
        process.errorOccurred.connect(on_error)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(on_timeout)
        cancellation_poll = QTimer()
        cancellation_poll.timeout.connect(stop_if_needed)
        try:
            process.start(self.executable, self.arguments)
            if not process.waitForStarted(3000):
                state["error"] = ProbeError(f"could not start {self.executable}")
            else:
                timeout.start(90_000)
                cancellation_poll.start(50)
                loop.exec()
            timeout.stop()
            cancellation_poll.stop()
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1000)
            if state["cancelled"] or self.isInterruptionRequested():
                return
            if state["timed_out"]:
                raise ProbeError(f"{self.executable} did not answer within 90 s")
            if state["error"] is not None:
                raise state["error"]
            if process.exitCode() != 0:
                raise ProbeError(f"{self.executable} exited with code {process.exitCode()}")
            output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="strict")
            document = json.loads(output)
            self.completed.emit(self.generation, self.source, document, None)
        except Exception as error:
            self.completed.emit(self.generation, self.source, None, error)
