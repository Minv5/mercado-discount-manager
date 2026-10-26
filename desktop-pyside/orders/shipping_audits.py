# -*- coding: utf-8 -*-
"""Shipping audits and secondary dimensions/weight persistence module."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.crypto import get_data_dir

logger = logging.getLogger(__name__)


def _get_db_path(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return get_data_dir() / "discount-manager.sqlite"


def init_shipping_audits_db(db_path: Path | None = None) -> None:
    """Ensure order_shipping_audits table exists in SQLite database."""
    target = _get_db_path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(target) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS order_shipping_audits (
                    order_id TEXT PRIMARY KEY,
                    shipment_id TEXT,
                    actual_dimensions TEXT NOT NULL,
                    actual_weight REAL DEFAULT 0.0,
                    source TEXT DEFAULT 'zhiying',
                    raw_json TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_shipping_audits_shipment 
                ON order_shipping_audits(shipment_id)
            """)
            conn.commit()
    except Exception as e:
        logger.warning("Failed to initialize order_shipping_audits table: %s", e)


def save_shipping_audit(
    order_id: str,
    shipment_id: str = "",
    actual_dimensions: str = "",
    actual_weight: float = 0.0,
    source: str = "zhiying",
    raw_json: str = "",
    db_path: Path | None = None,
) -> None:
    """Save or update secondary dimensions/weight audit for an order or shipment."""
    oid = str(order_id or "").strip()
    sid = str(shipment_id or "").strip()
    dims = str(actual_dimensions or "").strip()
    if not oid and not sid:
        return
    if not dims:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    target = _get_db_path(db_path)
    init_shipping_audits_db(target)

    try:
        with sqlite3.connect(target) as conn:
            conn.execute("""
                INSERT INTO order_shipping_audits (
                    order_id, shipment_id, actual_dimensions, actual_weight, source, raw_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    shipment_id = COALESCE(NULLIF(excluded.shipment_id, ''), order_shipping_audits.shipment_id),
                    actual_dimensions = excluded.actual_dimensions,
                    actual_weight = CASE WHEN excluded.actual_weight > 0 THEN excluded.actual_weight ELSE order_shipping_audits.actual_weight END,
                    source = excluded.source,
                    raw_json = CASE WHEN excluded.raw_json != '' THEN excluded.raw_json ELSE order_shipping_audits.raw_json END,
                    updated_at = excluded.updated_at
            """, (oid, sid, dims, actual_weight, source, raw_json, now_iso, now_iso))
            conn.commit()
    except Exception as e:
        logger.warning("Failed to save shipping audit for order %s: %s", oid, e)


def get_shipping_audit(
    order_id: str,
    shipment_id: str = "",
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Retrieve audit record by order_id or shipment_id."""
    oid = str(order_id or "").strip()
    sid = str(shipment_id or "").strip()
    target = _get_db_path(db_path)
    if not target.exists():
        return None

    try:
        with sqlite3.connect(target) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if oid:
                cursor.execute(
                    "SELECT * FROM order_shipping_audits WHERE order_id = ? LIMIT 1",
                    (oid,),
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
            if sid:
                cursor.execute(
                    "SELECT * FROM order_shipping_audits WHERE shipment_id = ? LIMIT 1",
                    (sid,),
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
    except Exception as e:
        logger.debug("Failed querying shipping audit: %s", e)
    return None


def get_all_shipping_audits(db_path: Path | None = None) -> dict[str, str]:
    """Return in-memory map of {order_id: actual_dimensions, shipment_id: actual_dimensions}."""
    target = _get_db_path(db_path)
    if not target.exists():
        return {}

    mapping: dict[str, str] = {}
    try:
        with sqlite3.connect(target) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT order_id, shipment_id, actual_dimensions FROM order_shipping_audits")
            for oid, sid, dims in cursor.fetchall():
                d = str(dims or "").strip()
                if d:
                    if oid:
                        mapping[str(oid).strip()] = d
                    if sid:
                        mapping[str(sid).strip()] = d
    except Exception as e:
        logger.debug("Failed reading all shipping audits: %s", e)
    return mapping
