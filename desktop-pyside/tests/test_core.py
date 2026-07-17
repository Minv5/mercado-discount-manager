from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import (  # noqa: E402
    EXCLUDE_ACTIVITY,
    ActionConflictError,
    action_for_mode,
    account_from_json,
    build_filters,
    confirmation_text,
    discount_inputs_enabled,
    execution_payload,
    resolve_global_action,
    task_display_counts,
)


class CoreMatrixTests(unittest.TestCase):
    def test_filter_combination_matrix(self) -> None:
        modes = ["自动判断", "批量报活动", "批量更新", "批量取消"]
        stores = ["all", "single"]
        sites = ["", "MLM"]
        sellers = ["", "seller-name", EXCLUDE_ACTIVITY]
        officials = ["", "deal-name", EXCLUDE_ACTIVITY]
        seen = 0
        for mode, _store, site, seller, official in itertools.product(modes, stores, sites, sellers, officials):
            filters = build_filters(site, seller, official)
            self.assertEqual(filters["siteIds"], [site] if site else [])
            self.assertEqual(filters["excludeSeller"], seller == EXCLUDE_ACTIVITY)
            self.assertEqual(filters["excludeOfficial"], official == EXCLUDE_ACTIVITY)
            if seller not in ("", EXCLUDE_ACTIVITY):
                self.assertEqual(filters["sellerActivityNames"], [seller])
            if official not in ("", EXCLUDE_ACTIVITY):
                self.assertEqual(filters["officialActivityNames"], [official])
            self.assertIn(action_for_mode(mode), ("", "enroll", "update", "cancel"))
            seen += 1
        self.assertEqual(seen, 144)

    def test_global_action_conflict_blocks_mixed_execution(self) -> None:
        self.assertEqual(resolve_global_action(["enroll", "enroll"]), "enroll")
        with self.assertRaisesRegex(ActionConflictError, "不同店铺需要不同动作"):
            resolve_global_action(["enroll", "update"])

    def test_cancel_disables_discounts_and_confirmation_hides_values(self) -> None:
        self.assertFalse(discount_inputs_enabled("批量取消"))
        text = confirmation_text("全部店铺", "墨西哥站", "cancel", 6, 7)
        self.assertNotIn("折扣", text)
        self.assertIn("批量取消", text)

    def test_manual_modes_keep_their_selected_action(self) -> None:
        self.assertEqual(action_for_mode("批量报活动"), "enroll")
        self.assertEqual(action_for_mode("批量更新"), "update")
        self.assertEqual(action_for_mode("批量取消"), "cancel")

    def test_execution_payload_preserves_confirmation_gate(self) -> None:
        payload = execution_payload(
            account_id="A1",
            action="update",
            filters=build_filters("MLM", "", ""),
            store_name="测试店",
            site_name_text="墨西哥站",
            seller_discount=6,
            official_discount=7,
            read_concurrency=2,
            activity_concurrency=2,
            write_concurrency=5,
        )
        self.assertEqual(payload["confirmText"], "REAL_SUBMIT")
        self.assertFalse(payload["prepareOnly"])
        self.assertEqual(payload["action"], "update")

    def test_account_consumes_api_store_name_without_local_id_mapping(self) -> None:
        named = account_from_json({
            "account_id": "A1",
            "raw_display_name": "ENGLISH-RAW",
            "store_name": "业务店名",
            "store_name_source": "explicit_alias",
        })
        self.assertEqual(named.raw_display_name, "ENGLISH-RAW")
        self.assertEqual(named.store_name, "业务店名")
        unnamed = account_from_json({"account_id": "A2", "raw_display_name": "ENGLISH-RAW"})
        self.assertEqual(unnamed.store_name, "店铺待命名")

    def test_task_display_count_prefers_unique_items_without_counting_relations_or_activity_failures(self) -> None:
        task = {
            "total_count": 140,
            "success_count": 80,
            "failed_count": 5,
            "skipped_count": 15,
            "relation_count": 140,
            "unique_item_count": 100,
            "activity_failure_count": 3,
        }
        self.assertEqual(task_display_counts(task), (100, 80, 5, 15))

if __name__ == "__main__":
    unittest.main()
