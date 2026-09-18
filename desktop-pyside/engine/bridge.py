from __future__ import annotations

import datetime
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .auth import AuthManager, OAuthInvalidGrantError
from .client import MercadoClient
from .crypto import decrypt_secret, encrypt_secret, get_data_dir
from .executor import ActionExecutor, ExecutionProgress
from .webhook_worker import WebhookWorker


class EngineBridge:
    """In-memory API dispatcher that handles all PySide UI requests natively without HTTP or Node.js."""
    _instance: "EngineBridge" | None = None

    @classmethod
    def get_instance(cls) -> "EngineBridge":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def dispatch_sync(cls, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return cls.get_instance().handle_request(method, path, body)

    def __init__(self):
        self.auth = AuthManager()
        self.client = MercadoClient(self.auth)
        self.executor = ActionExecutor(self.auth, self.client)
        self.webhook = WebhookWorker(self.auth, self.client)
        self.settings_file = get_data_dir() / "settings.json"

        self._prepares: dict[str, dict[str, Any]] = {}
        self._groups: dict[str, dict[str, Any]] = {}
        self._group_cancel_flags: dict[str, bool] = {}
        self._full_data_jobs: dict[str, dict[str, Any]] = {}
        self._oauth_states: dict[str, dict[str, Any]] = {}

    def _read_settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        if self.settings_file.exists():
            try:
                settings = json.loads(self.settings_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Check if settings has real discounts; if missing or legacy defaults (5/6), look up latest task
        latest_task: dict[str, Any] = {}
        try:
            tasks = self.executor.list_tasks(1)
            if tasks:
                latest_task = tasks[0]
        except Exception:
            pass
        latest_discount = latest_task.get("discount_percent") or 28
        latest_seller = latest_task.get("seller_discount_percent") or latest_discount
        latest_official = latest_task.get("official_discount_percent") or latest_discount

        defaults = {
            "sellerDefaultDiscount": int(latest_seller),
            "officialDefaultDiscount": int(latest_official),
            "autoShutdownAfterExecution": False,
            "autoRepriceOnWebhook": True,
        }
        for k, v in defaults.items():
            if k not in settings:
                settings[k] = v
            elif k == "sellerDefaultDiscount" and settings[k] == 5 and latest_seller != 5:
                settings[k] = int(latest_seller)
            elif k == "officialDefaultDiscount" and settings[k] == 6 and latest_official != 6:
                settings[k] = int(latest_official)
        return settings

    def _save_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        current = self._read_settings()
        current.update(updates)
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        return current

    def handle_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dispatch route matching GET /api/... or POST /api/... to the native Python engine."""
        method = method.upper()
        parsed = urllib.parse.urlparse(path)
        route = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Health
        if route == "/api/health":
            return {
                "ok": True,
                "status": "ok",
                "service": "native-python-engine",
                "product": "mercado-discount-manager",
                "protocol_version": "3",
                "build_fingerprint": "native-python-v2.0.17",
            }

        # 2. Settings
        if route == "/api/settings":
            if method == "POST":
                saved = self._save_settings(body or {})
                return {"ok": True, "settings": saved}
            return {"settings": self._read_settings()}

        # 3. Accounts
        if route == "/api/accounts":
            accounts = self.auth.list_accounts()
            return {"accounts": accounts}

        # 4. Sites
        if route.startswith("/api/accounts/") and route.endswith("/sites"):
            parts = route.split("/")
            account_id = parts[3]
            sites = self.auth.list_sites(account_id)
            return {"sites": sites}

        # 5. Promotions
        if route.startswith("/api/accounts/") and route.endswith("/promotions"):
            parts = route.split("/")
            account_id = parts[3]
            site_id = query.get("siteId", [""])[0]
            sites = self.auth.list_sites(account_id)
            if site_id:
                matched = [s for s in sites if s["site_id"] == site_id]
                c_uid = matched[0]["child_user_id"] if matched else (sites[0]["child_user_id"] if sites else "")
            else:
                c_uid = sites[0]["child_user_id"] if sites else ""

            if c_uid:
                try:
                    promos = self.client.get_seller_promotions(account_id, c_uid)
                    return {"promotions": promos}
                except Exception:
                    return {"promotions": []}
            return {"promotions": []}

        if route.startswith("/api/accounts/") and route.endswith("/promotions/fetch") and method == "POST":
            account_id = route.split("/")[3]
            sites = self.auth.list_sites(account_id)
            total = 0
            for s in sites:
                c_uid = s.get("child_user_id")
                if c_uid:
                    try:
                        promos = self.client.get_seller_promotions(account_id, c_uid)
                        total += len(promos)
                    except Exception:
                        pass
            return {"ok": True, "total": total}

        # 6. Today Global Discount
        if route == "/api/today/global-discount":
            settings = self._read_settings()
            seller = settings.get("sellerDefaultDiscount", 28)
            official = settings.get("officialDefaultDiscount", 28)
            return {
                "discount": {
                    "seller": seller,
                    "official": official,
                    "seller_discount": seller,
                    "official_discount": official,
                }
            }

        # 7. Today Decision
        if route == "/api/today/decision":
            return {
                "ok": True,
                "decision": {
                    "action": "enroll",
                    "reason": "新周期：批量报活动",
                },
                "status": "ready",
            }

        # 8. Tasks History
        if route == "/api/tasks":
            limit = int(query.get("limit", [50])[0])
            tasks = self.executor.list_tasks(limit)
            return {"tasks": tasks}

        if route == "/api/tasks/details":
            task_ids_param = query.get("taskIds") or query.get("task_ids") or []
            task_ids = []
            for p in task_ids_param:
                for v in p.split(","):
                    v = v.strip()
                    if v.isdigit():
                        task_ids.append(int(v))
            details = []
            if task_ids:
                conn = self.executor._get_conn()
                try:
                    cur = conn.cursor()
                    placeholders = ",".join("?" for _ in task_ids)
                    cur.execute(f"SELECT id, summary_json FROM promo_tasks WHERE id IN ({placeholders})", task_ids)
                    for r in cur.fetchall():
                        summary_str = r[1]
                        if summary_str:
                            try:
                                sm = json.loads(summary_str)
                                act_list = sm.get("activity_details") or []
                                details.extend(act_list)
                            except Exception:
                                pass
                finally:
                    conn.close()
            return {"ok": True, "details": details}

        if route == "/api/tasks/items":
            task_ids_param = query.get("task_ids") or query.get("taskIds") or []
            task_ids = []
            for p in task_ids_param:
                for v in p.split(","):
                    v = v.strip()
                    if v.isdigit():
                        task_ids.append(int(v))
            failed_items = []
            total_items = 0
            if task_ids:
                conn = self.executor._get_conn()
                try:
                    cur = conn.cursor()
                    placeholders = ",".join("?" for _ in task_ids)
                    cur.execute(f"SELECT id, summary_json, total_count FROM promo_tasks WHERE id IN ({placeholders})", task_ids)
                    for r in cur.fetchall():
                        total_items += int(r[2] or 0)
                        summary_str = r[1]
                        if summary_str:
                            try:
                                sm = json.loads(summary_str)
                                f_list = sm.get("failed_items") or []
                                failed_items.extend(f_list)
                            except Exception:
                                pass
                finally:
                    conn.close()
            return {"ok": True, "items": {"failed_items": failed_items, "unique_item_count": total_items}}

        # 8.5 Item status & Targeted Refresh
        if route.startswith("/api/items/") and route.endswith("/status"):
            item_id = route.split("/")[3].strip().upper()
            conn = self.executor._get_conn()
            actions = []
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT * FROM promo_action_results
                    WHERE item_id = ?
                    ORDER BY id DESC
                    LIMIT 50
                """, (item_id,))
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    acc_id = str(r.get("account_id") or "")
                    r["account_name"] = self.executor._get_store_alias(acc_id)
                    actions.append(r)
            finally:
                conn.close()
            return {"ok": True, "item_id": item_id, "actions": actions, "items": []}

        if route == "/api/items/targeted-refresh" and method == "POST":
            payload = body or {}
            item_ids = payload.get("itemIds") or payload.get("item_ids") or []
            account_ids = payload.get("accountIds") or payload.get("account_ids") or [a["account_id"] for a in self.auth.list_accounts()]
            results = []
            success_count = 0
            for itm in item_ids:
                itm_id = str(itm).strip().upper()
                ok = False
                price = None
                cand_count = 0
                err_msg = None
                for acc_id in account_ids:
                    try:
                        raw = self.client.get_item_detail(str(acc_id), itm_id)
                        if raw and raw.get("id"):
                            price = raw.get("original_price") or raw.get("price")
                            ok = True
                            success_count += 1
                            break
                    except Exception as e:
                        err_msg = str(e)
                results.append({
                    "item_id": itm_id,
                    "ok": ok,
                    "price": price,
                    "candidate_count": cand_count,
                    "error": err_msg if not ok else None,
                })
            return {
                "ok": True,
                "total_count": len(item_ids),
                "refreshed_count": success_count,
                "results": results,
            }

        if route.endswith("/post-cancel-refresh") and method == "POST":
            return {"ok": True, "refreshed": 1}

        # 8.6 OAuth Start & Complete
        if route in ("/api/oauth/start", "/api/oauth/start/from-config") and method == "POST":
            payload = body or {}
            client_id = str(payload.get("clientId") or payload.get("client_id") or "").strip()
            client_secret = str(payload.get("clientSecret") or payload.get("client_secret") or "").strip()
            redirect_uri = str(payload.get("redirectUri") or payload.get("redirect_uri") or "https://127.0.0.1/callback").strip()
            if not client_id or not client_secret:
                conn = self.auth._get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT client_id, client_secret_cipher FROM oauth_tokens WHERE client_id IS NOT NULL AND client_id != '' LIMIT 1")
                    row = cur.fetchone()
                    if row:
                        client_id = client_id or row["client_id"]
                        if not client_secret and row["client_secret_cipher"]:
                            client_secret = decrypt_secret(row["client_secret_cipher"])
                finally:
                    conn.close()

            if not client_id:
                return {"ok": False, "error": "请先在设置中填写美客多应用 Client ID"}

            state = uuid.uuid4().hex
            self._oauth_states[state] = {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "target_account_id": payload.get("accountId") or payload.get("account_id"),
            }
            auth_url = (
                f"https://auth.mercadolibre.com.mx/authorization"
                f"?response_type=code&client_id={urllib.parse.quote(client_id)}"
                f"&redirect_uri={urllib.parse.quote(redirect_uri)}&state={urllib.parse.quote(state)}"
            )
            return {"ok": True, "authorizationUrl": auth_url}

        if route == "/api/oauth/complete-callback" and method == "POST":
            payload = body or {}
            raw_callback = str(payload.get("callbackUrl") or payload.get("callback") or payload.get("callbackText") or "").strip()
            code = ""
            state = ""
            if "?" in raw_callback:
                parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_callback).query)
                code = parsed_qs.get("code", [""])[0]
                state = parsed_qs.get("state", [""])[0]
            elif raw_callback.startswith("TG-"):
                code = raw_callback
            else:
                code = raw_callback

            if not code:
                return {"ok": False, "error": "未能从输入中提取到有效的授权 Code"}

            state_data = self._oauth_states.get(state) or {}
            client_id = state_data.get("client_id")
            client_secret = state_data.get("client_secret")
            redirect_uri = state_data.get("redirect_uri") or "https://127.0.0.1/callback"

            if not client_id or not client_secret:
                conn = self.auth._get_connection()
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT client_id, client_secret_cipher FROM oauth_tokens WHERE client_id IS NOT NULL AND client_id != '' LIMIT 1")
                    row = cur.fetchone()
                    if row:
                        client_id = client_id or row["client_id"]
                        if not client_secret and row["client_secret_cipher"]:
                            client_secret = decrypt_secret(row["client_secret_cipher"])
                finally:
                    conn.close()

            if not client_id or not client_secret:
                return {"ok": False, "error": "缺少 Client ID 或 Client Secret，请重新在设置中发起授权"}

            url = f"{self.client.API_BASE}/oauth/token"
            data = urllib.parse.urlencode({
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    token_res = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                err_body = err.read().decode("utf-8", errors="replace")
                return {"ok": False, "error": f"美客多授权换取 Token 失败: HTTP {err.code} {err_body}"}
            except Exception as err:
                return {"ok": False, "error": f"请求美客多授权接口网络异常: {err}"}

            user_id = str(token_res.get("user_id") or "")
            access_token = str(token_res.get("access_token") or "")
            refresh_token = str(token_res.get("refresh_token") or "")
            expires_in = int(token_res.get("expires_in") or 21600)
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            expires_at = (now_dt + datetime.timedelta(seconds=expires_in)).isoformat()
            now_str = now_dt.isoformat()

            conn = self.auth._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO oauth_tokens (
                        account_id, display_name, site_id, access_token_cipher,
                        refresh_token_cipher, client_id, client_secret_cipher,
                        expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        access_token_cipher = excluded.access_token_cipher,
                        refresh_token_cipher = excluded.refresh_token_cipher,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at
                """, (
                    user_id,
                    f"店铺 {user_id}",
                    "CBT",
                    encrypt_secret(access_token),
                    encrypt_secret(refresh_token),
                    client_id,
                    encrypt_secret(client_secret),
                    expires_at,
                    now_str,
                ))
                cur.execute("""
                    INSERT INTO account_profiles (account_id, display_name, site_id, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        updated_at = excluded.updated_at
                """, (user_id, f"店铺 {user_id}", "CBT", now_str))
                conn.commit()
            finally:
                conn.close()

            return {
                "ok": True,
                "account": {
                    "account_id": user_id,
                    "store_name": f"店铺 {user_id}",
                }
            }

        # 9. Startup Refresh Status
        if route == "/api/startup-refresh/status":
            accounts = [a["account_id"] for a in self.auth.list_accounts()]
            readiness = {
                "ready": True,
                "ready_accounts": accounts,
                "blocked_account_ids": [],
            }
            return {
                "ok": True,
                "status": "ok",
                "refresh": {
                    "status": "ok",
                    "percent": 100,
                    "busy": False,
                    "stage": "idle",
                    "stage_label": "启动就绪",
                    "readiness": readiness,
                },
                "readiness": readiness,
                "busy": False,
            }
        if route in ("/api/startup-refresh/start", "/api/startup-refresh/stop"):
            accounts = [a["account_id"] for a in self.auth.list_accounts()]
            readiness = {
                "ready": True,
                "ready_accounts": accounts,
                "blocked_account_ids": [],
            }
            return {
                "ok": True,
                "status": "ok",
                "refresh": {
                    "status": "ok",
                    "percent": 100,
                    "busy": False,
                    "stage": "idle",
                    "stage_label": "启动就绪",
                    "readiness": readiness,
                },
                "readiness": readiness,
            }

        # 10. Auto-reprice / Webhook Status
        if route == "/api/auto-reprice/status":
            return {"running": self.webhook.is_running()}

        # 11. Concurrency benchmark
        if route == "/api/concurrency-benchmark/results":
            return {"results": {"read": 50, "write": 50}}

        # 11.5 Full-Data GET Jobs (只读全量GET数据)
        if route == "/api/full-data/start" and method == "POST":
            for j in self._full_data_jobs.values():
                if j.get("status") == "running":
                    return {"ok": True, "job_id": j["id"], "already_running": True}

            job_id = f"job_{uuid.uuid4().hex[:12]}"
            job_data = {
                "id": job_id,
                "status": "running",
                "logs": [],
                "progress": {"total_promotions": 0, "total_started": 0, "total_candidate": 0},
                "created_at": datetime.datetime.now().isoformat(),
            }
            self._full_data_jobs[job_id] = job_data

            def run_full_get():
                acc_ids = (body or {}).get("accountIds") or [a["account_id"] for a in self.auth.list_accounts()]
                tot_p = 0
                tot_s = 0
                tot_c = 0
                for acc_id in acc_ids:
                    store_name = acc_id
                    try:
                        store_name = self.auth.get_token(acc_id).store_name or acc_id
                    except Exception:
                        pass
                    job_data["logs"].append(f"[{store_name}] 开始扫描所有分站点活动...")
                    sites = self.auth.list_sites(acc_id)
                    for s in sites:
                        c_uid = s["child_user_id"]
                        s_id = s["site_id"]
                        try:
                            promos = self.client.get_seller_promotions(acc_id, c_uid)
                            tot_p += len(promos)
                            job_data["logs"].append(f"[{store_name}][{s_id}] 发现 {len(promos)} 个活动")
                            for p in promos:
                                p_id = str(p.get("id") or p.get("promotion_id") or "")
                                p_name = p.get("name") or p.get("title") or p_id
                                started = self.client.get_promotion_items(acc_id, c_uid, p_id, status="started", limit=50)
                                cand = self.client.get_promotion_items(acc_id, c_uid, p_id, status="candidate", limit=50)
                                tot_s += len(started)
                                tot_c += len(cand)
                                job_data["logs"].append(f"[{store_name}][{s_id}] 活动 [{p_name}]: 已报 {len(started)} 件，可报 {len(cand)} 件")
                        except Exception as err:
                            job_data["logs"].append(f"[{store_name}][{s_id}] 扫描活动异常: {err}")
                job_data["progress"] = {"total_promotions": tot_p, "total_started": tot_s, "total_candidate": tot_c}
                job_data["status"] = "completed"
                job_data["logs"].append("【全量GET数据完成】所有店铺及分站点活动已读取完毕。")

            threading.Thread(target=run_full_get, daemon=True, name=f"FullGet-{job_id}").start()
            return {"ok": True, "job_id": job_id}

        if route.startswith("/api/full-data/jobs/"):
            job_id = route.split("/")[-1]
            if job_id in self._full_data_jobs:
                job = self._full_data_jobs[job_id]
                after = int(query.get("after", [0])[0])
                all_logs = job.get("logs", [])
                slice_logs = all_logs[after:]
                return {
                    "ok": True,
                    "job": {**job, "logs": slice_logs},
                    "total_logs": len(all_logs),
                }
            return {"ok": False, "job": None}

        # 12. Execution active groups / submissions
        if route == "/api/execution/groups/active":
            # Return any currently running group
            running = [g for g in self._groups.values() if g.get("status") == "running"]
            return {"group": running[0] if running else None}
        if route == "/api/execution/submissions/active":
            return {"submission": None}

        # 13. Submissions Prepare
        if route == "/api/execution/submissions/prepare" and method == "POST":
            prepare_id = f"prep_{uuid.uuid4().hex[:12]}"
            payload = body or {}
            prep_data = {
                "prepare_id": prepare_id,
                "state": "prepared",
                "confirmation_token": f"tok_{uuid.uuid4().hex[:8]}",
                "resolved_action": payload.get("action") or "enroll",
                "payload": payload,
                "created_at": datetime.datetime.now().isoformat(),
            }
            self._prepares[prepare_id] = prep_data
            return {"ok": True, "prepare": prep_data}

        # 14. Query Submission Status
        if route.startswith("/api/execution/submissions/") and not route.endswith("/commit") and not route.endswith("/cancel"):
            prepare_id = route.split("/")[-1]
            if prepare_id in self._prepares:
                return {"ok": True, "prepare": self._prepares[prepare_id]}
            return {"ok": False, "prepare": None}

        # 15. Submissions Commit (Start background execution)
        if route.startswith("/api/execution/submissions/") and route.endswith("/commit") and method == "POST":
            prepare_id = route.split("/")[-2]
            prep_data = self._prepares.get(prepare_id)
            if not prep_data:
                return {"error": "未找到准备记录", "status": 404}

            group_id = f"grp_{uuid.uuid4().hex[:12]}"
            seller_disc = float(prep_data["payload"].get("sellerDiscountPercent") or prep_data["payload"].get("seller_discount") or 28.0)
            official_disc = float(prep_data["payload"].get("officialDiscountPercent") or prep_data["payload"].get("official_discount") or 28.0)
            group_data = {
                "id": group_id,
                "status": "running",
                "prepare_id": prepare_id,
                "action": prep_data["resolved_action"],
                "created_at": datetime.datetime.now().isoformat(),
                "result": {"success": 0, "failed": 0, "skipped": 0, "pending": 0, "accounting_complete": False},
                "scope": {
                    "account_ids": prep_data["payload"].get("account_ids") or prep_data["payload"].get("accountIds") or [],
                    "site_id": prep_data["payload"].get("filters", {}).get("site_id", "") or prep_data["payload"].get("filters", {}).get("siteId", ""),
                    "seller_discount_percent": seller_disc,
                    "official_discount_percent": official_disc,
                },
                "seller_discount_percent": seller_disc,
                "official_discount_percent": official_disc,
            }
            self._groups[group_id] = group_data
            self._group_cancel_flags[group_id] = False

            # Launch in background thread
            thread = threading.Thread(
                target=self._run_group_worker,
                args=(group_id, prep_data["payload"]),
                daemon=True,
                name=f"Executor-{group_id}",
            )
            thread.start()

            return {"ok": True, "group": group_data}

        # 16. Poll Group Status
        if route.startswith("/api/execution/groups/") and not route.endswith("/cancel"):
            group_id = route.split("/")[-1]
            if group_id in self._groups:
                return {"ok": True, "group": self._groups[group_id]}
            reconstructed = self._reconstruct_group_from_db(group_id)
            if reconstructed:
                return {"ok": True, "group": reconstructed}
            return {"ok": False, "group": None}

        # 17. Cancel Group
        if route.startswith("/api/execution/groups/") and route.endswith("/cancel"):
            group_id = route.split("/")[-2]
            self._group_cancel_flags[group_id] = True
            if group_id in self._groups:
                self._groups[group_id]["status"] = "cancelled"
            return {"ok": True}

        # Default catch-all
        return {"ok": True, "data": {}}

    def _reconstruct_group_from_db(self, group_id: str) -> dict[str, Any] | None:
        try:
            conn = self.executor._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM promo_tasks WHERE execution_group_id = ?", (group_id,))
                rows = [dict(r) for r in cur.fetchall()]
                if not rows:
                    return None
                total_success = sum(int(r.get("success_count") or 0) for r in rows)
                total_failed = sum(int(r.get("failed_count") or 0) for r in rows)
                total_skipped = sum(int(r.get("skipped_count") or 0) for r in rows)
                total_items = sum(int(r.get("total_count") or 0) for r in rows)
                first = rows[0]
                summary: dict[str, Any] = {}
                if first.get("summary_json"):
                    try:
                        summary = json.loads(first["summary_json"])
                    except Exception:
                        pass
                seller_disc = summary.get("seller_discount_percent") or first.get("discount_percent") or 28
                official_disc = summary.get("official_discount_percent") or first.get("discount_percent") or 28
                return {
                    "id": group_id,
                    "status": "completed",
                    "action": first.get("action") or "enroll",
                    "created_at": first.get("created_at"),
                    "finished_at": first.get("updated_at") or first.get("created_at"),
                    "scope": {
                        "account_ids": [r.get("account_id") for r in rows if r.get("account_id")],
                        "site_id": "",
                        "seller_discount_percent": seller_disc,
                        "official_discount_percent": official_disc,
                    },
                    "seller_discount_percent": seller_disc,
                    "official_discount_percent": official_disc,
                    "result": {
                        "store_count": len(rows),
                        "success": total_success,
                        "failed": total_failed,
                        "skipped": total_skipped,
                        "total": total_items,
                        "accounting_complete": True,
                    },
                }
            finally:
                conn.close()
        except Exception:
            return None

    def _run_group_worker(self, group_id: str, payload: dict[str, Any]) -> None:
        """Run execution synchronously on background worker thread with multi-store concurrency (三店同步)."""
        import concurrent.futures

        start_time = time.time()
        account_ids = payload.get("accountIds") or payload.get("account_ids") or []
        filters = payload.get("filters") or {}
        site_id = payload.get("site_id") or payload.get("siteId") or filters.get("siteId") or filters.get("site_id") or ""
        target_item_ids = payload.get("itemIds") or payload.get("item_ids") or []
        mode = payload.get("requested_action") or payload.get("action") or "enroll"
        if mode == "auto":
            mode = "enroll"
        seller_discount = float(payload.get("sellerDiscountPercent") or payload.get("seller_discount") or 28.0)
        official_discount = float(payload.get("officialDiscountPercent") or payload.get("official_discount") or 28.0)

        # Persist selected discount
        self._save_settings({
            "sellerDefaultDiscount": int(seller_discount) if seller_discount.is_integer() else seller_discount,
            "officialDefaultDiscount": int(official_discount) if official_discount.is_integer() else official_discount,
        })

        # Initialize children for real-time log polling
        children_map: dict[str, dict[str, Any]] = {}
        for acc_id in account_ids:
            store_name = acc_id
            try:
                t_info = self.auth.get_token(acc_id)
                store_name = t_info.store_name or acc_id
            except Exception:
                pass
            children_map[acc_id] = {
                "id": f"job_{acc_id}",
                "job_id": f"job_{acc_id}",
                "account_id": acc_id,
                "store_name": store_name,
                "status": "running",
                "user_logs": [],
            }

        if group_id in self._groups:
            self._groups[group_id]["children"] = list(children_map.values())

        total_success = 0
        total_failed = 0
        total_skipped = 0
        total_items = 0
        lock = threading.Lock()
        per_store_results: dict[str, dict[str, Any]] = {}

        def log_for_store(acc_id: str, msg: str):
            child = children_map.get(acc_id)
            if child:
                with lock:
                    child["user_logs"].append({
                        "id": f"log_{uuid.uuid4().hex[:8]}",
                        "message": msg,
                        "at": datetime.datetime.now().isoformat(),
                    })

        def run_store(acc_id: str):
            nonlocal total_success, total_failed, total_skipped, total_items
            if self._group_cancel_flags.get(group_id):
                return
            store_start = time.time()
            try:
                res = self.executor.run_execution(
                    account_id=str(acc_id),
                    site_id=str(site_id),
                    mode=str(mode),
                    seller_discount=seller_discount,
                    official_discount=official_discount,
                    on_log=lambda m: log_for_store(str(acc_id), m),
                    is_cancelled=lambda: self._group_cancel_flags.get(group_id, False),
                    group_id=group_id,
                    target_item_ids=target_item_ids,
                    filters=filters,
                )
            except Exception as e:
                log_for_store(str(acc_id), f"执行出现异常: {e}")
                res = {"status": "failed", "success": 0, "failed": 1, "skipped": 0, "total": 0}

            store_elapsed = time.time() - store_start
            if "duration_text" not in res:
                if store_elapsed >= 60:
                    res["duration_text"] = f"{int(store_elapsed // 60)}分{int(store_elapsed % 60)}秒"
                else:
                    res["duration_text"] = f"{int(store_elapsed)}秒"
            res["elapsed_seconds"] = round(store_elapsed, 1)

            child = children_map.get(acc_id)
            if child:
                child["status"] = "completed"
                if res.get("oauth_expired"):
                    child["oauth_expired"] = True

            with lock:
                per_store_results[str(acc_id)] = res
                total_success += res.get("success", 0)
                total_failed += res.get("failed", 0)
                total_skipped += res.get("skipped", 0)
                total_items += res.get("total", 0)

        # Multi-store concurrent execution: 三店同步启动
        if len(account_ids) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(account_ids), 3)) as pool:
                futures = [pool.submit(run_store, str(acc_id)) for acc_id in account_ids]
                concurrent.futures.wait(futures)
        elif account_ids:
            run_store(str(account_ids[0]))

        # Mark group finished
        is_canc = self._group_cancel_flags.get(group_id, False)
        stores_list = []
        any_oauth_expired = False
        expired_acc_id = None
        expired_store = None
        for acc_id in account_ids:
            r = per_store_results.get(acc_id, {})
            child = children_map.get(acc_id, {})
            is_expired = bool(r.get("oauth_expired"))
            if is_expired:
                any_oauth_expired = True
                expired_acc_id = acc_id
                expired_store = child.get("store_name") or acc_id
            store_failed = int(r.get("failed", 0))
            store_success = int(r.get("success", 0))
            if is_canc or r.get("status") == "cancelled":
                s_status = "cancelled"
            elif is_expired or (store_failed > 0 and store_success == 0):
                s_status = "failed"
            elif store_failed > 0 and store_success > 0:
                s_status = "partial_or_failed"
            else:
                s_status = "completed"
            stores_list.append({
                "account_id": acc_id,
                "store_name": child.get("store_name") or acc_id,
                "status": s_status,
                "success": r.get("success", 0),
                "failed": r.get("failed", 0),
                "skipped": r.get("skipped", 0),
                "total": r.get("total", 0),
                "elapsed_seconds": r.get("elapsed_seconds", 0),
                "duration_text": r.get("duration_text", ""),
                "oauth_expired": is_expired,
            })

        if is_canc:
            status = "cancelled"
        elif any_oauth_expired or (total_failed > 0 and total_success == 0):
            status = "failed"
        else:
            status = "completed"

        total_elapsed = time.time() - start_time
        if total_elapsed >= 60:
            total_dur_text = f"{int(total_elapsed // 60)}分{int(total_elapsed % 60)}秒"
        else:
            total_dur_text = f"{int(total_elapsed)}秒"

        if group_id in self._groups:
            self._groups[group_id]["status"] = status
            if any_oauth_expired:
                self._groups[group_id]["oauth_expired"] = True
                self._groups[group_id]["expired_account"] = expired_acc_id
                self._groups[group_id]["expired_store_name"] = expired_store
            self._groups[group_id]["result"] = {
                "store_count": len(account_ids),
                "stores": stores_list,
                "success": total_success,
                "failed": total_failed,
                "skipped": total_skipped,
                "total": total_items,
                "pending": 0,
                "accounting_complete": True,
                "elapsed_seconds": round(total_elapsed, 1),
                "duration_text": total_dur_text,
            }
            self._groups[group_id]["children"] = list(children_map.values())
            self._groups[group_id]["updated_at"] = datetime.datetime.now().isoformat()
