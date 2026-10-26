from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QTableWidget, QTextEdit, QWidget

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui_common import (
    CheckableComboBox,
    ElidedLabel,
    ImagePreviewDialog,
    attach_copy_menu,
    clear_disk_cache,
    clear_memory_cache,
    copy_pixmap,
    copy_pure_text,
    extract_copyable_text,
    field_label,
    get_placeholder_pixmap,
    label_copy_val,
    load_image_async,
    make_click_copy_handler,
    make_contain_pixmap,
    make_rounded_pixmap,
    make_table,
    populate_store_combo,
    section_label,
    show_image_preview,
    show_pure_copy_menu,
)


class UICommonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_clipboard_copy_pure_text_and_pixmap(self):
        copy_pure_text("  hello_world_123  ")
        self.assertEqual(QApplication.clipboard().text(), "hello_world_123")

        pix = QPixmap(10, 10)
        copy_pixmap(pix)
        self.assertFalse(QApplication.clipboard().pixmap().isNull())

    def test_extract_copyable_text(self):
        lbl = QLabel("<b>加粗标题</b>")
        self.assertEqual(extract_copyable_text(lbl), "加粗标题")

        edit = QTextEdit()
        edit.setPlainText("段落纯文本")
        self.assertEqual(extract_copyable_text(edit), "段落纯文本")

        line_edit = QLineEdit("单行文本")
        self.assertEqual(extract_copyable_text(line_edit), "单行文本")

        # Test fallback
        self.assertEqual(extract_copyable_text(None, fallback="默认值"), "默认值")

    def test_show_pure_copy_menu_and_attach(self):
        w = QLabel("目标文本")
        menu = show_pure_copy_menu(w, QPoint(10, 10), getter="静态复制值", popup=True)
        self.assertIsNotNone(menu)
        self.assertEqual(len(menu.actions()), 1)
        self.assertEqual(menu.actions()[0].text(), "复制")

        # Trigger action and check clipboard
        menu.actions()[0].trigger()
        self.assertEqual(QApplication.clipboard().text(), "静态复制值")

        # Test attach_copy_menu
        target = QLabel("右键目标")
        attach_copy_menu(target, get_val_fn=lambda: "自动提取值", popup=True)
        self.assertEqual(target.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)

    def test_make_selectable_label_and_selection_precedence(self):
        from ui_common import make_selectable_label
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF, QEvent

        lbl = QLabel("订单号: 2000018983593552")
        make_selectable_label(lbl, fallback_getter=lambda: "2000018983593552")
        self.assertTrue(lbl.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)

        # 1. When text is selected with mouse, show_pure_copy_menu copies the selected text (not fallback)
        lbl.setSelection(5, 16)  # Selects "2000018983593552"
        self.assertEqual(lbl.selectedText(), "2000018983593552")
        menu = show_pure_copy_menu(lbl, QPoint(5, 5), getter=lambda: "FALLBACK_IGNORE", popup=True)
        menu.actions()[0].trigger()
        self.assertEqual(QApplication.clipboard().text(), "2000018983593552")

        # 2. When partial text is selected, only that part is copied
        lbl.setSelection(5, 6)  # Selects "200001"
        self.assertEqual(lbl.selectedText(), "200001")
        menu2 = show_pure_copy_menu(lbl, QPoint(5, 5), getter=lambda: "FALLBACK_IGNORE", popup=True)
        menu2.actions()[0].trigger()
        self.assertEqual(QApplication.clipboard().text(), "200001")

        # 3. On mouse release, if text is selected, automatically copies selected text
        QApplication.clipboard().clear()
        rel_evt = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        lbl.mouseReleaseEvent(rel_evt)
        self.assertEqual(QApplication.clipboard().text(), "200001")

    def test_make_click_copy_handler(self):
        chip = QLabel("2000018676400878")
        handler = make_click_copy_handler(chip, lambda: "2000018676400878")
        self.assertTrue(callable(handler))

    def test_populate_store_combo_with_dicts_and_objects(self):
        combo = QComboBox()
        accounts_dict = [
            {"account_id": "111", "site_id": "MLM", "store_name": "墨西哥一店"},
            {"account_id": "222", "site_id": "MLB", "store_name": "巴西一店"},
        ]

        populate_store_combo(
            combo,
            accounts=accounts_dict,
            all_label="全部店铺 (聚合)",
            all_value="ALL",
            include_account_id_in_label=True,
        )

        self.assertEqual(combo.count(), 3)
        self.assertEqual(combo.itemText(0), "全部店铺 (聚合)")
        self.assertEqual(combo.itemData(0), "ALL")
        self.assertEqual(combo.itemData(1), "111")
        self.assertIn("111", combo.itemText(1))

        # Test selection retention
        combo.setCurrentIndex(2)
        populate_store_combo(
            combo,
            accounts=accounts_dict,
            all_label="全部店铺 (聚合)",
            all_value="ALL",
            include_account_id_in_label=True,
        )
        self.assertEqual(combo.currentData(), "222")

    def test_widgets_make_table_and_labels(self):
        table = make_table(["A", "B", "C"])
        self.assertIsInstance(table, QTableWidget)
        self.assertEqual(table.columnCount(), 3)
        self.assertEqual(table.horizontalHeaderItem(0).text(), "A")
        self.assertFalse(table.verticalHeader().isVisible())

        sec = section_label("区段标题")
        self.assertEqual(sec.objectName(), "sectionTitle")

        fld = field_label("字段描述")
        self.assertEqual(fld.objectName(), "muted")

    def test_elided_label(self):
        el = ElidedLabel("很长很长很长很长很长很长很长的文本")
        self.assertEqual(el.text(), "很长很长很长很长很长很长很长的文本")
        el.setText("新文本")
        self.assertEqual(el.text(), "新文本")

    def test_checkable_combobox(self):
        cb = CheckableComboBox(placeholder="选择站点")
        items = [("墨西哥 (MLM)", "MLM"), ("巴西 (MLB)", "MLB")]
        cb.set_items(items)
        self.assertEqual(cb.all_keys(), ["MLM", "MLB"])
        self.assertEqual(cb.checked_keys(), ["MLM", "MLB"])

        cb.set_checked_keys(["MLM"])
        self.assertEqual(cb.checked_keys(), ["MLM"])

    def test_image_cache_and_placeholders(self):
        pix = get_placeholder_pixmap(QSize(30, 30), "📦")
        self.assertFalse(pix.isNull())
        self.assertEqual(pix.size(), QSize(30, 30))

        rounded = make_rounded_pixmap(pix, QSize(20, 20), radius=4)
        self.assertFalse(rounded.isNull())

        contained = make_contain_pixmap(pix, QSize(25, 25), radius=2)
        self.assertFalse(contained.isNull())

        clear_memory_cache()
        clear_disk_cache()

    def test_lightbox_dialog(self):
        pix = QPixmap(100, 100)
        pix.fill(Qt.GlobalColor.green)
        dlg = ImagePreviewDialog(pixmap=pix, title="测试大图")
        self.assertEqual(dlg.windowTitle(), "测试大图")
        self.assertFalse(dlg.img_lbl.pixmap().isNull())
        self.assertTrue(dlg.prev_btn.isHidden())
        self.assertTrue(dlg.next_btn.isHidden())

    def test_lightbox_multi_image_gallery(self):
        pix1 = QPixmap(50, 50)
        pix1.fill(Qt.GlobalColor.red)
        pix2 = QPixmap(60, 60)
        pix2.fill(Qt.GlobalColor.blue)
        pix3 = QPixmap(70, 70)
        pix3.fill(Qt.GlobalColor.yellow)

        dlg = ImagePreviewDialog(images=[pix1, pix2, pix3], initial_index=0, title="买家凭据")
        self.assertEqual(dlg.counter_lbl.text(), "1 / 3")
        self.assertFalse(dlg.prev_btn.isEnabled())
        self.assertTrue(dlg.next_btn.isEnabled())

        # Switch to next
        dlg._on_next_clicked()
        self.assertEqual(dlg.counter_lbl.text(), "2 / 3")
        self.assertTrue(dlg.prev_btn.isEnabled())
        self.assertTrue(dlg.next_btn.isEnabled())

        # Switch to next again
        dlg._on_next_clicked()
        self.assertEqual(dlg.counter_lbl.text(), "3 / 3")
        self.assertTrue(dlg.prev_btn.isEnabled())
        self.assertFalse(dlg.next_btn.isEnabled())

        # Switch back to prev
        dlg._on_prev_clicked()
        self.assertEqual(dlg.counter_lbl.text(), "2 / 3")

    def test_lightbox_copy_image_context_menu_and_shortcut(self):
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent

        pix = QPixmap(80, 80)
        pix.fill(Qt.GlobalColor.magenta)
        dlg = ImagePreviewDialog(pixmap=pix, title="待复制图片")

        # 1. 验证大图弹窗保持极简设计，无多余的顶部复制按钮
        self.assertFalse(hasattr(dlg, "copy_btn"))

        # 2. 验证快捷键 Ctrl+C / Cmd+C 触发复制
        QApplication.clipboard().clear()
        evt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        dlg.keyPressEvent(evt)
        self.assertFalse(QApplication.clipboard().pixmap().isNull())

        # 3. 验证图片控件启用了自定义右键菜单，具备快捷复制图片能力
        self.assertEqual(dlg.img_lbl.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)

        # 4. 验证直接调用 copy_current_image 成功
        QApplication.clipboard().clear()
        self.assertTrue(dlg.copy_current_image())
        self.assertFalse(QApplication.clipboard().pixmap().isNull())
        dlg.close()


if __name__ == "__main__":
    unittest.main()
