from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .auth import AuthManager
from .client import MercadoClient
from .crypto import get_data_dir
from .pricing import calculate_deal_price, extract_item_net_proceeds


class WebhookWorker:
    DEFAULT_CLAIM_URL = ""
    DEFAULT_ACK_URL = ""

    def __init__(
        self,
        auth_manager: AuthManager | None = None,
        client: MercadoClient | None = None,
        claim_url: str = DEFAULT_CLAIM_URL,
        ack_url: str = DEFAULT_ACK_URL,
        secret: str | None = None,
        log_callback: Callable[[str], None] | None = None,
    ):
        self.auth = auth_manager or AuthManager()
        self.client = client or MercadoClient(self.auth)
        self.claim_url = claim_url
        self.ack_url = ack_url
        self._secret = secret
        self.log_callback = log_callback or (lambda msg: None)

        self._running = False
        self._thread: threading.Thread | None = None
        self.discount_percent = 30.0
        self.processed_count = 0
        self._price_cache: dict[str, float] = {}
        self._net_cache: dict[str, float] = {}
        self._shipping_cache: dict[str, float] = {}
        self._dim_cache: dict[str, tuple[str | None, str | None]] = {}
        self._item_locks: dict[str, threading.Lock] = {}
        self._item_locks_guard = threading.Lock()
        self._logs: list[str] = []
        self._logs_lock = threading.Lock()

    def _get_item_lock(self, item_id: str) -> threading.Lock:
        with self._item_locks_guard:
            lock = self._item_locks.get(item_id)
            if lock is None:
                lock = threading.Lock()
                self._item_locks[item_id] = lock
            return lock

    def log(self, msg: str, tag: str | None = None) -> None:
        formatted = f"[{tag}] {msg}" if tag else msg
        with self._logs_lock:
            self._logs.append(formatted)
            if len(self._logs) > 200:
                self._logs = self._logs[-200:]
        try:
            self.log_callback(formatted)
        except Exception:
            pass

    def pop_logs(self) -> list[str]:
        with self._logs_lock:
            if not self._logs:
                return []
            logs = list(self._logs)
            self._logs.clear()
            return logs

    def start(self, discount_percent: float = 30.0) -> None:
        """Start the background polling thread."""
        if self._running:
            self.discount_percent = discount_percent
            return
        self.discount_percent = discount_percent
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="WebhookWorkerThread")
        self._thread.start()
        self.log(f"后台变动监听已启动，当前基准净收益折扣: {self.discount_percent:.1f}%")

    def stop(self) -> None:
        """Stop the background polling thread."""
        if not self._running:
            return
        self._running = False
        self.log("后台变动监听已停止。")

    def is_running(self) -> bool:
        return self._running

    def _get_secret(self) -> str:
        if self._secret:
            return self._secret
        secret_path = get_data_dir() / "consumer-api.secret"
        if secret_path.exists():
            try:
                self._secret = secret_path.read_text(encoding="utf-8").strip()
                return self._secret
            except Exception:
                pass
        return ""

    def _run_loop(self) -> None:
        while self._running:
            had_events = False
            try:
                had_events = self._poll_and_process_batch()
            except Exception as err:
                time.sleep(5.0)

            # Shorter delay when events are flowing to prevent accumulation; longer when empty
            if had_events:
                time.sleep(0.1)
            else:
                time.sleep(5.0)

    def _poll_and_process_batch(self) -> bool:
        events = self._claim_events()
        if not events:
            return False

        def process_one(ev: dict[str, Any]) -> None:
            if not self._running:
                return
            try:
                self._process_single_event(ev)
            except Exception as proc_err:
                event_id = str(ev.get("event_id") or "")
                lease_id = str(ev.get("lease_id") or "")
                self._ack_event(event_id, lease_id, ok=False, error=str(proc_err))

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(process_one, events))

        return True

    def _extract_item_id(self, resource: str) -> str | None:
        """Extract item ID from various Mercado Libre webhook resource URIs."""
        clean = resource.strip()
        # 1. /marketplace/items/MLX12345 or /items/CBT12345
        m = re.search(r"/(?:marketplace/)?items/([A-Z0-9]+)", clean)
        if m:
            return m.group(1).upper()
        # 2. /marketplace/seller-promotions/promotions/(candidate|offer)/[A-Z0-9]+-(ML[A-Z0-9]+|CBT[0-9]+)-
        m = re.search(r"promotions/(?:candidate|offer)/[A-Z0-9]+-([A-Z0-9]+)-", clean)
        if m:
            return m.group(1).upper()
        # 3. Fallback generic match
        m = re.search(r"\b(ML[A-Z][0-9]+|CBT[0-9]+)\b", clean)
        if m:
            return m.group(1).upper()
        return None

    def _extract_dimensions_and_weight(self, raw_item: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract normalized dimensions and weight JSON strings from item detail (supporting root attributes and variations)."""
        attrs: dict[str, Any] = {}

        def _collect_attrs(source_list: list[Any]) -> None:
            for attr in source_list or []:
                if not isinstance(attr, dict):
                    continue
                aid = str(attr.get("id") or "").strip().upper()
                if not aid or aid in attrs:
                    continue
                v_struct = attr.get("value_struct")
                if isinstance(v_struct, dict) and v_struct.get("number") is not None:
                    attrs[aid] = {"number": v_struct.get("number"), "unit": v_struct.get("unit")}
                elif attr.get("value_name") is not None:
                    attrs[aid] = str(attr.get("value_name")).strip()

        _collect_attrs(raw_item.get("attributes") or [])

        dim_candidates = [
            "SELLER_PACKAGE_HEIGHT", "PACKAGE_HEIGHT", "HEIGHT",
            "SELLER_PACKAGE_WIDTH", "PACKAGE_WIDTH", "WIDTH",
            "SELLER_PACKAGE_LENGTH", "PACKAGE_LENGTH", "LENGTH", "DEPTH",
        ]
        weight_candidates = ["SELLER_PACKAGE_WEIGHT", "PACKAGE_WEIGHT", "WEIGHT"]

        has_dim = any(c in attrs for c in dim_candidates)
        has_weight = any(c in attrs for c in weight_candidates)

        if not (has_dim and has_weight):
            for v in raw_item.get("variations") or []:
                if isinstance(v, dict):
                    _collect_attrs(v.get("attributes") or [])
                    if any(c in attrs for c in dim_candidates) and any(c in attrs for c in weight_candidates):
                        break

        dims: dict[str, Any] = {}
        for key, candidates in [
            ("height", ["SELLER_PACKAGE_HEIGHT", "PACKAGE_HEIGHT", "HEIGHT"]),
            ("width", ["SELLER_PACKAGE_WIDTH", "PACKAGE_WIDTH", "WIDTH"]),
            ("length", ["SELLER_PACKAGE_LENGTH", "PACKAGE_LENGTH", "LENGTH", "DEPTH"]),
        ]:
            for cand in candidates:
                if cand in attrs:
                    dims[key] = attrs[cand]
                    break
        dim_str = json.dumps(dims, sort_keys=True, ensure_ascii=False) if dims else None

        weight_val = None
        for cand in weight_candidates:
            if cand in attrs:
                weight_val = attrs[cand]
                break
        weight_str = json.dumps(weight_val, sort_keys=True, ensure_ascii=False) if weight_val is not None else None
        return dim_str, weight_str

    def _load_db_snapshot(self, account_id: str, item_id: str) -> dict[str, Any] | None:
        """Load persisted item snapshot from SQLite item_price_cache if available."""
        if not isinstance(self.client, MercadoClient):
            return None
        try:
            import sqlite3
            db_path = get_data_dir() / "discount-manager.sqlite"
            if not db_path.exists():
                return None
            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT price, original_price, dimensions_json, weight_json, raw_json "
                    "FROM item_price_cache WHERE account_id = ? AND item_id = ? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (str(account_id), str(item_id)),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception:
            return None

    def _save_db_snapshot(
        self,
        account_id: str,
        child_user_id: str,
        site_id: str,
        item_id: str,
        base_price: float,
        raw_item: dict[str, Any],
        dim_str: str | None,
        weight_str: str | None,
        pricing_system: str | None = None,
    ) -> None:
        """Persist item snapshot (including net_proceeds and shipping breakdown) into SQLite item_price_cache."""
        if not isinstance(self.client, MercadoClient):
            return
        try:
            import datetime
            import sqlite3
            db_path = get_data_dir() / "discount-manager.sqlite"
            if not db_path.exists():
                return
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            orig_p = raw_item.get("original_price")
            orig_val = float(orig_p) if orig_p is not None else base_price
            curr_id = str(raw_item.get("currency_id") or "USD")
            status = str(raw_item.get("status") or "active").lower()
            avail_qty = float(raw_item.get("available_quantity") or 0)
            src_rev = str(raw_item.get("last_updated") or now_iso)
            raw_payload: dict[str, Any] = {
                "net_proceeds": raw_item.get("net_proceeds"),
                "shipping": raw_item.get("shipping"),
                "last_updated": raw_item.get("last_updated"),
                "cbt_item_id": raw_item.get("cbt_item_id"),
            }
            if pricing_system:
                raw_payload["pricing_system"] = pricing_system
            raw_json_str = json.dumps(raw_payload, ensure_ascii=False)

            conn = sqlite3.connect(db_path, timeout=10.0)
            try:
                conn.execute(
                    """
                    INSERT INTO item_price_cache
                      (account_id, child_user_id, site_id, item_id, price, original_price,
                       currency_id, status, available_quantity, dimensions_json, weight_json,
                       source_revision, observed_at, confirmed, raw_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(account_id, child_user_id, site_id, item_id) DO UPDATE SET
                      price = excluded.price,
                      original_price = excluded.original_price,
                      currency_id = excluded.currency_id,
                      status = excluded.status,
                      available_quantity = excluded.available_quantity,
                      dimensions_json = COALESCE(excluded.dimensions_json, item_price_cache.dimensions_json),
                      weight_json = COALESCE(excluded.weight_json, item_price_cache.weight_json),
                      source_revision = excluded.source_revision,
                      observed_at = excluded.observed_at,
                      raw_json = excluded.raw_json,
                      updated_at = excluded.updated_at
                    """,
                    (
                        str(account_id),
                        str(child_user_id),
                        str(site_id).upper(),
                        str(item_id),
                        base_price,
                        orig_val,
                        curr_id,
                        status,
                        avail_qty,
                        dim_str,
                        weight_str,
                        src_rev,
                        now_iso,
                        raw_json_str,
                        now_iso,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _is_platform_only_shipping_change_on_old_cbt(self, account_id: str, raw_item: dict[str, Any]) -> bool:
        """Check if a CBT item's global parent has not been modified recently (before 2026-09-25),
        proving the breakdown inconsistency comes from Mercado Libre platform adjusting shipping fees
        on a System 1 item rather than the seller modifying item data to System 2.
        """
        cbt_id = str(raw_item.get("cbt_item_id") or "").strip().upper()
        if not cbt_id or not isinstance(self.client, MercadoClient):
            return False
        try:
            raw_cbt = self.client.get_item_detail(account_id, cbt_id)
            cbt_upd = str(raw_cbt.get("last_updated") or "").strip()
            if cbt_upd and cbt_upd < "2026-09-25":
                return True
        except Exception:
            pass
        return False

    def _process_single_event(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id") or "")
        lease_id = str(event.get("lease_id") or "")
        topic = str(event.get("topic") or "").strip().lower()

        # 1. Fast bypass for candidate/offer broadcasts (silent ack immediately)
        if topic in ("public_candidates", "public_offers"):
            self._ack_event(event_id, lease_id, ok=True)
            return

        resource = str(event.get("resource") or "").strip()
        remote_user_id = str(event.get("remote_user_id") or event.get("user_id") or "").strip()

        item_id = self._extract_item_id(resource)
        if not item_id:
            # Cannot extract item id from resource, ack and skip
            self._ack_event(event_id, lease_id, ok=True)
            return

        account_id, child_user_id, site_id = self._resolve_route(remote_user_id, item_id)
        if not account_id or not child_user_id:
            # Item does not belong to authorized accounts
            self._ack_event(event_id, lease_id, ok=True)
            return

        self.processed_count += 1

        # 2. If global CBT parent item notification (/items/CBT... or /marketplace/items/CBT...),
        # fan out to all site child items under marketplace_items
        if item_id.startswith("CBT"):
            try:
                raw_cbt = self.client.get_item_detail(account_id, item_id)
            except Exception as fetch_err:
                err_str = str(fetch_err)
                if "404" in err_str or "not found" in err_str.lower():
                    self._ack_event(event_id, lease_id, ok=True)
                    return
                self._ack_event(event_id, lease_id, ok=False)
                return

            cbt_children = (
                raw_cbt.get("marketplace_items")
                or raw_cbt.get("site_items")
                or []
            )
            if not isinstance(cbt_children, list) or not cbt_children:
                self._ack_event(event_id, lease_id, ok=True)
                return

            all_ok = True
            for child_entry in cbt_children:
                if not isinstance(child_entry, dict):
                    continue
                c_item_id = str(child_entry.get("item_id") or child_entry.get("id") or "").strip().upper()
                c_user_id = str(child_entry.get("user_id") or child_entry.get("seller_id") or "").strip()
                c_site_id = str(child_entry.get("site_id") or c_item_id[:3]).strip().upper()
                if not c_item_id:
                    continue
                if not c_user_id:
                    _, c_user_id, c_site_id = self._resolve_route(account_id, c_item_id)
                if c_user_id:
                    ok = self._process_marketplace_item(account_id, c_user_id, c_site_id, c_item_id)
                    if not ok:
                        all_ok = False

            self._ack_event(event_id, lease_id, ok=all_ok)
            return

        # 3. Standard site marketplace item (MLB..., MLM..., MLC..., MLA..., MCO...)
        ok = self._process_marketplace_item(account_id, child_user_id, site_id, item_id)
        self._ack_event(event_id, lease_id, ok=ok)

    def _process_marketplace_item(
        self,
        account_id: str,
        child_user_id: str,
        site_id: str,
        item_id: str,
    ) -> bool:
        """Check a single marketplace item and dispatch to System 1 (platform shipping change -> re-enroll)
        or System 2 (seller data change -> exit all promotions and stay un-enrolled).
        """
        with self._get_item_lock(item_id):
            store_name = self._get_store_alias(account_id)

            # 步骤 1：查最新官方真实数据，核实商品状态与售价构成
            try:
                raw_item = self.client.get_item_detail(account_id, item_id)
            except Exception as fetch_err:
                err_str = str(fetch_err)
                if "404" in err_str or "not found" in err_str.lower():
                    self._price_cache.pop(item_id, None)
                    self._net_cache.pop(item_id, None)
                    self._shipping_cache.pop(item_id, None)
                    self._dim_cache.pop(item_id, None)
                    return True
                return False

            if not raw_item or not raw_item.get("id"):
                self._price_cache.pop(item_id, None)
                self._net_cache.pop(item_id, None)
                self._shipping_cache.pop(item_id, None)
                self._dim_cache.pop(item_id, None)
                return True

            # 修正真实归属子账号（防止墨西哥站 MLM 海外仓/自发货双子账号串台）
            real_seller_id = str(raw_item.get("seller_id") or "").strip()
            if real_seller_id and real_seller_id != str(account_id):
                child_user_id = real_seller_id
            real_site_id = str(raw_item.get("site_id") or site_id or item_id[:3]).strip().upper()
            if real_site_id and real_site_id != "CBT":
                site_id = real_site_id

            # 检查商品在售状态：非 active 状态（closed / inactive / under_review / paused）后台静默放行
            status = str(raw_item.get("status") or "").lower()
            if status != "active":
                self._price_cache.pop(item_id, None)
                self._net_cache.pop(item_id, None)
                self._shipping_cache.pop(item_id, None)
                self._dim_cache.pop(item_id, None)
                return True

            item_info = extract_item_net_proceeds(raw_item)
            current_price = float(item_info.price or 0.0)
            current_net = float(item_info.net_proceeds or 0.0)
            current_shipping = float(item_info.shipping_cost or 0.0)
            official_orig = float(raw_item.get("original_price") or 0.0)
            dim_str, weight_str = self._extract_dimensions_and_weight(raw_item)

            # 读取内存与 SQLite 历史快照（含卖家净回款 net_proceeds、纯运费与包装尺寸/重量）
            cached_price = self._price_cache.get(item_id)
            cached_net = self._net_cache.get(item_id)
            cached_shipping = self._shipping_cache.get(item_id)
            cached_dims = self._dim_cache.get(item_id)
            old_dim = cached_dims[0] if cached_dims else None
            old_weight = cached_dims[1] if cached_dims else None
            db_system = None

            db_snap = self._load_db_snapshot(account_id, item_id)
            if db_snap is not None:
                if cached_price is None:
                    db_p = db_snap.get("original_price") or db_snap.get("price")
                    if db_p is not None:
                        cached_price = float(db_p)
                if old_dim is None:
                    old_dim = db_snap.get("dimensions_json")
                if old_weight is None:
                    old_weight = db_snap.get("weight_json")
                raw_j = db_snap.get("raw_json")
                if raw_j:
                    try:
                        parsed_raw = json.loads(raw_j)
                        if isinstance(parsed_raw, dict):
                            db_system = parsed_raw.get("pricing_system")
                            net_obj = parsed_raw.get("net_proceeds") or {}
                            if isinstance(net_obj, dict):
                                if cached_net is None:
                                    net_amt = net_obj.get("amount")
                                    if net_amt is not None:
                                        cached_net = float(net_amt)
                                if cached_shipping is None:
                                    for concept in net_obj.get("additional_concepts") or []:
                                        if str(concept.get("id") or "").lower() == "shipping_cost":
                                            s_amt = concept.get("amount")
                                            if s_amt is not None:
                                                cached_shipping = float(s_amt)
                    except Exception:
                        pass

            # 判定变动类型：
            # - "seller_data"：卖家修改了商品数据（净回款 net_proceeds 或包装长宽高/重量） -> 体系 2（退活动且不重报）
            # - "platform_shipping"：卖家净回款与尺寸重量未变，仅平台调整了运费/汇率 -> 体系 1（在活动中则退旧价并重报回原折扣）
            change_type: str | None = None
            change_desc = ""

            dim_or_weight_changed = (
                (old_dim and dim_str and old_dim != dim_str)
                or (old_weight and weight_str and old_weight != weight_str)
            )

            if dim_or_weight_changed:
                change_type = "seller_data"
                change_desc = "包装尺寸/重量数据变动"
            elif cached_net is not None and cached_net > 0 and current_net > 0:
                if abs(cached_net - current_net) >= 0.01:
                    change_type = "seller_data"
                    ref_old = cached_price if cached_price is not None else cached_net
                    change_desc = f"售价变动 (${ref_old:.2f} -> ${current_price:.2f})"
                else:
                    # 卖家净回款与尺寸重量均分毫未动！
                    # 若当前商品正处于活动中（official_orig > 0）或已标记为体系1且总价构成因平台运费变动发生偏离：
                    if (
                        (official_orig > 0 or db_system == "system1_discount")
                        and db_system != "system2_155"
                        and (item_info.is_inconsistent or (cached_price is not None and abs(cached_price - current_price) >= 0.05))
                    ):
                        change_type = "platform_shipping"
                        ref_old = official_orig if official_orig > 0 else (cached_price or current_price)
                        if cached_shipping is None and official_orig > 0 and item_info.fee_rate > 0 and current_net > 0:
                            calc_old_s = round(official_orig * (1 - item_info.fee_rate) - current_net, 2)
                            if calc_old_s > 0:
                                cached_shipping = calc_old_s

                        if cached_shipping is not None and abs(cached_shipping - current_shipping) >= 0.01:
                            ship_desc = f"平台运费调整 (${cached_shipping:.2f} -> ${current_shipping:.2f})"
                        elif current_shipping > 0:
                            ship_desc = f"平台运费变动 (最新 ${current_shipping:.2f})"
                        else:
                            ship_desc = "平台运费调整"

                        if abs(ref_old - current_price) >= 0.01:
                            price_desc = f"，售价更新 (${ref_old:.2f} -> ${current_price:.2f})"
                        else:
                            price_desc = ""
                        change_desc = f"{ship_desc}{price_desc}"
            elif current_net > 0:
                # 本地尚无 cached_net（首次接触或此前未存 raw_json）
                if item_info.is_inconsistent and official_orig > 0:
                    if self._is_platform_only_shipping_change_on_old_cbt(account_id, raw_item):
                        change_type = "platform_shipping"
                        if cached_shipping is None and item_info.fee_rate > 0:
                            calc_old_s = round(official_orig * (1 - item_info.fee_rate) - current_net, 2)
                            if calc_old_s > 0:
                                cached_shipping = calc_old_s
                        if cached_shipping is not None and abs(cached_shipping - current_shipping) >= 0.01:
                            ship_desc = f"平台运费调整 (${cached_shipping:.2f} -> ${current_shipping:.2f})"
                        elif current_shipping > 0:
                            ship_desc = f"平台运费变动 (最新 ${current_shipping:.2f})"
                        else:
                            ship_desc = "平台运费调整"
                        change_desc = f"{ship_desc}，售价更新 (${official_orig:.2f} -> ${current_price:.2f})"
                    else:
                        change_type = "seller_data"
                        change_desc = f"售价变动 (${official_orig:.2f} -> ${current_price:.2f})"
                elif cached_price is not None and abs(cached_price - current_price) >= 0.01:
                    if (official_orig > 0 or db_system == "system1_discount") and self._is_platform_only_shipping_change_on_old_cbt(account_id, raw_item):
                        change_type = "platform_shipping"
                        if cached_shipping is not None and abs(cached_shipping - current_shipping) >= 0.01:
                            ship_desc = f"平台运费调整 (${cached_shipping:.2f} -> ${current_shipping:.2f})"
                        elif current_shipping > 0:
                            ship_desc = f"平台运费变动 (最新 ${current_shipping:.2f})"
                        else:
                            ship_desc = "平台运费调整"
                        change_desc = f"{ship_desc}，售价更新 (${cached_price:.2f} -> ${current_price:.2f})"
                    elif not self._is_platform_only_shipping_change_on_old_cbt(account_id, raw_item):
                        change_type = "seller_data"
                        change_desc = f"售价变动 (${cached_price:.2f} -> ${current_price:.2f})"
            else:
                # 非 CBT 净回款模式或单测 Mock 商品（仅含 price）
                if cached_price is None:
                    self._price_cache[item_id] = current_price
                    if current_shipping > 0:
                        self._shipping_cache[item_id] = current_shipping
                    self._dim_cache[item_id] = (dim_str, weight_str)
                    self._save_db_snapshot(account_id, child_user_id, site_id, item_id, current_price, raw_item, dim_str, weight_str, db_system)
                    return True
                if abs(cached_price - current_price) >= 0.01:
                    change_type = "seller_data"
                    change_desc = f"售价变动 (${cached_price:.2f} -> ${current_price:.2f})"

            if change_type is None:
                # 首次建立基线或数据无变动（含无活动商品遇到平台运费微调），静默更新快照并放行
                self._price_cache[item_id] = current_price
                if current_net > 0:
                    self._net_cache[item_id] = current_net
                if current_shipping > 0:
                    self._shipping_cache[item_id] = current_shipping
                self._dim_cache[item_id] = (dim_str, weight_str)
                self._save_db_snapshot(account_id, child_user_id, site_id, item_id, current_price, raw_item, dim_str, weight_str, db_system)
                return True

            # 查询该商品当前正在进行（started）、待开始（pending）及可报候选（candidate）的活动
            active_promos: list[dict[str, Any]] = []
            candidate_promos: list[dict[str, Any]] = []
            try:
                promos_res = self.client.request(
                    account_id,
                    "GET",
                    f"/marketplace/seller-promotions/items/{item_id}",
                    params={"app_version": "v2", "user_id": child_user_id},
                )
                raw_list = promos_res if isinstance(promos_res, list) else []
                for p in raw_list:
                    if not isinstance(p, dict):
                        continue
                    p_id = str(p.get("id") or "")
                    p_type = str(p.get("type") or "DEAL").upper()
                    p_status = str(p.get("status") or "").lower()
                    if p_status in ("started", "pending") and (p_id or p_type == "PRICE_DISCOUNT"):
                        active_promos.append({"id": p_id, "type": p_type, "status": p_status, "raw": p})
                    elif p_status == "candidate" and p_id and p_type not in ("PRICE_DISCOUNT", "BANK", "PAYMENT_METHOD"):
                        candidate_promos.append({"id": p_id, "type": p_type, "status": p_status, "raw": p})
            except Exception:
                for p in raw_item.get("promotions") or []:
                    if not isinstance(p, dict):
                        continue
                    p_id = str(p.get("id") or "")
                    p_type = str(p.get("type") or "DEAL").upper()
                    if p_id:
                        active_promos.append({"id": p_id, "type": p_type, "status": "started", "raw": p})

            # 分支 A：【体系 2（卖家修改了商品数据 -> 155% 净利润新体系）】退出所有活动，保持不变，不再参加任何活动
            if change_type == "seller_data":
                self._price_cache[item_id] = current_price
                if current_net > 0:
                    self._net_cache[item_id] = current_net
                if current_shipping > 0:
                    self._shipping_cache[item_id] = current_shipping
                self._dim_cache[item_id] = (dim_str, weight_str)
                self._save_db_snapshot(
                    account_id, child_user_id, site_id, item_id, current_price, raw_item, dim_str, weight_str,
                    pricing_system="system2_155",
                )

                if active_promos or official_orig > 0:
                    self.log(
                        f"【{store_name}】商品 {item_id} 检测到{change_desc}，"
                        f"执行新定价策略：退出所有活动，不再报任何活动。",
                        tag="自动退出",
                    )
                    for ap in active_promos:
                        p_id = ap["id"]
                        p_type = ap["type"]
                        p_raw = ap.get("raw") or {}
                        offer_id = str(p_raw.get("offer_id") or ap.get("offer_id") or "").strip() or None
                        try:
                            if offer_id:
                                self.client.cancel_promotion_item(
                                    account_id, child_user_id, item_id, p_id, p_type, offer_id=offer_id
                                )
                            else:
                                self.client.cancel_promotion_item(account_id, child_user_id, item_id, p_id, p_type)
                            self.log(f"【{store_name}】商品 {item_id}: 已退出活动 {p_id} ({p_type})。", tag="自动退出")
                        except Exception as cancel_err:
                            self.log(f"【{store_name}】商品 {item_id}: 尝试退出活动 {p_id} 异常: {cancel_err}", tag="自动退出")

                cbt_id = str(raw_item.get("cbt_item_id") or "").strip().upper()
            else:
                cbt_id = ""

        if change_type == "seller_data":
            if cbt_id.startswith("CBT"):
                self._cascade_cbt_system2_exit(account_id, cbt_id, trigger_item_id=item_id)
            return True

        with self._get_item_lock(item_id):

            # 分支 B：【体系 1（仅平台调整了运费）】退出旧锁死原价并按最新运费继续报回折扣
            started_promos = [ap for ap in active_promos if ap.get("status") == "started"]
            target_promos = started_promos if started_promos else candidate_promos
            if not target_promos:
                self._price_cache[item_id] = current_price
                if current_net > 0:
                    self._net_cache[item_id] = current_net
                if current_shipping > 0:
                    self._shipping_cache[item_id] = current_shipping
                self._dim_cache[item_id] = (dim_str, weight_str)
                self._save_db_snapshot(account_id, child_user_id, site_id, item_id, current_price, raw_item, dim_str, weight_str, db_system)
                return True

            discount = self._get_current_discount()
            self.log(
                f"【{store_name}】商品 {item_id} {change_desc}，退出旧活动",
                tag="自动退出",
            )

            cancel_failed_ids: set[str] = set()
            for ap in started_promos:
                p_id = ap["id"]
                p_type = ap["type"]
                p_raw = ap.get("raw") or {}
                offer_id = str(p_raw.get("offer_id") or ap.get("offer_id") or "").strip() or None
                try:
                    if offer_id:
                        self.client.cancel_promotion_item(
                            account_id, child_user_id, item_id, p_id, p_type, offer_id=offer_id
                        )
                    else:
                        self.client.cancel_promotion_item(account_id, child_user_id, item_id, p_id, p_type)
                except Exception as cancel_err:
                    err_str = str(cancel_err)
                    if "404" in err_str or "not found" in err_str.lower():
                        pass
                    else:
                        cancel_failed_ids.add(p_id)
                        self.log(f"【{store_name}】商品 {item_id}: 退出旧活动 {p_id} 异常: {cancel_err}", tag="自动退出")

            if started_promos and isinstance(self.client, MercadoClient):
                time.sleep(1.0)

            # 重新获取退出活动后的最新商品详情以刷新基准原价，并按之前的折扣重新报名回原活动
            try:
                fresh_item = self.client.get_item_detail(account_id, item_id)
                if isinstance(fresh_item, dict) and fresh_item.get("id"):
                    raw_item = fresh_item
                    item_info = extract_item_net_proceeds(raw_item)
                    current_price = float(item_info.price or current_price)
            except Exception:
                pass

            # 若存在退出异常的活动，查证其是否在美客多后台已转为 candidate；未转为 candidate 者跳过重报以防 429 冲突
            current_candidate_ids: set[str] = set()
            if cancel_failed_ids:
                try:
                    current_promos = self.client.request(
                        account_id,
                        "GET",
                        f"/marketplace/seller-promotions/items/{item_id}",
                        params={"app_version": "v2", "user_id": child_user_id},
                    )
                    if isinstance(current_promos, list):
                        for cp in current_promos:
                            if isinstance(cp, dict) and str(cp.get("status") or "").lower() == "candidate":
                                current_candidate_ids.add(str(cp.get("id") or ""))
                except Exception:
                    pass

            for ap in target_promos:
                p_id = ap["id"]
                p_type = ap["type"]
                p_raw = ap.get("raw") or {}

                if p_id in cancel_failed_ids and p_id not in current_candidate_ids:
                    self.log(f"【{store_name}】商品 {item_id}: 旧活动 {p_id} 尚未完全退出，跳过本次重报以避免频控冲突。", tag="自动退出")
                    continue

                calc = calculate_deal_price(item_info, discount, p_raw, p_type)
                if not calc.eligible or calc.deal_price <= 0:
                    self.log(f"【{store_name}】商品 {item_id}: 活动 {p_id} 重算跳过 ({calc.skip_reason})。", tag="自动报回")
                    continue
                offer_id = str(p_raw.get("offer_id") or "") or None
                enrolled_ok = False
                last_enroll_err: Exception | None = None
                for attempt in range(3):
                    try:
                        self.client.enroll_promotion_item(
                            account_id=account_id,
                            child_user_id=child_user_id,
                            item_id=item_id,
                            promotion_id=p_id,
                            promotion_type=p_type,
                            deal_price=calc.deal_price,
                            offer_id=offer_id,
                            original_price=calc.original_price,
                        )
                        self.log(
                            f"【{store_name}】商品 {item_id}: 按新运费报回活动 {p_id} ({p_type})，"
                            f"折后售价 ${calc.deal_price:.2f}。",
                            tag="自动报回",
                        )
                        enrolled_ok = True
                        break
                    except Exception as enroll_err:
                        last_enroll_err = enroll_err
                        err_msg = str(enroll_err)
                        if ("LockedEntityException" in err_msg or "Offer Locked" in err_msg) and attempt < 2:
                            time.sleep(2.5)
                            continue
                        break

                if not enrolled_ok and last_enroll_err is not None:
                    err_msg = str(last_enroll_err)
                    if "429" in err_msg or "rate_limited" in err_msg.lower():
                        self.log(f"【{store_name}】商品 {item_id}: 报回活动 {p_id} ({p_type}) 触发平台限流，等待下次同步。", tag="自动报回")
                    elif "LockedEntityException" in err_msg or "Offer Locked" in err_msg:
                        self.log(f"【{store_name}】商品 {item_id}: 失败，平台活动锁占用中（请稍后重试）", tag="自动报回")
                    elif "ERROR_CREDIBILITY_DISCOUNTED_PRICE" in err_msg:
                        sugg_p = float(p_raw.get("suggested_discounted_price") or (p_raw.get("raw") or {}).get("suggested_discounted_price") or 0.0)
                        if sugg_p <= 0:
                            try:
                                cand_info = self.client.request(
                                    account_id,
                                    "GET",
                                    f"/marketplace/seller-promotions/promotions/{p_id}/items",
                                    params={"user_id": child_user_id, "item_id": item_id, "status": "candidate", "app_version": "v2"},
                                )
                                res_items = cand_info.get("results") if isinstance(cand_info, dict) else (cand_info if isinstance(cand_info, list) else [])
                                if res_items and isinstance(res_items[0], dict):
                                    sugg_p = float(res_items[0].get("suggested_discounted_price") or 0.0)
                            except Exception:
                                pass
                        if sugg_p > 0:
                            reason = f"失败，折扣价高于平台预期（折后价 ${calc.deal_price:.2f} ，平台预期${sugg_p:.2f} ）"
                        else:
                            reason = f"失败，折扣价高于平台预期（折后价 ${calc.deal_price:.2f} ）"
                        self.log(f"【{store_name}】商品 {item_id}: {reason}", tag="自动报回")
                    elif "ITEM_NOT_ELIGIBLE" in err_msg:
                        self.log(f"【{store_name}】商品 {item_id}: 失败，不满足活动准入条件", tag="自动报回")
                    else:
                        clean_err = err_msg
                        m = re.search(r"Errors?:\s*(?:[A-Z_]+\s*-\s*)?([^,}\]]+)", clean_err)
                        if m:
                            clean_err = m.group(1).strip()
                        self.log(f"【{store_name}】商品 {item_id}: 失败，{clean_err}", tag="自动报回")

            # 报名完成后再次同步最新快照
            try:
                after_item = self.client.get_item_detail(account_id, item_id)
                if isinstance(after_item, dict) and after_item.get("id"):
                    raw_item = after_item
            except Exception:
                pass

            self._price_cache[item_id] = current_price
            if current_net > 0:
                self._net_cache[item_id] = current_net
            if current_shipping > 0:
                self._shipping_cache[item_id] = current_shipping
            self._dim_cache[item_id] = (dim_str, weight_str)
            self._save_db_snapshot(
                account_id, child_user_id, site_id, item_id, current_price, raw_item, dim_str, weight_str,
                pricing_system="system1_discount",
            )
            return True

    def _cascade_cbt_system2_exit(self, account_id: str, cbt_id: str, trigger_item_id: str) -> None:
        """When any child item under a global CBT parent triggers System 2 (seller data modification),
        cascade promotion exit and system2_155 snapshot marking to all sibling site items of the same CBT parent.
        """
        try:
            raw_cbt = self.client.get_item_detail(account_id, cbt_id)
        except Exception:
            return
        if not isinstance(raw_cbt, dict):
            return

        cbt_children = raw_cbt.get("marketplace_items") or raw_cbt.get("site_items") or []
        if not isinstance(cbt_children, list) or not cbt_children:
            return

        store_name = self._get_store_alias(account_id)
        for child_entry in cbt_children:
            if not isinstance(child_entry, dict):
                continue
            sib_id = str(child_entry.get("item_id") or child_entry.get("id") or "").strip().upper()
            if not sib_id or sib_id == trigger_item_id:
                continue

            with self._get_item_lock(sib_id):
                try:
                    sib_raw = self.client.get_item_detail(account_id, sib_id)
                except Exception:
                    continue
                if not isinstance(sib_raw, dict) or not sib_raw.get("id"):
                    continue

                sib_user_id = str(
                    sib_raw.get("seller_id")
                    or child_entry.get("user_id")
                    or child_entry.get("seller_id")
                    or ""
                ).strip()
                sib_site_id = str(sib_raw.get("site_id") or child_entry.get("site_id") or sib_id[:3]).strip().upper()
                if not sib_user_id:
                    _, sib_user_id, sib_site_id = self._resolve_route(account_id, sib_id)
                if not sib_user_id:
                    continue

                sib_status = str(sib_raw.get("status") or "").lower()
                if sib_status == "active":
                    sib_promos: list[dict[str, Any]] = []
                    try:
                        promos_res = self.client.request(
                            account_id,
                            "GET",
                            f"/marketplace/seller-promotions/items/{sib_id}",
                            params={"app_version": "v2", "user_id": sib_user_id},
                        )
                        for p in (promos_res if isinstance(promos_res, list) else []):
                            if not isinstance(p, dict):
                                continue
                            p_id = str(p.get("id") or "")
                            p_type = str(p.get("type") or "DEAL").upper()
                            p_status = str(p.get("status") or "").lower()
                            if p_status in ("started", "pending") and (p_id or p_type == "PRICE_DISCOUNT"):
                                sib_promos.append({
                                    "id": p_id,
                                    "type": p_type,
                                    "offer_id": str(p.get("offer_id") or "").strip() or None,
                                })
                    except Exception:
                        pass

                    if sib_promos:
                        for ap in sib_promos:
                            p_id = ap["id"]
                            p_type = ap["type"]
                            offer_id = ap.get("offer_id")
                            try:
                                if offer_id:
                                    self.client.cancel_promotion_item(
                                        account_id, sib_user_id, sib_id, p_id, p_type, offer_id=offer_id
                                    )
                                else:
                                    self.client.cancel_promotion_item(account_id, sib_user_id, sib_id, p_id, p_type)
                                self.log(f"【{store_name}】商品 {sib_id}: 因同父商品 {cbt_id} 数据变动，已联动退出活动 {p_id} ({p_type})。", tag="自动退出")
                            except Exception as cancel_err:
                                self.log(f"【{store_name}】商品 {sib_id}: 联动退出活动 {p_id} 异常: {cancel_err}", tag="自动退出")
                        try:
                            fresh_sib = self.client.get_item_detail(account_id, sib_id)
                            if isinstance(fresh_sib, dict) and fresh_sib.get("id"):
                                sib_raw = fresh_sib
                        except Exception:
                            pass

                sib_info = extract_item_net_proceeds(sib_raw)
                sib_price = float(sib_info.price or 0.0)
                sib_net = float(sib_info.net_proceeds or 0.0)
                sib_shipping = float(sib_info.shipping_cost or 0.0)
                sib_dim, sib_weight = self._extract_dimensions_and_weight(sib_raw)
                if sib_price > 0:
                    self._price_cache[sib_id] = sib_price
                if sib_net > 0:
                    self._net_cache[sib_id] = sib_net
                if sib_shipping > 0:
                    self._shipping_cache[sib_id] = sib_shipping
                self._dim_cache[sib_id] = (sib_dim, sib_weight)
                self._save_db_snapshot(
                    account_id, sib_user_id, sib_site_id, sib_id, sib_price, sib_raw, sib_dim, sib_weight,
                    pricing_system="system2_155",
                )

    def _get_current_discount(self) -> float:
        """Return the System 1 net proceeds discount (30.0% baseline)."""
        settings_file = self.auth.db_path.parent / "settings.json"
        if settings_file.exists():
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                val = data.get("sellerDefaultDiscount")
                if val is not None and float(val) > 10.0:
                    return float(val)
            except Exception:
                pass
        return 30.0

    def _get_store_alias(self, account_id: str) -> str:
        acc_str = str(account_id)
        settings_file = self.auth.db_path.parent / "settings.json"
        if settings_file.exists():
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                aliases = data.get("storeAliases") or {}
                if aliases.get(acc_str):
                    return str(aliases[acc_str])
            except Exception:
                pass
        try:
            tok = self.auth.get_token(acc_str)
            if tok.display_name:
                return tok.display_name
        except Exception:
            pass
        return f"店铺 {acc_str}"

    def _resolve_route(self, remote_user_id: str, item_id: str) -> tuple[str | None, str | None, str | None]:
        """Match remote_user_id or item_id to account_id, child_user_id, site_id."""
        accounts = self.auth.list_accounts()
        item_prefix = item_id[:3].upper() if len(item_id) >= 3 else ""

        def find_best_site(sites: list[dict[str, Any]]) -> dict[str, Any] | None:
            if not sites:
                return None
            if item_prefix:
                matched = [s for s in sites if str(s.get("site_id") or "").upper() == item_prefix]
                if matched:
                    # Prefer remote (cross-border drop shipping) over fulfillment when multiple child accounts exist
                    for s in matched:
                        if str(s.get("logistic_type") or "").lower() == "remote":
                            return s
                    return matched[0]
            return sites[0]

        # 1. Direct match by parent account id
        for acc in accounts:
            acc_id = acc["account_id"]
            if acc_id == remote_user_id:
                sites = self.auth.list_sites(acc_id)
                best = find_best_site(sites)
                if best:
                    return acc_id, best["child_user_id"], best["site_id"]

        # 2. Match by child_user_id across sites
        for acc in accounts:
            acc_id = acc["account_id"]
            sites = self.auth.list_sites(acc_id)
            for s in sites:
                if str(s.get("child_user_id")) == remote_user_id:
                    return acc_id, s["child_user_id"], s["site_id"]

        # Strictly reject external accounts not matching local parent or child store IDs
        return None, None, None

    def _get_consumer_scope(self) -> tuple[str, list[str]]:
        """Get comma-separated application IDs and unique user IDs for claim scope."""
        app_ids: set[str] = set()
        user_ids: set[str] = set()

        try:
            settings_path = get_data_dir() / "settings.json"
            if settings_path.exists():
                with open(settings_path, encoding="utf-8") as handle:
                    st = json.load(handle)
                    cfg_app = str(st.get("activityCallbackApplicationId") or "").strip()
                    if cfg_app:
                        for part in cfg_app.split(","):
                            clean_app = part.strip()
                            if clean_app.isdigit():
                                app_ids.add(clean_app)
        except Exception:
            pass

        if hasattr(self.auth, "_get_connection"):
            try:
                conn = self.auth._get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT DISTINCT client_id FROM oauth_tokens WHERE client_id IS NOT NULL AND client_id != ''")
                    for (cid,) in cur.fetchall():
                        cid_str = str(cid).strip()
                        if cid_str.isdigit():
                            app_ids.add(cid_str)

                    cur.execute("SELECT DISTINCT account_id FROM account_profiles WHERE account_id IS NOT NULL AND account_id != ''")
                    for (aid,) in cur.fetchall():
                        aid_str = str(aid).strip()
                        if aid_str.isdigit():
                            user_ids.add(aid_str)

                    cur.execute("SELECT DISTINCT child_user_id FROM marketplace_sites WHERE child_user_id IS NOT NULL AND child_user_id != ''")
                    for (cuid,) in cur.fetchall():
                        cuid_str = str(cuid).strip()
                        if cuid_str.isdigit():
                            user_ids.add(cuid_str)
                finally:
                    conn.close()
            except Exception:
                pass

        app_id_str = ", ".join(sorted(app_ids))
        return app_id_str, sorted(user_ids)

    def _claim_events(self) -> list[dict[str, Any]]:
        """Call claim endpoint with Bearer auth to retrieve a batch of pending notifications."""
        if not self.claim_url:
            try:
                settings_path = get_data_dir() / "settings.json"
                if settings_path.exists():
                    st = json.loads(settings_path.read_text(encoding="utf-8"))
                    self.claim_url = str(st.get("activityCallbackClaimUrl") or "").strip()
                    self.ack_url = str(st.get("activityCallbackAckUrl") or "").strip()
            except Exception:
                pass
        if not self.claim_url:
            return []
        secret = self._get_secret()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MercadoDiscountManager/2.0",
        }
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

        app_id_str, user_ids = self._get_consumer_scope()
        body_dict: dict[str, Any] = {}
        if app_id_str and user_ids:
            body_dict["application_id"] = app_id_str
            body_dict["user_ids"] = user_ids
        data_bytes = json.dumps(body_dict).encode("utf-8")

        try:
            req = urllib.request.Request(
                self.claim_url,
                data=data_bytes,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                events = data.get("events")
                if isinstance(events, list):
                    return events
                if data.get("event_id"):
                    return [data]
                return []
        except Exception:
            return []

    def _ack_event(
        self,
        event_id: str,
        lease_id: str,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        """Send ack to remove the event from cloud queue."""
        if not self.ack_url or not event_id or not lease_id:
            return
        secret = self._get_secret()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

        payload: dict[str, Any] = {
            "event_id": event_id,
            "lease_id": lease_id,
            "ok": bool(ok),
        }
        if not ok and error:
            payload["error"] = str(error)[:500]

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.ack_url,
                data=data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception:
            pass
