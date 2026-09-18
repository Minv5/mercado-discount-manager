import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.auth import AuthManager
from engine.bridge import EngineBridge
from engine.crypto import decrypt_secret, encrypt_secret
from engine.pricing import calculate_deal_price, extract_item_net_proceeds


class NativeEngineTests(unittest.TestCase):
    def test_crypto_roundtrip(self) -> None:
        secret = "meli_access_token_super_secret_12345"
        encrypted = encrypt_secret(secret)
        self.assertTrue(encrypted.startswith("v1:"))
        decrypted = decrypt_secret(encrypted)
        self.assertEqual(decrypted, secret)

    def test_auth_manager_lists_3_cbt_stores(self) -> None:
        auth = AuthManager()
        accounts = auth.list_accounts()
        self.assertEqual(len(accounts), 3)
        account_ids = {a["account_id"] for a in accounts}
        self.assertIn("2651442567", account_ids)
        self.assertIn("3332096437", account_ids)
        self.assertIn("3408885754", account_ids)

        # Check sites for account 2651442567
        sites = auth.list_sites("2651442567")
        site_ids = {s["site_id"] for s in sites}
        self.assertIn("MLM", site_ids)
        self.assertIn("MLB", site_ids)

    def test_pricing_prevents_double_discount_on_promotional_items(self) -> None:
        # Case MCO4413866694: Item currently on promo price $20.15, official catalog original is $24.82
        # Base net proceeds in ERP is $14.25, shipping is $6.97, sale fee is $3.60
        promo_item = {
            "id": "MCO4413866694",
            "price": 20.15,
            "original_price": 24.82,
            "currency_id": "USD",
            "net_proceeds": {
                "amount": 14.25,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 6.97},
                    {"id": "sale_fee", "amount": 3.60},
                ],
            },
        }
        info = extract_item_net_proceeds(promo_item)
        # Must take official base original price $24.82, NOT the current promotional $20.15
        self.assertEqual(info.price, 24.82)
        # Must preserve authoritative ERP net proceeds $14.25, NEVER override with discounted price
        self.assertEqual(info.net_proceeds, 14.25)
        self.assertEqual(info.shipping_cost, 6.97)

        # 28% discount on net proceeds (14.25 * 0.72 = 10.26)
        res = calculate_deal_price(info, 28.0)
        self.assertTrue(res.eligible)
        # Deal price must be $20.15, NEVER the catastrophic double-discounted $16.79
        self.assertAlmostEqual(res.deal_price, 20.15, places=2)
        self.assertAlmostEqual(res.target_net, 10.26, places=2)
        self.assertAlmostEqual(res.shipping_cost, 6.97, places=2)
        self.assertAlmostEqual(res.final_net_at_deal, 10.26, places=2)

    def test_pricing_hard_skip_when_platform_demand_exceeds_limit(self) -> None:
        item = {
            "id": "MLM3071064725",
            "price": 21.96,
            "original_price": 21.96,
            "currency_id": "USD",
            "net_proceeds": {
                "amount": 16.27,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 3.15},
                    {"id": "sale_fee", "amount": 2.54},
                ],
            },
        }
        info = extract_item_net_proceeds(item)
        # Target deal price is ~17.36. If platform demands max 16.00, it must be skipped!
        res = calculate_deal_price(info, 25.0, {"max_discounted_price": 16.00})
        self.assertFalse(res.eligible)
        self.assertIn("低于净回款保护售价", res.skip_reason)

    def test_bridge_dispatches_in_memory_routes(self) -> None:
        bridge = EngineBridge()
        health = bridge.handle_request("GET", "/api/health")
        self.assertTrue(health["ok"])
        self.assertEqual(health["protocol_version"], "3")
        self.assertEqual(health["build_fingerprint"], "native-python-v2.0.19")

        accounts_res = bridge.handle_request("GET", "/api/accounts")
        self.assertEqual(len(accounts_res["accounts"]), 3)

        tasks_res = bridge.handle_request("GET", "/api/tasks?limit=10")
        self.assertIn("tasks", tasks_res)

        details_res = bridge.handle_request("GET", "/api/tasks/details?taskIds=1015")
        self.assertTrue(details_res["ok"])
        self.assertIsInstance(details_res["details"], list)

        items_res = bridge.handle_request("GET", "/api/tasks/items?task_ids=1015")
        self.assertTrue(items_res["ok"])
        self.assertIn("failed_items", items_res["items"])

    def test_multi_store_concurrency(self) -> None:
        bridge = EngineBridge()
        calls = []

        def mock_run(account_id, site_id, mode, seller_discount, official_discount, is_cancelled=None, **kwargs):
            import time
            time.sleep(0.01)
            calls.append(account_id)
            return {"status": "completed", "success": 2, "failed": 0, "skipped": 1}

        bridge.executor.run_execution = mock_run
        payload = {
            "account_ids": ["2651442567", "3332096437", "3408885754"],
            "action": "批量报活动",
            "seller_discount": 5.0,
            "official_discount": 6.0,
        }
        bridge._groups["test_grp"] = {"status": "running"}
        bridge._run_group_worker("test_grp", payload)
        self.assertEqual(len(calls), 3)
        self.assertEqual(set(calls), {"2651442567", "3332096437", "3408885754"})
        self.assertEqual(bridge._groups["test_grp"]["result"]["success"], 6)
        self.assertEqual(bridge._groups["test_grp"]["result"]["skipped"], 3)

    def test_cancel_group_graceful_shutdown(self) -> None:
        bridge = EngineBridge()
        grp_id = "grp_test_cancel"

        def mock_cancelled_run(account_id, site_id, mode, seller_discount, official_discount, on_progress=None, is_cancelled=None, **kwargs):
            from engine.executor import ExecutionProgress
            # Report partial success before cancel
            p = ExecutionProgress(total=5, success=3, failed=0, skipped=1)
            if on_progress:
                on_progress(p)
            
            # Mid-execution: User clicks cancel!
            cancel_res = bridge.handle_request("POST", f"/api/execution/groups/{grp_id}/cancel")
            self.assertTrue(cancel_res.get("ok"))
            # Must be cancelling, not terminal cancelled prematurely!
            self.assertEqual(bridge._groups[grp_id]["status"], "cancelling")

            # Active endpoint should still recognize it while cancelling
            active_res = bridge.handle_request("GET", "/api/execution/groups/active")
            self.assertIsNotNone(active_res.get("group"))
            self.assertEqual(active_res["group"]["id"], grp_id)

            is_canc = is_cancelled() if is_cancelled else False
            self.assertTrue(is_canc)

            return {
                "status": "cancelled" if is_canc else "completed",
                "success": 3,
                "failed": 0,
                "skipped": 1,
                "total": 4,
            }

        bridge.executor.run_execution = mock_cancelled_run
        payload = {
            "account_ids": ["2651442567"],
            "action": "批量报活动",
            "seller_discount": 5.0,
            "official_discount": 6.0,
        }
        bridge._groups[grp_id] = {"id": grp_id, "status": "running", "result": {"success": 0, "failed": 0, "skipped": 0}}

        # Worker executes and wraps up
        bridge._run_group_worker(grp_id, payload)

        # Now status is terminal cancelled AND successful items are preserved
        self.assertEqual(bridge._groups[grp_id]["status"], "cancelled")
        self.assertEqual(bridge._groups[grp_id]["result"]["success"], 3)
        self.assertEqual(bridge._groups[grp_id]["result"]["skipped"], 1)

    def test_smart_promotion_seller_percentage_guard(self) -> None:
        item = {
            "id": "MLA3715427450",
            "price": 56.43,
            "currency_id": "USD",
            "net_proceeds": {"amount": 25.0, "additional_concepts": [{"id": "shipping_cost", "amount": 15.0}, {"id": "sale_fee", "amount": 6.77}]},
        }
        info = extract_item_net_proceeds(item)

        # 1. SMART candidate requires seller_percentage 41.16% -> final net below target net -> Should skip!
        cand_over = {"offer_id": "CAND-123", "seller_percentage": 41.16, "price": 32.85}
        res_over = calculate_deal_price(info, 28.0, promotion_constraints=cand_over, promotion_type="SMART")
        self.assertFalse(res_over.eligible)
        self.assertIn("低于目标保底净回款", res_over.skip_reason)

        # 2. SMART candidate final net proceeds >= target net -> Should be eligible!
        cand_ok = {"offer_id": "CAND-456", "seller_percentage": 10.0, "price": 42.09}
        res_ok = calculate_deal_price(info, 28.0, promotion_constraints=cand_ok, promotion_type="SMART")
        self.assertTrue(res_ok.eligible)
        self.assertGreaterEqual(res_ok.final_net_at_deal, res_ok.target_net)
    def test_oauth_invalid_grant_error_raised(self) -> None:
        import io
        import urllib.error
        from unittest.mock import patch
        from engine.auth import OAuthInvalidGrantError

        auth = AuthManager()
        error_body = b'{"message":"invalid_grant","error":"invalid_grant","status":400}'
        http_error = urllib.error.HTTPError(
            url="https://api.mercadolibre.com/oauth/token",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(error_body),
        )

        with patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(OAuthInvalidGrantError) as ctx:
                auth.get_token("2651442567", force_refresh=True)

            self.assertEqual(ctx.exception.account_id, "2651442567")
            self.assertIn("授权已失效或在后台被解除", str(ctx.exception))

    def test_pricing_top_deal_price_limit_guard(self) -> None:
        item = {
            "id": "MLM111",
            "price": 20.0,
            "currency_id": "USD",
            "net_proceeds": {"amount": 15.0, "additional_concepts": [{"id": "shipping_cost", "amount": 3.0}, {"id": "sale_fee", "amount": 2.0}]},
        }
        info = extract_item_net_proceeds(item)
        res = calculate_deal_price(info, 20.0, {"top_deal_price": 15.00})
        self.assertFalse(res.eligible)
        self.assertIn("平台要求限价", res.skip_reason)

    def test_pricing_suggested_price_does_not_block_enrollment(self) -> None:
        item = {
            "id": "MLM222",
            "price": 20.0,
            "currency_id": "USD",
            "net_proceeds": {"amount": 15.0, "additional_concepts": [{"id": "shipping_cost", "amount": 3.0}, {"id": "sale_fee", "amount": 2.0}]},
        }
        info = extract_item_net_proceeds(item)
        # Platform suggested price must NOT be treated as a hard skip threshold
        res = calculate_deal_price(info, 20.0, {"suggested_discounted_price": 14.00})
        self.assertTrue(res.eligible)
        self.assertIsNone(res.skip_reason)

    def test_pricing_deal_price_non_positive_guard(self) -> None:
        from engine.pricing import ItemNetProceeds
        info = ItemNetProceeds(
            item_id="TEST001",
            price=10.0,
            currency_id="USD",
            net_proceeds=1.0,
            shipping_cost=-2.0,
            sale_fee=1.0,
            fee_rate=0.1,
        )
        res = calculate_deal_price(info, 100.0)
        self.assertFalse(res.eligible)
        self.assertIn("计算活动价小于等于0", res.skip_reason)

    def test_client_get_promotion_items_deduplication(self) -> None:
        from unittest.mock import MagicMock
        from engine.client import MercadoClient
        mock_auth = MagicMock()
        mock_auth.get_valid_token.return_value = "mock_tok"
        client = MercadoClient(mock_auth)

        page1 = {
            "results": [{"id": "ITEM1"}, {"id": "ITEM2"}],
            "paging": {"total": 3, "search_after": "page2"},
        }
        page2 = {
            "results": [{"id": "ITEM2"}, {"id": "ITEM3"}],
            "paging": {"total": 3, "search_after": None},
        }
        client.request = MagicMock(side_effect=[page1, page2])

        items = client.get_promotion_items("2651442567", "c_1", "PROMO1")
        self.assertEqual(len(items), 3)
        self.assertEqual([i["id"] for i in items], ["ITEM1", "ITEM2", "ITEM3"])

    def test_client_enroll_promotion_item_uses_put_for_update(self) -> None:
        from unittest.mock import MagicMock
        from engine.client import MercadoClient
        mock_auth = MagicMock()
        mock_auth.get_valid_token.return_value = "mock_tok"
        client = MercadoClient(mock_auth)
        client.request = MagicMock(return_value={"status": "ok"})

        client.enroll_promotion_item(
            account_id="2651442567",
            child_user_id="c_1",
            item_id="ITEM1",
            promotion_id="P1",
            promotion_type="DEAL",
            deal_price=19.99,
            action="enroll",
        )
        self.assertEqual(client.request.call_args[0][1], "POST")

        client.enroll_promotion_item(
            account_id="2651442567",
            child_user_id="c_1",
            item_id="ITEM1",
            promotion_id="P1",
            promotion_type="DEAL",
            deal_price=18.99,
            action="update",
        )
        self.assertEqual(client.request.call_args[0][1], "PUT")

    def test_executor_filters_and_seller_campaign_c_prefix(self) -> None:
        from unittest.mock import MagicMock
        from engine.executor import ActionExecutor
        mock_auth = MagicMock()
        mock_auth.list_sites.return_value = [{"site_id": "MLM", "child_user_id": "c_mlm"}]
        mock_client = MagicMock()
        mock_client.get_seller_promotions.return_value = [
            {"id": "P_FINISHED", "status": "finished", "type": "DEAL"},
            {"id": "C-SELLER-1", "status": "active", "type": "CUSTOM", "name": "我的自建"},
            {"id": "OFFICIAL-1", "status": "active", "type": "DEAL", "name": "官方大促"},
        ]
        mock_client.get_promotion_items.return_value = []

        executor = ActionExecutor(mock_auth, mock_client)
        executor._get_store_alias = MagicMock(return_value="测试店铺")
        executor._record_task = MagicMock()

        filters = {"excludeSeller": True}
        executor.run_execution(
            account_id="2651442567",
            site_id="MLM",
            mode="enroll",
            seller_discount=20.0,
            official_discount=25.0,
            filters=filters,
        )

        called_promo_ids = [call[0][2] for call in mock_client.get_promotion_items.call_args_list]
        self.assertNotIn("P_FINISHED", called_promo_ids)
        self.assertNotIn("C-SELLER-1", called_promo_ids)
        self.assertIn("OFFICIAL-1", called_promo_ids)

    def test_executor_target_item_ids_filtering(self) -> None:
        from unittest.mock import MagicMock
        from engine.executor import ActionExecutor
        mock_auth = MagicMock()
        mock_auth.list_sites.return_value = [{"site_id": "MLM", "child_user_id": "c_mlm"}]
        mock_client = MagicMock()
        mock_client.get_seller_promotions.return_value = [
            {"id": "OFFICIAL-1", "status": "active", "type": "DEAL", "name": "官方活动"}
        ]
        mock_client.get_promotion_items.return_value = [
            {"id": "MLM100", "price": 10.0},
            {"id": "MLM200", "price": 20.0},
            {"id": "MLM300", "price": 30.0},
        ]
        mock_client.get_item_detail.return_value = {
            "id": "MLM100",
            "price": 10.0,
            "currency_id": "USD",
            "net_proceeds": {"amount": 7.0, "additional_concepts": []},
        }

        executor = ActionExecutor(mock_auth, mock_client)
        executor._get_store_alias = MagicMock(return_value="测试店铺")
        executor._record_task = MagicMock()

        res = executor.run_execution(
            account_id="2651442567",
            site_id="MLM",
            mode="enroll",
            seller_discount=20.0,
            official_discount=20.0,
            target_item_ids=["MLM100"],
        )
        self.assertEqual(res["total"], 1)
        self.assertEqual(mock_client.get_item_detail.call_count, 1)
        self.assertEqual(mock_client.get_item_detail.call_args[0][1], "MLM100")

    def test_webhook_worker_resolve_route_matches_by_site_prefix(self) -> None:
        from unittest.mock import MagicMock
        from engine.webhook_worker import WebhookWorker
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = [{"account_id": "2651442567", "store_name": "店1"}]
        mock_auth.list_sites.return_value = [
            {"site_id": "MLM", "child_user_id": "child_mlm"},
            {"site_id": "MLB", "child_user_id": "child_mlb"},
        ]
        mock_client = MagicMock()
        worker = WebhookWorker(mock_auth, mock_client)

        acc, c_uid, site = worker._resolve_route("2651442567", "MLB999888")
        self.assertEqual(acc, "2651442567")
        self.assertEqual(c_uid, "child_mlb")
        self.assertEqual(site, "MLB")

        acc2, c_uid2, site2 = worker._resolve_route("2651442567", "MLM111222")
        self.assertEqual(acc2, "2651442567")
        self.assertEqual(c_uid2, "child_mlm")
        self.assertEqual(site2, "MLM")

    def test_bridge_targeted_refresh_and_item_status_and_oauth_start(self) -> None:
        bridge = EngineBridge()
        status_res = bridge.handle_request("GET", "/api/items/MLM12345/status")
        self.assertTrue(status_res["ok"])
        self.assertEqual(status_res["item_id"], "MLM12345")

        ref_res = bridge.handle_request("POST", "/api/items/targeted-refresh", body={"item_ids": ["MLM12345"]})
        self.assertTrue(ref_res["ok"])
        self.assertEqual(ref_res["total_count"], 1)

        oauth_start = bridge.handle_request("POST", "/api/oauth/start", body={"clientId": "test_app_id"})
        self.assertTrue(oauth_start["ok"])
        self.assertIn("auth.mercadolibre.com.mx", oauth_start["authorizationUrl"])
        self.assertIn("test_app_id", oauth_start["authorizationUrl"])


if __name__ == "__main__":
    unittest.main()

