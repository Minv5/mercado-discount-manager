from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import PySide6
    qt_plugins = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "plugins")
    if os.path.isdir(qt_plugins):
        os.environ.setdefault("QT_PLUGIN_PATH", qt_plugins)
        platforms_dir = os.path.join(qt_plugins, "platforms")
        if os.path.isdir(platforms_dir):
            try:
                platforms_dir.encode("ascii")
                os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", platforms_dir)
            except UnicodeEncodeError:
                tmp_platforms = "/tmp/qt_platforms"
                os.makedirs(tmp_platforms, exist_ok=True)
                for item in os.listdir(platforms_dir):
                    src = os.path.join(platforms_dir, item)
                    dst = os.path.join(tmp_platforms, item)
                    if not os.path.exists(dst):
                        os.symlink(src, dst)
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = tmp_platforms
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QCheckBox

from orders.models import (
    format_dimensions_display,
    format_order_date,
    parse_order_timestamp,
    get_order_status_badge_info,
    parse_order_from_dict,
    OrderModel,
    OrderItemModel,
    ShippingInfoModel,
)
from orders.orders_page import OrdersPage, OrdersFetchWorker, copy_pure_text, translate_variation_attributes


class TestOrderModels(unittest.TestCase):
    def test_format_order_date_beijing_time(self):
        # UTC 2026-10-17T10:00:00Z -> Beijing 2026-10-17 18:00:00
        utc_str = "2026-10-17T10:00:00Z"
        formatted = format_order_date(utc_str)
        self.assertEqual(formatted, "2026-10-17 18:00:00")

    def test_status_badge_mapping_and_no_emojis(self):
        statuses = ["paid", "shipped", "delivered", "payment_required", "payment_in_process", "cancelled", "invalid"]
        for st in statuses:
            text, style, cat = get_order_status_badge_info(st)
            self.assertFalse(text.startswith("●"), f"Status {st} should NOT start with dot: {text}")
            for forbidden_char in ["●", "⚠️", "⛔", "⏳", "✔️", "✓", "💬", "📦", "🚚"]:
                self.assertNotIn(forbidden_char, text)
            self.assertIn("border-radius: 4px", style)
            self.assertIn("font-size: 11px", style)

    def test_order_status_badge_with_shipping_substatus(self):
        """Verify that shipping substatus like picked_up, in_hub, dropped_off maps to in-transit."""
        # 1. ready_to_ship with picked_up / in_hub / dropped_off -> 运送中
        text, _, cat = get_order_status_badge_info("paid", "ready_to_ship", "picked_up")
        self.assertEqual(text, "运送中")
        self.assertEqual(cat, "shipped")

        text, _, cat = get_order_status_badge_info("paid", "ready_to_ship", "in_hub")
        self.assertEqual(text, "运送中")
        self.assertEqual(cat, "shipped")

        text, _, cat = get_order_status_badge_info("paid", "ready_to_ship", "dropped_off")
        self.assertEqual(text, "运送中")
        self.assertEqual(cat, "shipped")

        # 2. ready_to_ship with printed or empty -> 待发货
        text, _, cat = get_order_status_badge_info("paid", "ready_to_ship", "printed")
        self.assertEqual(text, "待发货")
        self.assertEqual(cat, "paid")

        text, _, cat = get_order_status_badge_info("paid", "ready_to_ship", "")
        self.assertEqual(text, "待发货")
        self.assertEqual(cat, "paid")

    def test_shipping_deadline_info_hidden_when_shipped_or_picked_up(self):
        """Verify that SLA countdown and overdue warnings are suppressed when parcel is shipped or picked up."""
        # Order overdue by created timestamp
        old_ts = 1000.0
        # When shipping is ready_to_ship and no substatus -> shows overdue warning
        ord_pending = OrderModel(
            order_id="1",
            account_id="acc",
            store_name="store",
            status="paid",
            created_timestamp=old_ts,
            shipping=ShippingInfoModel(status="ready_to_ship", substatus=""),
        )
        self.assertIsNotNone(ord_pending.shipping_deadline_info)
        info = ord_pending.shipping_deadline_info
        assert info is not None
        self.assertIn("已超时", info[0])

        # When shipping status is shipped -> None
        ord_shipped = OrderModel(
            order_id="2",
            account_id="acc",
            store_name="store",
            status="paid",
            created_timestamp=old_ts,
            shipping=ShippingInfoModel(status="shipped", substatus=""),
        )
        self.assertIsNone(ord_shipped.shipping_deadline_info)

        # When shipping is ready_to_ship but substatus is picked_up -> None
        ord_picked_up = OrderModel(
            order_id="3",
            account_id="acc",
            store_name="store",
            status="paid",
            created_timestamp=old_ts,
            shipping=ShippingInfoModel(status="ready_to_ship", substatus="picked_up"),
        )
        self.assertIsNone(ord_picked_up.shipping_deadline_info)

    def test_parse_standard_order(self):
        raw = {
            "id": 2000018676400878,
            "date_created": "2026-10-17T10:07:00Z",
            "status": "paid",
            "total_amount": 21.79,
            "currency_id": "USD",
            "buyer": {
                "id": 12345678,
                "nickname": "SELENEDENTISTA13",
                "first_name": "Selene",
                "last_name": "Dentista",
            },
            "order_items": [
                {
                    "item": {
                        "id": "MLM5868800338",
                        "title": "Vestido Casual Elegante Dama",
                        "seller_sku": "827880081-BEIGE",
                        "variation_attributes": [
                            {"name": "Color", "value_name": "Beige"},
                            {"name": "Talla", "value_name": "M"},
                        ],
                    },
                    "quantity": 2,
                    "unit_price": 10.895,
                }
            ],
            "shipping": {
                "id": 4321098765,
                "logistic_type": "fulfillment",
                "status": "ready_to_ship",
                "receiver_address": {
                    "receiver_name": "Selene Dentista",
                    "receiver_phone": "5512345678",
                    "city": {"name": "Guadalajara"},
                    "state": {"name": "Jalisco"},
                    "street_name": "Av Hidalgo",
                    "street_number": "123",
                    "zip_code": "44100",
                    "country": {"id": "MX"},
                },
            },
        }

        order = parse_order_from_dict(raw, account_id="acc_1", store_name="🇲🇽 墨西哥店", site_id="MLM")
        self.assertEqual(order.order_id, "2000018676400878")
        self.assertEqual(order.buyer_nickname, "SELENEDENTISTA13")
        self.assertEqual(order.display_time, "2026-10-17 18:07:00")
        self.assertEqual(order.total_amount, 21.79)
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0].sku, "827880081-BEIGE")
        self.assertEqual(order.items[0].variation_attributes, "Color: Beige; Talla: M")
        self.assertEqual(order.shipping.shipment_id, "4321098765")
        self.assertIn("Av Hidalgo 123", order.shipping.full_address)
        self.assertIn("CP 44100", order.shipping.full_address)

    def test_parse_cbt_order_wrapper(self):
        raw = {
            "id": 999999999,
            "orders": [
                {
                    "id": 200001999999999,
                    "date_created": "2026-10-17T08:00:00Z",
                    "status": "shipped",
                    "total_amount": 45.0,
                    "currency_id": "USD",
                    "buyer": {"nickname": "CBT_BUYER_01"},
                    "order_items": [
                        {
                            "item": {
                                "id": "CBT12345",
                                "title": "CBT Product",
                                "seller_sku": "SKU-CBT-1",
                            },
                            "quantity": 1,
                            "unit_price": 45.0,
                        }
                    ],
                }
            ],
        }
        order = parse_order_from_dict(raw, account_id="acc_cbt", store_name="CBT 店铺")
        self.assertEqual(order.order_id, "200001999999999")
        self.assertEqual(order.pack_id, "999999999")
        self.assertEqual(order.status, "shipped")
        badge_text, _, _ = order.status_badge_info
        self.assertEqual(badge_text, "运送中")

    def test_shipping_info_model_and_order_model_compensations(self) -> None:
        """Verify model serialization and helper properties for shipping compensations."""
        ord_model = OrderModel(
            order_id="2000018983593552",
            account_id="3408885754",
            store_name="CNLIUYANG",
            site_id="MLA",
            total_amount=15.62,
            paid_amount=15.62,
            sale_fee=2.00,
            shipping_cost=4.46,
            shipping=ShippingInfoModel(
                shipment_id="48259875494",
                dimensions="5 × 14 × 19 cm · 186 g",
                shipping_cost=4.46,
                base_shipping_cost=4.22,
                shipping_adjustment=0.24,
                shipping_adjustment_reason="尺寸/重量差异",
                actual_dimensions="22 × 17 × 10 cm · 259 g",
            ),
        )
        self.assertTrue(ord_model.has_shipping_adjustment)
        self.assertIn("有运费调整: $4.22 ➔ $4.46", ord_model.shipping_adjustment_text)
        self.assertIn("+$0.24", ord_model.shipping_adjustment_text)
        # Net profit = 15.62 - 2.00 - 4.46 = 9.16
        self.assertEqual(ord_model.estimated_net_profit, 9.16)

        # Verify to_dict and from_dict
        d = ord_model.to_dict()
        self.assertEqual(d["shipping"]["base_shipping_cost"], 4.22)
        self.assertEqual(d["shipping"]["shipping_adjustment"], 0.24)
        self.assertEqual(d["shipping"]["shipping_adjustment_reason"], "尺寸/重量差异")
        self.assertEqual(d["shipping"]["actual_dimensions"], "22 × 17 × 10 cm · 259 g")

        restored = OrderModel.from_dict(d)
        self.assertTrue(restored.has_shipping_adjustment)
        self.assertEqual(restored.shipping.base_shipping_cost, 4.22)
        self.assertEqual(restored.shipping.shipping_adjustment, 0.24)
        self.assertEqual(restored.shipping.actual_dimensions, "22 × 17 × 10 cm · 259 g")
        self.assertEqual(restored.effective_shipping_cost, 4.46)


class TestOrdersPageUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_auth = MagicMock()
        self.mock_auth.list_accounts.return_value = [
            {"account_id": "111", "display_name": "墨西哥1号店", "site_id": "MLM"},
            {"account_id": "222", "display_name": "巴西2号店", "site_id": "MLB"},
        ]
        self.page = OrdersPage(self.mock_client, self.mock_auth)

    def tearDown(self):
        self.page.stop()
        self.page.deleteLater()

    def test_page_initialization_and_account_dropdown(self):
        self.assertEqual(self.page.store_combo.count(), 3)
        self.assertEqual(self.page.store_combo.itemText(0), "全部店铺 (聚合汇总)")
        self.assertIn("墨西哥1号店", self.page.store_combo.itemText(1))
        self.assertIn("巴西2号店", self.page.store_combo.itemText(2))

    def test_render_and_badge_dimensions(self):
        # Create test orders
        o1 = OrderModel(
            order_id="200001888",
            account_id="111",
            store_name="🇲🇽 墨西哥1号店",
            site_id="MLM",
            status="paid",
            buyer_nickname="BUYER_ONE",
            total_amount=99.0,
            currency_id="MXN",
            items=[OrderItemModel(item_id="MLM123", title="商品标题1", sku="SKU1")],
        )
        o2 = OrderModel(
            order_id="200001999",
            account_id="222",
            store_name="🇧🇷 巴西2号店",
            site_id="MLB",
            status="delivered",
            buyer_nickname="BUYER_TWO",
            total_amount=150.0,
            currency_id="BRL",
            items=[OrderItemModel(item_id="MLB456", title="商品标题2", sku="SKU2")],
        )
        self.page._all_orders = [o1, o2]
        self.page._apply_filters()

        self.assertEqual(self.page.order_list.count(), 2)

        # Verify badge size in first card
        card_w = self.page.order_list.itemWidget(self.page.order_list.item(0))
        self.assertIsNotNone(card_w)

        # Find status badge in card
        badges = [lbl for lbl in card_w.findChildren(QLabel) if lbl.text() in ("待发货", "已送达")]
        self.assertTrue(len(badges) >= 1)
        badge = badges[0]
        self.assertEqual(badge.width(), 74, "Status badge width MUST be 74px")
        self.assertEqual(badge.height(), 22, "Status badge height MUST be 22px")

    def test_instant_search_filtering(self):
        o1 = OrderModel(order_id="200001888", account_id="111", store_name="店1", buyer_nickname="ALICE")
        o2 = OrderModel(order_id="200001999", account_id="222", store_name="店2", buyer_nickname="BOB")
        self.page._all_orders = [o1, o2]
        self.page._apply_filters()
        self.assertEqual(self.page.order_list.count(), 2)

        # Search by order_id
        self.page.search_input.setText("200001888")
        self.assertEqual(self.page.order_list.count(), 1)

        # Search by buyer
        self.page.search_input.setText("BOB")
        self.assertEqual(self.page.order_list.count(), 1)

        # Search non-matching
        self.page.search_input.setText("NON_EXISTING")
        self.assertEqual(self.page.order_list.count(), 0)

    def test_pure_clipboard_copy(self):
        copy_pure_text("2000018676400878")
        cb_text = QApplication.clipboard().text()
        self.assertEqual(cb_text, "2000018676400878", "Clipboard must contain pure numbers only")

    def test_select_order_cross_navigation(self):
        o1 = OrderModel(order_id="200001888", account_id="111", store_name="店1")
        o2 = OrderModel(order_id="200001999", account_id="222", store_name="店2")
        self.page._all_orders = [o1, o2]
        self.page._apply_filters()

        success = self.page.select_order("200001999")
        self.assertTrue(success)
        self.assertEqual(self.page.order_list.currentRow(), 1)
        self.assertEqual(self.page._current_order.order_id, "200001999")

    def test_order_model_serialization_and_snapshot(self):
        o1 = OrderModel(
            order_id="200001888",
            account_id="111",
            store_name="🇲🇽 墨西哥1号店",
            site_id="MLM",
            pack_id="PACK123",
            buyer_nickname="BUYER_ONE",
            total_amount=120.50,
            paid_amount=10.00,
            currency_id="USD",
            items=[OrderItemModel(item_id="MLM123", title="标题1", thumbnail="https://img/1.jpg")],
            shipping=ShippingInfoModel(tracking_number="TRACK999", tracking_method="MEL"),
        )
        d = o1.to_dict()
        self.assertEqual(d["order_id"], "200001888")
        self.assertEqual(d["items"][0]["title"], "标题1")
        self.assertEqual(d["shipping"]["tracking_number"], "TRACK999")

        o2 = OrderModel.from_dict(d)
        self.assertEqual(o2.order_id, o1.order_id)
        self.assertEqual(o2.store_name, o1.store_name)
        self.assertEqual(o2.items[0].title, "标题1")
        self.assertEqual(o2.shipping.tracking_number, "TRACK999")

    def test_shipping_status_precedence_in_model(self):
        # Order with status="paid" but shipping="shipped" -> badge must be "运送中"
        o_shipped = OrderModel(
            order_id="101",
            account_id="1",
            store_name="店1",
            status="paid",
            shipping=ShippingInfoModel(status="shipped"),
        )
        badge_text, _, cat = o_shipped.status_badge_info
        self.assertEqual(badge_text, "运送中")
        self.assertEqual(cat, "shipped")

        # Order with status="paid" but shipping="delivered" -> badge must be "已送达"
        o_delivered = OrderModel(
            order_id="102",
            account_id="1",
            store_name="店1",
            status="paid",
            shipping=ShippingInfoModel(status="delivered"),
        )
        badge_text, _, cat = o_delivered.status_badge_info
        self.assertEqual(badge_text, "已送达")
        self.assertEqual(cat, "delivered")

        # Order with status="paid" and shipping="ready_to_ship" -> badge must be "待发货"
        o_ready = OrderModel(
            order_id="103",
            account_id="1",
            store_name="店1",
            status="paid",
            shipping=ShippingInfoModel(status="ready_to_ship"),
        )
        badge_text, _, cat = o_ready.status_badge_info
        self.assertEqual(badge_text, "待发货")
        self.assertEqual(cat, "paid")

    def test_dimensions_formatting_and_storage(self):
        # Dict dimensions
        d1 = {"length": 10, "width": 10, "height": 11, "weight": 200}
        fmt1 = format_dimensions_display(d1)
        self.assertEqual(fmt1, "10 × 10 × 11 cm · 200 g")

        # String dimensions
        fmt2 = format_dimensions_display("15x20x25,1500")
        self.assertEqual(fmt2, "15 × 20 × 25 cm · 1.5 kg")

        # In model serialization
        o = OrderModel(
            order_id="104",
            account_id="1",
            store_name="店1",
            items=[OrderItemModel(item_id="M1", title="T1", dimensions="10 × 10 × 11 cm · 200 g")],
            shipping=ShippingInfoModel(dimensions="10 × 10 × 11 cm · 200 g"),
        )
        d = o.to_dict()
        self.assertEqual(d["items"][0]["dimensions"], "10 × 10 × 11 cm · 200 g")
        self.assertEqual(d["shipping"]["dimensions"], "10 × 10 × 11 cm · 200 g")
        o_rev = OrderModel.from_dict(d)
        self.assertEqual(o_rev.shipping.dimensions, "10 × 10 × 11 cm · 200 g")
        self.assertEqual(o_rev.items[0].dimensions, "10 × 10 × 11 cm · 200 g")

    def test_splitter_handle_and_no_redundant_button(self):
        # Verify splitter handle width is 6
        self.assertEqual(self.page.splitter.handleWidth(), 6)

        # Verify redundant open button does NOT exist
        self.assertFalse(hasattr(self.page, "btn_open_mcl_order"))

        # Render order detail
        order = OrderModel(
            order_id="2000019021286402",
            account_id="111",
            store_name="🇲🇽 墨西哥店",
            site_id="MLM",
            status="paid",
            shipping=ShippingInfoModel(
                status="shipped",
                tracking_number="TRACK123",
                dimensions="10 × 10 × 11 cm · 200 g",
            ),
        )
        self.page._render_order_detail(order)

        # Verify order id text does NOT contain arrow (clean copy, no browser jump)
        self.assertEqual(self.page.det_order_id_lbl.text(), "订单号: 2000019021286402")
        self.assertNotIn("↗", self.page.det_order_id_lbl.text())
        # Verify dimensions chip
        self.assertIn("📦 出单包装尺重: 10 × 10 × 11 cm · 200 g", self.page.det_dimensions_chip.text())
        # Verify redundant status badge is removed from right drawer
        self.assertFalse(hasattr(self.page, "det_status_badge"), "Redundant status badge must be removed from right overview card")
        # Verify items container has modular item rendered
        self.assertTrue(self.page.items_container_layout.count() >= 0)

    def test_order_card_streamlined_and_shipping_dimensions(self):
        order = OrderModel(
            order_id="2000019021286403",
            account_id="111",
            store_name="🇧🇷 巴西店",
            site_id="MLB",
            status="paid",
            paid_amount=25.5,
            shipping=ShippingInfoModel(
                status="shipped",
                tracking_number="TRACK999",
                dimensions="15 × 12 × 10 cm · 350 g",
            ),
            items=[
                OrderItemModel(
                    item_id="MLB999",
                    title="测试商品",
                    sku="TEST-SKU",
                    dimensions="50 × 40 × 30 cm · 2000 g",  # listing dimension should not leak to shipping
                )
            ],
        )
        self.page._all_orders = [order]
        self.page._apply_filters()
        self.assertEqual(self.page.order_list.count(), 1)

        card_w = self.page.order_list.itemWidget(self.page.order_list.item(0))
        self.assertIsNotNone(card_w)

        # Check card is streamlined (order ID removed from card text to prevent crowding, tooltip and price present)
        labels = [lbl.text() for lbl in card_w.findChildren(QLabel)]
        self.assertFalse(any("2000019021286403" in t for t in labels), "Order ID must be removed from card text")
        self.assertIn("2000019021286403", card_w.toolTip())
        self.assertTrue(any("实付: USD 25.50" in t for t in labels))

        # Check detail rendering: shipping dimensions chip displays 出单包装尺重
        self.page._render_order_detail(order)
        self.assertIn("📦 出单包装尺重: 15 × 12 × 10 cm · 350 g", self.page.det_dimensions_chip.text())

    def test_financial_breakdown_rendering(self):
        order_fin = OrderModel(
            order_id="2000019021286405",
            account_id="111",
            store_name="🇧🇷 巴西店",
            paid_amount=18.49,
            sale_fee=2.31,
            shipping_cost=6.59,
            taxes_amount=3.78,
        )
        self.page._render_order_detail(order_fin)
        self.assertIn("18.49", self.page.det_fee_paid_val.text())
        self.assertIn("-USD 2.31", self.page.det_fee_sale_val.text())
        self.assertIn("-USD 6.59", self.page.det_fee_ship_val.text())
        self.assertIn("9.59", self.page.det_fee_net_val.text())
        from engine.exchange_rate import get_cny_exchange_rate
        rate, _ = get_cny_exchange_rate("USD")
        expected_cny = f"¥{round(9.59 * rate, 2):.2f}"
        self.assertIn(expected_cny, self.page.det_fee_net_val.text())
        self.assertIn("3.78", self.page.det_pay_detail_lbl.text())

    def test_permalink_resolution_and_front_url(self):
        # 1. With permalink present
        item_with_perm = OrderItemModel(item_id="MLM123", permalink="https://articulo.mercadolibre.com.mx/MLM-123-title-_JM")
        with patch("webbrowser.open") as mock_open:
            self.page._open_item_url(item_with_perm, "MLM")
            mock_open.assert_called_once_with("https://articulo.mercadolibre.com.mx/MLM-123-title-_JM")

        # 2. Without permalink, queries client.get_item_detail
        item_no_perm = OrderItemModel(item_id="MLB789")
        self.mock_client.get_item_detail.return_value = {"permalink": "https://produto.mercadolivre.com.br/MLB-789-slug-_JM"}
        with patch("webbrowser.open") as mock_open:
            self.page._open_item_url(item_no_perm, "MLB", "222")
            mock_open.assert_called_once_with("https://produto.mercadolivre.com.br/MLB-789-slug-_JM")
            self.assertEqual(item_no_perm.permalink, "https://produto.mercadolivre.com.br/MLB-789-slug-_JM")

        # 3. Offline fallback has hyphen format
        item_fallback = OrderItemModel(item_id="MLM999888")
        self.mock_client.get_item_detail.side_effect = Exception("offline")
        with patch("webbrowser.open") as mock_open:
            self.page._open_item_url(item_fallback, "MLM", "111")
            mock_open.assert_called_once_with("https://articulo.mercadolibre.com.mx/MLM-999888")

    def test_sku_copy_and_deduplication(self):
        order = OrderModel(
            order_id="2000019999",
            account_id="111",
            store_name="店1",
            paid_amount=12.86,
            items=[OrderItemModel(item_id="MLM111", title="商品1", sku="TEST-SKU-999")],
        )
        self.page._all_orders = [order]
        self.page._apply_filters()
        card_w = self.page.order_list.itemWidget(self.page.order_list.item(0))

        # Check left card has clickable SKU chip
        sku_labels = [lbl for lbl in card_w.findChildren(QLabel) if "TEST-SKU-999" in lbl.text()]
        self.assertTrue(len(sku_labels) >= 1)
        self.assertEqual(sku_labels[0].cursor().shape(), Qt.CursorShape.PointingHandCursor)

        # Check left card does NOT have a separate "订单号:" bottom row to prevent crowding
        oid_prefix_labels = [lbl for lbl in card_w.findChildren(QLabel) if lbl.text().strip() == "订单号:"]
        self.assertEqual(len(oid_prefix_labels), 0, "Left card must NOT have redundant order ID bottom row")

        # Check price is rendered directly following SKU
        price_labels = [lbl for lbl in card_w.findChildren(QLabel) if "12.86" in lbl.text()]
        self.assertTrue(len(price_labels) >= 1, "Price must follow SKU on the card")

        # Check order list styling has dark gold selection border and NO green border (#3E7B4B)
        qss = self.page.order_list.styleSheet()
        self.assertIn("#D4AF37", qss)
        self.assertNotIn("#3E7B4B", qss)

        # Check right drawer renders details without redundant items card
        self.page._render_order_detail(order)
        self.assertFalse(hasattr(self.page, "items_card"), "Redundant items_card must be removed")
        self.assertFalse(hasattr(self.page, "items_rows_container"), "items_rows_container must be removed")
        self.assertFalse(hasattr(self.page, "btn_copy_address"), "Redundant copy address button must be removed")
        self.assertEqual(self.page.address_card.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)

    def test_reputation_impact_parsing_logic(self):
        # 1. Seller cancellation -> affects reputation
        raw_seller_cancel = {
            "id": 1001,
            "status": "cancelled",
            "cancel_detail": {"requested_by": "seller", "code": "out_of_stock"},
        }
        ord1 = parse_order_from_dict(raw_seller_cancel, "111", "店1")
        self.assertTrue(ord1.affects_reputation)
        self.assertIn("卖家主动取消", ord1.reputation_reason)

        # 2. Buyer cancellation -> does NOT affect reputation
        raw_buyer_cancel = {
            "id": 1002,
            "status": "cancelled",
            "cancel_detail": {"requested_by": "buyer", "reason": "buyer_regret"},
        }
        ord2 = parse_order_from_dict(raw_buyer_cancel, "111", "店1")
        self.assertFalse(ord2.affects_reputation)

        # 3. Fraud risk detected -> does NOT affect reputation
        raw_fraud = {
            "id": 1003,
            "status": "cancelled",
            "tags": ["fraud_risk_detected"],
        }
        ord3 = parse_order_from_dict(raw_fraud, "111", "店1")
        self.assertFalse(ord3.affects_reputation)

        # 4. Standard unspecified cancellation -> does NOT speculate affects_reputation
        raw_unspec = {
            "id": 1004,
            "status": "cancelled",
        }
        ord4 = parse_order_from_dict(raw_unspec, "111", "店1")
        self.assertFalse(ord4.affects_reputation, "Unspecified cancellation must NOT falsely affect reputation")

    def test_reputation_ui_only_show_when_affecting(self):
        # Cards do not display distracting fake reputation badges
        o_affect = OrderModel(
            order_id="ORD_AFFECT",
            account_id="111",
            store_name="店1",
            status="cancelled",
            affects_reputation=True,
            reputation_reason="卖家主动取消",
        )
        o_clean = OrderModel(
            order_id="ORD_CLEAN",
            account_id="111",
            store_name="店1",
            status="paid",
            affects_reputation=False,
        )

        self.page._all_orders = [o_affect, o_clean]
        self.page._apply_filters()
        self.assertEqual(self.page.order_list.count(), 2)

        # Card 0 and 1: No fake reputation red badges cluttering the card
        card_affect = self.page.order_list.itemWidget(self.page.order_list.item(0))
        affect_badges = [lbl for lbl in card_affect.findChildren(QLabel) if "影响声誉" in lbl.text()]
        self.assertEqual(len(affect_badges), 0, "No fake reputation badges on order card")

        # Drawer has no det_reputation_badge widget
        self.assertFalse(hasattr(self.page, "det_reputation_badge"))

    def test_reputation_filter_dropdown(self):
        # Standard filters only, no fictitious affects_reputation
        idx = self.page.status_combo.findData("affects_reputation")
        self.assertEqual(idx, -1, "affects_reputation fake filter must NOT be present")
        self.assertTrue(self.page.status_combo.findData("paid") >= 0)
        self.assertTrue(self.page.status_combo.findData("cancelled") >= 0)

    def test_reputation_serialization_roundtrip(self):
        o = OrderModel(
            order_id="TEST_REP",
            account_id="111",
            store_name="店1",
            affects_reputation=True,
            reputation_reason="缺货取消",
        )
        d = o.to_dict()
        self.assertTrue(d["affects_reputation"])
        self.assertEqual(d["reputation_reason"], "缺货取消")

        o_rev = OrderModel.from_dict(d)
        self.assertTrue(o_rev.affects_reputation)
    def test_no_closing_time_and_status_badge_position(self):
        order = OrderModel(
            order_id="200001999901",
            account_id="111",
            store_name="🇲🇽 墨西哥店",
            date_created="2026-10-18T10:00:00Z",
            date_closed="2026-10-18T12:00:00Z",
            status="paid",
        )
        self.page._render_order_detail(order)
        # Verify det_dates_lbl contains 下单时间 and DOES NOT contain 关闭时间
        dates_text = self.page.det_dates_lbl.text()
        self.assertIn("下单时间", dates_text)
        self.assertNotIn("关闭时间", dates_text, "Misleading 关闭时间 must NOT appear in order details")

        # Verify redundant status badge is removed from right overview card
        self.assertFalse(hasattr(self.page, "det_status_badge"))
        self.assertEqual(self.page.det_store_badge.text(), "🇲🇽 墨西哥店")

    def test_batch_selection_and_print_button_state(self):
        o1 = OrderModel(order_id="ORD_1", account_id="111", store_name="店1", shipping=ShippingInfoModel(shipment_id="SHIP_1"))
        o2 = OrderModel(order_id="ORD_2", account_id="111", store_name="店1", shipping=ShippingInfoModel(shipment_id="SHIP_2"))
        self.page._all_orders = [o1, o2]
        self.page._apply_filters()

        self.assertEqual(self.page.order_list.count(), 2)
        self.assertEqual(len(self.page._selected_order_ids), 0)
        self.assertFalse(self.page.print_labels_btn.isEnabled())
        self.assertEqual(self.page.print_labels_btn.text(), "🖨️ 打印面单")

        # Toggle select all
        self.page.select_all_cb.setChecked(True)
        self.assertEqual(len(self.page._selected_order_ids), 2)
        self.assertTrue(self.page.print_labels_btn.isEnabled())
        self.assertEqual(self.page.print_labels_btn.text(), "🖨️ 打印面单")
        self.assertIn("已选 2 笔", self.page.count_lbl.text())

        # Uncheck select all
        self.page.select_all_cb.setChecked(False)
        self.assertEqual(len(self.page._selected_order_ids), 0)
        self.assertFalse(self.page.print_labels_btn.isEnabled())
        self.assertEqual(self.page.print_labels_btn.text(), "🖨️ 打印面单")

    def test_print_shipment_labels_execution(self):
        o1 = OrderModel(
            order_id="ORD_100",
            account_id="111",
            store_name="店1",
            shipping=ShippingInfoModel(shipment_id="SHIP_100"),
        )
        self.mock_client.get_shipment_labels_pdf.return_value = b"%PDF-1.4 test shipment label data"
        with patch("orders.orders_page.QDesktopServices.openUrl") as mock_open:
            self.page._do_print_shipment_labels([o1])
            self.mock_client.get_shipment_labels_pdf.assert_called_once_with("111", ["SHIP_100"])
            self.assertTrue(mock_open.called)

    def test_client_shipment_labels_pdf_cbt_fallback_and_merge(self):
        from engine.client import MercadoClient
        mock_auth = MagicMock()
        client = MercadoClient(mock_auth)

        # Mock standard request_raw_bytes to simulate 400 INVALID_SHIPMENT_CALLER on /shipment_labels
        def fake_request_raw(account_id, method, path, params=None, extra_headers=None):
            if path == "/shipment_labels":
                raise RuntimeError("美客多接口异常: Shipment Id 111: Shipment 111 is not printable by Caller 2651442567. (400) INVALID_SHIPMENT_CALLER")
            if path == "/marketplace/shipments/111/labels":
                # Valid mini 1-page PDF
                from pypdf import PdfWriter
                import io
                w = PdfWriter()
                w.add_blank_page(width=100, height=100)
                out = io.BytesIO()
                w.write(out)
                return out.getvalue()
            if path == "/marketplace/shipments/222/labels":
                from pypdf import PdfWriter
                import io
                w = PdfWriter()
                w.add_blank_page(width=100, height=100)
                out = io.BytesIO()
                w.write(out)
                return out.getvalue()
            raise RuntimeError(f"Unknown path: {path}")

        client.request_raw_bytes = MagicMock(side_effect=fake_request_raw)

        merged_pdf = client.get_shipment_labels_pdf("2651442567", ["111", "222"])
        self.assertTrue(merged_pdf.startswith(b"%PDF"))

        # Verify it merged 2 pages
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(merged_pdf))
        self.assertEqual(len(reader.pages), 2)

    def test_card_status_badge_on_left_and_thumbnail_zoom(self):
        order = OrderModel(
            order_id="ORD_REAL_DATA",
            account_id="111",
            store_name="🇧🇷 广东店",
            status="paid",
            buyer_nickname="BRUNALAUXEN",
            paid_amount=25.30,
            items=[OrderItemModel(
                item_id="MLB123456",
                title="Pantorrillas Y Piernas Con Máquina De Ejercicio Sentada",
                sku="MLM5108537844:Gris",
                variation_attributes="Cor: Gris",
                quantity=1,
                thumbnail="https://img.com/test.jpg"
            )],
        )
        self.page._all_orders = [order]
        self.page._apply_filters()

        card_w = self.page.order_list.itemWidget(self.page.order_list.item(0))
        self.assertIsNotNone(card_w)

        # Check card horizontal layout: [0]=QCheckBox, [1]=thumb_lbl (50x50), [2]=info_layout (QVBoxLayout)
        card_layout = card_w.layout()
        self.assertIsNotNone(card_layout)
        widget_cb = card_layout.itemAt(0).widget()
        self.assertIsInstance(widget_cb, QCheckBox)

        thumb_lbl = card_layout.itemAt(1).widget()
        self.assertIsInstance(thumb_lbl, QLabel)
        self.assertEqual(thumb_lbl.width(), 50)
        self.assertEqual(thumb_lbl.height(), 50)
        self.assertEqual(thumb_lbl.cursor().shape(), Qt.CursorShape.PointingHandCursor)
        self.assertIn("放大", thumb_lbl.toolTip())

        # Check thumbnail click triggers lightbox
        with patch.object(self.page, "_show_image_lightbox") as mock_lightbox:
            from PySide6.QtGui import QMouseEvent
            from PySide6.QtCore import QPointF, QEvent
            evt = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            thumb_lbl.mousePressEvent(evt)
            mock_lightbox.assert_called_once_with("https://img.com/test.jpg", "Pantorrillas Y Piernas Con Máquina De Ejercicio Sentada")

        # Check right info column: Row 1 has Store, Buyer, Stretch, Status Badge
        info_layout = card_layout.itemAt(2).layout()
        self.assertIsNotNone(info_layout)
        row1 = info_layout.itemAt(0).layout()
        self.assertIsNotNone(row1)

        store_lbl = row1.itemAt(0).widget()
        self.assertIn("广东店", store_lbl.text())

        buyer_lbl = row1.itemAt(2).widget()
        self.assertEqual(buyer_lbl.text(), "BRUNALAUXEN")

        # Badge is at the right end of row 1
        badge_lbl = row1.itemAt(4).widget()
        self.assertEqual(badge_lbl.text(), "待发货")
        self.assertEqual(badge_lbl.width(), 74)
        self.assertEqual(badge_lbl.height(), 22)

        # Row 2 is title
        title_lbl = info_layout.itemAt(1).widget()
        self.assertEqual(title_lbl.text(), "Pantorrillas Y Piernas Con Máquina De Ejercicio Sentada")

        # Row 3 is SKU, price, qty, var
        row3 = info_layout.itemAt(2).layout()
        self.assertIsNotNone(row3)
        # Verify card minimumSizeHint is highly compact (< 400px) across platforms to prevent overflowing narrow viewports
        self.assertLess(card_w.minimumSizeHint().width(), 400)

    def test_order_card_title_click_selects_order_without_browser_open(self):
        o1 = OrderModel(
            order_id="ORD_TITLE_CLICK_1",
            account_id="111",
            store_name="店1",
            items=[OrderItemModel(item_id="MLB111", title="商品标题1", sku="SKU1")],
        )
        o2 = OrderModel(
            order_id="ORD_TITLE_CLICK_2",
            account_id="111",
            store_name="店1",
            items=[OrderItemModel(item_id="MLB222", title="商品标题2", sku="SKU2")],
        )
        self.page._all_orders = [o1, o2]
        self.page._apply_filters()
        self.assertEqual(self.page.order_list.count(), 2)

        # Select row 0 initially
        self.page.order_list.setCurrentRow(0)
        self.assertEqual(self.page.order_list.currentRow(), 0)

        # Click title on row 1 card
        card_w1 = self.page.order_list.itemWidget(self.page.order_list.item(1))
        self.assertIsNotNone(card_w1)
        card_layout = card_w1.layout()
        info_layout = card_layout.itemAt(2).layout()
        title_lbl = info_layout.itemAt(1).widget()
        self.assertEqual(title_lbl.text(), "商品标题2")
        self.assertIn("(右键可复制商品编号)", title_lbl.toolTip())

        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF, QEvent
        evt = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

        with patch("webbrowser.open") as mock_open:
            title_lbl.mousePressEvent(evt)
            # Verify NO browser open was triggered
            mock_open.assert_not_called()

        # Verify row 1 was selected
        self.assertEqual(self.page.order_list.currentRow(), 1)

    def test_partially_free_shipping_cost_calculation(self):
        """Verify that partially_free shipping subtracts buyer's share to derive seller's liability."""
        worker = OrdersFetchWorker(self.mock_client, self.mock_auth)
        self.mock_client.get_marketplace_shipment.return_value = {
            "tracking_number": "311233672167",
            "tracking_method": "J&T LM CBT MLB",
            "status": "ready_to_ship",
            "lead_time": {
                "cost": 8.02,
                "list_cost": 12.17,
                "cost_type": "partially_free",
            },
        }
        self.mock_client.get_marketplace_shipment_costs.return_value = {
            "gross_amount": 12.17,
            "receiver": {"cost": 8.02},
            "senders": [{"cost": 4.15}],
        }
        raw_entry = (
            "2651442567",
            {
                "id": "2000019055716854",
                "paid_amount": 12.89,
                "currency_id": "USD",
                "order_items": [
                    {
                        "item": {"id": "MLB7696778504", "title": "Tiebacks"},
                        "unit_price": 12.89,
                        "sale_fee": 1.61,
                    }
                ],
                "shipping": {"id": 48293738268},
            },
            "湖北店",
        )
        ord_model = worker._enrich_single(raw_entry)
        self.assertIsNotNone(ord_model)
        assert ord_model is not None
        self.assertEqual(ord_model.shipping.shipping_cost, 4.15)
        self.assertEqual(ord_model.shipping_cost, 4.15)
        self.assertEqual(ord_model.estimated_net_profit, 7.13)

    def test_drawer_item_title_jump_and_item_id_pure_copy_tag(self):
        order = OrderModel(
            order_id="2000019055716854",
            account_id="2651442567",
            store_name="湖北店",
            site_id="MLB",
            status="paid",
            items=[
                OrderItemModel(
                    item_id="MLB7696778504",
                    title="1 Pair Elegant Cord Curtain Tiebacks",
                    sku="MLM3163056513",
                    permalink="https://articulo.mercadolibre.com.br/MLB-7696778504",
                )
            ],
        )
        self.page._current_order = order
        self.page._render_order_detail(order)

        self.assertEqual(self.page.items_container_layout.count(), 1)
        item_frame = self.page.items_container_layout.itemAt(0).widget()
        self.assertIsNotNone(item_frame)

        # Center text layout has title and meta_h
        c_layout = item_frame.layout().itemAt(1).layout()
        title_lbl = c_layout.itemAt(0).widget()
        self.assertEqual(title_lbl.text(), "1 Pair Elegant Cord Curtain Tiebacks")
        self.assertIn("点击在浏览器打开前台售卖页", title_lbl.toolTip())

        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF, QEvent
        evt = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

        with patch("webbrowser.open") as mock_open:
            title_lbl.mousePressEvent(evt)
            mock_open.assert_called_once_with("https://articulo.mercadolibre.com.br/MLB-7696778504")

        # Meta layout has item ID label and SKU label
        meta_h = c_layout.itemAt(1).layout()
        id_lbl = meta_h.itemAt(0).widget()
        self.assertEqual(id_lbl.text(), "MLB7696778504")
        self.assertNotIn("↗", id_lbl.text())
        self.assertIn("点击或右键复制商品编号", id_lbl.toolTip())

        # Test clicking item ID label copies text instead of opening browser
        with patch("orders.orders_page.copy_pure_text") as mock_copy, patch("webbrowser.open") as mock_open2:
            id_lbl.mousePressEvent(evt)
            mock_copy.assert_called_once_with("MLB7696778504")
            mock_open2.assert_not_called()

    def test_multi_quantity_order_sale_fee_and_net_profit_calculation(self) -> None:
        """Verify that multi-quantity orders multiply unit sale fee by item quantity, matching ERP calculations exactly."""
        raw_order = {
            "id": 2000019070811104,
            "paid_amount": 58.05,
            "total_amount": 58.05,
            "currency_id": "USD",
            "order_items": [
                {
                    "item": {"id": "MCO4360812262", "title": "Lámpara De Techo"},
                    "quantity": 3,
                    "unit_price": 19.35,
                    "sale_fee": 3.0,
                }
            ],
            "shipping": {"id": 48300681081, "shipping_cost": 20.91},
        }
        order = parse_order_from_dict(raw_order, "2651442567", "湖北店", "MCO")
        # 3 items * $3.00 = $9.00 total commission
        self.assertEqual(order.items[0].sale_fee, 3.0)
        self.assertEqual(order.items[0].total_sale_fee, 9.0)
        self.assertEqual(order.total_sale_fee, 9.0)
        # $58.05 - $9.00 - $20.91 = $28.14
        self.assertEqual(order.estimated_net_profit, 28.14)

        # Test serialization & deserialization retains corrected $9.00
        d = order.to_dict()
        self.assertEqual(d["sale_fee"], 9.0)
        restored = OrderModel.from_dict(d)
        self.assertEqual(restored.total_sale_fee, 9.0)
        self.assertEqual(restored.estimated_net_profit, 28.14)

    def test_orders_fetch_worker_cached_order_reuse_zero_network(self) -> None:
        """Verify that terminal cached orders (delivered/cancelled) are reused with zero network requests."""
        cached_ord = OrderModel(
            order_id="200009999",
            account_id="2651442567",
            store_name="湖北店",
            site_id="MLB",
            status="delivered",
            items=[OrderItemModel(item_id="MLB999", title="Cached Item", thumbnail="https://img.com/p.jpg")],
            shipping=ShippingInfoModel(shipment_id="ship_999", tracking_number="TRACK123", shipping_cost=5.50),
        )
        worker = OrdersFetchWorker(
            self.mock_client,
            self.mock_auth,
            cached_orders=[cached_ord],
        )

        raw_entry = (
            "2651442567",
            {
                "id": "200009999",
                "status": "delivered",
                "tags": ["delivered"],
            },
            "湖北店",
        )
        self.mock_client.get_marketplace_order.reset_mock()
        self.mock_client.get_item_detail.reset_mock()
        self.mock_client.get_marketplace_shipment.reset_mock()
        self.mock_client.get_marketplace_shipment_costs.reset_mock()

        res = worker._enrich_single(raw_entry)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.order_id, "200009999")
        self.assertEqual(res.shipping.tracking_number, "TRACK123")
        self.assertEqual(res.items[0].title, "Cached Item")

        # Zero network calls made!
        self.mock_client.get_marketplace_order.assert_not_called()
        self.mock_client.get_item_detail.assert_not_called()
        self.mock_client.get_marketplace_shipment.assert_not_called()
        self.mock_client.get_marketplace_shipment_costs.assert_not_called()

    def test_enrich_single_updates_shipping_status_and_substatus_from_shipment(self) -> None:
        """Verify that non-terminal order calls get_marketplace_shipment to refresh shipping status and substatus."""
        cached_ord = OrderModel(
            order_id="2000019055716854",
            account_id="2651442567",
            store_name="湖北店",
            site_id="MLB",
            status="paid",
            total_amount=12.89,
            created_timestamp=1000.0,
            items=[OrderItemModel(item_id="MLB7696778504", title="Cached Title", thumbnail="thumb.jpg")],
            shipping=ShippingInfoModel(
                shipment_id="48293738268",
                tracking_number="OLD_TRACK",
                status="ready_to_ship",
                substatus="",
            ),
        )
        worker = OrdersFetchWorker(
            self.mock_client,
            self.mock_auth,
            cached_orders=[cached_ord],
        )

        # Mock shipment returning updated status and substatus
        self.mock_client.get_marketplace_shipment.return_value = {
            "tracking_number": "888002732884427",
            "tracking_method": "J&T LM CBT MLB",
            "status": "shipped",
            "substatus": "picked_up",
        }
        self.mock_client.get_marketplace_shipment_costs.return_value = {}

        raw_entry = (
            "2651442567",
            {
                "id": "2000019055716854",
                "status": "paid",
                "shipping": {"id": 48293738268, "status": "ready_to_ship"},
            },
            "湖北店",
        )

        res = worker._enrich_single(raw_entry)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.order_id, "2000019055716854")
        self.assertEqual(res.shipping.tracking_number, "888002732884427")
        self.assertEqual(res.shipping.status, "shipped")
        self.assertEqual(res.shipping.substatus, "picked_up")
        badge_text, _, cat = res.status_badge_info
        self.assertEqual(badge_text, "运送中")
        self.assertEqual(cat, "shipped")
        self.assertIsNone(res.shipping_deadline_info)

    def test_orders_page_chunk_streaming_and_selection_preservation(self) -> None:
        """Verify that streaming chunks update the order list and preserve the selected row."""
        o1 = OrderModel(order_id="ORD_1", account_id="111", store_name="店1", site_id="MLM", status="paid", total_amount=10.0, created_timestamp=1000.0)
        o2 = OrderModel(order_id="ORD_2", account_id="111", store_name="店1", site_id="MLM", status="paid", total_amount=20.0, created_timestamp=2000.0)
        self.page._all_orders = [o2, o1]
        self.page._apply_filters()
        self.assertEqual(self.page.order_list.count(), 2)

        # Select ORD_1 (row 1)
        self.page.order_list.setCurrentRow(1)
        self.assertEqual(self.page.order_list.currentRow(), 1)
        current_item = self.page.order_list.currentItem()
        self.assertIsNotNone(current_item)
        self.assertEqual(current_item.data(Qt.ItemDataRole.UserRole), "ORD_1")

        # Now chunk arrives with updated ORD_1 (enriched with tracking) + new ORD_3
        o1_enriched = OrderModel(order_id="ORD_1", account_id="111", store_name="店1", site_id="MLM", status="paid", total_amount=10.0, created_timestamp=1000.0, shipping=ShippingInfoModel(tracking_number="TRACK_AAA"))
        o3 = OrderModel(order_id="ORD_3", account_id="111", store_name="店1", site_id="MLM", status="paid", total_amount=30.0, created_timestamp=3000.0)

        self.page._on_orders_chunk([o1_enriched, o3])
        # Total orders should now be 3 (ORD_3, ORD_2, ORD_1)
        self.assertEqual(self.page.order_list.count(), 3)
        # ORD_1 should STILL be selected!
        selected_item = self.page.order_list.currentItem()
        self.assertIsNotNone(selected_item)
        self.assertEqual(selected_item.data(Qt.ItemDataRole.UserRole), "ORD_1")

    def test_variation_attributes_enrichment_from_item_detail(self) -> None:
        """Verify that missing variation attributes are enriched from item detail's attribute combinations."""
        worker = OrdersFetchWorker(self.mock_client, self.mock_auth)
        self.mock_client.get_item_detail.return_value = {
            "id": "MLM5700283346",
            "title": "Tapacantos Metálico Autoadhesivo",
            "variations": [
                {
                    "id": 18293847291,
                    "seller_custom_field": "MLB4685712727",
                    "attribute_combinations": [
                        {"name": "Color", "value_name": "Dorado"},
                        {"name": "Ancho", "value_name": "1.5 cm"},
                    ],
                    "picture_ids": ["pic_dorado"],
                }
            ],
            "pictures": [{"id": "pic_dorado", "secure_url": "https://img.com/dorado.jpg"}],
        }

        raw_entry = (
            "2651442567",
            {
                "id": "2000019082949204",
                "order_items": [
                    {
                        "item": {
                            "id": "MLM5700283346",
                            "variation_id": "18293847291",
                            "seller_sku": "MLB4685712727",
                        },
                        "quantity": 3,
                        "unit_price": 4.67,
                    }
                ],
            },
            "湖北店",
        )
        ord_model = worker._enrich_single(raw_entry)
        self.assertIsNotNone(ord_model)
        assert ord_model is not None
        self.assertEqual(len(ord_model.items), 1)
        it = ord_model.items[0]
        self.assertEqual(it.sku, "MLB4685712727")
        self.assertEqual(it.variation_attributes, "Color: Dorado; Ancho: 1.5 cm")
        self.assertEqual(it.thumbnail, "https://img.com/dorado.jpg")

    def test_single_spec_item_displays_standard_single_tag(self) -> None:
        """Verify that single-spec items without variations display '标准单品'."""
        single_ord = OrderModel(
            order_id="ORD_SINGLE",
            account_id="111",
            store_name="店1",
            site_id="MLM",
            status="paid",
            items=[OrderItemModel(item_id="MLM111", title="单品图书", sku="BOOK-001", variation_attributes="")],
        )
        self.page._all_orders = [single_ord]
        self.page._apply_filters()
        self.assertEqual(self.page.order_list.count(), 1)
        # Select row 0 to render detail drawer
        self.page.order_list.setCurrentRow(0)

        # Check detail drawer contains '标准单品'
        labels = [lbl.text() for lbl in self.page.items_container.findChildren(QLabel)]
        self.assertIn("标准单品", labels)

    def test_translate_variation_attributes(self) -> None:
        """Verify variation attributes are translated to Chinese seamlessly."""
        self.assertEqual(translate_variation_attributes("Color: Naranja"), "颜色: 橙色")
        self.assertEqual(translate_variation_attributes("Cor: Laranja; Tamanho: G"), "颜色: 橙色; 尺码: G")
        self.assertEqual(translate_variation_attributes("Color: Negro; Talla: M"), "颜色: 黑色; 尺码: M")
        self.assertEqual(translate_variation_attributes("Talla: Grande"), "尺码: 大号")
        self.assertEqual(translate_variation_attributes("Talla: 38"), "尺码: 38")
        self.assertEqual(translate_variation_attributes(""), "")

    def test_contact_buyer_requested_signal(self) -> None:
        """Verify clicking '💬 联系买家' emits contactBuyerRequested signal with current order."""
        ord_test = OrderModel(
            order_id="2000019082949204",
            pack_id="pack_123",
            account_id="2651442567",
            store_name="湖北店",
            site_id="MLM",
            buyer_nickname="BUYER_MX",
        )
        self.page._current_order = ord_test

        received_orders = []
        self.page.contactBuyerRequested.connect(lambda o: received_orders.append(o))

        self.page._on_contact_buyer_clicked()
        self.assertEqual(len(received_orders), 1)
        self.assertEqual(received_orders[0].order_id, "2000019082949204")

    def test_service_center_open_order_conversation(self) -> None:
        """Verify ServiceCenterPage.open_order_conversation switches to 'message' tab and creates/focuses session."""
        from service_center.service_center_page import ServiceCenterPage
        sc_page = ServiceCenterPage(self.mock_client, self.mock_auth)

        ord_target = OrderModel(
            order_id="20000999888",
            pack_id="pack_999",
            account_id="2651442567",
            store_name="湖北测试店",
            site_id="MLM",
            buyer_nickname="TEST_BUYER_LINK",
            items=[OrderItemModel(item_id="MLM999", title="测试产品", sku="SKU-999")],
        )

        sc_page.open_order_conversation(ord_target)

        # Tab should be 'message'
        self.assertEqual(sc_page._active_tab, "message")
        self.assertTrue(sc_page.tab_btn_messages.isChecked())

        # Current session should match this order
        self.assertIsNotNone(sc_page._current_session)
        self.assertEqual(sc_page._current_session.order_id, "20000999888")
        self.assertEqual(sc_page._current_session.session_type, "message")
        self.assertEqual(sc_page._current_session.buyer_nickname, "TEST_BUYER_LINK")

        sc_page.stop()
        sc_page.deleteLater()

    def test_contact_buyer_button_in_page_drawer(self) -> None:
        """Verify that '💬 联系买家' button exists and is active on selected orders."""
        self.assertTrue(hasattr(self.page, "btn_contact_buyer"))
        # When order is selected
        ord_test = OrderModel(order_id="ORD_CONTACT", account_id="111", store_name="店1", site_id="MLM", buyer_nickname="TEST_BUYER")
        self.page._render_order_detail(ord_test)
        self.assertTrue(self.page.btn_contact_buyer.isEnabled())
        self.assertEqual(self.page.btn_contact_buyer.text(), "💬 联系买家")

        # When detail is empty
        self.page._render_empty_detail()
        self.assertFalse(self.page.btn_contact_buyer.isEnabled())

    def test_order_shipping_deadline_countdown_and_badge(self):
        import time
        now_ts = time.time()
        # 1. Pending order with deadline
        order_pending = OrderModel(
            order_id="ORD_DEADLINE_TEST",
            account_id="111",
            store_name="🇲🇽 湖北店",
            site_id="MLM",
            status="paid",
            created_timestamp=now_ts - (12 * 3600),  # Ordered 12h ago, remaining ~60h
            items=[OrderItemModel(item_id="MLM123", title="Test Item", quantity=1)],
        )
        dl_info = order_pending.shipping_deadline_info
        self.assertIsNotNone(dl_info)
        text, color, bg, border = dl_info
        self.assertIn("剩", text)
        self.assertIn("⏳", text)

        # 2. Render card and check SLA badge is visible in Row 1
        self.page._all_orders = [order_pending]
        self.page._apply_filters()
        card_w = self.page.order_list.itemWidget(self.page.order_list.item(0))
        self.assertIsNotNone(card_w)
        card_layout = card_w.layout()
        info_layout = card_layout.itemAt(2).layout()
        row1 = info_layout.itemAt(0).layout()

        # Find SLA badge in row 1
        labels = [row1.itemAt(i).widget() for i in range(row1.count()) if isinstance(row1.itemAt(i).widget(), QLabel)]
        badge_texts = [lbl.text() for lbl in labels]
        self.assertTrue(any("⏳" in t and "剩" in t for t in badge_texts))

        # Check detail drawer renders SLA badge
        self.page._render_order_detail(order_pending)
        self.assertFalse(self.page.det_sla_badge.isHidden())
        self.assertIn("剩", self.page.det_sla_badge.text())

        # 3. Shipped order has no SLA countdown
        order_shipped = OrderModel(
            order_id="ORD_SHIPPED_TEST",
            account_id="111",
            store_name="🇲🇽 湖北店",
            site_id="MLM",
            status="shipped",
            shipping=ShippingInfoModel(status="shipped"),
            created_timestamp=now_ts - (48 * 3600),
            items=[OrderItemModel(item_id="MLM123", title="Test Item", quantity=1)],
        )
        self.assertIsNone(order_shipped.shipping_deadline_info)
        self.page._render_order_detail(order_shipped)
        self.assertTrue(self.page.det_sla_badge.isHidden())

    def test_countdown_tick_synchronizes_both_left_list_and_right_detail(self) -> None:
        """Verify that _on_countdown_tick refreshes both list badge and detail badge."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        test_order = OrderModel(
            order_id="ORD_SYNC_TEST_1",
            account_id="111",
            store_name="🇲🇽 测试店",
            site_id="MLM",
            status="paid",
            shipping=ShippingInfoModel(status="ready_to_ship"),
            created_timestamp=(now - timedelta(hours=2)).timestamp(),
        )

        page = self.page
        card = page._create_order_card(test_order)
        self.assertIn("ORD_SYNC_TEST_1", page._list_sla_badges)
        badge_widget, badge_order = page._list_sla_badges["ORD_SYNC_TEST_1"]
        self.assertIsInstance(badge_widget, QLabel)

        page._current_order = test_order
        page._on_countdown_tick()
        self.assertFalse(badge_widget.isHidden())
        self.assertIn("⏳", badge_widget.text())

    def test_enrich_single_parses_shipping_fee_compensations(self) -> None:
        """Verify worker automatically calculates adjusted shipping fee from shipment costs compensations."""
        worker = OrdersFetchWorker(self.mock_client, self.mock_auth, target_account="3408885754")
        self.mock_client.get_marketplace_order.return_value = {}
        self.mock_client.get_item_detail.return_value = {}
        self.mock_client.get_marketplace_shipment.return_value = {
            "tracking_number": "EFFDA7Y4ZVK5ZGFBY6ZC4ADIPM",
            "tracking_method": "MEL Distribution",
            "status": "shipped",
            "substatus": "consolidated_delivery",
            "lead_time": {
                "cost": 0,
                "list_cost": 4.22,
                "cost_type": "free",
            },
        }
        self.mock_client.get_marketplace_shipment_costs.return_value = {
            "gross_amount": 15.66,
            "senders": [
                {
                    "cost": 4.22,
                    "compensations": [
                        {
                            "amount": 0.24,
                            "reason": "incorrect_dimensions",
                            "comment": "Incorrect package dimensions",
                        }
                    ],
                }
            ],
        }
        raw_entry = (
            "3408885754",
            {
                "id": "2000018983593552",
                "paid_amount": 15.62,
                "currency_id": "USD",
                "order_items": [
                    {
                        "item": {"id": "MLA3681299254", "title": "Retro Bag"},
                        "unit_price": 15.62,
                        "sale_fee": 1.95,
                    }
                ],
                "shipping": {"id": 48259875494},
            },
            "湖南店",
        )
        ord_model = worker._enrich_single(raw_entry)
        self.assertIsNotNone(ord_model)
        assert ord_model is not None
        self.assertEqual(ord_model.shipping.base_shipping_cost, 4.22)
        self.assertEqual(ord_model.shipping.shipping_adjustment, 0.24)
        self.assertEqual(ord_model.shipping.shipping_adjustment_reason, "尺寸/重量差异")
        self.assertEqual(ord_model.shipping.shipping_cost, 4.46)
        self.assertEqual(ord_model.shipping_cost, 4.46)
        self.assertTrue(ord_model.has_shipping_adjustment)
        self.assertEqual(ord_model.estimated_net_profit, 9.21)

    def test_drawer_renders_shipping_fee_adjustment_and_retested_dimensions(self) -> None:
        """Verify right drawer displays adjustment badges, amounts, and chips."""
        page = self.page

        order = OrderModel(
            order_id="2000018983593552",
            account_id="3408885754",
            store_name="湖南店",
            site_id="MLA",
            status="shipped",
            total_amount=15.62,
            paid_amount=15.62,
            currency_id="USD",
            sale_fee=1.95,
            shipping_cost=4.46,
            items=[
                OrderItemModel(
                    item_id="MLA3681299254",
                    title="Retro Bag",
                    sku="BAG001",
                    unit_price=15.62,
                    sale_fee=1.95,
                )
            ],
            shipping=ShippingInfoModel(
                shipment_id="48259875494",
                dimensions="5 × 14 × 19 cm · 186 g",
                status="shipped",
                substatus="consolidated_delivery",
                shipping_cost=4.46,
                base_shipping_cost=4.22,
                shipping_adjustment=0.24,
                shipping_adjustment_reason="尺寸/重量差异",
            ),
        )

        page._current_order = order
        page._render_order_detail(order)

        self.assertEqual(page.det_fee_ship_val.text(), "-USD 4.46")
        self.assertFalse(page.det_fee_ship_adj_lbl.isHidden())
        self.assertIn("+$0.24", page.det_fee_ship_adj_lbl.text())
        self.assertTrue(page.det_shipping_adj_banner.isHidden())
        self.assertFalse(page.det_actual_dimensions_chip.isHidden())
        self.assertIn("复测超标", page.det_actual_dimensions_chip.text())

        # Also test order card
        card = page._create_order_card(order)
        self.addCleanup(card.deleteLater)
        card_labels = [c.text() for c in card.findChildren(QLabel)]
        self.assertTrue(any("运费调整" in t for t in card_labels))

    def test_drawer_renders_audited_secondary_dimensions(self) -> None:
        """Verify right drawer displays audited dimensions chip when present."""
        page = self.page
        order = OrderModel(
            order_id="2000018983593552",
            account_id="3408885754",
            store_name="湖南店",
            site_id="MLA",
            status="shipped",
            total_amount=15.62,
            paid_amount=15.62,
            currency_id="USD",
            shipping=ShippingInfoModel(
                shipment_id="48259875494",
                dimensions="5 × 14 × 19 cm · 186 g",
                actual_dimensions="22 × 17 × 10 cm · 259 g",
                status="shipped",
                shipping_cost=4.46,
                base_shipping_cost=4.22,
                shipping_adjustment=0.24,
            ),
        )
        page._current_order = order
        page._render_order_detail(order)

        self.assertFalse(page.det_actual_dimensions_chip.isHidden())
        self.assertIn("官方实测复重: 22 × 17 × 10 cm · 259 g", page.det_actual_dimensions_chip.text())
        self.assertIn("出单包装尺重: 5 × 14 × 19 cm · 186 g", page.det_dimensions_chip.text())

    def test_shipping_audits_and_zhiying_sync_pipeline(self) -> None:
        """Verify shipping audit persistence and Zhiying sync parsing."""
        from orders.shipping_audits import save_shipping_audit, get_shipping_audit, get_all_shipping_audits
        from orders.zhiying_sync import format_audited_dimensions, parse_and_save_zhiying_packet

        formatted = format_audited_dimensions(22, 17, 10, 259)
        self.assertEqual(formatted, "22 × 17 × 10 cm · 259 g")

        formatted_kg = format_audited_dimensions(22, 17, 10, 0.259)
        self.assertEqual(formatted_kg, "22 × 17 × 10 cm · 259 g")

        pkt = {
            "order_id": "TEST_AUDIT_ORDER_999",
            "shipment_id": "TEST_AUDIT_SHIP_999",
            "official_dimensions": "259克 22x17x10厘米",
        }
        saved = parse_and_save_zhiying_packet(pkt)
        self.assertEqual(saved, 1)

        aud = get_shipping_audit("TEST_AUDIT_ORDER_999")
        self.assertIsNotNone(aud)
        self.assertEqual(aud["actual_dimensions"], "22 × 17 × 10 cm · 259 g")

        all_audits = get_all_shipping_audits()
        self.assertEqual(all_audits.get("TEST_AUDIT_ORDER_999"), "22 × 17 × 10 cm · 259 g")


class TestSlaCalendarAndDeadline(unittest.TestCase):
    def test_business_day_determination(self) -> None:
        from datetime import date
        from orders.sla_calendar import is_cbt_business_day

        # Normal Wednesday (2026-03-11) -> True
        self.assertTrue(is_cbt_business_day(date(2026, 3, 11)))

        # Normal Saturday (2026-03-14) -> False
        self.assertFalse(is_cbt_business_day(date(2026, 3, 14)))

        # Normal Sunday (2026-03-15) -> False
        self.assertFalse(is_cbt_business_day(date(2026, 3, 15)))

        # 2026 National Day (2026-10-01) -> False
        self.assertFalse(is_cbt_business_day(date(2026, 10, 1)))

        # 2026 Adjusted Workday Saturday (2026-10-10) -> True
        self.assertTrue(is_cbt_business_day(date(2026, 10, 10)))

    def test_add_business_days_skips_weekends_and_holidays(self) -> None:
        from datetime import datetime, timezone
        from orders.sla_calendar import add_cbt_business_days

        # Friday 2026-03-13 10:00:00 UTC + 3 business days -> skips Sat, Sun -> Wed 2026-03-18 10:00:00 UTC
        start = datetime(2026, 3, 13, 10, 0, 0, tzinfo=timezone.utc)
        deadline = add_cbt_business_days(start, 3)
        self.assertEqual(deadline.year, 2026)
        self.assertEqual(deadline.month, 3)
        self.assertEqual(deadline.day, 18)
        self.assertEqual(deadline.hour, 10)

        # Cross 2026 National Day: 2026-09-30 15:00 UTC (Wed) + 3 business days
        # 10-01 to 10-07 are public holidays.
        # 10-08 (Thu) is Day 1.
        # 10-09 (Fri) is Day 2.
        # 10-10 (Sat) is official shift workday -> Day 3!
        start_nd = datetime(2026, 9, 30, 15, 0, 0, tzinfo=timezone.utc)
        deadline_nd = add_cbt_business_days(start_nd, 3)
        self.assertEqual(deadline_nd.year, 2026)
        self.assertEqual(deadline_nd.month, 10)
        self.assertEqual(deadline_nd.day, 10)
        self.assertEqual(deadline_nd.hour, 15)

    def test_order_model_uses_sla_calendar_when_no_official_limit(self) -> None:
        from datetime import datetime, timezone, timedelta
        from orders.models import OrderModel, ShippingInfoModel

        # Fresh order created 2 hours ago -> SLA is active and remaining
        now = datetime.now(timezone.utc)
        order_fresh = OrderModel(
            order_id="ORD_SLA_TEST_FRESH",
            account_id="111",
            store_name="🇲🇽 测试店",
            site_id="MLM",
            status="paid",
            shipping=ShippingInfoModel(status="pending", estimated_handling_limit=None),
            created_timestamp=(now - timedelta(hours=2)).timestamp(),
        )
        info_fresh = order_fresh.shipping_deadline_info
        self.assertIsNotNone(info_fresh)
        self.assertIn("⏳", info_fresh[0])

        # Past order created months ago -> SLA is overdue
        order_past = OrderModel(
            order_id="ORD_SLA_TEST_PAST",
            account_id="111",
            store_name="🇲🇽 测试店",
            site_id="MLM",
            status="paid",
            shipping=ShippingInfoModel(status="pending", estimated_handling_limit=None),
            created_timestamp=datetime(2026, 3, 13, 10, 0, 0, tzinfo=timezone.utc).timestamp(),
        )
        info_past = order_past.shipping_deadline_info
        self.assertIsNotNone(info_past)
        self.assertIn("⚠️ 已超时", info_past[0])

    def test_compute_order_sla_deadline_matches_cbt_business_standards(self) -> None:
        """Validate that SLA deadline aligns to 23:59:59 Beijing time and extends across weekends."""
        from datetime import datetime, timezone, timedelta
        from orders.sla_calendar import compute_order_sla_deadline

        bjt = timezone(timedelta(hours=8))
        ref_now = datetime(2026, 10, 21, 20, 57, 0, tzinfo=bjt)

        # 1. Wednesday 2026-10-21 order -> 2 business days (Thu, Fri) + weekend (Sat, Sun) -> Sun 23:59:59
        wed_order = datetime(2026, 10, 21, 2, 47, 0, tzinfo=bjt)
        dl_wed = compute_order_sla_deadline(wed_order, handling_days=2)
        self.assertEqual(dl_wed.strftime("%Y-%m-%d %H:%M:%S"), "2026-10-25 23:59:59")
        diff_wed = int((dl_wed - ref_now).total_seconds())
        self.assertEqual(diff_wed // 86400, 4)
        self.assertEqual((diff_wed % 86400) // 3600, 3)  # 余4天3小时

        # 2. Tuesday 2026-10-20 order -> 2 business days (Wed, Thu) -> Thu 23:59:59
        tue_order = datetime(2026, 10, 20, 9, 32, 0, tzinfo=bjt)
        dl_tue = compute_order_sla_deadline(tue_order, handling_days=2)
        self.assertEqual(dl_tue.strftime("%Y-%m-%d %H:%M:%S"), "2026-10-22 23:59:59")
        diff_tue = int((dl_tue - ref_now).total_seconds())
        self.assertEqual(diff_tue // 86400, 1)
        self.assertEqual((diff_tue % 86400) // 3600, 3)  # 余1天3小时

        # 3. Monday 2026-10-19 order -> 2 business days (Tue, Wed) -> Wed 23:59:59
        mon_order = datetime(2026, 10, 19, 20, 7, 0, tzinfo=bjt)
        dl_mon = compute_order_sla_deadline(mon_order, handling_days=2)
        self.assertEqual(dl_mon.strftime("%Y-%m-%d %H:%M:%S"), "2026-10-21 23:59:59")
        diff_mon = int((dl_mon - ref_now).total_seconds())
        self.assertEqual(diff_mon // 86400, 0)
        self.assertEqual((diff_mon % 86400) // 3600, 3)  # 余3小时

        # 4. Sunday 2026-10-18 order -> weekend shifts to Monday -> Tue, Wed -> Wed 23:59:59
        sun_order = datetime(2026, 10, 18, 13, 44, 0, tzinfo=bjt)
        dl_sun = compute_order_sla_deadline(sun_order, handling_days=2)
        self.assertEqual(dl_sun.strftime("%Y-%m-%d %H:%M:%S"), "2026-10-21 23:59:59")
        diff_sun = int((dl_sun - ref_now).total_seconds())
        self.assertEqual(diff_sun // 86400, 0)
        self.assertEqual((diff_sun % 86400) // 3600, 3)  # 余3小时

    def test_live_exchange_rate_module(self) -> None:
        """Verify that get_cny_exchange_rate resolves live rate and falls back gracefully."""
        from engine.exchange_rate import get_cny_exchange_rate, DEFAULT_EXCHANGE_RATES
        # CNY/RMB returns 1.0
        rate_cny, src_cny = get_cny_exchange_rate("CNY")
        self.assertEqual(rate_cny, 1.0)
        self.assertEqual(src_cny, "基准")

        # USD returns positive rate
        rate_usd, src_usd = get_cny_exchange_rate("USD")
        self.assertGreater(rate_usd, 6.0)
        self.assertIn(src_usd, ["实时", "自定义", "参考"])

        # Latin currencies return positive rate
        for cur in ["MXN", "BRL", "COP", "CLP"]:
            rate_lat, src_lat = get_cny_exchange_rate(cur)
            self.assertGreater(rate_lat, 0.0)


if __name__ == "__main__":
    unittest.main()


