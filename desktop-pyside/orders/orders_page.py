from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QPoint, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from engine.auth import AuthManager
from engine.client import MercadoClient
from engine.crypto import get_data_dir
from orders.repository import OrdersRepository, translate_variation_attributes
from service_center.translator import translate_text
from orders.models import (
    BADGE_STYLE_BLUE,
    BADGE_STYLE_GOLD,
    BADGE_STYLE_GRAY,
    BADGE_STYLE_GREEN,
    BADGE_STYLE_RED,
    OrderItemModel,
    OrderModel,
    PaymentInfoModel,
    ShippingInfoModel,
    format_dimensions_display,
    format_order_date,
    get_order_status_badge_info,
    parse_order_from_dict,
)
from ui_common import (
    ElidedLabel,
    copy_pure_text,
    get_placeholder_pixmap,
    label_copy_val,
    load_image_async,
    make_selectable_label,
    populate_store_combo,
    show_image_preview,
    show_pure_copy_menu,
)
from core import SITE_FLAGS, get_store_alias, resolve_store_name
from theme import COLORS, STATUS_COLORS
from orders.shipping_audits import get_all_shipping_audits, init_shipping_audits_db, save_shipping_audit
from orders.zhiying_sync import ZhiyingSyncReceiver

logger = logging.getLogger(__name__)

SITE_DOMAINS = {
    "MLM": "https://articulo.mercadolibre.com.mx",
    "MLB": "https://produto.mercadolivre.com.br",
    "MLC": "https://articulo.mercadolibre.cl",
    "MCO": "https://articulo.mercadolibre.com.co",
    "MLA": "https://articulo.mercadolibre.com.ar",
    "MLU": "https://articulo.mercadolibre.com.uy",
    "MPE": "https://articulo.mercadolibre.com.pe",
}

from engine.exchange_rate import get_cny_exchange_rate, trigger_async_rate_refresh


# ElidedLabel, copy_pure_text, show_pure_copy_menu, label_copy_val
# are extracted to ui_common and re-exported above.


class OrdersFetchWorker(QThread):
    """Background thread to fetch and enrich orders across multiple accounts without freezing UI.

    Delegates all network fetching, concurrency, and order enrichment to OrdersRepository.
    """

    orders_chunk = Signal(list)
    orders_fetched = Signal(list)
    fetch_error = Signal(str)
    fetch_finished = Signal()

    def __init__(
        self,
        client: MercadoClient,
        auth_mgr: AuthManager,
        target_account: str | None = None,
        limit_per_account: int = 50,
        cached_orders: list[OrderModel] | None = None,
        parent: QWidget | None = None,
        repository: OrdersRepository | None = None,
    ):
        super().__init__(parent)
        self.client = client
        self.auth_mgr = auth_mgr
        self.target_account = target_account
        self.limit_per_account = limit_per_account
        self._is_stopped = False
        self.repository = repository or OrdersRepository(client, auth_mgr, cached_orders=cached_orders)

    @property
    def _cache_map(self) -> dict[str, OrderModel]:
        return self.repository._cache_map

    @property
    def _item_cache(self) -> dict[str, dict[str, Any]]:
        return self.repository._item_cache

    @property
    def _item_cache_lock(self) -> threading.Lock:
        return self.repository._item_cache_lock

    @property
    def _audits_map(self) -> dict[str, str]:
        return self.repository._audits_map

    def stop(self) -> None:
        self._is_stopped = True

    def _enrich_single(self, entry: tuple[str, dict[str, Any], str]) -> OrderModel | None:
        """Backward-compatible proxy delegating directly to OrdersRepository."""
        return self.repository.enrich_single(entry, is_stopped=lambda: self._is_stopped)

    def run(self) -> None:
        try:
            orders = self.repository.fetch_orders(
                target_account=self.target_account,
                limit_per_account=self.limit_per_account,
                on_chunk=self.orders_chunk.emit,
                is_stopped=lambda: self._is_stopped,
            )
            self.orders_fetched.emit(orders)
        except Exception as e:
            logger.error("Failed fetching orders in background: %s", e, exc_info=True)
            self.fetch_error.emit(str(e))
        finally:
            self.fetch_finished.emit()


class OrdersPage(QWidget):
    """Mercado Libre Orders Management Page.

    Features:
    - Multi-account aggregation with store selector and site tabs
    - Multi-status filter tabs with 74px x 22px fixed status badges
    - Real-time search across order ID, buyer nickname, item ID, SKU, tracking
    - Double-column layout: left high-density order cards + right deep order drawer
    - Pure text copying (pure numbers, no popups)
    - macOS native 26x26 circular lightbox close button
    - Offline local snapshot cache for instant 0-second launch
    - Export orders to Excel/CSV
    """

    oauthExpired = Signal(str, str)
    contactBuyerRequested = Signal(object)
    zhiyingPacketReceived = Signal()

    def __init__(self, client: MercadoClient, auth_mgr: AuthManager, parent: QWidget | None = None, enable_sync_receiver: bool = True):
        super().__init__(parent)
        self.client = client
        self.auth_mgr = auth_mgr
        self.repository = OrdersRepository(client, auth_mgr)
        self._all_orders: list[OrderModel] = []
        self._filtered_orders: list[OrderModel] = []
        self._current_order: OrderModel | None = None
        self._worker: OrdersFetchWorker | None = None
        self._thumb_cache: dict[str, QPixmap] = {}
        self._has_auto_fetched = False
        self._selected_order_ids: set[str] = set()

        self._current_site_filter = "ALL"
        self._current_status_filter = "ALL"
        self._current_time_filter = "ALL"
        self._list_sla_badges: dict[str, tuple[QLabel, OrderModel]] = {}

        self._zhiying_receiver: ZhiyingSyncReceiver | None = None
        if enable_sync_receiver and not os.environ.get("PYTEST_CURRENT_TEST"):
            self.zhiyingPacketReceived.connect(self._on_zhiying_sync_packet)
            self._zhiying_receiver = ZhiyingSyncReceiver(on_packet=lambda _pkg: self.zhiyingPacketReceived.emit())
            self._zhiying_receiver.start()

        self._build_ui()
        self._load_local_snapshot()

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(60000)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._countdown_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._has_auto_fetched:
            self._has_auto_fetched = True
            QTimer.singleShot(150, self.reload_orders)

    def _get_snapshot_path(self) -> Path:
        return self.repository.get_snapshot_path()

    def _save_local_snapshot(self) -> None:
        self.repository.save_snapshot(self._all_orders)

    def _load_local_snapshot(self) -> None:
        orders = self.repository.load_snapshot()
        if orders:
            self._all_orders = orders
            self._apply_filters()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        # 1. Top Control Bar (Store, Site, Status, Search, Buttons)
        top_bar = QFrame(self)
        top_bar.setObjectName("ordersControlSection")
        top_bar.setStyleSheet("""
            QFrame#ordersControlSection {
                background: #171D1A;
                border: 1px solid #4E472F;
                border-radius: 8px;
                padding: 6px 10px;
            }
        """)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(4, 4, 4, 4)
        top_layout.setSpacing(8)

        # Store Selector
        store_lbl = QLabel("店铺:")
        store_lbl.setStyleSheet("color: #A8A29E; font-size: 12px; font-weight: bold;")
        top_layout.addWidget(store_lbl)

        self.store_combo = QComboBox()
        self.store_combo.setMinimumWidth(180)
        self.store_combo.addItem("全部店铺 (聚合汇总)", "ALL")
        self.store_combo.currentIndexChanged.connect(self._on_store_changed)
        top_layout.addWidget(self.store_combo)

        # Site Tabs Filter
        site_lbl = QLabel("站点:")
        site_lbl.setStyleSheet("color: #A8A29E; font-size: 12px; font-weight: bold; margin-left: 6px;")
        top_layout.addWidget(site_lbl)

        self.site_combo = QComboBox()
        self.site_combo.addItem("全部站点", "ALL")
        self.site_combo.addItem("🇲🇽 墨西哥 (MLM)", "MLM")
        self.site_combo.addItem("🇧🇷 巴西 (MLB)", "MLB")
        self.site_combo.addItem("🇨🇱 智利 (MLC)", "MLC")
        self.site_combo.addItem("🇨🇴 哥伦比亚 (MCO)", "MCO")
        self.site_combo.addItem("🇦🇷 阿根廷 (MLA)", "MLA")
        self.site_combo.addItem("🇺🇾 乌拉圭 (MLU)", "MLU")
        self.site_combo.currentIndexChanged.connect(self._on_site_changed)
        top_layout.addWidget(self.site_combo)

        # Status Filter
        status_lbl = QLabel("状态:")
        status_lbl.setStyleSheet("color: #A8A29E; font-size: 12px; font-weight: bold; margin-left: 6px;")
        top_layout.addWidget(status_lbl)

        self.status_combo = QComboBox()
        self.status_combo.addItem("全部状态", "ALL")
        self.status_combo.addItem("待发货", "paid")
        self.status_combo.addItem("运送中", "shipped")
        self.status_combo.addItem("已送达", "delivered")
        self.status_combo.addItem("待付款", "payment_required")
        self.status_combo.addItem("已取消", "cancelled")
        self.status_combo.currentIndexChanged.connect(self._on_status_changed)
        top_layout.addWidget(self.status_combo)

        # Time Filter
        time_lbl = QLabel("时间:")
        time_lbl.setStyleSheet("color: #A8A29E; font-size: 12px; font-weight: bold; margin-left: 6px;")
        top_layout.addWidget(time_lbl)

        self.time_combo = QComboBox()
        self.time_combo.addItem("全部历史", "ALL")
        self.time_combo.addItem("近 7 天", "7")
        self.time_combo.addItem("近 30 天", "30")
        self.time_combo.addItem("近 90 天", "90")
        self.time_combo.currentIndexChanged.connect(self._on_time_changed)
        top_layout.addWidget(self.time_combo)

        # Instant Search Box
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索订单号 / 买家昵称 / 商品 ID / SKU / 运单号...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(220)
        self.search_input.textChanged.connect(self._apply_filters)
        self.search_input.returnPressed.connect(self._apply_filters)
        top_layout.addWidget(self.search_input, 1)

        # Refresh Button
        self.refresh_btn = QPushButton("⟳ 刷新订单")
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #232C24;
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
            }
            QPushButton:hover { background: #26352C; border-color: #6E5D2A; }
            QPushButton:pressed { background: #1F5A34; }
        """)
        self.refresh_btn.clicked.connect(self.reload_orders)
        top_layout.addWidget(self.refresh_btn)

        # Export Button
        self.export_btn = QPushButton("📥 导出 Excel")
        self.export_btn.setStyleSheet("""
            QPushButton {
                background: #232C24;
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
            }
            QPushButton:hover { background: #26352C; border-color: #6E5D2A; }
            QPushButton:pressed { background: #1F5A34; }
        """)
        self.export_btn.clicked.connect(self._export_orders)
        top_layout.addWidget(self.export_btn)

        main_layout.addWidget(top_bar)

        # 2. Main Work Area: Splitter (Left: Order List; Right: Order Detail Drawer)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setHandleWidth(6)
        self.splitter.setStyleSheet("""
            QSplitter::handle:horizontal {
                background: transparent;
                border: none;
                image: none;
                width: 6px;
            }
            QSplitter::handle:horizontal:hover {
                background: rgba(78, 71, 47, 0.35);
                border: none;
                image: none;
                border-radius: 3px;
            }
        """)

        # Left Container
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # List Header: Select All + count + Batch Print Button
        list_hdr = QHBoxLayout()
        list_hdr.setContentsMargins(4, 0, 4, 0)
        list_hdr.setSpacing(8)

        self.select_all_cb = QCheckBox("全选")
        self.select_all_cb.setStyleSheet("""
            QCheckBox {
                color: #A8A29E;
                font-size: 11px;
                font-weight: 500;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #4E472F;
                border-radius: 3px;
                background: #171D1A;
            }
            QCheckBox::indicator:checked {
                background: #D4AF37;
                border-color: #D4AF37;
            }
        """)
        self.select_all_cb.toggled.connect(self._toggle_select_all)
        list_hdr.addWidget(self.select_all_cb)

        self.count_lbl = QLabel("共找到 0 笔订单")
        self.count_lbl.setStyleSheet("color: #A8A29E; font-size: 11px; padding: 2px 0px;")
        list_hdr.addWidget(self.count_lbl)
        list_hdr.addStretch()

        self.print_labels_btn = QPushButton("🖨️ 打印面单")
        self.print_labels_btn.setEnabled(False)
        self.print_labels_btn.setStyleSheet("""
            QPushButton {
                background: #1E3A2B;
                color: #F6F3EA;
                border: 1px solid #2D6A4F;
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover:enabled { background: #264E3A; border-color: #3E8B66; }
            QPushButton:disabled { background: #1C221D; color: #78716C; border-color: #333D35; }
        """)
        self.print_labels_btn.clicked.connect(self._print_selected_labels)
        list_hdr.addWidget(self.print_labels_btn)

        left_layout.addLayout(list_hdr)

        # Order List Widget
        self.order_list = QListWidget()
        self.order_list.setObjectName("orderList")
        self.order_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.order_list.setStyleSheet("""
            QListWidget#orderList {
                background: #171D1A;
                border: 1px solid #4E472F;
                border-radius: 8px;
                outline: none;
                padding: 4px;
            }
            QListWidget#orderList::item {
                border-bottom: 1px solid rgba(78, 71, 47, 0.4);
                padding: 2px;
                border-radius: 6px;
                margin-bottom: 4px;
            }
            QListWidget#orderList::item:selected {
                background: #222620;
                border: 1px solid #D4AF37;
            }
            QListWidget#orderList::item:hover:!selected {
                background: #1C221D;
                border: 1px solid rgba(78, 71, 47, 0.5);
            }
        """)
        self.order_list.currentItemChanged.connect(self._on_order_selected)
        self.order_list.setResizeMode(QListView.ResizeMode.Adjust)
        left_layout.addWidget(self.order_list, 1)

        self.splitter.addWidget(left_widget)

        # Right Container: Order Detail Drawer
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setStyleSheet("""
            QScrollArea {
                background: #171B19;
                border: 1px solid #4E472F;
                border-radius: 8px;
            }
        """)

        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(16, 14, 16, 16)
        self.detail_layout.setSpacing(14)

        self._build_detail_drawer()

        right_scroll.setWidget(self.detail_container)
        self.splitter.addWidget(right_scroll)

        # Splitter proportion (42% left, 58% right)
        self.splitter.setSizes([450, 620])
        main_layout.addWidget(self.splitter, 1)

        self.refresh_accounts()

    def _build_detail_drawer(self) -> None:
        """Build modular sections in the right detail drawer."""
        # 1. Header Overview Card
        self.overview_card = QFrame()
        self.overview_card.setObjectName("surface")
        self.overview_card.setStyleSheet("""
            QFrame#surface {
                background: #202820;
                border: 1px solid #4E472F;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        ov_layout = QVBoxLayout(self.overview_card)
        ov_layout.setSpacing(8)

        ov_top = QHBoxLayout()
        ov_top.setSpacing(8)

        # Store badge (Left)
        self.det_store_badge = QLabel("-")
        self.det_store_badge.setStyleSheet("color: #A8A29E; font-size: 12px; font-weight: bold;")
        ov_top.addWidget(self.det_store_badge)

        self.det_sla_badge = QLabel("")
        self.det_sla_badge.hide()
        ov_top.addWidget(self.det_sla_badge)

        ov_top.addStretch()

        # Order creation date in the top right (No "关闭时间")
        self.det_dates_lbl = QLabel("下单时间: -")
        self.det_dates_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
        self.det_dates_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.det_dates_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.det_dates_lbl.customContextMenuRequested.connect(
            lambda pos: show_pure_copy_menu(self.det_dates_lbl, pos, lambda: label_copy_val(self.det_dates_lbl))
        )
        ov_top.addWidget(self.det_dates_lbl)
        ov_layout.addLayout(ov_top)

        # Order ID Row (Pure copy, no browser jump)
        ov_mid = QHBoxLayout()
        ov_mid.setSpacing(10)
        self.det_order_id_lbl = QLabel("订单号: -")
        self.det_order_id_lbl.setStyleSheet("""
            QLabel {
                color: #E6E2D8;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel:hover {
                color: #D4AF37;
            }
        """)
        make_selectable_label(
            self.det_order_id_lbl,
            fallback_getter=lambda: self._current_order.display_order_id if self._current_order else "",
            tooltip="鼠标选择即可复制 (或右键/快捷键复制纯订单号)",
        )
        ov_mid.addWidget(self.det_order_id_lbl)

        ov_mid.addStretch()
        ov_layout.addLayout(ov_mid)

        # Modular items list container in overview card
        self.items_container = QWidget()
        self.items_container_layout = QVBoxLayout(self.items_container)
        self.items_container_layout.setContentsMargins(0, 4, 0, 0)
        self.items_container_layout.setSpacing(6)
        ov_layout.addWidget(self.items_container)

        self.detail_layout.addWidget(self.overview_card)

        # 2. Payment & Financial Breakdown Card (Focused on commission, shipping cost and net profit)
        self.payment_card = QFrame()
        self.payment_card.setObjectName("surface")
        self.payment_card.setStyleSheet("""
            QFrame#surface {
                background: #202820;
                border: 1px solid #4E472F;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        pay_layout = QVBoxLayout(self.payment_card)
        pay_layout.setSpacing(10)

        pay_title = QLabel("💰 费用收支与利润结算")
        pay_title.setStyleSheet("color: #F6F3EA; font-size: 13px; font-weight: bold;")
        pay_layout.addWidget(pay_title)

        # 3 Indicators Grid Row: 平台手续费/佣金, 官方运费, 预估净结余
        grid_row = QHBoxLayout()
        grid_row.setSpacing(8)

        # Keep det_fee_paid_val for metadata and test compatibility without occupying a redundant big box
        self.det_fee_paid_val = QLabel("USD 0.00")
        self.det_fee_paid_val.setVisible(False)

        # Box 1: 平台手续费/佣金
        box_fee = QFrame()
        box_fee.setStyleSheet("background: #161D18; border: 1px solid #2D3830; border-radius: 6px; padding: 6px;")
        b_fee_lay = QVBoxLayout(box_fee)
        b_fee_lay.setContentsMargins(6, 6, 6, 6)
        b_fee_lay.setSpacing(3)
        lbl_t2 = QLabel("平台佣金 (手续费)")
        lbl_t2.setStyleSheet("color: #A8A29E; font-size: 11px;")
        lbl_t2.setToolTip("美客多平台销售佣金/手续费（Sale Fee）")
        self.det_fee_sale_val = QLabel("-USD 0.00")
        self.det_fee_sale_val.setStyleSheet("color: #FB923C; font-size: 14px; font-weight: bold;")
        make_selectable_label(self.det_fee_sale_val, fallback_getter=lambda: label_copy_val(self.det_fee_sale_val))
        b_fee_lay.addWidget(lbl_t2)
        b_fee_lay.addWidget(self.det_fee_sale_val)
        grid_row.addWidget(box_fee)

        # Box 2: 官方物流运费
        box_ship = QFrame()
        box_ship.setStyleSheet("background: #161D18; border: 1px solid #2D3830; border-radius: 6px; padding: 6px;")
        b_ship_lay = QVBoxLayout(box_ship)
        b_ship_lay.setContentsMargins(6, 6, 6, 6)
        b_ship_lay.setSpacing(3)
        lbl_t3 = QLabel("官方物流运费")
        lbl_t3.setStyleSheet("color: #A8A29E; font-size: 11px;")
        lbl_t3.setToolTip("官方物流运单登记运费（卖家包邮承担）")
        self.det_fee_ship_val = QLabel("-USD 0.00")
        self.det_fee_ship_val.setStyleSheet("color: #818CF8; font-size: 14px; font-weight: bold;")
        make_selectable_label(self.det_fee_ship_val, fallback_getter=lambda: label_copy_val(self.det_fee_ship_val))
        b_ship_lay.addWidget(lbl_t3)
        b_ship_lay.addWidget(self.det_fee_ship_val)

        self.det_fee_ship_adj_lbl = QLabel("")
        self.det_fee_ship_adj_lbl.setStyleSheet("color: #F87171; font-size: 10px; font-weight: 500;")
        self.det_fee_ship_adj_lbl.setVisible(False)
        make_selectable_label(self.det_fee_ship_adj_lbl, fallback_getter=lambda: label_copy_val(self.det_fee_ship_adj_lbl))
        b_ship_lay.addWidget(self.det_fee_ship_adj_lbl)

        grid_row.addWidget(box_ship)

        # Box 3: 预估净结余
        box_net = QFrame()
        box_net.setStyleSheet("background: #1A211D; border: 1px solid #4E472F; border-radius: 6px; padding: 6px;")
        b_net_lay = QVBoxLayout(box_net)
        b_net_lay.setContentsMargins(6, 6, 6, 6)
        b_net_lay.setSpacing(3)
        lbl_t4 = QLabel("预估净结余 (纯收益)")
        lbl_t4.setStyleSheet("color: #D4AF37; font-size: 11px; font-weight: bold;")
        lbl_t4.setToolTip("扣除佣金手续费与物流运费后的实际到手利润（自动折合人民币）")
        self.det_fee_net_val = QLabel("USD 0.00 (约 ¥0.00)")
        self.det_fee_net_val.setStyleSheet("color: #FCD34D; font-size: 14px; font-weight: bold;")
        make_selectable_label(self.det_fee_net_val, fallback_getter=lambda: label_copy_val(self.det_fee_net_val))
        b_net_lay.addWidget(lbl_t4)
        b_net_lay.addWidget(self.det_fee_net_val)
        grid_row.addWidget(box_net)

        pay_layout.addLayout(grid_row)

        self.det_shipping_adj_banner = QLabel("")
        self.det_shipping_adj_banner.setVisible(False)

        self.det_pay_detail_lbl = QLabel("支付方式: - · 支付流水号: -")
        self.det_pay_detail_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
        make_selectable_label(self.det_pay_detail_lbl, fallback_getter=lambda: label_copy_val(self.det_pay_detail_lbl))
        pay_layout.addWidget(self.det_pay_detail_lbl)

        self.detail_layout.addWidget(self.payment_card)

        # 3. Shipping & Tracking Card
        self.shipping_card = QFrame()
        self.shipping_card.setObjectName("surface")
        self.shipping_card.setStyleSheet("""
            QFrame#surface {
                background: #202820;
                border: 1px solid #4E472F;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        ship_layout = QVBoxLayout(self.shipping_card)
        ship_layout.setSpacing(8)

        ship_hdr = QHBoxLayout()
        ship_title = QLabel("🚚 物流与配送追踪")
        ship_title.setStyleSheet("color: #F6F3EA; font-size: 13px; font-weight: bold;")
        ship_hdr.addWidget(ship_title)
        ship_hdr.addStretch()

        self.btn_print_single = QPushButton("🖨️ 打印面单")
        self.btn_print_single.setStyleSheet("""
            QPushButton {
                background: #1E3A2B;
                color: #F6F3EA;
                border: 1px solid #2D6A4F;
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover { background: #264E3A; }
        """)
        self.btn_print_single.clicked.connect(self._print_current_order_label)
        ship_hdr.addWidget(self.btn_print_single)
        ship_layout.addLayout(ship_hdr)

        self.det_ship_meta_lbl = QLabel("物流方式: - · 承运商: - · 物流状态: -")
        self.det_ship_meta_lbl.setStyleSheet("color: #E6E2D8; font-size: 12px;")
        self.det_ship_meta_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.det_ship_meta_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.det_ship_meta_lbl.customContextMenuRequested.connect(
            lambda pos: show_pure_copy_menu(self.det_ship_meta_lbl, pos, lambda: label_copy_val(self.det_ship_meta_lbl))
        )
        ship_layout.addWidget(self.det_ship_meta_lbl)

        # Row 1: Package Number & Tracking Number Chips
        ship_row1 = QHBoxLayout()
        ship_row1.setSpacing(14)

        self.det_pack_chip = QLabel("包裹号: -")
        self.det_pack_chip.setStyleSheet("color: #A8A29E; font-size: 11px;")
        make_selectable_label(
            self.det_pack_chip,
            fallback_getter=lambda: self._current_order.pack_id if self._current_order else "",
            tooltip="鼠标选择即可复制 (或右键/快捷键复制纯包裹号)",
        )
        ship_row1.addWidget(self.det_pack_chip)

        self.det_track_chip = QLabel("运单号: -")
        self.det_track_chip.setStyleSheet("color: #A8A29E; font-size: 11px;")
        make_selectable_label(
            self.det_track_chip,
            fallback_getter=lambda: self._current_order.shipping.tracking_number if self._current_order else "",
            tooltip="鼠标选择即可复制 (或右键/快捷键复制纯运单号)",
        )
        ship_row1.addWidget(self.det_track_chip)
        ship_row1.addStretch()
        ship_layout.addLayout(ship_row1)

        # Row 2: Declared Packaging Dimensions & Audited Secondary Dimensions Badge
        ship_row2 = QHBoxLayout()
        ship_row2.setSpacing(10)

        self.det_dimensions_chip = QLabel("📦 出单包装尺重: -")
        self.det_dimensions_chip.setStyleSheet("color: #A8A29E; font-size: 11px;")
        make_selectable_label(
            self.det_dimensions_chip,
            fallback_getter=lambda: label_copy_val(self.det_dimensions_chip),
            tooltip="出单时系统锁定的运单包装尺重（鼠标选择即可复制）",
        )
        ship_row2.addWidget(self.det_dimensions_chip)

        self.det_actual_dimensions_chip = QLabel("")
        self.det_actual_dimensions_chip.setStyleSheet("""
            QLabel {
                color: #60A5FA;
                font-size: 11px;
                font-weight: 600;
                background: rgba(59, 130, 246, 0.15);
                border: 1px solid rgba(59, 130, 246, 0.4);
                border-radius: 4px;
                padding: 2px 8px;
            }
        """)
        self.det_actual_dimensions_chip.setVisible(False)
        make_selectable_label(
            self.det_actual_dimensions_chip,
            fallback_getter=lambda: label_copy_val(self.det_actual_dimensions_chip),
            tooltip="官方实测复重（鼠标选择即可复制）",
        )
        ship_row2.addWidget(self.det_actual_dimensions_chip)
        ship_row2.addStretch()
        ship_layout.addLayout(ship_row2)

        self.detail_layout.addWidget(self.shipping_card)

        # 4. Buyer and Address Card
        self.address_card = QFrame()
        self.address_card.setObjectName("surface")
        self.address_card.setStyleSheet("""
            QFrame#surface {
                background: #202820;
                border: 1px solid #4E472F;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        addr_layout = QVBoxLayout(self.address_card)
        addr_layout.setSpacing(8)

        addr_hdr = QHBoxLayout()
        addr_title = QLabel("👤 买家与收件信息")
        addr_title.setStyleSheet("color: #F6F3EA; font-size: 13px; font-weight: bold;")
        addr_hdr.addWidget(addr_title)
        addr_hdr.addStretch()

        self.btn_contact_buyer = QPushButton("💬 联系买家")
        self.btn_contact_buyer.setStyleSheet("""
            QPushButton {
                background: #1F3A2E;
                color: #A7F3D0;
                border: 1px solid #059669;
                border-radius: 4px;
                padding: 3px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover { background: #047857; color: #FFFFFF; }
            QPushButton:pressed { background: #065F46; }
            QPushButton:disabled { background: #1C221D; color: #78716C; border-color: #333D35; }
        """)
        self.btn_contact_buyer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_contact_buyer.setToolTip("跳转至客服售后中心并与该买家发起/查看私信")
        self.btn_contact_buyer.clicked.connect(self._on_contact_buyer_clicked)
        addr_hdr.addWidget(self.btn_contact_buyer)
        addr_layout.addLayout(addr_hdr)

        self.det_buyer_info_lbl = QLabel("收件人: - · 电话: -")
        self.det_buyer_info_lbl.setStyleSheet("color: #E6E2D8; font-size: 12px;")
        make_selectable_label(
            self.det_buyer_info_lbl,
            fallback_getter=lambda: label_copy_val(
                self.det_buyer_info_lbl,
                f"收件人: {self._current_order.shipping.receiver_name or '-'} · 电话: {self._current_order.shipping.receiver_phone or '-'}" if self._current_order else ""
            ),
            tooltip="鼠标选择即可复制 (或右键/快捷键复制)",
        )
        addr_layout.addWidget(self.det_buyer_info_lbl)

        self.det_address_lbl = QLabel("详细地址: -")
        self.det_address_lbl.setWordWrap(True)
        self.det_address_lbl.setStyleSheet("color: #C8C3B7; font-size: 12px; line-height: 1.4;")
        make_selectable_label(
            self.det_address_lbl,
            fallback_getter=lambda: label_copy_val(
                self.det_address_lbl,
                self._current_order.shipping.full_address if self._current_order else ""
            ),
            tooltip="鼠标选择即可复制 (或右键/快捷键复制详细地址)",
        )
        addr_layout.addWidget(self.det_address_lbl)

        # Enable right-click pure "复制" menu on address card
        self.address_card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.address_card.customContextMenuRequested.connect(
            lambda pos: show_pure_copy_menu(
                self.address_card,
                pos,
                lambda: self._get_full_shipping_text()
            )
        )

        self.detail_layout.addWidget(self.address_card)
        self.detail_layout.addStretch(1)

    def refresh_accounts(self) -> None:
        """Populate store dropdown with Chinese store aliases."""
        populate_store_combo(
            self.store_combo,
            auth_mgr=self.auth_mgr,
            all_label="全部店铺 (聚合汇总)",
            all_value="ALL",
            include_account_id_in_label=True,
        )

    def reload_orders(self) -> None:
        """Trigger asynchronous background fetching of orders with cache reuse and streaming chunks."""
        if self._worker and self._worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("正在拉取...")

        target_acc = self.store_combo.currentData() or "ALL"
        self._worker = OrdersFetchWorker(
            self.client,
            self.auth_mgr,
            target_account=target_acc,
            cached_orders=self._all_orders,
            parent=self,
            repository=self.repository,
        )
        self._worker.orders_chunk.connect(self._on_orders_chunk)
        self._worker.orders_fetched.connect(self._on_orders_fetched)
        self._worker.fetch_error.connect(self._on_fetch_error)
        self._worker.fetch_finished.connect(self._on_fetch_finished)
        self._worker.start()

    def _on_orders_chunk(self, chunk: list[OrderModel]) -> None:
        if not chunk:
            return
        existing_map = {o.order_id: idx for idx, o in enumerate(self._all_orders)}
        for o in chunk:
            if not o or not o.order_id:
                continue
            if o.order_id in existing_map:
                self._all_orders[existing_map[o.order_id]] = o
            else:
                self._all_orders.append(o)
                existing_map[o.order_id] = len(self._all_orders) - 1
        self._all_orders.sort(key=lambda x: x.created_timestamp, reverse=True)
        self._apply_filters()

    def _on_orders_fetched(self, orders: list[OrderModel]) -> None:
        self._all_orders = orders
        self._save_local_snapshot()
        self._apply_filters()

    def _on_fetch_error(self, err_msg: str) -> None:
        logger.warning("Order fetch background error: %s", err_msg)
        err_lower = str(err_msg).lower()
        if "401" in err_lower or "token" in err_lower or "unauthorized" in err_lower or "revoked" in err_lower:
            aid = str(getattr(self, "_current_store_filter", "") or "")
            self.oauthExpired.emit(aid, "")

    def _on_fetch_finished(self) -> None:
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("⟳ 刷新订单")
        self._worker = None

    def _on_store_changed(self) -> None:
        self.reload_orders()

    def _on_site_changed(self) -> None:
        self._current_site_filter = str(self.site_combo.currentData() or "ALL")
        self._apply_filters()

    def _on_status_changed(self) -> None:
        self._current_status_filter = str(self.status_combo.currentData() or "ALL")
        self._apply_filters()

    def _on_time_changed(self) -> None:
        self._current_time_filter = str(self.time_combo.currentData() or "ALL")
        self._apply_filters()

    def _apply_filters(self) -> None:
        query = self.search_input.text().strip().lower()
        now_ts = datetime.now(timezone.utc).timestamp()

        # Days filter calculation
        days_limit = 0.0
        if self._current_time_filter == "7":
            days_limit = 7 * 86400.0
        elif self._current_time_filter == "30":
            days_limit = 30 * 86400.0
        elif self._current_time_filter == "90":
            days_limit = 90 * 86400.0

        filtered = []
        for o in self._all_orders:
            # 1. Site Filter
            if self._current_site_filter != "ALL":
                if o.site_id and o.site_id != self._current_site_filter:
                    continue

            # 2. Status Filter
            if self._current_status_filter != "ALL":
                _, _, cat = o.status_badge_info
                if cat != self._current_status_filter and o.status != self._current_status_filter:
                    continue

            # 3. Time Filter
            if days_limit > 0:
                if (now_ts - o.created_timestamp) > days_limit:
                    continue

            # 4. Search Filter
            if query:
                text_corpus = (
                    f"{o.order_id} {o.pack_id} {o.buyer_nickname} {o.buyer_full_name} "
                    f"{o.shipping.tracking_number} {o.summary_skus} "
                    f"{' '.join(it.item_id + ' ' + it.title for it in o.items)}"
                ).lower()
                if query not in text_corpus:
                    continue

            filtered.append(o)

        self._filtered_orders = filtered
        self._render_order_list()

    def _render_order_list(self) -> None:
        prev_selected_oid = ""
        current_item = self.order_list.currentItem()
        if current_item:
            prev_selected_oid = current_item.data(Qt.ItemDataRole.UserRole) or ""

        self.order_list.clear()
        self._list_sla_badges.clear()
        self.count_lbl.setText(f"共找到 {len(self._filtered_orders)} 笔订单")

        target_row = 0
        for idx, order in enumerate(self._filtered_orders):
            item = QListWidgetItem(self.order_list)
            card_widget = self._create_order_card(order)
            h = card_widget.sizeHint().height()
            item.setSizeHint(QSize(0, max(h, 68)))
            item.setData(Qt.ItemDataRole.UserRole, order.order_id)
            self.order_list.addItem(item)
            self.order_list.setItemWidget(item, card_widget)
            if prev_selected_oid and order.order_id == prev_selected_oid:
                target_row = idx

        self._update_print_button_state()

        if self.order_list.count() > 0:
            self.order_list.setCurrentRow(target_row)
        else:
            self._render_empty_detail()

    def _create_order_card(self, order: OrderModel) -> QWidget:
        widget = QWidget()
        card_layout = QHBoxLayout(widget)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(10)

        # 1. Left controls: Checkbox + Thumbnail (Clickable Lightbox)
        cb = QCheckBox()
        cb.setChecked(order.order_id in self._selected_order_ids)
        cb.setStyleSheet("""
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #4E472F;
                border-radius: 3px;
                background: #171D1A;
            }
            QCheckBox::indicator:checked {
                background: #D4AF37;
                border-color: #D4AF37;
            }
        """)
        def _on_cb_toggled(checked: bool, oid=order.order_id):
            if checked:
                self._selected_order_ids.add(oid)
            else:
                self._selected_order_ids.discard(oid)
            self._update_print_button_state()
        cb.toggled.connect(_on_cb_toggled)
        card_layout.addWidget(cb, 0, Qt.AlignmentFlag.AlignVCenter)

        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(50, 50)
        thumb_lbl.setStyleSheet("background: #121614; border: 1px solid #4E472F; border-radius: 6px;")
        thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_async_thumbnail(order.primary_thumbnail, thumb_lbl)
        thumb_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        thumb_lbl.setToolTip("点击放大查看高清大图 (右键可直接复制图片)")
        def _on_thumb_clicked(e, url=order.primary_thumbnail, title=order.primary_item.title):
            if e.button() == Qt.MouseButton.LeftButton:
                if url:
                    self._show_image_lightbox(url, title)
        thumb_lbl.mousePressEvent = _on_thumb_clicked
        thumb_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        thumb_lbl.customContextMenuRequested.connect(
            lambda pos, lbl=thumb_lbl: show_pure_copy_menu(lbl, pos, lambda: lbl.pixmap())
        )
        card_layout.addWidget(thumb_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        # 2. Right Information Column (3 Rows matching user wireframe)
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(4)

        # Row 1: [店铺名] · [站点] · [买家] · [时效倒计时] ------------------ [待发货]
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        site_code = f" · {order.site_id}" if order.site_id else ""
        store_display = f"{order.store_name}{site_code}"
        store_lbl = QLabel(store_display)
        store_lbl.setStyleSheet("color: #A8A29E; font-size: 11px; font-weight: bold;")
        store_lbl.setMinimumWidth(0)
        store_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        store_lbl.customContextMenuRequested.connect(
            lambda pos, s=order.store_name, w=store_lbl: show_pure_copy_menu(w, pos, lambda: s)
        )
        row1.addWidget(store_lbl)

        # Shipping SLA Countdown Badge (for pending shipment orders, placed right after store & site)
        deadline_info = order.shipping_deadline_info
        if deadline_info:
            dl_text, dl_color, dl_bg, dl_border = deadline_info
            sla_badge = QLabel(f" {dl_text} ")
            sla_badge.setStyleSheet(f"""
                QLabel {{
                    color: {dl_color};
                    background: {dl_bg};
                    border: {dl_border};
                    border-radius: 3px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 1px 4px;
                }}
            """)
            sla_badge.setToolTip("发货截止时效倒计时 (美客多跨境履约 SLA 预警)")
            row1.addWidget(sla_badge)
            self._list_sla_badges[order.order_id] = (sla_badge, order)

        sep1 = QLabel(" · ")
        sep1.setStyleSheet("color: #78716C; font-size: 11px;")
        row1.addWidget(sep1)

        buyer_name = order.buyer_nickname or "买家"
        buyer_lbl = ElidedLabel(buyer_name)
        buyer_lbl.setStyleSheet("color: #F6F3EA; font-size: 11px; font-weight: bold;")
        buyer_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        buyer_lbl.setMinimumWidth(0)
        buyer_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        buyer_lbl.customContextMenuRequested.connect(
            lambda pos, b=buyer_name, w=buyer_lbl: show_pure_copy_menu(w, pos, lambda: b)
        )
        row1.addWidget(buyer_lbl)

        row1.addStretch(1)

        badge_text, badge_style, _ = order.status_badge_info
        badge = QLabel(badge_text)
        badge.setFixedSize(74, 22)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(badge_style)
        row1.addWidget(badge)

        info_layout.addLayout(row1)

        # Row 2: [商品标题]
        title_str = order.primary_item.title or (f"商品: {order.primary_item.item_id}" if order.primary_item.item_id else f"订单商品 ({len(order.items)} 件)")
        title_lbl = ElidedLabel(title_str)
        title_lbl.setStyleSheet("""
            QLabel {
                color: #E6E2D8;
                font-size: 12px;
                font-weight: 500;
            }
        """)
        title_lbl.setWordWrap(False)
        title_lbl.setMinimumWidth(0)
        title_lbl.setToolTip(f"{title_str}\n(右键可复制商品编号)")
        title_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        title_lbl.customContextMenuRequested.connect(
            lambda pos, lbl=title_lbl, it=order.primary_item: show_pure_copy_menu(
                lbl, pos, lambda: label_copy_val(lbl, it.item_id)
            )
        )
        def _on_title_clicked(e, oid=order.order_id):
            if e.button() == Qt.MouseButton.LeftButton:
                for r in range(self.order_list.count()):
                    it_item = self.order_list.item(r)
                    if it_item and it_item.data(Qt.ItemDataRole.UserRole) == oid:
                        self.order_list.setCurrentRow(r)
                        break
        title_lbl.mousePressEvent = _on_title_clicked
        info_layout.addWidget(title_lbl)

        # Row 3: [SKU标签]  [实付: USD 12.86]  [× 件数]
        sku_val = (order.primary_item.sku or "").strip()
        var_attrs = (order.primary_item.variation_attributes or "").strip()

        if order.paid_amount > 0:
            amt_text = f"实付: USD {order.paid_amount:.2f}"
        elif order.total_amount > 0:
            amt_text = f"总额: {order.currency_id} {order.total_amount:.2f}"
        else:
            amt_text = f"数量: {order.total_quantity}"

        row3 = QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setSpacing(6)

        if sku_val:
            sku_chip = ElidedLabel(f"SKU: {sku_val}")
            sku_chip.setStyleSheet("""
                QLabel {
                    color: #D4AF37;
                    font-size: 11px;
                    font-weight: 500;
                    background: rgba(212, 175, 55, 0.12);
                    border: 1px solid rgba(212, 175, 55, 0.35);
                    border-radius: 3px;
                    padding: 1px 4px;
                }
                QLabel:hover {
                    background: rgba(212, 175, 55, 0.25);
                    color: #FCD34D;
                }
            """)
            sku_chip.setMinimumWidth(0)
            sku_chip.setCursor(Qt.CursorShape.PointingHandCursor)
            orig_sku_press = getattr(sku_chip, "mousePressEvent", None)
            def _on_sku_press(e, s=sku_val):
                if e.button() == Qt.MouseButton.LeftButton:
                    copy_pure_text(s)
                if callable(orig_sku_press):
                    orig_sku_press(e)
            sku_chip.mousePressEvent = _on_sku_press
            make_selectable_label(sku_chip, fallback_getter=lambda s=sku_val: s, tooltip=f"鼠标选择即可复制 (或右键复制纯SKU: {sku_val})")
            row3.addWidget(sku_chip)

        lbl_price = QLabel(amt_text)
        lbl_price.setStyleSheet("color: #34D399; font-size: 11px; font-weight: bold;")
        lbl_price.setMinimumWidth(0)
        lbl_price.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lbl_price.customContextMenuRequested.connect(
            lambda pos, p=amt_text, w=lbl_price: show_pure_copy_menu(w, pos, lambda: p)
        )
        row3.addWidget(lbl_price)

        qty_lbl = QLabel(f"× {order.total_quantity}")
        qty_lbl.setStyleSheet("color: #FCD34D; font-size: 11px; font-weight: bold; background: rgba(245, 158, 11, 0.15); border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 3px; padding: 1px 4px;")
        qty_lbl.setMinimumWidth(0)
        qty_lbl.setToolTip(f"购买件数: {order.total_quantity} 件")
        row3.addWidget(qty_lbl)

        if var_attrs:
            zh_var_attrs = translate_variation_attributes(var_attrs)
            var_chip = ElidedLabel(f"🎨 {zh_var_attrs}")
            var_chip.setStyleSheet("""
                QLabel {
                    color: #60A5FA;
                    font-size: 11px;
                    font-weight: 500;
                    background: rgba(59, 130, 246, 0.12);
                    border: 1px solid rgba(59, 130, 246, 0.35);
                    border-radius: 3px;
                    padding: 1px 4px;
                }
                QLabel:hover {
                    background: rgba(59, 130, 246, 0.22);
                    color: #93C5FD;
                }
            """)
            var_chip.setMinimumWidth(0)
            var_chip.setCursor(Qt.CursorShape.PointingHandCursor)
            orig_var_press = getattr(var_chip, "mousePressEvent", None)
            def _on_var_press(e, v=zh_var_attrs):
                if e.button() == Qt.MouseButton.LeftButton:
                    copy_pure_text(v)
                if callable(orig_var_press):
                    orig_var_press(e)
            var_chip.mousePressEvent = _on_var_press
            tooltip_text = f"规格属性: {zh_var_attrs}"
            if zh_var_attrs != var_attrs:
                tooltip_text += f"\n原规格: {var_attrs}"
            tooltip_text += " (鼠标选择即可复制，右键复制)"
            make_selectable_label(var_chip, fallback_getter=lambda v=zh_var_attrs: v, tooltip=tooltip_text)
            row3.addWidget(var_chip)
        else:
            single_chip = QLabel("标准单品")
            single_chip.setStyleSheet("""
                QLabel {
                    color: #A8A29E;
                    font-size: 10px;
                    background: rgba(255, 255, 255, 0.05);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 3px;
                    padding: 1px 4px;
                }
            """)
            single_chip.setToolTip("该商品在平台为标准单品，无多变体规格")
            row3.addWidget(single_chip)

        if order.has_shipping_adjustment:
            adj_chip = QLabel("⚠️ 运费调整")
            adj_chip.setStyleSheet("""
                QLabel {
                    color: #F87171;
                    font-size: 10px;
                    font-weight: 500;
                    background: rgba(239, 68, 68, 0.15);
                    border: 1px solid rgba(239, 68, 68, 0.35);
                    border-radius: 3px;
                    padding: 1px 4px;
                }
            """)
            adj_chip.setToolTip(f"{order.shipping_adjustment_text}\n点击查看订单明细")
            row3.addWidget(adj_chip)

        row3.addStretch(1)
        info_layout.addLayout(row3)

        card_layout.addLayout(info_layout, 1)

        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        disp_oid = order.display_order_id
        if disp_oid != order.order_id:
            widget.setToolTip(f"销售主单: #{disp_oid}\n内部子单: {order.order_id}\n(右键可快速复制订单号)")
        else:
            widget.setToolTip(f"订单号: {order.order_id} (右键可快速复制订单号)")
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, oid=disp_oid, w=widget: show_pure_copy_menu(w, pos, lambda: oid)
        )

        def _on_card_click(event, oid=order.order_id):
            if event.button() == Qt.MouseButton.LeftButton:
                for r in range(self.order_list.count()):
                    it = self.order_list.item(r)
                    if it and it.data(Qt.ItemDataRole.UserRole) == oid:
                        self.order_list.setCurrentRow(r)
                        break
        widget.mousePressEvent = _on_card_click

        return widget

    def _get_full_shipping_text(self) -> str:
        if not self._current_order:
            return ""
        sh = self._current_order.shipping
        lines = [
            f"收件人: {sh.receiver_name or '-'}",
            f"电话: {sh.receiver_phone or '-'}",
            f"详细地址: {sh.full_address or '-'}",
        ]
        if sh.comment:
            lines.append(f"备注: {sh.comment}")
        return "\n".join(lines)

    def _toggle_select_all(self, checked: bool) -> None:
        if checked:
            for o in self._filtered_orders:
                self._selected_order_ids.add(o.order_id)
        else:
            self._selected_order_ids.clear()
        self._update_print_button_state()
        for i in range(self.order_list.count()):
            item = self.order_list.item(i)
            w = self.order_list.itemWidget(item)
            if w:
                cb = w.findChild(QCheckBox)
                if cb:
                    cb.blockSignals(True)
                    cb.setChecked(checked)
                    cb.blockSignals(False)

    def _update_print_button_state(self) -> None:
        count = len(self._selected_order_ids)
        self.print_labels_btn.setEnabled(count > 0)
        tot = len(self._filtered_orders)
        if count > 0:
            self.count_lbl.setText(f"共找到 {tot} 笔订单 (已选 {count} 笔)")
        else:
            self.count_lbl.setText(f"共找到 {tot} 笔订单")
        if self._filtered_orders and len(self._selected_order_ids) >= len(self._filtered_orders):
            self.select_all_cb.blockSignals(True)
            self.select_all_cb.setChecked(True)
            self.select_all_cb.blockSignals(False)
        elif not self._selected_order_ids:
            self.select_all_cb.blockSignals(True)
            self.select_all_cb.setChecked(False)
            self.select_all_cb.blockSignals(False)

    def _print_current_order_label(self) -> None:
        if not self._current_order:
            QMessageBox.warning(self, "提示", "请先选择需要打印面单的订单。")
            return
        self._do_print_shipment_labels([self._current_order])

    def _on_contact_buyer_clicked(self) -> None:
        if not self._current_order:
            QMessageBox.warning(self, "提示", "请先选择需要联系买家的订单。")
            return
        self.contactBuyerRequested.emit(self._current_order)

    def _print_selected_labels(self) -> None:
        if not self._selected_order_ids:
            QMessageBox.warning(self, "提示", "请先勾选需要打印面单的订单。")
            return
        selected = [o for o in self._all_orders if o.order_id in self._selected_order_ids]
        if not selected:
            QMessageBox.warning(self, "提示", "未找到选中的有效订单。")
            return
        self._do_print_shipment_labels(selected)

    def _do_print_shipment_labels(self, orders: list[OrderModel]) -> None:
        """Fetch official PDF shipping labels and open with system default PDF viewer for instant printing."""
        valid_orders = [o for o in orders if o.shipping.shipment_id]
        if not valid_orders:
            QMessageBox.warning(self, "提示", "所选订单均无有效的官方物流货运单号 (shipment_id)，无法拉取官方面单。")
            return

        by_account: dict[str, list[str]] = {}
        for o in valid_orders:
            aid = o.account_id
            sid = str(o.shipping.shipment_id).strip()
            if aid and sid:
                by_account.setdefault(aid, []).append(sid)

        if not by_account:
            QMessageBox.warning(self, "提示", "未识别到店铺授权身份，无法拉取官方面单。")
            return

        total_pdf_data: list[bytes] = []
        labels_dir = get_data_dir() / "labels"
        labels_dir.mkdir(parents=True, exist_ok=True)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for aid, sids in by_account.items():
                try:
                    pdf_bytes = self.client.get_shipment_labels_pdf(aid, sids)
                    if pdf_bytes and len(pdf_bytes) > 10:
                        total_pdf_data.append(pdf_bytes)
                except Exception as e:
                    logger.error("Failed fetching shipment label PDF for account %s: %s", aid, e)
                    QMessageBox.warning(self, "打印提示", f"拉取店铺 ({aid}) 面单失败：\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

        if not total_pdf_data:
            QMessageBox.critical(self, "打印失败", "未能从美客多官方接口获取到有效的 PDF 面单数据。")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            for idx, pdf_bytes in enumerate(total_pdf_data):
                suffix = f"_{idx + 1}" if len(total_pdf_data) > 1 else ""
                pdf_path = labels_dir / f"shipment_labels_{timestamp}{suffix}.pdf"
                with open(pdf_path, "wb") as f:
                    f.write(pdf_bytes)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf_path.resolve())))
        except Exception as e:
            logger.error("Failed saving/opening PDF labels: %s", e)
            QMessageBox.critical(self, "打开面单失败", f"面单保存或预览启动失败：\n{e}")
        except Exception as e:
            logger.error("Failed saving/opening PDF labels: %s", e)
            QMessageBox.critical(self, "打开面单失败", f"面单保存或预览启动失败：\n{e}")

    def _load_async_thumbnail(self, url: str, label: QLabel) -> None:
        """Load and scale product thumbnail smoothly with disk & memory cache."""
        clean_url = str(url or "").strip()
        if not clean_url:
            label.setPixmap(get_placeholder_pixmap(QSize(48, 48), "📦", radius=6))
            return

        def _on_loaded(pix: QPixmap) -> None:
            label.setPixmap(pix)

        load_image_async(clean_url, QSize(48, 48), _on_loaded, radius=6, crop=True)

    def _on_order_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            self._render_empty_detail()
            return
        oid = current.data(Qt.ItemDataRole.UserRole)
        matched = [o for o in self._filtered_orders if o.order_id == oid]
        if matched:
            self._current_order = matched[0]
            self._render_order_detail(self._current_order)
        else:
            self._render_empty_detail()

    def _render_order_detail(self, order: OrderModel) -> None:
        """Render selected order details in right drawer."""
        site_code = f" · {order.site_id}" if order.site_id else ""
        self.det_store_badge.setText(f"{order.store_name}{site_code}")
        deadline_info = order.shipping_deadline_info
        if deadline_info and hasattr(self, "det_sla_badge"):
            dl_text, dl_color, dl_bg, dl_border = deadline_info
            self.det_sla_badge.setText(f" {dl_text} ")
            self.det_sla_badge.setStyleSheet(f"""
                QLabel {{
                    color: {dl_color};
                    background: {dl_bg};
                    border: {dl_border};
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 2px 6px;
                }}
            """)
            self.det_sla_badge.setToolTip("发货截止时效倒计时 (美客多跨境履约 SLA 预警)")
            self.det_sla_badge.show()
        elif hasattr(self, "det_sla_badge"):
            self.det_sla_badge.hide()

        disp_oid = order.display_order_id
        if order.pack_id and order.pack_id != order.order_id and str(order.pack_id).startswith("20000"):
            self.det_order_id_lbl.setText(f"总订单号: {order.pack_id}  (子单号: {order.order_id})")
        else:
            self.det_order_id_lbl.setText(f"订单号: {disp_oid}")
        created_str = order.display_time or "未知"
        self.det_dates_lbl.setText(f"下单时间: {created_str}")

        # Render modular items in overview card
        while self.items_container_layout.count():
            item_elem = self.items_container_layout.takeAt(0)
            w = item_elem.widget()
            if w:
                w.deleteLater()

        items_to_show = order.items if order.items else [order.primary_item]
        for it in items_to_show:
            if not it.item_id and not it.title:
                continue
            item_row = QWidget()
            item_row.setStyleSheet("background: #171D1A; border: 1px solid rgba(78, 71, 47, 0.4); border-radius: 6px;")
            i_layout = QHBoxLayout(item_row)
            i_layout.setContentsMargins(8, 8, 8, 8)
            i_layout.setSpacing(10)

            # Thumbnail
            t_lbl = QLabel()
            t_lbl.setFixedSize(48, 48)
            t_lbl.setStyleSheet("background: #121614; border: 1px solid #4E472F; border-radius: 4px;")
            t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            t_lbl.setToolTip("点击放大查看大图 (右键可直接复制图片)")
            pic_url = it.thumbnail or order.primary_thumbnail
            self._load_async_thumbnail(pic_url, t_lbl)
            def _make_zoom_handler(u=pic_url, t=it.title):
                return lambda e: self._show_image_lightbox(u, t) if e.button() == Qt.MouseButton.LeftButton and u else None
            t_lbl.mousePressEvent = _make_zoom_handler()
            t_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            t_lbl.customContextMenuRequested.connect(lambda pos, lbl=t_lbl: show_pure_copy_menu(lbl, pos, lambda: lbl.pixmap()))
            i_layout.addWidget(t_lbl, 0, Qt.AlignmentFlag.AlignTop)

            # Center text column
            c_layout = QVBoxLayout()
            c_layout.setContentsMargins(0, 0, 0, 0)
            c_layout.setSpacing(4)

            t_title = QLabel(it.title or f"商品: {it.item_id}")
            t_title.setStyleSheet("""
                QLabel { color: #F6F3EA; font-size: 12px; font-weight: 500; }
                QLabel:hover { color: #60A5FA; text-decoration: underline; }
            """)
            t_title.setWordWrap(True)
            t_title.setCursor(Qt.CursorShape.PointingHandCursor)
            t_title.setToolTip("点击在浏览器打开前台售卖页 (右键复制商品编号)")
            t_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            def _make_item_url_handler(item_ref=it, sid=order.site_id, aid=order.account_id):
                return lambda e: self._open_item_url(item_ref, sid, aid) if e.button() == Qt.MouseButton.LeftButton else None
            t_title.mousePressEvent = _make_item_url_handler()
            t_title.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            t_title.customContextMenuRequested.connect(lambda pos, lbl=t_title, item_ref=it: show_pure_copy_menu(lbl, pos, lambda: label_copy_val(lbl, item_ref.item_id)))
            c_layout.addWidget(t_title)

            meta_h = QHBoxLayout()
            meta_h.setContentsMargins(0, 0, 0, 0)
            meta_h.setSpacing(6)

            # Item ID tag (click or right click to copy item_id)
            if it.item_id:
                id_lbl = QLabel(it.item_id)
                id_lbl.setStyleSheet("""
                    QLabel {
                        color: #93C5FD;
                        font-size: 11px;
                        background: rgba(59, 130, 246, 0.12);
                        border: 1px solid rgba(59, 130, 246, 0.35);
                        border-radius: 3px;
                        padding: 1px 4px;
                    }
                    QLabel:hover {
                        background: rgba(59, 130, 246, 0.25);
                        color: #BFDBFE;
                    }
                """)
                id_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                orig_id_press = getattr(id_lbl, "mousePressEvent", None)
                def _on_id_press(e, iid=it.item_id):
                    if e.button() == Qt.MouseButton.LeftButton:
                        copy_pure_text(iid)
                    if callable(orig_id_press):
                        orig_id_press(e)
                id_lbl.mousePressEvent = _on_id_press
                make_selectable_label(id_lbl, fallback_getter=lambda iid=it.item_id: iid, tooltip="点击或右键复制商品编号 (鼠标选择即可复制)")
                meta_h.addWidget(id_lbl)

            if it.sku:
                s_lbl = QLabel(f"SKU: {it.sku}")
                s_lbl.setStyleSheet("""
                    QLabel {
                        color: #D4AF37;
                        font-size: 11px;
                        background: rgba(212, 175, 55, 0.12);
                        border: 1px solid rgba(212, 175, 55, 0.35);
                        border-radius: 3px;
                        padding: 1px 4px;
                    }
                    QLabel:hover {
                        background: rgba(212, 175, 55, 0.25);
                        color: #FCD34D;
                    }
                """)
                s_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                make_selectable_label(s_lbl, fallback_getter=lambda s=it.sku: s, tooltip="鼠标选择即可复制 (或右键复制SKU)")
                meta_h.addWidget(s_lbl)

            if it.variation_attributes:
                zh_va = translate_variation_attributes(it.variation_attributes)
                v_lbl = QLabel(f"🎨 {zh_va}")
                v_lbl.setStyleSheet("""
                    QLabel {
                        color: #60A5FA;
                        font-size: 11px;
                        font-weight: 500;
                        background: rgba(59, 130, 246, 0.12);
                        border: 1px solid rgba(59, 130, 246, 0.35);
                        border-radius: 3px;
                        padding: 1px 5px;
                    }
                    QLabel:hover {
                        background: rgba(59, 130, 246, 0.22);
                        color: #93C5FD;
                    }
                """)
                v_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                det_tip = f"规格属性: {zh_va}"
                if zh_va != it.variation_attributes:
                    det_tip += f"\n原规格: {it.variation_attributes}"
                det_tip += " (鼠标选择即可复制，右键复制)"
                make_selectable_label(v_lbl, fallback_getter=lambda va=zh_va: va, tooltip=det_tip)
                meta_h.addWidget(v_lbl)
            else:
                v_lbl = QLabel("标准单品")
                v_lbl.setStyleSheet("""
                    QLabel {
                        color: #A8A29E;
                        font-size: 11px;
                        background: rgba(255, 255, 255, 0.05);
                        border: 1px solid rgba(255, 255, 255, 0.12);
                        border-radius: 3px;
                        padding: 1px 5px;
                    }
                """)
                v_lbl.setToolTip("该商品为标准单品，无多变体规格")
                meta_h.addWidget(v_lbl)

            if it.dimensions:
                d_lbl = QLabel(f"📦 {it.dimensions}")
                d_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
                meta_h.addWidget(d_lbl)

            meta_h.addStretch()
            c_layout.addLayout(meta_h)
            i_layout.addLayout(c_layout, 1)

            # Right quantity & price
            r_layout = QVBoxLayout()
            r_layout.setContentsMargins(0, 0, 0, 0)
            r_layout.setSpacing(2)
            r_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            q_lbl = QLabel(f"数量: × {it.quantity}")
            q_lbl.setStyleSheet("color: #FCD34D; font-size: 13px; font-weight: bold;")
            q_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            r_layout.addWidget(q_lbl)

            if it.unit_price > 0:
                p_lbl = QLabel(f"{order.currency_id} {it.unit_price:.2f}")
                p_lbl.setStyleSheet("color: #34D399; font-size: 11px; font-weight: 500;")
                p_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
                r_layout.addWidget(p_lbl)

            i_layout.addLayout(r_layout)
            self.items_container_layout.addWidget(item_row)

        # Address & Buyer
        buyer_nick = order.buyer_nickname or "买家"
        rec_name = order.shipping.receiver_name or order.buyer_full_name or "未填写"
        phone = order.shipping.receiver_phone or "无"
        self.det_buyer_info_lbl.setText(f"买家昵称: <b>{buyer_nick}</b>  ·  收件人: <b>{rec_name}</b>  ·  电话: <b>{phone}</b>")
        if hasattr(self, "btn_contact_buyer"):
            self.btn_contact_buyer.setEnabled(bool(order and order.order_id))

        addr = order.shipping.full_address or "官方集运仓发货 / 无详细街道地址"
        if order.shipping.comment:
            addr += f" ({order.shipping.comment})"
        self.det_address_lbl.setText(f"详细地址: {addr}")

        # Shipping
        ship_mode = order.shipping.logistic_type or "官方物流"
        substatus = order.shipping.substatus or order.shipping.status or "正常运输"
        carrier = order.shipping.tracking_method or "Mercado Envíos"
        meta_parts = [f"物流方式: {ship_mode}  ·  承运商: {carrier}  ·  最新状态: {substatus}"]
        timeline_parts = []
        if order.shipping.estimated_delivery_date:
            timeline_parts.append(f"买家承诺送达: {order.shipping.estimated_delivery_date[:10]}")
        if order.shipping.estimated_delivery_offset:
            timeline_parts.append(f"最晚妥投保证: {order.shipping.estimated_delivery_offset[:10]}")
        if timeline_parts:
            meta_parts.append("  ·  ".join(timeline_parts))
        self.det_ship_meta_lbl.setText("\n".join(meta_parts))

        if order.pack_id and str(order.pack_id).startswith("20000"):
            self.det_pack_chip.setText(f"销售主单: #{order.pack_id}")
        else:
            pid_text = order.pack_id or "无包裹号"
            self.det_pack_chip.setText(f"包裹号: {pid_text}")

        tid_text = order.shipping.tracking_number or "暂无运单号"
        self.det_track_chip.setText(f"运单号: {tid_text}")

        dim_text = order.shipping.dimensions or "未记录"
        self.det_dimensions_chip.setText(f"📦 出单包装尺重: {dim_text}")

        if hasattr(self, "det_actual_dimensions_chip"):
            if order.shipping.actual_dimensions:
                self.det_actual_dimensions_chip.setText(f"⚖️ 官方实测复重: {order.shipping.actual_dimensions}")
                self.det_actual_dimensions_chip.setStyleSheet("""
                    QLabel {
                        color: #60A5FA;
                        font-size: 11px;
                        font-weight: 600;
                        background: rgba(59, 130, 246, 0.15);
                        border: 1px solid rgba(59, 130, 246, 0.4);
                        border-radius: 4px;
                        padding: 2px 8px;
                    }
                """)
                self.det_actual_dimensions_chip.setToolTip("集运仓 DWS 自动化设备扫码实测数据（长宽高及物理实重）")
                self.det_actual_dimensions_chip.setVisible(True)
            elif order.has_shipping_adjustment:
                self.det_actual_dimensions_chip.setText(f"⚠️ 官方复测超标 (产生运费补扣 +${order.shipping.shipping_adjustment:g})")
                self.det_actual_dimensions_chip.setStyleSheet("""
                    QLabel {
                        color: #F87171;
                        font-size: 11px;
                        font-weight: 500;
                        background: rgba(239, 68, 68, 0.12);
                        border: 1px solid rgba(239, 68, 68, 0.35);
                        border-radius: 4px;
                        padding: 2px 8px;
                    }
                """)
                self.det_actual_dimensions_chip.setToolTip(f"该包裹在集运仓复测超标，官方产生运费调整补扣 +${order.shipping.shipping_adjustment:g}")
                self.det_actual_dimensions_chip.setVisible(True)
            else:
                self.det_actual_dimensions_chip.setVisible(False)

        # Payments & Financial Breakdown
        paid_val = order.paid_amount if order.paid_amount > 0 else order.total_amount
        cur = order.currency_id or "USD"
        self.det_fee_paid_val.setText(f"{cur} {paid_val:.2f}")

        # Platform Commission / Sale fee
        fee_val = order.total_sale_fee
        if fee_val > 0:
            self.det_fee_sale_val.setText(f"-{cur} {fee_val:.2f}")
        else:
            self.det_fee_sale_val.setText("免佣金/未记录")

        # Carrier shipping cost
        ship_val = order.effective_shipping_cost
        if ship_val > 0:
            self.det_fee_ship_val.setText(f"-{cur} {ship_val:.2f}")
        else:
            self.det_fee_ship_val.setText("免运费/未记录")

        if hasattr(self, "det_fee_ship_adj_lbl"):
            if order.has_shipping_adjustment:
                b = order.shipping.base_shipping_cost or round(order.effective_shipping_cost - order.shipping.shipping_adjustment, 2)
                self.det_fee_ship_adj_lbl.setText(f"原 ${b:g} + 补扣 +${order.shipping.shipping_adjustment:g}")
                self.det_fee_ship_adj_lbl.setToolTip(f"原始出单运费: ${b:g}，因复测超标补扣 ${order.shipping.shipping_adjustment:g}")
                self.det_fee_ship_adj_lbl.setVisible(True)
            else:
                self.det_fee_ship_adj_lbl.setVisible(False)

        if hasattr(self, "det_shipping_adj_banner"):
            self.det_shipping_adj_banner.setVisible(False)

        # Estimated net profit with RMB conversion
        net_val = order.estimated_net_profit
        rate, rate_src = get_cny_exchange_rate(cur)
        if rate > 0 and cur.upper() not in ("CNY", "RMB"):
            cny_val = round(net_val * rate, 2)
            if cny_val < 0:
                self.det_fee_net_val.setText(f"{cur} {net_val:.2f} (约 -¥{abs(cny_val):.2f})")
            else:
                self.det_fee_net_val.setText(f"{cur} {net_val:.2f} (约 ¥{cny_val:.2f})")
            self.det_fee_net_val.setToolTip(f"折算汇率: 1 {cur} ≈ {rate:.4f} CNY ({rate_src})")
        else:
            self.det_fee_net_val.setText(f"{cur} {net_val:.2f}")
            self.det_fee_net_val.setToolTip("")

        p_info = order.payments[0] if order.payments else PaymentInfoModel()
        pm = p_info.payment_method or "Mercado Pago"
        pid = p_info.payment_id or "无"
        taxes_str = f"  ·  税费: {cur} {order.taxes_amount:.2f}" if order.taxes_amount > 0 else ""
        self.det_pay_detail_lbl.setText(f"成交额: {cur} {paid_val:.2f}  ·  支付渠道: {pm}  ·  流水单号: {pid}{taxes_str}")

    def _open_item_url(self, item: OrderItemModel, site_id: str = "", account_id: str = "") -> None:
        """Open official buyer front-end selling page (permalink) with on-demand lookup and hyphen fallback."""
        url = (item.permalink or "").strip()
        aid = account_id or (self._current_order.account_id if self._current_order else "")
        if not url and self.client and aid and item.item_id:
            try:
                det = self.client.get_item_detail(aid, item.item_id)
                if det and det.get("permalink"):
                    url = str(det["permalink"]).strip()
                    item.permalink = url
                    self._save_local_snapshot()
            except Exception as e:
                logger.debug("Failed on-demand fetching permalink for %s: %s", item.item_id, e)

        if not url:
            clean_id = item.item_id.strip()
            prefix = clean_id[:3].upper() if len(clean_id) >= 3 else ""
            num_part = clean_id[3:] if len(clean_id) > 3 else clean_id
            domain = SITE_DOMAINS.get(prefix) or SITE_DOMAINS.get(site_id, "https://articulo.mercadolibre.com.mx")
            if num_part and prefix in SITE_DOMAINS:
                url = f"{domain}/{prefix}-{num_part}"
            else:
                url = f"{domain}/{clean_id}"

        if url:
            try:
                webbrowser.open(url)
            except Exception as e:
                logger.warning("Failed opening browser for url %s: %s", url, e)

    def _load_async_btn_pixmap(self, url: str, btn: QPushButton) -> None:
        """Load button thumbnail smoothly with disk & memory cache."""
        clean_url = str(url or "").strip()
        if not clean_url:
            btn.setText("📦")
            return

        def _on_loaded(pix: QPixmap) -> None:
            btn.setIcon(QIcon(pix))
            btn.setIconSize(QSize(50, 50))

        load_image_async(clean_url, QSize(54, 54), _on_loaded, radius=4, crop=True)

    def _show_image_lightbox(self, url: str, title: str) -> None:
        """Display high-resolution image preview using service center preview component."""
        show_image_preview(url, title=title or "商品大图", parent=self.window())

    def _on_order_id_clicked(self, event) -> None:
        if not self._current_order:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_order_in_browser()

    def _on_item_chip_clicked(self, event, item_id: str, site_id: str) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item_mock = OrderItemModel(item_id=item_id)
            aid = self._current_order.account_id if self._current_order else ""
            self._open_item_url(item_mock, site_id, aid)

    def _open_order_in_browser(self) -> None:
        if not self._current_order:
            return
        oid = self._current_order.order_id
        url = f"https://myaccount.mercadolibre.com.mx/sales/{oid}/detail"
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _render_empty_detail(self) -> None:
        self.det_store_badge.setText("-")
        if hasattr(self, "det_sla_badge"):
            self.det_sla_badge.hide()
        self.det_order_id_lbl.setText("订单号: -")
        self.det_dates_lbl.setText("下单时间: -")
        while self.items_container_layout.count():
            item_elem = self.items_container_layout.takeAt(0)
            w = item_elem.widget()
            if w:
                w.deleteLater()
        self.det_buyer_info_lbl.setText("收件人: - · 电话: -")
        self.det_address_lbl.setText("详细地址: -")
        self.det_ship_meta_lbl.setText("物流方式: -")
        self.det_pack_chip.setText("包裹号: -")
        self.det_track_chip.setText("运单号: -")
        self.det_dimensions_chip.setText("📦 出单包装尺重: -")
        if hasattr(self, "det_actual_dimensions_chip"):
            self.det_actual_dimensions_chip.setText("")
            self.det_actual_dimensions_chip.setVisible(False)
        if hasattr(self, "det_fee_ship_adj_lbl"):
            self.det_fee_ship_adj_lbl.setText("")
            self.det_fee_ship_adj_lbl.setVisible(False)
        if hasattr(self, "det_shipping_adj_banner"):
            self.det_shipping_adj_banner.setText("")
            self.det_shipping_adj_banner.setVisible(False)
        self.det_fee_paid_val.setText("USD 0.00")
        self.det_fee_sale_val.setText("-USD 0.00")
        self.det_fee_ship_val.setText("-USD 0.00")
        self.det_fee_net_val.setText("USD 0.00 (约 ¥0.00)")
        self.det_pay_detail_lbl.setText("支付方式: - · 支付流水号: -")
        if hasattr(self, "btn_contact_buyer"):
            self.btn_contact_buyer.setEnabled(False)

    def select_order(self, order_id: str) -> bool:
        """Select order by order_id programmatically (for cross-module navigation)."""
        oid_str = str(order_id).strip()
        if not oid_str:
            return False
        if self.search_input.text():
            self.search_input.clear()

        for r in range(self.order_list.count()):
            it = self.order_list.item(r)
            if it and str(it.data(Qt.ItemDataRole.UserRole)) == oid_str:
                self.order_list.setCurrentRow(r)
                return True
        return False

    def _export_orders(self) -> None:
        """Export current filtered order list to CSV file."""
        if not self._filtered_orders:
            QMessageBox.information(self, "提示", "当前没有可导出的订单数据。")
            return

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出订单数据",
            f"美客多订单导出_{now_str}.csv",
            "CSV 电子表格 (*.csv)",
        )
        if not file_path:
            return

        try:
            count = self.repository.export_to_csv(self._filtered_orders, file_path)
            QMessageBox.information(self, "导出成功", f"成功导出 {count} 笔订单数据至：\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出文件时发生异常：\n{e}")

    def _on_countdown_tick(self) -> None:
        """Tick countdown badges without re-fetching network data."""
        if not self.isVisible():
            return

        # 1. Update right detail SLA badge
        if self._current_order:
            deadline_info = self._current_order.shipping_deadline_info
            if deadline_info and hasattr(self, "det_sla_badge"):
                dl_text, dl_color, dl_bg, dl_border = deadline_info
                self.det_sla_badge.setText(f" {dl_text} ")
                self.det_sla_badge.setStyleSheet(f"""
                    QLabel {{
                        color: {dl_color};
                        background: {dl_bg};
                        border: {dl_border};
                        border-radius: 4px;
                        font-size: 11px;
                        font-weight: 600;
                        padding: 2px 6px;
                    }}
                """)
                self.det_sla_badge.show()
            elif hasattr(self, "det_sla_badge"):
                self.det_sla_badge.hide()

        # 2. Synchronously update left order list SLA badges
        for oid, (badge, ord_item) in list(self._list_sla_badges.items()):
            try:
                deadline_info = ord_item.shipping_deadline_info
                if deadline_info:
                    dl_text, dl_color, dl_bg, dl_border = deadline_info
                    badge.setText(f" {dl_text} ")
                    badge.setStyleSheet(f"""
                        QLabel {{
                            color: {dl_color};
                            background: {dl_bg};
                            border: {dl_border};
                            border-radius: 3px;
                            font-size: 11px;
                            font-weight: 600;
                            padding: 1px 4px;
                        }}
                    """)
                    badge.show()
                else:
                    badge.hide()
            except Exception:
                pass

    def _on_zhiying_sync_packet(self) -> None:
        """Handle incoming Zhiying sync packet on Qt GUI thread."""
        try:
            audits_map = get_all_shipping_audits()
            if not audits_map:
                return
            updated_any = False
            for o in self._all_orders:
                aud = audits_map.get(o.order_id) or audits_map.get(o.shipping.shipment_id)
                if aud and o.shipping.actual_dimensions != aud:
                    o.shipping.actual_dimensions = aud
                    updated_any = True
            if updated_any and self._current_order:
                cur_aud = audits_map.get(self._current_order.order_id) or audits_map.get(self._current_order.shipping.shipment_id)
                if cur_aud:
                    self._current_order.shipping.actual_dimensions = cur_aud
                    self._render_order_details(self._current_order)
        except Exception as e:
            logger.debug("Failed to handle zhiying sync packet update: %s", e)

    def stop(self) -> None:
        """Safely stop any ongoing background workers on application close."""
        if hasattr(self, "_zhiying_receiver") and self._zhiying_receiver:
            try:
                self._zhiying_receiver.stop()
            except Exception:
                pass
        if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
            self._countdown_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(1500)
