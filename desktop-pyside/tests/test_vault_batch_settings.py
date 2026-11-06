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

from items.vault_batch_settings import (
    PriceSettingDialog,
    StockSettingDialog,
    WeightSettingDialog,
    ManualSizeDialog,
    VolumeEstimateDialog
)

def test_price_setting_dialog():
    dlg = PriceSettingDialog(selected_count=5)
    assert "5" in dlg.windowTitle()
    # Test tiny adjustment
    dlg.radio_price_target.setChecked(True) # 售价
    dlg.radio_mode_tiny.setChecked(True)
    dlg.input_tiny.setText("2.5")
    data = dlg.get_data()
    assert data["price_type"] == "price"
    assert data["adjustment_type"] == "tiny"
    assert data["amount_or_rate"] == 2.5

def test_stock_setting_dialog():
    dlg = StockSettingDialog(selected_count=10)
    dlg.input_stock.setValue(150)
    data = dlg.get_data()
    assert data["stock"] == 150

def test_weight_setting_dialog():
    dlg = WeightSettingDialog(selected_count=3)
    # Mode fixed
    dlg.radio_mode_fixed.setChecked(True)
    dlg.input_fixed.setValue(650)
    data = dlg.get_data()
    assert data["mode"] == "fixed"
    assert data["value"] == 650

def test_size_setting_dialogs():
    # Manual
    manual_dlg = ManualSizeDialog(selected_count=8)
    manual_dlg.input_length.setValue(30)
    manual_dlg.input_width.setValue(25)
    manual_dlg.input_height.setValue(15)
    m_data = manual_dlg.get_data()
    assert m_data["length"] == 30
    assert m_data["width"] == 25
    assert m_data["height"] == 15

    # Estimate
    est_dlg = VolumeEstimateDialog(selected_count=8)
    est_dlg.radio_smart.setChecked(True)
    e_data = est_dlg.get_data()
    assert e_data["force_override"] is False

    est_dlg.radio_force.setChecked(True)
    e_data2 = est_dlg.get_data()
    assert e_data2["force_override"] is True
