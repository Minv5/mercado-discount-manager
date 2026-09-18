from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ItemNetProceeds:
    item_id: str
    price: float
    currency_id: str
    shipping_cost: float
    sale_fee: float
    fee_rate: float
    net_proceeds: float
    is_inconsistent: bool = False
    inconsistent_reason: str | None = None


@dataclass
class PricingResult:
    item_id: str
    original_price: float
    original_net: float
    target_net: float
    deal_price: float
    shipping_cost: float
    sale_fee_at_deal: float
    final_net_at_deal: float
    discount_percent: float
    eligible: bool
    skip_reason: str | None = None


def extract_item_net_proceeds(item: dict[str, Any]) -> ItemNetProceeds:
    """Extract price, shipping, sale_fee, and net_proceeds from official GET /marketplace/items/{id}."""
    item_id = str(item.get("id") or item.get("item_id") or "")
    currency_id = str(item.get("currency_id") or "USD").upper()

    net_obj = item.get("net_proceeds") or {}
    raw_amount = float(net_obj.get("amount") or 0.0)
    additional = net_obj.get("additional_concepts") or []

    shipping_cost = 0.0
    sale_fee = 0.0

    for concept in additional:
        cid = str(concept.get("id") or "").lower()
        amt = float(concept.get("amount") or 0.0)
        if cid == "shipping_cost":
            shipping_cost = amt
        elif cid == "sale_fee":
            sale_fee = amt

    # Authoritative catalog original price:
    # 1. Prefer item.get("original_price") (Mercado official catalog base price)
    # 2. If missing, use breakdown sum (net_proceeds + shipping + sale_fee)
    # 3. Fallback to item.get("price") only if breakdown sum is 0
    breakdown_sum = round(raw_amount + shipping_cost + sale_fee, 2)
    official_original = float(item.get("original_price") or 0.0)
    if official_original > 0:
        base_price = official_original
    elif breakdown_sum > 0:
        base_price = breakdown_sum
    else:
        base_price = float(item.get("price") or 0.0)

    # Fee rate derived from the official breakdown: sale_fee / base_price
    if base_price > 0 and sale_fee > 0:
        fee_rate = round(sale_fee / base_price, 6)
    elif breakdown_sum > 0 and sale_fee > 0:
        fee_rate = round(sale_fee / breakdown_sum, 6)
    else:
        fee_rate = 0.145  # Standard CBT category fee fallback

    # Base net proceeds: 100% authoritative from net_proceeds.amount
    # (Matches seller's ERP configuration, NEVER override with discounted price)
    effective_net = raw_amount

    return ItemNetProceeds(
        item_id=item_id,
        price=base_price,
        currency_id=currency_id,
        shipping_cost=shipping_cost,
        sale_fee=sale_fee,
        fee_rate=fee_rate,
        net_proceeds=effective_net,
        is_inconsistent=False,
        inconsistent_reason=None,
    )


def calculate_deal_price(
    item_info: ItemNetProceeds,
    discount_percent: float,
    promotion_constraints: dict[str, Any] | None = None,
    promotion_type: str = "DEAL",
) -> PricingResult:
    """Calculate the deal price targeting discount_percent on net proceeds,

    protecting shipping in full and skipping if promotion threshold cannot be met.
    """
    p_orig = item_info.price
    shipping = item_info.shipping_cost
    fee_rate = item_info.fee_rate
    orig_net = item_info.net_proceeds
    constraints = promotion_constraints or {}
    clean_p_type = promotion_type.strip().upper()

    if p_orig <= 0 or orig_net <= 0:
        return PricingResult(
            item_id=item_info.item_id,
            original_price=p_orig,
            original_net=orig_net,
            target_net=0.0,
            deal_price=p_orig,
            shipping_cost=shipping,
            sale_fee_at_deal=0.0,
            final_net_at_deal=0.0,
            discount_percent=discount_percent,
            eligible=False,
            skip_reason="原价或净回款小于等于0，跳过",
        )

    # Handle SMART co-funded offers
    if clean_p_type == "SMART" or constraints.get("offer_id"):
        seller_pct = float(constraints.get("seller_percentage") or 0.0)
        smart_price = float(constraints.get("price") or p_orig)
        if seller_pct > 0 and seller_pct > discount_percent:
            return PricingResult(
                item_id=item_info.item_id,
                original_price=p_orig,
                original_net=orig_net,
                target_net=round(orig_net * (1.0 - discount_percent / 100.0), 2),
                deal_price=smart_price,
                shipping_cost=shipping,
                sale_fee_at_deal=round(smart_price * fee_rate, 2),
                final_net_at_deal=round(smart_price * (1.0 - fee_rate) - shipping, 2),
                discount_percent=discount_percent,
                eligible=False,
                skip_reason=f"平台联合活动要求卖家承担折扣({seller_pct:.2f}%)高于设定上限({discount_percent:.1f}%)，跳过",
            )
        else:
            return PricingResult(
                item_id=item_info.item_id,
                original_price=p_orig,
                original_net=orig_net,
                target_net=round(orig_net * (1.0 - seller_pct / 100.0), 2),
                deal_price=smart_price,
                shipping_cost=shipping,
                sale_fee_at_deal=round(smart_price * fee_rate, 2),
                final_net_at_deal=round(smart_price * (1.0 - fee_rate) - shipping, 2),
                discount_percent=discount_percent,
                eligible=True,
                skip_reason=None,
            )

    # 1. Target net proceeds
    discount_factor = max(0.0, 1.0 - (discount_percent / 100.0))
    target_net = round(orig_net * discount_factor, 4)

    # 2. Reverse calculate deal price: P_deal * (1 - fee_rate) - shipping = target_net
    #    => P_deal = (target_net + shipping) / (1 - fee_rate)
    denom = 1.0 - fee_rate
    if denom <= 0.01:
        denom = 0.855  # fallback safe rate

    deal_price = round((target_net + shipping) / denom, 2)

    # Safety: deal_price cannot exceed original price
    if deal_price > p_orig:
        deal_price = p_orig

    # 3. Calculate final numbers at this deal price
    sale_fee_at_deal = round(deal_price * fee_rate, 2)
    final_net_at_deal = round(deal_price - sale_fee_at_deal - shipping, 2)

    # 4. Check against promotion constraints (platform max allowable price)
    max_allowed = constraints.get("max_discounted_price") or constraints.get("suggested_discounted_price")

    eligible = True
    skip_reason = None

    if max_allowed is not None:
        try:
            max_allowed_val = float(max_allowed)
            if max_allowed_val > 0 and deal_price > max_allowed_val:
                # Platform requires a lower price than our floor!
                # Iron rule: "依照设置的折扣为准，参加不上就不参加"
                eligible = False
                skip_reason = f"平台要求限价(${max_allowed_val:.2f})低于净回款保护售价(${deal_price:.2f})，跳过"
        except (ValueError, TypeError):
            pass

    return PricingResult(
        item_id=item_info.item_id,
        original_price=p_orig,
        original_net=round(orig_net, 2),
        target_net=round(target_net, 2),
        deal_price=deal_price,
        shipping_cost=shipping,
        sale_fee_at_deal=sale_fee_at_deal,
        final_net_at_deal=final_net_at_deal,
        discount_percent=discount_percent,
        eligible=eligible,
        skip_reason=skip_reason,
    )
