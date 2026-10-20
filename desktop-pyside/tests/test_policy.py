from __future__ import annotations

import unittest
from engine.policy import ActionType, ItemEventPolicy, extract_dimensions_and_weight


class TestItemEventPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_item_base = {
            "id": "MLM3554614505",
            "site_id": "MLM",
            "seller_id": 2668034127,
            "status": "active",
            "sub_status": [],
            "price": 13.69,
            "original_price": None,
            "net_proceeds": {
                "amount": 15.43,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 5.80},
                    {"id": "sale_fee", "amount": 3.89},
                ],
                "currency_id": "USD",
            },
            "cbt_item_id": "CBT5333385468",
        }

    def test_seller_net_proceeds_change_when_under_review(self) -> None:
        """When an item is under_review after seller modified net proceeds:
        Must cancel active promos for loss protection, place into pending review queue, and set ActionType.WAIT_PLATFORM_REVIEW.
        """
        raw = dict(self.raw_item_base)
        raw["status"] = "under_review"
        raw["sub_status"] = ["waiting_for_patch"]

        cached_snapshot = {
            "price": 13.69,
            "net_proceeds": 9.32,  # previous net proceeds was 9.32
            "shipping_cost": 2.25,
            "dimensions": None,
            "weight": None,
            "pricing_system": "system1_discount",
        }

        active_promos = [
            {"id": "C-MLM1665569", "type": "SELLER_CAMPAIGN", "name": "95折"}
        ]

        cbt_siblings = [
            {
                "item_id": "MLB999888777",
                "site_id": "MLB",
                "user_id": "3332096437",
                "active_promos": [{"id": "LGH-MLB1000", "type": "LIGHTNING", "name": "秒杀"}],
            }
        ]

        plan = ItemEventPolicy.evaluate(
            account_id="2651442567",
            child_user_id="2668034127",
            site_id="MLM",
            raw_item=raw,
            cached_snapshot=cached_snapshot,
            active_promos=active_promos,
            candidate_promos=[],
            cbt_siblings_data=cbt_siblings,
            store_alias="湖北店",
            discount_percent=30.0,
        )

        self.assertEqual(plan.decision_type, ActionType.WAIT_PLATFORM_REVIEW)
        self.assertTrue(plan.wait_for_review)
        self.assertEqual(len(plan.promos_to_cancel), 1)
        self.assertEqual(plan.promos_to_cancel[0].promotion_id, "C-MLM1665569")
        self.assertEqual(len(plan.sibling_cancels), 1)
        self.assertEqual(plan.sibling_cancels[0].item_id, "MLB999888777")
        self.assertTrue(any("平台审核中" in msg for msg, _ in plan.log_messages))
        self.assertTrue(any("湖北店" in msg for msg, _ in plan.log_messages))
        self.assertTrue(any("墨西哥站" in msg for msg, _ in plan.log_messages))

    def test_review_resolved_auto_reenroll(self) -> None:
        """When an item returns to active from under_review, auto re-enroll at new net proceeds."""
        raw = dict(self.raw_item_base)
        raw["status"] = "active"

        cached_snapshot = {
            "price": 25.12,
            "net_proceeds": 15.43,
            "shipping_cost": 5.80,
            "dimensions": None,
            "weight": None,
            "pricing_system": "system2_155",
        }

        candidate_promos = [
            {"id": "C-MLM1665569", "type": "SELLER_CAMPAIGN", "name": "95折", "offer_id": "OFFER_95_TEST"}
        ]

        plan = ItemEventPolicy.evaluate(
            account_id="2651442567",
            child_user_id="2668034127",
            site_id="MLM",
            raw_item=raw,
            cached_snapshot=cached_snapshot,
            active_promos=[],
            candidate_promos=candidate_promos,
            store_alias="湖北店",
            discount_percent=30.0,
            was_pending_review=True,
        )

        self.assertEqual(plan.decision_type, ActionType.REVIEW_RESOLVED_AUTO_REENROLL)
        self.assertFalse(plan.wait_for_review)
        self.assertEqual(len(plan.promos_to_enroll), 1)
        self.assertEqual(plan.promos_to_enroll[0].promotion_id, "C-MLM1665569")
        self.assertEqual(plan.promos_to_enroll[0].offer_id, "OFFER_95_TEST")
        self.assertTrue(plan.promos_to_enroll[0].deal_price > 15.43)
        self.assertTrue(any("平台审核已生效通过" in msg for msg, _ in plan.log_messages))

    def test_platform_shipping_change_recalculates_and_reenrolls(self) -> None:
        """When only shipping fee changes (System 1): cancel old promo lock and re-enroll with updated shipping."""
        raw = dict(self.raw_item_base)
        raw["net_proceeds"] = {
            "amount": 9.32,
            "additional_concepts": [
                {"id": "shipping_cost", "amount": 3.50},  # shipping increased from 2.25 to 3.50
                {"id": "sale_fee", "amount": 2.12},
            ],
            "currency_id": "USD",
        }
        raw["original_price"] = 13.69

        cached_snapshot = {
            "price": 13.69,
            "net_proceeds": 9.32,
            "shipping_cost": 2.25,
            "dimensions": None,
            "weight": None,
            "pricing_system": "system1_discount",
        }

        active_promos = [
            {"id": "C-MLM1665569", "type": "SELLER_CAMPAIGN", "name": "95折"}
        ]

        plan = ItemEventPolicy.evaluate(
            account_id="2651442567",
            child_user_id="2668034127",
            site_id="MLM",
            raw_item=raw,
            cached_snapshot=cached_snapshot,
            active_promos=active_promos,
            candidate_promos=[],
            store_alias="湖北店",
            discount_percent=30.0,
        )

        self.assertEqual(plan.decision_type, ActionType.PLATFORM_SHIPPING_CHANGE)
        self.assertEqual(len(plan.promos_to_cancel), 1)
        self.assertEqual(len(plan.promos_to_enroll), 1)
        self.assertTrue(any("运费" in msg for msg, _ in plan.log_messages))

    def test_no_change_updates_snapshot_only(self) -> None:
        """When net proceeds, shipping, and dimensions are all identical: NO_ACTION / UPDATE_SNAPSHOT_ONLY."""
        raw = dict(self.raw_item_base)
        cached_snapshot = {
            "price": 13.69,
            "net_proceeds": 15.43,
            "shipping_cost": 5.80,
            "dimensions": None,
            "weight": None,
            "pricing_system": "system2_155",
        }

        plan = ItemEventPolicy.evaluate(
            account_id="2651442567",
            child_user_id="2668034127",
            site_id="MLM",
            raw_item=raw,
            cached_snapshot=cached_snapshot,
            active_promos=[],
            candidate_promos=[],
            store_alias="湖北店",
        )

        self.assertEqual(plan.decision_type, ActionType.UPDATE_SNAPSHOT_ONLY)
        self.assertEqual(len(plan.promos_to_cancel), 0)
        self.assertEqual(len(plan.promos_to_enroll), 0)

    def test_evaluate_via_evaluation_context(self) -> None:
        """Verify ItemEventPolicy.evaluate accepts an EvaluationContext directly without data clumps."""
        from engine.policy import EvaluationContext

        raw = dict(self.raw_item_base)
        ctx = EvaluationContext(
            account_id="2651442567",
            child_user_id="2668034127",
            site_id="MLM",
            raw_item=raw,
            cached_snapshot={"price": 13.69, "net_proceeds": 15.43, "shipping_cost": 5.80},
            active_promos=[],
            candidate_promos=[],
            store_alias="湖北店",
        )
        plan = ItemEventPolicy.evaluate(ctx)
        self.assertEqual(plan.decision_type, ActionType.UPDATE_SNAPSHOT_ONLY)
        self.assertEqual(plan.item_id, "MLM3554614505")
        self.assertEqual(plan.site_id, "MLM")


if __name__ == "__main__":
    unittest.main()
