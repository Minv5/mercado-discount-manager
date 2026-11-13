from __future__ import annotations

import csv
import json
from typing import Any, Dict, List, Optional
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui_common.image_cache import get_placeholder_pixmap, load_image_async
from items.vault_pricing import net_proceeds
from items.vault_product_editor import (
    COUNTRY_STATION_MAP,
    CreateProductDialog,
    ProductEditorDialog,
    SingleProductEditDialog,
    TableCreateProductDialog,
)

__all__ = [
    "COUNTRY_STATION_MAP",
    "ProductEditorDialog",
    "CreateProductDialog",
    "SingleProductEditDialog",
    "TableCreateProductDialog",
    "VariantDrilldownDialog",
    "SingleProductPricingDialog",
    "ImportCostDialog",
]


class VariantDrilldownDialog(QDialog):
    """Fast drill-down dialog displaying real SKU variants for an individual product."""

    def __init__(self, product: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.product = dict(product)
        self.item_id = str(product.get("id") or "")
        self.setWindowTitle(f"SKU 变体清单 - {self.item_id}")
        self.resize(740, 480)
        self.setStyleSheet("""
            QDialog {
                background: #171B19;
            }
            QLabel {
                color: #E6E2D8;
            }
            QTableWidget {
                background: #18201C;
                border: 1px solid #4E472F;
                border-radius: 6px;
                gridline-color: #2E362F;
                font-size: 12px;
                color: #E6E2D8;
            }
            QTableWidget::item {
                padding: 4px;
                color: #E6E2D8;
            }
            QHeaderView::section {
                background: #1C231F;
                color: #C8C3B7;
                font-weight: 600;
                font-size: 11px;
                border: none;
                border-bottom: 1px solid #4E472F;
                padding: 6px;
            }
        """)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        hdr_frame = QFrame()
        hdr_frame.setObjectName("VaultDrillHeader")
        hdr_frame.setStyleSheet("QFrame#VaultDrillHeader { background: #202820; border: 1px solid #4E472F; border-radius: 8px; padding: 10px; }")
        hdr_layout = QHBoxLayout(hdr_frame)
        hdr_layout.setContentsMargins(8, 8, 8, 8)
        hdr_layout.setSpacing(12)

        lbl_thumb = QLabel()
        lbl_thumb.setFixedSize(56, 56)
        lbl_thumb.setStyleSheet("background: #18201C; border-radius: 6px;")
        lbl_thumb.setScaledContents(True)
        images = self.product.get("images") or []
        thumb_url = images[0] if (isinstance(images, list) and images) else ""
        if thumb_url and thumb_url.startswith(("http://", "https://")):
            load_image_async(
                thumb_url,
                QSize(56, 56),
                on_loaded=lambda pm: lbl_thumb.setPixmap(pm) if not pm.isNull() else None,
                radius=6,
            )
        else:
            lbl_thumb.setPixmap(get_placeholder_pixmap(QSize(56, 56), "📦", radius=6))
        hdr_layout.addWidget(lbl_thumb)

        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        lbl_t = QLabel(str(self.product.get("title") or "未命名商品"))
        lbl_t.setStyleSheet("font-weight: 600; font-size: 13px; color: #F6F3EA;")
        lbl_t.setWordWrap(True)
        info_box.addWidget(lbl_t)

        sub_line = QHBoxLayout()
        lbl_sub_id = QLabel(f"商品ID: {self.item_id}")
        lbl_sub_id.setStyleSheet("color: #60A5FA;")
        sub_line.addWidget(lbl_sub_id)
        sub_line.addSpacing(16)
        net_v = net_proceeds(self.product)
        lbl_sub_p = QLabel(f"默认净收益: $ {net_v:.2f}")
        lbl_sub_p.setStyleSheet("color: #E6E2D8;")
        sub_line.addWidget(lbl_sub_p)
        sub_line.addSpacing(16)
        var_count = int(self.product.get("variants_count") or 0)
        lbl_sub_vc = QLabel(f"共 {var_count} 个规格变体")
        lbl_sub_vc.setStyleSheet("color: #A39E93;")
        sub_line.addWidget(lbl_sub_vc)
        sub_line.addStretch()
        info_box.addLayout(sub_line)
        hdr_layout.addLayout(info_box, stretch=1)

        layout.addWidget(hdr_frame)

        self.table = QTableWidget()
        self.table.verticalHeader().setDefaultSectionSize(38)
        headers = ["变体规格", "子SKU编码", "净收益 (USD)", "库存", "状态", "操作"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for ci in range(1, len(headers)):
            self.table.horizontalHeader().setSectionResizeMode(ci, QHeaderView.ResizeMode.ResizeToContents)

        self._modified = False
        self._populate_variants_table()
        self.table.itemChanged.connect(self._mark_modified)
        layout.addWidget(self.table, stretch=1)

        btn_box = QHBoxLayout()
        btn_add = QPushButton("+ 添加变体规格")
        btn_add.setStyleSheet("background: #2F6B3F; color: #ffffff; border: none; border-radius: 6px; padding: 6px 14px; font-weight: 600;")
        btn_add.clicked.connect(self._add_variant_row)
        btn_box.addWidget(btn_add)

        btn_box.addStretch()
        btn_close = QPushButton("取消")
        btn_close.setStyleSheet("background: #18201C; color: #C8C3B7; border: 1px solid #4E472F; border-radius: 6px; padding: 6px 16px; font-weight: 500;")
        btn_close.clicked.connect(self.reject)
        btn_box.addWidget(btn_close)
        btn_save = QPushButton("保存")
        btn_save.setStyleSheet("background: #2F6B3F; color: #ffffff; border: none; border-radius: 6px; padding: 6px 18px; font-weight: 600;")
        btn_save.clicked.connect(self.accept)
        btn_box.addWidget(btn_save)
        layout.addLayout(btn_box)

    def _populate_variants_table(self):
        raw_variants = self.product.get("variants") or []
        if isinstance(raw_variants, str):
            try:
                variants = json.loads(raw_variants)
            except Exception:
                variants = []
        else:
            variants = list(raw_variants)

        self.table.setRowCount(len(variants))
        for row, v in enumerate(variants):
            name = str(v.get("kvId") or v.get("name") or v.get("spec") or f"规格 {row+1}")
            sku = str(v.get("sku") or f"{self.item_id}-{row+1:02d}")
            net_v = float(v.get("netProceed") if v.get("netProceed") is not None else (v.get("profit") if v.get("profit") is not None else (v.get("price") or net_proceeds(self.product))))
            stock = int(v.get("stock") if v.get("stock") is not None else 50)
            status = str(v.get("status") or "就绪")

            item_name = QTableWidgetItem(name)
            item_name.setData(Qt.ItemDataRole.UserRole, dict(v))
            item_sku = QTableWidgetItem(sku)
            item_price = QTableWidgetItem(f"{net_v:.2f}")
            item_stock = QTableWidgetItem(str(stock))
            item_status = QTableWidgetItem(status)

            item_price.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_stock.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table.setItem(row, 0, item_name)
            self.table.setItem(row, 1, item_sku)
            self.table.setItem(row, 2, item_price)
            self.table.setItem(row, 3, item_stock)
            self.table.setItem(row, 4, item_status)

            btn_del = QPushButton("删除")
            btn_del.setStyleSheet("color: #ef4444; background: transparent; border: none; font-size: 11px;")
            btn_del.clicked.connect(self._remove_button_row)
            self.table.setCellWidget(row, 5, btn_del)

    def _mark_modified(self, *_args) -> None:
        self._modified = True

    def is_modified(self) -> bool:
        return self._modified

    @staticmethod
    def _cell_number(item: Optional[QTableWidgetItem], fallback: float) -> float:
        try:
            return float(str(item.text()).strip()) if item else fallback
        except ValueError:
            return fallback

    def get_variants(self) -> List[Dict[str, Any]]:
        """Rows as shown, layered over each variant's original fields (images, size, ...)."""
        out: List[Dict[str, Any]] = []
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, 0)
            original = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
            variant = dict(original) if isinstance(original, dict) else {}
            name = name_item.text().strip() if name_item else ""
            net_v = round(self._cell_number(self.table.item(r, 2), 0.0), 2)
            variant.update({
                "kvId": name,
                "name": name,
                "sku": self.table.item(r, 1).text().strip() if self.table.item(r, 1) else "",
                "price": net_v,
                "netProceed": net_v,
                "profit": net_v,
                "stock": int(self._cell_number(self.table.item(r, 3), 0.0)),
                "status": self.table.item(r, 4).text().strip() if self.table.item(r, 4) else "就绪",
            })
            out.append(variant)
        return out

    def _remove_button_row(self):
        sender = self.sender()
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 5) is sender:
                self.table.removeRow(r)
                self._mark_modified()
                return

    def _add_variant_row(self):
        text, ok = QInputDialog.getText(self, "新增变体规格", "请输入规格名称 (如: 红色_XL):")
        if ok and text.strip():
            row = self.table.rowCount()
            self.table.insertRow(row)
            base_p = net_proceeds(self.product)
            base_sku = str(self.product.get("sku") or self.item_id[:8])

            self.table.setItem(row, 0, QTableWidgetItem(text.strip()))
            self.table.setItem(row, 1, QTableWidgetItem(f"{base_sku}-{row+1:02d}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{base_p:.2f}"))
            self.table.setItem(row, 3, QTableWidgetItem("50"))
            self.table.setItem(row, 4, QTableWidgetItem("就绪"))

            btn_del = QPushButton("删除")
            btn_del.setStyleSheet("color: #ef4444; background: transparent; border: none; font-size: 11px;")
            btn_del.clicked.connect(self._remove_button_row)
            self.table.setCellWidget(row, 5, btn_del)
            self._mark_modified()


class SingleProductPricingDialog(QDialog):
    """Dedicated AI & Formula Pricing dialog for a single product."""

    def __init__(self, product: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.product = dict(product)
        self.setWindowTitle(f"AI 智能核价台 - {self.product.get('id', '')}")
        self.setFixedWidth(500)
        self.setStyleSheet("""
            QDialog {
                background: #171B19;
            }
            QLabel {
                font-size: 12px;
                color: #C8C3B7;
            }
            QLineEdit, QDoubleSpinBox {
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                background: #18201C;
                color: #E6E2D8;
            }
            QLineEdit:focus, QDoubleSpinBox:focus {
                border-color: #2F6B3F;
                background: #1C231F;
            }
        """)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("VaultPriceHeader")
        card.setStyleSheet("QFrame#VaultPriceHeader { background: #202820; border: 1px solid #4E472F; border-radius: 8px; padding: 10px; }")
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(4)

        lbl_title = QLabel(str(self.product.get("title") or ""))
        lbl_title.setWordWrap(True)
        lbl_title.setStyleSheet("font-weight: 600; font-size: 12px; color: #F6F3EA;")
        card_layout.addWidget(lbl_title)

        sub_info = QHBoxLayout()
        sub_info.addWidget(QLabel(f"ID: {self.product.get('id', '')}"))
        sub_info.addStretch()
        cur_price = float(self.product.get("price") or 0.0)
        lbl_p = QLabel(f"当前售价: ${cur_price:.2f}")
        lbl_p.setStyleSheet("font-weight: bold; color: #60A5FA;")
        sub_info.addWidget(lbl_p)
        card_layout.addLayout(sub_info)
        layout.addWidget(card)

        calc_box = QFrame()
        calc_box.setObjectName("VaultPriceCalc")
        calc_box.setStyleSheet("QFrame#VaultPriceCalc { background: #202820; border: 1px solid #4E472F; border-radius: 8px; padding: 12px; }")
        form = QFormLayout(calc_box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        cny_box = QHBoxLayout()
        self.spn_cny = QDoubleSpinBox()
        self.spn_cny.setRange(0.0, 99999.0)
        self.spn_cny.setDecimals(2)
        self.spn_cny.setPrefix("¥ ")
        self.spn_cny.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_cny.valueChanged.connect(self._on_cny_changed)
        cny_box.addWidget(self.spn_cny)

        btn_ai_suggest = QPushButton("1688推荐采购比")
        btn_ai_suggest.setStyleSheet("""
            QPushButton {
                background: rgba(47, 107, 63, 0.35);
                color: #4ADE80;
                border: 1px solid #2F6B3F;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover { background: rgba(47, 107, 63, 0.55); }
        """)
        btn_ai_suggest.clicked.connect(self._fill_1688_estimate)
        cny_box.addWidget(btn_ai_suggest)
        form.addRow("1688采购价 (RMB):", cny_box)

        self.spn_rate = QDoubleSpinBox()
        self.spn_rate.setRange(1.0, 20.0)
        self.spn_rate.setDecimals(2)
        self.spn_rate.setValue(7.23)
        self.spn_rate.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_rate.valueChanged.connect(self._on_cny_changed)
        form.addRow("美元汇率:", self.spn_rate)

        self.spn_cost_usd = QDoubleSpinBox()
        self.spn_cost_usd.setRange(0.0, 9999.0)
        self.spn_cost_usd.setDecimals(2)
        self.spn_cost_usd.setPrefix("$ ")
        self.spn_cost_usd.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        cur_cost = float(self.product.get("cost_price") or 0.0)
        self.spn_cost_usd.setValue(cur_cost)
        self.spn_cost_usd.valueChanged.connect(self._recalc_net_profit)
        form.addRow("折合成本 (USD):", self.spn_cost_usd)

        self.spn_comm = QDoubleSpinBox()
        self.spn_comm.setRange(0.0, 50.0)
        self.spn_comm.setValue(16.0)
        self.spn_comm.setSuffix(" %")
        self.spn_comm.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spn_comm.valueChanged.connect(self._recalc_net_profit)
        form.addRow("平台佣金率:", self.spn_comm)
        layout.addWidget(calc_box)

        self.res_card = QFrame()
        self.res_card.setObjectName("VaultPriceResult")
        self.res_card.setStyleSheet("QFrame#VaultPriceResult { background: rgba(47, 107, 63, 0.2); border: 1px solid #2F6B3F; border-radius: 8px; padding: 12px; }")
        res_layout = QGridLayout(self.res_card)
        res_layout.setContentsMargins(6, 6, 6, 6)
        res_layout.setSpacing(8)

        res_layout.addWidget(QLabel("预估回款:"), 0, 0)
        self.lbl_payout = QLabel("$ 0.00")
        self.lbl_payout.setStyleSheet("font-weight: bold; color: #E6E2D8;")
        res_layout.addWidget(self.lbl_payout, 0, 1)

        res_layout.addWidget(QLabel("纯利润:"), 1, 0)
        self.lbl_net_profit = QLabel("$ 0.00")
        self.lbl_net_profit.setStyleSheet("font-weight: bold; font-size: 14px; color: #4ADE80;")
        res_layout.addWidget(self.lbl_net_profit, 1, 1)

        res_layout.addWidget(QLabel("利润率:"), 1, 2)
        self.lbl_margin = QLabel("0.0%")
        self.lbl_margin.setStyleSheet("font-weight: bold; color: #4ADE80;")
        res_layout.addWidget(self.lbl_margin, 1, 3)
        layout.addWidget(self.res_card)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet("""
            QPushButton {
                background: #18201C;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 6px 16px;
            }
            QPushButton:hover { background: #252A25; border-color: #6E5D2A; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_apply = QPushButton("保存核价结果")
        btn_apply.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 20px;
                font-weight: 600;
            }
            QPushButton:hover { background: #356F45; }
        """)
        btn_apply.clicked.connect(self.accept)
        btn_box.addWidget(btn_apply)
        layout.addLayout(btn_box)

        self._recalc_net_profit()

    def _fill_1688_estimate(self):
        price = float(self.product.get("price") or 0.0)
        est_usd = round(price * 0.42, 2)
        rate = self.spn_rate.value()
        self.spn_cny.setValue(round(est_usd * rate, 2))

    def _on_cny_changed(self):
        cny = self.spn_cny.value()
        rate = self.spn_rate.value()
        if rate > 0:
            self.spn_cost_usd.setValue(round(cny / rate, 2))

    def _recalc_net_profit(self):
        price = float(self.product.get("price") or 0.0)
        cost = self.spn_cost_usd.value()
        comm_rate = self.spn_comm.value() / 100.0
        payout = max(0.0, round(price * (1 - comm_rate), 2))
        profit = round(payout - cost, 2) if cost > 0 else 0.0
        margin = round((profit / price) * 100, 1) if price > 0 and cost > 0 else 0.0

        self.lbl_payout.setText(f"${payout:.2f}")
        color = "#16a34a" if profit > 0 else "#dc2626"
        self.lbl_net_profit.setText(f"${profit:.2f}")
        self.lbl_net_profit.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {color};")
        self.lbl_margin.setText(f"{margin:.1f}%")
        self.lbl_margin.setStyleSheet(f"font-weight: bold; color: {color};")

    def get_pricing_result(self) -> Dict[str, float]:
        cost = self.spn_cost_usd.value()
        price = float(self.product.get("price") or 0.0)
        profit = round(price - cost, 2) if cost > 0 else 0.0
        return {"cost_price": cost, "profit": profit}


class ImportCostDialog(QDialog):
    """Dialog to import costs via file or fast clipboard paste."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("批量导入商品成本")
        self.setFixedWidth(540)
        self.setStyleSheet("""
            QDialog {
                background: #171B19;
            }
            QLabel {
                font-size: 12px;
                color: #C8C3B7;
            }
            QTextEdit {
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 6px;
                font-size: 12px;
                background: #18201C;
                color: #E6E2D8;
                font-family: monospace;
            }
        """)
        self.imported_data: List[Dict[str, Any]] = []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        desc = QLabel("支持从本地 CSV/Excel 表格导入成本，或直接从表格复制并粘贴到下方快捷输入框：")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        file_box = QHBoxLayout()
        btn_select_file = QPushButton("📁 选择表格文件导入 (CSV/Excel)")
        btn_select_file.setStyleSheet("""
            QPushButton {
                background: rgba(47, 107, 63, 0.35);
                color: #4ADE80;
                border: 1px solid #2F6B3F;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
            }
            QPushButton:hover { background: rgba(47, 107, 63, 0.55); }
        """)
        btn_select_file.clicked.connect(self._select_file)
        file_box.addWidget(btn_select_file)

        btn_template = QPushButton("下载模板")
        btn_template.setStyleSheet("""
            QPushButton {
                background: #18201C;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover { background: #252A25; border-color: #6E5D2A; }
        """)
        btn_template.clicked.connect(self._download_template)
        file_box.addWidget(btn_template)
        layout.addLayout(file_box)

        layout.addWidget(QLabel("或者直接粘贴数据（格式：商品ID [空格/逗号/Tab] 成本USD）："))
        self.txt_paste = QTextEdit()
        self.txt_paste.setPlaceholderText("例如：\nPR02026102 19.6\nMCO4134278806 5.80\nMCO4134240610 6.30")
        self.txt_paste.setFixedHeight(140)
        layout.addWidget(self.txt_paste)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet("""
            QPushButton {
                background: #18201C;
                color: #C8C3B7;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 6px 16px;
            }
            QPushButton:hover { background: #252A25; border-color: #6E5D2A; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_confirm = QPushButton("开始导入并回填")
        btn_confirm.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 20px;
                font-weight: 600;
            }
            QPushButton:hover { background: #356F45; }
        """)
        btn_confirm.clicked.connect(self._confirm_import)
        btn_box.addWidget(btn_confirm)

        layout.addLayout(btn_box)

    def _select_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择成本表格", "", "表格文件 (*.csv *.tsv *.txt)")
        if not file_path:
            return
        try:
            items = []
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 2:
                        p_id = row[0].strip()
                        try:
                            cost = float(row[1].strip())
                            items.append({"id": p_id, "cost_price": cost})
                        except ValueError:
                            continue
            if items:
                self.imported_data = items
                QMessageBox.information(self, "读取成功", f"从表格解析到 {len(items)} 条商品成本数据！点击下方按钮完成回填。")
            else:
                QMessageBox.warning(self, "提示", "未在文件中找到有效的 [商品ID, 成本] 数据行。")
        except Exception as e:
            QMessageBox.critical(self, "读取失败", f"无法读取表格: {str(e)}")

    def _download_template(self):
        save_path, _ = QFileDialog.getSaveFileName(self, "保存成本导入模板", "成本导入模板.csv", "CSV 文件 (*.csv)")
        if not save_path:
            return
        try:
            with open(save_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["商品ID", "成本USD"])
                writer.writerow(["PR02026102", "19.60"])
                writer.writerow(["MCO4134278806", "5.80"])
            QMessageBox.information(self, "保存成功", f"模板已保存至:\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法写入文件: {str(e)}")

    def _confirm_import(self):
        paste_text = self.txt_paste.toPlainText().strip()
        if paste_text:
            for line in paste_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.replace(",", " ").replace("\t", " ").split()
                if len(parts) >= 2:
                    p_id = parts[0].strip()
                    try:
                        cost = float(parts[1].strip())
                        self.imported_data.append({"id": p_id, "cost_price": cost})
                    except ValueError:
                        continue

        if not self.imported_data:
            QMessageBox.warning(self, "提示", "请先选择表格文件或在文本框中粘贴要导入的成本数据！")
            return

        self.accept()

    def get_imported_costs(self) -> List[Dict[str, Any]]:
        return self.imported_data
