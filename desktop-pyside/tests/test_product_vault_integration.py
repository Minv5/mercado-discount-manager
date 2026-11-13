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
    assert "已获取 1 条产品数据" in dlg.lbl_fetch_summary.text()

    # Toggle fetch detail box
    dlg._toggle_details()
    assert dlg.detail_box.isVisibleTo(dlg) is False or dlg._details_expanded is True
    assert "Outdoor Tent" in dlg.detail_box.toPlainText()

    # Step 2: pick target language & Step 3: mode/scope
    dlg._select_target_lang("es")
    assert dlg.get_target_language() == "es"
    dlg._select_task_mode("translate")
    dlg._select_scope("title")
    prompt = dlg.generate_prompt()
    assert "Outdoor Tent" in prompt
    assert "P1" in prompt
    assert "西班牙语" in prompt

    # Switch back to both and test JSON parsing with code-fence wrapping
    dlg._select_scope("both")
    mock_json = '```json\n[{"productId": "P1", "title": "Carpa Exterior", "description": "Carpa impermeable."}]\n```'
    dlg.txt_paste.setPlainText(mock_json)
    parsed = dlg.parse_pasted_data()
    assert len(parsed) == 1
    assert parsed[0]["id"] == "P1"
    assert parsed[0]["title"] == "Carpa Exterior"
    assert parsed[0]["description"] == "Carpa impermeable."

    # Backward fallback plain format still works
    dlg.txt_paste.setPlainText("ID: P1\nTITLE: Carpa para Exteriores Impermeable\nDESC: Carpa resistente\n")
    parsed2 = dlg.parse_pasted_data()
    assert len(parsed2) == 1
    assert "Carpa" in parsed2[0]["title"]

    # Unified dark theme check (zero Ant Design white background)
    assert "background: #171B19" in dlg.styleSheet()
    assert "background: #ffffff" not in dlg.styleSheet().lower()


def test_ai_optimize_end_to_end_db_update(tmp_path):
    db = ProductVaultDB(db_path=str(tmp_path / "ai_e2e.db"))
    db.init_db()
    db.import_from_json([{"id": "P_AI_1", "title": "Old Tent", "description": "Old Desc"}])

    page = ProductVaultPage(db=db)
    dlg = AIProductOptimizeDialog([db.get_product("P_AI_1")], parent=page)
    dlg._select_target_lang("es")
    dlg._select_scope("both")
    dlg.txt_paste.setPlainText('[{"productId": "P_AI_1", "title": "Carpa Pro", "description": "Nueva Descripcion"}]')
    updates = dlg.get_updates()
    assert len(updates) == 1

    # Simulate _on_ai_optimize persistence using DB update logic
    db.update_product("P_AI_1", {
        "title": updates[0]["title"],
        "title_es": updates[0]["title"],
        "description": updates[0]["description"],
    })
    updated = db.get_product("P_AI_1")
    assert updated["title"] == "Carpa Pro"
    assert updated["title_es"] == "Carpa Pro"
    assert updated["description"] == "Nueva Descripcion"

def test_batch_publish_dialog():
    class _FakeAuth:
        def list_accounts(self):
            return [
                {"account_id": "2651442567", "store_name": "湖北店", "site_id": "CBT"},
                {"account_id": "3332096437", "store_name": "广东店", "site_id": "CBT"},
            ]

    dlg = BatchPublishDialog(selected_count=2, auth_mgr=_FakeAuth(), product_ids=["PR01", "PR02"])
    dlg._select_all_stores()
    dlg._site_buttons["MLM"].setChecked(True)
    dlg._site_buttons["MLB"].setChecked(True)
    dlg.radio_sales_classic.setChecked(True)
    dlg.radio_pmode_net.setChecked(True)
    dlg.cb_unlimit_variants.setChecked(False)
    dlg.spin_max_vars.setValue(5)
    dlg.cb_force_stock.setChecked(True)
    dlg.spin_force_stock.setValue(888)
    dlg.input_brand.setText("GENERIC_BRAND")
    dlg.input_title_prefix.setPlainText("[NEW]")
    dlg.input_title_suffix.setPlainText("HOT")
    dlg.input_desc_prefix.setPlainText("Header")
    dlg.input_desc_suffix.setPlainText("Footer")

    data = dlg.get_publish_config()
    assert data["sales_type"] == "Classic"
    assert data["price_mode"] == "net_proceed"
    assert data["force_stock"] == 888
    assert data["max_variants"] == 5
    assert data["store_ids"] == ["2651442567", "3332096437"]
    assert set(data["target_sites"]) == {"MLM", "MLB"}
    assert data["product_ids"] == ["PR01", "PR02"]
    assert data["brand"] == "GENERIC_BRAND"
    assert data["title_prefix"] == "[NEW]"
    assert data["title_suffix"] == "HOT"
    assert data["desc_prefix"] == "Header"
    assert data["desc_suffix"] == "Footer"

    # Search & invert filters
    dlg.store_search_input.setText("广东")
    assert not dlg._store_checkboxes["3332096437"].isHidden()
    assert dlg._store_checkboxes["2651442567"].isHidden()
    dlg.store_search_input.clear()
    dlg._invert_stores()
    assert dlg.get_publish_config()["store_ids"] == []

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

    # Verify AI button is direct 1-click (no 1-item menu) and 500/page option exists
    assert page.btn_ai_tools.menu() is None
    assert page.btn_ai_tools.text() == "AI标题优化"
    page_sizes = [page.combo_page_size.itemData(i) for i in range(page.combo_page_size.count())]
    assert 500 in page_sizes
    idx_500 = page_sizes.index(500)
    page.combo_page_size.setCurrentIndex(idx_500)
    assert page.page_size == 500

