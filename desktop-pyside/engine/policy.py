from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .pricing import ItemNetProceeds, calculate_deal_price, extract_item_net_proceeds


class ActionType(str, Enum):
    NO_ACTION = "NO_ACTION"
    UPDATE_SNAPSHOT_ONLY = "UPDATE_SNAPSHOT_ONLY"
    PLATFORM_SHIPPING_CHANGE = "PLATFORM_SHIPPING_CHANGE"
    SELLER_DATA_CHANGE = "SELLER_DATA_CHANGE"
    WAIT_PLATFORM_REVIEW = "WAIT_PLATFORM_REVIEW"
    REVIEW_RESOLVED_AUTO_REENROLL = "REVIEW_RESOLVED_AUTO_REENROLL"


@dataclass
class PromoAction:
    promotion_id: str
    promotion_type: str
    action: str  # 'cancel' or 'enroll'
    offer_id: str | None = None
    name: str = ""
    deal_price: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SiblingCancelAction:
    item_id: str
    site_id: str
    user_id: str
    promotion_id: str
    promotion_type: str
    offer_id: str | None = None
    promotion_name: str = ""
    reason: str = ""


@dataclass
class PolicyPlan:
    item_id: str
    decision_type: ActionType
    account_id: str
    child_user_id: str
    site_id: str
    current_price: float
    current_net: float
    current_shipping: float
    dim_str: str | None
    weight_str: str | None
    pricing_system: str
    change_desc: str = ""
    log_messages: list[tuple[str, str]] = field(default_factory=list)  # [(message, tag)]
    promos_to_cancel: list[PromoAction] = field(default_factory=list)
    promos_to_enroll: list[PromoAction] = field(default_factory=list)
    sibling_cancels: list[SiblingCancelAction] = field(default_factory=list)
    wait_for_review: bool = False
    raw_item_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationContext:
    """Encapsulates all evaluation inputs to prevent parameter clumps and primitive obsession."""
    account_id: str
    child_user_id: str
    site_id: str
    raw_item: dict[str, Any]
    cached_snapshot: dict[str, Any] | None = None
    active_promos: list[dict[str, Any]] = field(default_factory=list)
    candidate_promos: list[dict[str, Any]] = field(default_factory=list)
    cbt_siblings_data: list[dict[str, Any]] | None = None
    store_alias: str = ""
    discount_percent: float = 30.0
    was_pending_review: bool = False
    is_recently_shipping_reenrolled: bool = False
    is_old_cbt: bool = False


def _parse_num_with_unit(raw_val: Any, default_unit: str = "cm") -> tuple[float | None, str]:
    if raw_val is None:
        return None, default_unit
    if isinstance(raw_val, (int, float)):
        return float(raw_val), default_unit
    if isinstance(raw_val, dict):
        num = raw_val.get("number")
        unit = str(raw_val.get("unit") or default_unit).strip().lower()
        if num is not None:
            try:
                return float(num), unit
            except (ValueError, TypeError):
                return None, unit
        return None, default_unit
    if isinstance(raw_val, str):
        raw_str = raw_val.strip()
        import re
        m = re.match(r"^([\d.]+)\s*([a-zA-Z]*)$", raw_str)
        if m:
            try:
                n = float(m.group(1))
                u = m.group(2).lower() or default_unit
                return n, u
            except ValueError:
                pass
    return None, default_unit


def _convert_dim_to_cm(num: float | None, unit: str) -> float | None:
    if num is None:
        return None
    u = unit.lower()
    if u in ("m", "meter", "meters"):
        return num * 100.0
    if u in ("mm", "millimeter", "millimeters"):
        return num / 10.0
    if u in ("in", "inch", "inches"):
        return num * 2.54
    return num


def _convert_weight_to_grams(num: float | None, unit: str) -> float | None:
    if num is None:
        return None
    u = unit.lower()
    if u in ("kg", "kilogram", "kilograms"):
        return num * 1000.0
    if u in ("mg", "milligram"):
        return num / 1000.0
    if u in ("lb", "lbs", "pound", "pounds"):
        return num * 453.592
    if u in ("oz", "ounce", "ounces"):
        return num * 28.3495
    return num


def parse_dimensions_values(dim_input: str | dict[str, Any] | None) -> tuple[float | None, float | None, float | None]:
    """Parse (length, width, height) in centimeters from normalized or raw candidate dict/JSON."""
    if not dim_input:
        return None, None, None
    data = dim_input
    if isinstance(dim_input, str):
        try:
            data = json.loads(dim_input)
        except Exception:
            return None, None, None
    if not isinstance(data, dict):
        return None, None, None

    l_raw = None
    for k in ("PACKAGE_LENGTH", "SELLER_PACKAGE_LENGTH", "length", "LENGTH", "DEPTH"):
        if k in data:
            l_raw = data[k]
            break

    w_raw = None
    for k in ("PACKAGE_WIDTH", "SELLER_PACKAGE_WIDTH", "width", "WIDTH"):
        if k in data:
            w_raw = data[k]
            break

    h_raw = None
    for k in ("PACKAGE_HEIGHT", "SELLER_PACKAGE_HEIGHT", "height", "HEIGHT"):
        if k in data:
            h_raw = data[k]
            break

    l_num, l_u = _parse_num_with_unit(l_raw, "cm")
    w_num, w_u = _parse_num_with_unit(w_raw, "cm")
    h_num, h_u = _parse_num_with_unit(h_raw, "cm")

    return _convert_dim_to_cm(l_num, l_u), _convert_dim_to_cm(w_num, w_u), _convert_dim_to_cm(h_num, h_u)


def parse_weight_value(weight_input: str | dict[str, Any] | None) -> float | None:
    """Parse weight in grams from normalized or raw candidate dict/JSON."""
    if not weight_input:
        return None
    data = weight_input
    if isinstance(weight_input, str):
        try:
            data = json.loads(weight_input)
        except Exception:
            return None
    if isinstance(data, (int, float)):
        return float(data)
    if not isinstance(data, dict):
        return None

    if "number" in data:
        n, u = _parse_num_with_unit(data, "g")
        return _convert_weight_to_grams(n, u)

    for k in ("PACKAGE_WEIGHT", "SELLER_PACKAGE_WEIGHT", "weight", "WEIGHT"):
        if k in data:
            n, u = _parse_num_with_unit(data[k], "g")
            return _convert_weight_to_grams(n, u)

    return None


def is_dimensions_or_weight_changed(
    old_dim: str | dict[str, Any] | None,
    new_dim: str | dict[str, Any] | None,
    old_weight: str | dict[str, Any] | None,
    new_weight: str | dict[str, Any] | None,
) -> bool:
    """Determine whether dimensions or weight genuinely changed numerically.
    
    Tolerates differences in JSON structure, case sensitivity, and raw vs normalized format.
    Ignores initial baseline snapshot creation when old records are empty.
    """
    if not old_dim and not old_weight:
        return False
    if not new_dim and not new_weight:
        return False

    old_l, old_w, old_h = parse_dimensions_values(old_dim)
    new_l, new_w, new_h = parse_dimensions_values(new_dim)

    # Compare length, width, height with 0.1 cm tolerance
    for o_val, n_val in ((old_l, new_l), (old_w, new_w), (old_h, new_h)):
        if o_val is not None and n_val is not None:
            if abs(o_val - n_val) >= 0.1:
                return True

    old_wt = parse_weight_value(old_weight)
    new_wt = parse_weight_value(new_weight)

    # Compare weight with 1.0 g tolerance
    if old_wt is not None and new_wt is not None:
        if abs(old_wt - new_wt) >= 1.0:
            return True

    return False


def extract_dimensions_and_weight(raw_item: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract normalized dimensions and weight JSON strings from item detail."""
    attrs: dict[str, Any] = {}

    def _collect_attrs(source_list: list[Any]) -> None:
        for attr in source_list or []:
            if not isinstance(attr, dict):
                continue
            aid = str(attr.get("id") or "").strip().upper()
            if not aid or aid in attrs:
                continue
            v_struct = attr.get("value_struct")
            if isinstance(v_struct, dict) and v_struct.get("number") is not None:
                attrs[aid] = {"number": v_struct.get("number"), "unit": v_struct.get("unit")}
            elif attr.get("value_name") is not None:
                attrs[aid] = str(attr.get("value_name")).strip()

    _collect_attrs(raw_item.get("attributes") or [])

    dim_candidates = [
        "SELLER_PACKAGE_HEIGHT", "PACKAGE_HEIGHT", "HEIGHT",
        "SELLER_PACKAGE_WIDTH", "PACKAGE_WIDTH", "WIDTH",
        "SELLER_PACKAGE_LENGTH", "PACKAGE_LENGTH", "LENGTH", "DEPTH",
    ]
    weight_candidates = ["SELLER_PACKAGE_WEIGHT", "PACKAGE_WEIGHT", "WEIGHT"]

    has_dim = any(c in attrs for c in dim_candidates)
    has_weight = any(c in attrs for c in weight_candidates)

    if not (has_dim and has_weight):
        for v in raw_item.get("variations") or []:
            if not isinstance(v, dict):
                continue
            _collect_attrs(v.get("attributes") or [])
            if any(c in attrs for c in dim_candidates) and any(c in attrs for c in weight_candidates):
                break

    dims: dict[str, Any] = {}
    for key, candidates in [
        ("height", ["SELLER_PACKAGE_HEIGHT", "PACKAGE_HEIGHT", "HEIGHT"]),
        ("width", ["SELLER_PACKAGE_WIDTH", "PACKAGE_WIDTH", "WIDTH"]),
        ("length", ["SELLER_PACKAGE_LENGTH", "PACKAGE_LENGTH", "LENGTH", "DEPTH"]),
    ]:
        for cand in candidates:
            if cand in attrs:
                dims[key] = attrs[cand]
                break
    dim_str = json.dumps(dims, sort_keys=True, ensure_ascii=False) if dims else None

    weight_val = None
    for cand in weight_candidates:
        if cand in attrs:
            weight_val = attrs[cand]
            break
    weight_str = json.dumps(weight_val, sort_keys=True, ensure_ascii=False) if weight_val is not None else None
    return dim_str, weight_str


class ItemEventPolicy:
    """Pure domain decision policy for item events and pricing synchronizations.
    
    100% side-effect-free: Takes current item state, cache, promotions, and sibling data,
    and returns a PolicyPlan containing explicit actions and human-readable Chinese logs.
    """

    SITE_NAMES: dict[str, str] = {
        "MLM": "墨西哥站",
        "MLB": "巴西站",
        "MLC": "智利站",
        "MCO": "哥伦比亚站",
        "MLA": "阿根廷站",
        "MLU": "乌拉圭站",
        "MPE": "秘鲁站",
        "MEC": "厄瓜多尔站",
    }

    @classmethod
    def get_site_label(cls, item_id: str, site_id: str = "") -> str:
        s_id = site_id.upper() if site_id else (item_id[:3].upper() if len(item_id) >= 3 else "")
        return cls.SITE_NAMES.get(s_id, f"{s_id}站" if s_id else "")

    @staticmethod
    def format_platform_shipping_change_desc(
        ref_old_price: float,
        current_price: float,
        cached_shipping: float | None,
        current_shipping: float,
        current_net: float,
        fee_rate: float,
    ) -> str:
        eff_old_ship = cached_shipping
        if (
            (eff_old_ship is None or abs(eff_old_ship - current_shipping) < 0.01)
            and ref_old_price > 0
            and abs(ref_old_price - current_price) >= 0.01
            and fee_rate > 0
            and current_net > 0
        ):
            calc_old_s = round(ref_old_price * (1.0 - fee_rate) - current_net, 2)
            if calc_old_s > 0:
                eff_old_ship = calc_old_s

        if eff_old_ship is not None and abs(eff_old_ship - current_shipping) >= 0.01:
            ship_desc = f"平台运费调整 (${eff_old_ship:.2f} -> ${current_shipping:.2f})"
        elif current_shipping > 0:
            ship_desc = f"平台运费变动 (最新 ${current_shipping:.2f})"
        else:
            ship_desc = "平台运费调整"

        if abs(ref_old_price - current_price) >= 0.01:
            price_desc = f"，售价更新 (${ref_old_price:.2f} -> ${current_price:.2f})"
        else:
            price_desc = ""
        return f"{ship_desc}{price_desc}"

    @classmethod
    def _resolve_shipping_change(
        cls,
        item_info: ItemNetProceeds,
        cached_price: float | None,
        cached_shipping: float | None,
        current_shipping: float,
        current_price: float,
        official_orig: float,
    ) -> tuple[float, str]:
        """Unified helper to derive base reference price and format shipping change description."""
        if official_orig > 0 and abs(official_orig - current_price) >= 0.01:
            ref_old = official_orig
        elif cached_price is not None and abs(cached_price - current_price) >= 0.01:
            ref_old = cached_price
        elif cached_shipping is not None and item_info.fee_rate < 1.0 and item_info.net_proceeds > 0:
            ref_old = round((item_info.net_proceeds + cached_shipping) / (1.0 - item_info.fee_rate), 2)
        elif item_info.fee_rate < 1.0 and item_info.net_proceeds > 0:
            ref_old = round((item_info.net_proceeds + (cached_shipping or 0.0)) / (1.0 - item_info.fee_rate), 2)
        else:
            ref_old = current_price

        desc = cls.format_platform_shipping_change_desc(
            ref_old_price=ref_old,
            current_price=current_price,
            cached_shipping=cached_shipping,
            current_shipping=current_shipping,
            current_net=item_info.net_proceeds,
            fee_rate=item_info.fee_rate,
        )
        return ref_old, desc

    @classmethod
    def evaluate(
        cls,
        ctx: EvaluationContext | None = None,
        *,
        account_id: str = "",
        child_user_id: str = "",
        site_id: str = "",
        raw_item: dict[str, Any] | None = None,
        cached_snapshot: dict[str, Any] | None = None,
        active_promos: list[dict[str, Any]] | None = None,
        candidate_promos: list[dict[str, Any]] | None = None,
        cbt_siblings_data: list[dict[str, Any]] | None = None,
        store_alias: str = "",
        discount_percent: float = 30.0,
        was_pending_review: bool = False,
        is_recently_shipping_reenrolled: bool = False,
        is_old_cbt: bool = False,
    ) -> PolicyPlan:
        # Standardize into EvaluationContext
        if ctx is None:
            ctx = EvaluationContext(
                account_id=account_id,
                child_user_id=child_user_id,
                site_id=site_id,
                raw_item=raw_item or {},
                cached_snapshot=cached_snapshot,
                active_promos=active_promos or [],
                candidate_promos=candidate_promos or [],
                cbt_siblings_data=cbt_siblings_data,
                store_alias=store_alias,
                discount_percent=discount_percent,
                was_pending_review=was_pending_review,
                is_recently_shipping_reenrolled=is_recently_shipping_reenrolled,
                is_old_cbt=is_old_cbt,
            )

        item_id = str(ctx.raw_item.get("id") or "")
        site_label = cls.get_site_label(item_id, ctx.site_id)
        store_prefix = f"【{ctx.store_alias}】" if ctx.store_alias else ""
        item_tag = f"{store_prefix}{site_label}商品 {item_id}" if site_label else f"{store_prefix}商品 {item_id}"

        status = str(ctx.raw_item.get("status") or "").lower()
        sub_status = [str(s).lower() for s in (ctx.raw_item.get("sub_status") or [])]
        is_under_review = (status == "under_review" or "waiting_for_patch" in sub_status)

        item_info = extract_item_net_proceeds(ctx.raw_item)
        current_price = float(item_info.price or ctx.raw_item.get("price") or 0.0)
        current_net = float(item_info.net_proceeds or 0.0)
        current_shipping = float(item_info.shipping_cost or 0.0)
        official_orig = float(ctx.raw_item.get("original_price") or 0.0)
        dim_str, weight_str = extract_dimensions_and_weight(ctx.raw_item)

        # Baseline snapshot extraction
        cached = ctx.cached_snapshot or {}
        cached_price = float(cached["price"]) if cached.get("price") is not None else None
        cached_net = float(cached["net_proceeds"]) if cached.get("net_proceeds") is not None else None
        cached_shipping = float(cached["shipping_cost"]) if cached.get("shipping_cost") is not None else None
        old_dim = cached.get("dimensions")
        old_weight = cached.get("weight")
        db_system = cached.get("pricing_system")

        plan = PolicyPlan(
            item_id=item_id,
            decision_type=ActionType.NO_ACTION,
            account_id=ctx.account_id,
            child_user_id=ctx.child_user_id,
            site_id=ctx.site_id,
            current_price=current_price,
            current_net=current_net,
            current_shipping=current_shipping,
            dim_str=dim_str,
            weight_str=weight_str,
            pricing_system=db_system or "system1_discount",
            raw_item_snapshot=ctx.raw_item,
        )

        # Inactive or closed listing check
        if status in ("closed", "inactive", "paused"):
            plan.decision_type = ActionType.UPDATE_SNAPSHOT_ONLY
            return plan

        # Check dimension/weight change using semantic numerical comparison
        dim_or_weight_changed = is_dimensions_or_weight_changed(old_dim, dim_str, old_weight, weight_str)

        change_type: str | None = None
        change_desc = ""

        if dim_or_weight_changed:
            change_type = "seller_data"
            change_desc = "包装尺寸/重量数据变动"
        elif cached_net is not None and cached_net > 0 and current_net > 0:
            if abs(cached_net - current_net) >= 0.01:
                change_type = "seller_data"
                change_desc = f"净收益变动（${cached_net:.2f} -> ${current_net:.2f}）"
            else:
                shipping_changed = (
                    cached_shipping is not None and abs(cached_shipping - current_shipping) >= 0.01
                )
                already_synced_in_promo = (
                    (official_orig > 0 and not item_info.is_inconsistent)
                    or ctx.is_recently_shipping_reenrolled
                )
                inconsistent_or_price_diverged = (
                    (not already_synced_in_promo and item_info.is_inconsistent)
                    or (
                        not already_synced_in_promo
                        and cached_price is not None
                        and abs(cached_price - current_price) >= 0.05
                        and (official_orig > 0 or db_system == "system1_discount" or shipping_changed)
                    )
                )

                if not already_synced_in_promo and (shipping_changed or inconsistent_or_price_diverged) and db_system != "system2_155":
                    change_type = "platform_shipping"
                    _, change_desc = cls._resolve_shipping_change(
                        item_info=item_info,
                        cached_price=cached_price,
                        cached_shipping=cached_shipping,
                        current_shipping=current_shipping,
                        current_price=current_price,
                        official_orig=official_orig,
                    )
        elif current_net > 0:
            shipping_changed = (
                cached_shipping is not None and abs(cached_shipping - current_shipping) >= 0.01
            )
            already_synced_in_promo = (
                (official_orig > 0 and not item_info.is_inconsistent)
                or ctx.is_recently_shipping_reenrolled
            )
            if not already_synced_in_promo and shipping_changed and db_system != "system2_155":
                change_type = "platform_shipping"
                _, change_desc = cls._resolve_shipping_change(
                    item_info=item_info,
                    cached_price=cached_price,
                    cached_shipping=cached_shipping,
                    current_shipping=current_shipping,
                    current_price=current_price,
                    official_orig=official_orig,
                )
            elif not already_synced_in_promo and item_info.is_inconsistent and official_orig > 0:
                if ctx.is_old_cbt:
                    change_type = "platform_shipping"
                    _, change_desc = cls._resolve_shipping_change(
                        item_info=item_info,
                        cached_price=cached_price,
                        cached_shipping=cached_shipping,
                        current_shipping=current_shipping,
                        current_price=current_price,
                        official_orig=official_orig,
                    )
                else:
                    change_type = "seller_data"
                    change_desc = f"售价变动 (${official_orig:.2f} -> ${current_price:.2f})"
            elif not already_synced_in_promo and cached_price is not None and abs(cached_price - current_price) >= 0.01:
                if (official_orig > 0 or db_system == "system1_discount") and ctx.is_old_cbt:
                    change_type = "platform_shipping"
                    _, change_desc = cls._resolve_shipping_change(
                        item_info=item_info,
                        cached_price=cached_price,
                        cached_shipping=cached_shipping,
                        current_shipping=current_shipping,
                        current_price=current_price,
                        official_orig=official_orig,
                    )
                elif not ctx.is_old_cbt:
                    change_type = "seller_data"
                    change_desc = f"售价变动 (${cached_price:.2f} -> ${current_price:.2f})"
        else:
            if cached_price is None:
                plan.decision_type = ActionType.UPDATE_SNAPSHOT_ONLY
                return plan
            if abs(cached_price - current_price) >= 0.01:
                change_type = "seller_data"
                change_desc = f"售价变动 (${cached_price:.2f} -> ${current_price:.2f})"

        # 无需调整数据或变更已同步
        if change_type is None and not (is_under_review or ctx.was_pending_review):
            plan.decision_type = ActionType.UPDATE_SNAPSHOT_ONLY
            return plan

        # 分支 1：处于平台审核中（under_review / waiting_for_patch）
        if is_under_review:
            plan.wait_for_review = True
            plan.pricing_system = "system2_155"
            plan.decision_type = ActionType.WAIT_PLATFORM_REVIEW

            desc = change_desc or "数据变动"
            plan.change_desc = desc

            for ap in ctx.active_promos:
                p_id = ap.get("id") or ""
                p_type = ap.get("type") or "DEAL"
                p_raw = ap.get("raw") or ap
                p_name = str(ap.get("name") or p_raw.get("name") or "").strip()
                offer_id = str(p_raw.get("offer_id") or ap.get("offer_id") or "").strip() or None
                plan.promos_to_cancel.append(
                    PromoAction(
                        promotion_id=p_id,
                        promotion_type=p_type,
                        action="cancel",
                        offer_id=offer_id,
                        name=p_name,
                        raw=p_raw,
                    )
                )

            # 跨站点联动退出
            cbt_id = str(ctx.raw_item.get("cbt_item_id") or "").strip().upper()
            if cbt_id.startswith("CBT") and ctx.cbt_siblings_data:
                for sib in ctx.cbt_siblings_data:
                    sib_id = sib.get("item_id")
                    if sib_id and sib_id != item_id:
                        for sp in sib.get("active_promos") or []:
                            plan.sibling_cancels.append(
                                SiblingCancelAction(
                                    item_id=sib_id,
                                    site_id=sib.get("site_id") or sib_id[:3],
                                    user_id=sib.get("user_id") or "",
                                    promotion_id=sp.get("id") or "",
                                    promotion_type=sp.get("type") or "DEAL",
                                    offer_id=sp.get("offer_id"),
                                    promotion_name=sp.get("name") or "",
                                    reason=f"全球父商品 {cbt_id} {desc}",
                                )
                            )

            if plan.promos_to_cancel:
                plan.log_messages.append((
                    f"{item_tag}: 检测到{desc}（平台审核中），已触发防亏退出旧活动，待审核生效后将自动以新利润报回。",
                    "自动退出",
                ))
            else:
                plan.log_messages.append((
                    f"{item_tag}: 检测到{desc}（平台审核中），已置入待审队列，待审核生效后自动以新利润重算报回。",
                    "自动同步",
                ))
            return plan

        # 分支 2：审核结束恢复正常活跃（Review Resolved! 自动按新净收益重报活动）
        if ctx.was_pending_review and status == "active":
            plan.pricing_system = "system2_155"
            plan.decision_type = ActionType.REVIEW_RESOLVED_AUTO_REENROLL
            calc = calculate_deal_price(item_info, ctx.discount_percent)

            if calc.eligible and (ctx.active_promos or ctx.candidate_promos):
                target_promos = ctx.active_promos if ctx.active_promos else ctx.candidate_promos
                for p in target_promos:
                    p_id = p.get("id") or ""
                    p_type = p.get("type") or "DEAL"
                    p_raw = p.get("raw") or p
                    if p_type not in ("PRICE_DISCOUNT", "BANK", "PAYMENT_METHOD") and p_id:
                        offer_id = str(p.get("offer_id") or p_raw.get("offer_id") or "").strip() or None
                        plan.promos_to_enroll.append(
                            PromoAction(
                                promotion_id=p_id,
                                promotion_type=p_type,
                                action="enroll",
                                name=str(p.get("name") or p_raw.get("name") or "").strip(),
                                offer_id=offer_id,
                                deal_price=calc.deal_price,
                                raw=p_raw,
                            )
                        )
                plan.log_messages.append((
                    f"{item_tag}: 平台审核已生效通过！正在基于最新净收益 ${current_net:.2f} 重算并自动报回活动（折后价 ${calc.deal_price:.2f}）...",
                    "自动报回",
                ))
            else:
                plan.log_messages.append((
                    f"{item_tag}: 平台审核已生效通过，最新净收益 ${current_net:.2f} 已同步落盘（无适用活动候选）。",
                    "自动同步",
                ))
            return plan

        # 分支 3：活跃商品卖家主动修改数据（净收益或尺寸重量）-> 体系 2 防亏退出所有活动
        if change_type == "seller_data":
            plan.decision_type = ActionType.SELLER_DATA_CHANGE
            plan.change_desc = change_desc
            plan.pricing_system = "system2_155"

            if ctx.active_promos or official_orig > 0:
                plan.log_messages.append((
                    f"{item_tag} 检测到{change_desc}，执行新定价策略：退出所有活动，不再报任何活动。",
                    "自动退出",
                ))

            for ap in ctx.active_promos:
                p_id = ap.get("id") or ""
                p_type = ap.get("type") or "DEAL"
                p_raw = ap.get("raw") or ap
                p_name = str(ap.get("name") or p_raw.get("name") or "").strip()
                p_label = f"【{p_name}】" if p_name else p_id
                offer_id = str(p_raw.get("offer_id") or ap.get("offer_id") or "").strip() or None
                plan.promos_to_cancel.append(
                    PromoAction(
                        promotion_id=p_id,
                        promotion_type=p_type,
                        action="cancel",
                        offer_id=offer_id,
                        name=p_name,
                        raw=p_raw,
                    )
                )
                plan.log_messages.append((
                    f"{item_tag}: 已退出活动 {p_label}。",
                    "自动退出",
                ))

            # 跨站点联动退出
            cbt_id = str(ctx.raw_item.get("cbt_item_id") or "").strip().upper()
            if cbt_id.startswith("CBT") and ctx.cbt_siblings_data:
                for sib in ctx.cbt_siblings_data:
                    sib_id = sib.get("item_id")
                    if sib_id and sib_id != item_id:
                        for sp in sib.get("active_promos") or []:
                            plan.sibling_cancels.append(
                                SiblingCancelAction(
                                    item_id=sib_id,
                                    site_id=sib.get("site_id") or sib_id[:3],
                                    user_id=sib.get("user_id") or "",
                                    promotion_id=sp.get("id") or "",
                                    promotion_type=sp.get("type") or "DEAL",
                                    offer_id=sp.get("offer_id"),
                                    promotion_name=sp.get("name") or "",
                                    reason=f"全球父商品 {cbt_id} {change_desc}",
                                )
                            )
            return plan

        # 分支 4：体系 1 平台仅调整运费 -> 退出旧锁死原价并按最新运费继续报回折扣
        if change_type == "platform_shipping":
            plan.decision_type = ActionType.PLATFORM_SHIPPING_CHANGE
            plan.change_desc = change_desc
            plan.pricing_system = "system1_discount"

            started_promos = [ap for ap in ctx.active_promos if ap.get("status", "started") in ("started", "pending")]
            target_promos = started_promos if started_promos else ctx.candidate_promos
            if not target_promos:
                plan.decision_type = ActionType.UPDATE_SNAPSHOT_ONLY
                plan.log_messages.append((
                    f"{item_tag} 检测到{change_desc}，当前暂无可报活动，已更新最新运费基线。",
                    "运费更新",
                ))
                return plan

            plan.log_messages.append((
                f"{item_tag} {change_desc}，退出旧活动",
                "自动退出",
            ))

            for ap in started_promos:
                p_id = ap.get("id") or ""
                p_type = ap.get("type") or "DEAL"
                p_raw = ap.get("raw") or ap
                p_name = str(ap.get("name") or p_raw.get("name") or "").strip()
                offer_id = str(p_raw.get("offer_id") or ap.get("offer_id") or "").strip() or None
                plan.promos_to_cancel.append(
                    PromoAction(
                        promotion_id=p_id,
                        promotion_type=p_type,
                        action="cancel",
                        offer_id=offer_id,
                        name=p_name,
                        raw=p_raw,
                    )
                )

            calc = calculate_deal_price(item_info, ctx.discount_percent)
            if calc.eligible:
                for p in target_promos:
                    p_id = p.get("id") or ""
                    p_type = p.get("type") or "DEAL"
                    p_raw = p.get("raw") or p
                    p_name = str(p.get("name") or p_raw.get("name") or "").strip()
                    offer_id = str(p_raw.get("offer_id") or p.get("offer_id") or "").strip() or None
                    if p_type not in ("PRICE_DISCOUNT", "BANK", "PAYMENT_METHOD") and p_id:
                        plan.promos_to_enroll.append(
                            PromoAction(
                                promotion_id=p_id,
                                promotion_type=p_type,
                                action="enroll",
                                name=p_name,
                                offer_id=offer_id,
                                deal_price=calc.deal_price,
                                raw=p_raw,
                            )
                        )
            else:
                plan.log_messages.append((
                    f"{item_tag}: 检测到{change_desc}，退出旧活动后因运费上调暂不满足活动门槛。",
                    "自动退出",
                ))
            return plan

        # 默认：无实质变动，静默更新快照
        plan.decision_type = ActionType.UPDATE_SNAPSHOT_ONLY
        return plan
