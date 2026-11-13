from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import get_store_alias


_SITE_PILLS: List[tuple[str, str]] = [
    ("MLM", "墨西哥"),
    ("MLB", "巴西"),
    ("MLC", "智利"),
    ("MCO", "哥伦比亚"),
    ("MLA", "阿根廷"),
    ("MLU", "乌拉圭"),
    ("MPE", "秘鲁"),
    ("MLM_FULL", "墨西哥海外仓"),
    ("MLC_FULL", "智利海外仓"),
    ("MLA_FULL", "阿根廷海外仓"),
    ("MLB_FULL", "巴西海外仓"),
    ("MCO_FULL", "哥伦比亚海外仓"),
    ("MLU_FULL", "乌拉圭海外仓"),
]


def _asset_url(filename: str) -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return str(root / "assets" / filename).replace("\\", "/")


_CHECKBOX_SVG_URL = _asset_url("checkmark-white.png")
_RADIO_DOT_SVG_URL = _asset_url("radio-dot-green.png")


class BatchPublishDialog(QDialog):
    """1:1 YuanYou CBT Multi-Store Batch Publish Workspace."""

    def __init__(
        self,
        selected_count: int,
        auth_mgr: Optional[Any] = None,
        product_ids: Optional[List[str]] = None,
        preselected_store_id: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("BatchPublishDialog")
        self.selected_count = selected_count
        self.product_ids = list(product_ids or [])
        self.auth_mgr = auth_mgr
        if self.auth_mgr is None:
            try:
                from engine.auth import AuthManager
                self.auth_mgr = AuthManager()
            except Exception:
                self.auth_mgr = None

        self.setWindowTitle(f"上传产品 - 已选 {selected_count} 个待刊登商品")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.resize(900, 780)
        self.setMinimumSize(780, 620)

        self._accounts_data: List[Dict[str, str]] = []
        self._checked_store_ids: set[str] = set()
        if preselected_store_id:
            self._checked_store_ids.add(str(preselected_store_id))
        self._site_buttons: Dict[str, QPushButton] = {}
        self._store_checkboxes: Dict[str, QCheckBox] = {}

        self._load_accounts_data()
        self._apply_styles()
        self._init_ui()

    def _load_accounts_data(self) -> None:
        raw_accounts: List[Any] = []
        if self.auth_mgr is not None and hasattr(self.auth_mgr, "list_accounts"):
            try:
                raw_accounts = list(self.auth_mgr.list_accounts() or [])
            except Exception:
                raw_accounts = []

        self._accounts_data.clear()
        for acc in raw_accounts:
            if isinstance(acc, dict):
                aid = str(acc.get("account_id") or acc.get("id") or acc.get("user_id") or "").strip()
                site = str(acc.get("site_id") or "CBT").strip()
                raw_name = str(acc.get("store_name") or acc.get("name") or aid).strip()
            else:
                aid = str(
                    getattr(acc, "account_id", "")
                    or getattr(acc, "id", "")
                    or getattr(acc, "user_id", "")
                    or ""
                ).strip()
                site = str(getattr(acc, "site_id", "") or "CBT").strip()
                raw_name = str(getattr(acc, "store_name", "") or getattr(acc, "name", "") or aid).strip()
            if not aid:
                continue
            alias = get_store_alias(self.auth_mgr, aid, site) if self.auth_mgr else raw_name
            self._accounts_data.append({
                "account_id": aid,
                "site_id": site or "CBT",
                "store_name": alias or raw_name or aid,
                "raw_name": raw_name,
            })

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QDialog#BatchPublishDialog {{
                background: #171B19;
                color: #E6E2D8;
            }}
            QScrollArea {{
                background: #171B19;
                border: none;
            }}
            QWidget#PublishScrollContent, QWidget#StoreListInner {{
                background: transparent;
            }}
            QLabel {{
                color: #E6E2D8;
                font-size: 12px;
                background: transparent;
            }}
            QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
                background: #18201C;
                color: #E6E2D8;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                selection-background-color: #2F6B3F;
            }}
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
                border-color: #2F6B3F;
                background: #1C231F;
            }}
            QComboBox QAbstractItemView {{
                background: #18201C;
                color: #E6E2D8;
                border: 1px solid #4E472F;
                selection-background-color: #2F6B3F;
            }}
            QCheckBox, QRadioButton {{
                color: #E6E2D8;
                background: transparent;
                font-size: 12px;
                spacing: 7px;
                padding: 2px 0;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1.5px solid #787366;
                background: #18201C;
            }}
            QCheckBox::indicator:hover {{
                border-color: #4ADE80;
            }}
            QCheckBox::indicator:checked {{
                background: #2F6B3F;
                border: 1.5px solid #4ADE80;
                image: url("{_CHECKBOX_SVG_URL}");
            }}
            QRadioButton::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 8px;
                border: 1.5px solid #787366;
                background: #18201C;
            }}
            QRadioButton::indicator:hover {{
                border-color: #4ADE80;
            }}
            QRadioButton::indicator:checked {{
                background: #18201C;
                border: 2px solid #4ADE80;
                image: url("{_RADIO_DOT_SVG_URL}");
            }}
        """)

    @staticmethod
    def _make_field_label(text: str, required: bool = False) -> QLabel:
        lbl = QLabel()
        if required:
            lbl.setText(f"<span style='color:#F87171;'>*</span> {text}")
        else:
            lbl.setText(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        lbl.setFixedWidth(108)
        lbl.setStyleSheet("color: #C8C3B7; font-size: 12px; font-weight: 600; padding-top: 6px; background: transparent;")
        return lbl

    @staticmethod
    def _make_hint_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #8C877D; font-size: 11px; line-height: 1.45; background: transparent;")
        return lbl

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("PublishScrollContent")
        scroll.setWidget(content)

        grid = QGridLayout(content)
        grid.setContentsMargins(20, 16, 24, 16)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(1, 1)

        row = 0

        # 1. 选择店铺 (Multi-Store & Multi-Site Selector Matrix)
        grid.addWidget(self._make_field_label("选择店铺:", required=True), row, 0)
        grid.addWidget(self._build_store_selector(), row, 1)
        row += 1

        # 2. 商品ID (Multi-line Textarea + Total Count)
        grid.addWidget(self._make_field_label("商品ID:", required=True), row, 0)
        pid_box = QVBoxLayout()
        pid_box.setContentsMargins(0, 0, 0, 0)
        pid_box.setSpacing(4)
        self.txt_product_ids = QPlainTextEdit()
        self.txt_product_ids.setFixedHeight(78)
        self.txt_product_ids.setPlaceholderText("每行一个商品ID...")
        if self.product_ids:
            self.txt_product_ids.setPlainText("\n".join(self.product_ids))
        self.txt_product_ids.textChanged.connect(self._update_product_id_count)
        pid_box.addWidget(self.txt_product_ids)

        initial_cnt = len(self.product_ids) if self.product_ids else self.selected_count
        self.lbl_pid_count = QLabel(f"总数量: {initial_cnt} 个")
        self.lbl_pid_count.setStyleSheet("color: #8C877D; font-size: 11px; background: transparent;")
        pid_box.addWidget(self.lbl_pid_count)
        grid.addLayout(pid_box, row, 1)
        row += 1

        # 3. UP变体上限
        grid.addWidget(self._make_field_label("UP变体上限:"), row, 0)
        var_box = QVBoxLayout()
        var_box.setContentsMargins(0, 0, 0, 0)
        var_box.setSpacing(4)

        var_ctrl = QHBoxLayout()
        var_ctrl.setContentsMargins(0, 0, 0, 0)
        var_ctrl.setSpacing(10)
        self.cb_unlimit_variants = QCheckBox("不限制")
        self.cb_unlimit_variants.setChecked(True)
        self.spin_max_vars = QSpinBox()
        self.spin_max_vars.setRange(0, 100)
        self.spin_max_vars.setValue(0)
        self.spin_max_vars.setFixedWidth(100)
        self.spin_max_vars.setSpecialValueText("不限制")
        self.spin_max_vars.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin_max_vars.setEnabled(False)
        self.cb_unlimit_variants.toggled.connect(self._on_unlimit_variants_toggled)
        var_ctrl.addWidget(self.cb_unlimit_variants)
        var_ctrl.addWidget(self.spin_max_vars)
        var_ctrl.addStretch()
        var_box.addLayout(var_ctrl)

        var_box.addWidget(
            self._make_hint_label(
                "仅对 UP 店铺生效。上传时若产品变体数量超过该上限，则只按上限数量上传。例如设置为 5 时，变体超过 5 个也只上传 5 个。"
            )
        )
        grid.addLayout(var_box, row, 1)
        row += 1

        # 4. * 库存
        grid.addWidget(self._make_field_label("库存:", required=True), row, 0)
        stock_ctrl = QHBoxLayout()
        stock_ctrl.setContentsMargins(0, 0, 0, 0)
        stock_ctrl.setSpacing(8)
        self.spin_force_stock = QSpinBox()
        self.spin_force_stock.setRange(0, 999999)
        self.spin_force_stock.setValue(200)
        self.spin_force_stock.setFixedWidth(180)
        self.spin_force_stock.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        lbl_unit = QLabel("件")
        lbl_unit.setStyleSheet("color: #C8C3B7; font-size: 12px;")
        stock_ctrl.addWidget(self.spin_force_stock)
        stock_ctrl.addWidget(lbl_unit)
        stock_ctrl.addStretch()
        grid.addLayout(stock_ctrl, row, 1)
        row += 1

        # 5. 强制覆盖设置
        grid.addWidget(self._make_field_label("强制覆盖设置:"), row, 0)
        self.cb_force_stock = QCheckBox("强制覆盖库存")
        self.cb_force_stock.setChecked(True)
        grid.addWidget(self.cb_force_stock, row, 1)
        row += 1

        # 6. 尺码表
        grid.addWidget(self._make_field_label("尺码表:"), row, 0)
        size_lbl = self._make_hint_label(
            "上传服装、鞋类等需要尺码表的产品时，系统会自动匹配尺码表，但需提前将尺码表拉取到 ERP。"
        )
        size_lbl.setStyleSheet("color: #C8C3B7; font-size: 12px; padding-top: 6px; background: transparent;")
        grid.addWidget(size_lbl, row, 1)
        row += 1

        # 7. 保修
        grid.addWidget(self._make_field_label("保修:"), row, 0)
        guar_row = QHBoxLayout()
        guar_row.setContentsMargins(0, 0, 0, 0)
        guar_row.setSpacing(10)
        self.combo_guar = QComboBox()
        self.combo_guar.setFixedWidth(220)
        self.combo_guar.addItem("卖家保修", "seller")
        self.combo_guar.addItem("工厂保修", "factory")
        self.combo_guar.addItem("无保修", "none")
        self.combo_guar.currentIndexChanged.connect(self._on_guar_type_changed)

        self.spin_guar_days = QSpinBox()
        self.spin_guar_days.setRange(0, 365)
        self.spin_guar_days.setValue(7)
        self.spin_guar_days.setFixedWidth(180)
        self.spin_guar_days.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        lbl_day = QLabel("天")
        lbl_day.setStyleSheet("color: #C8C3B7; font-size: 12px;")
        guar_row.addWidget(self.combo_guar)
        guar_row.addWidget(self.spin_guar_days)
        guar_row.addWidget(lbl_day)
        guar_row.addStretch()
        grid.addLayout(guar_row, row, 1)
        row += 1

        # 8. 销售类型
        grid.addWidget(self._make_field_label("销售类型:"), row, 0)
        sales_box = QVBoxLayout()
        sales_box.setContentsMargins(0, 0, 0, 0)
        sales_box.setSpacing(4)

        sales_row = QHBoxLayout()
        sales_row.setSpacing(20)
        self.bg_sales = QButtonGroup(self)
        self.radio_sales_prem = QRadioButton("高流量高佣金 (Premium)")
        self.radio_sales_classic = QRadioButton("低流量低佣金 (Classic)")
        self.radio_sales_classic.setChecked(True)
        self.bg_sales.addButton(self.radio_sales_prem, 1)
        self.bg_sales.addButton(self.radio_sales_classic, 2)
        sales_row.addWidget(self.radio_sales_prem)
        sales_row.addWidget(self.radio_sales_classic)
        sales_row.addStretch()
        sales_box.addLayout(sales_row)
        sales_box.addWidget(
            self._make_hint_label(
                "高流量高佣金：商品流量较高，出单之后需要支付美客多佣金变高；低流量低佣金：商品流量较低，出单之后需要支付美客多佣金变低；"
            )
        )
        grid.addLayout(sales_box, row, 1)
        row += 1

        # 9. 价格模式 (With Formulas + Example Card)
        grid.addWidget(self._make_field_label("价格模式:"), row, 0)
        pmode_box = QVBoxLayout()
        pmode_box.setContentsMargins(0, 0, 0, 0)
        pmode_box.setSpacing(6)

        pmode_row = QHBoxLayout()
        pmode_row.setSpacing(24)
        self.bg_pmode = QButtonGroup(self)
        self.radio_pmode_net = QRadioButton("净收益模式")
        self.radio_pmode_price = QRadioButton("售价模式")
        self.radio_pmode_net.setChecked(True)
        self.bg_pmode.addButton(self.radio_pmode_net, 2)
        self.bg_pmode.addButton(self.radio_pmode_price, 1)
        pmode_row.addWidget(self.radio_pmode_net)
        pmode_row.addWidget(self.radio_pmode_price)
        pmode_row.addStretch()
        pmode_box.addLayout(pmode_row)

        lbl_pmode_desc = QLabel(
            "<span style='color:#60A5FA; font-weight:600;'>售价模式：</span>"
            "使用产品库中产品的<span style='color:#60A5FA;'>售价</span>作为发布售价上传，成本和利润不参与本模式的上传金额计算。<br>"
            "<span style='color:#4ADE80; font-weight:600;'>净收益模式：</span>"
            "使用产品库中产品的<span style='color:#4ADE80;'>成本 + 利润</span>之和作为目标净收益上传，不使用产品库中的售价；"
            "美客多根据目标净收益及站点费用计算最终售价。"
            "<span style='color:#F87171; font-weight:600;'>目标净收益（成本 + 利润）须大于 0。</span>"
        )
        lbl_pmode_desc.setWordWrap(True)
        lbl_pmode_desc.setStyleSheet("color: #C8C3B7; font-size: 11px; line-height: 1.5;")
        pmode_box.addWidget(lbl_pmode_desc)

        example_card = QFrame()
        example_card.setObjectName("ExampleCard")
        example_card.setStyleSheet("""
            QFrame#ExampleCard {
                background: rgba(59, 130, 246, 0.12);
                border: 1px solid rgba(96, 165, 250, 0.35);
                border-radius: 6px;
                padding: 8px 10px;
            }
        """)
        ex_layout = QVBoxLayout(example_card)
        ex_layout.setContentsMargins(4, 2, 4, 2)
        ex_layout.setSpacing(4)
        lbl_ex = QLabel(
            "<span style='color:#60A5FA; font-weight:bold;'>举例：</span>"
            "产品库中售价为 <b>30</b>、成本为 <b>12</b>、利润为 <b>6</b>（同一币种）。"
            "售价模式上传 <b>30</b>；净收益模式上传 <b>18 (12 + 6)</b>。<br>"
            "<span style='color:#4ADE80; font-weight:bold;'>净收益与利润：</span>"
            "净收益包含成本和目标利润；产品库中的利润是定价目标，实际成交利润以店铺结算及实际成本为准。"
        )
        lbl_ex.setWordWrap(True)
        lbl_ex.setStyleSheet("color: #E6E2D8; font-size: 11px; line-height: 1.45;")
        ex_layout.addWidget(lbl_ex)
        pmode_box.addWidget(example_card)

        grid.addLayout(pmode_box, row, 1)
        row += 1

        # 10. 品牌 + 侵权警告
        grid.addWidget(self._make_field_label("品牌:"), row, 0)
        brand_box = QVBoxLayout()
        brand_box.setContentsMargins(0, 0, 0, 0)
        brand_box.setSpacing(4)
        self.input_brand = QLineEdit()
        self.input_brand.setPlaceholderText("请输入品牌信息（不输入则默认使用产品自身的品牌信息）")
        brand_box.addWidget(self.input_brand)

        brand_warn = QLabel(
            "<span style='color:#FBBF24; font-weight:bold;'>⚠ 重要提示：</span>"
            "<span style='color:#F87171;'>强烈建议您输入一个品牌进行覆盖，因为采集过来的产品属性中可能包含品牌名字造成侵权。</span>"
        )
        brand_warn.setWordWrap(True)
        brand_warn.setStyleSheet("font-size: 11px; background: transparent;")
        brand_box.addWidget(brand_warn)
        grid.addLayout(brand_box, row, 1)
        row += 1

        # 11. 标题与描述前后缀 (4 multiline text areas matching YuanYou)
        grid.addWidget(self._make_field_label("标题前缀:"), row, 0)
        self.input_title_prefix = self._make_affix_editor("请输入标题前缀...")
        grid.addWidget(self.input_title_prefix, row, 1)
        row += 1

        grid.addWidget(self._make_field_label("标题后缀:"), row, 0)
        self.input_title_suffix = self._make_affix_editor("请输入标题后缀...")
        grid.addWidget(self.input_title_suffix, row, 1)
        row += 1

        grid.addWidget(self._make_field_label("描述前缀:"), row, 0)
        self.input_desc_prefix = self._make_affix_editor("请输入描述前缀...")
        grid.addWidget(self.input_desc_prefix, row, 1)
        row += 1

        grid.addWidget(self._make_field_label("描述后缀:"), row, 0)
        self.input_desc_suffix = self._make_affix_editor("请输入描述后缀...")
        grid.addWidget(self.input_desc_suffix, row, 1)
        row += 1

        outer.addWidget(scroll, stretch=1)

        # Sticky Bottom Action Bar
        footer = QFrame()
        footer.setObjectName("PublishFooter")
        footer.setStyleSheet("""
            QFrame#PublishFooter {
                background: #202820;
                border-top: 1px solid #4E472F;
            }
        """)
        btn_box = QHBoxLayout(footer)
        btn_box.setContentsMargins(20, 10, 20, 10)
        btn_box.setSpacing(12)

        self.cb_auto_freight = QCheckBox("根据中转运费模板自动追加至售价")
        self.cb_auto_freight.setChecked(True)
        btn_box.addWidget(self.cb_auto_freight)
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background: #18201C;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 7px 20px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { background: #252A25; border-color: #6E5D2A; color: #F6F3EA; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_submit = QPushButton("↑ 确认上传")
        btn_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_submit.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 7px 28px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover { background: #356F45; }
        """)
        btn_submit.clicked.connect(self._on_submit)
        btn_box.addWidget(btn_submit)

        outer.addWidget(footer)

    def _build_store_selector(self) -> QFrame:
        card = QFrame()
        card.setObjectName("StoreSelectorCard")
        card.setStyleSheet("""
            QFrame#StoreSelectorCard {
                background: #202820;
                border: 1px solid #4E472F;
                border-radius: 8px;
            }
        """)
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(8)

        # Top row: account stats + search + selected count + filter toggle
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        lbl_quota = QLabel(f"已授权店铺数: <b style='color:#4ADE80;'>{len(self._accounts_data)}</b> 个")
        lbl_quota.setStyleSheet("color: #C8C3B7; font-size: 12px;")
        top_row.addWidget(lbl_quota)

        self.store_search_input = QLineEdit()
        self.store_search_input.setPlaceholderText("🔍 搜索店铺名称或ID...")
        self.store_search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.store_search_input.setFixedHeight(28)
        self.store_search_input.textChanged.connect(self._refresh_store_list)
        top_row.addWidget(self.store_search_input, stretch=1)

        self.lbl_store_selected = QLabel(f"已选 {len(self._checked_store_ids)}/{len(self._accounts_data)} 个")
        self.lbl_store_selected.setStyleSheet("color: #C8C3B7; font-size: 12px;")
        top_row.addWidget(self.lbl_store_selected)

        self.btn_only_selected = QPushButton("只看已选")
        self.btn_only_selected.setCheckable(True)
        self.btn_only_selected.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_only_selected.setStyleSheet("""
            QPushButton {
                background: #18201C;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 5px;
                padding: 3px 10px;
                font-size: 11px;
            }
            QPushButton:checked {
                background: rgba(47, 107, 63, 0.35);
                color: #4ADE80;
                border-color: #2F6B3F;
                font-weight: bold;
            }
        """)
        self.btn_only_selected.toggled.connect(self._refresh_store_list)
        top_row.addWidget(self.btn_only_selected)
        box.addLayout(top_row)

        # Quick Actions Row: Store batch ops + Site batch ops
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)

        def _make_link_btn(title: str) -> QPushButton:
            b = QPushButton(title)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #4ADE80;
                    border: none;
                    padding: 2px 6px;
                    font-size: 11px;
                    font-weight: 500;
                }
                QPushButton:hover { text-decoration: underline; color: #86EFAC; }
            """)
            return b

        quick_row.addWidget(QLabel("店铺:"))
        b_all_s = _make_link_btn("全选")
        b_clr_s = _make_link_btn("清空")
        b_inv_s = _make_link_btn("反选")
        b_all_s.clicked.connect(self._select_all_stores)
        b_clr_s.clicked.connect(self._clear_all_stores)
        b_inv_s.clicked.connect(self._invert_stores)
        quick_row.addWidget(b_all_s)
        quick_row.addWidget(b_clr_s)
        quick_row.addWidget(b_inv_s)

        vsep = QFrame()
        vsep.setFrameShape(QFrame.Shape.VLine)
        vsep.setStyleSheet("color: #4E472F; margin: 2px 6px;")
        quick_row.addWidget(vsep)

        quick_row.addWidget(QLabel("站点:"))
        b_all_site = _make_link_btn("全选")
        b_clr_site = _make_link_btn("清空")
        b_all_site.clicked.connect(lambda: self._set_all_sites(True))
        b_clr_site.clicked.connect(lambda: self._set_all_sites(False))
        quick_row.addWidget(b_all_site)
        quick_row.addWidget(b_clr_site)
        quick_row.addStretch()
        box.addLayout(quick_row)

        # Site Tags Matrix (2 neat rows fitting all 13 site & warehouse tags)
        site_grid = QGridLayout()
        site_grid.setContentsMargins(0, 0, 0, 0)
        site_grid.setHorizontalSpacing(6)
        site_grid.setVerticalSpacing(6)

        pill_style = """
            QPushButton {
                background: #18201C;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 5px;
                padding: 3px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                border-color: #6E5D2A;
                color: #F6F3EA;
            }
            QPushButton:checked {
                background: rgba(47, 107, 63, 0.35);
                color: #4ADE80;
                border: 1px solid #2F6B3F;
                font-weight: 600;
            }
        """
        for idx, (code, label) in enumerate(_SITE_PILLS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(pill_style)
            self._site_buttons[code] = btn
            r_idx = 0 if idx < 7 else 1
            c_idx = idx if idx < 7 else (idx - 7)
            site_grid.addWidget(btn, r_idx, c_idx)

        site_container = QHBoxLayout()
        site_container.setContentsMargins(0, 0, 0, 0)
        site_container.addLayout(site_grid)
        site_container.addStretch()
        box.addLayout(site_container)

        # Real Authorized Stores Checkbox List Container
        self.store_list_frame = QFrame()
        self.store_list_frame.setObjectName("StoreListContainer")
        self.store_list_frame.setStyleSheet("""
            QFrame#StoreListContainer {
                background: #18201C;
                border: 1px solid #3E382A;
                border-radius: 6px;
            }
        """)
        frame_vbox = QVBoxLayout(self.store_list_frame)
        frame_vbox.setContentsMargins(6, 6, 6, 6)
        frame_vbox.setSpacing(2)

        self.store_scroll = QScrollArea()
        self.store_scroll.setWidgetResizable(True)
        self.store_scroll.setMinimumHeight(88)
        self.store_scroll.setMaximumHeight(132)
        self.store_scroll.setStyleSheet("background: transparent; border: none;")

        self.store_inner = QWidget()
        self.store_inner.setObjectName("StoreListInner")
        self.store_inner_layout = QVBoxLayout(self.store_inner)
        self.store_inner_layout.setContentsMargins(4, 2, 4, 2)
        self.store_inner_layout.setSpacing(4)

        for acc in self._accounts_data:
            aid = acc["account_id"]
            cb = QCheckBox(f"{acc['store_name']}   (ID: {aid} | 站点: {acc['site_id']})")
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.setChecked(aid in self._checked_store_ids)
            cb.toggled.connect(lambda checked, a=aid: self._on_store_toggled(a, checked))
            self._store_checkboxes[aid] = cb
            self.store_inner_layout.addWidget(cb)

        self.store_inner_layout.addStretch()
        self.store_scroll.setWidget(self.store_inner)
        frame_vbox.addWidget(self.store_scroll)

        self.lbl_no_store_match = QLabel("暂无匹配店铺（请调整搜索关键词或先绑定店铺）")
        self.lbl_no_store_match.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_no_store_match.setStyleSheet("color: #8C877D; font-size: 12px; padding: 14px 0;")
        frame_vbox.addWidget(self.lbl_no_store_match)

        box.addWidget(self.store_list_frame)

        self._refresh_store_list()
        return card

    @staticmethod
    def _make_affix_editor(placeholder: str) -> QPlainTextEdit:
        ed = QPlainTextEdit()
        ed.setPlaceholderText(placeholder)
        ed.setFixedHeight(52)
        return ed

    def _on_unlimit_variants_toggled(self, checked: bool) -> None:
        self.spin_max_vars.setEnabled(not checked)
        if checked:
            self.spin_max_vars.setValue(0)
        elif self.spin_max_vars.value() == 0:
            self.spin_max_vars.setValue(5)

    def _on_guar_type_changed(self) -> None:
        is_none = self.combo_guar.currentData() == "none"
        self.spin_guar_days.setEnabled(not is_none)

    def _update_product_id_count(self) -> None:
        ids = self._parse_product_ids()
        cnt = len(ids) if ids else self.selected_count
        self.lbl_pid_count.setText(f"总数量: {cnt} 个")

    def _parse_product_ids(self) -> List[str]:
        raw = self.txt_product_ids.toPlainText()
        tokens: List[str] = []
        for line in raw.replace(",", "\n").splitlines():
            item = line.strip()
            if item:
                tokens.append(item)
        return tokens

    def _store_matches_filter(self, acc: Dict[str, str]) -> bool:
        kw = self.store_search_input.text().strip().casefold()
        only_sel = self.btn_only_selected.isChecked()
        aid = acc["account_id"]
        matches_kw = (
            not kw
            or kw in acc["store_name"].casefold()
            or kw in aid.casefold()
            or kw in acc["raw_name"].casefold()
        )
        matches_sel = (not only_sel) or (aid in self._checked_store_ids)
        return matches_kw and matches_sel

    def _refresh_store_list(self) -> None:
        visible_count = 0
        for acc in self._accounts_data:
            aid = acc["account_id"]
            cb = self._store_checkboxes.get(aid)
            if not cb:
                continue
            show = self._store_matches_filter(acc)
            cb.setVisible(show)
            if show:
                visible_count += 1

        self.store_scroll.setVisible(visible_count > 0)
        self.lbl_no_store_match.setVisible(visible_count == 0)
        self.lbl_store_selected.setText(f"已选 {len(self._checked_store_ids)}/{len(self._accounts_data)} 个")

    def _on_store_toggled(self, account_id: str, checked: bool) -> None:
        if checked:
            self._checked_store_ids.add(account_id)
        else:
            self._checked_store_ids.discard(account_id)
        if self.btn_only_selected.isChecked() and not checked:
            cb = self._store_checkboxes.get(account_id)
            if cb:
                cb.setVisible(False)
        self.lbl_store_selected.setText(f"已选 {len(self._checked_store_ids)}/{len(self._accounts_data)} 个")

    def _select_all_stores(self) -> None:
        for acc in self._accounts_data:
            aid = acc["account_id"]
            cb = self._store_checkboxes.get(aid)
            if cb and self._store_matches_filter(acc):
                cb.blockSignals(True)
                cb.setChecked(True)
                cb.blockSignals(False)
                self._checked_store_ids.add(aid)
        self._refresh_store_list()

    def _clear_all_stores(self) -> None:
        for cb in self._store_checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._checked_store_ids.clear()
        self._refresh_store_list()

    def _invert_stores(self) -> None:
        for acc in self._accounts_data:
            aid = acc["account_id"]
            cb = self._store_checkboxes.get(aid)
            if not cb or not self._store_matches_filter(acc):
                continue
            new_state = not cb.isChecked()
            cb.blockSignals(True)
            cb.setChecked(new_state)
            cb.blockSignals(False)
            if new_state:
                self._checked_store_ids.add(aid)
            else:
                self._checked_store_ids.discard(aid)
        self._refresh_store_list()

    def _set_all_sites(self, checked: bool) -> None:
        for btn in self._site_buttons.values():
            btn.setChecked(checked)

    def _on_submit(self) -> None:
        if self._accounts_data and not self._checked_store_ids:
            QMessageBox.warning(self, "提示", "请先勾选至少 1 个目标店铺！")
            return
        selected_sites = [code for code, btn in self._site_buttons.items() if btn.isChecked()]
        pids = self._parse_product_ids()
        cnt = len(pids) if pids else self.selected_count
        site_txt = ", ".join(selected_sites) if selected_sites else "按店铺默认站点"
        store_txt = f"{len(self._checked_store_ids)} 个店铺" if self._checked_store_ids else "全部已授权店铺"

        ret = QMessageBox.question(
            self,
            "确认批量上传",
            f"准备将选中的 {cnt} 个商品正式上传至美客多店铺！\n\n"
            f"目标店铺: {store_txt}\n"
            f"目标站点: {site_txt}\n"
            f"销售类型: {'高流量高佣金 (Premium)' if self.radio_sales_prem.isChecked() else '低流量低佣金 (Classic)'}\n"
            f"价格模式: {'净收益模式' if self.radio_pmode_net.isChecked() else '售价模式'}\n\n"
            "确认立即开始上传任务吗？",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if ret == QMessageBox.StandardButton.Ok:
            self.accept()

    def get_publish_config(self) -> Dict[str, Any]:
        selected_stores = sorted(self._checked_store_ids)
        selected_sites = [code for code, btn in self._site_buttons.items() if btn.isChecked()]
        max_vars = None if self.cb_unlimit_variants.isChecked() else (
            self.spin_max_vars.value() if self.spin_max_vars.value() > 0 else None
        )
        return {
            "store_id": ",".join(selected_stores) if selected_stores else "ALL",
            "store_ids": selected_stores,
            "store_names": [
                acc["store_name"] for acc in self._accounts_data if acc["account_id"] in self._checked_store_ids
            ],
            "target_sites": selected_sites,
            "product_ids": self._parse_product_ids(),
            "sales_type": "Premium" if self.radio_sales_prem.isChecked() else "Classic",
            "price_mode": "price" if self.radio_pmode_price.isChecked() else "net_proceed",
            "force_stock": self.spin_force_stock.value() if self.cb_force_stock.isChecked() else None,
            "max_variants": max_vars,
            "guarantee_type": self.combo_guar.currentData(),
            "guarantee_days": self.spin_guar_days.value(),
            "brand": self.input_brand.text().strip(),
            "title_prefix": self.input_title_prefix.toPlainText().strip(),
            "title_suffix": self.input_title_suffix.toPlainText().strip(),
            "desc_prefix": self.input_desc_prefix.toPlainText().strip(),
            "desc_suffix": self.input_desc_suffix.toPlainText().strip(),
            "auto_freight": self.cb_auto_freight.isChecked(),
        }
