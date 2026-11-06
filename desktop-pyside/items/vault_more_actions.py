from __future__ import annotations

from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from items.vault_batch_settings import BaseVaultDialog


class GenerateVariantsDialog(BaseVaultDialog):
    """Batch Variant Generation Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"批量生成 {selected_count} 个产品的变体", parent)
        self.setFixedWidth(500)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(12)

        self.input_count = QSpinBox()
        self.input_count.setRange(1, 100)
        self.input_count.setValue(2)
        form.addRow("目标变体数量:", self.input_count)

        lbl_count_tip = QLabel("备注：只增不减，只会补充变体，不会删除已有变体。")
        lbl_count_tip.setStyleSheet("color: #8c8c8c; font-size: 11px;")
        form.addRow("", lbl_count_tip)

        self.switch_preset = QCheckBox("使用预设变体")
        self.switch_preset.setChecked(True)
        form.addRow("预设方案:", self.switch_preset)

        lbl_preset_tip = QLabel("预设：使用产品分类推荐预设值；自定义：优先使用下方填写的变体值。")
        lbl_preset_tip.setStyleSheet("color: #8c8c8c; font-size: 11px;")
        form.addRow("", lbl_preset_tip)

        self.input_custom = QLineEdit()
        self.input_custom.setPlaceholderText("变体值，逗号分隔，如: 红色, 蓝色, 黑色, XL, XXL")
        form.addRow("自定义变体值:", self.input_custom)

        lbl_custom_tip = QLabel("备注：数量不足时系统将自动按 Default1、Default2… 自动补齐。")
        lbl_custom_tip.setStyleSheet("color: #8c8c8c; font-size: 11px;")
        form.addRow("", lbl_custom_tip)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确认生成")
        btn_ok.setStyleSheet("background: #1677ff; color: #ffffff; border: none; font-weight: bold;")
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)

    def get_data(self) -> Dict[str, Any]:
        raw_custom = self.input_custom.text().strip()
        custom_list = [x.strip() for x in raw_custom.replace("，", ",").split(",") if x.strip()]
        return {
            "target_count": self.input_count.value(),
            "prefer_preset": self.switch_preset.isChecked(),
            "custom_values": custom_list
        }


class BatchAttrDialog(BaseVaultDialog):
    """Batch Common Product Attributes Modification Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"批量修改 {selected_count} 个产品属性", parent)
        self.setFixedWidth(440)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)

        self.input_brand = QLineEdit()
        self.input_brand.setPlaceholderText("留空则保持原品品牌不变")
        form.addRow("品牌 (Brand):", self.input_brand)

        self.input_model = QLineEdit()
        self.input_model.setPlaceholderText("产品型号")
        form.addRow("型号 (Model):", self.input_model)

        self.input_material = QLineEdit()
        self.input_material.setPlaceholderText("主要材质 (如: Polyester, Aluminum)")
        form.addRow("材质 (Material):", self.input_material)

        self.input_color = QLineEdit()
        self.input_color.setPlaceholderText("主要颜色 (如: Black, White)")
        form.addRow("颜色 / 规格:", self.input_color)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确定修改")
        btn_ok.setStyleSheet("background: #1677ff; color: #ffffff; border: none; font-weight: bold;")
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)

    def get_data(self) -> Dict[str, Any]:
        attrs: Dict[str, str] = {}
        if self.input_brand.text().strip():
            attrs["BRAND"] = self.input_brand.text().strip()
        if self.input_model.text().strip():
            attrs["MODEL"] = self.input_model.text().strip()
        if self.input_material.text().strip():
            attrs["MATERIAL"] = self.input_material.text().strip()
        if self.input_color.text().strip():
            attrs["COLOR"] = self.input_color.text().strip()
        return {"attributes": attrs}


class ReplaceKeywordDialog(BaseVaultDialog):
    """Batch Keyword Find & Replace Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"批量替换 {selected_count} 个产品的关键字", parent)
        self.setFixedWidth(440)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)

        self.input_kw = QLineEdit()
        self.input_kw.setPlaceholderText("想要替换的关键词 (必填)")
        form.addRow("关键字:", self.input_kw)

        self.input_rep = QLineEdit()
        self.input_rep.setPlaceholderText("不填则默认替换为空 (即直接删除该词)")
        form.addRow("替换成:", self.input_rep)

        # Ignore case
        case_row = QHBoxLayout()
        self.bg_case = QButtonGroup(self)
        self.radio_ignore = QRadioButton("忽略大小写")
        self.radio_match = QRadioButton("精确匹配大小写")
        self.radio_ignore.setChecked(True)
        self.bg_case.addButton(self.radio_ignore, 1)
        self.bg_case.addButton(self.radio_match, 2)
        case_row.addWidget(self.radio_ignore)
        case_row.addWidget(self.radio_match)
        case_row.addStretch()
        form.addRow("匹配规则:", case_row)

        # Positions
        pos_row = QHBoxLayout()
        self.cb_title = QCheckBox("标题")
        self.cb_desc = QCheckBox("描述")
        self.cb_title.setChecked(True)
        self.cb_desc.setChecked(True)
        pos_row.addWidget(self.cb_title)
        pos_row.addWidget(self.cb_desc)
        pos_row.addStretch()
        form.addRow("替换位置:", pos_row)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确认替换")
        btn_ok.setStyleSheet("background: #1677ff; color: #ffffff; border: none; font-weight: bold;")
        btn_ok.clicked.connect(self._on_confirm)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)

    def _on_confirm(self):
        if not self.input_kw.text().strip():
            QMessageBox.warning(self, "提示", "请输入想要替换的关键字！")
            return
        if not self.cb_title.isChecked() and not self.cb_desc.isChecked():
            QMessageBox.warning(self, "提示", "至少选择一个替换位置（标题或描述）！")
            return
        self.accept()

    def get_data(self) -> Dict[str, Any]:
        return {
            "keyword": self.input_kw.text().strip(),
            "replace_text": self.input_rep.text().strip(),
            "is_ignore_case": self.radio_ignore.isChecked(),
            "in_title": self.cb_title.isChecked(),
            "in_desc": self.cb_desc.isChecked()
        }


class BatchCategoryDialog(BaseVaultDialog):
    """Batch CBT Category Modification Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"批量修改 {selected_count} 个产品的分类", parent)
        self.selected_count = selected_count
        self.setFixedWidth(440)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        self.combo_site = QComboBox()
        self.combo_site.addItem("全球 (CBT)", "CBT")
        self.combo_site.addItem("🇧🇷 巴西 (MLB)", "MLB")
        self.combo_site.addItem("🇲🇽 墨西哥 (MLM)", "MLM")
        form.addRow("分类站点:", self.combo_site)

        self.input_cat_id = QLineEdit()
        self.input_cat_id.setPlaceholderText("如: MLB123456")
        form.addRow("分类代码 (ID):", self.input_cat_id)

        self.input_cat_name = QLineEdit()
        self.input_cat_name.setPlaceholderText("如: 家居 > 户外 > 露营帐篷")
        form.addRow("分类名称:", self.input_cat_name)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确认修改")
        btn_ok.setStyleSheet("background: #1677ff; color: #ffffff; border: none; font-weight: bold;")
        btn_ok.clicked.connect(self._on_confirm)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)

    def _on_confirm(self):
        if not self.input_cat_id.text().strip():
            QMessageBox.warning(self, "提示", "必须输入目标分类ID！")
            return
        ret = QMessageBox.question(
            self,
            "批量修改CBT分类",
            f"确认批量更改 {self.selected_count} 个产品的 CBT分类吗？",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        if ret == QMessageBox.StandardButton.Ok:
            self.accept()

    def get_data(self) -> Dict[str, Any]:
        return {
            "site_id": self.combo_site.currentData(),
            "category_id": self.input_cat_id.text().strip(),
            "category_name": self.input_cat_name.text().strip()
        }


class CopyToGroupDialog(BaseVaultDialog):
    """Clone Products to Specified Group Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"复制产品 - 共 {selected_count} 个产品到指定分组", parent)
        self.setFixedWidth(400)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        self.combo_group = QComboBox()
        self.combo_group.addItem("未分组", "-1")
        self.combo_group.addItem("手搓组", "GRP_MANUAL")
        self.combo_group.addItem("热销铺货组", "GRP_HOT")
        form.addRow("目标分组:", self.combo_group)
        layout.addLayout(form)

        lbl_note = QLabel("注意：复制产品将生成全新独立草稿记录，共享原图资源。")
        lbl_note.setStyleSheet("color: #8c8c8c; font-size: 11px;")
        layout.addWidget(lbl_note)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确认复制")
        btn_ok.setStyleSheet("background: #1677ff; color: #ffffff; border: none; font-weight: bold;")
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)

    def get_data(self) -> Dict[str, Any]:
        return {
            "target_group_id": self.combo_group.currentData(),
            "target_group_name": self.combo_group.currentText()
        }


class DeleteConfirmDialog(BaseVaultDialog):
    """High-Risk Delete Selected Products Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__("删除产品", parent)
        self.selected_count = selected_count
        self.setFixedWidth(440)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel(f"确认删除选中的 {self.selected_count} 个产品？")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(lbl_title)

        layout.addWidget(QLabel("请选择删除方式："))

        self.bg_mode = QButtonGroup(self)
        self.radio_backup = QRadioButton("备份删除（较慢，数据保留3天可恢复）")
        self.radio_direct = QRadioButton("直接删除（数据直接抹除，无法恢复）")
        self.radio_backup.setChecked(True)

        self.bg_mode.addButton(self.radio_backup, 1)
        self.bg_mode.addButton(self.radio_direct, 2)

        layout.addWidget(self.radio_backup)
        layout.addWidget(self.radio_direct)

        self.cb_delete_pic = QCheckBox("同时删除上传的图片（若有）")
        self.cb_delete_pic.setChecked(False)
        layout.addWidget(self.cb_delete_pic)

        lbl_pic_note = QLabel("删除后可节省存储费用，但图片将无法恢复。注意：复制产品共用图片，删除会相互影响。")
        lbl_pic_note.setStyleSheet("color: #8c8c8c; font-size: 11px; margin-left: 20px;")
        lbl_pic_note.setWordWrap(True)
        layout.addWidget(lbl_pic_note)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        self.btn_del = QPushButton("确认删除")
        self.btn_del.setStyleSheet("background: #ff4d4f; color: #ffffff; border: none; font-weight: bold;")
        self.btn_del.clicked.connect(self._on_confirm)
        btn_box.addWidget(self.btn_del)

        layout.addLayout(btn_box)

    def _on_confirm(self):
        if self.radio_direct.isChecked():
            ret = QMessageBox.warning(
                self,
                "确认直接删除",
                "直接删除后数据将无法恢复，是否继续？",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel
            )
            if ret != QMessageBox.StandardButton.Ok:
                return
        self.accept()

    def get_data(self) -> Dict[str, Any]:
        return {
            "soft_delete": self.radio_backup.isChecked(),
            "delete_images": self.cb_delete_pic.isChecked()
        }


class DeleteByFilterDialog(BaseVaultDialog):
    """High-Risk Delete By Query Conditions Dialog."""

    def __init__(self, total_count: int, parent: Optional[QWidget] = None):
        super().__init__("按筛选删除产品", parent)
        self.total_count = total_count
        self.setFixedWidth(460)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        self.lbl_msg = QLabel(
            f"删除当前筛选条件下的全部产品，不需要勾选产品。请先确认筛选条件。\n\n"
            f"直接删除 {self.total_count} 个产品后数据将无法恢复，是否继续？"
        )
        self.lbl_msg.setWordWrap(True)
        self.lbl_msg.setStyleSheet("color: #cf1322; font-size: 13px; line-height: 1.5;")
        layout.addWidget(self.lbl_msg)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确认直接删除")
        btn_ok.setStyleSheet("background: #ff4d4f; color: #ffffff; border: none; font-weight: bold;")
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)


class PreUploadImagesDialog(BaseVaultDialog):
    """Batch Self-Built Image Pre-Upload Dialog."""

    def __init__(self, items: List[Dict[str, Any]], parent: Optional[QWidget] = None):
        super().__init__("自建产品图片预上传", parent)
        self.items = items
        self.setFixedSize(750, 480)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Status summary
        all_imgs = [it for it in self.items if it.get("images")]
        self.total_images = len(all_imgs)

        self.lbl_summary = QLabel(f"待上传: {self.total_images} | 上传中: 0 | 上传成功: 0 | 上传失败: 0")
        self.lbl_summary.setStyleSheet("background: #fafafa; border: 1px solid #f0f0f0; border-radius: 4px; padding: 6px 12px; font-weight: bold;")
        layout.addWidget(self.lbl_summary)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["商品ID", "图片数量", "状态", "说明"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self.table.setRowCount(len(self.items))
        for idx, it in enumerate(self.items):
            pid = str(it.get("id") or "")
            imgs = it.get("images") or []
            img_cnt = str(len(imgs))
            status = "待上传" if imgs else "无图片"

            self.table.setItem(idx, 0, QTableWidgetItem(pid))
            self.table.setItem(idx, 1, QTableWidgetItem(img_cnt))
            self.table.setItem(idx, 2, QTableWidgetItem(status))
            self.table.setItem(idx, 3, QTableWidgetItem("就绪"))

        layout.addWidget(self.table)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_start = QPushButton("开始上传")
        btn_start.setStyleSheet("background: #1677ff; color: #ffffff; border: none; font-weight: bold;")
        btn_start.clicked.connect(self._mock_upload)
        btn_box.addWidget(btn_start)

        btn_close = QPushButton("关 闭")
        btn_close.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_close.clicked.connect(self.accept)
        btn_box.addWidget(btn_close)

        layout.addLayout(btn_box)

    def _mock_upload(self):
        for idx in range(self.table.rowCount()):
            cnt_item = self.table.item(idx, 1)
            if cnt_item and int(cnt_item.text()) > 0:
                self.table.setItem(idx, 2, QTableWidgetItem("上传成功"))
                self.table.setItem(idx, 3, QTableWidgetItem("公网URL已自动回填"))
        self.lbl_summary.setText(f"待上传: 0 | 上传中: 0 | 上传成功: {self.total_images} | 上传失败: 0")
        QMessageBox.information(self, "完成", "自建图片已批量上传并生成公网URL！")
