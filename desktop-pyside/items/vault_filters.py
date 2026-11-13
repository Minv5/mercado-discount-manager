from __future__ import annotations

from typing import Any, Dict, Optional
from PySide6.QtCore import QSignalBlocker, Qt, Signal
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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui_common.combos import populate_store_combo


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
                background: #202820;
                border: 1px solid #4E472F;
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
        lbl_icon = QLabel("AI核价台")
        lbl_icon.setStyleSheet("font-weight: bold; color: #4ADE80; font-size: 13px;")
        lbl_ver = QLabel("v1.4.36")
        lbl_ver.setStyleSheet("background: rgba(47, 107, 63, 0.3); color: #4ADE80; border: 1px solid #2F6B3F; border-radius: 4px; padding: 1px 6px; font-size: 11px; font-weight: 500;")
        title_box.addWidget(lbl_icon)
        title_box.addWidget(lbl_ver)
        layout.addLayout(title_box)

        # Action Buttons
        self.btn_ai_verify = QPushButton("AI核价 (已选/本页)")
        self.btn_ai_verify.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #356F45; }
        """)
        self.btn_ai_verify.clicked.connect(self.ai_verify_clicked.emit)
        layout.addWidget(self.btn_ai_verify)

        self.btn_list_verify = QPushButton("清单核价")
        self.btn_list_verify.setStyleSheet("""
            QPushButton {
                background: #18201C;
                color: #E6E2D8;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: #252A25; border-color: #6E5D2A; }
        """)
        self.btn_list_verify.clicked.connect(self.list_verify_clicked.emit)
        layout.addWidget(self.btn_list_verify)

        # 8 Capsule Status Badges
        self.capsule_defs = [
            ("this_page", "本页", "#C8C3B7", "rgba(200, 195, 183, 0.12)"),
            ("pending_verify", "待核价", "#FB923C", "rgba(251, 146, 60, 0.15)"),
            ("pending_fill", "待回填", "#60A5FA", "rgba(96, 165, 250, 0.15)"),
            ("filled", "已回填", "#4ADE80", "rgba(74, 222, 128, 0.15)"),
            ("questionable", "存疑待审", "#F87171", "rgba(248, 113, 113, 0.15)"),
            ("size_anomaly", "尺寸异常", "#F472B6", "rgba(244, 114, 182, 0.15)"),
            ("infringement_skip", "侵权跳过", "#FBBF24", "rgba(251, 191, 36, 0.15)"),
            ("failed", "失败", "#EF4444", "rgba(239, 68, 68, 0.15)"),
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

    def set_active_capsule(self, active_key: Optional[str]):
        """Visually marks the active filter capsule."""
        for key, name, text_color, bg_color in self.capsule_defs:
            lbl = self.capsule_labels.get(key)
            if not lbl:
                continue
            is_active = (key == active_key)
            border_style = f"2px solid {text_color}" if is_active else f"1px solid {text_color}40"
            font_weight = "bold" if is_active else "600"
            lbl.setStyleSheet(f"""
                QLabel {{
                    background: {bg_color};
                    color: {text_color};
                    border: {border_style};
                    border-radius: 12px;
                    padding: 3px 10px;
                    font-size: 11px;
                    font-weight: {font_weight};
                }}
                QLabel:hover {{
                    border: 2px solid {text_color};
                }}
            """)

    def update_counts(self, counts: Dict[str, int]):
        for key, name, text_color, bg_color in self.capsule_defs:
            val = counts.get(key, 0)
            if key in self.capsule_labels:
                self.capsule_labels[key].setText(f"{name} {val}")


class VaultFilterBar(QWidget):
    """Ultra-compact 2-Row ERP Product Filter Bar with collapsible drawer for advanced filters."""
    search_triggered = Signal(dict)
    reset_triggered = Signal()
    import_cost_clicked = Signal()
    manual_create_clicked = Signal()
    create_table_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None, auth_mgr: Optional[Any] = None):
        super().__init__(parent)
        self.setObjectName("VaultFilterBar")
        self.auth_mgr = auth_mgr
        self.is_collapsed = True
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(6)

        # Container styling (scoped strictly to #VaultFilterBar so descendants retain borders & surface)
        self.setStyleSheet("""
            QWidget#VaultFilterBar {
                background: #202820;
                border: 1px solid #4E472F;
                border-radius: 8px;
            }
            QWidget#VaultFilterBar QLabel {
                font-size: 12px;
                color: #C8C3B7;
                font-weight: 500;
                background: transparent;
                border: none;
            }
            QWidget#VaultFilterBar QLineEdit,
            QWidget#VaultFilterBar QComboBox,
            QWidget#VaultFilterBar QDateEdit {
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 12px;
                background: #18201C;
                color: #E6E2D8;
                min-height: 26px;
                max-height: 26px;
            }
            QWidget#VaultFilterBar QLineEdit:focus,
            QWidget#VaultFilterBar QComboBox:focus,
            QWidget#VaultFilterBar QDateEdit:focus {
                border-color: #2F6B3F;
                background: #1C231F;
            }
            QWidget#VaultFilterBar QComboBox QAbstractItemView {
                background: #18201C;
                color: #E6E2D8;
                border: 1px solid #4E472F;
                selection-background-color: #2F6B3F;
            }
        """)

        # ================= TOP 2-ROW FILTER & ACTION CARD =================
        top_card = QWidget()
        top_card.setObjectName("VaultTopCard")
        top_card.setStyleSheet("QWidget#VaultTopCard { background: transparent; border: none; }")
        card_layout = QHBoxLayout(top_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(14)

        # 4 Columns x 2 Rows Grid for Left Filters
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setContentsMargins(0, 0, 0, 0)

        def make_lbl(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setFixedWidth(74)
            return lbl

        # Col 0: 店铺 (行0) / 商品标题 (行1)
        grid.addWidget(make_lbl("店　　铺:"), 0, 0)
        self.store_combo = QComboBox()
        self.store_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.reload_stores(self.auth_mgr)
        self.store_combo.currentIndexChanged.connect(self._on_search)
        grid.addWidget(self.store_combo, 0, 1)

        grid.addWidget(make_lbl("商品标题:"), 1, 0)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("中/西/葡/英标题搜索...")
        self.title_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.title_input.returnPressed.connect(self._on_search)
        grid.addWidget(self.title_input, 1, 1)

        # Col 1: 站点 (行0) / CBT分类 (行1)
        grid.addWidget(make_lbl("站　　点:"), 0, 2)
        self.site_combo = QComboBox()
        self.site_combo.addItem("全部站点", "")
        self.site_combo.addItem("🇧🇷 巴西 (MLB)", "MLB")
        self.site_combo.addItem("🇲🇽 墨西哥 (MLM)", "MLM")
        self.site_combo.addItem("🇨🇱 智利 (MLC)", "MLC")
        self.site_combo.addItem("🇨🇴 哥伦比亚 (MCO)", "MCO")
        self.site_combo.addItem("🇦🇷 阿根廷 (MLA)", "MLA")
        self.site_combo.addItem("🇺🇾 乌拉圭 (MLU)", "MLU")
        self.site_combo.addItem("🇵🇪 秘鲁 (MPE)", "MPE")
        self.site_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(self.site_combo, 0, 3)

        grid.addWidget(make_lbl("CBT分类:"), 1, 2)
        self.category_combo = QComboBox()
        self.category_combo.addItem("全部分类", "")
        self.category_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(self.category_combo, 1, 3)

        # Col 2: 状态 (行0) / 商品ID (行1)
        grid.addWidget(make_lbl("状　　态:"), 0, 4)
        self.status_combo = QComboBox()
        self.status_combo.addItem("全部状态", "")
        self.status_combo.addItem("🟢 在售/活跃", "active")
        self.status_combo.addItem("🟡 暂停", "paused")
        self.status_combo.addItem("⚪ 草稿", "draft")
        self.status_combo.addItem("🔴 侵权/下架", "infringed")
        self.status_combo.addItem("📦 已本地发布", "published")
        self.status_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(self.status_combo, 0, 5)

        grid.addWidget(make_lbl("商 品 ID:"), 1, 4)
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("内部商品ID")
        self.id_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.id_input.returnPressed.connect(self._on_search)
        grid.addWidget(self.id_input, 1, 5)

        # Col 3: 产品分组 (行0) / 原美客多ID (行1)
        grid.addWidget(make_lbl("产品分组:"), 0, 6)
        self.group_combo = QComboBox()
        self.group_combo.addItem("全部分组", "")
        self.group_combo.addItem("未分组", "-1")
        self.group_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(self.group_combo, 0, 7)

        grid.addWidget(make_lbl("原美客多ID:"), 1, 6)
        self.cbt_id_input = QLineEdit()
        self.cbt_id_input.setPlaceholderText("CBT商品ID")
        self.cbt_id_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cbt_id_input.returnPressed.connect(self._on_search)
        grid.addWidget(self.cbt_id_input, 1, 7)

        # Proportional stretches across the 4 data columns
        grid.setColumnStretch(1, 12)
        grid.setColumnStretch(3, 10)
        grid.setColumnStretch(5, 10)
        grid.setColumnStretch(7, 10)

        card_layout.addLayout(grid, stretch=1)

        # Vertical Divider
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #3E382A; margin: 2px 2px;")
        card_layout.addWidget(sep)

        # Right Action Box (Unified 2x2 Button Grid)
        right_box = QVBoxLayout()
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(8)

        # Row 0: 手动创建 + 表格创建 + 高级筛选
        r0_act = QHBoxLayout()
        r0_act.setSpacing(6)

        self.btn_manual_create = QPushButton("＋ 手动创建产品")
        self.btn_manual_create.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_manual_create.setStyleSheet("""
            QPushButton {
                background: #203A2B;
                color: #4ADE80;
                border: 1px solid #2F6B3F;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 600;
                min-height: 26px;
                max-height: 26px;
            }
            QPushButton:hover { background: #2A4D38; border-color: #4ADE80; color: #FFFFFF; }
        """)
        self.btn_manual_create.clicked.connect(self.manual_create_clicked.emit)

        self.btn_create_table = QPushButton("📊 表格创建")
        self.btn_create_table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_create_table.setStyleSheet("""
            QPushButton {
                background: #18201C;
                color: #E6E2D8;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 600;
                min-height: 26px;
                max-height: 26px;
            }
            QPushButton:hover { background: #252A25; border-color: #6E5D2A; color: #F6F3EA; }
        """)
        self.btn_create_table.clicked.connect(self.create_table_clicked.emit)

        self.btn_toggle = QPushButton("高级筛选 ∨")
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #4ADE80;
                border: 1px solid #3E382A;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 500;
                min-height: 26px;
                max-height: 26px;
            }
            QPushButton:hover { border-color: #4ADE80; background: rgba(74, 222, 128, 0.08); }
        """)
        self.btn_toggle.clicked.connect(self._toggle_collapse)

        r0_act.addWidget(self.btn_manual_create)
        r0_act.addWidget(self.btn_create_table)
        r0_act.addWidget(self.btn_toggle)
        right_box.addLayout(r0_act)

        # Row 1: 重 置 + 查 询
        r1_act = QHBoxLayout()
        r1_act.setSpacing(8)

        self.btn_reset = QPushButton("重 置")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setStyleSheet("""
            QPushButton {
                background: #18201C;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 12px;
                font-weight: 500;
                min-height: 26px;
                max-height: 26px;
            }
            QPushButton:hover { background: #252A25; border-color: #6E5D2A; color: #F6F3EA; }
        """)
        self.btn_reset.clicked.connect(self._on_reset)

        self.btn_search = QPushButton("查 询")
        self.btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 4px 18px;
                font-size: 12px;
                font-weight: bold;
                min-height: 26px;
                max-height: 26px;
            }
            QPushButton:hover { background: #356F45; }
        """)
        self.btn_search.clicked.connect(self._on_search)

        r1_act.addWidget(self.btn_reset)
        r1_act.addWidget(self.btn_search)
        right_box.addLayout(r1_act)

        card_layout.addLayout(right_box)
        main_layout.addWidget(top_card)

        # ================= ROW 2: Advanced Section (Compact Single-Row Layout A) =================
        self.adv_widget = QWidget()
        self.adv_widget.setObjectName("VaultAdvWidget")
        self.adv_widget.setStyleSheet("QWidget#VaultAdvWidget { background: transparent; border: none; }")
        self.row3_widget = self.adv_widget  # Backward compatibility
        adv_layout = QVBoxLayout(self.adv_widget)
        adv_layout.setContentsMargins(0, 4, 0, 0)
        adv_layout.setSpacing(8)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1)
        div.setStyleSheet("border: none; border-top: 1px dashed #3E382A; background: transparent;")
        adv_layout.addWidget(div)

        adv_row = QHBoxLayout()
        adv_row.setContentsMargins(0, 0, 0, 0)
        adv_row.setSpacing(10)

        def _make_adv_lbl(text: str, width: int) -> QLabel:
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setFixedWidth(width)
            return lbl

        def _make_range_box(inp1: QLineEdit, inp2: QLineEdit) -> QWidget:
            box = QWidget()
            box.setObjectName("VaultRangeBox")
            box.setStyleSheet("QWidget#VaultRangeBox { background: transparent; border: none; }")
            box_layout = QHBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(4)
            inp1.setPlaceholderText("Min")
            inp2.setPlaceholderText("Max")
            inp1.returnPressed.connect(self._on_search)
            inp2.returnPressed.connect(self._on_search)
            sep = QLabel("~")
            sep.setFixedWidth(10)
            sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sep.setStyleSheet("color: #8C877D; font-weight: bold;")
            box_layout.addWidget(inp1, 1)
            box_layout.addWidget(sep)
            box_layout.addWidget(inp2, 1)
            return box

        # 1. 净收益区间
        self.min_net = QLineEdit()
        self.max_net = QLineEdit()
        self.min_price = self.min_net  # Compatibility alias
        self.max_price = self.max_net
        adv_row.addWidget(_make_adv_lbl("净收益区间:", 68))
        adv_row.addWidget(_make_range_box(self.min_net, self.max_net), stretch=10)

        # 2. 重 量 (g)
        self.min_weight = QLineEdit()
        self.max_weight = QLineEdit()
        adv_row.addWidget(_make_adv_lbl("重 量 (g):", 62))
        adv_row.addWidget(_make_range_box(self.min_weight, self.max_weight), stretch=10)

        # 3. 包装尺寸 (长 * 宽 * 高，任意留空/填 * / - 均代表该维度不限)
        dim_tip = "留空或填 * / - 表示该维度不限（例：长填 20、宽高不填，或直接输入 20*-*-）"
        self.length_input = QLineEdit()
        self.width_input = QLineEdit()
        self.height_input = QLineEdit()
        for dim_inp, ph in ((self.length_input, "长"), (self.width_input, "宽"), (self.height_input, "高")):
            dim_inp.setPlaceholderText(ph)
            dim_inp.setToolTip(dim_tip)
            dim_inp.returnPressed.connect(self._on_search)

        size_box = QWidget()
        size_box.setObjectName("VaultSizeBox")
        size_box.setStyleSheet("QWidget#VaultSizeBox { background: transparent; border: none; }")
        sb_layout = QHBoxLayout(size_box)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(4)
        for idx_d, dim_inp in enumerate((self.length_input, self.width_input, self.height_input)):
            sb_layout.addWidget(dim_inp, 1)
            if idx_d < 2:
                star = QLabel("*")
                star.setFixedWidth(8)
                star.setAlignment(Qt.AlignmentFlag.AlignCenter)
                star.setStyleSheet("color: #8C877D; font-weight: bold;")
                sb_layout.addWidget(star)
        lbl_cm = QLabel("cm")
        lbl_cm.setFixedWidth(22)
        lbl_cm.setStyleSheet("color: #8C877D; font-size: 11px;")
        sb_layout.addWidget(lbl_cm)

        lbl_size = _make_adv_lbl("包装尺寸:", 60)
        lbl_size.setToolTip(dim_tip)
        adv_row.addWidget(lbl_size)
        adv_row.addWidget(size_box, stretch=15)

        # 4. 是否有变体
        self.variant_combo = QComboBox()
        self.variant_combo.addItem("全部", "")
        self.variant_combo.addItem("有变体", "yes")
        self.variant_combo.addItem("无变体", "no")
        self.variant_combo.currentIndexChanged.connect(self._on_search)
        adv_row.addWidget(_make_adv_lbl("是否有变体:", 68))
        adv_row.addWidget(self.variant_combo, stretch=7)

        # 5. 排序方式
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("创建时间 (最新)", ("created_at", "desc"))
        self.sort_combo.addItem("净收益 (从高到低)", ("price", "desc"))
        self.sort_combo.addItem("净收益 (从低到高)", ("price", "asc"))
        self.sort_combo.addItem("库存 (从多到少)", ("stock", "desc"))
        self.sort_combo.addItem("重量 (从轻到重)", ("weight", "asc"))
        self.sort_combo.currentIndexChanged.connect(self._on_search)
        adv_row.addWidget(_make_adv_lbl("排序方式:", 60))
        adv_row.addWidget(self.sort_combo, stretch=11)

        adv_layout.addLayout(adv_row)
        main_layout.addWidget(self.adv_widget)

        # Default collapsed: Hides advanced row so default height is only ~68px
        self.is_collapsed = True
        self.adv_widget.hide()

    def _toggle_collapse(self):
        self.is_collapsed = not self.is_collapsed
        self.adv_widget.setVisible(not self.is_collapsed)
        self.btn_toggle.setText("收 起 ∧" if not self.is_collapsed else "高级筛选 ∨")

    def _on_search(self):
        filters = self.get_filters()
        self.search_triggered.emit(filters)

    def _on_reset(self):
        for cb in (
            self.store_combo,
            self.site_combo,
            self.status_combo,
            self.group_combo,
            self.category_combo,
            self.variant_combo,
            self.sort_combo,
        ):
            with QSignalBlocker(cb):
                cb.setCurrentIndex(0)
        self.title_input.clear()
        self.id_input.clear()
        self.cbt_id_input.clear()
        self.min_net.clear()
        self.max_net.clear()
        self.min_weight.clear()
        self.max_weight.clear()
        self.length_input.clear()
        self.width_input.clear()
        self.height_input.clear()
        self.reset_triggered.emit()
        self._on_search()

    @staticmethod
    def _parse_optional_float(text: str) -> Optional[float]:
        raw = (text or "").strip()
        if not raw or raw in ("-", "*", "不限"):
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _extract_dimension_filters(self) -> Dict[str, float]:
        l_raw = self.length_input.text().strip()
        w_raw = self.width_input.text().strip()
        h_raw = self.height_input.text().strip()

        # Support pasting full shorthand like "20*-*-" or "20*15*10" into Length input
        if ("*" in l_raw or "x" in l_raw.lower() or "×" in l_raw) and not w_raw and not h_raw:
            norm = l_raw.replace("×", "*").replace("x", "*").replace("X", "*")
            parts = [p.strip() for p in norm.split("*")]
            l_raw = parts[0] if len(parts) > 0 else ""
            w_raw = parts[1] if len(parts) > 1 else ""
            h_raw = parts[2] if len(parts) > 2 else ""

        dims: Dict[str, float] = {}
        for key, token in (("length", l_raw), ("width", w_raw), ("height", h_raw)):
            val = self._parse_optional_float(token)
            if val is not None:
                dims[key] = val
        return dims

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

        min_net = self._parse_optional_float(self.min_net.text())
        if min_net is not None:
            filters["min_net"] = min_net
            filters["min_price"] = min_net
        max_net = self._parse_optional_float(self.max_net.text())
        if max_net is not None:
            filters["max_net"] = max_net
            filters["max_price"] = max_net

        min_w = self._parse_optional_float(self.min_weight.text())
        if min_w is not None:
            filters["min_weight"] = min_w
        max_w = self._parse_optional_float(self.max_weight.text())
        if max_w is not None:
            filters["max_weight"] = max_w

        filters.update(self._extract_dimension_filters())

        if self.variant_combo.currentData():
            filters["has_variants"] = self.variant_combo.currentData() == "yes"

        sort_data = self.sort_combo.currentData()
        if sort_data:
            filters["sort_by"] = sort_data[0]
            filters["sort_order"] = sort_data[1]

        return filters

    def reload_stores(self, auth_mgr: Optional[Any] = None):
        """Populates store options dynamically from AuthManager."""
        auth = auth_mgr or self.auth_mgr
        if auth is None:
            try:
                from engine.auth import AuthManager
                auth = AuthManager()
            except Exception:
                auth = None
        self.auth_mgr = auth
        populate_store_combo(self.store_combo, auth_mgr=auth, all_label="全部店铺", all_value="")

    def set_stores(self, accounts: Optional[list[Any]] = None, auth_mgr: Optional[Any] = None):
        populate_store_combo(self.store_combo, accounts=accounts, auth_mgr=auth_mgr or self.auth_mgr, all_label="全部店铺", all_value="")

    def set_groups(self, groups: list[dict[str, Any]]):
        cur = self.group_combo.currentData()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("全部分组", "")
        self.group_combo.addItem("未分组", "-1")
        for g in groups:
            self.group_combo.addItem(g.get("name", ""), str(g.get("id", "")))
        idx = self.group_combo.findData(cur) if cur else 0
        self.group_combo.setCurrentIndex(max(0, idx))
        self.group_combo.blockSignals(False)

    def set_categories(self, categories: list[tuple[str, str]]):
        cur = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类", "")
        for cid, cname in categories:
            label = f"{cname} ({cid})" if cname and cname != cid else cid
            self.category_combo.addItem(label, cid)
        if cur:
            idx = self.category_combo.findData(cur)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)
        self.category_combo.blockSignals(False)
