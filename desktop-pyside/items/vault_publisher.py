from __future__ import annotations

from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from items.vault_batch_settings import BaseVaultDialog


class BatchPublishDialog(BaseVaultDialog):
    """CBT Multi-Store Batch Publish Dialog strictly matching YuanYou Publish wizard."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"上传产品 - 已选 {selected_count} 个待刊登商品", parent)
        self.selected_count = selected_count
        self.setFixedWidth(560)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)

        # 1. 选择店铺
        self.combo_store = QComboBox()
        self.combo_store.addItem("全部已授权店铺", "ALL")
        self.combo_store.addItem("巴西主店铺 (MLB - Store Alpha)", "ALPHA_MLB")
        self.combo_store.addItem("墨西哥主店铺 (MLM - Store Beta)", "BETA_MLM")
        form.addRow("选择店铺:", self.combo_store)

        # 2. 销售类型
        sales_row = QHBoxLayout()
        self.bg_sales = QButtonGroup(self)
        self.radio_sales_prem = QRadioButton("高流量高佣金 (Premium)")
        self.radio_sales_classic = QRadioButton("低流量低佣金 (Classic)")
        self.radio_sales_prem.setChecked(True)
        self.bg_sales.addButton(self.radio_sales_prem, 1)
        self.bg_sales.addButton(self.radio_sales_classic, 2)
        sales_row.addWidget(self.radio_sales_prem)
        sales_row.addWidget(self.radio_sales_classic)
        sales_row.addStretch()
        form.addRow("销售类型:", sales_row)

        # 3. 价格模式
        price_row = QHBoxLayout()
        self.bg_pmode = QButtonGroup(self)
        self.radio_pmode_price = QRadioButton("售价模式")
        self.radio_pmode_net = QRadioButton("净收益模式")
        self.radio_pmode_price.setChecked(True)
        self.bg_pmode.addButton(self.radio_pmode_price, 1)
        self.bg_pmode.addButton(self.radio_pmode_net, 2)
        price_row.addWidget(self.radio_pmode_price)
        price_row.addWidget(self.radio_pmode_net)
        price_row.addStretch()
        form.addRow("价格模式:", price_row)

        # 4. 强制覆盖库存
        stock_row = QHBoxLayout()
        self.cb_force_stock = QCheckBox("启用强制覆盖")
        self.cb_force_stock.setChecked(False)
        self.spin_force_stock = QSpinBox()
        self.spin_force_stock.setRange(0, 999999)
        self.spin_force_stock.setValue(999)
        self.spin_force_stock.setEnabled(False)
        self.cb_force_stock.toggled.connect(self.spin_force_stock.setEnabled)
        stock_row.addWidget(self.cb_force_stock)
        stock_row.addWidget(self.spin_force_stock)
        stock_row.addStretch()
        form.addRow("覆盖库存:", stock_row)

        # 5. UP变体上限
        self.spin_max_vars = QSpinBox()
        self.spin_max_vars.setRange(0, 100)
        self.spin_max_vars.setValue(0)
        self.spin_max_vars.setSpecialValueText("不限制")
        form.addRow("UP变体上限:", self.spin_max_vars)

        # 6. 保修
        guar_row = QHBoxLayout()
        self.combo_guar = QComboBox()
        self.combo_guar.addItem("卖家保修", "seller")
        self.combo_guar.addItem("工厂保修", "factory")
        self.combo_guar.addItem("不保修", "none")
        self.spin_guar_days = QSpinBox()
        self.spin_guar_days.setRange(0, 365)
        self.spin_guar_days.setSuffix(" 天")
        self.spin_guar_days.setValue(30)
        guar_row.addWidget(self.combo_guar)
        guar_row.addWidget(self.spin_guar_days)
        guar_row.addStretch()
        form.addRow("保修设置:", guar_row)

        # 7. 品牌与前后缀
        self.input_brand = QLineEdit()
        self.input_brand.setPlaceholderText("保持商品原值可留空")
        form.addRow("品牌覆盖:", self.input_brand)

        self.input_title_prefix = QLineEdit()
        self.input_title_prefix.setPlaceholderText("标题前缀")
        form.addRow("标题前缀:", self.input_title_prefix)

        self.input_title_suffix = QLineEdit()
        self.input_title_suffix.setPlaceholderText("标题后缀")
        form.addRow("标题后缀:", self.input_title_suffix)

        # 8. 自动追加国内运费
        self.cb_auto_freight = QCheckBox("根据中转运费模板自动追加至售价")
        self.cb_auto_freight.setChecked(True)
        form.addRow("运费计算:", self.cb_auto_freight)

        layout.addLayout(form)

        # Bottom Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_submit = QPushButton("🚀 开始批量上传刊登")
        btn_submit.setStyleSheet("background: #1677ff; color: #ffffff; border: none; font-weight: bold; padding: 6px 20px;")
        btn_submit.clicked.connect(self._on_submit)
        btn_box.addWidget(btn_submit)

        layout.addLayout(btn_box)

    def _on_submit(self):
        ret = QMessageBox.question(
            self,
            "确认批量上传",
            f"准备将选中的 {self.selected_count} 个商品正式发布刊登至美客多店铺！\n\n"
            f"销售模式: {'Premium' if self.radio_sales_prem.isChecked() else 'Classic'}\n"
            f"店铺: {self.combo_store.currentText()}\n\n"
            "确认立即开始刊登任务吗？",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        if ret == QMessageBox.StandardButton.Ok:
            self.accept()

    def get_publish_config(self) -> Dict[str, Any]:
        return {
            "store_id": self.combo_store.currentData(),
            "sales_type": "Premium" if self.radio_sales_prem.isChecked() else "Classic",
            "price_mode": "price" if self.radio_pmode_price.isChecked() else "net_proceed",
            "force_stock": self.spin_force_stock.value() if self.cb_force_stock.isChecked() else None,
            "max_variants": self.spin_max_vars.value() if self.spin_max_vars.value() > 0 else None,
            "guarantee_type": self.combo_guar.currentData(),
            "guarantee_days": self.spin_guar_days.value(),
            "brand": self.input_brand.text().strip(),
            "title_prefix": self.input_title_prefix.text().strip(),
            "title_suffix": self.input_title_suffix.text().strip(),
            "auto_freight": self.cb_auto_freight.isChecked()
        }
