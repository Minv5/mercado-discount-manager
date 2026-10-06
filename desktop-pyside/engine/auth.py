import datetime
import json
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .crypto import decrypt_secret, encrypt_secret, get_data_dir


class OAuthInvalidGrantError(RuntimeError):
    """Raised when a refresh token is expired, revoked, or rejected with invalid_grant."""

    def __init__(self, account_id: str, display_name: str = "", raw_error: str = ""):
        name = display_name or f"账号 {account_id}"
        super().__init__(f"店铺【{name}】美客多授权已失效或在后台被解除，请重新扫码授权。详情: {raw_error}")
        self.account_id = account_id
        self.display_name = name
        self.raw_error = raw_error


@dataclass
class AccountToken:
    account_id: str
    display_name: str
    access_token: str
    refresh_token: str
    client_id: str
    client_secret: str
    expires_at: str
    site_id: str | None = None
    auth_domain: str | None = None


@dataclass
class MarketplaceSite:
    account_id: str
    child_user_id: str
    site_id: str


class AuthManager:
    API_BASE = "https://api.mercadolibre.com"

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (get_data_dir() / "discount-manager.sqlite")
        self._refresh_lock = threading.Lock()
        self._token_cache: dict[str, AccountToken] = {}
        self._cache_lock = threading.Lock()
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._get_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS account_profiles (
                    account_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT 'mercadolibre',
                    display_name TEXT NOT NULL,
                    site_id TEXT,
                    fetched_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'oauth'
                );
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL DEFAULT 'mercadolibre',
                    account_id TEXT NOT NULL UNIQUE,
                    display_name TEXT,
                    site_id TEXT,
                    scopes TEXT,
                    access_token_cipher TEXT NOT NULL,
                    refresh_token_cipher TEXT,
                    token_type TEXT,
                    expires_at TEXT,
                    raw_json TEXT,
                    client_id TEXT,
                    client_secret_cipher TEXT,
                    redirect_uri TEXT,
                    auth_domain TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    client_secret_cipher TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    auth_domain TEXT NOT NULL,
                    code_verifier TEXT NOT NULL,
                    code_challenge TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processing_state TEXT NOT NULL DEFAULT 'pending',
                    claim_token TEXT,
                    claimed_at TEXT,
                    claim_expires_at TEXT,
                    consumed_at TEXT,
                    last_error_code TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS marketplace_sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    child_user_id TEXT NOT NULL,
                    site_id TEXT,
                    logistic_type TEXT,
                    last_promotion_status TEXT,
                    last_promotion_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, child_user_id)
                );
                CREATE TABLE IF NOT EXISTS promo_campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    promotion_id TEXT NOT NULL,
                    promotion_type TEXT NOT NULL,
                    merchant_id TEXT,
                    child_user_id TEXT NOT NULL DEFAULT '',
                    site_id TEXT NOT NULL DEFAULT '',
                    logistic_type TEXT,
                    name TEXT,
                    status TEXT,
                    start_date TEXT,
                    finish_date TEXT,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type)
                );
                CREATE TABLE IF NOT EXISTS promo_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    promotion_id TEXT NOT NULL,
                    promotion_type TEXT NOT NULL,
                    child_user_id TEXT NOT NULL DEFAULT '',
                    site_id TEXT NOT NULL DEFAULT '',
                    logistic_type TEXT,
                    item_id TEXT NOT NULL,
                    status TEXT,
                    currency_id TEXT,
                    original_price REAL,
                    price REAL,
                    suggested_discounted_price REAL,
                    min_discounted_price REAL,
                    max_discounted_price REAL,
                    source TEXT,
                    raw_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type, item_id)
                );
                CREATE TABLE IF NOT EXISTS promo_item_fetch_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    child_user_id TEXT NOT NULL DEFAULT '',
                    site_id TEXT NOT NULL DEFAULT '',
                    promotion_id TEXT NOT NULL,
                    promotion_type TEXT NOT NULL,
                    item_status TEXT NOT NULL,
                    platform_total INTEGER,
                    saved_count INTEGER NOT NULL DEFAULT 0,
                    detail_status TEXT NOT NULL,
                    warning TEXT,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, child_user_id, site_id, promotion_id, promotion_type, item_status)
                );
                CREATE TABLE IF NOT EXISTS promo_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    promotion_id TEXT NOT NULL,
                    promotion_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    discount_percent REAL,
                    direct_price REAL,
                    status TEXT NOT NULL,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    empty_count INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    summary_json TEXT,
                    execution_group_id TEXT,
                    execution_job_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS promo_action_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    account_id TEXT NOT NULL,
                    promotion_id TEXT NOT NULL,
                    promotion_type TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    deal_price REAL,
                    top_deal_price REAL,
                    error_cn TEXT,
                    error_raw TEXT,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES promo_tasks(id)
                );
                CREATE TABLE IF NOT EXISTS item_price_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    child_user_id TEXT NOT NULL DEFAULT '',
                    site_id TEXT NOT NULL DEFAULT '',
                    item_id TEXT NOT NULL,
                    price REAL,
                    original_price REAL,
                    currency_id TEXT,
                    status TEXT,
                    available_quantity REAL,
                    dimensions_json TEXT,
                    weight_json TEXT,
                    snapshot_hash TEXT,
                    source_revision TEXT,
                    observed_at TEXT,
                    change_flags_json TEXT,
                    confirmed INTEGER NOT NULL DEFAULT 1,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, child_user_id, site_id, item_id)
                );
                CREATE TABLE IF NOT EXISTS item_cleaner_score_cache (
                    item_id TEXT PRIMARY KEY,
                    score INTEGER,
                    level_wording TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS item_cleaner_visits_cache (
                    item_id TEXT PRIMARY KEY,
                    visits INTEGER,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS item_cleaner_info_cache (
                    item_id TEXT PRIMARY KEY,
                    data_json TEXT,
                    updated_at TEXT
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def list_accounts(self) -> list[dict[str, Any]]:
        """List all authorized accounts with their display names and status."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.account_id, p.display_name, p.site_id, t.expires_at, t.updated_at, t.client_id
                FROM account_profiles p
                LEFT JOIN oauth_tokens t ON p.account_id = t.account_id
                ORDER BY p.account_id
            """)
            rows = cur.fetchall()
            accounts = []
            for r in rows:
                accounts.append({
                    "id": r["account_id"],
                    "account_id": r["account_id"],
                    "display_name": r["display_name"] or f"账号 {r['account_id']}",
                    "store_name": r["display_name"] or f"店铺 {r['account_id']}",
                    "site_id": r["site_id"] or "CBT",
                    "expires_at": r["expires_at"],
                    "status": "active" if r["expires_at"] else "unauthorized",
                    "client_id": r["client_id"] or "",
                })
            return accounts
        finally:
            conn.close()

    def list_sites(self, account_id: str) -> list[dict[str, Any]]:
        """List all marketplace sub-sites for a CBT parent account."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT account_id, child_user_id, site_id
                FROM marketplace_sites
                WHERE account_id = ?
                ORDER BY site_id, child_user_id
            """, (str(account_id),))
            rows = cur.fetchall()
            return [
                {
                    "account_id": r["account_id"],
                    "child_user_id": r["child_user_id"],
                    "site_id": r["site_id"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_token(self, account_id: str, force_refresh: bool = False) -> AccountToken:
        """Get valid decrypted access token for account, automatically refreshing if expired/expiring."""
        clean_aid = str(account_id).strip()
        if not force_refresh:
            with self._cache_lock:
                cached = self._token_cache.get(clean_aid)
            if cached and cached.expires_at:
                try:
                    clean_exp = cached.expires_at.replace("Z", "+00:00")
                    exp_dt = datetime.datetime.fromisoformat(clean_exp)
                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                    if (exp_dt - now_dt).total_seconds() >= 600:
                        return cached
                except Exception:
                    pass

        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT account_id, display_name, site_id, access_token_cipher, refresh_token_cipher,
                       client_id, client_secret_cipher, expires_at, auth_domain
                FROM oauth_tokens
                WHERE account_id = ?
            """, (clean_aid,))
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"未找到店铺 {account_id} 的授权凭据，请在设置中授权。")

            access_token = decrypt_secret(row["access_token_cipher"])
            refresh_token = decrypt_secret(row["refresh_token_cipher"])
            client_id = row["client_id"] or ""
            client_secret = decrypt_secret(row["client_secret_cipher"]) if row["client_secret_cipher"] else ""
            expires_at = row["expires_at"] or ""

            # Check expiration (if expiring within 10 minutes or already expired, or forced)
            needs_refresh = force_refresh
            if expires_at and not needs_refresh:
                try:
                    clean_exp = expires_at.replace("Z", "+00:00")
                    exp_dt = datetime.datetime.fromisoformat(clean_exp)
                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                    if (exp_dt - now_dt).total_seconds() < 600:  # less than 10 mins
                        needs_refresh = True
                except Exception:
                    needs_refresh = True
            elif not expires_at:
                needs_refresh = True

            if needs_refresh and refresh_token and client_id and client_secret:
                with self._refresh_lock:
                    cur.execute("""
                        SELECT account_id, display_name, site_id, access_token_cipher, refresh_token_cipher,
                               client_id, client_secret_cipher, expires_at, auth_domain
                        FROM oauth_tokens
                        WHERE account_id = ?
                    """, (clean_aid,))
                    fresh_row = cur.fetchone()
                    if fresh_row:
                        fresh_exp = fresh_row["expires_at"] or ""
                        if not force_refresh and fresh_exp:
                            try:
                                clean_fresh = fresh_exp.replace("Z", "+00:00")
                                exp_dt = datetime.datetime.fromisoformat(clean_fresh)
                                now_dt = datetime.datetime.now(datetime.timezone.utc)
                                if (exp_dt - now_dt).total_seconds() >= 600:
                                    token_inst = AccountToken(
                                        account_id=str(fresh_row["account_id"]),
                                        display_name=fresh_row["display_name"] or f"账号 {fresh_row['account_id']}",
                                        access_token=decrypt_secret(fresh_row["access_token_cipher"]) or "",
                                        refresh_token=decrypt_secret(fresh_row["refresh_token_cipher"]) or "",
                                        client_id=fresh_row["client_id"] or "",
                                        client_secret=decrypt_secret(fresh_row["client_secret_cipher"]) if fresh_row["client_secret_cipher"] else "",
                                        expires_at=fresh_exp,
                                        site_id=fresh_row["site_id"],
                                        auth_domain=fresh_row["auth_domain"],
                                    )
                                    with self._cache_lock:
                                        self._token_cache[clean_aid] = token_inst
                                    return token_inst
                            except Exception:
                                pass
                        token_obj = self._refresh_token(
                            clean_aid,
                            client_id,
                            client_secret,
                            decrypt_secret(fresh_row["refresh_token_cipher"]) or refresh_token,
                            fresh_row["display_name"],
                            fresh_row["site_id"],
                        )
                        with self._cache_lock:
                            self._token_cache[clean_aid] = token_obj
                        return token_obj

            res_token = AccountToken(
                account_id=str(row["account_id"]),
                display_name=row["display_name"] or f"账号 {row['account_id']}",
                access_token=access_token or "",
                refresh_token=refresh_token or "",
                client_id=client_id,
                client_secret=client_secret,
                expires_at=expires_at,
                site_id=row["site_id"],
                auth_domain=row["auth_domain"],
            )
            with self._cache_lock:
                self._token_cache[clean_aid] = res_token
            return res_token
        finally:
            conn.close()

    def _refresh_token(
        self,
        account_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        display_name: str | None = None,
        site_id: str | None = None,
    ) -> AccountToken:
        """Call official POST /oauth/token to refresh access token silently."""
        url = f"{self.API_BASE}/oauth/token"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        body_params = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
        data = urllib.parse.urlencode(body_params).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            lower_body = err_body.lower()
            if err.code in (400, 401) and any(kw in lower_body for kw in ("invalid_grant", "invalid_client", "unauthorized", "revoked")):
                raise OAuthInvalidGrantError(account_id, display_name or "", err_body) from err
            raise RuntimeError(f"刷新店铺 {account_id} 授权失败: HTTP {err.code} {err_body}") from err
        except OAuthInvalidGrantError:
            raise
        except Exception as err:
            raise RuntimeError(f"刷新店铺 {account_id} 授权网络异常: {err}") from err

        new_access = str(result.get("access_token") or "")
        new_refresh = str(result.get("refresh_token") or refresh_token)
        expires_in = int(result.get("expires_in") or 21600)  # default 6h
        new_exp = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
        ).isoformat()
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Update in database
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE oauth_tokens
                SET access_token_cipher = ?,
                    refresh_token_cipher = ?,
                    expires_at = ?,
                    updated_at = ?
                WHERE account_id = ?
            """, (
                encrypt_secret(new_access),
                encrypt_secret(new_refresh),
                new_exp,
                now_str,
                str(account_id),
            ))
            conn.commit()
        finally:
            conn.close()

        tok = AccountToken(
            account_id=str(account_id),
            display_name=display_name or f"账号 {account_id}",
            access_token=new_access,
            refresh_token=new_refresh,
            client_id=client_id,
            client_secret=client_secret,
            expires_at=new_exp,
            site_id=site_id,
        )
        with self._cache_lock:
            self._token_cache[str(account_id).strip()] = tok
        return tok
