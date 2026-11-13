from __future__ import annotations

import csv
import json
import os
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from items.vault_batch_settings import (
    BaseVaultDialog,
    DARK_TIP_LABEL_QSS,
    build_dark_dialog_qss,
    style_cancel_button,
    style_primary_button,
)
from items.vault_pricing import net_proceeds, profit_for_net
from items.vault_variant_widgets import VariantSectionWidget
from ui_common.combos import populate_store_combo
from ui_common.image_cache import get_placeholder_pixmap, load_image_async


MAX_PRODUCT_IMAGES = 80
ALLOWED_LOCAL_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

COUNTRY_STATION_MAP: List[Tuple[str, str]] = [
    ("MLM", "🇲🇽 墨西哥 (MLM)"),
    ("MLB", "🇧🇷 巴西 (MLB)"),
    ("MLC", "🇨🇱 智利 (MLC)"),
    ("MCO", "🇨🇴 哥伦比亚 (MCO)"),
    ("MLA", "🇦🇷 阿根廷 (MLA)"),
    ("MLU", "🇺🇾 乌拉圭 (MLU)"),
    ("MPE", "🇵🇪 秘鲁 (MPE)"),
    ("CBT", "🌐 全球 (CBT)"),
]

STATUS_OPTIONS: List[Tuple[str, str]] = [
    ("⚪ 草稿", "draft"),
    ("🟦 待发布/就绪", "ready"),
    ("🟢 在售/活跃", "active"),
    ("🟡 暂停", "paused"),
    ("🔴 侵权/下架", "infringed"),
    ("📦 已本地发布", "published"),
]

_KEYWORD_CATEGORY_HINTS: List[Tuple[Tuple[str, ...], str, str]] = [
    (("fundas", "cojin", "almohada", "pillow", "cushion", "抱枕", "枕套"), "CBT1637", "Home > Bedding > Cushions & Covers"),
    (("zapat", "shoe", "sneaker", "bota", "鞋", "运动鞋"), "CBT109026", "Clothing & Accessories > Footwear > Sneakers"),
    (("audifon", "auricular", "earphone", "headphone", "蓝牙耳机", "耳机"), "CBT3697", "Consumer Electronics > Audio > Headphones"),
    (("reloj", "watch", "smartwatch", "手表", "智能表"), "CBT399230", "Jewelry & Watches > Wristwatches"),
    (("mochila", "bolso", "backpack", "bag", "背包", "手提包"), "CBT157249", "Luggage & Bags > Backpacks"),
    (("lampara", "luz", "led", "lamp", "台灯", "灯具"), "CBT1582", "Home > Lighting > Lamps"),
]


def _parse_json_maybe(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, type(fallback)):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, type(fallback)):
                return parsed
        except Exception:
            pass
    return fallback


class ProductEditorDialog(QDialog):
    """Unified 1:1 YuanYou single-page product editor powering CREATE and EDIT modes."""

    _LANG_TABS: List[Tuple[str, str]] = [
        ("es", "西班牙语"),
        ("pt", "葡萄牙语"),
        ("zh", "中文"),
        ("en", "英文"),
    ]
    _SITE_PILLS: List[Tuple[str, str]] = [
        ("MLM", "墨西哥"),
        ("MLB", "巴西"),
        ("MLC", "智利"),
        ("MCO", "哥伦比亚"),
        ("MLA", "阿根廷"),
        ("MLU", "乌拉圭"),
        ("MPE", "秘鲁"),
        ("CBT", "全球"),
    ]

    def __init__(
        self,
        mode: str = "CREATE",
        product: Optional[Dict[str, Any]] = None,
        auth_mgr: Optional[Any] = None,
        groups: Optional[List[str]] = None,
        on_audit_log: Optional[Callable[[str, str, str], None]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.mode = "EDIT" if str(mode).upper() == "EDIT" else "CREATE"
        self.product: Dict[str, Any] = dict(product or {})
        self.item_id = str(self.product.get("id") or "")
        self._on_audit_log = on_audit_log

        self.auth_mgr = auth_mgr
        if self.auth_mgr is None:
            try:
                from engine.auth import AuthManager
                self.auth_mgr = AuthManager()
            except Exception:
                self.auth_mgr = None

        self._initial_groups = list(groups or ["未分组", "手搓组"])
        self.setWindowTitle("创建产品" if self.mode == "CREATE" else f"编辑商品 - {self.item_id}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.resize(1120, 820)
        self.setMinimumSize(900, 620)
        self.setStyleSheet(build_dark_dialog_qss())

        self.created_item: Optional[Dict[str, Any]] = None
        self._saved_fields: Optional[Dict[str, Any]] = None

        self._nav_sections: List[Tuple[str, str]] = [
            ("basic", "基本信息"),
            ("category", "分类与属性"),
            ("variants", "变体"),
            ("images", "图片管理"),
        ]
        if self.mode == "EDIT":
            self._nav_sections.append(("logs", "操作日志"))

        self._active_nav: str = "basic"
        self._active_lang: str = "es"
        self._lang_titles: Dict[str, str] = {k: "" for k, _ in self._LANG_TABS}
        self._lang_descs: Dict[str, str] = {k: "" for k, _ in self._LANG_TABS}
        self._active_cat_site: str = "MLM"
        self._site_categories: Dict[str, Tuple[str, str]] = {}
        self._custom_attr_rows: List[Tuple[QLineEdit, QLineEdit, QWidget]] = []
        self._existing_logs: List[Any] = []
        self._syncing_lang: bool = False
        self._programmatic_scroll: bool = False

        self._nav_buttons: Dict[str, QPushButton] = {}
        self._lang_buttons: Dict[str, QPushButton] = {}
        self._site_pill_buttons: Dict[str, QPushButton] = {}
        self._section_widgets: Dict[str, QWidget] = {}

        self._init_ui()
        if self.mode == "EDIT":
            self._populate_from_product(self.product)
        else:
            self._init_defaults_for_create()

    def _card_frame(self) -> QFrame:
        card = QFrame()
        card.setObjectName("VaultEditorCard")
        card.setStyleSheet(
            "QFrame#VaultEditorCard { background: #202820; border: 1px solid #4E472F; border-radius: 8px; }"
        )
        return card

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Sticky Top Anchor Nav Bar (YuanYou thin-underline style)
        top_bar = QFrame()
        top_bar.setObjectName("VaultEditorTopNav")
        top_bar.setStyleSheet(
            "QFrame#VaultEditorTopNav { background: #1C231F; border-bottom: 1px solid #4E472F; }"
        )
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 6, 16, 0)
        top_layout.setSpacing(14)

        for key, label in self._nav_sections:
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._jump_to_section(k))
            self._nav_buttons[key] = btn
            top_layout.addWidget(btn)

        top_layout.addStretch()

        btn_predict_top = QPushButton("✨ 智能匹配分类")
        btn_predict_top.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_predict_top.setStyleSheet(
            "QPushButton { background: #18201C; color: #E5C158; border: 1px solid #4E472F; "
            "border-radius: 6px; padding: 5px 12px; font-size: 12px; font-weight: 600; margin-bottom: 6px; }"
            "QPushButton:hover { border-color: #6E5D2A; background: #252A25; }"
        )
        btn_predict_top.clicked.connect(self._predict_category)
        top_layout.addWidget(btn_predict_top)

        self.btn_fullscreen = QPushButton("⛶ 全屏")
        self.btn_fullscreen.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fullscreen.setStyleSheet(
            "QPushButton { background: #18201C; color: #C8C3B7; border: 1px solid #4E472F; "
            "border-radius: 6px; padding: 5px 10px; font-size: 12px; margin-bottom: 6px; }"
            "QPushButton:hover { border-color: #6E5D2A; color: #F6F3EA; }"
        )
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        top_layout.addWidget(self.btn_fullscreen)
        root.addWidget(top_bar)
        self._set_active_nav("basic")

        # 2. Scrollable Continuous Form Body
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { background: #171B19; border: none; }")
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("VaultEditorScrollContent")
        self.scroll_content.setStyleSheet("QWidget#VaultEditorScrollContent { background: #171B19; }")
        self.body_layout = QVBoxLayout(self.scroll_content)
        self.body_layout.setContentsMargins(20, 16, 20, 20)
        self.body_layout.setSpacing(16)

        self._build_basic_section()
        self._build_category_section()
        self._build_variant_section()
        self._build_images_section()
        if self.mode == "EDIT":
            self._build_logs_section()

        # Bottom viewport padding so clicking bottom nav links aligns section to viewport top
        bottom_spacer = QWidget()
        bottom_spacer.setMinimumHeight(420)
        bottom_spacer.setStyleSheet("background: transparent;")
        self.body_layout.addWidget(bottom_spacer)

        self.scroll_area.setWidget(self.scroll_content)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        root.addWidget(self.scroll_area, stretch=1)

        # 3. Sticky Bottom Bar
        bottom_bar = QFrame()
        bottom_bar.setObjectName("VaultEditorFooter")
        bottom_bar.setStyleSheet(
            "QFrame#VaultEditorFooter { background: #1C231F; border-top: 1px solid #4E472F; }"
        )
        b_layout = QHBoxLayout(bottom_bar)
        b_layout.setContentsMargins(20, 10, 20, 10)

        hint_text = (
            "💡 仅维护净收益；新建商品将直接写入本地产品库"
            if self.mode == "CREATE"
            else "💡 修改后请保存，所有变体/图片/属性变更将写入本地产品库并记入操作日志"
        )
        lbl_hint = QLabel(hint_text)
        lbl_hint.setStyleSheet(DARK_TIP_LABEL_QSS + " padding: 4px 10px;")
        b_layout.addWidget(lbl_hint)
        b_layout.addStretch()

        btn_cancel = QPushButton("取 消")
        style_cancel_button(btn_cancel)
        btn_cancel.clicked.connect(self.reject)
        b_layout.addWidget(btn_cancel)

        self.btn_submit = QPushButton("创建产品" if self.mode == "CREATE" else "保存修改")
        style_primary_button(self.btn_submit)
        self.btn_submit.clicked.connect(self._on_submit)
        b_layout.addWidget(self.btn_submit)
        root.addWidget(bottom_bar)

    def _build_basic_section(self) -> None:
        sec_basic = self._card_frame()
        self._section_widgets["basic"] = sec_basic
        bl = QVBoxLayout(sec_basic)
        bl.setContentsMargins(16, 14, 16, 16)
        bl.setSpacing(12)

        hdr = QHBoxLayout()
        lbl_b = QLabel("<b>基本信息</b>  <span style='color:#9A9589'>多语言标题、描述、净收益与包裹基础规格</span>")
        lbl_b.setStyleSheet("font-size: 14px; color: #F6F3EA;")
        hdr.addWidget(lbl_b)
        hdr.addStretch()
        for lang_code, lang_name in self._LANG_TABS:
            l_btn = QPushButton(lang_name)
            l_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            l_btn.clicked.connect(lambda _, lc=lang_code: self._switch_lang(lc))
            self._lang_buttons[lang_code] = l_btn
            hdr.addWidget(l_btn)
        bl.addLayout(hdr)

        # Title + char counter
        title_hdr = QHBoxLayout()
        title_hdr.addWidget(QLabel("<span style='color:#F87171'>*</span> 产品标题:"))
        title_hdr.addStretch()
        self.lbl_title_count = QLabel("0 / 60")
        self.lbl_title_count.setStyleSheet("color: #9A9589; font-size: 11px;")
        title_hdr.addWidget(self.lbl_title_count)
        bl.addLayout(title_hdr)

        self.txt_title = QPlainTextEdit()
        self.txt_title.setPlaceholderText("请输入当前语言标题（美客多建议 <= 60 字符，避免堆砌关键词）...")
        self.txt_title.setFixedHeight(62)
        self.txt_title.textChanged.connect(self._on_title_or_desc_edited)
        bl.addWidget(self.txt_title)

        bl.addWidget(QLabel("产品描述:"))
        self.txt_desc = QPlainTextEdit()
        self.txt_desc.setPlaceholderText("请输入产品纯文本卖点说明、清单与使用指南（支持按语种独立编辑）...")
        self.txt_desc.setFixedHeight(96)
        self.txt_desc.textChanged.connect(self._on_title_or_desc_edited)
        bl.addWidget(self.txt_desc)

        meta_grid = QGridLayout()
        meta_grid.setSpacing(10)

        meta_grid.addWidget(QLabel("归属店铺:"), 0, 0)
        self.combo_store = QComboBox()
        populate_store_combo(self.combo_store, auth_mgr=self.auth_mgr, all_label="")
        if self.combo_store.count() == 0:
            self.combo_store.addItem("暂无已授权店铺（纯本地商品）", "")
        meta_grid.addWidget(self.combo_store, 0, 1)

        meta_grid.addWidget(QLabel("默认站点:"), 0, 2)
        self.combo_site = QComboBox()
        for sc, sn in COUNTRY_STATION_MAP:
            self.combo_site.addItem(sn, sc)
        meta_grid.addWidget(self.combo_site, 0, 3)

        meta_grid.addWidget(QLabel("净收益 (USD):"), 1, 0)
        self.spn_net_proceed = QDoubleSpinBox()
        self.spn_net_proceed.setRange(-9999.0, 99999.0)
        self.spn_net_proceed.setDecimals(2)
        self.spn_net_proceed.setPrefix("$ ")
        self.spn_net_proceed.setValue(18.00)
        self.spn_net_proceed.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        meta_grid.addWidget(self.spn_net_proceed, 1, 1)

        meta_grid.addWidget(QLabel("主 SKU 编码:"), 1, 2)
        self.edt_sku = QLineEdit()
        self.edt_sku.setPlaceholderText("如 PV-SKU-001（变体可在此基础上批量前缀）")
        meta_grid.addWidget(self.edt_sku, 1, 3)

        meta_grid.addWidget(QLabel("默认重量 (g):"), 2, 0)
        w_wrap = QHBoxLayout()
        w_wrap.setSpacing(6)
        self.spn_weight = QDoubleSpinBox()
        self.spn_weight.setRange(0.0, 999999.0)
        self.spn_weight.setDecimals(0)
        self.spn_weight.setSuffix(" g")
        self.spn_weight.setValue(500.0)
        self.spn_weight.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_weight.valueChanged.connect(self._update_volumetric_badge)
        w_wrap.addWidget(self.spn_weight, stretch=1)
        self.lbl_vol_status = QLabel("实重")
        self.lbl_vol_status.setStyleSheet(
            "background: rgba(16, 185, 129, 0.15); color: #4ADE80; "
            "border: 1px solid #2F6B3F; border-radius: 4px; padding: 2px 8px; font-weight: bold;"
        )
        w_wrap.addWidget(self.lbl_vol_status)
        meta_grid.addLayout(w_wrap, 2, 1)

        meta_grid.addWidget(QLabel("默认尺寸 (cm):"), 2, 2)
        dim_box = QHBoxLayout()
        dim_box.setSpacing(6)
        self.spn_l = QDoubleSpinBox()
        self.spn_w = QDoubleSpinBox()
        self.spn_h = QDoubleSpinBox()
        # Aliases so external callers using spn_length / spn_width / spn_height work seamlessly
        self.spn_length = self.spn_l
        self.spn_width = self.spn_w
        self.spn_height = self.spn_h
        for d_spn, d_lbl, d_def in ((self.spn_l, "长", 20.0), (self.spn_w, "宽", 15.0), (self.spn_h, "高", 10.0)):
            d_spn.setRange(0.0, 999.0)
            d_spn.setDecimals(1)
            d_spn.setPrefix(f"{d_lbl}:")
            d_spn.setValue(d_def)
            d_spn.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            d_spn.valueChanged.connect(self._update_volumetric_badge)
            dim_box.addWidget(d_spn)
        meta_grid.addLayout(dim_box, 2, 3)

        meta_grid.addWidget(QLabel("默认库存:"), 3, 0)
        self.spn_stock = QSpinBox()
        self.spn_stock.setRange(0, 999999)
        self.spn_stock.setValue(50)
        self.spn_stock.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        meta_grid.addWidget(self.spn_stock, 3, 1)

        meta_grid.addWidget(QLabel("产品分组:"), 3, 2)
        self.combo_group = QComboBox()
        self.combo_group.setEditable(True)
        for g in self._initial_groups:
            self.combo_group.addItem(g)
        meta_grid.addWidget(self.combo_group, 3, 3)

        meta_grid.addWidget(QLabel("刊登状态:"), 4, 0)
        self.combo_status = QComboBox()
        for s_lbl, s_val in STATUS_OPTIONS:
            self.combo_status.addItem(s_lbl, s_val)
        meta_grid.addWidget(self.combo_status, 4, 1)

        meta_grid.addWidget(QLabel("负责销售员:"), 4, 2)
        self.edt_salesman = QLineEdit()
        self.edt_salesman.setPlaceholderText("选填")
        meta_grid.addWidget(self.edt_salesman, 4, 3)

        bl.addLayout(meta_grid)
        self.body_layout.addWidget(sec_basic)
        self._switch_lang("es", save_current=False)
        self._update_volumetric_badge()

    def _build_category_section(self) -> None:
        sec_cat = self._card_frame()
        self._section_widgets["category"] = sec_cat
        cl = QVBoxLayout(sec_cat)
        cl.setContentsMargins(16, 14, 16, 16)
        cl.setSpacing(10)

        lbl_c = QLabel("<b>分类与属性</b>  <span style='color:#9A9589'>分站点独立类目配置与 CBT 核心属性（仅保存真实输入）</span>")
        lbl_c.setStyleSheet("font-size: 14px; color: #F6F3EA;")
        cl.addWidget(lbl_c)

        site_pills_row = QHBoxLayout()
        site_pills_row.setSpacing(6)
        for sc, sn in self._SITE_PILLS:
            pbtn = QPushButton(f"{sn} ({sc})")
            pbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            pbtn.clicked.connect(lambda _, code=sc: self._switch_cat_site(code))
            self._site_pill_buttons[sc] = pbtn
            site_pills_row.addWidget(pbtn)
        site_pills_row.addStretch()
        cl.addLayout(site_pills_row)

        cat_form = QGridLayout()
        cat_form.setSpacing(10)
        cat_form.addWidget(QLabel("当前站点分类ID:"), 0, 0)
        self.edt_cat_id = QLineEdit()
        self.edt_cat_id.setPlaceholderText("如 CBT1637 / MLM12345")
        self.edt_cat_id.textChanged.connect(self._sync_current_site_cat)
        cat_form.addWidget(self.edt_cat_id, 0, 1)

        cat_form.addWidget(QLabel("分类路径名称:"), 0, 2)
        self.edt_cat_name = QLineEdit()
        self.edt_cat_name.setPlaceholderText("如 Home > Lighting > Lamps")
        self.edt_cat_name.textChanged.connect(self._sync_current_site_cat)
        cat_form.addWidget(self.edt_cat_name, 0, 3)

        cat_form.addWidget(QLabel("品牌 (BRAND):"), 1, 0)
        self.edt_attr_brand = QLineEdit()
        self.edt_attr_brand.setPlaceholderText("无品牌可填写 Generic / Genérico")
        cat_form.addWidget(self.edt_attr_brand, 1, 1)

        cat_form.addWidget(QLabel("型号 (MODEL):"), 1, 2)
        self.edt_attr_model = QLineEdit()
        self.edt_attr_model.setPlaceholderText("输入产品型号")
        cat_form.addWidget(self.edt_attr_model, 1, 3)

        cat_form.addWidget(QLabel("材质 (MATERIAL):"), 2, 0)
        self.edt_attr_material = QLineEdit()
        self.edt_attr_material.setPlaceholderText("选填，如 ABS / Cotton")
        cat_form.addWidget(self.edt_attr_material, 2, 1)

        cat_form.addWidget(QLabel("自定义类目属性:"), 2, 2)
        btn_add_attr = QPushButton("＋ 新增属性键值对")
        btn_add_attr.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_attr.setStyleSheet(
            "QPushButton { background: #18201C; color: #E5C158; border: 1px solid #4E472F; "
            "border-radius: 5px; padding: 4px 10px; font-size: 12px; }"
            "QPushButton:hover { border-color: #6E5D2A; background: #252A25; }"
        )
        btn_add_attr.clicked.connect(lambda: self._add_custom_attr_row("", ""))
        cat_form.addWidget(btn_add_attr, 2, 3, alignment=Qt.AlignmentFlag.AlignLeft)
        cl.addLayout(cat_form)

        self.custom_attrs_layout = QVBoxLayout()
        self.custom_attrs_layout.setSpacing(6)
        cl.addLayout(self.custom_attrs_layout)

        self.body_layout.addWidget(sec_cat)
        self._switch_cat_site("MLM")

    def _build_variant_section(self) -> None:
        sec_var = self._card_frame()
        self._section_widgets["variants"] = sec_var
        vl = QVBoxLayout(sec_var)
        vl.setContentsMargins(16, 14, 16, 16)
        vl.setSpacing(10)

        lbl_v = QLabel("<b>变体与 SKU 列表</b>  <span style='color:#9A9589'>自由增减多维规格，支持表头批量改值/公式/前缀、独立长宽高及图片绑定</span>")
        lbl_v.setStyleSheet("font-size: 14px; color: #F6F3EA;")
        vl.addWidget(lbl_v)

        self.variant_section = VariantSectionWidget(
            self._get_variant_defaults,
            self._get_current_images,
            self,
        )
        vl.addWidget(self.variant_section)
        self.body_layout.addWidget(sec_var)

    def _build_images_section(self) -> None:
        sec_img = self._card_frame()
        self._section_widgets["images"] = sec_img
        il = QVBoxLayout(sec_img)
        il.setContentsMargins(16, 14, 16, 16)
        il.setSpacing(10)

        lbl_i = QLabel("<b>图片管理</b>  <span style='color:#9A9589'>最多支持 80 张，可拖拽调序、设为主图，并同步给变体图片选择器</span>")
        lbl_i.setStyleSheet("font-size: 14px; color: #F6F3EA;")
        il.addWidget(lbl_i)

        img_bar = QHBoxLayout()
        img_bar.setSpacing(8)
        btn_add_url = QPushButton("＋ 添加图片链接")
        style_primary_button(btn_add_url)
        btn_add_url.clicked.connect(self._add_image_url)
        img_bar.addWidget(btn_add_url)

        btn_add_local = QPushButton("📁 选择本地图片")
        style_cancel_button(btn_add_local)
        btn_add_local.clicked.connect(self._add_local_images)
        img_bar.addWidget(btn_add_local)

        btn_set_main = QPushButton("⭐ 设为主图")
        style_cancel_button(btn_set_main)
        btn_set_main.clicked.connect(self._set_main_image)
        img_bar.addWidget(btn_set_main)

        btn_mv_left = QPushButton("◀ 前移")
        style_cancel_button(btn_mv_left)
        btn_mv_left.clicked.connect(lambda: self._move_selected_image(-1))
        img_bar.addWidget(btn_mv_left)

        btn_mv_right = QPushButton("后移 ▶")
        style_cancel_button(btn_mv_right)
        btn_mv_right.clicked.connect(lambda: self._move_selected_image(1))
        img_bar.addWidget(btn_mv_right)

        btn_del_img = QPushButton("🗑 删除选中")
        style_cancel_button(btn_del_img)
        btn_del_img.clicked.connect(self._delete_selected_images)
        img_bar.addWidget(btn_del_img)

        img_bar.addStretch()
        self.lbl_img_count = QLabel("共 0 / 80 张")
        self.lbl_img_count.setStyleSheet("color: #9A9589; font-size: 12px;")
        img_bar.addWidget(self.lbl_img_count)
        il.addLayout(img_bar)

        self.img_list = QListWidget()
        self.img_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.img_list.setIconSize(QSize(88, 88))
        self.img_list.setGridSize(QSize(112, 114))
        self.img_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.img_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.img_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.img_list.setMinimumHeight(210)
        self.img_list.setStyleSheet(
            "QListWidget { background: #18201C; border: 1px solid #4E472F; border-radius: 6px; padding: 8px; }"
            "QListWidget::item { color: #C8C3B7; border: 1px solid transparent; border-radius: 6px; padding: 4px; }"
            "QListWidget::item:selected { background: #203A2B; border: 1px solid #2F6B3F; color: #F6F3EA; }"
        )
        self.img_list.model().rowsMoved.connect(lambda *_: self._refresh_image_badges())
        il.addWidget(self.img_list)
        self.body_layout.addWidget(sec_img)

    def _build_logs_section(self) -> None:
        sec_logs = self._card_frame()
        self._section_widgets["logs"] = sec_logs
        ll = QVBoxLayout(sec_logs)
        ll.setContentsMargins(16, 14, 16, 16)
        ll.setSpacing(10)

        lbl_l = QLabel("<b>操作日志</b>  <span style='color:#9A9589'>仅展示真实本地入库、编辑与批处理历史记录</span>")
        lbl_l.setStyleSheet("font-size: 14px; color: #F6F3EA;")
        ll.addWidget(lbl_l)

        self.log_table = QTableWidget()
        log_headers = ["操作时间", "操作人", "操作动作", "详细记录"]
        self.log_table.setColumnCount(len(log_headers))
        self.log_table.setHorizontalHeaderLabels(log_headers)
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.log_table.setMinimumHeight(180)
        self.log_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ll.addWidget(self.log_table)
        self.body_layout.addWidget(sec_logs)

    # ---------------- Initialization & Hydration ----------------

    def _init_defaults_for_create(self) -> None:
        idx_ready = self.combo_status.findData("draft")
        if idx_ready >= 0:
            self.combo_status.setCurrentIndex(idx_ready)
        self._refresh_image_badges()

    def _populate_from_product(self, prod: Dict[str, Any]) -> None:
        attrs = _parse_json_maybe(prod.get("attributes"), {})
        saved_lang_titles = attrs.get("_lang_titles") if isinstance(attrs.get("_lang_titles"), dict) else {}
        saved_lang_descs = attrs.get("_lang_descs") if isinstance(attrs.get("_lang_descs"), dict) else {}

        base_title = str(prod.get("title") or "")
        base_desc = str(prod.get("description") or "")
        self._lang_titles = {
            "es": str(saved_lang_titles.get("es") or prod.get("title_es") or base_title),
            "pt": str(saved_lang_titles.get("pt") or prod.get("title_pt") or ""),
            "zh": str(saved_lang_titles.get("zh") or prod.get("title_zh") or ""),
            "en": str(saved_lang_titles.get("en") or ""),
        }
        self._lang_descs = {
            "es": str(saved_lang_descs.get("es") or base_desc),
            "pt": str(saved_lang_descs.get("pt") or ""),
            "zh": str(saved_lang_descs.get("zh") or ""),
            "en": str(saved_lang_descs.get("en") or ""),
        }
        self._switch_lang("es", save_current=False)

        # Store selection (restore account_id or store_name cleanly even if unmatched)
        acc_id = str(prod.get("account_id") or "").strip()
        store_nm = str(prod.get("store_name") or "").strip()
        idx_store = self.combo_store.findData(acc_id) if acc_id else -1
        if idx_store < 0 and store_nm:
            for i in range(self.combo_store.count()):
                if store_nm in self.combo_store.itemText(i):
                    idx_store = i
                    break
        if idx_store < 0 and (acc_id or store_nm):
            label = store_nm or f"店铺 ({acc_id})"
            self.combo_store.addItem(label, acc_id)
            idx_store = self.combo_store.count() - 1
        if idx_store >= 0:
            self.combo_store.setCurrentIndex(idx_store)

        # Site & Net Proceed & Weight/Dimensions
        site_code = str(prod.get("site_id") or "MLM").strip().upper() or "MLM"
        idx_site = self.combo_site.findData(site_code)
        if idx_site >= 0:
            self.combo_site.setCurrentIndex(idx_site)

        self.spn_net_proceed.setValue(net_proceeds(prod))

        self.edt_sku.setText(str(prod.get("sku") or ""))
        self.spn_weight.setValue(float(prod.get("weight") or 0.0))
        self.spn_l.setValue(float(prod.get("length") or 0.0))
        self.spn_w.setValue(float(prod.get("width") or 0.0))
        self.spn_h.setValue(float(prod.get("height") or 0.0))
        self.spn_stock.setValue(int(prod.get("stock") or 0))
        self._update_volumetric_badge()

        grp_name = str(prod.get("group_name") or "未分组").strip() or "未分组"
        if self.combo_group.findText(grp_name) < 0:
            self.combo_group.addItem(grp_name)
        self.combo_group.setCurrentText(grp_name)

        st_val = str(prod.get("status") or "draft").strip()
        idx_st = self.combo_status.findData(st_val)
        if idx_st >= 0:
            self.combo_status.setCurrentIndex(idx_st)
        else:
            self.combo_status.addItem(st_val, st_val)
            self.combo_status.setCurrentIndex(self.combo_status.count() - 1)

        self.edt_salesman.setText(str(prod.get("salesman") or ""))

        # Site categories & attributes (zero fake fallback data!)
        saved_site_cats = attrs.get("_site_categories") if isinstance(attrs.get("_site_categories"), dict) else {}
        self._site_categories = {}
        for k, pair in saved_site_cats.items():
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                self._site_categories[str(k)] = (str(pair[0]), str(pair[1]))
        cat_id = str(prod.get("category_id") or "").strip()
        cat_name = str(prod.get("category_name") or "").strip()
        if (cat_id or cat_name) and site_code not in self._site_categories:
            self._site_categories[site_code] = (cat_id, cat_name)
        self._switch_cat_site(site_code if site_code in self._site_pill_buttons else "MLM")

        self.edt_attr_brand.setText(str(attrs.get("BRAND") or attrs.get("brand") or ""))
        self.edt_attr_model.setText(str(attrs.get("MODEL") or attrs.get("model") or ""))
        self.edt_attr_material.setText(str(attrs.get("MATERIAL") or attrs.get("material") or ""))

        reserved_keys = {
            "BRAND", "brand", "MODEL", "model", "MATERIAL", "material",
            "_lang_titles", "_lang_descs", "_site_categories", "_variant_headers", "_logs",
        }
        for k, v in attrs.items():
            if k in reserved_keys or str(k).startswith("_"):
                continue
            self._add_custom_attr_row(str(k), str(v or ""))

        # Images first so VariantSectionWidget sees valid gallery URLs
        imgs = _parse_json_maybe(prod.get("images"), [])
        for u in imgs:
            if len(self._get_current_images()) >= MAX_PRODUCT_IMAGES:
                break
            if str(u).strip():
                self._append_image_item(str(u).strip())
        self._refresh_image_badges()

        # Variants
        variants = _parse_json_maybe(prod.get("variants"), [])
        headers_meta = attrs.get("_variant_headers") if isinstance(attrs.get("_variant_headers"), list) else None
        self.variant_section.load_variants(variants, headers_meta=headers_meta)

        # Real Audit Logs (no fabricated dates or COL strings)
        self._existing_logs = []
        raw_logs = _parse_json_maybe(prod.get("logs"), [])
        if raw_logs:
            self._existing_logs = list(raw_logs)
        elif isinstance(attrs.get("_logs"), list):
            self._existing_logs = list(attrs.get("_logs") or [])
        self._render_logs_table()

    def _render_logs_table(self) -> None:
        if not hasattr(self, "log_table"):
            return
        rows_to_show: List[Tuple[str, str, str, str]] = []
        for entry in self._existing_logs:
            if isinstance(entry, dict):
                rows_to_show.append((
                    str(entry.get("time") or "-"),
                    str(entry.get("operator") or "系统"),
                    str(entry.get("action") or "更新"),
                    str(entry.get("detail") or ""),
                ))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 4:
                rows_to_show.append((str(entry[0]), str(entry[1]), str(entry[2]), str(entry[3])))
            else:
                rows_to_show.append(("-", "系统", "记录", str(entry)))

        if not rows_to_show:
            created_at = str(self.product.get("created_at") or "-")[:19]
            updated_at = str(self.product.get("updated_at") or "")[:19]
            salesman = str(self.product.get("salesman") or "系统")
            rows_to_show.append((created_at, salesman, "商品入库", f"商品 {self.item_id} 本地入库创建"))
            if updated_at and updated_at != created_at:
                rows_to_show.append((updated_at, salesman, "本地更新", "最近一次保存更新"))

        self.log_table.setRowCount(len(rows_to_show))
        for r, row_vals in enumerate(rows_to_show):
            for c, val in enumerate(row_vals):
                self.log_table.setItem(r, c, QTableWidgetItem(val))

    # ---------------- Navigation & Scrollspy ----------------

    def _set_active_nav(self, active_key: str) -> None:
        self._active_nav = active_key
        for k, btn in self._nav_buttons.items():
            if k == active_key:
                btn.setStyleSheet(
                    "QPushButton { background: transparent; color: #4ADE80; border: none; "
                    "border-bottom: 2px solid #2F6B3F; border-radius: 0px; padding: 8px 6px 10px 6px; "
                    "font-size: 13px; font-weight: bold; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: transparent; color: #C8C3B7; border: none; "
                    "border-bottom: 2px solid transparent; border-radius: 0px; padding: 8px 6px 10px 6px; "
                    "font-size: 13px; }"
                    "QPushButton:hover { color: #F6F3EA; }"
                )

    def _jump_to_section(self, key: str) -> None:
        self._set_active_nav(key)
        sec = self._section_widgets.get(key)
        if not sec:
            return
        self._programmatic_scroll = True
        try:
            pos_y = sec.mapTo(self.scroll_content, QPoint(0, 0)).y() - 8
            sb = self.scroll_area.verticalScrollBar()
            sb.setValue(max(0, min(pos_y, sb.maximum())))
        finally:
            QTimer.singleShot(80, self._clear_scroll_guard)

    def _clear_scroll_guard(self) -> None:
        self._programmatic_scroll = False

    def _on_scroll_changed(self, value: int) -> None:
        if self._programmatic_scroll:
            return
        anchor_y = value + 28
        current_key = self._nav_sections[0][0]
        for key, _ in self._nav_sections:
            sec = self._section_widgets.get(key)
            if sec:
                sec_y = sec.mapTo(self.scroll_content, QPoint(0, 0)).y()
                if sec_y <= anchor_y:
                    current_key = key
        if current_key != self._active_nav:
            self._set_active_nav(current_key)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.btn_fullscreen.setText("⛶ 全屏")
        else:
            self.showFullScreen()
            self.btn_fullscreen.setText("⛶ 退出全屏")

    # ---------------- Language & Category Helpers ----------------

    def _switch_lang(self, lang_code: str, save_current: bool = True) -> None:
        if save_current and not self._syncing_lang and lang_code != self._active_lang:
            self._lang_titles[self._active_lang] = self.txt_title.toPlainText().strip()
            self._lang_descs[self._active_lang] = self.txt_desc.toPlainText().strip()
        self._active_lang = lang_code
        for k, btn in self._lang_buttons.items():
            if k == lang_code:
                btn.setStyleSheet(
                    "QPushButton { background: #2F6B3F; color: #FFFFFF; border: 1px solid #2F6B3F; "
                    "border-radius: 5px; padding: 3px 10px; font-size: 11px; font-weight: bold; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #18201C; color: #C8C3B7; border: 1px solid #4E472F; "
                    "border-radius: 5px; padding: 3px 10px; font-size: 11px; }"
                    "QPushButton:hover { border-color: #6E5D2A; color: #F6F3EA; }"
                )
        self._syncing_lang = True
        self.txt_title.setPlainText(self._lang_titles.get(lang_code, ""))
        self.txt_desc.setPlainText(self._lang_descs.get(lang_code, ""))
        self._syncing_lang = False
        self._update_title_counter()

    def _on_title_or_desc_edited(self) -> None:
        if self._syncing_lang:
            return
        self._lang_titles[self._active_lang] = self.txt_title.toPlainText().strip()
        self._lang_descs[self._active_lang] = self.txt_desc.toPlainText().strip()
        self._update_title_counter()

    def _update_title_counter(self) -> None:
        n = len(self.txt_title.toPlainText().strip())
        self.lbl_title_count.setText(f"{n} / 60")
        color = "#FBBF24" if n > 60 else ("#4ADE80" if n > 0 else "#9A9589")
        self.lbl_title_count.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")

    def _switch_cat_site(self, site_code: str) -> None:
        self._active_cat_site = site_code
        for sc, btn in self._site_pill_buttons.items():
            if sc == site_code:
                btn.setStyleSheet(
                    "QPushButton { background: #203A2B; color: #4ADE80; border: 1px solid #2F6B3F; "
                    "border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: bold; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #18201C; color: #C8C3B7; border: 1px solid #4E472F; "
                    "border-radius: 6px; padding: 4px 10px; font-size: 11px; }"
                    "QPushButton:hover { border-color: #6E5D2A; color: #F6F3EA; }"
                )
        cid, cname = self._site_categories.get(site_code, ("", ""))
        self.edt_cat_id.blockSignals(True)
        self.edt_cat_name.blockSignals(True)
        self.edt_cat_id.setText(cid)
        self.edt_cat_name.setText(cname)
        self.edt_cat_id.blockSignals(False)
        self.edt_cat_name.blockSignals(False)

    def _sync_current_site_cat(self) -> None:
        self._site_categories[self._active_cat_site] = (
            self.edt_cat_id.text().strip(),
            self.edt_cat_name.text().strip(),
        )

    def _predict_category(self) -> None:
        title_src = " ".join(v for v in self._lang_titles.values() if v).strip() or self.txt_title.toPlainText().strip()
        if not title_src:
            QMessageBox.information(self, "提示", "请先填写至少一种语言的产品标题，再使用本地关键词匹配建议分类。")
            return
        low = title_src.lower()
        matched = None
        for kws, cid, cname in _KEYWORD_CATEGORY_HINTS:
            if any(kw in low for kw in kws):
                matched = (cid, cname)
                break
        if matched is None:
            matched = ("CBT1000", "General Merchandise > Other")
        self.edt_cat_id.setText(matched[0])
        self.edt_cat_name.setText(matched[1])
        self._sync_current_site_cat()
        QMessageBox.information(
            self,
            "本地关键词匹配建议",
            f"已基于标题关键词填入建议分类：\n{matched[0]} — {matched[1]}\n\n注：实际美客多类目树在线预测接口将于阶段 2 接入。",
        )

    def _add_custom_attr_row(self, key: str = "", val: str = "") -> None:
        row_w = QWidget()
        row_w.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row_w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        edt_k = QLineEdit(key)
        edt_k.setPlaceholderText("属性编码（如 COLOR / SIZE / VOLTAGE）")
        edt_v = QLineEdit(val)
        edt_v.setPlaceholderText("属性值")
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(26, 26)
        btn_del.setStyleSheet("QPushButton { background: transparent; color: #F87171; border: none; font-weight: bold; }")
        btn_del.clicked.connect(lambda: self._remove_custom_attr_row(row_w, edt_k))
        hl.addWidget(edt_k, stretch=2)
        hl.addWidget(edt_v, stretch=3)
        hl.addWidget(btn_del)
        self._custom_attr_rows.append((edt_k, edt_v, row_w))
        self.custom_attrs_layout.addWidget(row_w)

    def _remove_custom_attr_row(self, row_w: QWidget, edt_k: QLineEdit) -> None:
        self._custom_attr_rows = [(k, v, w) for k, v, w in self._custom_attr_rows if k is not edt_k]
        row_w.setParent(None)
        row_w.deleteLater()

    def _update_volumetric_badge(self) -> None:
        w = float(self.spn_weight.value())
        l = float(self.spn_l.value())
        wi = float(self.spn_w.value())
        h = float(self.spn_h.value())
        vol_g = round(l * wi * h / 6000.0 * 1000.0) if (l > 0 and wi > 0 and h > 0) else 0
        is_throw = vol_g > w and vol_g > 0
        if is_throw:
            self.lbl_vol_status.setText(f"抛重 ({vol_g}g)")
            self.lbl_vol_status.setStyleSheet(
                "background: rgba(245, 158, 11, 0.15); color: #FBBF24; "
                "border: 1px solid #D97706; border-radius: 4px; padding: 2px 8px; font-weight: bold;"
            )
        else:
            self.lbl_vol_status.setText("实重")
            self.lbl_vol_status.setStyleSheet(
                "background: rgba(16, 185, 129, 0.15); color: #4ADE80; "
                "border: 1px solid #2F6B3F; border-radius: 4px; padding: 2px 8px; font-weight: bold;"
            )

    def _get_variant_defaults(self) -> Dict[str, Any]:
        return {
            "net_proceed": round(float(self.spn_net_proceed.value()), 2),
            "weight": float(self.spn_weight.value()),
            "length": float(self.spn_l.value()),
            "width": float(self.spn_w.value()),
            "height": float(self.spn_h.value()),
            "sku": self.edt_sku.text().strip(),
            "stock": int(self.spn_stock.value()),
        }

    # ---------------- Image Gallery Helpers ----------------

    def _get_current_images(self) -> List[str]:
        return [
            str(self.img_list.item(i).data(Qt.ItemDataRole.UserRole) or "").strip()
            for i in range(self.img_list.count())
            if self.img_list.item(i).data(Qt.ItemDataRole.UserRole)
        ]

    def _append_image_item(self, url_or_path: str) -> None:
        item = QListWidgetItem(Path(url_or_path).name or url_or_path[:18])
        item.setData(Qt.ItemDataRole.UserRole, url_or_path)
        item.setToolTip(url_or_path)
        item.setIcon(QIcon(get_placeholder_pixmap(QSize(84, 84), "🖼", radius=6)))
        self.img_list.addItem(item)

        if url_or_path.startswith(("http://", "https://")):
            load_image_async(
                url_or_path,
                QSize(84, 84),
                on_loaded=lambda pm, it=item: it.setIcon(QIcon(pm)) if not pm.isNull() else None,
                radius=6,
            )
        elif os.path.exists(url_or_path):
            pm = QPixmap(url_or_path)
            if not pm.isNull():
                item.setIcon(
                    QIcon(
                        pm.scaled(
                            84,
                            84,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                )

    def _refresh_image_badges(self) -> None:
        cnt = self.img_list.count()
        for i in range(cnt):
            it = self.img_list.item(i)
            url = str(it.data(Qt.ItemDataRole.UserRole) or "")
            short_name = Path(url).name if url else f"图{i + 1}"
            if len(short_name) > 12:
                short_name = short_name[:10] + ".."
            it.setText(f"⭐主图" if i == 0 else short_name)
        self.lbl_img_count.setText(f"共 {cnt} / {MAX_PRODUCT_IMAGES} 张")
        self.variant_section.sync_available_images(self._get_current_images())

    def _add_image_url(self) -> None:
        rem = MAX_PRODUCT_IMAGES - self.img_list.count()
        if rem <= 0:
            QMessageBox.warning(self, "已达上限", f"每个商品最多只能保存 {MAX_PRODUCT_IMAGES} 张图片。")
            return
        text, ok = QInputDialog.getMultiLineText(
            self,
            "添加图片链接",
            f"请输入 http(s) 图片 URL（支持多行批量粘贴，当前还可添加 {rem} 张）:",
        )
        if not ok or not text.strip():
            return
        added = 0
        for line in text.splitlines():
            u = line.strip()
            if not u:
                continue
            if self.img_list.count() >= MAX_PRODUCT_IMAGES:
                break
            if not u.startswith(("http://", "https://")):
                continue
            self._append_image_item(u)
            added += 1
        if added == 0:
            QMessageBox.warning(self, "格式无效", "请输入以 http:// 或 https:// 开头的有效图片链接。")
            return
        self._refresh_image_badges()

    def _add_local_images(self) -> None:
        rem = MAX_PRODUCT_IMAGES - self.img_list.count()
        if rem <= 0:
            QMessageBox.warning(self, "已达上限", f"每个商品最多只能保存 {MAX_PRODUCT_IMAGES} 张图片。")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择本地图片",
            "",
            "图片文件 (*.jpg *.jpeg *.png *.webp *.gif *.bmp)",
        )
        if not files:
            return
        for fp in files[:rem]:
            if Path(fp).suffix.lower() in ALLOWED_LOCAL_EXTS:
                self._append_image_item(fp)
        self._refresh_image_badges()

    def _set_main_image(self) -> None:
        selected = self.img_list.selectedItems()
        if not selected:
            return
        row = self.img_list.row(selected[0])
        if row > 0:
            it = self.img_list.takeItem(row)
            self.img_list.insertItem(0, it)
            self.img_list.setCurrentRow(0)
            self._refresh_image_badges()

    def _move_selected_image(self, delta: int) -> None:
        row = self.img_list.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if 0 <= new_row < self.img_list.count():
            it = self.img_list.takeItem(row)
            self.img_list.insertItem(new_row, it)
            self.img_list.setCurrentRow(new_row)
            self._refresh_image_badges()

    def _delete_selected_images(self) -> None:
        rows = sorted([self.img_list.row(it) for it in self.img_list.selectedItems()], reverse=True)
        for r in rows:
            self.img_list.takeItem(r)
        if rows:
            self._refresh_image_badges()

    # ---------------- Data Collection & Submission ----------------

    def _collect_attributes(self) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {}
        if self.edt_attr_brand.text().strip():
            attrs["BRAND"] = self.edt_attr_brand.text().strip()
        if self.edt_attr_model.text().strip():
            attrs["MODEL"] = self.edt_attr_model.text().strip()
        if self.edt_attr_material.text().strip():
            attrs["MATERIAL"] = self.edt_attr_material.text().strip()
        for ek, ev, _ in self._custom_attr_rows:
            k = ek.text().strip()
            v = ev.text().strip()
            if k:
                attrs[k] = v

        self._sync_current_site_cat()
        attrs["_lang_titles"] = {k: v.strip() for k, v in self._lang_titles.items() if v.strip()}
        attrs["_lang_descs"] = {k: v.strip() for k, v in self._lang_descs.items() if v.strip()}
        attrs["_site_categories"] = {
            k: [cid, cname] for k, (cid, cname) in self._site_categories.items() if cid or cname
        }
        attrs["_variant_headers"] = self.variant_section.get_dimension_headers()
        if self._existing_logs:
            attrs["_logs"] = list(self._existing_logs)
        return attrs

    def _collect_payload(self) -> Dict[str, Any]:
        self._lang_titles[self._active_lang] = self.txt_title.toPlainText().strip()
        self._lang_descs[self._active_lang] = self.txt_desc.toPlainText().strip()

        primary_title = (
            self._lang_titles.get("es")
            or self._lang_titles.get("pt")
            or self._lang_titles.get("zh")
            or self._lang_titles.get("en")
            or ""
        ).strip()
        primary_desc = (
            self._lang_descs.get("es")
            or self._lang_descs.get("pt")
            or self._lang_descs.get("zh")
            or self._lang_descs.get("en")
            or ""
        ).strip()

        net_val = round(float(self.spn_net_proceed.value()), 2)
        acc_id = str(self.combo_store.currentData() or "")
        store_lbl = self.combo_store.currentText() if acc_id else ""
        if not store_lbl and str(self.product.get("store_name") or "").strip():
            store_lbl = str(self.product.get("store_name") or "").strip()

        imgs = self._get_current_images()
        variants = self.variant_section.get_variants()
        total_stock = sum(int(v.get("stock") or 0) for v in variants) if variants else int(self.spn_stock.value())

        default_site = str(self.combo_site.currentData() or self._active_cat_site or "MLM")
        site_cid, site_cname = self._site_categories.get(
            default_site,
            (self.edt_cat_id.text().strip(), self.edt_cat_name.text().strip()),
        )

        payload: Dict[str, Any] = {
            "title": primary_title,
            "title_es": self._lang_titles.get("es", "").strip() or primary_title,
            "title_pt": self._lang_titles.get("pt", "").strip() or primary_title,
            "title_zh": self._lang_titles.get("zh", "").strip() or primary_title,
            "description": primary_desc,
            "sku": self.edt_sku.text().strip(),
            "account_id": acc_id,
            "store_name": store_lbl,
            "site_id": default_site,
            "category_id": site_cid or self.edt_cat_id.text().strip(),
            "category_name": site_cname or self.edt_cat_name.text().strip(),
            "weight": float(self.spn_weight.value()),
            "length": float(self.spn_l.value()),
            "width": float(self.spn_w.value()),
            "height": float(self.spn_h.value()),
            "stock": total_stock,
            "group_name": self.combo_group.currentText().strip() or "未分组",
            "salesman": self.edt_salesman.text().strip(),
            "attributes": self._collect_attributes(),
            "variants": variants,
            "variants_count": len(variants),
            "images": imgs,
            "status": str(self.combo_status.currentData() or "draft"),
        }
        if self.mode == "EDIT":
            # Cost and price belong to the card / cost import; the editor only moves the net target
            if net_val != net_proceeds(self.product):
                payload["profit"] = profit_for_net(net_val, self.product.get("cost_price"))
        else:
            payload.update({"price": net_val, "cost_price": 0.0, "profit": net_val})
        return payload

    def _on_submit(self) -> None:
        self._lang_titles[self._active_lang] = self.txt_title.toPlainText().strip()
        primary_title = (
            self._lang_titles.get("es")
            or self._lang_titles.get("pt")
            or self._lang_titles.get("zh")
            or self._lang_titles.get("en")
            or ""
        ).strip()
        if not primary_title:
            QMessageBox.warning(self, "缺少必填项", "请至少在一个语言标签页中填写产品标题！")
            self._jump_to_section("basic")
            self.txt_title.setFocus()
            return

        payload = self._collect_payload()
        self.created_item = payload
        self._saved_fields = payload
        if callable(self._on_audit_log) and self.mode == "EDIT":
            try:
                self._on_audit_log(
                    "编辑商品",
                    f"更新标题/属性与 {len(payload['variants'])} 个变体、{len(payload['images'])} 张图片",
                    payload.get("salesman") or "当前用户",
                )
            except Exception:
                pass
        self.accept()

    def get_created_item(self) -> Optional[Dict[str, Any]]:
        return self.created_item

    def get_updated_fields(self) -> Dict[str, Any]:
        if self._saved_fields is not None:
            return dict(self._saved_fields)
        return self._collect_payload()


class CreateProductDialog(ProductEditorDialog):
    """Manual single-product creation workspace powered by ProductEditorDialog."""

    def __init__(
        self,
        auth_mgr: Optional[Any] = None,
        groups: Optional[List[str]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(
            mode="CREATE",
            product=None,
            auth_mgr=auth_mgr,
            groups=groups,
            parent=parent,
        )


class SingleProductEditDialog(ProductEditorDialog):
    """Single-product full editor powered by ProductEditorDialog."""

    def __init__(
        self,
        product: Dict[str, Any],
        parent: Optional[QWidget] = None,
        auth_mgr: Optional[Any] = None,
        groups: Optional[List[str]] = None,
        on_audit_log: Optional[Callable[[str, str, str], None]] = None,
    ):
        super().__init__(
            mode="EDIT",
            product=product,
            auth_mgr=auth_mgr,
            groups=groups,
            on_audit_log=on_audit_log,
            parent=parent,
        )


def _col_letters_to_index(ref: str) -> int:
    col = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            col = col * 26 + (ord(ch) - ord("A") + 1)
        elif "a" <= ch <= "z":
            col = col * 26 + (ord(ch) - ord("a") + 1)
        else:
            break
    return max(0, col - 1)


def _read_xlsx_rows(file_path: str) -> List[Dict[str, str]]:
    """Parses first worksheet of an .xlsx workbook via stdlib zipfile + ElementTree."""
    ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(file_path, "r") as zf:
        names = zf.namelist()
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in names:
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.findall(f".//{ns_main}si"):
                txt_parts = [t.text or "" for t in si.findall(f".//{ns_main}t")]
                shared_strings.append("".join(txt_parts))

        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in names:
            candidates = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            if not candidates:
                return []
            sheet_name = candidates[0]

        sh_root = ET.fromstring(zf.read(sheet_name))
        raw_grid: List[List[str]] = []
        for row_el in sh_root.findall(f".//{ns_main}sheetData/{ns_main}row"):
            row_map: Dict[int, str] = {}
            max_col = -1
            for c_el in row_el.findall(f"{ns_main}c"):
                ref = c_el.attrib.get("r", "")
                c_idx = _col_letters_to_index(ref) if ref else (max_col + 1)
                c_type = c_el.attrib.get("t", "")
                val = ""
                if c_type == "inlineStr":
                    val = "".join(t.text or "" for t in c_el.findall(f".//{ns_main}t"))
                else:
                    v_el = c_el.find(f"{ns_main}v")
                    if v_el is not None and v_el.text is not None:
                        raw_txt = v_el.text
                        if c_type == "s":
                            try:
                                val = shared_strings[int(raw_txt)]
                            except Exception:
                                val = raw_txt
                        else:
                            val = raw_txt
                row_map[c_idx] = val.strip()
                if c_idx > max_col:
                    max_col = c_idx
            if max_col >= 0:
                row_list = [row_map.get(i, "") for i in range(max_col + 1)]
                if any(x.strip() for x in row_list):
                    raw_grid.append(row_list)

    if len(raw_grid) < 2:
        return []
    headers = [h.strip() for h in raw_grid[0]]
    out: List[Dict[str, str]] = []
    for r_vals in raw_grid[1:]:
        d: Dict[str, str] = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            d[h] = r_vals[i] if i < len(r_vals) else ""
        if any(str(v).strip() for v in d.values()):
            out.append(d)
    return out


class TableCreateProductDialog(BaseVaultDialog):
    """1:1 YuanYou '通过表格文件创建产品' dialog with Net-Proceeds-only CSV/TSV/XLSX support."""

    TEMPLATE_HEADERS = [
        "标题",
        "描述",
        "SKU",
        "净收益(USD)",
        "库存",
        "重量(g)",
        "长(cm)",
        "宽(cm)",
        "高(cm)",
        "站点",
        "归属店铺",
        "分组",
        "分类ID",
        "分类名称",
        "图片链接",
    ]

    def __init__(
        self,
        auth_mgr: Optional[Any] = None,
        groups: Optional[List[str]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__("通过表格文件创建产品", parent)
        self.auth_mgr = auth_mgr
        self._initial_groups = list(groups or ["未分组", "手搓组"])
        self._parsed_rows: List[Dict[str, Any]] = []
        self._valid_rows: List[Dict[str, Any]] = []
        self.resize(920, 620)
        self.setMinimumSize(780, 500)
        self._init_ui()

    @staticmethod
    def parse_spreadsheet_file(file_path: str) -> List[Dict[str, str]]:
        ext = Path(file_path).suffix.lower()
        if ext == ".xlsx":
            return _read_xlsx_rows(file_path)

        content: Optional[str] = None
        for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
            try:
                content = Path(file_path).read_text(encoding=enc)
                break
            except Exception:
                continue
        if content is None:
            raise ValueError("无法识别文件编码，请另存为 UTF-8 CSV 后重试")

        delim = "\t" if ext == ".tsv" else ","
        if delim == "," and "\t" in content.splitlines()[0] and "," not in content.splitlines()[0]:
            delim = "\t"
        reader = csv.DictReader(content.splitlines(), delimiter=delim)
        rows: List[Dict[str, str]] = []
        for r in reader:
            cleaned = {str(k).strip(): str(v or "").strip() for k, v in r.items() if k is not None}
            if any(cleaned.values()):
                rows.append(cleaned)
        return rows

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        lbl_tip = QLabel(
            "按表格模板批量导入待刊登产品（仅含净收益，不含成本列）。表格中留空的店铺、站点或分组将自动套用下方默认值："
        )
        lbl_tip.setWordWrap(True)
        lbl_tip.setStyleSheet(DARK_TIP_LABEL_QSS)
        layout.addWidget(lbl_tip)

        # Top defaults + template actions card
        cfg_card = QFrame()
        cfg_card.setObjectName("VaultTableImportCard")
        cfg_card.setStyleSheet(
            "QFrame#VaultTableImportCard { background: #202820; border: 1px solid #4E472F; border-radius: 8px; }"
        )
        cl = QGridLayout(cfg_card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(10)

        cl.addWidget(QLabel("默认归属店铺:"), 0, 0)
        self.combo_store = QComboBox()
        populate_store_combo(self.combo_store, auth_mgr=self.auth_mgr, all_label="")
        if self.combo_store.count() == 0:
            self.combo_store.addItem("纯本地商品（无店铺）", "")
        cl.addWidget(self.combo_store, 0, 1)

        cl.addWidget(QLabel("默认站点:"), 0, 2)
        self.combo_site = QComboBox()
        for sc, sn in COUNTRY_STATION_MAP:
            self.combo_site.addItem(sn, sc)
        cl.addWidget(self.combo_site, 0, 3)

        cl.addWidget(QLabel("默认分组:"), 0, 4)
        self.combo_group = QComboBox()
        self.combo_group.setEditable(True)
        for g in self._initial_groups:
            self.combo_group.addItem(g)
        cl.addWidget(self.combo_group, 0, 5)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_dl = QPushButton("⬇ 下载标准导入模板 (.csv)")
        style_cancel_button(btn_dl)
        btn_dl.clicked.connect(self._download_template)
        btn_row.addWidget(btn_dl)

        btn_pick = QPushButton("📁 选择表格文件 (.csv / .tsv / .xlsx)")
        style_primary_button(btn_pick)
        btn_pick.clicked.connect(self._select_file)
        btn_row.addWidget(btn_pick)

        self.lbl_file_path = QLabel("未选择文件")
        self.lbl_file_path.setStyleSheet("color: #9A9589; font-size: 12px;")
        btn_row.addWidget(self.lbl_file_path, stretch=1)
        cl.addLayout(btn_row, 1, 0, 1, 6)

        layout.addWidget(cfg_card)

        self.lbl_summary = QLabel("请先选择表格文件，导入前可在此预览与核对各字段：")
        self.lbl_summary.setStyleSheet("color: #E6E2D8; font-weight: 600;")
        layout.addWidget(self.lbl_summary)

        self.preview_table = QTableWidget()
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setColumnCount(8)
        self.preview_table.setHorizontalHeaderLabels(
            ["#", "标题", "SKU", "净收益(USD)", "库存", "重量(g)", "尺寸(cm)", "校验结果"]
        )
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col_i in (0, 2, 3, 4, 5, 6, 7):
            self.preview_table.horizontalHeader().setSectionResizeMode(col_i, QHeaderView.ResizeMode.ResizeToContents)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.preview_table, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch()
        btn_cancel = QPushButton("取 消")
        style_cancel_button(btn_cancel)
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        self.btn_import = QPushButton("确认导入并创建")
        style_primary_button(self.btn_import)
        self.btn_import.setEnabled(False)
        self.btn_import.clicked.connect(self._on_confirm)
        footer.addWidget(self.btn_import)
        layout.addLayout(footer)

    def _download_template(self) -> None:
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存产品导入模板",
            "产品表格创建模板.csv",
            "CSV 文件 (*.csv)",
        )
        if not save_path:
            return
        try:
            self.write_template_csv(save_path)
            QMessageBox.information(self, "保存成功", f"导入模板已保存至:\n{save_path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", f"无法写入模板文件: {exc}")

    @classmethod
    def write_template_csv(cls, save_path: str) -> None:
        with open(save_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(cls.TEMPLATE_HEADERS)
            writer.writerow([
                "Funda Cojin Decorativa Lino 45x45cm",
                "Funda suave lavable con cremallera oculta",
                "DEMO-CUSHION-01",
                "16.50",
                "50",
                "320",
                "25",
                "20",
                "4",
                "MLM",
                "",
                "手搓组",
                "CBT1637",
                "Home > Bedding > Cushions & Covers",
                "https://http2.mlstatic.com/D_NQ_NP_example1.jpg",
            ])

    def _select_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择批量创建产品表格",
            "",
            "表格文件 (*.csv *.tsv *.xlsx *.txt)",
        )
        if not file_path:
            return
        try:
            rows = self.parse_spreadsheet_file(file_path)
        except Exception as exc:
            QMessageBox.critical(self, "解析失败", f"无法读取表格: {exc}")
            return
        self.lbl_file_path.setText(Path(file_path).name)
        self.load_parsed_rows(rows)

    def load_parsed_rows(self, rows: List[Dict[str, str]]) -> None:
        self._parsed_rows = list(rows)
        self._valid_rows = []
        self.preview_table.setRowCount(len(rows))
        invalid_cnt = 0

        for idx, r in enumerate(rows):
            title = (
                r.get("标题")
                or r.get("title")
                or r.get("商品标题")
                or r.get("标题(西班牙语)")
                or ""
            ).strip()
            sku = (r.get("SKU") or r.get("sku") or "").strip()
            net_s = (
                r.get("净收益(USD)")
                or r.get("净收益")
                or r.get("net_proceeds")
                or r.get("profit")
                or r.get("售价")
                or "0"
            )
            stock_s = r.get("库存") or r.get("stock") or "0"
            weight_s = r.get("重量(g)") or r.get("重量") or r.get("weight") or "0"
            l_s = r.get("长(cm)") or r.get("长") or r.get("length") or "0"
            w_s = r.get("宽(cm)") or r.get("宽") or r.get("width") or "0"
            h_s = r.get("高(cm)") or r.get("高") or r.get("height") or "0"
            dim_s = (r.get("尺寸") or f"{l_s}*{w_s}*{h_s}").strip()

            ok = bool(title)
            if ok:
                self._valid_rows.append(dict(r))
                st_item = QTableWidgetItem("可导入")
                st_item.setForeground(QColor("#4ADE80"))
            else:
                invalid_cnt += 1
                st_item = QTableWidgetItem("缺少标题(跳过)")
                st_item.setForeground(QColor("#F87171"))

            vals = [str(idx + 1), title or "(空)", sku, str(net_s), str(stock_s), str(weight_s), dim_s]
            for ci, v in enumerate(vals):
                self.preview_table.setItem(idx, ci, QTableWidgetItem(v))
            self.preview_table.setItem(idx, 7, st_item)

        self.lbl_summary.setText(
            f"共读取 {len(rows)} 行：有效可导入 {len(self._valid_rows)} 行，异常跳过 {invalid_cnt} 行"
        )
        self.btn_import.setEnabled(len(self._valid_rows) > 0)

    def _on_confirm(self) -> None:
        if not self._valid_rows:
            QMessageBox.warning(self, "提示", "没有可导入的有效商品行（需至少包含标题）。")
            return
        self.accept()

    def get_rows(self) -> List[Dict[str, Any]]:
        default_acc = str(self.combo_store.currentData() or "")
        default_store = self.combo_store.currentText() if default_acc else ""
        default_site = str(self.combo_site.currentData() or "MLM")
        default_group = self.combo_group.currentText().strip() or "未分组"

        out: List[Dict[str, Any]] = []
        for r in self._valid_rows:
            row_copy = dict(r)
            if not (row_copy.get("归属店铺") or row_copy.get("店铺") or row_copy.get("store_name")):
                if default_store:
                    row_copy["store_name"] = default_store
                if default_acc:
                    row_copy["account_id"] = default_acc
            if not (row_copy.get("站点") or row_copy.get("site_id")):
                row_copy["site_id"] = default_site
            if not (row_copy.get("分组") or row_copy.get("group_name")):
                row_copy["group_name"] = default_group
            out.append(row_copy)
        return out
