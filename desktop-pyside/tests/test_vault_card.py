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

from items.vault_card import ProductCardWidget, ProductCardGrid

def test_card_widget_render():
    item = {
        "id": "PR02026102",
        "title": "3x2m Replacement Canopy Sidewall with Window for Party",
        "site_id": "MLB",
        "price": 19.49,
        "cost_price": 19.60,
        "profit": 0.0,
        "threshold_type": "高阈值",
        "weight": 502,
        "length": 24,
        "width": 19,
        "height": 10,
        "variants_count": 4,
        "group_name": "手搓组",
        "created_at": "2026-10-21 17:21"
    }

    card = ProductCardWidget(item)
    assert card.item_id == "PR02026102"
    assert "19.49" in card.lbl_price.text()
    assert "19.6" in card.lbl_cost.text()
    assert "502" in card.lbl_weight.text()
    assert "24 × 19 × 10" in card.lbl_size.text()
    assert "4" in card.lbl_variants.text()
    assert "手搓组" in card.lbl_group.text()

def test_card_widget_selection():
    item = {"id": "P1", "price": 10.0, "title": "Test Item"}
    card = ProductCardWidget(item)

    selected = []
    card.selection_changed.connect(lambda pid, checked: selected.append((pid, checked)))

    card.checkbox.setChecked(True)
    assert len(selected) == 1
    assert selected[0] == ("P1", True)

def test_card_grid_batch_selection():
    items = [
        {"id": "P1", "title": "Item 1", "price": 10.0},
        {"id": "P2", "title": "Item 2", "price": 20.0},
        {"id": "P3", "title": "Item 3", "price": 30.0}
    ]
    grid = ProductCardGrid()
    grid.set_items(items)

    assert grid.get_selected_ids() == []

    # Select all
    grid.select_all()
    assert set(grid.get_selected_ids()) == {"P1", "P2", "P3"}

    # Deselect all
    grid.deselect_all()
    assert grid.get_selected_ids() == []

    # Invert selection
    grid.select_item("P1", True)
    grid.invert_selection()
    assert set(grid.get_selected_ids()) == {"P2", "P3"}
