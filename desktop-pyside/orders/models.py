from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any


def format_order_date(date_val: Any) -> str:
    """Format various Mercado Libre ISO 8601 date representations into Beijing time 'YYYY-MM-DD HH:MM:SS'."""
    if not date_val:
        return ""
    if isinstance(date_val, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(date_val), tz=timezone(timedelta(hours=8)))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""
    raw_val = str(date_val).strip()
    if not raw_val:
        return ""
    try:
        clean_val = raw_val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_val)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    if "T" in raw_val:
        parts = raw_val.split("T")
        date_p = parts[0]
        time_p = parts[1][:8] if len(parts) > 1 else ""
        return f"{date_p} {time_p}".strip()
    return raw_val[:19]


def parse_order_timestamp(date_val: Any) -> float:
    """Parse date into timestamp float for chronological sorting."""
    if not date_val:
        return 0.0
    if isinstance(date_val, (int, float)):
        return float(date_val)
    raw_val = str(date_val).strip()
    if not raw_val:
        return 0.0
    try:
        clean_val = raw_val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


# Fixed Badge Style Definitions (Consistent with Service Center and Theme)
BADGE_STYLE_GOLD = "background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); font-size: 11px; font-weight: 500; border-radius: 4px;"
BADGE_STYLE_GREEN = "background: rgba(16, 185, 129, 0.14); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.30); font-size: 11px; font-weight: 500; border-radius: 4px;"
BADGE_STYLE_BLUE = "background: rgba(59, 130, 246, 0.14); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.30); font-size: 11px; font-weight: 500; border-radius: 4px;"
BADGE_STYLE_GRAY = "background: rgba(100, 116, 139, 0.16); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.28); font-size: 11px; font-weight: 500; border-radius: 4px;"
BADGE_STYLE_RED = "background: rgba(239, 68, 68, 0.16); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); font-size: 11px; font-weight: 500; border-radius: 4px;"


def format_dimensions_display(dims: Any) -> str:
    """Format dimensions into '10 × 10 × 11 cm · 200g'."""
    if not dims:
        return ""
    if isinstance(dims, dict):
        l = dims.get("length") or dims.get("l") or 0
        w = dims.get("width") or dims.get("w") or 0
        h = dims.get("height") or dims.get("h") or 0
        wt = dims.get("weight") or dims.get("wt") or 0
        dim_part = ""
        if l or w or h:
            dim_part = f"{l} × {w} × {h} cm"
        wt_part = ""
        if wt:
            try:
                f_wt = float(wt)
                if f_wt >= 1000:
                    wt_part = f"{f_wt / 1000:g} kg"
                else:
                    wt_part = f"{f_wt:g} g"
            except Exception:
                wt_part = f"{wt} g"
        parts = [p for p in (dim_part, wt_part) if p]
        return " · ".join(parts)
    raw = str(dims).strip()
    if not raw:
        return ""
    m = re.match(r"^([\d.]+)\s*[xX*×]\s*([\d.]+)\s*[xX*×]\s*([\d.]+)(?:[,;\s]+([\d.]+))?", raw)
    if m:
        l, w, h, wt = m.group(1), m.group(2), m.group(3), m.group(4)
        dim_str = f"{l} × {w} × {h} cm"
        if wt:
            try:
                f_wt = float(wt)
                wt_str = f"{f_wt / 1000:g} kg" if f_wt >= 1000 else f"{f_wt:g} g"
            except Exception:
                wt_str = f"{wt} g"
            return f"{dim_str} · {wt_str}"
        return dim_str
    return raw


def get_order_status_badge_info(status: str, shipping_status: str = "") -> tuple[str, str, str]:
    """Return (badge_text, qss_style, filter_category) for order status.

    Strictly prioritizes shipping.status over order payment status to correctly reflect delivery stage.
    """
    ship_st = (shipping_status or "").strip().lower()
    if ship_st in ("shipped", "in_transit"):
        return ("运送中", BADGE_STYLE_BLUE, "shipped")
    elif ship_st in ("delivered", "completed"):
        return ("已送达", BADGE_STYLE_GREEN, "delivered")
    elif ship_st in ("ready_to_ship", "to_be_shipped"):
        return ("待发货", BADGE_STYLE_GOLD, "paid")
    elif ship_st == "cancelled":
        return ("已取消", BADGE_STYLE_GRAY, "cancelled")
    elif ship_st in ("not_delivered", "failed"):
        return ("未送达", BADGE_STYLE_GRAY, "not_delivered")

    st = (status or "").strip().lower()
    if st in ("paid", "confirmed", "to_be_shipped", "ready_to_ship"):
        return ("待发货", BADGE_STYLE_GOLD, "paid")
    elif st in ("shipped", "in_transit"):
        return ("运送中", BADGE_STYLE_BLUE, "shipped")
    elif st in ("delivered", "completed"):
        return ("已送达", BADGE_STYLE_GREEN, "delivered")
    elif st in ("payment_required", "pending"):
        return ("待付款", BADGE_STYLE_GOLD, "payment_required")
    elif st in ("payment_in_process", "in_process"):
        return ("支付中", BADGE_STYLE_GOLD, "payment_in_process")
    elif st in ("cancelled", "invalid"):
        return ("已取消", BADGE_STYLE_GRAY, "cancelled")
    elif not st:
        return ("未知状态", BADGE_STYLE_GRAY, "unknown")
    else:
        # Fallback display with clean title
        disp = st.replace("_", " ").title()
        return (disp, BADGE_STYLE_GRAY, st)


@dataclass
class OrderItemModel:
    item_id: str = ""
    title: str = ""
    sku: str = ""
    variation_id: str = ""
    variation_attributes: str = ""
    quantity: int = 1
    unit_price: float = 0.0
    full_unit_price: float = 0.0
    currency_id: str = ""
    thumbnail: str = ""
    permalink: str = ""
    dimensions: str = ""
    sale_fee: float = 0.0

    @property
    def total_sale_fee(self) -> float:
        """Total sale fee for this item line (unit sale fee * quantity)."""
        return round(self.sale_fee * self.quantity, 2)


@dataclass
class ShippingInfoModel:
    shipment_id: str = ""
    logistic_type: str = ""
    tracking_number: str = ""
    tracking_method: str = ""
    status: str = ""
    substatus: str = ""
    receiver_name: str = ""
    receiver_phone: str = ""
    country_id: str = ""
    state_name: str = ""
    city_name: str = ""
    street_name: str = ""
    street_number: str = ""
    comment: str = ""
    zip_code: str = ""
    dimensions: str = ""
    shipping_cost: float = 0.0

    @property
    def full_address(self) -> str:
        parts = []
        if self.street_name or self.street_number:
            parts.append(f"{self.street_name} {self.street_number}".strip())
        if self.city_name:
            parts.append(self.city_name)
        if self.state_name:
            parts.append(self.state_name)
        if self.zip_code:
            parts.append(f"CP {self.zip_code}")
        if self.country_id:
            parts.append(self.country_id)
        return ", ".join(filter(None, parts))


@dataclass
class PaymentInfoModel:
    payment_id: str = ""
    payment_method: str = ""
    payment_type: str = ""
    status: str = ""
    transaction_amount: float = 0.0
    currency_id: str = ""
    date_approved: str = ""
    marketplace_fee: float = 0.0
    taxes_amount: float = 0.0


@dataclass
class OrderModel:
    order_id: str
    account_id: str
    store_name: str
    site_id: str = ""
    pack_id: str = ""
    date_created: str = ""
    date_closed: str = ""
    status: str = "paid"
    status_detail: str = ""
    buyer_id: str = ""
    buyer_nickname: str = ""
    buyer_full_name: str = ""
    total_amount: float = 0.0
    paid_amount: float = 0.0
    currency_id: str = "USD"
    sale_fee: float = 0.0
    shipping_cost: float = 0.0
    taxes_amount: float = 0.0
    items: list[OrderItemModel] = field(default_factory=list)
    shipping: ShippingInfoModel = field(default_factory=ShippingInfoModel)
    payments: list[PaymentInfoModel] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_timestamp: float = 0.0
    affects_reputation: bool = False
    reputation_reason: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def total_sale_fee(self) -> float:
        """Total platform commission fee (Sale Fee) across all item lines."""
        items_fee = round(sum(it.sale_fee * it.quantity for it in self.items), 2)
        if items_fee > 0:
            return items_fee
        if self.sale_fee > 0:
            return round(self.sale_fee, 2)
        return 0.0

    @property
    def effective_shipping_cost(self) -> float:
        """Shipping cost recorded on order or carrier shipment."""
        return round(self.shipping_cost or self.shipping.shipping_cost, 2)

    @property
    def estimated_net_profit(self) -> float:
        """Estimated net received profit after subtracting commission and shipping fees."""
        base = self.paid_amount if self.paid_amount > 0 else self.total_amount
        return round(base - self.total_sale_fee - self.effective_shipping_cost, 2)

    @property
    def display_time(self) -> str:
        return format_order_date(self.date_created)

    @property
    def status_badge_info(self) -> tuple[str, str, str]:
        return get_order_status_badge_info(self.status, self.shipping.status)

    @property
    def primary_item(self) -> OrderItemModel:
        if self.items:
            return self.items[0]
        return OrderItemModel()

    @property
    def primary_thumbnail(self) -> str:
        if self.items and self.items[0].thumbnail:
            return self.items[0].thumbnail
        return ""

    @property
    def total_quantity(self) -> int:
        return sum(it.quantity for it in self.items) or 1

    @property
    def summary_skus(self) -> str:
        skus = [it.sku for it in self.items if it.sku]
        return ", ".join(skus) if skus else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "account_id": self.account_id,
            "store_name": self.store_name,
            "site_id": self.site_id,
            "pack_id": self.pack_id,
            "date_created": self.date_created,
            "date_closed": self.date_closed,
            "status": self.status,
            "status_detail": self.status_detail,
            "buyer_id": self.buyer_id,
            "buyer_nickname": self.buyer_nickname,
            "buyer_full_name": self.buyer_full_name,
            "total_amount": self.total_amount,
            "paid_amount": self.paid_amount,
            "currency_id": self.currency_id,
            "sale_fee": self.sale_fee,
            "shipping_cost": self.shipping_cost,
            "taxes_amount": self.taxes_amount,
            "items": [asdict(it) for it in self.items],
            "shipping": asdict(self.shipping),
            "payments": [asdict(p) for p in self.payments],
            "tags": list(self.tags),
            "created_timestamp": self.created_timestamp,
            "affects_reputation": self.affects_reputation,
            "reputation_reason": self.reputation_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderModel:
        items = []
        for it in data.get("items") or []:
            if isinstance(it, dict):
                items.append(OrderItemModel(
                    item_id=str(it.get("item_id") or ""),
                    title=str(it.get("title") or ""),
                    sku=str(it.get("sku") or ""),
                    variation_id=str(it.get("variation_id") or ""),
                    variation_attributes=str(it.get("variation_attributes") or ""),
                    quantity=int(it.get("quantity") or 1),
                    unit_price=float(it.get("unit_price") or 0.0),
                    full_unit_price=float(it.get("full_unit_price") or 0.0),
                    currency_id=str(it.get("currency_id") or ""),
                    thumbnail=str(it.get("thumbnail") or ""),
                    permalink=str(it.get("permalink") or ""),
                    dimensions=str(it.get("dimensions") or ""),
                    sale_fee=float(it.get("sale_fee") or 0.0),
                ))
        sh = data.get("shipping") or {}
        shipping = ShippingInfoModel(
            shipment_id=str(sh.get("shipment_id") or ""),
            logistic_type=str(sh.get("logistic_type") or ""),
            tracking_number=str(sh.get("tracking_number") or ""),
            tracking_method=str(sh.get("tracking_method") or ""),
            status=str(sh.get("status") or ""),
            substatus=str(sh.get("substatus") or ""),
            receiver_name=str(sh.get("receiver_name") or ""),
            receiver_phone=str(sh.get("receiver_phone") or ""),
            country_id=str(sh.get("country_id") or ""),
            state_name=str(sh.get("state_name") or ""),
            city_name=str(sh.get("city_name") or ""),
            street_name=str(sh.get("street_name") or ""),
            street_number=str(sh.get("street_number") or ""),
            comment=str(sh.get("comment") or ""),
            zip_code=str(sh.get("zip_code") or ""),
            dimensions=str(sh.get("dimensions") or ""),
            shipping_cost=float(sh.get("shipping_cost") or 0.0),
        ) if isinstance(sh, dict) else ShippingInfoModel()

        payments = []
        for p in data.get("payments") or []:
            if isinstance(p, dict):
                payments.append(PaymentInfoModel(
                    payment_id=str(p.get("payment_id") or ""),
                    payment_method=str(p.get("payment_method") or ""),
                    payment_type=str(p.get("payment_type") or ""),
                    status=str(p.get("status") or ""),
                    transaction_amount=float(p.get("transaction_amount") or 0.0),
                    currency_id=str(p.get("currency_id") or ""),
                    date_approved=str(p.get("date_approved") or ""),
                    marketplace_fee=float(p.get("marketplace_fee") or 0.0),
                    taxes_amount=float(p.get("taxes_amount") or 0.0),
                ))

        created_ts = float(data.get("created_timestamp") or 0.0)
        date_created = str(data.get("date_created") or "")
        if not created_ts and date_created:
            created_ts = parse_order_timestamp(date_created)

        items_fee = round(sum(it.sale_fee * it.quantity for it in items), 2)
        raw_sale_fee = float(data.get("sale_fee") or 0.0)
        tot_sale_fee = items_fee if items_fee > 0 else raw_sale_fee

        return cls(
            order_id=str(data.get("order_id") or ""),
            account_id=str(data.get("account_id") or ""),
            store_name=str(data.get("store_name") or ""),
            site_id=str(data.get("site_id") or ""),
            pack_id=str(data.get("pack_id") or ""),
            date_created=date_created,
            date_closed=str(data.get("date_closed") or ""),
            status=str(data.get("status") or "paid"),
            status_detail=str(data.get("status_detail") or ""),
            buyer_id=str(data.get("buyer_id") or ""),
            buyer_nickname=str(data.get("buyer_nickname") or ""),
            buyer_full_name=str(data.get("buyer_full_name") or ""),
            total_amount=float(data.get("total_amount") or 0.0),
            paid_amount=float(data.get("paid_amount") or 0.0),
            currency_id=str(data.get("currency_id") or "USD"),
            sale_fee=tot_sale_fee,
            shipping_cost=float(data.get("shipping_cost") or 0.0),
            taxes_amount=float(data.get("taxes_amount") or 0.0),
            items=items,
            shipping=shipping,
            payments=payments,
            tags=list(data.get("tags") or []),
            created_timestamp=created_ts,
            affects_reputation=bool(data.get("affects_reputation") or False),
            reputation_reason=str(data.get("reputation_reason") or ""),
        )


def parse_order_from_dict(
    raw: dict[str, Any],
    account_id: str,
    store_name: str,
    site_id: str = "",
) -> OrderModel:
    """Parse raw Mercado Libre order json (CBT or standard) into strongly typed OrderModel."""
    # Check if CBT pack wrapper or skeleton
    pack_id = ""
    if "orders" in raw and isinstance(raw["orders"], list) and raw["orders"]:
        primary_ord = raw["orders"][0]
        oid = str(primary_ord.get("id") or "")
        pack_id = str(raw.get("id") or primary_ord.get("pack_id") or "")
    else:
        primary_ord = raw
        oid = str(raw.get("id") or "")
        pack_id = str(raw.get("pack_id") or "")

    # Date handling
    date_created = str(primary_ord.get("date_created") or raw.get("date_created") or "")
    date_closed = str(primary_ord.get("date_closed") or raw.get("date_closed") or "")
    ts = parse_order_timestamp(date_created)

    # Site derivation
    resolved_site = site_id or str(
        (raw.get("context") or {}).get("site")
        or (primary_ord.get("context") or {}).get("site")
        or raw.get("site_id")
        or ""
    ).strip()

    # Status & Tags
    tags = list(primary_ord.get("tags") or raw.get("tags") or [])
    status = str(primary_ord.get("status") or raw.get("status") or "paid").lower()
    if "delivered" in tags:
        status = "delivered"
    status_detail = str(primary_ord.get("status_detail") or (primary_ord.get("order_items") or [{}])[0].get("status") or "")

    # Buyer
    buyer_dict = primary_ord.get("buyer") or raw.get("buyer") or {}
    buyer_id = str(buyer_dict.get("id") or "")
    buyer_nickname = str(buyer_dict.get("nickname") or "").strip()
    first_n = str(buyer_dict.get("first_name") or "").strip()
    last_n = str(buyer_dict.get("last_name") or "").strip()
    buyer_full = f"{first_n} {last_n}".strip()

    if not buyer_nickname:
        if buyer_full:
            buyer_nickname = buyer_full
        elif buyer_id:
            buyer_nickname = f"买家 #{buyer_id[-4:]}"
        else:
            buyer_nickname = "买家"

    # Total amounts
    total_amount = float(primary_ord.get("total_amount") or raw.get("total_amount") or 0.0)
    paid_amount = float(primary_ord.get("paid_amount") or raw.get("paid_amount") or total_amount)
    currency_id = str(primary_ord.get("currency_id") or raw.get("currency_id") or "USD")

    # Order items
    items: list[OrderItemModel] = []
    raw_items = primary_ord.get("order_items") or raw.get("order_items") or []
    # If CBT config items present
    if not raw_items and "config" in raw and isinstance(raw["config"], dict):
        raw_items = raw["config"].get("items") or []

    for ri in raw_items:
        it_obj = ri.get("item") or ri
        item_id = str(it_obj.get("id") or "")
        title = str(it_obj.get("title") or "")
        sku = str(it_obj.get("seller_sku") or it_obj.get("seller_custom_field") or "")
        var_id = str(it_obj.get("variation_id") or "")
        qty = int(ri.get("quantity") or 1)
        unit_price = float(ri.get("unit_price") or 0.0)
        full_price = float(ri.get("full_unit_price") or unit_price)
        cur = str(ri.get("currency_id") or currency_id)
        thumb = ""
        if isinstance(it_obj.get("pictures"), list) and it_obj["pictures"]:
            thumb = str(it_obj["pictures"][0].get("url") or it_obj["pictures"][0].get("secure_url") or "")
        if not thumb:
            thumb = str(it_obj.get("thumbnail") or it_obj.get("secure_thumbnail") or "")
        permalink = str(it_obj.get("permalink") or "")

        # Format variation attributes
        var_attrs = []
        raw_vas = (
            it_obj.get("variation_attributes")
            or ri.get("variation_attributes")
            or it_obj.get("attribute_combinations")
            or ri.get("attribute_combinations")
            or []
        )
        for va in raw_vas:
            if isinstance(va, dict):
                vn = va.get("name") or va.get("id") or ""
                vv = va.get("value_name") or ""
                if vn and vv:
                    var_attrs.append(f"{vn}: {vv}")
                elif vv:
                    var_attrs.append(str(vv))
        var_text = "; ".join(var_attrs)
        it_dims = format_dimensions_display(it_obj.get("dimensions") or "")
        it_sale_fee = float(ri.get("sale_fee") or 0.0)

        items.append(
            OrderItemModel(
                item_id=item_id,
                title=title,
                sku=sku,
                variation_id=var_id,
                variation_attributes=var_text,
                quantity=qty,
                unit_price=unit_price,
                full_unit_price=full_price,
                currency_id=cur,
                thumbnail=thumb,
                permalink=permalink,
                dimensions=it_dims,
                sale_fee=it_sale_fee,
            )
        )

    # Shipping info
    shipping_dict = primary_ord.get("shipping") or raw.get("shipping") or raw.get("shipment") or {}
    ship_id = str(shipping_dict.get("id") or "")
    log_type = str(shipping_dict.get("logistic_type") or shipping_dict.get("shipping_mode") or "官方物流")
    ship_status = str(shipping_dict.get("status") or "")
    ship_substatus = str(shipping_dict.get("substatus") or "")
    rec_addr = shipping_dict.get("receiver_address") or {}
    rec_name = str(rec_addr.get("receiver_name") or buyer_full or "")
    rec_phone = str(rec_addr.get("receiver_phone") or "")
    country_id = str((rec_addr.get("country") or {}).get("id") or "")
    state_name = str((rec_addr.get("state") or {}).get("name") or "")
    city_name = str((rec_addr.get("city") or {}).get("name") or "")
    street_name = str(rec_addr.get("street_name") or "")
    street_number = str(rec_addr.get("street_number") or "")
    comment = str(rec_addr.get("comment") or "")
    zip_code = str(rec_addr.get("zip_code") or "")
    ship_dims = format_dimensions_display(shipping_dict.get("dimensions") or shipping_dict.get("shipping_dimensions") or "")

    shipping = ShippingInfoModel(
        shipment_id=ship_id,
        logistic_type=log_type,
        status=ship_status,
        substatus=ship_substatus,
        receiver_name=rec_name,
        receiver_phone=rec_phone,
        country_id=country_id,
        state_name=state_name,
        city_name=city_name,
        street_name=street_name,
        street_number=street_number,
        comment=comment,
        zip_code=zip_code,
        dimensions=ship_dims,
        shipping_cost=float(shipping_dict.get("shipping_cost") or 0.0),
    )

    # Payments
    payments: list[PaymentInfoModel] = []
    raw_pays = primary_ord.get("payments") or raw.get("payments") or []
    for rp in raw_pays:
        pid = str(rp.get("id") or "")
        pm = str(rp.get("payment_method_id") or "")
        pt = str(rp.get("payment_type") or "")
        pst = str(rp.get("status") or "")
        pamt = float(rp.get("transaction_amount") or 0.0)
        pcur = str(rp.get("currency_id") or currency_id)
        papp = format_order_date(rp.get("date_approved") or "")
        mp_fee = float(rp.get("marketplace_fee") or 0.0)
        tx_amt = float(rp.get("taxes_amount") or 0.0)
        payments.append(
            PaymentInfoModel(
                payment_id=pid,
                payment_method=pm,
                payment_type=pt,
                status=pst,
                transaction_amount=pamt,
                currency_id=pcur,
                date_approved=papp,
                marketplace_fee=mp_fee,
                taxes_amount=tx_amt,
            )
        )

    # Calculate financial totals: multiply each item's unit commission by its purchased quantity
    tot_sale_fee = round(sum(it.sale_fee * it.quantity for it in items), 2)
    raw_ship_cost = float(
        primary_ord.get("shipping_cost")
        or raw.get("shipping_cost")
        or shipping_dict.get("shipping_cost")
        or 0.0
    )
    raw_taxes = float((primary_ord.get("taxes") or raw.get("taxes") or {}).get("amount") or (payments[0].taxes_amount if payments else 0.0))

    # Reputation Impact Evaluation
    affects_rep = False
    rep_reason = ""

    if "affects_reputation" in raw:
        affects_rep = bool(raw.get("affects_reputation"))
        rep_reason = str(raw.get("reputation_reason") or "")
    elif "affects_reputation" in primary_ord:
        affects_rep = bool(primary_ord.get("affects_reputation"))
        rep_reason = str(primary_ord.get("reputation_reason") or "")
    else:
        cancel_detail = primary_ord.get("cancel_detail") or raw.get("cancel_detail") or {}
        req_by = str(cancel_detail.get("requested_by") or "").strip().lower()
        cancel_code = str(cancel_detail.get("code") or cancel_detail.get("reason") or "").strip()

        mediations = primary_ord.get("mediations") or raw.get("mediations") or []
        claims = primary_ord.get("claims") or raw.get("claims") or []

        tag_set = {str(t).lower() for t in tags}
        is_fraud = "fraud_risk_detected" in tag_set
        is_split = "splitted_order" in tag_set

        if req_by == "seller" or "seller_cancelled" in tag_set or "cancelled_by_seller" in tag_set:
            affects_rep = True
            rep_reason = "卖家主动取消"
            if cancel_code:
                rep_reason = f"卖家主动取消 ({cancel_code})"
        elif req_by == "buyer" or "buyer_cancelled" in tag_set or "cancelled_by_buyer" in tag_set:
            affects_rep = False
            rep_reason = "买家自行取消"
        elif req_by == "meli":
            affects_rep = False
            rep_reason = "平台系统取消"
        elif is_fraud:
            affects_rep = False
            rep_reason = "平台风控拦截"
        elif is_split:
            affects_rep = False
            rep_reason = "系统拆单"
        else:
            affects_rep = False
            rep_reason = ""

    return OrderModel(
        order_id=oid,
        account_id=account_id,
        store_name=store_name,
        site_id=resolved_site,
        pack_id=pack_id,
        date_created=date_created,
        date_closed=date_closed,
        status=status,
        status_detail=status_detail,
        buyer_id=buyer_id,
        buyer_nickname=buyer_nickname,
        buyer_full_name=buyer_full,
        total_amount=total_amount,
        paid_amount=paid_amount,
        currency_id=currency_id,
        sale_fee=tot_sale_fee,
        shipping_cost=raw_ship_cost,
        taxes_amount=raw_taxes,
        items=items,
        shipping=shipping,
        payments=payments,
        tags=tags,
        created_timestamp=ts,
        affects_reputation=affects_rep,
        reputation_reason=rep_reason,
        raw_data=raw,
    )

