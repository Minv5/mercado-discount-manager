from __future__ import annotations

import datetime
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .crypto import decrypt_secret, encrypt_secret, get_data_dir


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

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def list_accounts(self) -> list[dict[str, Any]]:
        """List all authorized accounts with their display names and status."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.account_id, p.display_name, p.site_id, t.expires_at, t.updated_at
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
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT account_id, display_name, site_id, access_token_cipher, refresh_token_cipher,
                       client_id, client_secret_cipher, expires_at, auth_domain
                FROM oauth_tokens
                WHERE account_id = ?
            """, (str(account_id),))
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
                    # Clean ISO format
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
                token_obj = self._refresh_token(account_id, client_id, client_secret, refresh_token, row["display_name"], row["site_id"])
                return token_obj

            return AccountToken(
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
            raise RuntimeError(f"刷新店铺 {account_id} 授权失败: HTTP {err.code} {err_body}") from err
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

        return AccountToken(
            account_id=str(account_id),
            display_name=display_name or f"账号 {account_id}",
            access_token=new_access,
            refresh_token=new_refresh,
            client_id=client_id,
            client_secret=client_secret,
            expires_at=new_exp,
            site_id=site_id,
        )
