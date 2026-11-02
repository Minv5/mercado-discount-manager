from __future__ import annotations

import datetime
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)

PUSHPLUS_API_URL = "https://www.pushplus.plus/send"


class PushService:
    """Manages real-time mobile push notifications for orders, pre-sales questions,

    post-sales buyer messages, and dispute claims via PushPlus (WeChat).
    """

    _instance: PushService | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.enabled: bool = False
        self.token: str = ""
        self.notify_orders: bool = True
        self.notify_questions: bool = True
        self.notify_messages: bool = True
        self.notify_claims: bool = True

        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="PushNotifyWorker")
        self._dedup_cache: dict[str, float] = {}
        self._dedup_lock = threading.Lock()
        self.log_callback: Callable[[str], None] | None = None

    @classmethod
    def instance(cls) -> PushService:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def update_config(self, settings: dict[str, Any]) -> None:
        """Update active push configuration from settings dictionary."""
        self.enabled = bool(settings.get("pushEnabled", False))
        self.token = str(settings.get("pushToken") or "").strip()
        self.notify_orders = bool(settings.get("pushNotifyOrders", True))
        self.notify_questions = bool(settings.get("pushNotifyQuestions", True))
        self.notify_messages = bool(settings.get("pushNotifyMessages", True))
        self.notify_claims = bool(settings.get("pushNotifyClaims", True))

    def _is_duplicate(self, dedup_key: str, ttl_seconds: float = 60.0) -> bool:
        """Check and update deduplication memory cache to avoid alert spam."""
        now = time.time()
        with self._dedup_lock:
            # Clean expired keys
            expired = [k for k, ts in self._dedup_cache.items() if now - ts > 300.0]
            for k in expired:
                self._dedup_cache.pop(k, None)

            last_time = self._dedup_cache.get(dedup_key)
            if last_time is not None and (now - last_time) < ttl_seconds:
                return True
            self._dedup_cache[dedup_key] = now
            return False

    def send_pushplus(
        self,
        token: str,
        title: str,
        content: str,
        template: str = "html",
        topic: str = "",
        timeout: float = 6.0,
    ) -> tuple[bool, str]:
        """Synchronously send a notification via PushPlus API.

        Returns (success: bool, message: str).
        """
        clean_token = token.strip()
        if not clean_token:
            return False, "PushPlus Token 未配置"

        payload = {
            "token": clean_token,
            "title": title[:100],
            "content": content,
            "template": template,
        }
        if topic:
            payload["topic"] = topic

        try:
            req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                PUSHPLUS_API_URL,
                data=req_data,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "MercadoDiscountManager/Desktop",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_text = resp.read().decode("utf-8")
                res = json.loads(resp_text)
                code = res.get("code")
                msg = str(res.get("msg") or "")
                # PushPlus returns code 200 on success
                if code == 200:
                    return True, "推送成功"
                return False, f"PushPlus 返回异常: [{code}] {msg}"
        except urllib.error.HTTPError as e:
            raw_err = e.read().decode("utf-8", errors="replace")
            return False, f"网络请求失败 HTTP {e.code}: {raw_err}"
        except urllib.error.URLError as e:
            return False, f"无法连接推送服务器: {e.reason}"
        except Exception as e:
            return False, f"推送出错: {str(e)}"

    def send_test_push(self, token: str) -> tuple[bool, str]:
        """Send a formatted test notification to verify user's token and phone reception."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = "【美客多折扣管家】手机推送测试成功"
        content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; padding: 12px; background: #fdfdfd; border-radius: 8px; border: 1px solid #e0e0e0;">
            <h3 style="color: #2e7d32; margin-top: 0; margin-bottom: 8px;">✅ 微信消息推送连接成功！</h3>
            <p style="color: #424242; font-size: 14px; margin: 4px 0;">这是一条来自<b>美客多折扣管家</b>的测试推送消息。</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 10px 0;" />
            <p style="color: #616161; font-size: 13px; margin: 3px 0;"><b>测试时间：</b>{now_str}</p>
            <p style="color: #616161; font-size: 13px; margin: 3px 0;"><b>推送状态：</b>通道通畅，手机可实时接收消息</p>
            <p style="color: #616161; font-size: 13px; margin: 3px 0;"><b>涵盖范围：</b>新订单、买家售前咨询、售后私信、索赔纠纷告警</p>
            <p style="color: #9e9e9e; font-size: 12px; margin-top: 10px; margin-bottom: 0;">美客多折扣管家 · 自动化生产力守护</p>
        </div>
        """
        return self.send_pushplus(token, title, content, template="html")

    def _dispatch_async(self, dedup_key: str, title: str, content: str) -> None:
        """Asynchronously dispatch notification if enabled and token present."""
        if not self.enabled or not self.token:
            return
        if self._is_duplicate(dedup_key):
            return

        def _worker() -> None:
            try:
                ok, err = self.send_pushplus(self.token, title, content, template="html")
                if not ok:
                    logger.warning("PushPlus async send failed: %s", err)
                    if self.log_callback:
                        self.log_callback(f"[推送失败] {err}")
            except Exception as ex:
                logger.error("Error in push notification worker: %s", ex)

        self._executor.submit(_worker)

    # --- Domain Specific Notification Methods ---

    def notify_order(
        self,
        store_name: str,
        site_id: str,
        order_id: str,
        item_title: str = "",
        total_amount: float | None = None,
        currency_id: str = "",
        buyer_nickname: str = "",
    ) -> None:
        """Trigger instant mobile push for newly placed orders."""
        if not self.notify_orders:
            return
        dedup_key = f"order:{order_id}"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = f"🛒【新订单提醒】{store_name or '店铺'} ({site_id or '拉美'})"

        amount_str = f"{currency_id} {total_amount:,.2f}" if total_amount is not None else "以平台结算为准"
        item_row = f'<p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>商品：</b>{item_title}</p>' if item_title else ""
        buyer_row = f'<p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>买家：</b>{buyer_nickname}</p>' if buyer_nickname else ""

        content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 14px; background: #ffffff; border-radius: 8px; border: 1px solid #e0e0e0;">
            <h3 style="color: #1565c0; margin-top: 0; margin-bottom: 10px;">🛒 收到美客多新订单！</h3>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>店铺：</b>{store_name} ({site_id})</p>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>订单号：</b><span style="font-family: monospace; color: #d32f2f;">{order_id}</span></p>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>订单金额：</b><b>{amount_str}</b></p>
            {item_row}
            {buyer_row}
            <p style="color: #616161; font-size: 12px; margin: 4px 0;"><b>下单时间：</b>{now_str}</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 10px 0;" />
            <p style="color: #2e7d32; font-size: 12px; margin: 0; font-weight: bold;">💡 提示：请及时登录客户端或后台核对打单发货！</p>
        </div>
        """
        self._dispatch_async(dedup_key, title, content)

    def notify_question(
        self,
        store_name: str,
        site_id: str,
        item_id: str,
        question_text: str = "",
        item_title: str = "",
    ) -> None:
        """Trigger instant mobile push for pre-sale product questions from potential buyers."""
        if not self.notify_questions:
            return
        dedup_key = f"question:{item_id}:{question_text[:20]}"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = f"❓【售前咨询提醒】{store_name or '店铺'} ({site_id or '拉美'})"

        content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 14px; background: #ffffff; border-radius: 8px; border: 1px solid #e0e0e0;">
            <h3 style="color: #e65100; margin-top: 0; margin-bottom: 10px;">❓ 收到买家新提问！</h3>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>店铺：</b>{store_name} ({site_id})</p>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>商品 ID：</b><span style="font-family: monospace;">{item_id}</span></p>
            {f'<p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>商品标题：</b>{item_title}</p>' if item_title else ''}
            <div style="background: #fff8e1; padding: 8px 10px; border-radius: 4px; border-left: 3px solid #ffa000; margin: 8px 0;">
                <p style="color: #5d4037; font-size: 13px; margin: 0;"><b>买家提问：</b>{question_text or '（买家咨询了商品详情）'}</p>
            </div>
            <p style="color: #616161; font-size: 12px; margin: 4px 0;"><b>咨询时间：</b>{now_str}</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 10px 0;" />
            <p style="color: #e65100; font-size: 12px; margin: 0; font-weight: bold;">💡 提示：极速回复提问可大幅提升店铺下单转化率！</p>
        </div>
        """
        self._dispatch_async(dedup_key, title, content)

    def notify_message(
        self,
        store_name: str,
        site_id: str,
        order_id: str,
        message_text: str = "",
        buyer_name: str = "",
    ) -> None:
        """Trigger instant mobile push for post-sale buyer private messages."""
        if not self.notify_messages:
            return
        dedup_key = f"message:{order_id}:{message_text[:20]}"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = f"💬【售后私信提醒】{store_name or '店铺'} ({site_id or '拉美'})"

        content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 14px; background: #ffffff; border-radius: 8px; border: 1px solid #e0e0e0;">
            <h3 style="color: #00838f; margin-top: 0; margin-bottom: 10px;">💬 收到买家售后新私信！</h3>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>店铺：</b>{store_name} ({site_id})</p>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>关联订单：</b><span style="font-family: monospace;">{order_id}</span></p>
            {f'<p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>买家姓名：</b>{buyer_name}</p>' if buyer_name else ''}
            <div style="background: #e0f7fa; padding: 8px 10px; border-radius: 4px; border-left: 3px solid #00acc1; margin: 8px 0;">
                <p style="color: #006064; font-size: 13px; margin: 0;"><b>最新消息：</b>{message_text or '（买家发来了订单售后私信）'}</p>
            </div>
            <p style="color: #616161; font-size: 12px; margin: 4px 0;"><b>接收时间：</b>{now_str}</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 10px 0;" />
            <p style="color: #00838f; font-size: 12px; margin: 0; font-weight: bold;">💡 提示：及时沟通安抚买家有助于避免售后升级为索赔！</p>
        </div>
        """
        self._dispatch_async(dedup_key, title, content)

    def notify_claim(
        self,
        store_name: str,
        site_id: str,
        order_id: str,
        claim_id: str = "",
        reason: str = "",
        stage: str = "",
    ) -> None:
        """Trigger high-priority alert mobile push for dispute claims and complaints."""
        if not self.notify_claims:
            return
        dedup_key = f"claim:{claim_id or order_id}"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = f"⚠️【索赔纠纷告警】{store_name or '店铺'} ({site_id or '拉美'})"

        content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 14px; background: #fff5f5; border-radius: 8px; border: 1px solid #ffcdd2;">
            <h3 style="color: #c62828; margin-top: 0; margin-bottom: 10px;">⚠️ 买家发起售后索赔 / 投诉！</h3>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>店铺：</b>{store_name} ({site_id})</p>
            <p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>关联订单：</b><span style="font-family: monospace; color: #b71c1c; font-weight: bold;">{order_id}</span></p>
            {f'<p style="color: #424242; font-size: 13px; margin: 4px 0;"><b>索赔编号：</b>{claim_id}</p>' if claim_id else ''}
            <div style="background: #ffebee; padding: 8px 10px; border-radius: 4px; border-left: 3px solid #e53935; margin: 8px 0;">
                <p style="color: #b71c1c; font-size: 13px; margin: 0;"><b>索赔原因：</b>{reason or '买家发起了纠纷投诉，请尽快处理'}</p>
                {f'<p style="color: #b71c1c; font-size: 12px; margin: 4px 0 0 0;"><b>当前状态：</b>{stage}</p>' if stage else ''}
            </div>
            <p style="color: #616161; font-size: 12px; margin: 4px 0;"><b>告警时间：</b>{now_str}</p>
            <hr style="border: none; border-top: 1px solid #ffcdd2; margin: 10px 0;" />
            <p style="color: #c62828; font-size: 12px; margin: 0; font-weight: bold;">🚨 紧急提醒：请务必在美客多平台 SLA 规定时限前登录处理，防止店铺信誉评分受损！</p>
        </div>
        """
        self._dispatch_async(dedup_key, title, content)
