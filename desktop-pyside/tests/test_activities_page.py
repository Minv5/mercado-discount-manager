from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

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

from PySide6.QtWidgets import QApplication

from activities import ActivityPage, ACTIVITY_HEADERS


class TestActivityPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_activity_page_initialization_and_headers(self):
        page = ActivityPage()
        self.assertIsNotNone(page.activity_table)
        self.assertIsNotNone(page.activity_log_box)
        self.assertEqual(page.activity_table.columnCount(), len(ACTIVITY_HEADERS))

    def test_activity_page_populate(self):
        page = ActivityPage()
        mock_acc = MagicMock()
        mock_acc.account_id = "ACC1"
        mock_acc.store_name = "测试店铺1"

        promos = [
            {
                "id": "PROMO_MLM_001",
                "account_id": "ACC1",
                "site_id": "MLM",
                "promotion_type": "DEAL",
                "name": "超级特惠活动",
                "status": "started",
                "total": 42,
            }
        ]

        page.populate(promos, [mock_acc])
        self.assertEqual(page.activity_table.rowCount(), 1)
        self.assertEqual(page.activity_table.item(0, 0).text(), "测试店铺1")
        self.assertIn("墨西哥", page.activity_table.item(0, 1).text())
        self.assertEqual(page.activity_table.item(0, 5).text(), "42")

    def test_activity_page_signals(self):
        page = ActivityPage()
        refresh_called = False
        def on_refresh():
            nonlocal refresh_called
            refresh_called = True

        page.refreshClicked.connect(on_refresh)
        page.activity_refresh_local_btn.click()
        self.assertTrue(refresh_called)


if __name__ == "__main__":
    unittest.main()
