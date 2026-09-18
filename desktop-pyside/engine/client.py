from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .auth import AuthManager


class MercadoClient:
    API_BASE = "https://api.mercadolibre.com"

    def __init__(self, auth_manager: AuthManager | None = None, max_concurrency: int = 8):
        self.auth = auth_manager or AuthManager()
        self._semaphore = threading.Semaphore(max_concurrency)

    def request(
        self,
        account_id: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Make an authenticated request to Mercado Libre API with 429 retry and token auto-refresh."""
        token_info = self.auth.get_token(account_id)
        access_token = token_info.access_token

        query_str = ""
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean_params:
                query_str = "?" + urllib.parse.urlencode(clean_params)

        url = f"{self.API_BASE.rstrip('/')}/{path.lstrip('/')}{query_str}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "version": "v2",
        }

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        with self._semaphore:
            for attempt in range(max_retries):
                req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        raw = resp.read().decode("utf-8")
                        if not raw:
                            return {}
                        return json.loads(raw)
                except urllib.error.HTTPError as err:
                    raw_err = err.read().decode("utf-8", errors="replace")
                    # Handle Token Expiration (401) -> refresh once and retry
                    if err.code == 401 and attempt < max_retries - 1:
                        new_token = self.auth.get_token(account_id, force_refresh=True)
                        headers["Authorization"] = f"Bearer {new_token.access_token}"
                        time.sleep(0.5)
                        continue

                    # Handle Rate Limit (429) & Capacity Constraints (409) -> exponential backoff
                    if err.code in (409, 429) and attempt < max_retries - 1:
                        sleep_time = (2 ** attempt) * 1.5
                        time.sleep(sleep_time)
                        continue

                    # Parse JSON error if possible
                    try:
                        err_json = json.loads(raw_err)
                        msg = err_json.get("message") or err_json.get("error") or raw_err
                    except Exception:
                        msg = raw_err
                    raise RuntimeError(f"美客多 API 报错 ({err.code}): {msg}") from err
                except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as err:
                    if attempt < max_retries - 1:
                        time.sleep(1.0 + attempt * 0.5)
                        continue
                    raise RuntimeError(f"美客多网络请求超时或连接失败: {err}") from err

            raise RuntimeError("美客多 API 请求重试次数已耗尽")

    # --- High-level authoritative endpoints ---

    def get_item_detail(self, account_id: str, item_id: str) -> dict[str, Any]:
        """GET /marketplace/items/{id} - The authoritative single source of truth for item pricing & net proceeds."""
        clean_id = item_id.strip()
        return self.request(account_id, "GET", f"/marketplace/items/{clean_id}")

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
    ) -> list[dict[str, Any]]:
        """GET /marketplace/seller-promotions/promotions/{promotion_id}/items with automatic pagination."""
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
