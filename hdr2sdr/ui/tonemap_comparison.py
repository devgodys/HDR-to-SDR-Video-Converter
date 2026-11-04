from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtGui import QImage, QPixmap

from hdr2sdr.core.commands import build_vf_chain
from hdr2sdr.core.constants import (
    GAMUT_MAPPING_LABELS,
    TONEMAP_PRO_CPU,
    TONEMAP_PRO_GPU,
    TONEMAP_PRO_GPU_ST2094,
)
from hdr2sdr.ui.preview_cache import PreviewCache, compute_preview_cache_key
from hdr2sdr.runtime import jobobject


@dataclass
class TonemapCompareItem:
    method: str
    gamut: str  # "Perceptual", "Relative", "Clip", or "N/A"
    engine: str  # "gpu" or "cpu"
    status: str = "pending"  # "pending", "rendering", "ready", "error", "cancelled"
    error_code: str = ""  # "failed_to_start", "corrupt_image", "process_failed"
    error_detail: str = ""  # Technical stderr line
    exit_code: int = 0
    error_message: str = ""
    pixmap: QPixmap | None = None
    file_path: Path | None = None
    cache_key: str = ""


def build_comparison_variants(
    has_bt2390: bool,
    has_st2094: bool = False,
    dv_profile: int | None = None,
) -> list[TonemapCompareItem]:
    """Pure builder: returns all valid tone-mapping and gamut-mapping combinations
    supported in the current build for the source.
    - GPU curves support gamut mapping (Perceptual, Relative, Clip).
    - CPU curves do not support gamut mapping (set to 'N/A').
    - Dolby Vision Profile 5 requires GPU tonemapping; CPU curves are excluded."""
    variants: list[TonemapCompareItem] = []

    # 1. GPU tonemap curves (libplacebo)
    if has_bt2390:
        gpu_methods = [
            "BT.2390 \u00b7 GPU tonemap",
            "Spline \u00b7 GPU tonemap",
            *list(TONEMAP_PRO_GPU),
        ]
        if has_st2094:
            gpu_methods += list(TONEMAP_PRO_GPU_ST2094)

        for m in gpu_methods:
            for g in GAMUT_MAPPING_LABELS:
                variants.append(TonemapCompareItem(method=m, gamut=g, engine="gpu"))

    # 2. CPU tonemap curves
    if dv_profile != 5:
        cpu_methods = [
            "Hable \u00b7 CPU tonemap",
            "Reinhard \u00b7 CPU tonemap",
            "Mobius \u00b7 CPU tonemap",
            *list(TONEMAP_PRO_CPU),
        ]
        for m in cpu_methods:
            variants.append(TonemapCompareItem(method=m, gamut="N/A", engine="cpu"))

    return variants


class TonemapComparator(QObject):
    """Executes sequential, non-parallel single-frame renders for all comparison variants.
    Provides cancellation, error reporting, progress tracking, and cache reuse.
    Operates completely asynchronously without blocking GUI thread."""

    variant_started = Signal(int, object)  # (index, item)
    variant_finished = Signal(int, object, bool)  # (index, item, success)
    all_finished = Signal()

    def __init__(
        self,
        ffmpeg_exe: str,
        src_path: str,
        seek: float,
        base_settings: Any,
        variants: list[TonemapCompareItem],
        cache: PreviewCache | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.ffmpeg_exe = ffmpeg_exe
        self.src_path = src_path
        self.seek = seek
        self.base_settings = base_settings
        self.variants = variants
        self.cache = cache or PreviewCache.get_default()
        self.active_proc: QProcess | None = None
        self._current_index = -1
        self._cancelled = False
        self._gen = 0
        self._single_target_index: int | None = None

    def prepare_cache(self) -> None:
        """Populate cache keys and restore any already-cached frames without launching FFmpeg."""
        for item in self.variants:
            v_gamut = item.gamut if item.engine == "gpu" else "Perceptual"
            v_settings = replace(self.base_settings, tonemap_method=item.method, gamut_mode=v_gamut)
            s_dict = asdict(v_settings) if hasattr(v_settings, "__dataclass_fields__") else v_settings.__dict__
            item.cache_key = compute_preview_cache_key(self.src_path, self.seek, s_dict)
            cached_pix = self.cache.get_pixmap(item.cache_key)
            if cached_pix is not None and not cached_pix.isNull():
                item.status = "ready"
                item.pixmap = cached_pix
                item.file_path = self.cache.get_path(item.cache_key)
            else:
                item.status = "pending"

    def start(self) -> None:
        self._cancelled = False
        self._single_target_index = None
        self._gen += 1
        self._cleanup_proc()
        self.prepare_cache()
        self._advance()

    def render_single(self, index: int) -> None:
        """Render only the specified variant index without rendering the rest of the queue."""
        if not (0 <= index < len(self.variants)):
            return
        self._cancelled = False
        self._gen += 1
        self._cleanup_proc()
        for i, v in enumerate(self.variants):
            if v.status == "rendering" and i != index:
                v.status = "pending"
        target = self.variants[index]
        if not target.cache_key:
            self.prepare_cache()
        target.status = "pending"
        self._single_target_index = index
        self._advance()

    def _cleanup_proc(self) -> None:
        proc = self.active_proc
        self.active_proc = None
        if proc is not None:
            try:
                proc.started.disconnect()
            except Exception:
                pass
            try:
                proc.finished.disconnect()
            except Exception:
                pass
            try:
                proc.errorOccurred.disconnect()
            except Exception:
                pass
            try:
                if proc.state() != QProcess.ProcessState.NotRunning:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.deleteLater()
            except Exception:
                pass

    def _advance(self) -> None:
        if self._cancelled:
            self.all_finished.emit()
            return

        if self._single_target_index is not None:
            t_idx = self._single_target_index
            if 0 <= t_idx < len(self.variants) and self.variants[t_idx].status == "pending":
                next_idx = t_idx
            else:
                self._single_target_index = None
                self.all_finished.emit()
                return
        else:
            next_idx = None
            for i, item in enumerate(self.variants):
                if item.status == "pending":
                    next_idx = i
                    break

            if next_idx is None:
                self.all_finished.emit()
                return

        self._current_index = next_idx
        item = self.variants[next_idx]
        item.status = "rendering"
        self.variant_started.emit(next_idx, item)

        v_gamut = item.gamut if item.engine == "gpu" else "Perceptual"
        v_settings = replace(self.base_settings, tonemap_method=item.method, gamut_mode=v_gamut)
        vf = build_vf_chain(v_settings)
        out_path = self.cache.get_path(item.cache_key)
        out_path.unlink(missing_ok=True)

        cmd = [
            self.ffmpeg_exe,
            "-hide_banner",
            "-y",
            "-ss",
            f"{self.seek:.2f}",
            "-i",
            str(self.src_path),
            "-frames:v",
            "1",
            "-vf",
            vf,
            "-c:v",
            "png",
            str(out_path),
        ]

        gen = self._gen
        proc = QProcess(self)
        proc.setProgram(cmd[0])
        proc.setArguments(cmd[1:])
        proc.started.connect(lambda p=proc: self._on_proc_started(p))
        proc.finished.connect(
            lambda exit_code, _status, gen=gen, idx=next_idx: self._on_proc_finished(exit_code, gen, idx)
        )
        proc.errorOccurred.connect(
            lambda err, gen=gen, idx=next_idx: self._on_proc_error(err, gen, idx)
        )
        self.active_proc = proc
        proc.start()

    def _on_proc_started(self, proc: QProcess) -> None:
        try:
            pid = proc.processId()
            if pid:
                jobobject.bind(pid)
        except Exception:
            pass

    def _on_proc_error(self, error: QProcess.ProcessError, gen: int, idx: int) -> None:
        if gen != self._gen or self._cancelled:
            return

        if error == QProcess.ProcessError.FailedToStart:
            self._cleanup_proc()
            item = self.variants[idx]
            item.status = "error"
            item.error_code = "failed_to_start"
            item.error_detail = ""
            item.exit_code = -1
            item.error_message = "Failed to start FFmpeg"
            self.variant_finished.emit(idx, item, False)
            self._advance()

    def _on_proc_finished(self, exit_code: int, gen: int, idx: int) -> None:
        if gen != self._gen or self._cancelled:
            return

        old_proc = self.active_proc
        stderr_text = ""
        if old_proc is not None:
            try:
                raw_err = old_proc.readAllStandardError().data()
                stderr_text = raw_err.decode("utf-8", errors="replace").strip()
            except Exception:
                pass
        self._cleanup_proc()

        item = self.variants[idx]
        out_path = self.cache.get_path(item.cache_key)

        success = False
        if exit_code == 0 and out_path.is_file():
            img = QImage(str(out_path))
            if not img.isNull():
                pix = QPixmap.fromImage(img)
                self.cache.put(item.cache_key, pix, out_path)
                item.status = "ready"
                item.pixmap = pix
                item.file_path = out_path
                item.error_code = ""
                item.error_detail = ""
                item.exit_code = 0
                item.error_message = ""
                success = True
            else:
                item.status = "error"
                item.error_code = "corrupt_image"
                item.error_detail = ""
                item.exit_code = 0
                item.error_message = "Corrupt or unreadable preview frame"
        else:
            item.status = "error"
            item.error_code = "process_failed"
            item.exit_code = exit_code
            err_detail = ""
            if stderr_text:
                lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
                if lines:
                    err_detail = lines[-1]
            item.error_detail = err_detail
            item.error_message = f"FFmpeg failed with exit code {exit_code}"

        self.variant_finished.emit(idx, item, success)
        self._advance()

    def cancel(self) -> None:
        self._cancelled = True
        self._single_target_index = None
        self._gen += 1
        self._cleanup_proc()

        for item in self.variants:
            if item.status in ("pending", "rendering"):
                item.status = "cancelled"

        self.all_finished.emit()
