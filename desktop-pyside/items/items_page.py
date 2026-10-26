from __future__ import annotations

import csv
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, QPoint, QSize, QUrl, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from engine.auth import AuthManager
from engine.client import MercadoClient
from items.models import ItemModel, load_items_snapshot, save_items_snapshot
from ui_common.clipboard import copy_pure_text
from ui_common.image_cache import load_image_async
from ui_common.lightbox import show_image_preview

logger = logging.getLogger(__name__)


class SafeWorkerBase(QObject):
    """Base class for background workers using standard threading.Thread."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._is_stopped: bool = False

    def start(self) -> None:
        self._is_stopped = False
        self._thread = threading.Thread(target=self._exec_run, daemon=True)
        self._thread.start()

    def _exec_run(self) -> None:
        try:
            self.run()
        except Exception as exc:
            logger.error("Unhandled exception in %s: %s", self.__class__.__name__, exc)

    def run(self) -> None:
        raise NotImplementedError

    def isRunning(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class FetchItemsWorker(SafeWorkerBase):
    """Asynchronously fetches listings across all configured stores and sites."""
    progress = Signal(int, int, str)  # current, total, status_text
    finished = Signal(list)           # list[ItemModel]
    error = Signal(str)

    def __init__(self, client: MercadoClient, auth_mgr: AuthManager):
        super().__init__()
        self.client = client
        self.auth_mgr = auth_mgr
        self._is_stopped = False

    def stop(self) -> None:
        self._is_stopped = True

    def run(self) -> None:
        try:
            accounts = self.auth_mgr.list_accounts()
            if not accounts:
                self.finished.emit([])
                return

            all_tasks = []
            for acc in accounts:
                aid = acc.get("account_id") or acc.get("id") or ""
                sname = acc.get("store_name") or acc.get("display_name") or acc.get("name") or aid
                sites = self.auth_mgr.list_sites(aid)
                for s in sites:
                    cuid = str(s.get("child_user_id") or "").strip()
                    sid = str(s.get("site_id") or "").strip()
                    if cuid and sid:
                        all_tasks.append((aid, sname, cuid, sid))

            total_items: list[ItemModel] = []
            total_tasks = len(all_tasks)
            completed_tasks = 0

            for aid, sname, cuid, sid in all_tasks:
                if self._is_stopped:
                    break
                self.progress.emit(completed_tasks, total_tasks, f"正在获取【{sname} · {sid}】商品列表...")
                try:
                    # 1. Fetch item IDs (up to 200 items per site for fast responsiveness)
                    offset = 0
                    limit = 50
                    max_fetch_per_site = 200
                    site_item_ids: list[str] = []
                    while offset < max_fetch_per_site and not self._is_stopped:
                        res = self.client.search_marketplace_items(aid, cuid, limit=limit, offset=offset)
                        results = res.get("results") or []
                        if not results:
                            break
                        site_item_ids.extend([str(i) for i in results if i])
                        total_count = res.get("paging", {}).get("total", 0)
                        offset += len(results)
                        if offset >= total_count or len(results) < limit:
                            break

                    # 2. Batch fetch item details in chunks of 20
                    chunk_size = 20
                    for i in range(0, len(site_item_ids), chunk_size):
                        if self._is_stopped:
                            break
                        batch_ids = site_item_ids[i : i + chunk_size]
                        details = self.client.get_items_batch(aid, batch_ids)
                        for d in details:
                            if not isinstance(d, dict) or not d.get("id"):
                                continue
                            sku = d.get("seller_custom_field") or ""
                            if not sku and d.get("variations"):
                                for v in d["variations"]:
                                    if isinstance(v, dict) and v.get("seller_custom_field"):
                                        sku = v["seller_custom_field"]
                                        break
                            thumb = d.get("thumbnail") or ""
                            if not thumb and d.get("pictures"):
                                pic0 = d["pictures"][0]
                                if isinstance(pic0, dict):
                                    thumb = pic0.get("url") or pic0.get("secure_url") or ""

                            model = ItemModel(
                                id=str(d.get("id") or ""),
                                title=str(d.get("title") or ""),
                                price=float(d.get("price") or 0.0),
                                currency_id=str(d.get("currency_id") or "USD"),
                                status=str(d.get("status") or "active"),
                                available_quantity=int(d.get("available_quantity") or 0),
                                sold_quantity=int(d.get("sold_quantity") or 0),
                                thumbnail=thumb,
                                permalink=str(d.get("permalink") or ""),
                                sku=str(sku or ""),
                                account_id=aid,
                                account_name=sname,
                                site_id=sid,
                                updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            )
                            total_items.append(model)
                except Exception as err:
                    logger.warning("Error fetching items for %s %s: %s", aid, sid, err)

                completed_tasks += 1
                self.progress.emit(completed_tasks, total_tasks, f"已完成 {completed_tasks}/{total_tasks} 站点同步")

            self.finished.emit(total_items)
        except Exception as err:
            logger.error("FetchItemsWorker fatal error: %s", err)
            self.error.emit(str(err))


class ItemsPage(QFrame):
    """Mercado Libre Multi-Store Items & Listings Management Workbench."""

    def __init__(self, client: MercadoClient, auth_mgr: AuthManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.client = client
        self.auth = auth_mgr

        self._all_items: list[ItemModel] = load_items_snapshot()
        self._filtered_items: list[ItemModel] = []
        self._sync_worker: FetchItemsWorker | None = None

        self._init_ui()
        self._apply_filter()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # 1. Header Toolbar
        top_bar = QWidget()
        top_bar_layout = QVBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar_layout.setSpacing(8)

        # Top line: Title & Action buttons
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        main_title = QLabel("📦 商品管理")
        main_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #F6F3EA;")
        sub_title = QLabel("全店铺商品信息汇总 · 实时在售状态 · 价格库存与前台直达")
        sub_title.setStyleSheet("font-size: 11px; color: #8C877D;")
        title_box.addWidget(main_title)
        title_box.addWidget(sub_title)
        header_row.addLayout(title_box)

        header_row.addStretch(1)

        self.export_btn = QPushButton("📥 导出表格")
        self.export_btn.setFixedHeight(30)
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setStyleSheet("""
            QPushButton {
                background: #1E293B;
                color: #94A3B8;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #334155;
                color: #F8FAFC;
                border-color: #475569;
            }
        """)
        self.export_btn.clicked.connect(self._export_items)
        header_row.addWidget(self.export_btn)

        self.sync_btn = QPushButton("⟳ 同步商品")
        self.sync_btn.setFixedHeight(30)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 4px;
                padding: 4px 14px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #387D4A;
                border-color: #6D5B2A;
            }
            QPushButton:disabled {
                background: #222623;
                color: #555555;
                border-color: #333333;
            }
        """)
        self.sync_btn.clicked.connect(self._start_sync)
        header_row.addWidget(self.sync_btn)

        top_bar_layout.addLayout(header_row)

        # Filters line: Store, Site, Status, Search
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        # Store Selector
        filter_row.addWidget(QLabel("店铺:"))
        self.store_combo = QComboBox()
        self.store_combo.setFixedHeight(28)
        self.store_combo.setMinimumWidth(110)
        self.store_combo.setStyleSheet("""
            QComboBox {
                background: #1A1D1A;
                color: #F6F3EA;
                border: 1px solid #3B382C;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 12px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1A1D1A;
                color: #F6F3EA;
                selection-background-color: #2F6B3F;
            }
        """)
        self.store_combo.addItem("全部店铺", "all")
        for acc in self.auth.list_accounts():
            aid = acc.get("account_id") or acc.get("id") or ""
            sname = acc.get("store_name") or acc.get("display_name") or acc.get("name") or aid
            self.store_combo.addItem(sname, aid)
        self.store_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.store_combo)

        # Site Selector
        filter_row.addWidget(QLabel("站点:"))
        self.site_combo = QComboBox()
        self.site_combo.setFixedHeight(28)
        self.site_combo.setMinimumWidth(110)
        self.site_combo.setStyleSheet(self.store_combo.styleSheet())
        self.site_combo.addItem("全部站点", "all")
        self.site_combo.addItem("🇧🇷 巴西 (MLB)", "MLB")
        self.site_combo.addItem("🇲🇽 墨西哥 (MLM)", "MLM")
        self.site_combo.addItem("🇨🇱 智利 (MLC)", "MLC")
        self.site_combo.addItem("🇨🇴 哥伦比亚 (MCO)", "MCO")
        self.site_combo.addItem("🇦🇷 阿根廷 (MLA)", "MLA")
        self.site_combo.addItem("🇺🇾 乌拉圭 (MLU)", "MLU")
        self.site_combo.addItem("🇵🇪 秘鲁 (MPE)", "MPE")
        self.site_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.site_combo)

        # Status Selector
        filter_row.addWidget(QLabel("状态:"))
        self.status_combo = QComboBox()
        self.status_combo.setFixedHeight(28)
        self.status_combo.setMinimumWidth(95)
        self.status_combo.setStyleSheet(self.store_combo.styleSheet())
        self.status_combo.addItem("全部状态", "all")
        self.status_combo.addItem("🟢 在售", "active")
        self.status_combo.addItem("🟡 暂停", "paused")
        self.status_combo.addItem("⚪ 已下架", "closed")
        self.status_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.status_combo)

        # Search Bar
        self.search_input = QLineEdit()
        self.search_input.setFixedHeight(28)
        self.search_input.setPlaceholderText("🔍 搜索商品标题 / 商品 ID / SKU...")
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: #141714;
                color: #F6F3EA;
                border: 1px solid #3B382C;
                border-radius: 4px;
                padding: 2px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #6D5B2A;
            }
        """)
        self.search_input.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_input, 1)

        top_bar_layout.addLayout(filter_row)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: #1E231F;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: #2F6B3F;
                border-radius: 2px;
            }
        """)
        self.progress_bar.hide()
        top_bar_layout.addWidget(self.progress_bar)

        self.status_hint = QLabel("")
        self.status_hint.setStyleSheet("font-size: 11px; color: #8C877D;")
        self.status_hint.hide()
        top_bar_layout.addWidget(self.status_hint)

        main_layout.addWidget(top_bar)

        # 2. Main Items Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "主图",
            "商品标题",
            "商品 ID",
            "卖家 SKU",
            "店铺 / 站点",
            "售价",
            "库存",
            "状态",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #121412;
                alternate-background-color: #161A16;
                color: #E6E4DC;
                border: 1px solid #2B2E2B;
                border-radius: 6px;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 4px 6px;
                border-bottom: 1px solid #1C201C;
            }
            QTableWidget::item:selected {
                background-color: #1F3826;
                color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #1A1D1A;
                color: #A8A29E;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-bottom: 1px solid #333833;
                padding: 6px;
            }
        """)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 54)  # Thumbnail
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Title
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(2, 130)  # Item ID
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(3, 130)  # SKU
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(4, 150)  # Store / Site
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(5, 100)  # Price
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(6, 75)   # Stock
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(7, 85)   # Status

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        main_layout.addWidget(self.table, 1)

        # 3. Footer Summary Bar
        footer = QHBoxLayout()
        self.count_label = QLabel("共 0 个商品")
        self.count_label.setStyleSheet("font-size: 12px; color: #A8A29E;")
        footer.addWidget(self.count_label)

        footer.addStretch(1)

        self.tip_label = QLabel("💡 提示: 双击商品行或右键菜单可复制 ID/SKU 或直接在浏览器打开前台页面")
        self.tip_label.setStyleSheet("font-size: 11px; color: #666666;")
        footer.addWidget(self.tip_label)

        main_layout.addLayout(footer)

    # ------------------------------------------------------------------
    # Data Filtering & Rendering
    # ------------------------------------------------------------------

    def _apply_filter(self) -> None:
        store_val = self.store_combo.currentData() or "all"
        site_val = self.site_combo.currentData() or "all"
        status_val = self.status_combo.currentData() or "all"
        query = self.search_input.text().strip().lower()

        filtered = []
        for it in self._all_items:
            if store_val != "all" and it.account_id != store_val:
                continue
            if site_val != "all" and it.site_id != site_val:
                continue
            if status_val != "all" and it.status.lower() != status_val.lower():
                continue
            if query:
                match = (
                    query in it.id.lower()
                    or query in it.title.lower()
                    or query in it.sku.lower()
                )
                if not match:
                    continue
            filtered.append(it)

        self._filtered_items = filtered
        self._render_table()
        self._update_counts()

    def _render_table(self) -> None:
        self.table.setRowCount(len(self._filtered_items))
        self.table.verticalHeader().setDefaultSectionSize(48)

        for row, it in enumerate(self._filtered_items):
            # Col 0: Thumbnail
            thumb_lbl = QLabel()
            thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_lbl.setFixedSize(44, 44)
            thumb_lbl.setStyleSheet("background: #181B18; border-radius: 4px;")
            if it.thumbnail:
                load_image_async(it.thumbnail, QSize(40, 40), thumb_lbl.setPixmap, radius=4)
                # Click to view lightbox
                thumb_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                thumb_lbl.mousePressEvent = lambda ev, img=it.thumbnail: show_image_preview([img], parent=self)
            self.table.setCellWidget(row, 0, thumb_lbl)

            # Col 1: Title
            title_item = QTableWidgetItem(it.title)
            title_item.setToolTip(f"【商品标题】\n{it.title}\n\n【前台链接】\n{it.permalink}")
            title_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.table.setItem(row, 1, title_item)

            # Col 2: Item ID (clickable link style)
            id_item = QTableWidgetItem(it.id)
            id_item.setForeground(QColor("#60A5FA"))
            id_item.setToolTip("双击复制或打开前台")
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.table.setItem(row, 2, id_item)

            # Col 3: SKU
            sku_item = QTableWidgetItem(it.sku or "--")
            sku_item.setForeground(QColor("#FBBF24" if it.sku else "#777777"))
            sku_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.table.setItem(row, 3, sku_item)

            # Col 4: Store / Site
            store_site_item = QTableWidgetItem(f"{it.site_display}\n({it.account_name})")
            store_site_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.table.setItem(row, 4, store_site_item)

            # Col 5: Price
            price_item = QTableWidgetItem(it.price_display)
            price_item.setForeground(QColor("#34D399"))
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            self.table.setItem(row, 5, price_item)

            # Col 6: Stock
            stock_item = QTableWidgetItem(str(it.available_quantity))
            stock_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, stock_item)

            # Col 7: Status Badge
            status_item = QTableWidgetItem(it.status_display)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
            if it.status == "active":
                status_item.setForeground(QColor("#4ADE80"))
            elif it.status == "paused":
                status_item.setForeground(QColor("#FBBF24"))
            else:
                status_item.setForeground(QColor("#94A3B8"))
            self.table.setItem(row, 7, status_item)

    def _update_counts(self) -> None:
        total = len(self._all_items)
        active = sum(1 for i in self._all_items if i.status == "active")
        paused = sum(1 for i in self._all_items if i.status == "paused")
        closed = sum(1 for i in self._all_items if i.status == "closed")
        showing = len(self._filtered_items)
        self.count_label.setText(
            f"汇总: {total} 个商品 (🟢在售: {active} | 🟡暂停: {paused} | ⚪已下架: {closed}) · 当前展示: <b>{showing}</b> 个"
        )

    # ------------------------------------------------------------------
    # User Actions: Sync, Context Menu, Export
    # ------------------------------------------------------------------

    def _start_sync(self) -> None:
        if self._sync_worker and self._sync_worker.isRunning():
            return

        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("⟳ 正在同步...")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_hint.show()
        self.status_hint.setText("正在连接美客多官方接口获取各店铺商品...")

        self._sync_worker = FetchItemsWorker(self.client, self.auth)
        self._sync_worker.progress.connect(self._on_sync_progress)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.error.connect(self._on_sync_error)
        self._sync_worker.start()

    def _on_sync_progress(self, current: int, total: int, text: str) -> None:
        self.status_hint.setText(text)
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        else:
            self.progress_bar.setRange(0, 0)

    def _on_sync_finished(self, items: list[ItemModel]) -> None:
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("⟳ 同步商品")
        self.progress_bar.hide()
        self.status_hint.hide()

        if items:
            self._all_items = items
            save_items_snapshot(self._all_items)
            self._apply_filter()
            QMessageBox.information(self, "同步完成", f"成功同步各店铺 {len(items)} 个商品数据！")
        else:
            QMessageBox.warning(self, "同步提示", "未获取到商品数据，请检查网络或店铺授权状态。")

    def _on_sync_error(self, err: str) -> None:
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("⟳ 同步商品")
        self.progress_bar.hide()
        self.status_hint.hide()
        QMessageBox.critical(self, "同步失败", f"获取商品数据时发生异常：\n{err}")

    def _show_context_menu(self, pos: QPoint) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._filtered_items):
            return

        item = self._filtered_items[row]
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #1A1D1A;
                color: #F6F3EA;
                border: 1px solid #3B382C;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                font-size: 12px;
            }
            QMenu::item:selected {
                background: #2F6B3F;
            }
        """)

        act_copy_id = menu.addAction(f"复制商品 ID ({item.id})")
        act_copy_sku = menu.addAction(f"复制卖家 SKU ({item.sku or '无'})")
        act_copy_title = menu.addAction("复制商品标题")
        menu.addSeparator()
        act_copy_link = menu.addAction("复制前台商品链接")
        act_open_web = menu.addAction("在默认浏览器打开 ↗")

        selected = menu.exec(self.table.viewport().mapToGlobal(pos))
        if selected == act_copy_id:
            copy_pure_text(item.id)
        elif selected == act_copy_sku:
            copy_pure_text(item.sku or "")
        elif selected == act_copy_title:
            copy_pure_text(item.title)
        elif selected == act_copy_link:
            copy_pure_text(item.permalink)
        elif selected == act_open_web:
            if item.permalink:
                QDesktopServices.openUrl(QUrl(item.permalink))

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._filtered_items):
            return
        item = self._filtered_items[row]
        if column == 2:  # ID clicked
            copy_pure_text(item.id)
            if item.permalink:
                QDesktopServices.openUrl(QUrl(item.permalink))
        elif column == 3:  # SKU clicked
            copy_pure_text(item.sku or "")
        elif item.permalink:
            QDesktopServices.openUrl(QUrl(item.permalink))

    def _export_items(self) -> None:
        if not self._filtered_items:
            QMessageBox.information(self, "提示", "当前没有可导出的商品数据。")
            return

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出商品汇总数据",
            f"美客多商品汇总_{now_str}.csv",
            "CSV 电子表格 (*.csv)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "商品 ID",
                    "卖家 SKU",
                    "商品标题",
                    "所属店铺",
                    "站点",
                    "售价",
                    "币种",
                    "可用库存",
                    "历史销量",
                    "官方在售状态",
                    "前台链接",
                ])
                for it in self._filtered_items:
                    writer.writerow([
                        it.id,
                        it.sku,
                        it.title,
                        it.account_name,
                        it.site_id,
                        it.price,
                        it.currency_id,
                        it.available_quantity,
                        it.sold_quantity,
                        it.status_display,
                        it.permalink,
                    ])
            QMessageBox.information(self, "导出成功", f"成功导出 {len(self._filtered_items)} 个商品数据至：\n{file_path}")
        except Exception as err:
            QMessageBox.critical(self, "导出失败", f"导出文件时发生异常：\n{err}")
