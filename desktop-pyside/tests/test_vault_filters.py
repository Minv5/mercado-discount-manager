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
