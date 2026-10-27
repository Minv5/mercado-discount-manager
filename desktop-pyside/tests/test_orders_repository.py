from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.auth import AuthManager
from engine.client import MercadoClient
from orders.models import OrderItemModel, OrderModel, ShippingInfoModel
from orders.repository import OrdersRepository, translate_variation_attributes


class TestOrdersRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_client = MagicMock(spec=MercadoClient)
        self.mock_auth = MagicMock(spec=AuthManager)
        self.mock_auth.list_accounts.return_value = [{"account_id": "acc_1", "site_id": "MLB"}]
        self.repo = OrdersRepository(self.mock_client, self.mock_auth)

    def test_translate_variation_attributes(self) -> None:
        self.assertEqual(translate_variation_attributes(""), "")
        self.assertEqual(translate_variation_attributes("Color: Negro; Talla: M"), "颜色: 黑色; 尺码: M")
        self.assertEqual(translate_variation_attributes("cor: azul; tamanho: grande"), "颜色: 蓝色; 尺码: 大号")
        self.assertEqual(translate_variation_attributes("Tamano: Unico"), "尺寸: 均码")

    def test_terminal_order_cached_fast_return(self) -> None:
        cached = OrderModel(
            order_id="ORD_100",
            account_id="acc_1",
            store_name="旧店铺",
            status="delivered",
            shipping=ShippingInfoModel(status="delivered"),
        )
        repo = OrdersRepository(self.mock_client, self.mock_auth, cached_orders=[cached])

        entry = ("acc_1", {"id": "ORD_100", "status": "delivered"}, "新店铺")
        enriched = repo.enrich_single(entry)

        self.assertIsNotNone(enriched)
        self.assertEqual(enriched.order_id, "ORD_100")
        self.assertEqual(enriched.store_name, "新店铺")
        # Should not make any API calls for terminal status
        self.mock_client.get_marketplace_order.assert_not_called()
        self.mock_client.get_item_detail.assert_not_called()
        self.mock_client.get_marketplace_shipment.assert_not_called()

    def test_enrich_single_shipment_details_and_shipping_costs(self) -> None:
        self.mock_client.get_marketplace_shipment.return_value = {
            "tracking_number": "TRK123456",
            "tracking_method": "J&T Express",
            "status": "ready_to_ship",
            "substatus": "printed",
            "dimensions": {"length": 15, "width": 10, "height": 5, "weight": 200},
            "lead_time": {
                "cost_type": "partially_free",
                "cost": 5.0,
                "list_cost": 15.0,
            },
        }
        self.mock_client.get_marketplace_shipment_costs.return_value = {
            "senders": [
                {
                    "cost": 10.0,
                    "compensations": [{"amount": 2.5, "reason": "dimensions weight difference"}],
                }
            ]
        }

        entry = (
            "acc_1",
            {
                "id": "ORD_200",
                "status": "paid",
                "order_items": [
                    {
                        "item": {"id": "MLB12345", "title": "测试商品"},
                        "quantity": 1,
                        "unit_price": 50.0,
                    }
                ],
                "shipping": {"id": 999888},
            },
            "店铺A",
        )

        order = self.repo.enrich_single(entry)
        self.assertIsNotNone(order)
        self.assertEqual(order.order_id, "ORD_200")
        self.assertEqual(order.shipping.tracking_number, "TRK123456")
        self.assertEqual(order.shipping.tracking_method, "J&T Express")
        self.assertEqual(order.shipping.base_shipping_cost, 10.0)
        self.assertEqual(order.shipping.shipping_adjustment, 2.5)
        self.assertEqual(order.shipping.shipping_adjustment_reason, "尺寸/重量差异")
        self.assertEqual(order.shipping.shipping_cost, 12.5)

    def test_snapshot_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            test_snapshot_file = Path(tmpdir) / "orders_snapshot.json"
            with patch.object(OrdersRepository, "get_snapshot_path", return_value=test_snapshot_file):
                orders = [
                    OrderModel(
                        order_id="ORD_S1",
                        account_id="acc_1",
                        site_id="MLB",
                        status="paid",
                        store_name="店铺1",
                        created_timestamp=1700000000.0,
                    ),
                    OrderModel(
                        order_id="ORD_S2",
                        account_id="acc_1",
                        site_id="MLB",
                        status="shipped",
                        store_name="店铺1",
                        created_timestamp=1700000500.0,
                    ),
                ]

                saved = self.repo.save_snapshot(orders)
                self.assertTrue(saved)
                self.assertTrue(test_snapshot_file.exists())

                loaded = self.repo.load_snapshot()
                self.assertEqual(len(loaded), 2)
                # Sorted descending by timestamp
                self.assertEqual(loaded[0].order_id, "ORD_S2")
                self.assertEqual(loaded[1].order_id, "ORD_S1")

    def test_export_to_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "export_test.csv"
            orders = [
                OrderModel(
                    order_id="ORD_EXP_1",
                    account_id="acc_1",
                    pack_id="PACK_1",
                    store_name="店铺1",
                    site_id="MLB",
                    date_created="2026-10-27T10:00:00Z",
                    status="paid",
                    buyer_nickname="BuyerOne",
                    total_amount=100.0,
                    paid_amount=95.0,
                    currency_id="USD",
                    items=[
                        OrderItemModel(
                            item_id="MLB999",
                            title="商品标题",
                            sku="SKU-1",
                            variation_attributes="Color: Negro",
                            quantity=2,
                            unit_price=50.0,
                        )
                    ],
                    shipping=ShippingInfoModel(
                        receiver_name="张三",
                        receiver_phone="12345678",
                        street_name="Av Paulista",
                        city_name="Sao Paulo",
                        country_id="BR",
                        logistic_type="fulfillment",
                        tracking_number="TRK999",
                    ),
                )
            ]

            count = OrdersRepository.export_to_csv(orders, csv_path)
            self.assertEqual(count, 1)
            self.assertTrue(csv_path.exists())

            with open(csv_path, encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                rows = list(reader)
                self.assertEqual(len(rows), 2)  # Header + 1 row
                header = rows[0]
                row = rows[1]
                self.assertIn("订单号", header)
                self.assertIn("变体规格", header)
                self.assertEqual(row[0], "ORD_EXP_1")
                self.assertEqual(row[14], "颜色: 黑色")
                self.assertEqual(row[17], "100.0")
