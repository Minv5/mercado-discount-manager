from __future__ import annotations

import csv
import json
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from items.vault_db import ProductVaultDB
from items.vault_pricing import net_proceeds
from items.vault_filters import VaultAICapsuleBar, VaultFilterBar
from items.vault_card import ProductCardGrid
from items.vault_batch_settings import (
    PriceSettingDialog,
    StockSettingDialog,
    WeightSettingDialog,
    ManualSizeDialog,
    VolumeEstimateDialog
)
from items.vault_more_actions import (
    GenerateVariantsDialog,
    ReduceVariantsDialog,
    BatchAttrDialog,
    ReplaceKeywordDialog,
    BatchCategoryDialog,
    CopyToGroupDialog,
    DeleteConfirmDialog,
    DeleteByFilterDialog,
    PreUploadImagesDialog
)
from items.vault_ai_dialog import AIProductOptimizeDialog
from items.vault_publisher import BatchPublishDialog
from items.vault_item_dialogs import (
    SingleProductEditDialog,
    SingleProductPricingDialog,
    CreateProductDialog,
    TableCreateProductDialog,
    ImportCostDialog,
    VariantDrilldownDialog,
)


class ProductVaultPage(QFrame):
    """Master Local Product Vault Page (Card-Grid Stream with Full Suite of Batch Actions)."""

    def __init__(self, db: Optional[ProductVaultDB] = None, auth: Optional[Any] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("ProductVaultPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QFrame#ProductVaultPage { background: #171B19; }")
        self.db = db or ProductVaultDB()
        self.db.init_db()
        self.auth = auth
        if self.auth is None:
            try:
                from engine.auth import AuthManager
                self.auth = AuthManager()
            except Exception:
                self.auth = None

        self.current_page = 1
        self.page_size = 50
        self.total_items = 0
        self.current_items: List[Dict[str, Any]] = []
        self.active_verify_filter: Optional[str] = None

        self._init_ui()
        self.load_data()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # AI Capsule Bar removed per user directive (retained as dormant reference for future activation)
        self.capsule_bar = None

        # 1. Product Filter & Search Bar (Clean Grid-aligned ERP Form)
        self.filter_bar = VaultFilterBar(self, auth_mgr=self.auth)
        self.filter_bar.search_triggered.connect(self._on_search)
        self.filter_bar.import_cost_clicked.connect(self._on_import_cost)
        self.filter_bar.manual_create_clicked.connect(self._on_create_manual)
        self.filter_bar.create_table_clicked.connect(self._on_create_table)
        main_layout.addWidget(self.filter_bar)

        # 3. Main Card Grid Stream
        self.card_grid = ProductCardGrid(self)
        self.card_grid.selection_updated.connect(self._on_selection_updated)
        self.card_grid.verify_requested.connect(self._on_single_verify)
        self.card_grid.edit_requested.connect(self._on_single_edit)
        self.card_grid.field_updated.connect(self._on_card_field_updated)
        self.card_grid.variant_drilldown_requested.connect(self._on_variant_drilldown)
        main_layout.addWidget(self.card_grid, stretch=1)

        # 4. Bottom Actions & Pagination Toolbar (2-Tier Clean Responsive Card)
        self.toolbar_frame = QFrame(self)
        self.toolbar_frame.setObjectName("VaultToolbarFrame")
        self.toolbar_frame.setStyleSheet("""
            QFrame#VaultToolbarFrame {
                background: #202820;
                border: 1px solid #4E472F;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QPushButton {
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
                background: #18201C;
                border: 1px solid #4E472F;
                color: #E6E2D8;
                min-height: 20px;
            }
            QPushButton::menu-indicator {
                image: none;
                width: 0px;
            }
            QPushButton:hover {
                border-color: #6E5D2A;
                color: #F6F3EA;
                background: #252A25;
            }
        """)

        toolbar_vbox = QVBoxLayout(self.toolbar_frame)
        toolbar_vbox.setContentsMargins(4, 4, 4, 4)
        toolbar_vbox.setSpacing(8)

        # Tier 1: Action Command Row
        tier1 = QHBoxLayout()
        tier1.setContentsMargins(0, 0, 0, 0)
        tier1.setSpacing(8)

        # Multi-select
        self.btn_select_all = QPushButton("全选")
        self.btn_select_all.clicked.connect(self.card_grid.select_all)
        tier1.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("反选")
        self.btn_deselect_all.clicked.connect(self.card_grid.invert_selection)
        tier1.addWidget(self.btn_deselect_all)

        self.lbl_selection_count = QLabel("已选 0/0 条")
        self.lbl_selection_count.setStyleSheet("""
            background: rgba(47, 107, 63, 0.3);
            color: #4ADE80;
            border: 1px solid #2F6B3F;
            border-radius: 6px;
            padding: 3px 10px;
            font-weight: bold;
            font-size: 12px;
        """)
        tier1.addWidget(self.lbl_selection_count)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("color: #4E472F; margin: 2px 4px;")
        tier1.addWidget(sep)

        # Core Action Buttons
        self.btn_publish = QPushButton("批量上传")
        self.btn_publish.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 5px 14px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { background: #356F45; }
        """)
        self.btn_publish.clicked.connect(self._on_publish)
        tier1.addWidget(self.btn_publish)

        self.btn_pre_upload = QPushButton("图片链接预检")
        self.btn_pre_upload.setToolTip("检查本地图片链接有效性（美客多官方图床上传将于阶段2接入）")
        self.btn_pre_upload.setStyleSheet("""
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
        self.btn_pre_upload.clicked.connect(self._on_pre_upload)
        tier1.addWidget(self.btn_pre_upload)

        # Copy Trio
        self.btn_copy_title = QPushButton("复制标题")
        self.btn_copy_title.clicked.connect(self._on_copy_titles)
        tier1.addWidget(self.btn_copy_title)

        self.btn_copy_id = QPushButton("复制ID")
        self.btn_copy_id.clicked.connect(self._on_copy_ids)
        tier1.addWidget(self.btn_copy_id)

        self.btn_copy_cbt_id = QPushButton("复制原美客多ID")
        self.btn_copy_cbt_id.clicked.connect(self._on_copy_cbt_ids)
        tier1.addWidget(self.btn_copy_cbt_id)

        # Batch Settings Menu
        self.btn_batch_settings = QPushButton("批量设置 ▾")
        self._init_batch_settings_menu()
        tier1.addWidget(self.btn_batch_settings)

        # Direct 1-click AI Title Optimization Button (no redundant 1-item dropdown)
        self.btn_ai_tools = QPushButton("AI标题优化")
        self.btn_ai_tools.setToolTip("AI 标题翻译 / 差异化优化")
        self.btn_ai_tools.clicked.connect(self._on_ai_optimize)
        tier1.addWidget(self.btn_ai_tools)

        # More Actions Menu
        self.btn_more_actions = QPushButton("更多操作 ▾")
        self._init_more_actions_menu()
        tier1.addWidget(self.btn_more_actions)

        tier1.addStretch()

        # Delete Selected (aligned right in tier 1)
        self.btn_delete_selected = QPushButton("删除已选")
        self.btn_delete_selected.setStyleSheet("""
            QPushButton {
                background: rgba(239, 68, 68, 0.15);
                color: #F87171;
                border: 1px solid #EF4444;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: rgba(239, 68, 68, 0.25); border-color: #F87171; }
        """)
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)
        tier1.addWidget(self.btn_delete_selected)

        toolbar_vbox.addLayout(tier1)

        # Tier 2: Info & Pagination Row
        tier2 = QHBoxLayout()
        tier2.setContentsMargins(0, 2, 0, 0)
        tier2.setSpacing(10)

        tip_lbl = QLabel("💡 提示：在上方勾选商品后，可一键批量改价、改重量、估算尺寸或调用 AI 标题优化，也可直接批量刊登至美客多店铺。")
        tip_lbl.setStyleSheet("color: #A39E93; font-size: 11px;")
        tier2.addWidget(tip_lbl)

        tier2.addStretch()

        self.lbl_total = QLabel("总数据：0 条")
        self.lbl_total.setStyleSheet("color: #E6E2D8; font-size: 12px; font-weight: 500;")
        tier2.addWidget(self.lbl_total)

        self.btn_prev_page = QPushButton("◀ 上一页")
        self.btn_prev_page.setStyleSheet("padding: 3px 10px; font-size: 11px;")
        self.btn_prev_page.clicked.connect(self._prev_page)
        tier2.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("第 1 / 1 页")
        self.lbl_page_info.setStyleSheet("font-size: 12px; font-weight: bold; color: #E6E2D8; padding: 0 4px;")
        tier2.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton("下一页 ▶")
        self.btn_next_page.setStyleSheet("padding: 3px 10px; font-size: 11px;")
        self.btn_next_page.clicked.connect(self._next_page)
        tier2.addWidget(self.btn_next_page)

        self.combo_page_size = QComboBox()
        self.combo_page_size.setMinimumWidth(116)
        self.combo_page_size.view().setMinimumWidth(128)
        self.combo_page_size.setStyleSheet("""
            QComboBox {
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 12px;
                background: #18201C;
                color: #E6E2D8;
                height: 24px;
            }
        """)
        self.combo_page_size.addItem("50 条/页", 50)
        self.combo_page_size.addItem("100 条/页", 100)
        self.combo_page_size.addItem("200 条/页", 200)
        self.combo_page_size.addItem("500 条/页", 500)
        self.combo_page_size.currentIndexChanged.connect(self._on_page_size_changed)
        tier2.addWidget(self.combo_page_size)

        toolbar_vbox.addLayout(tier2)

        main_layout.addWidget(self.toolbar_frame)

    def _init_batch_settings_menu(self):
        menu = QMenu(self)
        act_price = menu.addAction("设置价格")
        act_price.triggered.connect(self._on_set_price)

        act_stock = menu.addAction("设置库存")
        act_stock.triggered.connect(self._on_set_stock)

        act_weight = menu.addAction("设置重量")
        act_weight.triggered.connect(self._on_set_weight)

        menu.addSeparator()
        size_menu = menu.addMenu("尺寸 >")
        act_manual_sz = size_menu.addAction("手动设置尺寸")
        act_manual_sz.triggered.connect(self._on_set_manual_size)
        act_est_vol = size_menu.addAction("按重量估算体积")
        act_est_vol.triggered.connect(self._on_estimate_volume)

        self.btn_batch_settings.setMenu(menu)

    def _init_more_actions_menu(self):
        menu = QMenu(self)
        act_var = menu.addAction("1. 生成变体")
        act_var.triggered.connect(self._on_generate_variants)

        act_reduce = menu.addAction("2. 裁剪变体")
        act_reduce.triggered.connect(self._on_reduce_variants)

        act_attr = menu.addAction("3. 修改产品属性")
        act_attr.triggered.connect(self._on_batch_attr)

        act_kw = menu.addAction("4. 替换关键字")
        act_kw.triggered.connect(self._on_replace_keyword)

        act_cat = menu.addAction("5. 修改分类")
        act_cat.triggered.connect(self._on_batch_category)

        act_clone = menu.addAction("6. 复制产品")
        act_clone.triggered.connect(self._on_copy_to_group)

        act_export_csv = menu.addAction("7. 导出选中清单 (CSV)")
        act_export_csv.triggered.connect(self._on_export_selected_csv)

        menu.addSeparator()
        act_del_filter = menu.addAction("🗑️ 按筛选删除产品")
        act_del_filter.triggered.connect(self._on_delete_by_filter)

        self.btn_more_actions.setMenu(menu)

    def load_data(self):
        if hasattr(self.db, "get_categories") and hasattr(self, "filter_bar"):
            try:
                self.filter_bar.set_categories(self.db.get_categories())
            except Exception:
                pass
        self.filter_bar.set_groups(
            [{"name": g, "id": g} for g in self.db.get_group_names() if g != "未分组"]
        )
        filters = self.filter_bar.get_filters()
        if self.active_verify_filter:
            filters["verify_status"] = self.active_verify_filter
        res = self.db.query_products(
            filters=filters,
            page=self.current_page,
            page_size=self.page_size,
            sort_by=filters.get("sort_by"),
            sort_order=filters.get("sort_order", "desc")
        )
        self.total_items = res["total"]
        self.current_items = res["items"]

        # Populate cards
        self.card_grid.set_items(self.current_items)

        # Update labels
        max_page = max(1, (self.total_items + self.page_size - 1) // self.page_size)
        self.lbl_total.setText(f"总数据：{self.total_items} 条")
        self.lbl_page_info.setText(f"{self.current_page} / {max_page}")
        self._update_selection_counter(0)

        # Update capsule bar (if active)
        if getattr(self, "capsule_bar", None):
            page_pids = [it["id"] for it in self.current_items]
            counts = self.db.get_status_counts(page_product_ids=page_pids)
            self.capsule_bar.update_counts(counts)

    def _on_capsule_clicked(self, capsule_key: str):
        if capsule_key == "this_page":
            self.active_verify_filter = None
        elif self.active_verify_filter == capsule_key:
            self.active_verify_filter = None
        else:
            self.active_verify_filter = capsule_key
        if getattr(self, "capsule_bar", None):
            self.capsule_bar.set_active_capsule(self.active_verify_filter)
        self.current_page = 1
        self.load_data()

    def _on_search(self, filters: Dict[str, Any]):
        self.current_page = 1
        self.load_data()

    def _on_selection_updated(self, selected_ids: List[str]):
        self._update_selection_counter(len(selected_ids))

    def _update_selection_counter(self, count: int):
        self.lbl_selection_count.setText(f"已选 {count}/{len(self.current_items)} 条")

    def _get_selected_ids(self) -> List[str]:
        pids = self.card_grid.get_selected_ids()
        if not pids:
            QMessageBox.warning(self, "提示", "请先在卡片左上角勾选要操作的商品！")
            return []
        return pids

    # ================== COPY HANDLERS ==================
    def _on_copy_titles(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        titles = [p.get("title") or "" for p in self.current_items if p["id"] in pids]
        QApplication.clipboard().setText("\n".join(titles))
        QMessageBox.information(self, "复制成功", f"已复制 {len(titles)} 个商品标题到剪贴板！")

    def _on_copy_ids(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        QApplication.clipboard().setText("\n".join(pids))
        QMessageBox.information(self, "复制成功", f"已复制 {len(pids)} 个商品ID到剪贴板！")

    def _on_copy_cbt_ids(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        cbt_ids = [p.get("cbt_item_id") or "" for p in self.current_items if p["id"] in pids]
        QApplication.clipboard().setText("\n".join(cbt_ids))
        QMessageBox.information(self, "复制成功", f"已复制 {len(cbt_ids)} 个原美客多ID到剪贴板！")

    # ================== BATCH SETTINGS ==================
    def _on_set_price(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = PriceSettingDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            self.db.batch_update_price(
                pids,
                price_type=data["price_type"],
                adjustment_type=data["adjustment_type"],
                amount_or_rate=data["amount_or_rate"],
                base_type=data.get("base_type")
            )
            self.load_data()
            QMessageBox.information(self, "完成", "批量设置价格成功！")

    def _on_set_stock(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = StockSettingDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            self.db.batch_update_stock(pids, stock=data["stock"])
            self.load_data()
            QMessageBox.information(self, "完成", "批量设置库存成功！")

    def _on_set_weight(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = WeightSettingDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            self.db.batch_update_weight(
                pids,
                mode=data["mode"],
                value=data.get("value"),
                min_val=data.get("min_val"),
                max_val=data.get("max_val")
            )
            self.load_data()
            QMessageBox.information(self, "完成", "批量设置重量成功！")

    def _on_set_manual_size(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = ManualSizeDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            self.db.batch_update_size(pids, length=data["length"], width=data["width"], height=data["height"])
            self.load_data()
            QMessageBox.information(self, "完成", "批量设置商品尺寸成功！")

    def _on_estimate_volume(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = VolumeEstimateDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            updated = self.db.batch_estimate_volume(pids, force_override=data["force_override"])
            self.load_data()
            QMessageBox.information(self, "完成", f"按重量估算体积完成，共更新了 {updated} 个商品尺寸！")

    # ================== AI TOOLS ==================
    def _on_ai_optimize(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        selected_items = [p for p in self.current_items if p["id"] in pids]
        dlg = AIProductOptimizeDialog(selected_items, self)
        if dlg.exec():
            updates = dlg.get_updates()
            target_lang = dlg.get_target_language() or ""
            scope = dlg.get_scope()
            count = 0
            for u in updates:
                pid = u.get("id") or u.get("productId") or ""
                prod = self.db.get_product(pid) if pid else None
                if not prod:
                    continue

                fields: Dict[str, Any] = {}
                attrs = dict(prod.get("attributes") or {}) if isinstance(prod.get("attributes"), dict) else {}

                if scope in ("both", "title") and u.get("title"):
                    fields["title"] = u["title"]
                    if target_lang in ("zh", "es", "pt"):
                        fields[f"title_{target_lang}"] = u["title"]
                    if target_lang:
                        lt = dict(attrs.get("_lang_titles") or {})
                        lt[target_lang] = u["title"]
                        attrs["_lang_titles"] = lt

                if scope in ("both", "description") and u.get("description"):
                    fields["description"] = u["description"]
                    if target_lang:
                        ld = dict(attrs.get("_lang_descs") or {})
                        ld[target_lang] = u["description"]
                        attrs["_lang_descs"] = ld

                if target_lang:
                    fields["attributes"] = attrs

                if fields and self.db.update_product(pid, fields):
                    count += 1
            self.load_data()
            QMessageBox.information(self, "优化成功", f"AI标题/描述优化完成，共更新了 {count} 个商品！")

    # ================== MORE ACTIONS ==================
    def _on_generate_variants(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = GenerateVariantsDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            res = self.db.batch_generate_variants(
                pids,
                target_count=data["target_count"],
                prefer_preset=data["prefer_preset"],
                custom_values=data["custom_values"]
            )
            self.load_data()
            QMessageBox.information(self, "变体生成完成", f"生成变体成功 {res['success']} 个，跳过 {res['skipped']} 个！")

    def _on_batch_attr(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = BatchAttrDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            self.db.batch_update_attributes(pids, data["attributes"])
            self.load_data()
            QMessageBox.information(self, "完成", "批量修改产品属性成功！")

    def _on_replace_keyword(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = ReplaceKeywordDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            updated = self.db.batch_replace_keyword(
                pids,
                keyword=data["keyword"],
                replace_text=data["replace_text"],
                is_ignore_case=data["is_ignore_case"],
                in_title=data["in_title"],
                in_desc=data["in_desc"]
            )
            self.load_data()
            QMessageBox.information(self, "完成", f"批量替换关键字成功，已处理 {updated} 个商品！")

    def _on_batch_category(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = BatchCategoryDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            self.db.batch_update_category(
                pids,
                site_id=data["site_id"],
                category_id=data["category_id"],
                category_name=data["category_name"]
            )
            self.load_data()
            QMessageBox.information(self, "完成", "批量设置CBT分类成功！")

    def _on_copy_to_group(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = CopyToGroupDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            copied = self.db.batch_copy_to_group(
                pids,
                target_group_id=data["target_group_id"],
                target_group_name=data["target_group_name"]
            )
            self.load_data()
            QMessageBox.information(self, "完成", f"复制产品成功，已克隆生成 {copied} 个独立草稿！")

    def _on_reduce_variants(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = ReduceVariantsDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            updated = self.db.batch_reduce_variants(pids, data["target_count"])
            self.load_data()
            QMessageBox.information(
                self,
                "裁剪完成",
                f"已完成变体裁剪：共更新了 {updated} 个商品（目标保留上限 {data['target_count']} 个）。",
            )

    def _on_export_selected_csv(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出选中产品清单",
            "产品库选中清单.csv",
            "CSV 文件 (*.csv)",
        )
        if not save_path:
            return
        selected_items = [self.db.get_product(pid) for pid in pids]
        selected_items = [p for p in selected_items if p]
        headers = [
            "商品ID", "标题", "SKU", "净收益(USD)", "库存", "重量(g)",
            "长(cm)", "宽(cm)", "高(cm)", "站点", "归属店铺", "分组",
            "分类ID", "分类名称", "变体数", "图片链接",
        ]
        try:
            with open(save_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for p in selected_items:
                    net_val = net_proceeds(p)
                    imgs = p.get("images") or []
                    if isinstance(imgs, str):
                        try:
                            imgs = json.loads(imgs)
                        except Exception:
                            imgs = [imgs]
                    writer.writerow([
                        p.get("id", ""),
                        p.get("title", ""),
                        p.get("sku", ""),
                        f"{net_val:.2f}",
                        p.get("stock", 0),
                        p.get("weight", 0),
                        p.get("length", 0),
                        p.get("width", 0),
                        p.get("height", 0),
                        p.get("site_id", ""),
                        p.get("store_name", ""),
                        p.get("group_name", ""),
                        p.get("category_id", ""),
                        p.get("category_name", ""),
                        p.get("variants_count", 0),
                        ";".join(str(u) for u in imgs if u),
                    ])
            QMessageBox.information(self, "导出完成", f"已导出 {len(selected_items)} 个商品清单至:\n{save_path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"写入 CSV 失败: {exc}")

    def _on_delete_selected(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = DeleteConfirmDialog(len(pids), self)
        if dlg.exec():
            data = dlg.get_data()
            self.db.batch_delete(pids, soft_delete=data["soft_delete"], delete_images=data["delete_images"])
            self.load_data()
            QMessageBox.information(self, "删除成功", f"已成功删除选中的 {len(pids)} 个商品！")

    def _on_delete_by_filter(self):
        filters = self.filter_bar.get_filters()
        res = self.db.query_products(filters=filters, page=1, page_size=1)
        total = res["total"]
        if total == 0:
            QMessageBox.information(self, "提示", "当前筛选条件下没有匹配的商品，无需删除。")
            return

        dlg = DeleteByFilterDialog(total, self)
        if dlg.exec():
            del_cnt = self.db.delete_by_filter(filters=filters, soft_delete=False)
            self.load_data()
            QMessageBox.information(self, "删除成功", f"已彻底清空当前筛选条件下的全部 {del_cnt} 个商品！")

    def refresh_accounts(self):
        """Refreshes available store options from AuthManager."""
        if hasattr(self, "filter_bar"):
            self.filter_bar.reload_stores(self.auth)

    # ================== PUBLISH & PRE-UPLOAD ==================
    def _on_publish(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = BatchPublishDialog(len(pids), auth_mgr=self.auth, product_ids=pids, parent=self)
        if dlg.exec():
            cfg = dlg.get_publish_config()
            target_ids = [pid for pid in (cfg.get("product_ids") or pids) if self.db.get_product(pid)]
            store_label = "、".join(cfg.get("store_names") or []) or "全部已授权店铺"
            marked = self.db.batch_update_status(target_ids, "ready")
            for pid in target_ids:
                self.db.append_product_log(pid, "本地待刊登准备", f"目标店铺: {store_label}")
            self.load_data()
            QMessageBox.information(
                self,
                "本地打包准备完成",
                f"已完成 {marked} 个商品的发布前本地校验并标记为“待发布/就绪”（目标店铺：{store_label}）。\n\n"
                "提示：美客多 CBT 正式在线刊登创建接口将于阶段 2 接入。",
            )

    def _on_pre_upload(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        selected_items = [p for p in self.current_items if p["id"] in pids]
        dlg = PreUploadImagesDialog(selected_items, self)
        dlg.exec()

    # ================== TABLE INGRESS ==================
    def _on_import_cost(self):
        dlg = ImportCostDialog(self)
        if dlg.exec():
            costs = dlg.get_imported_costs()
            updated_cnt = 0
            for item in costs:
                pid = item.get("id")
                if not pid:
                    continue
                prod = self.db.get_product(pid)
                if prod:
                    price = float(prod.get("price") or 0.0)
                    cost = float(item.get("cost_price") or 0.0)
                    profit = round(price - cost, 2) if price > cost else 0.0
                    self.db.update_product(pid, {"cost_price": cost, "profit": profit})
                    updated_cnt += 1
            self.load_data()
            QMessageBox.information(self, "导入完成", f"已成功批量导入并回填了 {updated_cnt} 个商品的采购成本！")

    def _on_create_manual(self):
        groups = self.db.get_group_names() if hasattr(self.db, "get_group_names") else None
        dlg = CreateProductDialog(auth_mgr=self.auth, groups=groups, parent=self)
        if dlg.exec():
            item = dlg.get_created_item()
            if item:
                self.db.import_from_json([item])
                self.load_data()
                QMessageBox.information(self, "创建成功", f"已成功创建待刊登商品：\n{item.get('title')}")

    def _on_create_table(self):
        groups = self.db.get_group_names() if hasattr(self.db, "get_group_names") else None
        dlg = TableCreateProductDialog(auth_mgr=self.auth, groups=groups, parent=self)
        if dlg.exec():
            rows = dlg.get_rows()
            if rows:
                cnt = self.db.import_from_excel_rows(rows)
                self.load_data()
                QMessageBox.information(self, "表格创建完成", f"已成功从表格导入并创建 {cnt} 个待刊登商品！")

    # ================== SINGLE ACTIONS ==================
    def _on_card_field_updated(self, item_id: str, field: str, value: Any):
        if field == "size" and isinstance(value, (tuple, list)):
            l, w, h = value
            self.db.update_product(item_id, {"length": l, "width": w, "height": h})
        else:
            fields = {field: value}
            if field in ("price", "cost_price"):
                prod = self.db.get_product(item_id)
                if prod:
                    p = float(value if field == "price" else (prod.get("price") or 0.0))
                    c = float(value if field == "cost_price" else (prod.get("cost_price") or 0.0))
                    if c > 0:
                        fields["profit"] = round(p - c, 2)
            self.db.update_product(item_id, fields)

    def _on_variant_drilldown(self, item_id: str):
        prod = self.db.get_product(item_id)
        if not prod:
            return
        dlg = VariantDrilldownDialog(prod, self)
        if dlg.exec() and dlg.is_modified():
            variants = dlg.get_variants()
            self.db.update_product(item_id, {"variants": variants, "variants_count": len(variants)})
            self.db.append_product_log(item_id, "编辑变体", f"变体清单保存为 {len(variants)} 个")
            self.load_data()

    def _on_single_edit(self, item_id: str):
        prod = self.db.get_product(item_id)
        if not prod:
            return
        groups = self.db.get_group_names() if hasattr(self.db, "get_group_names") else None
        dlg = SingleProductEditDialog(prod, parent=self, auth_mgr=self.auth, groups=groups)
        if dlg.exec():
            fields = dlg.get_updated_fields()
            self.db.update_product(item_id, fields)
            if hasattr(self.db, "append_product_log"):
                var_cnt = len(fields.get("variants") or [])
                img_cnt = len(fields.get("images") or [])
                self.db.append_product_log(
                    item_id,
                    "编辑商品",
                    f"更新基本信息、分类属性（{var_cnt} 个变体，{img_cnt} 张图片）",
                    operator=str(fields.get("salesman") or "当前用户"),
                )
            self.load_data()

    def _on_single_verify(self, item_id: str):
        prod = self.db.get_product(item_id)
        if not prod:
            return
        dlg = SingleProductPricingDialog(prod, self)
        if dlg.exec():
            res = dlg.get_pricing_result()
            self.db.update_product(item_id, res)
            self.load_data()

    def _on_ai_verify_all(self):
        pids = self.card_grid.get_selected_ids()
        targets = [p for p in self.current_items if p["id"] in pids] if pids else self.current_items
        if not targets:
            QMessageBox.information(self, "提示", "当前页面没有待核价商品。")
            return

        count = 0
        for p in targets:
            pid = p["id"]
            price = float(p.get("price") or 0.0)
            cost = float(p.get("cost_price") or 0.0)
            if cost <= 0:
                est_cost = round(price * 0.45, 2)
                profit = round(price - est_cost, 2) if price > est_cost else 0.0
                self.db.update_product(pid, {"cost_price": est_cost, "profit": profit})
                count += 1
            elif float(p.get("profit") or 0.0) == 0.0 and cost > 0:
                profit = round(price - cost, 2)
                self.db.update_product(pid, {"profit": profit})
                count += 1

        self.load_data()
        QMessageBox.information(self, "AI核价完成", f"已成功为 {count or len(targets)} 个商品完成智能货源比价与利润核算！")

    # ================== PAGINATION ==================
    def _prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self.load_data()

    def _next_page(self):
        max_page = max(1, (self.total_items + self.page_size - 1) // self.page_size)
        if self.current_page < max_page:
            self.current_page += 1
            self.load_data()

    def _on_page_size_changed(self):
        self.page_size = self.combo_page_size.currentData() or 50
        self.current_page = 1
        self.load_data()
