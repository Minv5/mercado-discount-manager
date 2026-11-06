from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set
from PySide6.QtCore import QSize, Qt, Signal
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

from ui_common.image_cache import get_placeholder_pixmap, load_image_async

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

        self.setFixedWidth(224)
        self.setObjectName("ProductCardWidget")
        self._update_card_style(selected=False)

        self._init_ui()
        self._load_image()

    def _update_card_style(self, selected: bool):
        self.is_selected = selected
        if selected:
            self.setStyleSheet("""
                QFrame#ProductCardWidget {
                    background: #f0f7ff;
                    border: 2px solid #2563eb;
                    border-radius: 8px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#ProductCardWidget {
                    background: #ffffff;
                    border: 1px solid #e2e8f0;
                    border-radius: 8px;
                }
                QFrame#ProductCardWidget:hover {
                    border: 1px solid #93c5fd;
                }
            """)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 1. Top Image container with overlay Checkbox
        img_container = QWidget()
        img_container.setFixedHeight(180)
        img_layout = QVBoxLayout(img_container)
        img_layout.setContentsMargins(0, 0, 0, 0)

        # Main thumbnail
        self.lbl_image = QLabel(img_container)
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setStyleSheet("background: #f1f5f9; border-radius: 6px;")
        self.lbl_image.setFixedHeight(180)
        self.lbl_image.setScaledContents(True)

        # Top-left Checkbox
        self.checkbox = QCheckBox(img_container)
        self.checkbox.setChecked(False)
        self.checkbox.setStyleSheet("""
            QCheckBox {
                background: rgba(255, 255, 255, 0.85);
                border-radius: 4px;
                padding: 2px;
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
            var_badge.setStyleSheet("background: rgba(15,23,42,0.75); color: #ffffff; font-size: 10px; font-weight: 500; border-radius: 3px; padding: 2px 6px;")
            var_badge.move(176, 6)

        layout.addWidget(img_container)

        # 2. Site & Threshold row
        site_id = str(self.item.get("site_id") or "MLB")
        site_str = COUNTRY_NAMES.get(site_id, f"🌐 {site_id}")
        threshold_str = str(self.item.get("threshold_type") or "高阈值")

        site_row = QHBoxLayout()
        site_row.setContentsMargins(0, 0, 0, 0)
        lbl_site = QLabel(f"按 {site_str}")
        lbl_site.setStyleSheet("color: #475569; font-size: 11px; font-weight: 500;")
        site_row.addWidget(lbl_site)

        site_row.addStretch()

        is_high = "高" in threshold_str
        thresh_color = "#c2410c" if is_high else "#1d4ed8"
        thresh_bg = "#fff7ed" if is_high else "#eff6ff"
        thresh_border = "#fed7aa" if is_high else "#bfdbfe"
        lbl_thresh = QLabel(threshold_str)
        lbl_thresh.setStyleSheet(f"background: {thresh_bg}; color: {thresh_color}; border: 1px solid {thresh_border}; border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: 600;")
        site_row.addWidget(lbl_thresh)

        layout.addLayout(site_row)

        # 3. ID, Copy, and Verify row
        id_row = QHBoxLayout()
        id_row.setContentsMargins(0, 0, 0, 0)
        id_row.setSpacing(4)

        short_id = self.item_id[:12] + "..." if len(self.item_id) > 12 else self.item_id
        lbl_id = QLabel(f"ID: {short_id}")
        lbl_id.setToolTip(f"完整商品ID: {self.item_id}")
        lbl_id.setStyleSheet("color: #2563eb; font-size: 11px; font-family: -apple-system, monospace; font-weight: 500;")
        id_row.addWidget(lbl_id)

        btn_copy = QPushButton("📋")
        btn_copy.setFixedSize(18, 18)
        btn_copy.setToolTip("复制完整ID")
        btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copy.setStyleSheet("background: transparent; border: none; font-size: 11px;")
        btn_copy.clicked.connect(self._copy_id)
        id_row.addWidget(btn_copy)

        id_row.addStretch()

        btn_verify = QPushButton("🤖 核价")
        btn_verify.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_verify.setStyleSheet("""
            QPushButton {
                background: #eff6ff;
                color: #1d4ed8;
                border: 1px solid #bfdbfe;
                border-radius: 4px;
                padding: 1px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #dbeafe;
                border-color: #93c5fd;
            }
        """)
        btn_verify.clicked.connect(lambda: self.verify_clicked.emit(self.item_id))
        id_row.addWidget(btn_verify)

        layout.addLayout(id_row)

        # 4. 6-Metrics Matrix Box (2 rows x 3 cols)
        metric_box = QFrame()
        metric_box.setStyleSheet("background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 4px;")
        grid = QGridLayout(metric_box)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(3)

        # Row 1 headers & values
        price = float(self.item.get("price") or 0.0)
        cost = float(self.item.get("cost_price") or 0.0)
        profit = float(self.item.get("profit") or round(price - cost, 2))

        cost_color = "#dc2626" if cost >= price else "#334155"
        profit_color = "#16a34a" if profit > 0 else "#94a3b8"

        lbl_hdr_price = QLabel("售价 USD")
        lbl_hdr_price.setStyleSheet("color: #64748b; font-size: 9.5px; font-weight: 500;")
        lbl_hdr_cost = QLabel("成本 USD")
        lbl_hdr_cost.setStyleSheet("color: #64748b; font-size: 9.5px; font-weight: 500;")
        lbl_hdr_profit = QLabel("利润 USD")
        lbl_hdr_profit.setStyleSheet("color: #64748b; font-size: 9.5px; font-weight: 500;")

        grid.addWidget(lbl_hdr_price, 0, 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(lbl_hdr_cost, 0, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(lbl_hdr_profit, 0, 2, Qt.AlignmentFlag.AlignCenter)

        self.lbl_price = QLabel(f"{price:.2f}")
        self.lbl_price.setStyleSheet("color: #0f172a; font-weight: bold; font-size: 12px;")
        self.lbl_cost = QLabel(f"{cost:.2f}")
        self.lbl_cost.setStyleSheet(f"color: {cost_color}; font-weight: 600; font-size: 12px;")
        self.lbl_profit = QLabel(f"{profit:.2f}")
        self.lbl_profit.setStyleSheet(f"color: {profit_color}; font-weight: bold; font-size: 12px;")

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
        lbl_hdr_w.setStyleSheet("color: #64748b; font-size: 9.5px; font-weight: 500;")
        lbl_hdr_sz = QLabel("尺寸 cm")
        lbl_hdr_sz.setStyleSheet("color: #64748b; font-size: 9.5px; font-weight: 500;")
        lbl_hdr_var = QLabel("变体")
        lbl_hdr_var.setStyleSheet("color: #64748b; font-size: 9.5px; font-weight: 500;")

        grid.addWidget(lbl_hdr_w, 2, 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(lbl_hdr_sz, 2, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(lbl_hdr_var, 2, 2, Qt.AlignmentFlag.AlignCenter)

        self.lbl_weight = QLabel(f"{int(weight)}")
        self.lbl_weight.setStyleSheet("color: #334155; font-size: 11px;")
        self.lbl_size = QLabel(f"{l} × {w} × {h}")
        self.lbl_size.setToolTip(f"长 {l}cm × 宽 {w}cm × 高 {h}cm")
        self.lbl_size.setStyleSheet("color: #334155; font-size: 9.5px; font-weight: 500;")
        self.lbl_variants = QLabel(f"{var_count}")
        self.lbl_variants.setStyleSheet("color: #2563eb; font-weight: bold; font-size: 11px;")

        grid.addWidget(self.lbl_weight, 3, 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.lbl_size, 3, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.lbl_variants, 3, 2, Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(metric_box)

        # 5. Product Title (2-line wrap)
        self.lbl_title = QLabel(str(self.item.get("title") or "未命名商品"))
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setFixedHeight(34)
        self.lbl_title.setToolTip(str(self.item.get("title") or ""))
        self.lbl_title.setStyleSheet("font-size: 11px; color: #1e293b; font-weight: 500; line-height: 1.25;")
        layout.addWidget(self.lbl_title)

        # 6. Footer (Group, Time, Editor)
        footer_row1 = QHBoxLayout()
        footer_row1.setContentsMargins(0, 0, 0, 0)
        grp = str(self.item.get("group_name") or "未分组")
        self.lbl_group = QLabel(f"分组: {grp}")
        self.lbl_group.setStyleSheet("color: #64748b; font-size: 10px;")
        footer_row1.addWidget(self.lbl_group)

        footer_row1.addStretch()

        dt = str(self.item.get("created_at") or "")[:16]
        self.lbl_time = QLabel(dt)
        self.lbl_time.setStyleSheet("color: #94a3b8; font-size: 10px;")
        footer_row1.addWidget(self.lbl_time)
        layout.addLayout(footer_row1)

        footer_row2 = QHBoxLayout()
        footer_row2.setContentsMargins(0, 0, 0, 0)
        salesman = str(self.item.get("salesman") or "")
        lbl_sales = QLabel(f"业务员: {salesman}" if salesman else "")
        lbl_sales.setStyleSheet("color: #64748b; font-size: 10px;")
        footer_row2.addWidget(lbl_sales)

        footer_row2.addStretch()

        btn_edit = QPushButton("编辑")
        btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_edit.setStyleSheet("color: #2563eb; background: transparent; border: none; font-size: 11px; font-weight: 600;")
        btn_edit.clicked.connect(lambda: self.edit_clicked.emit(self.item_id))
        footer_row2.addWidget(btn_edit)

        layout.addLayout(footer_row2)

    def _load_image(self):
        images = self.item.get("images") or []
        thumb_url = ""
        if isinstance(images, list) and images:
            thumb_url = str(images[0] or "")
        elif isinstance(images, str):
            try:
                parsed = json.loads(images)
                if parsed and isinstance(parsed, list):
                    thumb_url = str(parsed[0] or "")
                else:
                    thumb_url = images
            except Exception:
                thumb_url = images

        if thumb_url and thumb_url.startswith(("http://", "https://", "data:")):
            self.lbl_image.setText("")
            load_image_async(
                thumb_url,
                QSize(208, 180),
                on_loaded=self._on_image_loaded,
                radius=6,
                crop=True,
            )
        else:
            self.lbl_image.setPixmap(get_placeholder_pixmap(QSize(208, 180), "📦", radius=6))
            self.lbl_image.setText("")

    def _on_image_loaded(self, pixmap: QPixmap):
        if not pixmap.isNull():
            self.lbl_image.setPixmap(pixmap)
            self.lbl_image.setText("")

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
        self.setStyleSheet("QScrollArea { border: none; background: #f1f5f9; }")

        self.container = QWidget()
        self.container.setStyleSheet("background: #f1f5f9;")
        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)
        self.grid_layout.setHorizontalSpacing(14)
        self.grid_layout.setVerticalSpacing(14)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.setWidget(self.container)

        self.cards: Dict[str, ProductCardWidget] = {}
        self.selected_ids: Set[str] = set()
        self._cached_items: List[Dict[str, Any]] = []

    def set_items(self, items: List[Dict[str, Any]]):
        """Re-populates the card stream."""
        self._cached_items = items
        # Clear existing
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self.cards.clear()
        self.selected_ids.clear()

        viewport_w = self.viewport().width() if self.viewport().width() > 300 else 1000
        cols = max(1, (viewport_w - 24) // 238)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._cached_items:
            return
        # Dynamically recalculate column count if width changes significantly
        viewport_w = self.viewport().width()
        cols = max(1, (viewport_w - 24) // 238)
        current_cols = self.grid_layout.columnCount()
        if cols != current_cols and cols > 0:
            for idx, it in enumerate(self._cached_items):
                pid = str(it.get("id") or "")
                if pid in self.cards:
                    card = self.cards[pid]
                    row = idx // cols
                    col = idx % cols
                    self.grid_layout.addWidget(card, row, col)

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
