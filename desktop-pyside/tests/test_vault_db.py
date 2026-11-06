import os
import sys
import tempfile
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from items.vault_db import ProductVaultDB

@pytest.fixture
def db():
    # Use temporary database for test isolation
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    vault = ProductVaultDB(db_path=path)
    vault.init_db()
    yield vault
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

def test_init_and_empty_query(db):
    res = db.query_products()
    assert res["total"] == 0
    assert len(res["items"]) == 0
    assert res["page"] == 1

def test_import_from_json_and_excel(db):
    # Channel 1: JSON import (Collector / Plugin)
    json_data = [
        {
            "id": "PR001",
            "cbt_item_id": "MLB1001",
            "title": "3x2m Replacement Canopy Sidewall",
            "sku": "CANOPY-01",
            "store_name": "Store Alpha",
            "site_id": "MLB",
            "price": 19.49,
            "cost_price": 10.0,
            "stock": 100,
            "weight": 502,
            "length": 24,
            "width": 19,
            "height": 10,
            "images": ["https://img.test/1.jpg"]
        }
    ]
    count1 = db.import_from_json(json_data)
    assert count1 == 1

    # Channel 2: Excel / CSV rows import
    excel_rows = [
        {
            "id": "PR002",
            "title": "Soft Non-Slip Polyester Rocking Chair",
            "sku": "CHAIR-02",
            "store_name": "Store Beta",
            "site_id": "MLM",
            "price": "10.67",
            "cost_price": "13.63",
            "stock": "50",
            "weight": "600",
            "length": "50",
            "width": "15",
            "height": "12"
        }
    ]
    count2 = db.import_from_excel_rows(excel_rows)
    assert count2 == 1

    res = db.query_products()
    assert res["total"] == 2

def test_clone_product(db):
    db.import_from_json([
        {
            "id": "PR001",
            "title": "Sample Product",
            "sku": "SKU-01",
            "price": 25.0,
            "cost_price": 15.0
        }
    ])
    new_id = db.clone_product("PR001", target_group_id="G1", target_group_name="Group 1")
    assert new_id is not None
    assert new_id != "PR001"

    res = db.query_products()
    assert res["total"] == 2
    cloned = db.get_product(new_id)
    assert cloned["group_id"] == "G1"
    assert cloned["group_name"] == "Group 1"
    assert cloned["title"] == "Sample Product"

def test_query_filtering_and_sorting(db):
    db.import_from_json([
        {
            "id": "P1",
            "title": "Camisa Polo Masculina",
            "site_id": "MLB",
            "store_name": "Store Alpha",
            "price": 20.0,
            "cost_price": 10.0,
            "weight": 200,
            "variants_count": 3
        },
        {
            "id": "P2",
            "title": "Reloj Inteligente Deportivo",
            "site_id": "MLM",
            "store_name": "Store Beta",
            "price": 50.0,
            "cost_price": 20.0,
            "weight": 800,
            "variants_count": 0
        }
    ])

    # Filter by site
    res_mlb = db.query_products(filters={"site_id": "MLB"})
    assert res_mlb["total"] == 1
    assert res_mlb["items"][0]["id"] == "P1"

    # Filter by price range
    res_price = db.query_products(filters={"min_price": 30.0})
    assert res_price["total"] == 1
    assert res_price["items"][0]["id"] == "P2"

    # Filter by keyword (case-insensitive substring)
    res_kw = db.query_products(filters={"keyword": "polo"})
    assert res_kw["total"] == 1
    assert res_kw["items"][0]["id"] == "P1"

    # Filter by has_variants
    res_var = db.query_products(filters={"has_variants": "yes"})
    assert res_var["total"] == 1
    assert res_var["items"][0]["id"] == "P1"

def test_batch_update_price(db):
    db.import_from_json([
        {"id": "P1", "price": 10.0, "cost_price": 5.0, "profit": 5.0},
        {"id": "P2", "price": 20.0, "cost_price": 10.0, "profit": 10.0}
    ])

    # 1. 微调: +3 USD
    db.batch_update_price(["P1", "P2"], price_type="price", adjustment_type="tiny", amount_or_rate=3.0)
    p1 = db.get_product("P1")
    p2 = db.get_product("P2")
    assert p1["price"] == 13.0
    assert p2["price"] == 23.0

    # 2. 按百分比: 80% (打8折)
    db.batch_update_price(["P1"], price_type="price", adjustment_type="percent", amount_or_rate=80.0, base_type="price")
    p1 = db.get_product("P1")
    assert round(p1["price"], 2) == 10.40

    # 3. 按固定价格: 99.0
    db.batch_update_price(["P2"], price_type="price", adjustment_type="fixed", amount_or_rate=99.0)
    p2 = db.get_product("P2")
    assert p2["price"] == 99.0

def test_batch_update_stock_and_weight(db):
    db.import_from_json([
        {"id": "P1", "stock": 10, "weight": 100},
        {"id": "P2", "stock": 20, "weight": 200}
    ])

    # Stock
    db.batch_update_stock(["P1", "P2"], stock=999)
    assert db.get_product("P1")["stock"] == 999
    assert db.get_product("P2")["stock"] == 999

    # Weight: fixed
    db.batch_update_weight(["P1"], mode="fixed", value=350)
    assert db.get_product("P1")["weight"] == 350

    # Weight: tiny adjust (-50)
    db.batch_update_weight(["P2"], mode="tiny", value=-50)
    assert db.get_product("P2")["weight"] == 150

    # Weight: random range
    db.batch_update_weight(["P1"], mode="random", min_val=400, max_val=500)
    w = db.get_product("P1")["weight"]
    assert 400 <= w <= 500

def test_batch_update_size_and_volume_estimate(db):
    db.import_from_json([
        {"id": "P1", "weight": 1000, "length": 0, "width": 0, "height": 0},
        {"id": "P2", "weight": 2000, "length": 30, "width": 20, "height": 10}
    ])

    # Manual size
    db.batch_update_size(["P1"], length=25, width=20, height=15)
    p1 = db.get_product("P1")
    assert p1["length"] == 25
    assert p1["width"] == 20
    assert p1["height"] == 15

    # Volume estimate - smart fill (only fill P2 if empty, but P2 has size so it should NOT change)
    db.batch_estimate_volume(["P2"], force_override=False)
    p2 = db.get_product("P2")
    assert p2["length"] == 30

    # Volume estimate - force override
    db.batch_estimate_volume(["P2"], force_override=True)
    p2 = db.get_product("P2")
    assert p2["length"] > 0
    assert p2["width"] > 0
    assert p2["height"] > 0

def test_batch_replace_keyword(db):
    db.import_from_json([
        {
            "id": "P1",
            "title": "Super Waterproof Outdoor Tent",
            "description": "Best Tent for Camping"
        }
    ])

    # Replace "Tent" with "Canopy" in both title and description
    db.batch_replace_keyword(["P1"], keyword="Tent", replace_text="Canopy", is_ignore_case=True, in_title=True, in_desc=True)
    p1 = db.get_product("P1")
    assert "Canopy" in p1["title"]
    assert "Tent" not in p1["title"]
    assert "Canopy" in p1["description"]

def test_batch_generate_variants(db):
    db.import_from_json([
        {"id": "P1", "variants": [], "variants_count": 0}
    ])

    # Target 3 variants with custom values ["Red", "Blue"]
    res = db.batch_generate_variants(["P1"], target_count=3, custom_values=["Red", "Blue"])
    assert res["success"] == 1
    p1 = db.get_product("P1")
    assert p1["variants_count"] == 3
    # 3rd should be auto-padded with Default1
    var_names = [v.get("name") or v.get("value") for v in p1["variants"]]
    assert "Red" in var_names
    assert "Blue" in var_names
    assert "Default1" in var_names

def test_delete_selected_and_delete_by_filter(db):
    db.import_from_json([
        {"id": "P1", "site_id": "MLB", "status": "draft"},
        {"id": "P2", "site_id": "MLB", "status": "draft"},
        {"id": "P3", "site_id": "MLM", "status": "draft"}
    ])

    # 1. Soft delete P1
    db.batch_delete(["P1"], soft_delete=True)
    res = db.query_products()
    assert res["total"] == 2
    assert [p["id"] for p in res["items"]] == ["P2", "P3"]

    # 2. Delete by filter: site_id="MLM"
    del_count = db.delete_by_filter(filters={"site_id": "MLM"}, soft_delete=False)
    assert del_count == 1
    res2 = db.query_products()
    assert res2["total"] == 1
    assert res2["items"][0]["id"] == "P2"
