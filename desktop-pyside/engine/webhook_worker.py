from __future__ import annotations

import datetime
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .auth import AuthManager
from .client import MercadoClient, clean_error_message
from .crypto import get_data_dir
from .execution_adapter import PolicyExecutionAdapter
from .policy import (
    ActionType,
    EvaluationContext,
    ItemEventPolicy,
    PolicyPlan,
    PromoAction,
    SiblingCancelAction,
    extract_dimensions_and_weight,
)
from .pricing import calculate_deal_price, extract_item_net_proceeds


class WebhookWorker:
    """Listens for Mercado Libre webhook notifications and dispatches item events.

    Delegates all plan executions, network mutations, CBT cascades, and persistence
    to PolicyExecutionAdapter.
    """

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
        customer_service_callback: Callable[[dict[str, Any]], None] | None = None,
        execution_adapter: PolicyExecutionAdapter | None = None,
    ):
        _auth = auth_manager or AuthManager()
        _client = client or MercadoClient(_auth)
        self.adapter = execution_adapter or PolicyExecutionAdapter(_auth, _client)

        self.claim_url = claim_url
        self.ack_url = ack_url
        self._secret = secret
        self.log_callback = log_callback or (lambda msg: None)
        self.customer_service_callback = customer_service_callback

        self._running = False
        self._thread: threading.Thread | None = None
        self.discount_percent = 30.0
        self.processed_count = 0
        self._has_new_tasks = False
        self._tasks_lock = threading.Lock()
        self._logs: list[str] = []
        self._logs_lock = threading.Lock()

    # --- Properties Delegating to PolicyExecutionAdapter for Clean Seams & Backward Compatibility ---

    @property
    def auth(self) -> AuthManager:
        return self.adapter.auth

    @auth.setter
    def auth(self, val: AuthManager) -> None:
        self.adapter.auth = val

    @property
    def client(self) -> MercadoClient:
        return self.adapter.client

    @client.setter
    def client(self, val: MercadoClient) -> None:
        self.adapter.client = val

    @property
    def _price_cache(self) -> dict[str, float]:
        return self.adapter._price_cache

    @_price_cache.setter
    def _price_cache(self, val: dict[str, float]) -> None:
        self.adapter._price_cache = val

    @property
    def _net_cache(self) -> dict[str, float]:
        return self.adapter._net_cache

    @_net_cache.setter
    def _net_cache(self, val: dict[str, float]) -> None:
        self.adapter._net_cache = val

    @property
    def _shipping_cache(self) -> dict[str, float]:
        return self.adapter._shipping_cache

    @_shipping_cache.setter
    def _shipping_cache(self, val: dict[str, float]) -> None:
        self.adapter._shipping_cache = val

    @property
    def _dim_cache(self) -> dict[str, tuple[str | None, str | None]]:
        return self.adapter._dim_cache

    @_dim_cache.setter
    def _dim_cache(self, val: dict[str, tuple[str | None, str | None]]) -> None:
        self.adapter._dim_cache = val

    @property
    def _item_locks(self) -> dict[str, threading.Lock]:
        return self.adapter._item_locks

    @property
    def _item_locks_guard(self) -> threading.Lock:
        return self.adapter._item_locks_guard

    @property
    def _recently_exited_promos(self) -> dict[tuple[str, str], float]:
        return self.adapter._recently_exited_promos

    @_recently_exited_promos.setter
    def _recently_exited_promos(self, val: dict[tuple[str, str], float]) -> None:
        self.adapter._recently_exited_promos = val

    @property
    def _recent_shipping_reenrolls(self) -> dict[str, tuple[float, float, float]]:
        return self.adapter._recent_shipping_reenrolls

    @_recent_shipping_reenrolls.setter
    def _recent_shipping_reenrolls(self, val: dict[str, tuple[float, float, float]]) -> None:
        self.adapter._recent_shipping_reenrolls = val

    @property
    def _recent_cbt_cascades(self) -> dict[str, float]:
        return self.adapter._recent_cbt_cascades

    @_recent_cbt_cascades.setter
    def _recent_cbt_cascades(self, val: dict[str, float]) -> None:
        self.adapter._recent_cbt_cascades = val

    @property
    def _exits_lock(self) -> threading.Lock:
        return self.adapter._exits_lock

    @property
    def _pending_review_items(self) -> dict[str, dict[str, Any]]:
        return self.adapter._pending_review_items

    @_pending_review_items.setter
    def _pending_review_items(self, val: dict[str, dict[str, Any]]) -> None:
        self.adapter._pending_review_items = val

    @property
    def _pending_review_lock(self) -> threading.Lock:
        return self.adapter._pending_review_lock

    # --- Delegated Adapter Methods ---

    def _get_item_lock(self, item_id: str) -> threading.Lock:
        return self.adapter.get_item_lock(item_id)

    def _is_recently_exited(self, item_id: str, promo_id: str, ttl_sec: float = 60.0) -> bool:
        return self.adapter.is_recently_exited(item_id, promo_id, ttl_sec=ttl_sec)

    def _mark_recently_exited(self, item_id: str, promo_id: str) -> None:
        self.adapter.mark_recently_exited(item_id, promo_id)

    def _unmark_recently_exited(self, item_id: str, promo_id: str) -> None:
        self.adapter.unmark_recently_exited(item_id, promo_id)

    def _cancel_promotion_with_readback(
        self,
        account_id: str,
        child_user_id: str,
        item_id: str,
        promotion_id: str,
        promotion_type: str,
        offer_id: str | None = None,
    ) -> tuple[bool, str]:
        return self.adapter.cancel_promotion_with_readback(
            account_id, child_user_id, item_id, promotion_id, promotion_type, offer_id=offer_id
        )

    def _is_recently_shipping_reenrolled(
        self, item_id: str, net_proceeds: float, shipping_cost: float, ttl_sec: float = 60.0
    ) -> bool:
        return self.adapter.is_recently_shipping_reenrolled(item_id, net_proceeds, shipping_cost, ttl_sec=ttl_sec)

    def _mark_shipping_reenrolled(self, item_id: str, net_proceeds: float, shipping_cost: float) -> None:
        self.adapter.mark_shipping_reenrolled(item_id, net_proceeds, shipping_cost)

    def _should_cascade_cbt(self, cbt_id: str, cooldown_sec: float = 60.0) -> bool:
        return self.adapter.should_cascade_cbt(cbt_id, cooldown_sec=cooldown_sec)

    def _load_db_snapshot(self, account_id: str, item_id: str) -> dict[str, Any] | None:
        return self.adapter.load_db_snapshot(account_id, item_id)

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
        self.adapter.save_db_snapshot(
            account_id, child_user_id, site_id, item_id, base_price, raw_item, dim_str, weight_str, pricing_system
        )

    def _load_cached_snapshot(self, account_id: str, item_id: str) -> dict[str, Any]:
        return self.adapter.load_cached_snapshot(account_id, item_id)

    def _is_platform_only_shipping_change_on_old_cbt(self, account_id: str, raw_item: dict[str, Any]) -> bool:
        return self.adapter.is_platform_only_shipping_change_on_old_cbt(account_id, raw_item)

    def _collect_cbt_siblings(self, account_id: str, cbt_id: str, trigger_item_id: str) -> list[dict[str, Any]]:
        return self.adapter.collect_cbt_siblings(account_id, cbt_id, trigger_item_id)

    def _cascade_cbt_system2_exit(
        self,
        account_id: str,
        cbt_id: str,
        trigger_item_id: str,
        cbt_reason: str = "",
    ) -> None:
        self.adapter.cascade_cbt_system2_exit(
            account_id, cbt_id, trigger_item_id=trigger_item_id, cbt_reason=cbt_reason, on_log=self.log
        )

    def _get_store_alias(self, account_id: str) -> str:
        return self.adapter.get_store_alias(account_id)

    @staticmethod
    def _get_site_label(item_or_site: str) -> str:
        return PolicyExecutionAdapter.get_site_label(item_or_site)

    def _resolve_route(self, remote_user_id: str, item_id: str) -> tuple[str | None, str | None, str | None]:
        return self.adapter.resolve_route(remote_user_id, item_id)

    def _extract_dimensions_and_weight(self, raw_item: dict[str, Any]) -> tuple[str | None, str | None]:
        return extract_dimensions_and_weight(raw_item)

    @staticmethod
    def _format_platform_shipping_change_desc(
        ref_old_price: float,
        current_price: float,
        cached_shipping: float | None,
        current_shipping: float,
        current_net: float,
        fee_rate: float,
    ) -> str:
        eff_old_ship = cached_shipping
        if (
            (eff_old_ship is None or abs(eff_old_ship - current_shipping) < 0.01)
            and ref_old_price > 0
            and abs(ref_old_price - current_price) >= 0.01
            and fee_rate > 0
            and current_net > 0
        ):
            calc_old_s = round(ref_old_price * (1.0 - fee_rate) - current_net, 2)
            if calc_old_s > 0:
                eff_old_ship = calc_old_s

        if eff_old_ship is not None and abs(eff_old_ship - current_shipping) >= 0.01:
            ship_desc = f"平台运费调整 (${eff_old_ship:.2f} -> ${current_shipping:.2f})"
        elif current_shipping > 0:
            ship_desc = f"平台运费变动 (最新 ${current_shipping:.2f})"
        else:
            ship_desc = "平台运费调整"

        if abs(ref_old_price - current_price) >= 0.01:
            price_desc = f"，售价更新 (${ref_old_price:.2f} -> ${current_price:.2f})"
        else:
            price_desc = ""
        return f"{ship_desc}{price_desc}"

    # --- Logging & Task Helpers ---

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

    def pop_has_new_tasks(self) -> bool:
        return False

    def _record_auto_task(self, *args: Any, **kwargs: Any) -> None:
        return None

    # --- Worker Thread Lifecycle ---

    def start(self, discount_percent: float = 30.0) -> None:
        if self._running:
            self.discount_percent = discount_percent
            return
        self.discount_percent = discount_percent
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="WebhookWorkerThread")
        self._thread.start()
        self.log(f"后台变动监听已启动，当前基准净收益折扣: {self.discount_percent:.1f}%")

    def stop(self) -> None:
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
        last_review_reconcile = time.time()
        while self._running:
            had_events = False
            try:
                had_events = self._poll_and_process_batch()
            except Exception:
                time.sleep(5.0)

            # 定期 15 分钟（900秒）低频巡检审核中队列，防止平台漏推通知导致商品无法自动报回
            now = time.time()
            if (now - last_review_reconcile) >= 900.0:
                last_review_reconcile = now
                try:
                    self._reconcile_pending_reviews()
                except Exception:
                    pass

            if had_events:
                time.sleep(0.1)
            else:
                time.sleep(5.0)

    def _reconcile_pending_reviews(self) -> None:
        """Periodic reconciliation for items in under_review queue to auto re-enroll once platform review passes."""
        with self.adapter._pending_review_lock:
            items_to_check = list(self.adapter._pending_review_items.items())
        if not items_to_check:
            return
        for item_id, meta in items_to_check:
            if not self._running:
                break
            try:
                acc_id = meta["account_id"]
                c_user_id = meta["child_user_id"]
                s_id = meta["site_id"]
                self._process_marketplace_item(acc_id, c_user_id, s_id, item_id)
            except Exception:
                pass

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
        clean = resource.strip()
        m = re.search(r"/(?:marketplace/)?items/([A-Z0-9]+)", clean)
        if m:
            return m.group(1).upper()
        m = re.search(r"promotions/(?:candidate|offer)/[A-Z0-9]+-([A-Z0-9]+)-", clean)
        if m:
            return m.group(1).upper()
        m = re.search(r"\b(ML[A-Z][0-9]+|CBT[0-9]+)\b", clean)
        if m:
            return m.group(1).upper()
        return None

    def _process_single_event(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id") or "")
        lease_id = str(event.get("lease_id") or "")
        topic = str(event.get("topic") or "").strip().lower()

        # 1. Customer service topics
        cs_topics = (
            "questions", "marketplace_questions",
            "claims", "marketplace_claims",
            "messages", "marketplace_messages",
            "orders", "orders_v2", "marketplace_orders",
        )
        if topic in cs_topics:
            if self.customer_service_callback:
                try:
                    self.customer_service_callback(event)
                except Exception:
                    pass
            self._ack_event(event_id, lease_id, ok=True)
            return

        resource = str(event.get("resource") or "").strip()
        remote_user_id = str(event.get("remote_user_id") or event.get("user_id") or "").strip()

        item_id = self._extract_item_id(resource)
        if not item_id:
            self._ack_event(event_id, lease_id, ok=True)
            return

        account_id, child_user_id, site_id = self._resolve_route(remote_user_id, item_id)
        if not account_id or not child_user_id:
            self._ack_event(event_id, lease_id, ok=True)
            return

        self.processed_count += 1

        # 2. Global CBT parent item notification
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
            seen_c_items: set[str] = set()
            for child_entry in cbt_children:
                if not isinstance(child_entry, dict):
                    continue
                c_item_id = str(child_entry.get("item_id") or child_entry.get("id") or "").strip().upper()
                c_user_id = str(child_entry.get("user_id") or child_entry.get("seller_id") or "").strip()
                c_site_id = str(child_entry.get("site_id") or c_item_id[:3]).strip().upper()
                if not c_item_id or c_item_id in seen_c_items:
                    continue
                seen_c_items.add(c_item_id)
                if not c_user_id:
                    _, c_user_id, c_site_id = self._resolve_route(account_id, c_item_id)
                if c_user_id:
                    ok = self._process_marketplace_item(account_id, c_user_id, c_site_id, c_item_id)
                    if not ok:
                        all_ok = False

            self._ack_event(event_id, lease_id, ok=all_ok)
            return

        # 3. Standard site marketplace item
        ok = self._process_marketplace_item(account_id, child_user_id, site_id, item_id)
        self._ack_event(event_id, lease_id, ok=ok)

    def _process_marketplace_item(
        self,
        account_id: str,
        child_user_id: str,
        site_id: str,
        item_id: str,
    ) -> bool:
        """Check a single marketplace item and execute plan via PolicyExecutionAdapter."""
        with self.adapter.get_item_lock(item_id):
            store_name = self.adapter.get_store_alias(account_id)

            # 步骤 1：查最新官方真实数据，核实商品状态与售价构成
            try:
                raw_item = self.client.get_item_detail(account_id, item_id)
            except Exception as fetch_err:
                err_str = str(fetch_err)
                if "404" in err_str or "not found" in err_str.lower():
                    self.adapter.clear_item_cache(item_id)
                    return True
                return False

            if not raw_item or not raw_item.get("id"):
                self.adapter.clear_item_cache(item_id)
                return True

            # 修正真实归属子账号（防止墨西哥站 MLM 海外仓/自发货双子账号串台）
            real_seller_id = str(raw_item.get("seller_id") or "").strip()
            if real_seller_id and real_seller_id != str(account_id):
                child_user_id = real_seller_id
            real_site_id = str(raw_item.get("site_id") or site_id or item_id[:3]).strip().upper()
            if real_site_id and real_site_id != "CBT":
                site_id = real_site_id

            # 检查商品在售状态：非 active 且非 under_review 状态（closed / inactive / paused）后台静默放行
            status = str(raw_item.get("status") or "").lower()
            sub_status = [str(s).lower() for s in (raw_item.get("sub_status") or [])]
            if status not in ("active", "under_review") and "waiting_for_patch" not in sub_status:
                self.adapter.clear_item_cache(item_id)
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
                    p_name = str(p.get("name") or "").strip()
                    if p_status in ("started", "pending") and (p_id or p_type == "PRICE_DISCOUNT"):
                        active_promos.append({"id": p_id, "type": p_type, "status": p_status, "name": p_name, "raw": p})
                    elif p_status == "candidate" and p_id and p_type not in ("PRICE_DISCOUNT", "BANK", "PAYMENT_METHOD"):
                        candidate_promos.append({"id": p_id, "type": p_type, "status": p_status, "name": p_name, "raw": p})
            except Exception:
                for p in raw_item.get("promotions") or []:
                    if not isinstance(p, dict):
                        continue
                    p_id = str(p.get("id") or "")
                    p_type = str(p.get("type") or "DEAL").upper()
                    p_name = str(p.get("name") or "").strip()
                    if p_id:
                        active_promos.append({"id": p_id, "type": p_type, "status": "started", "name": p_name, "raw": p})

            # 读取内存与 SQLite 历史快照
            cached_snapshot = self.adapter.load_cached_snapshot(account_id, item_id)

            item_info = extract_item_net_proceeds(raw_item)
            is_debounced = self.adapter.is_recently_shipping_reenrolled(
                item_id, float(item_info.net_proceeds or 0.0), float(item_info.shipping_cost or 0.0)
            )
            is_old_cbt = self.adapter.is_platform_only_shipping_change_on_old_cbt(account_id, raw_item)

            with self.adapter._pending_review_lock:
                was_pending = item_id in self.adapter._pending_review_items

            # 纯逻辑领域决策策略评估（通过 EvaluationContext 消除参数泥团）
            ctx = EvaluationContext(
                account_id=account_id,
                child_user_id=child_user_id,
                site_id=site_id,
                raw_item=raw_item,
                cached_snapshot=cached_snapshot,
                active_promos=active_promos,
                candidate_promos=candidate_promos,
                store_alias=store_name,
                discount_percent=self._get_current_discount(),
                was_pending_review=was_pending,
                is_recently_shipping_reenrolled=is_debounced,
                is_old_cbt=is_old_cbt,
            )
            plan = ItemEventPolicy.evaluate(ctx)

            # 通过 PolicyExecutionAdapter 执行所有副作用与网络动作
            return self.adapter.execute_plan(plan, on_log=self.log)

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
        try:
            settings_path = get_data_dir() / "settings.json"
            if settings_path.exists():
                mtime = settings_path.stat().st_mtime
                if not self.claim_url or not self.ack_url or mtime != getattr(self, "_settings_mtime", 0):
                    st = json.loads(settings_path.read_text(encoding="utf-8"))
                    c_url = str(st.get("activityCallbackClaimUrl") or "").strip()
                    a_url = str(st.get("activityCallbackAckUrl") or "").strip()
                    if c_url:
                        self.claim_url = c_url
                    if a_url:
                        self.ack_url = a_url
                    self._settings_mtime = mtime
        except Exception:
            pass
        if not self.claim_url:
            return []
        secret = self._get_secret()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
                self._claim_fail_streak = 0
                events = data.get("events")
                if isinstance(events, list):
                    return events
                if data.get("event_id"):
                    return [data]
                return []
        except Exception as err:
            streak = getattr(self, "_claim_fail_streak", 0) + 1
            self._claim_fail_streak = streak
            now_t = time.time()
            if streak >= 6 and now_t - getattr(self, "_last_claim_err_t", 0) > 60:
                self._last_claim_err_t = now_t
                self.log(f"网关通信异常: {clean_error_message(err)}")
            return []

    def _ack_event(
        self,
        event_id: str,
        lease_id: str,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        """Send ack to remove the event from cloud queue."""
        if not self.ack_url:
            try:
                settings_path = get_data_dir() / "settings.json"
                if settings_path.exists():
                    st = json.loads(settings_path.read_text(encoding="utf-8"))
                    self.ack_url = str(st.get("activityCallbackAckUrl") or "").strip()
            except Exception:
                pass
        if not self.ack_url or not event_id or not lease_id:
            return
        secret = self._get_secret()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MercadoDiscountManager/2.0",
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
