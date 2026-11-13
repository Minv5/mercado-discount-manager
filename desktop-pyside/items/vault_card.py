from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPixmap, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui_common.clipboard import show_pure_copy_menu
from ui_common.image_cache import get_placeholder_pixmap, load_image_async
from ui_common.lightbox import show_image_preview

COUNTRY_NAMES = {
    "MLB": "🇧🇷 巴西",
    "MLM": "🇲🇽 墨西哥",
    "MLC": "🇨🇱 智利",
    "MCO": "🇨🇴 哥伦比亚",
    "MLA": "🇦🇷 阿根廷",
    "MLU": "🇺🇾 乌拉圭",
    "MPE": "🇵🇪 秘鲁",
}


class TwoLineTitleLabel(QLabel):
    """Title label that shows at most two wrapped lines; anything longer ends in an ellipsis."""

    def __init__(self, full_text: str, parent: Optional[QWidget] = None):
        super().__init__(full_text, parent)
        self._full_text = full_text
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            return
        self.ensurePolished()
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        text_layout = QTextLayout(self._full_text, self.font())
        text_layout.setTextOption(option)
        text_layout.beginLayout()
        second_start = -1
        overflow = False
        for line_no in range(3):
            line = text_layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            if line_no == 1:
                second_start = line.textStart()
            overflow = line_no == 2
        text_layout.endLayout()

        shown = self._full_text
        if overflow and second_start >= 0:
            tail = self.fontMetrics().elidedText(
                self._full_text[second_start:], Qt.TextElideMode.ElideRight, width - 4
            )
            shown = self._full_text[:second_start] + tail
        if shown != self.text():
            self.setText(shown)


class ProductCardWidget(QFrame):
    """High-fidelity product card modeled 1:1 after YuanYou ERP production card."""
    selection_changed = Signal(str, bool)
    edit_clicked = Signal(str)
    verify_clicked = Signal(str)
    field_updated = Signal(str, str, object)  # (item_id, field_name, new_value)
    variant_drilldown_requested = Signal(str)

    def __init__(self, item: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.item = dict(item)
        self.item_id = str(item.get("id") or "")
        self.is_selected = False
        self.is_in_quick_edit = False

        self.setMinimumWidth(212)
        self.setFixedHeight(400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setObjectName("ProductCardWidget")
        self._update_card_style(selected=False)

        self._init_ui()
        self._load_image()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "img_container") and hasattr(self, "lbl_image"):
            w = max(196, self.img_container.width())
            self.lbl_image.setGeometry(0, 0, w, 176)
            if hasattr(self, "lbl_status"):
                self.lbl_status.adjustSize()
                self.lbl_status.move(max(4, w - self.lbl_status.width() - 6), 6)

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        self.edit_clicked.emit(self.item_id)

    def _update_card_style(self, selected: bool):
        self.is_selected = selected
        if getattr(self, "is_in_quick_edit", False):
            # Active in-place editing: vibrant green border with dark card surface
            self.setStyleSheet("""
                QFrame#ProductCardWidget {
                    background: #1F2E23;
                    border: 2px solid #34D399;
                    border-radius: 8px;
                }
            """)
            return

        status = int(self.item.get("status_code") or (5 if self.item.get("status") == "infringed" else (2 if self.item.get("status") == "pending" else 1)))
        
        if selected:
            border_css = "2px solid #D4AF37"
            bg_css = "#232C24"
        elif status == 5:
            border_css = "1px solid #EF4444"
            bg_css = "#202820"
        elif status == 2:
            border_css = "1px solid #F59E0B"
            bg_css = "#202820"
        else:
            border_css = "1px solid #4E472F"
            bg_css = "#202820"

        self.setStyleSheet(f"""
            QFrame#ProductCardWidget {{
                background: {bg_css};
                border: {border_css};
                border-radius: 8px;
            }}
            QFrame#ProductCardWidget:hover {{
                border: {2 if selected else 1}px solid {'#D4AF37' if selected else '#6E5D2A'};
            }}
        """)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        # 1. Image container (176px height, fluid width)
        self.img_container = QWidget(self)
        self.img_container.setFixedHeight(176)
        self.img_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Main thumbnail
        self.lbl_image = QLabel(self.img_container)
        self.lbl_image.setGeometry(0, 0, 212, 176)
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setObjectName("VaultCardImage")
        self.lbl_image.setStyleSheet(
            "QLabel#VaultCardImage { background: #121614; border: 1px solid #4E472F; border-radius: 6px; }"
        )
        self.lbl_image.setScaledContents(True)
        self.lbl_image.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_image.setToolTip("点击查看高清大图")
        self.lbl_image.mousePressEvent = self._on_image_clicked

        # Top-left Checkbox inside image (Sleek, transparent, non-intrusive)
        self.checkbox = QCheckBox(self.lbl_image)
        self.checkbox.setChecked(False)
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.setStyleSheet("""
            QCheckBox { background: transparent; }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 3px;
                border: 1px solid #8A7432;
                background: rgba(18, 22, 20, 0.75);
            }
            QCheckBox::indicator:hover { border-color: #D4AF37; }
            QCheckBox::indicator:checked {
                background: #D4AF37;
                border-color: #D4AF37;
            }
        """)
        self.checkbox.move(8, 8)
        self.checkbox.toggled.connect(self._on_check_toggled)

        # Bottom-left Resolution Indicator strictly inside lbl_image
        self.lbl_resolution = QLabel(self.lbl_image)
        self.lbl_resolution.setGeometry(6, 150, 72, 18)
        self.lbl_resolution.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_resolution.setStyleSheet("""
            background: rgba(0, 0, 0, 0.7);
            color: #E6E2D8;
            font-size: 9.5px;
            font-weight: 500;
            border-radius: 3px;
            padding: 1px 4px;
        """)
        self.lbl_resolution.hide()

        # Status badge (已侵权 / 暂定) strictly on top right if present
        status = int(self.item.get("status_code") or (5 if self.item.get("status") == "infringed" else (2 if self.item.get("status") == "pending" else 1)))
        if status == 5:
            self.lbl_status = QLabel("已侵权", self.lbl_image)
            self.lbl_status.setStyleSheet("background: #dc2626; color: #ffffff; font-size: 10px; font-weight: bold; border-radius: 3px; padding: 2px 5px;")
            self.lbl_status.adjustSize()
            self.lbl_status.move(160, 6)
        elif status == 2:
            self.lbl_status = QLabel("暂定", self.lbl_image)
            self.lbl_status.setStyleSheet("background: #d97706; color: #ffffff; font-size: 10px; font-weight: bold; border-radius: 3px; padding: 2px 5px;")
            self.lbl_status.adjustSize()
            self.lbl_status.move(168, 6)

        layout.addWidget(self.img_container)

        # 2. Site & Threshold & Volumetric Bar
        site_id = str(self.item.get("site_id") or "MLB")
        site_str = COUNTRY_NAMES.get(site_id, f"🌐 {site_id}")
        threshold_str = str(self.item.get("threshold_type") or "低阈值")

        site_row = QHBoxLayout()
        site_row.setContentsMargins(0, 0, 0, 0)
        store_name = str(self.item.get("store_name") or "").strip()
        lbl_site = QLabel(f"{store_name} · {site_id}" if store_name else site_str)
        lbl_site.setToolTip(f"{store_name} · {site_str}" if store_name else site_str)
        lbl_site.setStyleSheet("color: #A8A29E; font-size: 11px; font-weight: bold;")
        site_row.addWidget(lbl_site)

        site_row.addStretch()

        # Volumetric Throw Weight Badge
        weight = float(self.item.get("weight") or 0.0)
        l = float(self.item.get("length") or 0.0)
        w = float(self.item.get("width") or 0.0)
        h = float(self.item.get("height") or 0.0)
        volumetric_g = round(l * w * h / 6000.0 * 1000.0) if (l > 0 and w > 0 and h > 0) else 0
        is_throw = volumetric_g > weight and volumetric_g > 0

        self.lbl_volumetric = QLabel("抛重" if is_throw else "实重")
        if is_throw:
            vol_style = "background: rgba(245, 158, 11, 0.15); color: #FBBF24; border: 1px solid #D97706;"
        else:
            vol_style = "background: rgba(16, 185, 129, 0.15); color: #10B981; border: 1px solid #059669;"
        self.lbl_volumetric.setStyleSheet(f"""
            {vol_style}
            border-radius: 3px;
            padding: 1px 4px;
            font-size: 9.5px;
            font-weight: 600;
        """)
        self.lbl_volumetric.setToolTip(f"计抛说明：体积 {volumetric_g}g / 实重 {int(weight)}g ({'触发计抛' if is_throw else '未计抛'})")
        site_row.addWidget(self.lbl_volumetric)

        # Threshold Badge
        is_high = "高" in threshold_str
        if is_high:
            thresh_style = "background: rgba(245, 158, 11, 0.15); color: #FBBF24; border: 1px solid #D97706;"
        else:
            thresh_style = "background: rgba(59, 130, 246, 0.15); color: #60A5FA; border: 1px solid #2563EB;"
        lbl_thresh = QLabel(threshold_str)
        lbl_thresh.setStyleSheet(f"{thresh_style} border-radius: 3px; padding: 1px 5px; font-size: 9.5px; font-weight: 600;")
        site_row.addWidget(lbl_thresh)

        layout.addLayout(site_row)

        # 3. Title (two lines, right-click to copy)
        self.lbl_title = TwoLineTitleLabel(str(self.item.get("title") or "未命名商品"))
        self.lbl_title.setFixedHeight(34)
        self.lbl_title.setToolTip(f"{self.lbl_title.full_text()}\n(右键可复制标题)")
        self.lbl_title.setStyleSheet("font-size: 12px; color: #E6E2D8; font-weight: 500;")
        self.lbl_title.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lbl_title.customContextMenuRequested.connect(
            lambda pos: show_pure_copy_menu(self.lbl_title, pos, self.lbl_title.full_text)
        )
        layout.addWidget(self.lbl_title)

        # 4. ID chip: click to copy
        id_row = QHBoxLayout()
        id_row.setContentsMargins(0, 0, 0, 0)
        id_row.setSpacing(6)
        short_id = self.item_id[:14] + "…" if len(self.item_id) > 14 else self.item_id
        lbl_id = QLabel(f"ID: {short_id}")
        lbl_id.setToolTip(f"{self.item_id}\n(点击复制商品ID)")
        lbl_id.setCursor(Qt.CursorShape.PointingHandCursor)
        lbl_id.setStyleSheet("""
            QLabel {
                color: #D4AF37;
                font-size: 11px;
                font-weight: 500;
                background: rgba(212, 175, 55, 0.12);
                border: 1px solid rgba(212, 175, 55, 0.35);
                border-radius: 3px;
                padding: 1px 4px;
            }
            QLabel:hover { background: rgba(212, 175, 55, 0.25); color: #FCD34D; }
        """)
        lbl_id.mousePressEvent = lambda e: self._copy_id()
        id_row.addWidget(lbl_id)
        id_row.addStretch()
        layout.addLayout(id_row)

        # 4. 6-Metrics Section: In-place Switcher between Display Table and Edit Form
        self.metric_stack = QStackedWidget(self)
        self.metric_stack.setFixedHeight(82)

        # ---- PAGE 0: Display Table (1:1 with YuanYou Image 3) ----
        self.display_frame = QFrame()
        self.display_frame.setStyleSheet("""
            QFrame {
                background: #161D18;
                border: 1px solid #2D3830;
                border-radius: 6px;
            }
        """)
        disp_layout = QVBoxLayout(self.display_frame)
        disp_layout.setContentsMargins(0, 0, 0, 0)
        disp_layout.setSpacing(0)

        # Row 1: 售价 | 成本 | 利润
        row1 = QWidget()
        row1.setStyleSheet("border-bottom: 1px solid #2D3830;")
        row1_layout = QHBoxLayout(row1)
        row1_layout.setContentsMargins(0, 2, 0, 2)
        row1_layout.setSpacing(0)

        price = float(self.item.get("price") or 0.0)
        cost = float(self.item.get("cost_price") or 0.0)
        profit = float(self.item.get("profit") or 0.0)
        if profit == 0.0 and cost > 0:
            profit = round(price - cost, 2)

        cost_color = "#F87171" if cost >= price and price > 0 else "#F6F3EA"
        profit_color = "#4ADE80" if profit >= 0 else "#F87171"

        # Col 1: 售价
        c1 = QWidget()
        c1.setCursor(Qt.CursorShape.PointingHandCursor)
        c1.mousePressEvent = lambda e: self._start_single_edit("售价", "price", price, "$")
        c1.setStyleSheet("border-right: 1px solid #2D3830;")
        l1 = QVBoxLayout(c1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(1)
        lbl_h_p = QLabel("售价 USD")
        lbl_h_p.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_h_p.setStyleSheet("color: #A39E93; font-size: 10px; border: none;")
        self.lbl_price = QLabel(f"{price:.2f}" if price > 0 else "--")
        self.lbl_price.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_price.setStyleSheet("color: #F6F3EA; font-weight: bold; font-size: 13px; border: none;")
        l1.addWidget(lbl_h_p)
        l1.addWidget(self.lbl_price)
        row1_layout.addWidget(c1, stretch=1)

        # Col 2: 成本
        c2 = QWidget()
        c2.setCursor(Qt.CursorShape.PointingHandCursor)
        c2.mousePressEvent = lambda e: self._start_single_edit("成本", "cost_price", cost, "$")
        c2.setStyleSheet("border-right: 1px solid #2D3830;")
        l2 = QVBoxLayout(c2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.setSpacing(1)
        lbl_h_c = QLabel("成本 USD")
        lbl_h_c.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_h_c.setStyleSheet("color: #A39E93; font-size: 10px; border: none;")
        self.lbl_cost = QLabel(f"{cost:.2f}" if cost > 0 else "--")
        self.lbl_cost.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cost.setStyleSheet(f"color: {cost_color}; font-weight: bold; font-size: 13px; border: none;")
        l2.addWidget(lbl_h_c)
        l2.addWidget(self.lbl_cost)
        row1_layout.addWidget(c2, stretch=1)

        # Col 3: 利润
        c3 = QWidget()
        c3.setCursor(Qt.CursorShape.PointingHandCursor)
        c3.mousePressEvent = lambda e: self._start_single_edit("利润", "profit", profit, "$")
        l3 = QVBoxLayout(c3)
        l3.setContentsMargins(0, 0, 0, 0)
        l3.setSpacing(1)
        lbl_h_pr = QLabel("利润 USD")
        lbl_h_pr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_h_pr.setStyleSheet("color: #A39E93; font-size: 10px; border: none;")
        self.lbl_profit = QLabel(f"{profit:.2f}" if (cost > 0 or profit != 0) else "--")
        self.lbl_profit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_profit.setStyleSheet(f"color: {profit_color}; font-weight: bold; font-size: 13px; border: none;")
        l3.addWidget(lbl_h_pr)
        l3.addWidget(self.lbl_profit)
        row1_layout.addWidget(c3, stretch=1)

        disp_layout.addWidget(row1)

        # Row 2: 重量 | 尺寸 | 变体
        row2 = QWidget()
        row2_layout = QHBoxLayout(row2)
        row2_layout.setContentsMargins(0, 2, 0, 2)
        row2_layout.setSpacing(0)

        # Col 4: 重量
        c4 = QWidget()
        c4.setCursor(Qt.CursorShape.PointingHandCursor)
        c4.mousePressEvent = lambda e: self._start_single_edit("重量", "weight", weight, "g", is_int=True)
        c4.setStyleSheet("border-right: 1px solid #2D3830;")
        l4 = QVBoxLayout(c4)
        l4.setContentsMargins(0, 0, 0, 0)
        l4.setSpacing(1)
        lbl_h_w = QLabel("重量 g")
        lbl_h_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_h_w.setStyleSheet("color: #A39E93; font-size: 10px; border: none;")
        self.lbl_weight = QLabel(f"{int(weight)}" if weight > 0 else "--")
        self.lbl_weight.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_weight.setStyleSheet("color: #E6E2D8; font-size: 12px; border: none;")
        l4.addWidget(lbl_h_w)
        l4.addWidget(self.lbl_weight)
        row2_layout.addWidget(c4, stretch=1)

        # Col 5: 尺寸
        c5 = QWidget()
        c5.setCursor(Qt.CursorShape.PointingHandCursor)
        c5.mousePressEvent = lambda e: self._start_size_edit()
        c5.setStyleSheet("border-right: 1px solid #2D3830;")
        l5 = QVBoxLayout(c5)
        l5.setContentsMargins(0, 0, 0, 0)
        l5.setSpacing(1)
        lbl_h_sz = QLabel("尺寸 cm")
        lbl_h_sz.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_h_sz.setStyleSheet("color: #A39E93; font-size: 10px; border: none;")
        size_str = f"{int(l)} × {int(w)} × {int(h)}" if (l > 0 and w > 0 and h > 0) else "--"
        self.lbl_size = QLabel(size_str)
        self.lbl_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_size.setStyleSheet("color: #E6E2D8; font-size: 10px; font-weight: 500; border: none;")
        l5.addWidget(lbl_h_sz)
        l5.addWidget(self.lbl_size)
        row2_layout.addWidget(c5, stretch=1)

        # Col 6: 变体
        var_count = int(self.item.get("variants_count") or 0)
        c6 = QWidget()
        c6.setCursor(Qt.CursorShape.PointingHandCursor)
        c6.mousePressEvent = lambda e=None: self.variant_drilldown_requested.emit(self.item_id)
        l6 = QVBoxLayout(c6)
        l6.setContentsMargins(0, 0, 0, 0)
        l6.setSpacing(1)
        lbl_h_v = QLabel("变体")
        lbl_h_v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_h_v.setStyleSheet("color: #A39E93; font-size: 10px; border: none;")
        self.lbl_variants = QLabel(f"{var_count}")
        self.lbl_variants.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_variants.setStyleSheet("color: #60A5FA; font-weight: bold; font-size: 13px; text-decoration: underline; border: none;")
        self.lbl_variants.mousePressEvent = lambda e=None: self.variant_drilldown_requested.emit(self.item_id)
        l6.addWidget(lbl_h_v)
        l6.addWidget(self.lbl_variants)
        row2_layout.addWidget(c6, stretch=1)

        disp_layout.addWidget(row2)
        self.metric_stack.addWidget(self.display_frame)

        # ---- PAGE 1: Size Edit Form (1:1 with YuanYou Image 4) ----
        self.size_edit_frame = QFrame()
        self.size_edit_frame.setStyleSheet("background: transparent; border: none;")
        size_layout = QVBoxLayout(self.size_edit_frame)
        size_layout.setContentsMargins(2, 2, 2, 2)
        size_layout.setSpacing(4)

        # 3 columns for L, W, H with cm suffix
        dim_inputs_row = QHBoxLayout()
        dim_inputs_row.setContentsMargins(0, 0, 0, 0)
        dim_inputs_row.setSpacing(4)

        # Col L
        col_l = QVBoxLayout()
        col_l.setSpacing(1)
        lbl_l = QLabel("长:")
        lbl_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_l.setStyleSheet("color: #E6E2D8; font-size: 11px;")
        self.inp_l = QDoubleSpinBox()
        self.inp_l.setRange(0.0, 999.0)
        self.inp_l.setDecimals(0)
        self.inp_l.setValue(l)
        self.inp_l.setSuffix(" cm")
        self.inp_l.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.inp_l.setStyleSheet("border: 1px solid #2F6B3F; border-radius: 4px; padding: 2px 4px; font-size: 11px; background: #141714; color: #F6F3EA;")
        col_l.addWidget(lbl_l)
        col_l.addWidget(self.inp_l)
        dim_inputs_row.addLayout(col_l)

        # Col W
        col_w = QVBoxLayout()
        col_w.setSpacing(1)
        lbl_w = QLabel("宽:")
        lbl_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_w.setStyleSheet("color: #E6E2D8; font-size: 11px;")
        self.inp_w = QDoubleSpinBox()
        self.inp_w.setRange(0.0, 999.0)
        self.inp_w.setDecimals(0)
        self.inp_w.setValue(w)
        self.inp_w.setSuffix(" cm")
        self.inp_w.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.inp_w.setStyleSheet("border: 1px solid #4E472F; border-radius: 4px; padding: 2px 4px; font-size: 11px; background: #141714; color: #F6F3EA;")
        col_w.addWidget(lbl_w)
        col_w.addWidget(self.inp_w)
        dim_inputs_row.addLayout(col_w)

        # Col H
        col_h = QVBoxLayout()
        col_h.setSpacing(1)
        lbl_h = QLabel("高:")
        lbl_h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_h.setStyleSheet("color: #E6E2D8; font-size: 11px;")
        self.inp_h = QDoubleSpinBox()
        self.inp_h.setRange(0.0, 999.0)
        self.inp_h.setDecimals(0)
        self.inp_h.setValue(h)
        self.inp_h.setSuffix(" cm")
        self.inp_h.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.inp_h.setStyleSheet("border: 1px solid #4E472F; border-radius: 4px; padding: 2px 4px; font-size: 11px; background: #141714; color: #F6F3EA;")
        col_h.addWidget(lbl_h)
        col_h.addWidget(self.inp_h)
        dim_inputs_row.addLayout(col_h)

        size_layout.addLayout(dim_inputs_row)

        # Buttons row: 取消 & 确认
        size_btn_row = QHBoxLayout()
        size_btn_row.setContentsMargins(4, 0, 4, 0)
        size_btn_row.setSpacing(8)

        btn_size_cancel = QPushButton("取 消")
        btn_size_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_size_cancel.setStyleSheet("""
            QPushButton {
                background: #1C231F;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 4px;
                padding: 3px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                border-color: #6E5D2A;
                color: #F6F3EA;
            }
        """)
        btn_size_cancel.clicked.connect(self._cancel_edit)
        size_btn_row.addWidget(btn_size_cancel)

        btn_size_ok = QPushButton("确 认")
        btn_size_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_size_ok.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 3px 14px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #356F45;
            }
        """)
        btn_size_ok.clicked.connect(self._save_size_edit)
        size_btn_row.addWidget(btn_size_ok)

        size_layout.addLayout(size_btn_row)
        self.metric_stack.addWidget(self.size_edit_frame)

        # ---- PAGE 2: Single Metric Edit Form (售价/成本/利润/重量) ----
        self.single_edit_frame = QFrame()
        self.single_edit_frame.setStyleSheet("background: transparent; border: none;")
        single_layout = QVBoxLayout(self.single_edit_frame)
        single_layout.setContentsMargins(4, 2, 4, 2)
        single_layout.setSpacing(4)

        single_input_row = QHBoxLayout()
        self.lbl_single_edit_name = QLabel("售价:")
        self.lbl_single_edit_name.setStyleSheet("color: #E6E2D8; font-size: 11px; font-weight: 500;")
        single_input_row.addWidget(self.lbl_single_edit_name)

        self.inp_single = QDoubleSpinBox()
        self.inp_single.setRange(0.0, 99999.0)
        self.inp_single.setDecimals(2)
        self.inp_single.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.inp_single.setStyleSheet("border: 1px solid #2F6B3F; border-radius: 4px; padding: 2px 4px; font-size: 11px; background: #141714; color: #F6F3EA;")
        single_input_row.addWidget(self.inp_single, stretch=1)
        single_layout.addLayout(single_input_row)

        single_btn_row = QHBoxLayout()
        single_btn_row.setContentsMargins(4, 0, 4, 0)
        single_btn_row.setSpacing(8)

        btn_single_cancel = QPushButton("取 消")
        btn_single_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_single_cancel.setStyleSheet("""
            QPushButton {
                background: #1C231F;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 4px;
                padding: 3px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                border-color: #6E5D2A;
                color: #F6F3EA;
            }
        """)
        btn_single_cancel.clicked.connect(self._cancel_edit)
        single_btn_row.addWidget(btn_single_cancel)

        btn_single_ok = QPushButton("确 认")
        btn_single_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_single_ok.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 3px 14px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #356F45;
            }
        """)
        btn_single_ok.clicked.connect(self._save_single_edit)
        single_btn_row.addWidget(btn_single_ok)

        single_layout.addLayout(single_btn_row)
        self.metric_stack.addWidget(self.single_edit_frame)

        layout.addWidget(self.metric_stack)

        # Spare height collects here instead of stretching the rows above
        layout.addStretch(1)

        # 6. Footer: group / time on the left, actions on the right
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(6)

        meta_col = QVBoxLayout()
        meta_col.setContentsMargins(0, 0, 0, 0)
        meta_col.setSpacing(1)
        grp = str(self.item.get("group_name") or "未分组")
        self.lbl_group = QLabel(f"分组: {grp}")
        self.lbl_group.setStyleSheet("color: #A8A29E; font-size: 10px;")
        meta_col.addWidget(self.lbl_group)
        self.lbl_time = QLabel(str(self.item.get("created_at") or "")[:16])
        self.lbl_time.setStyleSheet("color: #78716C; font-size: 10px;")
        meta_col.addWidget(self.lbl_time)
        footer.addLayout(meta_col, stretch=1)

        self.btn_verify = QPushButton("核价")
        self.btn_verify.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_verify.setFixedHeight(24)
        self.btn_verify.setStyleSheet("""
            QPushButton {
                background: #1E3A2B;
                color: #F6F3EA;
                border: 1px solid #2D6A4F;
                border-radius: 4px;
                padding: 2px 9px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover { background: #264E3A; border-color: #3E8B66; }
        """)
        self.btn_verify.clicked.connect(lambda: self.verify_clicked.emit(self.item_id))
        footer.addWidget(self.btn_verify, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.btn_edit = QPushButton("编辑")
        self.btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit.setFixedHeight(24)
        self.btn_edit.setStyleSheet("""
            QPushButton {
                background: #232C24;
                color: #E6E2D8;
                border: 1px solid #4E472F;
                border-radius: 4px;
                padding: 2px 9px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover { background: #26352C; border-color: #6E5D2A; }
        """)
        self.btn_edit.clicked.connect(lambda: self.edit_clicked.emit(self.item_id))
        footer.addWidget(self.btn_edit, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(footer)

    # In-place Edit Methods (1:1 with YuanYou Image 4)
    def _start_size_edit(self):
        self.is_in_quick_edit = True
        self._update_card_style(self.is_selected)
        l = float(self.item.get("length") or 0.0)
        w = float(self.item.get("width") or 0.0)
        h = float(self.item.get("height") or 0.0)
        self.inp_l.setValue(l)
        self.inp_w.setValue(w)
        self.inp_h.setValue(h)
        self.metric_stack.setCurrentIndex(1)
        self.inp_l.setFocus()
        self.inp_l.selectAll()

    def _save_size_edit(self):
        nl = self.inp_l.value()
        nw = self.inp_w.value()
        nh = self.inp_h.value()
        self.item["length"] = nl
        self.item["width"] = nw
        self.item["height"] = nh
        size_str = f"{int(nl)} × {int(nw)} × {int(nh)}" if (nl > 0 and nw > 0 and nh > 0) else "--"
        self.lbl_size.setText(size_str)
        self._recalc_volumetric()
        self._cancel_edit()
        self.field_updated.emit(self.item_id, "size", (nl, nw, nh))

    def _start_single_edit(self, name: str, field: str, value: float, unit: str, is_int: bool = False):
        self.is_in_quick_edit = True
        self._current_single_field = field
        self._current_single_is_int = is_int
        self._update_card_style(self.is_selected)

        self.lbl_single_edit_name.setText(f"{name}:")
        if is_int:
            self.inp_single.setDecimals(0)
            self.inp_single.setSuffix(f" {unit}")
            self.inp_single.setPrefix("")
        else:
            self.inp_single.setDecimals(2)
            if unit == "$":
                self.inp_single.setPrefix("$ ")
                self.inp_single.setSuffix("")
            else:
                self.inp_single.setPrefix("")
                self.inp_single.setSuffix(f" {unit}")

        self.inp_single.setValue(value)
        self.metric_stack.setCurrentIndex(2)
        self.inp_single.setFocus()
        self.inp_single.selectAll()

    def _save_single_edit(self):
        field = getattr(self, "_current_single_field", "price")
        new_val = self.inp_single.value()
        self.item[field] = new_val

        if field == "price":
            self.lbl_price.setText(f"{new_val:.2f}")
            cost = float(self.item.get("cost_price") or 0.0)
            if cost > 0:
                profit = round(new_val - cost, 2)
                self.item["profit"] = profit
                self.lbl_profit.setText(f"{profit:.2f}")
                self.lbl_profit.setStyleSheet(f"color: {'#52c41a' if profit >= 0 else '#ff4d4f'}; font-weight: bold; font-size: 13px; border: none;")
        elif field == "cost_price":
            self.lbl_cost.setText(f"{new_val:.2f}")
            price = float(self.item.get("price") or 0.0)
            profit = round(price - new_val, 2)
            self.item["profit"] = profit
            self.lbl_profit.setText(f"{profit:.2f}")
            self.lbl_profit.setStyleSheet(f"color: {'#52c41a' if profit >= 0 else '#ff4d4f'}; font-weight: bold; font-size: 13px; border: none;")
        elif field == "profit":
            self.lbl_profit.setText(f"{new_val:.2f}")
            self.lbl_profit.setStyleSheet(f"color: {'#52c41a' if new_val >= 0 else '#ff4d4f'}; font-weight: bold; font-size: 13px; border: none;")
        elif field == "weight":
            self.lbl_weight.setText(f"{int(new_val)}")
            self._recalc_volumetric()

        self._cancel_edit()
        self.field_updated.emit(self.item_id, field, new_val)

    def _cancel_edit(self):
        self.is_in_quick_edit = False
        self.metric_stack.setCurrentIndex(0)
        self._update_card_style(self.is_selected)

    def _recalc_volumetric(self):
        weight = float(self.item.get("weight") or 0.0)
        l = float(self.item.get("length") or 0.0)
        w = float(self.item.get("width") or 0.0)
        h = float(self.item.get("height") or 0.0)
        volumetric_g = round(l * w * h / 6000.0 * 1000.0) if (l > 0 and w > 0 and h > 0) else 0
        is_throw = volumetric_g > weight and volumetric_g > 0
        self.lbl_volumetric.setText("抛重" if is_throw else "实重")
        vol_color = "#ea580c" if is_throw else "#059669"
        vol_bg = "#fff7ed" if is_throw else "#ecfdf5"
        vol_border = "#fed7aa" if is_throw else "#a7f3d0"
        self.lbl_volumetric.setStyleSheet(f"""
            background: {vol_bg};
            color: {vol_color};
            border: 1px solid {vol_border};
            border-radius: 3px;
            padding: 1px 4px;
            font-size: 9.5px;
            font-weight: 600;
        """)
        self.lbl_volumetric.setToolTip(f"计抛说明：体积 {volumetric_g}g / 实重 {int(weight)}g ({'触发计抛' if is_throw else '未计抛'})")

    def _show_audit_logs(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #1C231F;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 6px;
                font-size: 11px;
                color: #E6E2D8;
            }
            QMenu::item {
                padding: 5px 12px;
                color: #E6E2D8;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #203A2B;
                color: #FFFFFF;
            }
            QMenu::item:disabled {
                color: #9A9589;
            }
            QMenu::separator {
                height: 1px;
                background: #4E472F;
                margin: 4px 6px;
            }
        """)
        header_act = menu.addAction(f"📋 商品 [{self.item_id[:10]}] 变更日志")
        header_act.setEnabled(False)
        menu.addSeparator()

        logs = self.item.get("logs") or []
        if isinstance(logs, str):
            try:
                logs = json.loads(logs)
            except Exception:
                logs = []

        if not logs:
            attrs = self.item.get("attributes") or {}
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except Exception:
                    attrs = {}
            if isinstance(attrs, dict) and isinstance(attrs.get("_logs"), list):
                logs = list(reversed(attrs.get("_logs") or []))

        if not logs:
            dt = str(self.item.get("created_at") or "")[:16]
            upd = str(self.item.get("updated_at") or "")[:16]
            logs = [f"{dt or '-'} | 商品创建入库"]
            if upd and upd != dt:
                logs.append(f"{upd} | 最近本地更新")

        for log_entry in reversed(logs):
            if isinstance(log_entry, dict):
                t = str(log_entry.get("time") or "")[:16]
                act = str(log_entry.get("action") or "更新")
                det = str(log_entry.get("detail") or "")
                text = f"{t} | {act}" + (f" · {det}" if det else "")
            else:
                text = str(log_entry)
            menu.addAction(text)

        menu.exec(QCursor.pos())

    @staticmethod
    def _to_high_res_url(url: str) -> str:
        """Converts Mercado Libre low-res icon thumbnail (-I.ext) to 500x500 high-res (-O.ext)."""
        if not url:
            return ""
        s = str(url).strip()
        for ext in [".jpg", ".webp", ".png", ".jpeg"]:
            if f"-I{ext}" in s:
                return s.replace(f"-I{ext}", f"-O{ext}")
        return s

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

        if not thumb_url:
            thumb_url = self.item.get("thumbnail") or self.item.get("image_url") or ""

        thumb_url = self._to_high_res_url(thumb_url)

        if thumb_url and thumb_url.startswith(("http://", "https://", "data:")):
            self.lbl_image.setText("")
            load_image_async(
                thumb_url,
                QSize(212, 176),
                on_loaded=self._on_image_loaded,
                radius=6,
                crop=True,
            )
        else:
            self.lbl_image.setPixmap(get_placeholder_pixmap(QSize(212, 176), "📦", radius=6))
            self.lbl_image.setText("")

    def _on_image_loaded(self, pixmap: QPixmap):
        if not pixmap.isNull():
            self._cached_pixmap = pixmap
            self.lbl_image.setPixmap(pixmap)
            self.lbl_image.setText("")
            w, h = pixmap.width(), pixmap.height()
            self.lbl_resolution.setText(f"{w} × {h}")
            self.lbl_resolution.show()

    def _on_image_clicked(self, event):
        images = self.item.get("images") or []
        if isinstance(images, str):
            try:
                images = json.loads(images)
            except Exception:
                images = [images]
        if not isinstance(images, list):
            images = [str(images)]
        valid_images = [img for img in images if img and str(img).startswith(("http://", "https://", "data:"))]
        thumb = self.item.get("thumbnail") or self.item.get("image_url") or ""
        if thumb and str(thumb).startswith(("http://", "https://", "data:")):
            if thumb not in valid_images:
                valid_images.append(thumb)

        valid_images = [self._to_high_res_url(img) for img in valid_images]

        if valid_images:
            show_image_preview(valid_images, title=f"查看大图 - {self.item_id}", parent=self.window(), initial_index=0)
        else:
            pm = getattr(self, "_cached_pixmap", None) or self.lbl_image.pixmap()
            if pm and not pm.isNull():
                show_image_preview(pm, title=f"查看大图 - {self.item_id}", parent=self.window())

    def _copy_id(self):
        cb = QApplication.clipboard()
        if cb:
            cb.setText(self.item_id)

    def mousePressEvent(self, event):
        if getattr(self, "is_in_quick_edit", False):
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_selection()
        super().mousePressEvent(event)

    def _toggle_selection(self):
        if getattr(self, "is_in_quick_edit", False):
            return
        self.checkbox.setChecked(not self.checkbox.isChecked())

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
    field_updated = Signal(str, str, object)
    variant_drilldown_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { border: none; background: #171B19; }")

        self.container = QWidget()
        self.container.setObjectName("VaultCardGridContainer")
        # Scoped to the container itself: an unscoped rule would repaint every label inside the cards
        self.container.setStyleSheet("QWidget#VaultCardGridContainer { background: #171B19; }")
        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)
        self.grid_layout.setHorizontalSpacing(12)
        self.grid_layout.setVerticalSpacing(12)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.setWidget(self.container)

        self.cards: Dict[str, ProductCardWidget] = {}
        self.selected_ids: Set[str] = set()
        self._cached_items: List[Dict[str, Any]] = []
        self._current_cols: int = 0
        self._spacer_widgets: List[QWidget] = []

    def _calc_cols(self) -> int:
        vp_w = self.viewport().width() if self.viewport().width() > 300 else 1160
        usable_w = max(216, vp_w - 24)
        return max(1, (usable_w + 12) // 228)

    def _clear_spacers(self) -> None:
        for sp in self._spacer_widgets:
            self.grid_layout.removeWidget(sp)
            sp.deleteLater()
        self._spacer_widgets.clear()

    def _apply_column_stretches(self, cols: int) -> None:
        self._clear_spacers()
        for c in range(24):
            self.grid_layout.setColumnStretch(c, 1 if c < cols else 0)
        # Keep uniform column widths when fewer items than columns exist
        item_cnt = len(self._cached_items)
        if 0 < item_cnt < cols:
            for c in range(item_cnt, cols):
                sp = QWidget(self.container)
                sp.setFixedHeight(1)
                sp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                self.grid_layout.addWidget(sp, 0, c)
                self._spacer_widgets.append(sp)

    def set_items(self, items: List[Dict[str, Any]]):
        """Re-populates the card stream."""
        self.setUpdatesEnabled(False)
        try:
            self._cached_items = items
            self._clear_spacers()
            while self.grid_layout.count():
                item = self.grid_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

            self.cards.clear()
            self.selected_ids.clear()

            cols = self._calc_cols()
            self._current_cols = cols
            self._apply_column_stretches(cols)

            for idx, it in enumerate(items):
                pid = str(it.get("id") or "")
                card = ProductCardWidget(it)
                card.selection_changed.connect(self._on_item_selection)
                card.edit_clicked.connect(self.edit_requested.emit)
                card.verify_clicked.connect(self.verify_requested.emit)
                card.field_updated.connect(self.field_updated.emit)
                card.variant_drilldown_requested.connect(self.variant_drilldown_requested.emit)

                row = idx // cols
                col = idx % cols
                self.grid_layout.addWidget(card, row, col)
                self.cards[pid] = card
        finally:
            self.setUpdatesEnabled(True)

    def _relayout_cards(self, cols: int):
        if cols < 1:
            cols = 1
        self.setUpdatesEnabled(False)
        try:
            for card in self.cards.values():
                self.grid_layout.removeWidget(card)
            self._apply_column_stretches(cols)
            for idx, it in enumerate(self._cached_items):
                pid = str(it.get("id") or "")
                if pid in self.cards:
                    card = self.cards[pid]
                    row = idx // cols
                    col = idx % cols
                    self.grid_layout.addWidget(card, row, col)
        finally:
            self.setUpdatesEnabled(True)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._cached_items:
            return
        cols = self._calc_cols()
        if cols != self._current_cols:
            self._current_cols = cols
            self._relayout_cards(cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._cached_items:
            return
        cols = self._calc_cols()
        if cols != getattr(self, "_current_cols", 0) and cols > 0:
            self._current_cols = cols
            self._relayout_cards(cols)

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
