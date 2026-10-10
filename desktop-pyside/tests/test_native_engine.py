import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import Account
from engine.auth import AuthManager
from engine.bridge import EngineBridge
from engine.crypto import decrypt_secret, encrypt_secret
from engine.pricing import calculate_deal_price, extract_item_net_proceeds
from engine.webhook_worker import WebhookWorker


class NativeEngineTests(unittest.TestCase):
    _temp_dir: str | None = None
    _prev_data_dir: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._prev_data_dir = os.environ.get("MDM_DATA_DIR")
        cls._temp_dir = tempfile.mkdtemp(prefix="mdm_native_test_")
        os.environ["MDM_DATA_DIR"] = cls._temp_dir
        EngineBridge._instance = None

        auth = AuthManager()
        conn = auth._get_connection()
        try:
            now = "2026-09-30T00:00:00Z"
            exp = "2099-01-01T00:00:00Z"
            tok_cipher = encrypt_secret("test_access_token_fixture")
            ref_cipher = encrypt_secret("test_refresh_token_fixture")
            sec_cipher = encrypt_secret("test_client_secret_fixture")

            profiles = [
                ("2651442567", "mercadolibre", "CNHUBEISHENGRUIHESHANGM", "CBT", now, "oauth"),
                ("3332096437", "mercadolibre", "CNGUANGZHOULINGTANGMINB", "CBT", now, "oauth"),
                ("3408885754", "mercadolibre", "CNLIUYANGSHIZHEPINGDIAN", "CBT", now, "oauth"),
            ]
            for acc_id, provider, name, site_id, fetched_at, source in profiles:
                conn.execute(
                    "INSERT OR REPLACE INTO account_profiles (account_id, provider, display_name, site_id, fetched_at, source) VALUES (?, ?, ?, ?, ?, ?)",
                    (acc_id, provider, name, site_id, fetched_at, source),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO oauth_tokens
                    (provider, account_id, display_name, site_id, access_token_cipher, refresh_token_cipher,
                     client_id, client_secret_cipher, expires_at, auth_domain, created_at, updated_at)
                    VALUES ('mercadolibre', ?, ?, 'CBT', ?, ?, 'test_client_id', ?, ?, 'https://api.mercadolibre.com', ?, ?)
                    """,
                    (acc_id, name, tok_cipher, ref_cipher, sec_cipher, exp, now, now),
                )

            sites = [
                ("2651442567", "2668033833", "MLC"),
                ("2651442567", "2668031897", "MLB"),
                ("2651442567", "2668034137", "MCO"),
                ("2651442567", "2668034127", "MLM"),
                ("2651442567", "2668033839", "MLM"),
                ("2651442567", "3005675422", "MLA"),
                ("2651442567", "3184159748", "MLU"),
                ("3332096437", "3333531536", "MLM"),
                ("3332096437", "3333531544", "MLU"),
                ("3332096437", "3333531540", "MLA"),
                ("3332096437", "3333531550", "MLM"),
                ("3332096437", "3333531560", "MLB"),
                ("3332096437", "3333531568", "MLC"),
                ("3332096437", "3333530776", "MCO"),
                ("3408885754", "3407224975", "MCO"),
                ("3408885754", "3407225955", "MLC"),
                ("3408885754", "3407225957", "MLM"),
                ("3408885754", "3407224977", "MLA"),
                ("3408885754", "3407225959", "MLU"),
                ("3408885754", "3407227823", "MLM"),
                ("3408885754", "3407227825", "MLB"),
            ]
            for acc_id, child_id, site_id in sites:
                conn.execute(
                    "INSERT OR REPLACE INTO marketplace_sites (account_id, child_user_id, site_id, updated_at) VALUES (?, ?, ?, ?)",
                    (acc_id, child_id, site_id, now),
                )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls) -> None:
        EngineBridge._instance = None
        if cls._prev_data_dir is None:
            os.environ.pop("MDM_DATA_DIR", None)
        else:
            os.environ["MDM_DATA_DIR"] = cls._prev_data_dir
        if cls._temp_dir and os.path.exists(cls._temp_dir):
            shutil.rmtree(cls._temp_dir, ignore_errors=True)

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

    def test_pricing_does_not_locally_block_when_platform_demand_lower(self) -> None:
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
        # Target deal price is ~17.36. Platform metadata requires max 16.00.
        # Strict rule: Software must NOT locally intercept or pre-filter; directly submit to platform API!
        res = calculate_deal_price(info, 25.0, {"max_discounted_price": 16.00})
        self.assertTrue(res.eligible)
        self.assertIsNone(res.skip_reason)

    def test_bridge_dispatches_in_memory_routes(self) -> None:
        bridge = EngineBridge()
        health = bridge.handle_request("GET", "/api/health")
        self.assertTrue(health["ok"])
        self.assertEqual(health["protocol_version"], "3")
        self.assertEqual(health["build_fingerprint"], "native-python-v2.1.13")

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

    def test_pricing_top_deal_price_does_not_locally_block(self) -> None:
        item = {
            "id": "MLM111",
            "price": 20.0,
            "currency_id": "USD",
            "net_proceeds": {"amount": 15.0, "additional_concepts": [{"id": "shipping_cost", "amount": 3.0}, {"id": "sale_fee", "amount": 2.0}]},
        }
        info = extract_item_net_proceeds(item)
        res = calculate_deal_price(info, 20.0, {"top_deal_price": 15.00})
        self.assertTrue(res.eligible)
        self.assertIsNone(res.skip_reason)

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
        executor._init_task_record = MagicMock(return_value=None)
        executor._checkpoint_task = MagicMock()
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
        executor._init_task_record = MagicMock(return_value=None)
        executor._checkpoint_task = MagicMock()
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

    def test_webhook_worker_extract_item_id_and_pop_logs(self) -> None:
        from unittest.mock import MagicMock
        from engine.webhook_worker import WebhookWorker
        worker = WebhookWorker(MagicMock(), MagicMock())
        self.assertEqual(worker._extract_item_id("/marketplace/items/MLC4393139538"), "MLC4393139538")
        self.assertEqual(worker._extract_item_id("/items/CBT4704373846"), "CBT4704373846")
        self.assertEqual(
            worker._extract_item_id("/marketplace/seller-promotions/promotions/candidate/CANDIDATE-MLB4977530443-77310235931/3333531560"),
            "MLB4977530443",
        )
        self.assertIsNone(worker._extract_item_id("/orders/12345"))

        worker.log("测试日志1")
        worker.log("测试日志2")
        logs = worker.pop_logs()
        self.assertEqual(len(logs), 2)
        self.assertIn("测试日志1", logs[0])
        self.assertEqual(len(worker.pop_logs()), 0)

    def test_bridge_targeted_refresh_and_item_status_and_oauth_start(self) -> None:
        from unittest.mock import MagicMock
        bridge = EngineBridge()
        bridge.client.get_item_detail = MagicMock(return_value={"id": "MLM12345", "price": 19.99})
        status_res = bridge.handle_request("GET", "/api/items/MLM12345/status")
        self.assertTrue(status_res["ok"])
        self.assertEqual(status_res["item_id"], "MLM12345")

        ref_res = bridge.handle_request("POST", "/api/items/targeted-refresh", body={"item_ids": ["MLM12345"]})
        self.assertTrue(ref_res["ok"])
        self.assertEqual(ref_res["total_count"], 1)

        missing_redirect = bridge.handle_request("POST", "/api/oauth/start", body={"clientId": "test_app_id"})
        self.assertFalse(missing_redirect["ok"])
        self.assertIn("Redirect URI", missing_redirect["error"])

        oauth_start = bridge.handle_request("POST", "/api/oauth/start", body={"clientId": "test_app_id", "redirectUri": "https://127.0.0.1/callback"})
        self.assertTrue(oauth_start["ok"])
        self.assertIn("auth.mercadolibre.com.mx", oauth_start["authorizationUrl"])
        self.assertIn("test_app_id", oauth_start["authorizationUrl"])

    def test_client_per_account_semaphore_isolation(self) -> None:
        from engine.client import MercadoClient
        client = MercadoClient(max_concurrency=10)
        sem1 = client._get_semaphore("account_1")
        sem2 = client._get_semaphore("account_2")
        self.assertIsNot(sem1, sem2)
        sem1.acquire()
        self.assertTrue(sem2.acquire(blocking=False))
        sem2.release()
        sem1.release()

    def test_https_connection_pool_reuses_connection(self) -> None:
        from engine.client import HTTPSConnectionPool
        import http.client
        pool = HTTPSConnectionPool(max_size=2)
        conn1 = pool.get_connection()
        self.assertIsInstance(conn1, http.client.HTTPSConnection)
        pool.release_connection(conn1, close=False)
        conn2 = pool.get_connection()
        self.assertIs(conn1, conn2)
        pool.release_connection(conn2, close=True)

    def test_executor_item_detail_cache_avoids_duplicate_requests(self) -> None:
        from unittest.mock import MagicMock
        from engine.executor import ActionExecutor
        mock_auth = MagicMock()
        mock_auth.list_sites.return_value = [{"site_id": "MLM", "child_user_id": "c_mlm"}]
        mock_client = MagicMock()
        mock_client.get_seller_promotions.return_value = [
            {"id": "PROMO_A", "status": "active", "type": "DEAL", "name": "促A"},
            {"id": "PROMO_B", "status": "active", "type": "DEAL", "name": "促B"},
        ]
        # Same item appears in both promotions!
        mock_client.get_promotion_items.side_effect = [
            [{"id": "MLM_SAME", "price": 10.0}],
            [{"id": "MLM_SAME", "price": 10.0}],
        ]
        mock_client.get_item_detail.return_value = {
            "id": "MLM_SAME",
            "price": 10.0,
            "currency_id": "USD",
            "net_proceeds": {"amount": 8.0, "additional_concepts": []},
        }

        executor = ActionExecutor(mock_auth, mock_client)
        executor._get_store_alias = MagicMock(return_value="测试店铺")
        executor._init_task_record = MagicMock(return_value=999)
        executor._checkpoint_task = MagicMock()
        executor._record_task = MagicMock()

        res = executor.run_execution(
            account_id="2651442567",
            site_id="MLM",
            mode="enroll",
            seller_discount=20.0,
            official_discount=20.0,
        )

        # Even though item appeared in two promotions, get_item_detail was only fetched once!
        self.assertEqual(mock_client.get_item_detail.call_count, 1)
        self.assertEqual(executor._init_task_record.call_count, 1)
        self.assertGreaterEqual(executor._checkpoint_task.call_count, 1)
        self.assertEqual(executor._record_task.call_count, 1)

    def test_bridge_user_logs_capped_at_rolling_buffer(self) -> None:
        from engine.bridge import EngineBridge
        bridge = EngineBridge()
        # Mock group with a child
        group_id = "grp_test_buffer"
        bridge._groups[group_id] = {
            "id": group_id,
            "status": "running",
            "children": [{
                "id": "job_acc1",
                "account_id": "acc1",
                "user_logs": [{"id": f"log_{i}", "message": f"msg {i}"} for i in range(250)],
            }],
        }
        res = bridge.handle_request("GET", f"/api/execution/groups/{group_id}")
        self.assertTrue(res["ok"])

    def test_webhook_worker_strict_account_routing_rejects_external_stores(self) -> None:
        from engine.webhook_worker import WebhookWorker
        worker = WebhookWorker()

        # 1. External account that does not match local parent or child store IDs
        acc_id, child_id, site_id = worker._resolve_route("3560797046", "MLM3227815447")
        self.assertIsNone(acc_id)
        self.assertIsNone(child_id)
        self.assertIsNone(site_id)

        # 2. Known local parent account resolves correctly
        acc_id, child_id, site_id = worker._resolve_route("2651442567", "MLM123456")
        self.assertEqual(acc_id, "2651442567")
        self.assertEqual(site_id, "MLM")
        self.assertIn(child_id, ["2668034127", "2668033839"])

    def test_webhook_worker_handles_404_deleted_item_gracefully(self) -> None:
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker.client.get_item_detail.side_effect = RuntimeError("美客多 API 报错 (404): Item with id MLB123 not found")
        worker._ack_event = MagicMock()

        event = {
            "event_id": "test_evt_404",
            "lease_id": "test_lease_404",
            "topic": "items",
            "resource": "/marketplace/items/MLB123",
            "user_id": "2651442567",
        }

        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("test_evt_404", "test_lease_404", ok=True)
        # 404 is now completely silent (Silent ACK, zero log pollution)
        self.assertEqual(len(worker._logs), 0)
        self.assertNotIn("MLB123", worker._price_cache)

    def test_webhook_worker_handles_closed_item_gracefully(self) -> None:
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker.client.get_item_detail.return_value = {"id": "MLB123", "status": "closed"}
        worker._ack_event = MagicMock()

        event = {
            "event_id": "test_evt_closed",
            "lease_id": "test_lease_closed",
            "topic": "items",
            "resource": "/marketplace/items/MLB123",
            "user_id": "2651442567",
        }

        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("test_evt_closed", "test_lease_closed", ok=True)
        # Closed items are now completely silent (Silent ACK)
        self.assertEqual(len(worker._logs), 0)

    def test_webhook_worker_fast_bypasses_candidates_and_offers(self) -> None:
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker._ack_event = MagicMock()

        for topic in ["public_candidates", "public_offers"]:
            event = {
                "event_id": f"evt_{topic}",
                "lease_id": f"lease_{topic}",
                "topic": topic,
                "resource": "/marketplace/seller-promotions/promotions/candidate/123",
                "user_id": "2651442567",
            }
            worker._process_single_event(event)
            worker._ack_event.assert_called_with(f"evt_{topic}", f"lease_{topic}", ok=True)
            # Must NOT invoke Mercado API for candidate/offer broadcasts
            worker.client.get_item_detail.assert_not_called()
            self.assertEqual(len(worker._logs), 0)

    def test_webhook_worker_silent_ack_when_price_unchanged(self) -> None:
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker.client.get_item_detail.return_value = {
            "id": "MLB123",
            "status": "active",
            "price": 100.0,
            "shipping": {"free_shipping": False},
        }
        worker._ack_event = MagicMock()
        worker._price_cache["MLB123"] = 100.0

        event = {
            "event_id": "test_evt_stock",
            "lease_id": "test_lease_stock",
            "topic": "items",
            "resource": "/marketplace/items/MLB123",
            "user_id": "2651442567",
        }

        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("test_evt_stock", "test_lease_stock", ok=True)
        # Price hasn't changed, so no recalculation logs
        self.assertEqual(len(worker._logs), 0)

    def test_webhook_worker_cold_start_initializes_cache_without_touching_promotions(self) -> None:
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker.client.get_item_detail.return_value = {
            "id": "MLB123",
            "status": "active",
            "price": 100.0,
            "shipping": {"free_shipping": False},
        }
        worker._ack_event = MagicMock()
        # Empty cache simulates cold start
        self.assertNotIn("MLB123", worker._price_cache)

        event = {
            "event_id": "test_evt_cold",
            "lease_id": "test_lease_cold",
            "topic": "items",
            "resource": "/marketplace/items/MLB123",
            "user_id": "2651442567",
        }

        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("test_evt_cold", "test_lease_cold", ok=True)
        # Baseline cached
        self.assertEqual(worker._price_cache["MLB123"], 100.0)
        # Absolutely NO promo cancellation or enrollment
        worker.client.cancel_promotion_item.assert_not_called()
        worker.client.enroll_promotion_item.assert_not_called()
        self.assertEqual(len(worker._logs), 0)

    def test_webhook_worker_detects_price_diff_and_cancels_all_promotions_without_re_enroll(self) -> None:
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker.client.get_item_detail.return_value = {
            "id": "MLB123",
            "status": "active",
            "price": 80.0,
            "shipping": {"free_shipping": False},
        }
        # Simulate active promo returned
        worker.client.request.return_value = [{"id": "PROMO_999", "type": "DEAL", "status": "started"}]
        worker._ack_event = MagicMock()
        worker._price_cache["MLB123"] = 100.0

        event = {
            "event_id": "test_evt_price_change",
            "lease_id": "test_lease_price_change",
            "topic": "items",
            "resource": "/marketplace/items/MLB123",
            "user_id": "2651442567",
        }

        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("test_evt_price_change", "test_lease_price_change", ok=True)
        # Verify cancel called
        worker.client.cancel_promotion_item.assert_called_once()
        # Strictly verify re-enroll is NEVER called
        worker.client.enroll_promotion_item.assert_not_called()
        self.assertTrue(any("售价变动 ($100.00 -> $80.00)" in line for line in worker._logs))
        self.assertTrue(any("已退出活动 PROMO_999" in line for line in worker._logs))
        self.assertEqual(worker._price_cache["MLB123"], 80.0)

    def test_client_default_concurrency_is_18(self) -> None:
        from engine.client import MercadoClient
        client = MercadoClient()
        self.assertEqual(client.max_concurrency_per_account, 18)
        sem = client._get_semaphore("test_acc")
        self.assertEqual(sem._value, 18)


    def test_executor_empty_promotions_log_distinguishes_action(self) -> None:
        from engine.executor import ActionExecutor
        from unittest.mock import MagicMock
        executor = ActionExecutor()
        executor.client = MagicMock()
        executor.auth = MagicMock()
        executor._init_task_record = MagicMock(return_value=9999)
        executor._checkpoint_task = MagicMock()
        executor._record_task = MagicMock()
        executor.auth.list_sites.return_value = [
            {"site_id": "MLM", "child_user_id": "3333531536"},
        ]
        executor.client.get_seller_promotions.return_value = []
        logs = []

        # 1. Action = cancel
        executor.run_execution(
            account_id="test_acc",
            site_id="MLM",
            mode="批量取消",
            seller_discount=0,
            official_discount=0,
            on_log=logs.append,
            target_item_ids=["ITEM1"],
        )
        self.assertTrue(any("当前无活动需要退出" in msg and "子账号: 3333531536" in msg for msg in logs))
        self.assertFalse(any("暂无可报活动" in msg for msg in logs))

        # 2. Action = enroll
        logs.clear()
        executor.run_execution(
            account_id="test_acc",
            site_id="MLM",
            mode="批量提报",
            seller_discount=10,
            official_discount=10,
            on_log=logs.append,
            target_item_ids=["ITEM1"],
        )
        self.assertTrue(any("暂无可报活动" in msg and "子账号: 3333531536" in msg for msg in logs))
        self.assertFalse(any("当前无活动需要退出" in msg for msg in logs))

    def test_webhook_worker_detects_in_promo_breakdown_inconsistency_without_prior_cache(self) -> None:
        """Real case MLB7269271374: item modified during promo, original_price locked at 14.94 while breakdown is 27.43."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker.client.get_item_detail.return_value = {
            "id": "MLB7269271374",
            "site_id": "MLB",
            "seller_id": 3407227825,
            "status": "active",
            "price": 11.51,
            "original_price": 14.94,
            "net_proceeds": {
                "amount": 14.60,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 9.40},
                    {"id": "sale_fee", "amount": 3.43},
                ],
                "currency_id": "USD",
            },
        }
        worker.client.request.return_value = [
            {"id": "C-MLB5402231", "type": "SELLER_CAMPAIGN", "status": "started"},
            {"id": "P-MLB18049186", "type": "DEAL", "status": "started"},
            {"id": "P-MLB18061082", "type": "DEAL", "status": "pending"},
        ]
        worker._ack_event = MagicMock()
        # Even with empty _price_cache (first notification after restart), must detect and cancel all 3 promos!
        self.assertNotIn("MLB7269271374", worker._price_cache)

        event = {
            "event_id": "evt_mlb_inconsistent",
            "lease_id": "lease_mlb_inconsistent",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLB7269271374",
            "remote_user_id": "3408885754",
        }
        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("evt_mlb_inconsistent", "lease_mlb_inconsistent", ok=True)
        self.assertEqual(worker.client.cancel_promotion_item.call_count, 3)

    def test_webhook_worker_expands_cbt_parent_item_to_site_children(self) -> None:
        """Real case CBT3819274457: parent item notification fans out to MLB7269271374 and MLM5826739672."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()

        def fake_detail(acc_id: str, iid: str):
            if iid == "CBT3819274457":
                return {
                    "id": "CBT3819274457",
                    "site_id": "CBT",
                    "marketplace_items": [
                        {"item_id": "MLB7269271374", "user_id": 3407227825, "site_id": "MLB"},
                        {"item_id": "MLM5826739672", "user_id": 3407227823, "site_id": "MLM"},
                    ],
                }
            if iid == "MLB7269271374":
                return {
                    "id": "MLB7269271374",
                    "site_id": "MLB",
                    "seller_id": 3407227825,
                    "status": "active",
                    "price": 11.51,
                    "original_price": 14.94,
                    "net_proceeds": {
                        "amount": 14.60,
                        "additional_concepts": [
                            {"id": "shipping_cost", "amount": 9.40},
                            {"id": "sale_fee", "amount": 3.43},
                        ],
                    },
                }
            return {
                "id": "MLM5826739672",
                "site_id": "MLM",
                "seller_id": 3407227823,
                "status": "active",
                "price": 11.72,
                "original_price": 15.21,
                "net_proceeds": {
                    "amount": 14.60,
                    "additional_concepts": [
                        {"id": "shipping_cost", "amount": 7.00},
                        {"id": "sale_fee", "amount": 3.96},
                    ],
                },
            }

        worker.client.get_item_detail.side_effect = fake_detail
        worker.client.request.return_value = [{"id": "C-PROMO1", "type": "SELLER_CAMPAIGN", "status": "started"}]
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_cbt_fanout",
            "lease_id": "lease_cbt_fanout",
            "topic": "items",
            "resource": "/items/CBT3819274457",
            "remote_user_id": "3408885754",
        }
        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("evt_cbt_fanout", "lease_cbt_fanout", ok=True)
        self.assertEqual(worker.client.cancel_promotion_item.call_count, 2)

    def test_webhook_worker_system1_platform_shipping_change_re_enrolls(self) -> None:
        """System 1 item: seller net_proceeds (15.13) unchanged, only platform shipping_cost increased -> exit and re-enroll at discount."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        # Cached net_proceeds is 15.13, old total price was 25.83
        worker._price_cache["MLM6192859628"] = 25.83
        worker._net_cache["MLM6192859628"] = 15.13

        worker.client.get_item_detail.return_value = {
            "id": "MLM6192859628",
            "site_id": "MLM",
            "seller_id": 3407227823,
            "status": "active",
            "price": 19.62,
            "original_price": 25.83,
            "net_proceeds": {
                "amount": 15.13,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 7.70},
                    {"id": "sale_fee", "amount": 4.19},
                ],
                "currency_id": "USD",
            },
        }
        worker.client.request.return_value = [
            {"id": "C-MLM1477572", "type": "SELLER_CAMPAIGN", "status": "started"}
        ]
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_sys1_ship",
            "lease_id": "lease_sys1_ship",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLM6192859628",
            "remote_user_id": "3408885754",
        }
        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("evt_sys1_ship", "lease_sys1_ship", ok=True)
        worker.client.cancel_promotion_item.assert_called_once()
        worker.client.enroll_promotion_item.assert_called_once()
        self.assertTrue(any("按新运费报回活动 C-MLM1477572" in line for line in worker._logs))

    def test_webhook_worker_system2_unenrolled_item_never_enrolls_on_shipping_change(self) -> None:
        """System 2 / newly uploaded 155% item (original_price=None): platform shipping change never triggers enrollment."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker._price_cache["MLB_NEW_155"] = 25.00
        worker._net_cache["MLB_NEW_155"] = 14.60

        worker.client.get_item_detail.return_value = {
            "id": "MLB_NEW_155",
            "site_id": "MLB",
            "seller_id": 3407227825,
            "status": "active",
            "price": 26.20,
            "original_price": None,
            "net_proceeds": {
                "amount": 14.60,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 8.20},
                    {"id": "sale_fee", "amount": 3.40},
                ],
                "currency_id": "USD",
            },
        }
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_sys2_ship",
            "lease_id": "lease_sys2_ship",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLB_NEW_155",
            "remote_user_id": "3408885754",
        }
        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("evt_sys2_ship", "lease_sys2_ship", ok=True)
        worker.client.cancel_promotion_item.assert_not_called()
        worker.client.enroll_promotion_item.assert_not_called()
        self.assertEqual(len(worker._logs), 0)

    def test_webhook_worker_cbt_cascade_system2_exit_across_sites(self) -> None:
        """When one child item under a CBT parent triggers System 2 exit, all sibling sites under the same CBT also exit promotions."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker._price_cache["MLB111111"] = 14.02
        worker._net_cache["MLB111111"] = 10.40

        def fake_get_item_detail(acc_id: str, item_id: str) -> dict:
            if item_id == "MLB111111":
                return {
                    "id": "MLB111111",
                    "cbt_item_id": "CBT999888",
                    "site_id": "MLB",
                    "seller_id": 3407227825,
                    "status": "active",
                    "price": 29.63,
                    "original_price": None,
                    "net_proceeds": {
                        "amount": 19.33,
                        "additional_concepts": [
                            {"id": "shipping_cost", "amount": 6.00},
                            {"id": "sale_fee", "amount": 4.30},
                        ],
                        "currency_id": "USD",
                    },
                }
            if item_id == "CBT999888":
                return {
                    "id": "CBT999888",
                    "marketplace_items": [
                        {"item_id": "MLB111111", "user_id": 3407227825, "site_id": "MLB"},
                        {"item_id": "MLM222222", "user_id": 3407227823, "site_id": "MLM"},
                    ],
                }
            if item_id == "MLM222222":
                return {
                    "id": "MLM222222",
                    "cbt_item_id": "CBT999888",
                    "site_id": "MLM",
                    "seller_id": 3407227823,
                    "status": "active",
                    "price": 8.04,
                    "original_price": 10.40,
                    "net_proceeds": {
                        "amount": 6.75,
                        "additional_concepts": [
                            {"id": "shipping_cost", "amount": 2.25},
                            {"id": "sale_fee", "amount": 1.40},
                        ],
                        "currency_id": "USD",
                    },
                }
            return {}

        worker.client.get_item_detail.side_effect = fake_get_item_detail

        def fake_request(acc_id: str, method: str, path: str, params: dict | None = None) -> list:
            if "MLM222222" in path:
                return [{"id": "P-MLM17611046", "type": "DEAL", "status": "started"}]
            return []

        worker.client.request.side_effect = fake_request
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_cbt_cascade",
            "lease_id": "lease_cbt_cascade",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLB111111",
            "remote_user_id": "3408885754",
        }
        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("evt_cbt_cascade", "lease_cbt_cascade", ok=True)
        worker.client.cancel_promotion_item.assert_called_once_with(
            "3408885754", "3407227823", "MLM222222", "P-MLM17611046", "DEAL"
        )
        self.assertTrue(any("因同父商品 CBT999888 数据变动，已联动退出活动 P-MLM17611046" in line for line in worker._logs))

    def test_webhook_worker_deduplication_and_cascade_debouncing(self) -> None:
        """Verify Webhook deduplicates multiple variations of the same CBT sibling item,
        debounces CBT cascades within cooldown, and prevents duplicate promo exit logs.
        """
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        def fake_detail(acc: str, it_id: str) -> dict:
            if it_id == "CBT555":
                return {
                    "id": "CBT555",
                    "marketplace_items": [
                        {"item_id": "MLB200", "user_id": "111", "site_id": "MLB"},
                        {"item_id": "MLB200", "user_id": "111", "site_id": "MLB"},  # duplicate variation
                    ],
                }
            if it_id == "MLB200":
                return {"id": "MLB200", "status": "active", "seller_id": "111", "site_id": "MLB"}
            return {}

        worker.client.get_item_detail.side_effect = fake_detail
        worker.client.request.return_value = [
            {"id": "P-MLB999", "type": "DEAL", "status": "started"}
        ]
        worker._cascade_cbt_system2_exit("acc1", "CBT555", trigger_item_id="MLC100")
        # Should only call cancel_promotion_item ONCE despite MLB200 appearing twice in marketplace_items
        worker.client.cancel_promotion_item.assert_called_once_with(
            "acc1", "111", "MLB200", "P-MLB999", "DEAL"
        )
        # Calling cascade again immediately for the same CBT555 should be debounced
        worker.client.cancel_promotion_item.reset_mock()
        worker._cascade_cbt_system2_exit("acc1", "CBT555", trigger_item_id="MLC100")
        worker.client.cancel_promotion_item.assert_not_called()

    def test_webhook_worker_displays_promotion_name_in_logs(self) -> None:
        """Verify Webhook logs display human-readable promotion names 【Super Hot Sale】 when present in promotion data."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker._dim_cache["MLB333"] = ('{"length": 10}', '1.0')
        worker.client.get_item_detail.return_value = {
            "id": "MLB333",
            "status": "active",
            "seller_id": "111",
            "site_id": "MLB",
            "attributes": [{"id": "PACKAGE_LENGTH", "value_name": "20 cm", "value_struct": {"number": 20, "unit": "cm"}}],
        }
        worker.client.request.return_value = [
            {"id": "P-MLB8888", "name": "Super Hot Sale", "type": "DEAL", "status": "started"}
        ]
        worker._process_marketplace_item("acc1", "111", "MLB", "MLB333")
        self.assertTrue(any("已退出活动 【Super Hot Sale】 (DEAL)" in line for line in worker._logs))

    def test_webhook_worker_log_formatting_and_shipping_separation(self) -> None:
        """Verify Webhook logs use [自动退出] and [自动报回], cleanly separate shipping cost from listing price, and omit internal terms."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker._price_cache["MLB7611962534"] = 30.63
        worker._net_cache["MLB7611962534"] = 15.06
        worker._shipping_cache["MLB7611962534"] = 11.35

        worker.client.get_item_detail.return_value = {
            "id": "MLB7611962534",
            "site_id": "MLB",
            "seller_id": 3407227825,
            "status": "active",
            "price": 28.63,
            "original_price": 30.63,
            "net_proceeds": {
                "amount": 15.06,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 14.51},
                    {"id": "sale_fee", "amount": 4.22},
                ],
                "currency_id": "USD",
            },
        }
        worker.client.request.return_value = [
            {"id": "P-MLB18061082", "type": "DEAL", "status": "started"}
        ]
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_shipping_separate",
            "lease_id": "lease_shipping_separate",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLB7611962534",
            "remote_user_id": "3408885754",
        }
        worker._process_single_event(event)
        worker._ack_event.assert_called_once_with("evt_shipping_separate", "lease_shipping_separate", ok=True)
        worker.client.cancel_promotion_item.assert_called_once()
        worker.client.enroll_promotion_item.assert_called_once()

        # Check logs
        logs = worker._logs
        self.assertEqual(len(logs), 2)
        # 1. Tags must be [自动退出] and [自动报回]
        exit_logs = [l for l in logs if l.startswith("[自动退出]")]
        re_enroll_logs = [l for l in logs if l.startswith("[自动报回]")]
        self.assertEqual(len(exit_logs), 1)
        self.assertEqual(len(re_enroll_logs), 1)

        # 2. Must clearly separate pure shipping cost from original listing price
        self.assertTrue(any("平台运费调整 ($11.35 -> $14.51)" in l for l in exit_logs))
        self.assertTrue(any("售价更新 ($30.63 -> $33.79)" in l for l in exit_logs))
        self.assertTrue(any("退出旧活动" in l for l in exit_logs))

        # 3. Must format deal price cleanly
        self.assertTrue(any("按新运费报回活动 P-MLB18061082 (DEAL)，折后售价 $" in l for l in re_enroll_logs))

        # 4. Strictly no internal terminology or old prefixes
        for l in logs:
            self.assertNotIn("体系1商品", l)
            self.assertNotIn("[Webhook 自动改价重报]", l)

    def test_webhook_worker_credibility_error_concise_logging(self) -> None:
        """Verify that ERROR_CREDIBILITY_DISCOUNTED_PRICE logs concise, human-readable reason."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker._price_cache["MLC4236736938"] = 15.00
        worker._net_cache["MLC4236736938"] = 12.35
        worker._shipping_cache["MLC4236736938"] = 1.20

        worker.client.get_item_detail.return_value = {
            "id": "MLC4236736938",
            "site_id": "MLC",
            "seller_id": 3407225955,
            "status": "active",
            "price": 17.04,
            "original_price": 15.00,
            "net_proceeds": {
                "amount": 12.35,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 1.88},
                    {"id": "sale_fee", "amount": 2.81},
                ],
                "currency_id": "USD",
            },
        }
        worker.client.request.side_effect = [
            # 1. get item promotions: started DEAL
            [{"id": "P-MLC17951022", "type": "DEAL", "status": "started", "raw": {"suggested_discounted_price": 14.81}}],
            # 2. cancel promotions: ok
            {},
        ]
        worker.client.enroll_promotion_item.side_effect = RuntimeError(
            "美客多 API 报错 (400): Errors: ERROR_CREDIBILITY_DISCOUNTED_PRICE - The discounted price is not credible."
        )
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_cred_test",
            "lease_id": "lease_cred_test",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLC4236736938",
            "remote_user_id": "3408885754",
        }
        worker._process_single_event(event)

        # Check logs
        enroll_fail_logs = [l for l in worker._logs if "失败，折扣价高于平台预期" in l]
        self.assertEqual(len(enroll_fail_logs), 1)
        self.assertIn("折后价 $", enroll_fail_logs[0])
        self.assertIn("平台预期$14.81", enroll_fail_logs[0])
        self.assertNotIn("ERROR_CREDIBILITY_DISCOUNTED_PRICE", enroll_fail_logs[0])
        self.assertNotIn("The discounted price is not credible", enroll_fail_logs[0])

    def test_webhook_extracts_dimensions_and_weight_from_variations(self) -> None:
        """Verify that items with dimensions/weight only in variations are correctly extracted."""
        worker = WebhookWorker(client=MagicMock())
        item_with_variations = {
            "id": "MLB4897041997",
            "attributes": [
                {"id": "BRAND", "value_name": "Genérico"},
                {"id": "MODEL", "value_name": "Toy-01"},
            ],
            "variations": [
                {
                    "id": 205241801533,
                    "attributes": [
                        {"id": "PACKAGE_HEIGHT", "value_struct": {"number": 14, "unit": "cm"}},
                        {"id": "PACKAGE_LENGTH", "value_struct": {"number": 16, "unit": "cm"}},
                        {"id": "PACKAGE_WIDTH", "value_struct": {"number": 13, "unit": "cm"}},
                        {"id": "PACKAGE_WEIGHT", "value_struct": {"number": 410, "unit": "g"}},
                    ],
                }
            ],
        }
        dim_str, weight_str = worker._extract_dimensions_and_weight(item_with_variations)
        self.assertIsNotNone(dim_str)
        self.assertIsNotNone(weight_str)
        assert dim_str is not None and weight_str is not None
        dims = json.loads(dim_str)
        self.assertEqual(dims["height"]["number"], 14)
        self.assertEqual(dims["length"]["number"], 16)
        self.assertEqual(dims["width"]["number"], 13)
        weight = json.loads(weight_str)
        self.assertEqual(weight["number"], 410)

    @patch("time.sleep", return_value=None)
    def test_webhook_enroll_retry_on_locked_entity_and_clean_log(self, _mock_sleep: MagicMock) -> None:
        """Verify that encountering LockedEntityException retries with backoff and formats a clean log."""
        worker = WebhookWorker(client=MagicMock())
        worker._accounts = [Account("3332096437", "", "CBT", "广东店")]
        worker._price_cache["MLB4897041997"] = 28.00
        worker._net_cache["MLB4897041997"] = 12.58
        worker._shipping_cache["MLB4897041997"] = 8.50

        worker.client.get_item_detail.return_value = {
            "id": "MLB4897041997",
            "site_id": "MLB",
            "seller_id": 3333531560,
            "status": "active",
            "price": 26.15,
            "original_price": 28.00,
            "net_proceeds": {
                "amount": 12.58,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 10.30},
                    {"id": "sale_fee", "amount": 3.27},
                ],
                "currency_id": "USD",
            },
        }
        worker.client.request.side_effect = [
            # 1. get item promotions: started DEAL
            [{"id": "P-MLB18027202", "type": "DEAL", "status": "started", "raw": {}}],
            # 2. cancel
            {},
        ]
        # Always fail with LockedEntityException
        worker.client.enroll_promotion_item.side_effect = RuntimeError(
            "美客多 API 报错 (400): Errors: LockedEntityException: Offer Locked [MLB4897041997]"
        )
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_lock_test",
            "lease_id": "lease_lock_test",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLB4897041997",
            "remote_user_id": "3332096437",
        }
        worker._process_single_event(event)

        # Verified retried 3 times (attempt 0, 1, 2)
        self.assertEqual(worker.client.enroll_promotion_item.call_count, 3)

        # Check clean log output
        lock_logs = [l for l in worker._logs if "平台活动锁占用中" in l]
        self.assertEqual(len(lock_logs), 1)
        self.assertIn("商品 MLB4897041997: 失败，平台活动锁占用中（请稍后重试）", lock_logs[0])
        self.assertNotIn("LockedEntityException", lock_logs[0])


if __name__ == "__main__":
    unittest.main()


