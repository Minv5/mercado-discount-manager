from __future__ import annotations

import http.client
import json
import queue
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
from typing import Any

from .auth import AuthManager


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
    text_lower = text.lower()

    # 1. 500 / internal error
    if code == 500 or "oops! something went wrong" in text_lower or "internal_error" in text_lower or "internal server error" in text_lower:
        return "平台服务繁忙，请稍后重试 (500)"

    # 2. 502 / 503 / 504 / Gateway & Maintenance
    if code in (502, 503, 504) or any(k in text_lower for k in ("bad gateway", "service unavailable", "gateway timeout")):
        return f"平台网关超时/维护中 ({code or 503})"

    # 3. 400 / 409 Activity Locked
    if "lockedentityexception" in text_lower or "offer locked" in text_lower:
        return "活动已锁定，平台禁止退出"

    # 4. 400 Credibility
    if "error_credibility_discounted_price" in text_lower or "price is not credible" in text_lower:
        return "折后价未达近期成交价门槛"

    # 5. 400 Eligibility
    if "item_not_eligible" in text_lower:
        return "未达活动受邀门槛"

    # 6. Already in promotion
    if "already in promotion" in text_lower or "already" in text_lower:
        return "已在活动中(自动跳过)"

    # 7. Required promotion_type
    if "promotion_type is required" in text_lower:
        return "缺少活动类型参数"

    # 8. Active item not modifiable / deletable
    if "deleted is not modifiable" in text_lower:
        return "商品活跃/出单中，禁止删除"

    # 9. 404 not CBT
    if "not a cbt item" in text_lower:
        return "非全球CBT商品 (404)"

    # 10. 404 not found
    if code == 404 or "not found" in text_lower or "not_found" in text_lower:
        return "商品或活动不存在 (404)"

    # 11. 403 User identification
    if "can not identify the user" in text_lower or "cannot identify the user" in text_lower:
        return "站点无权限或账号不匹配 (403)"

    # 12. 403 Forbidden / Access denied
    if code == 403 or "forbidden" in text_lower or "access_denied" in text_lower:
        return "站点访问受限 (403)"

    # 13. 401 Auth expired
    if code == 401 or any(k in text_lower for k in ("invalid_grant", "consumer_auth_required", "expired_token")):
        return "店铺授权已过期 (401)"

    # 14. 429 Rate limit
    if code == 429 or "too many requests" in text_lower or "rate_limit" in text_lower:
        return "平台限流，降速排队中 (429)"

    # 15. Network error
    if any(k in text_lower for k in ("remotedisconnected", "connectionreset", "brokenpipe", "timeout", "socket hang up", "cannotsendrequest")):
        return "网络超时或中断"

    # Fallback: clean up noisy wrappers
    clean = text
    m = re.search(r"Errors?:\s*(?:[A-Z_]+\s*-\s*)?([^,}\]]+)", clean)
    if m:
        clean = m.group(1).strip()
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

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        with self._get_semaphore(account_id):
            for attempt in range(max_retries):
                conn = self._conn_pool.get_connection()
                try:
                    conn.request(method.upper(), req_path, body=data, headers=headers)
                    resp = conn.getresponse()
                    status_code = resp.status
                    raw_bytes = resp.read()
                    raw = raw_bytes.decode("utf-8", errors="replace")
                    is_close = resp.getheader("Connection", "").lower() == "close"
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

                    # Handle Rate Limit (429) & Capacity Constraints (409) -> exponential backoff
                    if status_code in (409, 429) and attempt < max_retries - 1:
                        sleep_time = (2 ** attempt) * 1.5
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
                    raise RuntimeError(f"美客多网络请求超时或连接失败: {err}") from err
                except Exception:
                    self._conn_pool.release_connection(conn, close=True)
                    raise

            raise RuntimeError("美客多 API 请求重试次数已耗尽")

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
        """GET /items?ids=ID1,ID2... (up to 20 items per official limit) with optional attributes projection."""
        if not item_ids:
            return []
        clean_ids = [str(i).strip() for i in item_ids if str(i).strip()][:20]
        if not clean_ids:
            return []
        params: dict[str, Any] = {"ids": ",".join(clean_ids)}
        if attributes:
            params["attributes"] = ",".join(attributes)
        try:
            res = self.request(account_id, "GET", "/items", params=params)
            if isinstance(res, list):
                items = []
                for entry in res:
                    if isinstance(entry, dict) and entry.get("code") == 200 and isinstance(entry.get("body"), dict):
                        items.append(entry["body"])
                    elif isinstance(entry, dict) and "id" in entry:
                        items.append(entry)
                return items
        except Exception:
            pass
        return []

    def get_seller_promotions(self, account_id: str, child_user_id: str) -> list[dict[str, Any]]:
        """GET /marketplace/seller-promotions/users/{child_user_id} - List all available promotions for site."""
        clean_uid = child_user_id.strip()
        data = self.request(account_id, "GET", f"/marketplace/seller-promotions/users/{clean_uid}", params={"app_version": "v2"})
        return data.get("results") or []

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
