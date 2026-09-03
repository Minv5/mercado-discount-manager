import re
from typing import Any


_TECHNICAL_REASON_PATTERNS = (
    (re.compile(r"\bPROMOTION_ITEMS_(?:UNREADABLE|INCOMPLETE)\b|\bresults\s*[\"']?\s*[:=]\s*null\b|已报名商品明细不完整|已报名明细不完整|平台商品明细不可验证", re.IGNORECASE), "平台未返回可读取的商品清单，暂无法确认取消结果。"),
    (re.compile(r"\bpending[_\s-]*relations?[_\s-]*present\b", re.IGNORECASE), "存在待平台确认的商品关系。"),
    (re.compile(r"\baccounting[_\s-]*complete\s*[\"']?\s*[:=]\s*false\b|\baccounting_(?:incomplete|not_proven)\b", re.IGNORECASE), "本次结果尚未全部确认。"),
    (re.compile(r"\b(?:current|baseline)_snapshot_(?:missing|invalid|incomplete)\b|\bno_routes\b", re.IGNORECASE), "完整商品快照不足，暂不统计新增/减少。"),
)


def business_reason_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    token_only = re.compile(
        r"^(?:PROMOTION_ITEMS_(?:UNREADABLE|INCOMPLETE)|pending[_\s-]*relations?[_\s-]*present|"
        r"accounting[_\s-]*complete\s*[\"']?\s*[:=]\s*false|accounting_(?:incomplete|not_proven)|"
        r"results\s*[\"']?\s*[:=]\s*null|已报名商品明细不完整|已报名明细不完整|平台商品明细不可验证)$",
        re.IGNORECASE,
    )
    technical_text = raw
    matched_technical = False
    for pattern, label in _TECHNICAL_REASON_PATTERNS:
        if pattern.search(technical_text):
            if token_only.fullmatch(raw) or raw.lstrip().startswith(("{", "[")):
                return label
            technical_text = pattern.sub(label, technical_text)
            matched_technical = True
    if matched_technical:
        return technical_text
    text = raw.replace("_", " ")
    replacements = (
        (re.compile(r"\bapi incomplete marketplace candidate\b", re.IGNORECASE), "商品明细不完整"),
        (re.compile(r"\bpartial api sparse marketplace candidate\b", re.IGNORECASE), "平台商品明细读取不完整"),
        (re.compile(r"\bparameters unconfirmed\b", re.IGNORECASE), "活动参数未确认"),
        (re.compile(r"\brunning\b", re.IGNORECASE), "执行中"),
        (re.compile(r"\bpartial or failed\b", re.IGNORECASE), "部分完成/有失败"),
        (re.compile(r"\bempty or failed\b", re.IGNORECASE), "未执行/无可处理商品"),
    )
    for pattern, label in replacements:
        text = pattern.sub(label, text)
    return text


def reason_matches(value: Any, pattern_index: int) -> bool:
    return bool(_TECHNICAL_REASON_PATTERNS[pattern_index][0].search(str(value or "")))


def has_readback_incomplete_reason(value: Any) -> bool:
    if isinstance(value, dict):
        if reason_matches(value.get("reason"), 0) or reason_matches(value.get("error"), 0):
            return True
        reasons = value.get("failure_reasons") or value.get("incomplete_reasons") or []
        return any(has_readback_incomplete_reason(item) for item in reasons)
    if isinstance(value, list):
        return any(has_readback_incomplete_reason(item) for item in value)
    return reason_matches(value, 0)


def completeness_notice(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    reasons = value.get("incomplete_reasons") or []
    notices = []
    if any(reason_matches(reason, 1) for reason in reasons):
        notices.append("存在待平台确认的商品关系。")
    if value.get("accounting_complete") is False or any(reason_matches(reason, 2) for reason in reasons):
        notices.append("本次结果尚未全部确认。")
    return "；".join(dict.fromkeys(notices))
