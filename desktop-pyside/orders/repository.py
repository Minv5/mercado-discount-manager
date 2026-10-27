from __future__ import annotations

import csv
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core import get_store_alias
from engine.auth import AuthManager
from engine.client import MercadoClient
from engine.crypto import get_data_dir
from orders.models import (
    OrderItemModel,
    OrderModel,
    format_dimensions_display,
    parse_order_from_dict,
    parse_order_timestamp,
)
from orders.shipping_audits import get_all_shipping_audits
from service_center.translator import translate_text

logger = logging.getLogger(__name__)

ATTR_NAME_DICT: dict[str, str] = {
    "color": "颜色",
    "cor": "颜色",
    "colour": "颜色",
    "talla": "尺码",
    "tamanho": "尺码",
    "size": "尺码",
    "tamaño": "尺寸",
    "tamano": "尺寸",
    "modelo": "型号",
    "model": "型号",
    "material": "材质",
    "longitud": "长度",
    "comprimento": "长度",
    "length": "长度",
    "voltaje": "电压",
    "voltagem": "电压",
    "voltage": "电压",
    "ancho": "宽度",
    "largura": "宽度",
    "width": "宽度",
    "alto": "高度",
    "altura": "高度",
    "height": "高度",
    "capacidad": "容量",
    "capacidade": "容量",
    "capacity": "容量",
    "cantidad": "数量",
    "quantidade": "数量",
    "quantity": "数量",
    "sabor": "口味",
    "flavor": "口味",
    "diseño": "款式",
    "diseno": "款式",
    "desenho": "款式",
    "tipo": "类型",
    "type": "类型",
    "estilo": "风格",
    "style": "风格",
    "peso": "重量",
    "weight": "重量",
}

ATTR_VAL_DICT: dict[str, str] = {
    "negro": "黑色",
    "preto": "黑色",
    "black": "黑色",
    "blanco": "白色",
    "branco": "白色",
    "white": "白色",
    "rojo": "红色",
    "vermelho": "红色",
    "red": "红色",
    "azul": "蓝色",
    "blue": "蓝色",
    "verde": "绿色",
    "green": "绿色",
    "amarillo": "黄色",
    "amarelo": "黄色",
    "yellow": "黄色",
    "naranja": "橙色",
    "laranja": "橙色",
    "orange": "橙色",
    "rosa": "粉色",
    "rosado": "粉色",
    "cor-de-rosa": "粉色",
    "pink": "粉色",
    "dorado": "金色",
    "dourado": "金色",
    "oro": "金色",
    "gold": "金色",
    "plateado": "银色",
    "prata": "银色",
    "plata": "银色",
    "silver": "银色",
    "gris": "灰色",
    "cinza": "灰色",
    "grey": "灰色",
    "gray": "灰色",
    "café": "棕色",
    "cafe": "棕色",
    "marrón": "棕色",
    "marron": "棕色",
    "castanho": "棕色",
    "brown": "棕色",
    "morado": "紫色",
    "púrpura": "紫色",
    "purpura": "紫色",
    "roxo": "紫色",
    "violeta": "紫色",
    "purple": "紫色",
    "beige": "米色",
    "beje": "米色",
    "transparente": "透明",
    "clear": "透明",
    "multicolor": "彩色",
    "colorido": "彩色",
    "azul marino": "深蓝",
    "azul-marinho": "深蓝",
    "navy": "深蓝",
    "azul cielo": "天蓝",
    "azul celeste": "天蓝",
    "azul-claro": "浅蓝",
    "light blue": "浅蓝",
    "verde militar": "军绿",
    "verde oliva": "军绿",
    "army green": "军绿",
    "verde claro": "浅绿",
    "light green": "浅绿",
    "verde oscuro": "深绿",
    "verde-escuro": "深绿",
    "dark green": "深绿",
    "rojo oscuro": "酒红",
    "vinho": "酒红",
    "bordô": "酒红",
    "bordo": "酒红",
    "burgundy": "酒红",
    "gris oscuro": "深灰",
    "cinza escuro": "深灰",
    "chumbo": "深灰",
    "dark gray": "深灰",
    "gris claro": "浅灰",
    "cinza claro": "浅灰",
    "light gray": "浅灰",
    "chico": "小号",
    "pequeno": "小号",
    "small": "小号",
    "mediano": "中号",
    "médio": "中号",
    "medio": "中号",
    "medium": "中号",
    "grande": "大号",
    "large": "大号",
    "extra grande": "加大号",
    "extra chico": "加小号",
    "extra pequeno": "加小号",
    "único": "均码",
    "unico": "均码",
    "talla única": "均码",
    "tamanho único": "均码",
    "one size": "均码",
}

_ATTR_CACHE: dict[str, str] = {}
COMMON_DIRECT_KEEP = {"xs", "s", "m", "l", "xl", "xxl", "xxxl", "p", "g", "gg", "xg", "xgg", "u"}


def translate_variation_attributes(raw_attrs: str) -> str:
    """Translate e-commerce variation attributes (e.g. 'Color: Naranja; Talla: M') to Chinese ('颜色: 橙色; 尺码: M')."""
    clean = str(raw_attrs or "").strip()
    if not clean:
        return ""
    if clean in _ATTR_CACHE:
        return _ATTR_CACHE[clean]

    pairs = [p.strip() for p in clean.split(";") if p.strip()]
    translated_pairs = []
    for pair in pairs:
        if ":" in pair:
            k, v = pair.split(":", 1)
            k_clean = k.strip()
            v_clean = v.strip()
            k_zh = ATTR_NAME_DICT.get(k_clean.lower(), k_clean)
            v_low = v_clean.lower()
            if v_low in COMMON_DIRECT_KEEP or re.match(r"^[\d\.\-\+xX/]+(\s*(cm|mm|m|g|kg|ml|l|v|w|hz|oz|in))?$", v_clean, re.I):
                v_zh = v_clean
            else:
                v_zh = ATTR_VAL_DICT.get(v_low, None)
                if not v_zh:
                    v_norm = v_low.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
                    v_zh = ATTR_VAL_DICT.get(v_norm, None)
                if not v_zh:
                    try:
                        res = translate_text(v_clean, target_lang="zh", timeout=2.0)
                        v_zh = res if res else v_clean
                    except Exception:
                        v_zh = v_clean
            translated_pairs.append(f"{k_zh}: {v_zh}")
        else:
            p_low = pair.lower()
            if p_low in COMMON_DIRECT_KEEP or re.match(r"^[\d\.\-\+xX/]+(\s*(cm|mm|m|g|kg|ml|l|v|w|hz|oz|in))?$", pair, re.I):
                val_zh = pair
            else:
                val_zh = ATTR_VAL_DICT.get(p_low)
                if not val_zh:
                    try:
                        res = translate_text(pair, target_lang="zh", timeout=2.0)
                        val_zh = res if res else pair
                    except Exception:
                        val_zh = pair
            translated_pairs.append(val_zh)

    result = "; ".join(translated_pairs)
    _ATTR_CACHE[clean] = result
    return result


class OrdersRepository:
    """Domain repository for fetching, enriching, caching, and exporting Mercado Libre orders.

    Zero dependency on Qt widgets; purely handles network I/O, domain calculation,
    cache management, and snapshot persistence.
    """

    def __init__(
        self,
        client: MercadoClient,
        auth_mgr: AuthManager,
        cached_orders: list[OrderModel] | None = None,
    ) -> None:
        self.client = client
        self.auth_mgr = auth_mgr
        self._cache_map: dict[str, OrderModel] = {}
        self._item_cache: dict[str, dict[str, Any]] = {}
        self._item_cache_lock = threading.Lock()

        try:
            self._audits_map: dict[str, str] = get_all_shipping_audits()
        except Exception:
            self._audits_map = {}

        if cached_orders:
            for o in cached_orders:
                if not o or not o.order_id:
                    continue
                self._cache_map[str(o.order_id)] = o
                if o.pack_id:
                    self._cache_map[str(o.pack_id)] = o
                for it in o.items:
                    if it.item_id and (it.thumbnail or it.title or it.dimensions):
                        with self._item_cache_lock:
                            existing = self._item_cache.get(it.item_id) or {}
                            self._item_cache[it.item_id] = {
                                "title": it.title or existing.get("title", ""),
                                "thumbnail": it.thumbnail or existing.get("thumbnail", ""),
                                "dimensions": it.dimensions or existing.get("dimensions", ""),
                                "permalink": it.permalink or existing.get("permalink", ""),
                            }

    def enrich_single(
        self,
        entry: tuple[str, dict[str, Any], str],
        is_stopped: Callable[[], bool] | None = None,
    ) -> OrderModel | None:
        """Enrich a single order skeleton with shipment details, dimensions, tracking, and item thumbnails."""
        if is_stopped and is_stopped():
            return None

        aid, raw_item, store_label = entry

        # Determine real order id
        sub_orders = raw_item.get("orders") or []
        real_oid = ""
        if sub_orders and isinstance(sub_orders, list):
            real_oid = str(sub_orders[0].get("id") or "")
        if not real_oid:
            real_oid = str(raw_item.get("id") or "")

        # 1. Fast cache check
        cached_ord = self._cache_map.get(real_oid) or self._cache_map.get(str(raw_item.get("id") or ""))
        if cached_ord:
            cached_status = str(cached_ord.status or "").lower()
            cached_ship_st = str(cached_ord.shipping.status or "").lower()
            raw_status = str(
                (sub_orders[0].get("status") if sub_orders and isinstance(sub_orders, list) else None)
                or raw_item.get("status")
                or ""
            ).lower()
            if "delivered" in (raw_item.get("tags") or []):
                raw_status = "delivered"
            elif not raw_status and cached_status:
                raw_status = cached_status

            is_terminal = cached_status in ("delivered", "cancelled") or cached_ship_st in ("delivered", "cancelled")
            if is_terminal:
                if store_label and cached_ord.store_name != store_label:
                    cached_ord.store_name = store_label
                return cached_ord

        # 2. Enrich order
        full_data = raw_item
        has_items = bool(
            raw_item.get("order_items")
            or (sub_orders and isinstance(sub_orders, list) and sub_orders[0].get("order_items"))
        )
        if real_oid and not has_items:
            try:
                det = self.client.get_marketplace_order(aid, real_oid)
                if det and isinstance(det, dict) and "id" in det:
                    full_data = det
            except Exception:
                pass

        site_id = str(
            (full_data.get("context") or {}).get("site")
            or full_data.get("site_id")
            or ""
        ).strip()
        accurate_store = get_store_alias(self.auth_mgr, aid, site_id) or store_label

        ord_model = parse_order_from_dict(full_data, aid, accurate_store, site_id)
        if not ord_model.pack_id:
            ord_model.pack_id = str(raw_item.get("id") or "")
        if not ord_model.shipping.shipment_id:
            raw_sh_id = str((raw_item.get("shipment") or {}).get("id") or "")
            if raw_sh_id:
                ord_model.shipping.shipment_id = raw_sh_id

        # Enrich items (using item cache first)
        for it in ord_model.items:
            try:
                with self._item_cache_lock:
                    cached_it = self._item_cache.get(it.item_id)
                if cached_it:
                    if not it.title and cached_it.get("title"):
                        it.title = cached_it["title"]
                    if not it.permalink and cached_it.get("permalink"):
                        it.permalink = cached_it["permalink"]
                    if not it.dimensions and cached_it.get("dimensions"):
                        it.dimensions = cached_it["dimensions"]
                    var_dict = cached_it.get("variations") or {}
                    var_info = var_dict.get(str(it.variation_id or ""))
                    if var_info:
                        if not it.thumbnail and var_info.get("thumbnail"):
                            it.thumbnail = var_info["thumbnail"]
                        if not it.variation_attributes and var_info.get("attributes"):
                            it.variation_attributes = var_info["attributes"]
                        if not it.sku and var_info.get("sku"):
                            it.sku = var_info["sku"]
                    elif not it.thumbnail and cached_it.get("thumbnail"):
                        it.thumbnail = cached_it["thumbnail"]

                need_var_pic = bool(it.variation_id and str(it.variation_id).strip() and not it.thumbnail)
                need_var_attrs = bool(it.variation_id and str(it.variation_id).strip() and not it.variation_attributes)
                if not it.thumbnail or not it.title or need_var_pic or need_var_attrs or not it.dimensions or not it.permalink:
                    it_detail = self.client.get_item_detail(aid, it.item_id)
                    if it_detail:
                        if not it.title:
                            it.title = str(it_detail.get("title") or "")
                        if not it.permalink:
                            it.permalink = str(it_detail.get("permalink") or "")

                        pictures = it_detail.get("pictures") or []
                        pic_map = {}
                        if isinstance(pictures, list):
                            for p in pictures:
                                if isinstance(p, dict) and p.get("id"):
                                    pic_map[str(p["id"])] = str(p.get("secure_url") or p.get("url") or "")

                        var_pic_url = ""
                        var_id_str = str(it.variation_id or "").strip()
                        if not var_id_str and it.sku:
                            for v in (it_detail.get("variations") or []):
                                if isinstance(v, dict) and str(v.get("seller_custom_field") or "") == it.sku:
                                    var_id_str = str(v.get("id") or "")
                                    it.variation_id = var_id_str
                                    break

                        if var_id_str:
                            for v in (it_detail.get("variations") or []):
                                if isinstance(v, dict) and str(v.get("id")) == var_id_str:
                                    pic_ids = v.get("picture_ids") or []
                                    if pic_ids and str(pic_ids[0]) in pic_map:
                                        var_pic_url = pic_map[str(pic_ids[0])]
                                    if not it.variation_attributes:
                                        combos = v.get("attribute_combinations") or v.get("attributes") or []
                                        v_attrs = []
                                        for c in combos:
                                            if isinstance(c, dict):
                                                cn = c.get("name") or c.get("id") or ""
                                                cv = c.get("value_name") or ""
                                                if cn and cv:
                                                    v_attrs.append(f"{cn}: {cv}")
                                                elif cv:
                                                    v_attrs.append(str(cv))
                                        if v_attrs:
                                            it.variation_attributes = "; ".join(v_attrs)
                                    if not it.sku and v.get("seller_custom_field"):
                                        it.sku = str(v.get("seller_custom_field"))
                                    break

                        if var_pic_url:
                            it.thumbnail = var_pic_url
                        elif not it.thumbnail:
                            if pictures and isinstance(pictures, list):
                                it.thumbnail = str(pictures[0].get("secure_url") or pictures[0].get("url") or "")
                            if not it.thumbnail:
                                it.thumbnail = str(it_detail.get("thumbnail") or "")

                        if not it.dimensions:
                            attrs = {str(a.get("id") or "").upper(): a for a in (it_detail.get("attributes") or []) if isinstance(a, dict)}
                            h_attr = attrs.get("SELLER_PACKAGE_HEIGHT") or attrs.get("PACKAGE_HEIGHT")
                            w_attr = attrs.get("SELLER_PACKAGE_WIDTH") or attrs.get("PACKAGE_WIDTH")
                            l_attr = attrs.get("SELLER_PACKAGE_LENGTH") or attrs.get("PACKAGE_LENGTH")
                            wt_attr = attrs.get("SELLER_PACKAGE_WEIGHT") or attrs.get("PACKAGE_WEIGHT")
                            if h_attr and w_attr and l_attr:
                                def _get_val(at):
                                    vs = at.get("value_struct")
                                    if isinstance(vs, dict) and vs.get("number") is not None:
                                        return vs.get("number")
                                    vn = at.get("value_name") or ""
                                    m = re.search(r"[\d.]+", str(vn))
                                    return m.group(0) if m else vn
                                h_val = _get_val(h_attr)
                                w_val = _get_val(w_attr)
                                l_val = _get_val(l_attr)
                                wt_val = _get_val(wt_attr) if wt_attr else None
                                it.dimensions = format_dimensions_display({"length": l_val, "width": w_val, "height": h_val, "weight": wt_val})

                        with self._item_cache_lock:
                            existing = self._item_cache.get(it.item_id) or {}
                            var_dict = existing.get("variations") or {}
                            if var_id_str:
                                var_dict[var_id_str] = {
                                    "thumbnail": it.thumbnail,
                                    "attributes": it.variation_attributes,
                                    "sku": it.sku,
                                }
                            self._item_cache[it.item_id] = {
                                "title": it.title,
                                "thumbnail": it.thumbnail,
                                "dimensions": it.dimensions,
                                "permalink": it.permalink,
                                "variations": var_dict,
                            }
            except Exception:
                pass

        # Enrich shipment tracking, dimensions, and actual seller shipping liability
        if ord_model.shipping.shipment_id:
            try:
                cached_ship_st = str(cached_ord.shipping.status or "").lower() if cached_ord else ""
                if cached_ord and cached_ord.shipping.tracking_number and cached_ship_st in ("delivered", "cancelled"):
                    ord_model.shipping.tracking_number = cached_ord.shipping.tracking_number
                    ord_model.shipping.tracking_method = cached_ord.shipping.tracking_method
                    ord_model.shipping.status = cached_ord.shipping.status
                    ord_model.shipping.substatus = cached_ord.shipping.substatus
                    ord_model.shipping.dimensions = cached_ord.shipping.dimensions
                    ord_model.shipping.shipping_cost = cached_ord.shipping.shipping_cost
                    ord_model.shipping_cost = cached_ord.shipping_cost
                    ord_model.shipping.handling_hours = cached_ord.shipping.handling_hours
                    ord_model.shipping.estimated_delivery_date = cached_ord.shipping.estimated_delivery_date
                    ord_model.shipping.estimated_delivery_offset = cached_ord.shipping.estimated_delivery_offset
                    ord_model.shipping.base_shipping_cost = cached_ord.shipping.base_shipping_cost
                    ord_model.shipping.shipping_adjustment = cached_ord.shipping.shipping_adjustment
                    ord_model.shipping.shipping_adjustment_reason = cached_ord.shipping.shipping_adjustment_reason
                    ord_model.shipping.actual_dimensions = cached_ord.shipping.actual_dimensions
                else:
                    ship_det = self.client.get_marketplace_shipment(aid, ord_model.shipping.shipment_id)
                    if ship_det:
                        if ship_det.get("tracking_number"):
                            ord_model.shipping.tracking_number = str(ship_det["tracking_number"])
                        elif cached_ord and cached_ord.shipping.tracking_number:
                            ord_model.shipping.tracking_number = cached_ord.shipping.tracking_number

                        if ship_det.get("tracking_method"):
                            ord_model.shipping.tracking_method = str(ship_det["tracking_method"])
                        elif cached_ord and cached_ord.shipping.tracking_method:
                            ord_model.shipping.tracking_method = cached_ord.shipping.tracking_method

                        if ship_det.get("status"):
                            ord_model.shipping.status = str(ship_det["status"])
                        if ship_det.get("substatus"):
                            ord_model.shipping.substatus = str(ship_det["substatus"])

                        if not ord_model.shipping.estimated_handling_limit:
                            ship_limit = str(
                                (ship_det.get("estimated_handling_limit") or {}).get("date")
                                or (ship_det.get("lead_time") or {}).get("estimated_handling_limit", {}).get("date")
                                or ""
                            )
                            if ship_limit:
                                ord_model.shipping.estimated_handling_limit = ship_limit
                        dims = ship_det.get("dimensions") or ship_det.get("shipping_dimensions")
                        if dims:
                            ord_model.shipping.dimensions = format_dimensions_display(dims)
                        elif cached_ord and cached_ord.shipping.dimensions:
                            ord_model.shipping.dimensions = cached_ord.shipping.dimensions

                        lead_time = ship_det.get("lead_time") or {}
                        est_del = lead_time.get("estimated_delivery_time") or {}
                        handling_val = est_del.get("handling")
                        if handling_val is not None:
                            try:
                                ord_model.shipping.handling_hours = int(handling_val)
                            except Exception:
                                pass
                        est_date = str(est_del.get("date") or "")
                        if est_date:
                            ord_model.shipping.estimated_delivery_date = est_date
                        est_offset = str((est_del.get("offset") or {}).get("date") or "")
                        if est_offset:
                            ord_model.shipping.estimated_delivery_offset = est_offset

                        cost_type = str(lead_time.get("cost_type") or "").lower()
                        buyer_cost = float(lead_time.get("cost") or 0.0)
                        list_cost = float(lead_time.get("list_cost") or buyer_cost or 0.0)
                        seller_ship_cost = list_cost
                        if cost_type == "partially_free":
                            seller_ship_cost = max(round(list_cost - buyer_cost, 2), 0.0)
                        elif cost_type == "free":
                            seller_ship_cost = list_cost
                        elif cost_type == "not_free":
                            seller_ship_cost = 0.0

                        base_ship_cost = seller_ship_cost
                        adj_ship_cost = 0.0
                        adj_reason = ""

                        try:
                            costs_det = self.client.get_marketplace_shipment_costs(aid, ord_model.shipping.shipment_id)
                            if isinstance(costs_det, dict) and "senders" in costs_det:
                                senders = costs_det.get("senders") or []
                                if senders and isinstance(senders[0], dict):
                                    if "cost" in senders[0]:
                                        base_ship_cost = float(senders[0]["cost"] or 0.0)
                                    compensations = senders[0].get("compensations") or []
                                    if compensations:
                                        adj_ship_cost = round(sum(float(c.get("amount") or 0.0) for c in compensations), 2)
                                        comp_reasons = [str(c.get("reason") or c.get("comment") or "") for c in compensations]
                                        if any("dimension" in r.lower() for r in comp_reasons):
                                            adj_reason = "尺寸/重量差异"
                                        elif comp_reasons and comp_reasons[0]:
                                            adj_reason = comp_reasons[0]
                                        else:
                                            adj_reason = "运费调整"
                        except Exception:
                            pass

                        final_ship_cost = round(base_ship_cost + adj_ship_cost, 2)
                        ord_model.shipping.base_shipping_cost = base_ship_cost
                        ord_model.shipping.shipping_adjustment = adj_ship_cost
                        ord_model.shipping.shipping_adjustment_reason = adj_reason
                        ord_model.shipping.shipping_cost = final_ship_cost
                        ord_model.shipping_cost = final_ship_cost

                        # Smart backfill core order attributes from shipment if missing
                        ship_date = str(ship_det.get("date_created") or "").strip()
                        if ship_date and (not ord_model.date_created or ord_model.created_timestamp == 0.0):
                            ord_model.date_created = ship_date
                            ord_model.created_timestamp = parse_order_timestamp(ship_date)

                        ship_site = str((ship_det.get("source") or {}).get("site_id") or "").strip()
                        if not ship_site and ord_model.items:
                            pfx = str(ord_model.items[0].item_id or "")[:3].upper()
                            if pfx in ("MLA", "MLM", "MLB", "MLC", "MCO", "MLU", "MPE"):
                                ship_site = pfx
                        if ship_site and not ord_model.site_id:
                            ord_model.site_id = ship_site
                            if not ord_model.store_name or ord_model.store_name == aid:
                                ord_model.store_name = get_store_alias(self.auth_mgr, aid, ship_site) or ord_model.store_name

                        dest = ship_det.get("destination") or {}
                        rec_name = str(dest.get("receiver_name") or "").strip()
                        if rec_name:
                            ord_model.shipping.receiver_name = rec_name
                            if not ord_model.buyer_nickname or ord_model.buyer_nickname.startswith("买家"):
                                ord_model.buyer_nickname = rec_name
                        dest_addr = dest.get("shipping_address") or {}
                        if dest_addr:
                            if not ord_model.shipping.city_name:
                                ord_model.shipping.city_name = str((dest_addr.get("city") or {}).get("name") or "")
                            if not ord_model.shipping.state_name:
                                ord_model.shipping.state_name = str((dest_addr.get("state") or {}).get("name") or "")
                            if not ord_model.shipping.country_id:
                                ord_model.shipping.country_id = str((dest_addr.get("country") or {}).get("id") or "")
                            if not ord_model.shipping.street_name:
                                ord_model.shipping.street_name = str(dest_addr.get("street_name") or "")
                            if not ord_model.shipping.street_number:
                                ord_model.shipping.street_number = str(dest_addr.get("street_number") or "")
                            if not ord_model.shipping.zip_code:
                                ord_model.shipping.zip_code = str(dest_addr.get("zip_code") or "")

                        dec_val = float(ship_det.get("declared_value") or 0.0)
                        if dec_val > 0.0:
                            if ord_model.total_amount <= 0.0:
                                ord_model.total_amount = dec_val
                            if ord_model.paid_amount <= 0.0:
                                ord_model.paid_amount = dec_val
                            if not ord_model.currency_id:
                                ord_model.currency_id = str(ship_det.get("currency_id") or "USD")

                        ext_ref = str(ship_det.get("external_reference") or "").strip()
                        if ext_ref and not ord_model.pack_id:
                            ord_model.pack_id = ext_ref
            except Exception:
                pass

        if hasattr(self, "_audits_map") and self._audits_map and not ord_model.shipping.actual_dimensions:
            aud = self._audits_map.get(ord_model.order_id) or self._audits_map.get(ord_model.shipping.shipment_id)
            if aud:
                ord_model.shipping.actual_dimensions = aud

        if ord_model.order_id:
            self._cache_map[ord_model.order_id] = ord_model
        return ord_model

    def fetch_orders(
        self,
        target_account: str | None = None,
        limit_per_account: int = 50,
        on_chunk: Callable[[list[OrderModel]], None] | None = None,
        is_stopped: Callable[[], bool] | None = None,
    ) -> list[OrderModel]:
        """Fetch orders across all or targeted accounts, streaming initial skeletons and enriched chunks."""
        orders_by_id: dict[str, OrderModel] = {}
        accounts = self.auth_mgr.list_accounts()
        target_list = accounts
        if target_account and target_account != "ALL":
            target_list = [a for a in accounts if str(a.get("account_id") or a.get("id")) == str(target_account)]

        raw_skeletons: list[tuple[str, dict[str, Any], str]] = []
        immediate_chunk: list[OrderModel] = []

        for acc in target_list:
            if is_stopped and is_stopped():
                break
            aid = str(acc.get("account_id") or acc.get("id") or "").strip()
            if not aid:
                continue
            site_id = str(acc.get("site_id") or "").strip()
            store_label = get_store_alias(self.auth_mgr, aid, site_id)

            # 1. CBT search
            cbt_success = False
            results = []
            try:
                res_cbt = self.client.search_marketplace_orders(aid, limit=limit_per_account)
                results = res_cbt.get("results") or []
                if results:
                    cbt_success = True
            except Exception as cbt_err:
                logger.debug("CBT search error for %s: %s", aid, cbt_err)

            # 2. Local site search fallback
            if not cbt_success:
                try:
                    res_std = self.client.search_orders(aid, limit=limit_per_account)
                    results = res_std.get("results") or []
                except Exception as std_err:
                    logger.debug("Std search error for %s: %s", aid, std_err)

            for raw_item in results:
                raw_skeletons.append((aid, raw_item, store_label))

                # Fast skeleton parse or cache hit for instant presentation
                sub_orders = raw_item.get("orders") or []
                real_oid = str(sub_orders[0].get("id") if sub_orders and isinstance(sub_orders, list) else "") or str(raw_item.get("id") or "")
                cached = self._cache_map.get(real_oid) or self._cache_map.get(str(raw_item.get("id") or ""))
                if cached:
                    orders_by_id[cached.order_id] = cached
                    immediate_chunk.append(cached)
                else:
                    try:
                        skeleton = parse_order_from_dict(raw_item, aid, store_label, site_id)
                        if skeleton.order_id:
                            orders_by_id[skeleton.order_id] = skeleton
                            immediate_chunk.append(skeleton)
                    except Exception:
                        pass

            if immediate_chunk and on_chunk:
                on_chunk(list(immediate_chunk))
                immediate_chunk.clear()

        # Concurrent enrich single orders for those needing updates
        to_enrich: list[tuple[str, dict[str, Any], str]] = []
        for item in raw_skeletons:
            aid, raw_item, store_label = item
            sub_orders = raw_item.get("orders") or []
            real_oid = str(sub_orders[0].get("id") if sub_orders and isinstance(sub_orders, list) else "") or str(raw_item.get("id") or "")
            cached = self._cache_map.get(real_oid) or self._cache_map.get(str(raw_item.get("id") or ""))
            cached_ship_st = str(cached.shipping.status or "").lower() if cached else ""
            if cached and (cached.status in ("delivered", "cancelled") or cached_ship_st in ("delivered", "cancelled")):
                orders_by_id[cached.order_id] = cached
                continue
            to_enrich.append(item)

        if to_enrich:
            enriched_chunk: list[OrderModel] = []
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(self.enrich_single, item, is_stopped) for item in to_enrich]
                for fut in as_completed(futures):
                    if is_stopped and is_stopped():
                        break
                    try:
                        res = fut.result()
                        if res and res.order_id:
                            orders_by_id[res.order_id] = res
                            enriched_chunk.append(res)
                            if len(enriched_chunk) >= 8 and on_chunk:
                                on_chunk(list(enriched_chunk))
                                enriched_chunk.clear()
                    except Exception as e:
                        logger.debug("Enrich order error: %s", e)
            if enriched_chunk and on_chunk:
                on_chunk(list(enriched_chunk))
                enriched_chunk.clear()

        all_orders = list(orders_by_id.values())
        all_orders.sort(key=lambda o: o.created_timestamp, reverse=True)
        return all_orders

    # --- Local Snapshot Storage ---

    @staticmethod
    def get_snapshot_path() -> Path:
        return get_data_dir() / "orders_snapshot.json"

    def save_snapshot(self, orders: list[OrderModel], limit: int = 200) -> bool:
        """Persist top orders into orders_snapshot.json."""
        try:
            path = self.get_snapshot_path()
            data = [o.to_dict() for o in orders[:limit]]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            logger.debug("Failed to save orders snapshot: %s", e)
            return False

    def load_snapshot(self) -> list[OrderModel]:
        """Load and parse orders from orders_snapshot.json."""
        try:
            path = self.get_snapshot_path()
            if not path.exists():
                return []
            content = path.read_text(encoding="utf-8")
            raw_list = json.loads(content)
            orders: list[OrderModel] = []
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                if "order_id" in item:
                    o = OrderModel.from_dict(item)
                    if not o.site_id and o.items:
                        pfx = str(o.items[0].item_id or "")[:3].upper()
                        if pfx in ("MLA", "MLM", "MLB", "MLC", "MCO", "MLU", "MPE"):
                            o.site_id = pfx
                    if o.account_id:
                        alias = get_store_alias(self.auth_mgr, o.account_id, o.site_id)
                        if alias:
                            o.store_name = alias
                    if o.order_id:
                        orders.append(o)
                else:
                    aid = str(item.get("seller", {}).get("id") or "")
                    if not aid:
                        sub = item.get("orders") or []
                        if sub and isinstance(sub, list):
                            aid = str(sub[0].get("seller", {}).get("id") or "")
                    site = str(item.get("context", {}).get("site") or item.get("site_id") or "")
                    store_label = get_store_alias(self.auth_mgr, aid, site)
                    o = parse_order_from_dict(item, aid, store_label, site)
                    if o.order_id:
                        orders.append(o)

            if orders:
                try:
                    audits_map = get_all_shipping_audits()
                    if audits_map:
                        for o in orders:
                            if not o.shipping.actual_dimensions:
                                aud = audits_map.get(o.order_id) or audits_map.get(o.shipping.shipment_id)
                                if aud:
                                    o.shipping.actual_dimensions = aud
                except Exception:
                    pass
                orders.sort(key=lambda x: x.created_timestamp, reverse=True)
            return orders
        except Exception as e:
            logger.debug("Failed to load local orders snapshot: %s", e)
            return []

    # --- CSV Export ---

    @staticmethod
    def export_to_csv(orders: list[OrderModel], file_path: str | Path) -> int:
        """Export given orders to CSV file. Returns number of rows exported."""
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "订单号", "包裹号", "店铺名称", "站点", "下单时间",
                "官方状态", "取消责任归属", "买家昵称", "收件人", "收件电话", "收件地址",
                "商品ID", "商品标题", "SKU", "变体规格", "数量", "单价",
                "订单总额", "实收金额", "币种", "物流方式", "运单号"
            ])
            for o in orders:
                sh = o.shipping
                primary_item = o.primary_item
                rep_str = "卖家责任取消" if o.affects_reputation else "正常"
                writer.writerow([
                    o.order_id,
                    o.pack_id,
                    o.store_name,
                    o.site_id,
                    o.display_time,
                    o.status,
                    rep_str,
                    o.buyer_nickname,
                    sh.receiver_name,
                    sh.receiver_phone,
                    sh.full_address,
                    primary_item.item_id,
                    primary_item.title,
                    primary_item.sku,
                    translate_variation_attributes(primary_item.variation_attributes),
                    o.total_quantity,
                    primary_item.unit_price,
                    o.total_amount,
                    o.paid_amount,
                    o.currency_id,
                    sh.logistic_type,
                    sh.tracking_number,
                ])
        return len(orders)
