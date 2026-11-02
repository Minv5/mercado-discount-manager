from __future__ import annotations

import os
import sys
import unittest
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

from PySide6.QtWidgets import QApplication
from push_service import PushService
from dialogs import SettingsDialog


class PushServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PushService()
        self.service.enabled = True
        self.service.token = "test_token_123"

    def test_update_config(self) -> None:
        settings = {
            "pushEnabled": True,
            "pushToken": "my_token_abc",
            "pushNotifyOrders": False,
            "pushNotifyQuestions": True,
            "pushNotifyMessages": True,
            "pushNotifyClaims": False,
        }
        self.service.update_config(settings)
        self.assertTrue(self.service.enabled)
        self.assertEqual(self.service.token, "my_token_abc")
        self.assertFalse(self.service.notify_orders)
        self.assertTrue(self.service.notify_questions)
        self.assertTrue(self.service.notify_messages)
        self.assertFalse(self.service.notify_claims)

    def test_is_duplicate(self) -> None:
        self.assertFalse(self.service._is_duplicate("key_1", ttl_seconds=10.0))
        self.assertTrue(self.service._is_duplicate("key_1", ttl_seconds=10.0))
        self.assertFalse(self.service._is_duplicate("key_2", ttl_seconds=10.0))

    @patch("urllib.request.urlopen")
    def test_send_pushplus_success(self, mock_urlopen) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"code": 200, "msg": "\xe6\x88\x90\xe5\x8a\x9f", "data": "12345"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ok, msg = self.service.send_pushplus("valid_token", "Test Title", "<p>Test</p>")
        self.assertTrue(ok)
        self.assertEqual(msg, "推送成功")

    @patch("urllib.request.urlopen")
    def test_send_pushplus_failure_code(self, mock_urlopen) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"code": 600, "msg": "Token invalid"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ok, msg = self.service.send_pushplus("invalid_token", "Test", "<p>Fail</p>")
        self.assertFalse(ok)
        self.assertIn("Token invalid", msg)

    def test_empty_token_returns_error(self) -> None:
        ok, msg = self.service.send_pushplus("", "Title", "Content")
        self.assertFalse(ok)
        self.assertIn("Token 未配置", msg)

    @patch.object(PushService, "send_pushplus", return_value=(True, "推送成功"))
    def test_send_test_push(self, mock_send) -> None:
        ok, msg = self.service.send_test_push("test_token")
        self.assertTrue(ok)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(args[0], "test_token")
        self.assertIn("测试成功", args[1])

    @patch.object(PushService, "_dispatch_async")
    def test_domain_notifications(self, mock_dispatch) -> None:
        # Order notification
        self.service.notify_orders = True
        self.service.notify_order("广东店", "MLM", "2000001234", item_title="测试商品", total_amount=199.5, currency_id="MXN")
        mock_dispatch.assert_called_once()
        call_args = mock_dispatch.call_args[0]
        self.assertEqual(call_args[0], "order:2000001234")
        self.assertIn("新订单提醒", call_args[1])
        self.assertIn("2000001234", call_args[2])
        self.assertIn("199.50", call_args[2])

        # Pre-sale question
        mock_dispatch.reset_mock()
        self.service.notify_questions = True
        self.service.notify_question("湖北店", "MLB", "MLB998877", question_text="有现货吗？")
        mock_dispatch.assert_called_once()
        q_args = mock_dispatch.call_args[0]
        self.assertIn("售前咨询提醒", q_args[1])
        self.assertIn("有现货吗？", q_args[2])

        # Post-sale message
        mock_dispatch.reset_mock()
        self.service.notify_messages = True
        self.service.notify_message("湖南店", "MLC", "2000005678", message_text="物流还没到")
        mock_dispatch.assert_called_once()
        m_args = mock_dispatch.call_args[0]
        self.assertIn("售后私信提醒", m_args[1])
        self.assertIn("物流还没到", m_args[2])

        # Dispute claim
        mock_dispatch.reset_mock()
        self.service.notify_claims = True
        self.service.notify_claim("广东店", "MLM", "2000009999", claim_id="CLAIM-123", reason="商品破损")
        mock_dispatch.assert_called_once()
        c_args = mock_dispatch.call_args[0]
        self.assertIn("索赔纠纷告警", c_args[1])
        self.assertIn("商品破损", c_args[2])


class SettingsDialogPushTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_push_tab_ui_and_serialization(self) -> None:
        init_settings = {
            "pushEnabled": True,
            "pushToken": "mock_token_xyz",
            "pushNotifyOrders": True,
            "pushNotifyQuestions": False,
            "pushNotifyMessages": True,
            "pushNotifyClaims": True,
        }
        dlg = SettingsDialog(init_settings, [], [], "")

        # Verify Tab exists and controls are populated
        self.assertEqual(dlg.tabs.tabText(4), "消息推送")
        self.assertTrue(dlg.push_enabled.isChecked())
        self.assertEqual(dlg.push_token.text(), "mock_token_xyz")
        self.assertTrue(dlg.push_notify_orders.isChecked())
        self.assertFalse(dlg.push_notify_questions.isChecked())
        self.assertTrue(dlg.push_notify_messages.isChecked())
        self.assertTrue(dlg.push_notify_claims.isChecked())

        # Verify values export
        values = dlg.values()
        self.assertTrue(values["pushEnabled"])
        self.assertEqual(values["pushToken"], "mock_token_xyz")
        self.assertTrue(values["pushNotifyOrders"])
        self.assertFalse(values["pushNotifyQuestions"])
        self.assertTrue(values["pushNotifyMessages"])
        self.assertTrue(values["pushNotifyClaims"])

        # Test apply_settings_context
        updated = {
            "pushEnabled": False,
            "pushToken": "new_token_123",
            "pushNotifyOrders": False,
            "pushNotifyQuestions": True,
            "pushNotifyMessages": False,
            "pushNotifyClaims": False,
        }
        dlg.apply_settings_context(updated)
        self.assertFalse(dlg.push_enabled.isChecked())
        self.assertEqual(dlg.push_token.text(), "new_token_123")
        self.assertFalse(dlg.push_notify_orders.isChecked())
        self.assertTrue(dlg.push_notify_questions.isChecked())
