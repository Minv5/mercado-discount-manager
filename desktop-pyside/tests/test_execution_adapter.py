from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.auth import AuthManager
from engine.client import MercadoClient
from engine.execution_adapter import PolicyExecutionAdapter
from engine.policy import ActionType, PolicyPlan, PromoAction


class TestPolicyExecutionAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_auth = MagicMock(spec=AuthManager)
        self.mock_auth.db_path = Path(tempfile.gettempdir()) / "test_auth.sqlite"
        self.mock_client = MagicMock(spec=MercadoClient)
        self.adapter = PolicyExecutionAdapter(self.mock_auth, self.mock_client)

    def test_cache_clearing(self) -> None:
        self.adapter._price_cache["MLB100"] = 120.0
        self.adapter._net_cache["MLB100"] = 80.0
        self.adapter._shipping_cache["MLB100"] = 15.0
        self.adapter._dim_cache["MLB100"] = ("10x10x10", "1kg")

        self.adapter.clear_item_cache("MLB100")

        self.assertNotIn("MLB100", self.adapter._price_cache)
        self.assertNotIn("MLB100", self.adapter._net_cache)
        self.assertNotIn("MLB100", self.adapter._shipping_cache)
        self.assertNotIn("MLB100", self.adapter._dim_cache)

    def test_cancel_promotion_with_readback_healing(self) -> None:
        # 1. Normal success
        self.mock_client.cancel_promotion_item.return_value = None
        ok, err = self.adapter.cancel_promotion_with_readback("acc1", "usr1", "MLB100", "P1", "DEAL")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertTrue(self.adapter.is_recently_exited("MLB100", "P1"))

        # 2. Network error healed when remote readback confirms exited
        self.mock_client.cancel_promotion_item.side_effect = RuntimeError("美客多网络请求超时或连接失败: IncompleteRead")
        self.mock_client.get_item_promotions.return_value = [{"id": "P2", "status": "candidate"}]
        ok2, err2 = self.adapter.cancel_promotion_with_readback("acc1", "usr1", "MLB100", "P2", "DEAL")
        self.assertTrue(ok2)
        self.assertEqual(err2, "")
        self.assertTrue(self.adapter.is_recently_exited("MLB100", "P2"))

        # 3. 404 error treated as success (already removed)
        self.mock_client.cancel_promotion_item.side_effect = RuntimeError("404 Not Found")
        self.mock_client.get_item_promotions.side_effect = RuntimeError("404 Not Found")
        ok3, err3 = self.adapter.cancel_promotion_with_readback("acc1", "usr1", "MLB100", "P3", "DEAL")
        self.assertTrue(ok3)
        self.assertEqual(err3, "")

    def test_execute_plan_cancellation_and_reenroll(self) -> None:
        plan = PolicyPlan(
            item_id="MLB100",
            decision_type=ActionType.PLATFORM_SHIPPING_CHANGE,
            account_id="acc1",
            child_user_id="usr1",
            site_id="MLB",
            current_price=100.0,
            current_net=70.0,
            current_shipping=12.0,
            dim_str="10x10x10",
            weight_str="1kg",
            pricing_system="system1",
            log_messages=[("运费更新", "自动报回")],
            promos_to_cancel=[
                PromoAction(promotion_id="P_OLD", promotion_type="DEAL", action="cancel", name="限时大促")
            ],
            promos_to_enroll=[
                PromoAction(promotion_id="P_NEW", promotion_type="DEAL", action="enroll", deal_price=85.0, name="新大促")
            ],
            raw_item_snapshot={"id": "MLB100", "price": 100.0},
        )

        logs_collected: list[tuple[str, str | None]] = []

        def on_log(msg: str, tag: str | None = None) -> None:
            logs_collected.append((msg, tag))

        res = self.adapter.execute_plan(plan, on_log=on_log)
        self.assertTrue(res)

        # Cancel called
        self.mock_client.cancel_promotion_item.assert_called_once_with(
            "acc1", "usr1", "MLB100", "P_OLD", "DEAL"
        )
        # Enroll called
        self.mock_client.enroll_promotion_item.assert_called_once_with(
            account_id="acc1",
            child_user_id="usr1",
            item_id="MLB100",
            promotion_id="P_NEW",
            promotion_type="DEAL",
            deal_price=85.0,
            offer_id=None,
            original_price=100.0,
        )

        # Caches updated
        self.assertEqual(self.adapter._price_cache.get("MLB100"), 100.0)
        self.assertEqual(self.adapter._net_cache.get("MLB100"), 70.0)
        self.assertEqual(self.adapter._shipping_cache.get("MLB100"), 12.0)
        self.assertEqual(self.adapter._dim_cache.get("MLB100"), ("10x10x10", "1kg"))

    def test_execute_plan_cbt_cascade(self) -> None:
        plan = PolicyPlan(
            item_id="MLB100",
            decision_type=ActionType.SELLER_DATA_CHANGE,
            account_id="acc1",
            child_user_id="usr1",
            site_id="MLB",
            current_price=120.0,
            current_net=80.0,
            current_shipping=15.0,
            dim_str=None,
            weight_str=None,
            pricing_system="system2_155",
            change_desc="卖家修改售价 $100 -> $120",
            raw_item_snapshot={"id": "MLB100", "cbt_item_id": "CBT999"},
        )

        def fake_get_item_detail(acc_id: str, item_id: str) -> dict:
            if item_id == "CBT999":
                return {
                    "id": "CBT999",
                    "marketplace_items": [
                        {"item_id": "MLB100", "user_id": "usr1", "site_id": "MLB"},
                        {"item_id": "MLM200", "user_id": "usr2", "site_id": "MLM"},
                    ],
                }
            if item_id == "MLM200":
                return {
                    "id": "MLM200",
                    "status": "active",
                    "price": 200.0,
                    "net_proceeds": {"amount": 140.0, "additional_concepts": [{"id": "shipping_cost", "amount": 25.0}]},
                }
            return {}

        self.mock_client.get_item_detail.side_effect = fake_get_item_detail
        self.mock_client.request.return_value = [{"id": "P_SIBLING", "type": "DEAL", "status": "started"}]

        res = self.adapter.execute_plan(plan)
        self.assertTrue(res)

        # Sibling promo cancelled
        self.mock_client.cancel_promotion_item.assert_called_once_with(
            "acc1", "usr2", "MLM200", "P_SIBLING", "DEAL"
        )
