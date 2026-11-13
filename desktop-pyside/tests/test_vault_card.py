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


def test_card_volumetric_calculation_and_badge():
    # Item 1: 50x50x50 cm, weight 1000g -> volumetric = 50*50*50/6000*1000 = 20833g > 1000g -> 抛重
    throw_item = {
        "id": "P_THROW",
        "title": "Large Lightweight Pillow",
        "weight": 1000,
        "length": 50,
        "width": 50,
        "height": 50,
    }
    card_throw = ProductCardWidget(throw_item)
    assert card_throw.lbl_volumetric.text() == "抛重"

    # Item 2: 10x10x5 cm, weight 2000g -> volumetric = 500/6 = 83g < 2000g -> 实重
    actual_item = {
        "id": "P_ACTUAL",
        "title": "Small Heavy Dumbbell",
        "weight": 2000,
        "length": 10,
        "width": 10,
        "height": 5,
    }
    card_actual = ProductCardWidget(actual_item)
    assert card_actual.lbl_volumetric.text() == "实重"


def test_card_signals_and_variant_drilldown():
    item = {
        "id": "PR_DRILL",
        "title": "Multi-Color Chair Cover",
        "variants_count": 4,
        "price": 20.0
    }
    card = ProductCardWidget(item)

    drilldowns = []
    card.variant_drilldown_requested.connect(lambda pid: drilldowns.append(pid))
    card.lbl_variants.mousePressEvent(None)
    assert drilldowns == ["PR_DRILL"]

    # Test image resolution hook
    from PySide6.QtGui import QPixmap
    pm = QPixmap(800, 600)
    card._on_image_loaded(pm)
    assert card.lbl_resolution.text() == "800 × 600"
    assert not card.lbl_resolution.isHidden()


def test_card_inplace_editing_flow():
    item = {
        "id": "PR_EDIT_TEST",
        "title": "Chair Cover",
        "price": 20.0,
        "cost_price": 10.0,
        "length": 20.0,
        "width": 15.0,
        "height": 5.0,
        "weight": 300.0,
    }
    card = ProductCardWidget(item)

    updates = []
    card.field_updated.connect(lambda pid, f, v: updates.append((pid, f, v)))

    # 1. Test In-place Size Edit
    card._start_size_edit()
    assert card.metric_stack.currentIndex() == 1
    assert card.is_in_quick_edit is True

    card.inp_l.setValue(25.0)
    card.inp_w.setValue(22.0)
    card.inp_h.setValue(10.0)
    card._save_size_edit()

    assert card.metric_stack.currentIndex() == 0
    assert card.is_in_quick_edit is False
    assert card.lbl_size.text() == "25 × 22 × 10"
    assert updates[-1] == ("PR_EDIT_TEST", "size", (25.0, 22.0, 10.0))

    # 2. Test In-place Single Price Edit
    card._start_single_edit("售价", "price", 20.0, "$")
    assert card.metric_stack.currentIndex() == 2
    assert card.is_in_quick_edit is True

    card.inp_single.setValue(35.50)
    card._save_single_edit()

    assert card.metric_stack.currentIndex() == 0
    assert card.is_in_quick_edit is False
    assert card.lbl_price.text() == "35.50"
    assert updates[-1] == ("PR_EDIT_TEST", "price", 35.50)


def test_card_image_clicked_zoom(monkeypatch):
    from unittest.mock import MagicMock
    import items.vault_card as vc

    mock_show = MagicMock()
    monkeypatch.setattr(vc, "show_image_preview", mock_show)

    item = {
        "id": "P_ZOOM",
        "title": "Zoom Product",
        "thumbnail": "https://http2.mlstatic.com/D_123.jpg",
        "images": ["https://http2.mlstatic.com/D_123.jpg", "https://http2.mlstatic.com/D_456.jpg"]
    }
    card = ProductCardWidget(item)
    card._on_image_clicked(None)

    assert mock_show.called
    args, kwargs = mock_show.call_args
    assert "https://http2.mlstatic.com/D_123.jpg" in args[0]


def test_to_high_res_url():
    low = "http://http2.mlstatic.com/D_769239-CBT98404758043_112025-I.jpg"
    high = ProductCardWidget._to_high_res_url(low)
    assert high == "http://http2.mlstatic.com/D_769239-CBT98404758043_112025-O.jpg"


def test_card_global_click_selection(monkeypatch):
    from unittest.mock import MagicMock
    import items.vault_card as vc
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest

    mock_show = MagicMock()
    monkeypatch.setattr(vc, "show_image_preview", mock_show)

    item = {
        "id": "P_CLICK_SEL",
        "title": "Selectable Item",
        "price": 25.0,
    }
    card = ProductCardWidget(item)
    card.show()

    selections = []
    card.selection_changed.connect(lambda pid, checked: selections.append((pid, checked)))

    assert not card.checkbox.isChecked()

    # 1. Click card body toggles selection
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(50, 200))
    assert card.checkbox.isChecked() is True
    assert selections[-1] == ("P_CLICK_SEL", True)

    # 2. Click title toggles selection
    QTest.mouseClick(card.lbl_title, Qt.MouseButton.LeftButton)
    assert card.checkbox.isChecked() is False
    assert selections[-1] == ("P_CLICK_SEL", False)

    # 3. Click image directly triggers zoom preview!
    card._on_image_clicked(None)
    assert mock_show.called


def test_responsive_card_grid_fills_width_and_partial_row():
    from items.vault_card import ProductCardGrid

    grid = ProductCardGrid()
    grid.resize(1160, 600)
    grid.show()
    app.processEvents()

    # Full row fills viewport flush to right margin (no ~180px void)
    items = [{"id": f"M{i}", "title": f"Item {i}", "price": 10.0 + i} for i in range(6)]
    grid.set_items(items)
    app.processEvents()

    vp_w = grid.viewport().width()
    cols = grid._current_cols
    assert cols >= 4
    first_row = [grid.cards[f"M{i}"] for i in range(cols)]
    right_edge = first_row[-1].pos().x() + first_row[-1].width()
    assert vp_w - right_edge <= 20
    assert abs(first_row[0].lbl_image.width() - (first_row[0].width() - 16)) <= 4

    # Partial row (only 2 items) must not blow up to half-screen mega-cards
    grid.set_items([{"id": "S1", "title": "Single 1"}, {"id": "S2", "title": "Single 2"}])
    app.processEvents()
    assert grid.cards["S1"].width() <= 300




