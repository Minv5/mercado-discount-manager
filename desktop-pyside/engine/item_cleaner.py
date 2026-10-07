import concurrent.futures
import contextlib
import datetime
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from engine.client import MercadoClient
from engine.crypto import get_data_dir


@dataclass
class CleanerFilterCriteria:
    account_id: str = ""
    store_name: str = ""
    selected_site_ids: list[str] = field(default_factory=list) # 选定扫描的站点列表 (空列表表示不限制，扫描所有站点)
    # 组合判定模式: "and" (同时满足所有勾选条件，精准漏斗) 或 "or" (满足任一勾选条件)
    filter_mode: str = "and"
    # 新品冷启动保护期 (Grace Period): 上架未满 N 天自动豁免保护
    enable_grace_period: bool = True
    grace_period_days: int = 7       # 上架未满 N 天自动豁免
    # 浏览量条件
    enable_visits_filter: bool = True
    visits_mode: str = "total"       # "total" (历史总浏览量) 或 "window" (近N天)
    visits_days: int = 30            # 近N天
    visits_is_zero_only: bool = True # 是否仅0浏览量
    visits_threshold: int = 0        # 浏览量低于等于该值
    # 质量评分条件
    enable_score_filter: bool = True
    score_threshold: int = 60        # 评分低于该值 (0-100)
    # 违规与失效条件
    enable_policy_filter: bool = True # 违反政策失效 / 举报停用 / 异常关闭
    # 扫描上限
    max_scan_limit: int = 100000     # 最多扫描商品数 (默认无限制)
    # 清店模式 (清空全店所有商品)
    is_wipe_store_mode: bool = False


@dataclass
class ScannedItemRecord:
    item_id: str
    account_id: str
    site_id: str
    title: str
    status: str
    store_name: str = ""
    sub_status: list[str] = field(default_factory=list)
    score: int | None = None
    level_wording: str = ""
    visits: int = 0
    sold_quantity: int = 0
    date_created: str = ""           # 官方上架时间 (YYYY-MM-DD)
    days_on_sale: int = 0            # 已上架天数
    unmet_reasons: list[str] = field(default_factory=list)
    has_sales: bool = False          # 是否有销售记录 (风控资产保护)
    is_protected_new_item: bool = False # 是否处于新品保护期 (自动豁免)
    is_selected_for_delete: bool = False # 是否勾选待删除 (有销量或新品保护时为 False)


class RateLimiter:
    """线程安全预约时间片限流器（Reservation-based Rate Limiter）。
    锁内仅进行微秒级原子时间预约，锁外独立休眠，彻底消除多线程锁争用，确保单店独立跑满 22 QPS。
    """
    def __init__(self, max_qps: float = 22.0):
        self.interval = 1.0 / max(1.0, max_qps)
        self.lock = threading.Lock()
        self.next_allowed_time = time.monotonic()

    def acquire(self) -> None:
        now = time.monotonic()
        with self.lock:
            if now > self.next_allowed_time:
                self.next_allowed_time = now
            scheduled_time = self.next_allowed_time
            self.next_allowed_time += self.interval

        sleep_duration = scheduled_time - now
        if sleep_duration > 0:
            time.sleep(sleep_duration)


def parse_listing_age_days(date_str: str) -> tuple[str, int]:
    """解析官方 ISO 上架时间字符串，返回 (日期文本 YYYY-MM-DD, 已上架天数)。"""
    if not date_str:
        return ("", 999)
    try:
        clean_str = date_str.replace("Z", "+00:00")
        created_dt = datetime.datetime.fromisoformat(clean_str)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        delta = now_dt - created_dt
        days = max(0, delta.days)
        return (created_dt.strftime("%Y-%m-%d"), days)
    except Exception:
        fallback_str = date_str[:10] if len(date_str) >= 10 else date_str
        return (fallback_str, 999)


def _get_db_path() -> Path:
    return get_data_dir() / "discount-manager.sqlite"


_cleaner_tables_ensured = False


def _ensure_cleaner_tables_once(conn: sqlite3.Connection) -> None:
    global _cleaner_tables_ensured
    if _cleaner_tables_ensured:
        return
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS item_cleaner_score_cache ("
        "item_id TEXT PRIMARY KEY, score INTEGER, level_wording TEXT, updated_at TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS item_cleaner_visits_cache ("
        "item_id TEXT PRIMARY KEY, visits INTEGER, updated_at TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS item_cleaner_info_cache ("
        "item_id TEXT PRIMARY KEY, title TEXT, sold_quantity INTEGER, date_created TEXT, status TEXT, sub_status TEXT, site_id TEXT, updated_at TEXT)"
    )
    conn.commit()
    _cleaner_tables_ensured = True


@contextlib.contextmanager
def _open_cleaner_db(db_path: Path, timeout: float = 10.0):
    """确保 SQLite 连接在事务完成后立即显式关闭，避免 Windows 文件句柄锁泄漏。"""
    conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        _ensure_cleaner_tables_once(conn)
        yield conn
    finally:
        conn.close()


def get_cached_score(item_id: str) -> tuple[int, str] | None:
    """从本地 SQLite 读取缓存的商品刊登评分（7天内有效）。"""
    db_path = _get_db_path()
    if not db_path.exists():
        return None
    try:
        with _open_cleaner_db(db_path, timeout=5) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT score, level_wording, updated_at FROM item_cleaner_score_cache WHERE item_id = ?",
                (item_id,),
            )
            row = cur.fetchone()
            if row:
                score, level_wording, updated_at = row
                return (int(score), str(level_wording or ""))
    except Exception:
        pass
    return None


def save_cached_scores(score_entries: list[tuple[str, int, str]]) -> None:
    """批量将商品刊登评分沉淀至本地 SQLite 数据库。"""
    if not score_entries:
        return
    db_path = _get_db_path()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with _open_cleaner_db(db_path, timeout=10) as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT OR REPLACE INTO item_cleaner_score_cache (item_id, score, level_wording, updated_at) "
                "VALUES (?, ?, ?, ?)",
                [(iid, sc, wording, now_iso) for iid, sc, wording in score_entries],
            )
            conn.commit()
    except Exception:
        pass


def get_cached_visits(item_id: str, max_age_hours: int = 24) -> int | None:
    """从本地 SQLite 读取缓存的商品浏览量（24小时内有效）。"""
    db_path = _get_db_path()
    if not db_path.exists():
        return None
    try:
        with _open_cleaner_db(db_path, timeout=5) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT visits, updated_at FROM item_cleaner_visits_cache WHERE item_id = ?",
                (item_id,),
            )
            row = cur.fetchone()
            if row:
                visits, updated_at = row
                try:
                    up_dt = datetime.datetime.fromisoformat(updated_at)
                    if (datetime.datetime.now(datetime.timezone.utc) - up_dt).total_seconds() < max_age_hours * 3600:
                        return int(visits)
                except Exception:
                    return int(visits)
    except Exception:
        pass
    return None


def save_cached_visits(visit_entries: list[tuple[str, int]]) -> None:
    """批量将商品浏览量沉淀至本地 SQLite 数据库。"""
    if not visit_entries:
        return
    db_path = _get_db_path()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with _open_cleaner_db(db_path, timeout=10) as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT OR REPLACE INTO item_cleaner_visits_cache (item_id, visits, updated_at) "
                "VALUES (?, ?, ?)",
                [(iid, v, now_iso) for iid, v in visit_entries],
            )
            conn.commit()
    except Exception:
        pass


def get_cached_item_info(item_id: str, max_age_hours: int = 72) -> dict[str, Any] | None:
    """从本地 SQLite 读取缓存的商品基础档案（标题、销量、上架时间等，72小时内有效）。"""
    db_path = _get_db_path()
    if not db_path.exists():
        return None
    try:
        with _open_cleaner_db(db_path, timeout=5) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT title, sold_quantity, date_created, status, sub_status, site_id, updated_at FROM item_cleaner_info_cache WHERE item_id = ?",
                (item_id,),
            )
            row = cur.fetchone()
            if row:
                title, sold_quantity, date_created, status, sub_status_raw, site_id, updated_at = row
                try:
                    up_dt = datetime.datetime.fromisoformat(updated_at)
                    if (datetime.datetime.now(datetime.timezone.utc) - up_dt).total_seconds() < max_age_hours * 3600:
                        sub_st = json.loads(sub_status_raw) if sub_status_raw else []
                        return {
                            "id": item_id,
                            "title": title or "",
                            "sold_quantity": int(sold_quantity or 0),
                            "date_created": date_created or "",
                            "status": status or "",
                            "sub_status": sub_st,
                            "site_id": site_id or "",
                        }
                except Exception:
                    pass
    except Exception:
        pass
    return None


def save_cached_item_info(item_entries: list[dict[str, Any]]) -> None:
    """批量将商品基础档案沉淀至本地 SQLite 数据库。"""
    if not item_entries:
        return
    db_path = _get_db_path()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with _open_cleaner_db(db_path, timeout=10) as conn:
            cur = conn.cursor()
            rows = []
            for it in item_entries:
                iid = str(it.get("id") or "")
                if not iid:
                    continue
                title = str(it.get("title") or "")
                sold = int(it.get("sold_quantity") or 0)
                dc = str(it.get("date_created") or "")
                st = str(it.get("status") or "")
                sub_st = json.dumps(list(it.get("sub_status") or []))
                site = str(it.get("site_id") or "")
                rows.append((iid, title, sold, dc, st, sub_st, site, now_iso))
            cur.executemany(
                "INSERT OR REPLACE INTO item_cleaner_info_cache (item_id, title, sold_quantity, date_created, status, sub_status, site_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
    except Exception:
        pass


def mark_cached_items_deleted(item_ids: list[str]) -> None:
    """在本地 SQLite 缓存中将指定商品标记为已删除终态，避免后续扫描读取陈旧活跃缓存。"""
    if not item_ids:
        return
    db_path = _get_db_path()
    if not db_path.exists():
        return
    try:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        sub_st = json.dumps(["deleted"])
        with _open_cleaner_db(db_path, timeout=10) as conn:
            cur = conn.cursor()
            cur.executemany(
                "UPDATE item_cleaner_info_cache SET status = 'closed', sub_status = ?, updated_at = ? WHERE item_id = ?",
                [(sub_st, now_iso, iid) for iid in item_ids],
            )
            cur.executemany(
                "DELETE FROM item_cleaner_visits_cache WHERE item_id = ?",
                [(iid,) for iid in item_ids],
            )
            cur.executemany(
                "DELETE FROM item_cleaner_score_cache WHERE item_id = ?",
                [(iid,) for iid in item_ids],
            )
            conn.commit()
    except Exception:
        pass


class ItemCleanerEngine:
    def __init__(self, client: MercadoClient):
        self.client = client

    def scan_shop_items(
        self,
        criteria: CleanerFilterCriteria,
        on_item_matched: Callable[[ScannedItemRecord], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> list[ScannedItemRecord]:
        """扫描店铺商品并按设定的多重条件过滤出不达标商品（母子商品合规 + 智能漏斗 + 新品保护期）。"""
        account_id = criteria.account_id
        store_display = criteria.store_name or account_id
        matched_records: list[ScannedItemRecord] = []

        def log(msg: str) -> None:
            if on_log:
                on_log(msg)

        log(f"[商品扫描] ==================================================")
        log(f"[商品扫描] 开始合规扫描店铺 【{store_display}】")
        if criteria.enable_grace_period:
            log(f"[商品扫描] 🛡️ 上架时长门槛已启用: 仅处理超过 {criteria.grace_period_days} 天商品（未满天数新品自动豁免）")

        # 获取当前店铺的所有分站点子账号与站点信息
        child_sites: list[dict[str, str]] = []
        try:
            if hasattr(self.client, "auth") and hasattr(self.client.auth, "list_sites"):
                raw_sites = self.client.auth.list_sites(account_id)
                for s in raw_sites:
                    c_uid = str(s.get("child_user_id") or "").strip()
                    s_id = str(s.get("site_id") or "").strip()
                    if c_uid and s_id:
                        child_sites.append({"child_user_id": c_uid, "site_id": s_id})
        except Exception:
            pass

        unique_child_sites: list[dict[str, str]] = []
        seen_child_uids: set[str] = set()
        for cs in child_sites:
            cuid = cs["child_user_id"]
            if cuid not in seen_child_uids:
                if criteria.selected_site_ids and cs.get("site_id") not in criteria.selected_site_ids:
                    continue
                seen_child_uids.add(cuid)
                unique_child_sites.append(cs)

        # -------------------------------------------------------------
        # 通道 0: 【清店模式】极速全量检索当前店铺所有商品
        # -------------------------------------------------------------
        if criteria.is_wipe_store_mode:
            log(f"[商品扫描] ==================================================")
            log(f"[商品扫描] ⚠️ 【{store_display}】已开启【清店模式】：正在极速全量检索店铺全部商品")
            log(f"[商品扫描] ==================================================")

            wipe_item_ids: list[str] = []
            seen_wipe_ids: set[str] = set()

            # 1. 快速抓取所有分站点全量商品
            for cs in unique_child_sites:
                if (stop_event and stop_event.is_set()) or len(wipe_item_ids) >= criteria.max_scan_limit:
                    break
                c_uid = cs["child_user_id"]
                scroll_id = None
                while len(wipe_item_ids) < criteria.max_scan_limit:
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        res = self.client.search_marketplace_items(
                            account_id,
                            c_uid,
                            limit=100,
                            search_type="scan",
                            scroll_id=scroll_id,
                        )
                        r_list = res.get("results") or []
                        if not r_list:
                            break
                        for iid in r_list:
                            if iid not in seen_wipe_ids:
                                seen_wipe_ids.add(iid)
                                wipe_item_ids.append(iid)
                                if len(wipe_item_ids) >= criteria.max_scan_limit:
                                    break
                        new_scroll = res.get("scroll_id")
                        if not new_scroll or len(r_list) < 100:
                            break
                        scroll_id = new_scroll
                    except Exception:
                        break

            # 2. 仅当未配置分站点且未指定特定分站点时，才抓取 CBT 母体全量商品
            if not unique_child_sites and not criteria.selected_site_ids and len(wipe_item_ids) == 0:
                scroll_id = None
                while len(wipe_item_ids) < criteria.max_scan_limit:
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        res = self.client.search_user_items(
                            account_id,
                            limit=100,
                            search_type="scan",
                            scroll_id=scroll_id,
                        )
                        r_list = res.get("results") or []
                        if not r_list:
                            break
                        for iid in r_list:
                            if iid not in seen_wipe_ids:
                                seen_wipe_ids.add(iid)
                                wipe_item_ids.append(iid)
                                if len(wipe_item_ids) >= criteria.max_scan_limit:
                                    break
                        new_scroll = res.get("scroll_id")
                        if not new_scroll or len(r_list) < 100:
                            break
                        scroll_id = new_scroll
                    except Exception:
                        break

            total_found = len(wipe_item_ids)
            log(f"[商品扫描] 【{store_display}】清店全量检索完成，共锁定商品 {total_found:,} 件，正在整理详情...")

            # 批量并发拉取基础详情
            item_details_map: dict[str, dict[str, Any]] = {}
            missing_ids: list[str] = []
            for iid in wipe_item_ids:
                c_it = get_cached_item_info(iid)
                if c_it:
                    item_details_map[iid] = c_it
                else:
                    missing_ids.append(iid)

            if missing_ids:
                max_fetch = min(len(missing_ids), 2000)
                fetch_slice = missing_ids[:max_fetch]
                c_chunks = [fetch_slice[i : i + 50] for i in range(0, len(fetch_slice), 50)]
                p_lock = threading.Lock()
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, len(c_chunks)))) as p_exec:
                    def _fetch_b(c_list: list[str]):
                        try:
                            res = self.client.get_items_batch(
                                account_id,
                                c_list,
                                attributes=["id", "title", "status", "sub_status", "sold_quantity", "site_id", "date_created"],
                            )
                            if isinstance(res, list):
                                with p_lock:
                                    for it in res:
                                        it_id = str(it.get("id") or "")
                                        if it_id:
                                            item_details_map[it_id] = it
                        except Exception:
                            pass
                    futures = [p_exec.submit(_fetch_b, chunk) for chunk in c_chunks]
                    concurrent.futures.wait(futures)

            for iid in wipe_item_ids:
                it = item_details_map.get(iid, {})
                status_raw = str(it.get("status") or "active")
                sub_status = [str(s) for s in (it.get("sub_status") or [])]
                if "deleted" in sub_status or status_raw == "inactive":
                    continue
                sold = int(it.get("sold_quantity") or 0)
                site_id = str(it.get("site_id") or ("CBT" if iid.startswith("CBT") else iid[:3]))
                if criteria.selected_site_ids and site_id not in criteria.selected_site_ids:
                    continue
                title = str(it.get("title") or f"[{site_id}] 店铺商品 ({iid})")
                date_created_raw = str(it.get("date_created") or "")
                date_display, days_on_sale = parse_listing_age_days(date_created_raw)
                has_sales = (sold > 0)

                rec = ScannedItemRecord(
                    item_id=iid,
                    account_id=account_id,
                    store_name=store_display,
                    site_id=site_id,
                    title=title,
                    status="清店待删",
                    sub_status=sub_status,
                    score=None,
                    level_wording="",
                    visits=0,
                    sold_quantity=sold,
                    date_created=date_display,
                    days_on_sale=days_on_sale,
                    unmet_reasons=["【清店模式】清空全店商品"],
                    has_sales=has_sales,
                    is_protected_new_item=False,
                    is_selected_for_delete=True,
                )
                matched_records.append(rec)
                if on_item_matched:
                    on_item_matched(rec)

            log(f"[商品扫描] ✅ 【{store_display}】【清店模式】扫描完成，共生成清店待删除商品 {len(matched_records):,} 件")
            log(f"[商品扫描] ==================================================")
            return matched_records

        # -------------------------------------------------------------
        # 通道 1: 定向提取违规、失效、未激活、待审核商品
        # (覆盖母体 CBT 与各分站点子店政策违规/待整改/暂停/已关闭商品)
        # -------------------------------------------------------------
        policy_item_dict: dict[str, dict[str, Any]] = {}
        if criteria.enable_policy_filter:
            log(f"[商品扫描] 【{store_display}】正在拉取违规与待整改商品")

            # 1.1 分站点子账号违规排查
            for cs in unique_child_sites:
                if stop_event and stop_event.is_set():
                    break
                c_uid = cs["child_user_id"]
                s_id = cs["site_id"]

                # 违规分类查询：forbidden(政策违规封禁), waiting_for_patch(待整改优化), pending_documentation(待补资质文件)
                sub_status_targets = ["forbidden", "waiting_for_patch", "pending_documentation"]
                for sub_st in sub_status_targets:
                    if stop_event and stop_event.is_set():
                        break
                    scroll_id = None
                    fetched_count = 0
                    while fetched_count < criteria.max_scan_limit:
                        if stop_event and stop_event.is_set():
                            break
                        try:
                            res = self.client.search_marketplace_items(
                                account_id,
                                c_uid,
                                sub_status=sub_st,
                                limit=100,
                                search_type="scan",
                                scroll_id=scroll_id,
                            )
                            r_list = res.get("results") or []
                            if not r_list:
                                break
                            for iid in r_list:
                                if iid not in policy_item_dict:
                                    policy_item_dict[iid] = {
                                        "site_id": s_id,
                                        "sub_status": [sub_st],
                                        "initial_status": "under_review" if sub_st in ("forbidden", "pending_documentation") else "waiting_for_patch",
                                        "is_policy": True,
                                    }
                                else:
                                    if sub_st not in policy_item_dict[iid]["sub_status"]:
                                        policy_item_dict[iid]["sub_status"].append(sub_st)
                            fetched_count += len(r_list)
                            new_scroll = res.get("scroll_id")
                            if not new_scroll or len(r_list) < 100:
                                break
                            scroll_id = new_scroll
                        except Exception:
                            break

                # 状态异常查询：paused(已暂停), closed(已关闭)
                for st in ["paused", "closed"]:
                    if stop_event and stop_event.is_set():
                        break
                    scroll_id = None
                    fetched_count = 0
                    while fetched_count < criteria.max_scan_limit:
                        if stop_event and stop_event.is_set():
                            break
                        try:
                            res = self.client.search_marketplace_items(
                                account_id,
                                c_uid,
                                status=st,
                                limit=100,
                                search_type="scan",
                                scroll_id=scroll_id,
                            )
                            r_list = res.get("results") or []
                            if not r_list:
                                break
                            for iid in r_list:
                                if iid not in policy_item_dict:
                                    policy_item_dict[iid] = {
                                        "site_id": s_id,
                                        "sub_status": [],
                                        "initial_status": st,
                                        "is_policy": True,
                                    }
                            fetched_count += len(r_list)
                            new_scroll = res.get("scroll_id")
                            if not new_scroll or len(r_list) < 100:
                                break
                            scroll_id = new_scroll
                        except Exception:
                            break

            # 1.2 CBT 母体账号违规与异常排查 (仅在未限定特定分站点时排查母体，排除已彻底删除的 inactive 历史死品)
            if not criteria.selected_site_ids:
                cbt_problem_statuses = ["not_yet_active", "under_review", "paused", "closed"]
                for p_status in cbt_problem_statuses:
                    if stop_event and stop_event.is_set():
                        break
                    scroll_id = None
                    status_fetched = 0
                    while status_fetched < criteria.max_scan_limit:
                        if stop_event and stop_event.is_set():
                            break
                        try:
                            res = self.client.search_user_items(
                                account_id,
                                status=p_status,
                                limit=100,
                                search_type="scan",
                                scroll_id=scroll_id,
                            )
                            r_list = res.get("results") or []
                            if not r_list:
                                break
                            for iid in r_list:
                                if iid not in policy_item_dict:
                                    policy_item_dict[iid] = {
                                        "site_id": "CBT",
                                        "sub_status": [],
                                        "initial_status": p_status,
                                        "is_policy": True,
                                    }
                            status_fetched += len(r_list)
                            new_scroll = res.get("scroll_id")
                            if not new_scroll or len(r_list) < 100:
                                break
                            scroll_id = new_scroll
                        except Exception:
                            break

            log(f"[商品扫描] 【{store_display}】已提取违规与待整改商品: {len(policy_item_dict):,} 件")

        # -------------------------------------------------------------
        # 通道 2: 针对在售商品 (active) 分析 0 浏览、评分与新品保护
        # -------------------------------------------------------------
        active_candidates: dict[str, dict[str, Any]] = {}
        new_items_protected_count = 0
        item_details_map: dict[str, dict[str, Any]] = {}

        if criteria.enable_visits_filter or criteria.enable_score_filter:
            log(f"[商品扫描] 【{store_display}】正在检索在售商品 ID 列表")
            active_ids: list[str] = []
            seen_active_ids: set[str] = set()
            item_site_map: dict[str, str] = {}

            # 2.1 从分站点检索在售商品
            for cs in unique_child_sites:
                if stop_event and stop_event.is_set() or len(active_ids) >= criteria.max_scan_limit:
                    break
                c_uid = cs["child_user_id"]
                s_id = cs.get("site_id") or "分站"
                site_active_count = 0
                scroll_id = None
                while len(active_ids) < criteria.max_scan_limit:
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        res = self.client.search_marketplace_items(
                            account_id,
                            c_uid,
                            status="active",
                            limit=100,
                            search_type="scan",
                            scroll_id=scroll_id,
                        )
                        r_list = res.get("results") or []
                        if not r_list:
                            break
                        for iid in r_list:
                            if iid not in seen_active_ids:
                                seen_active_ids.add(iid)
                                active_ids.append(iid)
                                item_site_map[iid] = s_id
                                site_active_count += 1
                                if len(active_ids) >= criteria.max_scan_limit:
                                    break
                        new_scroll = res.get("scroll_id")
                        if not new_scroll or len(r_list) < 100:
                            break
                        scroll_id = new_scroll
                    except Exception:
                        break
                log(f"[商品扫描] 【{store_display} - {s_id}站】在售商品检索完成: 共 {site_active_count:,} 件")

            # 2.2 仅当分站点未检索到任何在售商品时，才从母体检索在售商品兜底
            if len(active_ids) == 0:
                scroll_id = None
                while len(active_ids) < criteria.max_scan_limit:
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        res = self.client.search_user_items(
                            account_id,
                            status="active",
                            limit=100,
                            search_type="scan",
                            scroll_id=scroll_id,
                        )
                        r_list = res.get("results") or []
                        if not r_list:
                            break
                        for iid in r_list:
                            if iid not in seen_active_ids:
                                seen_active_ids.add(iid)
                                active_ids.append(iid)
                                item_site_map[iid] = "CBT"
                                if len(active_ids) >= criteria.max_scan_limit:
                                    break
                        new_scroll = res.get("scroll_id")
                        if not new_scroll or len(r_list) < 100:
                            break
                        scroll_id = new_scroll
                    except Exception:
                        break
                if active_ids:
                    log(f"[商品扫描] 【{store_display} - CBT母体】在售商品检索完成: 共 {len(active_ids):,} 件")

            log(f"[商品扫描] 【{store_display}】已选站点在售商品汇总: 共 {len(active_ids):,} 件")

            # 2.3 批量/并发拉取在售商品档案 (title, sold_quantity, date_created 等)
            if active_ids:
                cached_info_count = 0
                need_fetch_info_ids: list[str] = []
                for iid in active_ids:
                    cached_it = get_cached_item_info(iid)
                    if cached_it:
                        item_details_map[iid] = cached_it
                        cached_info_count += 1
                    else:
                        need_fetch_info_ids.append(iid)

                if need_fetch_info_ids:
                    attrs = ["id", "title", "status", "sub_status", "sold_quantity", "site_id", "date_created"]
                    new_cached_entries: list[dict[str, Any]] = []
                    info_lock = threading.Lock()

                    chunk_size = 20
                    chunks = [need_fetch_info_ids[i : i + chunk_size] for i in range(0, len(need_fetch_info_ids), chunk_size)]
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, len(chunks)))) as ex:
                        def _fetch_batch_chunk(c_ids: list[str]):
                            try:
                                res = self.client.get_items_batch(account_id, c_ids, attributes=attrs)
                                if isinstance(res, list) and res:
                                    with info_lock:
                                        for it in res:
                                            it_id = str(it.get("id") or "")
                                            if it_id:
                                                item_details_map[it_id] = it
                                                new_cached_entries.append(it)
                            except Exception:
                                pass
                        futures = [ex.submit(_fetch_batch_chunk, c) for c in chunks]
                        concurrent.futures.wait(futures)

                    missing_child_ids = [i for i in need_fetch_info_ids if i not in item_details_map]
                    if missing_child_ids:
                        tot_child = len(missing_child_ids)
                        log(f"[商品扫描] 【{store_display}】正在核验在售商品档案与销量: 共 {tot_child:,} 件")
                        c_done = 0
                        start_c_time = time.monotonic()
                        last_c_log = start_c_time

                        def _fetch_child(iid: str):
                            nonlocal c_done, last_c_log
                            if stop_event and stop_event.is_set():
                                return
                            try:
                                d = self.client.get_item_detail(account_id, iid)
                                if isinstance(d, dict) and d:
                                    with info_lock:
                                        item_details_map[iid] = d
                                        new_cached_entries.append(d)
                            except Exception:
                                pass
                            finally:
                                with info_lock:
                                    c_done += 1
                                    cur = c_done
                                    now_mono = time.monotonic()
                                    if cur % 100 == 0 or (now_mono - last_c_log) >= 5.0 or cur == tot_child:
                                        last_c_log = now_mono
                                        elapsed = max(0.1, now_mono - start_c_time)
                                        speed = cur / elapsed
                                        pct = (cur / tot_child) * 100.0
                                        log(f"[商品扫描] 【{store_display}】基础档案核验进度: {cur}/{tot_child} ({pct:.1f}%) | 速率: {speed:.1f} req/s")

                        with concurrent.futures.ThreadPoolExecutor(max_workers=min(24, max(1, tot_child))) as child_ex:
                            futures = [child_ex.submit(_fetch_child, iid) for iid in missing_child_ids]
                            concurrent.futures.wait(futures)

                    if new_cached_entries:
                        save_cached_item_info(new_cached_entries)

            # 2.4 筛选出真正需要进一步核验流量与评分的候选池（严格保护冷启动新品与分站点过滤）
            eligible_pool: list[str] = []
            for iid in active_ids:
                it = item_details_map.get(iid, {})
                s_id = str(it.get("site_id") or item_site_map.get(iid) or ("CBT" if iid.startswith("CBT") else iid[:3]))

                # 站点前置严格过滤：若不在选定站点列表中则直接跳过
                if criteria.selected_site_ids and s_id not in criteria.selected_site_ids:
                    continue

                # 核心风控：上架时长门槛 / 新品保护 (未满指定天数则直接豁免保护)
                date_created_str = str(it.get("date_created") or "")
                _, days_on_sale = parse_listing_age_days(date_created_str)
                if criteria.enable_grace_period and days_on_sale < criteria.grace_period_days:
                    new_items_protected_count += 1
                    continue

                eligible_pool.append(iid)

            sold_items_count = sum(1 for iid in eligible_pool if int(item_details_map.get(iid, {}).get("sold_quantity") or 0) > 0)
            if sold_items_count > 0:
                log(f"[商品扫描] 🛡️ 【{store_display}】发现历史出单商品 {sold_items_count:,} 件，已自动设为核心保护状态（禁止删除）")
            if new_items_protected_count > 0:
                log(f"[商品扫描] 🛡️ 【{store_display}】成功豁免未满 {criteria.grace_period_days} 天新品 {new_items_protected_count:,} 件")

            # 汇总待核验商品的分站点分布
            site_dist: dict[str, int] = {}
            for iid in eligible_pool:
                it = item_details_map.get(iid, {})
                s = str(it.get("site_id") or item_site_map.get(iid) or ("CBT" if iid.startswith("CBT") else iid[:3]))
                site_dist[s] = site_dist.get(s, 0) + 1
            dist_desc = ", ".join(f"{s}: {c:,}件" for s, c in sorted(site_dist.items()))
            log(f"[商品扫描] 【{store_display}】待核验流量与评分商品: 共 {len(eligible_pool):,} 件 ({dist_desc or '无'})")

            # 2.5 真实浏览量核验 (单品官方契约 + 本地 SQLite 缓存 + 32 QPS 并发 Worker)
            visits_map: dict[str, int] = {}
            if criteria.enable_visits_filter and eligible_pool:
                cached_visits_count = 0
                need_fetch_visits_ids: list[str] = []
                for iid in eligible_pool:
                    cached_v = get_cached_visits(iid)
                    if cached_v is not None:
                        visits_map[iid] = cached_v
                        cached_visits_count += 1
                    else:
                        need_fetch_visits_ids.append(iid)

                log(f"[商品扫描] 【{store_display}】正在核验真实浏览量: 待联网核查 {len(need_fetch_visits_ids):,} 件（缓存命中 {cached_visits_count:,} 件）")

                if need_fetch_visits_ids:
                    rate_limiter = RateLimiter(max_qps=32.0)
                    newly_fetched_visits: list[tuple[str, int]] = []
                    v_fetch_lock = threading.Lock()
                    v_done_counter = 0
                    total_v_fetch = len(need_fetch_visits_ids)
                    start_v_time = time.monotonic()
                    last_v_log_time = start_v_time

                    def _fetch_single_visit(iid: str):
                        nonlocal v_done_counter, last_v_log_time
                        if stop_event and stop_event.is_set():
                            return
                        rate_limiter.acquire()
                        try:
                            v_count = self.client.get_item_visits(account_id, iid)
                            with v_fetch_lock:
                                visits_map[iid] = v_count
                                newly_fetched_visits.append((iid, v_count))
                        except Exception:
                            pass
                        finally:
                            with v_fetch_lock:
                                v_done_counter += 1
                                cur_done = v_done_counter
                                now_mono = time.monotonic()
                                if len(newly_fetched_visits) >= 200:
                                    batch_to_save = list(newly_fetched_visits)
                                    newly_fetched_visits.clear()
                                    save_cached_visits(batch_to_save)

                                if cur_done % 100 == 0 or (now_mono - last_v_log_time) >= 5.0 or cur_done == total_v_fetch:
                                    last_v_log_time = now_mono
                                    elapsed = max(0.1, now_mono - start_v_time)
                                    speed = cur_done / elapsed
                                    rem_count = total_v_fetch - cur_done
                                    rem_sec = int(rem_count / max(0.1, speed))
                                    rem_str = f"{rem_sec // 60}分{rem_sec % 60}秒" if rem_sec >= 60 else f"{rem_sec}秒"
                                    pct = (cur_done / total_v_fetch) * 100.0
                                    log(
                                        f"[商品扫描] 【{store_display}】浏览量核查进度: {cur_done}/{total_v_fetch} ({pct:.1f}%) | "
                                        f"速率: {speed:.1f} req/s | 剩余时间: {rem_str}"
                                    )

                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, max(1, len(need_fetch_visits_ids)))) as v_executor:
                        v_futures = [v_executor.submit(_fetch_single_visit, iid) for iid in need_fetch_visits_ids]
                        concurrent.futures.wait(v_futures)

                    if newly_fetched_visits:
                        save_cached_visits(newly_fetched_visits)

            # 2.6 质量评分分析
            # 美客多官方契约：CBT 跨境母体商品不支持单品质量评分，但分站点子店商品（MLB/MLM/MLA/MCO等）拥有官方真实评分！
            score_results: dict[str, tuple[int | None, str]] = {}
            items_to_score = [iid for iid in eligible_pool if not iid.startswith("CBT")]
            cbt_items_count = len(eligible_pool) - len(items_to_score)

            if criteria.enable_score_filter and cbt_items_count > 0:
                log(f"[商品扫描] 【{store_display}】检测到 {cbt_items_count:,} 件 CBT 跨境母体商品，评分条件自动豁免")

            if criteria.enable_score_filter and items_to_score:
                cached_count = 0
                need_fetch_ids: list[str] = []
                for iid in items_to_score:
                    cached = get_cached_score(iid)
                    if cached is not None:
                        score_results[iid] = cached
                        cached_count += 1
                    else:
                        need_fetch_ids.append(iid)

                log(
                    f"[商品扫描] 【{store_display}】正在核查商品刊登质量评分: 共 {len(items_to_score):,} 件 "
                    f"（缓存命中 {cached_count:,} 件，待联网核验 {len(need_fetch_ids):,} 件）"
                )

                if need_fetch_ids:
                    rate_limiter = RateLimiter(max_qps=32.0)
                    newly_fetched_scores: list[tuple[str, int, str]] = []
                    score_lock = threading.Lock()
                    done_counter = 0
                    total_fetch = len(need_fetch_ids)
                    start_fetch_time = time.monotonic()
                    last_log_time = start_fetch_time

                    def _fetch_single_score(iid: str):
                        nonlocal done_counter, last_log_time
                        if stop_event and stop_event.is_set():
                            return
                        rate_limiter.acquire()
                        try:
                            perf = self.client.get_item_performance(account_id, iid)
                            if isinstance(perf, dict) and "score" in perf:
                                sc = int(perf.get("score") or 0)
                                wording = str(perf.get("level_wording") or perf.get("level") or "")
                                with score_lock:
                                    score_results[iid] = (sc, wording)
                                    newly_fetched_scores.append((iid, sc, wording))
                        except Exception:
                            pass
                        finally:
                            with score_lock:
                                done_counter += 1
                                cur_done = done_counter
                                now_mono = time.monotonic()
                                if len(newly_fetched_scores) >= 200:
                                    batch_to_save = list(newly_fetched_scores)
                                    newly_fetched_scores.clear()
                                    save_cached_scores(batch_to_save)

                                if cur_done % 100 == 0 or (now_mono - last_log_time) >= 5.0 or cur_done == total_fetch:
                                    last_log_time = now_mono
                                    elapsed = max(0.1, now_mono - start_fetch_time)
                                    speed = cur_done / elapsed
                                    rem_count = total_fetch - cur_done
                                    rem_sec = int(rem_count / max(0.1, speed))
                                    rem_str = f"{rem_sec // 60}分{rem_sec % 60}秒" if rem_sec >= 60 else f"{rem_sec}秒"
                                    pct = (cur_done / total_fetch) * 100.0
                                    log(
                                        f"[商品扫描] 【{store_display}】刊登评分核验进度: {cur_done}/{total_fetch} ({pct:.1f}%) | "
                                        f"速率: {speed:.1f} req/s | 剩余时间: {rem_str}"
                                    )

                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, max(1, len(need_fetch_ids)))) as score_executor:
                        score_futures = [score_executor.submit(_fetch_single_score, iid) for iid in need_fetch_ids]
                        concurrent.futures.wait(score_futures)

                    if newly_fetched_scores:
                        save_cached_scores(newly_fetched_scores)

            # 2.7 判定在售商品是否命中不达标条件
            for iid in eligible_pool:
                if stop_event and stop_event.is_set():
                    break
                is_cbt_item = iid.startswith("CBT")
                v = visits_map.get(iid, 0)
                hit_visits = False
                if criteria.enable_visits_filter:
                    if criteria.visits_is_zero_only and v == 0:
                        hit_visits = True
                    elif not criteria.visits_is_zero_only and v <= criteria.visits_threshold:
                        hit_visits = True

                hit_score = False
                score_val = None
                score_wording = ""
                if criteria.enable_score_filter and not is_cbt_item:
                    score_info = score_results.get(iid)
                    if score_info is not None:
                        score_val, score_wording = score_info
                        if score_val is not None and score_val < criteria.score_threshold:
                            hit_score = True

                matched = False
                unmet: list[str] = []
                if criteria.filter_mode == "and":
                    conds = []
                    if criteria.enable_visits_filter:
                        conds.append(hit_visits)
                    if criteria.enable_score_filter and not is_cbt_item:
                        conds.append(hit_score)
                    if conds and all(conds):
                        matched = True
                        if hit_visits:
                            unmet.append("0 浏览量" if v == 0 else f"浏览量 ≤ {criteria.visits_threshold} (实际: {v})")
                        if hit_score and not is_cbt_item:
                            unmet.append(f"刊登评分 {score_val}分 < {criteria.score_threshold}分")
                else:  # OR 模式
                    if hit_visits:
                        matched = True
                        unmet.append("0 浏览量" if v == 0 else f"浏览量 ≤ {criteria.visits_threshold} (实际: {v})")
                    if hit_score and not is_cbt_item:
                        matched = True
                        unmet.append(f"刊登评分 {score_val}分 < {criteria.score_threshold}分")

                if matched and unmet:
                    active_candidates[iid] = {
                        "visits": v,
                        "score": score_val,
                        "level_wording": score_wording,
                        "unmet_reasons": unmet,
                        "is_policy": False,
                    }

        # -------------------------------------------------------------
        # 通道 3: 汇总待清理商品记录并补齐必要信息
        # -------------------------------------------------------------
        all_unmet_ids = list(dict.fromkeys(list(policy_item_dict.keys()) + list(active_candidates.keys())))
        log(f"[商品扫描] 【{store_display}】共锁定待排查商品 {len(all_unmet_ids):,} 件，正在整理详细列表")

        # 对未获取基础详情的违规商品，并发补充详情（优先查缓存）
        missing_detail_ids = [iid for iid in all_unmet_ids if iid not in item_details_map]
        if missing_detail_ids:
            still_missing: list[str] = []
            for iid in missing_detail_ids:
                cached_it = get_cached_item_info(iid)
                if cached_it:
                    item_details_map[iid] = cached_it
                else:
                    still_missing.append(iid)

            if still_missing:
                cbt_missing = [i for i in still_missing if i.startswith("CBT")]
                child_missing = [i for i in still_missing if not i.startswith("CBT")]
                new_policy_cached: list[dict[str, Any]] = []
                p_lock = threading.Lock()

                if cbt_missing:
                    chunk_size = 20
                    c_chunks = [cbt_missing[i:i+chunk_size] for i in range(0, len(cbt_missing), chunk_size)]
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(c_chunks)))) as pb_executor:
                        def _fetch_cbt_batch(c_ids: list[str]):
                            try:
                                res = self.client.get_items_batch(account_id, c_ids, attributes=["id", "title", "status", "sub_status", "sold_quantity", "site_id", "date_created"])
                                if isinstance(res, list):
                                    with p_lock:
                                        for it in res:
                                            it_id = str(it.get("id") or "")
                                            if it_id:
                                                item_details_map[it_id] = it
                                                new_policy_cached.append(it)
                            except Exception:
                                pass
                        futures = [pb_executor.submit(_fetch_cbt_batch, c) for c in c_chunks]
                        concurrent.futures.wait(futures)

                if child_missing:
                    max_detail_fetch = min(len(child_missing), 500)
                    fetch_targets = child_missing[:max_detail_fetch]
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(24, max(1, len(fetch_targets)))) as pb_executor:
                        def _fetch_child_p(cid: str):
                            if stop_event and stop_event.is_set():
                                return
                            try:
                                d = self.client.get_item_detail(account_id, cid)
                                if isinstance(d, dict) and d:
                                    with p_lock:
                                        item_details_map[cid] = d
                                        new_policy_cached.append(d)
                            except Exception:
                                pass
                        futures = [pb_executor.submit(_fetch_child_p, cid) for cid in fetch_targets]
                        concurrent.futures.wait(futures)

                if new_policy_cached:
                    save_cached_item_info(new_policy_cached)

        for iid in all_unmet_ids:
            it = item_details_map.get(iid, {})
            p_info = policy_item_dict.get(iid)
            initial_status = p_info.get("initial_status", "") if p_info else ""
            default_site = p_info.get("site_id", "") if p_info else ""

            status = str(it.get("status") or initial_status or "")
            sub_status = [str(s) for s in (it.get("sub_status") or (p_info.get("sub_status") if p_info else []))]
            if "deleted" in sub_status or status == "inactive":
                continue
            sold = int(it.get("sold_quantity") or 0)
            site_id = str(it.get("site_id") or default_site or ("CBT" if iid.startswith("CBT") else iid[:3]))
            if criteria.selected_site_ids and site_id not in criteria.selected_site_ids:
                continue
            title = str(it.get("title") or f"[{site_id}] 违规停用商品 ({iid})")
            date_created_raw = str(it.get("date_created") or "")
            date_display, days_on_sale = parse_listing_age_days(date_created_raw)

            has_sales = (sold > 0)

            is_policy_item = False
            reasons: list[str] = []
            status_wording = ""

            if p_info is not None:
                if "waiting_for_patch" in sub_status or initial_status == "waiting_for_patch":
                    is_policy_item = True
                    status_wording = "待整改"
                    reasons.append("待修正整改 (waiting_for_patch: 封面待优化/标题不符)")
                elif "pending_documentation" in sub_status or initial_status == "pending_documentation":
                    is_policy_item = True
                    status_wording = "待补文件"
                    reasons.append("因政策待审 (pending_documentation: 待提交资质文件)")
                elif "forbidden" in sub_status or initial_status == "forbidden":
                    is_policy_item = True
                    status_wording = "政策失效"
                    reasons.append("因违反政策失效 (forbidden: 平台停用)")
                elif "banned" in sub_status or "suspended" in sub_status:
                    is_policy_item = True
                    status_wording = "未激活"
                    reasons.append("因违反政策失效 (suspended: 产品被平台禁止停用)")
                elif status == "paused" or (not status and initial_status == "paused"):
                    is_policy_item = True
                    status_wording = "已暂停"
                    reasons.append("商品已被暂停销售")
                elif status == "closed" or (not status and initial_status == "closed"):
                    is_policy_item = True
                    status_wording = "已关闭"
                    reasons.append("商品已关闭下架")
                elif status in ("not_yet_active", "under_review"):
                    is_policy_item = True
                    status_wording = "未激活"
                    reasons.append(f"因违反政策失效 (状态: {status})")
                elif status and status != "active":
                    is_policy_item = True
                    status_wording = "异常下架"
                    reasons.append(f"状态异常 ({status})")

            cand = active_candidates.get(iid)
            if not is_policy_item:
                if not cand:
                    continue
                reasons = list(cand.get("unmet_reasons") or [])
                status_wording = "在售 (低质量/无浏览)"
            elif cand:
                for r in (cand.get("unmet_reasons") or []):
                    if r not in reasons:
                        reasons.append(r)

            # 核心风控保护：仅当商品依然在正常在售（status == 'active' 且非政策违规）时，出单记录才豁免保护（保持未勾选）；
            # 若商品已被平台明确下架、停用、暂停、关闭或政策失效，则属于失效垃圾资产，即使历史曾出单也不再保护，默认勾选待删除
            is_active_sale = (has_sales and not is_policy_item and status == "active")
            is_selected = (not is_active_sale)

            score = cand.get("score") if cand else None
            level_wording = cand.get("level_wording") or "" if cand else ""
            visits = cand.get("visits") or 0 if cand else 0

            rec = ScannedItemRecord(
                item_id=iid,
                account_id=account_id,
                store_name=store_display,
                site_id=site_id,
                title=title,
                status=status_wording,
                sub_status=sub_status,
                score=score,
                level_wording=level_wording,
                visits=visits,
                sold_quantity=sold,
                date_created=date_display,
                days_on_sale=days_on_sale,
                unmet_reasons=reasons,
                has_sales=has_sales,
                is_protected_new_item=False,
                is_selected_for_delete=is_selected,
            )
            matched_records.append(rec)
            if on_item_matched:
                on_item_matched(rec)

            if is_active_sale:
                log(f"[商品扫描] 🛡️ 【{store_display}】在售商品 {iid} 检测到已出单 {sold} 件，触发经营资产保护（保持未勾选）")
            elif has_sales:
                log(f"[商品扫描] ⚠️ 【{store_display}】商品 {iid} 虽曾出单 {sold} 件但已被平台下架停用，纳入待清理: 【{status_wording}】 {', '.join(reasons)}")
            else:
                log(f"[商品扫描] ❌ 【{store_display}】商品 {iid} 判定不合格: 【{status_wording}】 {', '.join(reasons)}")

        log(f"[商品扫描] ==================================================")
        log(f"[商品扫描] ✅ 【{store_display}】扫描完成，共检出待清理商品 {len(matched_records):,} 件")
        if new_items_protected_count > 0:
            log(f"[商品扫描] 🛡️ 【{store_display}】已自动豁免冷启动新品 {new_items_protected_count:,} 件")
        log(f"[商品扫描] ==================================================")
        return matched_records

    def execute_batch_delete(
        self,
        account_id: str,
        item_ids: list[str],
        store_name: str = "",
        on_item_deleted: Callable[[str, bool, str], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """批量执行删除 (两阶段：先变更 closed，再提交 deleted=true)。支持单店并发加速。"""
        def log(msg: str) -> None:
            if on_log:
                on_log(msg)

        store_display = store_name or account_id
        total = len(item_ids)
        log(f"[商品删除] --------------------------------------------------")
        log(f"[商品删除] 【{store_display}】开始执行 CBT 跨境自发货全局下架删除任务，目标商品总数: {total} 件")
        log(f"[商品删除] --------------------------------------------------")

        success_count = 0
        failed_count = 0
        failures: list[dict[str, str]] = []
        successful_ids: list[str] = []
        del_lock = threading.Lock()
        processed_count = 0
        rate_limiter = RateLimiter(max_qps=25.0)

        start_time = time.monotonic()
        last_log_time = start_time

        def _delete_single_item(item_id: str) -> tuple[str, bool, str]:
            if stop_event and stop_event.is_set():
                return item_id, False, "用户手动中止"
            rate_limiter.acquire()
            success = False
            err_msg = ""
            try:
                if total <= 20:
                    log(f"[商品删除] 【{store_display}】正在执行 CBT 跨境自发货全局下架删除 {item_id}")
                res = self.client.delete_item(account_id, item_id)
                success = True
                if total <= 20:
                    if isinstance(res, dict) and res.get("already_deleted"):
                        log(f"[商品删除] ✅ 【{store_display}】商品 {item_id} 在美客多官方平台已是彻底下架/删除状态，已自动同步")
                    else:
                        log(f"[商品删除] ✅ 【{store_display}】商品 {item_id} 已成功从美客多平台下架删除")
            except Exception as e:
                err_msg = str(e)
                log(f"[商品删除] ❌ 【{store_display}】商品 {item_id} 下架删除失败: {err_msg}")

            return item_id, success, err_msg

        if total > 0:
            max_workers = min(18, max(1, total))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_item = {executor.submit(_delete_single_item, iid): iid for iid in item_ids}
                for future in concurrent.futures.as_completed(future_to_item):
                    if stop_event and stop_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        log(f"[商品删除] 【{store_display}】批量删除已被用户手动中止")
                        break

                    with del_lock:
                        processed_count += 1
                        cur_idx = processed_count

                    iid = future_to_item[future]
                    try:
                        _, succ, err = future.result()
                    except Exception as e:
                        succ, err = False, str(e)

                    with del_lock:
                        if succ:
                            success_count += 1
                            successful_ids.append(iid)
                        else:
                            failed_count += 1
                            failures.append({"item_id": iid, "error": err})

                        if total > 20:
                            now_mono = time.monotonic()
                            if cur_idx % 50 == 0 or (now_mono - last_log_time) >= 5.0 or cur_idx == total:
                                last_log_time = now_mono
                                elapsed = max(0.1, now_mono - start_time)
                                speed = cur_idx / elapsed
                                pct = (cur_idx / total) * 100.0
                                log(f"[商品删除] 【{store_display}】删除进度: {cur_idx}/{total} ({pct:.1f}%) | 速率: {speed:.1f} 件/s")

                    if on_progress:
                        on_progress(cur_idx, total, f"删除 {iid}")
                    if on_item_deleted:
                        on_item_deleted(iid, succ, err)

        if total > 20 and success_count > 0:
            log(f"[商品删除] ✅ 【{store_display}】共 {success_count} 件商品已成功从美客多平台下架删除")

        if successful_ids:
            mark_cached_items_deleted(successful_ids)

        return {
            "account_id": account_id,
            "store_name": store_display,
            "total": total,
            "success_count": success_count,
            "failed_count": failed_count,
            "failures": failures,
        }



def get_cleaner_draft_file() -> Path:
    return get_data_dir() / "cleaner_draft.json"


def save_cleaner_draft(records: list[ScannedItemRecord]) -> None:
    path = get_cleaner_draft_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        if path.exists():
            path.unlink(missing_ok=True)
        return
    data = []
    for r in records:
        data.append({
            "item_id": r.item_id,
            "account_id": r.account_id,
            "site_id": r.site_id,
            "title": r.title,
            "status": r.status,
            "store_name": r.store_name,
            "sub_status": r.sub_status,
            "score": r.score,
            "level_wording": r.level_wording,
            "visits": r.visits,
            "sold_quantity": r.sold_quantity,
            "date_created": r.date_created,
            "days_on_sale": r.days_on_sale,
            "unmet_reasons": r.unmet_reasons,
            "has_sales": r.has_sales,
            "is_selected_for_delete": r.is_selected_for_delete,
        })
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def is_item_confirmed_deleted(rec: ScannedItemRecord) -> bool:
    """判定商品是否已经处于 100% 确认的彻底删除/下架状态（终态，无需调用 API）。

    判定依据（硬边界）：
    1. 状态明确为 '已删除' / '已彻底删除'；
    2. 美客多官方 sub_status 权威契约明确包含 'deleted'；
    3. 状态中包含历史网关确认留存的 '404' 与 'not a cbt item' / 'not_found'。
    """
    if not rec:
        return False
    st = str(rec.status or "").strip()
    if st in ("已删除", "已彻底删除"):
        return True
    sub_st = [str(s).lower() for s in (rec.sub_status or [])]
    if "deleted" in sub_st:
        return True
    st_lower = st.lower()
    if "404" in st_lower and ("not a cbt item" in st_lower or "not_found" in st_lower or "not found" in st_lower):
        return True
    return False


def load_cleaner_draft() -> list[ScannedItemRecord]:
    path = get_cleaner_draft_file()
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
        raw_list = json.loads(content)
        if not isinstance(raw_list, list):
            return []
        records = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or "").strip()
            account_id = str(item.get("account_id") or "").strip()
            if not item_id or not account_id:
                continue
            st_raw = str(item.get("status") or "")
            sub_st = list(item.get("sub_status") or [])
            st_lower = st_raw.lower()
            if "404" in st_lower and ("not a cbt item" in st_lower or "not_found" in st_lower or "not found" in st_lower):
                st_raw = "已删除"
                if "deleted" not in sub_st:
                    sub_st.append("deleted")

            has_sales = bool(item.get("has_sales") or False)
            raw_selected = bool(item.get("is_selected_for_delete") or False)
            is_del = (st_raw in ("已删除", "已彻底删除") or "deleted" in sub_st)
            is_active_sale = (has_sales and ("在售" in st_raw or st_raw == "active"))
            is_sel = raw_selected and not is_del and not is_active_sale

            rec = ScannedItemRecord(
                item_id=str(item.get("item_id") or ""),
                account_id=str(item.get("account_id") or ""),
                site_id=str(item.get("site_id") or ""),
                title=str(item.get("title") or ""),
                status=st_raw,
                store_name=str(item.get("store_name") or ""),
                sub_status=sub_st,
                score=item.get("score"),
                level_wording=str(item.get("level_wording") or ""),
                visits=int(item.get("visits") or 0),
                sold_quantity=int(item.get("sold_quantity") or 0),
                date_created=str(item.get("date_created") or ""),
                days_on_sale=int(item.get("days_on_sale") or 0),
                unmet_reasons=list(item.get("unmet_reasons") or []),
                has_sales=has_sales,
                is_selected_for_delete=is_sel,
            )
            records.append(rec)
        return records
    except Exception:
        return []


def clear_cleaner_draft() -> None:
    path = get_cleaner_draft_file()
    if path.exists():
        path.unlink(missing_ok=True)

