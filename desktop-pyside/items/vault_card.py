from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

COUNTRY_NAMES = {
    "MLB": "🇧🇷 巴西",
    "MLM": "🇲🇽 墨西哥",
    "MLC": "🇨🇱 智利",
    "MCO": "🇨🇴 哥伦比亚",
    "MLA": "🇦🇷 阿根廷",
    "MLU": "🇺🇾 乌拉圭",
    "MPE": "🇵🇪 秘鲁",
}


class ProductCardWidget(QFrame):
    """Card widget faithfully modeled after YuanYou product cards."""
    selection_changed = Signal(str, bool)
    edit_clicked = Signal(str)
    verify_clicked = Signal(str)

    def __init__(self, item: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.item = item
        self.item_id = str(item.get("id") or "")
        self.is_selected = False

        self.setFixedWidth(210)
        self.setObjectName("ProductCardWidget")
        self._update_card_style(selected=False)

        self._init_ui()

    def _update_card_style(self, selected: bool):
        self.is_selected = selected
        border_color = "#1677ff" if selected else "#e8e8e8"
        bg_color = "#f6faff" if selected else "#ffffff"
        self.setStyleSheet(f"""
            QFrame#ProductCardWidget {{
                background: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
        """)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 1. Top Image container with overlay Checkbox
        img_container = QWidget()
        img_container.setFixedHeight(170)
        img_layout = QVBoxLayout(img_container)
        img_layout.setContentsMargins(0, 0, 0, 0)

        # Main thumbnail
        self.lbl_image = QLabel(img_container)
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setStyleSheet("background: #f5f5f5; border-radius: 6px;")
        self.lbl_image.setFixedHeight(170)

        images = self.item.get("images") or []
        if images and isinstance(images, list) and images[0]:
            self.lbl_image.setText("📷 图片")
        else:
            self.lbl_image.setText("📷 无图片")

        # Top-left Checkbox
        self.checkbox = QCheckBox(img_container)
        self.checkbox.setChecked(False)
        self.checkbox.setStyleSheet("""
            QCheckBox {
                background: transparent;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        self.checkbox.move(6, 6)
        self.checkbox.toggled.connect(self._on_check_toggled)

        # Top-right Variant thumbnail indicator
        if int(self.item.get("variants_count") or 0) > 0:
            var_badge = QLabel("变体", img_container)
            var_badge.setStyleSheet("background: rgba(0,0,0,0.55); color: #fff; font-size: 10px; border-radius: 3px; padding: 1px 4px;")
            var_badge.move(170, 6)

        layout.addWidget(img_container)

        # 2. Site & Threshold row
        site_id = str(self.item.get("site_id") or "MLB")
        site_str = COUNTRY_NAMES.get(site_id, f"🌐 {site_id}")
        threshold_str = str(self.item.get("threshold_type") or "高阈值")

        site_row = QHBoxLayout()
        site_row.setContentsMargins(0, 0, 0, 0)
        lbl_site = QLabel(f"按 {site_str}")
        lbl_site.setStyleSheet("color: #595959; font-size: 11px;")
        site_row.addWidget(lbl_site)

        site_row.addStretch()

        is_high = "高" in threshold_str
        thresh_color = "#d46b08" if is_high else "#0958d9"
        thresh_bg = "#fff7e6" if is_high else "#e6f4ff"
        lbl_thresh = QLabel(threshold_str)
        lbl_thresh.setStyleSheet(f"background: {thresh_bg}; color: {thresh_color}; border-radius: 3px; padding: 1px 4px; font-size: 10px;")
        site_row.addWidget(lbl_thresh)

        layout.addLayout(site_row)

        # 3. ID, Copy, and Verify row
        id_row = QHBoxLayout()
        id_row.setContentsMargins(0, 0, 0, 0)
        id_row.setSpacing(4)

        short_id = self.item_id[:13] + "..." if len(self.item_id) > 13 else self.item_id
        lbl_id = QLabel(f"ID: {short_id}")
        lbl_id.setStyleSheet("color: #1677ff; font-size: 11px; font-family: monospace;")
        id_row.addWidget(lbl_id)

        btn_copy = QPushButton("📋")
        btn_copy.setFixedSize(18, 18)
        btn_copy.setToolTip("复制完整ID")
        btn_copy.setStyleSheet("background: transparent; border: none; font-size: 11px; cursor: pointer;")
        btn_copy.clicked.connect(self._copy_id)
        id_row.addWidget(btn_copy)

        id_row.addStretch()

        btn_verify = QPushButton("🤖 核价")
        btn_verify.setStyleSheet("""
            QPushButton {
                background: #e6f4ff;
                color: #0958d9;
                border: 1px solid #91caff;
                border-radius: 4px;
                padding: 1px 6px;
                font-size: 11px;
            }
            QPushButton:hover { background: #bae0ff; }
        """)
        btn_verify.clicked.connect(lambda: self.verify_clicked.emit(self.item_id))
        id_row.addWidget(btn_verify)

        layout.addLayout(id_row)

        # 4. 6-Metrics Matrix Box (2 rows x 3 cols)
        metric_box = QFrame()
        metric_box.setStyleSheet("background: #fafafa; border: 1px solid #f0f0f0; border-radius: 4px; padding: 4px;")
        grid = QGridLayout(metric_box)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        # Row 1 headers & values
        price = float(self.item.get("price") or 0.0)
        cost = float(self.item.get("cost_price") or 0.0)
        profit = float(self.item.get("profit") or round(price - cost, 2))

        cost_color = "#cf1322" if cost >= price else "#595959"
        profit_color = "#389e0d" if profit > 0 else "#8c8c8c"

        lbl_hdr_price = QLabel("售价 USD")
        lbl_hdr_price.setStyleSheet("color: #8c8c8c; font-size: 9px;")
        lbl_hdr_cost = QLabel("成本 USD")
        lbl_hdr_cost.setStyleSheet("color: #8c8c8c; font-size: 9px;")
        lbl_hdr_profit = QLabel("利润 USD")
        lbl_hdr_profit.setStyleSheet("color: #8c8c8c; font-size: 9px;")

        grid.addWidget(lbl_hdr_price, 0, 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(lbl_hdr_cost, 0, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(lbl_hdr_profit, 0, 2, Qt.AlignmentFlag.AlignCenter)

        self.lbl_price = QLabel(f"{price:.2f}")
        self.lbl_price.setStyleSheet("color: #262626; font-weight: bold; font-size: 12px;")
        self.lbl_cost = QLabel(f"{cost:.2f}")
        self.lbl_cost.setStyleSheet(f"color: {cost_color}; font-size: 12px;")
        self.lbl_profit = QLabel(f"{profit:.2f}")
        self.lbl_profit.setStyleSheet(f"color: {profit_color}; font-size: 12px;")

        grid.addWidget(self.lbl_price, 1, 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.lbl_cost, 1, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.lbl_profit, 1, 2, Qt.AlignmentFlag.AlignCenter)

        # Row 2 headers & values
        weight = float(self.item.get("weight") or 0.0)
        l = int(self.item.get("length") or 0)
        w = int(self.item.get("width") or 0)
        h = int(self.item.get("height") or 0)
        var_count = int(self.item.get("variants_count") or 0)

        lbl_hdr_w = QLabel("重量 g")
        lbl_hdr_w.setStyleSheet("color: #8c8c8c; font-size: 9px;")
        lbl_hdr_sz = QLabel("尺寸 cm")
        lbl_hdr_sz.setStyleSheet("color: #8c8c8c; font-size: 9px;")
        lbl_hdr_var = QLabel("变体")
        lbl_hdr_var.setStyleSheet("color: #8c8c8c; font-size: 9px;")

        grid.addWidget(lbl_hdr_w, 2, 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(lbl_hdr_sz, 2, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(lbl_hdr_var, 2, 2, Qt.AlignmentFlag.AlignCenter)

        self.lbl_weight = QLabel(f"{int(weight)}")
        self.lbl_weight.setStyleSheet("color: #595959; font-size: 11px;")
        self.lbl_size = QLabel(f"{l} × {w} × {h}")
        self.lbl_size.setStyleSheet("color: #595959; font-size: 10px;")
        self.lbl_variants = QLabel(f"{var_count}")
        self.lbl_variants.setStyleSheet("color: #1677ff; font-weight: bold; font-size: 11px;")

        grid.addWidget(self.lbl_weight, 3, 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.lbl_size, 3, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.lbl_variants, 3, 2, Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(metric_box)

        # 5. Product Title (2-line wrap)
        self.lbl_title = QLabel(str(self.item.get("title") or "未命名商品"))
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setFixedHeight(34)
        self.lbl_title.setStyleSheet("font-size: 11px; color: #262626; line-height: 1.3;")
        layout.addWidget(self.lbl_title)

        # 6. Footer (Group, Time, Editor)
        footer_row1 = QHBoxLayout()
        footer_row1.setContentsMargins(0, 0, 0, 0)
        grp = str(self.item.get("group_name") or "未分组")
        self.lbl_group = QLabel(f"分组: {grp}")
        self.lbl_group.setStyleSheet("color: #8c8c8c; font-size: 10px;")
        footer_row1.addWidget(self.lbl_group)

        footer_row1.addStretch()

        dt = str(self.item.get("created_at") or "")[:16]
        self.lbl_time = QLabel(dt)
        self.lbl_time.setStyleSheet("color: #bfbfbf; font-size: 10px;")
        footer_row1.addWidget(self.lbl_time)
        layout.addLayout(footer_row1)

        footer_row2 = QHBoxLayout()
        footer_row2.setContentsMargins(0, 0, 0, 0)
        salesman = str(self.item.get("salesman") or "")
        lbl_sales = QLabel(f"业务员: {salesman}" if salesman else "")
        lbl_sales.setStyleSheet("color: #8c8c8c; font-size: 10px;")
        footer_row2.addWidget(lbl_sales)

        footer_row2.addStretch()

        btn_edit = QPushButton("编辑")
        btn_edit.setStyleSheet("color: #1677ff; background: transparent; border: none; font-size: 11px; cursor: pointer;")
        btn_edit.clicked.connect(lambda: self.edit_clicked.emit(self.item_id))
        footer_row2.addWidget(btn_edit)

        layout.addLayout(footer_row2)

    def _copy_id(self):
        cb = QApplication.clipboard()
        if cb:
            cb.setText(self.item_id)

    def _on_check_toggled(self, checked: bool):
        self._update_card_style(checked)
        self.selection_changed.emit(self.item_id, checked)

    def set_selected(self, selected: bool):
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(selected)
        self._update_card_style(selected)
        self.checkbox.blockSignals(False)


class ProductCardGrid(QScrollArea):
    """Responsive card grid container matching YuanYou card streams."""
    selection_updated = Signal(list)
    edit_requested = Signal(str)
    verify_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea { border: none; background: #f5f7fa; }")

        self.container = QWidget()
        self.container.setStyleSheet("background: #f5f7fa;")
        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)
        self.grid_layout.setHorizontalSpacing(14)
        self.grid_layout.setVerticalSpacing(14)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.setWidget(self.container)

        self.cards: Dict[str, ProductCardWidget] = {}
        self.selected_ids: Set[str] = set()

    def set_items(self, items: List[Dict[str, Any]]):
        """Re-populates the card stream."""
        # Clear existing
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self.cards.clear()
        self.selected_ids.clear()

        cols = max(1, (self.width() - 30) // 225) if self.width() > 300 else 4
        for idx, it in enumerate(items):
            pid = str(it.get("id") or "")
            card = ProductCardWidget(it)
            card.selection_changed.connect(self._on_item_selection)
            card.edit_clicked.connect(self.edit_requested.emit)
            card.verify_clicked.connect(self.verify_requested.emit)

            row = idx // cols
            col = idx % cols
            self.grid_layout.addWidget(card, row, col)
            self.cards[pid] = card

    def _on_item_selection(self, pid: str, checked: bool):
        if checked:
            self.selected_ids.add(pid)
        else:
            self.selected_ids.discard(pid)
        self.selection_updated.emit(list(self.selected_ids))

    def select_all(self):
        for pid, card in self.cards.items():
            card.set_selected(True)
            self.selected_ids.add(pid)
        self.selection_updated.emit(list(self.selected_ids))

    def deselect_all(self):
        for card in self.cards.values():
            card.set_selected(False)
        self.selected_ids.clear()
        self.selection_updated.emit([])

    def invert_selection(self):
        new_selected = set()
        for pid, card in self.cards.items():
            was_sel = pid in self.selected_ids
            card.set_selected(not was_sel)
            if not was_sel:
                new_selected.add(pid)
        self.selected_ids = new_selected
        self.selection_updated.emit(list(self.selected_ids))

    def select_item(self, pid: str, selected: bool):
        if pid in self.cards:
            self.cards[pid].set_selected(selected)
            if selected:
                self.selected_ids.add(pid)
            else:
                self.selected_ids.discard(pid)
            self.selection_updated.emit(list(self.selected_ids))

    def get_selected_ids(self) -> List[str]:
        return list(self.selected_ids)
