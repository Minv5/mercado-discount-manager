import os
import sys
import json
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

import zipfile
from items.vault_db import ProductVaultDB
from items.vault_item_dialogs import (
    ProductEditorDialog,
    SingleProductEditDialog,
    SingleProductPricingDialog,
    CreateProductDialog,
    TableCreateProductDialog,
    ImportCostDialog,
    VariantDrilldownDialog,
)
from items.vault_variant_widgets import (
    BatchVariantColumnDialog,
    VariantImagePickerDialog,
    compute_batch_sku_updates,
)
from items.product_vault_page import ProductVaultPage


def test_update_product(tmp_path):
    db_file = tmp_path / "test_item_dialogs.db"
    db = ProductVaultDB(db_path=db_file)
    db.init_db()

    item = {
        "id": "PR_TEST_01",
        "title": "Old Title",
        "price": 20.0,
        "cost_price": 0.0,
        "profit": 0.0,
        "weight": 100.0,
    }
    db.import_from_json([item])

    # Test single product update
    success = db.update_product("PR_TEST_01", {
        "title": "New Title",
        "price": 25.0,
        "cost_price": 10.0,
        "profit": 15.0,
        "weight": 250.0,
    })
    assert success is True

    prod = db.get_product("PR_TEST_01")
    assert prod["title"] == "New Title"
    assert prod["price"] == 25.0
    assert prod["cost_price"] == 10.0
    assert prod["profit"] == 15.0
    assert prod["weight"] == 250.0


def test_single_product_edit_dialog(tmp_path):
    item = {
        "id": "PR_EDIT_01",
        "title": "Cojin Decorativo",
        "title_es": "Cojin Decorativo",
        "title_zh": "装饰抱枕",
        "description": "Funda suave",
        "price": 15.0,
        "profit": 15.0,
        "weight": 300.0,
        "length": 10.0,
        "width": 10.0,
        "height": 5.0,
        "group_name": "手搓组",
        "salesman": "Tom",
        "stock": 50,
        "site_id": "MLM",
        "category_id": "CBT1637",
        "category_name": "Home > Cushions",
        "status": "draft",
        "images": ["https://img.example.com/a.jpg"],
        "attributes": {
            "BRAND": "TestBrand",
            "_variant_headers": [{"key": "颜色", "values": ["黑色", "蓝色"]}],
        },
        "variants": [
            {"kvId": "黑色", "netProceed": 15.0, "weight": 300, "length": 10, "width": 10, "height": 5, "sku": "SKU-BLK", "stock": 20, "images": ["https://img.example.com/a.jpg"]},
            {"kvId": "蓝色", "netProceed": 16.5, "weight": 310, "length": 10, "width": 10, "height": 5, "sku": "SKU-BLU", "stock": 30, "images": []},
        ],
    }
    dlg = SingleProductEditDialog(item)
    assert isinstance(dlg, ProductEditorDialog)
    assert dlg.spn_net_proceed.value() == 15.0
    assert dlg.spn_weight.value() == 300.0
    assert dlg.edt_cat_id.text() == "CBT1637"
    assert dlg.edt_attr_brand.text() == "TestBrand"
    assert dlg.variant_section.sku_table.rowCount() == 2
    assert dlg.img_list.count() == 1

    # No fake Nov-6 log entry when no _logs exist
    assert dlg.log_table.rowCount() >= 1
    for r in range(dlg.log_table.rowCount()):
        assert "2026-11-06" not in dlg.log_table.item(r, 0).text()

    # Modify and verify fields
    dlg.txt_title.setPlainText("Cojin Decorativo Updated")
    dlg.spn_net_proceed.setValue(19.5)
    dlg._add_custom_attr_row("COLOR", "Negros")
    dlg._on_submit()

    fields = dlg.get_updated_fields()
    assert fields["title"] == "Cojin Decorativo Updated"
    assert fields["profit"] == 19.5
    assert fields["attributes"]["COLOR"] == "Negros"
    assert fields["variants_count"] == 2
    assert len(fields["images"]) == 1

    # Full SQLite update + reopen roundtrip
    db = ProductVaultDB(db_path=tmp_path / "edit_roundtrip.db")
    db.init_db()
    db.import_from_json([item])
    db.update_product("PR_EDIT_01", fields)
    db.append_product_log("PR_EDIT_01", "编辑商品", "单元测试更新")
    reloaded = db.get_product("PR_EDIT_01")
    dlg2 = SingleProductEditDialog(reloaded)
    assert dlg2.txt_title.toPlainText() == "Cojin Decorativo Updated"
    assert dlg2.spn_net_proceed.value() == 19.5
    assert dlg2.variant_section.sku_table.rowCount() == 2
    assert dlg2.log_table.item(0, 2).text() == "编辑商品"


def test_single_product_pricing_dialog():
    item = {
        "id": "PR_PRICE_01",
        "title": "Test Gazebo",
        "price": 100.0,
        "cost_price": 0.0,
    }
    dlg = SingleProductPricingDialog(item)
    assert dlg.spn_cost_usd.value() == 0.0

    # Simulate 1688 estimate fill
    dlg._fill_1688_estimate()
    assert dlg.spn_cost_usd.value() > 0

    res = dlg.get_pricing_result()
    assert res["cost_price"] > 0
    assert res["profit"] > 0


def test_create_product_dialog(tmp_path):
    class _FakeAuth:
        def list_accounts(self):
            return [
                {"account_id": "2651442567", "store_name": "湖北店", "site_id": "CBT"},
                {"account_id": "3332096437", "store_name": "广东店", "site_id": "CBT"},
            ]

    dlg = CreateProductDialog(auth_mgr=_FakeAuth(), groups=["未分组", "手搓组", "热销组"])
    # Verify real store is selected (no placeholder empty store when auth accounts exist)
    assert dlg.combo_store.currentData() == "2651442567"
    # Net proceeds only (no spn_cost or spn_price selling price fields)
    assert not hasattr(dlg, "spn_cost")
    assert hasattr(dlg, "spn_net_proceed")

    # Fill multi-language Spanish & Chinese titles and descriptions
    dlg.txt_title.setPlainText("Carpa Impermeable Camping")
    dlg.txt_desc.setPlainText("Carpas para exteriores resistentes al agua.")
    dlg._switch_lang("zh")
    dlg.txt_title.setPlainText("户外防水露营帐篷")
    dlg.txt_desc.setPlainText("加厚防雨防风露营帐篷。")

    # Fill net proceed, stock, weight, L*W*H dimensions, site category, attributes
    dlg.spn_net_proceed.setValue(49.99)
    dlg.spn_stock.setValue(80)
    dlg.spn_weight.setValue(1200)
    dlg.spn_length.setValue(30)
    dlg.spn_width.setValue(25)
    dlg.spn_height.setValue(15)
    dlg._switch_cat_site("MLB")
    dlg.edt_cat_id.setText("MLB12345")
    dlg.edt_cat_name.setText("Deportes > Camping")
    dlg.edt_attr_brand.setText("Generic")

    # Add images before selecting variant images
    dlg._append_image_item("https://img.example.com/main.jpg")
    dlg._append_image_item("https://img.example.com/sub.jpg")
    dlg._refresh_image_badges()

    # Add 2 variant dimensions -> generates 2x2 = 4 SKU rows
    dlg.variant_section.add_dimension("颜色", ["黑色", "蓝色"])
    dlg.variant_section.add_dimension("尺码", ["M", "L"])
    dlg.variant_section.rebuild_sku_rows()
    assert dlg.variant_section.sku_table.rowCount() == 4

    # Bind image on row 0 ("黑色_M") and apply to same "黑色" variants
    dlg.variant_section._row_images[0] = ["https://img.example.com/sub.jpg"]
    dlg.variant_section._apply_images_to_targets(0, ["https://img.example.com/sub.jpg"], "黑色", notify=False)

    dlg._on_submit()
    item = dlg.get_created_item()
    assert item is not None
    assert item["title"] == "Carpa Impermeable Camping"
    assert item["title_es"] == "Carpa Impermeable Camping"
    assert item["title_zh"] == "户外防水露营帐篷"
    assert item["price"] == 49.99
    assert item["profit"] == 49.99
    assert item["cost_price"] == 0.0
    assert item["length"] == 30.0
    assert item["width"] == 25.0
    assert item["height"] == 15.0
    assert item["account_id"] == "2651442567"
    assert "湖北" in item["store_name"]
    assert item["category_id"] == "MLB12345"
    assert item["variants_count"] == 4
    assert item["variants"][0]["images"] == ["https://img.example.com/sub.jpg"]
    assert item["variants"][1]["images"] == ["https://img.example.com/sub.jpg"]
    assert item["variants"][2]["images"] == []
    assert len(item["images"]) == 2
    assert item["attributes"]["BRAND"] == "Generic"

    # Persist into DB and verify round-trip
    db = ProductVaultDB(db_path=tmp_path / "create_roundtrip.db")
    db.init_db()
    item["id"] = "PV_ROUNDTRIP_1"
    db.import_from_json([item])
    saved = db.get_product("PV_ROUNDTRIP_1")
    assert saved is not None
    assert saved["title_es"] == "Carpa Impermeable Camping"
    assert saved["variants_count"] == 4
    assert saved["length"] == 30.0


def test_import_cost_dialog():
    dlg = ImportCostDialog()
    dlg.txt_paste.setPlainText("PR01 12.5\nPR02 8.3")
    dlg._confirm_import()

    costs = dlg.get_imported_costs()
    assert len(costs) == 2
    assert costs[0]["id"] == "PR01"
    assert costs[0]["cost_price"] == 12.5
    assert costs[1]["id"] == "PR02"
    assert costs[1]["cost_price"] == 8.3


def test_capsule_filtering(tmp_path):
    db_file = tmp_path / "test_capsules.db"
    db = ProductVaultDB(db_path=db_file)
    db.init_db()

    items = [
        {"id": "P_UNVERIFIED", "title": "Unverified Item", "price": 20.0, "cost_price": 0.0, "profit": 0.0},
        {"id": "P_FILLED", "title": "Filled Item", "price": 30.0, "cost_price": 10.0, "profit": 20.0},
    ]
    db.import_from_json(items)

    page = ProductVaultPage(db=db)
    assert page.total_items == 2

    # Click pending_verify capsule
    page._on_capsule_clicked("pending_verify")
    assert page.total_items == 1
    assert page.current_items[0]["id"] == "P_UNVERIFIED"

    # Click filled capsule
    page._on_capsule_clicked("filled")
    assert page.total_items == 1
    assert page.current_items[0]["id"] == "P_FILLED"

    # Reset by clicking filled capsule again (toggle off)
    page._on_capsule_clicked("filled")
    assert page.total_items == 2


def test_variant_drilldown_dialog():
    item = {
        "id": "PR_VAR_TEST",
        "title": "Elastic Chair Cover Pack",
        "price": 14.5,
        "variants": json.dumps([
            {"name": "Color: Blue", "sku": "PR-VAR-01", "price": 14.5, "stock": 80, "status": "在售"},
            {"name": "Color: Red", "sku": "PR-VAR-02", "price": 15.0, "stock": 45, "status": "在售"},
        ])
    }
    dlg = VariantDrilldownDialog(item)
    assert dlg.table.rowCount() == 2
    assert dlg.table.item(0, 0).text() == "Color: Blue"
    assert dlg.table.item(0, 1).text() == "PR-VAR-01"
    assert dlg.table.item(1, 0).text() == "Color: Red"


def test_compute_batch_sku_updates_and_scoped_modal():
    rows = [
        {"kvId": "黑色_M", "netProceed": 100.0, "sku": "CUSH-BLK", "length": 20, "width": 15, "height": 10, "stock": 50},
        {"kvId": "蓝色_L", "netProceed": 25.0, "sku": "CUSH-BLU", "length": 30, "width": 20, "height": 12, "stock": 10},
    ]

    # Formula + tail replace on netProceed: 100*1.2 + 3.45 = 123.45 -> tail "9" -> 129.45
    res_net = compute_batch_sku_updates(rows, "netProceed", {
        "mode": "formula",
        "multiplier": 1.2,
        "addend": 3.45,
        "subtrahend": 0.0,
        "rounding": "round",
        "precision": 2,
        "tail": "9",
    })
    assert res_net[0]["netProceed"] == 129.45
    # 25 * 1.2 + 3.45 = 33.45 -> tail "9" -> 39.45
    assert res_net[1]["netProceed"] == 39.45

    # Partial size update: change length=40 only, preserve width & height per row
    res_sz = compute_batch_sku_updates(rows, "size", {"size": ["40", "", ""]})
    assert res_sz[0] == {"length": 40.0, "width": 15.0, "height": 10.0}
    assert res_sz[1] == {"length": 40.0, "width": 20.0, "height": 12.0}

    # SKU prefix / replace
    res_sku = compute_batch_sku_updates(rows, "sku", {"mode": "replace", "search": "CUSH-", "text": "PRO-"})
    assert [x["sku"] for x in res_sku] == ["PRO-BLK", "PRO-BLU"]

    # Scoped checklist selection in BatchVariantColumnDialog
    dlg = BatchVariantColumnDialog("stock", rows)
    dlg.radio_selected.setChecked(True)
    dlg.scope_list.item(0).setCheckState(dlg.scope_list.item(0).checkState().__class__.Checked)
    dlg.edt_val.setText("99")
    dlg._on_apply()
    updates = dlg.get_row_updates()
    assert updates == {0: {"stock": 99}}


def test_variant_image_picker_order():
    imgs = ["https://img.test/1.jpg", "https://img.test/2.jpg", "https://img.test/3.jpg"]
    picker = VariantImagePickerDialog(imgs, selected_images=["https://img.test/2.jpg"])
    picker._toggle_image("https://img.test/1.jpg")
    assert picker.get_selected_images() == ["https://img.test/2.jpg", "https://img.test/1.jpg"]


def test_table_create_product_dialog_csv_and_xlsx(tmp_path):
    csv_path = tmp_path / "template.csv"
    TableCreateProductDialog.write_template_csv(str(csv_path))
    parsed_csv = TableCreateProductDialog.parse_spreadsheet_file(str(csv_path))
    assert len(parsed_csv) == 1
    assert "成本" not in parsed_csv[0]
    assert parsed_csv[0]["净收益(USD)"] == "16.50"

    # Build a minimal valid .xlsx with sparse column ref skipping B2
    xlsx_path = tmp_path / "batch_create.xlsx"
    with zipfile.ZipFile(xlsx_path, "w") as zf:
        zf.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<si><t>标题</t></si>"
            "<si><t>描述</t></si>"
            "<si><t>净收益(USD)</t></si>"
            "<si><t>尺寸</t></si>"
            "<si><t>Lampara LED Escritorio</t></si>"
            "<si><t>30*20*15</t></si>"
            "</sst>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1">'
            '<c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="s"><v>1</v></c>'
            '<c r="C1" t="s"><v>2</v></c>'
            '<c r="D1" t="s"><v>3</v></c>'
            "</row>"
            '<row r="2">'
            '<c r="A2" t="s"><v>4</v></c>'
            '<c r="C2"><v>24.8</v></c>'
            '<c r="D2" t="s"><v>5</v></c>'
            "</row>"
            "</sheetData>"
            "</worksheet>",
        )

    parsed_xlsx = TableCreateProductDialog.parse_spreadsheet_file(str(xlsx_path))
    assert len(parsed_xlsx) == 1
    assert parsed_xlsx[0]["标题"] == "Lampara LED Escritorio"
    assert parsed_xlsx[0]["描述"] == ""
    assert parsed_xlsx[0]["净收益(USD)"] == "24.8"
    assert parsed_xlsx[0]["尺寸"] == "30*20*15"

    dlg = TableCreateProductDialog()
    dlg.load_parsed_rows(parsed_xlsx)
    assert dlg.preview_table.rowCount() == 1
    assert dlg.btn_import.isEnabled() is True

    db = ProductVaultDB(db_path=tmp_path / "table_import.db")
    db.init_db()
    cnt = db.import_from_excel_rows(dlg.get_rows())
    assert cnt == 1
    saved = db.query_products()["items"][0]
    assert saved["profit"] == 24.8
    assert saved["length"] == 30.0
    assert saved["width"] == 20.0
    assert saved["height"] == 15.0

