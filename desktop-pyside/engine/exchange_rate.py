"""Real-time Multi-currency Exchange Rate Engine for Mercado Libre Orders.

Provides live foreign exchange rates to CNY/RMB with multi-tier fallback:
1. User-customized rate in settings.json (priority override)
2. Live market rate via official financial feeds (Sina Finance / Open Exchange Rates)
3. Local disk cache (exchange_rates.json)
4. Industry standard baseline fallback (e.g. 7.20 for USD)
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from .crypto import get_data_dir

logger = logging.getLogger(__name__)

DEFAULT_EXCHANGE_RATES: dict[str, float] = {
    "USD": 7.20,
    "MXN": 0.37,
    "BRL": 1.30,
    "COP": 0.0017,
    "CLP": 0.0075,
    "ARS": 0.005,
    "UYU": 0.17,
    "PEN": 1.95,
    "CNY": 1.0,
    "RMB": 1.0,
}

_memory_rates: dict[str, float] = {}
_last_fetch_ts: float = 0.0
_is_fetching = False
_lock = threading.Lock()


def _get_cache_file() -> Path:
    return get_data_dir() / "exchange_rates.json"


def _load_disk_cache() -> tuple[dict[str, float], float]:
    path = _get_cache_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rates = data.get("rates") or {}
            ts = float(data.get("timestamp") or 0.0)
            clean_rates = {k.upper(): float(v) for k, v in rates.items() if float(v) > 0}
            return clean_rates, ts
        except Exception as e:
            logger.debug("Failed loading exchange_rates.json: %s", e)
    return {}, 0.0


def _save_disk_cache(rates: dict[str, float]) -> None:
    try:
        path = _get_cache_file()
        payload = {
            "rates": rates,
            "timestamp": time.time(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug("Failed saving exchange_rates.json: %s", e)


def fetch_live_rates_network() -> dict[str, float]:
    """Fetch live rates from authoritative public feeds."""
    rates: dict[str, float] = {"CNY": 1.0, "RMB": 1.0}
    usd_cny = 0.0

    # 1. Try Sina finance for live USD/CNY rate (ultra fast onshore quote)
    try:
        req = urllib.request.Request(
            "https://hq.sinajs.cn/list=fx_susdcny",
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            content = resp.read().decode("gbk", errors="ignore")
            m = re.search(r'\"([^\"]+)\"', content)
            if m:
                parts = m.group(1).split(",")
                val = float(parts[1])
                if val > 0:
                    usd_cny = val
    except Exception as e:
        logger.debug("Sina fx_susdcny fetch failed: %s", e)

    # 2. Try Open Exchange Rates API for Latin currencies
    try:
        req2 = urllib.request.Request(
            "https://open.er-api.com/v6/latest/USD",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req2, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            r = data.get("rates") or {}
            if not usd_cny:
                usd_cny = float(r.get("CNY") or 7.20)
            rates["USD"] = round(usd_cny, 4)
            for c in ["MXN", "BRL", "COP", "CLP", "ARS", "UYU", "PEN"]:
                usd_cur = float(r.get(c) or 0.0)
                if usd_cur > 0:
                    rates[c] = round(usd_cny / usd_cur, 6)
    except Exception as e:
        logger.debug("Open ER API fetch failed: %s", e)
        if usd_cny:
            rates["USD"] = round(usd_cny, 4)

    return rates


def refresh_exchange_rates(force: bool = False) -> None:
    """Synchronously refresh exchange rates if cache is stale (> 3600s)."""
    global _last_fetch_ts, _memory_rates, _is_fetching
    with _lock:
        now = time.time()
        if not force and _memory_rates and (now - _last_fetch_ts) < 3600:
            return
        if _is_fetching:
            return
        _is_fetching = True

    try:
        live = fetch_live_rates_network()
        if live and live.get("USD"):
            with _lock:
                _memory_rates.update(live)
                _last_fetch_ts = time.time()
            _save_disk_cache(_memory_rates)
    except Exception as e:
        logger.debug("Refresh exchange rates error: %s", e)
    finally:
        with _lock:
            _is_fetching = False


def trigger_async_rate_refresh() -> None:
    """Spawn background thread to refresh rates without blocking UI."""
    t = threading.Thread(target=refresh_exchange_rates, args=(False,), daemon=True)
    t.start()


def get_cny_exchange_rate(currency: str) -> tuple[float, str]:
    """Get CNY exchange rate and its source ('实时', '自定义', or '参考')."""
    cur = (currency or "USD").strip().upper()
    if cur in ("CNY", "RMB"):
        return 1.0, "基准"

    # 1. User custom setting from settings.json
    try:
        s_path = get_data_dir() / "settings.json"
        if s_path.exists():
            cfg = json.loads(s_path.read_text(encoding="utf-8"))
            custom_rates = cfg.get("exchangeRates") or {}
            if cur in custom_rates and float(custom_rates[cur]) > 0:
                return float(custom_rates[cur]), "自定义"
            if cur == "USD" and cfg.get("usdToCnyRate") and float(cfg["usdToCnyRate"]) > 0:
                return float(cfg["usdToCnyRate"]), "自定义"
    except Exception:
        pass

    # 2. Check memory cache
    with _lock:
        if cur in _memory_rates and _memory_rates[cur] > 0:
            return _memory_rates[cur], "实时"

    # 3. Check disk cache
    disk_rates, ts = _load_disk_cache()
    if disk_rates and cur in disk_rates:
        with _lock:
            _memory_rates.update(disk_rates)
            _last_fetch_ts = ts
        trigger_async_rate_refresh()
        return disk_rates[cur], "实时"

    # 4. Trigger async refresh and return default fallback
    trigger_async_rate_refresh()
    default_val = DEFAULT_EXCHANGE_RATES.get(cur, 7.20 if cur == "USD" else 1.0)
    return default_val, "参考"
