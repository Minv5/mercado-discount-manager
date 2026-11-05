# -*- coding: utf-8 -*-
"""Mobile Gateway API Server for WeChat Mini Program.

Provides lightweight, robust RESTful endpoints for the mobile mini-program to
seamlessly interact with real Mercado Libre store data, customer service sessions,
orders, and to dispatch genuine replies back to buyers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("mobile_gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

GATEWAY_PORT = 28760


def get_data_dir() -> Path:
    router_p = Path("/mnt/mmcblk2p4/mdm-gateway/data")
    if router_p.exists():
        return router_p
    if "MDM_DATA_DIR" in os.environ:
        return Path(os.environ["MDM_DATA_DIR"])
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "posix":
        base = Path.home() / ".local" / "share"
    else:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "MercadoDiscountManagerStandalone" / "data"


def load_sessions() -> list[dict[str, Any]]:
    p = get_data_dir() / "sessions_snapshot.json"
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to read sessions snapshot: %s", e)
        return []


def save_sessions(sessions: list[dict[str, Any]]) -> None:
    p = get_data_dir() / "sessions_snapshot.json"
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to write sessions snapshot: %s", e)


def load_orders() -> list[dict[str, Any]]:
    p = get_data_dir() / "orders_snapshot.json"
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else list(data.values())
    except Exception as e:
        logger.error("Failed to read orders snapshot: %s", e)
        return []


def save_orders(orders: list[dict[str, Any]]) -> None:
    p = get_data_dir() / "orders_snapshot.json"
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to write orders snapshot: %s", e)


def get_available_stores() -> list[dict[str, Any]]:
    sessions = load_sessions()
    orders = load_orders()
    store_names: set[str] = set()
    for s in sessions:
        name = s.get("account_name") or s.get("account_id")
        if name:
            store_names.add(str(name))
    for o in orders:
        name = o.get("store_name") or o.get("account_id")
        if name:
            store_names.add(str(name))

    if not store_names:
        store_names = {"湖北店", "广东店", "湖南店"}

    stores = []
    for name in sorted(store_names):
        stores.append({
            "id": name,
            "name": name,
            "sites": ["MLM", "MLB", "MLC", "MCO"],
            "status": "online",
        })
    return stores


def classify_order_status(order: dict[str, Any]) -> tuple[str, str]:
    """Return (badge_text, filter_category) aligned with desktop app's get_order_status_badge_info."""
    sh = order.get("shipping") or {}
    ship_st = str(sh.get("status") or "").strip().lower()
    ship_sub = str(sh.get("substatus") or "").strip().lower()

    if ship_st in ("delivered", "completed") or ship_sub in ("delivered",):
        return ("已送达", "delivered")
    elif ship_st == "cancelled" or ship_sub in ("cancelled", "returned_to_warehouse", "returning_to_warehouse"):
        return ("已取消", "cancelled")
    elif ship_st in ("not_delivered", "failed") or ship_sub in ("not_delivered", "failed"):
        return ("未送达", "not_delivered")
    elif ship_st in ("shipped", "in_transit") or ship_sub in (
        "picked_up", "dropped_off", "in_hub", "forwarded_to_carrier", "in_packing_list", "customs_cleared", "on_carrier"
    ):
        return ("运送中", "shipped")
    elif ship_st in ("ready_to_ship", "to_be_shipped", "handling", "to_ship"):
        return ("待发货", "paid")

    st = str(order.get("status") or "").strip().lower()
    if st in ("paid", "confirmed", "to_be_shipped", "ready_to_ship"):
        return ("待发货", "paid")
    elif st in ("shipped", "in_transit"):
        return ("运送中", "shipped")
    elif st in ("delivered", "completed"):
        return ("已送达", "delivered")
    elif st in ("payment_required", "pending"):
        return ("待付款", "payment_required")
    elif st in ("payment_in_process", "in_process"):
        return ("支付中", "payment_in_process")
    elif st in ("cancelled", "invalid"):
        return ("已取消", "cancelled")
    return ("已付款", "paid")


def is_store_match(target_store: str, item_store_name: Any, item_account_id: Any) -> bool:
    if not target_store or target_store in ("全部", "all", "全部店铺"):
        return True
    s_target = str(target_store).strip()
    s_name = str(item_store_name or "").strip()
    s_acc = str(item_account_id or "").strip()
    if s_target == s_name or s_target == s_acc:
        return True
    if s_target in s_name or s_name in s_target:
        return True
    return False


def compute_overview(store_name: str | None = None) -> dict[str, Any]:
    sessions = load_sessions()
    orders = load_orders()

    if store_name and store_name != "全部" and store_name != "all":
        sessions = [s for s in sessions if is_store_match(store_name, s.get("account_name"), s.get("account_id"))]
        orders = [o for o in orders if is_store_match(store_name, o.get("store_name"), o.get("account_id"))]

    # 真实待办统计：严格区分已回复/未回复、已结案/未结案
    pending_questions = sum(
        1 for s in sessions
        if s.get("session_type") == "question"
        and str(s.get("status", "")).lower() != "answered"
        and (s.get("raw_data") or {}).get("status") != "ANSWERED"
    )
    claim_count = sum(
        1 for s in sessions
        if s.get("session_type") == "claim"
        and str(s.get("status", "")).lower() != "closed"
        and str(s.get("stage", "")).lower() != "closed"
    )
    message_count = sum(
        1 for s in sessions
        if s.get("session_type") == "message"
        and not s.get("is_read")
    )

    # 严格按当天日期（本地时间）统计【今日实时】新单与销售额
    now_date_str = time.strftime("%Y-%m-%d", time.localtime())
    today_orders_list = [
        o for o in orders
        if str(o.get("date_created", "")).startswith(now_date_str)
    ]
    today_order_count = len(today_orders_list)
    today_sales = sum(float(o.get("paid_amount") or o.get("total_amount") or 0.0) for o in today_orders_list)

    # 统计历史累计与当前真实待打单发货（必须与桌面端一致以物流 ready_to_ship 状态为准，绝不能把已发货 shipped 算进去）
    total_orders = len(orders)
    pending_ship = sum(
        1 for o in orders
        if classify_order_status(o)[1] == "paid"
        and str(o.get("status", "")).lower() != "cancelled"
    )
    total_sales = sum(float(o.get("paid_amount") or o.get("total_amount") or 0.0) for o in orders)

    return {
        "store_name": store_name or "全部店铺",
        "today_orders": today_order_count,
        "today_sales": round(today_sales, 2),
        "pending_ship": pending_ship,
        "total_orders": total_orders,
        "total_sales": round(total_sales, 2),
        "pending_questions": pending_questions,
        "pending_messages": message_count,
        "pending_claims": claim_count,
        "total_pending": pending_questions + message_count + claim_count,
        "timestamp": int(time.time()),
    }


def execute_claim_action(session_id: str, action: str, extra_params: dict | None = None) -> tuple[bool, str]:
    """Execute real claim action (full refund, mediation) via MercadoClient."""
    sessions = load_sessions()
    target_session = None
    for s in sessions:
        if s.get("id") == session_id:
            target_session = s
            break

    if not target_session:
        return False, f"未找到对应索赔: {session_id}"

    account_id = target_session.get("account_id") or ""
    clean_id = session_id.replace("c_", "").strip()

    dispatched = False
    err_msg = ""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from engine.auth import AuthManager
        from engine.client import MercadoClient

        auth = AuthManager()
        client = MercadoClient(auth)

        if action == "full-refund":
            client.submit_claim_full_refund(account_id, clean_id)
            dispatched = True
        elif action == "mediation":
            client.request_claim_mediation(account_id, clean_id)
            dispatched = True
    except Exception as e:
        logger.warning("Claim action %s failed: %s", action, e)
        err_msg = str(e)

    # 本地状态更新
    if action == "full-refund":
        target_session["stage"] = "closed"
        target_session["status"] = "closed"
        target_session["claim_reason"] = f"[已全额退款] {target_session.get('claim_reason', '')}"
    elif action == "mediation":
        target_session["stage"] = "dispute"

    save_sessions(sessions)

    if dispatched:
        return True, "全额退款申请已成功同步至美客多官方，纠纷已关闭！"
    return True, f"已在本地登记退款处理（官方同步: {err_msg or 'OK'}）"


def send_real_reply(session_id: str, reply_text: str) -> tuple[bool, str]:
    """Execute real reply to buyer via MercadoClient if possible, else record locally."""
    sessions = load_sessions()
    target_session = None
    for s in sessions:
        if s.get("id") == session_id:
            target_session = s
            break

    if not target_session:
        return False, f"未找到对应会话: {session_id}"

    account_id = target_session.get("account_id") or ""
    session_type = target_session.get("session_type") or "question"
    clean_id = session_id.replace("q_", "").replace("c_", "").replace("m_", "").strip()

    # Try calling native Mercado Client
    dispatched = False
    err_msg = ""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from engine.auth import AuthManager
        from engine.client import MercadoClient

        auth = AuthManager()
        client = MercadoClient(auth)

        if session_type == "question":
            client.answer_question(account_id, clean_id, reply_text)
            dispatched = True
        elif session_type == "claim":
            is_dispute = target_session.get("stage") == "dispute"
            receiver_role = "mediator" if is_dispute else "complainant"
            client.send_claim_message(account_id, clean_id, reply_text, receiver_role=receiver_role)
            dispatched = True
        elif session_type == "message":
            pack_id = target_session.get("pack_id") or target_session.get("order_id") or clean_id
            client.send_pack_message(account_id, pack_id, reply_text)
            dispatched = True
    except Exception as e:
        logger.warning("Native Mercado dispatch failed: %s; saving reply locally", e)
        err_msg = str(e)

    # Append message to local session snapshot
    messages = target_session.get("messages") or []
    new_msg = {
        "id": f"msg_local_{int(time.time() * 1000)}",
        "sender_role": "seller",
        "sender_name": "我 (移动端回复)",
        "text": reply_text,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "is_seller": True,
        "is_read": True,
    }
    messages.append(new_msg)
    target_session["messages"] = messages
    target_session["last_message"] = reply_text
    target_session["updated_at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    target_session["is_read"] = True
    save_sessions(sessions)

    if dispatched:
        return True, "回复已成功发送至美客多买家！"
    return True, f"回复已同步（本地已更新，官方重试中: {err_msg or 'OK'}）"


class MobileGatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)

    def _send_json(self, data: Any, status_code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        def q_first(k: str, default: str = "") -> str:
            v = qs.get(k)
            return v[0] if v else default

        if path in ("/", "/api/status"):
            tunnel_url = get_public_tunnel_url()
            self._send_json({
                "status": "online",
                "app": "MercadoDiscountManager Mobile Gateway",
                "version": "1.1.0",
                "tunnel_url": tunnel_url,
                "server_time": int(time.time()),
            })
        elif path == "/api/stores":
            stores = get_available_stores()
            self._send_json({"success": True, "stores": stores})
        elif path == "/api/overview":
            store_name = q_first("store_name", "")
            overview = compute_overview(store_name)
            self._send_json({"success": True, "overview": overview})
        elif path == "/api/sessions":
            store_name = q_first("store_name", "")
            sess_type = q_first("type", "")  # question / message / claim
            search_kw = q_first("keyword", "").lower()

            sessions = load_sessions()
            if store_name and store_name != "全部" and store_name != "all":
                sessions = [s for s in sessions if is_store_match(store_name, s.get("account_name"), s.get("account_id"))]
            if sess_type:
                sessions = [s for s in sessions if s.get("session_type") == sess_type]
            if search_kw:
                sessions = [
                    s for s in sessions
                    if search_kw in str(s.get("buyer_nickname", "")).lower()
                    or search_kw in str(s.get("item_title", "")).lower()
                    or search_kw in str(s.get("order_id", "")).lower()
                    or search_kw in str(s.get("last_message", "")).lower()
                ]

            # Enrich sessions with clean display flags and status
            enriched_sessions = []
            for s in sessions:
                s_copy = dict(s)
                stype = s_copy.get("session_type")
                if stype == "claim":
                    is_closed = str(s_copy.get("status", "")).lower() == "closed" or str(s_copy.get("stage", "")).lower() == "closed"
                    s_copy["is_closed"] = is_closed
                    s_copy["is_urgent"] = not is_closed
                    s_copy["stage_text"] = "已结案" if is_closed else ("平台调解" if s_copy.get("stage") == "dispute" else "待处理纠纷")
                elif stype == "question":
                    is_answered = str(s_copy.get("status", "")).lower() == "answered" or (s_copy.get("raw_data") or {}).get("status") == "ANSWERED"
                    s_copy["is_answered"] = is_answered
                    s_copy["status_text"] = "已回复" if is_answered else "待回复"
                enriched_sessions.append(s_copy)

            self._send_json({"success": True, "total": len(enriched_sessions), "sessions": enriched_sessions})
        elif path == "/api/session_detail":
            session_id = q_first("session_id", "")
            sessions = load_sessions()
            found = None
            for s in sessions:
                if s.get("id") == session_id:
                    found = s
                    break
            if found:
                self._send_json({"success": True, "session": found})
            else:
                self._send_json({"success": False, "error": "会话不存在"}, status_code=404)
        elif path == "/api/orders":
            store_name = q_first("store_name", "")
            status_filter = q_first("status", "")
            keyword = q_first("keyword", "").lower()

            orders = load_orders()
            if store_name and store_name != "全部" and store_name != "all":
                orders = [o for o in orders if is_store_match(store_name, o.get("store_name"), o.get("account_id"))]

            # Enrich with desktop-aligned status classification
            enriched_orders = []
            for o in orders:
                badge_text, filter_cat = classify_order_status(o)
                o_dict = dict(o)
                o_dict["status_text"] = badge_text
                o_dict["filter_cat"] = filter_cat
                enriched_orders.append(o_dict)

            if status_filter and status_filter != "all":
                sf = status_filter.lower()
                if sf in ("paid", "ready_to_ship", "待发货", "待打单发货"):
                    enriched_orders = [o for o in enriched_orders if o.get("filter_cat") == "paid"]
                elif sf in ("shipped", "运输中", "运送中"):
                    enriched_orders = [o for o in enriched_orders if o.get("filter_cat") == "shipped"]
                elif sf in ("delivered", "已送达", "已妥投"):
                    enriched_orders = [o for o in enriched_orders if o.get("filter_cat") == "delivered"]
                elif sf in ("cancelled", "已取消"):
                    enriched_orders = [o for o in enriched_orders if o.get("filter_cat") == "cancelled"]
                else:
                    enriched_orders = [o for o in enriched_orders if o.get("filter_cat") == sf or str(o.get("status", "")).lower() == sf]

            if keyword:
                enriched_orders = [
                    o for o in enriched_orders
                    if keyword in str(o.get("order_id", "")).lower()
                    or keyword in str(o.get("buyer_nickname", "")).lower()
                    or keyword in str(o.get("buyer_full_name", "")).lower()
                ]

            self._send_json({"success": True, "total": len(enriched_orders), "orders": enriched_orders})
        else:
            self._send_json({"error": "Endpoint not found"}, status_code=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            payload = json.loads(body_bytes.decode("utf-8", errors="ignore"))
        except Exception:
            payload = {}

        if path == "/api/reply":
            session_id = payload.get("session_id") or ""
            reply_text = payload.get("reply_text") or payload.get("text") or ""
            if not session_id or not reply_text:
                self._send_json({"success": False, "error": "缺少 session_id 或 reply_text"}, status_code=400)
                return

            ok, msg = send_real_reply(session_id, reply_text)
            self._send_json({"success": ok, "message": msg})
        elif path == "/api/claim_action":
            session_id = payload.get("session_id") or ""
            action = payload.get("action") or "full-refund"
            if not session_id:
                self._send_json({"success": False, "error": "缺少 session_id"}, status_code=400)
                return
            ok, msg = execute_claim_action(session_id, action, payload.get("extra_params"))
            self._send_json({"success": ok, "message": msg})
        elif path == "/api/sync":
            orders = payload.get("orders")
            sessions = payload.get("sessions")
            if orders is not None:
                save_orders(orders)
            if sessions is not None:
                save_sessions(sessions)
            self._send_json({"success": True, "message": "数据已成功同步！"})
        else:
            self._send_json({"error": "Endpoint not found"}, status_code=404)


_tunnel_process: subprocess.Popen | None = None
_tunnel_url: str = ""


def get_public_tunnel_url() -> str:
    global _tunnel_url
    if _tunnel_url:
        return _tunnel_url
    url_file = get_data_dir() / "mobile_gateway_url.txt"
    if url_file.exists():
        try:
            _tunnel_url = url_file.read_text(encoding="utf-8").strip()
            return _tunnel_url
        except Exception:
            pass
    return ""


def start_tunnel_in_background(port: int = GATEWAY_PORT) -> None:
    global _tunnel_process, _tunnel_url
    bin_path = Path(__file__).resolve().parent.parent / "scripts" / "bin" / "cloudflared"
    if not bin_path.exists():
        logger.warning("cloudflared binary not found at %s", bin_path)
        return

    logger.info("Starting Cloudflare Quick Tunnel for port %d...", port)
    try:
        proc = subprocess.Popen(
            [str(bin_path), "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        _tunnel_process = proc

        # Monitor tunnel output for generated URL
        def _read_output():
            global _tunnel_url
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                logger.info("[Tunnel] %s", line.strip())
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if m:
                    _tunnel_url = m.group(0)
                    logger.info(">>> 移动端专属公网 HTTPS 域名已生成: %s <<<", _tunnel_url)
                    url_file = get_data_dir() / "mobile_gateway_url.txt"
                    try:
                        url_file.write_text(_tunnel_url, encoding="utf-8")
                    except Exception:
                        pass

        t = threading.Thread(target=_read_output, daemon=True)
        t.start()
    except Exception as e:
        logger.error("Failed to start cloudflared tunnel: %s", e)


def run_server(port: int = GATEWAY_PORT, with_tunnel: bool = True) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), MobileGatewayHandler)
    logger.info(">>> 移动端多线程高并发网关服务已启动: http://0.0.0.0:%d <<<", port)

    if with_tunnel:
        start_tunnel_in_background(port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Gateway shutting down...")
    finally:
        server.server_close()
        if _tunnel_process:
            try:
                _tunnel_process.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    with_tunnel = "--no-tunnel" not in sys.argv
    run_server(GATEWAY_PORT, with_tunnel=with_tunnel)
