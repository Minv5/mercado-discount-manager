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

from items.vault_filters import VaultFilterBar, VaultAICapsuleBar

def test_filter_bar_initialization():
    bar = VaultFilterBar()
    assert bar is not None
    f = bar.get_filters()
    assert isinstance(f, dict)
    assert not f.get("keyword")
    assert not f.get("status")
    assert not f.get("site_id")

def test_filter_bar_signals():
    bar = VaultFilterBar()
    triggered = []

    def on_search(f):
        triggered.append(f)

    bar.search_triggered.connect(on_search)

    bar.title_input.setText("test canopy")
    bar.btn_search.click()

    assert len(triggered) == 1
    assert triggered[0]["keyword"] == "test canopy"

    # Reset
    bar.btn_reset.click()
    assert bar.title_input.text() == ""

    # Test toggle collapse
    bar.btn_toggle.click()
    assert bar.is_collapsed is False
    assert "收 起" in bar.btn_toggle.text()
    bar.btn_toggle.click()
    assert bar.is_collapsed is True
    assert "高级筛选" in bar.btn_toggle.text()

def test_ai_capsule_bar_updates():
    capsules = VaultAICapsuleBar()
    counts = {
        "this_page": 50,
        "pending_verify": 10,
        "pending_fill": 5,
        "filled": 35,
        "questionable": 2,
        "size_anomaly": 1,
        "infringement_skip": 0,
        "failed": 0
    }
    capsules.update_counts(counts)
    assert "50" in capsules.capsule_labels["this_page"].text()
    assert "10" in capsules.capsule_labels["pending_verify"].text()
    assert "35" in capsules.capsule_labels["filled"].text()


def test_filter_bar_store_loading_and_selection():
    bar = VaultFilterBar()
    mock_accounts = [
        {"account_id": "ACC100", "store_name": "巴西一号店", "site_id": "MLB"},
        {"account_id": "ACC200", "store_name": "墨西哥旗舰店", "site_id": "MLM"},
    ]
    bar.set_stores(mock_accounts)
    assert bar.store_combo.count() == 3
    assert bar.store_combo.itemText(0) == "全部店铺"
    assert bar.store_combo.itemData(1) == "ACC100"
    assert bar.store_combo.itemData(2) == "ACC200"

    searches = []
    bar.search_triggered.connect(lambda f: searches.append(f))
    bar.store_combo.setCurrentIndex(1)
    assert len(searches) == 1
    assert searches[0]["account_id"] == "ACC100"


def test_filter_bar_net_proceeds_and_lwh_wildcards():
    bar = VaultFilterBar()
    bar.min_net.setText("12.5")
    bar.max_net.setText("45.0")

    # Partial dimensions: 20 * - * - => only length constrained
    bar.length_input.setText("20")
    bar.width_input.setText("-")
    bar.height_input.setText("*")
    f = bar.get_filters()
    assert f["min_net"] == 12.5
    assert f["max_net"] == 45.0
    assert f["length"] == 20.0
    assert "width" not in f
    assert "height" not in f

    # Shorthand pasted directly into length field: "20*-*-"
    bar.length_input.setText("20*-*-")
    bar.width_input.clear()
    bar.height_input.clear()
    f2 = bar.get_filters()
    assert f2["length"] == 20.0
    assert "width" not in f2
    assert "height" not in f2

    # Full L x W x H
    bar.length_input.setText("20")
    bar.width_input.setText("15")
    bar.height_input.setText("10")
    f3 = bar.get_filters()
    assert f3["length"] == 20.0
    assert f3["width"] == 15.0
    assert f3["height"] == 10.0

    # Reset clears all fields cleanly
    bar.btn_reset.click()
    f4 = bar.get_filters()
    assert "min_net" not in f4
    assert "length" not in f4
    assert "width" not in f4
    assert "height" not in f4


def test_filter_bar_manual_create_categories_and_status():
    bar = VaultFilterBar()
    manual_hits = []
    table_hits = []
    bar.manual_create_clicked.connect(lambda: manual_hits.append(1))
    bar.create_table_clicked.connect(lambda: table_hits.append(1))

    bar.btn_manual_create.click()
    bar.btn_create_table.click()
    assert len(manual_hits) == 1
    assert len(table_hits) == 1

    # Dynamic category loading preserves current choice
    bar.set_categories([("CBT1637", "Home > Cushions"), ("CBT3697", "Audio > Headphones")])
    assert bar.category_combo.count() == 3
    bar.category_combo.setCurrentIndex(1)
    assert bar.get_filters()["category_id"] == "CBT1637"
    bar.set_categories([("CBT1637", "Home > Cushions"), ("CBT9999", "Lighting")])
    assert bar.category_combo.currentData() == "CBT1637"

    # Aligned status options
    statuses = [bar.status_combo.itemData(i) for i in range(bar.status_combo.count())]
    for s in ("", "active", "paused", "draft", "infringed", "published"):
        assert s in statuses


