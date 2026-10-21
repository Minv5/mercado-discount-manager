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
from PySide6.QtWidgets import QApplication, QLabel, QWidget, QMenu, QFrame, QSizePolicy, QListView

from app import create_application
from service_center.models import (
    SITE_FLAGS,
    SITE_LANGUAGES,
    SITE_NAMES,
    ServiceMessage,
    ServiceSession,
    resolve_site_language,
)
from service_center.template_manager import ReplyTemplate, TemplateManager
from service_center.toast_notification import ToastNotification
from service_center.translator import translate_text, _TRANSLATION_CACHE
from service_center.service_center_page import (
    FetchSessionsWorker,
    SendReplyWorker,
    ServiceCenterPage,
)


class TestServiceCenterModels(unittest.TestCase):
    def test_site_mappings(self):
        self.assertEqual(SITE_LANGUAGES.get("MLB"), "pt")
        self.assertEqual(SITE_LANGUAGES.get("MLM"), "es")
        self.assertEqual(SITE_LANGUAGES.get("MLC"), "es")
        self.assertEqual(SITE_LANGUAGES.get("MCO"), "es")
        self.assertEqual(resolve_site_language("MLB"), "pt")
        self.assertEqual(resolve_site_language("MLM"), "es")
        self.assertEqual(resolve_site_language("UNKNOWN"), "es")

        self.assertIn("🇧🇷", SITE_FLAGS.get("MLB", ""))
        self.assertIn("🇲🇽", SITE_FLAGS.get("MLM", ""))

    def test_service_message_and_session(self):
        msg = ServiceMessage(
            id="m1",
            sender_role="buyer",
            sender_name="买家小张",
            text="Tem pronta entrega?",
            text_translated="有现货吗？",
            created_at="2026-10-14T10:00:00Z",
            is_seller=False,
        )
        self.assertEqual(msg.id, "m1")
        self.assertFalse(msg.is_seller)
        self.assertEqual(msg.text_translated, "有现货吗？")

        session = ServiceSession(
            id="q_12345",
            session_type="question",
            account_id="acc_001",
            account_name="官方旗舰店",
            site_id="MLB",
            buyer_id="b_999",
            buyer_nickname="Buyer999",
            item_id="MLB12345678",
            item_title="蓝牙耳机",
            status="unanswered",
            created_at="2026-10-14T10:00:00Z",
            summary="Tem pronta entrega?",
            summary_translated="有现货吗？",
            can_reply=True,
            is_unread=True,
            messages=[msg],
        )
        self.assertEqual(session.id, "q_12345")
        self.assertEqual(session.session_type, "question")
        self.assertTrue(session.can_reply)
        self.assertEqual(len(session.messages), 1)


class TestServiceCenterTranslator(unittest.TestCase):
    def setUp(self):
        _TRANSLATION_CACHE.clear()

    def test_cached_translation(self):
        _TRANSLATION_CACHE[("你好", "auto", "pt")] = "Olá"
        result = translate_text("你好", target_lang="pt")
        self.assertEqual(result, "Olá")

    @patch("urllib.request.urlopen")
    def test_google_translate_mock(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'[[["Hola amigo","\xe4\xbd\xa0\xe5\xa5\xbd\xe6\x9c\x8b\xe5\x8f\x8b",null,null,10]],null,"zh-CN"]'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = translate_text("你好朋友", target_lang="es")
        self.assertEqual(res, "Hola amigo")
        self.assertEqual(_TRANSLATION_CACHE.get(("你好朋友", "auto", "es")), "Hola amigo")

    @patch("urllib.request.urlopen", side_effect=Exception("Network error"))
    def test_translate_failure_fallback(self, mock_urlopen):
        res = translate_text("网络故障兜底", target_lang="es")
        self.assertEqual(res, "网络故障兜底")


class TestServiceCenterTemplateManager(unittest.TestCase):
    def test_default_templates(self):
        tmp_path = "/tmp/test_mdm_templates.json"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        mgr = TemplateManager(storage_path=tmp_path)
        try:
            templates = mgr.list_templates()
            self.assertGreaterEqual(len(templates), 4)

            pre_templates = mgr.list_templates(category="pre_sale")
            self.assertGreaterEqual(len(pre_templates), 2)
            c_templates = mgr.list_templates(category="claim")
            self.assertGreaterEqual(len(c_templates), 1)

            new_tmpl = ReplyTemplate(
                id="t_custom_01",
                title="测试模板",
                category="pre_sale",
                text_zh="亲，产品质保一年。",
                text_es="Hola, nuestro producto tiene garantía de 1 año.",
                text_pt="Olá, nosso produto tem garantia de 1 ano.",
            )
            mgr.add_template(new_tmpl)
            self.assertEqual(new_tmpl.title, "测试模板")
            self.assertIn(new_tmpl, mgr.list_templates())

            mgr.remove_template(new_tmpl.id)
            self.assertNotIn(new_tmpl, mgr.list_templates())
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestToastNotification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application()

    def test_toast_creation_and_signals(self):
        parent = QWidget()
        parent.resize(800, 600)
        toast = ToastNotification(
            parent=parent,
            title="测试提醒",
            message="收到来自买家的提问",
            store_name="巴西一店",
            site_name="MLB",
            session_id="q_1001",
            session_type="question",
            duration_ms=0,
        )
        self.assertEqual(toast.session_id, "q_1001")
        self.assertEqual(toast.session_type, "question")

        parent.show()
        toast.show_at_corner(parent)
        self.assertTrue(toast.isVisible())

        clicked_args = []
        toast.clicked.connect(lambda sid, stype: clicked_args.append((sid, stype)))
        toast._on_click()
        self.assertEqual(clicked_args, [("q_1001", "question")])


class TestServiceCenterPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application()

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_auth = MagicMock()
        self.mock_auth.list_accounts.return_value = [
            {"account_id": "acc_1", "display_name": "店铺A"},
            {"account_id": "acc_2", "display_name": "店铺B"},
        ]
        self.snapshot_patcher = patch("service_center.service_center_page.load_sessions_snapshot", return_value=[])
        self.save_patcher = patch("service_center.service_center_page.save_sessions_snapshot")
        self.mock_load_snapshot = self.snapshot_patcher.start()
        self.mock_save_snapshot = self.save_patcher.start()
        self.page = ServiceCenterPage(self.mock_client, self.mock_auth)

    def tearDown(self):
        self.snapshot_patcher.stop()
        self.save_patcher.stop()
        if hasattr(self.page, "worker") and self.page.worker and self.page.worker.isRunning():
            self.page.worker.wait(2000)

    def test_page_initial_ui(self):
        self.assertEqual(self.page.store_combo.count(), 3)
        self.assertEqual(self.page.site_combo.count(), 7)
        self.assertEqual(self.page._active_tab, "question")
        self.assertFalse(self.page.reply_dock.isEnabled())

    def test_switch_tab(self):
        self.assertTrue(self.page.session_list.uniformItemSizes())
        self.page._switch_tab("claim")
        self.assertEqual(self.page._active_tab, "claim")
        self.assertTrue(self.page.tab_btn_claims.isChecked())
        self.assertFalse(self.page.tab_btn_questions.isChecked())

        # Redundant switch to same tab should return early without re-clearing if items exist
        with patch.object(self.page, "_apply_filter") as mock_filter:
            self.page._switch_tab("claim")
            if self.page.session_list.count() > 0:
                mock_filter.assert_not_called()

        self.page._switch_tab("message")
        self.assertEqual(self.page._active_tab, "message")
        self.assertTrue(self.page.tab_btn_messages.isChecked())

    def test_refresh_accounts(self):
        self.mock_auth.list_accounts.return_value = [
            {"account_id": "acc_1", "display_name": "店铺A"},
            {"account_id": "acc_2", "display_name": "店铺B"},
            {"account_id": "acc_3", "display_name": "新店铺C"},
        ]
        self.page.refresh_accounts()
        self.assertEqual(self.page.store_combo.count(), 4)

    def test_load_sessions_and_unread_count(self):
        unread_counts = []
        self.page.unreadCountChanged.connect(lambda c: unread_counts.append(c))

        s1 = ServiceSession(
            id="q_1",
            session_type="question",
            account_id="acc_1",
            account_name="店铺A",
            site_id="MLB",
            buyer_id="b1",
            buyer_nickname="Buyer1",
            item_id="MLB111",
            item_title="商品1",
            status="unanswered",
            created_at="2026-10-14T10:00:00Z",
            summary="Olá, tem estoque?",
            summary_translated="你好，有库存吗？",
            can_reply=True,
            is_unread=True,
        )
        s2 = ServiceSession(
            id="c_1",
            session_type="claim",
            account_id="acc_1",
            account_name="店铺A",
            site_id="MLM",
            buyer_id="b2",
            buyer_nickname="Buyer2",
            item_id="MLM222",
            item_title="商品2",
            status="opened",
            created_at="2026-10-14T09:00:00Z",
            summary="El paquete no llegó",
            summary_translated="包裹未收到",
            can_reply=True,
            is_unread=True,
        )
        self.page._on_sessions_loaded([s1, s2])

        self.assertEqual(self.page.session_list.count(), 1)
        self.assertEqual(unread_counts[-1], 2)
        self.assertIn("1", self.page.tab_btn_questions.text())
        self.assertIn("1", self.page.tab_btn_claims.text())

    def test_incoming_webhook_event(self):
        event = {
            "topic": "questions",
            "resource": "/questions/99887766",
            "user_id": "acc_1",
            "application_id": "app_test",
        }
        with patch.object(self.page, "reload_sessions") as mock_reload:
            self.page.handle_incoming_webhook_event(event)
            mock_reload.assert_called_once()

    def test_product_link_and_browser_open(self):
        s = ServiceSession(
            id="q_br",
            session_type="question",
            account_id="acc_1",
            account_name="店铺A",
            site_id="MLB",
            buyer_id="b1",
            buyer_nickname="Buyer1",
            item_id="MLB99887766",
            item_title="耳机降噪",
            can_reply=True,
        )
        self.assertEqual(s.item_url, "https://produto.mercadolivre.com.br/MLB-99887766")

        self.page._display_session_detail(s)
        self.assertTrue(self.page.context_link_lbl.openExternalLinks())
        self.assertTrue(self.page.open_browser_btn.isEnabled())
        self.assertIn("MLB99887766", self.page.context_link_lbl.text())

        with patch("PySide6.QtGui.QDesktopServices.openUrl") as mock_open:
            mock_open.return_value = True
            self.page._open_item_in_browser()
            mock_open.assert_called_once()
            called_url = mock_open.call_args[0][0].toString()
            self.assertEqual(called_url, "https://produto.mercadolivre.com.br/MLB-99887766")

    def test_reload_sessions_guard_prevents_duplicate_worker(self):
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        self.page.worker = mock_worker

        with patch("service_center.service_center_page.FetchSessionsWorker") as mock_cls:
            self.page.reload_sessions()
            mock_cls.assert_not_called()

    def test_page_stop_terminates_threads(self):
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        self.page.worker = mock_worker
        self.page.stop()
        mock_worker.quit.assert_called_once()
        mock_worker.wait.assert_called_once()

    def test_clean_message_text_and_format_date(self):
        from service_center.service_center_page import clean_message_text, format_message_date

        html_sample = '<p dir="ltr"><span style="white-space: pre-wrap;">Olá!&nbsp;Tudo bem?</span></p><br/>Segunda linha.'
        clean = clean_message_text(html_sample)
        self.assertEqual(clean, "Olá! Tudo bem?\nSegunda linha.")

        # Test dictionary message date from MercadoLibre (converted to Beijing time UTC+8)
        dict_date = {"created": "2026-10-07T18:41:52.123-04:00", "received": "2026-10-07T18:41:52Z"}
        self.assertEqual(format_message_date(dict_date), "2026-10-08 06:41:52")

        # Test string ISO date (converted to Beijing time UTC+8)
        iso_date = "2026-09-28T01:10:18.346-04:00"
        self.assertEqual(format_message_date(iso_date), "2026-09-28 13:10:18")

    def test_on_messages_loaded_backfills_buyer_and_sorts(self):
        s = ServiceSession(
            id="c_558",
            session_type="claim",
            account_id="acc_1",
            account_name="店铺A",
            site_id="MLB",
            buyer_id="",
            buyer_nickname="",
            order_id="ord_1",
            status="opened",
            summary="索赔摘要",
            can_reply=True,
        )
        self.page._display_session_detail(s)
        self.assertEqual(len(s.messages), 0)

        # Simulate messages loaded with detected buyer name
        msg1 = ServiceMessage(
            id="m1",
            sender_role="buyer",
            sender_name="买家 123456",
            text="商品有问题",
            created_at="2026-10-07 15:00:00",
            is_seller=False,
        )
        msg2 = ServiceMessage(
            id="m2",
            sender_role="seller",
            sender_name="卖家",
            text="您好，请发图片",
            created_at="2026-10-07 15:10:00",
            is_seller=True,
        )
        self.page._on_messages_loaded("c_558", [msg1, msg2], detected_buyer_name="买家 123456")
        self.assertEqual(s.buyer_nickname, "买家 123456")
        self.assertEqual(len(s.messages), 2)
        self.assertIn("买家: <b>买家 123456</b>", self.page.context_meta_lbl.text())

    def test_fetch_conversation_messages_worker_claim(self):
        from service_center.service_center_page import FetchConversationMessagesWorker

        mock_client = MagicMock()
        mock_client.get_claim_messages.return_value = [
            {"id": "2", "sender_role": "mediator", "message": "<p>调解说明</p>", "date_created": "2026-10-07T12:00:00Z"},
            {"id": "1", "sender_role": "complainant", "message": "买家反馈", "date_created": "2026-10-07T10:00:00Z"},
        ]
        s = ServiceSession(
            id="c_999",
            session_type="claim",
            account_id="acc_1",
            account_name="广东店",
            site_id="MLB",
        )
        worker = FetchConversationMessagesWorker(mock_client, s)
        results = []
        worker.finished.connect(lambda sid, msgs, bname: results.append((sid, msgs, bname)))
        worker.run()

        self.assertEqual(len(results), 1)
        sid, msgs, bname = results[0]
        self.assertEqual(sid, "c_999")
        self.assertEqual(len(msgs), 2)
        # Verify chronological reverse
        self.assertEqual(msgs[0].id, "1")
        self.assertEqual(msgs[0].text, "买家反馈")
        self.assertEqual(msgs[1].id, "2")
        self.assertEqual(msgs[1].text, "调解说明")

    def test_batch_item_enrichment_per_account(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_sites.return_value = []
        mock_client.get_questions.return_value = {"questions": [
            {"id": "q1", "item_id": "MLB111", "text": "咨询1", "date_created": "2026-10-14T01:00:00Z"},
            {"id": "q2", "item_id": "MLM222", "text": "咨询2", "date_created": "2026-10-14T02:00:00Z"},
        ]}

        def _mock_batch(aid, item_ids):
            if aid == "acc_1":
                return [{"id": "MLB111", "secure_thumbnail": "https://img.ml/1.jpg", "title": "商品1"}]
            elif aid == "acc_2":
                return [{"id": "MLM222", "secure_thumbnail": "https://img.ml/2.jpg", "title": "商品2"}]
            return []

        mock_client.get_items_batch.side_effect = _mock_batch

        worker = FetchSessionsWorker(mock_client, mock_auth, ["acc_1", "acc_2"], "question")
        results = []
        worker.finished.connect(lambda sessions: results.extend(sessions))
        worker.run()

        self.assertEqual(len(results), 4)  # 2 questions per account in mocked loop
        acc1_s = next(s for s in results if s.account_id == "acc_1" and s.item_id == "MLB111")
        acc2_s = next(s for s in results if s.account_id == "acc_2" and s.item_id == "MLM222")
        self.assertEqual(acc1_s.item_thumbnail, "https://img.ml/1.jpg")
        self.assertEqual(acc2_s.item_thumbnail, "https://img.ml/2.jpg")

    def test_active_msg_workers_lifecycle(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)
        self.assertTrue(hasattr(page, "_active_msg_workers"))
        self.assertEqual(len(page._active_msg_workers), 0)

        s = ServiceSession(id="c_1", session_type="claim", account_id="acc_1", account_name="测试店", site_id="MLB")
        with patch("service_center.service_center_page.FetchConversationMessagesWorker") as MockWorkerCls:
            mock_w = MagicMock()
            MockWorkerCls.return_value = mock_w
            page._fetch_conversation_messages(s)
            self.assertIn(mock_w, page._active_msg_workers)
            mock_w.start.assert_called_once()

        page.stop()
        self.assertEqual(len(page._active_msg_workers), 0)
        page.deleteLater()

    def test_fetch_sessions_with_closed_claims_and_order_messages(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_sites.return_value = [{"child_user_id": "cuid_1", "site_id": "MLB"}]
        mock_client.get_questions.return_value = {"questions": []}

        def _mock_claims(aid, cuid, status="opened", limit=30, **kwargs):
            if status == "opened":
                return {"data": [{"id": "claim_op", "resource_id": "ord_1", "stage": "claim", "reason_id": "defect", "date_created": "2026-10-14T01:00:00Z"}]}
            else:
                return {"data": [{"id": "claim_cl", "resource_id": "ord_2", "stage": "dispute", "reason_id": "resolved", "date_created": "2026-10-10T01:00:00Z"}]}

        mock_client.get_claims.side_effect = _mock_claims
        mock_client.get_unread_pack_messages.return_value = {"results": []}
        mock_client.search_marketplace_orders.return_value = {"results": [
            {
                "id": "ord_3",
                "pack_id": "pack_3",
                "buyer": {"id": "b_123", "nickname": "买家小李"},
                "order_items": [{"item": {"id": "MLB999", "title": "测试包邮商品"}, "unit_price": 88.0}],
                "currency_id": "R$",
            }
        ]}
        mock_client.get_pack_messages.return_value = {
            "messages": [
                {
                    "id": "m1",
                    "from": {"user_id": "cuid_1"},
                    "text": "亲，商品已发货，请注意查收快递。",
                    "message_date": {"created": "2026-10-14T03:00:00Z", "read": "2026-10-14T03:10:00Z"},
                }
            ]
        }
        mock_client.get_marketplace_order.return_value = None
        mock_client.get_items_batch.return_value = []

        worker = FetchSessionsWorker(mock_client, mock_auth, ["acc_1"], "all")
        results = []
        worker.finished.connect(lambda sessions: results.extend(sessions))
        worker.run()

        self.assertEqual(len(results), 3)
        claim_op = next(s for s in results if s.id == "c_claim_op")
        claim_cl = next(s for s in results if s.id == "c_claim_cl")
        msg_ord = next(s for s in results if s.id == "m_pack_3")

        self.assertEqual(claim_op.status, "opened")
        self.assertTrue(claim_op.can_reply)
        self.assertTrue(claim_op.is_unread)

        self.assertEqual(claim_cl.status, "closed")
        self.assertFalse(claim_cl.can_reply)
        self.assertFalse(claim_cl.is_unread)
        self.assertIn("[已结案]", claim_cl.summary)

        self.assertEqual(msg_ord.session_type, "message")
        self.assertEqual(msg_ord.buyer_nickname, "买家小李")
        self.assertEqual(msg_ord.item_title, "测试包邮商品")
        self.assertEqual(msg_ord.item_price, "R$ 88.0")
        self.assertEqual(msg_ord.status, "read")
        self.assertFalse(msg_ord.is_unread)

    @classmethod
    def setUpClass(cls):
        cls.app = create_application()

    def test_main_window_service_center_nav(self):
        mock_api = MagicMock()
        mock_api.get.return_value = {}
        mock_api.bridge = None
        mock_service = MagicMock()

        from main_window import MainWindow

        win = MainWindow(mock_api, mock_service, auto_start=False)
        self.assertTrue(hasattr(win, "service_center_page"))
        self.assertTrue(hasattr(win, "service_center_btn"))
        self.assertEqual(win.pages.widget(3), win.service_center_page)
        self.assertEqual(win.nav_buttons[3], win.service_center_btn)

        # Test switching to page 3
        with patch.object(win.service_center_page, "reload_sessions"):
            win._show_page(3)
        self.assertEqual(win.pages.currentIndex(), 3)
        self.assertTrue(win.service_center_btn.isChecked())

        # Test unread badge (permanently displays 客服售后 without badge per design)
        win._on_service_center_unread_count_changed(8)
        self.assertEqual(win.service_center_btn.text(), "客服售后")

        win._on_service_center_unread_count_changed(0)
        self.assertEqual(win.service_center_btn.text(), "客服售后")
        win.close()

    def test_sessions_chronological_and_unread_sorting(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        # Create sessions with mixed timestamps and read status
        s1 = ServiceSession(id="1", session_type="claim", account_id="acc_1", account_name="湖北店", site_id="MLB", created_at="2026-07-28T21:20:00Z", is_unread=False)
        s2 = ServiceSession(id="2", session_type="claim", account_id="acc_1", account_name="湖北店", site_id="MLB", created_at="2026-09-06T06:16:00Z", is_unread=False)
        s3 = ServiceSession(id="3", session_type="claim", account_id="acc_1", account_name="湖北店", site_id="MLB", created_at="2026-08-20T03:27:00Z", is_unread=False)
        s4 = ServiceSession(id="4", session_type="claim", account_id="acc_1", account_name="湖北店", site_id="MLB", created_at="2026-10-01T18:44:00Z", is_unread=True)
        s5 = ServiceSession(id="5", session_type="claim", account_id="acc_1", account_name="湖北店", site_id="MLB", created_at="2026-10-10T12:00:00Z", is_unread=False)

        page._active_tab = "claim"
        page._all_sessions = [s1, s2, s3, s4, s5]
        page._apply_filter()

        # Expected order (strict descending by created_at):
        # 1. s5 (2026-10-10)
        # 2. s4 (2026-10-01)
        # 3. s2 (2026-09-06)
        # 4. s3 (2026-08-20)
        # 5. s1 (2026-07-28)
        list_ids = [page.session_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(page.session_list.count())]
        self.assertEqual(list_ids, ["5", "4", "2", "3", "1"])
        page.close()

    def test_get_item_url_hyphen_formatting(self):
        from service_center.models import get_item_url
        self.assertEqual(get_item_url("MLB", "MLB4985681051"), "https://produto.mercadolivre.com.br/MLB-4985681051")
        self.assertEqual(get_item_url("MLM", "MLM-1234567"), "https://articulo.mercadolibre.com.mx/MLM-1234567")
        self.assertEqual(get_item_url("MLC", "MLC998877"), "https://articulo.mercadolibre.cl/MLC-998877")
        self.assertEqual(get_item_url("MCO", "MCO112233"), "https://articulo.mercadolibre.com.co/MCO-112233")
        # Permalink override
        self.assertEqual(get_item_url("MLB", "MLB123", "https://produto.mercadolivre.com.br/custom-slug"), "https://produto.mercadolivre.com.br/custom-slug")

    def test_session_unread_dismissal_on_selection_and_toggle(self):
        from service_center.service_center_page import ImagePreviewDialog
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        s1 = ServiceSession(id="m_100", session_type="message", account_id="acc_1", account_name="湖北店", site_id="MLB", is_unread=True)
        page._all_sessions = [s1]
        page._active_tab = "message"
        page._apply_filter()

        self.assertEqual(page.session_list.count(), 1)
        item = page.session_list.item(0)

        # Select item -> should auto-dismiss unread
        page._on_session_selected(item, None)
        self.assertFalse(s1.is_unread)
        self.assertEqual(page.toggle_read_btn.text(), "标为未读")

        # Toggle back to unread manually
        page._toggle_current_session_read_status()
        self.assertTrue(s1.is_unread)
        self.assertEqual(page.toggle_read_btn.text(), "标为已读")

        # Toggle back to read
        page._toggle_current_session_read_status()
        self.assertFalse(s1.is_unread)
        self.assertEqual(page.toggle_read_btn.text(), "标为未读")

        # Test ImagePreviewDialog can be constructed
        dlg = ImagePreviewDialog(title="测试图片", parent=page)
        self.assertIsNotNone(dlg)
        dlg.close()
    def test_load_image_async_local_file_and_dual_callback_kwargs(self):
        import tempfile
        import time
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QColor, QPixmap
        from service_center.image_cache import load_image_async

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name

        try:
            pix = QPixmap(32, 32)
            pix.fill(QColor("red"))
            pix.save(tmp_path, "PNG")

            loaded_pixmaps = []
            # Test using on_loaded
            load_image_async(tmp_path, QSize(32, 32), on_loaded=lambda p: loaded_pixmaps.append(p))
            for _ in range(50):
                self.app.processEvents()
                if loaded_pixmaps:
                    break
                time.sleep(0.04)
            self.assertGreaterEqual(len(loaded_pixmaps), 1)
            self.assertFalse(loaded_pixmaps[0].isNull())

            # Test using legacy/alias callback keyword argument
            callback_pixmaps = []
            load_image_async(tmp_path, QSize(32, 32), callback=lambda p: callback_pixmaps.append(p))
            for _ in range(50):
                self.app.processEvents()
                if callback_pixmaps:
                    break
                time.sleep(0.04)
            self.assertGreaterEqual(len(callback_pixmaps), 1)
            self.assertFalse(callback_pixmaps[0].isNull())

            # Test using crop=False (contain mode for preview)
            contain_pixmaps = []
            load_image_async(tmp_path, QSize(60, 30), on_loaded=lambda p: contain_pixmaps.append(p), crop=False)
            for _ in range(50):
                self.app.processEvents()
                if contain_pixmaps:
                    break
                time.sleep(0.04)
            self.assertGreaterEqual(len(contain_pixmaps), 1)
            self.assertFalse(contain_pixmaps[0].isNull())
            # For 32x32 into 60x30 with KeepAspectRatio, height should be <= 30
            self.assertLessEqual(contain_pixmaps[0].height(), 30)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_fetch_sessions_worker_streaming_chunks(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_sites.return_value = [{"child_user_id": "c1", "site_id": "MLB"}]
        mock_client.get_questions.return_value = {
            "questions": [{"id": "q10", "item_id": "MLB10", "text": "咨询Q10", "date_created": "2026-10-15T01:00:00Z"}]
        }
        mock_client.get_claims.return_value = {"data": []}
        mock_client.get_unread_pack_messages.return_value = {"results": []}
        mock_client.search_marketplace_orders.return_value = {"results": []}
        mock_client.get_items_batch.return_value = []

        worker = FetchSessionsWorker(mock_client, mock_auth, ["acc_1"], "all")
        chunks = []
        finished_res = []
        worker.sessions_chunk.connect(lambda c: chunks.extend(c))
        worker.finished.connect(lambda f: finished_res.extend(f))

        worker.run()
        self.assertGreaterEqual(len(chunks), 1)
        self.assertGreaterEqual(len(finished_res), 1)
        self.assertEqual(finished_res[0].id, "q_q10")

    def test_show_image_preview_execution(self):
        from PySide6.QtGui import QPixmap
        from service_center.service_center_page import show_image_preview
        with patch("service_center.service_center_page.ImagePreviewDialog.exec") as mock_exec:
            # Test with dummy network URL
            show_image_preview("https://http2.mlstatic.com/D_NQ_NP_12345-I.jpg", title="网络大图")
            mock_exec.assert_called()

            # Test with pixmap directly
            mock_exec.reset_mock()
            show_image_preview(QPixmap(20, 20), title="直传位图")
            mock_exec.assert_called_once()


    def test_fetch_sessions_worker_cache_inheritance(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_sites.return_value = [{"child_user_id": "c1", "site_id": "MLB"}]
        mock_client.get_questions.return_value = {"questions": []}
        mock_client.get_claims.return_value = {
            "data": [
                {
                    "id": "1001",
                    "resource_id": "order_1001",
                    "resource": "order",
                    "stage": "claim",
                    "reason_id": "defect",
                    "date_created": "2026-10-15T00:00:00Z",
                    "players": [{"role": "complainant", "user_id": "buyer_1"}],
                }
            ],
            "paging": {"total": 1},
        }
        mock_client.get_unread_pack_messages.return_value = {"results": []}
        mock_client.search_marketplace_orders.return_value = {"results": []}
        mock_client.get_items_batch.return_value = []

        cached_sess = ServiceSession(
            id="c_1001",
            session_type="claim",
            account_id="acc_1",
            account_name="店铺1",
            site_id="MLB",
            item_id="MLB1001",
            item_title="已缓存的商品标题",
            item_thumbnail="http://example.com/thumb1001.jpg",
            item_price="R$ 55.0",
            sku="SKU-1001",
            order_id="order_1001",
            buyer_nickname="小张买家",
            status="opened",
        )

        worker = FetchSessionsWorker(
            mock_client,
            mock_auth,
            ["acc_1"],
            "all",
            cached_sessions=[cached_sess],
        )
        finished_res = []
        worker.finished.connect(lambda f: finished_res.extend(f))
        worker.run()

        self.assertEqual(len(finished_res), 1)
        res = finished_res[0]
        self.assertEqual(res.id, "c_1001")
        self.assertEqual(res.item_title, "已缓存的商品标题")
        self.assertEqual(res.item_thumbnail, "http://example.com/thumb1001.jpg")
        self.assertEqual(res.item_price, "R$ 55.0")
        self.assertEqual(res.sku, "SKU-1001")
        self.assertEqual(res.buyer_nickname, "小张买家")
        # get_marketplace_order should NOT be called because title and item_id are cached
        mock_client.get_marketplace_order.assert_not_called()

    def test_enrich_account_sessions_404_pruning(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_client.get_marketplace_order.side_effect = Exception("HTTP 404 Not Found")

        worker = FetchSessionsWorker(mock_client, mock_auth, ["acc_1"], "claim")
        s = ServiceSession(
            id="c_2001",
            session_type="claim",
            account_id="acc_1",
            account_name="店铺1",
            site_id="MLB",
            order_id="404_order_id",
            item_title="商品 MLB2001",
        )
        worker._enrich_account_sessions("acc_1", [s])
        self.assertIn("404_order_id", worker._not_found_orders)
        # Verify get_marketplace_shipment was NEVER called
        mock_client.get_marketplace_shipment.assert_not_called()


class TestServiceCenterSnapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application()

    def test_save_and_load_sessions_snapshot(self):
        import tempfile
        from service_center.service_center_page import save_sessions_snapshot, load_sessions_snapshot
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_data_dir = Path(tmp_dir)
            with patch("engine.crypto.get_data_dir", return_value=tmp_data_dir):
                    s = ServiceSession(
                        id="c_test1",
                        session_type="claim",
                        account_id="acc_1",
                        account_name="店铺1",
                        site_id="MLB",
                        item_id="MLB999",
                        item_title="商品标题",
                        item_thumbnail="http://example.com/img.jpg",
                        item_price="R$ 99.9",
                        sku="SKU123",
                        order_id="2000001",
                        status="opened",
                        summary="退款测试",
                        is_unread=True,
                    )
                    save_sessions_snapshot([s])
                    loaded = load_sessions_snapshot()
                    self.assertEqual(len(loaded), 1)
                    self.assertEqual(loaded[0].id, "c_test1")
                    self.assertEqual(loaded[0].item_title, "商品标题")
                    self.assertEqual(loaded[0].item_thumbnail, "http://example.com/img.jpg")
                    self.assertEqual(loaded[0].item_price, "R$ 99.9")
                    self.assertEqual(loaded[0].sku, "SKU123")
                    self.assertTrue(loaded[0].is_unread)

    def test_safe_worker_base_lifecycle(self):
        from PySide6.QtCore import QCoreApplication, Signal
        from service_center.service_center_page import SafeWorkerBase
        import time

        class ConcreteWorker(SafeWorkerBase):
            done_signal = Signal(str)

            def run(self):
                while not self._is_stopped:
                    time.sleep(0.01)
                    break
                self.done_signal.emit("ok")

        w = ConcreteWorker()
        received = []
        w.done_signal.connect(lambda v: received.append(v))
        self.assertFalse(w.isRunning())
        w.start()
        self.assertTrue(w.wait(2000))
        self.assertFalse(w.isRunning())
        QCoreApplication.processEvents()
        self.assertEqual(received, ["ok"])

        # Test quit
        w2 = ConcreteWorker()
        w2.start()
        w2.quit()
        self.assertTrue(w2.wait(2000))

    def test_message_three_states_and_blocked(self):
        """Verify:
        1. Buyer sent last msg -> new_message (is_unread=True)
        2. Seller sent last msg, buyer not read -> unread (is_unread=False)
        3. Seller sent last msg, buyer read -> read (is_unread=False)
        4. Cancelled order -> blocked (can_reply=False)
        """
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_sites.return_value = [{"child_user_id": "seller_sub_1", "site_id": "MLM"}]
        mock_client.get_questions.return_value = {"questions": []}
        mock_client.get_claims.return_value = {"data": []}
        mock_client.get_unread_pack_messages.return_value = {"results": []}

        # 4 orders representing each state
        mock_client.search_marketplace_orders.return_value = {"results": [
            {
                "id": "ord_buyer_msg",
                "pack_id": "pack_buyer",
                "buyer": {"id": "b_1", "nickname": "买家A"},
                "orders": [{"id": "so_1", "status": "paid", "seller": {"id": "seller_sub_1"}}],
            },
            {
                "id": "ord_seller_unread",
                "pack_id": "pack_unr",
                "buyer": {"id": "b_2", "nickname": "买家B"},
                "orders": [{"id": "so_2", "status": "paid", "seller": {"id": "seller_sub_1"}}],
            },
            {
                "id": "ord_seller_read",
                "pack_id": "pack_rd",
                "buyer": {"id": "b_3", "nickname": "买家C"},
                "orders": [{"id": "so_3", "status": "paid", "seller": {"id": "seller_sub_1"}}],
            },
            {
                "id": "ord_cancelled",
                "pack_id": "pack_cnl",
                "buyer": {"id": "b_4", "nickname": "买家D"},
                "orders": [{"id": "so_4", "status": "cancelled", "seller": {"id": "seller_sub_1"}}],
            },
        ]}

        def _mock_pack_msgs(aid, pid):
            if pid == "pack_buyer":
                return {"messages": [{"id": "m_b", "from": {"user_id": "b_1"}, "text": "有现货吗？", "message_date": {"created": "2026-10-15T10:00:00Z"}}]}
            elif pid == "pack_unr":
                return {"messages": [{"id": "m_s1", "from": {"user_id": "seller_sub_1"}, "text": "有的亲，今天发出", "message_date": {"created": "2026-10-15T10:05:00Z", "read": None}}]}
            elif pid == "pack_rd":
                return {"messages": [{"id": "m_s2", "from": {"user_id": "seller_sub_1"}, "text": "亲，发货单号已更新", "message_date": {"created": "2026-10-15T10:10:00Z", "read": "2026-10-15T10:30:00Z"}}]}
            elif pid == "pack_cnl":
                return {"messages": [{"id": "m_c", "from": {"user_id": "b_4"}, "text": "已申请取消", "message_date": {"created": "2026-10-15T10:15:00Z"}}]}
            return {"messages": []}

        mock_client.get_pack_messages.side_effect = _mock_pack_msgs
        mock_client.get_marketplace_order.return_value = None
        mock_client.get_items_batch.return_value = []

        worker = FetchSessionsWorker(mock_client, mock_auth, ["acc_main"], "all")
        results = []
        worker.finished.connect(lambda sessions: results.extend(sessions))
        worker.run()

        s_buyer = next(s for s in results if s.id == "m_pack_buyer")
        s_unr = next(s for s in results if s.id == "m_pack_unr")
        s_rd = next(s for s in results if s.id == "m_pack_rd")
        s_cnl = next(s for s in results if s.id == "m_pack_cnl")

        # 1. Buyer sent -> 新信息
        self.assertEqual(s_buyer.status, "new_message")
        self.assertTrue(s_buyer.is_unread)
        self.assertTrue(s_buyer.can_reply)

        # 2. Seller sent, buyer unread -> 未读
        self.assertEqual(s_unr.status, "unread")
        self.assertFalse(s_unr.is_unread)
        self.assertTrue(s_unr.can_reply)

        # 3. Seller sent, buyer read -> 已读
        self.assertEqual(s_rd.status, "read")
        self.assertFalse(s_rd.is_unread)
        self.assertTrue(s_rd.can_reply)

        # 4. Cancelled -> 不可回复 (blocked)
        self.assertEqual(s_cnl.status, "blocked")
        self.assertFalse(s_cnl.is_unread)
        self.assertFalse(s_cnl.can_reply)

    def test_question_chat_bubble_has_no_unread_badge(self):
        """Verify pre-sale question answers do NOT display '未读'."""
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        # Question session
        q_sess = ServiceSession(
            id="q_123",
            session_type="question",
            account_id="acc_1",
            account_name="广东店",
            site_id="MLM",
        )
        page._current_session = q_sess

        seller_msg = ServiceMessage(
            id="rep_1",
            sender_role="seller",
            sender_name="卖家",
            text="您好，有现货",
            created_at="2026-10-15T12:00:00Z",
            is_seller=True,
            is_read=False,
        )
        bubble = page._create_chat_bubble(seller_msg)
        labels = [lbl.text() for lbl in bubble.findChildren(QLabel)]
        header_text = labels[0] if labels else ""
        self.assertNotIn("未读", header_text)
        self.assertNotIn("已读", header_text)
        self.assertIn("卖家", header_text)

        # Post-sale message session DOES show 未读 / 已读
        m_sess = ServiceSession(
            id="m_123",
            session_type="message",
            account_id="acc_1",
            account_name="广东店",
            site_id="MLM",
        )
        page._current_session = m_sess
        bubble_msg = page._create_chat_bubble(seller_msg)
        labels_msg = [lbl.text() for lbl in bubble_msg.findChildren(QLabel)]
        header_text_msg = labels_msg[0] if labels_msg else ""
        self.assertIn("未读", header_text_msg)

        # Test splitter width
        self.assertEqual(page.splitter.handleWidth(), 8)
        page.close()

    def test_claim_action_toolbar_and_status_filter(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        # 1. Test status filter combo items
        combo_items = [page.status_combo.itemData(i) for i in range(page.status_combo.count())]
        self.assertIn("all", combo_items)
        self.assertIn("opened", combo_items)
        self.assertIn("closed", combo_items)

        # 2. Test claim actions frame visibility for non-claim session
        page._display_session_detail(None)
        self.assertTrue(page.claim_actions_frame.isHidden())

        q_sess = ServiceSession(
            id="q_100",
            session_type="question",
            account_id="acc_1",
            account_name="测试店",
            site_id="MLM",
        )
        page._display_session_detail(q_sess)
        self.assertTrue(page.claim_actions_frame.isHidden())

        # 3. Test claim actions frame visibility and button states for opened claim
        claim_opened = ServiceSession(
            id="c_5001",
            session_type="claim",
            account_id="acc_1",
            account_name="测试店",
            site_id="MLM",
            order_id="2000018676400878",
            status="opened",
            stage="claim",
        )
        page._display_session_detail(claim_opened)
        self.assertFalse(page.claim_actions_frame.isHidden())
        self.assertTrue(page.claim_dispute_btn.isEnabled())
        self.assertTrue(page.claim_partial_refund_btn.isEnabled())
        self.assertTrue(page.claim_full_refund_btn.isEnabled())
        self.assertIn("买家发起投诉", page.claim_stage_lbl.text())

        # 4. Test dispute stage disables dispute button
        claim_dispute = ServiceSession(
            id="c_5002",
            session_type="claim",
            account_id="acc_1",
            account_name="测试店",
            site_id="MLM",
            order_id="2000018676400879",
            status="opened",
            stage="dispute",
        )
        page._display_session_detail(claim_dispute)
        self.assertFalse(page.claim_dispute_btn.isEnabled())
        self.assertEqual(page.claim_dispute_btn.text(), "调解中")

        # 5. Test closed claim disables all claim action buttons
        claim_closed = ServiceSession(
            id="c_5003",
            session_type="claim",
            account_id="acc_1",
            account_name="测试店",
            site_id="MLM",
            order_id="2000018676400880",
            status="closed",
            stage="none",
        )
        page._display_session_detail(claim_closed)
        self.assertFalse(page.claim_dispute_btn.isEnabled())
        self.assertFalse(page.claim_partial_refund_btn.isEnabled())
        self.assertFalse(page.claim_full_refund_btn.isEnabled())

        # 6. Test claim item card displays order_id
        item_widget = page._create_session_item_widget(claim_opened)
        self.assertIsNotNone(item_widget)
        labels = [lbl.text() for lbl in item_widget.findChildren(QLabel)]
        order_found = any("2000018676400878" in t for t in labels)
        self.assertTrue(order_found)

        page.close()

    def test_claim_client_api_methods(self):
        # Test client methods exists and dispatch correctly
        from engine.client import MercadoClient
        mock_auth = MagicMock()
        client = MercadoClient(mock_auth)

        with patch.object(client, "request", return_value={"id": "dispute_123"}) as mock_req:
            res = client.request_claim_mediation("acc_1", "5588")
            mock_req.assert_called_with("acc_1", "POST", "/post-purchase/v1/claims/5588/actions/open-dispute", body={})
            self.assertEqual(res, {"id": "dispute_123"})

        with patch.object(client, "request", return_value={"offers": []}) as mock_req:
            res = client.get_claim_partial_refund_offers("acc_1", "5588")
            mock_req.assert_called_with("acc_1", "GET", "/post-purchase/v1/claims/5588/partial-refund/available-offers")
            self.assertEqual(res, {"offers": []})

        with patch.object(client, "request", return_value={"status": "ok"}) as mock_req:
            res = client.submit_claim_partial_refund("acc_1", "5588", percentage=25.0)
            mock_req.assert_called_with(
                "acc_1",
                "POST",
                "/post-purchase/v1/claims/5588/expected-resolutions/partial-refund",
                body={"percentage": 25.0},
            )
            self.assertEqual(res, {"status": "ok"})

        with patch.object(client, "request", return_value={"status": "refunded"}) as mock_req:
            res = client.submit_claim_full_refund("acc_1", "5588")
            mock_req.assert_called_with("acc_1", "POST", "/post-purchase/v1/claims/5588/expected-resolutions/refund", body={})
            self.assertEqual(res, {"status": "refunded"})

    def test_display_session_detail_product_link_recovery(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)
        s = ServiceSession(
            id="m_2000015370814845",
            session_type="message",
            account_id="acc_1",
            account_name="湖南店",
            site_id="MLB",
            item_id="",
            item_permalink="https://produto.mercadolivre.com.br/MLB-4940836269-jogo-de-2-cortinas-com-estampa-de-leo-para-sala-e-quarto-_JM",
            order_id="2000018815569506",
            pack_id="2000015370814845",
            item_title="订单 2000018815569506 售后私信",
        )
        page._display_session_detail(s)

        # item_id should be automatically recovered from permalink
        self.assertEqual(s.item_id, "MLB4940836269")
        # Product link should be rendered in context_link_lbl
        self.assertIn("商品链接:", page.context_link_lbl.text())
        self.assertIn("MLB4940836269", page.context_link_lbl.text())
        self.assertIn("关联订单号: <b>2000018815569506</b>", page.context_link_lbl.text())
        self.assertIn("包裹号: <b>2000015370814845</b>", page.context_link_lbl.text())
        # Button should show default browser open
        self.assertEqual(page.open_browser_btn.text(), "在默认浏览器打开 ↗")
        page.close()

    def test_alternate_picture_selection_when_seller_attachment_present(self):
        from service_center.service_center_page import FetchConversationMessagesWorker
        mock_client = MagicMock()
        # Message from seller with attachment
        mock_client.get_pack_messages.return_value = {
            "messages": [
                {
                    "id": "msg_001",
                    "from": {"user_id": "3407227825"},
                    "text": "Hello, here is the new crown design",
                    "message_date": {"created": "2026-10-06T14:01:00Z"},
                    "message_attachments": [{"filename": "crown.jpg"}],
                }
            ],
            "message_resources": [],
        }
        # Item detail has 2 pictures: pic 0 is crown (899456), pic 1 is original (904251)
        mock_client.get_item_detail.return_value = {
            "pictures": [
                {"id": "899456_crown", "url": "http://img.com/crown.jpg"},
                {"id": "904251_orig", "url": "http://img.com/orig_no_crown.jpg"},
            ]
        }
        s = ServiceSession(
            id="m_pack_100",
            session_type="message",
            account_id="acc_1",
            account_name="湖南店",
            site_id="MLB",
            item_id="MLB4940836269",
            buyer_id="217313585",
            item_thumbnail="http://img.com/899456_crown.jpg",
            order_id="ord_100",
            pack_id="pack_100",
        )
        worker = FetchConversationMessagesWorker(mock_client, s)
        worker.run()

        # Alternate picture (orig_no_crown) should be selected to avoid colliding with attachment
        self.assertEqual(s.item_thumbnail, "http://img.com/orig_no_crown.jpg")

    def test_extract_sku_picture_url(self):
        from service_center.service_center_page import extract_sku_picture_url

        item = {
            "id": "MLB123456",
            "thumbnail": "http://img.com/thumb.jpg",
            "secure_thumbnail": "https://img.com/thumb.jpg",
            "pictures": [
                {"id": "PIC_COVER", "secure_url": "https://img.com/cover.jpg"},
                {"id": "PIC_RED", "secure_url": "https://img.com/red.jpg"},
                {"id": "PIC_BLUE", "secure_url": "https://img.com/blue.jpg"},
            ],
            "variations": [
                {
                    "id": 11111,
                    "picture_ids": ["PIC_RED"],
                    "seller_custom_field": "SKU-RED-M",
                },
                {
                    "id": 22222,
                    "picture_ids": ["PIC_BLUE"],
                    "seller_custom_field": "SKU-BLUE-L",
                },
            ],
        }

        # 1. Match by variation_id
        url_red = extract_sku_picture_url(item, variation_id="11111")
        self.assertEqual(url_red, "https://img.com/red.jpg")

        url_blue = extract_sku_picture_url(item, variation_id="22222")
        self.assertEqual(url_blue, "https://img.com/blue.jpg")

        # 2. Match by SKU fallback
        url_sku = extract_sku_picture_url(item, sku="SKU-RED-M")
        self.assertEqual(url_sku, "https://img.com/red.jpg")

        # 3. Fallback to pictures[0] if no variation matches
        url_fallback = extract_sku_picture_url(item, variation_id="99999")
        self.assertEqual(url_fallback, "https://img.com/cover.jpg")

        # 4. Fallback when pictures empty
        empty_pics_item = {"thumbnail": "http://img.com/fallback.jpg"}
        url_empty = extract_sku_picture_url(empty_pics_item)
        self.assertEqual(url_empty, "http://img.com/fallback.jpg")

        # 5. Match by attribute_combinations (e.g. Marrón claro in SKU)
        item_with_combos = {
            "id": "MLB4934938243",
            "pictures": [
                {"id": "PIC_DEF", "secure_url": "https://img.com/default.jpg"},
                {"id": "PIC_BROWN", "secure_url": "https://img.com/brown_blanket.jpg"},
            ],
            "variations": [
                {
                    "id": 205559943495,
                    "picture_ids": ["PIC_BROWN"],
                    "attribute_combinations": [
                        {"id": "COLOR", "name": "Cor", "value_name": "Marrón claro"}
                    ],
                }
            ],
        }
        url_combo = extract_sku_picture_url(item_with_combos, sku="MLB7026838674:Marrón claro")
        self.assertEqual(url_combo, "https://img.com/brown_blanket.jpg")

    def test_list_item_thumbnail_click_disabled(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel
        from service_center.service_center_page import ServiceCenterPage

        mock_client = MagicMock()
        mock_auth = MagicMock()
        page = ServiceCenterPage(mock_client, mock_auth)
        sess = ServiceSession(
            id="s_100",
            session_type="claim",
            account_id="acc_1",
            account_name="官方店",
            site_id="MLB",
            item_thumbnail="https://img.com/thumb.jpg",
        )
        card = page._create_session_item_widget(sess)
        labels = card.findChildren(QLabel)
        thumb_lbl = labels[0]
        self.assertTrue(thumb_lbl.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))

    def test_enrich_claim_with_sku_variation_image(self):
        from service_center.service_center_page import FetchSessionsWorker

        mock_client = MagicMock()
        mock_auth = MagicMock()

        # Mock order response returning variation_id and seller_sku
        mock_client.get_marketplace_order.return_value = {
            "pack_id": "pack_claim_1",
            "buyer": {"id": 123456, "nickname": "买家小李"},
            "currency_id": "BRL",
            "order_items": [
                {
                    "item": {
                        "id": "MLB999888",
                        "title": "潮流连帽卫衣",
                        "variation_id": 888123,
                        "seller_sku": "HOODIE-GOLD-XL",
                    },
                    "unit_price": 129.9,
                }
            ],
        }

        # Mock item batch response returning variations and picture list
        mock_client.get_items_batch.return_value = [
            {
                "id": "MLB999888",
                "title": "潮流连帽卫衣",
                "pictures": [
                    {"id": "PIC_DEFAULT", "secure_url": "https://img.com/default_hoodie.jpg"},
                    {"id": "PIC_GOLD", "secure_url": "https://img.com/gold_hoodie.jpg"},
                ],
                "variations": [
                    {
                        "id": 888123,
                        "picture_ids": ["PIC_GOLD"],
                        "seller_custom_field": "HOODIE-GOLD-XL",
                    }
                ],
                "permalink": "https://produto.mercadolivre.com.br/MLB999888",
            }
        ]

        worker = FetchSessionsWorker(mock_client, mock_auth, ["acc_1"], "claim")
        claim_sess = ServiceSession(
            id="c_claim_001",
            session_type="claim",
            account_id="acc_1",
            account_name="官方旗舰店",
            site_id="MLB",
            order_id="2000008888",
            status="opened",
        )

        worker._enrich_account_sessions("acc_1", [claim_sess])

        # Verify claim session enriched with precise SKU details
        self.assertEqual(claim_sess.item_id, "MLB999888")
        self.assertEqual(claim_sess.item_title, "潮流连帽卫衣")
        self.assertEqual(claim_sess.variation_id, "888123")
        self.assertEqual(claim_sess.sku, "HOODIE-GOLD-XL")
        self.assertEqual(claim_sess.item_thumbnail, "https://img.com/gold_hoodie.jpg")
        self.assertEqual(claim_sess.item_permalink, "https://produto.mercadolivre.com.br/MLB999888")

    def test_webhook_incoming_marketplace_topics(self):
        """Test incoming webhooks with marketplace_ prefix properly trigger toast and reload."""
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = [{"account_id": "acc_1"}]
        mock_auth.get_account.return_value = {"name": "广东店"}

        page = ServiceCenterPage(mock_client, mock_auth)
        page._sound_enabled = True

        with patch("service_center.service_center_page.play_notification_chime") as mock_chime, \
             patch.object(page, "reload_sessions") as mock_reload, \
             patch("service_center.service_center_page.ToastNotification") as mock_toast:
            mock_toast_instance = MagicMock()
            mock_toast.return_value = mock_toast_instance

            # Test marketplace_questions
            event_q = {
                "topic": "marketplace_questions",
                "resource": "/marketplace/questions/998877",
                "user_id": "acc_1",
            }
            page.handle_incoming_webhook_event(event_q)

            mock_chime.assert_called_once()
            mock_reload.assert_called_with(silent=True)
            mock_toast.assert_called_once()
            self.assertEqual(mock_toast.call_args[1]["session_type"], "question")
            self.assertEqual(mock_toast.call_args[1]["title"], "收到买家新提问")

            # Test marketplace_messages
            mock_chime.reset_mock()
            mock_reload.reset_mock()
            mock_toast.reset_mock()

            event_m = {
                "topic": "marketplace_messages",
                "resource": "/marketplace/messages/packs/554433",
                "user_id": "acc_1",
            }
            page.handle_incoming_webhook_event(event_m)

            mock_chime.assert_called_once()
            mock_reload.assert_called_with(silent=True)
            mock_toast.assert_called_once()
            self.assertEqual(mock_toast.call_args[1]["session_type"], "message")
            self.assertEqual(mock_toast.call_args[1]["title"], "收到买家新私信")

            # Test marketplace_claims
            mock_chime.reset_mock()
            mock_reload.reset_mock()
            mock_toast.reset_mock()

            event_c = {
                "topic": "marketplace_claims",
                "resource": "/marketplace/claims/778899",
                "user_id": "acc_1",
            }
            page.handle_incoming_webhook_event(event_c)

            mock_chime.assert_called_once()
            mock_reload.assert_called_with(silent=True)
            mock_toast.assert_called_once()
            self.assertEqual(mock_toast.call_args[1]["session_type"], "claim")
            self.assertEqual(mock_toast.call_args[1]["title"], "收到新索赔")

        page.stop()

    def test_sound_toggle_and_auto_poll_timer(self):
        """Test sound toggle button and auto-poll timer interval and callback."""
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []

        page = ServiceCenterPage(mock_client, mock_auth)
        self.assertTrue(page._sound_enabled)
        self.assertIn("开", page.sound_btn.text())

        # Toggle sound off
        page._toggle_sound()
        self.assertFalse(page._sound_enabled)
        self.assertIn("关", page.sound_btn.text())

        # Toggle sound on (triggers chime preview)
        with patch("service_center.service_center_page.play_notification_chime") as mock_chime:
            page._toggle_sound()
            self.assertTrue(page._sound_enabled)
            self.assertIn("开", page.sound_btn.text())
            mock_chime.assert_called_once()

        # Check auto poll timer
        self.assertTrue(hasattr(page, "_poll_timer"))
        self.assertEqual(page._poll_timer.interval(), 30000)

        with patch.object(page, "reload_sessions") as mock_reload:
            page._on_auto_poll_timeout()
            mock_reload.assert_called_once_with(silent=True)

        page.stop()

    def test_chat_copy_single_and_all_messages(self):
        """Test right-click context menu copying single message and all messages in conversation."""
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []

        page = ServiceCenterPage(mock_client, mock_auth)

        msg1 = ServiceMessage(
            id="m1",
            sender_role="buyer",
            sender_name="买家小明",
            text="请问有现货吗？",
            text_translated="¿Tienen stock?",
            created_at="2026-10-16T12:00:00Z",
            is_seller=False,
        )
        msg2 = ServiceMessage(
            id="m2",
            sender_role="seller",
            sender_name="卖家",
            text="有的，下单当天即可发出。",
            created_at="2026-10-16T12:05:00Z",
            is_seller=True,
        )

        sess = ServiceSession(
            id="s1",
            session_type="question",
            account_id="acc_1",
            account_name="广东店",
            site_id="MLM",
            messages=[msg1, msg2],
        )
        page._current_session = sess

        # Test single message format & copy (pure text)
        formatted_single = page._format_single_message_text(msg1, include_header=False)
        self.assertIn("请问有现货吗？", formatted_single)
        self.assertIn("(译: ¿Tienen stock?)", formatted_single)
        self.assertNotIn("买家小明", formatted_single)

        # Test single message format & copy (with header)
        formatted_single_hdr = page._format_single_message_text(msg1, include_header=True)
        self.assertIn("买家小明", formatted_single_hdr)
        self.assertIn("2026-10-16 12:00", formatted_single_hdr)
        self.assertIn("请问有现货吗？", formatted_single_hdr)

        page._copy_single_message(msg1, include_header=False)
        self.assertEqual(QApplication.clipboard().text(), formatted_single)

        # Test direct right-click on single message
        page._show_chat_context_menu(QPoint(0, 0), page.chat_container, msg1)
        if hasattr(page, "_last_context_menu") and page._last_context_menu:
            page._last_context_menu.actions()[0].trigger()
        self.assertEqual(QApplication.clipboard().text(), formatted_single)

        # Test direct right-click on blank container (copies all)
        page._show_chat_context_menu(QPoint(0, 0), page.chat_container, None)
        if hasattr(page, "_last_context_menu") and page._last_context_menu:
            page._last_context_menu.actions()[0].trigger()
        clipboard_all = QApplication.clipboard().text()
        self.assertIn("买家小明", clipboard_all)
        self.assertIn("请问有现货吗？", clipboard_all)
        self.assertIn("有的，下单当天即可发出。", clipboard_all)

        # Test send finished without QMessageBox popup
        with patch("service_center.service_center_page.QMessageBox.information") as mock_info:
            page.target_input.setPlainText("Test answer")
            page._on_send_finished(True, "OK")
            mock_info.assert_not_called()

        page.stop()

    def test_service_center_v2_1_66_ui_and_flow_enhancements(self):
        """Test v2.1.66 UI improvements: symmetric bottom bar, pending tab counts, single input box in-place flow, claim stage Chinese translation."""
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        # 1. Test ServiceSession.is_pending
        q_unans = ServiceSession(id="q1", session_type="question", account_id="acc1", account_name="店1", site_id="MLB", status="unanswered", can_reply=True)
        q_ans = ServiceSession(id="q2", session_type="question", account_id="acc1", account_name="店1", site_id="MLB", status="answered", can_reply=False)
        c_opened = ServiceSession(id="c1", session_type="claim", account_id="acc1", account_name="店1", site_id="MLM", status="opened", stage="claim")
        c_closed = ServiceSession(id="c2", session_type="claim", account_id="acc1", account_name="店1", site_id="MLM", status="closed", stage="none")
        m_unread = ServiceSession(id="m1", session_type="message", account_id="acc1", account_name="店1", site_id="MLB", is_unread=True)
        m_read = ServiceSession(id="m2", session_type="message", account_id="acc1", account_name="店1", site_id="MLB", is_unread=False)

        self.assertTrue(q_unans.is_pending)
        self.assertFalse(q_ans.is_pending)
        self.assertTrue(c_opened.is_pending)
        self.assertFalse(c_closed.is_pending)
        self.assertTrue(m_unread.is_pending)
        self.assertFalse(m_read.is_pending)

        # 2. Test tab pending counts and list summary
        page._all_sessions = [q_unans, q_ans, c_opened, c_closed, m_read]
        page._update_tab_counts()
        page._apply_filter()
        self.assertEqual(page.tab_btn_questions.text(), "售前咨询 (1待回复)")
        self.assertEqual(page.tab_btn_claims.text(), "订单索赔 (1进行中)")
        self.assertEqual(page.tab_btn_messages.text(), "售后私信")

        # Active tab is question (default)
        self.assertEqual(page.list_summary_lbl.text(), "共 2 条提问")

        # Switch to claims
        page._switch_tab("claim")
        self.assertEqual(page.list_summary_lbl.text(), "共 2 笔索赔")

        # Switch to messages
        page._switch_tab("message")
        self.assertEqual(page.list_summary_lbl.text(), "共 1 条私信")

        # 3. Test Chinese claim stage translation in context_link_lbl
        page._display_session_detail(c_opened)
        self.assertIn("买家发起投诉", page.context_link_lbl.text())

        c_dispute = ServiceSession(id="c3", session_type="claim", account_id="acc1", account_name="店1", site_id="MLM", status="opened", stage="dispute")
        page._display_session_detail(c_dispute)
        self.assertIn("平台官方调解中", page.context_link_lbl.text())

        # 4. Test single input box in-place flow
        page.reply_input.setPlainText("你好")
        self.assertEqual(page.zh_input.toPlainText(), "你好")
        self.assertEqual(page.target_input.toPlainText(), "你好")
        self.assertEqual(page.char_count_lbl.text(), "2 / 500")

        # Simulate translation finished
        page._on_translate_finished("Hola")
        self.assertEqual(page.reply_input.toPlainText(), "Hola")
        self.assertEqual(page.zh_input.toPlainText(), "Hola")
        self.assertEqual(page.target_input.toPlainText(), "Hola")

        # 5. Test refresh button in bottom bar
        self.assertEqual(page.refresh_btn.text(), "刷新")
        with patch.object(page, "reload_sessions") as mock_rel:
            page.refresh_btn.click()
            mock_rel.assert_called_once()

        page.stop()

    def test_service_center_v2_1_68_image_upload_and_template_removal(self):
        """Test v2.1.68 improvements: template button removed, upload image button added, preview strip & SendReplyWorker attachments."""
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        # 1. Verify template button is removed and upload image button is present
        self.assertTrue(hasattr(page, "upload_img_btn"))
        self.assertEqual(page.upload_img_btn.text(), "📷 上传图片")

        # 2. Test button state based on session type
        q_sess = ServiceSession(id="q1", session_type="question", account_id="acc1", account_name="店1", site_id="MLB", status="unanswered", can_reply=True)
        c_sess = ServiceSession(id="c_1", session_type="claim", account_id="acc1", account_name="店1", site_id="MLM", status="opened", stage="claim")

        page._display_session_detail(q_sess)
        self.assertFalse(page.upload_img_btn.isEnabled())
        self.assertIn("纯文字", page.upload_img_btn.toolTip())

        page._display_session_detail(c_sess)
        self.assertTrue(page.upload_img_btn.isEnabled())

        # 3. Test image selection & preview management
        import tempfile
        tmp1 = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp1.write(b"fake_jpg_content")
        tmp1.close()

        page.selected_images.append(tmp1.name)
        page._refresh_image_previews()
        self.assertFalse(page.preview_container.isHidden())
        self.assertIn("1/3", page.img_count_lbl.text())

        # Test remove selected image
        page._remove_selected_image(0)
        self.assertEqual(len(page.selected_images), 0)
        self.assertTrue(page.preview_container.isHidden())

        # 4. Test SendReplyWorker uploading claim attachment and sending message with attachments
        mock_client.upload_claim_attachment.return_value = {"filename": "hash_123.jpg"}
        worker = SendReplyWorker(mock_client, c_sess, "Hola amigo", image_paths=[tmp1.name])
        worker.run()
        mock_client.upload_claim_attachment.assert_called_once()
        mock_client.send_claim_message.assert_called_once_with("acc1", "1", "Hola amigo", receiver_role="complainant", attachments=["hash_123.jpg"])

        # 4.1 Test SendReplyWorker routes to mediator when in dispute stage
        mock_client.reset_mock()
        c_dispute_sess = ServiceSession(id="c_2", session_type="claim", stage="dispute", account_id="acc1", account_name="测试店", site_id="MLM")
        worker_dispute = SendReplyWorker(mock_client, c_dispute_sess, "Acepto", image_paths=[])
        worker_dispute.run()
        mock_client.send_claim_message.assert_called_once_with("acc1", "2", "Acepto", receiver_role="mediator", attachments=[])

        # Clean tmp
        try:
            os.remove(tmp1.name)
        except Exception:
            pass

        page.stop()

    def test_service_center_v2_1_69_message_persistence_and_smooth_rendering(self):
        """Test that conversation messages persist in local snapshot and don't flicker on reload."""
        import tempfile
        from service_center.service_center_page import save_sessions_snapshot, load_sessions_snapshot
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_data_dir = Path(tmp_dir)
            with patch("engine.crypto.get_data_dir", return_value=tmp_data_dir):
                msg1 = ServiceMessage(
                    id="m1",
                    sender_role="buyer",
                    sender_name="买家",
                    text="商品有问题，申请退款",
                    created_at="2026-10-15T12:00:00Z",
                )
                sess = ServiceSession(
                    id="c_5594407561",
                    session_type="claim",
                    account_id="acc1",
                    account_name="湖北店",
                    site_id="MLM",
                    item_id="MLM5868800338",
                    item_title="Puerta Plegable",
                    sku="827880081-BEIGE",
                    status="opened",
                    summary="[进行中] [CLAIM] 索赔原因: PDD9942",
                    messages=[msg1],
                )

                # 1. Verify snapshot saves and loads messages
                save_sessions_snapshot([sess])
                loaded = load_sessions_snapshot()
                self.assertEqual(len(loaded), 1)
                self.assertEqual(len(loaded[0].messages), 1)
                self.assertEqual(loaded[0].messages[0].text, "商品有问题，申请退款")

                # 2. Test ServiceCenterPage preserving messages during chunk and loaded callbacks
                mock_client = MagicMock()
                mock_auth = MagicMock()
                mock_auth.list_accounts.return_value = [{"account_id": "acc1", "name": "湖北店"}]
                mock_auth.list_sites.return_value = [{"child_user_id": "c1", "site_id": "MLM"}]

                page = ServiceCenterPage(mock_client, mock_auth)
                page._active_tab = "claim"
                page._all_sessions = [sess]

                # Select the session
                page._display_session_detail(sess)
                self.assertEqual(page._displayed_messages_count, 1)

                # Simulate background chunk callback arriving with empty messages and new SKU
                incoming_chunk_sess = ServiceSession(
                    id="c_5594407561",
                    session_type="claim",
                    account_id="acc1",
                    account_name="湖北店",
                    site_id="MLM",
                    item_id="MLM5868800338",
                    item_title="Puerta Plegable Actualizada",
                    sku="827880081-BEIGE-V2",
                    status="opened",
                    summary="[进行中] [CLAIM] 索赔原因: PDD9942",
                    messages=[],
                )
                # This should NOT crash with context_sku_lbl AttributeError and should preserve sess.messages
                page._on_sessions_chunk([incoming_chunk_sess])
                self.assertEqual(len(page._all_sessions[0].messages), 1)
                self.assertIn("827880081-BEIGE-V2", page.context_meta_lbl.text())

                # Simulate on_sessions_loaded callback arriving with empty messages
                page._on_sessions_loaded([incoming_chunk_sess])
                self.assertEqual(len(page._all_sessions[0].messages), 1)

                # Same session re-displayed should not rebuild or wipe chat layout
                page._display_session_detail(page._all_sessions[0])
                self.assertEqual(page._displayed_messages_count, 1)

                page.stop()

    @patch("service_center.service_center_page.load_sessions_snapshot", return_value=[])
    @patch("service_center.service_center_page.save_sessions_snapshot")
    def test_service_center_v2_1_70_unified_copy_menu_and_bubble_dimension_lock(self, mock_save, mock_load):
        """Test v2.1.70: unified simple copy menu without tooltips, bubble physical dimension lock, and list stability."""
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = [{"account_id": "acc1", "name": "湖北店"}]
        mock_auth.list_sites.return_value = [{"child_user_id": "c1", "site_id": "MLM"}]

        page = ServiceCenterPage(mock_client, mock_auth)
        sess = ServiceSession(
            id="c_558899",
            session_type="claim",
            account_id="acc1",
            account_name="湖北店",
            site_id="MLM",
            item_id="MLM5868800338",
            item_title="Puerta Plegable Tipo Acordeón",
            item_permalink="https://articulo.mercadolibre.com.mx/MLM5868800338",
            order_id="2000018676400878",
            sku="827880081-BEIGE",
            status="opened",
            messages=[
                ServiceMessage(
                    id="m101",
                    sender_role="buyer",
                    sender_name="SELENEDENTISTA13",
                    text="En la publicidad dice puerta plegable y esta es una cortina",
                    created_at="2026-10-15T12:27:00Z",
                    is_seller=False,
                )
            ],
        )
        page._all_sessions = [sess]
        page._display_session_detail(sess)

        # 1. Test unified right-click copy menu on context_link_lbl
        with patch.object(QMenu, "popup") as mock_popup:
            page._show_simple_copy_menu(QPoint(10, 10), page.context_link_lbl)
            self.assertIsNotNone(page._last_context_menu)
            actions = page._last_context_menu.actions()
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].text(), "复制")
            mock_popup.assert_called_once()

            # Trigger copy action
            actions[0].trigger()
            clip_text = QApplication.clipboard().text()
            self.assertIn("MLM5868800338", clip_text)
            self.assertIn("2000018676400878", clip_text)

        # 2. Test bubble physical dimension lock & alignment
        msg = sess.messages[0]
        bubble = page._create_chat_bubble(msg)
        self.assertEqual(bubble.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Maximum)

        # Find bubble_frame inside container
        frames = bubble.findChildren(QFrame)
        self.assertTrue(len(frames) > 0)
        bubble_frame = frames[0]
        self.assertEqual(bubble_frame.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Maximum)

        # Find labels inside bubble
        labels = bubble.findChildren(QLabel)
        self.assertTrue(len(labels) >= 2)
        for lbl in labels:
            self.assertTrue(bool(lbl.alignment() & Qt.AlignmentFlag.AlignTop))

        # 3. Test _clear_chat_bubbles maintains isolated bottom stretch
        page._clear_chat_bubbles()
        self.assertEqual(page.chat_layout.count(), 1)
        self.assertIsNotNone(page.chat_layout.itemAt(0).spacerItem())

        # 4. Test chunk streaming does not clear list or wipe detail when user is viewing session
        with patch.object(page, "_apply_filter") as mock_filter:
            page._on_sessions_chunk([sess])
            # Should NOT call _apply_filter while user has active session
            mock_filter.assert_not_called()

        page.stop()

    @patch("service_center.service_center_page.load_sessions_snapshot", return_value=[])
    @patch("service_center.service_center_page.save_sessions_snapshot")
    def test_service_center_v2_1_71_pure_id_copy_and_independent_chips(self, mock_save, mock_load):
        """Test v2.1.71: independent metadata chips and pure ID copy without prefix/suffix/separator."""
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent, QPointF

        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = [{"account_id": "acc1", "name": "湖北店"}]
        mock_auth.list_sites.return_value = [{"child_user_id": "c1", "site_id": "MLM"}]

        page = ServiceCenterPage(mock_client, mock_auth)
        sess = ServiceSession(
            id="c_998877",
            session_type="claim",
            account_id="acc1",
            account_name="湖北店",
            site_id="MLM",
            item_id="MLM5868800338",
            item_title="Puerta Plegable Tipo Acordeón",
            order_id="2000018676400878",
            pack_id="2000015238595195",
            stage="dispute",
            status="opened",
        )
        page._all_sessions = [sess]
        page._display_session_detail(sess)

        # 1. 验证芯片控件已展示且物理独立
        page.show()
        self.assertFalse(page.tags_row_widget.isHidden())
        self.assertFalse(page.chip_order_lbl.isHidden())
        self.assertFalse(page.chip_pack_lbl.isHidden())
        self.assertFalse(page.chip_item_lbl.isHidden())

        # 2. 验证关联订单号右键复制 -> 100% 纯数字单号
        QApplication.clipboard().clear()
        with patch.object(QMenu, "popup"):
            page.chip_order_lbl.customContextMenuRequested.emit(QPoint(5, 5))
            self.assertIsNotNone(page._last_context_menu)
            actions = page._last_context_menu.actions()
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].text(), "复制")
            actions[0].trigger()
            self.assertEqual(QApplication.clipboard().text(), "2000018676400878")
            self.assertNotIn("关联订单号:", QApplication.clipboard().text())
            self.assertNotIn("|", QApplication.clipboard().text())

        # 3. 验证关联订单号左键单击复制 -> 100% 纯数字单号
        QApplication.clipboard().clear()
        left_click = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(5, 5),
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        page.chip_order_lbl.mousePressEvent(left_click)
        self.assertEqual(QApplication.clipboard().text(), "2000018676400878")

        # 4. 验证商品 ID：左键点击打开浏览器，右键点击复制 100% 纯商品编号
        self.assertNotIn("#60A5FA", page.chip_item_lbl.text())
        self.assertIn("MLM5868800338", page.chip_item_lbl.text())
        self.assertFalse(page.sep_item_order.isHidden())
        self.assertFalse(page.sep_order_pack.isHidden())
        self.assertFalse(page.sep_pack_stage.isHidden())
        self.assertFalse(page.chip_stage_lbl.isHidden())
        self.assertIn("平台官方调解中", page.chip_stage_lbl.text())

        with patch.object(page, "_open_item_in_browser") as mock_open_browser:
            page.chip_item_lbl.mousePressEvent(left_click)
            mock_open_browser.assert_called_once()

        QApplication.clipboard().clear()
        with patch.object(QMenu, "popup"):
            page.chip_item_lbl.customContextMenuRequested.emit(QPoint(5, 5))
            self.assertIsNotNone(page._last_context_menu)
            actions = page._last_context_menu.actions()
            self.assertEqual(len(actions), 1)
            actions[0].trigger()
            self.assertEqual(QApplication.clipboard().text(), "MLM5868800338")
            self.assertNotIn("商品链接:", QApplication.clipboard().text())

        # 5. 验证包裹号左键与右键复制 -> 100% 纯包裹号
        QApplication.clipboard().clear()
        page.chip_pack_lbl.mousePressEvent(left_click)
        self.assertEqual(QApplication.clipboard().text(), "2000015238595195")
        self.assertNotIn("包裹号:", QApplication.clipboard().text())

        # 6. 验证商品标题右键复制 -> 100% 纯商品标题
        QApplication.clipboard().clear()
        with patch.object(QMenu, "popup"):
            page._show_simple_copy_menu(QPoint(5, 5), page.context_title_lbl)
            self.assertIsNotNone(page._last_context_menu)
            actions = page._last_context_menu.actions()
            self.assertEqual(len(actions), 1)
            actions[0].trigger()
            self.assertEqual(QApplication.clipboard().text(), "Puerta Plegable Tipo Acordeón")

        page.stop()

    def test_service_center_v2_1_74_badge_fixed_size_and_macos_close_button(self):
        """Test v2.1.74: fixed badge size (74x22), unified dot badges without emoji, macOS circular close button, and sender name purification."""
        from service_center.service_center_page import ImagePreviewDialog
        from PySide6.QtWidgets import QPushButton

        # 1. 验证 ImagePreviewDialog 的关闭按钮是 macOS 原生圆形纯图标
        dlg = ImagePreviewDialog(title="大图预览测试")
        close_btns = [btn for btn in dlg.findChildren(QPushButton) if btn.text() == "✕"]
        self.assertEqual(len(close_btns), 1)
        close_btn = close_btns[0]
        self.assertEqual(close_btn.width(), 26)
        self.assertEqual(close_btn.height(), 26)
        self.assertNotIn("关闭", close_btn.text())
        dlg.close()

        # 2. 验证会话列表卡片上的状态徽章固定为 74x22，采用大厂小圆点规范且无彩色 Emoji
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = [{"account_id": "acc1", "name": "湖北店"}]
        mock_auth.list_sites.return_value = [{"child_user_id": "c1", "site_id": "MLM"}]
        page = ServiceCenterPage(mock_client, mock_auth)

        # 索赔进行中
        s_claim = ServiceSession(id="c1", session_type="claim", status="opened", account_id="acc1", account_name="湖北店", site_id="MLM", buyer_nickname="BuyerA")
        w_claim = page._create_session_item_widget(s_claim)
        labels = w_claim.findChildren(QLabel)
        badge_claim = [l for l in labels if "● 索赔中" in l.text()]
        self.assertEqual(len(badge_claim), 1)
        self.assertEqual(badge_claim[0].width(), 74)
        self.assertEqual(badge_claim[0].height(), 22)
        self.assertNotIn("⚠️", badge_claim[0].text())

        # 索赔已结案
        s_closed = ServiceSession(id="c2", session_type="claim", status="closed", account_id="acc1", account_name="湖北店", site_id="MLM", buyer_nickname="BuyerB")
        w_closed = page._create_session_item_widget(s_closed)
        badge_closed = [l for l in w_closed.findChildren(QLabel) if "● 已结案" in l.text()]
        self.assertEqual(len(badge_closed), 1)
        self.assertEqual(badge_closed[0].width(), 74)
        self.assertEqual(badge_closed[0].height(), 22)
        self.assertNotIn("✔️", badge_closed[0].text())

        # 提问待回复
        s_q = ServiceSession(id="q1", session_type="question", status="unanswered", account_id="acc1", account_name="湖北店", site_id="MLM", buyer_nickname="BuyerC")
        w_q = page._create_session_item_widget(s_q)
        badge_q = [l for l in w_q.findChildren(QLabel) if "● 待回复" in l.text()]
        self.assertEqual(len(badge_q), 1)
        self.assertEqual(badge_q[0].width(), 74)
        self.assertEqual(badge_q[0].height(), 22)

        # 售后私信不可回复
        s_m = ServiceSession(id="m1", session_type="message", status="blocked", can_reply=False, account_id="acc1", account_name="湖北店", site_id="MLM", buyer_nickname="BuyerD")
        w_m = page._create_session_item_widget(s_m)
        badge_m = [l for l in w_m.findChildren(QLabel) if "● 不可回复" in l.text()]
        self.assertEqual(len(badge_m), 1)
        self.assertEqual(badge_m[0].width(), 74)
        self.assertEqual(badge_m[0].height(), 22)
        self.assertNotIn("⛔", badge_m[0].text())

        # 3. 验证聊天气泡中发件人净名为 "卖家"，绝不带 "(我)"
        seller_msg = ServiceMessage(id="msg_1", sender_role="seller", sender_name="卖家", text="测试回复", created_at="2026-10-17T18:00:00Z", is_seller=True)
        bubble = page._create_chat_bubble(seller_msg)
        bubble_labels = [l.text() for l in bubble.findChildren(QLabel)]
        self.assertTrue(any("卖家" in txt for txt in bubble_labels))
        self.assertFalse(any("(我)" in txt for txt in bubble_labels))

        page.stop()

    def test_mediator_message_bubble_and_refresh_workflow(self):
        """测试美客多官方调解员消息专属卡片渲染与手动/后台静默刷新机制"""
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        # 1. 测试官方调解员气泡渲染
        mediator_msg = ServiceMessage(
            id="med_1",
            sender_role="mediator",
            sender_name="美客多仲裁调解员",
            text="买家要求部分退款30%，请在10月23日前回复",
            text_translated="买家要求部分退款30% [原文: El comprador solicita...]",
            created_at="2026-10-20 14:00:00",
            is_seller=False,
        )
        bubble = page._create_chat_bubble(mediator_msg)
        frames = bubble.findChildren(QFrame)
        mediator_frame = next((f for f in frames if f.objectName() == "mediatorBubble"), None)
        self.assertIsNotNone(mediator_frame, "调解员消息必须拥有 mediatorBubble 专属样式卡片")

        labels = [l.text() for l in bubble.findChildren(QLabel)]
        self.assertTrue(any("平台仲裁调解专员通知" in txt for txt in labels), "必须包含官方仲裁调解专员提示栏")
        self.assertTrue(any("美客多官方调解员" in txt for txt in labels), "必须展示美客多官方调解员身份")
        self.assertTrue(any("买家要求部分退款30%" in txt for txt in labels), "必须正确渲染调解正文")

        # 2. 测试切换会话时 refresh_chat_btn 状态与静默拉取
        claim_sess = ServiceSession(
            id="c_5594407561",
            session_type="claim",
            account_id="acc_1",
            account_name="测试店",
            site_id="MLM",
            messages=[mediator_msg],
        )

        with patch.object(page, "_fetch_conversation_messages") as mock_fetch:
            page._display_session_detail(claim_sess)
            self.assertFalse(page.refresh_chat_btn.isHidden(), "索赔会话必须显示刷新消息按钮")
            self.assertEqual(page.refresh_chat_btn.text(), "⟳ 刷新消息")
            mock_fetch.assert_called_once_with(claim_sess)

        # 3. 测试点击刷新消息按钮触发实时拉取
        with patch.object(page, "_fetch_conversation_messages") as mock_fetch:
            page._on_refresh_chat_clicked()
            mock_fetch.assert_called_once_with(claim_sess)
            self.assertFalse(page.refresh_chat_btn.isEnabled())
            self.assertEqual(page.refresh_chat_btn.text(), "⟳ 正在同步...")

        page.stop()

    def test_claim_affects_reputation_feature(self):
        from engine.client import MercadoClient
        from service_center.models import ServiceSession
        from service_center.service_center_page import (
            ServiceCenterPage,
            save_sessions_snapshot,
            load_sessions_snapshot,
            FetchConversationMessagesWorker,
        )
        import tempfile
        from pathlib import Path

        # 1. Test MercadoClient method
        client = MercadoClient()
        with patch.object(client, "request", return_value={"affects_reputation": "not_affected"}) as mock_req:
            res = client.get_claim_affects_reputation("acc_1", "5594407561")
            self.assertEqual(res["affects_reputation"], "not_affected")
            mock_req.assert_called_once_with("acc_1", "GET", "/post-purchase/v1/claims/5594407561/affects-reputation")

        # 2. Test ServiceSession model property
        sess_clean = ServiceSession(id="c_1", session_type="claim", account_id="acc_1", account_name="店1", site_id="MLM", order_id="2000001", stage="claim", affects_reputation="not_affected")
        sess_impact = ServiceSession(id="c_2", session_type="claim", account_id="acc_1", account_name="店1", site_id="MLM", affects_reputation="affected")
        sess_default = ServiceSession(id="c_3", session_type="claim", account_id="acc_1", account_name="店1", site_id="MLM")
        self.assertEqual(sess_clean.affects_reputation_display, "否")
        self.assertEqual(sess_impact.affects_reputation_display, "是")
        self.assertEqual(sess_default.affects_reputation_display, "")

        # 3. Test snapshot serialization and restoration
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "sessions_snapshot.json"
            with patch("engine.crypto.get_data_dir", return_value=Path(tmp_dir)):
                save_sessions_snapshot([sess_clean, sess_impact])
                loaded = load_sessions_snapshot()
                self.assertEqual(len(loaded), 2)
                self.assertEqual(loaded[0].affects_reputation, "not_affected")
                self.assertEqual(loaded[1].affects_reputation, "affected")

        # 4. Test card rendering with reputation badges
        mock_c = MagicMock()
        mock_a = MagicMock()
        mock_a.list_accounts.return_value = [{"account_id": "acc_1", "display_name": "店1"}]
        mock_a.list_sites.return_value = []
        page = ServiceCenterPage(mock_c, mock_a)
        page.resize(1200, 800)
        page.show()
        QApplication.processEvents()
        page.splitter.setSizes([410, 790])

        # Test splitter protection and list resize mode
        self.assertFalse(page.splitter.isCollapsible(0))
        self.assertFalse(page.splitter.isCollapsible(1))
        self.assertEqual(page.session_list.resizeMode(), QListView.ResizeMode.Adjust)
        self.assertGreaterEqual(page.splitter.sizes()[0], 380)

        card_clean = page._create_session_item_widget(sess_clean)
        clean_labels = [l.text() for l in card_clean.findChildren(QLabel)]
        self.assertTrue(any("影响信誉: 否" in t for t in clean_labels), "不影响信誉时卡片必须显示‘影响信誉: 否’")

        card_impact = page._create_session_item_widget(sess_impact)
        impact_labels = [l.text() for l in card_impact.findChildren(QLabel)]
        self.assertTrue(any("影响信誉: 是" in t for t in impact_labels), "影响信誉时卡片必须显示‘影响信誉: 是’")

        card_default = page._create_session_item_widget(sess_default)
        default_labels = [l.text() for l in card_default.findChildren(QLabel)]
        self.assertFalse(any("影响信誉" in t for t in default_labels), "未获取信誉状态时不展示信誉徽章")

        # 5. Test detail chip view
        page._display_session_detail(sess_clean)
        self.assertFalse(page.chip_rep_lbl.isHidden())
        self.assertIn("不影响信誉", page.chip_rep_lbl.text())
        self.assertFalse(page.sep_stage_rep.isHidden())

        page._display_session_detail(sess_impact)
        self.assertFalse(page.chip_rep_lbl.isHidden())
        self.assertIn("影响信誉", page.chip_rep_lbl.text())

        page._display_session_detail(None)
        self.assertTrue(page.chip_rep_lbl.isHidden())
        self.assertTrue(page.sep_stage_rep.isHidden())

        # 6. Test FetchConversationMessagesWorker fetches affects_reputation
        worker_sess = ServiceSession(id="c_9999", session_type="claim", account_id="acc_1", account_name="店1", site_id="MLM")
        mock_c = MagicMock()
        mock_c.get_claim_messages.return_value = []
        mock_c.get_claim_affects_reputation.return_value = {"affects_reputation": "not_affected"}
        worker = FetchConversationMessagesWorker(mock_c, worker_sess)
        worker.run()
        self.assertEqual(worker_sess.affects_reputation, "not_affected")
        mock_c.get_claim_affects_reputation.assert_called_once_with("acc_1", "9999")

        page.stop()

    def test_chat_bubble_typography_and_duplicate_suppression(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        # 1. Duplicate translation suppression: "aceptar" vs "aceptar"
        dup_msg = ServiceMessage(
            id="m_dup",
            sender_role="seller",
            sender_name="卖家",
            text="aceptar",
            text_translated="aceptar",
            created_at="2026-10-21 01:24:00",
            is_seller=True,
        )
        bubble_dup = page._create_chat_bubble(dup_msg)
        labels_dup = [lbl.text() for lbl in bubble_dup.findChildren(QLabel)]
        self.assertFalse(any("译:" in txt for txt in labels_dup), "相同原词绝不展示 '译: aceptar'")

        # 2. Valid translation typography: no <i> tags, clean sans-serif text
        trans_msg = ServiceMessage(
            id="m_trans",
            sender_role="buyer",
            sender_name="买家",
            text="Hola, cuándo llega mi paquete?",
            text_translated="你好，我的包裹什么时候到达？",
            created_at="2026-10-21 01:25:00",
            is_seller=False,
        )
        bubble_trans = page._create_chat_bubble(trans_msg)
        labels_trans = [lbl.text() for lbl in bubble_trans.findChildren(QLabel)]
        self.assertTrue(any("译: 你好，我的包裹什么时候到达？" in txt for txt in labels_trans))
        self.assertFalse(any("<i>" in txt for txt in labels_trans), "中文译文绝对禁止使用 <i> 标签导致字体变异")

        # 3. System message formatting: clean neutral frame
        sys_msg = ServiceMessage(
            id="m_sys",
            sender_role="system",
            sender_name="系统提示",
            text="⏳ 正在加载历史对话记录，请稍候...",
            created_at="2026-10-21 01:26:00",
            is_seller=False,
        )
        bubble_sys = page._create_chat_bubble(sys_msg)
        frames_sys = bubble_sys.findChildren(QFrame)
        self.assertTrue(any(f.objectName() == "systemBubble" for f in frames_sys))
        labels_sys = [lbl.text() for lbl in bubble_sys.findChildren(QLabel)]
        self.assertFalse(any("平台仲裁调解专员通知" in txt for txt in labels_sys))
        self.assertFalse(any("美客多官方调解员" in txt for txt in labels_sys))

        page.stop()

    def test_post_sale_empty_messages_shows_friendly_hint(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        msg_sess = ServiceSession(
            id="m_pack_100",
            session_type="message",
            account_id="acc_1",
            account_name="测试店铺",
            site_id="MLM",
            order_id="2000019082949204",
            pack_id="2000015622052975",
        )
        page._current_session = msg_sess
        page._on_messages_loaded("m_pack_100", [])

        # Should render empty hint, not arbitration
        bubbles = page.chat_container.findChildren(QLabel)
        all_text = " ".join(b.text() for b in bubbles)
        self.assertIn("暂无历史私信", all_text)
        self.assertNotIn("平台仲裁调解专员通知", all_text)

        page.stop()

    def test_progressive_chunk_rendering_for_large_session_list(self):
        mock_client = MagicMock()
        mock_auth = MagicMock()
        mock_auth.list_accounts.return_value = []
        page = ServiceCenterPage(mock_client, mock_auth)

        # Create 100 dummy claim sessions
        large_claims = [
            ServiceSession(
                id=f"c_{i}",
                session_type="claim",
                account_id="acc_1",
                account_name="测试店",
                site_id="MLB",
                status="opened",
                created_at="2026-10-20T10:00:00Z",
            )
            for i in range(100)
        ]
        page._all_sessions = large_claims

        # Switch to claim tab
        page._switch_tab("claim")

        # Instant chunk: should immediately render first 20 items (responsive within 40ms)
        self.assertEqual(page.session_list.count(), 20)
        self.assertEqual(page.list_summary_lbl.text(), "共 100 笔索赔")
        self.assertTrue(page._chunk_render_timer.isActive())

        # Flush remaining chunks
        page._flush_pending_chunks()
        self.assertEqual(page.session_list.count(), 100)
        self.assertFalse(page._chunk_render_timer.isActive())

        page.stop()

    def test_get_pack_messages_404_returns_empty_dict(self):
        mock_client = MagicMock()
        from engine.client import MercadoClient
        client = MercadoClient(MagicMock())
        with patch.object(client, "request", side_effect=Exception("HTTP 404: Not Found")):
            res = client.get_pack_messages("acc_1", "pack_123")
            self.assertEqual(res, {"messages": [], "paging": {"total": 0}})


if __name__ == "__main__":
    unittest.main()
