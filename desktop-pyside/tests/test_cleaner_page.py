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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cleaner import CleanerPage, CleanerTableModel, CLEANER_HEADERS
from engine.item_cleaner import ScannedItemRecord


class TestCleanerPageAndModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cleaner_table_model_basic(self):
        model = CleanerTableModel(CLEANER_HEADERS)
        self.assertEqual(model.rowCount(), 0)
        self.assertEqual(model.columnCount(), len(CLEANER_HEADERS))

        rec = ScannedItemRecord(
            item_id="CBT100",
            account_id="ACC1",
            site_id="MLM",
            title="测试商品",
            status="active",
            store_name="店铺1",
            visits=0,
            has_sales=False,
            is_selected_for_delete=True,
            score=55.0,
            level_wording="差",
            unmet_reasons=["评分不达标"],
        )
        model.set_records([rec])
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.data(model.index(0, 2), Qt.ItemDataRole.DisplayRole), "CBT100")
        self.assertEqual(model.data(model.index(0, 0), Qt.ItemDataRole.CheckStateRole), Qt.CheckState.Checked)

        # Toggle check state
        model.setData(model.index(0, 0), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        self.assertFalse(rec.is_selected_for_delete)

    def test_cleaner_table_model_safe_selection(self):
        model = CleanerTableModel(CLEANER_HEADERS)
        rec1 = ScannedItemRecord(item_id="CBT1", account_id="ACC1", site_id="MLM", title="A", status="active", visits=0, has_sales=False)
        rec2 = ScannedItemRecord(item_id="CBT2", account_id="ACC1", site_id="MLM", title="B", status="active", visits=10, has_sales=True, sold_quantity=5)
        model.set_records([rec1, rec2])

        skipped = model.select_all_safe()
        self.assertEqual(skipped, 1)  # rec2 with sales is safely protected
        self.assertTrue(rec1.is_selected_for_delete)
        self.assertFalse(rec2.is_selected_for_delete)

        model.unselect_all()
        self.assertFalse(rec1.is_selected_for_delete)
        self.assertFalse(rec2.is_selected_for_delete)

    def test_cleaner_page_widget_initialization(self):
        mock_engine = MagicMock()
        page = CleanerPage(engine=mock_engine)

        self.assertIsNotNone(page.cleaner_table)
        self.assertIsNotNone(page.cleaner_model)
        self.assertIsNotNone(page.cleaner_log_box)
        self.assertTrue(page.cleaner_visits_check.isChecked())
        self.assertTrue(page.cleaner_score_check.isChecked())
        self.assertTrue(page.cleaner_grace_check.isChecked())
        self.assertFalse(page.cleaner_wipe_store_check.isChecked())

        # Toggle wipe store mode
        page.cleaner_wipe_store_check.setChecked(True)
        self.assertFalse(page.cleaner_wipe_notice.isHidden())
        self.assertFalse(page.cleaner_visits_check.isEnabled())
        self.assertFalse(page.cleaner_score_check.isEnabled())

        page.cleaner_wipe_store_check.setChecked(False)
        self.assertTrue(page.cleaner_wipe_notice.isHidden())
        self.assertTrue(page.cleaner_visits_check.isEnabled())
        self.assertTrue(page.cleaner_score_check.isEnabled())


if __name__ == "__main__":
    unittest.main()
