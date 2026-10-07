import os
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.item_cleaner import (
    ItemCleanerEngine,
    CleanerFilterCriteria,
    ScannedItemRecord,
    clear_cleaner_draft,
    get_cleaner_draft_file,
    is_item_confirmed_deleted,
    load_cleaner_draft,
    save_cleaner_draft,
)

class TestItemCleanerEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._prev_data_dir = os.environ.get("MDM_DATA_DIR")
        try:
            cls._temp_dir_obj = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:
            cls._temp_dir_obj = tempfile.TemporaryDirectory()
        os.environ["MDM_DATA_DIR"] = cls._temp_dir_obj.name

    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        if cls._prev_data_dir is None:
            os.environ.pop("MDM_DATA_DIR", None)
        else:
            os.environ["MDM_DATA_DIR"] = cls._prev_data_dir
        if hasattr(cls, "_temp_dir_obj"):
            try:
                cls._temp_dir_obj.cleanup()
            except Exception:
                pass

    def setUp(self):
        self.mock_client = MagicMock()

        def _mock_batch(acc, ids, **kw):
            if isinstance(self.mock_client.get_items_batch.return_value, list):
                return self.mock_client.get_items_batch.return_value
            res = []
            for cid in ids:
                try:
                    d = self.mock_client.get_item_detail(acc, cid)
                    if isinstance(d, dict) and d:
                        res.append(d)
                except Exception:
                    pass
            return res
        self.mock_client.get_items_batch.side_effect = _mock_batch

        def _mock_visit(acc, iid):
            if hasattr(self.mock_client, "get_items_visits"):
                val = self.mock_client.get_items_visits.return_value
                if isinstance(val, dict):
                    return val.get(iid, 0)
            return 0
        self.mock_client.get_item_visits.side_effect = _mock_visit

        self.engine = ItemCleanerEngine(self.mock_client)

    def test_scan_and_protect_sold_items(self):
        """测试核心风控：有销售历史的商品命中不达标后，自动标记 has_sales=True 且 is_selected_for_delete=False"""
        criteria = CleanerFilterCriteria(
            account_id="3408885754",
            enable_visits_filter=True,
            visits_mode="total",
            visits_is_zero_only=True,
            enable_score_filter=True,
            score_threshold=60,
        )

        # 模拟店铺有两个商品：item1 无销量, item2 有销量已出单 5 件
        self.mock_client.search_user_items.return_value = {
            "results": ["MLB111", "MLB222"],
            "paging": {"total": 2},
        }
        self.mock_client.get_items_visits.return_value = {
            "MLB111": 0,
            "MLB222": 0,
        }
        self.mock_client.get_item_detail.side_effect = lambda acc, item_id: {
            "MLB111": {
                "id": "MLB111", "status": "active", "sold_quantity": 0,
                "title": "Unsold Item", "site_id": "MLB", "pictures": [{"size": "500x500"}]
            },
            "MLB222": {
                "id": "MLB222", "status": "active", "sold_quantity": 5,
                "title": "Sold Item (Protected)", "site_id": "MLB", "pictures": [{"size": "500x500"}]
            },
        }[item_id]

        self.mock_client.get_item_performance.return_value = {
            "score": 45,
            "level_wording": "Basic",
        }

        matched = self.engine.scan_shop_items(criteria)

        self.assertEqual(len(matched), 2)
        
        # item1: 无销量，默认勾选待删除
        item1 = next(r for r in matched if r.item_id == "MLB111")
        self.assertFalse(item1.has_sales)
        self.assertTrue(item1.is_selected_for_delete)
        self.assertIn("0 浏览量", item1.unmet_reasons)
        self.assertIn("刊登评分 45分 < 60分", item1.unmet_reasons)

        # item2: 有销量，核心风控触发，强制保护不勾选待删除！
        item2 = next(r for r in matched if r.item_id == "MLB222")
        self.assertTrue(item2.has_sales)
        self.assertFalse(item2.is_selected_for_delete)
        self.assertEqual(item2.sold_quantity, 5)

    def test_policy_violation_filter(self):
        """测试违规失效品过滤 (如 waiting_for_patch / suspended)"""
        criteria = CleanerFilterCriteria(
            account_id="3408885754",
            enable_policy_filter=True,
        )

        self.mock_client.search_user_items.return_value = {
            "results": ["MLB333"],
            "paging": {"total": 1},
        }
        self.mock_client.get_item_detail.return_value = {
            "id": "MLB333",
            "status": "under_review",
            "sub_status": ["waiting_for_patch"],
            "sold_quantity": 0,
            "title": "Policy Violated Item",
            "site_id": "MLB",
        }

        matched = self.engine.scan_shop_items(criteria)
        self.assertEqual(len(matched), 1)
        self.assertTrue(any("waiting_for_patch" in r for r in matched[0].unmet_reasons))

    def test_policy_violation_with_sales_not_protected(self):
        """测试已明确被平台下架/政策失效的商品，即使历史出过单，也不再触发保护，默认勾选待删除"""
        criteria = CleanerFilterCriteria(
            account_id="3408885754",
            enable_policy_filter=True,
        )

        self.mock_client.search_user_items.return_value = {
            "results": ["MLB_POLICY_SALES_999"],
            "paging": {"total": 1},
        }
        self.mock_client.get_item_detail.return_value = {
            "id": "MLB_POLICY_SALES_999",
            "status": "closed",
            "sub_status": ["forbidden"],
            "sold_quantity": 3,
            "title": "Banned Item with Past Sales",
            "site_id": "MLB",
        }

        matched = self.engine.scan_shop_items(criteria)
        self.assertEqual(len(matched), 1)
        item = matched[0]
        self.assertTrue(item.has_sales)
        self.assertEqual(item.sold_quantity, 3)
        self.assertTrue(item.is_selected_for_delete)

    def test_execute_batch_delete(self):
        """测试 CBT 跨境自发货全局下架删除执行流程"""
        self.mock_client.delete_item.return_value = {"deleted": True}

        logs = []
        res = self.engine.execute_batch_delete(
            account_id="3408885754",
            item_ids=["MLB111", "MLB222"],
            on_log=lambda msg: logs.append(msg),
        )

        self.assertEqual(res["total"], 2)
        self.assertEqual(res["success_count"], 2)
        self.assertEqual(res["failed_count"], 0)
        self.assertEqual(self.mock_client.delete_item.call_count, 2)
        self.assertTrue(any("已成功从美客多平台下架删除" in log for log in logs))

    def test_execute_batch_delete_cbt(self):
        """测试 CBT 跨境商品直接通过 delete_item 走 global 专用通道"""
        self.mock_client.delete_item.return_value = {"status": "inactive"}
        logs = []
        res = self.engine.execute_batch_delete(
            account_id="2651442567",
            item_ids=["CBT111", "CBT222"],
            on_log=lambda msg: logs.append(msg),
        )
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["success_count"], 2)
        self.assertEqual(self.mock_client.delete_item.call_count, 2)
        self.assertTrue(any("CBT 跨境自发货全局下架删除" in log for log in logs))

    def test_filter_mode_and_vs_or(self):
        """测试 AND 与 OR 判定组合模式"""
        # 商品只命中低分 (30分)，但有浏览量 (10次)
        self.mock_client.search_user_items.return_value = {
            "results": ["MLB888"],
            "paging": {"total": 1},
        }
        self.mock_client.get_items_visits.return_value = {"MLB888": 10}
        self.mock_client.get_item_detail.return_value = {
            "id": "MLB888", "status": "active", "sold_quantity": 0, "title": "Test Item", "site_id": "MLB"
        }
        self.mock_client.get_item_performance.return_value = {"score": 30}

        # 模式 1: AND 模式 (要求同时满足 0浏览量 且 评分<60)，因为浏览量为10，不满足0浏览量，所以不应命中！
        crit_and = CleanerFilterCriteria(
            account_id="3408885754",
            filter_mode="and",
            enable_visits_filter=True,
            visits_is_zero_only=True,
            enable_score_filter=True,
            score_threshold=60,
        )
        matched_and = self.engine.scan_shop_items(crit_and)
        self.assertEqual(len(matched_and), 0)

        # 模式 2: OR 模式 (满足任一条件即可)，评分<60 命中，所以应当命中！
        crit_or = CleanerFilterCriteria(
            account_id="3408885754",
            filter_mode="or",
            enable_visits_filter=True,
            visits_is_zero_only=True,
            enable_score_filter=True,
            score_threshold=60,
        )
        matched_or = self.engine.scan_shop_items(crit_or)
        self.assertEqual(len(matched_or), 1)
        self.assertEqual(matched_or[0].item_id, "MLB888")

    def test_draft_persistence_and_auto_burn(self):
        """测试草稿暂存、恢复加载与自动即焚机制"""
        clear_cleaner_draft()
        self.assertEqual(load_cleaner_draft(), [])

        rec1 = ScannedItemRecord(
            item_id="CBT1001",
            account_id="ACC1",
            site_id="MLM",
            title="Sample CBT 1",
            status="active",
            store_name="Store 1",
            visits=0,
            has_sales=False,
            is_selected_for_delete=True,
        )
        rec2 = ScannedItemRecord(
            item_id="CBT1002",
            account_id="ACC1",
            site_id="MLB",
            title="Sample CBT 2 (Sold)",
            status="active",
            store_name="Store 1",
            visits=5,
            has_sales=True,
            is_selected_for_delete=False,
        )

        save_cleaner_draft([rec1, rec2])
        self.assertTrue(get_cleaner_draft_file().exists())

        loaded = load_cleaner_draft()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].item_id, "CBT1001")
        self.assertTrue(loaded[0].is_selected_for_delete)
        self.assertEqual(loaded[1].item_id, "CBT1002")
        self.assertTrue(loaded[1].has_sales)
        self.assertFalse(loaded[1].is_selected_for_delete)

        # 模拟即焚销毁
        clear_cleaner_draft()
        self.assertFalse(get_cleaner_draft_file().exists())
        self.assertEqual(load_cleaner_draft(), [])

    def test_grace_period_protection(self):
        """测试新品冷启动保护期：上架未满 7 天的新品自动豁免，绝不误下架"""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        two_days_ago = (now - datetime.timedelta(days=2)).isoformat()
        thirty_days_ago = (now - datetime.timedelta(days=30)).isoformat()

        criteria = CleanerFilterCriteria(
            account_id="3408885754",
            filter_mode="and",
            enable_visits_filter=True,
            visits_is_zero_only=True,
            enable_score_filter=True,
            score_threshold=60,
            enable_grace_period=True,
            grace_period_days=7,
        )

        # 两个商品均是 0 浏览、评分 40 分：
        # NEW_ITEM 上架仅 2 天 (在保护期内)
        # OLD_ITEM 上架 30 天 (已过保护期)
        self.mock_client.search_user_items.return_value = {
            "results": ["NEW_ITEM", "OLD_ITEM"],
            "paging": {"total": 2},
        }
        self.mock_client.get_items_visits.return_value = {
            "NEW_ITEM": 0,
            "OLD_ITEM": 0,
        }
        self.mock_client.get_items_batch.return_value = [
            {
                "id": "NEW_ITEM",
                "title": "Fresh New Listing",
                "status": "active",
                "sold_quantity": 0,
                "site_id": "MLM",
                "date_created": two_days_ago,
            },
            {
                "id": "OLD_ITEM",
                "title": "Old Dead Listing",
                "status": "active",
                "sold_quantity": 0,
                "site_id": "MLM",
                "date_created": thirty_days_ago,
            },
        ]
        self.mock_client.get_item_performance.return_value = {
            "score": 40,
            "level_wording": "Basic",
        }

        matched = self.engine.scan_shop_items(criteria)

        # NEW_ITEM 被自动豁免，只有 OLD_ITEM 被判定为待清理
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].item_id, "OLD_ITEM")
        self.assertIn("0 浏览量", matched[0].unmet_reasons)
        self.assertIn("刊登评分 40分 < 60分", matched[0].unmet_reasons)

    def test_rate_limiter_and_sqlite_cache(self):
        """测试本地 SQLite 评分持久化沉淀与复用"""
        from engine.item_cleaner import RateLimiter, get_cached_score, save_cached_scores
        rl = RateLimiter(max_qps=50.0)
        rl.acquire()

        save_cached_scores([("TEST_CACHE_ITEM_1", 88, "Professional")])
        res = get_cached_score("TEST_CACHE_ITEM_1")
        self.assertIsNotNone(res)
        self.assertEqual(res[0], 88)
    def test_cbt_items_bypass_score_checking(self):
        """测试 CBT 跨境商品由于官方不支持评分，自动免除单品查分并基于 0 浏览成功匹配"""
        criteria = CleanerFilterCriteria(
            account_id="3332096437",
            enable_visits_filter=True,
            visits_mode="total",
            visits_is_zero_only=True,
            enable_score_filter=True,
            score_threshold=60,
            filter_mode="and",
        )
        self.mock_client.search_user_items.return_value = {
            "results": ["CBT123456789"],
            "paging": {"total": 1},
        }
        self.mock_client.get_items_visits.return_value = {"CBT123456789": 0}
        self.mock_client.get_items_batch.return_value = [
            {
                "id": "CBT123456789",
                "title": "CBT Cross Border Item",
                "status": "active",
                "sold_quantity": 0,
                "site_id": "CBT",
                "date_created": "2025-01-01T00:00:00.000Z",
            }
        ]
        matched = self.engine.scan_shop_items(criteria)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].item_id, "CBT123456789")
        self.assertIn("0 浏览量", matched[0].unmet_reasons)
        self.mock_client.get_item_performance.assert_not_called()

    def test_child_sites_policy_violations_and_scores(self):
        """测试分站点子店（巴西MLB、墨西哥MLM）政策违规拉取、评分核查与店铺别名透出"""
        self.mock_client.auth = MagicMock()
        self.mock_client.auth.list_sites.return_value = [
            {"account_id": "2651442567", "child_user_id": "2668031897", "site_id": "MLB"},
            {"account_id": "2651442567", "child_user_id": "2668033839", "site_id": "MLM"},
        ]

        # 模拟分站点违规查询：MLB 下有 1 件 forbidden、1 件 waiting_for_patch 和 1 件 pending_documentation
        def _mock_search_mp(acc, cuid, sub_status=None, status=None, **kw):
            if cuid == "2668031897" and sub_status == "forbidden":
                return {"results": ["MLB777001"], "paging": {"total": 1}}
            if cuid == "2668031897" and sub_status == "waiting_for_patch":
                return {"results": ["MLB777002"], "paging": {"total": 1}}
            if cuid == "2668031897" and sub_status == "pending_documentation":
                return {"results": ["MLB777003"], "paging": {"total": 1}}
            return {"results": [], "paging": {"total": 0}}

        self.mock_client.search_marketplace_items.side_effect = _mock_search_mp
        self.mock_client.search_user_items.return_value = {"results": [], "paging": {"total": 0}}
        self.mock_client.get_items_batch.return_value = []
        self.mock_client.get_item_detail.side_effect = lambda acc, item_id: {
            "MLB777001": {
                "id": "MLB777001", "status": "under_review", "sub_status": ["forbidden"],
                "sold_quantity": 0, "title": "Banned Brazilian Item", "site_id": "MLB"
            },
            "MLB777002": {
                "id": "MLB777002", "status": "active", "sub_status": ["waiting_for_patch"],
                "sold_quantity": 0, "title": "Patch Required Item", "site_id": "MLB"
            },
            "MLB777003": {
                "id": "MLB777003", "status": "under_review", "sub_status": ["pending_documentation"],
                "sold_quantity": 0, "title": "Doc Pending Item", "site_id": "MLB"
            },
        }.get(item_id, {})

        criteria = CleanerFilterCriteria(
            account_id="2651442567",
            store_name="湖北店",
            enable_policy_filter=True,
            enable_visits_filter=False,
            enable_score_filter=False,
        )

        logs = []
        matched = self.engine.scan_shop_items(criteria, on_log=lambda m: logs.append(m))

        self.assertEqual(len(matched), 3)
        rec_forbidden = next(r for r in matched if r.item_id == "MLB777001")
        self.assertEqual(rec_forbidden.site_id, "MLB")
        self.assertEqual(rec_forbidden.status, "政策失效")
        self.assertTrue(any("forbidden" in reason for reason in rec_forbidden.unmet_reasons))

        rec_patch = next(r for r in matched if r.item_id == "MLB777002")
        self.assertEqual(rec_patch.site_id, "MLB")
        self.assertEqual(rec_patch.status, "待整改")
        self.assertTrue(any("waiting_for_patch" in reason for reason in rec_patch.unmet_reasons))

        rec_doc = next(r for r in matched if r.item_id == "MLB777003")
        self.assertEqual(rec_doc.site_id, "MLB")
        self.assertEqual(rec_doc.status, "待补文件")
        self.assertTrue(any("pending_documentation" in reason for reason in rec_doc.unmet_reasons))

        # 验证别名透出与日志前缀严格规范
        self.assertTrue(any("【湖北店】" in log for log in logs))
        self.assertTrue(all(not log.endswith("...") for log in logs))
        self.assertTrue(all("[商品扫描]" in log for log in logs if log.startswith("[")))

    def test_client_delete_item_404_idempotent(self):
        """测试 CBT 删除接口遇到 404 not a cbt item 时幂等返回已删除状态，不抛出异常"""
        from engine.client import MercadoClient
        mock_auth = MagicMock()
        client = MercadoClient(mock_auth)

        # 模拟官方 404 错误
        client.request = MagicMock(side_effect=RuntimeError("美客多 API 报错 (404): item MCO4200590930 is not a cbt item"))

        res = client.delete_item("3408885754", "MCO4200590930")
        self.assertIsInstance(res, dict)
        self.assertTrue(res.get("already_deleted"))
        self.assertEqual(res.get("status"), "paused")
        self.assertIn("deleted", res.get("sub_status", []))

    def test_client_delete_item_active_two_phase_transition(self):
        """测试在售商品遇到 (deleted is not modifiable) 拦截时自动执行先 paused 再 deleted 的两阶段流转"""
        from engine.client import MercadoClient
        mock_auth = MagicMock()
        client = MercadoClient(mock_auth)

        # 第一次调用报 400 deleted is not modifiable，后续调用依次成功
        calls = []
        def fake_request(account_id, method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if len(calls) == 1:
                raise RuntimeError("美客多 API 报错 (400): Cannot update item MLM3235218275 [status:active, has_bids:false] (deleted is not modifiable.)")
            if len(calls) == 2:
                return {"id": "MLM3235218275", "status": "paused"}
            return {"id": "MLM3235218275", "deleted": True}

        client.request = MagicMock(side_effect=fake_request)
        res = client.delete_item("3332096437", "MLM3235218275")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0], ("PUT", "/global/items/MLM3235218275", {"deleted": True}))
        self.assertEqual(calls[1], ("PUT", "/global/items/MLM3235218275", {"status": "paused"}))
        self.assertEqual(calls[2], ("PUT", "/global/items/MLM3235218275", {"deleted": True}))
        self.assertTrue(res.get("deleted"))

    def test_batch_delete_handles_already_deleted_as_success(self):
        """测试批量删除流程中，对于已在平台删除的商品（already_deleted）自动判定成功并记录日志"""
        self.mock_client.delete_item.return_value = {
            "id": "MCO4200590930",
            "status": "inactive",
            "sub_status": ["forbidden", "deleted"],
            "already_deleted": True,
        }

        logs = []
        result = self.engine.execute_batch_delete(
            account_id="3408885754",
            store_name="测试店",
            item_ids=["MCO4200590930"],
            on_log=lambda m: logs.append(m),
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertTrue(any("已是彻底下架/删除状态，已自动同步" in log for log in logs))

    def test_scan_shop_items_filters_out_deleted_sub_status(self):
        """测试扫描时不纳管 sub_status 包含 deleted 的已彻底注销死品"""
        self.mock_client.auth = MagicMock()
        self.mock_client.auth.list_sites.return_value = []
        self.mock_client.search_user_items.return_value = {
            "results": ["MLB100001", "MLB100002"],
            "paging": {"total": 2},
        }
        self.mock_client.get_items_batch.return_value = []
        self.mock_client.get_item_detail.side_effect = lambda acc, item_id: {
            "MLB100001": {
                "id": "MLB100001", "status": "under_review", "sub_status": ["forbidden"],
                "sold_quantity": 0, "title": "Banned Active Item", "site_id": "MLB"
            },
            "MLB100002": {
                "id": "MLB100002", "status": "inactive", "sub_status": ["forbidden", "deleted"],
                "sold_quantity": 0, "title": "Already Deleted Item", "site_id": "MLB"
            },
        }.get(item_id, {})

        criteria = CleanerFilterCriteria(
            account_id="3408885754",
            enable_policy_filter=True,
            enable_visits_filter=False,
            enable_score_filter=False,
        )

        matched = self.engine.scan_shop_items(criteria)
        # MLB100002 包含 deleted，必须被过滤，只有 MLB100001 会被纳入
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].item_id, "MLB100001")

    def test_is_item_confirmed_deleted(self):
        """测试 100% 确认已删除判定的 3 个硬边界条件"""
        rec_del = ScannedItemRecord(item_id="1", account_id="a", site_id="MLB", title="t", status="已删除")
        self.assertTrue(is_item_confirmed_deleted(rec_del))

        rec_sub = ScannedItemRecord(item_id="2", account_id="a", site_id="MLB", title="t", status="inactive", sub_status=["forbidden", "deleted"])
        self.assertTrue(is_item_confirmed_deleted(rec_sub))

        rec_404 = ScannedItemRecord(
            item_id="3", account_id="a", site_id="MLB", title="t",
            status="删除失败: 美客多 API 报错 (404): item MCO2058565081 is not a cbt item"
        )
        self.assertTrue(is_item_confirmed_deleted(rec_404))

        # 未确认删除的在售/违规商品，严禁误判为已删除
        rec_review = ScannedItemRecord(item_id="4", account_id="a", site_id="MLB", title="t", status="under_review", sub_status=["forbidden"])
        self.assertFalse(is_item_confirmed_deleted(rec_review))

        rec_active = ScannedItemRecord(item_id="5", account_id="a", site_id="MLB", title="t", status="active", sub_status=[])
        self.assertFalse(is_item_confirmed_deleted(rec_active))

    def test_load_cleaner_draft_normalizes_and_unselects_deleted_items(self):
        """测试加载草稿时自动将 404 记录标准化为已删除，且不默认勾选待删除"""
        rec_404 = ScannedItemRecord(
            item_id="MCO404", account_id="acc", site_id="MCO", title="Title 1",
            status="删除失败: 美客多 API 报错 (404): item MCO404 is not a cbt item",
            is_selected_for_delete=True,
        )
        rec_active = ScannedItemRecord(
            item_id="MLB200", account_id="acc", site_id="MLB", title="Title 2",
            status="active",
            is_selected_for_delete=True,
        )
        save_cleaner_draft([rec_404, rec_active])

        loaded = load_cleaner_draft()
        self.assertEqual(len(loaded), 2)

        r_404 = next(r for r in loaded if r.item_id == "MCO404")
        self.assertEqual(r_404.status, "已删除")
        self.assertIn("deleted", r_404.sub_status)
        self.assertFalse(r_404.is_selected_for_delete)  # 已确认删除的不应勾选

        r_active = next(r for r in loaded if r.item_id == "MLB200")
        self.assertEqual(r_active.status, "active")
        self.assertTrue(r_active.is_selected_for_delete)  # 真正待删除的正常勾选

    def test_selected_site_ids_filtering(self):
        """测试指定站点多选过滤：仅扫描和保留选定站点的商品，排除未勾选站点"""
        self.mock_client.auth = MagicMock()
        self.mock_client.auth.list_sites.return_value = [
            {"account_id": "2651442567", "child_user_id": "2668031897", "site_id": "MLB"},
            {"account_id": "2651442567", "child_user_id": "2668033839", "site_id": "MLM"},
        ]

        self.mock_client.search_user_items.return_value = {
            "results": ["MLB101", "MLM202"],
            "paging": {"total": 2},
        }
        self.mock_client.get_items_batch.return_value = [
            {
                "id": "MLB101",
                "title": "巴西在售品",
                "status": "active",
                "sold_quantity": 0,
                "site_id": "MLB",
                "date_created": "2025-01-01T00:00:00.000Z",
            },
            {
                "id": "MLM202",
                "title": "墨西哥在售品",
                "status": "active",
                "sold_quantity": 0,
                "site_id": "MLM",
                "date_created": "2025-01-01T00:00:00.000Z",
            },
        ]
        self.mock_client.get_items_visits.return_value = {"MLB101": 0, "MLM202": 0}

        # 仅选择 MLM 站点
        criteria = CleanerFilterCriteria(
            account_id="2651442567",
            store_name="多站点店铺",
            selected_site_ids=["MLM"],
            enable_visits_filter=True,
            visits_mode="total",
            visits_is_zero_only=True,
            enable_score_filter=False,
            enable_policy_filter=False,
        )

        matched = self.engine.scan_shop_items(criteria)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].item_id, "MLM202")
        self.assertEqual(matched[0].site_id, "MLM")


if __name__ == "__main__":
    unittest.main()



