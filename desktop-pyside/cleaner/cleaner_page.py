from __future__ import annotations

import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from cleaner.table_model import CLEANER_HEADERS, CleanerTableModel
from dialogs import CopyZeroVisitIdsDialog, LogViewer
from engine.auth import AuthManager
from engine.client import MercadoClient
from engine.item_cleaner import (
    CleanerFilterCriteria,
    ItemCleanerEngine,
    ScannedItemRecord,
    clear_cleaner_draft,
    is_item_confirmed_deleted,
    load_cleaner_draft,
    mark_cached_items_deleted,
    save_cleaner_draft,
)
from ui_common.widgets import CheckableComboBox, section_label


class CleanerPage(QFrame):
    """商品合规风控与僵尸品清理页面模块 (CleanerPage)。

    独立承载：
    - 过滤与清店控制面板
    - 虚拟化表格模型与交互
    - 多店铺并发扫描与结果汇总
    - 批量删除收口与草稿缓存管理
    - 0浏览商品ID提取导出
    """

    def __init__(
        self,
        engine: ItemCleanerEngine | None = None,
        gui_dispatcher: Any = None,
        log_cb: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("surface")
        self.cleaner_engine = engine or ItemCleanerEngine(MercadoClient(AuthManager(), max_concurrency=64))
        self.gui_dispatcher = gui_dispatcher
        self.log_cb = log_cb
        self.accounts: list[Any] = []
        self.cleaner_records: list[ScannedItemRecord] = []
        self.cleaner_scan_stop_event = threading.Event()
        self.cleaner_delete_stop_event = threading.Event()
        self.cleaner_is_scanning = False
        self.cleaner_is_deleting = False

        self._cleaner_save_timer = QTimer(self)
        self._cleaner_save_timer.setSingleShot(True)
        self._cleaner_save_timer.setInterval(1200)
        self._cleaner_save_timer.timeout.connect(self._save_cleaner_draft_async)

        self._build_ui()
        self._load_cleaner_draft()

    def _dispatch(self, fn: Callable[[], Any]) -> None:
        if self.gui_dispatcher and hasattr(self.gui_dispatcher, "dispatch"):
            self.gui_dispatcher.dispatch(fn)
        else:
            QTimer.singleShot(0, fn)

    def log(self, message: str) -> None:
        if self.log_cb:
            try:
                self.log_cb(message)
                return
            except Exception:
                pass
        self._cleaner_append_log(message)

    def _cleaner_append_log(self, text: str) -> None:
        if hasattr(self, "cleaner_log_box") and self.cleaner_log_box is not None:
            self.cleaner_log_box.append_log_line(f"[{datetime.now():%H:%M:%S}] {text}")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
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
        self.cleaner_account_combo.setFixedWidth(240)
        self.cleaner_account_combo.setFixedHeight(32)
        self.cleaner_account_combo.addItem("全部店铺（合并分析所有店铺）", "all")
        self.cleaner_account_combo.currentIndexChanged.connect(self._refresh_cleaner_sites)
        top_bar.addWidget(self.cleaner_account_combo)
        top_bar.addSpacing(16)

        top_bar.addWidget(QLabel("选择站点:"))
        self.cleaner_site_combo = CheckableComboBox()
        self.cleaner_site_combo.setFixedWidth(180)
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
        self.cleaner_visits_threshold_combo.setFixedWidth(210)
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
        self.cleaner_score_spin.setFixedWidth(136)
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
        self.cleaner_filter_mode_combo.setFixedWidth(172)
        self.cleaner_filter_mode_combo.addItem("全部满足 (AND)", "and")
        self.cleaner_filter_mode_combo.addItem("任一满足 (OR)", "or")
        self.cleaner_filter_mode_combo.setCurrentIndex(0)
        grid.addWidget(self.cleaner_filter_mode_combo, 1, 3)
        grid.setColumnStretch(4, 1)

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

        self.cleaner_table = QTableView()
        self.cleaner_model = CleanerTableModel(CLEANER_HEADERS, self)
        self.cleaner_model.on_selection_changed_cb = self._on_cleaner_selection_changed
        self.cleaner_table.setModel(self.cleaner_model)
        self.cleaner_table.setAlternatingRowColors(True)
        self.cleaner_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cleaner_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.cleaner_table.setShowGrid(True)
        self.cleaner_table.setSortingEnabled(True)
        self.cleaner_table.verticalHeader().setVisible(False)
        self.cleaner_table.verticalHeader().setDefaultSectionSize(32)
        self.cleaner_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self.cleaner_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.cleaner_table.setColumnWidth(0, 48)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(1, 110)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(2, 130)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(3, 70)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(4, 250)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(5, 110)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(6, 85)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(7, 120)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(8, 165)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(9, 115)
        header.setSectionResizeMode(10, QHeaderView.ResizeMode.Interactive)
        self.cleaner_table.setColumnWidth(10, 280)

        # 独立运行日志框与商品表格垂直分割（操作按钮整合至日志标题行右侧，消除底部留白）
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(6)

        log_header_row = QHBoxLayout()
        log_header_row.addWidget(section_label("商品清理运行日志"))
        log_header_row.addStretch(1)

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

        log_header_row.addWidget(self.cleaner_scan_btn)
        log_header_row.addWidget(self.cleaner_batch_delete_btn)
        log_header_row.addWidget(self.cleaner_copy_zero_visits_btn)
        log_header_row.addWidget(self.cleaner_clear_draft_btn)
        log_layout.addLayout(log_header_row)

        self.cleaner_log_box = LogViewer()
        self.cleaner_log_box.append_log_line(f"[{datetime.now():%H:%M:%S}] [商品清理] 风控与清理引擎就绪。支持常规风控筛选或清店模式。")
        log_layout.addWidget(self.cleaner_log_box, 1)

        cleaner_splitter = QSplitter(Qt.Orientation.Vertical)
        cleaner_splitter.setChildrenCollapsible(False)
        cleaner_splitter.addWidget(self.cleaner_table)
        cleaner_splitter.addWidget(log_frame)
        cleaner_splitter.setSizes([350, 180])
        layout.addWidget(cleaner_splitter, 1)

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

    def update_accounts(self, accounts: list[Any], selected_account_id: str | None = None) -> None:
        self.accounts = list(accounts)
        if hasattr(self, "cleaner_account_combo"):
            current = selected_account_id or self.cleaner_account_combo.currentData() or "all"
            self.cleaner_account_combo.blockSignals(True)
            self.cleaner_account_combo.clear()
            self.cleaner_account_combo.addItem("全部店铺（合并分析所有店铺）", "all")
            for acc in self.accounts:
                label = getattr(acc, "store_name", "") or getattr(acc, "account_id", "")
                self.cleaner_account_combo.addItem(f"{label} ({acc.account_id})", acc.account_id)
            idx = self.cleaner_account_combo.findData(current)
            if idx >= 0:
                self.cleaner_account_combo.setCurrentIndex(idx)
            else:
                self.cleaner_account_combo.setCurrentIndex(0)
            self.cleaner_account_combo.blockSignals(False)
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

        parent_win = self.parent()
        for row in getattr(parent_win, "operating_rows_cache", []):
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
                if acc.account_id in target_account_ids and getattr(acc, "site_id", None):
                    seen_sites.add(acc.site_id.upper())

        # 联动过滤经营站点配置
        settings = getattr(parent_win, "settings", {}) or {}
        operating_cfg = settings.get("operatingSites") or {}
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

        from core import site_name
        site_tuples = [(f"{site_name(sid)} ({sid})", sid) for sid in sorted(seen_sites)]
        self.cleaner_site_combo.set_items(site_tuples)

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
                self._dispatch(lambda: self.log(
                    f"[商品扫描] ==================== 开始多店铺并发商品合规扫描 ===================="
                ))
                self._dispatch(lambda: self.log(
                    f"[商品扫描] 扫描范围: {'【全部店铺】(' + str(tot_accs) + '个店铺全并发扫描)' if is_all else ('【' + (target_accounts[0].store_name or target_accounts[0].account_id) + '】')} | "
                    f"目标站点: {site_desc} | "
                    f"单店上限: 无限制"
                ))

                def _scan_single_store(acc_item: tuple[int, Any]):
                    acc_idx, acc = acc_item
                    if self.cleaner_scan_stop_event.is_set():
                        return

                    store_name = acc.store_name or acc.account_id
                    self._dispatch(lambda i=acc_idx, n=store_name, t=tot_accs: (
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
                                self._dispatch(lambda b=batch: self._cleaner_add_table_rows_batch(b))

                    def _on_matched(rec: ScannedItemRecord):
                        with buf_lock:
                            matched_buffer.append(rec)
                        _flush_buffer(force=False)

                    def _on_prog(cur: int, tot: int, step: str):
                        pass

                    def _on_lg(msg: str):
                        self._dispatch(lambda m=msg: self.log(m))

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
                self._dispatch(lambda m="\n".join(summary_lines): self.log(m))
            except Exception as e:
                err_msg = str(e)
                self._dispatch(lambda m=err_msg: self.log(f"[商品扫描] ❌ 扫描过程异常: {m}"))
            finally:
                self._dispatch(self._on_cleaner_scan_finished)

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
                        self._dispatch(lambda s=store_name, n=len(already_del_recs): self.log(
                            f"[商品删除] ⏩ 【{s}】已直接跳过 {n} 件已确认彻底删除的商品（无需重复调用 API）"
                        ))
                        for r in already_del_recs:
                            self._dispatch(lambda i=r.item_id: (
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
                        self._dispatch(lambda i=iid, st=st_text, s=succ: (
                            self.cleaner_model.update_item_status(i, st, s)
                            if hasattr(self, "cleaner_model") and self.cleaner_model is not None else None
                        ))

                    def _on_prog(cur: int, tot: int, step: str):
                        pass

                    def _on_lg(msg: str):
                        self._dispatch(lambda m=msg: self.log(m))

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

                self._dispatch(lambda m="\n".join(summary_lines): self.log(m))

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
                self._dispatch(lambda msg=info_msg: QMessageBox.information(
                    self, "删除完成", msg
                ))
            finally:
                def _done():
                    self.cleaner_is_deleting = False
                    self.cleaner_batch_delete_btn.setText("批量删除")
                    self.cleaner_batch_delete_btn.setEnabled(True)
                    self.cleaner_scan_btn.setEnabled(True)
                    self.cleaner_clear_draft_btn.setEnabled(True)

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
                self._dispatch(_done)

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

    def shutdown(self) -> None:
        if self._cleaner_save_timer and self._cleaner_save_timer.isActive():
            self._cleaner_save_timer.stop()
            try:
                save_cleaner_draft(self.cleaner_records)
            except Exception:
                pass
