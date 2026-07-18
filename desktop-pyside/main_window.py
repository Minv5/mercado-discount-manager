from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from api_client import ApiClient, ApiError
from core import (
    EXCLUDE_ACTIVITY,
    Account,
    ActionConflictError,
    account_from_json,
    action_for_mode,
    action_label,
    business_date_from_timestamp,
    build_filters,
    completed_execution_for_scope,
    confirmation_text,
    discount_inputs_enabled,
    execution_completion_text,
    execution_group_payload,
    normalize_activity_name,
    promotion_bucket,
    promotion_display_name,
    resolve_global_action,
    site_name,
    task_display_counts,
)
from dialogs import ConfirmDialog, DetailsDialog, SellerCampaignCreateDialog, SettingsDialog, target_label
from diagnostics import diagnostic_event
from service_manager import NodeServiceManager, ServiceError
from theme import APP_QSS
from workers import Worker


TASK_HEADERS = ["时间", "动作", "活动", "类型", "商品（唯一/活动关系）", "结果", "失败（商品/活动）", "失败原因"]
ACTIVITY_HEADERS = ["店铺", "站点", "类型", "活动", "状态", "商品数"]
RECORD_VIEW_LIMITS = {"recent": 20, "all": 300}


class MainWindow(QMainWindow):
    ready = Signal()

    def __init__(self, api: ApiClient, service: NodeServiceManager, *, auto_start: bool = True):
        super().__init__()
        self.api = api
        self.service = service
        self.thread_pool = QThreadPool.globalInstance()
        self.workers: set[Worker] = set()
        self.settings: dict[str, Any] = {}
        self.accounts: list[Account] = []
        self.store_map: dict[str, list[str]] = {}
        self.promotions: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.records_cache: dict[str, list[dict[str, Any]]] = {}
        self.records_view = "recent"
        self.today_execution_groups: list[dict[str, Any]] = []
        self.current_today_completion: dict[str, Any] | None = None
        self.today_completion_ready = False
        self.today_completion_request_token = 0
        self.operating_rows_cache: list[dict[str, Any]] = []
        self.benchmark_text_cache = "自动并发按实测和接口反馈调整。"
        self.global_seller_discount = 5
        self.global_official_discount = 6
        self.auto_action = ""
        self.scope_refresh_token = 0
        self.auto_decision_token = 0
        self.scope_ready = False
        self.running_group: dict[str, Any] = {}
        self.pending_group_payload: dict[str, Any] | None = None
        self.preparing_submission: dict[str, Any] = {}
        self.pending_prepare_payload: dict[str, Any] | None = None
        self.prepare_poll_busy = False
        self.prepare_poll_failure_count = 0
        self.prepare_progress_key = ""
        self.job_log_counts: dict[str, int] = {}
        self.poll_failure_count = 0
        self.commit_recovery_poll_count = 0
        self.poll_busy = False
        self.records_request_token = 0
        self.ui_busy = False
        self._closing = False
        self._build_ui()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(900)
        self.poll_timer.timeout.connect(self._poll_group)
        self.prepare_poll_timer = QTimer(self)
        self.prepare_poll_timer.setInterval(1000)
        self.prepare_poll_timer.timeout.connect(self._poll_prepare)
        if auto_start:
            QTimer.singleShot(0, self.startup)

    def _build_ui(self) -> None:
        self.setWindowTitle("美客多活动管家")
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)
        icon_path = resource_path("assets/app.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)
        root.addWidget(self._build_header())

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setChildrenCollapsible(False)
        controls = self._build_controls()
        controls.setMinimumWidth(310)
        controls.setMaximumWidth(360)
        workspace.addWidget(controls)

        right = QSplitter(Qt.Orientation.Vertical)
        right.setChildrenCollapsible(False)
        self.pages = QStackedWidget()
        self.records_page, self.records_table = self._build_records_page()
        self.activity_page, self.activity_table = self._build_activity_page()
        self.pages.addWidget(self.records_page)
        self.pages.addWidget(self.activity_page)
        right.addWidget(self.pages)
        right.addWidget(self._build_log_surface())
        right.setSizes([610, 190])
        workspace.addWidget(right)
        workspace.setSizes([330, 1050])
        root.addWidget(workspace, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("正在准备工作台...")

    def _surface(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("surface")
        return frame

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("brandSurface")
        header.setFixedHeight(84)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(44, 44)
        pixmap = QIcon(str(resource_path("assets/app-icon.png"))).pixmap(QSize(44, 44))
        icon.setPixmap(pixmap)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_text.setContentsMargins(0, 0, 0, 0)
        title = QLabel("美客多活动管家")
        title.setObjectName("brandTitle")
        subtitle = QLabel("批量管理美客多促销与折扣活动")
        subtitle.setObjectName("brandSubtitle")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        layout.addLayout(brand_text)
        layout.addStretch(1)
        self.nav_buttons: list[QPushButton] = []
        for label, page in (("工作台", 0), ("活动管理", 1)):
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setFixedHeight(36)
            button.clicked.connect(lambda checked=False, index=page: self._show_page(index))
            self.nav_buttons.append(button)
            layout.addWidget(button)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("nav")
        self.settings_button.setFixedHeight(36)
        self.settings_button.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_button)
        self.nav_buttons[0].setChecked(True)
        return header

    def _build_controls(self) -> QFrame:
        frame = self._surface()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("controlContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        scope_section, scope_layout = self._control_section("执行范围")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["自动判断", "批量报活动", "批量更新", "批量取消"])
        self.store_combo = QComboBox()
        self.site_combo = QComboBox()
        scope_layout.addWidget(field_label("模式"))
        scope_layout.addWidget(self.mode_combo)
        scope_layout.addWidget(field_label("店铺"))
        scope_layout.addWidget(self.store_combo)
        scope_layout.addWidget(field_label("站点"))
        scope_layout.addWidget(self.site_combo)
        content_layout.addWidget(scope_section)

        activity_section, activity_layout = self._control_section("活动参数")
        self.seller_combo = QComboBox()
        self.official_combo = QComboBox()
        self.seller_discount = discount_spin(5)
        self.official_discount = discount_spin(6)
        activity_layout.addWidget(field_label("自建活动"))
        seller_row = QHBoxLayout()
        seller_row.addWidget(self.seller_combo, 1)
        seller_row.addWidget(self.seller_discount)
        activity_layout.addLayout(seller_row)
        activity_layout.addWidget(field_label("官方活动"))
        official_row = QHBoxLayout()
        official_row.addWidget(self.official_combo, 1)
        official_row.addWidget(self.official_discount)
        activity_layout.addLayout(official_row)
        content_layout.addWidget(activity_section)

        today_section, today_layout = self._control_section("今日判断")
        today_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.today_label = QLabel("正在读取今日折扣和当前范围...")
        self.today_label.setWordWrap(True)
        self.today_label.setMinimumHeight(86)
        today_layout.addWidget(self.today_label)
        self.component_label = QLabel("正在启动程序组件...")
        self.component_label.setObjectName("muted")
        today_layout.addWidget(self.component_label)
        today_layout.addStretch(1)
        content_layout.addWidget(today_section, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.execute_button = QPushButton("提交执行")
        self.execute_button.setObjectName("primary")
        self.execute_button.setMinimumHeight(42)
        self.execute_button.clicked.connect(self._on_execute_clicked)
        layout.addWidget(self.execute_button)
        self.mode_combo.currentTextChanged.connect(self._mode_changed)
        self.store_combo.currentIndexChanged.connect(self._scope_changed)
        self.site_combo.currentIndexChanged.connect(self._site_changed)
        self.seller_combo.currentIndexChanged.connect(self._scope_changed)
        self.official_combo.currentIndexChanged.connect(self._scope_changed)
        return frame

    @staticmethod
    def _control_section(title: str) -> tuple[QFrame, QVBoxLayout]:
        section = QFrame()
        section.setObjectName("controlSection")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(12, 10, 12, 12)
        section_layout.setSpacing(7)
        heading = section_label(title)
        heading.ensurePolished()
        heading.setMinimumHeight(heading.fontMetrics().lineSpacing() + 4)
        heading.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        section_layout.addWidget(heading)
        return section, section_layout

    def _build_records_page(self) -> tuple[QFrame, QTableWidget]:
        surface = self._surface()
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(12, 10, 12, 12)
        top = QHBoxLayout()
        top.addWidget(section_label("执行记录"))
        top.addStretch(1)
        self.records_view_combo = QComboBox()
        self.records_view_combo.addItem("最近20", "recent")
        self.records_view_combo.addItem("全部历史", "all")
        self.records_view_combo.setFixedWidth(112)
        self.records_view_combo.currentIndexChanged.connect(self._records_view_changed)
        self.records_refresh_button = QPushButton("刷新")
        self.records_refresh_button.clicked.connect(self.refresh_records)
        QWidget.setTabOrder(self.records_view_combo, self.records_refresh_button)
        top.addWidget(self.records_view_combo)
        top.addWidget(self.records_refresh_button)
        layout.addLayout(top)
        table = make_table(TASK_HEADERS)
        table.horizontalHeaderItem(4).setToolTip(
            "前者是唯一商品，同一商品跨多个活动只计算一次；后者是活动商品关系，同一商品在每个活动中分别计算。"
        )
        table.horizontalHeaderItem(5).setToolTip("批量取消显示取消请求成功、平台确认移除和待平台确认；其它动作显示成功与跳过。")
        table.horizontalHeaderItem(6).setToolTip("前者是商品失败，后者是活动失败；活动失败不计入商品失败。")
        header = table.horizontalHeader()
        for column, width in enumerate((75, 72, 100, 52, 145, 118, 110)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(column, width)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.itemDoubleClicked.connect(lambda _item: self._show_task_details())
        table.itemSelectionChanged.connect(self._show_selected_summary)
        layout.addWidget(table, 1)
        return surface, table

    def _build_activity_page(self) -> tuple[QFrame, QTableWidget]:
        surface = self._surface()
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(12, 10, 12, 12)
        top = QHBoxLayout()
        top.addWidget(section_label("活动管理"))
        top.addStretch(1)
        refresh_local = QPushButton("刷新列表")
        reload_live = QPushButton("重新读取活动")
        refresh_local.clicked.connect(self.refresh_scope)
        reload_live.clicked.connect(self._reload_live_promotions)
        top.addWidget(refresh_local)
        top.addWidget(reload_live)
        layout.addLayout(top)
        table = make_table(ACTIVITY_HEADERS)
        layout.addWidget(table, 1)
        return surface, table

    def _build_log_surface(self) -> QFrame:
        surface = self._surface()
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.addWidget(section_label("运行日志"))
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box, 1)
        return surface

    def startup(self) -> None:
        self._set_busy(True, "正在启动程序组件...")
        self._run_worker(self.service.ensure_started, self._service_ready, self._startup_failed)

    def _service_ready(self, started: object) -> None:
        self.component_label.setText("程序组件已连接")
        self.log("程序组件已连接。" if started else "已连接现有程序组件。")
        self._run_worker(self._load_initial_bundle, self._apply_initial_bundle, self._startup_failed)

    def _load_initial_bundle(self) -> dict[str, Any]:
        return {
            "settings": self.api.get("/api/settings").get("settings", {}),
            "accounts": self.api.get("/api/accounts").get("accounts", []),
            "discount": self.api.get("/api/today/global-discount").get("discount", {}),
            "execution": self.api.get("/api/execution/groups/active", timeout=10),
            "submission": self.api.get("/api/execution/submissions/active", timeout=10),
        }

    def _apply_initial_bundle(self, bundle: object) -> None:
        data = dict(bundle or {})
        self.settings = dict(data.get("settings") or {})
        self.accounts = [account_from_json(row) for row in data.get("accounts") or []]
        self.accounts = [account for account in self.accounts if account.account_id]
        discount = dict(data.get("discount") or {})
        self.global_seller_discount = int(discount.get("seller_discount") or self.settings.get("sellerDefaultDiscount") or 5)
        self.global_official_discount = int(discount.get("official_discount") or self.settings.get("officialDefaultDiscount") or 6)
        self._apply_global_discounts()
        self._fill_store_combo()
        self._set_busy(False, "基础数据已加载，正在读取店铺站点...")
        self.log("基础数据已加载，正在读取店铺站点。")
        active_group = dict(dict(data.get("execution") or {}).get("group") or {})
        if active_group:
            self.log("检测到未完成执行，正在恢复进度。")
            self._group_started({"group": active_group})
        elif dict(data.get("submission") or {}).get("prepare"):
            prepare = dict(dict(data.get("submission") or {}).get("prepare") or {})
            state = str(prepare.get("state") or "")
            if state in {"preparing", "prepared", "reconfirm_required"}:
                self.log("检测到未完成的执行范围准备，正在恢复核对进度。")
                self._prepare_started({"prepare": prepare})
            elif state in {"committing", "creating", "created", "starting"}:
                self.pending_group_payload = {
                    "prepare_id": str(prepare.get("prepare_id") or ""),
                    "commit_sent": True,
                }
                self.log("检测到已确认但尚未建立执行组的提交，正在安全恢复。")
                self._set_execution_busy(True)
                self.poll_timer.start()
        self.refresh_scope()
        self.ready.emit()
        QTimer.singleShot(0, self.refresh_records)

    def _startup_failed(self, message: str) -> None:
        self._set_busy(False, "工作台未准备好")
        self.component_label.setText("程序组件未连接")
        self.log("工作台准备失败：" + product_error(message))
        QMessageBox.warning(self, "美客多活动管家", product_error(message))

    def _fill_store_combo(self) -> None:
        blocker = QSignalBlocker(self.store_combo)
        self.store_combo.clear()
        self.store_map = {"all": [account.account_id for account in self.accounts]}
        self.store_combo.addItem("全部店铺", "all")
        grouped: dict[str, list[str]] = {}
        for account in self.accounts:
            grouped.setdefault(account.store_name, []).append(account.account_id)
        for store in sorted(grouped):
            key = "store:" + store
            self.store_map[key] = grouped[store]
            self.store_combo.addItem(store, key)
        del blocker

    def selected_account_ids(self) -> list[str]:
        return list(self.store_map.get(str(self.store_combo.currentData() or "all"), []))

    def selected_store_text(self) -> str:
        return self.store_combo.currentText() or "全部店铺"

    def selected_site_id(self) -> str:
        return str(self.site_combo.currentData() or "")

    def current_filters(self) -> dict[str, Any]:
        return build_filters(
            self.selected_site_id(),
            str(self.seller_combo.currentData() or ""),
            str(self.official_combo.currentData() or ""),
        )

    def refresh_scope(self) -> None:
        account_ids = self.selected_account_ids()
        if not account_ids:
            self.scope_ready = False
            self._set_busy(False, "当前范围没有可用店铺")
            return
        self.scope_ready = False
        self._set_busy(True, "正在读取店铺站点...")
        self.scope_refresh_token += 1
        token = self.scope_refresh_token
        selected_site = self.selected_site_id()
        self._run_worker(
            lambda: self._load_scope_bundle(account_ids, selected_site),
            lambda result: self._apply_scope_bundle(token, result),
            lambda error: self._scope_load_failed(token, error),
        )

    def _scope_load_failed(self, token: int, error: str) -> None:
        if token != self.scope_refresh_token:
            return
        self.scope_ready = False
        self._set_busy(False, "店铺站点未准备好")
        self.log("活动范围读取失败：" + product_error(error))

    def _load_scope_bundle(self, account_ids: list[str], selected_site: str) -> dict[str, Any]:
        sites: list[dict[str, Any]] = []
        promotions: list[dict[str, Any]] = []
        for account_id in account_ids:
            for row in self.api.get(f"/api/accounts/{account_id}/sites").get("sites", []):
                sites.append({**row, "account_id": account_id})
            path = ApiClient.query(f"/api/accounts/{account_id}/promotions", siteId=selected_site)
            for row in self.api.get(path).get("promotions", []):
                promotions.append({**row, "account_id": account_id})
        return {"sites": sites, "promotions": promotions, "selected_site": selected_site}

    def _apply_scope_bundle(self, token: int, result: object) -> None:
        if token != self.scope_refresh_token:
            return
        data = dict(result or {})
        selected_site = str(data.get("selected_site") or "")
        sites = list(data.get("sites") or [])
        account_names = {account.account_id: account.store_name for account in self.accounts}
        cached = {
            (str(row.get("account_id") or ""), str(row.get("site_id") or "").upper()): row
            for row in self.operating_rows_cache
        }
        for row in sites:
            account_id = str(row.get("account_id") or "")
            site_id = str(row.get("site_id") or "").upper()
            if account_id and site_id:
                cached[(account_id, site_id)] = {
                    **row,
                    "account_id": account_id,
                    "site_id": site_id,
                    "store_name": account_names.get(account_id, "当前店铺"),
                }
        self.operating_rows_cache = list(cached.values())
        self.promotions = list(data.get("promotions") or [])
        blocker = QSignalBlocker(self.site_combo)
        self.site_combo.clear()
        self.site_combo.addItem("全部站点", "")
        unique_sites = sorted({str(row.get("site_id") or "") for row in sites if row.get("site_id")})
        for site_id in unique_sites:
            self.site_combo.addItem(site_name(site_id), site_id)
        index = self.site_combo.findData(selected_site)
        self.site_combo.setCurrentIndex(max(0, index))
        del blocker
        self._fill_activity_combos()
        self._populate_activities()
        self.scope_ready = True
        self._set_busy(False, "工作台已就绪")
        self.log("店铺站点与活动范围已加载。")
        self._refresh_auto_decision()

    def _fill_activity_combos(self) -> None:
        for combo, prefix in ((self.seller_combo, "自建"), (self.official_combo, "官方")):
            blocker = QSignalBlocker(combo)
            combo.clear()
            combo.addItem(f"全部{prefix}活动", "")
            combo.addItem(f"不处理{prefix}活动", EXCLUDE_ACTIVITY)
            bucket = "seller" if combo is self.seller_combo else "official"
            choices: dict[str, str] = {}
            for promotion in self.promotions:
                if promotion_bucket(str(promotion.get("promotion_type") or "")) != bucket:
                    continue
                display = promotion_display_name(promotion)
                key = normalize_activity_name(display)
                if key and display:
                    choices.setdefault(key, display)
            for key, display in sorted(choices.items(), key=lambda item: item[1].casefold()):
                combo.addItem(display, key)
            del blocker

    def _mode_changed(self, mode: str) -> None:
        if mode == "自动判断":
            self._apply_global_discounts()
        self._update_discount_state()
        self._refresh_auto_decision()

    def _scope_changed(self, _index: int = -1) -> None:
        if self.sender() is self.store_combo:
            self.refresh_scope()
        else:
            self._refresh_auto_decision()

    def _site_changed(self, _index: int = -1) -> None:
        self.refresh_scope()

    def _apply_global_discounts(self) -> None:
        if self.mode_combo.currentText() == "自动判断":
            self.seller_discount.setValue(self.global_seller_discount)
            self.official_discount.setValue(self.global_official_discount)

    def _update_discount_state(self) -> None:
        enabled = discount_inputs_enabled(self.mode_combo.currentText(), self.auto_action)
        self.seller_discount.setEnabled(enabled)
        self.official_discount.setEnabled(enabled)

    def _refresh_auto_decision(self) -> None:
        self.auto_decision_token += 1
        decision_token = self.auto_decision_token
        mode_action = action_for_mode(self.mode_combo.currentText())
        if not self.today_completion_ready:
            self.current_today_completion = None
            self.auto_action = ""
            self._update_discount_state()
            self.today_label.setText(self._discount_summary() + " 正在核对今日执行记录，核对完成前不会重复提交。")
            self._sync_submit_availability()
            return
        self.current_today_completion = self._completion_for_current_scope()
        if self.current_today_completion:
            completed_text = execution_completion_text(self.current_today_completion)
            self.auto_action = ""
            self._update_discount_state()
            if mode_action:
                self.today_label.setText(
                    completed_text
                    + f" 当前选择{action_label(mode_action)}；提交前会再次提示这是今天的另一项真实操作。"
                )
            else:
                self.today_label.setText(
                    completed_text
                    + " 自动模式已停止普通重复提交；如确需补跑，请切换到手动模式并再次确认。"
                )
            self._sync_submit_availability()
            return
        if mode_action:
            self.auto_action = ""
            self._update_discount_state()
            self.today_label.setText(f"当前为{action_label(mode_action)}，使用界面中的手动折扣设置。" if mode_action != "cancel" else "当前为批量取消，取消不使用折扣。")
            self._sync_submit_availability()
            return
        account_ids = self.selected_account_ids()
        if not account_ids:
            self.today_label.setText(self._discount_summary() + " 当前店铺没有可用授权账号。")
            self._sync_submit_availability()
            return
        self.today_label.setText(self._discount_summary() + " 正在判断当前范围的执行动作...")
        filters = self.current_filters()
        self._run_worker(
            lambda: self._resolve_action(account_ids, filters),
            lambda action: self._auto_action_ready(action, decision_token),
            lambda error: self._auto_action_error(error, decision_token),
        )

    def _resolve_action(self, account_ids: list[str], filters: dict[str, Any]) -> str:
        result = self.api.post("/api/today/decision", {"accountIds": account_ids, "filters": filters})
        decision = result.get("decision") or {}
        return str(decision.get("action") or "")

    def _auto_action_ready(self, action: object, decision_token: int | None = None) -> None:
        if decision_token is not None and decision_token != self.auto_decision_token:
            return
        self.auto_action = str(action or "")
        self._update_discount_state()
        suffix = "，取消不使用折扣。" if self.auto_action == "cancel" else "。"
        self.today_label.setText(f"{self._discount_summary()} 当前范围应执行{action_label(self.auto_action)}{suffix}")
        self._sync_submit_availability()

    def _auto_action_error(self, message: str, decision_token: int | None = None) -> None:
        if decision_token is not None and decision_token != self.auto_decision_token:
            return
        self.auto_action = ""
        self._update_discount_state()
        self.today_label.setText(self._discount_summary() + " " + product_error(message))
        self._sync_submit_availability()

    def _discount_summary(self) -> str:
        return f"今日折扣：自建{self.global_seller_discount}%，官方{self.global_official_discount}%。"

    def _records_view_changed(self) -> None:
        view = str(self.records_view_combo.currentData() or "recent")
        if view == self.records_view:
            return
        self.records_view = view
        self.records_request_token += 1
        cached = self.records_cache.get(view)
        if cached is None:
            self._apply_current_records([])
            self.refresh_records()
            return
        self._apply_current_records(cached)

    def refresh_records(self) -> None:
        self._request_record_views([self.records_view])

    def _request_record_views(self, views: list[str]) -> None:
        requested = list(dict.fromkeys(view for view in views if view in RECORD_VIEW_LIMITS))
        if not requested:
            return
        if "recent" in requested or ("all" in requested and "recent" not in self.records_cache):
            self.today_completion_ready = False
            self.current_today_completion = None
            self._sync_submit_availability()
        self.records_request_token += 1
        token = self.records_request_token

        def load() -> dict[str, list[dict[str, Any]]]:
            if len(requested) == 1:
                view = requested[0]
                limit = RECORD_VIEW_LIMITS[view]
                return {view: list(self.api.get(f"/api/tasks?limit={limit}").get("tasks", []))}
            with ThreadPoolExecutor(max_workers=len(requested)) as executor:
                futures = {
                    view: executor.submit(self.api.get, f"/api/tasks?limit={RECORD_VIEW_LIMITS[view]}")
                    for view in requested
                }
                return {view: list(future.result().get("tasks", [])) for view, future in futures.items()}

        self._run_worker(
            load,
            lambda payload: self._records_loaded(dict(payload or {}), token),
            lambda error: self._records_load_failed(error, requested, token),
        )

    def _records_loaded(self, payload: dict[str, list[dict[str, Any]]], token: int) -> None:
        if token != self.records_request_token:
            return
        for view, rows in payload.items():
            if view in RECORD_VIEW_LIMITS:
                self.records_cache[view] = list(rows or [])
        current = self.records_cache.get(self.records_view)
        if current is not None:
            self._apply_current_records(current)
        completion_rows = payload.get("recent")
        if completion_rows is None and "recent" not in self.records_cache:
            completion_rows = payload.get("all")
        if completion_rows is not None:
            self._request_today_execution_groups(completion_rows)

    def _records_load_failed(self, error: object, requested: list[str], token: int) -> None:
        if token != self.records_request_token:
            return
        self.log("执行记录读取失败：" + product_error(error))
        if "recent" in requested or ("all" in requested and "recent" not in self.records_cache):
            self.today_completion_ready = False
            self.current_today_completion = None
            self.today_label.setText(self._discount_summary() + " 今日执行记录暂未核对，当前不可提交。")
            self._sync_submit_availability()

    def _request_today_execution_groups(self, records: list[dict[str, Any]]) -> None:
        today = datetime.now().date()
        group_ids: list[str] = []
        seen: set[str] = set()
        for task in records:
            if str(task.get("mode") or "real") != "real":
                continue
            group_id = str(task.get("execution_group_id") or "")
            timestamp = task.get("updated_at") or task.get("created_at")
            if not group_id or group_id in seen or business_date_from_timestamp(timestamp) != today:
                continue
            seen.add(group_id)
            group_ids.append(group_id)
        self.today_completion_request_token += 1
        token = self.today_completion_request_token
        if not group_ids:
            self._apply_today_execution_groups([], token)
            return

        def load() -> list[dict[str, Any]]:
            groups: list[dict[str, Any]] = []
            for group_id in group_ids:
                payload = self.api.get(f"/api/execution/groups/{group_id}?compact=1", timeout=10)
                group = dict(payload.get("group") or {})
                if group:
                    groups.append(group)
            return groups

        self._run_worker(
            load,
            lambda groups: self._apply_today_execution_groups(list(groups or []), token),
            lambda error: self._today_execution_groups_failed(error, token),
        )

    def _apply_today_execution_groups(self, groups: list[dict[str, Any]], token: int | None = None) -> None:
        if token is not None and token != self.today_completion_request_token:
            return
        self.today_execution_groups = [dict(group) for group in groups]
        self.today_completion_ready = True
        self.current_today_completion = self._completion_for_current_scope()
        self._refresh_auto_decision()
        self._sync_submit_availability()

    def _today_execution_groups_failed(self, error: object, token: int) -> None:
        if token != self.today_completion_request_token:
            return
        self.today_completion_ready = False
        self.current_today_completion = None
        self.today_label.setText(self._discount_summary() + " 今日执行记录暂未核对，当前不可提交。")
        self.log("今日执行记录核对失败：" + product_error(error))
        self._sync_submit_availability()

    def _completion_for_current_scope(self) -> dict[str, Any] | None:
        return completed_execution_for_scope(
            self.today_execution_groups,
            self.selected_account_ids(),
            self.current_filters(),
        )

    def _apply_current_records(self, records: list[dict[str, Any]]) -> None:
        self.records = list(records)
        self._populate_records_table()

    def _populate_records_table(self) -> None:
        self.records_table.setRowCount(0)
        for task in self.records:
            row = self.records_table.rowCount()
            self.records_table.insertRow(row)
            _total, _success, failed, _skipped = task_display_counts(task)
            unique_items = optional_contract_count(task, "unique_item_count")
            relation_count = optional_contract_count(task, "relation_count")
            activity_failures = optional_contract_count(task, "activity_failure_count")
            values = [
                short_date(str(task.get("created_at") or task.get("updated_at") or "")),
                action_label(str(task.get("action") or "")),
                activity_summary_text(task),
                "提交" if str(task.get("mode") or "real") == "real" else "预览",
                f"{count_or_marker(unique_items, '旧记录未区分')} / {count_or_marker(relation_count)}",
                record_result_text(task),
                f"商品 {failed} / 活动 {count_or_marker(activity_failures)}",
                str(task.get("short_failure_reason") or task.get("failure_reason") or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, task)
                self.records_table.setItem(row, column, item)
        self.records_table.resizeRowsToContents()

    def _populate_activities(self) -> None:
        self.activity_table.setRowCount(0)
        account_map = {account.account_id: account for account in self.accounts}
        for promotion in self.promotions:
            row = self.activity_table.rowCount()
            self.activity_table.insertRow(row)
            account = account_map.get(str(promotion.get("account_id") or ""))
            values = [
                account.store_name if account else "当前店铺",
                site_name(str(promotion.get("site_id") or "")),
                promotion_type_text(str(promotion.get("promotion_type") or "")),
                promotion_display_name(promotion),
                status_text(str(promotion.get("status") or "")),
                str(promotion.get("total") or promotion.get("items_total") or 0),
            ]
            for column, value in enumerate(values):
                self.activity_table.setItem(row, column, QTableWidgetItem(value))

    def _show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)

    def _show_task_details(self) -> None:
        row = self.records_table.currentRow()
        if row < 0 or not self.records_table.item(row, 0):
            return
        task = dict(self.records_table.item(row, 0).data(Qt.ItemDataRole.UserRole) or {})
        ids = task.get("task_ids") or [task.get("id")]
        ids = [int(value) for value in ids if value]
        if not ids:
            DetailsDialog("批次详情", business_task_text(task), self).exec()
            return
        path = "/api/tasks/details?taskIds=" + ",".join(str(value) for value in ids)
        self._run_worker(
            lambda: self.api.get(path).get("details", []),
            lambda details: DetailsDialog("批次详情", business_details_text(task, list(details or [])), self).exec(),
            lambda error: QMessageBox.warning(self, "批次详情", product_error(error)),
        )

    def _show_selected_summary(self) -> None:
        row = self.records_table.currentRow()
        if row < 0 or not self.records_table.item(row, 0):
            return
        task = dict(self.records_table.item(row, 0).data(Qt.ItemDataRole.UserRole) or {})
        reason = str(task.get("failure_reason") or task.get("short_failure_reason") or "")
        if reason:
            self.log("所选批次失败原因：" + reason)

    def _reload_live_promotions(self) -> None:
        account_ids = self.selected_account_ids()
        self._set_busy(True, "正在读取活动...")

        def reload() -> list[tuple[str, int]]:
            result: list[tuple[str, int]] = []
            for account_id in account_ids:
                payload = self.api.post(f"/api/accounts/{account_id}/promotions/fetch", {}, timeout=120)
                result.append((account_id, int(payload.get("total") or 0)))
            return result

        def done(rows: object) -> None:
            for account_id, total in list(rows or []):
                self.log(f"{self._store_for_account(account_id)}：活动读取完成，共 {total} 个。")
            self._set_busy(False, "活动已刷新")
            self.refresh_scope()

        self._run_worker(reload, done, lambda error: self._operation_error("活动读取", error))

    def _on_execute_clicked(self) -> None:
        if self.preparing_submission:
            if str(self.preparing_submission.get("state") or "") == "stopping":
                self.log("正在停止准备，请稍候。")
                return
            if self.preparing_submission.get("prepare_id"):
                self._request_cancel_prepare()
            else:
                self.log("准备记录正在建立，请稍候。")
            return
        if self.running_group or self.pending_group_payload:
            self._request_cancel_jobs()
            return
        account_ids = self.selected_account_ids()
        if not account_ids:
            QMessageBox.information(self, "提交执行", "当前店铺没有可用授权账号。")
            return
        requested_action = action_for_mode(self.mode_combo.currentText()) or "auto"
        if not self.today_completion_ready:
            QMessageBox.information(self, "提交执行", "正在核对今天是否已有真实操作，核对完成后才能继续。")
            return
        self.current_today_completion = self._completion_for_current_scope()
        if requested_action == "auto" and self.current_today_completion:
            self.log("当前范围今天已完成真实操作，自动模式不会重复准备。")
            return
        account_ids = self.selected_account_ids()
        settings = self.settings
        filters = self.current_filters()
        site_text = self.site_combo.currentText() or "全部站点"
        seller_discount = self.seller_discount.value()
        official_discount = self.official_discount.value()
        store_names = {account_id: self._store_for_account(account_id) for account_id in account_ids}
        read = int(settings.get("readConcurrency") or 2)
        activity = int(settings.get("previewConcurrency") or 2)
        write = int(settings.get("writeConcurrency") or 2)
        submission_id = str(uuid.uuid4())
        payload = execution_group_payload(
            account_ids=account_ids,
            action=requested_action,
            filters=filters,
            store_names=store_names,
            site_name_text=site_text,
            seller_discount=seller_discount,
            official_discount=official_discount,
            read_concurrency=read,
            activity_concurrency=activity,
            write_concurrency=write,
            client_submission_id=submission_id,
        )
        payload["requested_action"] = requested_action
        self.preparing_submission = {"client_submission_id": submission_id, "state": "starting"}
        self.pending_prepare_payload = dict(payload)
        self._set_prepare_busy(True)
        self.log("正在准备最终执行范围，期间不会提交商品。")
        self._run_worker(
            lambda: self.api.post(
                "/api/execution/submissions/prepare", payload, timeout=20,
                timeout_message="准备请求响应延迟，正在恢复已保存的准备记录。",
            ),
            self._prepare_started,
            self._prepare_start_failed,
        )

    def _prepare_started(self, response: object) -> None:
        prepare = dict(dict(response or {}).get("prepare") or {})
        prepare_id = str(prepare.get("prepare_id") or dict(response or {}).get("prepare_id") or "")
        if not prepare_id:
            self._prepare_start_failed(ApiError("程序未返回可恢复的准备记录。", kind="unknown", retryable=True))
            return
        prepare["prepare_id"] = prepare_id
        self.preparing_submission = prepare
        self.prepare_poll_failure_count = 0
        self._set_prepare_busy(True)
        if str(prepare.get("state") or "") in {"prepared", "reconfirm_required"}:
            self._prepare_polled({"prepare": prepare})
            return
        self._log_prepare_progress(prepare)
        if not self.prepare_poll_timer.isActive():
            self.prepare_poll_timer.start()
        self._poll_prepare()

    def _prepare_start_failed(self, error: object) -> None:
        if isinstance(error, ApiError):
            code = str(error.payload.get("code") or "")
            details = dict(error.payload.get("details") or {})
            if code == "TODAY_COMPLETED":
                self._prepare_blocked_by_today_completion(details)
                return
            if code == "CONFIRM_SAME_DAY_ACTION":
                self._confirm_server_same_day_action(details)
                return
        if isinstance(error, ApiError) and error.retryable:
            self.log("准备请求响应暂未确认，正在查找已保存的准备记录；不会重复提交。")
            self._set_prepare_busy(True)
            if not self.prepare_poll_timer.isActive():
                self.prepare_poll_timer.start()
            return
        self.preparing_submission = {}
        self.pending_prepare_payload = None
        self._set_prepare_busy(False)
        self._operation_error("准备执行", error)

    def _prepare_blocked_by_today_completion(self, details: dict[str, Any]) -> None:
        completed = dict(details.get("completed") or {})
        message = execution_completion_text(completed) if completed else "今天当前范围已有真实任务，自动模式不会重复准备。"
        self.preparing_submission = {}
        self.pending_prepare_payload = None
        self.prepare_poll_timer.stop()
        self._set_prepare_busy(False)
        self.today_label.setText(self._discount_summary() + " " + message)
        self.log(message + " 自动模式未创建新的执行准备。")
        QMessageBox.information(self, "今日已完成", message + "\n\n自动模式不会重复执行当前范围。")

    def _confirm_server_same_day_action(self, details: dict[str, Any]) -> None:
        token = str(details.get("confirmation_token") or "")
        payload = dict(self.pending_prepare_payload or {})
        if not token or not payload:
            self.preparing_submission = {}
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self._operation_error("准备执行", "未取得有效的二次确认，请重新操作。")
            return
        completed = dict(details.get("completed") or {})
        requested_action = str(payload.get("requested_action") or payload.get("action") or "")
        completed_text = execution_completion_text(completed) if completed else "今天当前范围已有真实操作。"
        if bool(details.get("same_action")):
            message = (
                f"{completed_text}\n"
                f"现在仍将准备{action_label(requested_action)}，可能重复处理同一范围。\n\n"
                "仅在明确需要补跑时继续。"
            )
        else:
            message = (
                f"{completed_text}\n"
                f"现在将准备{action_label(requested_action)}，这是今天的另一项真实操作。\n\n"
                "请确认这确实是当前意图。"
            )
        dialog = ConfirmDialog("今日已有真实操作", message, "确认继续", "取消", self)
        if dialog.exec() != QDialogAccepted:
            self.preparing_submission = {}
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self.log("已取消今天的额外真实操作，未创建执行准备。")
            self._run_worker(
                lambda: self.api.post(
                    "/api/execution/submissions/same-day-confirmations/cancel",
                    {"confirmation_token": token}, timeout=10,
                ),
                lambda _response: None,
                lambda _error: self.log("本次取消已生效；本地确认记录暂未同步。"),
            )
            return
        payload["same_day_confirmation_token"] = token
        self.pending_prepare_payload = payload
        self.preparing_submission = {
            "client_submission_id": str(payload.get("client_submission_id") or ""),
            "state": "starting",
        }
        self._set_prepare_busy(True)
        self.log("已确认今天继续此项操作，正在建立执行准备；尚未提交商品。")
        self._run_worker(
            lambda: self.api.post(
                "/api/execution/submissions/prepare", payload, timeout=20,
                timeout_message="准备请求响应延迟，正在恢复已保存的准备记录。",
            ),
            self._prepare_started,
            self._prepare_start_failed,
        )

    def _poll_prepare(self) -> None:
        if self.prepare_poll_busy or not self.preparing_submission:
            return
        self.prepare_poll_busy = True
        prepare_id = str(self.preparing_submission.get("prepare_id") or "")

        def poll() -> dict[str, Any]:
            if prepare_id:
                return self.api.get(
                    f"/api/execution/submissions/{prepare_id}", timeout=15,
                    timeout_message="准备进度查询延迟，后台仍在核对范围。",
                )
            active = self.api.get(
                "/api/execution/submissions/active", timeout=10,
                timeout_message="准备进度查询延迟，后台仍在核对范围。",
            )
            if active.get("prepare"):
                return {"prepare": active.get("prepare")}
            if self.pending_prepare_payload:
                return self.api.post(
                    "/api/execution/submissions/prepare", self.pending_prepare_payload, timeout=20,
                    timeout_message="准备请求响应延迟，正在恢复已保存的准备记录。",
                )
            return {"prepare": {}}

        self._run_worker(poll, self._prepare_polled, self._prepare_poll_failed)

    def _prepare_polled(self, response: object) -> None:
        self.prepare_poll_busy = False
        prepare = dict(dict(response or {}).get("prepare") or {})
        if not prepare.get("prepare_id"):
            self._prepare_poll_failed(ApiError("尚未读取到已保存的准备记录。", kind="unknown", retryable=True))
            return
        self.preparing_submission = prepare
        self.prepare_poll_failure_count = 0
        state = str(prepare.get("state") or "")
        if state == "preparing":
            self._log_prepare_progress(prepare)
            self._set_prepare_busy(True)
            if not self.prepare_poll_timer.isActive():
                self.prepare_poll_timer.start()
            return
        self.prepare_poll_timer.stop()
        self.preparing_submission = {}
        self.prepare_progress_key = ""
        if state == "prepared":
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self._submission_prepared({"prepare": prepare})
            return
        if state == "reconfirm_required":
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self.log("检测到最终范围变化，请按更新后的摘要再次确认。")
            self._confirm_submission(prepare)
            return
        if state in {"failed", "expired"}:
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self._operation_error("准备执行", str(prepare.get("error") or "执行范围准备未完成。"))
            return
        if state in {"cancelled", "paused"}:
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self.log("准备已停止，未创建执行组、未提交商品。" if state == "cancelled" else "本次准备已暂停，可在有效期内按相同范围恢复。")
            return
        if prepare.get("group"):
            self._group_started({"group": prepare.get("group")})
            return
        self._prepare_poll_failed(ApiError("准备状态正在收口，稍后继续查询。", kind="unknown", retryable=True))

    def _prepare_poll_failed(self, error: object) -> None:
        self.prepare_poll_busy = False
        self.prepare_poll_failure_count += 1
        count = self.prepare_poll_failure_count
        kind = getattr(error, "kind", "unknown")
        if kind == "timeout":
            if count == 1 or count % 3 == 0:
                self.log("准备进度查询延迟，后台仍在核对范围，正在自动重试。")
        elif kind == "connection":
            health_ok = self.service.is_healthy(timeout=1.0) if hasattr(self.service, "is_healthy") else False
            if not health_ok and count >= 3:
                self.log("程序组件连续无法连接，准备状态尚未确认；程序不会重复提交。")
            elif count == 1:
                self.log("准备进度连接暂时中断，正在重新连接。")
        elif count == 1:
            self.log("准备进度暂时无法读取，正在自动重试。")
        self._set_prepare_busy(True)
        if not self.prepare_poll_timer.isActive():
            self.prepare_poll_timer.start()

    def _log_prepare_progress(self, prepare: dict[str, Any]) -> None:
        progress = dict(prepare.get("progress") or {})
        message = str(progress.get("message") or "正在核对执行范围")
        current = " / ".join(str(progress.get(key) or "") for key in ("current_store", "current_site", "current_activity") if progress.get(key))
        percent = max(0, min(100, int(progress.get("percent") or 0)))
        key = f"{message}|{current}|{percent}"
        if key == self.prepare_progress_key:
            return
        self.prepare_progress_key = key
        suffix = f"：{current}" if current else ""
        self.log(f"{message}{suffix}（{percent}%）。")

    def _submission_prepared(self, response: object) -> None:
        self.prepare_poll_timer.stop()
        self.preparing_submission = {}
        self.pending_prepare_payload = None
        prepare = dict(dict(response or {}).get("prepare") or {})
        if not prepare.get("prepare_id"):
            self._operation_error("准备执行", "程序未返回可确认的执行范围。", execution=True)
            return
        seller_detection = dict(prepare.get("seller_detection") or {})
        confirmed_absent = list(seller_detection.get("confirmed_absent") or [])
        needs_review = list(seller_detection.get("needs_manual_review") or seller_detection.get("visibility_unknown") or [])
        if needs_review:
            self.log(f"有 {len(needs_review)} 个店铺站点未能确认自建活动可见性，本次禁止自动创建。")
        if prepare.get("resolved_action") == "enroll" and confirmed_absent:
            dialog = SellerCampaignCreateDialog(confirmed_absent, self)
            if dialog.exec() != QDialogAccepted:
                self.log("提交执行已取消，未创建活动、未启动执行任务。")
                self._set_prepare_busy(False)
                self._discard_prepared_submission(str(prepare.get("prepare_id") or ""))
                return
            values = dialog.values()
            self._run_worker(
                lambda: self.api.post(f"/api/execution/submissions/{prepare['prepare_id']}/seller-input", values, timeout=30),
                self._submission_input_saved,
                lambda error: self._operation_error("保存创建范围", error, execution=True),
            )
            return
        self._confirm_submission(prepare)

    def _submission_input_saved(self, response: object) -> None:
        prepare = dict(dict(response or {}).get("prepare") or {})
        errors = list(dict(prepare.get("seller_input") or {}).get("validation_errors") or [])
        if errors:
            self._operation_error("创建自建活动", "创建参数未通过检查：" + "；".join(str(value) for value in errors), execution=True)
            return
        self._confirm_submission(prepare)

    def _confirm_submission(self, prepare: dict[str, Any]) -> None:
        self._set_prepare_busy(False)
        summary = str(prepare.get("confirmation_summary") or "请确认本次最终执行范围。")
        selected = list(dict(prepare.get("seller_input") or {}).get("selected_targets") or [])
        if selected:
            summary += f"\n将创建自建活动的店铺站点：{len(selected)} 个。\n" + "\n".join(target_label(row) for row in selected)
        dialog = ConfirmDialog("最终执行确认", summary, "确认执行", "取消", self)
        if dialog.exec() != QDialogAccepted:
            self.log("提交执行已取消，未创建活动、未启动执行任务。")
            self._discard_prepared_submission(str(prepare.get("prepare_id") or ""))
            return
        commit_body = {"confirmText": "REAL_SUBMIT"}
        if selected:
            commit_body["createConfirmText"] = "CREATE_SELLER_CAMPAIGN"
        self.pending_group_payload = {"prepare_id": prepare["prepare_id"], "commit_body": commit_body}
        self.pending_group_payload["commit_sent"] = True
        self._set_execution_busy(True)
        self.log("最终范围已确认，正在提交本次执行。")
        self._run_worker(
            lambda: self.api.post(f"/api/execution/submissions/{prepare['prepare_id']}/commit", commit_body, timeout=30),
            self._commit_accepted,
            self._group_start_failed,
        )

    def _commit_accepted(self, response: object) -> None:
        payload = dict(response or {})
        group = dict(payload.get("group") or {})
        if group.get("id"):
            self._group_started(payload)
            return
        prepare = dict(payload.get("prepare") or {})
        prepare_id = str(prepare.get("prepare_id") or prepare.get("id") or dict(self.pending_group_payload or {}).get("prepare_id") or "")
        if not prepare_id:
            self._group_start_failed(ApiError("后台未返回本次提交状态。", kind="unknown", retryable=True))
            return
        if self.pending_group_payload is None:
            self.pending_group_payload = {"prepare_id": prepare_id, "commit_sent": True}
        else:
            self.pending_group_payload["prepare_id"] = prepare_id
            self.pending_group_payload["commit_sent"] = True
        self.commit_recovery_poll_count = 0
        self.poll_failure_count = 0
        self.poll_timer.setInterval(1200)
        self._set_execution_busy(True)
        if not self.poll_timer.isActive():
            self.poll_timer.start()
        self._commit_submission_polled(prepare or {"prepare_id": prepare_id, "state": "committing"})

    def _group_started(self, response: object) -> None:
        group = dict(dict(response or {}).get("group") or {})
        if not group.get("id"):
            self._operation_error("提交执行", "后台没有返回执行组。", execution=True)
            return
        self.running_group = group
        self.prepare_poll_timer.stop()
        self.preparing_submission = {}
        self.pending_prepare_payload = None
        self.pending_group_payload = None
        self.job_log_counts = {str(child.get("job_id") or ""): 0 for child in group.get("children") or []}
        self.poll_failure_count = 0
        self.commit_recovery_poll_count = 0
        self.poll_timer.setInterval(900)
        self._set_execution_busy(True)
        self.poll_timer.start()
        self._poll_group()

    def _group_start_failed(self, error: object) -> None:
        payload = getattr(error, "payload", {}) if isinstance(error, ApiError) else {}
        group = dict(payload.get("group") or {}) if isinstance(payload, dict) else {}
        if group.get("id"):
            self._group_started({"group": group})
            return
        code = str(payload.get("code") or "") if isinstance(payload, dict) else ""
        if code == "COMMIT_IN_PROGRESS":
            self.log("后台正在处理同一次提交，正在恢复进度；不会重复提交。")
            self._set_execution_busy(True)
            self.poll_timer.setInterval(1500)
            if not self.poll_timer.isActive():
                self.poll_timer.start()
            return
        if isinstance(error, ApiError) and not error.retryable and error.kind == "http":
            self.pending_group_payload = None
            self.poll_timer.stop()
            self._operation_error("提交执行", error, execution=True)
            return
        self.log("提交响应暂未确认，正在按同一提交编号恢复；程序不会重复提交。")
        self._set_execution_busy(True)
        self.poll_timer.setInterval(1500)
        if not self.poll_timer.isActive():
            self.poll_timer.start()

    def _poll_group(self) -> None:
        if self.poll_busy or (not self.running_group and not self.pending_group_payload):
            return
        self.poll_busy = True
        group_id = str(self.running_group.get("id") or "")
        pending = dict(self.pending_group_payload or {})

        def poll() -> dict[str, Any]:
            if group_id:
                return self.api.get(f"/api/execution/groups/{group_id}", timeout=20)
            if pending:
                prepare_id = str(pending.get("prepare_id") or "")
                return self.api.get(
                    f"/api/execution/submissions/{prepare_id}", timeout=20,
                    timeout_message="提交进度查询延迟，后台仍在核对最终范围。",
                )
            active = self.api.get("/api/execution/groups/active", timeout=10)
            if active.get("group"):
                return {"group": active.get("group")}
            return {"group": {}}

        self._run_worker(poll, self._group_polled, self._poll_group_failed)

    def _group_polled(self, response: object) -> None:
        self.poll_busy = False
        payload = dict(response or {})
        if payload.get("prepare"):
            self._commit_submission_polled(dict(payload.get("prepare") or {}))
            return
        terminal = {"completed", "failed", "cancelled", "interrupted"}
        group = dict(payload.get("group") or response or {})
        if not group.get("id"):
            self._poll_group_failed(ApiError("未读取到执行组状态。", kind="unknown", retryable=True))
            return
        self.running_group = group
        self.pending_group_payload = None
        self.poll_failure_count = 0
        for child in list(group.get("children") or []):
            job_id = str(child.get("job_id") or child.get("id") or "")
            logs = list(child.get("userLogs") or child.get("user_logs") or [])
            start = self.job_log_counts.get(job_id, 0)
            for line in logs[start:]:
                message = execution_log_message(line)
                if message:
                    self.log(message)
            self.job_log_counts[job_id] = len(logs)
        if str(group.get("status") or "").lower() in terminal:
            self.poll_timer.stop()
            result = dict(group.get("result") or {})
            stores = list(result.get("stores") or [])
            action = str(result.get("action") or group.get("action") or "")
            for store_result in stores:
                status = str(store_result.get("status") or "")
                account_id = str(store_result.get("account_id") or "")
                store = self._store_for_account(account_id) if account_id else str(store_result.get("store_name") or "当前店铺")
                site = str(store_result.get("site_name") or self.site_combo.currentText() or "全部站点")
                ending = {
                    "completed": "完成",
                    "failed": "未完整完成",
                    "cancelled": "已停止",
                    "interrupted": "意外中断",
                }.get(status.lower(), status_text(status))
                self.log(f"{store} / {site}：{action_label(action)}{ending}，{execution_result_text(store_result, action)}。")
            self.log(
                f"本次{action_label(action)}总汇总：店铺 {int(result.get('store_count') or len(stores))} 个，"
                f"{execution_result_text(result, action)}。"
            )
            self.running_group.clear()
            self.pending_group_payload = None
            self.job_log_counts.clear()
            self.poll_failure_count = 0
            self._set_execution_busy(False)
            self._refresh_records_after_group()

    def _commit_submission_polled(self, prepare: dict[str, Any]) -> None:
        state = str(prepare.get("state") or "").lower()
        group = dict(prepare.get("group") or {})
        group_id = str(prepare.get("group_id") or group.get("id") or "")
        if group.get("id"):
            self._group_started({"group": group})
            return
        if state == "executing" and group_id:
            self._group_started({"group": {"id": group_id, "status": "queued", "children": []}})
            return
        if state in {"committing", "creating", "created", "starting"}:
            self.commit_recovery_poll_count += 1
            self.poll_failure_count = 0
            progress = dict(prepare.get("progress") or {})
            message = str(progress.get("message") or "后台正在处理已确认的提交")
            if self.commit_recovery_poll_count == 1 or self.commit_recovery_poll_count % 10 == 0:
                self.log(message + "，正在继续查询进度。")
            intervals = (1200, 1500, 2000, 3000, 5000)
            self.poll_timer.setInterval(intervals[min(len(intervals) - 1, self.commit_recovery_poll_count // 5)])
            self._set_execution_busy(True)
            if not self.poll_timer.isActive():
                self.poll_timer.start()
            return
        if state == "reconfirm_required":
            self.poll_timer.stop()
            self.pending_group_payload = None
            self.commit_recovery_poll_count = 0
            self.poll_timer.setInterval(900)
            self._set_execution_busy(False)
            changes = [str(value) for value in list(prepare.get("reconfirm_changes") or []) if str(value)]
            if changes:
                self.log("最终核对发现范围变化：" + "；".join(changes) + "。请按更新后的摘要再次确认。")
            else:
                self.log("最终核对发现范围变化，请按更新后的摘要再次确认。")
            self._confirm_submission(prepare)
            return
        if state in {"failed", "expired", "cancelled", "paused", "terminal"}:
            self.poll_timer.stop()
            self.pending_group_payload = None
            self.commit_recovery_poll_count = 0
            self.poll_timer.setInterval(900)
            self._set_execution_busy(False)
            message = str(prepare.get("error") or "")
            if state in {"cancelled", "paused"}:
                self.log("本次提交已停止，未继续启动商品执行。")
                return
            self._operation_error("提交执行", message or "本次提交未建立执行组，已停止。", execution=True)
            return
        self._poll_group_failed(ApiError("提交状态正在收口，稍后继续查询。", kind="unknown", retryable=True))

    def _refresh_records_after_group(self) -> None:
        if self.records_view == "all":
            self._request_record_views(["recent", "all"])
            return
        self.records_cache.pop("all", None)
        self._request_record_views(["recent"])

    def _poll_group_failed(self, error: object) -> None:
        self.poll_busy = False
        self.poll_failure_count += 1
        count = self.poll_failure_count
        kind = getattr(error, "kind", "unknown")
        if kind == "timeout":
            if count == 1 or count % 3 == 0:
                self.log("进度查询延迟，任务仍在执行，正在自动重试。")
        elif kind == "connection":
            health_ok = self.service.is_healthy(timeout=1.0)
            if not health_ok and count >= 3:
                self.log("程序组件连续无法连接，任务状态尚未确认；程序不会重复提交。")
            elif count == 1:
                self.log("执行进度连接暂时中断，正在重新连接，任务状态保持不变。")
        elif getattr(error, "status", 0) == 404:
            self.log("正在从已保存记录恢复执行进度，任务状态尚未确认。")
        elif count == 1:
            self.log("执行进度暂时无法读取，正在自动重试，任务状态保持不变。")
        self._set_execution_busy(True)
        if not self.poll_timer.isActive():
            self.poll_timer.start()

    def _request_cancel_jobs(self) -> None:
        group_id = str(self.running_group.get("id") or "")
        prepare_id = str(dict(self.pending_group_payload or {}).get("prepare_id") or "")
        if not group_id and not prepare_id:
            return
        if QMessageBox.question(self, "停止任务", "将停止尚未开始的商品并保留已完成结果，是否继续？") != QMessageBox.StandardButton.Yes:
            return
        if prepare_id and not group_id:
            self.execute_button.setEnabled(False)

            def stopped(response: object) -> None:
                prepare = dict(dict(response or {}).get("prepare") or {})
                if prepare.get("group"):
                    self._group_started({"group": prepare.get("group")})
                    return
                self.poll_timer.stop()
                self.pending_group_payload = None
                self.commit_recovery_poll_count = 0
                self._set_execution_busy(False)
                self.log("已停止提交核对，未继续创建活动或启动商品执行。")

            self._run_worker(
                lambda: self.api.post(f"/api/execution/submissions/{prepare_id}/cancel", {}, timeout=15),
                stopped,
                lambda error: self.log("停止提交请求暂未确认，正在继续查询状态：" + product_error(error)),
            )
            return
        self._run_worker(
            lambda: self.api.post(f"/api/execution/groups/{group_id}/cancel", {}),
            lambda _result: self.log("已请求停止执行任务，正在等待已开始的商品收口。"),
            lambda error: self.log("停止任务请求失败：" + product_error(error)),
        )

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self.settings,
            self.accounts,
            list(self.operating_rows_cache),
            self.benchmark_text_cache,
            self,
        )
        dialog.authorize_requested.connect(lambda: self._start_oauth(dialog))
        dialog.complete_authorization_requested.connect(lambda callback: self._complete_oauth(dialog, callback))
        dialog.refresh_requested.connect(lambda: self._refresh_accounts_from_settings(dialog))
        self._run_worker(
            self._load_settings_context,
            lambda context: self._apply_settings_context(dialog, context),
            lambda error: self.log("设置后台刷新未完成：" + product_error(error)),
        )
        if dialog.exec() != QDialogAccepted:
            return
        values = dialog.values()
        values["defaultFilters"] = self.current_filters()
        self._run_worker(
            lambda: self.api.post("/api/settings", values).get("settings", values),
            self._settings_saved,
            lambda error: self._operation_error("保存设置", error),
        )

    def _load_settings_context(self) -> dict[str, Any]:
        ids = [account.account_id for account in self.accounts]
        refresh_path = ApiClient.query("/api/accounts/profiles/refresh", accountIds=",".join(ids))
        refreshed = self.api.get(refresh_path, timeout=45)
        accounts = [account_from_json(row) for row in refreshed.get("accounts") or []]

        def load_sites(account: Account) -> list[dict[str, Any]]:
            result = self.api.get(f"/api/accounts/{account.account_id}/sites?includeAll=1&probeBusiness=1", timeout=120)
            return [
                {**site, "account_id": account.account_id, "store_name": account.store_name}
                for site in result.get("sites", [])
            ]

        operating: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(accounts)))) as executor:
            for rows in executor.map(load_sites, accounts):
                operating.extend(rows)
        benchmark = self.api.get("/api/concurrency-benchmark/results").get("results", {})
        return {"accounts": accounts, "operating": operating, "benchmark": benchmark}

    def _apply_settings_context(self, dialog: SettingsDialog, context: object) -> None:
        data = dict(context or {})
        accounts = list(data.get("accounts") or self.accounts)
        operating = list(data.get("operating") or [])
        benchmark = benchmark_text(dict(data.get("benchmark") or {}))
        self.accounts = accounts
        self.operating_rows_cache = operating
        self.benchmark_text_cache = benchmark
        if dialog.isVisible():
            dialog.apply_background_context(accounts, operating, benchmark)

    def _settings_saved(self, settings: object) -> None:
        self.settings = dict(settings or self.settings)
        self.log("设置已保存。")
        self._run_worker(self._load_initial_bundle, self._apply_initial_bundle, lambda error: self._operation_error("刷新设置", error))

    def _start_oauth(self, dialog: SettingsDialog) -> None:
        self._run_worker(
            lambda: self.api.post("/api/oauth/start/from-config", {}),
            lambda result: self._oauth_started(dialog, dict(result or {})),
            lambda error: QMessageBox.warning(dialog, "账号授权", product_error(error)),
        )

    def _oauth_started(self, dialog: SettingsDialog, result: dict[str, Any]) -> None:
        url = str(result.get("authorization_url") or result.get("url") or "")
        if url:
            QDesktopServices.openUrl(QUrl(url))
            QMessageBox.information(dialog, "账号授权", "已打开授权页面。完成后请粘贴浏览器回调链接。")
        else:
            QMessageBox.warning(dialog, "账号授权", str(result.get("error") or "未读取到授权链接。"))

    def _complete_oauth(self, dialog: SettingsDialog, callback: str) -> None:
        if not callback:
            QMessageBox.information(dialog, "账号授权", "请粘贴浏览器回调链接。")
            return
        self._run_worker(
            lambda: self.api.post("/api/oauth/complete-callback", {"callback": callback, "callbackText": callback}),
            lambda _result: self._refresh_accounts_from_settings(dialog),
            lambda error: QMessageBox.warning(dialog, "账号授权", product_error(error)),
        )

    def _refresh_accounts_from_settings(self, dialog: SettingsDialog) -> None:
        self._run_worker(
            self._load_settings_context,
            lambda context: self._apply_settings_context(dialog, context),
            lambda error: QMessageBox.warning(dialog, "刷新账号", product_error(error)),
        )

    def _store_for_account(self, account_id: str) -> str:
        for account in self.accounts:
            if account.account_id == account_id:
                return account.store_name
        return "当前店铺"

    def _show_settings_page(self) -> None:
        self._open_settings()

    def _operation_error(self, operation: str, message: str, *, execution: bool = False) -> None:
        self.poll_busy = False
        self._set_busy(False, f"{operation}未完成")
        if execution and not self.running_group and not self.pending_group_payload and not self.preparing_submission:
            self._set_execution_busy(False)
        readable = product_error(message)
        self.log(f"{operation}未完成：{readable}")
        QMessageBox.warning(self, operation, readable)

    def _set_busy(self, busy: bool, message: str) -> None:
        self.ui_busy = busy
        self.statusBar().showMessage(message)
        if not self.running_group and not self.pending_group_payload and not self.preparing_submission:
            self.execute_button.setEnabled(not busy and self._can_start_submission())
        self.records_refresh_button.setEnabled(not busy)

    def _set_execution_busy(self, busy: bool) -> None:
        self.execute_button.setEnabled(busy or self._can_start_submission())
        self.execute_button.setText("停止任务" if busy else "提交执行")
        for control in (self.mode_combo, self.store_combo, self.site_combo, self.seller_combo, self.official_combo):
            control.setEnabled(not busy)
        self._update_discount_state()

    def _set_prepare_busy(self, busy: bool) -> None:
        if not busy:
            self.execute_button.setText("提交执行")
            self.execute_button.setEnabled(self._can_start_submission())
            for control in (self.mode_combo, self.store_combo, self.site_combo, self.seller_combo, self.official_combo):
                control.setEnabled(True)
            self._update_discount_state()
            return
        state = str(self.preparing_submission.get("state") or "")
        has_prepare_id = bool(self.preparing_submission.get("prepare_id"))
        self.execute_button.setText("正在停止" if state == "stopping" else "停止准备" if has_prepare_id else "正在准备")
        self.execute_button.setEnabled(has_prepare_id and state != "stopping")
        for control in (self.mode_combo, self.store_combo, self.site_combo, self.seller_combo, self.official_combo):
            control.setEnabled(False)
        self._update_discount_state()

    def _can_start_submission(self) -> bool:
        if self.ui_busy or not self.scope_ready or not self.today_completion_ready:
            return False
        if self.mode_combo.currentText() == "自动判断" and self._completion_for_current_scope():
            return False
        return True

    def _sync_submit_availability(self) -> None:
        if self.running_group or self.pending_group_payload or self.preparing_submission:
            return
        self.execute_button.setEnabled(self._can_start_submission())

    def _discard_prepared_submission(self, prepare_id: str) -> None:
        if not prepare_id:
            return
        self._run_worker(
            lambda: self.api.post(f"/api/execution/submissions/{prepare_id}/cancel", {}, timeout=10),
            lambda _result: None,
            lambda _error: self.log("本次准备取消状态暂未同步，下次启动会继续安全核对。"),
        )

    def _request_cancel_prepare(self) -> None:
        prepare_id = str(self.preparing_submission.get("prepare_id") or "")
        if not prepare_id or str(self.preparing_submission.get("state") or "") == "stopping":
            return
        self.preparing_submission["state"] = "stopping"
        self._set_prepare_busy(True)
        self.log("正在停止准备，已完成的只读核对会保留，不会创建执行组或提交商品。")

        def stopped(_response: object) -> None:
            self.prepare_poll_timer.stop()
            self.prepare_poll_busy = False
            self.preparing_submission = {}
            self.pending_prepare_payload = None
            self.prepare_progress_key = ""
            self._set_prepare_busy(False)
            self.log("已停止准备，未创建执行组、未提交商品。")

        def failed(error: object) -> None:
            self.preparing_submission["state"] = "preparing"
            self._set_prepare_busy(True)
            if not self.prepare_poll_timer.isActive():
                self.prepare_poll_timer.start()
            self.log("停止准备请求暂未确认，正在继续查询状态：" + product_error(error))

        self._run_worker(
            lambda: self.api.post(f"/api/execution/submissions/{prepare_id}/cancel", {}, timeout=15),
            stopped,
            failed,
        )

    def log(self, message: str) -> None:
        self.log_box.append(f"[{datetime.now():%H:%M:%S}] {message}")

    def _run_worker(self, function: Callable[[], Any], on_result: Callable[[object], None], on_error: Callable[[object], None]) -> None:
        worker = Worker(function)
        self.workers.add(worker)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        worker.signals.finished.connect(lambda current=worker: self.workers.discard(current))
        self.thread_pool.start(worker)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        diagnostic_event("main_window_close_event", running_group=bool(self.running_group), already_closing=self._closing)
        if self.preparing_submission and not self.running_group and not self.pending_group_payload:
            prepare_id = str(self.preparing_submission.get("prepare_id") or "")
            progress = int(dict(self.preparing_submission.get("progress") or {}).get("percent") or 0)
            self.log("执行范围仍在后台准备；关闭界面后会继续核对，重新打开可接回同一进度。")
            diagnostic_event("prepare_detached_on_close", prepare_id=prepare_id, progress=progress)
            self.prepare_poll_timer.stop()
            self.poll_timer.stop()
            self.service.detach()
            event.accept()
            return
        if (self.running_group or self.pending_group_payload) and not self._closing:
            answer = QMessageBox.question(self, "关闭软件", "当前任务仍在执行。关闭会停止未完成任务并保留已完成结果，是否继续？")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._closing = True
            group_id = str(self.running_group.get("id") or "")
            prepare_id = str(dict(self.pending_group_payload or {}).get("prepare_id") or "")
            try:
                if prepare_id and not group_id:
                    result = self.api.post(f"/api/execution/submissions/{prepare_id}/cancel", {}, timeout=3)
                    group_id = str(dict(result.get("group") or {}).get("id") or "")
                elif not group_id:
                    active = self.api.get("/api/execution/groups/active", timeout=3)
                    group_id = str(dict(active.get("group") or {}).get("id") or "")
                if group_id:
                    self.api.post(f"/api/execution/groups/{group_id}/cancel", {}, timeout=3)
            except ApiError:
                self.log("关闭时提交停止状态暂未确认，程序组件将继续在后台收口；重新打开可恢复查询。")
                self.poll_timer.stop()
                self.prepare_poll_timer.stop()
                self.service.detach()
                event.accept()
                return
        self.poll_timer.stop()
        self.prepare_poll_timer.stop()
        self.service.stop()
        event.accept()


def resource_path(relative: str) -> Path:
    import sys

    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


def make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(True)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(36)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
    return table


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    return label


def discount_spin(value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(1, 90)
    spin.setValue(value)
    spin.setSuffix("%")
    spin.ensurePolished()
    probe_width = 200
    spin.resize(probe_width, spin.sizeHint().height())
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    edit_rect = spin.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox,
        option,
        QStyle.SubControl.SC_SpinBoxEditField,
        spin,
    )
    non_text_width = probe_width - edit_rect.width()
    text_width = spin.fontMetrics().horizontalAdvance("90%")
    spin.setFixedWidth(text_width + 8 + non_text_width)
    return spin


def short_date(value: str) -> str:
    if not value:
        return ""
    return value[:10].replace("-", "/")


def optional_contract_count(source: dict[str, Any], key: str) -> int | None:
    value = source.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def count_or_marker(value: int | None, missing: str = "-") -> str:
    return str(value) if value is not None else missing


def activity_summary_text(task: dict[str, Any]) -> str:
    parts = []
    seller = str(task.get("seller_activity_text") or "").strip()
    official = str(task.get("official_activity_text") or "").strip()
    if seller:
        parts.append("自建 " + seller)
    if official:
        parts.append("官方 " + official)
    return " / ".join(parts) or "-"


def record_result_text(task: dict[str, Any]) -> str:
    action = str(task.get("action") or "").lower()
    _total, success, _failed, skipped = task_display_counts(task)
    if action != "cancel":
        return f"成功 {success} / 跳过 {skipped}"
    request_success = optional_contract_count(task, "request_success_count")
    verified_removed = optional_contract_count(task, "live_verified_removed_count")
    pending = optional_contract_count(task, "pending_verification_count")
    if request_success is None and verified_removed is None and pending is None:
        return "旧记录未区分"
    return (
        f"取消请求 {count_or_marker(request_success)}\n"
        f"确认移除 {count_or_marker(verified_removed)}\n"
        f"待平台确认 {count_or_marker(pending)}"
    )


def execution_result_text(result: dict[str, Any], action: str) -> str:
    unique_items = optional_contract_count(result, "unique_item_count")
    relations = optional_contract_count(result, "relation_count")
    activity_failures = optional_contract_count(result, "activity_failure_count")
    failed = int(result.get("failed") or result.get("failed_count") or 0)
    skipped = int(result.get("skipped") or result.get("skipped_count") or 0)
    common = (
        f"唯一商品 {count_or_marker(unique_items, '旧记录未区分')}，"
        f"活动商品关系 {count_or_marker(relations)}"
    )
    if str(action or "").lower() == "cancel":
        request_success = optional_contract_count(result, "request_success_count")
        verified_removed = optional_contract_count(result, "live_verified_removed_count")
        pending = optional_contract_count(result, "pending_verification_count")
        cancellation = (
            f"取消请求成功 {count_or_marker(request_success, '旧记录未区分')}，"
            f"平台确认移除 {count_or_marker(verified_removed, '旧记录未区分')}，"
            f"取消请求已提交，待平台回查确认 {count_or_marker(pending, '旧记录未区分')}"
        )
        return (
            f"{common}，{cancellation}，商品失败 {failed}，"
            f"活动失败 {count_or_marker(activity_failures)}，跳过 {skipped}"
        )
    success = int(result.get("success") or result.get("success_count") or 0)
    return (
        f"{common}，成功 {success}，商品失败 {failed}，"
        f"活动失败 {count_or_marker(activity_failures)}，跳过 {skipped}"
    )


def promotion_type_text(value: str) -> str:
    return {
        "SELLER_CAMPAIGN": "自建活动",
        "DEAL": "官方活动",
        "SMART": "SMART",
        "LIGHTNING": "限时活动",
    }.get(value.upper(), value or "其它活动")


def status_text(value: str) -> str:
    return {
        "started": "进行中",
        "pending": "待开始",
        "candidate": "可报名",
        "completed": "已完成",
        "failed": "未完整完成",
        "cancelled": "已停止",
        "interrupted": "意外中断",
        "running": "执行中",
    }.get(value.lower(), value or "-")


def execution_log_message(value: object) -> str:
    if isinstance(value, dict):
        return execution_log_message(str(value.get("message") or ""))
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return value
            if isinstance(decoded, dict):
                return execution_log_message(str(decoded.get("message") or ""))
        terminal_counts = all(marker in text for marker in ("成功", "失败", "跳过"))
        if terminal_counts and (
            text.startswith("结束：总商品")
            or text.startswith("执行任务已按规则停止：")
            or "完成：活动" in text
        ):
            return ""
        return text
    return ""


def execution_job_summary(job: dict[str, Any]) -> tuple[str, dict[str, int]]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    request_summary = job.get("request_summary") if isinstance(job.get("request_summary"), dict) else {}
    action = str(result.get("action") or request_summary.get("action") or "")
    success = int(execution.get("success") or 0)
    failed = int(execution.get("failed") or 0)
    skipped = int(execution.get("skipped") or 0)
    total = max(int(execution.get("total") or 0), success + failed + skipped)
    return action, {"total": total, "success": success, "failed": failed, "skipped": skipped}


def business_task_text(task: dict[str, Any]) -> str:
    _total, success, failed, skipped = task_display_counts(task)
    unique_items = optional_contract_count(task, "unique_item_count")
    relations = optional_contract_count(task, "relation_count")
    activity_failures = optional_contract_count(task, "activity_failure_count")
    lines = [
        f"时间：{short_date(str(task.get('created_at') or ''))}\n"
        f"动作：{action_label(str(task.get('action') or ''))}\n"
        f"自建折扣：{task.get('seller_activity_text') or '-'}\n"
        f"官方折扣：{task.get('official_activity_text') or '-'}",
        f"唯一商品：{count_or_marker(unique_items, '旧记录未区分')}",
        f"活动商品关系：{count_or_marker(relations, '旧记录未区分')}",
    ]
    if str(task.get("action") or "").lower() == "cancel":
        lines.extend([
            f"取消请求成功：{count_or_marker(optional_contract_count(task, 'request_success_count'), '旧记录未区分')}",
            f"平台确认移除：{count_or_marker(optional_contract_count(task, 'live_verified_removed_count'), '旧记录未区分')}",
            "取消请求已提交，待平台回查确认："
            + count_or_marker(optional_contract_count(task, "pending_verification_count"), "旧记录未区分"),
        ])
    else:
        lines.append(f"成功：{success}")
    lines.extend([
        f"商品失败：{failed}",
        f"活动失败：{count_or_marker(activity_failures, '旧记录未区分')}",
        f"跳过：{skipped}",
        f"失败原因：{task.get('failure_reason') or task.get('short_failure_reason') or '-'}",
    ])
    return "\n".join(lines)


def business_details_text(task: dict[str, Any], details: list[dict[str, Any]]) -> str:
    lines = [business_task_text(task), "", "店铺 / 站点 / 活动明细："]
    for row in details:
        store = row.get("store_name") or "当前店铺"
        site = row.get("site_name") or site_name(str(row.get("site_id") or ""))
        activity = row.get("promotion_name") or row.get("activity_name") or "当前活动"
        lines.append(f"- {store} / {site} / {activity}：{execution_result_text(row, str(row.get('action') or task.get('action') or ''))}")
    return "\n".join(lines)


def benchmark_text(results: dict[str, Any]) -> str:
    latest = dict(results.get("write_latest_status") or {})
    stable = int(latest.get("verified_stable_concurrency") or 350)
    minimum = int(latest.get("daily_recommended_min") or 300)
    maximum = int(latest.get("daily_recommended_max") or 320)
    return f"自动并发按实测和接口反馈调整。当前重复验证最高稳定档 {stable}；日常建议 {minimum}-{maximum}。手动数值仅用于排障或主管要求。"


def product_error(message: str) -> str:
    clean = str(message or "").strip()
    if not clean:
        return "当前操作没有完成，请稍后重试。"
    technical = ("requires an element", "target element has type", "JsonElement", "fetch failed", "rate limit")
    if any(value.lower() in clean.lower() for value in technical):
        if "rate limit" in clean.lower():
            return "平台接口限流，请稍后重试。"
        if "fetch failed" in clean.lower():
            return "网络连接失败，请稍后重试。"
        return "任务结果不完整，已完成结果仍会保留，请查看历史记录。"
    return clean


QDialogAccepted = 1
