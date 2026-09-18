from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .auth import AuthManager
from .client import MercadoClient
from .pricing import calculate_deal_price, extract_item_net_proceeds


class WebhookWorker:
    DEFAULT_CLAIM_URL = "https://webhook.xingtupro1020.com/meli-callback/consumer/claim"
    DEFAULT_ACK_URL = "https://webhook.xingtupro1020.com/meli-callback/consumer/ack"

    def __init__(
        self,
        auth_manager: AuthManager | None = None,
        client: MercadoClient | None = None,
        claim_url: str = DEFAULT_CLAIM_URL,
        ack_url: str = DEFAULT_ACK_URL,
        log_callback: Callable[[str], None] | None = None,
    ):
        self.auth = auth_manager or AuthManager()
        self.client = client or MercadoClient(self.auth)
        self.claim_url = claim_url
        self.ack_url = ack_url
        self.log_callback = log_callback or (lambda msg: None)

        self._running = False
        self._thread: threading.Thread | None = None
        self.discount_percent = 6.0  # default 6%

    def log(self, msg: str) -> None:
        try:
            self.log_callback(f"[Webhook 自动改价重报] {msg}")
        except Exception:
            pass

    def start(self, discount_percent: float = 6.0) -> None:
        """Start the background polling thread."""
        if self._running:
            return
        self.discount_percent = discount_percent
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="WebhookWorkerThread")
        self._thread.start()
        self.log("后台监控已启动，正在监听美客多变动通知...")

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._running = False
        self.log("后台监控已停止。")

    def is_running(self) -> bool:
        return self._running

    def _run_loop(self) -> None:
        while self._running:
            try:
                self._poll_and_process_one()
            except Exception as err:
                # Silent retry on network errors, don't crash loop
                time.sleep(5.0)
            time.sleep(10.0)

    def _poll_and_process_one(self) -> None:
        """Fetch one pending event, process with 3-step atomic operation, and ack."""
        event = self._claim_event()
        if not event:
            return

        event_id = event.get("event_id") or ""
        claim_token = event.get("claim_token") or ""
        resource = str(event.get("resource") or "")
        remote_user_id = str(event.get("remote_user_id") or "")

        # 1. Parse item ID
        item_id_match = re.search(r"(ML[A-Z0-9]+)", resource)
        if not item_id_match:
            self._ack_event(event_id, claim_token, status="skipped", reason="无法从资源解析商品ID")
            return

        item_id = item_id_match.group(1)

        # 2. Resolve account and child user
        account_id, child_user_id, site_id = self._resolve_route(remote_user_id, item_id)
        if not account_id or not child_user_id:
            self._ack_event(event_id, claim_token, status="skipped", reason="无法匹配归属店铺")
            return

        self.log(f"捕获变动事件: 店铺 {account_id} / 商品 {item_id}")

        try:
            # 步骤 1：查最新真数据，覆盖本地
            raw_item = self.client.get_item_detail(account_id, item_id)
            item_info = extract_item_net_proceeds(raw_item)
            self.log(f"第1步[抓取最新]: 原价=${item_info.price}，运费=${item_info.shipping_cost}，真实净回款=${item_info.net_proceeds}")

            # 步骤 2：取消旧活动（物理止血）
            # Check if item is currently in promotions
            promotions = raw_item.get("promotions") or []
            if not promotions and raw_item.get("promotion"):
                promotions = [raw_item["promotion"]]

            active_promo_id = None
            active_promo_type = None
            for p in promotions:
                p_id = str(p.get("id") or p.get("promotion_id") or "")
                p_type = str(p.get("type") or p.get("promotion_type") or "DEAL").upper()
                if p_id:
                    active_promo_id = p_id
                    active_promo_type = p_type
                    try:
                        self.client.cancel_promotion_item(account_id, child_user_id, item_id, p_id, p_type)
                        self.log(f"第2步[取消旧活动]: 已成功退出旧活动 {p_id}，杜绝旧低价成交！")
                    except Exception as cancel_err:
                        self.log(f"第2步[取消旧活动]: 尝试退出活动异常(已忽略): {cancel_err}")

            # 步骤 3：按最新数据重新测算并提报
            pricing = calculate_deal_price(item_info, self.discount_percent)
            if pricing.eligible and active_promo_id and active_promo_type:
                try:
                    self.client.enroll_promotion_item(
                        account_id,
                        child_user_id,
                        item_id,
                        active_promo_id,
                        active_promo_type,
                        pricing.deal_price,
                    )
                    self.log(f"第3步[新价重报成功]: 目标净回款=${pricing.target_net}，已提报新活动价=${pricing.deal_price}")
                except Exception as enroll_err:
                    self.log(f"第3步[新价重报失败]: {enroll_err}")
            elif not pricing.eligible:
                self.log(f"第3步[跳过提报]: {pricing.skip_reason}，保持退出状态以保本！")
            else:
                self.log(f"第3步[已更新数据]: 算得新保护售价=${pricing.deal_price}，待下次批量活动报入。")

            self._ack_event(event_id, claim_token, status="completed")

        except Exception as proc_err:
            self.log(f"处理商品 {item_id} 异常: {proc_err}")
            self._ack_event(event_id, claim_token, status="failed", reason=str(proc_err))

    def _resolve_route(self, remote_user_id: str, item_id: str) -> tuple[str | None, str | None, str | None]:
        """Match remote_user_id or item_id to account_id, child_user_id, site_id."""
        accounts = self.auth.list_accounts()
        # Direct match by parent account id
        for acc in accounts:
            acc_id = acc["account_id"]
            if acc_id == remote_user_id:
                sites = self.auth.list_sites(acc_id)
                if sites:
                    return acc_id, sites[0]["child_user_id"], sites[0]["site_id"]

        # Match by child_user_id across sites
        for acc in accounts:
            acc_id = acc["account_id"]
            sites = self.auth.list_sites(acc_id)
            for s in sites:
                if s["child_user_id"] == remote_user_id:
                    return acc_id, s["child_user_id"], s["site_id"]

        # Fallback to first active account
        if accounts:
            first_acc = accounts[0]["account_id"]
            sites = self.auth.list_sites(first_acc)
            if sites:
                return first_acc, sites[0]["child_user_id"], sites[0]["site_id"]

        return None, None, None

    def _claim_event(self) -> dict[str, Any] | None:
        """Call claim endpoint to retrieve one pending notification."""
        try:
            req = urllib.request.Request(self.claim_url, headers={"Accept": "application/json"}, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("event") or (data if data.get("event_id") else None)
        except Exception:
            return None

    def _ack_event(self, event_id: str, claim_token: str, status: str = "completed", reason: str | None = None) -> None:
        """Send ack to remove the message from the queue."""
        if not event_id:
            return
        payload = {
            "event_id": event_id,
            "claim_token": claim_token,
            "status": status,
            "reason": reason,
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.ack_url,
                data=data,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception:
            pass
