from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
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


def _asset_url(filename: str) -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return str(root / "assets" / filename).replace("\\", "/")


_CHECKMARK_PNG_URL = _asset_url("checkmark-white.png")
_RADIO_DOT_PNG_URL = _asset_url("radio-dot-green.png")


DARK_TIP_LABEL_QSS = "color: #9A9589; font-size: 11px; background: transparent;"
DARK_WARN_LABEL_QSS = "color: #F59E0B; font-size: 11px; background: transparent;"
DARK_DANGER_LABEL_QSS = "color: #F87171; font-size: 13px; line-height: 1.5; background: transparent;"


def style_cancel_button(btn: QPushButton) -> None:
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet("""
        QPushButton {
            background: #18201C;
            color: #C8C3B7;
            border: 1px solid #4E472F;
            border-radius: 6px;
            padding: 6px 16px;
            font-size: 12px;
            font-weight: 500;
        }
        QPushButton:hover { background: #252A25; border-color: #6E5D2A; color: #F6F3EA; }
    """)


def style_primary_button(btn: QPushButton) -> None:
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet("""
        QPushButton {
            background: #2F6B3F;
            color: #FFFFFF;
            border: 1px solid #356F45;
            border-radius: 6px;
            padding: 6px 18px;
            font-size: 12px;
            font-weight: 700;
        }
        QPushButton:hover { background: #356F45; border-color: #4ADE80; }
        QPushButton:disabled { background: #1C231F; color: #777266; border-color: #363225; }
    """)


def style_danger_button(btn: QPushButton) -> None:
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet("""
        QPushButton {
            background: #991B1B;
            color: #FFFFFF;
            border: 1px solid #EF4444;
            border-radius: 6px;
            padding: 6px 18px;
            font-size: 12px;
            font-weight: 700;
        }
        QPushButton:hover { background: #B91C1C; border-color: #F87171; }
    """)


def build_dark_dialog_qss() -> str:
    return f"""
        QDialog {{
            background: #171B19;
            color: #E6E2D8;
        }}
        QLabel {{
            font-size: 13px;
            color: #C8C3B7;
            background: transparent;
        }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
            border: 1px solid #4E472F;
            border-radius: 6px;
            padding: 5px 8px;
            font-size: 12px;
            background: #18201C;
            color: #E6E2D8;
            selection-background-color: #203A2B;
            selection-color: #FFFFFF;
        }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            min-height: 28px;
        }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
        QPlainTextEdit:focus, QTextEdit:focus {{
            border-color: #2F6B3F;
            background: #1C231F;
        }}
        QComboBox::drop-down {{
            border: none;
            width: 22px;
            background: transparent;
        }}
        QComboBox QAbstractItemView {{
            background: #1C231F;
            color: #E6E2D8;
            border: 1px solid #4E472F;
            selection-background-color: #203A2B;
            selection-color: #FFFFFF;
            outline: none;
        }}
        QCheckBox, QRadioButton {{
            color: #E6E2D8;
            spacing: 7px;
            background: transparent;
            font-size: 12px;
        }}
        QCheckBox::indicator {{
            width: 15px;
            height: 15px;
            border-radius: 3px;
            border: 1px solid #6E5D2A;
            background: #18201C;
        }}
        QCheckBox::indicator:checked {{
            background: #2F6B3F;
            border-color: #4ADE80;
            image: url("{_CHECKMARK_PNG_URL}");
        }}
        QListWidget::indicator {{
            width: 15px;
            height: 15px;
            border-radius: 3px;
            border: 1px solid #6E5D2A;
            background: #141816;
        }}
        QListWidget::indicator:checked {{
            background: #2F6B3F;
            border-color: #4ADE80;
            image: url("{_CHECKMARK_PNG_URL}");
        }}
        QRadioButton::indicator {{
            width: 15px;
            height: 15px;
            border-radius: 8px;
            border: 1px solid #6E5D2A;
            background: #18201C;
        }}
        QRadioButton::indicator:checked {{
            background: #203A2B;
            border-color: #4ADE80;
            image: url("{_RADIO_DOT_PNG_URL}");
        }}
        QTableWidget {{
            background: #18201C;
            color: #E6E2D8;
            border: 1px solid #4E472F;
            border-radius: 6px;
            gridline-color: #2E362F;
            font-size: 12px;
        }}
        QTableWidget::item {{
            padding: 4px 6px;
            color: #E6E2D8;
        }}
        QTableWidget::item:selected {{
            background: #203A2B;
            color: #FFFFFF;
        }}
        QHeaderView::section {{
            background: #202820;
            color: #C8C3B7;
            font-weight: 600;
            font-size: 12px;
            border: none;
            border-bottom: 1px solid #4E472F;
            border-right: 1px solid #2E362F;
            padding: 6px;
        }}
        QTableCornerButton::section {{
            background: #202820;
            border: none;
            border-bottom: 1px solid #4E472F;
        }}
        QScrollArea {{
            background: transparent;
            border: none;
        }}
        QScrollBar:vertical {{
            background: #171B19;
            width: 8px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: #4E472F;
            min-height: 28px;
            border-radius: 4px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
    """


class BaseVaultDialog(QDialog):
    """Base dialog with unified Black-Gold dark theme styling."""

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setStyleSheet(build_dark_dialog_qss())


class PriceSettingDialog(BaseVaultDialog):
    """Batch Price Modification Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"批量编辑价格（已选 {selected_count} 条）", parent)
        self.selected_count = selected_count
        self.setFixedWidth(440)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. 要修改的价格
        lbl_target = QLabel("要修改的价格:")
        lbl_target.setStyleSheet("font-weight: bold; color: #F6F3EA;")
        layout.addWidget(lbl_target)

        target_row = QHBoxLayout()
        self.bg_target = QButtonGroup(self)
        self.radio_price_target = QRadioButton("售价")
        self.radio_cost_target = QRadioButton("成本")
        self.radio_profit_target = QRadioButton("利润")
        self.radio_price_target.setChecked(True)

        self.bg_target.addButton(self.radio_price_target, 1)
        self.bg_target.addButton(self.radio_cost_target, 2)
        self.bg_target.addButton(self.radio_profit_target, 3)

        target_row.addWidget(self.radio_price_target)
        target_row.addWidget(self.radio_cost_target)
        target_row.addWidget(self.radio_profit_target)
        target_row.addStretch()
        layout.addLayout(target_row)

        # 2. 价格调整方式
        lbl_mode = QLabel("价格调整方式:")
        lbl_mode.setStyleSheet("font-weight: bold; color: #F6F3EA;")
        layout.addWidget(lbl_mode)

        mode_row = QHBoxLayout()
        self.bg_mode = QButtonGroup(self)
        self.radio_mode_tiny = QRadioButton("微调")
        self.radio_mode_percent = QRadioButton("按百分比")
        self.radio_mode_fixed = QRadioButton("按固定价格")
        self.radio_mode_tiny.setChecked(True)

        self.bg_mode.addButton(self.radio_mode_tiny, 1)
        self.bg_mode.addButton(self.radio_mode_percent, 2)
        self.bg_mode.addButton(self.radio_mode_fixed, 3)

        mode_row.addWidget(self.radio_mode_tiny)
        mode_row.addWidget(self.radio_mode_percent)
        mode_row.addWidget(self.radio_mode_fixed)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Input fields container
        self.input_tiny = QLineEdit()
        self.input_tiny.setPlaceholderText("正数上调，负数下调 (如 2 或 -1.5)")
        layout.addWidget(self.input_tiny)

        # Percent options (hidden by default)
        self.percent_box = QWidget()
        p_layout = QVBoxLayout(self.percent_box)
        p_layout.setContentsMargins(0, 0, 0, 0)
        p_layout.setSpacing(6)

        base_row = QHBoxLayout()
        base_row.addWidget(QLabel("基准价格:"))
        self.combo_base = QComboBox()
        self.combo_base.addItem("售价", "price")
        self.combo_base.addItem("成本", "cost_price")
        base_row.addWidget(self.combo_base)
        base_row.addStretch()
        p_layout.addLayout(base_row)

        self.input_percent = QLineEdit()
        self.input_percent.setPlaceholderText("比例百分比 (如 80 表示打8折，120 表示加价20%)")
        p_layout.addWidget(self.input_percent)

        layout.addWidget(self.percent_box)
        self.percent_box.setVisible(False)

        # Fixed price input (hidden by default)
        self.fixed_box = QWidget()
        f_layout = QVBoxLayout(self.fixed_box)
        f_layout.setContentsMargins(0, 0, 0, 0)
        self.input_fixed = QLineEdit()
        self.input_fixed.setPlaceholderText("统一修改为此固定价格 (USD)")
        f_layout.addWidget(self.input_fixed)
        lbl_fixed_warn = QLabel("⚠ 注意：该方式会将所选产品的价格统一修改成一样的值，请谨慎操作")
        lbl_fixed_warn.setStyleSheet(DARK_WARN_LABEL_QSS)
        f_layout.addWidget(lbl_fixed_warn)

        layout.addWidget(self.fixed_box)
        self.fixed_box.setVisible(False)

        # Connect radio mode switch
        self.bg_mode.idToggled.connect(self._on_mode_switched)

        # Bottom Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.btn_cancel = QPushButton("取 消")
        style_cancel_button(self.btn_cancel)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_ok = QPushButton("确 认")
        style_primary_button(self.btn_ok)
        self.btn_ok.clicked.connect(self._on_confirm)
        btn_box.addWidget(self.btn_ok)

        layout.addLayout(btn_box)

    def _on_mode_switched(self, mode_id: int, checked: bool):
        if not checked:
            return
        self.input_tiny.setVisible(mode_id == 1)
        self.percent_box.setVisible(mode_id == 2)
        self.fixed_box.setVisible(mode_id == 3)

    def _on_confirm(self):
        ret = QMessageBox.warning(
            self,
            "确认批量修改",
            f"确认批量更改 {self.selected_count} 条商品价格吗？\n该操作将直接更新本地数据库，是否继续？",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel
        )
        if ret == QMessageBox.StandardButton.Ok:
            self.accept()

    def get_data(self) -> Dict[str, Any]:
        target_map = {1: "price", 2: "cost_price", 3: "profit"}
        target = target_map.get(self.bg_target.checkedId(), "price")

        mode_id = self.bg_mode.checkedId()
        if mode_id == 1:
            val = float(self.input_tiny.text().strip() or "0")
            return {"price_type": target, "adjustment_type": "tiny", "amount_or_rate": val}
        elif mode_id == 2:
            val = float(self.input_percent.text().strip() or "100")
            base = self.combo_base.currentData()
            return {"price_type": target, "adjustment_type": "percent", "amount_or_rate": val, "base_type": base}
        else:
            val = float(self.input_fixed.text().strip() or "0")
            return {"price_type": target, "adjustment_type": "fixed", "amount_or_rate": val}


class StockSettingDialog(BaseVaultDialog):
    """Batch Stock Modification Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"批量修改共 {selected_count} 个商品库存", parent)
        self.setFixedWidth(360)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        self.input_stock = QSpinBox()
        self.input_stock.setRange(0, 999999)
        self.input_stock.setSuffix(" 个")
        self.input_stock.setValue(100)
        form.addRow("库存:", self.input_stock)
        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("取 消")
        style_cancel_button(btn_cancel)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确定更新")
        style_primary_button(btn_ok)
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def get_data(self) -> Dict[str, Any]:
        return {"stock": self.input_stock.value()}


class WeightSettingDialog(BaseVaultDialog):
    """Batch Weight Modification Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"批量修改共 {selected_count} 个商品重量", parent)
        self.setFixedWidth(400)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_mode = QLabel("重量更新方式:")
        lbl_mode.setStyleSheet("font-weight: bold; color: #F6F3EA;")
        layout.addWidget(lbl_mode)

        mode_row = QHBoxLayout()
        self.bg_mode = QButtonGroup(self)
        self.radio_mode_fixed = QRadioButton("固定")
        self.radio_mode_random = QRadioButton("区间内随机")
        self.radio_mode_tiny = QRadioButton("微调重量")
        self.radio_mode_fixed.setChecked(True)

        self.bg_mode.addButton(self.radio_mode_fixed, 1)
        self.bg_mode.addButton(self.radio_mode_random, 2)
        self.bg_mode.addButton(self.radio_mode_tiny, 3)

        mode_row.addWidget(self.radio_mode_fixed)
        mode_row.addWidget(self.radio_mode_random)
        mode_row.addWidget(self.radio_mode_tiny)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Fixed box
        self.fixed_box = QWidget()
        f_layout = QHBoxLayout(self.fixed_box)
        f_layout.setContentsMargins(0, 0, 0, 0)
        self.input_fixed = QSpinBox()
        self.input_fixed.setRange(1, 999999)
        self.input_fixed.setSuffix(" g")
        self.input_fixed.setValue(500)
        f_layout.addWidget(QLabel("重量:"))
        f_layout.addWidget(self.input_fixed)
        f_layout.addStretch()
        layout.addWidget(self.fixed_box)

        # Random box
        self.random_box = QWidget()
        r_layout = QHBoxLayout(self.random_box)
        r_layout.setContentsMargins(0, 0, 0, 0)
        self.input_min = QSpinBox()
        self.input_min.setRange(1, 999999)
        self.input_min.setSuffix(" g")
        self.input_min.setValue(300)
        self.input_max = QSpinBox()
        self.input_max.setRange(1, 999999)
        self.input_max.setSuffix(" g")
        self.input_max.setValue(800)
        r_layout.addWidget(QLabel("区间:"))
        r_layout.addWidget(self.input_min)
        r_layout.addWidget(QLabel("~"))
        r_layout.addWidget(self.input_max)
        r_layout.addStretch()
        layout.addWidget(self.random_box)
        self.random_box.setVisible(False)

        # Tiny box
        self.tiny_box = QWidget()
        t_layout = QVBoxLayout(self.tiny_box)
        t_layout.setContentsMargins(0, 0, 0, 0)
        self.input_tiny = QSpinBox()
        self.input_tiny.setRange(-999999, 999999)
        self.input_tiny.setSuffix(" g")
        self.input_tiny.setValue(50)
        t_layout.addWidget(self.input_tiny)
        lbl_note = QLabel("备注：正数表示上调重量，负数表示下调重量")
        lbl_note.setStyleSheet(DARK_TIP_LABEL_QSS)
        t_layout.addWidget(lbl_note)
        layout.addWidget(self.tiny_box)
        self.tiny_box.setVisible(False)

        self.bg_mode.idToggled.connect(self._on_mode_changed)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("取 消")
        style_cancel_button(btn_cancel)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确定更新")
        style_primary_button(btn_ok)
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def _on_mode_changed(self, mode_id: int, checked: bool):
        if not checked:
            return
        self.fixed_box.setVisible(mode_id == 1)
        self.random_box.setVisible(mode_id == 2)
        self.tiny_box.setVisible(mode_id == 3)

    def get_data(self) -> Dict[str, Any]:
        mode_id = self.bg_mode.checkedId()
        if mode_id == 1:
            return {"mode": "fixed", "value": float(self.input_fixed.value())}
        elif mode_id == 2:
            return {"mode": "random", "min_val": float(self.input_min.value()), "max_val": float(self.input_max.value())}
        else:
            return {"mode": "tiny", "value": float(self.input_tiny.value())}


class ManualSizeDialog(BaseVaultDialog):
    """Manual Dimensions Setup Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"批量修改共 {selected_count} 个商品尺寸", parent)
        self.setFixedWidth(400)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QHBoxLayout()
        form.setSpacing(10)

        self.input_length = QSpinBox()
        self.input_length.setRange(1, 999)
        self.input_length.setSuffix(" cm")
        self.input_length.setValue(20)

        self.input_width = QSpinBox()
        self.input_width.setRange(1, 999)
        self.input_width.setSuffix(" cm")
        self.input_width.setValue(20)

        self.input_height = QSpinBox()
        self.input_height.setRange(1, 999)
        self.input_height.setSuffix(" cm")
        self.input_height.setValue(20)

        form.addWidget(QLabel("长:"))
        form.addWidget(self.input_length)
        form.addWidget(QLabel("宽:"))
        form.addWidget(self.input_width)
        form.addWidget(QLabel("高:"))
        form.addWidget(self.input_height)
        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("取 消")
        style_cancel_button(btn_cancel)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确定更新")
        style_primary_button(btn_ok)
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def get_data(self) -> Dict[str, Any]:
        return {
            "length": float(self.input_length.value()),
            "width": float(self.input_width.value()),
            "height": float(self.input_height.value())
        }


class VolumeEstimateDialog(BaseVaultDialog):
    """Estimate Volume by Weight Dialog."""

    def __init__(self, selected_count: int, parent: Optional[QWidget] = None):
        super().__init__(f"按重量估算体积 - 共 {selected_count} 个商品", parent)
        self.setFixedWidth(420)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_mode = QLabel("覆盖模式:")
        lbl_mode.setStyleSheet("font-weight: bold; color: #F6F3EA;")
        layout.addWidget(lbl_mode)

        self.bg_mode = QButtonGroup(self)
        self.radio_smart = QRadioButton("智能填充（仅填充无尺寸）")
        self.radio_force = QRadioButton("强制覆盖（含已有尺寸）")
        self.radio_smart.setChecked(True)

        self.bg_mode.addButton(self.radio_smart, 1)
        self.bg_mode.addButton(self.radio_force, 2)

        layout.addWidget(self.radio_smart)
        lbl_smart_tip = QLabel("  ✓ 智能填充：仅对尚未填写尺寸的产品进行估算，已有尺寸的不做修改。")
        lbl_smart_tip.setStyleSheet(DARK_TIP_LABEL_QSS)
        layout.addWidget(lbl_smart_tip)

        layout.addWidget(self.radio_force)
        lbl_force_tip = QLabel("  ⚠ 强制覆盖：无论产品是否已有尺寸，都会重新按重量计算并覆盖。")
        lbl_force_tip.setStyleSheet(DARK_WARN_LABEL_QSS)
        layout.addWidget(lbl_force_tip)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("取 消")
        style_cancel_button(btn_cancel)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("确定生成")
        style_primary_button(btn_ok)
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def get_data(self) -> Dict[str, Any]:
        return {"force_override": self.radio_force.isChecked()}
