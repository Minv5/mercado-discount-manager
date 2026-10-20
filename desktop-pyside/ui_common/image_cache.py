from __future__ import annotations

import hashlib
import logging
import ssl
import threading
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap

from engine.crypto import get_data_dir

logger = logging.getLogger(__name__)

_MEM_CACHE: dict[str, QPixmap] = {}
_DISK_CACHE_DIR = get_data_dir() / "image_cache"
_LOCK = threading.Lock()
_IMG_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="img_dl")


def _get_cache_path(url: str) -> Path:
    _DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
    return _DISK_CACHE_DIR / f"{url_hash}.dat"


def make_rounded_pixmap(pixmap: QPixmap, size: QSize, radius: int = 6) -> QPixmap:
    """Scale pixmap and apply smooth rounded corners."""
    if pixmap.isNull():
        return pixmap
    scaled = pixmap.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    # Center crop to exact size
    crop_x = max(0, (scaled.width() - size.width()) // 2)
    crop_y = max(0, (scaled.height() - size.height()) // 2)
    cropped = scaled.copy(crop_x, crop_y, size.width(), size.height())

    rounded = QPixmap(size)
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return rounded


def make_contain_pixmap(pixmap: QPixmap, size: QSize, radius: int = 4) -> QPixmap:
    """Scale pixmap preserving aspect ratio without cropping (contain/letterbox)."""
    if pixmap.isNull():
        return pixmap
    scaled = pixmap.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if radius <= 0:
        return scaled
    rounded = QPixmap(scaled.size())
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, scaled.width(), scaled.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return rounded


def get_placeholder_pixmap(size: QSize, text: str = "📦", radius: int = 6) -> QPixmap:
    """Generate dark moss placeholder thumbnail with subtle border."""
    pix = QPixmap(size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    painter.fillPath(path, QBrush(QColor("#1A211D")))

    pen = QPen(QColor("#4E472F"))
    pen.setWidth(1)
    painter.setPen(pen)
    painter.drawPath(path)

    painter.setPen(QColor("#C8C3B7"))
    font = QFont("PingFang SC", max(9, size.height() // 3))
    painter.setFont(font)
    painter.drawText(QRect(0, 0, size.width(), size.height()), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pix


def load_image_async(
    url: str,
    target_size: QSize,
    on_loaded: Callable[[QPixmap], None] | None = None,
    radius: int = 6,
    crop: bool = True,
    **kwargs: Any,
) -> None:
    """Load image asynchronously from local disk, memory cache, or network, returning rounded QPixmap on GUI thread."""
    callback: Callable[[QPixmap], None] | None = on_loaded or kwargs.get("callback")
    if not callback:
        return

    clean_url = str(url or "").strip()
    if not clean_url:
        callback(get_placeholder_pixmap(target_size, "📦", radius=radius))
        return

    # 1. Local file path check (skip filesystem stat for remote URLs)
    is_local_file = False
    if not clean_url.startswith(("http://", "https://", "data:")):
        local_p = Path(clean_url)
        try:
            if local_p.is_file():
                is_local_file = True
        except Exception:
            is_local_file = False

    if not is_local_file and not clean_url.startswith(("http://", "https://")):
        callback(get_placeholder_pixmap(target_size, "📦", radius=radius))
        return

    # 2. Check Memory Cache
    cache_key = f"{'local:' if is_local_file else ''}{clean_url}_{target_size.width()}x{target_size.height()}_{radius}_{crop}"
    with _LOCK:
        if cache_key in _MEM_CACHE:
            callback(_MEM_CACHE[cache_key])
            return

    # 3. For local files, process asynchronously via event loop without waiting in network thread pool
    if is_local_file:
        def _process_local() -> None:
            raw_bytes: bytes | None = None
            try:
                raw_bytes = local_p.read_bytes()
            except Exception as e:
                logger.debug("Failed reading local image %s: %s", clean_url, e)

            if raw_bytes:
                img = QImage()
                if img.loadFromData(raw_bytes) and not img.isNull():
                    aspect = Qt.AspectRatioMode.KeepAspectRatioByExpanding if crop else Qt.AspectRatioMode.KeepAspectRatio
                    scaled = img.scaled(target_size, aspect, Qt.TransformationMode.SmoothTransformation)
                    pix = QPixmap.fromImage(scaled)
                    rounded = make_rounded_pixmap(pix, target_size, radius=radius) if crop else make_contain_pixmap(pix, target_size, radius=radius)
                    with _LOCK:
                        _MEM_CACHE[cache_key] = rounded
                    callback(rounded)
                    return
            callback(get_placeholder_pixmap(target_size, "📦", radius=radius))

        app_inst = QCoreApplication.instance()
        if app_inst:
            QTimer.singleShot(0, app_inst, _process_local)
        else:
            _process_local()
        return

    # 4. Asynchronous worker for fetching network images
    def _worker() -> None:
        fetch_url = clean_url
        if fetch_url.startswith("http://"):
            fetch_url = "https://" + fetch_url[7:]

        disk_path = _get_cache_path(clean_url)
        raw_bytes: bytes | None = None

        # Check Disk Cache
        if disk_path.exists():
            try:
                cached_data = disk_path.read_bytes()
                if len(cached_data) > 100:
                    raw_bytes = cached_data
            except Exception:
                raw_bytes = None

        # Network Fetch with unverified SSL context and fallback
        if not raw_bytes:
                ssl_ctx = ssl._create_unverified_context()
                candidate_urls = [fetch_url]
                if clean_url != fetch_url:
                    candidate_urls.append(clean_url)
                if fetch_url.startswith("https://"):
                    candidate_urls.append("http://" + fetch_url[8:])

                for attempt in range(2):
                    for u in candidate_urls:
                        try:
                            req = urllib.request.Request(
                                u,
                                headers={
                                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                                    "Accept": "image/*",
                                },
                            )
                            with urllib.request.urlopen(req, context=ssl_ctx, timeout=9.0) as resp:
                                data = resp.read()
                                if len(data) > 100:
                                    raw_bytes = data
                                    try:
                                        disk_path.write_bytes(raw_bytes)
                                    except Exception as write_err:
                                        logger.debug("Failed writing disk cache for %s: %s", u, write_err)
                                    break
                        except Exception as err:
                            logger.debug("Attempt %s failed to download image %s: %s", attempt, u, err)
                    if raw_bytes:
                        break
                    time.sleep(0.2)

        prepared_img: QImage | None = None
        if raw_bytes:
            img = QImage()
            if img.loadFromData(raw_bytes) and not img.isNull():
                aspect = Qt.AspectRatioMode.KeepAspectRatioByExpanding if crop else Qt.AspectRatioMode.KeepAspectRatio
                prepared_img = img.scaled(
                    target_size,
                    aspect,
                    Qt.TransformationMode.SmoothTransformation,
                )

        def _on_main_thread(qimg: QImage | None) -> None:
            if qimg and not qimg.isNull():
                pix = QPixmap.fromImage(qimg)
                if crop:
                    rounded = make_rounded_pixmap(pix, target_size, radius=radius)
                else:
                    rounded = make_contain_pixmap(pix, target_size, radius=radius)
                with _LOCK:
                    if len(_MEM_CACHE) > 300:
                        for k in list(_MEM_CACHE.keys())[:80]:
                            _MEM_CACHE.pop(k, None)
                    _MEM_CACHE[cache_key] = rounded
            else:
                rounded = get_placeholder_pixmap(target_size, "📦", radius=radius)

            try:
                callback(rounded)
            except Exception as e:
                logger.debug("Error in image callback: %s", e)

        app_inst = QCoreApplication.instance()
        if app_inst:
            QTimer.singleShot(0, app_inst, lambda: _on_main_thread(prepared_img))
        else:
            _on_main_thread(prepared_img)

    _IMG_POOL.submit(_worker)


def clear_memory_cache() -> None:
    """Clear memory pixmap cache."""
    with _LOCK:
        _MEM_CACHE.clear()


def get_cache_size_bytes() -> int:
    """Calculate the total size of downloaded images in disk cache."""
    if not _DISK_CACHE_DIR.exists():
        return 0
    total = 0
    for p in _DISK_CACHE_DIR.glob("*.dat"):
        try:
            total += p.stat().st_size
        except Exception:
            pass
    return total


def clear_disk_cache() -> int:
    """Clear all cached images on disk. Returns number of files removed."""
    if not _DISK_CACHE_DIR.exists():
        return 0
    removed = 0
    for p in _DISK_CACHE_DIR.glob("*.dat"):
        try:
            p.unlink()
            removed += 1
        except Exception:
            pass
    clear_memory_cache()
    return removed
