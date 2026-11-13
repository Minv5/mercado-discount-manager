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

from items.vault_more_actions import (
    GenerateVariantsDialog,
    ReduceVariantsDialog,
    BatchAttrDialog,
    ReplaceKeywordDialog,
    BatchCategoryDialog,
    CopyToGroupDialog,
    DeleteConfirmDialog,
    DeleteByFilterDialog,
    PreUploadImagesDialog
)

def test_generate_variants_dialog():
    dlg = GenerateVariantsDialog(selected_count=3)
    dlg.input_count.setValue(5)
    dlg.switch_preset.setChecked(False)
    dlg.input_custom.setText("Red, Blue, Green")
    data = dlg.get_data()
    assert data["target_count"] == 5
    assert data["prefer_preset"] is False
    assert data["custom_values"] == ["Red", "Blue", "Green"]

def test_replace_keyword_dialog():
    dlg = ReplaceKeywordDialog(selected_count=12)
    dlg.input_kw.setText("Shoe")
    dlg.input_rep.setText("Sneaker")
    dlg.radio_ignore.setChecked(True)
    dlg.cb_title.setChecked(True)
    dlg.cb_desc.setChecked(False)
    data = dlg.get_data()
    assert data["keyword"] == "Shoe"
    assert data["replace_text"] == "Sneaker"
    assert data["is_ignore_case"] is True
    assert data["in_title"] is True
    assert data["in_desc"] is False

def test_batch_attr_dialog():
    dlg = BatchAttrDialog(selected_count=4)
    dlg.input_brand.setText("Nike")
    dlg.input_model.setText("Air Max")
    data = dlg.get_data()
    assert data["attributes"]["BRAND"] == "Nike"
    assert data["attributes"]["MODEL"] == "Air Max"

def test_delete_confirm_dialog():
    dlg = DeleteConfirmDialog(selected_count=7)
    dlg.radio_backup.setChecked(True)
    dlg.cb_delete_pic.setChecked(True)
    data = dlg.get_data()
    assert data["soft_delete"] is True
    assert data["delete_images"] is True

def test_delete_by_filter_dialog():
    dlg = DeleteByFilterDialog(total_count=150)
    assert "150" in dlg.lbl_msg.text()

def test_pre_upload_images_dialog():
    items = [
        {"id": "P1", "images": ["https://img.test/1.jpg"]},
        {"id": "P2", "images": []}
    ]
    dlg = PreUploadImagesDialog(items)
    assert dlg.total_images == 1
    assert "1" in dlg.lbl_summary.text()
    assert "background: #171B19" in dlg.styleSheet()
    assert "background: #ffffff" not in dlg.styleSheet().lower()


def test_reduce_variants_dialog():
    dlg = ReduceVariantsDialog(selected_count=5)
    dlg.input_count.setValue(3)
    assert dlg.get_data() == {"target_count": 3}
    assert "background: #171B19" in dlg.styleSheet()
