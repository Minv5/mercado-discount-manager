from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import promotion_display_name, site_name
from dialogs import LogViewer
from ui_common.widgets import make_table, section_label

ACTIVITY_HEADERS = ["店铺", "站点", "类型", "活动", "状态", "商品数"]


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
    }.get(str(value or "").strip().lower(), str(value or "") or "-")


class ActivityPage(QFrame):
    """活动管理页面模块 (ActivityPage)。

    独立管理：
    - 活动列表表格展示与自适应列宽
    - 在线活动重新读取与全量 GET 数据操作入口
    - 专属活动运行日志追踪
    """

    refreshClicked = Signal()
    reloadLiveClicked = Signal()
    fullGetClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("surface")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
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

        self.activity_refresh_local_btn.clicked.connect(self.refreshClicked.emit)
        self.activity_reload_live_btn.clicked.connect(self.reloadLiveClicked.emit)
        self.full_get_button.clicked.connect(self.fullGetClicked.emit)

        top.addWidget(self.activity_refresh_local_btn)
        top.addWidget(self.activity_reload_live_btn)
        top.addWidget(self.full_get_button)
        top_layout.addLayout(top)

        self.activity_table = make_table(ACTIVITY_HEADERS)
        header = self.activity_table.horizontalHeader()
        header.setMinimumSectionSize(54)
        for column in (0, 1, 2, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        top_layout.addWidget(self.activity_table, 1)

        # 独立运行日志框
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(4)
        log_layout.addWidget(section_label("活动运行日志"))
        self.activity_log_box = LogViewer()
        self.activity_log_box.append_log_line(
            f"[{datetime.now():%H:%M:%S}] [活动管理] 活动管理引擎就绪。点击上方按钮可刷新或全量读取美客多活动。"
        )
        log_layout.addWidget(self.activity_log_box, 1)

        activity_splitter = QSplitter(Qt.Orientation.Vertical)
        activity_splitter.setChildrenCollapsible(False)
        activity_splitter.addWidget(top_frame)
        activity_splitter.addWidget(log_frame)
        activity_splitter.setSizes([380, 180])

        layout.addWidget(activity_splitter, 1)

    def populate(self, promotions: list[dict[str, Any]], accounts: list[Any]) -> None:
        """根据当前活动列表与店铺列表更新活动管理表格展示。"""
        self.activity_table.setRowCount(0)
        account_map = {getattr(acc, "account_id", ""): acc for acc in accounts}
        for promotion in promotions:
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
                getattr(account, "store_name", "") if account else "当前店铺",
                site_name(s_id),
                promotion_type_text(p_type),
                promotion_display_name(promotion),
                status_text(str(promotion.get("status") or "")),
                str(promotion.get("total") or promotion.get("items_total") or 0),
            ]
            for column, value in enumerate(values):
                self.activity_table.setItem(row, column, QTableWidgetItem(value))

    def append_log_line(self, line: str) -> None:
        self.activity_log_box.append_log_line(line)
