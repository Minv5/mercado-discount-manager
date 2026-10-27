from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from engine.crypto import get_data_dir

logger = logging.getLogger(__name__)

_PRINTED_FILE: Path | None = None
_PRINTED_CACHE: dict[str, str] = {}
_INITIALIZED = False


def _get_printed_file() -> Path:
    global _PRINTED_FILE
    if _PRINTED_FILE is None:
        _PRINTED_FILE = get_data_dir() / "printed_orders.json"
    return _PRINTED_FILE


def load_printed_orders() -> dict[str, str]:
    global _PRINTED_CACHE, _INITIALIZED
    if _INITIALIZED:
        return _PRINTED_CACHE
    f = _get_printed_file()
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _PRINTED_CACHE = {str(k).strip(): str(v).strip() for k, v in data.items()}
        except Exception as e:
            logger.warning("Failed loading printed_orders.json: %s", e)
    _INITIALIZED = True
    return _PRINTED_CACHE


def mark_order_ids_printed(order_ids: list[str]) -> None:
    cache = load_printed_orders()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed = False
    for oid in order_ids:
        s_oid = str(oid or "").strip()
        if s_oid:
            cache[s_oid] = now_str
            changed = True
    if changed:
        try:
            f = _get_printed_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed saving printed_orders.json: %s", e)


def is_order_printed(order_id: str, pack_id: str = "", shipping_status: str = "", shipping_substatus: str = "") -> bool:
    sub_st = (shipping_substatus or "").strip().lower()
    if sub_st in ("printed", "shipped", "delivered"):
        return True
    st = (shipping_status or "").strip().lower()
    if st in ("shipped", "delivered", "in_transit"):
        return True
    cache = load_printed_orders()
    s_oid = str(order_id or "").strip()
    if s_oid and s_oid in cache:
        return True
    s_pid = str(pack_id or "").strip()
    if s_pid and s_pid in cache:
        return True
    return False


def get_order_print_time(order_id: str, pack_id: str = "") -> str:
    cache = load_printed_orders()
    s_oid = str(order_id or "").strip()
    if s_oid and s_oid in cache:
        return cache[s_oid]
    s_pid = str(pack_id or "").strip()
    if s_pid and s_pid in cache:
        return cache[s_pid]
    return ""


def set_printed_file_for_test(path: Path | None) -> None:
    global _PRINTED_FILE, _INITIALIZED, _PRINTED_CACHE
    _PRINTED_FILE = path
    _PRINTED_CACHE.clear()
    _INITIALIZED = False


def clear_printed_cache_for_test(empty_cache: bool = True) -> None:
    global _PRINTED_CACHE, _INITIALIZED
    _PRINTED_CACHE.clear()
    _INITIALIZED = empty_cache
