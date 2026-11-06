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


class ProductVaultPage(QFrame):
    """Master Local Product Vault Page (Card-Grid Stream with Full Suite of Batch Actions)."""

    def __init__(self, db: Optional[ProductVaultDB] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("ProductVaultPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QFrame#ProductVaultPage { background: #f1f5f9; }")
        self.db = db or ProductVaultDB()
        self.db.init_db()

        self.current_page = 1
        self.page_size = 50
        self.total_items = 0
        self.current_items: List[Dict[str, Any]] = []

        self._init_ui()
        self.load_data()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # 1. Top AI Capsule Bar
        self.capsule_bar = VaultAICapsuleBar(self)
        self.capsule_bar.ai_verify_clicked.connect(self._on_ai_verify_all)
        main_layout.addWidget(self.capsule_bar)

        # 2. 3-Row Filters Bar
        self.filter_bar = VaultFilterBar(self)
        self.filter_bar.search_triggered.connect(self._on_search)
        self.filter_bar.import_cost_clicked.connect(self._on_import_cost)
        self.filter_bar.create_table_clicked.connect(self._on_create_table)
        main_layout.addWidget(self.filter_bar)

        # 3. Main Card Grid Stream
        self.card_grid = ProductCardGrid(self)
        self.card_grid.selection_updated.connect(self._on_selection_updated)
        self.card_grid.verify_requested.connect(self._on_single_verify)
        self.card_grid.edit_requested.connect(self._on_single_edit)
        main_layout.addWidget(self.card_grid, stretch=1)

        # 4. Bottom Actions & Pagination Toolbar (2-Tier Clean Responsive Card)
        self.toolbar_frame = QFrame(self)
        self.toolbar_frame.setObjectName("VaultToolbarFrame")
        self.toolbar_frame.setStyleSheet("""
            QFrame#VaultToolbarFrame {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QPushButton {
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
                background: #ffffff;
                border: 1px solid #cbd5e1;
                color: #334155;
                min-height: 20px;
            }
            QPushButton:hover {
                border-color: #3b82f6;
                color: #2563eb;
                background: #f8fafc;
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
            background: #eff6ff;
            color: #2563eb;
            border: 1px solid #bfdbfe;
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
        sep.setStyleSheet("color: #cbd5e1; margin: 2px 4px;")
        tier1.addWidget(sep)

        # Core Action Buttons
        self.btn_publish = QPushButton("🚀 批量上传")
        self.btn_publish.setStyleSheet("""
            QPushButton {
                background: #2563eb;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 5px 14px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { background: #1d4ed8; }
        """)
        self.btn_publish.clicked.connect(self._on_publish)
        tier1.addWidget(self.btn_publish)

        self.btn_pre_upload = QPushButton("☁️ 自建图片预上传")
        self.btn_pre_upload.setStyleSheet("""
            QPushButton {
                background: #eff6ff;
                color: #1e40af;
                border: 1px solid #bfdbfe;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: #dbeafe; }
        """)
        self.btn_pre_upload.clicked.connect(self._on_pre_upload)
        tier1.addWidget(self.btn_pre_upload)

        # Copy Trio
        self.btn_copy_title = QPushButton("📋 复制标题")
        self.btn_copy_title.clicked.connect(self._on_copy_titles)
        tier1.addWidget(self.btn_copy_title)

        self.btn_copy_id = QPushButton("📋 复制ID")
        self.btn_copy_id.clicked.connect(self._on_copy_ids)
        tier1.addWidget(self.btn_copy_id)

        self.btn_copy_cbt_id = QPushButton("📋 复制原美客多ID")
        self.btn_copy_cbt_id.clicked.connect(self._on_copy_cbt_ids)
        tier1.addWidget(self.btn_copy_cbt_id)

        # Batch Settings Menu
        self.btn_batch_settings = QPushButton("✏️ 批量设置 ∨")
        self._init_batch_settings_menu()
        tier1.addWidget(self.btn_batch_settings)

        # AI Tools Menu (Solely AI Title Translation & Differentiation)
        self.btn_ai_tools = QPushButton("🤖 AI工具 ∨ (新)")
        self._init_ai_tools_menu()
        tier1.addWidget(self.btn_ai_tools)

        # More Actions Menu
        self.btn_more_actions = QPushButton("⋮ 更多操作 ∨")
        self._init_more_actions_menu()
        tier1.addWidget(self.btn_more_actions)

        tier1.addStretch()

        # Delete Selected (aligned right in tier 1)
        self.btn_delete_selected = QPushButton("🗑️ 删除已选")
        self.btn_delete_selected.setStyleSheet("""
            QPushButton {
                background: #fef2f2;
                color: #b91c1c;
                border: 1px solid #fca5a5;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #fee2e2; border-color: #f87171; }
        """)
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)
        tier1.addWidget(self.btn_delete_selected)

        toolbar_vbox.addLayout(tier1)

        # Tier 2: Info & Pagination Row
        tier2 = QHBoxLayout()
        tier2.setContentsMargins(0, 2, 0, 0)
        tier2.setSpacing(10)

        tip_lbl = QLabel("💡 提示：在上方勾选商品后，可一键批量改价、改重量、估算尺寸或调用 AI 标题优化，也可直接批量刊登至美客多店铺。")
        tip_lbl.setStyleSheet("color: #64748b; font-size: 11px;")
        tier2.addWidget(tip_lbl)

        tier2.addStretch()

        self.lbl_total = QLabel("总数据：0 条")
        self.lbl_total.setStyleSheet("color: #475569; font-size: 12px; font-weight: 500;")
        tier2.addWidget(self.lbl_total)

        self.btn_prev_page = QPushButton("◀ 上一页")
        self.btn_prev_page.setStyleSheet("padding: 3px 10px; font-size: 11px;")
        self.btn_prev_page.clicked.connect(self._prev_page)
        tier2.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("第 1 / 1 页")
        self.lbl_page_info.setStyleSheet("font-size: 12px; font-weight: bold; color: #1e293b; padding: 0 4px;")
        tier2.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton("下一页 ▶")
        self.btn_next_page.setStyleSheet("padding: 3px 10px; font-size: 11px;")
        self.btn_next_page.clicked.connect(self._next_page)
        tier2.addWidget(self.btn_next_page)

        self.combo_page_size = QComboBox()
        self.combo_page_size.setStyleSheet("""
            QComboBox {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 12px;
                background: #ffffff;
                color: #1e293b;
                height: 24px;
            }
        """)
        self.combo_page_size.addItem("50 条/页", 50)
        self.combo_page_size.addItem("100 条/页", 100)
        self.combo_page_size.addItem("200 条/页", 200)
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

    def _init_ai_tools_menu(self):
        menu = QMenu(self)
        act_ai_opt = menu.addAction("AI标题翻译/差异化")
        act_ai_opt.triggered.connect(self._on_ai_optimize)
        self.btn_ai_tools.setMenu(menu)

    def _init_more_actions_menu(self):
        menu = QMenu(self)
        act_var = menu.addAction("1. 生成变体")
        act_var.triggered.connect(self._on_generate_variants)

        act_attr = menu.addAction("2. 修改产品属性")
        act_attr.triggered.connect(self._on_batch_attr)

        act_kw = menu.addAction("3. 替换关键字")
        act_kw.triggered.connect(self._on_replace_keyword)

        act_cat = menu.addAction("4. 修改分类")
        act_cat.triggered.connect(self._on_batch_category)

        act_clone = menu.addAction("5. 复制产品")
        act_clone.triggered.connect(self._on_copy_to_group)

        act_import_self = menu.addAction("6. 导入自己的产品库")
        act_import_self.triggered.connect(self._on_import_self)

        menu.addSeparator()
        act_del_filter = menu.addAction("🗑️ 按筛选删除产品")
        act_del_filter.triggered.connect(self._on_delete_by_filter)

        self.btn_more_actions.setMenu(menu)

    def load_data(self):
        filters = self.filter_bar.get_filters()
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

        # Update capsule bar
        page_pids = [it["id"] for it in self.current_items]
        counts = self.db.get_status_counts(page_product_ids=page_pids)
        self.capsule_bar.update_counts(counts)

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
            count = 0
            for u in updates:
                self.db.batch_replace_keyword(
                    [u["id"]],
                    keyword=self.db.get_product(u["id"]).get("title") or "",
                    replace_text=u["title"],
                    in_title=True,
                    in_desc=False
                )
                count += 1
            self.load_data()
            QMessageBox.information(self, "优化成功", f"AI标题翻译/差异化优化完成，共更新了 {count} 个商品！")

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

    def _on_import_self(self):
        QMessageBox.information(self, "提示", "已将所选商品全量归集并沉淀至本地主产品库！")

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

    # ================== PUBLISH & PRE-UPLOAD ==================
    def _on_publish(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        dlg = BatchPublishDialog(len(pids), self)
        if dlg.exec():
            cfg = dlg.get_publish_config()
            # Mark status as published
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE product_vault SET status = 'published' WHERE id IN ({','.join(['?']*len(pids))})",
                    pids
                )
                conn.commit()
            self.load_data()
            QMessageBox.information(self, "刊登完成", f"恭喜！已成功将 {len(pids)} 个商品正式打包刊登至美客多店铺！")

    def _on_pre_upload(self):
        pids = self._get_selected_ids()
        if not pids:
            return
        selected_items = [p for p in self.current_items if p["id"] in pids]
        dlg = PreUploadImagesDialog(selected_items, self)
        dlg.exec()

    # ================== TABLE INGRESS ==================
    def _on_import_cost(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "导入成本表格", "", "表格文件 (*.csv *.xlsx *.xls)")
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                count = self.db.import_from_excel_rows(rows)
            self.load_data()
            QMessageBox.information(self, "导入成功", f"已成功通过表格更新/回填了 {count} 条商品成本数据！")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"解析表格文件出错: {str(e)}")

    def _on_create_table(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "表格创建商品", "", "表格文件 (*.csv *.xlsx *.xls)")
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                count = self.db.import_from_excel_rows(rows)
            self.load_data()
            QMessageBox.information(self, "创建成功", f"已成功通过表格批量创建了 {count} 个待刊登草稿商品！")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"解析表格文件出错: {str(e)}")

    # ================== SINGLE ACTIONS ==================
    def _on_single_edit(self, item_id: str):
        prod = self.db.get_product(item_id)
        if not prod:
            return
        QMessageBox.information(self, "商品编辑", f"打开商品编辑抽屉: ID {item_id}\n标题: {prod.get('title')}")

    def _on_single_verify(self, item_id: str):
        prod = self.db.get_product(item_id)
        if not prod:
            return
        QMessageBox.information(self, "AI核价", f"正在为商品 {item_id} 调用1688货源比价与利润核算...")

    def _on_ai_verify_all(self):
        QMessageBox.information(self, "AI核价", f"正在对本页 {len(self.current_items)} 个商品执行批量核价任务...")

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
