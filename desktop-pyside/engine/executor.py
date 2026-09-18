from __future__ import annotations

import datetime
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .auth import AuthManager, OAuthInvalidGrantError
from .client import MercadoClient
from .crypto import get_data_dir
from .pricing import calculate_deal_price, extract_item_net_proceeds


@dataclass
class ExecutionProgress:
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    current_item: str = ""
    status: str = "running"
    message: str = ""


SITE_NAMES = {
    "MLB": "巴西站",
    "MLM": "墨西哥站",
    "MLA": "阿根廷站",
    "MLC": "智利站",
    "MCO": "哥伦比亚站",
    "MLU": "乌拉圭站",
    "MPE": "秘鲁站",
    "MEC": "厄瓜多尔站",
}


class ActionExecutor:
    def __init__(self, auth_manager: AuthManager | None = None, client: MercadoClient | None = None):
        self.auth = auth_manager or AuthManager()
        self.client = client or MercadoClient(self.auth)
        self.db_path = get_data_dir() / "discount-manager.sqlite"

    def _get_conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.row_factory = sqlite3.Row
        return conn

    def _get_store_alias(self, account_id: str) -> str:
        acc_str = str(account_id)
        settings_file = self.db_path.parent / "settings.json"
        if settings_file.exists():
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                aliases = data.get("storeAliases") or {}
                if aliases.get(acc_str):
                    return str(aliases[acc_str])
            except Exception:
                pass
        try:
            tok = self.auth.get_token(acc_str)
            if tok.display_name:
                return tok.display_name
        except Exception:
            pass
        return f"店铺 {acc_str}"

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        """List execution records for the workbench table."""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM promo_tasks
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            aliases = {}
            settings_file = self.db_path.parent / "settings.json"
            if settings_file.exists():
                try:
                    data = json.loads(settings_file.read_text(encoding="utf-8"))
                    aliases = data.get("storeAliases") or {}
                except Exception:
                    pass

            tasks = []
            for r in rows:
                t = dict(r)
                if t.get("summary_json"):
                    try:
                        summary = json.loads(t["summary_json"])
                        t["summary"] = summary
                        if isinstance(summary, dict):
                            for k, v in summary.items():
                                if k not in t or t[k] is None:
                                    t[k] = v
                    except Exception:
                        pass
                acc_id = str(t.get("account_id") or "")
                if not t.get("store_name"):
                    t["store_name"] = aliases.get(acc_id) or f"店铺 {acc_id}"
                if t.get("discount") is None and t.get("discount_percent") is not None:
                    t["discount"] = t["discount_percent"]
                if t.get("seller_discount_percent") is None and t.get("discount_percent") is not None:
                    t["seller_discount_percent"] = t["discount_percent"]
                if t.get("official_discount_percent") is None and t.get("discount_percent") is not None:
                    t["official_discount_percent"] = t["discount_percent"]
                tasks.append(t)
            return tasks
        finally:
            conn.close()

    def run_execution(
        self,
        account_id: str,
        site_id: str,
        mode: str,
        seller_discount: float,
        official_discount: float,
        on_log: Callable[[str], None] | None = None,
        on_progress: Callable[[ExecutionProgress], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        group_id: str | None = None,
        target_item_ids: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run execution for selected store and site across the requested mode with multi-site concurrency."""
        import concurrent.futures
        import threading
        import time

        start_time = time.time()
        log = on_log or (lambda m: None)
        report_progress = on_progress or (lambda p: None)
        cancelled = is_cancelled or (lambda: False)

        group_id = group_id or f"grp_{uuid.uuid4().hex[:12]}"
        target_item_set = {str(x).strip().upper() for x in target_item_ids if str(x).strip()} if target_item_ids else None
        flt = filters or {}

        store_name = self._get_store_alias(account_id)
        site_label = SITE_NAMES.get(site_id, site_id) if site_id else "全部站点"

        log(f"[{store_name}] 开始执行任务 [{mode}]，站点: {site_label}...")

        # 1. Match child users / sites
        all_sites = self.auth.list_sites(account_id)
        if site_id:
            target_sites = [s for s in all_sites if s["site_id"] == site_id]
        else:
            settings_file = self.db_path.parent / "settings.json"
            op_sites: set[str] = set()
            if settings_file.exists():
                try:
                    s_data = json.loads(settings_file.read_text(encoding="utf-8"))
                    raw_op = (s_data.get("operatingSites") or {}).get(str(account_id))
                    if isinstance(raw_op, (list, set, tuple)) and raw_op:
                        op_sites = {str(x).strip().upper() for x in raw_op if str(x).strip()}
                except Exception:
                    pass
            if op_sites:
                target_sites = [s for s in all_sites if s["site_id"].upper() in op_sites]
            else:
                target_sites = all_sites

        if not target_sites:
            log(f"[{store_name}] 未找到可执行的分站点子账号，任务结束。")
            return {"status": "empty", "total": 0, "success": 0, "failed": 0, "skipped": 0}

        progress = ExecutionProgress()
        progress_lock = threading.Lock()
        activity_lock = threading.Lock()
        all_results: list[dict[str, Any]] = []
        activity_details: list[dict[str, Any]] = []
        failed_items: list[dict[str, Any]] = []

        action = "enroll"
        if mode in ("update", "批量更新"):
            action = "update"
        elif mode in ("cancel", "批量取消"):
            action = "cancel"

        oauth_expired = False

        def process_item(item_cand: dict[str, Any], promo_info: dict[str, Any], s_id: str, c_uid: str, s_label: str) -> str:
            if cancelled():
                return "skipped"

            item_id = str(item_cand.get("id") or item_cand.get("item_id") or "")
            if not item_id:
                return "skipped"

            if target_item_set and item_id.upper() not in target_item_set:
                return "skipped"

            p_id = str(promo_info.get("id") or promo_info.get("promotion_id") or "")
            p_type = str(promo_info.get("type") or promo_info.get("promotion_type") or "DEAL").upper()
            p_name = promo_info.get("name") or promo_info.get("title") or p_id
            is_seller = (p_type == "SELLER_CAMPAIGN") or p_id.upper().startswith("C-")
            discount_pct = seller_discount if is_seller else official_discount
            offer_id = str(item_cand.get("offer_id") or "")

            stock_val = 5
            stock_raw = item_cand.get("stock")
            if isinstance(stock_raw, dict):
                stock_val = int(stock_raw.get("min") or stock_raw.get("minimum") or 5)
            elif isinstance(stock_raw, (int, float)):
                stock_val = int(stock_raw)

            with progress_lock:
                progress.total += 1
                progress.current_item = item_id

            if action == "cancel":
                try:
                    self.client.cancel_promotion_item(
                        account_id, c_uid, item_id, p_id, p_type, offer_id=offer_id
                    )
                    with progress_lock:
                        progress.success += 1
                        all_results.append({"item_id": item_id, "promotion_id": p_id, "status": "success"})
                        report_progress(progress)
                    log(f"[{store_name}][{s_label}] 商品 {item_id} 已退出活动 [{p_name}]")
                    return "success"
                except Exception as err:
                    with progress_lock:
                        progress.failed += 1
                        all_results.append({"item_id": item_id, "promotion_id": p_id, "status": "failed", "error": str(err)})
                        report_progress(progress)
                    with activity_lock:
                        failed_items.append({"item_id": item_id, "promotion_id": p_id, "promotion_name": p_name, "site_id": s_id, "reason": str(err)})
                    log(f"[{store_name}][{s_label}] 商品 {item_id} 退出活动失败: {err}")
                    return "failed"

            # Step A: Authoritative Real-time GET /marketplace/items/{id}
            try:
                raw_item = self.client.get_item_detail(account_id, item_id)
                item_info = extract_item_net_proceeds(raw_item)
            except Exception as err:
                with progress_lock:
                    progress.failed += 1
                    all_results.append({"item_id": item_id, "promotion_id": p_id, "status": "failed", "error": str(err)})
                    report_progress(progress)
                with activity_lock:
                    failed_items.append({"item_id": item_id, "promotion_id": p_id, "promotion_name": p_name, "site_id": s_id, "reason": str(err)})
                log(f"[{store_name}][{s_label}] 商品 {item_id} 抓取实时数据失败: {err}")
                return "failed"

            # Step B: Pricing calculation with shipping protection & floor guard
            pricing = calculate_deal_price(
                item_info=item_info,
                discount_percent=discount_pct,
                promotion_constraints=item_cand,
                promotion_type=p_type,
            )

            if not pricing.eligible:
                with progress_lock:
                    progress.skipped += 1
                    all_results.append({"item_id": item_id, "promotion_id": p_id, "status": "skipped", "reason": pricing.skip_reason})
                    report_progress(progress)
                log(f"[{store_name}][{s_label}] 商品 {item_id}：{pricing.skip_reason}")
                return "skipped"

            # Step C: Submit promotion enroll/update
            if cancelled():
                return "skipped"

            try:
                self.client.enroll_promotion_item(
                    account_id=account_id,
                    child_user_id=c_uid,
                    item_id=item_id,
                    promotion_id=p_id,
                    promotion_type=p_type,
                    deal_price=pricing.deal_price,
                    offer_id=offer_id,
                    original_price=pricing.original_price,
                    stock=stock_val,
                    action=action,
                )
                with progress_lock:
                    progress.success += 1
                    all_results.append({
                        "item_id": item_id,
                        "promotion_id": p_id,
                        "deal_price": pricing.deal_price,
                        "status": "success",
                    })
                    report_progress(progress)
                if p_type == "SMART":
                    log(f"[{store_name}][{s_label}] 提报成功: 商品 {item_id} 报入联合活动 [{p_name}] (卖家承担 {item_cand.get('seller_percentage', 0)}%)")
                else:
                    log(
                        f"[{store_name}][{s_label}] 提报成功: 商品 {item_id} 报入 [{p_name}] | "
                        f"原价 ${pricing.original_price:.2f} -> 折扣价 ${pricing.deal_price:.2f} (全额保运费 ${pricing.shipping_cost:.2f}, 目标净回款 ${pricing.target_net:.2f})"
                    )
                return "success"
            except Exception as err:
                err_msg = str(err)
                if "ERROR_CREDIBILITY_DISCOUNTED_PRICE" in err_msg:
                    reason = "平台公信力规则未通过(秒杀折后价未达近期销量门槛)"
                elif "ITEM_NOT_ELIGIBLE" in err_msg:
                    reason = "商品暂不满足该活动准入条件"
                elif "ALREADY" in err_msg.upper():
                    reason = "商品已在此活动中（自动跳过）"
                    with progress_lock:
                        progress.skipped += 1
                        all_results.append({"item_id": item_id, "promotion_id": p_id, "status": "skipped", "reason": reason})
                        report_progress(progress)
                    log(f"[{store_name}][{s_label}] 商品 {item_id} 已在活动 [{p_name}] 中，自动跳过")
                    return "skipped"
                else:
                    reason = err_msg

                with progress_lock:
                    progress.failed += 1
                    all_results.append({"item_id": item_id, "promotion_id": p_id, "status": "failed", "error": reason})
                    report_progress(progress)
                with activity_lock:
                    failed_items.append({"item_id": item_id, "promotion_id": p_id, "promotion_name": p_name, "site_id": s_id, "reason": reason})
                log(f"[{store_name}][{s_label}] 提报未完成: 商品 {item_id} 报入 [{p_name}] 平台反馈: {reason}")
                return "failed"

        def process_site(site_info: dict[str, Any]):
            nonlocal oauth_expired
            if cancelled():
                return
            c_uid = site_info["child_user_id"]
            s_id = site_info["site_id"]
            site_label = SITE_NAMES.get(s_id, s_id)
            log(f"[{store_name}][{site_label}] 扫描活动列表 (子账号: {c_uid})...")

            try:
                promotions = self.client.get_seller_promotions(account_id, c_uid)
            except OAuthInvalidGrantError as err:
                oauth_expired = True
                log(f"[{store_name}] 店铺授权失效: {err}")
                return
            except Exception as err:
                log(f"[{store_name}][{site_label}] 读取活动列表失败: {err}")
                return

            if not promotions:
                log(f"[{store_name}][{site_label}] 暂无可报活动。")
                return

            query_status = "started" if action in ("update", "cancel") else "candidate"

            for promo in promotions:
                if cancelled() or oauth_expired:
                    break
                p_id = str(promo.get("id") or promo.get("promotion_id") or "")
                p_type = str(promo.get("type") or promo.get("promotion_type") or "DEAL").upper()
                p_name = str(promo.get("name") or promo.get("title") or p_id)
                p_status = str(promo.get("status") or "").strip().lower()

                # 1. 过滤已结束/失效的活动
                if p_status in ("finished", "closed", "expired", "inactive"):
                    continue

                # 2. Filter out non-item payment-method promotions like Pix/Bank
                if p_type in ("BANK", "PAYMENT_METHOD"):
                    continue

                # 3. 自建活动与官方活动特征识别及过滤
                is_seller = (p_type == "SELLER_CAMPAIGN") or p_id.upper().startswith("C-")
                if flt:
                    if is_seller:
                        if flt.get("excludeSeller"):
                            continue
                        seller_names = flt.get("sellerActivityNames") or []
                        if seller_names:
                            seller_names_set = {str(x).strip() for x in seller_names if str(x).strip()}
                            if p_name not in seller_names_set and p_id not in seller_names_set:
                                continue
                    else:
                        if flt.get("excludeOfficial"):
                            continue
                        official_names = flt.get("officialActivityNames") or []
                        if official_names:
                            official_names_set = {str(x).strip() for x in official_names if str(x).strip()}
                            if p_name not in official_names_set and p_id not in official_names_set:
                                continue

                try:
                    candidates = self.client.get_promotion_items(
                        account_id, c_uid, p_id, status=query_status
                    )
                except OAuthInvalidGrantError as err:
                    oauth_expired = True
                    log(f"[{store_name}] 店铺授权失效: {err}")
                    break
                except Exception as err:
                    log(f"[{store_name}][{site_label}] 读取活动 [{p_name}] 商品候选失败: {err}")
                    continue

                if not candidates:
                    continue

                if target_item_set:
                    candidates = [
                        c for c in candidates
                        if str(c.get("id") or c.get("item_id") or "").strip().upper() in target_item_set
                    ]
                    if not candidates:
                        continue

                log(f"[{store_name}][{site_label}] 活动 [{p_name}] 找到 {len(candidates)} 个候选商品，并发核算处理...")

                p_success = 0
                p_failed = 0
                p_skipped = 0
                p_lock = threading.Lock()

                def wrapped_process_item(cand):
                    nonlocal p_success, p_failed, p_skipped
                    res = process_item(cand, promo, s_id, c_uid, site_label)
                    with p_lock:
                        if res == "success":
                            p_success += 1
                        elif res == "failed":
                            p_failed += 1
                        elif res == "skipped":
                            p_skipped += 1

                # Process items concurrently within this promotion!
                max_item_workers = min(len(candidates), 6)
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_item_workers) as item_pool:
                    futures = [
                        item_pool.submit(wrapped_process_item, cand)
                        for cand in candidates
                    ]
                    concurrent.futures.wait(futures)

                with activity_lock:
                    activity_details.append({
                        "account_id": str(account_id),
                        "store_name": store_name,
                        "site_id": s_id,
                        "site_name": site_label,
                        "promotion_id": p_id,
                        "promotion_name": p_name,
                        "promotion_type": p_type,
                        "action": action,
                        "total_count": len(candidates),
                        "success_count": p_success,
                        "failed_count": p_failed,
                        "skipped_count": p_skipped,
                        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "unique_item_count": len(candidates),
                        "relation_count": len(candidates),
                        "request_success_count": p_success,
                        "live_verified_removed_count": p_success if action == "cancel" else 0,
                    })

        # Process sites concurrently! (多站点并发)
        max_site_workers = min(len(target_sites), 5)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_site_workers) as site_pool:
            futures = [site_pool.submit(process_site, s_info) for s_info in target_sites]
            concurrent.futures.wait(futures)

        elapsed_sec = time.time() - start_time
        if elapsed_sec >= 60:
            duration_text = f"{int(elapsed_sec // 60)}分{int(elapsed_sec % 60)}秒"
        else:
            duration_text = f"{int(elapsed_sec)}秒"

        progress.status = "completed" if not cancelled() else "cancelled"
        progress.message = f"[{store_name}] 执行完毕（耗时 {duration_text}）：成功 {progress.success}，跳过 {progress.skipped}，失败 {progress.failed}"
        log(progress.message)
        report_progress(progress)

        # 记录本次执行入库
        self._record_task(
            account_id=account_id,
            action=action,
            mode=mode,
            seller_discount=seller_discount,
            official_discount=official_discount,
            progress=progress,
            group_id=group_id,
            store_name=store_name,
            activity_details=activity_details,
            failed_items=failed_items,
            elapsed_seconds=round(elapsed_sec, 1),
            duration_text=duration_text,
        )

        return {
            "status": progress.status,
            "total": progress.total,
            "success": progress.success,
            "failed": progress.failed,
            "skipped": progress.skipped,
            "group_id": group_id,
            "elapsed_seconds": round(elapsed_sec, 1),
            "duration_text": duration_text,
            "account_id": str(account_id),
            "store_name": store_name,
            "oauth_expired": oauth_expired,
            "expired_account": str(account_id) if oauth_expired else None,
            "expired_store_name": store_name if oauth_expired else None,
        }

    def _record_task(
        self,
        account_id: str,
        action: str,
        mode: str,
        seller_discount: float,
        official_discount: float,
        progress: ExecutionProgress,
        group_id: str,
        store_name: str = "",
        activity_details: list[dict[str, Any]] | None = None,
        failed_items: list[dict[str, Any]] | None = None,
        elapsed_seconds: float = 0.0,
        duration_text: str = "",
    ) -> None:
        """Save execution summary row to promo_tasks for workbench display."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        s_val = int(seller_discount) if float(seller_discount).is_integer() else seller_discount
        o_val = int(official_discount) if float(official_discount).is_integer() else official_discount
        discount_percent = float(s_val)
        act_details = activity_details or []
        f_items = failed_items or []
        summary = {
            "success": progress.success,
            "failed": progress.failed,
            "skipped": progress.skipped,
            "total": progress.total,
            "unique_item_count": progress.total,
            "relation_count": progress.total,
            "request_success_count": progress.success,
            "live_verified_removed_count": progress.success if action == "cancel" else 0,
            "pending_verification_count": 0,
            "activity_failure_count": 0,
            "elapsed_seconds": elapsed_seconds,
            "duration_text": duration_text,
            "seller_discount_percent": s_val,
            "official_discount_percent": o_val,
            "seller_activity_text": f"{s_val}%",
            "official_activity_text": f"{o_val}%",
            "store_name": store_name or self._get_store_alias(account_id),
            "activity_details": act_details,
            "failed_items": f_items,
        }
        summary_str = json.dumps(summary, ensure_ascii=False)
        for retry in range(5):
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO promo_tasks (
                        account_id, promotion_id, promotion_type, action, mode,
                        discount_percent, status, total_count, success_count,
                        failed_count, skipped_count, completed, summary_json,
                        execution_group_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(account_id),
                    "ALL",
                    "BATCH",
                    action,
                    "real",
                    discount_percent,
                    progress.status,
                    progress.total,
                    progress.success,
                    progress.failed,
                    progress.skipped,
                    1 if progress.status == "completed" else 0,
                    summary_str,
                    group_id,
                    now_str,
                    now_str,
                ))
                task_id = cur.lastrowid
                if f_items and task_id:
                    for item in f_items:
                        try:
                            cur.execute("""
                                INSERT INTO promo_action_results (
                                    task_id, account_id, promotion_id, promotion_type,
                                    item_id, action, mode, status, error_cn, error_raw, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                task_id,
                                str(account_id),
                                item.get("promotion_id") or "ALL",
                                "BATCH",
                                item.get("item_id") or "",
                                action,
                                "real",
                                "failed",
                                item.get("reason") or "",
                                item.get("reason") or "",
                                now_str,
                            ))
                        except Exception:
                            pass
                conn.commit()
                break
            except Exception:
                time.sleep(0.5 * (retry + 1))
            finally:
                conn.close()
