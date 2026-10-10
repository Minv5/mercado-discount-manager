from __future__ import annotations

import dataclasses
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QSignalBlocker, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QDesktopServices, QIcon, QStandardItem, QStandardItemModel, QTextCursor
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from api_client import ApiClient, ApiError
from callback_endpoints import migrate_oauth_redirect_uri
from core import (
    EXCLUDE_ACTIVITY,
    Account,
    ActionConflictError,
    account_from_json,
    action_for_mode,
    action_label,
    business_date_from_timestamp,
    build_filters,
    _coerce_business_date,
    completed_execution_for_scope,
    discount_inputs_enabled,
    execution_completion_text,
    execution_group_payload,
    normalize_activity_name,
    promotion_bucket,
    promotion_display_name,
    targeted_cancel_filters,
    resolve_global_action,
    site_name,
    task_display_counts,
)
from dialogs import (
    AutoShutdownCountdownDialog,
    CopyZeroVisitIdsDialog,
    DetailsDialog,
    ItemQueryDialog,
    LogViewer,
    SellerCampaignCreateDialog,
    SettingsDialog,
    TargetedCancelDialog,
    UpdateDialog,
    execute_system_shutdown,
    get_last_canceled_batch,
)
from engine.updater import ReleaseInfo, check_github_latest_release
from diagnostics import diagnostic_event
from reason_text import (
    business_reason_text,
    completeness_notice as _completeness_notice,
    has_readback_incomplete_reason as _has_readback_incomplete_reason,
    reason_matches as _reason_matches,
)
from service_manager import NodeServiceManager, ServiceError
from theme import APP_QSS
from workers import GuiDispatcher, Worker
from engine.item_cleaner import (
    ItemCleanerEngine,
    CleanerFilterCriteria,
    ScannedItemRecord,
    clear_cleaner_draft,
    is_item_confirmed_deleted,
    load_cleaner_draft,
    mark_cached_items_deleted,
    save_cleaner_draft,
)
from engine.client import MercadoClient
from engine.auth import AuthManager


TASK_HEADERS = ["时间", "动作", "折扣", "活动", "类型", "商品 / 处理项", "结果", "失败", "失败原因"]
ACTIVITY_HEADERS = ["店铺", "站点", "类型", "活动", "状态", "商品数"]
CLEANER_HEADERS = ["选择", "店铺", "商品ID", "站点", "商品标题", "刊登评分", "浏览量", "销售量", "上架时间", "状态", "不达标原因"]
RECORD_VIEW_LIMITS = {"recent": 20, "all": 300}
STARTUP_PHASE_LABELS = {
    "service_connect": "程序组件连接",
    "initial_bundle": "基础数据",
    "scope_bundle": "店铺与活动范围",
    "records_bundle": "今日执行记录",
    "startup_readiness": "启动缓存",
}


class CheckableComboBox(QComboBox):
    """支持多选复选框的下拉框控件，支持一键全选/反选与选择项自适应摘要显示。"""
    selectionChanged = Signal(list)

    def __init__(self, parent: QWidget | None = None, placeholder: str = "全部站点") -> None:
        super().__init__(parent)
        self.placeholder = placeholder
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.view().viewport().installEventFilter(self)
        self._line_edit = QLineEdit(self)
        self._line_edit.setReadOnly(True)
        self._line_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._line_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._line_edit.setStyleSheet("border: none; background: transparent; color: inherit; padding: 0;")
        self._line_edit.installEventFilter(self)
        self.setLineEdit(self._line_edit)
        self._model.itemChanged.connect(self._on_item_changed)
        self._updating = False

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj == self._line_edit:
            if event.type() == QEvent.Type.MouseButtonPress:
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                if self.view().isVisible():
                    self.hidePopup()
                else:
                    self.showPopup()
                return True
        if obj == self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            index = self.view().indexAt(event.pos())
            item = self._model.itemFromIndex(index)
            if item:
                if item.data(Qt.ItemDataRole.UserRole) == "__ALL__":
                    new_state = Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
                    self._updating = True
                    for r in range(self._model.rowCount()):
                        it = self._model.item(r)
                        if it:
                            it.setCheckState(new_state)
                    self._updating = False
                    self._update_display_text()
                else:
                    new_state = Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
                    item.setCheckState(new_state)
                    self._sync_all_checkbox()
                return True
        return super().eventFilter(obj, event)

    def _sync_all_checkbox(self) -> None:
        if self._updating:
            return
        all_item = None
        other_checked = 0
        total_others = 0
        for r in range(self._model.rowCount()):
            it = self._model.item(r)
            if not it:
                continue
            if it.data(Qt.ItemDataRole.UserRole) == "__ALL__":
                all_item = it
            else:
                total_others += 1
                if it.checkState() == Qt.CheckState.Checked:
                    other_checked += 1
        if all_item:
            self._updating = True
            if other_checked == total_others and total_others > 0:
                all_item.setCheckState(Qt.CheckState.Checked)
            elif other_checked > 0:
                all_item.setCheckState(Qt.CheckState.PartiallyChecked)
            else:
                all_item.setCheckState(Qt.CheckState.Unchecked)
            self._updating = False
        self._update_display_text()

    def _on_item_changed(self, item: QStandardItem) -> None:
        if self._updating:
            return
        self._sync_all_checkbox()

    def set_items(self, items: list[tuple[str, str]], checked_keys: list[str] | None = None) -> None:
        """items: [(display_name, key)]"""
        new_keys = [k for _, k in items]
        if self.all_keys() == new_keys and checked_keys is None:
            return
        self._updating = True
        self._model.clear()
        if not items:
            self._updating = False
            self._update_display_text()
            return

        all_item = QStandardItem("全部站点 (全选/反选)")
        all_item.setData("__ALL__", Qt.ItemDataRole.UserRole)
        all_item.setCheckable(True)
        all_item.setCheckState(Qt.CheckState.Checked)
        self._model.appendRow(all_item)

        initial_checked = set(checked_keys) if checked_keys is not None else {k for _, k in items}
        for name, key in items:
            it = QStandardItem(name)
            it.setData(key, Qt.ItemDataRole.UserRole)
            it.setCheckable(True)
            st = Qt.CheckState.Checked if key in initial_checked else Qt.CheckState.Unchecked
            it.setCheckState(st)
            self._model.appendRow(it)

        self._updating = False
        self._sync_all_checkbox()

    def checked_keys(self) -> list[str]:
        keys = []
        for r in range(self._model.rowCount()):
            it = self._model.item(r)
            if it and it.data(Qt.ItemDataRole.UserRole) != "__ALL__":
                if it.checkState() == Qt.CheckState.Checked:
                    keys.append(str(it.data(Qt.ItemDataRole.UserRole)))
        return keys

    def all_keys(self) -> list[str]:
        keys = []
        for r in range(self._model.rowCount()):
            it = self._model.item(r)
            if it and it.data(Qt.ItemDataRole.UserRole) != "__ALL__":
                keys.append(str(it.data(Qt.ItemDataRole.UserRole)))
        return keys

    def set_checked_keys(self, keys: list[str]) -> None:
        target_set = set(keys)
        self._updating = True
        for r in range(self._model.rowCount()):
            it = self._model.item(r)
            if it and it.data(Qt.ItemDataRole.UserRole) != "__ALL__":
                k = str(it.data(Qt.ItemDataRole.UserRole))
                it.setCheckState(Qt.CheckState.Checked if k in target_set else Qt.CheckState.Unchecked)
        self._updating = False
        self._sync_all_checkbox()

    def display_text(self) -> str:
        return self._line_edit.text()

    def _update_display_text(self) -> None:
        selected_names: list[str] = []
        total = 0
        for r in range(self._model.rowCount()):
            it = self._model.item(r)
            if it and it.data(Qt.ItemDataRole.UserRole) != "__ALL__":
                total += 1
                if it.checkState() == Qt.CheckState.Checked:
                    raw_text = it.text().split(" (")[0]
                    selected_names.append(raw_text)
        if total == 0:
            text = "无可用站点"
        elif len(selected_names) == total:
            text = f"全部站点 ({total}个)"
        elif not selected_names:
            text = "⚠️ 未选择站点"
        elif len(selected_names) <= 2:
            text = "、".join(selected_names)
        else:
            text = f"已选 {len(selected_names)}/{total} 站点"
        self._line_edit.setText(text)
        self.selectionChanged.emit(self.checked_keys())


class CleanerTableModel(QAbstractTableModel):
    def __init__(self, headers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.headers = list(headers)
        self._records: list[ScannedItemRecord] = []
        self._id_to_row: dict[str, int] = {}
        self.on_selection_changed_cb: Callable[[], None] | None = None

    @property
    def records(self) -> list[ScannedItemRecord]:
        return self._records

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.headers):
                return self.headers[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        default_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            return default_flags | Qt.ItemFlag.ItemIsUserCheckable
        return default_flags

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._records)):
            return None
        rec = self._records[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole:
            if col == 0:
                return Qt.CheckState.Checked if rec.is_selected_for_delete else Qt.CheckState.Unchecked
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return ""
            elif col == 1:
                return rec.store_name or rec.account_id
            elif col == 2:
                return rec.item_id
            elif col == 3:
                return rec.site_id
            elif col == 4:
                return rec.title
            elif col == 5:
                return f"{rec.score} 分 ({rec.level_wording})" if rec.score is not None else "未评估"
            elif col == 6:
                return str(rec.visits)
            elif col == 7:
                if rec.has_sales:
                    if "在售" in rec.status or rec.status == "active":
                        return f"⚠️ 已售 {rec.sold_quantity} 件 (在售保护)"
                    return f"已售 {rec.sold_quantity} 件 (已下架)"
                return "0"
            elif col == 8:
                return f"{rec.date_created} ({rec.days_on_sale}天前)" if rec.date_created else "-"
            elif col == 9:
                return rec.status
            elif col == 10:
                return ", ".join(rec.unmet_reasons)
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 7 and rec.has_sales:
                if "在售" in rec.status or rec.status == "active":
                    return QBrush(QColor("#ff8a65"))
                return QBrush(QColor("#C8C3B7"))
            if col == 9:
                if rec.status == "已删除":
                    return QBrush(QColor("#188038"))
                elif "失败" in rec.status or "err" in rec.status.lower() or "fail" in rec.status.lower():
                    return QBrush(QColor("#d93025"))
            return None

        if role == Qt.ItemDataRole.BackgroundRole:
            if rec.has_sales and ("在售" in rec.status or rec.status == "active"):
                return QBrush(QColor("#2d2019"))
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 4:
                return rec.title
            elif col == 7 and rec.has_sales:
                if "在售" in rec.status or rec.status == "active":
                    return f"该商品为在售出单核心资产（已售 {rec.sold_quantity} 件），系统默认排除在批量删除之外！"
                return f"该商品历史曾出单 {rec.sold_quantity} 件，但已被平台明确下架/封禁，默认纳入批量清理范围。"
            elif col == 8:
                return f"官方上架时间: {rec.date_created} (已在架 {rec.days_on_sale} 天)"
            elif col == 9:
                return f"当前状态: {rec.status}"
            elif col == 10:
                return ", ".join(rec.unmet_reasons)
            return None

        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """支持点击表头对任意指标列进行原生升序/降序重排。"""
        self.beginResetModel()
        reverse = (order == Qt.SortOrder.DescendingOrder)
        if column == 1:
            self._records.sort(key=lambda r: (r.store_name or ""), reverse=reverse)
        elif column == 2:
            self._records.sort(key=lambda r: r.item_id, reverse=reverse)
        elif column == 3:
            self._records.sort(key=lambda r: r.site_id, reverse=reverse)
        elif column == 4:
            self._records.sort(key=lambda r: r.title, reverse=reverse)
        elif column == 5:
            self._records.sort(key=lambda r: (r.score if r.score is not None else -1), reverse=reverse)
        elif column == 6:  # 浏览量 (整数数值排序)
            self._records.sort(key=lambda r: r.visits, reverse=reverse)
        elif column == 7:  # 销售量 (整数数值排序)
            self._records.sort(key=lambda r: r.sold_quantity, reverse=reverse)
        elif column == 8:  # 上架时间 (按上架天数数值排序)
            self._records.sort(key=lambda r: (r.days_on_sale, r.date_created), reverse=reverse)
        elif column == 9:  # 状态
            self._records.sort(key=lambda r: r.status, reverse=reverse)
        elif column == 10:  # 不达标原因
            self._records.sort(key=lambda r: (", ".join(r.unmet_reasons)), reverse=reverse)
        self._id_to_row = {r.item_id: idx for idx, r in enumerate(self._records)}
        self.endResetModel()

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or not (0 <= index.row() < len(self._records)):
            return False
        rec = self._records[index.row()]
        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            checked = (value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value or value == 2)
            if rec.is_selected_for_delete != checked:
                rec.is_selected_for_delete = checked
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
                if self.on_selection_changed_cb:
                    self.on_selection_changed_cb()
            return True
        return False

    def set_records(self, records: list[ScannedItemRecord]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self._id_to_row = {r.item_id: idx for idx, r in enumerate(self._records)}
        self.endResetModel()

    def upsert_records(self, new_records: list[ScannedItemRecord]) -> None:
        if not new_records:
            return
        truly_new: list[ScannedItemRecord] = []
        for rec in new_records:
            row = self._id_to_row.get(rec.item_id)
            if row is not None and 0 <= row < len(self._records):
                old_rec = self._records[row]
                # 1. 增量合并未达标原因（unmet_reasons），保持顺序去重
                merged_reasons = list(old_rec.unmet_reasons or [])
                for r in (rec.unmet_reasons or []):
                    if r not in merged_reasons:
                        merged_reasons.append(r)
                old_rec.unmet_reasons = merged_reasons

                # 2. 增量合并 sub_status
                merged_sub_st = list(old_rec.sub_status or [])
                for s in (rec.sub_status or []):
                    if s not in merged_sub_st:
                        merged_sub_st.append(s)
                old_rec.sub_status = merged_sub_st

                # 3. 刷新最新指标
                if rec.score is not None:
                    old_rec.score = rec.score
                    old_rec.level_wording = rec.level_wording
                old_rec.visits = rec.visits
                old_rec.sold_quantity = rec.sold_quantity
                old_rec.has_sales = rec.has_sales
                if rec.title:
                    old_rec.title = rec.title
                if rec.store_name:
                    old_rec.store_name = rec.store_name
                if rec.days_on_sale:
                    old_rec.days_on_sale = rec.days_on_sale
                if rec.date_created:
                    old_rec.date_created = rec.date_created

                # 4. 状态保护：已处于已删除终态的商品绝不倒退
                if is_item_confirmed_deleted(old_rec):
                    old_rec.status = "已删除"
                    old_rec.is_selected_for_delete = False
                else:
                    if rec.status:
                        old_rec.status = rec.status
                    is_active_sale = old_rec.has_sales and ("在售" in (rec.status or old_rec.status) or (rec.status or old_rec.status) == "active")
                    if is_active_sale:
                        old_rec.is_selected_for_delete = False
                    elif rec.is_selected_for_delete:
                        old_rec.is_selected_for_delete = True

                self.dataChanged.emit(
                    self.index(row, 0),
                    self.index(row, len(self.headers) - 1),
                    [
                        Qt.ItemDataRole.DisplayRole,
                        Qt.ItemDataRole.ForegroundRole,
                        Qt.ItemDataRole.CheckStateRole,
                        Qt.ItemDataRole.BackgroundRole,
                        Qt.ItemDataRole.ToolTipRole,
                    ],
                )
            else:
                truly_new.append(rec)

        if truly_new:
            first = len(self._records)
            last = first + len(truly_new) - 1
            self.beginInsertRows(QModelIndex(), first, last)
            self._records.extend(truly_new)
            for idx, r in enumerate(truly_new, start=first):
                self._id_to_row[r.item_id] = idx
            self.endInsertRows()

    def append_records(self, new_records: list[ScannedItemRecord]) -> None:
        self.upsert_records(new_records)

    def clear(self) -> None:
        self.beginResetModel()
        self._records.clear()
        self._id_to_row.clear()
        self.endResetModel()

    def update_item_status(self, item_id: str, new_status: str, succ: bool) -> None:
        row = self._id_to_row.get(item_id)
        if row is not None and 0 <= row < len(self._records):
            rec = self._records[row]
            rec.status = new_status
            if succ:
                rec.is_selected_for_delete = False
                sub_st = list(rec.sub_status or [])
                if "deleted" not in sub_st:
                    sub_st.append("deleted")
                rec.sub_status = sub_st
            self.dataChanged.emit(
                self.index(row, 0),
                self.index(row, len(self.headers) - 1),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.CheckStateRole],
            )

    def select_all_safe(self) -> int:
        skipped = 0
        for rec in self._records:
            if rec.has_sales:
                rec.is_selected_for_delete = False
                skipped += 1
            elif is_item_confirmed_deleted(rec):
                rec.is_selected_for_delete = False
                skipped += 1
            else:
                rec.is_selected_for_delete = True
        if self._records:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._records) - 1, 0),
                [Qt.ItemDataRole.CheckStateRole],
            )
        if self.on_selection_changed_cb:
            self.on_selection_changed_cb()
        return skipped

    def unselect_all(self) -> None:
        for rec in self._records:
            rec.is_selected_for_delete = False
        if self._records:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._records) - 1, 0),
                [Qt.ItemDataRole.CheckStateRole],
            )
        if self.on_selection_changed_cb:
            self.on_selection_changed_cb()


class MainWindow(QMainWindow):
    ready = Signal()

    def __init__(self, api: ApiClient, service: NodeServiceManager, *, auto_start: bool = True):
        super().__init__()
        self.api = api
        self.service = service
        self.thread_pool = QThreadPool.globalInstance()
        self.gui_dispatcher = GuiDispatcher(self)
        self.workers: set[Worker] = set()
        self.settings: dict[str, Any] = {}
        self.auto_shutdown_session_initialized = False
        self.accounts: list[Account] = []
        self.store_map: dict[str, list[str]] = {}
        self.promotions: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.records_cache: dict[str, list[dict[str, Any]]] = {}
        self.records_view = "recent"
        self.today_execution_groups: list[dict[str, Any]] = []
        self.current_today_completion: dict[str, Any] | None = None
        self.today_completion_ready = False
        self.startup_ready = False
        self.startup_refresh_status = "pending"
        self.startup_readiness: dict[str, Any] = {}
        self.startup_account_audits: list[dict[str, Any]] = []
        self.refresh_busy = False
        self.refresh_poll_busy = False
        self.refresh_poll_token = 0
        self.refresh_poll_inflight_token: int | None = None
        self.startup_attempt_token = 0
        self.today_completion_request_token = 0
        self.operating_rows_cache: list[dict[str, Any]] = []
        self.benchmark_text_cache = "自动并发按实测和接口反馈调整。"
        self.global_seller_discount = 28
        self.global_official_discount = 28
        self.auto_action = ""
        self.scope_refresh_token = 0
        self.scope_inputs_ready = False
        self.site_discovery_attempted: set[str] = set()
        self.initial_site_discovery_consumed = False
        self.initial_site_discovery_pending = False
        self.auto_decision_token = 0
        self.scope_ready = False
        self.scope_retry_count = 0
        self.running_group: dict[str, Any] = {}
        self.execution_started_at: float = 0.0
        self.pending_group_payload: dict[str, Any] | None = None
        self.preparing_submission: dict[str, Any] = {}
        self.pending_prepare_payload: dict[str, Any] | None = None
        self.prepare_poll_busy = False
        self.prepare_poll_failure_count = 0
        self.prepare_progress_key = ""
        self.prepare_read_key = ""
        self.prepare_stage_seen = ""
        self.refresh_progress_key = ""
        self.account_progress_key = ""
        self.startup_final_log_key = ""
        self.background_group_log_key = ""
        self.background_group_poll_busy = False
        self.initial_bundle_retry_count = 0
        self.initial_bundle_retry_token = 0
        self.startup_status_finalized = False
        self.startup_refresh_start_requested = False
        self.job_log_seen: dict[str, set[str]] = {}
        self.poll_failure_count = 0
        self.commit_recovery_poll_count = 0
        self.poll_busy = False
        self.records_request_token = 0
        self.ui_busy = False
        self._startup_phase_started_at: dict[str, float] = {}
        self._service_progress_key = ""
        self._closing = False
        self.cleaner_engine = ItemCleanerEngine(MercadoClient(AuthManager(), max_concurrency=64))
        self.cleaner_records: list[ScannedItemRecord] = []
        self.cleaner_scan_stop_event = threading.Event()
        self.cleaner_delete_stop_event = threading.Event()
        self.cleaner_is_scanning = False
        self.cleaner_is_deleting = False
        self.cleaner_model: CleanerTableModel | None = None
        self._cleaner_save_timer = QTimer(self)
        self._cleaner_save_timer.setSingleShot(True)
        self._cleaner_save_timer.setInterval(1200)
        self._cleaner_save_timer.timeout.connect(self._save_cleaner_draft_async)
        self.enrollment_log_box: LogViewer | None = None
        self.activity_log_box: LogViewer | None = None
        self.cleaner_log_box: LogViewer | None = None
        self.targeted_log_box: LogViewer | None = None
        self.query_log_box: LogViewer | None = None
        self.log_box: LogViewer | None = None
        self._log_auto_scroll = True
        self._latest_release_info: ReleaseInfo | None = None
        self._build_ui()
        self._load_cleaner_draft()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(900)
        self.poll_timer.timeout.connect(self._poll_group)
        self.refresh_poll_timer = QTimer(self)
        self.refresh_poll_timer.setInterval(5000)
        self.refresh_poll_timer.timeout.connect(self._poll_startup_refresh)
        self.background_group_timer = QTimer(self)
        self.background_group_timer.setInterval(15000)
        self.background_group_timer.timeout.connect(self._poll_background_execution_groups)
        self.initial_bundle_retry_timer = QTimer(self)
        self.initial_bundle_retry_timer.setSingleShot(True)
        self.initial_bundle_retry_timer.timeout.connect(self._retry_initial_bundle)
        self.prepare_poll_timer = QTimer(self)
        self.prepare_poll_timer.setInterval(1000)
        self.prepare_poll_timer.timeout.connect(self._poll_prepare)
        self.auto_reprice_timer = QTimer(self)
        self.auto_reprice_timer.setInterval(5000)
        self.auto_reprice_timer.timeout.connect(self._poll_auto_reprice)
        if auto_start:
            QTimer.singleShot(0, self.startup)
            QTimer.singleShot(3000, lambda: self._check_update_async(manual=False))

    def _build_ui(self) -> None:
        self.setWindowTitle("美客多活动管家")
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            target_w = min(1440, max(1120, int(avail.width() * 0.92)))
            target_h = min(880, max(680, int(avail.height() * 0.88)))
            min_w = min(1080, max(800, avail.width() - 40))
            min_h = min(640, max(500, avail.height() - 80))
            self.setMinimumSize(min_w, min_h)
            self.resize(target_w, target_h)
        else:
            self.setMinimumSize(1080, 640)
            self.resize(1280, 780)
        icon_path = resource_path("assets/app.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)
        root.addWidget(self._build_header())

        # 0: 活动报名页面（自包含左侧“报名参数控制面板”与右侧“执行记录表格”）
        self.enrollment_page = QSplitter(Qt.Orientation.Horizontal)
        self.enrollment_page.setChildrenCollapsible(False)
        self.controls = self._build_controls()
        self.controls.setMinimumWidth(320)
        self.controls.setMaximumWidth(400)
        self.enrollment_page.addWidget(self.controls)
        self.records_page, self.records_table = self._build_records_page()
        self.enrollment_page.addWidget(self.records_page)
        self.enrollment_page.setSizes([340, 960])

        # 1: 活动管理页面；2: 商品清理页面（各自作为独立全宽视图）
        self.activity_page, self.activity_table = self._build_activity_page()
        self.cleaner_page, self.cleaner_table = self._build_cleaner_page()

        self.pages = QStackedWidget()
        self.pages.addWidget(self.enrollment_page)
        self.pages.addWidget(self.activity_page)
        self.pages.addWidget(self.cleaner_page)

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.pages)

        self.settings_page = SettingsDialog(
            self.settings,
            self.accounts,
            list(self.operating_rows_cache),
            self.benchmark_text_cache,
            self,
            embedded=True,
        )
        self.settings_page.authorize_requested.connect(lambda: self._start_oauth(self.settings_page))
        self.settings_page.complete_authorization_requested.connect(lambda callback: self._complete_oauth(self.settings_page, callback))
        self.settings_page.refresh_requested.connect(lambda: self._refresh_accounts_from_settings(self.settings_page))
        self.settings_page.save_requested.connect(self._save_settings_from_page)
        self.settings_page.back_requested.connect(lambda: self._show_page(0))
        self.settings_page.check_update_requested.connect(lambda: self._check_update_async(manual=True))
        self.view_stack.addWidget(self.settings_page)

        self.query_page = ItemQueryDialog(self, embedded=True)
        self.query_log_box = self.query_page.log_box
        self.query_page.query_requested.connect(self._run_item_query_on_surface)
        self.query_page.back_requested.connect(lambda: self._show_page(0))
        self.view_stack.addWidget(self.query_page)

        self.targeted_cancel_page = TargetedCancelDialog(
            "当前范围",
            self,
            submission_ready=self._can_start_targeted_cancel,
            seller_discount=5,
            official_discount=6,
            embedded=True,
        )
        self.targeted_log_box = self.targeted_cancel_page.log_box
        self.targeted_cancel_page.submitted.connect(self._start_targeted_item_execution)
        self.targeted_cancel_page.back_requested.connect(lambda: self._show_page(0))
        self.view_stack.addWidget(self.targeted_cancel_page)

        # 主工作区：各功能页面内部自包含独立且贴合其布局的运行日志框
        root.addWidget(self.view_stack, 1)
        self.setCentralWidget(central)
        self.statusBar().hide()

    def _surface(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("surface")
        return frame

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("brandSurface")
        header.setFixedHeight(72)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setFixedSize(44, 44)
        pixmap = QIcon(str(resource_path("assets/app-icon.png"))).pixmap(QSize(44, 44))
        icon.setPixmap(pixmap)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(2)
        brand_text.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel("美客多活动管家")
        title.setObjectName("brandTitle")
        title_row.addWidget(title)

        self.version_label = QLabel(f"v{product_version()}")
        self.version_label.setObjectName("versionBadge")
        self.version_label.setToolTip("当前安装版本")
        self.version_label.setStyleSheet(
            "color: #C2A649; font-size: 11px; font-weight: bold; "
            "padding: 2px 7px; border: 1px solid #4E472F; border-radius: 4px; "
            "background: rgba(78, 71, 47, 0.25);"
        )
        title_row.addWidget(self.version_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.update_notice_btn = QPushButton("🚀 发现新版")
        self.update_notice_btn.setObjectName("updateNoticeBtn")
        self.update_notice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_notice_btn.setVisible(False)
        self.update_notice_btn.setStyleSheet(
            "QPushButton {"
            "  color: #10B981; font-size: 11px; font-weight: bold;"
            "  padding: 2px 9px; border: 1px solid #059669; border-radius: 4px;"
            "  background: rgba(16, 185, 129, 0.15);"
            "}"
            "QPushButton:hover {"
            "  background: rgba(16, 185, 129, 0.35);"
            "  border-color: #10B981;"
            "  color: #34D399;"
            "}"
        )
        self.update_notice_btn.clicked.connect(self._on_update_notice_clicked)
        title_row.addWidget(self.update_notice_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)

        subtitle = QLabel("批量管理美客多促销与折扣活动")
        subtitle.setObjectName("brandSubtitle")
        brand_text.addLayout(title_row)
        brand_text.addWidget(subtitle)
        layout.addLayout(brand_text)
        layout.addStretch(1)

        self.nav_buttons: list[QPushButton] = []

        # 1. 活动报名
        enrollment_btn = QPushButton("活动报名")
        enrollment_btn.setObjectName("nav")
        enrollment_btn.setCheckable(True)
        enrollment_btn.setFixedSize(90, 34)
        enrollment_btn.clicked.connect(lambda checked=False: self._show_page(0))
        self.nav_buttons.append(enrollment_btn)
        layout.addWidget(enrollment_btn)

        # 2. 活动管理
        activity_btn = QPushButton("活动管理")
        activity_btn.setObjectName("nav")
        activity_btn.setCheckable(True)
        activity_btn.setFixedSize(90, 34)
        activity_btn.clicked.connect(lambda checked=False: self._show_page(1))
        self.nav_buttons.append(activity_btn)
        layout.addWidget(activity_btn)

        # 3. 查 询
        self.query_button = QPushButton("查  询")
        self.query_button.setObjectName("nav")
        self.query_button.setCheckable(True)
        self.query_button.setFixedSize(90, 34)
        self.query_button.clicked.connect(self._show_query_page)
        layout.addWidget(self.query_button)

        # 4. 按ID操作
        self.targeted_cancel_button = QPushButton("按ID操作")
        self.targeted_cancel_button.setObjectName("nav")
        self.targeted_cancel_button.setCheckable(True)
        self.targeted_cancel_button.setFixedSize(90, 34)
        self.targeted_cancel_button.clicked.connect(self._show_targeted_cancel_page)
        layout.addWidget(self.targeted_cancel_button)

        # 5. 商品清理
        cleaner_btn = QPushButton("商品清理")
        cleaner_btn.setObjectName("nav")
        cleaner_btn.setCheckable(True)
        cleaner_btn.setFixedSize(90, 34)
        cleaner_btn.clicked.connect(lambda checked=False: self._show_page(2))
        self.nav_buttons.append(cleaner_btn)
        layout.addWidget(cleaner_btn)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("color: #4E472F; margin: 6px 4px;")
        layout.addWidget(sep)

        # 6. 设 置
        self.settings_button = QPushButton("设  置")
        self.settings_button.setObjectName("nav")
        self.settings_button.setCheckable(True)
        self.settings_button.setFixedSize(90, 34)
        self.settings_button.clicked.connect(self._show_settings_page)
        layout.addWidget(self.settings_button)

        layout.addSpacing(6)
        self.auto_shutdown_check = QCheckBox("执行完自动关机")
        self.auto_shutdown_check.setObjectName("muted")
        self.auto_shutdown_check.setChecked(bool(self.settings.get("autoShutdownAfterExecution")))
        self.auto_shutdown_check.stateChanged.connect(self._on_auto_shutdown_toggled)
        layout.addWidget(self.auto_shutdown_check)
        self.nav_buttons[0].setChecked(True)
        return header

    def _build_controls(self) -> QFrame:
        frame = self._surface()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("controlContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 4, 0)
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
        self.seller_discount = discount_spin(self.global_seller_discount)
        self.official_discount = discount_spin(self.global_official_discount)
        self.seller_discount.valueChanged.connect(self._on_discount_spin_changed)
        self.official_discount.valueChanged.connect(self._on_discount_spin_changed)
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
        self.execute_button = QPushButton("开始执行")
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
        layout.setSpacing(6)

        top_frame = QFrame()
        top_layout = QVBoxLayout(top_frame)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(section_label("执行记录"))
        top.addStretch(1)
        self.records_view_combo = QComboBox()
        self.records_view_combo.addItem("最近20", "recent")
        self.records_view_combo.addItem("全部历史", "all")
        self.records_view_combo.setFixedWidth(130)
        self.records_view_combo.setFixedHeight(32)
        self.records_view_combo.currentIndexChanged.connect(self._records_view_changed)
        self.records_refresh_button = QPushButton("刷新")
        self.records_refresh_button.setFixedHeight(32)
        self.records_refresh_button.clicked.connect(self.refresh_records)
        QWidget.setTabOrder(self.records_view_combo, self.records_refresh_button)
        top.addWidget(self.records_view_combo)
        top.addWidget(self.records_refresh_button)
        top_layout.addLayout(top)
        self.records_delta_label = QLabel("较昨日商品变化：暂无可比较快照，数据不足")
        self.records_delta_label.setObjectName("muted")
        self.records_delta_label.setToolTip("需要服务端提供前一日和当日的完整商品身份快照后才能计算，界面不会根据不完整数据推算。")
        top_layout.addWidget(self.records_delta_label)
        table = make_table(TASK_HEADERS)
        table.horizontalHeaderItem(5).setToolTip(
            "涉及商品是按商品编号去重后的件数；处理项是商品×活动的组合数，同一商品参加多个活动会生成多条任务。"
        )
        table.horizontalHeaderItem(6).setToolTip("批量取消显示取消请求成功、成功取消和待平台确认；其它动作显示成功与跳过。")
        table.horizontalHeaderItem(7).setToolTip("前者是商品失败，后者是活动失败；活动失败不计入商品失败。")
        header = table.horizontalHeader()
        header.setMinimumSectionSize(54)
        for column in (0, 1, 2, 4, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (3, 5, 6, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.itemDoubleClicked.connect(lambda _item: self._show_task_details())
        table.itemSelectionChanged.connect(self._show_selected_summary)
        top_layout.addWidget(table, 1)

        # 独立运行日志框
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(4)
        log_layout.addWidget(section_label("运行日志"))
        self.enrollment_log_box = LogViewer()
        self.log_box = self.enrollment_log_box  # 向后兼容
        log_layout.addWidget(self.enrollment_log_box, 1)

        records_splitter = QSplitter(Qt.Orientation.Vertical)
        records_splitter.setChildrenCollapsible(False)
        records_splitter.addWidget(top_frame)
        records_splitter.addWidget(log_frame)
        records_splitter.setSizes([380, 180])

        layout.addWidget(records_splitter, 1)
        return surface, table

    def _build_activity_page(self) -> tuple[QFrame, QTableWidget]:
        surface = self._surface()
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        top_frame = QFrame()
        top_layout = QVBoxLayout(top_frame)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(section_label("活动管理"))
        top.addStretch(1)
        self.activity_refresh_local_btn = QPushButton("刷新列表")
        self.activity_refresh_local_btn.setFixedHeight(32)
        self.activity_reload_live_btn = QPushButton("重新读取活动")
        self.activity_reload_live_btn.setFixedHeight(32)
        self.full_get_button = QPushButton("全量GET数据")
        self.full_get_button.setFixedHeight(32)
        self.activity_refresh_local_btn.clicked.connect(self._on_activity_refresh_clicked)
        self.activity_reload_live_btn.clicked.connect(self._reload_live_promotions)
        self.full_get_button.clicked.connect(self._on_full_get_clicked)
        top.addWidget(self.activity_refresh_local_btn)
        top.addWidget(self.activity_reload_live_btn)
        top.addWidget(self.full_get_button)
        top_layout.addLayout(top)

        table = make_table(ACTIVITY_HEADERS)
        header = table.horizontalHeader()
        header.setMinimumSectionSize(54)
        for column in (0, 1, 2, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        top_layout.addWidget(table, 1)

        # 独立运行日志框
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(4)
        log_layout.addWidget(section_label("活动运行日志"))
        self.activity_log_box = LogViewer()
        self.activity_log_box.append_log_line(f"[{datetime.now():%H:%M:%S}] [活动管理] 活动管理引擎就绪。点击上方按钮可刷新或全量读取美客多活动。")
        log_layout.addWidget(self.activity_log_box, 1)

        activity_splitter = QSplitter(Qt.Orientation.Vertical)
        activity_splitter.setChildrenCollapsible(False)
        activity_splitter.addWidget(top_frame)
        activity_splitter.addWidget(log_frame)
        activity_splitter.setSizes([380, 180])

        layout.addWidget(activity_splitter, 1)
        return surface, table

    def _on_activity_refresh_clicked(self) -> None:
        self.log("[活动管理] 正在刷新活动列表...")
        self.refresh_scope()

    def _build_cleaner_page(self) -> tuple[QFrame, QTableWidget]:
        surface = self._surface()
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        # 顶栏：标题 + 清店模式专属醒目标签 + 店铺选择与扫描上限
        top_bar = QHBoxLayout()
        top_bar.addWidget(section_label("商品合规风控与清理"))
        top_bar.addStretch(1)

        mode_badge = QFrame()
        mode_badge.setStyleSheet("background: rgba(224, 108, 117, 0.12); border: 1px solid #e06c75; border-radius: 6px;")
        mode_badge_layout = QHBoxLayout(mode_badge)
        mode_badge_layout.setContentsMargins(8, 3, 8, 3)
        self.cleaner_wipe_store_check = QCheckBox("⚠️ 清店模式（清空全店商品）")
        self.cleaner_wipe_store_check.setChecked(False)
        self.cleaner_wipe_store_check.setToolTip("开启后将忽略常规过滤条件，极速检索全店铺所有商品并设为待删除")
        self.cleaner_wipe_store_check.setStyleSheet("color: #ff7b85; font-weight: bold; border: none; background: transparent;")
        self.cleaner_wipe_store_check.toggled.connect(self._on_cleaner_wipe_store_toggled)
        mode_badge_layout.addWidget(self.cleaner_wipe_store_check)
        top_bar.addWidget(mode_badge)
        top_bar.addSpacing(16)

        top_bar.addWidget(QLabel("选择店铺:"))
        self.cleaner_account_combo = QComboBox()
        self.cleaner_account_combo.setMinimumWidth(220)
        self.cleaner_account_combo.setFixedHeight(32)
        self.cleaner_account_combo.addItem("全部店铺（合并分析所有店铺）", "all")
        self.cleaner_account_combo.currentIndexChanged.connect(self._refresh_cleaner_sites)
        top_bar.addWidget(self.cleaner_account_combo)
        top_bar.addSpacing(16)

        top_bar.addWidget(QLabel("选择站点:"))
        self.cleaner_site_combo = CheckableComboBox()
        self.cleaner_site_combo.setMinimumWidth(200)
        self.cleaner_site_combo.setFixedHeight(32)
        top_bar.addWidget(self.cleaner_site_combo)
        top_bar.addSpacing(12)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # 过滤控制面板（黑金卡片规范 + 4列精准栅格对齐）
        filter_box = QFrame()
        filter_box.setObjectName("cleanerFilterSection")
        fb_layout = QVBoxLayout(filter_box)
        fb_layout.setContentsMargins(12, 10, 12, 10)
        fb_layout.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)

        # Row 0: 浏览量过滤 + 刊登质量评分过滤
        self.cleaner_visits_check = QCheckBox("浏览量过滤:")
        self.cleaner_visits_check.setChecked(True)
        self.cleaner_visits_check.setToolTip("开启后过滤上架至今总浏览量低于门槛的商品（结合上架时长门槛可精准筛选上架超期且无流量的商品）")
        grid.addWidget(self.cleaner_visits_check, 0, 0)

        self.cleaner_visits_threshold_combo = QComboBox()
        self.cleaner_visits_threshold_combo.setFixedHeight(32)
        self.cleaner_visits_threshold_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cleaner_visits_threshold_combo.addItem("等于 0 次 (至今无流量)", 0)
        self.cleaner_visits_threshold_combo.addItem("≤ 5 次 (几乎无流量)", 5)
        self.cleaner_visits_threshold_combo.addItem("≤ 10 次 (极低流量)", 10)
        self.cleaner_visits_threshold_combo.addItem("≤ 20 次 (低流量)", 20)
        grid.addWidget(self.cleaner_visits_threshold_combo, 0, 1)

        self.cleaner_score_check = QCheckBox("刊登质量评分过滤:")
        self.cleaner_score_check.setChecked(True)
        grid.addWidget(self.cleaner_score_check, 0, 2)

        self.cleaner_score_spin = QSpinBox()
        self.cleaner_score_spin.setFixedHeight(32)
        self.cleaner_score_spin.setRange(0, 100)
        self.cleaner_score_spin.setValue(60)
        self.cleaner_score_spin.setPrefix("低于 ")
        self.cleaner_score_spin.setSuffix(" 分")
        grid.addWidget(self.cleaner_score_spin, 0, 3)

        # Row 1: 上架时长门槛 (老品筛选/新品保护) + 违规失效品 + 判定模式
        self.cleaner_grace_check = QCheckBox("上架时长门槛:")
        self.cleaner_grace_check.setChecked(True)
        self.cleaner_grace_check.setToolTip("开启后仅处理上架时长超过指定天数的商品（自动保护未满天数的新品不被清理）")
        grid.addWidget(self.cleaner_grace_check, 1, 0)

        grace_h = QHBoxLayout()
        grace_h.setSpacing(14)
        grace_h.setContentsMargins(0, 0, 0, 0)
        self.cleaner_grace_spin = QSpinBox()
        self.cleaner_grace_spin.setFixedHeight(32)
        self.cleaner_grace_spin.setRange(1, 180)
        self.cleaner_grace_spin.setValue(30)
        self.cleaner_grace_spin.setPrefix("仅处理超过 ")
        self.cleaner_grace_spin.setSuffix(" 天商品 (保护新品)")
        grace_h.addWidget(self.cleaner_grace_spin)
        self.cleaner_policy_check = QCheckBox("违规失效品 (因违反政策失效 / 举报停用)")
        self.cleaner_policy_check.setChecked(True)
        grace_h.addWidget(self.cleaner_policy_check)
        grace_h.addStretch(1)
        grid.addLayout(grace_h, 1, 1)

        grid.addWidget(QLabel("判定模式:"), 1, 2)

        self.cleaner_filter_mode_combo = QComboBox()
        self.cleaner_filter_mode_combo.setFixedHeight(32)
        self.cleaner_filter_mode_combo.addItem("全部满足 (AND)", "and")
        self.cleaner_filter_mode_combo.addItem("任一满足 (OR)", "or")
        self.cleaner_filter_mode_combo.setCurrentIndex(0)
        grid.addWidget(self.cleaner_filter_mode_combo, 1, 3)

        # 联动控制：勾选状态与子参数可用性同步
        self.cleaner_visits_check.toggled.connect(self._sync_cleaner_filter_states)
        self.cleaner_score_check.toggled.connect(self._sync_cleaner_filter_states)
        self.cleaner_grace_check.toggled.connect(self._sync_cleaner_filter_states)
        self._sync_cleaner_filter_states()

        fb_layout.addLayout(grid)

        # 清店模式专属动态横幅
        self.cleaner_wipe_notice = QLabel("⚠️ 当前处于【清店模式】：已自动忽略上方常规风控条件，点击「扫描」将检索全店铺商品并全选为待删除。")
        self.cleaner_wipe_notice.setStyleSheet("color: #ff7b85; font-weight: bold; padding: 4px 8px; background: rgba(224, 108, 117, 0.12); border-radius: 4px;")
        self.cleaner_wipe_notice.setVisible(False)
        fb_layout.addWidget(self.cleaner_wipe_notice)

        layout.addWidget(filter_box)

        table = QTableView()
        self.cleaner_model = CleanerTableModel(CLEANER_HEADERS, self)
        self.cleaner_model.on_selection_changed_cb = self._on_cleaner_selection_changed
        table.setModel(self.cleaner_model)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setShowGrid(True)
        table.setSortingEnabled(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(32)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 48)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(1, 110)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(2, 130)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(3, 70)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(4, 250)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(5, 110)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(6, 85)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(7, 120)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(8, 165)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(9, 115)
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(10, 280)

        # 独立运行日志框与商品表格垂直分割
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(4)
        log_layout.addWidget(section_label("商品清理运行日志"))
        self.cleaner_log_box = LogViewer()
        self.cleaner_log_box.append_log_line(f"[{datetime.now():%H:%M:%S}] [商品清理] 风控与清理引擎就绪。支持常规风控筛选或清店模式。")
        log_layout.addWidget(self.cleaner_log_box, 1)

        cleaner_splitter = QSplitter(Qt.Orientation.Vertical)
        cleaner_splitter.setChildrenCollapsible(False)
        cleaner_splitter.addWidget(table)
        cleaner_splitter.addWidget(log_frame)
        cleaner_splitter.setSizes([350, 160])
        layout.addWidget(cleaner_splitter, 1)

        bottom_box = QVBoxLayout()
        bottom_box.setSpacing(6)
        action_bar = QHBoxLayout()

        self.cleaner_scan_btn = QPushButton("扫描待清理商品")
        self.cleaner_scan_btn.setFixedSize(140, 34)
        self.cleaner_scan_btn.setStyleSheet("font-weight: bold; padding: 6px 10px;")
        self.cleaner_scan_btn.clicked.connect(self._on_start_cleaner_scan)

        self.cleaner_batch_delete_btn = QPushButton("批量删除")
        self.cleaner_batch_delete_btn.setFixedSize(110, 34)
        self.cleaner_batch_delete_btn.setStyleSheet("font-weight: bold; padding: 6px 10px;")
        self.cleaner_batch_delete_btn.clicked.connect(self._on_cleaner_batch_delete)

        self.cleaner_copy_zero_visits_btn = QPushButton("复制0浏览ID")
        self.cleaner_copy_zero_visits_btn.setFixedSize(130, 34)
        self.cleaner_copy_zero_visits_btn.setStyleSheet("font-weight: bold; padding: 6px 10px;")
        self.cleaner_copy_zero_visits_btn.clicked.connect(self._on_copy_zero_visits_ids)

        self.cleaner_clear_draft_btn = QPushButton("清除记录")
        self.cleaner_clear_draft_btn.setFixedSize(110, 34)
        self.cleaner_clear_draft_btn.setStyleSheet("font-weight: bold; padding: 6px 10px;")
        self.cleaner_clear_draft_btn.clicked.connect(self._on_cleaner_clear_records)

        action_bar.addWidget(self.cleaner_scan_btn)
        action_bar.addWidget(self.cleaner_batch_delete_btn)
        action_bar.addWidget(self.cleaner_copy_zero_visits_btn)
        action_bar.addWidget(self.cleaner_clear_draft_btn)
        action_bar.addStretch(1)
        bottom_box.addLayout(action_bar)

        layout.addLayout(bottom_box)
        return surface, table

    def _sync_cleaner_filter_states(self) -> None:
        if getattr(self, "cleaner_wipe_store_check", None) and self.cleaner_wipe_store_check.isChecked():
            return
        v_enabled = self.cleaner_visits_check.isChecked()
        self.cleaner_visits_threshold_combo.setEnabled(v_enabled)

        s_enabled = self.cleaner_score_check.isChecked()
        self.cleaner_score_spin.setEnabled(s_enabled)

        g_enabled = self.cleaner_grace_check.isChecked()
        self.cleaner_grace_spin.setEnabled(g_enabled)

    def _on_cleaner_wipe_store_toggled(self, checked: bool) -> None:
        disabled = checked
        if hasattr(self, "cleaner_wipe_notice") and self.cleaner_wipe_notice is not None:
            self.cleaner_wipe_notice.setVisible(checked)
        self.cleaner_visits_check.setEnabled(not disabled)
        self.cleaner_score_check.setEnabled(not disabled)
        self.cleaner_policy_check.setEnabled(not disabled)
        self.cleaner_grace_check.setEnabled(not disabled)
        self.cleaner_filter_mode_combo.setEnabled(not disabled)
        if not disabled:
            self._sync_cleaner_filter_states()
        else:
            self.cleaner_visits_threshold_combo.setEnabled(False)
            self.cleaner_score_spin.setEnabled(False)
            self.cleaner_grace_spin.setEnabled(False)
        if checked:
            self.log("[清店] ⚠️ 已切换至【清店模式】（清空全店商品）。点击扫描将极速检索店铺所有在售/下架商品并设为待删除。")
        else:
            self.log("[商品清理] 已切回【常规风控过滤模式】。")

    def _cleaner_append_log(self, text: str) -> None:
        if hasattr(self, "cleaner_log_box") and self.cleaner_log_box is not None:
            self.cleaner_log_box.append_log_line(f"[{datetime.now():%H:%M:%S}] {text}")
        else:
            self.log(text)

    def _load_cleaner_draft(self) -> None:
        try:
            records = load_cleaner_draft()
            if not records:
                return
            valid_records = [r for r in records if r.item_id and r.account_id]
            if not valid_records:
                return
            self.cleaner_records = valid_records
            if hasattr(self, "cleaner_model") and self.cleaner_model is not None:
                self.cleaner_model.set_records(valid_records)
            self._update_cleaner_stats()
            self.log(f"[商品清理] 发现上次暂存的扫描草稿，已自动恢复 {len(valid_records)} 件商品（支持直接批量删除，或点击「清除记录」）。")
        except Exception as e:
            self.log(f"[商品清理] 加载未完成草稿异常: {e}")

    def _on_cleaner_clear_records(self) -> None:
        if self.cleaner_is_scanning or self.cleaner_is_deleting:
            QMessageBox.warning(self, "商品清理", "任务正在执行中，请先停止当前任务！")
            return
        if not self.cleaner_records:
            QMessageBox.information(self, "清除记录", "当前暂无任何扫描商品记录，无需清除。")
            return
        reply = QMessageBox.question(
            self,
            "清除记录确认",
            "确定要清除当前所有扫描商品记录吗？\n\n操作将清空本地列表与草稿缓存，此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        clear_cleaner_draft()
        self.cleaner_records.clear()
        if hasattr(self, "cleaner_model") and self.cleaner_model is not None:
            self.cleaner_model.clear()
        self._update_cleaner_stats()
        self.log("[商品清理] 已清除本地所有扫描商品记录与草稿缓存。")

    def _on_cleaner_rescan(self) -> None:
        self._on_cleaner_clear_records()

    def _on_start_cleaner_scan(self, bypass_confirm: bool = False) -> None:
        if self.cleaner_is_scanning:
            self.cleaner_scan_stop_event.set()
            self.cleaner_scan_btn.setText("正在停止...")
            self.cleaner_scan_btn.setEnabled(False)
            self.log("[商品清理] 已请求中止扫描，正在等待当前请求结束...")
            return

        selected_key = str(self.cleaner_account_combo.currentData() or "all")
        if selected_key == "all":
            target_accounts = list(self.accounts)
        else:
            target_accounts = [a for a in self.accounts if a.account_id == selected_key]

        if not target_accounts:
            QMessageBox.warning(self, "商品扫描", "未找到可扫描的已授权店铺，请先添加店铺授权！")
            return

        is_wipe = hasattr(self, "cleaner_wipe_store_check") and self.cleaner_wipe_store_check.isChecked()
        if is_wipe and not bypass_confirm:
            store_str = "全部店铺" if selected_key == "all" else (target_accounts[0].store_name or target_accounts[0].account_id)
            confirm = QMessageBox.question(
                self,
                "清店模式扫描确认",
                f"当前已开启【清店（清空全店商品）】模式！\n\n"
                f"将检索【{store_str}】的全量商品并将其全部标记为待删除。\n\n"
                f"确认开始扫描全店商品吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        # 站点多选校验
        selected_sites: list[str] = []
        if hasattr(self, "cleaner_site_combo"):
            selected_sites = self.cleaner_site_combo.checked_keys()
            all_sites = self.cleaner_site_combo.all_keys()
            if all_sites and not selected_sites:
                QMessageBox.warning(self, "商品扫描", "请至少勾选一个需要扫描的站点！")
                return

        scan_limit = 100000

        target_account_ids = {acc.account_id for acc in target_accounts}
        is_all = (selected_key == "all")
        if is_all or not self.cleaner_records:
            self.cleaner_records.clear()
            if hasattr(self, "cleaner_model") and self.cleaner_model is not None:
                self.cleaner_model.clear()
            clear_cleaner_draft()
        else:
            self.cleaner_records = [r for r in self.cleaner_records if r.account_id not in target_account_ids]
            if hasattr(self, "cleaner_model") and self.cleaner_model is not None:
                self.cleaner_model.set_records(self.cleaner_records)
            save_cleaner_draft(self.cleaner_records)

        self.cleaner_scan_stop_event.clear()
        self.cleaner_is_scanning = True
        self.cleaner_scan_btn.setText("停止扫描")
        self.cleaner_scan_btn.setEnabled(True)
        self.cleaner_batch_delete_btn.setEnabled(False)
        self.cleaner_clear_draft_btn.setEnabled(False)
        self.log("[商品扫描] 正在准备扫描分析（已重置目标店铺旧有记录，开启全新权威排查）...")

        def _worker():
            try:
                tot_accs = len(target_accounts)
                is_all = (selected_key == "all")

                site_desc = "全部站点" if not selected_sites or (hasattr(self, "cleaner_site_combo") and len(selected_sites) == len(self.cleaner_site_combo.all_keys())) else "、".join(selected_sites)
                self.gui_dispatcher.dispatch(lambda: self.log(
                    f"[商品扫描] ==================== 开始多店铺并发商品合规扫描 ===================="
                ))
                self.gui_dispatcher.dispatch(lambda: self.log(
                    f"[商品扫描] 扫描范围: {'【全部店铺】(' + str(tot_accs) + '个店铺全并发扫描)' if is_all else ('【' + (target_accounts[0].store_name or target_accounts[0].account_id) + '】')} | "
                    f"目标站点: {site_desc} | "
                    f"单店上限: 无限制"
                ))

                def _scan_single_store(acc_item: tuple[int, Any]):
                    acc_idx, acc = acc_item
                    if self.cleaner_scan_stop_event.is_set():
                        return

                    store_name = acc.store_name or acc.account_id
                    self.gui_dispatcher.dispatch(lambda i=acc_idx, n=store_name, t=tot_accs: (
                        self.log(f"[商品扫描] 🚀 店铺并发启动 [{i}/{t}]: 【{n}】(ID: {acc.account_id})")
                    ))

                    crit = CleanerFilterCriteria(
                        account_id=acc.account_id,
                        store_name=store_name,
                        selected_site_ids=selected_sites,
                        filter_mode=str(self.cleaner_filter_mode_combo.currentData() or "and"),
                        enable_grace_period=self.cleaner_grace_check.isChecked(),
                        grace_period_days=self.cleaner_grace_spin.value(),
                        enable_visits_filter=self.cleaner_visits_check.isChecked(),
                        visits_mode="total",
                        visits_days=30,
                        visits_is_zero_only=(self.cleaner_visits_threshold_combo.currentIndex() == 0),
                        visits_threshold=int(self.cleaner_visits_threshold_combo.currentData() or 0),
                        enable_score_filter=self.cleaner_score_check.isChecked(),
                        score_threshold=self.cleaner_score_spin.value(),
                        enable_policy_filter=self.cleaner_policy_check.isChecked(),
                        max_scan_limit=scan_limit,
                        is_wipe_store_mode=is_wipe,
                    )

                    matched_buffer: list[ScannedItemRecord] = []
                    buf_lock = threading.Lock()
                    last_flush = [time.monotonic()]

                    def _flush_buffer(force: bool = False):
                        with buf_lock:
                            if not matched_buffer:
                                return
                            now_m = time.monotonic()
                            if force or len(matched_buffer) >= 200 or (now_m - last_flush[0]) >= 0.5:
                                batch = list(matched_buffer)
                                matched_buffer.clear()
                                last_flush[0] = now_m
                                self.gui_dispatcher.dispatch(lambda b=batch: self._cleaner_add_table_rows_batch(b))

                    def _on_matched(rec: ScannedItemRecord):
                        with buf_lock:
                            matched_buffer.append(rec)
                        _flush_buffer(force=False)

                    def _on_prog(cur: int, tot: int, step: str):
                        pass

                    def _on_lg(msg: str):
                        self.gui_dispatcher.dispatch(lambda m=msg: self.log(m))

                    self.cleaner_engine.scan_shop_items(
                        crit,
                        on_item_matched=_on_matched,
                        on_progress=_on_prog,
                        on_log=_on_lg,
                        stop_event=self.cleaner_scan_stop_event,
                    )
                    _flush_buffer(force=True)

                with ThreadPoolExecutor(max_workers=max(1, tot_accs)) as scan_executor:
                    futures = [
                        scan_executor.submit(_scan_single_store, (idx, acc))
                        for idx, acc in enumerate(target_accounts, 1)
                    ]
                    wait(futures)

                from collections import Counter
                valid_records = list(self.cleaner_records)
                store_counts = Counter(r.store_name or r.account_id for r in valid_records)
                store_breakdown = " | ".join(f"【{name}】: {cnt:,}件" for name, cnt in store_counts.items()) if store_counts else "无"
                tot_unmet = len(valid_records)
                tot_sold_protect = sum(1 for r in valid_records if r.has_sales)
                tot_for_delete = sum(1 for r in valid_records if r.is_selected_for_delete)
                tot_unselected = tot_unmet - tot_sold_protect - tot_for_delete
                if tot_unselected > 0:
                    summary_details = f"其中 {tot_sold_protect:,} 件已出单自动保护，{tot_for_delete:,} 件待清理删除，{tot_unselected:,} 件未勾选"
                else:
                    summary_details = f"其中 {tot_sold_protect:,} 件已出单自动保护，{tot_for_delete:,} 件待清理删除"
                summary_lines = [
                    "[商品扫描] ==================== 扫描分析全部完成 ====================",
                    f"[商品扫描] 店铺分项统计: {store_breakdown}",
                    f"[商品扫描] 全局汇总: 发现不达标商品 {tot_unmet:,} 件 ({summary_details})",
                ]
                self.gui_dispatcher.dispatch(lambda m="\n".join(summary_lines): self.log(m))
            except Exception as e:
                err_msg = str(e)
                self.gui_dispatcher.dispatch(lambda m=err_msg: self.log(f"[商品扫描] ❌ 扫描过程异常: {m}"))
            finally:
                self.gui_dispatcher.dispatch(self._on_cleaner_scan_finished)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_stop_cleaner_scan(self) -> None:
        self.cleaner_scan_stop_event.set()
        self.cleaner_scan_btn.setText("正在停止")
        self.cleaner_scan_btn.setEnabled(False)
        self.log("[商品扫描] 已请求中止扫描，正在等待当前请求结束")

    def _on_stop_cleaner_delete(self) -> None:
        self.cleaner_delete_stop_event.set()
        self.cleaner_batch_delete_btn.setText("正在停止...")
        self.cleaner_batch_delete_btn.setEnabled(False)
        self.log("[商品删除] 🛑 已请求中止批量删除，正在等待当前请求安全收口...")

    def _on_cleaner_scan_finished(self) -> None:
        self.cleaner_is_scanning = False
        self.cleaner_scan_btn.setText("扫描待清理商品")
        self.cleaner_scan_btn.setEnabled(True)
        self.cleaner_batch_delete_btn.setEnabled(True)
        self.cleaner_clear_draft_btn.setEnabled(True)
        save_cleaner_draft(self.cleaner_records)
        self._update_cleaner_stats()

    def _cleaner_add_table_rows_batch(self, recs: list[ScannedItemRecord]) -> None:
        if not recs:
            return
        if hasattr(self, "cleaner_model") and self.cleaner_model is not None:
            self.cleaner_model.upsert_records(recs)
            self.cleaner_records = list(self.cleaner_model.records)
        else:
            self.cleaner_records.extend(recs)
        self._update_cleaner_stats()

    def _on_cleaner_selection_changed(self) -> None:
        self._update_cleaner_stats()
        self._schedule_cleaner_draft_save()

    def _on_cleaner_table_item_changed(self, item: Any) -> None:
        self._on_cleaner_selection_changed()

    def _schedule_cleaner_draft_save(self) -> None:
        timer = getattr(self, "_cleaner_save_timer", None)
        if timer is not None:
            timer.start(1200)

    def _save_cleaner_draft_async(self) -> None:
        recs_snapshot = list(self.cleaner_records)
        def _bg():
            try:
                save_cleaner_draft(recs_snapshot)
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

    def _update_cleaner_stats(self) -> None:
        if hasattr(self, "cleaner_batch_delete_btn"):
            if getattr(self, "cleaner_is_deleting", False):
                return
            self.cleaner_batch_delete_btn.setText("批量删除")

    def _on_cleaner_batch_delete(self) -> None:
        if self.cleaner_is_deleting:
            self.cleaner_delete_stop_event.set()
            self.cleaner_batch_delete_btn.setText("正在停止...")
            self.cleaner_batch_delete_btn.setEnabled(False)
            self.log("[商品删除] 已请求中止批量删除，正在等待当前请求安全收口...")
            return

        if self.cleaner_is_scanning:
            return

        selected_records = [r for r in self.cleaner_records if r.is_selected_for_delete]
        if not selected_records:
            QMessageBox.information(self, "商品清理", "当前未勾选任何待删除商品！")
            return

        active_sold_selected = [r for r in selected_records if r.has_sales and ("在售" in r.status or r.status == "active")]
        already_del_selected = [r for r in selected_records if is_item_confirmed_deleted(r)]
        total_count = len(selected_records)

        if active_sold_selected:
            msg = (
                f"在选中的 {total_count} 件商品中，包含 {len(active_sold_selected)} 件【依然在售且有销售出单历史】的核心资产商品！\n"
                f"出单商品一旦删除不可恢复，确认要删除这 {total_count} 件商品吗？"
            )
            reply = QMessageBox.warning(
                self, "高危删除确认", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
            )
        else:
            extra_tip = f"\n（其中 {len(already_del_selected)} 件已确认处于删除终态，将直接跳过无需调用 API）" if already_del_selected else ""
            msg = f"确认要彻底删除选中的 {total_count} 件不达标商品吗？{extra_tip}\n\n操作将在美客多官方平台下架并彻底抹除，不可恢复。"
            reply = QMessageBox.question(
                self, "批量删除确认", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
            )

        if reply != QMessageBox.StandardButton.Yes:
            return

        grouped_by_acc: dict[str, list[ScannedItemRecord]] = {}
        for rec in selected_records:
            grouped_by_acc.setdefault(rec.account_id, []).append(rec)

        self.cleaner_is_deleting = True
        self.cleaner_batch_delete_btn.setText("停止删除")
        self.cleaner_batch_delete_btn.setEnabled(True)
        self.cleaner_scan_btn.setEnabled(False)
        self.cleaner_clear_draft_btn.setEnabled(False)
        self.cleaner_delete_stop_event.clear()

        def _worker():
            try:
                total_succ = 0
                total_skipped = 0
                total_fail = 0
                tot_stores = len(grouped_by_acc)
                del_lock = threading.Lock()
                deleted_ids: set[str] = set()
                store_results: list[dict[str, Any]] = []

                def _delete_store_items(pair: tuple[str, list[ScannedItemRecord]]):
                    acc_id, recs = pair
                    if self.cleaner_delete_stop_event.is_set():
                        return
                    store_name = recs[0].store_name or acc_id

                    # 严格判定硬边界：拆分出 100% 已确认彻底删除的商品 与 真正待向官方发请求的商品
                    already_del_recs = [r for r in recs if is_item_confirmed_deleted(r)]
                    pending_recs = [r for r in recs if not is_item_confirmed_deleted(r)]

                    if already_del_recs:
                        with del_lock:
                            nonlocal total_skipped
                            total_skipped += len(already_del_recs)
                            for r in already_del_recs:
                                deleted_ids.add(r.item_id)
                        self.gui_dispatcher.dispatch(lambda s=store_name, n=len(already_del_recs): self.log(
                            f"[商品删除] ⏩ 【{s}】已直接跳过 {n} 件已确认彻底删除的商品（无需重复调用 API）"
                        ))
                        for r in already_del_recs:
                            self.gui_dispatcher.dispatch(lambda i=r.item_id: (
                                self.cleaner_model.update_item_status(i, "已删除", True)
                                if hasattr(self, "cleaner_model") and self.cleaner_model is not None else None
                            ))

                    if not pending_recs:
                        with del_lock:
                            store_results.append({
                                "account_id": acc_id,
                                "store_name": store_name,
                                "success": 0,
                                "skipped": len(already_del_recs),
                                "failed": 0,
                                "total": len(recs),
                            })
                        return

                    target_ids = [r.item_id for r in pending_recs]

                    def _on_item_del(iid: str, succ: bool, err: str):
                        with del_lock:
                            if succ:
                                deleted_ids.add(iid)
                        st_text = "已删除" if succ else f"删除失败: {err}"
                        self.gui_dispatcher.dispatch(lambda i=iid, st=st_text, s=succ: (
                            self.cleaner_model.update_item_status(i, st, s)
                            if hasattr(self, "cleaner_model") and self.cleaner_model is not None else None
                        ))

                    def _on_prog(cur: int, tot: int, step: str):
                        pass

                    def _on_lg(msg: str):
                        self.gui_dispatcher.dispatch(lambda m=msg: self.log(m))

                    res = self.cleaner_engine.execute_batch_delete(
                        account_id=acc_id,
                        item_ids=target_ids,
                        store_name=store_name,
                        on_item_deleted=_on_item_del,
                        on_progress=_on_prog,
                        on_log=_on_lg,
                        stop_event=self.cleaner_delete_stop_event,
                    )
                    with del_lock:
                        nonlocal total_succ, total_fail
                        succ_count = res.get("success_count", 0)
                        fail_count = res.get("failed_count", 0)
                        total_succ += succ_count
                        total_fail += fail_count
                        store_results.append({
                            "account_id": acc_id,
                            "store_name": store_name,
                            "success": succ_count,
                            "skipped": len(already_del_recs),
                            "failed": fail_count,
                            "total": len(recs),
                        })

                with ThreadPoolExecutor(max_workers=max(1, tot_stores)) as del_executor:
                    futures = [
                        del_executor.submit(_delete_store_items, pair)
                        for pair in grouped_by_acc.items()
                    ]
                    wait(futures)

                store_results.sort(key=lambda s: s.get("store_name", ""))

                summary_lines = [
                    "[商品删除] ==================== 批量删除全部执行完成 ====================",
                    "[商品删除] 各店铺分项明细：",
                ]
                for st in store_results:
                    s_name = st["store_name"]
                    s_succ = st["success"]
                    s_skip = st["skipped"]
                    s_fail = st["failed"]
                    summary_lines.append(
                        f"[商品删除]   - 【{s_name}】: 成功下架删除 {s_succ:,} 件，跳过已删除 {s_skip:,} 件，失败 {s_fail:,} 件"
                    )
                summary_lines.append(
                    f"[商品删除] 全局合计: 成功下架 {total_succ:,} 件，直接跳过 {total_skipped:,} 件，删除失败 {total_fail:,} 件"
                )

                # 精确分类统计：绝不可把已经处于删除终态的历史记录误统为“受保护商品”
                all_current_records = list(self.cleaner_records)
                confirmed_deleted_count = sum(
                    1 for r in all_current_records
                    if is_item_confirmed_deleted(r) or r.item_id in deleted_ids
                )
                actual_protected_recs = [
                    r for r in all_current_records
                    if not is_item_confirmed_deleted(r)
                    and r.item_id not in deleted_ids
                    and (
                        (r.has_sales and ("在售" in r.status or r.status == "active"))
                        or r.is_protected_new_item
                    )
                ]
                actual_protected_count = len(actual_protected_recs)
                unselected_pending_count = sum(
                    1 for r in all_current_records
                    if not is_item_confirmed_deleted(r)
                    and r.item_id not in deleted_ids
                    and r not in actual_protected_recs
                )

                if actual_protected_count > 0:
                    summary_lines.append(
                        f"[商品删除] 本地草稿已同步更新，剩余 {actual_protected_count:,} 件受保护商品安全留存（出单/新品保护）"
                    )
                else:
                    summary_lines.append(
                        f"[商品删除] 本地草稿已同步更新，列表中当前包含 {confirmed_deleted_count:,} 件已删除终态记录（可随时点击「清除记录」清空）。"
                    )
                summary_lines.append(
                    "[商品删除] ============================================================"
                )

                self.gui_dispatcher.dispatch(lambda m="\n".join(summary_lines): self.log(m))

                dialog_details = "\n".join(
                    f"• 【{st['store_name']}】: 成功 {st['success']:,} 件，跳过 {st['skipped']:,} 件，失败 {st['failed']:,} 件"
                    for st in store_results
                )
                info_parts = [
                    "批量删除已全部执行完毕！\n",
                    f"【各店铺分项明细】\n{dialog_details}\n",
                    "【全局合计】",
                    f"成功下架删除: {total_succ:,} 件",
                    f"直接跳过(已处于删除终态): {total_skipped:,} 件",
                    f"删除失败: {total_fail:,} 件",
                ]
                if actual_protected_count > 0:
                    info_parts.append(f"\n剩余受保护商品: {actual_protected_count:,} 件 (在售出单/新品保护)")
                if unselected_pending_count > 0:
                    info_parts.append(f"手动未勾选保留: {unselected_pending_count:,} 件")

                info_msg = "\n".join(info_parts)
                self.gui_dispatcher.dispatch(lambda msg=info_msg: QMessageBox.information(
                    self, "删除完成", msg
                ))
            finally:
                def _done():
                    self.cleaner_is_deleting = False
                    self.cleaner_batch_delete_btn.setText("批量删除")
                    self.cleaner_batch_delete_btn.setEnabled(True)
                    self.cleaner_scan_btn.setEnabled(True)
                    self.cleaner_clear_draft_btn.setEnabled(True)

                    # 取消删除后自动即焚逻辑：已删除商品保留在列表中，由用户人为手动点击「清除记录」清理
                    if hasattr(self, "cleaner_model") and self.cleaner_model is not None:
                        for iid in deleted_ids:
                            self.cleaner_model.update_item_status(iid, "已删除", True)
                        self.cleaner_records = list(self.cleaner_model.records)
                    else:
                        for r in self.cleaner_records:
                            if r.item_id in deleted_ids:
                                r.status = "已删除"
                                r.is_selected_for_delete = False
                                sub_st = list(r.sub_status or [])
                                if "deleted" not in sub_st:
                                    sub_st.append("deleted")
                                r.sub_status = sub_st

                    if deleted_ids:
                        mark_cached_items_deleted(list(deleted_ids))
                    save_cleaner_draft(self.cleaner_records)
                    self._update_cleaner_stats()
                    self.log(
                        f"[商品删除] 批量删除完成：成功下架 {total_succ} 件，直接跳过已删除 {total_skipped} 件，失败 {total_fail} 件。"
                        f"所有记录已完整保留，您可随时复制ID或点击「清除记录」清空。"
                    )
                self.gui_dispatcher.dispatch(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_copy_zero_visits_ids(self) -> None:
        records = self.cleaner_records
        if not records:
            records = load_cleaner_draft()
        if not records:
            QMessageBox.information(self, "商品清理", "当前暂无扫描商品，请先点击「扫描待清理商品」！")
            return

        candidates = []
        for r in records:
            visits = getattr(r, "visits", 0) or 0
            sold = getattr(r, "sold_quantity", 0) or 0
            has_sales = getattr(r, "has_sales", False)
            if visits > 0 or sold > 0 or has_sales:
                continue
            sub_status = getattr(r, "sub_status", []) or []
            if "waiting_for_patch" in sub_status or "pending_documentation" in sub_status:
                continue
            status = getattr(r, "status", "") or ""
            if status in ("已暂停", "已关闭", "paused", "closed"):
                continue
            candidates.append(r)

        if not candidates:
            QMessageBox.information(self, "商品清理", "当前暂存商品中未发现符合条件的 0 浏览待删除商品！")
            return

        site_prio = {"MLM": 0, "MLB": 1, "MCO": 2, "MLC": 3, "MLA": 4, "MLU": 5}

        by_title: dict[str, list[Any]] = {}
        distinct_ids: list[str] = []
        for c in candidates:
            t = (getattr(c, "title", "") or "").strip()
            if t and "违规停用商品" not in t:
                by_title.setdefault(t, []).append(c)
            else:
                distinct_ids.append(getattr(c, "item_id", ""))

        representative_ids: list[str] = []
        for t, group in by_title.items():
            group_sorted = sorted(group, key=lambda x: site_prio.get(getattr(x, "site_id", ""), 99))
            representative_ids.append(getattr(group_sorted[0], "item_id", ""))

        all_target_ids = representative_ids + distinct_ids
        all_target_ids_clean = [i for i in all_target_ids if i]
        all_target_ids_sorted = sorted(all_target_ids_clean, key=lambda iid: site_prio.get(iid[:3], 99))
        final_ids = list(dict.fromkeys(all_target_ids_sorted))

        chunk_size = 500
        batches = [final_ids[i : i + chunk_size] for i in range(0, len(final_ids), chunk_size)]

        dlg = CopyZeroVisitIdsDialog(batches, self)
        dlg.exec()

    def startup(self) -> None:
        self.startup_attempt_token += 1
        token = self.startup_attempt_token
        self.initial_bundle_retry_count = 0
        self.initial_bundle_retry_token = token
        self.initial_bundle_retry_timer.stop()
        self.scope_inputs_ready = False
        self.scope_ready = False
        self.scope_refresh_token += 1
        self.startup_final_log_key = ""
        self.startup_status_finalized = False
        self.startup_refresh_start_requested = False
        self._set_busy(True, "正在启动程序组件...")
        self._run_worker(
            lambda: self._ensure_service_started(token),
            lambda started: self._service_ready(started, token),
            lambda error: self._startup_failed(error, token),
            phase="service_connect",
        )

    def _ensure_service_started(self, token: int | None = None) -> bool:
        return self.service.ensure_started(
            progress_callback=lambda progress: self._service_progress_from_worker(progress, token)
        )

    def _service_progress_from_worker(self, progress: dict[str, object], token: int | None = None) -> None:
        # ServiceManager runs in a worker.  Only enqueue a data-only callback;
        # widget access remains on the GUI thread.
        self.gui_dispatcher.dispatch(
            lambda data=dict(progress): self._apply_service_progress(data, token)
        )

    def _apply_service_progress(self, progress: dict[str, object], token: int | None = None) -> None:
        if token is not None and token != self.startup_attempt_token:
            return
        state = str(progress.get("state") or "")
        elapsed_ms = int(progress.get("elapsed_ms") or 0)
        if state in {"reused", "ready"}:
            return
        key = f"{state}|{elapsed_ms // 1000}|{progress.get('cause_code') or ''}"
        if key == self._service_progress_key:
            return
        self._service_progress_key = key
        seconds = max(0, elapsed_ms // 1000)
        if state == "soft_timeout":
            message = f"程序组件仍在启动（已耗时 {seconds} 秒），继续等待本地服务响应..."
        elif state == "failed":
            message = "程序组件启动未完成，正在整理失败原因..."
        else:
            message = f"程序组件启动中（已耗时 {seconds} 秒）..."
        if not self.startup_status_finalized:
            self._show_status(message)
        diagnostic_event(
            "service_start_progress",
            phase="service_connect",
            state=state,
            elapsed_ms=elapsed_ms,
            cause_code=str(progress.get("cause_code") or ""),
            callback_thread=threading.get_ident(),
        )

    def _service_ready(self, started: object, token: int | None = None) -> None:
        if token is not None and token != self.startup_attempt_token:
            return
        self.component_label.setText("程序组件已连接")
        elapsed_ms = self._phase_elapsed_ms("service_connect")
        self.log(
            ("程序组件已连接。" if started else "已连接现有程序组件。")
            + f"（耗时 {elapsed_ms / 1000:.1f} 秒）"
        )
        # Startup readiness is independent from the heavier initial bundle.
        # Poll as soon as the local service is connected so a slow settings,
        # account, history, or scope endpoint cannot hide the backend terminal
        # state or leave the UI showing only the component-connected line.
        if not self.refresh_poll_timer.isActive():
            self.refresh_poll_timer.start()
        if hasattr(self, "auto_reprice_timer") and not self.auto_reprice_timer.isActive():
            self.auto_reprice_timer.start()
        self._request_initial_bundle(token)

    def _request_initial_bundle(self, token: int) -> None:
        if token != self.startup_attempt_token:
            return
        self._run_worker(
            self._load_initial_bundle,
            lambda bundle: self._initial_bundle_ready(bundle, token),
            lambda error: self._initial_bundle_failed(error, token),
            phase="initial_bundle",
            soft_error=True,
        )

    def _initial_bundle_ready(self, bundle: object, token: int) -> None:
        if token != self.startup_attempt_token:
            return
        self.initial_bundle_retry_timer.stop()
        self.initial_bundle_retry_count = 0
        self._apply_initial_bundle(dict(bundle or {}), token)

    def _initial_bundle_failed(self, error: object, token: int) -> None:
        if token != self.startup_attempt_token:
            return
        self.initial_bundle_retry_count += 1
        service_alive = self.service.owns_process or self.service.is_healthy(timeout=3.0)
        if self.initial_bundle_retry_count <= 3 and service_alive:
            self.initial_bundle_retry_token = token
            self.log(
                f"基础数据读取较慢，程序组件仍正常；5 秒后自动重试"
                f"（{self.initial_bundle_retry_count}/3），不会重复启动服务。"
            )
            self.initial_bundle_retry_timer.start(5000)
            return
        self._startup_phase_failed("initial_bundle", error)
        self._startup_failed(product_error(error), token)

    def _retry_initial_bundle(self) -> None:
        self._request_initial_bundle(self.initial_bundle_retry_token)

    def _load_initial_bundle(self) -> dict[str, Any]:
        return {
            "settings": self.api.get("/api/settings").get("settings", {}),
            "accounts": self.api.get("/api/accounts").get("accounts", []),
            "discount": self.api.get("/api/today/global-discount").get("discount", {}),
            "execution": self.api.get("/api/execution/groups/active", timeout=10),
            "submission": self.api.get("/api/execution/submissions/active", timeout=10),
        }

    def _apply_startup_refresh_status(self, refresh: object) -> None:
        data = dict(refresh or {})
        status = str(data.get("status") or "")
        self.startup_refresh_status = status
        self.startup_readiness = dict(data.get("readiness") or {})
        self.startup_account_audits = [dict(row) for row in data.get("account_audits") or [] if isinstance(row, dict)]
        self.startup_ready = status == "ok" and self.startup_readiness.get("ready") is True
        if status == "failed":
            self._log_startup_refresh_progress(data)
            message = startup_refresh_blocked_text(data)
            self.startup_status_finalized = True
            self._append_startup_final_log(status, message)
            self._show_status("缓存同步被阻断，详情见运行日志", message)
            return
        if status in {"ok", "blocked", "degraded"}:
            self._log_startup_refresh_progress(data)
            message = startup_refresh_success_text(data) if self.startup_ready else startup_refresh_blocked_text(data)
            self.startup_status_finalized = True
            self._append_startup_final_log(status, message)
            self._show_status("缓存同步完成" if self.startup_ready else "缓存同步被阻断，详情见运行日志", message)
            if self.startup_ready and not self.scope_ready and self.scope_inputs_ready:
                self.log("启动缓存已就绪，正在自动同步店铺与活动范围...")
                self.refresh_scope()
            if self.startup_ready and not self.today_completion_ready:
                self.log("启动缓存已就绪，正在自动核对今日执行记录...")
                self.refresh_records()
            return
        if status in {"pending", "running"}:
            self.startup_ready = False
            self.startup_final_log_key = ""
            self.startup_status_finalized = False
            if status == "running":
                self.refresh_progress_key = ""
                self.account_progress_key = ""
                self.log("正在刷新数据变动的缓存，刷新完成前暂不能开始执行。")
            self._set_refresh_busy(True)
            if status == "running":
                self._log_startup_refresh_progress(refresh)
            self.refresh_poll_timer.start()

    def _log_startup_refresh_progress(self, refresh: object) -> None:
        data = dict(refresh or {})
        stage_label = str(data.get("stage_label") or "")
        account_progress = dict(data.get("account_progress") or {})
        replay_progress = dict(data.get("replay_progress") or {})
        if account_progress:
            final_line = str(account_progress.get("final_line") or "").strip()
            if final_line and final_line != self.account_progress_key:
                self.account_progress_key = final_line
                self.log(final_line)
            elif not final_line:
                try:
                    completed = int(account_progress.get("completed") or 0)
                    total = int(account_progress.get("total") or 0)
                except (TypeError, ValueError):
                    completed, total = 0, 0
                active_store = str(account_progress.get("active_store") or "").strip()
                account_text = (
                    f"活动缓存：已完成 {completed}/{total} 家"
                    + (f"｜正在处理：{active_store}" if active_store else "")
                )
                if account_text != self.account_progress_key:
                    self.account_progress_key = account_text
                    self.log(account_text)

        replay = replay_progress or dict(data.get("cbt_replay") or {})
        is_replay = bool(replay) or stage_label == "补偿最近48小时商品通知"
        if is_replay:
            status = str(replay.get("status") or "running")
            try:
                attempted = int(replay.get("attempted") or 0)
            except (TypeError, ValueError):
                attempted = 0
            total_value = replay.get("total", replay.get("progress_total"))
            total_known = replay.get("total_known") is True or (
                total_value is not None and str(total_value).strip() != ""
            )
            try:
                total = int(total_value) if total_known else 0
            except (TypeError, ValueError):
                total_known, total = False, 0
            if status == "counting" and not total_known:
                replay_text = "商品通知补偿：正在统计最近48小时待处理数据…"
            elif status in {"ready", "completed"}:
                unique_resources = int(replay.get("remaining_resources") or replay.get("remaining_unique_resources") or 0)
                replay_text = f"CBT父商品通知：{total if total_known else 0}条已完成本地分类"
                if unique_resources:
                    replay_text += f"｜{unique_resources}个无可操作站点子商品已隔离"
                replay_text += "｜需平台补查0个｜可处理剩余0条"
            else:
                wave = int(replay.get("wave") or 0)
                page = int(replay.get("page") or replay.get("pages") or 0)
                if status == "cooldown":
                    prefix = f"商品补偿第{wave}波完成，等待 {int(replay.get('cooldown_ms') or 0)}ms 后继续"
                else:
                    prefix = f"商品补偿第{wave}波第{page}页" if wave or page else "商品通知补偿"
                processed = f"{attempted}/{total}条通知" if total_known else f"{attempted}条通知"
                replay_text = (
                    f"{prefix}：已处理{processed}"
                    f"｜CBT {int(replay.get('unique_cbt') or replay.get('unique_cbt_resource_attempted') or 0)} 个"
                    f"｜匹配子商品 {int(replay.get('route_targets') or replay.get('route_target_matched') or replay.get('route_target_attempted') or 0)} 个"
                    f"｜缓存更新 {int(replay.get('cache_updated') or replay.get('cache_updated_count') or 0)} 个"
                    f"｜GET {int(replay.get('physical_get_used') or 0)}/{int(replay.get('physical_budget') or 0)}"
                )
                if int(replay.get("targeted_fallback_planned") or 0):
                    replay_text += f"｜待定向补查 {int(replay.get('targeted_fallback_planned') or 0)} 个"
                remaining = replay.get("remaining_events", replay.get("remaining_eligible_events"))
                if remaining is not None:
                    replay_text += f"｜剩余 {int(remaining or 0)} 条通知"
            key = f"replay|{replay_text}"
            if key != self.refresh_progress_key:
                self.refresh_progress_key = key
                self.log(replay_text)
            self._show_status("")
            return

        if stage_label:
            message = str(data.get("message") or stage_label).strip()
            key = f"account|{message}|{account_progress.get('completed')}|{account_progress.get('active_store')}"
            if key != self.refresh_progress_key:
                self.refresh_progress_key = key
                self.log(message)
            self._show_status("")

    def _record_startup_poll_diagnostic(
        self,
        branch: str,
        request_token: int,
        *,
        current_token: int | None = None,
        status: str = "",
        percent: int | None = None,
        elapsed_ms: int = 0,
        error: object | None = None,
    ) -> None:
        diagnostic_event(
            "startup_refresh_poll",
            phase="startup_readiness",
            branch=branch,
            request_token=request_token,
            current_token=self.refresh_poll_token if current_token is None else current_token,
            status=status,
            percent=percent if percent is not None else -1,
            elapsed_ms=max(0, int(elapsed_ms)),
            error_kind=type(error).__name__ if error is not None else "",
            cause_code=str(getattr(error, "code", "") or getattr(error, "cause_code", "") or ""),
        )

    def _refresh_poll_response_summary(self, data: object) -> tuple[dict[str, Any], str, int | None]:
        payload = dict(data or {})
        refresh = {
            **dict(payload.get("refresh") or {}),
            "cbt_callback": payload.get("cbt_callback"),
            "daily_item_delta": payload.get("daily_item_delta"),
            "webhook_item_summary": payload.get("webhook_item_summary"),
            "readiness": payload.get("readiness") or dict(payload.get("refresh") or {}).get("readiness") or {},
            "account_progress": payload.get("account_progress") or dict(payload.get("refresh") or {}).get("account_progress"),
            "replay_progress": payload.get("replay_progress") or dict(payload.get("refresh") or {}).get("replay_progress"),
        }
        raw_percent = refresh.get("percent")
        try:
            percent = int(raw_percent) if raw_percent is not None else None
        except (TypeError, ValueError):
            percent = None
        return refresh, str(refresh.get("status") or ""), percent

    def _poll_startup_refresh(self) -> None:
        if self.refresh_poll_busy:
            return
        self.refresh_poll_token += 1
        request_token = self.refresh_poll_token
        self.refresh_poll_inflight_token = request_token
        self.refresh_poll_busy = True
        started_at = time.perf_counter()
        self._record_startup_poll_diagnostic("requested", request_token)
        self._run_worker(
            lambda: self.api.get("/api/startup-refresh/status"),
            lambda data: self._finalize_startup_refresh_poll(request_token, started_at, data=data),
            lambda error: self._finalize_startup_refresh_poll(request_token, started_at, error=error),
            phase="startup_readiness",
            soft_error=True,
        )

    def _finalize_startup_refresh_poll(
        self,
        request_token: int,
        started_at: float,
        *,
        data: object | None = None,
        error: object | None = None,
    ) -> None:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        refresh: dict[str, Any] = {}
        status = ""
        percent: int | None = None
        stale_result = False
        try:
            if error is not None:
                if request_token != self.refresh_poll_token:
                    stale_result = True
                    self._record_startup_poll_diagnostic(
                        "stale",
                        request_token,
                        status=status,
                        percent=percent,
                        elapsed_ms=elapsed_ms,
                        error=error,
                    )
                    return
                self._record_startup_poll_diagnostic(
                    "error",
                    request_token,
                    status=status,
                    percent=percent,
                    elapsed_ms=elapsed_ms,
                    error=error,
                )
                self.startup_ready = False
                self._set_refresh_busy(False)
                self.log("启动缓存状态暂未同步，继续等待本地服务响应：" + product_error(error))
                self._show_status("缓存同步被阻断，详情见运行日志", "启动缓存状态暂未同步，继续等待本地服务响应：" + product_error(error))
                self.refresh_poll_timer.start()
                return

            if isinstance(data, dict):
                for log_entry in data.get("auto_reprice_logs") or []:
                    if log_entry:
                        self.log(str(log_entry))

            refresh, status, percent = self._refresh_poll_response_summary(data)
            if request_token != self.refresh_poll_token:
                stale_result = True
                self._record_startup_poll_diagnostic(
                    "stale",
                    request_token,
                    status=status,
                    percent=percent,
                    elapsed_ms=elapsed_ms,
                )
                return

            self._record_startup_poll_diagnostic(
                "completed",
                request_token,
                status=status,
                percent=percent,
                elapsed_ms=elapsed_ms,
            )
            if status == "running":
                self._set_refresh_busy(True)
                self._log_startup_refresh_progress(refresh)
                self.refresh_poll_timer.start()
                return

            self.refresh_poll_timer.stop()
            self._apply_startup_refresh_status(refresh)
            self._set_refresh_busy(False)
        except Exception as callback_error:
            if request_token == self.refresh_poll_token:
                self._record_startup_poll_diagnostic(
                    "error",
                    request_token,
                    status=status,
                    percent=percent,
                    elapsed_ms=elapsed_ms,
                    error=callback_error,
                )
                self.startup_ready = False
                self._set_refresh_busy(False)
                self.log("加载启动缓存阶段失败：" + product_error(callback_error))
                self._show_status("缓存同步被阻断，详情见运行日志", "加载启动缓存阶段失败：" + product_error(callback_error))
                self.refresh_poll_timer.start()
            else:
                stale_result = True
                self._record_startup_poll_diagnostic(
                    "stale",
                    request_token,
                    status=status,
                    percent=percent,
                    elapsed_ms=elapsed_ms,
                    error=callback_error,
                )
        finally:
            if self.refresh_poll_inflight_token == request_token:
                self.refresh_poll_inflight_token = None
                self.refresh_poll_busy = False
                if stale_result and not self.refresh_poll_timer.isActive():
                    self.refresh_poll_timer.start()
                self._record_startup_poll_diagnostic(
                    "finalized",
                    request_token,
                    status=status,
                    percent=percent,
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                )

    def _apply_startup_refresh_poll(self, token: int, data: object) -> None:
        """Compatibility wrapper for frozen callers and focused tests."""
        started_at = time.perf_counter()
        self._finalize_startup_refresh_poll(token, started_at, data=data)

    def _startup_refresh_poll_failed(self, token: int, error: object) -> None:
        """Compatibility wrapper for frozen callers and focused tests."""
        started_at = time.perf_counter()
        self._finalize_startup_refresh_poll(token, started_at, error=error)

    def _set_refresh_busy(self, busy: bool) -> None:
        self.refresh_busy = busy
        if busy:
            self.startup_ready = False
            self.execute_button.setText("刷新缓存中…")
            self.execute_button.setEnabled(True)
            self.targeted_cancel_button.setEnabled(self._can_open_targeted_cancel())
            return
        self.execute_button.setText("开始执行")
        self.execute_button.setEnabled(self._can_start_submission())
        self.targeted_cancel_button.setEnabled(self._can_open_targeted_cancel())
        for control in (self.mode_combo, self.store_combo, self.site_combo, self.seller_combo, self.official_combo):
            control.setEnabled(True)
        self._update_discount_state()

    def _stop_startup_refresh(self) -> None:
        self.execute_button.setEnabled(False)
        self.execute_button.setText("正在停止缓存补偿…")
        self.log("正在请求停止缓存补偿，已完成的本地缓存更新会保留。")

        def failed(error: object) -> None:
            self.log("停止缓存补偿请求未确认：" + product_error(error))
            if self.refresh_busy:
                self.execute_button.setText("刷新缓存中…")
                self.execute_button.setEnabled(True)

        self._run_worker(
            lambda: self.api.post("/api/startup-refresh/stop", {}),
            lambda _result: self.log("已请求停止缓存补偿，正在等待当前读取安全收口。"),
            failed,
            phase="startup_readiness",
        )

    def _apply_initial_bundle(self, bundle: object, token: int | None = None) -> None:
        if token is not None and token != self.startup_attempt_token:
            return
        data = dict(bundle or {})
        self.settings = dict(data.get("settings") or {})
        first_session_load = not self.auto_shutdown_session_initialized
        if first_session_load:
            self.auto_shutdown_session_initialized = True
            self.settings["autoShutdownAfterExecution"] = False
        blocker = QSignalBlocker(self.auto_shutdown_check)
        self.auto_shutdown_check.setChecked(bool(self.settings.get("autoShutdownAfterExecution")))
        del blocker
        if first_session_load:
            self._run_worker(
                lambda: self.api.post("/api/settings", {"autoShutdownAfterExecution": False}).get("settings", {}),
                lambda settings: self.settings.update(dict(settings or {})),
                lambda error: self.log("自动关机默认关闭状态保存失败：" + product_error(error)),
            )
        aliases = dict(self.settings.get("storeAliases") or {})
        accounts_raw = []
        for row in data.get("accounts") or []:
            r = dict(row)
            acc_id = str(r.get("account_id") or r.get("user_id") or r.get("id") or "")
            if acc_id in aliases and str(aliases[acc_id]).strip():
                alias_name = str(aliases[acc_id]).strip()
                r["store_name"] = alias_name
                r["display_name"] = alias_name
            accounts_raw.append(r)
        self.accounts = [account_from_json(row) for row in accounts_raw]
        self.accounts = [account for account in self.accounts if account.account_id]
        discount = dict(data.get("discount") or {})
        self.global_seller_discount = int(
            discount.get("seller_discount")
            or discount.get("seller")
            or self.settings.get("sellerDefaultDiscount")
            or 28
        )
        self.global_official_discount = int(
            discount.get("official_discount")
            or discount.get("official")
            or self.settings.get("officialDefaultDiscount")
            or 28
        )
        self._apply_global_discounts()
        if hasattr(self, "settings_page"):
            self.settings_page.apply_settings_context(self.settings)
        # Compatibility for explicit/focused callers that pass a refresh
        # snapshot. The real startup path uses the independent poll timer,
        # avoiding a stale snapshot captured before the initial bundle ends.
        if data.get("refresh") is not None:
            refresh = dict(data.get("refresh") or {})
            refresh_context = dict(data.get("refresh_diagnostics") or {})
            refresh.update({
                "cbt_callback": refresh_context.get("cbt_callback"),
                "daily_item_delta": refresh_context.get("daily_item_delta"),
            })
            self._apply_startup_refresh_status(refresh)
        self._fill_store_combo()
        elapsed_ms = self._phase_elapsed_ms("initial_bundle")
        self._set_busy(False, f"基础数据已加载（耗时 {elapsed_ms / 1000:.1f} 秒），正在读取店铺站点...")
        self.log(f"基础数据已加载（耗时 {elapsed_ms / 1000:.1f} 秒），正在读取店铺站点。")
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
        if not self.initial_site_discovery_consumed:
            self.initial_site_discovery_pending = True
            self.initial_site_discovery_consumed = True
        self.scope_inputs_ready = True
        self.refresh_scope()
        self.ready.emit()
        QTimer.singleShot(0, self.refresh_records)

    def _startup_failed(self, message: str, token: int | None = None) -> None:
        if token is not None and token != self.startup_attempt_token:
            return
        self._set_busy(False, "工作台未准备好")
        self.component_label.setText("程序组件未连接")
        self.log("工作台准备失败：" + product_error(message))
        QMessageBox.warning(self, "美客多活动管家", product_error(message))

    def _fill_store_combo(self) -> None:
        blocker = QSignalBlocker(self.store_combo)
        self.store_combo.clear()
        aliases = dict(self.settings.get("storeAliases") or {})
        if aliases:
            self.accounts = [
                dataclasses.replace(a, store_name=str(aliases[a.account_id]).strip())
                if a.account_id in aliases and str(aliases[a.account_id]).strip()
                else a
                for a in self.accounts
            ]
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
        if hasattr(self, "cleaner_account_combo"):
            cleaner_blocker = QSignalBlocker(self.cleaner_account_combo)
            self.cleaner_account_combo.clear()
            self.cleaner_account_combo.addItem(f"全部店铺（合并分析所有店铺 - {len(self.accounts)}个）", "all")
            for account in self.accounts:
                self.cleaner_account_combo.addItem(f"{account.store_name} ({account.account_id})", account.account_id)
            self.cleaner_account_combo.setCurrentIndex(0)
            del cleaner_blocker
            self._refresh_cleaner_sites()

    def _refresh_cleaner_sites(self) -> None:
        if not hasattr(self, "cleaner_site_combo") or not hasattr(self, "cleaner_account_combo"):
            return
        selected_account = str(self.cleaner_account_combo.currentData() or "all")
        seen_sites: set[str] = set()

        if selected_account == "all":
            target_account_ids = {a.account_id for a in self.accounts}
        else:
            target_account_ids = {selected_account}

        for row in getattr(self, "operating_rows_cache", []):
            acc_id = str(row.get("account_id") or "")
            if acc_id in target_account_ids:
                sid = str(row.get("site_id") or "").upper()
                if sid:
                    seen_sites.add(sid)

        if hasattr(self, "cleaner_engine") and hasattr(self.cleaner_engine.client, "auth"):
            for acc_id in target_account_ids:
                try:
                    for s in self.cleaner_engine.client.auth.list_sites(acc_id):
                        sid = str(s.get("site_id") or "").upper()
                        if sid:
                            seen_sites.add(sid)
                except Exception:
                    pass

        if not seen_sites and getattr(self, "accounts", None):
            for acc in self.accounts:
                if acc.account_id in target_account_ids and acc.site_id:
                    seen_sites.add(acc.site_id.upper())

        # 联动过滤经营站点配置
        operating_cfg = getattr(self, "settings", {}).get("operatingSites") or {}
        allowed_sites_for_targets: set[str] = set()
        has_cfg = False
        for aid in target_account_ids:
            if aid in operating_cfg and isinstance(operating_cfg[aid], list):
                has_cfg = True
                for s in operating_cfg[aid]:
                    if str(s).strip():
                        allowed_sites_for_targets.add(str(s).strip().upper())
        if has_cfg:
            seen_sites = seen_sites.intersection(allowed_sites_for_targets)

        site_tuples = [(f"{site_name(sid)} ({sid})", sid) for sid in sorted(seen_sites)]
        self.cleaner_site_combo.set_items(site_tuples)

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

    def refresh_scope(self, *, is_retry: bool = False) -> None:
        if not is_retry:
            self.scope_retry_count = 0
        if not self.scope_inputs_ready:
            return
        account_ids = self.selected_account_ids()
        if not account_ids:
            self.scope_ready = False
            self._set_busy(False, "当前范围没有可用店铺")
            return
        self.scope_ready = False
        self._set_busy(True, "正在读取店铺站点...")
        self.scope_refresh_token += 1
        token = self.scope_refresh_token
        startup_token = self.startup_attempt_token
        selected_site = self.selected_site_id()
        discover_missing_sites = self.initial_site_discovery_pending
        self.initial_site_discovery_pending = False
        self._run_worker(
            lambda: self._load_scope_bundle(
                account_ids,
                selected_site,
                discover_missing_sites=discover_missing_sites,
                startup_token=startup_token,
            ),
            lambda result: self._apply_scope_bundle(token, result, startup_token),
            lambda error: self._scope_load_failed(token, error, startup_token),
            phase="scope_bundle",
        )

    def _scope_load_failed(self, token: int, error: object, startup_token: int | None = None) -> None:
        if token != self.scope_refresh_token or (startup_token is not None and startup_token != self.startup_attempt_token):
            return
        self.scope_ready = False
        self._set_busy(False, "店铺站点未准备好")
        self.log("活动范围读取失败：" + product_error(error))
        if self.startup_ready and self.scope_inputs_ready and self.scope_retry_count < 2:
            self.scope_retry_count += 1
            self.log(f"启动缓存已就绪，将在 1 秒后自动重试读取活动范围（{self.scope_retry_count}/2）...")
            QTimer.singleShot(1000, lambda: self.refresh_scope(is_retry=True))

    def _load_scope_bundle(
        self,
        account_ids: list[str],
        selected_site: str,
        *,
        discover_missing_sites: bool = False,
        startup_token: int | None = None,
    ) -> dict[str, Any]:
        if startup_token is not None and startup_token != self.startup_attempt_token:
            return {"stale": True, "selected_site": selected_site}
        sites: list[dict[str, Any]] = []
        promotions: list[dict[str, Any]] = []

        operating_cfg = self.settings.get("operatingSites") or {}
        for account_id in account_ids:
            if startup_token is not None and startup_token != self.startup_attempt_token:
                return {"stale": True, "selected_site": selected_site}
            account_sites = list(
                self.api.get(f"/api/accounts/{account_id}/sites").get("sites", [])
            )
            if (
                discover_missing_sites
                and not account_sites
                and account_id not in self.site_discovery_attempted
            ):
                self.site_discovery_attempted.add(account_id)
                if startup_token is not None and startup_token != self.startup_attempt_token:
                    return {"stale": True, "selected_site": selected_site}
                account_sites = list(
                    self.api.get(
                        f"/api/accounts/{account_id}/sites?refresh=1",
                        timeout=60,
                    ).get("sites", [])
                )

            # 经营站点联动过滤
            allowed_sites = None
            if account_id in operating_cfg and isinstance(operating_cfg[account_id], list):
                allowed_sites = {str(x).strip().upper() for x in operating_cfg[account_id] if str(x).strip()}

            for row in account_sites:
                s_id = str(row.get("site_id") or "").upper()
                if allowed_sites is not None and s_id not in allowed_sites:
                    continue
                sites.append({**row, "account_id": account_id})

            if allowed_sites is not None and selected_site and selected_site.upper() not in allowed_sites:
                continue

            path = ApiClient.query(f"/api/accounts/{account_id}/promotions", siteId=selected_site)
            if startup_token is not None and startup_token != self.startup_attempt_token:
                return {"stale": True, "selected_site": selected_site}
            for row in self.api.get(path).get("promotions", []):
                p_site = str(row.get("site_id") or "").upper()
                if allowed_sites is not None and p_site and p_site not in allowed_sites:
                    continue
                promotions.append({**row, "account_id": account_id})
        return {"sites": sites, "promotions": promotions, "selected_site": selected_site}

    def _apply_scope_bundle(self, token: int, result: object, startup_token: int | None = None) -> None:
        if token != self.scope_refresh_token or (startup_token is not None and startup_token != self.startup_attempt_token):
            return
        data = dict(result or {})
        if data.get("stale"):
            return
        self.scope_retry_count = 0
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
        self._refresh_cleaner_sites()
        self.scope_ready = True
        elapsed_ms = self._phase_elapsed_ms("scope_bundle")
        self._set_busy(False, f"工作台已就绪（范围耗时 {elapsed_ms / 1000:.1f} 秒）")
        self.log(f"店铺站点与活动范围已加载（耗时 {elapsed_ms / 1000:.1f} 秒）。")
        self._request_startup_refresh_after_scope()
        self._refresh_auto_decision()

    def _request_startup_refresh_after_scope(self) -> None:
        if self.startup_refresh_start_requested or self.startup_status_finalized:
            return
        self.startup_refresh_start_requested = True
        self._set_refresh_busy(True)

        def failed(error: object) -> None:
            self.startup_refresh_start_requested = False
            self.log("启动缓存触发暂未确认，后台定时器会继续兜底：" + product_error(error))

        self._run_worker(
            lambda: self.api.post("/api/startup-refresh/start", {}, timeout=10),
            lambda _result: (self.refresh_poll_timer.start(), self._poll_startup_refresh()),
            failed,
            phase="startup_readiness",
            soft_error=True,
        )

    def _fill_activity_combos(self) -> None:
        for combo, prefix in ((self.seller_combo, "自建"), (self.official_combo, "官方")):
            blocker = QSignalBlocker(combo)
            combo.clear()
            combo.addItem(f"全部{prefix}活动", "")
            combo.addItem(f"不处理{prefix}活动", EXCLUDE_ACTIVITY)
            bucket = "seller" if combo is self.seller_combo else "official"
            choices: dict[str, str] = {}
            for promotion in self.promotions:
                p_type = str(promotion.get("promotion_type") or promotion.get("type") or "")
                if promotion_bucket(p_type) != bucket:
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
        if not self.scope_inputs_ready:
            return
        if self.sender() is self.store_combo:
            self.refresh_scope()
        else:
            self._refresh_auto_decision()

    def _site_changed(self, _index: int = -1) -> None:
        if not self.scope_inputs_ready:
            return
        self.refresh_scope()

    def _apply_global_discounts(self) -> None:
        blocker_s = QSignalBlocker(self.seller_discount)
        blocker_o = QSignalBlocker(self.official_discount)
        self.seller_discount.setValue(self.global_seller_discount)
        self.official_discount.setValue(self.global_official_discount)
        del blocker_s, blocker_o

    def _on_discount_spin_changed(self) -> None:
        if self.mode_combo.currentText() == "自动判断":
            self.global_seller_discount = int(self.seller_discount.value())
            self.global_official_discount = int(self.official_discount.value())
            self._refresh_auto_decision()
        else:
            self._sync_submit_availability()
        self._run_worker(
            lambda: self.api.post("/api/settings", {
                "sellerDefaultDiscount": int(self.seller_discount.value()),
                "officialDefaultDiscount": int(self.official_discount.value()),
            }),
            lambda _res: None,
            lambda _err: None,
            phase="settings_save",
        )

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
                self.today_label.setText(completed_text + f" 当前选择{action_label(mode_action)}。")
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
        raw_decision = result.get("decision")
        if isinstance(raw_decision, str):
            decision = {"action": raw_decision}
        elif isinstance(raw_decision, dict):
            decision = raw_decision
        else:
            decision = {}
        if str(decision.get("action") or "") == "configuration_required":
            raise RuntimeError(str(decision.get("reason") or "请先在设置中填写自动周期最高折扣。"))
        return str(decision.get("action") or "enroll")

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
            phase="records_bundle",
        )

    def _records_loaded(self, payload: dict[str, list[dict[str, Any]]], token: int) -> None:
        if token != self.records_request_token:
            return
        self._records_retrying = False
        for view, rows in payload.items():
            if view in RECORD_VIEW_LIMITS:
                self.records_cache[view] = list(rows or [])
        current = self.records_cache.get(self.records_view)
        if current is not None:
            self._apply_current_records(current)
        elapsed_ms = self._phase_elapsed_ms("records_bundle")
        if not self.startup_status_finalized:
            self._show_status(
                "正在核对今日执行记录",
                f"今日执行记录已返回（耗时 {elapsed_ms / 1000:.1f} 秒），正在核对今日状态...",
            )
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
            if self.startup_ready and not getattr(self, "_records_retrying", False):
                self._records_retrying = True
                def _retry_records() -> None:
                    self._records_retrying = False
                    if not self.today_completion_ready:
                        self.refresh_records()
                QTimer.singleShot(5000, _retry_records)

    def _request_today_execution_groups(self, records: list[dict[str, Any]]) -> None:
        today = _coerce_business_date(None)
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
        self._append_background_group_summary(self.today_execution_groups)
        if any(self._group_needs_background_poll(group) for group in self.today_execution_groups):
            if not self.background_group_timer.isActive():
                self.background_group_timer.start()
        else:
            self.background_group_timer.stop()
        self.today_completion_ready = True
        self.current_today_completion = self._completion_for_current_scope()
        self._refresh_auto_decision()
        self._sync_submit_availability()

    @staticmethod
    def _group_needs_background_poll(group: dict[str, Any]) -> bool:
        if str(group.get("status") or "").lower() not in {"completed", "partial_or_failed", "failed", "cancelled", "interrupted"}:
            return False
        result = dict(group.get("result") or {})
        return result.get("accounting_complete") is not True or int(result.get("pending") or 0) > 0

    def _poll_background_execution_groups(self) -> None:
        if self.background_group_poll_busy:
            return
        group_ids = [str(group.get("id") or "") for group in self.today_execution_groups if str(group.get("id") or "")]
        if not group_ids:
            self.background_group_timer.stop()
            return
        self.background_group_poll_busy = True

        def load() -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for group_id in group_ids:
                payload = self.api.get(f"/api/execution/groups/{group_id}?compact=1", timeout=10)
                group = dict(payload.get("group") or {})
                if group:
                    rows.append(group)
            return rows

        def success(groups: object) -> None:
            self.background_group_poll_busy = False
            rows = [dict(group) for group in list(groups or [])]
            self.today_execution_groups = rows
            self._append_background_group_summary(rows)
            self.current_today_completion = self._completion_for_current_scope()
            self._refresh_auto_decision()
            self._sync_submit_availability()
            if not any(self._group_needs_background_poll(group) for group in rows):
                self.background_group_timer.stop()

        def failure(_error: object) -> None:
            self.background_group_poll_busy = False

        self._run_worker(load, success, failure)

    def _poll_auto_reprice(self) -> None:
        if getattr(self, "_auto_reprice_poll_busy", False) or self._closing:
            return
        self._auto_reprice_poll_busy = True

        def load() -> dict[str, Any]:
            return self.api.get("/api/auto-reprice/status", timeout=5)

        def success(payload: object) -> None:
            self._auto_reprice_poll_busy = False
            if isinstance(payload, dict):
                for log_entry in payload.get("logs") or []:
                    if log_entry:
                        self.log(str(log_entry))

        def failure(_error: object) -> None:
            self._auto_reprice_poll_busy = False

        self._run_worker(load, success, failure, phase="auto_reprice_poll", soft_error=True)

    def _append_background_group_summary(self, groups: list[dict[str, Any]]) -> None:
        terminal = [
            dict(group)
            for group in groups
            if str(group.get("status") or "").lower() in {"completed", "partial_or_failed", "failed", "cancelled", "interrupted"}
        ]
        if not terminal:
            return
        group = max(terminal, key=lambda row: str(row.get("updated_at") or row.get("finished_at") or ""))
        result = dict(group.get("result") or {})
        success = int(result.get("success") or 0)
        failed = int(result.get("failed") or 0)
        skipped = int(result.get("skipped") or 0)
        pending = int(result.get("pending") or result.get("pending_verification_count") or 0)
        if not any((success, failed, skipped, pending)):
            return
        if str(group.get("status") or "").lower() == "completed" and pending == 0:
            return
        action = str(result.get("action") or group.get("action") or "")
        key = f"{group.get('id')}|{success}|{failed}|{skipped}|{pending}"
        if key == self.background_group_log_key:
            return
        self.background_group_log_key = key
        suffix = "；当前结果尚未全部确认。" if pending > 0 or result.get("accounting_complete") is not True else "。"
        self.log(
            f"后台{action_label(action)}结果已更新：成功 {success}，失败 {failed}，跳过 {skipped}，"
            f"待平台确认 {pending}{suffix}"
        )

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
        latest_delta = next(
            (dict(task.get("daily_item_delta") or {}) for task in self.records if isinstance(task.get("daily_item_delta"), dict)),
            {},
        )
        delta_text, delta_tooltip = daily_item_delta_text(latest_delta)
        self.records_delta_label.setText(delta_text)
        self.records_delta_label.setToolTip(delta_tooltip)
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
            created_at = str(task.get("created_at") or task.get("updated_at") or "")
            time_text, time_tooltip = record_timestamp_text(created_at)
            values = [
                time_text,
                action_label(str(task.get("action") or "")),
                record_discount_text(task),
                record_activity_text(task),
                "已中止" if str(task.get("status") or "") == "cancelled" else ("提交" if str(task.get("mode") or "real") == "real" else "预览"),
                record_scope_text(unique_items, relation_count),
                record_result_text(task),
                f"商品 {failed} / 活动 {count_or_marker(activity_failures)}",
                business_reason_text(task.get("short_failure_reason") or task.get("failure_reason") or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    time_tooltip if column == 0
                    else record_scope_tooltip(unique_items, relation_count) if column == 4
                    else value
                )
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
            s_id = str(promotion.get("site_id") or "").strip()
            if not s_id:
                p_id = str(promotion.get("id") or promotion.get("promotion_id") or "").upper()
                for code in ("MLB", "MLM", "MLC", "MCO", "MLA", "MLU", "MPE", "MEC"):
                    if code in p_id:
                        s_id = code
                        break
            p_type = str(promotion.get("promotion_type") or promotion.get("type") or "").strip()
            values = [
                account.store_name if account else "当前店铺",
                site_name(s_id),
                promotion_type_text(p_type),
                promotion_display_name(promotion),
                status_text(str(promotion.get("status") or "")),
                str(promotion.get("total") or promotion.get("items_total") or 0),
            ]
            for column, value in enumerate(values):
                self.activity_table.setItem(row, column, QTableWidgetItem(value))

    def _update_nav_selection(self, active_button: QPushButton | None) -> None:
        buttons = [
            *getattr(self, "nav_buttons", []),
            getattr(self, "settings_button", None),
            getattr(self, "query_button", None),
            getattr(self, "targeted_cancel_button", None),
        ]
        for btn in buttons:
            if btn is not None:
                is_active = (btn is active_button)
                btn.setChecked(is_active)
                btn.setProperty("checked", "true" if is_active else "false")
                style = btn.style()
                if style:
                    style.unpolish(btn)
                    style.polish(btn)

    def _show_page(self, index: int) -> None:
        if hasattr(self, "view_stack"):
            self.view_stack.setCurrentWidget(self.pages)
        self.pages.setCurrentIndex(index)
        active_btn = self.nav_buttons[index] if index < len(self.nav_buttons) else None
        self._update_nav_selection(active_btn)

    def _show_settings_page(self, initial_tab: str = "") -> None:
        if hasattr(self, "view_stack") and hasattr(self, "settings_page"):
            if self.view_stack.currentWidget() == self.settings_page and not initial_tab:
                last_idx = self.pages.currentIndex() if hasattr(self, "pages") else 0
                self._show_page(last_idx)
                return
            self._update_nav_selection(getattr(self, "settings_button", None))
            if initial_tab:
                self.settings_page.switch_tab(initial_tab)
            self.view_stack.setCurrentWidget(self.settings_page)
            self._run_worker(
                self._load_settings_context,
                lambda context: self._apply_settings_context(self.settings_page, context),
                lambda error: self.log("设置后台刷新未完成：" + product_error(error)),
            )

    def _check_update_async(self, manual: bool = False) -> None:
        """后台异步检测 GitHub 最新 Release，静默不阻塞主界面。"""
        curr_ver = product_version()

        def _worker() -> None:
            info = check_github_latest_release(curr_ver)
            QTimer.singleShot(0, lambda: self._handle_update_check_result(info, manual))

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_update_check_result(self, info: ReleaseInfo | None, manual: bool = False) -> None:
        """主线程处理更新检测结果。"""
        if info and info.is_newer:
            self._latest_release_info = info
            self.update_notice_btn.setText(f"🚀 发现新版 v{info.version}")
            self.update_notice_btn.setToolTip(f"发现新版本 v{info.version}，点击查看详情并一键升级")
            self.update_notice_btn.setVisible(True)
            if manual:
                self._open_update_dialog(info)
        elif manual:
            curr_ver = product_version()
            if info:
                QMessageBox.information(
                    self,
                    "检查更新",
                    f"当前安装版本 (v{curr_ver}) 已经是最新版本，暂无可用更新。",
                )
            else:
                QMessageBox.warning(
                    self,
                    "检查更新",
                    f"当前安装版本为 v{curr_ver}。\n检查更新服务器连接失败，请稍后重试或前往 GitHub 查看最新版本。",
                )

    def _on_update_notice_clicked(self) -> None:
        """点击标题栏提示药丸触发。"""
        if self._latest_release_info:
            self._open_update_dialog(self._latest_release_info)
        else:
            self._check_update_async(manual=True)

    def _open_update_dialog(self, info: ReleaseInfo) -> None:
        """弹出更新模态对话框。"""
        dlg = UpdateDialog(info, product_version(), parent=self)
        dlg.exec()

    def _show_query_page(self) -> None:
        if hasattr(self, "view_stack") and hasattr(self, "query_page"):
            if self.view_stack.currentWidget() == self.query_page:
                last_idx = self.pages.currentIndex() if hasattr(self, "pages") else 0
                self._show_page(last_idx)
                return
            self._update_nav_selection(getattr(self, "query_button", None))
            self.view_stack.setCurrentWidget(self.query_page)
            self.query_page.item_input.setFocus()

    def _show_targeted_cancel_page(self) -> None:
        if hasattr(self, "view_stack") and hasattr(self, "targeted_cancel_page"):
            if self.view_stack.currentWidget() == self.targeted_cancel_page:
                last_idx = self.pages.currentIndex() if hasattr(self, "pages") else 0
                self._show_page(last_idx)
                return
            self._update_nav_selection(getattr(self, "targeted_cancel_button", None))
            self._sync_targeted_cancel_page_scope()
            self.view_stack.setCurrentWidget(self.targeted_cancel_page)

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
        items_path = "/api/tasks/items?task_ids=" + ",".join(str(value) for value in ids) + "&limit=200"

        def load() -> dict[str, Any]:
            return {
                "details": list(self.api.get(path).get("details", [])),
                "items": dict(self.api.get(items_path).get("items", {})),
            }

        self._run_worker(
            load,
            lambda payload: DetailsDialog(
                "批次详情",
                task_detail_text(task, list(payload["details"] or []), dict(payload["items"] or {})),
                self,
            ).exec(),
            lambda error: QMessageBox.warning(self, "批次详情", product_error(error)),
        )

    def _show_selected_summary(self) -> None:
        row = self.records_table.currentRow()
        if row < 0 or not self.records_table.item(row, 0):
            return
        task = dict(self.records_table.item(row, 0).data(Qt.ItemDataRole.UserRole) or {})
        reason = str(task.get("failure_reason") or task.get("short_failure_reason") or "")
        if reason:
            self.log("所选批次失败原因：" + business_reason_text(reason))

    def _reload_live_promotions(self) -> None:
        account_ids = self.selected_account_ids()
        self._set_busy(True, "正在读取活动...")
        self.log("[活动管理] 正在向美客多重新读取在线活动列表...")

        def reload() -> list[tuple[str, int]]:
            result: list[tuple[str, int]] = []
            for account_id in account_ids:
                payload = self.api.post(f"/api/accounts/{account_id}/promotions/fetch", {}, timeout=120)
                result.append((account_id, int(payload.get("total") or 0)))
            return result

        def done(rows: object) -> None:
            for account_id, total in list(rows or []):
                self.log(f"[活动管理] {self._store_for_account(account_id)}：活动读取完成，共 {total} 个。")
            self._set_busy(False, "活动已刷新")
            self.refresh_scope()

        self._run_worker(reload, done, lambda error: self._operation_error("活动读取", error))

    def _on_full_get_clicked(self) -> None:
        if self.ui_busy or self.refresh_busy:
            QMessageBox.information(self, "全量GET数据", "当前有刷新任务正在进行，请稍候。")
            return
        if self.running_group or self.pending_group_payload or self.preparing_submission:
            QMessageBox.information(self, "全量GET数据", "当前有执行任务正在运行，请等待任务完成后再拉取数据。")
            return
        account_ids = self.selected_account_ids()
        if not account_ids:
            QMessageBox.information(self, "全量GET数据", "未找到可用店铺授权。")
            return
        store_names = "、".join(self._store_for_account(acc) for acc in account_ids)
        confirm = QMessageBox.question(
            self,
            "全量GET数据确认",
            f"将对【{store_names}】执行全量 GET 数据：\n\n"
            "• 从美客多官方接口读取最新活动列表\n"
            "• 逐活动读取已报名商品及可报名商品明细\n"
            "• 刷新本地全部活动与商品数据库缓存\n\n"
            "（全程仅做 GET 查询，不会提交任何修改或写操作）\n\n"
            "是否立即开始？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._start_full_get_data(account_ids)

    def _start_full_get_data(self, account_ids: list[str]) -> None:
        self._set_busy(True, "正在全量 GET 美客多数据...")
        self.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self.log("【全量GET】正在向美客多发起全量数据读取任务...")

        def fetch_loop() -> dict[str, Any]:
            start_res = self.api.post("/api/full-data/start", {"accountIds": account_ids}, timeout=15)
            job_id = str(start_res.get("job_id") or "")
            if not job_id:
                raise RuntimeError("未能启动全量读取任务。")
            if start_res.get("already_running"):
                self.gui_dispatcher.dispatch(lambda: self.log("【全量GET】检测到后台已有全量读取任务正在进行，正在接入实时进度..."))

            cursor = 0
            while True:
                time.sleep(1.0)
                try:
                    res = self.api.get(f"/api/full-data/jobs/{job_id}?after={cursor}", timeout=10)
                except Exception:
                    continue
                job = dict(res.get("job") or {})
                new_logs = list(job.get("logs") or [])
                if new_logs:
                    cursor = int(res.get("total_logs") or (cursor + len(new_logs)))
                    for line in new_logs:
                        self.gui_dispatcher.dispatch(lambda msg=line: self.log(msg))

                status = str(job.get("status") or "")
                if status == "completed":
                    return job
                elif status == "failed":
                    raise RuntimeError(str(job.get("error") or "全量读取任务异常中断。"))

        def done(job_data: object) -> None:
            self._set_busy(False, "全量 GET 数据完成")
            data = dict(job_data or {})
            progress = dict(data.get("progress") or {})
            tot_promos = progress.get("total_promotions", 0)
            tot_started = progress.get("total_started", 0)
            tot_cand = progress.get("total_candidate", 0)
            self.log(f"【全量GET】完成！共刷新 {tot_promos} 个活动，已报商品 {tot_started} 件，候选商品 {tot_cand} 件。正在刷新界面...")
            self.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            self.refresh_scope()
            self.refresh_records()

        def error(err: object) -> None:
            self._set_busy(False, "全量 GET 失败")
            self._operation_error("全量GET数据", err)

        self._run_worker(fetch_loop, done, error, phase="full_get_data")

    def _on_execute_clicked(self) -> None:
        if self.refresh_busy:
            self._stop_startup_refresh()
            return
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
        scope_names = "、".join(self._store_for_account(account_id) for account_id in account_ids) or "无"
        self.log(f"执行范围确认：店铺={scope_names}，站点={site_text}，动作={requested_action}，折扣=自建{seller_discount}%/官方{official_discount}%。")
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
        self.prepare_stage_seen = ""
        if state == "prepared":
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self.log("准备完成（100%）。")
            self._submission_prepared({"prepare": prepare})
            return
        if state == "reconfirm_required":
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self.log("执行动作或活动结构已变化，本次未执行，请重新开始核对范围。")
            self._operation_error("核对执行范围", "执行动作或活动结构已变化，本次未执行，请重新开始核对范围。")
            return
        if state in {"failed", "expired"}:
            self.pending_prepare_payload = None
            self._set_prepare_busy(False)
            self._operation_error("准备执行", prepare_failure_message(prepare))
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

    PREPARE_STAGE_LABELS = {
        "queued": "排队等待",
        "accounts": "核对店铺范围",
        "catalog": "刷新店铺活动",
        "started": "核对已报名商品",
        "items": "核对可报名商品",
        "seller": "核对自建活动",
        "final_catalog": "提交前复核",
        "final_targeted": "提交前复核",
        "targeted_lookup": "定位指定商品",
        "targeted_verify": "核对命中活动",
        "finalizing": "整理最终范围",
    }

    def _log_prepare_progress(self, prepare: dict[str, Any]) -> None:
        progress = dict(prepare.get("progress") or {})
        message = str(progress.get("message") or "正在核对执行范围")
        stage = str(progress.get("stage") or "")
        stage_label = self.PREPARE_STAGE_LABELS.get(stage, "")
        current = " / ".join(str(progress.get(key) or "") for key in ("current_store", "current_site", "current_activity") if progress.get(key))
        percent = max(0, min(100, int(progress.get("percent") or 0)))
        scheduler_text = self._prepare_scheduler_text(progress)
        read_match = re.match(r"正在读取(已报名|可报名)商品（活动 \d+/(\d+)）", message)
        if read_match:
            read_key = f"read-items:{read_match.group(1)}"
            if read_key == self.prepare_read_key:
                return
            self.prepare_read_key = read_key
            message = f"正在读取{read_match.group(1)}商品（共 {read_match.group(2)} 个活动）"
            current = ""
            percent = 0
            scheduler_text = ""
        # Stage anchor: emit an explicit "stage started" line once per stage so
        # the progress is anchored (0% -> stage transitions) instead of jumping
        # straight to a mid-stage percent.
        if stage_label and stage != self.prepare_stage_seen:
            self.prepare_stage_seen = stage
            self.prepare_progress_key = ""
            self.log(f"[{stage_label}] 开始。")
        key = f"{stage}|{message}|{current}|{percent}|{scheduler_text}"
        if key == self.prepare_progress_key:
            return
        self.prepare_progress_key = key
        suffix = f"：{current}" if current else ""
        metrics = f" {scheduler_text}" if scheduler_text else ""
        prefix = f"[{stage_label}] " if stage_label else ""
        percent_text = f"（{percent}%）" if percent > 0 else ""
        self.log(f"{prefix}{message}{suffix}{percent_text}。{metrics}")

    @staticmethod
    def _prepare_scheduler_text(progress: dict[str, Any]) -> str:
        scheduler = dict(progress.get("read_scheduler") or {})
        dynamic_limit = max(0, int(scheduler.get("dynamic_limit") or 0))
        max_limit = max(0, int(scheduler.get("max_limit") or 0))
        if not dynamic_limit and not max_limit:
            return ""
        inflight = max(0, int(scheduler.get("inflight") or 0))
        peak = max(0, int(scheduler.get("peak") or 0))
        detail = max(0, int(scheduler.get("detail_inflight") or 0))
        detail_limit = max(0, int(scheduler.get("detail_limit") or 0))
        fallback = max(0, int(scheduler.get("fallback_active") or 0))
        fallback_limit = max(0, int(scheduler.get("fallback_per_account") or 0))
        queued = max(0, int(scheduler.get("queued") or 0))
        limited = max(0, int(scheduler.get("rate_limit_count") or 0))
        network_errors = max(0, int(scheduler.get("network_error_count") or 0))
        service_errors = max(0, int(scheduler.get("service_error_count") or 0))
        timeout_errors = max(0, int(scheduler.get("timeout_error_count") or 0))
        failures = max(0, int(scheduler.get("failure_count") or 0))
        retries = max(0, int(scheduler.get("retry_count") or 0))
        local_concurrency = max(1, int(scheduler.get("local_work_concurrency") or 1))
        local_queries = max(0, int(scheduler.get("local_db_batch_queries") or 0))
        cooldown_seconds = (max(0, int(scheduler.get("cooldown_ms") or 0)) + 999) // 1000
        account_parts = []
        for row in list(scheduler.get("per_account") or []):
            if not isinstance(row, dict):
                continue
            store_name = str(row.get("store_name") or "").strip()
            account_inflight = max(0, int(row.get("inflight") or 0))
            if store_name and account_inflight:
                account_parts.append(f"{store_name} {account_inflight}")
        account_text = f"；店铺并发 {'、'.join(account_parts)}" if account_parts else ""
        return (
            f"本地整理并行 {local_concurrency}（批量查询 {local_queries}）；"
            f"平台读取并发 {inflight}/{dynamic_limit}（峰值 {peak}，上限 {max_limit}）；"
            f"详情 {detail}/{detail_limit}，库存兜底 {fallback}（每店上限 {fallback_limit}），排队 {queued}；"
            f"限流 {limited} 次，网络异常 {network_errors} 次，服务异常 {service_errors} 次，"
            f"超时 {timeout_errors} 次，最终失败 {failures} 次，重试 {retries} 次，"
            f"冷却 {cooldown_seconds} 秒{account_text}。"
        )

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
        targeted = dict(prepare.get("targeted_item_action") or prepare.get("targeted_cancel") or {})
        if targeted.get("enabled") is True:
            action = str(targeted.get("action") or prepare.get("resolved_action") or "cancel")
            action_text = "取消" if action == "cancel" else "报名"
            unmatched_ids = [str(value) for value in targeted.get("unmatched_item_ids") or [] if str(value)]
            unmatched_count = int(targeted.get("unmatched_item_count") or len(unmatched_ids))
            deferred_activity_count = int(targeted.get("deferred_activity_count") or 0)
            deferred_route_count = int(targeted.get("deferred_route_relation_count") or 0)
            account_lines: list[str] = []
            for row in list(targeted.get("accounts") or []):
                account_lines.append(
                    f"{row.get('store_name') or '店铺'}：商品 {int(row.get('unique_item_count') or 0)} 个，"
                    f"活动 {int(row.get('activity_count') or 0)} 个，关系 {int(row.get('relation_count') or 0)} 条"
                )
            self.log(
                f"指定商品{action_text}核对完成：匹配商品 {int(targeted.get('matched_item_count') or 0)} 个，"
                f"活动 {int(targeted.get('activity_count') or 0)} 个，"
                f"商品×活动关系 {int(targeted.get('relation_count') or 0)} 条；"
                f"未找到 {unmatched_count} 个留待重查或人工核实；"
                f"读取未完成活动 {deferred_activity_count} 个，路由不完整关系 {deferred_route_count} 条。"
            )
            for line in account_lines:
                self.log(f"指定{action_text}范围：" + line + "。")
            self.log(f"按商品 ID {action_text}采用单次确认，正在提交上述精确活动关系。")
        self._submit_prepared_submission(prepare)

    def _submission_input_saved(self, response: object) -> None:
        prepare = dict(dict(response or {}).get("prepare") or {})
        errors = list(dict(prepare.get("seller_input") or {}).get("validation_errors") or [])
        if errors:
            self._operation_error("创建自建活动", "创建参数未通过检查：" + "；".join(str(value) for value in errors), execution=True)
            return
        self._submit_prepared_submission(prepare)

    def _submit_prepared_submission(self, prepare: dict[str, Any]) -> None:
        selected = list(dict(prepare.get("seller_input") or {}).get("selected_targets") or [])
        commit_body = {
            "confirmText": "REAL_SUBMIT",
            "confirmationToken": str(prepare.get("confirmation_token") or ""),
        }
        if selected:
            commit_body["createConfirmText"] = "CREATE_SELLER_CAMPAIGN"
        self.pending_group_payload = {"prepare_id": prepare["prepare_id"], "commit_body": commit_body}
        self.pending_group_payload["commit_sent"] = True
        self._set_execution_busy(True)
        self.log("准备完成，正在启动任务。")
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
        self.execution_started_at = time.perf_counter()
        self.running_group = group
        self.prepare_poll_timer.stop()
        self.preparing_submission = {}
        self.pending_prepare_payload = None
        self.pending_group_payload = None
        self.job_log_seen = {str(child.get("job_id") or ""): set() for child in group.get("children") or []}
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
        terminal = {"completed", "partial_or_failed", "failed", "cancelled", "interrupted"}
        group = dict(payload.get("group") or response or {})
        if not group.get("id"):
            self._poll_group_failed(ApiError("未读取到执行组状态。", kind="unknown", retryable=True))
            return
        self.running_group = group
        self.pending_group_payload = None
        self.poll_failure_count = 0
        pending_log_lines: list[tuple[str, int, int, object]] = []
        for child_index, child in enumerate(list(group.get("children") or [])):
            job_id = str(child.get("job_id") or child.get("id") or "")
            logs = list(child.get("userLogs") or child.get("user_logs") or [])
            current_keys = {execution_log_identity(line) for line in logs}
            seen = self.job_log_seen.setdefault(job_id, set())
            seen.intersection_update(current_keys)
            for line_index, line in enumerate(logs):
                identity = execution_log_identity(line)
                if identity in seen:
                    continue
                at = str(line.get("at") or "") if isinstance(line, dict) else ""
                pending_log_lines.append((at, child_index, line_index, line))
                seen.add(identity)
        for _at, _child_index, _line_index, line in sorted(
            pending_log_lines,
            key=lambda entry: (not entry[0], entry[0], entry[1], entry[2]),
        ):
            message = execution_log_message(line)
            if message:
                self.log(message)
        if str(group.get("status") or "").lower() in terminal:
            self.poll_timer.stop()
            result = dict(group.get("result") or {})
            stores = list(result.get("stores") or [])
            action = str(result.get("action") or group.get("action") or "")
            duration_text = str(result.get("duration_text") or "")
            if not duration_text and getattr(self, "execution_started_at", 0.0) > 0.0:
                elapsed_sec = max(0.0, time.perf_counter() - self.execution_started_at)
                if elapsed_sec >= 60:
                    duration_text = f"{int(elapsed_sec // 60)}分{int(elapsed_sec % 60)}秒"
                else:
                    duration_text = f"{int(elapsed_sec)}秒"

            if group.get("oauth_expired"):
                self.handle_oauth_expired(str(group.get("expired_account") or ""), str(group.get("expired_store_name") or ""))

            for store_result in stores:
                if store_result.get("oauth_expired"):
                    acc = str(store_result.get("account_id") or "")
                    name = str(store_result.get("store_name") or "")
                    self.handle_oauth_expired(acc, name)
                status = str(store_result.get("status") or "")
                account_id = str(store_result.get("account_id") or "")
                store = self._store_for_account(account_id) if account_id else str(store_result.get("store_name") or "当前店铺")
                site = str(store_result.get("site_name") or self.site_combo.currentText() or "全部站点")
                ending = {
                    "completed": "完成",
                    "partial_or_failed": "部分完成",
                    "failed": "未完整完成",
                    "cancelled": "已停止",
                    "interrupted": "意外中断",
                }.get(status.lower(), status_text(status))
                store_dur = f"（耗时 {store_result['duration_text']}）" if store_result.get("duration_text") else ""
                self.log(f"{store} / {site}：{action_label(action)}{ending}{store_dur}，{execution_result_text(store_result, action)}。")
            dur_suffix = f"，总耗时：{duration_text}" if duration_text else ""
            self.log(
                f"本次{action_label(action)}总汇总：店铺 {int(result.get('store_count') or len(stores))} 个，"
                f"{execution_result_text(result, action)}{dur_suffix}。"
            )
            self.running_group.clear()
            self.pending_group_payload = None
            self.job_log_seen.clear()
            self.poll_failure_count = 0
            self._set_execution_busy(False)
            self._refresh_records_after_group()
            # The activity table is a separate cached scope from task history.
            # Refresh it after every terminal execution so cancelled items do
            # not remain visible until the next manual scope refresh.
            self.refresh_scope()
            self._maybe_auto_shutdown()
            if action == "cancel":
                group_id = str(group.get("id") or "")
                if group_id:
                    self.log("取消活动已完成，正在立即同步刷新涉及活动的最新商品缓存...")
                    self._run_worker(
                        lambda: self.api.post(f"/api/execution/groups/{group_id}/post-cancel-refresh", timeout=60),
                        self._post_cancel_refresh_finished,
                        lambda error: self.log(f"涉及活动商品缓存同步提示：{product_error(error)}"),
                    )

    def _maybe_auto_shutdown(self) -> None:
        if not self.auto_shutdown_check.isChecked():
            return
        self.log("所有执行任务已结束，已触发自动关机倒计时（60 秒）...")
        dialog = AutoShutdownCountdownDialog(seconds=60, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and not dialog.cancelled:
            self.log("正在执行系统关机命令...")
            execute_system_shutdown()
        else:
            self.log("用户已取消自动关机。")

    def _post_cancel_refresh_finished(self, result: object) -> None:
        payload = dict(result or {})
        refreshed = int(payload.get("refreshed") or 0)
        self.log(f"涉及活动商品缓存刷新完成，已同步最新价格与状态（{refreshed} 个活动）。")
        last_canceled = get_last_canceled_batch()
        if last_canceled and last_canceled.get("count"):
            count = int(last_canceled["count"])
            self.log(f"💡 提示：刚刚取消的 {count} 个商品已自动记录，点击「按商品 ID 操作活动」即可一键载入重新报名。")
        self.refresh_scope()

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
            self.log("执行动作或活动结构已变化，本次未执行，请重新开始核对范围。")
            self._operation_error("提交执行", "执行动作或活动结构已变化，本次未执行，请重新开始核对范围。", execution=True)
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

    def _on_auto_shutdown_toggled(self) -> None:
        enabled = self.auto_shutdown_check.isChecked()
        def save() -> dict[str, Any]:
            return self.api.post("/api/settings", {"autoShutdownAfterExecution": enabled}).get("settings", {})
        self._run_worker(
            save,
            lambda settings: self.log("已开启自动关机，执行完成后 60 秒关机。" if enabled else "已关闭自动关机。"),
            lambda error: self.log("自动关机设置保存失败：" + product_error(error)),
        )

    def _open_targeted_cancel(self) -> None:
        if self.preparing_submission or self.running_group or self.pending_group_payload:
            QMessageBox.information(self, "按商品 ID 操作活动", "当前已有准备或执行任务，请等待其结束后再操作。")
            return
        account_ids = self.selected_account_ids()
        if not account_ids:
            QMessageBox.information(self, "按商品 ID 操作活动", "当前店铺没有可用授权账号。")
            return
        store_text = self.selected_store_text()
        site_text = self.site_combo.currentText() or "全部站点"
        seller_text = self.seller_combo.currentText() or "全部自建活动"
        official_text = self.official_combo.currentText() or "全部官方活动"
        locked_seller_discount = int(self.seller_discount.value())
        locked_official_discount = int(self.official_discount.value())
        dialog = TargetedCancelDialog(
            f"店铺={store_text}；站点={site_text}；自建活动={seller_text}；官方活动={official_text}",
            self,
            submission_ready=self._can_start_targeted_cancel,
            seller_discount=locked_seller_discount,
            official_discount=locked_official_discount,
        )
        if dialog.exec() != QDialogAccepted:
            return
        self._start_targeted_item_execution(dialog.item_ids(), dialog.action())

    def _start_targeted_item_execution(self, item_ids: list[str], action: str) -> None:
        if not item_ids:
            return
        account_ids = self.selected_account_ids()
        if not account_ids:
            QMessageBox.information(self, "按商品 ID 操作活动", "当前店铺没有可用授权账号。")
            return
        store_names = {account_id: self._store_for_account(account_id) for account_id in account_ids}
        store_text = "、".join(store_names.values())
        site_text = self.site_combo.currentText() or "全部站点"
        locked_seller_discount = int(self.seller_discount.value())
        locked_official_discount = int(self.official_discount.value())

        if action == "refresh_cache":
            self.log(f"正在向美客多官方接口刷新 {len(item_ids)} 个指定商品的最新数据与活动资格...")
            selected_site = str(self.site_combo.currentData() or "").strip()
            refresh_timeout = max(300, len(item_ids) * 3)
            self._run_worker(
                lambda: self.api.post(
                    "/api/items/targeted-refresh",
                    {
                        "accountIds": account_ids,
                        "itemIds": item_ids,
                        "siteIds": [selected_site] if selected_site else [],
                    },
                    timeout=refresh_timeout,
                    timeout_message="商品缓存刷新时间较长，后台仍在处理中...",
                ),
                self._targeted_refresh_finished,
                self._targeted_refresh_failed,
            )
            return

        if not self._can_start_targeted_cancel():
            QMessageBox.information(self, "按商品 ID 操作活动", "缓存补偿或其它任务仍在运行，本次没有提交；请稍后重试。")
            return
        settings = self.settings
        submission_id = str(uuid.uuid4())
        payload = execution_group_payload(
            account_ids=account_ids,
            action=action,
            filters=(self.current_filters() if action == "enroll" else targeted_cancel_filters(str(self.site_combo.currentData() or ""))),
            store_names=store_names,
            site_name_text=site_text,
            seller_discount=locked_seller_discount,
            official_discount=locked_official_discount,
            read_concurrency=int(settings.get("readConcurrency") or 2),
            activity_concurrency=int(settings.get("previewConcurrency") or 2),
            write_concurrency=int(settings.get("writeConcurrency") or 2),
            client_submission_id=submission_id,
        )
        payload.update({
            "requested_action": action,
            "targetedItemAction": True,
            "targetedCancelAllActivities": action == "cancel",
            "itemIds": item_ids,
        })
        self.preparing_submission = {"client_submission_id": submission_id, "state": "starting"}
        self.pending_prepare_payload = dict(payload)
        self._set_prepare_busy(True)
        self.log(f"正在核对 {len(item_ids)} 个指定商品的可{'取消' if action == 'cancel' else '报名'}活动；当前只读取命中范围。")
        self._run_worker(
            lambda: self.api.post(
                "/api/execution/submissions/prepare", payload, timeout=20,
                timeout_message="指定商品操作范围准备较慢，正在恢复已保存的准备记录。",
            ),
            self._prepare_started,
            self._prepare_start_failed,
        )

    def _targeted_refresh_failed(self, error: Exception) -> None:
        msg = product_error(error)
        if "后台仍在处理中" in msg or "超时" in msg or "timed out" in msg.lower():
            self.log(f"⏳ 商品缓存刷新提示：{msg}（完成后将自动生效，无需重复点击）")
        else:
            self.log(f"❌ 商品缓存刷新失败：{msg}")

    def _targeted_refresh_finished(self, payload: object) -> None:
        data = dict(payload or {})
        count = int(data.get("refreshed_count") or 0)
        total = int(data.get("total_count") or 0)
        results = list(data.get("results") or [])
        self.log(f"✅ 已成功刷新 {count}/{total} 个商品的平台最新数据与活动资格！")
        for res in results[:10]:
            item_id = str(res.get("item_id") or "")
            price = res.get("price")
            candidates = int(res.get("candidate_count") or 0)
            if res.get("ok"):
                self.log(f"  • {item_id}: 原价 {price}，当前可报 {candidates} 个活动")
            else:
                self.log(f"  • {item_id}: 刷新失败 - {res.get('error', '未知错误')}")
        if len(results) > 10:
            self.log(f"  ...其余 {len(results) - 10} 个商品已全部更新至本地缓存。")
        self._refresh_scope()

    def _run_item_query(self, dialog: ItemQueryDialog, item_id: str) -> None:
        safe_id = quote(item_id, safe="")
        self._run_worker(
            lambda: self.api.get(
                "/api/items/{}/status".format(safe_id),
                timeout=30,
                timeout_message="商品查询等待时间较长，本地记录仍未返回。",
            ),
            lambda payload: dialog.show_result(payload),
            lambda error: dialog.show_error(product_error(error)),
        )

    def _run_item_query_on_surface(self, item_id: str) -> None:
        self._run_item_query(self.query_page, item_id)

    def _sync_targeted_cancel_page_scope(self) -> None:
        store_text = self.selected_store_text()
        site_text = self.site_combo.currentText() or "全部站点"
        seller_text = self.seller_combo.currentText() or "全部自建活动"
        official_text = self.official_combo.currentText() or "全部官方活动"
        locked_seller_discount = int(self.seller_discount.value())
        locked_official_discount = int(self.official_discount.value())
        scope_text = f"店铺={store_text}；站点={site_text}；自建活动={seller_text}；官方活动={official_text}"
        self.targeted_cancel_page.update_scope(scope_text, locked_seller_discount, locked_official_discount)

    def _save_settings_from_page(self) -> None:
        values = self.settings_page.values()
        values["defaultFilters"] = self.current_filters()
        self._run_worker(
            lambda: self.api.post("/api/settings", values).get("settings", values),
            self._settings_saved,
            lambda error: self._operation_error("保存设置", error),
        )

    def _open_settings(self, initial_tab: str = "") -> None:
        self._show_settings_page(initial_tab=initial_tab)

    def _load_settings_context(self) -> dict[str, Any]:
        settings = dict(self.api.get("/api/settings").get("settings") or {})
        ids = [account.account_id for account in self.accounts]
        refresh_path = ApiClient.query("/api/accounts/profiles/refresh", accountIds=",".join(ids))
        refreshed = self.api.get(refresh_path, timeout=45)
        accounts = [account_from_json(row) for row in refreshed.get("accounts") or []]

        def load_sites(account: Account) -> list[dict[str, Any]]:
            result = self.api.get(
                f"/api/accounts/{account.account_id}/sites?includeAll=1&probeBusiness=1&refresh=1",
                timeout=120,
            )
            return [
                {**site, "account_id": account.account_id, "store_name": account.store_name}
                for site in result.get("sites", [])
            ]

        operating: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(accounts)))) as executor:
            for rows in executor.map(load_sites, accounts):
                operating.extend(rows)
        benchmark = self.api.get("/api/concurrency-benchmark/results").get("results", {})
        return {"settings": settings, "accounts": accounts, "operating": operating, "benchmark": benchmark}

    def _apply_settings_context(self, dialog: SettingsDialog, context: object) -> None:
        data = dict(context or {})
        settings = dict(data.get("settings") or {})
        accounts = list(data.get("accounts") or self.accounts)
        operating = list(data.get("operating") or [])
        benchmark = benchmark_text(dict(data.get("benchmark") or {}))
        if settings:
            self.settings = settings
        self.accounts = accounts
        self.operating_rows_cache = operating
        self.benchmark_text_cache = benchmark
        if dialog.isVisible() or getattr(dialog, "embedded", False):
            dialog.apply_settings_context(settings)
            dialog.apply_background_context(accounts, operating, benchmark)

    def _settings_saved(self, settings: object) -> None:
        self.settings = dict(settings or self.settings)
        self.log(
            "设置已保存：读取全局上限 {read}，活动目录并发上限 {activity}，商品写入全局上限 {write}。".format(
                read=self.settings.get("readConcurrency", 125),
                activity=self.settings.get("previewConcurrency", 192),
                write=self.settings.get("writeConcurrency", 160),
            )
        )
        QMessageBox.information(self, "保存设置", "系统设置已成功保存！")
        self._show_page(0)
        self._run_worker(
            self._load_initial_bundle,
            self._apply_initial_bundle,
            lambda error: self._operation_error("刷新设置", error),
            phase="initial_bundle",
        )

    def _start_oauth(self, dialog: SettingsDialog) -> None:
        client_id = dialog.oauth_client_id.text().strip()
        client_secret = dialog.oauth_client_secret.text().strip()
        redirect_uri = migrate_oauth_redirect_uri(dialog.oauth_redirect_uri.text().strip())
        if not client_id:
            QMessageBox.warning(dialog, "账号授权", "请先填写美客多应用 Client ID。")
            return
        if not redirect_uri:
            QMessageBox.warning(dialog, "账号授权", "请先填写在美客多开发者后台登记的 OAuth 回调地址（Redirect URI）。")
            return
        if client_id and not client_secret:
            for app in getattr(dialog, "oauth_apps", []):
                if str(app.get("clientId") or "").strip() == client_id:
                    uncommitted_secret = str(app.get("clientSecret") or "").strip()
                    if uncommitted_secret:
                        client_secret = uncommitted_secret
                    break
        payload: dict[str, str] = {}
        payload["clientId"] = client_id
        if client_secret:
            payload["clientSecret"] = client_secret
        payload["redirectUri"] = redirect_uri

        def call_start() -> dict[str, Any]:
            if client_id:
                return self.api.post("/api/oauth/start", payload)
            return self.api.post("/api/oauth/start/from-config", payload)

        self._run_worker(
            call_start,
            lambda result: self._oauth_started(dialog, dict(result or {})),
            lambda error: QMessageBox.warning(dialog, "账号授权", product_error(error)),
        )

    def _oauth_started(self, dialog: SettingsDialog, result: dict[str, Any]) -> None:
        url = str(
            result.get("authorizationUrl")
            or result.get("authorization_url")
            or result.get("url")
            or ""
        )
        if url:
            opened = QDesktopServices.openUrl(QUrl(url))
            QApplication.clipboard().setText(url)
            if opened:
                QMessageBox.information(
                    dialog,
                    "账号授权",
                    "已在浏览器中打开授权页面（授权链接也已同步复制到剪贴板）。\n\n请在浏览器中完成登录并同意授权；完成后复制地址栏跳转的完整回调网址，粘贴至下方输入框并点击「完成授权」。",
                )
            else:
                QMessageBox.information(
                    dialog,
                    "账号授权",
                    "已将授权链接复制到剪贴板，请粘贴至浏览器地址栏打开以完成授权。\n\n授权完成后请复制地址栏跳转的完整回调网址，粘贴至下方输入框并点击「完成授权」。",
                )
        else:
            QMessageBox.warning(dialog, "账号授权", str(result.get("error") or "未读取到授权链接。"))

    def _complete_oauth(self, dialog: SettingsDialog, callback: str) -> None:
        if not callback:
            QMessageBox.information(dialog, "账号授权", "请粘贴浏览器回调链接。")
            return
        payload = {
            "callbackUrl": callback,
            "callback": callback,
            "callbackText": callback,
        }

        def _on_success(result: dict[str, Any]) -> None:
            self._refresh_accounts_from_settings(dialog)
            dialog.callback_edit.clear()
            account_name = ""
            if isinstance(result, dict) and result.get("account"):
                acc = result["account"]
                account_name = str(acc.get("store_name") or acc.get("account_id") or "")
            msg = f"账号授权成功：{account_name} 已绑定。" if account_name else "账号授权成功！已绑定至当前系统。"
            QMessageBox.information(dialog, "账号授权", msg)

        self._run_worker(
            lambda: self.api.post("/api/oauth/complete-callback", payload),
            _on_success,
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

    def handle_oauth_expired(self, account_id: str, display_name: str = "") -> None:
        """Prompt user to re-authorize account when refresh token is revoked or expired."""
        account_id_str = str(account_id or "").strip()
        if not hasattr(self, "_notified_expired_accounts"):
            self._notified_expired_accounts = set()
        if account_id_str and account_id_str in self._notified_expired_accounts:
            return
        if account_id_str:
            self._notified_expired_accounts.add(account_id_str)

        store_title = display_name or (self._store_for_account(account_id_str) if account_id_str else "指定店铺")
        msg = (
            f"检测到店铺「{store_title}」美客多授权已过期或在后台被解除授权。\n\n"
            "需要重新获取授权才能继续提报活动。是否立即打开设置页面重新授权？"
        )
        reply = QMessageBox.warning(
            self,
            "店铺授权失效",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._open_settings(initial_tab="auth")

    def _operation_error(self, operation: str, message: str, *, execution: bool = False) -> None:
        self.poll_busy = False
        self._set_busy(False, f"{operation}未完成")
        if execution and not self.running_group and not self.pending_group_payload and not self.preparing_submission:
            self._set_execution_busy(False)
        readable = product_error(message)
        self.log(f"{operation}未完成：{readable}")

        msg_str = str(message)
        if "invalid_grant" in msg_str.lower() or "授权已失效或在后台被解除" in msg_str:
            acc_id = self.current_account_id() or ""
            self.handle_oauth_expired(acc_id, readable)
            return

        QMessageBox.warning(self, operation, readable)

    def _set_busy(self, busy: bool, message: str) -> None:
        self.ui_busy = busy
        if busy:
            self.startup_status_finalized = False
            self._show_status(message, message)
        elif not self.startup_status_finalized:
            self._show_status(message, message)
        if not self.running_group and not self.pending_group_payload and not self.preparing_submission:
            self.execute_button.setEnabled(not busy and self._can_start_submission())
            self.targeted_cancel_button.setEnabled(self._can_open_targeted_cancel())
            if hasattr(self, "full_get_button"):
                self.full_get_button.setEnabled(not busy)
        self.records_refresh_button.setEnabled(not busy)

    def _set_execution_busy(self, busy: bool) -> None:
        self.execute_button.setEnabled(busy or self._can_start_submission())
        self.execute_button.setText("停止任务" if busy else "开始执行")
        self.targeted_cancel_button.setEnabled(not busy and self._can_open_targeted_cancel())
        if hasattr(self, "full_get_button"):
            self.full_get_button.setEnabled(not busy)
        for control in (self.mode_combo, self.store_combo, self.site_combo, self.seller_combo, self.official_combo):
            control.setEnabled(not busy)
        self._update_discount_state()

    def _set_prepare_busy(self, busy: bool) -> None:
        if not busy:
            self.execute_button.setText("开始执行")
            self.execute_button.setEnabled(self._can_start_submission())
            self.targeted_cancel_button.setEnabled(self._can_open_targeted_cancel())
            if hasattr(self, "full_get_button"):
                self.full_get_button.setEnabled(True)
            for control in (self.mode_combo, self.store_combo, self.site_combo, self.seller_combo, self.official_combo):
                control.setEnabled(True)
            self._update_discount_state()
            return
        state = str(self.preparing_submission.get("state") or "")
        has_prepare_id = bool(self.preparing_submission.get("prepare_id"))
        self.execute_button.setText("正在停止" if state == "stopping" else "停止准备" if has_prepare_id else "正在准备")
        self.execute_button.setEnabled(has_prepare_id and state != "stopping")
        self.targeted_cancel_button.setEnabled(False)
        if hasattr(self, "full_get_button"):
            self.full_get_button.setEnabled(False)
        for control in (self.mode_combo, self.store_combo, self.site_combo, self.seller_combo, self.official_combo):
            control.setEnabled(False)
        self._update_discount_state()

    def _can_start_submission(self) -> bool:
        if self.ui_busy or not self._scope_startup_ready() or not self.scope_ready or not self.today_completion_ready:
            return False
        if self.mode_combo.currentText() == "自动判断" and self._completion_for_current_scope():
            return False
        if self.mode_combo.currentText() == "自动判断" and self.auto_action not in {"enroll", "update", "cancel"}:
            return False
        return True

    def _can_start_targeted_cancel(self) -> bool:
        return (
            not self.ui_busy
            and not self.refresh_busy
            and not self.preparing_submission
            and not self.running_group
            and not self.pending_group_payload
            and bool(self.selected_account_ids())
        )

    def _can_open_targeted_cancel(self) -> bool:
        return (
            not self.preparing_submission
            and not self.running_group
            and not self.pending_group_payload
            and bool(self.accounts)
        )

    def _scope_startup_ready(self) -> bool:
        if self.startup_ready and (self.startup_readiness.get("ready") is True or not self.startup_readiness):
            return True
        selected = set(self.selected_account_ids())
        if not selected or self.startup_refresh_status not in {"ok", "blocked", "degraded"}:
            return False
        ready_accounts = set(str(value) for value in self.startup_readiness.get("ready_accounts") or [])
        blocked_accounts = set(str(value) for value in self.startup_readiness.get("blocked_account_ids") or [])
        return selected.issubset(ready_accounts) and not selected.intersection(blocked_accounts)

    def _sync_submit_availability(self) -> None:
        if self.refresh_busy:
            self.execute_button.setText("刷新缓存中…")
            self.execute_button.setEnabled(True)
            self.targeted_cancel_button.setEnabled(self._can_open_targeted_cancel())
            return
        if self.running_group or self.pending_group_payload or self.preparing_submission:
            return
        self.execute_button.setEnabled(self._can_start_submission())
        self.targeted_cancel_button.setEnabled(self._can_open_targeted_cancel())

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
            self.prepare_read_key = ""
            self.prepare_stage_seen = ""
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
        formatted = f"[{datetime.now():%H:%M:%S}] {message}"
        cleaner_tags = ("[商品清理]", "[商品扫描]", "[清店]", "[商品删除]")
        activity_tags = ("[活动管理]", "[活动撤销]", "【全量GET】", "活动读取", "全量GET", "[全量GET]")
        targeted_tags = ("[按ID操作]", "[指定商品]", "按商品 ID", "指定商品", "命中范围", "命中活动", "商品缓存刷新", "商品 ID", "已成功刷新")

        is_cleaner = any(tag in message for tag in cleaner_tags)
        is_activity = any(tag in message for tag in activity_tags)
        is_targeted = any(tag in message for tag in targeted_tags)

        # 1. 路由至按ID操作专属日志框（仅当匹配指定商品/按ID操作专属标签时）
        if hasattr(self, "targeted_log_box") and self.targeted_log_box is not None:
            if is_targeted:
                self.targeted_log_box.append_log_line(formatted)

        # 2. 路由至商品清理或活动管理或常规主工作台日志框
        if is_cleaner:
            if hasattr(self, "cleaner_log_box") and self.cleaner_log_box is not None:
                self.cleaner_log_box.append_log_line(formatted)
            elif hasattr(self, "log_box") and self.log_box is not None:
                self.log_box.append_log_line(formatted)
        elif is_activity:
            if hasattr(self, "activity_log_box") and self.activity_log_box is not None:
                self.activity_log_box.append_log_line(formatted)
            elif hasattr(self, "log_box") and self.log_box is not None:
                self.log_box.append_log_line(formatted)
        else:
            if hasattr(self, "enrollment_log_box") and self.enrollment_log_box is not None:
                self.enrollment_log_box.append_log_line(formatted)
            elif hasattr(self, "log_box") and self.log_box is not None:
                self.log_box.append_log_line(formatted)

    def _append_startup_final_log(self, status: str, message: str) -> None:
        """Append one timestamped terminal startup summary per final state."""
        key = f"{status}|{message}"
        if key == self.startup_final_log_key:
            return
        self.startup_final_log_key = key
        self.log(message)

    def _show_status(self, short_text: str, full_text: str | None = None) -> None:
        """Keep runtime progress out of the bottom border; details live in the log."""
        status_bar = self.statusBar()
        status_bar.clearMessage()
        status_bar.setToolTip("")

    def _phase_label(self, phase: str) -> str:
        return STARTUP_PHASE_LABELS.get(phase, phase or "后台")

    def _record_worker_stage(
        self,
        phase: str,
        state: str,
        elapsed_ms: int,
        *,
        payload_size: int | None = None,
        error: object | None = None,
    ) -> None:
        error_kind = type(error).__name__ if error is not None else ""
        cause_code = str(getattr(error, "code", "") or getattr(error, "cause_code", "") or "")
        diagnostic_event(
            "ui_worker_stage",
            phase=phase or "worker",
            state=state,
            elapsed_ms=max(0, int(elapsed_ms)),
            callback_thread=threading.get_ident(),
            payload_size=payload_size if payload_size is not None else -1,
            error_kind=error_kind,
            cause_code=cause_code,
        )

    def _startup_phase_started(self, phase: str) -> None:
        if phase not in STARTUP_PHASE_LABELS:
            return
        started_at = time.perf_counter()
        self._startup_phase_started_at[phase] = started_at
        self._record_worker_stage(phase, "started", 0, payload_size=0)
        if not self.startup_status_finalized:
            self._show_status(f"正在{self._phase_label(phase)}...")

    def _phase_elapsed_ms(self, phase: str) -> int:
        started_at = self._startup_phase_started_at.get(phase)
        if started_at is None:
            return 0
        return max(0, int((time.perf_counter() - started_at) * 1000))

    def _startup_phase_failed(self, phase: str, error: object) -> None:
        if phase not in STARTUP_PHASE_LABELS:
            return
        self.startup_ready = False
        if phase in {"service_connect", "initial_bundle", "scope_bundle"}:
            self.scope_ready = False
        if phase in {"service_connect", "initial_bundle", "records_bundle"}:
            self.today_completion_ready = False
        label = self._phase_label(phase)
        elapsed_ms = self._phase_elapsed_ms(phase)
        message = f"加载{label}阶段失败：{product_error(error)}（耗时 {elapsed_ms / 1000:.1f} 秒）"
        if not self.startup_status_finalized:
            self._show_status(f"加载{label}失败", message)
        self.log(message)
        self._set_busy(False, message)

    def _callback_error(self, phase: str, error: object) -> RuntimeError:
        return RuntimeError(f"加载{self._phase_label(phase)}阶段失败：界面结果应用未完成。")

    def _safe_payload_size(self, value: object) -> int:
        try:
            encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
            return len(encoded)
        except Exception:
            return -1

    def _handle_worker_callback_failure(
        self,
        phase: str,
        started_at: float,
        on_error: Callable[[object], None],
        error: Exception,
        payload_size: int,
    ) -> None:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        self._record_worker_stage(
            phase,
            "callback_failed",
            elapsed_ms,
            payload_size=payload_size,
            error=error,
        )
        safe_error = self._callback_error(phase, error)
        self._startup_phase_failed(phase, safe_error)
        try:
            on_error(safe_error)
        except Exception as callback_error:
            self._record_worker_stage(
                phase,
                "error_callback_failed",
                elapsed_ms,
                payload_size=payload_size,
                error=callback_error,
            )

    def _run_worker(
        self,
        function: Callable[[], Any],
        on_result: Callable[[object], None],
        on_error: Callable[[object], None],
        *,
        phase: str = "worker",
        on_progress: Callable[[object], None] | None = None,
        soft_error: bool = False,
    ) -> None:
        started_at = time.perf_counter()
        self._startup_phase_started(phase)
        worker = Worker(function)
        self.workers.add(worker)

        def queue_result(result: object) -> None:
            payload_size = self._safe_payload_size(result)
            self.gui_dispatcher.dispatch(
                lambda: self._run_gui_result(
                    phase,
                    started_at,
                    on_result,
                    on_error,
                    result,
                    payload_size,
                )
            )

        def queue_error(error: object) -> None:
            self.gui_dispatcher.dispatch(
                lambda: self._run_gui_error(phase, started_at, on_error, error, soft_error=soft_error)
            )

        def queue_progress(progress: object) -> None:
            if on_progress is None:
                return
            self.gui_dispatcher.dispatch(
                lambda: self._run_gui_progress(phase, started_at, on_progress, progress)
            )

        worker.signals.result.connect(queue_result)
        worker.signals.error.connect(queue_error)
        worker.signals.progress.connect(queue_progress)
        worker.signals.finished.connect(
            lambda current=worker: self.gui_dispatcher.dispatch(
                lambda: self.workers.discard(current)
            )
        )
        self.thread_pool.start(worker)

    def _run_gui_result(
        self,
        phase: str,
        started_at: float,
        on_result: Callable[[object], None],
        on_error: Callable[[object], None],
        result: object,
        payload_size: int,
    ) -> None:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        if phase in STARTUP_PHASE_LABELS and phase != "startup_readiness" and not self.startup_status_finalized:
            self._show_status(
                f"{self._phase_label(phase)}数据已返回",
                f"{self._phase_label(phase)}数据已返回（耗时 {elapsed_ms / 1000:.1f} 秒），正在更新界面...",
            )
        try:
            on_result(result)
        except Exception as error:
            self._handle_worker_callback_failure(phase, started_at, on_error, error, payload_size)
            return
        self._record_worker_stage(
            phase,
            "completed",
            elapsed_ms,
            payload_size=payload_size,
        )

    def _run_gui_error(
        self,
        phase: str,
        started_at: float,
        on_error: Callable[[object], None],
        error: object,
        *,
        soft_error: bool = False,
    ) -> None:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        self._record_worker_stage(phase, "failed", elapsed_ms, error=error)
        if not soft_error:
            self._startup_phase_failed(phase, error)
        try:
            on_error(error)
        except Exception as callback_error:
            if not soft_error:
                self._startup_phase_failed(phase, error)
            self._record_worker_stage(phase, "error_callback_failed", elapsed_ms, error=callback_error)

    def _run_gui_progress(
        self,
        phase: str,
        started_at: float,
        on_progress: Callable[[object], None],
        progress: object,
    ) -> None:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            on_progress(progress)
        except Exception as error:
            self._record_worker_stage(phase, "progress_callback_failed", elapsed_ms, error=error)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        diagnostic_event("main_window_close_event", running_group=bool(self.running_group), already_closing=self._closing)
        save_timer = getattr(self, "_cleaner_save_timer", None)
        if save_timer is not None and save_timer.isActive():
            save_timer.stop()
            try:
                save_cleaner_draft(self.cleaner_records)
            except Exception:
                pass
        if self.preparing_submission and not self.running_group and not self.pending_group_payload:
            prepare_id = str(self.preparing_submission.get("prepare_id") or "")
            progress = int(dict(self.preparing_submission.get("progress") or {}).get("percent") or 0)
            self.log("执行范围仍在后台准备；关闭界面后会继续核对，重新打开可接回同一进度。")
            diagnostic_event("prepare_detached_on_close", prepare_id=prepare_id, progress=progress)
            self.prepare_poll_timer.stop()
            self.poll_timer.stop()
            refresh_timer = getattr(self, "refresh_poll_timer", None)
            if refresh_timer is not None:
                refresh_timer.stop()
            auto_reprice_timer = getattr(self, "auto_reprice_timer", None)
            if auto_reprice_timer is not None:
                auto_reprice_timer.stop()
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
        refresh_timer = getattr(self, "refresh_poll_timer", None)
        if refresh_timer is not None:
            refresh_timer.stop()
        auto_reprice_timer = getattr(self, "auto_reprice_timer", None)
        if auto_reprice_timer is not None:
            auto_reprice_timer.stop()
        self.service.stop()
        event.accept()


def resource_path(relative: str) -> Path:
    import sys

    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


def product_version() -> str:
    candidates = [
        resource_path("app/build-info.json"),
        resource_path("app/package.json"),
        resource_path("build-info.json"),
        Path(__file__).resolve().parents[1] / "package.json",
    ]
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = str(payload.get("version") or payload.get("product_version") or "").strip()
        if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", value):
            return value
    # Native Python engine release product version
    return "2.1.06"


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
    spin.setFixedWidth(max(88, min(104, text_width + 18 + non_text_width)))
    return spin


def short_date(value: str) -> str:
    if not value:
        return ""
    return value[:10].replace("-", "/")


def record_timestamp_text(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return value, value
    compact = parsed.strftime("%Y/%m/%d\n%H:%M:%S")
    return compact, f"{parsed:%Y-%m-%d %H:%M:%S %Z}\n原始时间：{value}"


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


def record_scope_text(unique_items: int | None, relations: int | None) -> str:
    if unique_items is None and relations is None:
        return "旧记录未区分 / -"
    return f"{count_or_marker(unique_items, '旧记录未区分')} 件 / {count_or_marker(relations)} 项"


def record_scope_tooltip(unique_items: int | None, relations: int | None) -> str:
    return (
        f"涉及商品：{count_or_marker(unique_items, '旧记录未区分')} 件（按商品编号去重）\n"
        f"处理项：{count_or_marker(relations, '旧记录未区分')} 项（商品×活动）"
    )


def daily_item_delta_text(delta: dict[str, Any]) -> tuple[str, str]:
    status = str(delta.get("status") or "").lower()
    added = optional_contract_count(delta, "added_count")
    removed = optional_contract_count(delta, "removed_count")
    current_date = str(delta.get("current_date") or "")
    baseline_date = str(delta.get("baseline_date") or "")
    if status in {"ready", "complete"} and added is not None and removed is not None and current_date and baseline_date:
        return (
            f"较昨日：新增 {added} 件，减少 {removed} 件",
            f"比较区间：{baseline_date} → {current_date}\n仅使用服务端确认完整的每日商品身份快照。",
        )
    reason = str(delta.get("reason_cn") or delta.get("reason") or "").strip()
    tooltip = "需要服务端提供前一日和当日的完整商品身份快照后才能计算，界面不会根据不完整数据推算。"
    if reason:
        tooltip += "\n数据状态：" + business_reason_text(reason)
    insufficient_routes = list(delta.get("insufficient_routes") or [])
    if insufficient_routes:
        route_text = "、".join(
            f"{row.get('account_id') or '-'} / {row.get('child_user_id') or '-'} / {row.get('site_id') or '-'}"
            for row in insufficient_routes[:20]
        )
        tooltip += "\n未完成路由：" + route_text
    unresolved_count = int(delta.get("route_unresolved_count") or 0)
    if unresolved_count:
        tooltip += f"\n路由未确认：{unresolved_count} 条"
    return "较昨日商品变化：完整快照不足，暂不统计新增/减少", tooltip


def _startup_replay_metrics_text(replay: dict[str, Any]) -> str:
    """Render the terminal CBT replay facts without collapsing categories."""
    if not replay:
        return ""
    item_categories = dict(replay.get("classification_item_counts") or {})
    categories = dict(replay.get("terminal_categories") or replay.get("classification_counts") or {})

    def category(name: str) -> int:
        value = item_categories.get(name)
        if value is None:
            value = replay.get(f"{name}_count")
        if value is None:
            value = categories.get(name, 0)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    success = replay.get("unique_resource_succeeded")
    if success is None:
        success = replay.get("succeeded", replay.get("replayed", 0))
    cache_updated = replay.get("cache_updated_count")
    physical_used = replay.get("physical_get_used")
    physical_budget = replay.get("physical_budget", replay.get("total_budget"))
    try:
        success = max(0, int(success or 0))
    except (TypeError, ValueError):
        success = 0
    try:
        cache_updated = max(0, int(cache_updated or 0))
    except (TypeError, ValueError):
        cache_updated = 0
    parts = [
        f"CBT成功 {success} 个",
        f"子缓存更新 {cache_updated} 个",
        f"字段不足 {category('quarantined_unknown')} 个",
        f"路线缺失 {category('route_catalog_gap')} 个",
        f"无关/不可用排除 {category('terminal_irrelevant')} 个",
        f"跨账号排除 {category('terminal_foreign')} 个",
        f"待重试 {category('eligible_partial') + category('eligible_budget_remaining') + category('eligible_retryable')} 个",
    ]
    if physical_used is not None or physical_budget is not None:
        try:
            used_text = str(max(0, int(physical_used or 0)))
        except (TypeError, ValueError):
            used_text = "未知"
        try:
            budget_text = str(max(0, int(physical_budget or 0))) if physical_budget is not None else "未知"
        except (TypeError, ValueError):
            budget_text = "未知"
        parts.append(f"GET {used_text}/{budget_text}")
    remaining_events = replay.get("remaining_eligible_events", replay.get("remaining_events"))
    remaining_items = replay.get("remaining_eligible_resources", replay.get("remaining_unique_resources"))
    if remaining_events is not None or remaining_items is not None:
        try:
            event_text = str(max(0, int(remaining_events or 0)))
        except (TypeError, ValueError):
            event_text = "未知"
        try:
            item_text = str(max(0, int(remaining_items or 0)))
        except (TypeError, ValueError):
            item_text = "未知"
        parts.append(f"剩余可处理事件 {event_text} / 商品 {item_text}")
    return "；".join(parts)


def startup_refresh_success_text(refresh: dict[str, Any]) -> str:
    readiness = dict(refresh.get("readiness") or {})
    parts = ["商品和活动缓存同步完成" if readiness.get("ready") is True else "活动缓存刷新已结束，执行仍被阻断"]
    audits = [row for row in refresh.get("account_audits") or [] if isinstance(row, dict)]
    if audits:
        account_parts = []
        expired_ids: set[str] = set()
        for audit in audits:
            store = str(audit.get("store_name") or "当前店铺")
            status = str(audit.get("status") or "unknown")
            label = {"ok": "完成", "failed": "失败", "unknown": "未确认", "running": "进行中"}.get(status, "未确认")
            account_parts.append(f"{store}：{label}")
            for stage in audit.get("stages") or []:
                for skipped in stage.get("expired_skipped") or []:
                    if isinstance(skipped, dict) and skipped.get("promotion_id"):
                        expired_ids.add(str(skipped.get("promotion_id")))
        if account_parts and all(part.endswith("：完成") for part in account_parts):
            parts.append(f"{len(account_parts)}家店铺均完成")
        else:
            parts.append("店铺结果：" + "；".join(account_parts))
        if expired_ids:
            parts.append(f"已跳过已结束活动 {len(expired_ids)} 个")
    webhook_items = dict(refresh.get("webhook_item_summary") or {})
    refreshed_items = int(webhook_items.get("refreshed_item_count") or 0)
    changed_items = int(webhook_items.get("changed_item_count") or 0)
    if refreshed_items:
        parts.append(f"今日商品快照已更新 {refreshed_items} 个，其中 {changed_items} 个检测到数据变化")
    callback = dict(refresh.get("cbt_callback") or {})
    replay_summary = dict(refresh.get("cbt_replay") or callback.get("last_replay") or {})
    categories = dict(replay_summary.get("classification_item_counts")
                      or replay_summary.get("terminal_categories")
                      or replay_summary.get("classification_counts")
                      or {})
    if int(categories.get("terminal_irrelevant") or 0):
        parts.append(f"已安全排除无关或不可用商品 {int(categories.get('terminal_irrelevant') or 0)} 个")
    if int(categories.get("terminal_no_actionable_global_parent") or 0):
        parts.append(f"全球父商品 {int(categories.get('terminal_no_actionable_global_parent') or 0)} 个暂无可操作站点子商品，已隔离且不影响活动")
    if int(categories.get("route_catalog_gap") or 0):
        parts.append(f"路线缺失 {int(categories.get('route_catalog_gap') or 0)} 个，需先做路线校准")
    if int(categories.get("quarantined_unknown") or 0):
        parts.append(f"字段不足或多候选 {int(categories.get('quarantined_unknown') or 0)} 个，已隔离")
    retryable_events = int(callback.get("retryable_failed_count") or 0)
    retryable_items = int(callback.get("retryable_failed_item_count") or 0)
    if retryable_events or retryable_items:
        parts.append(f"商品通知读取失败 {max(retryable_events, retryable_items)} 个，稍后可重试")
    replay = replay_summary
    if replay:
        remaining_eligible = int(replay.get("remaining_eligible_resources") or 0)
        retryable = (
            int(replay.get("eligible_partial_count") or 0)
            + int(replay.get("eligible_budget_remaining_count") or 0)
            + int(replay.get("eligible_retryable_count") or 0)
        )
        if remaining_eligible or retryable:
            parts.append(f"仍有 {max(remaining_eligible, retryable)} 个商品待继续处理")
    delta = dict(refresh.get("daily_item_delta") or {})
    if str(delta.get("status") or "").lower() == "ready" and delta.get("added_count") is not None:
        parts.append(f"商品差异缓存新增 {int(delta.get('added_count') or 0)} 件")
        if delta.get("removed_count") is not None:
            parts.append(f"减少 {int(delta.get('removed_count') or 0)} 件")
    return "；".join(parts) + "。"


def startup_refresh_blocked_text(refresh: dict[str, Any]) -> str:
    data = dict(refresh or {})
    readiness = dict(data.get("readiness") or {})
    reasons = [
        str(row.get("reason_cn") or "").strip()
        for row in readiness.get("reasons") or []
        if isinstance(row, dict) and str(row.get("reason_cn") or "").strip()
    ]
    error = str(data.get("error") or "").strip()
    if error and not reasons:
        reasons.append(business_reason_text(error))
    if not reasons:
        reasons.append("启动缓存完整性尚未确认。")
    replay = dict(data.get("cbt_replay") or dict(data.get("cbt_callback") or {}).get("last_replay") or {})
    categories = dict(replay.get("classification_item_counts")
                      or replay.get("terminal_categories")
                      or replay.get("classification_counts")
                      or {})
    category_text = []
    if int(categories.get("terminal_irrelevant") or 0):
        category_text.append(f"已删除或不可用 {int(categories.get('terminal_irrelevant') or 0)} 个，可排除")
    if int(categories.get("terminal_foreign") or 0):
        category_text.append(f"跨账号 {int(categories.get('terminal_foreign') or 0)} 个，保留审计")
    if int(categories.get("terminal_no_actionable_global_parent") or 0):
        category_text.append(f"全球父商品 {int(categories.get('terminal_no_actionable_global_parent') or 0)} 个暂无站点子商品，已隔离，不影响当前活动")
    if int(categories.get("route_catalog_gap") or 0):
        category_text.append(f"路线缺失 {int(categories.get('route_catalog_gap') or 0)} 个，需路线校准")
    if int(categories.get("quarantined_unknown") or 0):
        category_text.append(f"字段不足或多候选 {int(categories.get('quarantined_unknown') or 0)} 个，已隔离")
    reasons.extend(category_text)
    audits = [row for row in data.get("account_audits") or [] if isinstance(row, dict)]
    account_parts = []
    for audit in audits:
        store = str(audit.get("store_name") or "当前店铺")
        status = str(audit.get("status") or "unknown")
        label = {"ok": "完成", "failed": "失败", "unknown": "未确认", "running": "进行中"}.get(status, "未确认")
        account_parts.append(f"{store}：{label}")
    if account_parts:
        reasons.append("店铺结果：" + "；".join(account_parts))
    metrics = _startup_replay_metrics_text(replay)
    if metrics:
        reasons.append(metrics)
    return "启动缓存未达到执行条件：" + "；".join(dict.fromkeys(reasons)) + "。"


def record_activity_text(task: dict[str, Any]) -> str:
    store = task.get("store_name") or ""
    site = task.get("site_name") or (site_name(str(task.get("site_id") or "")) if task.get("site_id") else "全部站点")
    if str(task.get("promotion_type") or "").upper() == "BATCH" or not task.get("promotion_id"):
        if store and store != "当前店铺":
            return f"{store} / {site}"
        return "批量汇总"
    activity = task.get("promotion_name") or task.get("activity_name") or "当前活动"
    if store and store != "当前店铺":
        return f"{store} / {site} / {activity}"
    return f"{site} / {activity}" if site else activity


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
        platform_pending = optional_contract_count(task, "platform_pending_count")
        action_success = {
            "enroll": "报名成功",
            "update": "更新成功",
        }.get(action, "成功")
        lines = [f"{action_success} {success}"]
        if platform_pending is not None:
            lines.append(f"平台待生效 {platform_pending}")
        category_text = failure_category_text(task.get("failure_reasons"), _failed)
        lines.append(f"跳过 {skipped}")
        if category_text:
            lines.append(f"失败 {_failed}（{category_text}）")
        notice = _completeness_notice(task)
        if notice:
            lines.append(notice)
        return "\n".join(lines)
    request_success = optional_contract_count(task, "request_success_count")
    verified_removed = optional_contract_count(task, "live_verified_removed_count")
    pending = optional_contract_count(task, "pending_verification_count")
    if request_success is None and verified_removed is None and pending is None:
        return "旧记录未区分"
    if request_success is not None and _has_readback_incomplete_reason(task):
        pending_text = count_or_marker(pending if pending is not None else task.get("failed_count"))
        lines = [
            f"取消请求已成功：{count_or_marker(request_success)}",
            f"部分商品仍待平台确认：{pending_text}",
            f"成功取消：{count_or_marker(verified_removed)}",
        ]
    else:
        lines = [
            f"取消请求 {count_or_marker(request_success)}",
            f"成功取消 {count_or_marker(verified_removed)}",
            f"待平台确认 {count_or_marker(pending)}",
        ]
    notice = _completeness_notice(task)
    if notice:
        lines.append(notice)
    return "\n".join(lines)


def is_reservation_start(value: object) -> bool:
    if not value:
        return False
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        start = datetime.fromisoformat(text)
        return start > datetime.now(start.tzinfo)
    except (TypeError, ValueError):
        return False


def failure_category_text(failure_reasons: object, failed_count: int) -> str:
    reasons = failure_reasons if isinstance(failure_reasons, list) else []
    if not reasons:
        # No categorized reasons available (legacy rows or server without
        # reason breakdown): do not guess "rejected" for every failure.
        return ""
    readback_incomplete = 0
    pending_relations = 0
    under_review = 0
    discount = 0
    for item in reasons:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "")
        count = int(item.get("count") or 0)
        if _reason_matches(reason, 0):
            readback_incomplete += count
        elif _reason_matches(reason, 1):
            pending_relations += count
        elif "审核" in reason:
            under_review += count
        elif "折扣" in reason or ("价格" in reason and "不认可" in reason):
            discount += count
    rejected = max(int(failed_count or 0) - under_review - discount, 0)
    parts = []
    if readback_incomplete > 0:
        parts.append(f"平台未返回可读取的商品清单 {readback_incomplete}")
    if pending_relations > 0:
        parts.append(f"待平台确认 {pending_relations}")
    if under_review > 0:
        parts.append(f"审核中 {under_review}")
    if discount > 0:
        parts.append(f"折扣不被认可 {discount}")
    if rejected > 0:
        parts.append(f"拒绝 {rejected}")
    return " / ".join(parts) if parts else ""


def execution_result_text(result: dict[str, Any], action: str) -> str:
    unique_items = optional_contract_count(result, "unique_item_count")
    relations = optional_contract_count(result, "relation_count")
    activity_failures = optional_contract_count(result, "activity_failure_count")
    failed = int(result.get("failed") or result.get("failed_count") or 0)
    skipped = int(result.get("skipped") or result.get("skipped_count") or 0)
    common = (
        f"处理 {count_or_marker(relations)} 项，"
        f"涉及 {count_or_marker(unique_items, '旧记录未区分')} 件商品"
    )
    normalized_action = str(action or "").lower()
    if normalized_action == "cancel":
        request_success = optional_contract_count(result, "request_success_count")
        verified_removed = optional_contract_count(result, "live_verified_removed_count")
        pending = optional_contract_count(result, "pending_verification_count")
        readback_incomplete = _has_readback_incomplete_reason(result)
        if request_success is not None and readback_incomplete:
            pending_count = pending if pending is not None else failed
            cancellation = (
                f"取消请求已成功 {count_or_marker(request_success)}，"
                f"部分商品仍待平台确认 {count_or_marker(pending_count)}，"
                f"成功取消 {count_or_marker(verified_removed, '旧记录未区分')}"
            )
            failed_text = f"部分商品待平台确认 {count_or_marker(pending_count)}"
        else:
            cancellation = (
                f"取消请求成功 {count_or_marker(request_success, '旧记录未区分')}，"
                f"成功取消 {count_or_marker(verified_removed, '旧记录未区分')}，"
                f"取消请求已提交，待平台回查确认 {count_or_marker(pending, '旧记录未区分')}"
            )
            failed_text = f"商品失败 {failed}"
        notice = _completeness_notice(result)
        if notice:
            cancellation += f"，{notice}"
        # skipped for cancel duplicates pending_verification; do not repeat it.
        return (
            f"{common}，{cancellation}，{failed_text}，"
            f"活动失败 {count_or_marker(activity_failures)}"
        )
    success = int(result.get("success") or result.get("success_count") or 0)
    platform_pending = optional_contract_count(result, "platform_pending_count")
    pending_verification = optional_contract_count(result, "pending_verification_count")
    if pending_verification is None:
        pending_verification = optional_contract_count(result, "pending")
    pending_text = ""
    if platform_pending is not None:
        pending_text = f"，平台已接受待生效 {platform_pending}"
    additional_pending = max(int(pending_verification or 0) - int(platform_pending or 0), 0)
    if additional_pending:
        pending_text += f"，仍待平台确认 {additional_pending}"
    success_label = {"enroll": "报名成功", "update": "更新成功"}.get(normalized_action, "成功")
    if is_reservation_start(result.get("promotion_start_date")):
        success_label = {"enroll": "预约成功", "update": "更新成功"}.get(normalized_action, "成功")
    failed_text = f"商品失败 {failed}"
    category_text = failure_category_text(result.get("failure_reasons"), failed)
    if category_text:
        failed_text += f"（{category_text}）"
    notice = _completeness_notice(result)
    if notice:
        failed_text += f"，{notice}"
    return (
        f"{common}，{success_label} {success}{pending_text}，{failed_text}，"
        f"活动失败 {count_or_marker(activity_failures)}，跳过 {skipped}"
    )


def promotion_type_text(value: str) -> str:
    key = str(value or "").strip().upper()
    return {
        "SELLER_CAMPAIGN": "自建活动",
        "CUSTOM": "自建活动",
        "PRICE_DISCOUNT": "单品折扣",
        "DEAL": "官方活动",
        "MARKETPLACE_CAMPAIGN": "官方活动",
        "SMART": "SMART",
        "LIGHTNING": "限时活动",
    }.get(key, key or "其它活动")


def status_text(value: str) -> str:
    return {
        "started": "进行中",
        "pending": "待开始",
        "candidate": "可报名",
        "completed": "已完成",
        "partial_or_failed": "部分完成",
        "failed": "未完整完成",
        "cancelled": "已停止",
        "cancelling": "停止中",
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
        important_markers = ("失败", "异常", "错误", "限流", "冷却", "重试", "恢复", "中断", "停止", "待平台", "完成")
        low_value_markers = ("正在处理活动", "正在读取", "正在核对", "详情 ", "排队 ", "本地整理", "分页 ")
        is_pure_concurrency_line = (
            text.startswith("并发处理活动任务")
            or text.startswith("并发读取站点活动")
            or text.startswith("并发读取活动商品")
            or text.startswith("并发提交")
        )
        if any(marker in text for marker in low_value_markers) and not any(marker in text for marker in important_markers):
            return ""
        if is_pure_concurrency_line:
            return ""
        return business_reason_text(text)
    return ""


def execution_log_identity(value: object) -> str:
    if isinstance(value, dict):
        explicit = value.get("event_id") or value.get("id")
        if explicit:
            return "id:" + str(explicit)
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return repr(value)
    return str(value)


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


def record_discount_text(task: dict[str, Any]) -> str:
    if str(task.get("action") or "").lower() == "cancel":
        return "-"
    parts = []
    seller = task.get("seller_activity_text")
    if not seller and task.get("seller_discount_percent") is not None:
        s_dp = task.get("seller_discount_percent")
        try:
            s_val = float(s_dp)
            s_str = f"{int(s_val)}" if s_val.is_integer() else f"{s_val}"
            seller = f"{s_str}%"
        except (ValueError, TypeError):
            seller = f"{s_dp}%"
    official = task.get("official_activity_text")
    if not official and task.get("official_discount_percent") is not None:
        o_dp = task.get("official_discount_percent")
        try:
            o_val = float(o_dp)
            o_str = f"{int(o_val)}" if o_val.is_integer() else f"{o_val}"
            official = f"{o_str}%"
        except (ValueError, TypeError):
            official = f"{o_dp}%"
    if seller:
        parts.append(f"自建{seller}")
    if official:
        parts.append(f"官方{official}")
    if parts:
        return " / ".join(parts)
    dp = task.get("discount_percent") if task.get("discount_percent") is not None else task.get("discount")
    if dp is not None:
        try:
            val = float(dp)
            val_str = f"{int(val)}" if val.is_integer() else f"{val}"
            return f"自建{val_str}% / 官方{val_str}%"
        except (ValueError, TypeError):
            return f"{dp}%"
    return "-"


def task_detail_text(task: dict[str, Any], details: list[dict[str, Any]], items: dict[str, Any]) -> str:
    summary_task = dict(task)
    if items.get("unique_item_count") is not None:
        summary_task["unique_item_count"] = items.get("unique_item_count")
    if items.get("relation_count") is not None:
        summary_task["relation_count"] = items.get("relation_count")
    if items.get("activity_failure_count") is not None:
        summary_task["activity_failure_count"] = items.get("activity_failure_count")
    for field in ("request_success_count", "live_verified_removed_count", "pending_verification_count"):
        if summary_task.get(field) is None:
            summary_task[field] = sum(int(row.get(field) or 0) for row in details)
    base = business_details_text(summary_task, details)
    failed = list(items.get("failed_items") or [])
    parts = [base]
    if failed:
        seen: dict[str, int] = {}
        for row in failed:
            item_id = str(row.get("item_id") or "")
            seen[item_id] = seen.get(item_id, 0) + 1
        unique_failed = []
        for row in failed:
            item_id = str(row.get("item_id") or "")
            if item_id and item_id not in {str(existing.get("item_id") or "") for existing in unique_failed}:
                unique_failed.append(row)
        total_failed = int(summary_task.get("failed_count") or 0)
        lines = [f"\n失败商品明细（去重 {len(unique_failed)} 件 / 共 {total_failed} 件失败）："]
        for row in unique_failed[:100]:
            reason = business_reason_text(row.get("reason"))
            lines.append(f"  {row.get('item_id') or '-'} - {reason or '未知原因'}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def business_task_text(task: dict[str, Any], reserved_count: int | None = None) -> str:
    _total, success, failed, skipped = task_display_counts(task)
    unique_items = optional_contract_count(task, "unique_item_count")
    relations = optional_contract_count(task, "relation_count")
    activity_failures = optional_contract_count(task, "activity_failure_count")
    action = str(task.get("action") or "").lower()
    lines = [
        f"时间：{short_date(str(task.get('created_at') or ''))}\n"
        f"动作：{action_label(str(task.get('action') or ''))}",
    ]
    # Discounts only apply to enroll/update. Cancel shows no discount: the
    # persisted seller/official_discount_percent is the enroll/update value and
    # would mislead a cancel summary.
    if action != "cancel":
        seller_text = task.get("seller_activity_text") or (f"{task.get('seller_discount_percent')}%" if task.get("seller_discount_percent") else "")
        official_text = task.get("official_activity_text") or (f"{task.get('official_discount_percent')}%" if task.get("official_discount_percent") else "")
        if not seller_text and not official_text:
            dp = task.get("discount_percent") if task.get("discount_percent") is not None else task.get("discount")
            if dp is not None:
                try:
                    val = float(dp)
                    val_str = f"{int(val)}" if val.is_integer() else f"{val}"
                    seller_text = f"{val_str}%"
                    official_text = f"{val_str}%"
                except (ValueError, TypeError):
                    seller_text = f"{dp}%"
                    official_text = f"{dp}%"
        lines.append(f"自建折扣：{seller_text or '-'}\n官方折扣：{official_text or '-'}")
    lines.extend([
        f"涉及商品：{count_or_marker(unique_items, '旧记录未区分')} 件（按商品编号去重）",
        f"需处理项：{count_or_marker(relations, '旧记录未区分')} 项（商品×活动）",
    ])
    request_success = None
    pending = None
    if action == "cancel":
        request_success = optional_contract_count(task, "request_success_count")
        pending = optional_contract_count(task, "pending_verification_count")
        if request_success is not None and _has_readback_incomplete_reason(task):
            pending_count = pending if pending is not None else failed
            lines.extend([
                f"取消请求已成功：{count_or_marker(request_success)}",
                f"部分商品仍待平台确认：{count_or_marker(pending_count)}",
                f"成功取消：{count_or_marker(optional_contract_count(task, 'live_verified_removed_count'))}",
            ])
        else:
            lines.extend([
                f"取消请求成功：{count_or_marker(request_success, '旧记录未区分')}",
                f"成功取消：{count_or_marker(optional_contract_count(task, 'live_verified_removed_count'), '旧记录未区分')}",
                "取消请求已提交，待平台回查确认："
                + count_or_marker(pending, "旧记录未区分"),
            ])
    else:
        success_label = {
            "enroll": "报名成功",
            "update": "更新成功",
        }.get(action, "成功")
        if reserved_count is None:
            lines.append(f"{success_label}：{success}")
        else:
            reserved = int(reserved_count)
            active = max(int(success) - reserved, 0)
            lines.append(f"{success_label}：{success}（立即生效 {active}，预约成功 {reserved}）")
        platform_pending = optional_contract_count(task, "platform_pending_count")
        if platform_pending:
            lines.append(f"平台已接受待生效：{platform_pending}")
    readback_incomplete = action == "cancel" and request_success is not None and _has_readback_incomplete_reason(task)
    failed_line = (
        f"部分商品待平台确认：{count_or_marker(pending if pending is not None else failed)}"
        if readback_incomplete
        else f"商品失败：{failed}"
    )
    category_text = failure_category_text(task.get("failure_reasons"), failed)
    if category_text:
        failed_line += f"（{category_text}）"
    notice = _completeness_notice(task)
    if notice:
        failed_line += f"，{notice}"
    # For cancel, "skipped" rows are actually pending platform verification
    # (same set as pending_verification_count); label them accordingly instead
    # of showing a duplicate "skipped" number.
    skip_label = "待平台回查" if action == "cancel" else "跳过"
    lines.extend([
        failed_line,
        f"活动失败：{count_or_marker(activity_failures, '旧记录未区分')}",
        f"{skip_label}：{skipped}",
        f"失败原因：{business_reason_text(task.get('failure_reason') or task.get('short_failure_reason') or '-')}",
    ])
    return "\n".join(lines)

def business_details_text(task: dict[str, Any], details: list[dict[str, Any]]) -> str:
    reserved_count = sum(
        int(row.get("success_count") or 0)
        for row in details
        if is_reservation_start(row.get("promotion_start_date"))
    )
    lines = [business_task_text(task, reserved_count), "", "店铺 / 站点 / 活动明细："]
    for row in details:
        promotion_type = str(row.get("promotion_type") or "").upper()
        promotion_id = str(row.get("promotion_id") or "")
        if promotion_type == "BATCH" or promotion_id.upper() == "__BATCH__":
            continue
        store = row.get("store_name") or "当前店铺"
        site = row.get("site_name") or site_name(str(row.get("site_id") or ""))
        activity = row.get("promotion_name") or row.get("activity_name") or "当前活动"
        stamp = short_date(str(row.get("created_at") or ""))
        lines.append(f"- {stamp} {store} / {site} / {activity}：{execution_result_text(row, str(row.get('action') or task.get('action') or ''))}")
    return "\n".join(lines)


def benchmark_text(results: dict[str, Any]) -> str:
    _ = results
    return (
        "真实上限：商品读取 125，活动目录 192；商品写入按动作分别为"
        "批量取消 160、批量报名 160、批量更新 128；报名 192 在全店铺持续运行中出现密集限流，已停用。"
        "设置中的写入上限是总开关，实际任务不会超过对应动作的实测上限；"
        "遇到限流、网络异常、平台服务异常或超时会自动降档并持久续跑。"
    )


def product_error(message: str) -> str:
    clean = str(message or "").strip()
    if not clean:
        return "当前操作没有完成，请稍后重试。"
    readable = business_reason_text(clean)
    if readable != clean:
        return readable
    technical = ("requires an element", "target element has type", "JsonElement", "fetch failed", "rate limit")
    if any(value.lower() in clean.lower() for value in technical):
        if "rate limit" in clean.lower():
            return "平台接口限流，请稍后重试。"
        if "fetch failed" in clean.lower():
            return "网络连接失败，请稍后重试。"
        return "任务结果不完整，已完成结果仍会保留，请查看历史记录。"
    return clean


def prepare_failure_message(prepare: dict[str, Any]) -> str:
    kind = str(prepare.get("error_kind") or "").strip()
    messages = {
        "rate_limit": "平台读取触发限流，请稍后重新核对。",
        "service": "平台服务暂时异常，请稍后重新核对。",
        "timeout": "平台读取超时，请稍后重新核对。",
        "network": "网络连接暂时异常，请检查网络后重新核对。",
        "local_contract": "程序处理平台数据时发现格式异常，已安全停止准备。",
        "local_storage": "本地状态暂时无法保存，已安全停止准备，请稍后重新核对。",
        "unknown": "准备范围时发生未分类异常，已安全停止。",
    }
    if kind in messages:
        return messages[kind]
    return business_reason_text(prepare.get("error") or "执行范围准备未完成。")


QDialogAccepted = 1
