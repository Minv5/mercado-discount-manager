# -*- coding: utf-8 -*-
"""Local Zhiying order data synchronizer and bypass receiver."""

from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

from orders.shipping_audits import save_shipping_audit

logger = logging.getLogger(__name__)

DEFAULT_SYNC_PORT = 28759


def format_audited_dimensions(
    length: float | int | str | None,
    width: float | int | str | None,
    height: float | int | str | None,
    weight: float | int | str | None,
) -> str:
    """Format dimensions and weight into standard readable string: '22 × 17 × 10 cm · 259 g'."""
    def _clean(val: Any) -> str:
        if val is None:
            return ""
        s = str(val).strip()
        m = re.search(r"[\d.]+", s)
        if m:
            n = float(m.group(0))
            return f"{n:g}"
        return s

    l_s = _clean(length)
    w_s = _clean(width)
    h_s = _clean(height)
    wt_s = _clean(weight)

    parts = []
    if l_s and w_s and h_s:
        parts.append(f"{l_s} × {w_s} × {h_s} cm")
    elif l_s or w_s or h_s:
        parts.append(f"{l_s or '-'} × {w_s or '-'} × {h_s or '-'} cm")

    if wt_s:
        try:
            wt_num = float(wt_s)
            if wt_num < 10.0 and "." in wt_s:  # Likely kg, e.g. 0.259 kg -> 259 g
                parts.append(f"{int(round(wt_num * 1000))} g")
            else:
                parts.append(f"{wt_num:g} g")
        except ValueError:
            parts.append(f"{wt_s} g")

    return " · ".join(parts)


def parse_and_save_zhiying_packet(data: dict[str, Any] | list[dict[str, Any]]) -> int:
    """Parse Zhiying order dataset and persist secondary weigh-in data."""
    items = data if isinstance(data, list) else [data]
    count = 0

    for item in items:
        if not isinstance(item, dict):
            continue

        oid = str(item.get("order_id") or item.get("id") or item.get("OrderNo") or item.get("OrderId") or "").strip()
        sid = str(item.get("shipment_id") or item.get("shipmentId") or item.get("ShipmentId") or "").strip()

        # Extract dimensions & weight from multiple common field names
        raw_dims = (
            item.get("actual_dimensions")
            or item.get("official_weight")
            or item.get("official_dimensions")
            or item.get("OfficialWeight")
            or item.get("AuditedDimensions")
            or ""
        )
        l_val = item.get("length") or item.get("Length") or item.get("len")
        w_val = item.get("width") or item.get("Width")
        h_val = item.get("height") or item.get("Height")
        wt_val = item.get("weight") or item.get("Weight") or item.get("actual_weight")

        formatted_dims = ""
        if l_val or w_val or h_val or wt_val:
            formatted_dims = format_audited_dimensions(l_val, w_val, h_val, wt_val)
        elif raw_dims:
            # e.g. "259克 22x17x10厘米" or "22.0x17.0x10.0,259.0"
            s = str(raw_dims).strip()
            # Try matching "259克 22x17x10厘米"
            m1 = re.search(r"(\d+(?:\.\d+)?)\s*克.*?(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*厘米", s)
            if m1:
                wt, l, w, h = m1.groups()
                formatted_dims = format_audited_dimensions(l, w, h, wt)
            else:
                m2 = re.search(r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", s)
                if m2:
                    l, w, h, wt = m2.groups()
                    formatted_dims = format_audited_dimensions(l, w, h, wt)
                else:
                    formatted_dims = s

        if (oid or sid) and formatted_dims:
            wt_float = 0.0
            if wt_val:
                try:
                    wt_float = float(wt_val)
                except (ValueError, TypeError):
                    pass
            save_shipping_audit(
                order_id=oid,
                shipment_id=sid,
                actual_dimensions=formatted_dims,
                actual_weight=wt_float,
                source="zhiying_sync",
                raw_json=json.dumps(item, ensure_ascii=False),
            )
            count += 1

    return count


class _ZhiyingSyncHandler(BaseHTTPRequestHandler):
    on_packet_received: Callable[[dict[str, Any]], None] | None = None

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet logging

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            payload = json.loads(post_body.decode("utf-8", errors="ignore"))
            count = parse_and_save_zhiying_packet(payload)
            if _ZhiyingSyncHandler.on_packet_received and payload:
                try:
                    _ZhiyingSyncHandler.on_packet_received(payload)
                except Exception:
                    pass

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "saved_count": count}).encode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class _ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class ZhiyingSyncReceiver:
    """Lightweight local loopback receiver to automatically capture Zhiying secondary weigh-in packets."""

    def __init__(self, port: int = DEFAULT_SYNC_PORT, on_packet: Callable[[dict[str, Any]], None] | None = None):
        self.port = port
        self.on_packet = on_packet
        self._server: _ReusableHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._is_running = False

    def start(self) -> None:
        if self._is_running:
            return
        _ZhiyingSyncHandler.on_packet_received = self.on_packet
        try:
            self._server = _ReusableHTTPServer(("127.0.0.1", self.port), _ZhiyingSyncHandler)
            self._is_running = True
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            logger.info("Zhiying sync receiver started on http://127.0.0.1:%d", self.port)
        except Exception as e:
            logger.warning("Could not start Zhiying sync receiver on port %d: %s", self.port, e)

    def stop(self) -> None:
        if not self._is_running or not self._server:
            return
        self._is_running = False
        srv = self._server
        self._server = None
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None
