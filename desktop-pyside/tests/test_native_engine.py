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
from engine.bridge import EngineBridge, validate_seller_campaign_input
from engine.crypto import decrypt_secret, encrypt_secret
from engine.pricing import calculate_deal_price, extract_item_net_proceeds
from engine.webhook_worker import WebhookWorker
from dialogs import render_item_status_text


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
        self.assertEqual(health["build_fingerprint"], "native-python-v2.2.29")

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
        self.assertTrue(any("因全球父商品 CBT999888 净收益变动（$10.40 -> $19.33），已联动退出活动 P-MLM17611046" in line for line in worker._logs))

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
        self.assertTrue(any("已退出活动 【Super Hot Sale】。" in line for line in worker._logs))

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

        # 3. Must format deal price cleanly without trailing English promotion type
        self.assertTrue(any("按新运费报回活动 P-MLB18061082，折后售价 $" in l for l in re_enroll_logs))
        self.assertFalse(any("(DEAL)" in l for l in re_enroll_logs))

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

    def test_webhook_platform_shipping_detected_for_unpromoted_item(self) -> None:
        """Verify that items without active promotions also detect shipping changes and log baseline updates."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker(client=MagicMock())
        worker._accounts = [Account("3332096437", "", "CBT", "广东店")]
        worker._price_cache["MLM5826762560"] = 22.19
        worker._net_cache["MLM5826762560"] = 12.42
        worker._shipping_cache["MLM5826762560"] = 7.00

        # Item is active, but not in any promotions (original_price is None/not present)
        worker.client.get_item_detail.return_value = {
            "id": "MLM5826762560",
            "site_id": "MLM",
            "seller_id": 2668034127,
            "status": "active",
            "price": 24.50,
            "net_proceeds": {
                "amount": 12.42,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 9.31},
                    {"id": "sale_fee", "amount": 2.77},
                ],
                "currency_id": "USD",
            },
        }
        # Promotions query returns empty
        worker.client.request.return_value = []
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_ship_test_1",
            "lease_id": "lease_ship_test_1",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLM5826762560",
            "remote_user_id": "2651442567",
        }
        worker._process_single_event(event)

        # Shipping cache must be updated
        self.assertEqual(worker._shipping_cache["MLM5826762560"], 9.31)

        # Must log platform shipping change with [运费更新] tag and price update
        ship_logs = [l for l in worker._logs if l.startswith("[运费更新]")]
        self.assertEqual(len(ship_logs), 1)
        self.assertIn("平台运费调整 ($7.00 -> $9.31)", ship_logs[0])
        self.assertIn("售价更新 ($22.19 -> $24.50)", ship_logs[0])
        self.assertIn("当前暂无可报活动，已更新最新运费基线", ship_logs[0])

    def test_webhook_stale_cache_does_not_exit_already_synced_active_promo(self) -> None:
        """Verify that if an item in an active promotion already has original_price matching current breakdown,
        a stale local shipping cache silently syncs without redundant exit/re-enroll."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker(client=MagicMock())
        worker._accounts = [Account("2651442567", "", "CBT", "湖北店")]
        worker._price_cache["MLM6295103910"] = 16.51
        worker._net_cache["MLM6295103910"] = 10.05
        worker._shipping_cache["MLM6295103910"] = 3.85

        # Active promotion was already updated at 00:34 to original_price=21.01 (10.05 + 7.70 + 3.26 = 21.01)
        worker.client.get_item_detail.return_value = {
            "id": "MLM6295103910",
            "site_id": "MLM",
            "seller_id": 2668034127,
            "status": "active",
            "price": 19.70,
            "original_price": 21.01,
            "net_proceeds": {
                "amount": 10.05,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 7.70},
                    {"id": "sale_fee", "amount": 3.26},
                ],
                "currency_id": "USD",
            },
        }
        worker._ack_event = MagicMock()

        event = {
            "event_id": "evt_stale_sync",
            "lease_id": "lease_stale_sync",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLM6295103910",
            "remote_user_id": "2651442567",
        }
        worker._process_single_event(event)

        # Cache must be silently updated to 21.01 and 7.70 without cancelling promotions
        self.assertEqual(worker._price_cache["MLM6295103910"], 21.01)
        self.assertEqual(worker._shipping_cache["MLM6295103910"], 7.70)
        worker.client.cancel_promotion_item.assert_not_called()
        worker.client.enroll_promotion_item.assert_not_called()
        self.assertEqual(len(worker._logs), 0)

    def test_webhook_shipping_old_cost_derivation_and_reenroll_debounce(self) -> None:
        """Real case MLB7586029946: when local cached_shipping already equals new shipping ($19.84) while
        active promo original_price is locked at $125.09, derive old shipping ($13.96 -> $19.84) and debounce
        duplicate events within 60s when platform original_price lags."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker(client=MagicMock())
        worker._accounts = [Account("3408885754", "", "CBT", "湖南店")]
        # Local cache already recorded shipping_cost=19.84 while active promo is still locked at 125.09
        worker._price_cache["MLB7586029946"] = 131.81
        worker._net_cache["MLB7586029946"] = 95.49
        worker._shipping_cache["MLB7586029946"] = 19.84

        worker.client.get_item_detail.return_value = {
            "id": "MLB7586029946",
            "site_id": "MLB",
            "seller_id": 3407227825,
            "status": "active",
            "price": 119.81,
            "original_price": 125.09,
            "net_proceeds": {
                "amount": 95.49,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 19.84},
                    {"id": "sale_fee", "amount": 16.48},
                ],
                "currency_id": "USD",
            },
        }
        worker.client.request.return_value = [
            {"id": "C-MLB5402231", "name": "95", "type": "SELLER_CAMPAIGN", "status": "started"}
        ]
        worker._ack_event = MagicMock()

        ev1 = {
            "event_id": "evt_7586_1",
            "lease_id": "lease_7586_1",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLB7586029946",
            "remote_user_id": "3408885754",
        }
        ev2 = {
            "event_id": "evt_7586_2",
            "lease_id": "lease_7586_2",
            "topic": "marketplace_items",
            "resource": "/marketplace/items/MLB7586029946",
            "remote_user_id": "3408885754",
        }
        worker._process_single_event(ev1)
        # Second event arrives immediately while platform API still returns original_price=125.09
        worker._process_single_event(ev2)

        # Must only exit and re-enroll ONCE (debounced on second event)
        worker.client.cancel_promotion_item.assert_called_once()
        worker.client.enroll_promotion_item.assert_called_once()
        self.assertEqual(len(worker._logs), 2)
        self.assertIn("平台运费调整 ($13.96 -> $19.84)，售价更新 ($125.09 -> $131.81)，退出旧活动", worker._logs[0])
        self.assertIn("按新运费报回活动 【95】", worker._logs[1])

    def test_validate_seller_campaign_input(self) -> None:
        # 1. Valid input (<= 14 days and same month)
        valid_input = {
            "name": "95",
            "startDate": "2026-10-11T00:00:00",
            "finishDate": "2026-10-25T23:59:59",
        }
        self.assertEqual(validate_seller_campaign_input(valid_input), [])

        # 2. Valid with midnight (e.g. 2026-10-25T00:00:00)
        valid_midnight = {
            "name": "95",
            "startDate": "2026-10-11T00:00:00",
            "finishDate": "2026-10-25T00:00:00",
        }
        self.assertEqual(validate_seller_campaign_input(valid_midnight), [])

        # 3. Missing name
        no_name = {
            "name": "",
            "startDate": "2026-10-11T00:00:00",
            "finishDate": "2026-10-25T23:59:59",
        }
        self.assertIn("缺少自建活动名称 name", validate_seller_campaign_input(no_name))

        # 4. Finish before start
        inverted = {
            "name": "95",
            "startDate": "2026-10-20T00:00:00",
            "finishDate": "2026-10-10T00:00:00",
        }
        self.assertIn("结束日期必须晚于开始日期", validate_seller_campaign_input(inverted))

        # 5. Cross natural month (cannot exceed month end)
        cross_month = {
            "name": "95",
            "startDate": "2026-10-28T00:00:00",
            "finishDate": "2026-11-05T00:00:00",
        }
        self.assertIn("结束日期不能超过开始日期所在月份的最后一天", validate_seller_campaign_input(cross_month))

        # 6. Exceeds 14 days
        exceeds_14 = {
            "name": "95",
            "startDate": "2026-10-11T00:00:00",
            "finishDate": "2026-10-27T00:00:00",
        }
        self.assertIn("自建活动持续时间不能超过 14 天", validate_seller_campaign_input(exceeds_14))

    def test_client_create_seller_campaign(self) -> None:
        bridge = EngineBridge.get_instance()
        with patch.object(bridge.client, "request", return_value={"id": "C-MLB999888", "promotion_type": "SELLER_CAMPAIGN"}) as mock_req:
            res = bridge.client.create_seller_campaign(
                account_id="2651442567",
                child_user_id="3333531550",
                name="95折特惠",
                start_date="2026-10-11T00:00:00",
                finish_date="2026-10-25T00:00:00",
                sub_type="FLEXIBLE_PERCENTAGE",
            )
            self.assertEqual(res["id"], "C-MLB999888")
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args[1]
            self.assertEqual(call_kwargs["account_id"], "2651442567")
            self.assertEqual(call_kwargs["method"], "POST")
            self.assertEqual(call_kwargs["path"], "/marketplace/seller-promotions/seller-campaign/3333531550")
            self.assertEqual(call_kwargs["body"]["promotion_type"], "SELLER_CAMPAIGN")
            self.assertEqual(call_kwargs["body"]["name"], "95折特惠")
            self.assertEqual(call_kwargs["body"]["sub_type"], "FLEXIBLE_PERCENTAGE")
            self.assertEqual(call_kwargs["extra_headers"]["X-Caller-Id"], "2651442567")
            self.assertEqual(call_kwargs["extra_headers"]["X-Client-Id"], "2651442567")

    def test_bridge_prepare_detects_seller_campaign_absence(self) -> None:
        bridge = EngineBridge.get_instance()
        # Mock client to return no seller campaign for site
        with patch.object(bridge.client, "get_seller_promotions", return_value=[{"id": "P-100", "type": "DEAL", "status": "started"}]):
            res = bridge.handle_request("POST", "/api/execution/submissions/prepare", {
                "action": "enroll",
                "accountIds": ["2651442567"],
                "site_id": "MLB",
            })
            self.assertTrue(res.get("ok"))
            prep = res.get("prepare", {})
            seller_det = prep.get("seller_detection", {})
            confirmed_absent = seller_det.get("confirmed_absent", [])
            self.assertTrue(len(confirmed_absent) > 0)
            target = confirmed_absent[0]
            self.assertEqual(target.get("account_id"), "2651442567")
            self.assertEqual(target.get("site_id"), "MLB")
            self.assertFalse(target.get("hasSellerCampaign"))

        # When action is cancel, confirmed_absent should be empty
        cancel_res = bridge.handle_request("POST", "/api/execution/submissions/prepare", {
            "action": "cancel",
            "accountIds": ["2651442567"],
            "site_id": "MLB",
        })
        cancel_prep = cancel_res.get("prepare", {})
        self.assertEqual(cancel_prep.get("seller_detection", {}).get("confirmed_absent", []), [])

    def test_bridge_prepare_ignores_fulfillment_sites_for_seller_campaign(self) -> None:
        bridge = EngineBridge.get_instance()
        mock_sites = [
            {"site_id": "MLM", "child_user_id": "111", "logistic_type": "fulfillment"},
            {"site_id": "MLM", "child_user_id": "222", "logistic_type": "remote"},
        ]
        with patch.object(bridge.auth, "list_sites", return_value=mock_sites):
            def mock_get_promos(acc_id: str, child_uid: str):
                if child_uid == "222":
                    return [{"id": "C-MLM100", "type": "SELLER_CAMPAIGN", "status": "started"}]
                return []

            with patch.object(bridge.client, "get_seller_promotions", side_effect=mock_get_promos):
                res = bridge.handle_request("POST", "/api/execution/submissions/prepare", {
                    "action": "enroll",
                    "accountIds": ["2651442567"],
                    "site_id": "MLM",
                })
                self.assertTrue(res.get("ok"))
                prep = res.get("prepare", {})
                seller_det = prep.get("seller_detection", {})
                self.assertEqual(seller_det.get("confirmed_absent", []), [])
                existing = seller_det.get("existing", [])
                self.assertEqual(len(existing), 1)
                self.assertEqual(existing[0].get("child_user_id"), "222")

    def test_bridge_seller_input_endpoint(self) -> None:
        bridge = EngineBridge.get_instance()
        prep_res = bridge.handle_request("POST", "/api/execution/submissions/prepare", {
            "action": "enroll",
            "accountIds": ["2651442567"],
            "site_id": "MLB",
        })
        prep_id = prep_res["prepare"]["prepare_id"]

        # Valid input save
        save_res = bridge.handle_request("POST", f"/api/execution/submissions/{prep_id}/seller-input", {
            "name": "95",
            "startDate": "2026-10-11T00:00:00",
            "finishDate": "2026-10-20T23:59:59",
            "targetSelections": [{"accountId": "2651442567", "childUserId": "123", "siteId": "MLB"}],
        })
        self.assertTrue(save_res.get("ok"))
        seller_input = save_res["prepare"]["seller_input"]
        self.assertEqual(seller_input["name"], "95")
        self.assertEqual(len(seller_input["selected_targets"]), 1)
        self.assertEqual(seller_input["validation_errors"], [])

        # Invalid date error check (exceeding 14 days or across month)
        err_res = bridge.handle_request("POST", f"/api/execution/submissions/{prep_id}/seller-input", {
            "name": "95",
            "startDate": "2026-10-11T00:00:00",
            "finishDate": "2026-10-31T23:59:59",
        })
        self.assertIn("自建活动持续时间不能超过 14 天", err_res["prepare"]["seller_input"]["validation_errors"])

    def test_item_status_and_dialog_unified_activity_names(self) -> None:
        bridge = EngineBridge.get_instance()
        conn = bridge.executor._get_conn()
        try:
            # Insert a campaign into promo_campaigns
            conn.execute("""
                INSERT OR REPLACE INTO promo_campaigns
                (account_id, promotion_id, promotion_type, child_user_id, site_id, name, status, raw_json, updated_at)
                VALUES ('2651442567', 'P-MLB18049186', 'DEAL', '3333531550', 'MLB', 'Vem fazer Compritta', 'started', '{}', '2026-10-11T00:00:00')
            """)
            # Insert a promo_action_result
            conn.execute("""
                INSERT INTO promo_tasks (id, account_id, promotion_id, promotion_type, action, mode, status, created_at, updated_at)
                VALUES (99991, '2651442567', 'P-MLB18049186', 'DEAL', 'enroll', 'real', 'completed', '2026-10-11 12:00:00', '2026-10-11 12:00:00')
            """)
            conn.execute("""
                INSERT INTO promo_action_results (task_id, account_id, promotion_id, promotion_type, item_id, action, mode, status, deal_price, created_at)
                VALUES (99991, '2651442567', 'P-MLB18049186', 'DEAL', 'MLB999000111', 'enroll', 'real', 'success', 89.9, '2026-10-11 12:00:00')
            """)
            conn.execute("""
                INSERT INTO promo_action_results (task_id, account_id, promotion_id, promotion_type, item_id, action, mode, status, deal_price, created_at)
                VALUES (99991, '2651442567', 'PRICE_DISCOUNT', 'PRICE_DISCOUNT', 'MLB999000111', 'enroll', 'real', 'success', 79.9, '2026-10-11 11:00:00')
            """)
            conn.commit()
        finally:
            conn.close()

        with patch.object(bridge.client, "get_item_detail", return_value={"id": "MLB999000111", "price": 99.0, "currency_id": "BRL"}), \
             patch.object(bridge.client, "get_item_promotions", return_value=[{"id": "PRICE_DISCOUNT", "type": "PRICE_DISCOUNT", "status": "started"}]):
            res = bridge.handle_request("GET", "/api/items/MLB999000111/status")
            self.assertTrue(res.get("ok"))
            actions = res.get("actions", [])
            self.assertTrue(len(actions) >= 2)
            # Row 0: PRICE_DISCOUNT -> 自建活动（单品折扣）
            self.assertEqual(actions[0]["promotion_name"], "自建活动（单品折扣）")
            # Row 1: P-MLB18049186 joined with promo_campaigns -> Vem fazer Compritta
            self.assertEqual(actions[1]["promotion_name"], "Vem fazer Compritta")

            # Check render_item_status_text formatting
            items = res.get("items", [])
            rendered = render_item_status_text("MLB999000111", actions, items, [])
            # Must NOT contain double dash - -
            self.assertNotIn("- -", rendered)
            # Must contain 自建活动（单品折扣）
            self.assertIn("自建活动（单品折扣）", rendered)
            # Latest action must be formatted nicely
            self.assertIn("最近一次操作：报名　自建活动（单品折扣）", rendered)

    def test_delete_tasks_and_api_route(self) -> None:
        bridge = EngineBridge()
        conn = bridge.executor._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO promo_tasks (
                    id, account_id, promotion_id, promotion_type, action, mode, status, created_at, updated_at
                )
                VALUES (88881, '2651442567', 'ALL', 'BATCH', 'enroll', 'real', 'completed', '2026-10-11', '2026-10-11'),
                       (88882, '2651442567', 'ALL', 'BATCH', 'cancel', 'real', 'completed', '2026-10-11', '2026-10-11')
            """)
            cur.execute("""
                INSERT OR REPLACE INTO promo_action_results (id, task_id, account_id, promotion_id, promotion_type, item_id, action, mode, status, created_at)
                VALUES (888801, 88881, '2651442567', 'P1', 'BATCH', 'MLB1', 'enroll', 'real', 'success', '2026-10-11'),
                       (888802, 88882, '2651442567', 'P2', 'BATCH', 'MLB2', 'cancel', 'real', 'success', '2026-10-11')
            """)
            conn.commit()
        finally:
            conn.close()

        # Delete by task_ids
        res = bridge.handle_request("DELETE", "/api/tasks", {"task_ids": [88881]})
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("deleted_count"), 1)

        conn = bridge.executor._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM promo_tasks WHERE id = 88881")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT COUNT(*) FROM promo_action_results WHERE task_id = 88881")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT COUNT(*) FROM promo_tasks WHERE id = 88882")
            self.assertEqual(cur.fetchone()[0], 1)
        finally:
            conn.close()

    def test_client_incomplete_read_handling(self):
        """Verify MercadoClient handles http.client.IncompleteRead gracefully for 2xx and 4xx partials."""
        from engine.client import MercadoClient
        import http.client

        mock_auth = MagicMock()
        mock_auth.get_token.return_value = MagicMock(access_token="test_tok")
        client = MercadoClient(mock_auth)

        # 1. 200 with IncompleteRead but valid partial JSON -> succeeds
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getheader.return_value = "close"
        mock_resp.read.side_effect = http.client.IncompleteRead(b'{"status": "ok"}', expected=100)
        mock_conn.getresponse.return_value = mock_resp

        with patch.object(client._conn_pool, "get_connection", return_value=mock_conn):
            res = client.request("test_acc", "DELETE", "/test/path")
            self.assertEqual(res, {"status": "ok"})

        # 2. 400 with IncompleteRead containing pending/locked message -> parses clean error
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 400
        mock_resp.getheader.return_value = "close"
        mock_resp.read.side_effect = http.client.IncompleteRead(
            b'{"message": "Cannot delete promotion in pending status", "error": "bad_request"}',
            expected=120,
        )
        mock_conn.getresponse.return_value = mock_resp

        with patch.object(client._conn_pool, "get_connection", return_value=mock_conn):
            with self.assertRaises(RuntimeError) as cm:
                client.request("test_acc", "DELETE", "/test/path")
            self.assertIn("活动处于审核生效中，平台禁止退出", str(cm.exception))

    def test_webhook_cancel_promotion_with_readback_healing(self):
        """Verify WebhookWorker heals cancel failures if remote promotion is actually gone."""
        from engine.webhook_worker import WebhookWorker

        mock_client = MagicMock()
        # Cancel fails with IncompleteRead / network error
        mock_client.cancel_promotion_item.side_effect = RuntimeError("美客多网络请求超时或连接失败: IncompleteRead")
        # But readback shows the promo is already candidate (exited)
        mock_client.get_item_promotions.return_value = [
            {"id": "P-TEST", "type": "DEAL", "status": "candidate"}
        ]

        worker = WebhookWorker(client=mock_client)
        success, err = worker._cancel_promotion_with_readback(
            "acc1", "usr1", "MLB100", "P-TEST", "DEAL"
        )
        self.assertTrue(success)
        self.assertEqual(err, "")
        self.assertTrue(worker._is_recently_exited("MLB100", "P-TEST"))

        # If readback still shows started, it should return False and clean error
        mock_client.get_item_promotions.return_value = [
            {"id": "P-LOCKED", "type": "DEAL", "status": "started"}
        ]
        mock_client.cancel_promotion_item.side_effect = RuntimeError("美客多接口异常: 活动已锁定，平台禁止退出 (400)")
        success2, err2 = worker._cancel_promotion_with_readback(
            "acc1", "usr1", "MLB100", "P-LOCKED", "DEAL"
        )
        self.assertFalse(success2)
        self.assertIn("活动已锁定", err2)

    def test_client_409_conflict_not_retried(self) -> None:
        """Verify HTTP 409 Conflict is not retried in exponential backoff loop, preventing anti-scraping blocks."""
        from unittest.mock import MagicMock, patch
        from engine.client import MercadoClient

        mock_auth = MagicMock()
        mock_auth.get_token.return_value = MagicMock(access_token="test_token")
        client = MercadoClient(mock_auth)

        # Mock connection returning 409 response
        mock_resp = MagicMock()
        mock_resp.status = 409
        mock_resp.getheader.return_value = ""
        mock_resp.read.return_value = b'{"message": "Item conflicts with another active promotion", "error": "conflict"}'

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch.object(client._conn_pool, "get_connection", return_value=mock_conn):
            with patch("time.sleep") as mock_sleep:
                with self.assertRaises(RuntimeError) as ctx:
                    client.request("acc1", "POST", "/marketplace/seller-promotions/items/MLM123", body={})
                # sleep must NOT be called for 409 retry
                mock_sleep.assert_not_called()
                self.assertIn("409", str(ctx.exception))
                self.assertIn("conflicts", str(ctx.exception))

    def test_client_attachment_multipart_upload(self) -> None:
        """Verify client constructs multipart/form-data request for attachments."""
        from unittest.mock import MagicMock, patch
        from engine.client import MercadoClient

        mock_auth = MagicMock()
        mock_auth.get_token.return_value = MagicMock(access_token="test_token")
        client = MercadoClient(mock_auth)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.getheader.return_value = ""
        mock_resp.read.return_value = b'{"filename": "abc-123.jpg", "id": "abc-123.jpg"}'

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch.object(client._conn_pool, "get_connection", return_value=mock_conn):
            res = client.upload_claim_attachment("acc1", "5584116118", b"test_bytes", "evidence.jpg")
            self.assertEqual(res.get("filename"), "abc-123.jpg")
            self.assertTrue(mock_conn.request.called)
            req_call = mock_conn.request.call_args
            method, path = req_call[0][:2]
            headers = req_call[1]["headers"]
            body = req_call[1]["body"]
            self.assertEqual(method, "POST")
            self.assertIn("/post-purchase/v1/claims/5584116118/attachments", path)
            self.assertIn("multipart/form-data; boundary=", headers["Content-Type"])
            self.assertIn(b"evidence.jpg", body)
            self.assertIn(b"test_bytes", body)

    def test_webhook_record_auto_task_and_pop_has_new_tasks(self) -> None:
        """Verify WebhookWorker does not insert tasks into promo_tasks & promo_action_results to avoid polluting execution records."""
        import tempfile
        from pathlib import Path
        from engine.auth import AuthManager
        from engine.webhook_worker import WebhookWorker

        with tempfile.TemporaryDirectory() as td:
            db_file = Path(td) / "test-db.sqlite"
            auth = AuthManager(db_path=db_file)
            worker = WebhookWorker(auth_manager=auth)

            self.assertFalse(worker.pop_has_new_tasks())

            task_id = worker._record_auto_task(
                account_id="3408885754",
                child_user_id="3407227825",
                site_id="MLB",
                item_id="MLB5086758861",
                promotion_id="C-MLB5960233",
                promotion_type="MARKETPLACE_CAMPAIGN",
                promotion_name="95",
                action="cancel",
                success=True,
                deal_price=27.45,
            )
            self.assertIsNone(task_id)
            self.assertFalse(worker.pop_has_new_tasks())

            conn = auth._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM promo_tasks")
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT COUNT(*) FROM promo_action_results")
                self.assertEqual(cur.fetchone()[0], 0)
            finally:
                conn.close()

    def test_webhook_in_promo_deal_price_does_not_trigger_seller_data_or_cascade(self) -> None:
        """Verify WebhookWorker does NOT misidentify active promotional deal price as seller price modification,
        and does NOT cascade exit CBT sibling sites when net proceeds remain unchanged."""
        from engine.webhook_worker import WebhookWorker
        from unittest.mock import MagicMock

        worker = WebhookWorker()
        worker.client = MagicMock()
        worker._price_cache["MCO2290987455"] = 18.84
        worker._net_cache["MCO2290987455"] = 8.03
        worker._shipping_cache["MCO2290987455"] = 8.08

        # Simulated Mercado Libre response: Item currently in active promotion with deal price 17.43 and original 18.84
        worker.client.get_item_detail.return_value = {
            "id": "MCO2290987455",
            "site_id": "MCO",
            "seller_id": "3332096437",
            "status": "active",
            "price": 17.43,
            "original_price": 18.84,
            "cbt_item_id": "CBT5495629886",
            "net_proceeds": {
                "amount": 8.03,
                "additional_concepts": [
                    {"id": "shipping_cost", "amount": 8.08},
                    {"id": "sale_fee", "amount": 2.73},
                ],
                "currency_id": "USD",
            },
        }
        worker.client.request.return_value = [
            {"id": "P-MCO17969016", "name": "AON HyI", "type": "DEAL", "status": "started"}
        ]

        worker._cascade_cbt_system2_exit = MagicMock()
        res = worker._process_marketplace_item("3332096437", "3332096437", "MCO", "MCO2290987455")
        self.assertTrue(res)
        # Sibling sites must NOT be cascade cancelled!
        worker._cascade_cbt_system2_exit.assert_not_called()
        # Item itself must NOT be cancelled due to seller_data!
        worker.client.cancel_promotion_item.assert_not_called()


if __name__ == "__main__":
    unittest.main()


