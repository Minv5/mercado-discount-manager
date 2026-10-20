from __future__ import annotations

# Re-export from ui_common.image_cache for full backward compatibility
from ui_common.image_cache import (
    _DISK_CACHE_DIR,
    _IMG_POOL,
    _LOCK,
    _MEM_CACHE,
    _get_cache_path,
    clear_disk_cache,
    clear_memory_cache,
    get_cache_size_bytes,
    get_placeholder_pixmap,
    load_image_async,
    make_contain_pixmap,
    make_rounded_pixmap,
)

__all__ = [
    "clear_disk_cache",
    "clear_memory_cache",
    "get_cache_size_bytes",
    "get_placeholder_pixmap",
    "load_image_async",
    "make_contain_pixmap",
    "make_rounded_pixmap",
    "_DISK_CACHE_DIR",
    "_IMG_POOL",
    "_LOCK",
    "_MEM_CACHE",
    "_get_cache_path",
]
