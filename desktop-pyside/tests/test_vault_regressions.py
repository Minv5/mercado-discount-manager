"""Regression tests for vault data-integrity defects found in the 2026-11-11 audit."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])

from items.product_vault_page import ProductVaultPage
from items.vault_db import ProductVaultDB
from items.vault_item_dialogs import SingleProductEditDialog, VariantDrilldownDialog
from items.vault_pricing import net_proceeds

RICH_ATTRS = {
    "BRAND": "Acme",
    "_logs": [{"time": "2026-11-01 10:00:00", "action": "创建", "detail": "", "operator": "x"}],
    "_lang_titles": {"es": "Carpa", "pt": "Barraca"},
    "_variant_headers": [{"key": "Color", "values": ["Rojo"]}],
}


@pytest.fixture
def db(tmp_path):
    vault = ProductVaultDB(db_path=str(tmp_path / "vault.db"))
    vault.init_db()
    return vault


def seed(db, **overrides):
    item = {
        "id": "P1", "title": "Carpa", "description": "Desc larga", "sku": "SKU1",
        "price": 30.0, "cost_price": 12.0, "profit": 18.0, "stock": 5,
        "group_name": "手搓组", "status": "ready", "account_id": "A1",
        "attributes": dict(RICH_ATTRS),
        "variants": [{"kvId": "Rojo", "name": "Rojo", "sku": "SKU1-R", "stock": 3, "images": ["https://x/1.jpg"]}],
    }
    item.update(overrides)
    db.import_from_json([item])
    return db.get_product(item["id"])


def test_net_proceeds_is_cost_plus_profit_and_falls_back_to_price():
    assert net_proceeds({"price": 30, "cost_price": 12, "profit": 6}) == 18
    assert net_proceeds({"price": 30, "cost_price": 0, "profit": 0}) == 30


def test_editing_title_only_keeps_price_cost_and_profit(db):
    prod = seed(db)
    dlg = SingleProductEditDialog(prod)
    dlg.txt_title.setPlainText("Carpa nueva")
    db.update_product("P1", dlg._collect_payload())
    saved = db.get_product("P1")
    assert (saved["price"], saved["cost_price"], saved["profit"]) == (30.0, 12.0, 18.0)
    assert saved["title"] == "Carpa nueva"


def test_editing_net_proceeds_moves_profit_but_not_cost(db):
    prod = seed(db)
    dlg = SingleProductEditDialog(prod)
    assert dlg.spn_net_proceed.value() == 30.0
    dlg.spn_net_proceed.setValue(40.0)
    db.update_product("P1", dlg._collect_payload())
    saved = db.get_product("P1")
    assert (saved["price"], saved["cost_price"], saved["profit"]) == (30.0, 12.0, 28.0)


def test_inline_card_edit_does_not_raise(db):
    seed(db)
    page = ProductVaultPage(db=db, auth=object())
    page._on_card_field_updated("P1", "weight", 250.0)
    page._on_card_field_updated("P1", "size", (10.0, 8.0, 4.0))
    saved = db.get_product("P1")
    assert (saved["weight"], saved["length"], saved["height"]) == (250.0, 10.0, 4.0)


def test_batch_category_keeps_logs_titles_and_variant_headers(db):
    seed(db)
    db.batch_update_category(["P1"], site_id="MLM", category_id="CBT1", category_name="Cat")
    saved = db.get_product("P1")
    assert saved["category_id"] == "CBT1"
    for key, value in RICH_ATTRS.items():
        assert saved["attributes"][key] == value
    assert saved["attributes"]["_site_categories"]["MLM"] == ["CBT1", "Cat"]


def test_batch_price_profit_target_is_stored_as_given(db):
    seed(db)
    db.batch_update_price(["P1"], price_type="profit", adjustment_type="fixed", amount_or_rate=8.0)
    saved = db.get_product("P1")
    assert (saved["price"], saved["cost_price"], saved["profit"]) == (30.0, 12.0, 8.0)


def test_reimporting_exported_sheet_only_changes_the_columns_it_carries(db):
    seed(db)
    db.import_from_excel_rows([{"商品ID": "P1", "标题": "Carpa", "库存": "99"}])
    saved = db.get_product("P1")
    assert saved["stock"] == 99
    assert saved["description"] == "Desc larga"
    assert saved["status"] == "ready"
    assert saved["group_name"] == "手搓组"
    assert saved["cost_price"] == 12.0
    assert len(saved["variants"]) == 1 and saved["variants_count"] == 1
    assert saved["attributes"]["_logs"] == RICH_ATTRS["_logs"]


def test_reimported_net_proceeds_respects_existing_cost(db):
    seed(db)
    db.import_from_excel_rows([{"商品ID": "P1", "净收益(USD)": "20"}])
    saved = db.get_product("P1")
    assert (saved["cost_price"], saved["profit"]) == (12.0, 8.0)


def test_new_sheet_row_uses_net_proceeds_as_price_and_profit(db):
    db.import_from_excel_rows([{"id": "N1", "标题": "Nuevo", "净收益(USD)": "16.5", "account_id": "A9", "归属店铺": "店"}])
    saved = db.get_product("N1")
    assert (saved["price"], saved["cost_price"], saved["profit"]) == (16.5, 0.0, 16.5)
    assert saved["group_name"] == "未分组"


def test_sheet_import_keeps_account_id_so_store_filter_finds_it(db):
    db.import_from_excel_rows([{"id": "N1", "标题": "Nuevo", "account_id": "A9", "store_name": "店"}])
    assert db.query_products(filters={"account_id": "A9"})["total"] == 1


def test_group_filter_separates_named_groups_from_ungrouped(db):
    seed(db)
    seed(db, id="P2", group_name="未分组")
    ungrouped = db.query_products(filters={"group_id": "-1"})["items"]
    assert [p["id"] for p in ungrouped] == ["P2"]
    named = db.query_products(filters={"group_id": "手搓组"})["items"]
    assert [p["id"] for p in named] == ["P1"]


def test_page_offers_existing_groups_in_filter(db):
    seed(db)
    page = ProductVaultPage(db=db, auth=object())
    labels = [page.filter_bar.group_combo.itemText(i) for i in range(page.filter_bar.group_combo.count())]
    assert "手搓组" in labels and labels.count("未分组") == 1


def test_variant_drilldown_returns_edits_and_page_saves_them(db, monkeypatch):
    prod = seed(db)
    dlg = VariantDrilldownDialog(prod)
    assert not dlg.is_modified()
    dlg.table.item(0, 3).setText("7")
    assert dlg.is_modified()
    variants = dlg.get_variants()
    assert variants[0]["stock"] == 7
    assert variants[0]["images"] == ["https://x/1.jpg"]

    page = ProductVaultPage(db=db, auth=object())

    def fake_exec(self):
        self.table.item(0, 3).setText("9")
        self.table.removeRow(0) if False else None
        return True

    monkeypatch.setattr(VariantDrilldownDialog, "exec", fake_exec)
    page._on_variant_drilldown("P1")
    saved = db.get_product("P1")
    assert saved["variants"][0]["stock"] == 9 and saved["variants_count"] == 1


def test_publish_marks_only_ids_left_in_dialog_and_names_chosen_stores(db, monkeypatch):
    import items.product_vault_page as page_mod

    seed(db, status="draft")
    seed(db, id="P2", status="draft")

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return True

        def get_publish_config(self):
            return {"product_ids": ["P2", "GHOST"], "store_ids": ["A1"], "store_names": ["广东店"]}

    messages = []
    monkeypatch.setattr(page_mod, "BatchPublishDialog", FakeDialog)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: messages.append(a[2]))
    page = ProductVaultPage(db=db, auth=object())
    page.card_grid.select_all()
    page._on_publish()
    assert db.get_product("P1")["status"] == "draft"
    assert db.get_product("P2")["status"] == "ready"
    assert "广东店" in messages[0] and "1 个商品" in messages[0]
    assert db.get_product("P2")["attributes"]["_logs"][0]["detail"] == "目标店铺: 广东店"


def test_card_title_is_capped_at_two_lines_and_labels_stay_transparent():
    from PySide6.QtWidgets import QLabel
    from items.vault_card import ProductCardGrid

    long_title = "Protective Case Compatible With Moto G60/g40 Fusion Concha Negra – Pintura Al Óleo Vintage De Flores"
    grid = ProductCardGrid()
    grid.resize(760, 520)
    grid.set_items([{"id": "L1", "title": long_title}, {"id": "S1", "title": "Funda corta"}])
    grid.show()
    app.processEvents()

    long_label = grid.cards["L1"].lbl_title
    assert long_label.text().endswith("…") and long_label.text() != long_title
    assert long_label.full_text() == long_title and long_label.toolTip().startswith(long_title)
    assert long_label.fontMetrics().boundingRect(
        0, 0, long_label.width(), 1000, int(long_label.alignment()) | 0x1000, long_label.text()
    ).height() <= long_label.height()
    assert grid.cards["S1"].lbl_title.text() == "Funda corta"

    # The grid background must not cascade into the cards' labels, and no empty placeholder label is left behind
    assert grid.container.styleSheet().startswith("QWidget#")
    assert all(lbl.text() or lbl.pixmap() is not None for lbl in grid.cards["S1"].findChildren(QLabel) if lbl.isVisible() and lbl.parent() is grid.cards["S1"])
