from __future__ import annotations

import csv
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

from PySide6.QtWidgets import QApplication

from items.items_page import ItemsPage
from items.models import ItemModel, load_items_snapshot, save_items_snapshot


app = QApplication.instance() or QApplication([])


class TestItemModels(unittest.TestCase):
    def test_item_model_properties(self):
        it = ItemModel(
            id="MLB4922026167",
            title="Espelho Adesivo Flexível 50x100cm",
            price=11.36,
            currency_id="USD",
            status="active",
            available_quantity=45,
            sold_quantity=120,
            sku="MLB4807928701",
            account_id="3408885754",
            account_name="湖南店",
            site_id="MLB",
        )
        self.assertEqual(it.site_display, "🇧🇷 巴西 · MLB")
        self.assertEqual(it.status_display, "在售")
        self.assertEqual(it.price_display, "USD 11.36")

    def test_snapshot_roundtrip(self):
        it1 = ItemModel(id="MCO1", title="商品1", price=10.0, status="active", site_id="MCO")
        it2 = ItemModel(id="MLM2", title="商品2", price=25.5, status="closed", site_id="MLM")

        test_dir = Path("/tmp/test_mdm_items")
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file = test_dir / "items_snapshot.json"

        with patch("items.models.get_items_snapshot_path", return_value=test_file):
            save_items_snapshot([it1, it2])
            loaded = load_items_snapshot()
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].id, "MCO1")
            self.assertEqual(loaded[0].price, 10.0)
            self.assertEqual(loaded[1].id, "MLM2")
            self.assertEqual(loaded[1].status, "closed")

        if test_file.exists():
            test_file.unlink()


class TestItemsPage(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_auth = MagicMock()
        self.mock_auth.list_accounts.return_value = [
            {"account_id": "acc_1", "store_name": "湖北店"},
            {"account_id": "acc_2", "store_name": "湖南店"},
        ]
        self.mock_auth.list_sites.return_value = [
            {"child_user_id": "c_1", "site_id": "MLB"},
        ]

    def test_items_page_initialization_and_filtering(self):
        page = ItemsPage(self.mock_client, self.mock_auth)
        self.assertEqual(page.table.columnCount(), 8)

        # Inject sample items
        it1 = ItemModel(id="MLB1", title="无线蓝牙耳机", sku="SKU-EAR-01", price=19.9, status="active", account_id="acc_1", site_id="MLB")
        it2 = ItemModel(id="MLM2", title="加湿器香薰机", sku="SKU-HUM-02", price=35.0, status="paused", account_id="acc_2", site_id="MLM")
        it3 = ItemModel(id="MCO3", title="户外运动水杯", sku="SKU-CUP-03", price=12.5, status="closed", account_id="acc_1", site_id="MCO")

        page._all_items = [it1, it2, it3]
        page._apply_filter()

        # Initial: All items shown
        self.assertEqual(len(page._filtered_items), 3)
        self.assertEqual(page.table.rowCount(), 3)

        # Filter by status: active
        idx = page.status_combo.findData("active")
        page.status_combo.setCurrentIndex(idx)
        self.assertEqual(len(page._filtered_items), 1)
        self.assertEqual(page._filtered_items[0].id, "MLB1")

        # Reset status & search by query
        page.status_combo.setCurrentIndex(0)
        page.search_input.setText("水杯")
        self.assertEqual(len(page._filtered_items), 1)
        self.assertEqual(page._filtered_items[0].id, "MCO3")

        # Search by SKU
        page.search_input.setText("HUM")
        self.assertEqual(len(page._filtered_items), 1)
        self.assertEqual(page._filtered_items[0].sku, "SKU-HUM-02")

    def test_export_items_csv(self):
        page = ItemsPage(self.mock_client, self.mock_auth)
        it = ItemModel(id="MLB99", title="测试导出商品", sku="SKU99", price=50.0, status="active", account_id="acc_1", account_name="湖北店", site_id="MLB")
        page._filtered_items = [it]

        export_target = "/tmp/test_export_items.csv"
        if os.path.exists(export_target):
            os.remove(export_target)

        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(export_target, "CSV")):
            with patch("PySide6.QtWidgets.QMessageBox.information"):
                page._export_items()

        self.assertTrue(os.path.exists(export_target))
        with open(export_target, "r", encoding="utf-8-sig") as f:
            reader = list(csv.reader(f))
            self.assertGreaterEqual(len(reader), 2)
            self.assertEqual(reader[0][0], "商品 ID")
            self.assertEqual(reader[1][0], "MLB99")
            self.assertEqual(reader[1][1], "SKU99")
            self.assertEqual(reader[1][2], "测试导出商品")

        if os.path.exists(export_target):
            os.remove(export_target)
