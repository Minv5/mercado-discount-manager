import os
import sys
from pathlib import Path
import pytest

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

app = QApplication.instance() or QApplication([])

from items.vault_ai_dialog import AIProductOptimizeDialog
from items.vault_publisher import BatchPublishDialog
from items.product_vault_page import ProductVaultPage
from items.vault_db import ProductVaultDB

def test_ai_product_optimize_dialog():
    items = [
        {"id": "P1", "title": "Outdoor Tent", "description": "Good tent"}
    ]
    dlg = AIProductOptimizeDialog(items)
    assert "1" in dlg.windowTitle()
    prompt = dlg.generate_prompt()
    assert "Outdoor Tent" in prompt
    assert "P1" in prompt

    # Test paste response parsing
    mock_ai_output = "ID: P1\nTITLE: Carpa para Exteriores Impermeable\nDESC: Carpa resistente para acampar\n"
    dlg.txt_paste.setPlainText(mock_ai_output)
    parsed = dlg.parse_pasted_data()
    assert len(parsed) == 1
    assert parsed[0]["id"] == "P1"
    assert "Carpa" in parsed[0]["title"]

def test_batch_publish_dialog():
    dlg = BatchPublishDialog(selected_count=3)
    dlg.radio_sales_classic.setChecked(True)
    dlg.cb_force_stock.setChecked(True)
    dlg.spin_force_stock.setValue(888)
    data = dlg.get_publish_config()
    assert data["sales_type"] == "Classic"
    assert data["force_stock"] == 888

def test_product_vault_page_full_lifecycle(tmp_path):
    db_file = str(tmp_path / "vault_test.db")
    db = ProductVaultDB(db_path=db_file)
    db.init_db()
    db.import_from_json([
        {
            "id": "PR_TEST_01",
            "title": "3x2m Replacement Canopy Sidewall",
            "site_id": "MLB",
            "price": 19.49,
            "cost_price": 10.0,
            "weight": 500
        },
        {
            "id": "PR_TEST_02",
            "title": "Soft Polyester Rocking Chair",
            "site_id": "MLM",
            "price": 25.00,
            "cost_price": 15.0,
            "weight": 800
        }
    ])

    page = ProductVaultPage(db=db)
    assert page is not None
    # Verify card grid items loaded
    assert len(page.card_grid.cards) == 2
    assert "PR_TEST_01" in page.card_grid.cards

    # Test Select All in bottom toolbar
    page.btn_select_all.click()
    assert set(page.card_grid.get_selected_ids()) == {"PR_TEST_01", "PR_TEST_02"}
    assert "已选 2/2 条" in page.lbl_selection_count.text()

    # Test Deselect
    page.btn_deselect_all.click()
    assert page.card_grid.get_selected_ids() == []
    assert "已选 0/2 条" in page.lbl_selection_count.text()
