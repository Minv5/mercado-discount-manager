from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable


MODE_ACTIONS = {
    "自动判断": "",
    "批量报活动": "enroll",
    "批量更新": "update",
    "批量取消": "cancel",
}

ACTION_LABELS = {
    "enroll": "批量报名",
    "update": "批量更新",
    "cancel": "批量取消",
}

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

EXCLUDE_ACTIVITY = "__exclude__"
BUSINESS_TIMEZONE = timezone(timedelta(hours=8))
TERMINAL_EXECUTION_GROUP_STATES = {"completed", "failed", "cancelled", "interrupted"}


@dataclass(frozen=True)
class Option:
    value: str
    text: str


@dataclass(frozen=True)
class Account:
    account_id: str
    raw_display_name: str
    site_id: str
    store_name: str


class ActionConflictError(ValueError):
    pass


def action_for_mode(mode: str) -> str:
    return MODE_ACTIONS.get(mode, "")


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, "自动判断")


def site_name(site_id: str) -> str:
    key = str(site_id or "").strip().upper()
    return SITE_NAMES.get(key, key or "未知站点")


def account_from_json(row: dict[str, Any]) -> Account:
    account_id = str(row.get("account_id") or row.get("user_id") or "").strip()
    raw_display_name = str(row.get("raw_display_name") or row.get("display_name") or "").strip()
    store_name = str(row.get("store_name") or "店铺待命名").strip() or "店铺待命名"
    return Account(account_id, raw_display_name, str(row.get("site_id") or ""), store_name)


def normalize_activity_name(value: str) -> str:
    return " ".join(str(value or "").replace("\u200b", "").replace("\ufeff", "").split()).casefold()


def promotion_display_name(row: dict[str, Any]) -> str:
    for key in ("name", "promotion_name", "title", "label"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return str(row.get("promotion_id") or "").strip()


def promotion_bucket(promotion_type: str) -> str:
    value = str(promotion_type or "").upper()
    if value == "SELLER_CAMPAIGN":
        return "seller"
    if value == "DEAL":
        return "official"
    if value == "SMART":
        return "smart"
    if value == "LIGHTNING":
        return "lightning"
    return "other"


def build_filters(site_id: str, seller_value: str, official_value: str) -> dict[str, Any]:
    site_id = str(site_id or "")
    seller_value = str(seller_value or "")
    official_value = str(official_value or "")
    exclude_seller = seller_value == EXCLUDE_ACTIVITY
    exclude_official = official_value == EXCLUDE_ACTIVITY
    return {
        "siteId": site_id,
        "siteIds": [site_id] if site_id else [],
        "promotionTypes": [],
        "keywords": [],
        "sellerActivityNames": [] if exclude_seller or not seller_value else [seller_value],
        "officialActivityNames": [] if exclude_official or not official_value else [official_value],
        "excludeSeller": exclude_seller,
        "excludeOfficial": exclude_official,
    }


def _normalized_scope_names(values: object) -> tuple[str, ...]:
    source = values if isinstance(values, (list, tuple, set)) else []
    return tuple(sorted({normalize_activity_name(str(value or "")) for value in source if str(value or "").strip()}))


def execution_scope_key(account_ids: Iterable[str], filters: dict[str, Any]) -> tuple[object, ...]:
    return (
        tuple(sorted({str(value or "").strip() for value in account_ids if str(value or "").strip()})),
        str(filters.get("siteId") or "").strip().upper(),
        _normalized_scope_names(filters.get("sellerActivityNames")),
        _normalized_scope_names(filters.get("officialActivityNames")),
        bool(filters.get("excludeSeller")),
        bool(filters.get("excludeOfficial")),
    )


def _group_scope_key(group: dict[str, Any]) -> tuple[object, ...] | None:
    scope = dict(group.get("scope") or {})
    if not scope:
        return None
    return (
        tuple(sorted({str(value or "").strip() for value in scope.get("account_ids") or [] if str(value or "").strip()})),
        str(scope.get("site_id") or "").strip().upper(),
        _normalized_scope_names(scope.get("seller_activity_names")),
        _normalized_scope_names(scope.get("official_activity_names")),
        bool(scope.get("exclude_seller")),
        bool(scope.get("exclude_official")),
    )


def business_date_from_timestamp(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BUSINESS_TIMEZONE).date()


def _coerce_business_date(value: date | datetime | str | None) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=BUSINESS_TIMEZONE)
        return value.astimezone(BUSINESS_TIMEZONE).date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            pass
    return datetime.now(BUSINESS_TIMEZONE).date()


def completed_execution_for_scope(
    groups: Iterable[dict[str, Any]],
    account_ids: Iterable[str],
    filters: dict[str, Any],
    *,
    business_date: date | datetime | str | None = None,
) -> dict[str, Any] | None:
    wanted_scope = execution_scope_key(account_ids, filters)
    wanted_date = _coerce_business_date(business_date)
    matches = []
    for group in groups:
        if str(group.get("status") or "") not in TERMINAL_EXECUTION_GROUP_STATES:
            continue
        if _group_scope_key(group) != wanted_scope:
            continue
        finished_at = group.get("finished_at") or group.get("updated_at")
        if business_date_from_timestamp(finished_at) != wanted_date:
            continue
        matches.append(group)
    if not matches:
        return None
    return max(matches, key=lambda row: str(row.get("finished_at") or row.get("updated_at") or ""))


def execution_action_summary(group: dict[str, Any]) -> str:
    action = str(group.get("action") or "")
    label = action_label(action)
    if action == "cancel":
        return label
    scope = dict(group.get("scope") or {})
    seller = scope.get("seller_discount_percent")
    official = scope.get("official_discount_percent")
    if seller is None or official is None:
        return label
    return f"{label}{int(seller)}%/{int(official)}%"


def execution_completion_text(group: dict[str, Any]) -> str:
    result = dict(group.get("result") or {})
    success = max(0, int(result.get("success") or 0))
    failed = max(0, int(result.get("failed") or 0))
    skipped = max(0, int(result.get("skipped") or 0))
    total = max(int(result.get("total") or 0), success + failed + skipped)
    finished_text = str(group.get("finished_at") or group.get("updated_at") or "")
    try:
        finished = datetime.fromisoformat(finished_text.replace("Z", "+00:00"))
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        time_text = finished.astimezone(BUSINESS_TIMEZONE).strftime("%H:%M")
    except ValueError:
        time_text = "时间未知"
    prefix = "今日已完成" if str(group.get("status") or "") == "completed" else "今日已有执行记录"
    return (
        f"{prefix}：{execution_action_summary(group)}（{time_text}）；"
        f"商品{total}，成功{success}，失败{failed}，跳过{skipped}。"
    )


def resolve_global_action(actions: Iterable[str]) -> str:
    clean = {str(action or "").strip() for action in actions if str(action or "").strip()}
    if not clean:
        raise ActionConflictError("当前范围没有可执行动作。")
    if len(clean) != 1:
        readable = "、".join(action_label(action) for action in sorted(clean))
        raise ActionConflictError(f"不同店铺需要不同动作（{readable}），请手动选择报名、更新或取消后分开执行。")
    return next(iter(clean))


def discount_inputs_enabled(mode: str, auto_action: str = "") -> bool:
    action = action_for_mode(mode)
    return action != "cancel" and not (not action and auto_action == "cancel")


def confirmation_text(store: str, site: str, action: str, seller_discount: int, official_discount: int) -> str:
    lines = [f"店铺范围：{store}", f"站点范围：{site}", f"执行动作：{action_label(action)}"]
    if action != "cancel":
        lines.append(f"自建折扣：{seller_discount}%    官方折扣：{official_discount}%")
    lines.append("以上为最终执行参数，请确认后执行。")
    return "\n".join(lines)


def execution_payload(
    *,
    account_id: str,
    action: str,
    filters: dict[str, Any],
    store_name: str,
    site_name_text: str,
    seller_discount: int,
    official_discount: int,
    read_concurrency: int,
    activity_concurrency: int,
    write_concurrency: int,
) -> dict[str, Any]:
    return {
        "accountId": account_id,
        "action": action,
        "confirmText": "REAL_SUBMIT",
        "filters": filters,
        "selectedStoreName": store_name,
        "selectedSiteName": site_name_text,
        "priceMode": "discount",
        "sellerDiscountPercent": seller_discount,
        "officialDiscountPercent": official_discount,
        "readConcurrency": read_concurrency,
        "siteConcurrency": read_concurrency,
        "activityConcurrency": activity_concurrency,
        "writeConcurrency": write_concurrency,
        "globalWriteConcurrency": write_concurrency,
        "fetchMode": "full",
        "requireFullFetch": False,
        "sampleOnly": False,
        "prepareOnly": False,
    }


def execution_group_payload(
    *,
    account_ids: list[str],
    action: str,
    filters: dict[str, Any],
    store_names: dict[str, str],
    site_name_text: str,
    seller_discount: int,
    official_discount: int,
    read_concurrency: int,
    activity_concurrency: int,
    write_concurrency: int,
    client_submission_id: str,
) -> dict[str, Any]:
    return {
        "accountIds": list(account_ids),
        "client_submission_id": client_submission_id,
        "action": action,
        "confirmText": "REAL_SUBMIT",
        "filters": filters,
        "storeNames": dict(store_names),
        "selectedSiteName": site_name_text,
        "priceMode": "discount",
        "sellerDiscountPercent": seller_discount,
        "officialDiscountPercent": official_discount,
        "readConcurrency": read_concurrency,
        "siteConcurrency": read_concurrency,
        "activityConcurrency": activity_concurrency,
        "writeConcurrency": write_concurrency,
        "globalWriteConcurrency": write_concurrency,
        "fetchMode": "full",
        "requireFullFetch": False,
        "sampleOnly": False,
        "prepareOnly": False,
    }


def task_display_counts(task: dict[str, Any]) -> tuple[int, int, int, int]:
    success = int(task.get("success_count") or task.get("success") or 0)
    failed = int(task.get("failed_count") or task.get("failed") or 0)
    skipped = int(task.get("skipped_count") or task.get("skipped") or 0)
    if task.get("unique_item_count") is not None:
        return int(task.get("unique_item_count") or 0), success, failed, skipped
    total = int(task.get("total_count") or task.get("total") or 0)
    return max(total, success), success, failed, skipped
