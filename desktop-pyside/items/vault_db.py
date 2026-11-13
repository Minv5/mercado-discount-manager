from __future__ import annotations

import json
import math
import os
import random
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from items.vault_pricing import profit_for_net


def get_default_vault_db_path() -> str:
    """Returns platform-standard local database path for product vault."""
    if sys.platform == "win32":
        base_dir = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        app_dir = os.path.join(base_dir, "MercadoDiscountManagerStandalone")
    else:
        app_dir = os.path.expanduser("~/Library/Application Support/MercadoDiscountManagerStandalone")
    os.makedirs(app_dir, exist_ok=True)
    return os.path.join(app_dir, "product_vault.db")


class ProductVaultDB:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_default_vault_db_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Initializes tables and indexes for the local product vault."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS product_vault (
                    id TEXT PRIMARY KEY,
                    cbt_item_id TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    title_zh TEXT DEFAULT '',
                    title_es TEXT DEFAULT '',
                    title_pt TEXT DEFAULT '',
                    sku TEXT DEFAULT '',
                    store_name TEXT DEFAULT '',
                    account_id TEXT DEFAULT '',
                    site_id TEXT DEFAULT '',
                    category_id TEXT DEFAULT '',
                    category_name TEXT DEFAULT '',
                    group_id TEXT DEFAULT '-1',
                    group_name TEXT DEFAULT '未分组',
                    salesman TEXT DEFAULT '',
                    price REAL DEFAULT 0.0,
                    cost_price REAL DEFAULT 0.0,
                    profit REAL DEFAULT 0.0,
                    threshold_type TEXT DEFAULT '',
                    stock INTEGER DEFAULT 0,
                    weight REAL DEFAULT 0.0,
                    length REAL DEFAULT 0.0,
                    width REAL DEFAULT 0.0,
                    height REAL DEFAULT 0.0,
                    variants_count INTEGER DEFAULT 0,
                    images TEXT DEFAULT '[]',
                    attributes TEXT DEFAULT '{}',
                    variants TEXT DEFAULT '[]',
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'draft',
                    is_deleted INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS product_vault_groups (
                    id TEXT PRIMARY KEY,
                    name TEXT UNIQUE,
                    created_at TEXT DEFAULT ''
                )
                """
            )

            # Indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pv_site ON product_vault(site_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pv_group ON product_vault(group_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pv_del ON product_vault(is_deleted)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pv_price ON product_vault(price)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pv_created ON product_vault(created_at)")
            conn.commit()

        self._auto_seed_from_snapshot_if_empty()

    def _auto_seed_from_snapshot_if_empty(self):
        """Auto-populates vault from local items_snapshot.json if vault is currently empty."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT count(*) FROM product_vault")
                count = cursor.fetchone()[0]
                if count > 0:
                    return

            from items.models import get_items_snapshot_path
            p = get_items_snapshot_path()
            if not p.exists():
                return

            with open(p, "r", encoding="utf-8") as f:
                raw_items = json.load(f)

            if not raw_items or not isinstance(raw_items, list):
                return

            mapped = []
            for it in raw_items:
                if not isinstance(it, dict) or not it.get("id"):
                    continue
                price = float(it.get("price") or 0.0)
                thumb = it.get("thumbnail") or ""
                mapped.append({
                    "id": str(it.get("id") or ""),
                    "cbt_item_id": str(it.get("id") or ""),
                    "title": str(it.get("title") or ""),
                    "sku": str(it.get("sku") or ""),
                    "store_name": str(it.get("account_name") or ""),
                    "account_id": str(it.get("account_id") or ""),
                    "site_id": str(it.get("site_id") or ""),
                    "price": price,
                    "cost_price": 0.0,
                    "profit": 0.0,
                    "threshold_type": "高阈值" if price >= 15 else "低阈值",
                    "stock": int(it.get("available_quantity") or 0),
                    "weight": 0.0,
                    "length": 0.0,
                    "width": 0.0,
                    "height": 0.0,
                    "images": [thumb] if thumb else [],
                    "group_name": "未分组",
                    "salesman": "",
                    "status": "draft",
                })

            if mapped:
                self.import_from_json(mapped)
        except Exception:
            pass

    def import_from_json(self, items: List[Dict[str, Any]]) -> int:
        """Channel 1: Imports items collected via browser plugin or API packets."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for it in items:
                p_id = str(it.get("id") or f"PV_{uuid.uuid4().hex[:8]}")
                cursor.execute("SELECT cost_price FROM product_vault WHERE id = ?", (p_id,))
                existing = cursor.fetchone()
                if existing is not None:
                    # Re-import only touches what the incoming row actually carries
                    fields = self._provided_fields(it, float(existing[0] or 0.0))
                    if fields:
                        self._apply_update(cursor, p_id, fields, now)
                    count += 1
                    continue

                cbt_id = str(it.get("cbt_item_id") or "")
                title = str(it.get("title") or "")
                title_zh = str(it.get("title_zh") or title)
                title_es = str(it.get("title_es") or title)
                title_pt = str(it.get("title_pt") or title)
                sku = str(it.get("sku") or "")
                store = str(it.get("store_name") or "")
                acc = str(it.get("account_id") or "")
                site = str(it.get("site_id") or "")
                cat_id = str(it.get("category_id") or "")
                cat_name = str(it.get("category_name") or "")
                grp_id = str(it.get("group_id") or "-1")
                grp_name = str(it.get("group_name") or "未分组")
                salesman = str(it.get("salesman") or "")

                price = float(it.get("price") or 0.0)
                cost = float(it.get("cost_price") or 0.0)
                profit = float(it.get("profit") if it.get("profit") is not None else round(price - cost, 2))
                if it.get("net_proceeds") is not None:
                    net = float(it["net_proceeds"])
                    if not it.get("price"):
                        price = net
                    profit = profit_for_net(net, cost)
                threshold_type = str(it.get("threshold_type") or ("高阈值" if price >= 15 else "低阈值"))

                stock = int(it.get("stock") or 0)
                weight = float(it.get("weight") or 0.0)
                length = float(it.get("length") or 0.0)
                width = float(it.get("width") or 0.0)
                height = float(it.get("height") or 0.0)

                variants = it.get("variants") or []
                var_count = int(it.get("variants_count") or len(variants))
                images = it.get("images") or []
                attributes = it.get("attributes") or {}
                desc = str(it.get("description") or "")
                status = str(it.get("status") or "draft")

                cursor.execute(
                    """
                    INSERT INTO product_vault (
                        id, cbt_item_id, title, title_zh, title_es, title_pt, sku,
                        store_name, account_id, site_id, category_id, category_name,
                        group_id, group_name, salesman, price, cost_price, profit,
                        threshold_type, stock, weight, length, width, height,
                        variants_count, images, attributes, variants, description,
                        status, is_deleted, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, 0, ?, ?
                    )
                    """,
                    (
                        p_id, cbt_id, title, title_zh, title_es, title_pt, sku,
                        store, acc, site, cat_id, cat_name,
                        grp_id, grp_name, salesman, price, cost, profit,
                        threshold_type, stock, weight, length, width, height,
                        var_count, json.dumps(images, ensure_ascii=False),
                        json.dumps(attributes, ensure_ascii=False),
                        json.dumps(variants, ensure_ascii=False),
                        desc, status, now, now
                    )
                )
                count += 1
            conn.commit()
        return count

    @staticmethod
    def _to_float(val: Any, default: float = 0.0) -> float:
        if val is None or val == "":
            return default
        s = str(val).strip().lstrip("$¥￥").replace(",", "")
        if not s:
            return default
        try:
            return float(s)
        except Exception:
            return default

    @staticmethod
    def _to_int(val: Any, default: int = 0) -> int:
        if val is None or val == "":
            return default
        s = str(val).strip().replace(",", "")
        if not s:
            return default
        try:
            return int(float(s))
        except Exception:
            return default

    def import_from_excel_rows(self, rows: List[Dict[str, Any]]) -> int:
        """Channel 2: Imports items parsed from Excel / CSV files."""
        normalized = []
        for r in rows:
            p_id = r.get("id") or r.get("商品ID") or r.get("产品ID") or f"EX_{uuid.uuid4().hex[:8]}"
            title = str(r.get("title") or r.get("标题") or r.get("商品标题") or "").strip()
            title_es = str(r.get("title_es") or r.get("标题(西班牙语)") or r.get("西班牙语标题") or "").strip()
            title_pt = str(r.get("title_pt") or r.get("标题(葡萄牙语)") or r.get("葡萄牙语标题") or "").strip()
            title_zh = str(r.get("title_zh") or r.get("标题(中文)") or r.get("中文标题") or "").strip()
            if not title:
                title = title_es or title_pt or title_zh

            desc = str(
                r.get("description") or r.get("描述") or r.get("产品描述")
                or r.get("描述(西班牙语)") or ""
            ).strip()
            sku = str(r.get("sku") or r.get("SKU") or "").strip()
            store = str(r.get("store_name") or r.get("归属店铺") or r.get("店铺") or "").strip()
            site = str(r.get("site_id") or r.get("站点") or "").strip()
            account_id = str(r.get("account_id") or "").strip()
            cat_id = str(r.get("category_id") or r.get("分类ID") or r.get("类目ID") or "").strip()
            cat_name = str(r.get("category_name") or r.get("分类名称") or r.get("类目名称") or "").strip()

            def given(*keys: str) -> Any:
                for k in keys:
                    if r.get(k) not in (None, ""):
                        return r.get(k)
                return None

            # Only columns the row actually fills are carried, so re-importing an
            # exported sheet never blanks data the sheet does not contain.
            item_dict: Dict[str, Any] = {"id": p_id}
            for col, val in (
                ("title", title), ("title_es", title_es), ("title_pt", title_pt), ("title_zh", title_zh),
                ("description", desc), ("sku", sku), ("store_name", store), ("account_id", account_id),
                ("site_id", site), ("category_id", cat_id), ("category_name", cat_name),
            ):
                if val:
                    item_dict[col] = val

            raw_group = given("group_name", "分组", "产品分组")
            if raw_group is not None:
                item_dict["group_name"] = str(raw_group).strip() or "未分组"

            raw_price = given("price", "售价")
            if raw_price is not None:
                item_dict["price"] = self._to_float(raw_price)
            raw_cost = given("cost_price", "成本")
            if raw_cost is not None:
                item_dict["cost_price"] = self._to_float(raw_cost)
            raw_net = given("net_proceeds", "净收益", "净收益(USD)", "profit")
            if raw_net is not None:
                item_dict["net_proceeds"] = self._to_float(raw_net)

            raw_stock = given("stock", "库存")
            if raw_stock is not None:
                item_dict["stock"] = self._to_int(raw_stock)
            for col, keys in (
                ("weight", ("weight", "重量", "重量(g)")),
                ("length", ("length", "长", "长(cm)")),
                ("width", ("width", "宽", "宽(cm)")),
                ("height", ("height", "高", "高(cm)")),
            ):
                raw_dim = given(*keys)
                if raw_dim is not None:
                    item_dict[col] = self._to_float(raw_dim)

            dim_str = str(given("dimensions", "尺寸", "尺寸(cm)") or "").strip()
            if dim_str and not any(item_dict.get(c) for c in ("length", "width", "height")):
                parts = [p.strip() for p in dim_str.replace("×", "*").replace("x", "*").replace("X", "*").split("*")]
                if len(parts) == 3:
                    item_dict["length"], item_dict["width"], item_dict["height"] = (self._to_float(p) for p in parts)

            img = given("images", "图片", "图片链接") or []
            if isinstance(img, str):
                img = [x.strip() for x in img.replace("|", ";").replace("\n", ";").split(";") if x.strip()]
            if img:
                item_dict["images"] = img

            normalized.append(item_dict)
        return self.import_from_json(normalized)

    def clone_product(self, product_id: str, target_group_id: Optional[str] = None, target_group_name: Optional[str] = None) -> Optional[str]:
        """Channel 3: Clones an existing product into an independent draft."""
        prod = self.get_product(product_id)
        if not prod:
            return None
        new_id = f"PV_{uuid.uuid4().hex[:8]}"
        prod["id"] = new_id
        if target_group_id is not None:
            prod["group_id"] = target_group_id
            prod["group_name"] = target_group_name or "未分组"
        self.import_from_json([prod])
        return new_id

    def get_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM product_vault WHERE id = ?", (product_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def update_product(self, product_id: str, fields: Dict[str, Any]) -> bool:
        """Updates specific fields of an individual product."""
        if not product_id or not fields:
            return False
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            updated = self._apply_update(cursor, product_id, fields, now)
            conn.commit()
            return updated

    _UPDATABLE_FIELDS = (
        "cbt_item_id", "title", "title_zh", "title_es", "title_pt", "sku", "store_name",
        "account_id", "site_id", "category_id", "category_name", "group_id",
        "group_name", "salesman", "price", "cost_price", "profit",
        "threshold_type", "stock", "weight", "length", "width", "height",
        "variants_count", "images", "attributes", "variants", "description", "status",
    )
    _JSON_FIELDS = ("images", "attributes", "variants")

    def _apply_update(self, cursor: sqlite3.Cursor, product_id: str, fields: Dict[str, Any], now: str) -> bool:
        set_clauses = []
        params: List[Any] = []
        for k, v in fields.items():
            if k not in self._UPDATABLE_FIELDS:
                continue
            set_clauses.append(f"{k} = ?")
            if k in self._JSON_FIELDS and not isinstance(v, str):
                params.append(json.dumps(v, ensure_ascii=False))
            else:
                params.append(v)
        if not set_clauses:
            return False
        set_clauses.append("updated_at = ?")
        params.extend([now, product_id])
        cursor.execute(f"UPDATE product_vault SET {', '.join(set_clauses)} WHERE id = ?", params)
        return cursor.rowcount > 0

    def _provided_fields(self, item: Dict[str, Any], existing_cost: float) -> Dict[str, Any]:
        """Columns an incoming row explicitly carries, for updating a product that already exists."""
        fields = {k: item[k] for k in self._UPDATABLE_FIELDS if item.get(k) is not None}
        if "variants" in fields and "variants_count" not in fields:
            fields["variants_count"] = len(fields["variants"] or [])
        if item.get("net_proceeds") is not None:
            cost = fields.get("cost_price", existing_cost)
            fields["profit"] = profit_for_net(item["net_proceeds"], cost)
        return fields

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for json_key in ("images", "attributes", "variants"):
            if json_key in d and isinstance(d[json_key], str):
                try:
                    d[json_key] = json.loads(d[json_key])
                except Exception:
                    d[json_key] = [] if json_key != "attributes" else {}
        return d

    def query_products(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: Optional[str] = None,
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """Executes multi-criteria dynamic filtering and pagination."""
        filters = filters or {}
        where_clauses = ["is_deleted = 0"]
        params: List[Any] = []

        if filters.get("site_id"):
            where_clauses.append("site_id = ?")
            params.append(filters["site_id"])

        if filters.get("store_name"):
            where_clauses.append("store_name = ?")
            params.append(filters["store_name"])

        if filters.get("account_id"):
            where_clauses.append("account_id = ?")
            params.append(filters["account_id"])

        if filters.get("status"):
            st_val = str(filters["status"]).strip()
            if st_val in ("active", "ready"):
                where_clauses.append("status IN ('active', 'ready')")
            elif st_val in ("pending", "paused"):
                where_clauses.append("status IN ('pending', 'paused')")
            else:
                where_clauses.append("status = ?")
                params.append(st_val)

        if filters.get("group_id"):
            gid = str(filters["group_id"])
            if gid == "-1":
                where_clauses.append("(group_name = '未分组' OR group_name = '' OR group_name IS NULL)")
            else:
                where_clauses.append("(group_id = ? OR group_name = ?)")
                params.extend([gid, gid])

        if filters.get("keyword"):
            kw = f"%{filters['keyword']}%"
            where_clauses.append("(title LIKE ? OR title_zh LIKE ? OR title_es LIKE ? OR title_pt LIKE ?)")
            params.extend([kw, kw, kw, kw])

        pid_query = filters.get("product_id") or filters.get("id")
        if pid_query:
            where_clauses.append("id LIKE ?")
            params.append(f"%{pid_query}%")

        if filters.get("cbt_item_id"):
            where_clauses.append("cbt_item_id LIKE ?")
            params.append(f"%{filters['cbt_item_id']}%")

        if filters.get("category_id"):
            where_clauses.append("category_id = ?")
            params.append(filters["category_id"])

        min_net_val = filters.get("min_net") if filters.get("min_net") is not None else filters.get("min_price")
        if min_net_val is not None:
            where_clauses.append("price >= ?")
            params.append(float(min_net_val))

        max_net_val = filters.get("max_net") if filters.get("max_net") is not None else filters.get("max_price")
        if max_net_val is not None:
            where_clauses.append("price <= ?")
            params.append(float(max_net_val))

        if filters.get("min_cost") is not None:
            where_clauses.append("cost_price >= ?")
            params.append(float(filters["min_cost"]))

        if filters.get("max_cost") is not None:
            where_clauses.append("cost_price <= ?")
            params.append(float(filters["max_cost"]))

        if filters.get("min_profit") is not None:
            where_clauses.append("profit >= ?")
            params.append(float(filters["min_profit"]))

        if filters.get("max_profit") is not None:
            where_clauses.append("profit <= ?")
            params.append(float(filters["max_profit"]))

        if filters.get("min_weight") is not None:
            where_clauses.append("weight >= ?")
            params.append(float(filters["min_weight"]))

        if filters.get("max_weight") is not None:
            where_clauses.append("weight <= ?")
            params.append(float(filters["max_weight"]))

        # Verify status filters from AI capsules
        v_status = filters.get("verify_status")
        if v_status == "pending_verify":
            where_clauses.append("(cost_price = 0 OR cost_price IS NULL)")
        elif v_status == "pending_fill":
            where_clauses.append("cost_price > 0 AND (profit = 0 OR profit IS NULL)")
        elif v_status == "filled":
            where_clauses.append("cost_price > 0 AND profit > 0")
        elif v_status == "size_anomaly":
            where_clauses.append("(length = 0 OR width = 0 OR height = 0)")
        elif v_status in ("questionable", "infringement_skip", "failed"):
            where_clauses.append("1=0")

        if filters.get("has_variants") in ("yes", True, 1):
            where_clauses.append("variants_count > 0")
        elif filters.get("has_variants") in ("no", False, 0):
            where_clauses.append("variants_count = 0")

        if filters.get("has_size") in ("yes", True, 1):
            where_clauses.append("(length > 0 AND width > 0 AND height > 0)")
        elif filters.get("has_size") in ("no", False, 0):
            where_clauses.append("NOT (length > 0 AND width > 0 AND height > 0)")

        for dim_col in ("length", "width", "height"):
            dim_val = filters.get(dim_col)
            if dim_val is not None and str(dim_val).strip() not in ("", "-", "*"):
                try:
                    where_clauses.append(f"ABS({dim_col} - ?) < 0.01")
                    params.append(float(dim_val))
                except (TypeError, ValueError):
                    pass

        where_sql = " AND ".join(where_clauses)

        # Sorting
        allowed_sorts = {
            "created_at": "created_at",
            "updated_at": "updated_at",
            "price": "price",
            "net": "price",
            "net_proceeds": "price",
            "cost_price": "cost_price",
            "profit": "profit",
            "stock": "stock",
            "weight": "weight"
        }
        order_col = allowed_sorts.get(sort_by, "created_at")
        order_dir = "ASC" if sort_order.lower() == "asc" else "DESC"

        offset = max(0, (page - 1) * page_size)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Count
            count_sql = f"SELECT COUNT(*) FROM product_vault WHERE {where_sql}"
            cursor.execute(count_sql, params)
            total = cursor.fetchone()[0]

            # Fetch page
            fetch_sql = f"""
                SELECT * FROM product_vault
                WHERE {where_sql}
                ORDER BY {order_col} {order_dir}
                LIMIT ? OFFSET ?
            """
            cursor.execute(fetch_sql, params + [page_size, offset])
            items = [self._row_to_dict(row) for row in cursor.fetchall()]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    def batch_update_price(
        self,
        product_ids: List[str],
        price_type: str = "price",
        adjustment_type: str = "tiny",
        amount_or_rate: float = 0.0,
        base_type: Optional[str] = None
    ) -> int:
        """Batch updates prices (fine-tune, percent, or fixed)."""
        if not product_ids:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for pid in product_ids:
                prod = self.get_product(pid)
                if not prod:
                    continue
                current_price = prod.get(price_type) or 0.0
                base_val = prod.get(base_type or price_type) or current_price

                if adjustment_type == "tiny":
                    new_val = max(0.0, round(current_price + amount_or_rate, 2))
                elif adjustment_type == "percent":
                    new_val = max(0.0, round(base_val * (amount_or_rate / 100.0), 2))
                elif adjustment_type == "fixed":
                    new_val = max(0.0, round(amount_or_rate, 2))
                else:
                    new_val = current_price

                if price_type == "profit":
                    cursor.execute(
                        "UPDATE product_vault SET profit = ?, updated_at = ? WHERE id = ?",
                        (new_val, now, pid)
                    )
                elif price_type in ("price", "cost_price"):
                    p = new_val if price_type == "price" else prod["price"]
                    c = new_val if price_type == "cost_price" else prod["cost_price"]
                    cursor.execute(
                        f"UPDATE product_vault SET {price_type} = ?, profit = ?, updated_at = ? WHERE id = ?",
                        (new_val, round(p - c, 2), now, pid)
                    )
                else:
                    continue
                count += 1
            conn.commit()
        return count

    def batch_update_stock(self, product_ids: List[str], stock: int) -> int:
        if not product_ids:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE product_vault SET stock = ?, updated_at = ? WHERE id IN ({','.join(['?']*len(product_ids))})",
                [max(0, stock), now] + product_ids
            )
            conn.commit()
            return cursor.rowcount

    def batch_update_weight(
        self,
        product_ids: List[str],
        mode: str = "fixed",
        value: Optional[float] = None,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None
    ) -> int:
        if not product_ids:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for pid in product_ids:
                if mode == "fixed":
                    new_w = max(0.0, float(value or 0.0))
                elif mode == "tiny":
                    prod = self.get_product(pid)
                    old_w = (prod.get("weight") or 0.0) if prod else 0.0
                    new_w = max(0.0, old_w + float(value or 0.0))
                elif mode == "random":
                    low = int(min_val or 0)
                    high = int(max_val or low)
                    new_w = float(random.randint(min(low, high), max(low, high)))
                else:
                    continue
                cursor.execute(
                    "UPDATE product_vault SET weight = ?, updated_at = ? WHERE id = ?",
                    (new_w, now, pid)
                )
                count += 1
            conn.commit()
        return count

    def batch_update_size(self, product_ids: List[str], length: float, width: float, height: float) -> int:
        if not product_ids:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE product_vault
                SET length = ?, width = ?, height = ?, updated_at = ?
                WHERE id IN ({','.join(['?']*len(product_ids))})
                """,
                [max(0.0, length), max(0.0, width), max(0.0, height), now] + product_ids
            )
            conn.commit()
            return cursor.rowcount

    def batch_estimate_volume(self, product_ids: List[str], force_override: bool = False) -> int:
        """Estimates dimensions (L, W, H) from item weight using geometric ratio (5:3:2)."""
        if not product_ids:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for pid in product_ids:
                prod = self.get_product(pid)
                if not prod:
                    continue
                has_sz = (prod.get("length") or 0) > 0 and (prod.get("width") or 0) > 0 and (prod.get("height") or 0) > 0
                if has_sz and not force_override:
                    continue

                w = max(100.0, float(prod.get("weight") or 500.0))
                # Volumetric estimation assuming density 0.2 g/cm^3
                vol = w * 5.0
                # Proportions L:W:H = 5:3:2 -> vol = 30 * x^3
                x = math.pow(vol / 30.0, 1.0 / 3.0)
                l = round(x * 5.0, 1)
                wd = round(x * 3.0, 1)
                h = round(x * 2.0, 1)

                cursor.execute(
                    "UPDATE product_vault SET length = ?, width = ?, height = ?, updated_at = ? WHERE id = ?",
                    (l, wd, h, now, pid)
                )
                count += 1
            conn.commit()
        return count

    def batch_replace_keyword(
        self,
        product_ids: List[str],
        keyword: str,
        replace_text: str = "",
        is_ignore_case: bool = True,
        in_title: bool = True,
        in_desc: bool = True
    ) -> int:
        if not product_ids or not keyword:
            return 0
        flags = re.IGNORECASE if is_ignore_case else 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for pid in product_ids:
                prod = self.get_product(pid)
                if not prod:
                    continue
                t = prod.get("title") or ""
                d = prod.get("description") or ""

                if in_title:
                    t = re.sub(re.escape(keyword), replace_text, t, flags=flags)
                if in_desc:
                    d = re.sub(re.escape(keyword), replace_text, d, flags=flags)

                cursor.execute(
                    "UPDATE product_vault SET title = ?, description = ?, updated_at = ? WHERE id = ?",
                    (t, d, now, pid)
                )
                count += 1
            conn.commit()
        return count

    def batch_update_category(
        self,
        product_ids: List[str],
        site_id: str,
        category_id: str,
        category_name: str,
        attributes: Optional[Dict[str, Any]] = None
    ) -> int:
        if not product_ids:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for pid in product_ids:
                prod = self.get_product(pid)
                if not prod:
                    continue
                attrs = prod.get("attributes") if isinstance(prod.get("attributes"), dict) else {}
                attrs.update(attributes or {})
                site_cats = attrs.get("_site_categories") if isinstance(attrs.get("_site_categories"), dict) else {}
                if site_id:
                    site_cats[str(site_id)] = [category_id, category_name]
                    attrs["_site_categories"] = site_cats
                cursor.execute(
                    """
                    UPDATE product_vault
                    SET site_id = ?, category_id = ?, category_name = ?, attributes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (site_id, category_id, category_name, json.dumps(attrs, ensure_ascii=False), now, pid),
                )
                count += 1
            conn.commit()
        return count

    def batch_update_status(self, product_ids: List[str], status: str) -> int:
        if not product_ids:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE product_vault SET status = ?, updated_at = ? "
                f"WHERE is_deleted = 0 AND id IN ({','.join(['?'] * len(product_ids))})",
                [status, now] + list(product_ids),
            )
            conn.commit()
            return cursor.rowcount

    def batch_update_attributes(self, product_ids: List[str], attributes_dict: Dict[str, Any]) -> int:
        if not product_ids or not attributes_dict:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for pid in product_ids:
                prod = self.get_product(pid)
                if not prod:
                    continue
                current_attrs = prod.get("attributes") or {}
                current_attrs.update(attributes_dict)
                cursor.execute(
                    "UPDATE product_vault SET attributes = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(current_attrs, ensure_ascii=False), now, pid)
                )
                count += 1
            conn.commit()
        return count

    def batch_generate_variants(
        self,
        product_ids: List[str],
        target_count: int,
        prefer_preset: bool = True,
        custom_values: Optional[List[str]] = None
    ) -> Dict[str, int]:
        """Generates variants up to target_count (adds only, never deletes)."""
        custom_values = custom_values or []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        success = 0
        skipped = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for pid in product_ids:
                prod = self.get_product(pid)
                if not prod:
                    skipped += 1
                    continue
                existing_vars = prod.get("variants") or []
                needed = target_count - len(existing_vars)
                if needed <= 0:
                    skipped += 1
                    continue

                new_vars = list(existing_vars)
                custom_idx = 0
                pad_idx = 1
                for i in range(needed):
                    if custom_idx < len(custom_values):
                        val_name = custom_values[custom_idx]
                        custom_idx += 1
                    else:
                        val_name = f"Default{pad_idx}"
                        pad_idx += 1
                    new_vars.append({
                        "id": f"VAR_{uuid.uuid4().hex[:6]}",
                        "name": val_name,
                        "price": prod["price"],
                        "stock": prod["stock"],
                        "sku": f"{prod['sku']}-{val_name}"
                    })

                cursor.execute(
                    """
                    UPDATE product_vault
                    SET variants = ?, variants_count = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (json.dumps(new_vars, ensure_ascii=False), len(new_vars), now, pid)
                )
                success += 1
            conn.commit()
        return {"success": success, "skipped": skipped}

    def batch_copy_to_group(self, product_ids: List[str], target_group_id: str, target_group_name: Optional[str] = None) -> int:
        count = 0
        for pid in product_ids:
            res = self.clone_product(pid, target_group_id=target_group_id, target_group_name=target_group_name)
            if res:
                count += 1
        return count

    def batch_delete(self, product_ids: List[str], soft_delete: bool = True, delete_images: bool = False) -> int:
        if not product_ids:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if soft_delete:
                cursor.execute(
                    f"UPDATE product_vault SET is_deleted = 1, updated_at = ? WHERE id IN ({','.join(['?']*len(product_ids))})",
                    [now] + product_ids
                )
            else:
                cursor.execute(
                    f"DELETE FROM product_vault WHERE id IN ({','.join(['?']*len(product_ids))})",
                    product_ids
                )
            conn.commit()
            return cursor.rowcount

    def delete_by_filter(self, filters: Dict[str, Any], soft_delete: bool = True) -> int:
        """Deletes all items matching criteria without needing multi-selection."""
        res = self.query_products(filters=filters, page=1, page_size=100000)
        pids = [p["id"] for p in res["items"]]
        if not pids:
            return 0
        return self.batch_delete(pids, soft_delete=soft_delete)

    def get_status_counts(self, page_product_ids: Optional[List[str]] = None) -> Dict[str, int]:
        """Calculates counters for the 8 AI Price Verifier status capsules."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM product_vault WHERE is_deleted = 0")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM product_vault WHERE is_deleted = 0 AND (cost_price = 0 OR cost_price IS NULL)")
            pending_verify = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM product_vault WHERE is_deleted = 0 AND cost_price > 0 AND (profit = 0 OR profit IS NULL)")
            pending_fill = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM product_vault WHERE is_deleted = 0 AND cost_price > 0 AND profit > 0")
            filled = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM product_vault WHERE is_deleted = 0 AND (length = 0 OR width = 0 OR height = 0)")
            size_anomaly = cursor.fetchone()[0]

        return {
            "this_page": len(page_product_ids) if page_product_ids is not None else 0,
            "pending_verify": pending_verify,
            "pending_fill": pending_fill,
            "filled": filled,
            "questionable": 0,
            "size_anomaly": size_anomaly,
            "infringement_skip": 0,
            "failed": 0,
            "total": total
        }

    def get_group_names(self) -> List[str]:
        """Returns distinct non-empty product group names."""
        names: List[str] = []
        seen = set()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT group_name FROM product_vault WHERE is_deleted = 0 AND group_name != '' ORDER BY group_name"
            )
            for row in cursor.fetchall():
                g = str(row[0] or "").strip()
                if g and g not in seen:
                    seen.add(g)
                    names.append(g)
        for default_name in ("未分组", "手搓组"):
            if default_name not in seen:
                seen.add(default_name)
                names.append(default_name)
        return names

    def get_categories(self) -> List[tuple[str, str]]:
        """Returns distinct non-empty (category_id, category_name) pairs."""
        results: List[tuple[str, str]] = []
        seen = set()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT category_id, category_name FROM product_vault "
                "WHERE is_deleted = 0 AND category_id != '' ORDER BY category_name, category_id"
            )
            for row in cursor.fetchall():
                cid = str(row[0] or "").strip()
                cname = str(row[1] or "").strip() or cid
                if cid and cid not in seen:
                    seen.add(cid)
                    results.append((cid, cname))
        return results

    def batch_reduce_variants(self, product_ids: List[str], target_count: int) -> int:
        """Trims variants list per product to target_count (0 clears all variants)."""
        if not product_ids:
            return 0
        limit = max(0, int(target_count))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(product_ids))
            cursor.execute(
                f"SELECT id, variants, variants_count, attributes FROM product_vault WHERE id IN ({placeholders}) AND is_deleted = 0",
                list(product_ids),
            )
            rows = cursor.fetchall()
            for row in rows:
                pid = row[0]
                try:
                    raw_vars = json.loads(row[1] or "[]")
                except Exception:
                    raw_vars = []
                if not isinstance(raw_vars, list):
                    raw_vars = []
                old_cnt = int(row[2] or len(raw_vars))
                if len(raw_vars) <= limit and old_cnt <= limit:
                    continue

                kept = [] if limit == 0 else raw_vars[:limit]
                try:
                    attrs = json.loads(row[3] or "{}")
                except Exception:
                    attrs = {}
                if not isinstance(attrs, dict):
                    attrs = {}
                if limit == 0 and "_variant_headers" in attrs:
                    attrs["_variant_headers"] = []
                logs = attrs.get("_logs")
                if not isinstance(logs, list):
                    logs = []
                logs.insert(
                    0,
                    {
                        "time": now,
                        "action": "裁剪变体",
                        "detail": f"原 {max(old_cnt, len(raw_vars))} 个变体 -> 保留 {len(kept)} 个",
                        "operator": "当前用户",
                    },
                )
                attrs["_logs"] = logs[:100]

                cursor.execute(
                    "UPDATE product_vault SET variants = ?, variants_count = ?, attributes = ?, updated_at = ? WHERE id = ?",
                    (
                        json.dumps(kept, ensure_ascii=False),
                        len(kept),
                        json.dumps(attrs, ensure_ascii=False),
                        now,
                        pid,
                    ),
                )
                updated += 1
            conn.commit()
        return updated

    def append_product_log(self, product_id: str, action: str, detail: str = "", operator: str = "当前用户") -> bool:
        """Appends a real timestamped audit log entry to attributes['_logs']."""
        if not product_id:
            return False
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT attributes FROM product_vault WHERE id = ?", (product_id,))
            row = cursor.fetchone()
            if not row:
                return False
            try:
                attrs = json.loads(row[0] or "{}")
            except Exception:
                attrs = {}
            if not isinstance(attrs, dict):
                attrs = {}
            logs = attrs.get("_logs")
            if not isinstance(logs, list):
                logs = []
            logs.insert(
                0,
                {
                    "time": now,
                    "action": str(action or ""),
                    "detail": str(detail or ""),
                    "operator": str(operator or "当前用户"),
                },
            )
            attrs["_logs"] = logs[:100]
            cursor.execute(
                "UPDATE product_vault SET attributes = ?, updated_at = ? WHERE id = ?",
                (json.dumps(attrs, ensure_ascii=False), now, product_id),
            )
            conn.commit()
        return True
