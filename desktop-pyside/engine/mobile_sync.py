# -*- coding: utf-8 -*-
"""Automatic silent synchronizer pushing desktop state to mobile gateway on soft router."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request

from engine.crypto import get_data_dir

logger = logging.getLogger(__name__)

ACCESS_TOKEN_FILENAME = "mobile_gateway_token.txt"


def read_gateway_token() -> str:
    """Shared access token the gateway expects; empty when the mobile link was never set up."""
    try:
        return (get_data_dir() / ACCESS_TOKEN_FILENAME).read_text(encoding="utf-8").strip()
    except Exception:
        return ""

PRIMARY_GATEWAY_SYNC_URL = "http://192.168.1.2:28760/api/sync"
FALLBACK_GATEWAY_SYNC_URL = "https://minv5.trade/api/sync"


class MobileGatewaySynchronizer:
    """Thread-safe debounced synchronizer pushing snapshots to the soft router gateway."""

    def __init__(
        self,
        primary_url: str = PRIMARY_GATEWAY_SYNC_URL,
        fallback_url: str = FALLBACK_GATEWAY_SYNC_URL,
        debounce_seconds: float = 0.5,
        token_provider: Callable[[], str] = read_gateway_token,
    ) -> None:
        self._token_provider = token_provider
        self.primary_url = primary_url
        self.fallback_url = fallback_url
        self.debounce_seconds = debounce_seconds

        self._lock = threading.Lock()
        self._pending_sessions: list[dict[str, Any]] | None = None
        self._pending_orders: list[dict[str, Any]] | None = None
        self._timer: threading.Timer | None = None

    def trigger(
        self,
        sessions: list[dict[str, Any]] | None = None,
        orders: list[dict[str, Any]] | None = None,
        immediate: bool = False,
    ) -> None:
        """Queue or immediately dispatch updated data to the mobile gateway."""
        with self._lock:
            if sessions is not None:
                self._pending_sessions = sessions
            if orders is not None:
                self._pending_orders = orders

            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

            delay = 0.05 if immediate else self.debounce_seconds
            self._timer = threading.Timer(delay, self._dispatch)
            self._timer.daemon = True
            self._timer.start()

    def _load_snapshot_from_disk_if_needed(self, filename: str) -> list[dict[str, Any]] | None:
        try:
            p = get_data_dir() / filename
            if p.exists():
                text = p.read_text(encoding="utf-8")
                data = json.loads(text)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.debug("Failed to read %s: %s", filename, e)
        return None

    def _dispatch(self) -> None:
        with self._lock:
            self._timer = None
            sessions = self._pending_sessions
            orders = self._pending_orders
            self._pending_sessions = None
            self._pending_orders = None

        if sessions is None and orders is None:
            return

        payload: dict[str, Any] = {}
        if sessions is not None:
            payload["sessions"] = sessions
        if orders is not None:
            payload["orders"] = orders

        self._post_sync(payload)

    def _post_sync(self, payload: dict[str, Any]) -> bool:
        """Try sending payload to primary (LAN) then fallback (WAN)."""
        token = self._token_provider()
        if not token:
            logger.debug("Mobile gateway sync skipped: no access token configured")
            return False
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception as err:
            logger.debug("Failed to serialize sync payload: %s", err)
            return False

        endpoints = [(self.primary_url, 3.0), (self.fallback_url, 6.0)]
        for url, timeout in endpoints:
            try:
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "User-Agent": "MDM-Desktop-Sync/1.0",
                        "Authorization": f"Bearer {token}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        logger.info("Mobile gateway sync successful via %s", url)
                        return True
            except Exception as e:
                logger.debug("Mobile gateway sync via %s failed: %s", url, e)

        logger.warning("Mobile gateway sync failed on both LAN and WAN endpoints.")
        return False


_global_synchronizer = MobileGatewaySynchronizer()
_sync_enabled = False


def set_mobile_sync_enabled(enabled: bool) -> None:
    """Order/session snapshots leave this machine only after the user opts in."""
    global _sync_enabled
    _sync_enabled = bool(enabled)


def trigger_mobile_sync(
    sessions: list[dict[str, Any]] | None = None,
    orders: list[dict[str, Any]] | None = None,
    immediate: bool = False,
) -> None:
    """Global helper to trigger mobile gateway sync without blocking."""
    if not _sync_enabled:
        return
    try:
        _global_synchronizer.trigger(sessions=sessions, orders=orders, immediate=immediate)
    except Exception as e:
        logger.debug("trigger_mobile_sync error: %s", e)


def full_sync_from_disk_async(immediate: bool = True) -> None:
    """Helper to read disk snapshots and push full state to mobile gateway asynchronously."""
    if not _sync_enabled:
        return

    def _do():
        sessions = _global_synchronizer._load_snapshot_from_disk_if_needed("sessions_snapshot.json")
        orders = _global_synchronizer._load_snapshot_from_disk_if_needed("orders_snapshot.json")
        if sessions is not None or orders is not None:
            trigger_mobile_sync(sessions=sessions, orders=orders, immediate=immediate)

    threading.Thread(target=_do, daemon=True, name="FullDiskSyncThread").start()
