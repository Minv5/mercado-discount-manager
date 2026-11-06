from __future__ import annotations

from typing import Any, Dict, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class VaultAICapsuleBar(QFrame):
    """AI Price Verifier Control Bar with 8 status capsules matching YuanYou layout."""
    ai_verify_clicked = Signal()
    list_verify_clicked = Signal()
    capsule_clicked = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("VaultAICapsuleBar")
        self.setStyleSheet("""
            QFrame#VaultAICapsuleBar {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 12px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # Left: Version and Title
        title_box = QHBoxLayout()
        title_box.setSpacing(6)
        lbl_icon = QLabel("🤖 AI核价台")
        lbl_icon.setStyleSheet("font-weight: bold; color: #2563eb; font-size: 13px;")
        lbl_ver = QLabel("v1.4.36")
        lbl_ver.setStyleSheet("background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; border-radius: 4px; padding: 1px 6px; font-size: 11px; font-weight: 500;")
        title_box.addWidget(lbl_icon)
        title_box.addWidget(lbl_ver)
        layout.addLayout(title_box)

        # Action Buttons
        self.btn_ai_verify = QPushButton("🚀 AI核价 (已选/本页)")
        self.btn_ai_verify.setStyleSheet("""
            QPushButton {
                background: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #1d4ed8; }
        """)
        self.btn_ai_verify.clicked.connect(self.ai_verify_clicked.emit)
        layout.addWidget(self.btn_ai_verify)

        self.btn_list_verify = QPushButton("🎯 清单核价")
        self.btn_list_verify.setStyleSheet("""
            QPushButton {
                background: #f8fafc;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: #f1f5f9; border-color: #94a3b8; }
        """)
        self.btn_list_verify.clicked.connect(self.list_verify_clicked.emit)
        layout.addWidget(self.btn_list_verify)

        # 8 Capsule Status Badges
        self.capsule_defs = [
            ("this_page", "本页", "#475569", "#f1f5f9"),
            ("pending_verify", "待核价", "#c2410c", "#fff7ed"),
            ("pending_fill", "待回填", "#1d4ed8", "#eff6ff"),
            ("filled", "已回填", "#15803d", "#f0fdf4"),
            ("questionable", "存疑待审", "#b91c1c", "#fef2f2"),
            ("size_anomaly", "尺寸异常", "#be185d", "#fdf2f8"),
            ("infringement_skip", "侵权跳过", "#b45309", "#fffbeb"),
            ("failed", "失败", "#b91c1c", "#fef2f2"),
        ]

        self.capsule_labels: Dict[str, QLabel] = {}
        for key, name, text_color, bg_color in self.capsule_defs:
            lbl = QLabel(f"{name} 0")
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.setStyleSheet(f"""
                QLabel {{
                    background: {bg_color};
                    color: {text_color};
                    border: 1px solid {text_color}40;
                    border-radius: 12px;
                    padding: 3px 10px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QLabel:hover {{
                    border: 1px solid {text_color};
                }}
            """)
            lbl.mousePressEvent = lambda ev, k=key: self.capsule_clicked.emit(k)
            layout.addWidget(lbl)
            self.capsule_labels[key] = lbl

        layout.addStretch()

    def update_counts(self, counts: Dict[str, int]):
        for key, name, text_color, bg_color in self.capsule_defs:
            val = counts.get(key, 0)
            if key in self.capsule_labels:
                self.capsule_labels[key].setText(f"{name} {val}")


class VaultFilterBar(QWidget):
    """3-Row comprehensive filter bar strictly aligned with YuanYou layout."""
    search_triggered = Signal(dict)
    reset_triggered = Signal()
    import_cost_clicked = Signal()
    create_table_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("VaultFilterBar")

        self.is_collapsed = False
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        # Container styling
        self.setStyleSheet("""
            QWidget#VaultFilterBar {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            QLabel {
                font-size: 12px;
                color: #475569;
                font-weight: 500;
            }
            QLineEdit, QComboBox, QDateEdit {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 12px;
                background: #f8fafc;
                color: #0f172a;
                min-height: 26px;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus {
                border-color: #2563eb;
                background: #ffffff;
            }
        """)

        # ================= ROW 1 =================
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        # 1. 店铺
        row1.addWidget(QLabel("店铺:"))
        self.store_combo = QComboBox()
        self.store_combo.addItem("全部店铺", "")
        row1.addWidget(self.store_combo)

        # 2. 站点
        row1.addWidget(QLabel("站点:"))
        self.site_combo = QComboBox()
        self.site_combo.addItem("全部站点", "")
        self.site_combo.addItem("🇧🇷 巴西 (MLB)", "MLB")
        self.site_combo.addItem("🇲🇽 墨西哥 (MLM)", "MLM")
        self.site_combo.addItem("🇨🇱 智利 (MLC)", "MLC")
        self.site_combo.addItem("🇨🇴 哥伦比亚 (MCO)", "MCO")
        self.site_combo.addItem("🇦🇷 阿根廷 (MLA)", "MLA")
        self.site_combo.addItem("🇺🇾 乌拉圭 (MLU)", "MLU")
        self.site_combo.addItem("🇵🇪 秘鲁 (MPE)", "MPE")
        row1.addWidget(self.site_combo)

        # 3. 状态
        row1.addWidget(QLabel("状态:"))
        self.status_combo = QComboBox()
        self.status_combo.addItem("全部状态", "")
        self.status_combo.addItem("🟢 在售/活跃", "ready")
        self.status_combo.addItem("🟡 暂停", "paused")
        self.status_combo.addItem("⚪ 草稿", "draft")
        row1.addWidget(self.status_combo)

        # 4. 产品分组
        row1.addWidget(QLabel("产品分组:"))
        self.group_combo = QComboBox()
        self.group_combo.addItem("全部分组", "")
        self.group_combo.addItem("未分组", "-1")
        row1.addWidget(self.group_combo)

        row1.addStretch()
        main_layout.addLayout(row1)

        # ================= ROW 2 =================
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        # 1. 自动识别语言的商品标题搜索框
        row2.addWidget(QLabel("商品标题:"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("自动识别中/西/葡/英标题搜索...")
        self.title_input.setMinimumWidth(240)
        self.title_input.returnPressed.connect(self._on_search)
        row2.addWidget(self.title_input)

        # 2. 商品 ID
        row2.addWidget(QLabel("商品ID:"))
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("内部商品ID")
        self.id_input.setFixedWidth(130)
        self.id_input.returnPressed.connect(self._on_search)
        row2.addWidget(self.id_input)

        # 3. 原美客多 ID
        row2.addWidget(QLabel("原美客多ID:"))
        self.cbt_id_input = QLineEdit()
        self.cbt_id_input.setPlaceholderText("CBT商品ID")
        self.cbt_id_input.setFixedWidth(130)
        self.cbt_id_input.returnPressed.connect(self._on_search)
        row2.addWidget(self.cbt_id_input)

        # 4. CBT 分类
        row2.addWidget(QLabel("CBT分类:"))
        self.category_combo = QComboBox()
        self.category_combo.addItem("全部分类", "")
        row2.addWidget(self.category_combo)

        row2.addStretch()
        main_layout.addLayout(row2)

        # ================= ROW 3 (Collapsible) =================
        self.row3_widget = QWidget()
        row3 = QHBoxLayout(self.row3_widget)
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setSpacing(8)

        # 售价区间
        row3.addWidget(QLabel("售价:"))
        self.min_price = QLineEdit()
        self.min_price.setPlaceholderText("Min")
        self.min_price.setFixedWidth(55)
        row3.addWidget(self.min_price)
        row3.addWidget(QLabel("~"))
        self.max_price = QLineEdit()
        self.max_price.setPlaceholderText("Max")
        self.max_price.setFixedWidth(55)
        row3.addWidget(self.max_price)

        # 成本区间
        row3.addWidget(QLabel("成本:"))
        self.min_cost = QLineEdit()
        self.min_cost.setPlaceholderText("Min")
        self.min_cost.setFixedWidth(55)
        row3.addWidget(self.min_cost)
        row3.addWidget(QLabel("~"))
        self.max_cost = QLineEdit()
        self.max_cost.setPlaceholderText("Max")
        self.max_cost.setFixedWidth(55)
        row3.addWidget(self.max_cost)

        # 重量区间
        row3.addWidget(QLabel("重量(g):"))
        self.min_weight = QLineEdit()
        self.min_weight.setPlaceholderText("Min")
        self.min_weight.setFixedWidth(55)
        row3.addWidget(self.min_weight)
        row3.addWidget(QLabel("~"))
        self.max_weight = QLineEdit()
        self.max_weight.setPlaceholderText("Max")
        self.max_weight.setFixedWidth(55)
        row3.addWidget(self.max_weight)

        # 变体与尺寸
        row3.addWidget(QLabel("变体:"))
        self.variant_combo = QComboBox()
        self.variant_combo.addItem("全部", "")
        self.variant_combo.addItem("有变体", "yes")
        self.variant_combo.addItem("无变体", "no")
        row3.addWidget(self.variant_combo)

        row3.addWidget(QLabel("尺寸:"))
        self.size_combo = QComboBox()
        self.size_combo.addItem("全部", "")
        self.size_combo.addItem("有尺寸", "yes")
        self.size_combo.addItem("无尺寸", "no")
        row3.addWidget(self.size_combo)

        # 排序
        row3.addWidget(QLabel("排序:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("创建时间 (最新)", ("created_at", "desc"))
        self.sort_combo.addItem("售价 (从高到低)", ("price", "desc"))
        self.sort_combo.addItem("售价 (从低到高)", ("price", "asc"))
        self.sort_combo.addItem("利润 (从高到低)", ("profit", "desc"))
        self.sort_combo.addItem("库存 (从多到少)", ("stock", "desc"))
        self.sort_combo.addItem("重量 (从轻到重)", ("weight", "asc"))
        row3.addWidget(self.sort_combo)

        row3.addStretch()

        # Action Buttons
        self.btn_reset = QPushButton("重 置")
        self.btn_reset.setStyleSheet("""
            QPushButton {
                background: #f8fafc;
                color: #475569;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: #f1f5f9; border-color: #94a3b8; color: #1e293b; }
        """)
        self.btn_reset.clicked.connect(self._on_reset)
        row3.addWidget(self.btn_reset)

        self.btn_search = QPushButton("查 询")
        self.btn_search.setStyleSheet("""
            QPushButton {
                background: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 4px 18px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #1d4ed8; }
        """)
        self.btn_search.clicked.connect(self._on_search)
        row3.addWidget(self.btn_search)

        self.btn_toggle = QPushButton("收 起 ∧")
        self.btn_toggle.setStyleSheet("color: #2563eb; border: none; font-size: 12px; background: transparent; font-weight: 500;")
        self.btn_toggle.clicked.connect(self._toggle_collapse)
        row3.addWidget(self.btn_toggle)

        # Table & Cost buttons
        self.btn_import_cost = QPushButton("📥 导入成本")
        self.btn_import_cost.setStyleSheet("""
            QPushButton {
                background: #f0fdf4;
                color: #15803d;
                border: 1px solid #86efac;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #dcfce7; }
        """)
        self.btn_import_cost.clicked.connect(self.import_cost_clicked.emit)
        row3.addWidget(self.btn_import_cost)

        self.btn_create_table = QPushButton("➕ 表格创建")
        self.btn_create_table.setStyleSheet("""
            QPushButton {
                background: #eff6ff;
                color: #1e40af;
                border: 1px solid #93c5fd;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #dbeafe; }
        """)
        self.btn_create_table.clicked.connect(self.create_table_clicked.emit)
        row3.addWidget(self.btn_create_table)

        main_layout.addWidget(self.row3_widget)

    def _toggle_collapse(self):
        self.is_collapsed = not self.is_collapsed
        self.row3_widget.setVisible(not self.is_collapsed)
        self.btn_toggle.setText("展 开 ∨" if self.is_collapsed else "收 起 ∧")

    def _on_search(self):
        filters = self.get_filters()
        self.search_triggered.emit(filters)

    def _on_reset(self):
        self.store_combo.setCurrentIndex(0)
        self.site_combo.setCurrentIndex(0)
        self.status_combo.setCurrentIndex(0)
        self.group_combo.setCurrentIndex(0)
        self.title_input.clear()
        self.id_input.clear()
        self.cbt_id_input.clear()
        self.category_combo.setCurrentIndex(0)
        self.min_price.clear()
        self.max_price.clear()
        self.min_cost.clear()
        self.max_cost.clear()
        self.min_weight.clear()
        self.max_weight.clear()
        self.variant_combo.setCurrentIndex(0)
        self.size_combo.setCurrentIndex(0)
        self.sort_combo.setCurrentIndex(0)
        self.reset_triggered.emit()
        self._on_search()

    def get_filters(self) -> Dict[str, Any]:
        filters: Dict[str, Any] = {}
        if self.store_combo.currentData():
            filters["account_id"] = self.store_combo.currentData()
        if self.site_combo.currentData():
            filters["site_id"] = self.site_combo.currentData()
        if self.status_combo.currentData():
            filters["status"] = self.status_combo.currentData()
        if self.group_combo.currentData():
            filters["group_id"] = self.group_combo.currentData()

        if self.title_input.text().strip():
            kw = self.title_input.text().strip()
            filters["title"] = kw
            filters["keyword"] = kw
        if self.id_input.text().strip():
            filters["id"] = self.id_input.text().strip()
        if self.cbt_id_input.text().strip():
            filters["cbt_item_id"] = self.cbt_id_input.text().strip()
        if self.category_combo.currentData():
            filters["category_id"] = self.category_combo.currentData()

        # Ranges
        try:
            if self.min_price.text().strip():
                filters["min_price"] = float(self.min_price.text().strip())
            if self.max_price.text().strip():
                filters["max_price"] = float(self.max_price.text().strip())
        except ValueError:
            pass

        try:
            if self.min_cost.text().strip():
                filters["min_cost"] = float(self.min_cost.text().strip())
            if self.max_cost.text().strip():
                filters["max_cost"] = float(self.max_cost.text().strip())
        except ValueError:
            pass

        try:
            if self.min_weight.text().strip():
                filters["min_weight"] = float(self.min_weight.text().strip())
            if self.max_weight.text().strip():
                filters["max_weight"] = float(self.max_weight.text().strip())
        except ValueError:
            pass

        if self.variant_combo.currentData():
            filters["has_variants"] = self.variant_combo.currentData() == "yes"
        if self.size_combo.currentData():
            filters["has_size"] = self.size_combo.currentData() == "yes"

        sort_data = self.sort_combo.currentData()
        if sort_data:
            filters["sort_by"] = sort_data[0]
            filters["sort_order"] = sort_data[1]

        return filters

    def set_groups(self, groups: list[dict[str, Any]]):
        self.group_combo.clear()
        self.group_combo.addItem("全部分组", "")
        self.group_combo.addItem("未分组", "-1")
        for g in groups:
            self.group_combo.addItem(g.get("name", ""), str(g.get("id", "")))
