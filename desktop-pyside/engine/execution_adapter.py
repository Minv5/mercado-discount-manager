from __future__ import annotations

import datetime
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .auth import AuthManager
from .client import MercadoClient, clean_error_message
from .crypto import get_data_dir
from .policy import (
    ActionType,
    PolicyPlan,
    PromoAction,
    SiblingCancelAction,
    extract_dimensions_and_weight,
)
from .pricing import extract_item_net_proceeds


class PolicyExecutionAdapter:
    """Side-effect adapter outside the pure logic seam.

    Translates PolicyPlan instructions into Mercado Libre API network operations,
    manages retry loops, 404/IncompleteRead readback self-healing, cross-site CBT
    cascade exits, and persists item snapshots to SQLite and memory caches.
    """

    def __init__(
        self,
        auth_manager: AuthManager | None = None,
        client: MercadoClient | None = None,
    ) -> None:
        self.auth = auth_manager or AuthManager()
        self.client = client or MercadoClient(self.auth)

        # Snapshot and debounce caches
        self._price_cache: dict[str, float] = {}
        self._net_cache: dict[str, float] = {}
        self._shipping_cache: dict[str, float] = {}
        self._dim_cache: dict[str, tuple[str | None, str | None]] = {}

        self._item_locks: dict[str, threading.Lock] = {}
        self._item_locks_guard = threading.Lock()

        self._recently_exited_promos: dict[tuple[str, str], float] = {}
        self._recent_shipping_reenrolls: dict[str, tuple[float, float, float]] = {}
        self._recent_cbt_cascades: dict[str, float] = {}
        self._exits_lock = threading.Lock()

        self._pending_review_items: dict[str, dict[str, Any]] = {}
        self._pending_review_lock = threading.Lock()

    def get_item_lock(self, item_id: str) -> threading.Lock:
        """Retrieve or create a reentrant/thread lock per item ID."""
        with self._item_locks_guard:
            lock = self._item_locks.get(item_id)
            if lock is None:
                lock = threading.Lock()
                self._item_locks[item_id] = lock
            return lock

    def clear_item_cache(self, item_id: str) -> None:
        """Evict an item from all in-memory snapshot caches (e.g. on 404 or closed status)."""
        self._price_cache.pop(item_id, None)
        self._net_cache.pop(item_id, None)
        self._shipping_cache.pop(item_id, None)
        self._dim_cache.pop(item_id, None)

    # --- Debounce & Cooldown Helpers ---

    def is_recently_exited(self, item_id: str, promo_id: str, ttl_sec: float = 60.0) -> bool:
        now = time.time()
        with self._exits_lock:
            ts = self._recently_exited_promos.get((item_id, promo_id))
            if ts and (now - ts) < ttl_sec:
                return True
            if len(self._recently_exited_promos) > 500:
                self._recently_exited_promos = {
                    k: v for k, v in self._recently_exited_promos.items() if (now - v) < ttl_sec
                }
            return False

    def mark_recently_exited(self, item_id: str, promo_id: str) -> None:
        with self._exits_lock:
            self._recently_exited_promos[(item_id, promo_id)] = time.time()

    def unmark_recently_exited(self, item_id: str, promo_id: str) -> None:
        with self._exits_lock:
            self._recently_exited_promos.pop((item_id, promo_id), None)

    def is_recently_shipping_reenrolled(
        self, item_id: str, net_proceeds: float, shipping_cost: float, ttl_sec: float = 60.0
    ) -> bool:
        now = time.time()
        key_net = round(net_proceeds, 2)
        key_ship = round(shipping_cost, 2)
        with self._exits_lock:
            rec = self._recent_shipping_reenrolls.get(item_id)
            if rec is not None:
                prev_net, prev_ship, ts = rec
                if (now - ts) < ttl_sec and abs(prev_net - key_net) < 0.01 and abs(prev_ship - key_ship) < 0.01:
                    return True
            if len(self._recent_shipping_reenrolls) > 500:
                self._recent_shipping_reenrolls = {
                    k: v for k, v in self._recent_shipping_reenrolls.items() if (now - v[2]) < ttl_sec
                }
            return False

    def mark_shipping_reenrolled(self, item_id: str, net_proceeds: float, shipping_cost: float) -> None:
        with self._exits_lock:
            self._recent_shipping_reenrolls[item_id] = (
                round(net_proceeds, 2),
                round(shipping_cost, 2),
                time.time(),
            )

    def should_cascade_cbt(self, cbt_id: str, cooldown_sec: float = 60.0) -> bool:
        now = time.time()
        with self._exits_lock:
            last = self._recent_cbt_cascades.get(cbt_id)
            if last and (now - last) < cooldown_sec:
                return False
            self._recent_cbt_cascades[cbt_id] = now
            if len(self._recent_cbt_cascades) > 200:
                self._recent_cbt_cascades = {
                    k: v for k, v in self._recent_cbt_cascades.items() if (now - v) < cooldown_sec
                }
            return True

    # --- Store & Site Helpers ---

    def get_store_alias(self, account_id: str) -> str:
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

    @staticmethod
    def get_site_label(item_or_site: str) -> str:
        code = str(item_or_site or "").strip()[:3].upper()
        site_map = {
            "MLB": "巴西站",
            "MLM": "墨西哥站",
            "MLA": "阿根廷站",
            "MLC": "智利站",
            "MCO": "哥伦比亚站",
            "MLU": "乌拉圭站",
            "MPE": "秘鲁站",
            "MEC": "厄瓜多尔站",
        }
        name = site_map.get(code)
        return f"【{name}】" if name else (f"【{code}站】" if code and code != "CBT" else "")

    def resolve_route(self, remote_user_id: str, item_id: str) -> tuple[str | None, str | None, str | None]:
        """Match remote_user_id or item_id to account_id, child_user_id, site_id."""
        accounts = self.auth.list_accounts()
        item_prefix = item_id[:3].upper() if len(item_id) >= 3 else ""

        def find_best_site(sites: list[dict[str, Any]]) -> dict[str, Any] | None:
            if not sites:
                return None
            if item_prefix:
                matched = [s for s in sites if str(s.get("site_id") or "").upper() == item_prefix]
                if matched:
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

        return None, None, None

    # --- Snapshot Persistence (SQLite & Memory) ---

    def load_db_snapshot(self, account_id: str, item_id: str) -> dict[str, Any] | None:
        """Load persisted item snapshot from SQLite item_price_cache if available."""
        if not isinstance(self.client, MercadoClient):
            return None
        try:
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

    def save_db_snapshot(
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

    def load_cached_snapshot(self, account_id: str, item_id: str) -> dict[str, Any]:
        """Load in-memory and SQLite cached snapshot for an item."""
        cached_price = self._price_cache.get(item_id)
        cached_net = self._net_cache.get(item_id)
        cached_shipping = self._shipping_cache.get(item_id)
        cached_dims = self._dim_cache.get(item_id)
        old_dim = cached_dims[0] if cached_dims else None
        old_weight = cached_dims[1] if cached_dims else None
        db_system = None

        db_snap = self.load_db_snapshot(account_id, item_id)
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

        return {
            "price": cached_price,
            "net_proceeds": cached_net,
            "shipping_cost": cached_shipping,
            "dimensions": old_dim,
            "weight": old_weight,
            "pricing_system": db_system,
        }

    def is_platform_only_shipping_change_on_old_cbt(self, account_id: str, raw_item: dict[str, Any]) -> bool:
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

    # --- Cancellation with Readback Self-Healing ---

    def cancel_promotion_with_readback(
        self,
        account_id: str,
        child_user_id: str,
        item_id: str,
        promotion_id: str,
        promotion_type: str,
        offer_id: str | None = None,
    ) -> tuple[bool, str]:
        """Cancels a promotion item with automatic readback verification against network disconnects/IncompleteRead.
        Returns (success: bool, clean_err: str).
        """
        try:
            if offer_id:
                self.client.cancel_promotion_item(
                    account_id, child_user_id, item_id, promotion_id, promotion_type, offer_id=offer_id
                )
            else:
                self.client.cancel_promotion_item(
                    account_id, child_user_id, item_id, promotion_id, promotion_type
                )
            self.mark_recently_exited(item_id, promotion_id)
            return True, ""
        except Exception as cancel_err:
            # 读回自愈核验：检查远端是否实际已经退出了（避免服务端响应单向断开造成假失败）
            try:
                remote_promos = self.client.get_item_promotions(account_id, child_user_id, item_id)
                remote_matching = [
                    rp for rp in remote_promos
                    if str(rp.get("id") or "") == str(promotion_id) and rp.get("status") in ("started", "pending")
                ]
                if not remote_matching:
                    self.mark_recently_exited(item_id, promotion_id)
                    return True, ""
            except Exception:
                pass

            err_str = str(cancel_err)
            if "404" in err_str or "not found" in err_str.lower():
                self.mark_recently_exited(item_id, promotion_id)
                return True, ""

            clean_err = clean_error_message(cancel_err)
            return False, clean_err

    # --- CBT Cross-Site Cascade Exits ---

    def collect_cbt_siblings(self, account_id: str, cbt_id: str, trigger_item_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        try:
            raw_cbt = self.client.get_item_detail(account_id, cbt_id)
            if not isinstance(raw_cbt, dict):
                return results
            cbt_children = raw_cbt.get("marketplace_items") or raw_cbt.get("site_items") or []
            seen_sibs: set[str] = set()
            for child_entry in cbt_children:
                if not isinstance(child_entry, dict):
                    continue
                sib_id = str(child_entry.get("item_id") or child_entry.get("id") or "").strip().upper()
                if not sib_id or sib_id == trigger_item_id or sib_id in seen_sibs:
                    continue
                seen_sibs.add(sib_id)
                sib_user_id = str(child_entry.get("user_id") or child_entry.get("seller_id") or "").strip()
                sib_site_id = str(child_entry.get("site_id") or sib_id[:3]).strip().upper()
                if not sib_user_id:
                    _, sib_user_id, sib_site_id = self.resolve_route(account_id, sib_id)
                if not sib_user_id:
                    continue
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
                            if not self.is_recently_exited(sib_id, p_id):
                                sib_promos.append({
                                    "id": p_id,
                                    "type": p_type,
                                    "name": str(p.get("name") or "").strip(),
                                    "offer_id": str(p.get("offer_id") or "").strip() or None,
                                })
                except Exception:
                    pass
                results.append({
                    "item_id": sib_id,
                    "site_id": sib_site_id,
                    "user_id": sib_user_id,
                    "active_promos": sib_promos,
                })
        except Exception:
            pass
        return results

    def cascade_cbt_system2_exit(
        self,
        account_id: str,
        cbt_id: str,
        trigger_item_id: str,
        cbt_reason: str = "",
        on_log: Callable[[str, str | None], None] | None = None,
    ) -> None:
        """When any child item under a global CBT parent triggers System 2 (seller data modification),
        cascade promotion exit and system2_155 snapshot marking to all sibling site items of the same CBT parent.
        """
        def log(msg: str, tag: str | None = None) -> None:
            if on_log:
                try:
                    on_log(msg, tag)
                except Exception:
                    pass

        if not self.should_cascade_cbt(cbt_id):
            return
        try:
            raw_cbt = self.client.get_item_detail(account_id, cbt_id)
        except Exception:
            return
        if not isinstance(raw_cbt, dict):
            return

        cbt_children = raw_cbt.get("marketplace_items") or raw_cbt.get("site_items") or []
        if not isinstance(cbt_children, list) or not cbt_children:
            return

        store_name = self.get_store_alias(account_id)
        seen_sibs: set[str] = set()
        reason_str = cbt_reason if cbt_reason else "处于美客多平台审核或数据变动中"
        for child_entry in cbt_children:
            if not isinstance(child_entry, dict):
                continue
            sib_id = str(child_entry.get("item_id") or child_entry.get("id") or "").strip().upper()
            if not sib_id or sib_id == trigger_item_id or sib_id in seen_sibs:
                continue
            seen_sibs.add(sib_id)

            with self.get_item_lock(sib_id):
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
                    _, sib_user_id, sib_site_id = self.resolve_route(account_id, sib_id)
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
                                if not self.is_recently_exited(sib_id, p_id):
                                    sib_promos.append({
                                        "id": p_id,
                                        "type": p_type,
                                        "name": str(p.get("name") or "").strip(),
                                        "offer_id": str(p.get("offer_id") or "").strip() or None,
                                    })
                    except Exception:
                        pass

                    if sib_promos:
                        for ap in sib_promos:
                            p_id = ap["id"]
                            p_type = ap["type"]
                            p_name = ap.get("name") or ""
                            p_label = f"【{p_name}】" if p_name else p_id
                            offer_id = ap.get("offer_id")
                            sib_site_tag = self.get_site_label(sib_id)
                            success, clean_err = self.cancel_promotion_with_readback(
                                account_id, sib_user_id, sib_id, p_id, p_type, offer_id=offer_id
                            )
                            if success:
                                log(f"【{store_name}】{sib_site_tag}商品 {sib_id}: 因全球父商品 {cbt_id} {reason_str}，已联动退出活动 {p_label}。", tag="自动退出")
                            else:
                                log(f"【{store_name}】{sib_site_tag}商品 {sib_id}: 因全球父商品 {cbt_id} {reason_str}，联动退出活动 {p_label} 异常: {clean_err}", tag="自动退出")
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
                sib_dim, sib_weight = extract_dimensions_and_weight(sib_raw)
                if sib_price > 0:
                    self._price_cache[sib_id] = sib_price
                if sib_net > 0:
                    self._net_cache[sib_id] = sib_net
                if sib_shipping > 0:
                    self._shipping_cache[sib_id] = sib_shipping
                self._dim_cache[sib_id] = (sib_dim, sib_weight)
                self.save_db_snapshot(
                    account_id, sib_user_id, sib_site_id, sib_id, sib_price, sib_raw, sib_dim, sib_weight,
                    pricing_system="system2_155",
                )

    # --- Primary Plan Execution Entrypoint ---

    def execute_plan(
        self,
        plan: PolicyPlan,
        on_log: Callable[[str, str | None], None] | None = None,
    ) -> bool:
        """Execute all side-effects dictated by a PolicyPlan.

        Handles:
        1. Emitting human-friendly domain logs.
        2. Canceling target promotions with readback self-healing.
        3. Cascading System 2 exits to sibling CBT items when applicable.
        4. Re-enrolling promotions with exponential retry and backoff.
        5. Managing pending review item tracking.
        6. Updating in-memory cache and SQLite snapshots.
        """
        def log(msg: str, tag: str | None = None) -> None:
            if on_log:
                try:
                    on_log(msg, tag)
                except Exception:
                    pass

        # 1. 输出策略决策的人性化业务日志
        for msg, tag in plan.log_messages:
            log(msg, tag=tag)

        store_name = self.get_store_alias(plan.account_id)
        item_id = plan.item_id
        account_id = plan.account_id
        child_user_id = plan.child_user_id
        site_id = plan.site_id

        # 2. 执行退出活动指令（主商品）
        cancel_failed_ids: set[str] = set()
        for ap in plan.promos_to_cancel:
            p_id = ap.promotion_id
            p_type = ap.promotion_type
            if self.is_recently_exited(item_id, p_id, ttl_sec=15.0):
                continue
            p_label = f"【{ap.name}】" if ap.name else p_id
            success, clean_err = self.cancel_promotion_with_readback(
                account_id, child_user_id, item_id, p_id, p_type, offer_id=ap.offer_id
            )
            if not success:
                cancel_failed_ids.add(p_id)
                s_tag = self.get_site_label(item_id)
                log(f"【{store_name}】{s_tag}商品 {item_id}: 退出旧活动 {p_label} 异常: {clean_err}", tag="自动退出")

        # 3. CBT 跨站点联动退出（体系2）
        cbt_id = str(plan.raw_item_snapshot.get("cbt_item_id") or "").strip().upper()
        if plan.decision_type in (ActionType.SELLER_DATA_CHANGE, ActionType.WAIT_PLATFORM_REVIEW) and cbt_id.startswith("CBT"):
            self.cascade_cbt_system2_exit(
                account_id, cbt_id, trigger_item_id=item_id, cbt_reason=plan.change_desc, on_log=on_log
            )

        # 4. 执行重报活动指令
        if plan.promos_to_enroll:
            if plan.promos_to_cancel and isinstance(self.client, MercadoClient):
                time.sleep(1.0)
            for ep in plan.promos_to_enroll:
                p_id = ep.promotion_id
                p_type = ep.promotion_type
                p_label = f"【{ep.name}】" if ep.name else p_id
                if p_id in cancel_failed_ids:
                    continue
                offer_id = ep.offer_id
                s_tag = self.get_site_label(item_id)
                enrolled_ok = False
                last_enroll_err: Exception | None = None
                reason_prefix = "按新净收益" if plan.decision_type == ActionType.REVIEW_RESOLVED_AUTO_REENROLL else "按新运费"
                for attempt in range(3):
                    try:
                        self.client.enroll_promotion_item(
                            account_id=account_id,
                            child_user_id=child_user_id,
                            item_id=item_id,
                            promotion_id=p_id,
                            promotion_type=p_type,
                            deal_price=ep.deal_price,
                            offer_id=offer_id,
                            original_price=plan.current_price,
                        )
                        log(
                            f"【{store_name}】{s_tag}商品 {item_id}: {reason_prefix}报回活动 {p_label}，"
                            f"折后售价 ${ep.deal_price:.2f}。",
                            tag="自动报回",
                        )
                        enrolled_ok = True
                        self.unmark_recently_exited(item_id, p_id)
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
                        log(f"【{store_name}】{s_tag}商品 {item_id}: 报回活动 {p_label} 触发平台限流，等待下次同步。", tag="自动报回")
                    elif "LockedEntityException" in err_msg or "Offer Locked" in err_msg:
                        log(f"【{store_name}】{s_tag}商品 {item_id}: 失败，平台活动锁占用中（请稍后重试）", tag="自动报回")
                    elif "ERROR_CREDIBILITY_DISCOUNTED_PRICE" in err_msg:
                        p_raw = ep.raw or {}
                        sugg_p = 0.0
                        for cand_dict in (p_raw, p_raw.get("raw") or {}, (p_raw.get("raw") or {}).get("raw") or {}):
                            if isinstance(cand_dict, dict) and cand_dict.get("suggested_discounted_price") is not None:
                                try:
                                    val = float(cand_dict["suggested_discounted_price"])
                                    if val > 0:
                                        sugg_p = val
                                        break
                                except (ValueError, TypeError):
                                    pass
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
                            reason = f"失败，折扣价高于平台预期（折后价 ${ep.deal_price:.2f} ，平台预期${sugg_p:.2f} ）"
                        else:
                            reason = f"失败，折扣价高于平台预期（折后价 ${ep.deal_price:.2f} ）"
                        log(f"【{store_name}】{s_tag}商品 {item_id}: {reason}", tag="自动报回")
                    elif "ITEM_NOT_ELIGIBLE" in err_msg:
                        log(f"【{store_name}】{s_tag}商品 {item_id}: 失败，不满足活动准入条件", tag="自动报回")
                    else:
                        clean_err = clean_error_message(last_enroll_err)
                        log(f"【{store_name}】{s_tag}商品 {item_id}: 失败，{clean_err}", tag="自动报回")

        # 5. 待审队列管理（双重保障）
        with self._pending_review_lock:
            if plan.wait_for_review:
                self._pending_review_items[item_id] = {
                    "account_id": account_id,
                    "child_user_id": child_user_id,
                    "site_id": site_id,
                    "timestamp": time.time(),
                }
            else:
                self._pending_review_items.pop(item_id, None)

        # 6. 更新内存快照缓存与 SQLite 持久化
        self._price_cache[item_id] = plan.current_price
        if plan.current_net > 0:
            self._net_cache[item_id] = plan.current_net
        if plan.current_shipping > 0:
            self._shipping_cache[item_id] = plan.current_shipping
        if plan.dim_str or plan.weight_str:
            self._dim_cache[item_id] = (plan.dim_str, plan.weight_str)
        if plan.decision_type == ActionType.PLATFORM_SHIPPING_CHANGE:
            self.mark_shipping_reenrolled(item_id, plan.current_net, plan.current_shipping)

        self.save_db_snapshot(
            account_id, child_user_id, site_id, item_id, plan.current_price, plan.raw_item_snapshot,
            plan.dim_str, plan.weight_str, plan.pricing_system
        )
        return True
