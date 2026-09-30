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
    # When an item is modified during an active promotion, Mercado Libre updates
    # net_proceeds.amount immediately while locking top-level original_price to the
    # pre-modification enrollment price. Therefore, when breakdown_sum diverges from
    # official_original (>= 0.05), breakdown_sum reflects the true updated base price
    # and signals an in-promotion item modification (is_inconsistent=True).
    breakdown_sum = round(raw_amount + shipping_cost + sale_fee, 2)
    official_original = float(item.get("original_price") or 0.0)
    is_inconsistent = False
    inconsistent_reason = None

    if official_original > 0 and breakdown_sum > 0 and abs(breakdown_sum - official_original) >= 0.05:
        is_inconsistent = True
        inconsistent_reason = f"最新价格构成(${breakdown_sum:.2f})与活动锁死原价(${official_original:.2f})不一致"
        base_price = breakdown_sum
    elif official_original > 0:
        base_price = official_original
    elif breakdown_sum > 0:
        base_price = breakdown_sum
    else:
        base_price = float(item.get("price") or 0.0)

    # Fee rate derived from the official breakdown: sale_fee / base_price
    if official_original > 0 and sale_fee > 0:
        fee_rate = round(sale_fee / official_original, 6)
    elif base_price > 0 and sale_fee > 0:
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
        is_inconsistent=is_inconsistent,
        inconsistent_reason=inconsistent_reason,
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

        # 1. Target net proceeds threshold strictly based on user's net proceeds discount
        target_net = round(orig_net * (1.0 - discount_percent / 100.0), 2)

        # 2. Seller's effective settlement price after seller-funded discount
        if seller_pct > 0:
            effective_seller_price = round(p_orig * (1.0 - seller_pct / 100.0), 2)
        else:
            effective_seller_price = smart_price

        # 3. Final net proceeds strictly protecting shipping cost in full
        sale_fee_at_deal = round(effective_seller_price * fee_rate, 2)
        final_net_at_deal = round(effective_seller_price - sale_fee_at_deal - shipping, 2)

        # 4. Strict net proceeds floor check: must not breach target_net
        if final_net_at_deal < target_net:
            return PricingResult(
                item_id=item_info.item_id,
                original_price=p_orig,
                original_net=orig_net,
                target_net=target_net,
                deal_price=smart_price,
                shipping_cost=shipping,
                sale_fee_at_deal=sale_fee_at_deal,
                final_net_at_deal=final_net_at_deal,
                discount_percent=discount_percent,
                eligible=False,
                skip_reason=f"联合活动实际净回款(${final_net_at_deal:.2f})低于目标保底净回款(${target_net:.2f})，跳过",
            )
        else:
            return PricingResult(
                item_id=item_info.item_id,
                original_price=p_orig,
                original_net=orig_net,
                target_net=target_net,
                deal_price=smart_price,
                shipping_cost=shipping,
                sale_fee_at_deal=sale_fee_at_deal,
                final_net_at_deal=final_net_at_deal,
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

    if deal_price <= 0:
        return PricingResult(
            item_id=item_info.item_id,
            original_price=p_orig,
            original_net=round(orig_net, 2),
            target_net=round(target_net, 2),
            deal_price=deal_price,
            shipping_cost=shipping,
            sale_fee_at_deal=0.0,
            final_net_at_deal=0.0,
            discount_percent=discount_percent,
            eligible=False,
            skip_reason="计算活动价小于等于0，跳过",
        )

    # Safety: deal_price cannot exceed original price
    if deal_price > p_orig:
        deal_price = p_orig

    # 3. Calculate final numbers at this deal price
    sale_fee_at_deal = round(deal_price * fee_rate, 2)
    final_net_at_deal = round(deal_price - sale_fee_at_deal - shipping, 2)

    # 4. Result is handed over to Mercado Libre API directly without local pre-filtering
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
        eligible=True,
        skip_reason=None,
    )
