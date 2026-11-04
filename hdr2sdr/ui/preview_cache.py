from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from PySide6.QtGui import QImage, QPixmap


def compute_preview_cache_key(
    src_path: str | Path,
    seek: float,
    settings_dict: dict[str, Any],
) -> str:
    """Computes a deterministic, collision-resistant cache key for a preview frame.
    Accounts for normalized source identity (path, size, mtime_ns), seek position,
    and all image-affecting filter parameters without scanning large video contents."""
    src_p = Path(src_path).resolve()
    norm_path = os.path.normcase(os.path.normpath(str(src_p)))
    st_size = 0
    st_mtime_ns = 0
    try:
        st = src_p.stat()
        st_size = st.st_size
        st_mtime_ns = st.st_mtime_ns
    except OSError:
        pass

    seek_str = f"{float(seek):.2f}"

    payload = {
        "src": norm_path,
        "size": st_size,
        "mtime_ns": st_mtime_ns,
        "seek": seek_str,
        "tonemap_method": settings_dict.get("tonemap_method", ""),
        "gamut_mode": settings_dict.get("gamut_mode", "Perceptual"),
        "dv_rpu": bool(settings_dict.get("dv_rpu", False)),
        "peak_detect": bool(settings_dict.get("peak_detect", False)),
        "desat": float(settings_dict.get("desat", 2.0)),
        "denoise": settings_dict.get("denoise", "Off"),
        "autocrop_value": settings_dict.get("autocrop_value", ""),
        "resharpen": bool(settings_dict.get("resharpen", False)),
        "deband": bool(settings_dict.get("deband", False)),
        "brightness": settings_dict.get("brightness", 0),
        "saturation": settings_dict.get("saturation", 0),
        "colortemperature": settings_dict.get("colortemperature", 6500),
        "resolution_label": settings_dict.get("resolution_label", "Source (no scaling)"),
    }
    raw = json.dumps(payload, sort_keys=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"pv_{digest}"


class PreviewCache:
    """Manages cached preview frames in a dedicated temporary directory and in-memory LRU.
    Bounded both in memory and on disk to prevent unbounded storage consumption."""

    _instance: PreviewCache | None = None

    def __init__(
        self,
        cache_dir: Path | None = None,
        max_memory_items: int = 64,
        max_disk_items: int = 128,
        max_disk_bytes: int = 100 * 1024 * 1024,
    ):
        if cache_dir is None:
            cache_dir = Path(tempfile.gettempdir()) / "hdr2sdr_preview_cache"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_memory_items = max_memory_items
        self.max_disk_items = max_disk_items
        self.max_disk_bytes = max_disk_bytes
        self._memory_cache: OrderedDict[str, QPixmap] = OrderedDict()

    @classmethod
    def get_default(cls) -> PreviewCache:
        if cls._instance is None:
            cls._instance = PreviewCache()
        return cls._instance

    def get_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.png"

    def _put_memory(self, key: str, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        if key in self._memory_cache:
            self._memory_cache.move_to_end(key)
        self._memory_cache[key] = pixmap
        while len(self._memory_cache) > self.max_memory_items:
            self._memory_cache.popitem(last=False)

    def get_pixmap(self, key: str) -> QPixmap | None:
        if key in self._memory_cache:
            self._memory_cache.move_to_end(key)
            pix = self._memory_cache[key]
            if not pix.isNull():
                return pix
        p = self.get_path(key)
        if p.is_file():
            img = QImage(str(p))
            if not img.isNull():
                pix = QPixmap.fromImage(img)
                self._put_memory(key, pix)
                return pix
        return None

    def put(self, key: str, pixmap: QPixmap, source_file: Path | None = None) -> None:
        if not pixmap.isNull():
            self._put_memory(key, pixmap)
        out_p = self.get_path(key)
        saved = False
        if source_file is not None and Path(source_file).is_file() and Path(source_file) != out_p:
            try:
                shutil.copy2(source_file, out_p)
                saved = True
            except OSError:
                pass
        if not saved and not pixmap.isNull():
            try:
                pixmap.save(str(out_p), "PNG")
                saved = True
            except Exception:
                pass

        if saved:
            self.prune_disk()

    def prune_disk(self) -> None:
        """Evicts oldest preview files if disk cache exceeds item or byte quotas."""
        try:
            files = list(self.cache_dir.glob("pv_*.png"))
            if not files:
                return

            stats = []
            total_size = 0
            for f in files:
                try:
                    st = f.stat()
                    stats.append((f, st.st_mtime, st.st_size))
                    total_size += st.st_size
                except OSError:
                    pass

            if len(stats) <= self.max_disk_items and total_size <= self.max_disk_bytes:
                return

            # Sort oldest first by mtime
            stats.sort(key=lambda x: x[1])

            remaining_count = len(stats)
            for f, _, sz in stats:
                if remaining_count > self.max_disk_items or total_size > self.max_disk_bytes:
                    try:
                        f.unlink(missing_ok=True)
                        remaining_count -= 1
                        total_size -= sz
                    except OSError:
                        pass
                else:
                    break
        except Exception:
            pass

    def clear(self) -> None:
        self._memory_cache.clear()
        for f in self.cache_dir.glob("pv_*.png"):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
