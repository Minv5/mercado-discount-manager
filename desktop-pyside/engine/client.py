from __future__ import annotations

import http.client
import json
import logging
import os
import queue
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
from typing import Any

from .auth import AuthManager

logger = logging.getLogger(__name__)


class HTTPSConnectionPool:
    """Thread-safe persistent HTTPS connection pool supporting HTTP Keep-Alive."""

    def __init__(self, host: str = "api.mercadolibre.com", timeout: int = 30, max_size: int = 16):
        self.host = host
        self.timeout = timeout
        self.max_size = max_size
        self._pool: queue.LifoQueue[http.client.HTTPSConnection] = queue.LifoQueue(maxsize=max_size)
        self._ctx = ssl.create_default_context()

    def get_connection(self) -> http.client.HTTPSConnection:
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            return http.client.HTTPSConnection(self.host, timeout=self.timeout, context=self._ctx)

    def release_connection(self, conn: http.client.HTTPSConnection, close: bool = False) -> None:
        if close:
            try:
                conn.close()
            except Exception:
                pass
            return
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass


def format_clean_api_error(status_code: int | None, raw_msg: str) -> str:
    """Format technical Mercado Libre error into concise human-readable Chinese."""
    text = str(raw_msg or "").strip()
    code = int(status_code) if status_code is not None else 0

    # 预处理：剥离重复的前缀与后缀，防止多层捕获重复叠加
    clean = re.sub(r"^(?:美客多接口异常:\s*|美客多 API 报错\s*(?:\(\d+\))?:\s*)+", "", text).strip()
    clean = re.sub(r"(?:\s*\(\d+\))+\s*$", "", clean).strip()
    clean = re.sub(r"^\[([A-Z0-9_-]+)$", r"\1", clean).strip()
    text_lower = clean.lower()

    # 1. 500 / internal error
    if code == 500 or "oops! something went wrong" in text_lower or "internal_error" in text_lower or "internal server error" in text_lower:
        return "平台服务繁忙，请稍后重试 (500)"

    # 2. 502 / 503 / 504 / Gateway & Maintenance
    if code in (502, 503, 504) or any(k in text_lower for k in ("bad gateway", "service unavailable", "gateway timeout")):
        return f"平台网关超时/维护中 ({code or 503})"

    # 3. 400 Activity in Pending status
    if any(k in text_lower for k in ("status pending", "pending status")):
        return "活动处于审核生效中，平台禁止退出"

    # 3.1 400 / 409 Activity Locked
    if (
        "lockedentityexception" in text_lower
        or "offer locked" in text_lower
        or any(k in text_lower for k in ("cannot be deleted", "cannot remove", "not allowed to remove", "is locked", "is not allowed to delete", "cannot delete"))
    ):
        return "活动已锁定，平台禁止退出"

    # 4. 400 Minimum Discount Percent
    if "minimum_discount_percent" in text_lower or "discount final price must be more than" in text_lower:
        return "折扣幅度未达平台最低门槛(至少5%)"

    # 5. 400 Item status not allowed
    if "item status is not allowed (closed)" in text_lower or "status is not allowed (closed)" in text_lower:
        return "商品已下架关闭(禁止提报)"
    if "item status is not allowed (under_review)" in text_lower or "status is not allowed (under_review)" in text_lower:
        return "商品审核中(禁止提报)"
    if "item status is not allowed" in text_lower or "status is not allowed" in text_lower:
        return "商品当前状态禁止提报"

    # 6. 400 User not allowed to create promotion (如 Full 仓履约子账号)
    if "user is not allowed to create this type of promotion" in text_lower:
        return "该子账号受限，无权自建此类型活动(已跳过)"

    # 7. 400 Credibility
    if "error_credibility_original_price" in text_lower or "original price is not credible" in text_lower:
        return "原价未达平台公信力门槛"
    if "error_credibility_discounted_price" in text_lower or "price is not credible" in text_lower:
        return "折后价未达近期成交价门槛"
    if "rest_client_error" in text_lower and "credibility" in text_lower:
        return "平台公信力校验接口超时/异常"

    # 7.1 400 Price Greater Than Current / Disallowed Price Increase
    if "price_gt_current" in text_lower or "new offer price is greater than actual offer" in text_lower:
        return "新折扣价高于当前活动生效价(禁止涨价)"
    if "new deal_price must be lower than current deal_price" in text_lower:
        return "秒杀价必须低于当前生效价(禁止涨价)"

    # 7.2 400 Condition must be new
    if "item condition must be new" in text_lower or "condition must be new" in text_lower:
        return "商品非全新成色(二手/翻新禁止参与)"

    # 7.3 400 Item prices data unavailable
    if "item prices data is unavailable" in text_lower:
        return "平台价格数据同步中或缺失 (400)"

    # 7.4 400 No offers found
    if "no offers found for item" in text_lower:
        return "商品无有效报价或未上架 (400)"

    # 7.5 400 Invalid promotion type
    if "invalid promotion type" in text_lower:
        return "活动类型不支持或无效 (400)"

    # 7.6 408 Error getting item
    if code == 408 or "error getting item" in text_lower:
        return "商品详情读取超时 (408)"

    # 7.7 400 Promotion deadline / finish_date
    if (
        any(k in text_lower for k in ("deadline", "finish_date", "already finished", "promotion has ended"))
        or (code == 400 and re.search(r"\b202\d-\d{2}-\d{2}t\d{2}:\d{2}", text_lower))
    ):
        return "已过活动提报截止时间"

    # 8. 400 Eligibility
    if "item_not_eligible" in text_lower:
        return "未达活动受邀门槛"

    # 9. Already in promotion
    if "already in promotion" in text_lower or "already" in text_lower:
        return "已在活动中(自动跳过)"

    # 10. Required promotion_type
    if "promotion_type is required" in text_lower:
        return "缺少活动类型参数"

    # 11. Active item not modifiable / deletable
    if "deleted is not modifiable" in text_lower:
        return "商品活跃/出单中，禁止删除"

    # 12. 404 not CBT
    if "not a cbt item" in text_lower:
        return "非全球CBT商品 (404)"

    # 13. 404 not found
    if code == 404 or "not found" in text_lower or "not_found" in text_lower:
        return "商品或活动不存在 (404)"

    # 14. 403 User identification
    if "can not identify the user" in text_lower or "cannot identify the user" in text_lower:
        return "站点无权限或账号不匹配 (403)"

    # 15. 403 Forbidden / Access denied
    if code == 403 or "forbidden" in text_lower or "access_denied" in text_lower:
        return "站点访问受限 (403)"

    # 16. 401 Auth expired
    if code == 401 or any(k in text_lower for k in ("invalid_grant", "consumer_auth_required", "expired_token")):
        return "店铺授权已过期 (401)"

    # 17. 429 Rate limit
    if code == 429 or "too many requests" in text_lower or "rate_limit" in text_lower:
        return "平台限流，降速排队中 (429)"

    # 18. 422 Seller blocked
    if code == 422 or "seller is temporarily blocked" in text_lower:
        return "卖家账号此操作暂被平台限制 (422)"

    # 19. 409 Repeated failures
    if "this request has failed too many times" in text_lower:
        return "多次重复提报失败，平台已暂时拦截 (409)"

    # 20. Network error
    if any(
        k in text_lower
        for k in (
            "remotedisconnected",
            "connectionreset",
            "brokenpipe",
            "timeout",
            "timed out",
            "socket hang up",
            "cannotsendrequest",
            "connection refused",
            "no route to host",
            "unreachable",
            "host is down",
        )
    ):
        return "网络超时或中断"

    # Fallback: clean up noisy wrappers without truncating error description
    no_err_prefix = re.sub(r"^Errors?:\s*", "", clean).strip()
    sub_clean = re.sub(r"^[A-Z_]+\s*-\s*", "", no_err_prefix).strip()
    sub_clean = re.sub(r"^\[[A-Z0-9_-]+\]\s*-\s*", "", sub_clean).strip()
    sub_clean = sub_clean.strip("[]").strip()
    if sub_clean and not re.fullmatch(r"(?:(?:CBT|MLM|MLB|MCO|MLC|MLA)\d+|202\d-\d{2}-\d{2}T[\d:.Z+-]+)", sub_clean, re.IGNORECASE):
        clean = sub_clean
    elif no_err_prefix:
        clean = no_err_prefix
    if code > 0:
        return f"美客多接口异常: {clean} ({code})"
    return clean


def clean_error_message(err: Any) -> str:
    """Extract and format any exception or error string into clean human-readable text."""
    if err is None:
        return ""
    err_str = str(err).strip()
    code_match = re.search(r"\b(4\d\d|5\d\d)\b", err_str)
    code = int(code_match.group(1)) if code_match else None
    return format_clean_api_error(code, err_str)


class MercadoClient:
    API_BASE = "https://api.mercadolibre.com"

    def __init__(self, auth_manager: AuthManager | None = None, max_concurrency: int = 18):
        self.auth = auth_manager or AuthManager()
        self.max_concurrency_per_account = max_concurrency
        self._account_semaphores: dict[str, threading.Semaphore] = {}
        self._sem_lock = threading.Lock()
        parsed = urllib.parse.urlparse(self.API_BASE)
        self._host = parsed.netloc or "api.mercadolibre.com"
        self._conn_pool = HTTPSConnectionPool(host=self._host, timeout=30, max_size=max(36, max_concurrency * 4))

    def _get_semaphore(self, account_id: str) -> threading.Semaphore:
        clean_acc = str(account_id or "default").strip()
        with self._sem_lock:
            if clean_acc not in self._account_semaphores:
                self._account_semaphores[clean_acc] = threading.Semaphore(self.max_concurrency_per_account)
            return self._account_semaphores[clean_acc]

    def request(
        self,
        account_id: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        max_retries: int = 3,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to Mercado Libre API with Keep-Alive connection reuse, 429 retry and token auto-refresh."""
        token_info = self.auth.get_token(account_id)
        access_token = token_info.access_token

        query_str = ""
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean_params:
                query_str = "?" + urllib.parse.urlencode(clean_params)

        req_path = f"/{path.lstrip('/')}{query_str}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "version": "v2",
            "Connection": "keep-alive",
        }
        if extra_headers:
            headers.update(extra_headers)

        data = None
        if body is not None:
            if isinstance(body, bytes):
                data = body
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/octet-stream"
            else:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"

        with self._get_semaphore(account_id):
            for attempt in range(max_retries):
                conn = self._conn_pool.get_connection()
                try:
                    conn.request(method.upper(), req_path, body=data, headers=headers)
                    resp = conn.getresponse()
                    status_code = resp.status
                    is_close = (resp.getheader("Connection", "") or "").lower() == "close"
                    try:
                        raw_bytes = resp.read()
                    except http.client.IncompleteRead as inc_err:
                        is_close = True
                        raw_bytes = inc_err.partial or b""
                        decoded = raw_bytes.decode("utf-8", errors="replace").strip()
                        is_valid_json = False
                        if decoded:
                            try:
                                json.loads(decoded)
                                is_valid_json = True
                            except Exception:
                                pass
                        if not ((200 <= status_code < 300 and (not decoded or is_valid_json)) or (status_code >= 400 and is_valid_json)):
                            raise

                    raw = raw_bytes.decode("utf-8", errors="replace")
                    self._conn_pool.release_connection(conn, close=is_close)

                    if 200 <= status_code < 300:
                        if not raw.strip():
                            return {}
                        return json.loads(raw)

                    # Handle Token Expiration (401) -> refresh once and retry
                    if status_code == 401 and attempt < max_retries - 1:
                        new_token = self.auth.get_token(account_id, force_refresh=True)
                        headers["Authorization"] = f"Bearer {new_token.access_token}"
                        time.sleep(0.5)
                        continue

                    # Handle Rate Limit (429) -> exponential backoff (do NOT retry 409 conflict to avoid triggering anti-scraping blocks)
                    if status_code == 429 and attempt < max_retries - 1:
                        sleep_time = (2 ** attempt) * 1.5
                        time.sleep(sleep_time)
                        continue

                    # Handle Server Glitches (500, 502, 503, 504) -> exponential backoff retry
                    if status_code in (500, 502, 503, 504) and attempt < max_retries - 1:
                        sleep_time = (2 ** attempt) * 0.8
                        time.sleep(sleep_time)
                        continue

                    # Parse JSON error if possible
                    try:
                        err_json = json.loads(raw)
                        causes = err_json.get("cause") or []
                        if isinstance(causes, list) and causes:
                            cause_msgs = [str(c.get("message") or "") for c in causes if isinstance(c, dict) and c.get("message")]
                            detail = " | ".join(cause_msgs) if cause_msgs else ""
                            base_msg = err_json.get("message") or err_json.get("error") or raw
                            msg = f"{base_msg} ({detail})" if detail and detail != base_msg else base_msg
                        else:
                            msg = err_json.get("message") or err_json.get("error") or raw
                    except Exception:
                        msg = raw
                    clean_msg = format_clean_api_error(status_code, msg)
                    raise RuntimeError(clean_msg)

                except (http.client.RemoteDisconnected, http.client.CannotSendRequest,
                        BrokenPipeError, ConnectionResetError, urllib.error.URLError,
                        TimeoutError, OSError, http.client.HTTPException) as err:
                    self._conn_pool.release_connection(conn, close=True)
                    if attempt < max_retries - 1:
                        time.sleep(1.0 + attempt * 0.5)
                        continue
                    if isinstance(err, http.client.IncompleteRead):
                        part_str = (err.partial or b"").decode("utf-8", errors="replace").strip()
                        if part_str:
                            clean_part = clean_error_message(part_str)
                            if clean_part and clean_part != part_str:
                                raise RuntimeError(f"美客多接口异常: {clean_part}") from err
                    raise RuntimeError(f"美客多网络请求超时或连接失败: {err}") from err
                except Exception:
                    self._conn_pool.release_connection(conn, close=True)
                    raise

            raise RuntimeError("美客多 API 请求重试次数已耗尽")

    def request_bytes(
        self,
        account_id: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> bytes:
        """Make an authenticated request returning raw binary bytes (for attachments/images)."""
        token_info = self.auth.get_token(account_id)
        access_token = token_info.access_token

        query_str = ""
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean_params:
                query_str = "?" + urllib.parse.urlencode(clean_params)

        req_path = f"/{path.lstrip('/')}{query_str}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Connection": "keep-alive",
        }

        with self._get_semaphore(account_id):
            for attempt in range(max_retries):
                conn = self._conn_pool.get_connection()
                try:
                    conn.request(method.upper(), req_path, headers=headers)
                    resp = conn.getresponse()
                    status_code = resp.status
                    is_close = resp.getheader("Connection", "").lower() == "close"
                    raw_bytes = resp.read()
                    self._conn_pool.release_connection(conn, close=is_close)

                    if 200 <= status_code < 300:
                        return raw_bytes

                    if status_code == 401 and attempt < max_retries - 1:
                        new_token = self.auth.get_token(account_id, force_refresh=True)
                        headers["Authorization"] = f"Bearer {new_token.access_token}"
                        time.sleep(0.5)
                        continue

                    if status_code in (409, 429) and attempt < max_retries - 1:
                        time.sleep((2 ** attempt) * 1.5)
                        continue

                    raise RuntimeError(f"Download failed with status {status_code}")
                except Exception as err:
                    self._conn_pool.release_connection(conn, close=True)
                    if attempt < max_retries - 1:
                        time.sleep(1.0)
                        continue
                    raise RuntimeError(f"Attachment download failed: {err}") from err
        return b""

    # --- High-level authoritative endpoints ---

    def get_item_detail(self, account_id: str, item_id: str) -> dict[str, Any]:
        """GET /marketplace/items/{id} - The authoritative single source of truth for item pricing & net proceeds."""
        clean_id = item_id.strip()
        return self.request(account_id, "GET", f"/marketplace/items/{clean_id}")

    def get_items_batch(
        self,
        account_id: str,
        item_ids: list[str],
        attributes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve cross-border CBT items via official /marketplace/items/{id} endpoint."""
        if not item_ids:
            return []
        clean_ids = [str(i).strip() for i in item_ids if str(i).strip()][:20]
        if not clean_ids:
            return []

        from concurrent.futures import ThreadPoolExecutor, as_completed
        items: list[dict[str, Any]] = []

        def _fetch_one(iid: str) -> dict[str, Any] | None:
            try:
                return self.get_item_detail(account_id, iid)
            except Exception as err:
                logger.debug("Failed to fetch CBT item %s: %s", iid, err)
                return None

        max_workers = min(len(clean_ids), 8)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {executor.submit(_fetch_one, iid): iid for iid in clean_ids}
            for future in as_completed(future_to_id):
                item = future.result()
                if item and isinstance(item, dict) and "id" in item:
                    items.append(item)
        return items

    def get_seller_promotions(self, account_id: str, child_user_id: str) -> list[dict[str, Any]]:
        """GET /marketplace/seller-promotions/users/{child_user_id} - List all available promotions for site."""
        clean_uid = child_user_id.strip()
        data = self.request(account_id, "GET", f"/marketplace/seller-promotions/users/{clean_uid}", params={"app_version": "v2"})
        return data.get("results") or []

    def create_seller_campaign(
        self,
        account_id: str,
        child_user_id: str,
        name: str,
        start_date: str,
        finish_date: str,
        sub_type: str = "FLEXIBLE_PERCENTAGE",
    ) -> dict[str, Any]:
        """POST /marketplace/seller-promotions/seller-campaign/{child_user_id} - Create seller campaign."""
        clean_uid = str(child_user_id).strip()
        body = {
            "promotion_type": "SELLER_CAMPAIGN",
            "name": str(name).strip(),
            "sub_type": str(sub_type).strip(),
            "start_date": str(start_date).strip(),
            "finish_date": str(finish_date).strip(),
        }
        extra_headers = {
            "X-Caller-Id": str(account_id),
            "X-Client-Id": str(account_id),
        }
        return self.request(
            account_id=account_id,
            method="POST",
            path=f"/marketplace/seller-promotions/seller-campaign/{clean_uid}",
            body=body,
            extra_headers=extra_headers,
        )

    def get_promotion_items(
        self,
        account_id: str,
        child_user_id: str,
        promotion_id: str,
        status: str = "candidate",
        page_size: int = 50,
        max_items: int | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """GET /marketplace/seller-promotions/promotions/{promotion_id}/items with automatic pagination."""
        if max_items is None and "limit" in kwargs:
            max_items = kwargs["limit"]
        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        offset = 0
        search_after = None
        consecutive_empty = 0

        while True:
            params: dict[str, Any] = {
                "user_id": child_user_id.strip(),
                "status": status,
                "limit": min(page_size, 50),
                "app_version": "v2",
            }
            if search_after:
                params["search_after"] = search_after
            elif offset > 0:
                params["offset"] = offset

            data = self.request(
                account_id,
                "GET",
                f"/marketplace/seller-promotions/promotions/{promotion_id.strip()}/items",
                params=params,
            )

            raw_results = data.get("results") or []
            new_results = []
            for r in raw_results:
                rid = str(r.get("id") or r.get("item_id") or "")
                if rid:
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                new_results.append(r)

            if new_results:
                all_items.extend(new_results)
                consecutive_empty = 0
            else:
                consecutive_empty += 1

            if max_items and len(all_items) >= max_items:
                all_items = all_items[:max_items]
                break

            paging = data.get("paging") or {}
            total = paging.get("total")
            if total is not None and len(all_items) >= total:
                break

            next_search_after = (
                paging.get("search_after")
                or paging.get("searchAfter")
                or data.get("search_after")
                or data.get("searchAfter")
            )
            if next_search_after and str(next_search_after) != str(search_after):
                search_after = str(next_search_after)
            elif not next_search_after:
                if not raw_results or len(raw_results) < params["limit"]:
                    break
                offset += len(raw_results)
            else:
                break

            if consecutive_empty >= 5:
                break

        return all_items

    def get_item_promotions(self, account_id: str, child_user_id: str, item_id: str) -> list[dict[str, Any]]:
        """GET /marketplace/seller-promotions/items/{item_id}?user_id={child_user_id}&app_version=v2"""
        data = self.request(
            account_id,
            "GET",
            f"/marketplace/seller-promotions/items/{item_id.strip()}",
            params={"user_id": child_user_id.strip(), "app_version": "v2"},
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results") or []
        return []

    def enroll_promotion_item(
        self,
        account_id: str,
        child_user_id: str,
        item_id: str,
        promotion_id: str,
        promotion_type: str,
        deal_price: float,
        offer_id: str | None = None,
        original_price: float | None = None,
        stock: int | None = None,
        action: str = "enroll",
    ) -> dict[str, Any]:
        """POST (or PUT if action='update') /marketplace/seller-promotions/items/{item_id}?user_id={child_user_id}&app_version=v2"""
        clean_type = promotion_type.strip().upper()
        params = {"user_id": child_user_id.strip(), "app_version": "v2"}

        if clean_type == "SMART" and offer_id:
            body = {
                "promotion_id": promotion_id.strip(),
                "promotion_type": "SMART",
                "offer_id": offer_id.strip(),
            }
        elif clean_type == "LIGHTNING":
            body = {
                "deal_id": promotion_id.strip(),
                "promotion_type": "LIGHTNING",
                "deal_price": round(deal_price, 2),
                "original_price": round(original_price or deal_price, 2),
                "stock": stock or 5,
            }
        else:
            body = {
                "promotion_id": promotion_id.strip(),
                "promotion_type": clean_type,
                "deal_price": round(deal_price, 2),
            }

        http_method = "PUT" if action.lower() == "update" else "POST"
        return self.request(
            account_id,
            http_method,
            f"/marketplace/seller-promotions/items/{item_id.strip()}",
            params=params,
            body=body,
        )

    def cancel_promotion_item(
        self,
        account_id: str,
        child_user_id: str,
        item_id: str,
        promotion_id: str,
        promotion_type: str,
        offer_id: str | None = None,
    ) -> dict[str, Any]:
        """DELETE /marketplace/seller-promotions/items/{item_id}?user_id={child_user_id}&promotion_id={id}&promotion_type={type}&app_version=v2"""
        clean_type = promotion_type.strip().upper()
        params = {
            "user_id": child_user_id.strip(),
            "promotion_id": promotion_id.strip(),
            "promotion_type": clean_type,
            "app_version": "v2",
        }
        if offer_id:
            params["offer_id"] = offer_id.strip()
        return self.request(
            account_id,
            "DELETE",
            f"/marketplace/seller-promotions/items/{item_id.strip()}",
            params=params,
        )

    def get_item_visits(self, account_id: str, item_id: str) -> int:
        """GET /visits/items?ids={id} - Get total visits for a single item (official limit: 1 item per request)."""
        clean_id = str(item_id).strip()
        if not clean_id:
            return 0
        try:
            res = self.request(account_id, "GET", f"/visits/items?ids={clean_id}")
            if isinstance(res, dict) and clean_id in res:
                val = res[clean_id]
                return int(val) if str(val).isdigit() or isinstance(val, (int, float)) else 0
        except Exception:
            pass
        return 0

    def get_items_visits(self, account_id: str, item_ids: list[str]) -> dict[str, int]:
        """Get total visits for item IDs. Official API limits to 1 item per request."""
        res_map: dict[str, int] = {}
        for iid in item_ids:
            clean_id = str(iid).strip()
            if clean_id:
                res_map[clean_id] = self.get_item_visits(account_id, clean_id)
        return res_map

    def get_item_visits_window(self, account_id: str, item_id: str, last_days: int = 30) -> int:
        """GET /items/{id}/visits/time_window?last={N}&unit=day - Get visits within last N days."""
        clean_id = str(item_id).strip()
        days = min(max(1, int(last_days)), 150)
        try:
            res = self.request(account_id, "GET", f"/items/{clean_id}/visits/time_window", params={"last": days, "unit": "day"})
            if isinstance(res, dict):
                return int(res.get("total_visits") or 0)
        except Exception:
            pass
        return 0

    def get_item_performance(self, account_id: str, item_id: str) -> dict[str, Any]:
        """GET /item/{id}/performance - Get listing quality score (0-100) and level wording."""
        clean_id = str(item_id).strip()
        try:
            res = self.request(account_id, "GET", f"/item/{clean_id}/performance")
            if isinstance(res, dict):
                return res
        except Exception as e:
            return {"error": str(e)}
        return {}

    def search_user_items(
        self,
        account_id: str,
        status: str | None = None,
        sub_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        search_type: str | None = None,
        scroll_id: str | None = None,
    ) -> dict[str, Any]:
        """GET /users/{account_id}/items/search - Search listings owned by account."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if search_type:
            params["search_type"] = search_type
        if scroll_id:
            params["scroll_id"] = scroll_id
        elif offset > 0:
            params["offset"] = offset
        if status:
            params["status"] = status
        if sub_status:
            params["sub_status"] = sub_status
        return self.request(account_id, "GET", f"/users/{account_id}/items/search", params=params)

    def search_marketplace_items(
        self,
        account_id: str,
        child_user_id: str,
        status: str | None = None,
        sub_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        search_type: str | None = None,
        scroll_id: str | None = None,
    ) -> dict[str, Any]:
        """GET /marketplace/users/{child_user_id}/items/search - Search listings owned by child marketplace user."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if search_type:
            params["search_type"] = search_type
        if scroll_id:
            params["scroll_id"] = scroll_id
        elif offset > 0:
            params["offset"] = offset
        if status:
            params["status"] = status
        if sub_status:
            params["sub_status"] = sub_status
        return self.request(account_id, "GET", f"/marketplace/users/{child_user_id.strip()}/items/search", params=params)


    def close_item(self, account_id: str, item_id: str) -> dict[str, Any]:
        """Inactivate CBT cross-border listing via official /global/items/{id} endpoint."""
        clean_id = str(item_id).strip()
        return self.request(account_id, "PUT", f"/global/items/{clean_id}", body={"status": "paused"})

    def delete_item(self, account_id: str, item_id: str) -> dict[str, Any]:
        """Permanently delete CBT cross-border listing via official lifecycle protocol."""
        clean_id = str(item_id).strip()
        try:
            return self.request(account_id, "PUT", f"/global/items/{clean_id}", body={"deleted": True})
        except Exception as e:
            err_str = str(e).lower()
            if "deleted is not modifiable" in err_str or "status:active" in err_str:
                self.request(account_id, "PUT", f"/global/items/{clean_id}", body={"status": "paused"})
                return self.request(account_id, "PUT", f"/global/items/{clean_id}", body={"deleted": True})
            if "404" in err_str and ("not a cbt item" in err_str or "not_found" in err_str or "not found" in err_str):
                return {
                    "id": clean_id,
                    "status": "paused",
                    "sub_status": ["forbidden", "deleted"],
                    "already_deleted": True,
                }
            raise

    # ------------------------------------------------------------------
    # Customer Service & Post-Purchase Messaging Endpoints
    # ------------------------------------------------------------------

    def get_questions(
        self,
        account_id: str,
        status: str | None = "UNANSWERED",
        limit: int = 50,
        offset: int = 0,
        sort: str = "date_desc",
    ) -> dict[str, Any]:
        """GET /marketplace/questions/search - Search cross-border CBT questions for seller."""
        params: dict[str, Any] = {
            "limit": min(limit, 50),
            "offset": offset,
        }
        if status:
            params["status"] = status
        data = self.request(account_id, "GET", "/marketplace/questions/search", params=params)
        if isinstance(data, dict) and data.get("questions") is None:
            data["questions"] = []
        return data

    def answer_question(self, account_id: str, question_id: int | str, text: str) -> dict[str, Any]:
        """POST /marketplace/answers or /answers - Submit an answer to a buyer question."""
        body = {
            "question_id": int(question_id),
            "text": str(text).strip(),
        }
        try:
            return self.request(account_id, "POST", "/marketplace/answers", body=body)
        except Exception:
            return self.request(account_id, "POST", "/answers", body=body)

    def get_claims(
        self,
        account_id: str,
        child_user_id: str,
        status: str = "opened",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """GET /post-purchase/v1/claims/search - Search claims for child marketplace user."""
        params: dict[str, Any] = {
            "user_id": str(child_user_id).strip(),
            "status": status,
            "limit": min(limit, 50),
            "offset": offset,
        }
        return self.request(account_id, "GET", "/post-purchase/v1/claims/search", params=params)

    def get_claim_detail(self, account_id: str, claim_id: int | str) -> dict[str, Any]:
        """GET /post-purchase/v1/claims/{claim_id} - Retrieve claim dispute metadata and resolutions."""
        cid = str(claim_id).strip()
        return self.request(account_id, "GET", f"/post-purchase/v1/claims/{cid}")

    def get_claim_affects_reputation(self, account_id: str, claim_id: int | str) -> dict[str, Any]:
        """GET /post-purchase/v1/claims/{claim_id}/affects-reputation - Check if claim affects seller reputation."""
        cid = str(claim_id).strip()
        return self.request(account_id, "GET", f"/post-purchase/v1/claims/{cid}/affects-reputation")

    def get_claim_messages(self, account_id: str, claim_id: int | str) -> list[dict[str, Any]]:
        """GET /post-purchase/v1/claims/{claim_id}/messages - Fetch mediation conversation stream."""
        cid = str(claim_id).strip()
        res = self.request(account_id, "GET", f"/post-purchase/v1/claims/{cid}/messages")
        if isinstance(res, list):
            return res
        if isinstance(res, dict) and "data" in res:
            return res["data"]
        return []

    def upload_attachment_multipart(
        self,
        account_id: str,
        path: str,
        file_bytes: bytes,
        filename: str,
        mime_type: str = "image/jpeg",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upload binary attachment using standard multipart/form-data."""
        import uuid
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        crlf = b"\r\n"
        clean_fn = os.path.basename(filename) or "image.jpg"
        disposition = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{clean_fn}"\r\nContent-Type: {mime_type}\r\n\r\n'.encode("utf-8")
        body_bytes = disposition + file_bytes + crlf + f"--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        return self.request(
            account_id,
            "POST",
            path,
            params=params,
            body=body_bytes,
            extra_headers=headers,
        )

    def upload_claim_attachment(
        self,
        account_id: str,
        claim_id: int | str,
        file_bytes: bytes,
        filename: str,
        mime_type: str = "image/jpeg",
    ) -> dict[str, Any]:
        """POST /post-purchase/v1/claims/{claim_id}/attachments - Upload attachment file to claim."""
        cid = str(claim_id).strip()
        return self.upload_attachment_multipart(
            account_id=account_id,
            path=f"/post-purchase/v1/claims/{cid}/attachments",
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
        )

    def send_claim_message(
        self,
        account_id: str,
        claim_id: int | str,
        message: str,
        receiver_role: str = "complainant",
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST /post-purchase/v1/claims/{claim_id}/actions/send-message - Reply to buyer or mediator with optional attachments."""
        cid = str(claim_id).strip()
        body: dict[str, Any] = {
            "message": str(message).strip(),
            "receiver_role": receiver_role,
        }
        if attachments:
            body["attachments"] = [str(a).strip() for a in attachments if str(a).strip()]

        try:
            return self.request(account_id, "POST", f"/post-purchase/v1/claims/{cid}/actions/send-message", body=body)
        except Exception as err:
            # If rejected because dispute stage requires mediator or vice-versa, attempt graceful fallback once
            alt_role = "mediator" if receiver_role == "complainant" else "complainant"
            body["receiver_role"] = alt_role
            try:
                return self.request(account_id, "POST", f"/post-purchase/v1/claims/{cid}/actions/send-message", body=body)
            except Exception:
                pass
            raise err

    def download_claim_attachment(self, account_id: str, claim_id: int | str, filename: str) -> bytes:
        """GET /post-purchase/v1/claims/{claim_id}/attachments/{filename}/download - Retrieve binary attachment."""
        cid = str(claim_id).strip()
        fn = str(filename).strip()
        return self.request_bytes(account_id, "GET", f"/post-purchase/v1/claims/{cid}/attachments/{fn}/download")

    def request_claim_mediation(self, account_id: str, claim_id: int | str) -> dict[str, Any]:
        """POST /post-purchase/v1/claims/{claim_id}/actions/open-dispute - Request Mercado Libre mediation."""
        cid = str(claim_id).strip()
        return self.request(account_id, "POST", f"/post-purchase/v1/claims/{cid}/actions/open-dispute", body={})

    def get_claim_partial_refund_offers(self, account_id: str, claim_id: int | str) -> dict[str, Any]:
        """GET /post-purchase/v1/claims/{claim_id}/partial-refund/available-offers - Query available partial refund offers."""
        cid = str(claim_id).strip()
        return self.request(account_id, "GET", f"/post-purchase/v1/claims/{cid}/partial-refund/available-offers")

    def submit_claim_partial_refund(
        self,
        account_id: str,
        claim_id: int | str,
        percentage: float | int | None = None,
        amount: float | None = None,
    ) -> dict[str, Any]:
        """POST /post-purchase/v1/claims/{claim_id}/expected-resolutions/partial-refund - Submit partial refund proposal."""
        cid = str(claim_id).strip()
        body: dict[str, Any] = {}
        if percentage is not None:
            body["percentage"] = int(percentage) if (isinstance(percentage, int) or (isinstance(percentage, float) and percentage.is_integer())) else float(percentage)
        elif amount is not None:
            body["amount"] = float(amount)
        return self.request(account_id, "POST", f"/post-purchase/v1/claims/{cid}/expected-resolutions/partial-refund", body=body)

    def submit_claim_full_refund(self, account_id: str, claim_id: int | str) -> dict[str, Any]:
        """POST /post-purchase/v1/claims/{claim_id}/expected-resolutions/refund - Grant full refund to resolve claim."""
        cid = str(claim_id).strip()
        return self.request(account_id, "POST", f"/post-purchase/v1/claims/{cid}/expected-resolutions/refund", body={})

    def get_unread_pack_messages(self, account_id: str, child_user_id: str) -> dict[str, Any]:
        """GET /marketplace/messages/unread?user_id={child_user_id} - List packs with unread messages."""
        params = {"user_id": str(child_user_id).strip()}
        return self.request(account_id, "GET", "/marketplace/messages/unread", params=params)

    def get_pack_messages(self, account_id: str, pack_id: int | str) -> dict[str, Any]:
        """GET /marketplace/messages/packs/{pack_id}?tag=post_sale - Retrieve order post-purchase conversation."""
        pid = str(pack_id).strip()
        try:
            return self.request(account_id, "GET", f"/marketplace/messages/packs/{pid}", params={"tag": "post_sale"})
        except Exception as err:
            err_str = str(err)
            if "belong to a pack" in err_str.lower() or "pack/purchase" in err_str.lower():
                try:
                    ord_data = self.get_marketplace_order(account_id, pid)
                    real_pack = str(ord_data.get("pack_id") or "").strip()
                    if real_pack and real_pack != pid:
                        return self.request(account_id, "GET", f"/marketplace/messages/packs/{real_pack}", params={"tag": "post_sale"})
                except Exception:
                    pass
            if "not_found" in err_str.lower() or "not found" in err_str.lower() or "404" in err_str:
                return {"messages": [], "paging": {"total": 0}}
            raise

    def upload_pack_attachment(
        self,
        account_id: str,
        file_bytes: bytes,
        filename: str,
        mime_type: str = "image/jpeg",
        site_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /messages/attachments - Upload attachment file to post-sale message pool."""
        params: dict[str, Any] = {}
        if site_id:
            params["site_id"] = site_id
        return self.upload_attachment_multipart(
            account_id=account_id,
            path="/messages/attachments",
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            params=params,
        )

    def send_pack_message(
        self,
        account_id: str,
        pack_id: int | str,
        text: str,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST /marketplace/messages/packs/{pack_id}?tag=post_sale - Send post-purchase reply to buyer with optional attachments."""
        pid = str(pack_id).strip()
        body: dict[str, Any] = {
            "text": str(text).strip(),
            "text_translated": str(text).strip(),
        }
        if attachments:
            body["attachments"] = [str(a).strip() for a in attachments if str(a).strip()]
        try:
            return self.request(account_id, "POST", f"/marketplace/messages/packs/{pid}", params={"tag": "post_sale"}, body=body)
        except Exception as err:
            err_str = str(err)
            if "belong to a pack" in err_str.lower() or "pack/purchase" in err_str.lower():
                try:
                    ord_data = self.get_marketplace_order(account_id, pid)
                    real_pack = str(ord_data.get("pack_id") or "").strip()
                    if real_pack and real_pack != pid:
                        return self.request(account_id, "POST", f"/marketplace/messages/packs/{real_pack}", params={"tag": "post_sale"}, body=body)
                except Exception:
                    pass
            raise

    def get_marketplace_order(self, account_id: str, order_id: int | str) -> dict[str, Any]:
        """GET /marketplace/orders/{order_id} - Retrieve detailed cross-border CBT order."""
        oid = str(order_id).strip()
        return self.request(account_id, "GET", f"/marketplace/orders/{oid}")

    def get_marketplace_shipment(self, account_id: str, shipment_id: int | str) -> dict[str, Any]:
        """GET /marketplace/shipments/{shipment_id} - Retrieve detailed cross-border CBT shipment."""
        sid = str(shipment_id).strip()
        return self.request(account_id, "GET", f"/marketplace/shipments/{sid}")

    def get_marketplace_shipment_costs(self, account_id: str, shipment_id: int | str) -> dict[str, Any]:
        """GET /marketplace/shipments/{shipment_id}/costs - Retrieve shipping cost breakdown (sender vs receiver)."""
        sid = str(shipment_id).strip()
        return self.request(account_id, "GET", f"/marketplace/shipments/{sid}/costs")

    def search_marketplace_orders(
        self,
        account_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "date_desc",
    ) -> dict[str, Any]:
        """GET /marketplace/orders/search - Search cross-border CBT orders (defaults to date_desc)."""
        params: dict[str, Any] = {
            "seller": str(account_id).strip(),
            "limit": min(limit, 50),
            "offset": offset,
        }
        if sort:
            params["sort"] = sort
        if status:
            params["order.status"] = status
        return self.request(account_id, "GET", "/marketplace/orders/search", params=params)

    def get_order(self, account_id: str, order_id: int | str) -> dict[str, Any]:
        """GET /orders/{order_id} - Retrieve detailed local site order."""
        oid = str(order_id).strip()
        return self.request(account_id, "GET", f"/orders/{oid}")

    def get_shipment(self, account_id: str, shipment_id: int | str) -> dict[str, Any]:
        """GET /shipments/{shipment_id} - Retrieve detailed shipment information."""
        sid = str(shipment_id).strip()
        return self.request(account_id, "GET", f"/shipments/{sid}")

    def search_orders(
        self,
        account_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "date_desc",
        buyer: str | None = None,
    ) -> dict[str, Any]:
        """GET /orders/search - Search local site orders."""
        params: dict[str, Any] = {
            "seller": str(account_id).strip(),
            "limit": min(limit, 50),
            "offset": offset,
        }
        if sort:
            params["sort"] = sort
        if status:
            params["order.status"] = status
        if buyer:
            params["buyer"] = str(buyer).strip()
        return self.request(account_id, "GET", "/orders/search", params=params)

    def request_raw_bytes(
        self,
        account_id: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        max_retries: int = 3,
    ) -> bytes:
        """Make an authenticated request returning raw binary bytes (e.g. PDF shipping labels)."""
        token_info = self.auth.get_token(account_id)
        access_token = token_info.access_token

        query_str = ""
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean_params:
                query_str = "?" + urllib.parse.urlencode(clean_params)

        req_path = f"/{path.lstrip('/')}{query_str}"
        headers = {
            "Accept": "application/pdf, application/octet-stream, */*",
            "Authorization": f"Bearer {access_token}",
            "Connection": "keep-alive",
        }
        if extra_headers:
            headers.update(extra_headers)

        with self._get_semaphore(account_id):
            for attempt in range(max_retries):
                conn = self._conn_pool.get_connection()
                try:
                    conn.request(method.upper(), req_path, headers=headers)
                    resp = conn.getresponse()
                    status_code = resp.status
                    is_close = (resp.getheader("Connection", "") or "").lower() == "close"
                    raw_bytes = resp.read()
                    self._conn_pool.release_connection(conn, close=is_close)

                    if 200 <= status_code < 300:
                        return raw_bytes

                    if status_code == 401 and attempt < max_retries - 1:
                        new_token = self.auth.get_token(account_id, force_refresh=True)
                        headers["Authorization"] = f"Bearer {new_token.access_token}"
                        time.sleep(0.5)
                        continue

                    raw = raw_bytes.decode("utf-8", errors="replace")
                    try:
                        err_json = json.loads(raw)
                        msg = err_json.get("message") or err_json.get("error") or raw
                    except Exception:
                        msg = raw
                    clean_msg = format_clean_api_error(status_code, msg)
                    raise RuntimeError(clean_msg)
                except (http.client.RemoteDisconnected, http.client.CannotSendRequest,
                        BrokenPipeError, ConnectionResetError, urllib.error.URLError,
                        TimeoutError, OSError, http.client.HTTPException):
                    self._conn_pool.release_connection(conn, close=True)
                    if attempt < max_retries - 1:
                        time.sleep(1.0 + attempt * 0.5)
                        continue
                    raise
        return b""

    def get_marketplace_shipment_labels_pdf(self, account_id: str, shipment_id: int | str) -> bytes:
        """GET /marketplace/shipments/{shipment_id}/labels - Retrieve CBT cross-border shipping label PDF."""
        sid = str(shipment_id).strip()
        return self.request_raw_bytes(
            account_id=account_id,
            method="GET",
            path=f"/marketplace/shipments/{sid}/labels",
        )

    def get_shipment_labels_pdf(self, account_id: str, shipment_ids: list[int | str]) -> bytes:
        """GET /shipment_labels?shipment_ids={ids}&response_type=pdf - Download batch PDF shipping labels.
        Automatically fallbacks to CBT /marketplace/shipments/{id}/labels when Caller is CBT or not printable."""
        clean_ids = [str(s).strip() for s in shipment_ids if str(s).strip()]
        if not clean_ids:
            return b""

        # First, try standard batch /shipment_labels
        s_ids = ",".join(clean_ids)
        try:
            res = self.request_raw_bytes(
                account_id=account_id,
                method="GET",
                path="/shipment_labels",
                params={"shipment_ids": s_ids, "response_type": "pdf"},
            )
            if res and len(res) > 20 and res.startswith(b"%PDF"):
                return res
        except Exception as err:
            err_str = str(err).lower()
            if "invalid_shipment_caller" not in err_str and "not printable by caller" not in err_str and "400" not in err_str:
                raise

        # Fallback for CBT cross-border accounts: fetch each shipment label via /marketplace/shipments/{id}/labels
        collected_pdfs: list[bytes] = []
        for sid in clean_ids:
            try:
                pdf_b = self.get_marketplace_shipment_labels_pdf(account_id, sid)
                if pdf_b and len(pdf_b) > 20 and pdf_b.startswith(b"%PDF"):
                    collected_pdfs.append(pdf_b)
            except Exception as e:
                logger.warning("Failed fetching marketplace shipment label %s for %s: %s", sid, account_id, e)

        if not collected_pdfs:
            raise RuntimeError(f"未能获取到运单 ({', '.join(clean_ids)}) 的有效面单数据。")

        if len(collected_pdfs) == 1:
            return collected_pdfs[0]

        # Merge multiple PDFs into a single multi-page document
        try:
            import io
            from pypdf import PdfReader, PdfWriter
            writer = PdfWriter()
            for b in collected_pdfs:
                reader = PdfReader(io.BytesIO(b))
                for page in reader.pages:
                    writer.add_page(page)
            out = io.BytesIO()
            writer.write(out)
            return out.getvalue()
        except Exception as e:
            logger.warning("Failed merging PDFs with pypdf, returning first PDF: %s", e)
            return collected_pdfs[0]

