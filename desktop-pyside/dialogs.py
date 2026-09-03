from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from PySide6.QtCore import QDate, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyledItemDelegate,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from callback_endpoints import (
    DEFAULT_ACTIVITY_CALLBACK_ACK_URL,
    DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL,
    DEFAULT_WEBHOOK_CALLBACK_URL,
)
from core import Account, parse_targeted_cancel_item_ids, site_name
from reason_text import business_reason_text


class AliasEditorDelegate(QStyledItemDelegate):
    """Keeps the compact table editor readable without changing global inputs."""

    def __init__(self, table: QTableWidget):
        super().__init__(table)
        self._table = table

    def createEditor(self, parent: QWidget, _option: Any, _index: Any) -> QLineEdit:
        editor = QLineEdit(parent)
        editor.setFont(self._table.font())
        editor.setFrame(False)
        editor.setStyleSheet("QLineEdit { padding: 0 4px; border: 0; background: transparent; }")
        return editor

    def updateEditorGeometry(self, editor: QWidget, option: Any, _index: Any) -> None:
        editor.setGeometry(option.rect.adjusted(1, 1, -1, -1))


class ConfirmDialog(QDialog):
    def __init__(self, title: str, message: str, ok_text: str = "确认", cancel_text: str = "取消", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setMaximumWidth(720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        body.setContentsMargins(2, 2, 8, 2)
        scroll.setWidget(body)
        screen = self.screen() or (parent.screen() if parent else None)
        max_body = max(120, int((screen.availableGeometry().height() if screen else 800) * 0.58))
        body_width = 620
        body.setFixedWidth(body_width - 56)
        measured = body.sizeHint().height() + 10
        scroll.setMinimumHeight(min(max(80, measured), max_body))
        scroll.setMaximumHeight(max_body)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox()
        ok = buttons.addButton(ok_text, QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        buttons.addButton(cancel_text, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setTabOrder(ok, buttons.buttons()[-1])
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.reject)


class TargetedCancelDialog(QDialog):
    def __init__(
        self,
        scope_text: str,
        parent: QWidget | None = None,
        submission_ready: Callable[[], bool] | None = None,
        seller_discount: int = 0,
        official_discount: int = 0,
    ):
        super().__init__(parent)
        self.setWindowTitle("按商品 ID 操作活动")
        self.setModal(True)
        self.resize(620, 430)
        self.setMinimumSize(520, 360)
        self._item_ids: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        heading = QLabel("批量处理指定商品的活动")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        scope = QLabel(f"核对范围：{scope_text}\n只会处理该范围内与输入商品 ID 精确匹配的已报名活动关系。")
        scope.setWordWrap(True)
        layout.addWidget(scope)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("操作类型"))
        self.action_combo = QComboBox()
        self.action_combo.addItem("报名活动", "enroll")
        self.action_combo.addItem("取消活动", "cancel")
        self.action_combo.currentIndexChanged.connect(self._sync_submit_state)
        action_row.addWidget(self.action_combo, 1)
        layout.addLayout(action_row)

        self.discount_note = QLabel()
        self.discount_note.setWordWrap(True)
        layout.addWidget(self.discount_note)
        self._seller_discount = int(seller_discount)
        self._official_discount = int(official_discount)

        self.item_input = QPlainTextEdit()
        self.item_input.setPlaceholderText("每行一个或用逗号分隔，例如：\nMLB1234567890\nMLM1234567890")
        layout.addWidget(self.item_input, 1)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName("muted")
        layout.addWidget(self.note)

        buttons = QDialogButtonBox()
        self.submit_button = buttons.addButton("开始核对并取消", QDialogButtonBox.ButtonRole.AcceptRole)
        self.submit_button.setObjectName("primary")
        buttons.addButton("返回", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.reject)
        self._submission_ready = submission_ready
        self._readiness_timer = QTimer(self)
        self._readiness_timer.setInterval(500)
        self._readiness_timer.timeout.connect(self._sync_submit_state)
        if submission_ready is not None:
            self._readiness_timer.start()
        self._sync_submit_state()

    def _sync_submit_state(self) -> None:
        ready = True if self._submission_ready is None else bool(self._submission_ready())
        action_text = "报名" if self.action() == "enroll" else "取消"
        self.discount_note.setText(
            f"本次报名折扣已锁定：自建活动 {self._seller_discount}%｜官方活动 {self._official_discount}%"
            if self.action() == "enroll" else "本次取消不使用折扣参数。"
        )
        self.submit_button.setText(f"开始核对并{action_text}")
        self.submit_button.setEnabled(ready)
        self.note.setText(
            f"点击后只读取命中的活动；存在匹配商品时会直接提交{action_text}，不再弹出第二个确认框。单次最多 200 个商品 ID。"
            if ready else
            "缓存补偿仍在运行。可以先填写商品 ID；补偿结束后本按钮会自动启用，也可以关闭窗口后用主按钮停止补偿。"
        )

    def _validate_and_accept(self) -> None:
        try:
            self._item_ids = parse_targeted_cancel_item_ids(self.item_input.toPlainText())
        except ValueError as error:
            QMessageBox.information(self, "按商品 ID 操作活动", str(error))
            return
        self.accept()

    def item_ids(self) -> list[str]:
        return list(self._item_ids)

    def action(self) -> str:
        return str(self.action_combo.currentData() or "enroll")


class SellerCampaignCreateDialog(QDialog):
    def __init__(self, targets: list[dict[str, Any]], parent: QWidget | None = None):
        super().__init__(parent)
        self.targets = targets
        self.setWindowTitle("创建自建活动")
        self.resize(620, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        help_text = QLabel("以下店铺站点已由可验证来源确认不存在自建活动。请勾选本次需要创建的目标；默认全部不勾选。")
        help_text.setWordWrap(True)
        root.addWidget(help_text)
        select_row = QHBoxLayout()
        select_all = QPushButton("全选")
        select_none = QPushButton("全不选")
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        select_none.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(select_all)
        select_row.addWidget(select_none)
        select_row.addStretch(1)
        root.addLayout(select_row)
        self.scope_list = QListWidget()
        self.scope_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for target in targets:
            label = target_label(target)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, target)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.scope_list.addItem(item)
        root.addWidget(self.scope_list, 1)

        form = QFormLayout()
        self.name_edit = QLineEdit("95")
        self.start_edit = QDateEdit(QDate.currentDate())
        self.finish_edit = QDateEdit()
        self.start_edit.setCalendarPopup(True)
        self.finish_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("yyyy-MM-dd")
        self.finish_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_edit.dateChanged.connect(self._sync_finish_range)
        self._sync_finish_range(self.start_edit.date())
        form.addRow("自建活动名", self.name_edit)
        form.addRow("开始日期", self.start_edit)
        form.addRow("结束日期", self.finish_edit)
        root.addLayout(form)
        note = QLabel("只创建 SELLER_CAMPAIGN 自建活动；确认后创建所选目标，并继续进入最终执行确认。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认创建并继续")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _sync_finish_range(self, value: QDate) -> None:
        last_day = calendar.monthrange(value.year(), value.month())[1]
        maximum = QDate(value.year(), value.month(), last_day)
        self.finish_edit.setMinimumDate(value)
        self.finish_edit.setMaximumDate(maximum)
        self.finish_edit.setDate(maximum)

    def _set_all_checked(self, checked: bool) -> None:
        for index in range(self.scope_list.count()):
            item = self.scope_list.item(index)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _validate(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.information(self, "创建自建活动", "请输入自建活动名。")
            return
        self.accept()

    def selected_targets(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index in range(self.scope_list.count()):
            item = self.scope_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(dict(item.data(Qt.ItemDataRole.UserRole)))
        return result

    def values(self) -> dict[str, Any]:
        start = self.start_edit.date().toPython()
        finish = self.finish_edit.date().toPython() + timedelta(days=1)
        return {
            "name": self.name_edit.text().strip(),
            "startDate": start.isoformat() + "T00:00:00",
            "finishDate": finish.isoformat() + "T00:00:00",
            "targetSelections": [
                {
                    "accountId": target_account_id(target),
                    "childUserId": str(target.get("child_user_id") or target.get("childUserId") or ""),
                    "siteId": target_site_id(target),
                }
                for target in self.selected_targets()
            ],
        }


class DetailsDialog(QDialog):
    def __init__(self, title: str, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 600)
        layout = QVBoxLayout(self)
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        layout.addWidget(buttons)


def _local_time(value: object) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)


def _item_action_text(value: object) -> str:
    return {"enroll": "报名", "update": "改价", "cancel": "取消"}.get(str(value or "").lower(), str(value or "-"))


def _item_action_status_text(value: object) -> str:
    return {
        "success": "成功",
        "request_success": "已提交",
        "live_verified_removed": "已确认移除",
        "live_still_started": "仍在活动",
        "pending_verification": "待平台确认",
        "skipped": "已跳过",
        "failed": "失败",
        "partial_or_failed": "部分失败",
        "cancelled": "已取消",
        "interrupted": "中断",
    }.get(str(value or "").lower(), str(value or "-"))


def _item_promotion_type_text(value: object) -> str:
    return {
        "SELLER_CAMPAIGN": "自建活动",
        "DEAL": "官方活动",
        "SMART": "SMART",
        "LIGHTNING": "限时活动",
    }.get(str(value or "").upper(), str(value or "其它活动"))


def _item_platform_status_text(value: object) -> str:
    return {
        "started": "进行中",
        "pending": "待开始",
        "candidate": "可报名",
        "completed": "已完成",
        "failed": "未完整完成",
        "cancelled": "已停止",
    }.get(str(value or "").lower(), str(value or "-"))


def render_item_status_text(
    item_id: str,
    actions: list[dict[str, Any]],
    items: list[dict[str, Any]],
    price_cache: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append(f"商品：{item_id}")
    if items:
        lines.append(f"当前活动关系：{len(items)} 个")
        for relation in items:
            raw = {}
            try:
                raw = json.loads(relation.get("raw_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            item_status = raw.get("status") or relation.get("cached_status")
            price = raw.get("price") if raw.get("price") is not None else relation.get("price")
            original = raw.get("original_price") if raw.get("original_price") is not None else relation.get("original_price")
            promo_label = relation.get("promotion_name") or relation.get("promotion_id") or "-"
            price_text = "未报名" if str(item_status or "").lower() == "candidate" else (price if price is not None else "-")
            base_price = original if original is not None else (price if price is not None else "-")
            lines.append(
                "- {promotion}（{promotion_type}）：{status}｜活动价：{price}｜折扣基准：{base} {currency}".format(
                    promotion=promo_label,
                    promotion_type=_item_promotion_type_text(relation.get("promotion_type")),
                    status=_item_platform_status_text(item_status),
                    price=price_text,
                    base=base_price,
                    currency=relation.get("currency_id") or "-",
                )
            )
        first = items[0]
        lines.append("活动缓存更新：{}　账号：{}".format(_local_time(first.get("updated_at")), first.get("account_name") or first.get("account_id") or "-"))
    else:
        lines.append("当前活动关系：无")
    if price_cache:
        pc = price_cache[0]
        lines.append("")
        lines.append(
            "最新价格快照：{price}（原价 {original}，{currency}）".format(
                price=pc.get("price") if pc.get("price") is not None else "-",
                original=pc.get("original_price") if pc.get("original_price") is not None else "-",
                currency=pc.get("currency_id") or "-",
            )
        )
        lines.append("快照账号：{}　时间：{}".format(pc.get("account_name") or pc.get("account_id") or "-", _local_time(pc.get("updated_at"))))
    if actions:
        latest = actions[0]
        lines.append("")
        lines.append("最近一次操作：{action}　{promotion}".format(
            action=_item_action_text(latest.get("action")),
            promotion=latest.get("promotion_name") or latest.get("promotion_id") or "-",
        ))
        lines.append("结果：{status}　时间：{time}".format(
            status=_item_action_status_text(latest.get("status")),
            time=_local_time(latest.get("created_at")),
        ))
        if latest.get("deal_price") is not None:
            lines.append("价格：{}".format(latest.get("deal_price")))
        if latest.get("error_cn"):
            lines.append("说明：{}".format(business_reason_text(latest.get("error_cn"))))
    return "\n".join(lines)


class ItemQueryDialog(QDialog):
    query_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("商品查询")
        self.resize(840, 620)
        self.setMinimumSize(680, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        heading = QLabel("商品查询")
        heading.setObjectName("sectionTitle")
        root.addWidget(heading)

        search_row = QHBoxLayout()
        self.item_input = QLineEdit()
        self.item_input.setPlaceholderText("输入商品 ID，如 MLB7258072116")
        self.item_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self.item_input, 1)
        self.search_button = QPushButton("查询")
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self._on_search)
        search_row.addWidget(self.search_button)
        root.addLayout(search_row)

        self.status_box = QPlainTextEdit()
        self.status_box.setReadOnly(True)
        self.status_box.setPlaceholderText("输入商品 ID 后点击查询，或按回车。")
        self.status_box.setMaximumHeight(190)
        root.addWidget(self.status_box)

        history_label = QLabel("操作历史")
        history_label.setObjectName("sectionTitle")
        root.addWidget(history_label)

        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(["时间", "店铺", "活动", "动作", "状态", "价格", "说明"])
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setShowGrid(True)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.verticalHeader().setDefaultSectionSize(36)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.history_table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        root.addWidget(buttons)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.reject)

    def _on_search(self) -> None:
        item_id = self.item_input.text().strip().upper()
        if not item_id:
            QMessageBox.information(self, "商品查询", "请输入商品 ID。")
            return
        self.status_box.setPlainText("正在查询 {} ...".format(item_id))
        self.history_table.setRowCount(0)
        self.search_button.setEnabled(False)
        self.query_requested.emit(item_id)

    def show_result(self, payload: object) -> None:
        if not self.isVisible():
            return
        self.search_button.setEnabled(True)
        data = dict(payload or {})
        item_id = str(data.get("item_id") or self.item_input.text().strip().upper())
        actions = list(data.get("actions") or [])
        items = list(data.get("items") or [])
        price_cache = list(data.get("price_cache") or [])
        if not actions and not items and not price_cache:
            self.status_box.setPlainText("{}：未找到该商品的任何记录，可能从未参与过活动。".format(item_id))
            return
        self.status_box.setPlainText(render_item_status_text(item_id, actions, items, price_cache))
        self._fill_history(actions)

    def show_error(self, message: object) -> None:
        if not self.isVisible():
            return
        self.search_button.setEnabled(True)
        self.status_box.setPlainText("查询失败：{}".format(business_reason_text(message or "未知错误")))

    def _fill_history(self, actions: list[dict[str, Any]]) -> None:
        table = self.history_table
        table.setRowCount(len(actions))
        for row, record in enumerate(actions):
            cells = [
                _local_time(record.get("created_at")),
                str(record.get("account_name") or record.get("account_id") or ""),
                str(record.get("promotion_name") or record.get("promotion_id") or ""),
                _item_action_text(record.get("action")),
                _item_action_status_text(record.get("status")),
                str(record.get("deal_price") if record.get("deal_price") is not None else "-"),
                business_reason_text(record.get("error_cn") or ""),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                table.setItem(row, col, item)


class SettingsDialog(QDialog):
    authorize_requested = Signal()
    complete_authorization_requested = Signal(str)
    refresh_requested = Signal()

    def __init__(
        self,
        settings: dict[str, Any],
        accounts: list[Account],
        operating_rows: list[dict[str, Any]],
        benchmark_text: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.accounts = accounts
        self.operating_rows = operating_rows
        self._initial_operating_sites = {
            str(account_id): [str(site_id).upper() for site_id in site_ids]
            for account_id, site_ids in dict(settings.get("operatingSites") or {}).items()
            if isinstance(site_ids, list)
        }
        self._site_selection_dirty = False
        self._merging_sites = False
        self._initial_aliases = {
            str(account_id): str(alias).strip()
            for account_id, alias in dict(settings.get("storeAliases") or {}).items()
            if str(alias).strip()
        }
        self._initial_store_names = {account.account_id: account.store_name for account in accounts}
        self._initial_field_values: dict[str, object] = {}
        self.setWindowTitle("设置")
        self.resize(760, 620)
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._daily_tab(), "日常设置")
        tabs.addTab(self._stores_tab(), "店铺与站点")
        tabs.addTab(self._auth_tab(), "账号授权")
        tabs.addTab(self._advanced_tab(benchmark_text), "高级")
        root.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _daily_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.seller_discount = QSpinBox()
        self.official_discount = QSpinBox()
        for field in (self.seller_discount, self.official_discount):
            field.setRange(1, 90)
            field.setSuffix(" %")
        self.seller_discount.setValue(int(self.settings.get("sellerDefaultDiscount", 5)))
        self.official_discount.setValue(int(self.settings.get("officialDefaultDiscount", 6)))
        self.seller_max_discount = QSpinBox()
        self.official_max_discount = QSpinBox()
        for field in (self.seller_max_discount, self.official_max_discount):
            field.setRange(0, 90)
            field.setSpecialValueText("未设置")
            field.setSuffix(" %")
        self.seller_max_discount.setValue(_bounded_int(self.settings.get("sellerMaxDiscount"), 0, 0, 90))
        self.official_max_discount.setValue(_bounded_int(self.settings.get("officialMaxDiscount"), 0, 0, 90))
        form.addRow("自建默认折扣", self.seller_discount)
        form.addRow("官方默认折扣", self.official_discount)
        form.addRow("自建最高折扣", self.seller_max_discount)
        form.addRow("官方最高折扣", self.official_max_discount)
        note = QLabel("自动判断达到两项最高折扣后，本次完成；下一执行周期有已报名商品时批量取消。未设置时自动判断不会执行。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow("自动周期", note)
        return page

    def _stores_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        info = QLabel("原始名称用于识别账号；店铺名称用于日常显示。只勾选实际经营的站点。")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.store_table = QTableWidget(0, 2)
        self.store_table.setHorizontalHeaderLabels(["原始店铺名称", "店铺名称"])
        self.store_table.setSortingEnabled(False)
        # Keep the account identity readable and avoid a permanent editor from a single click.
        self.store_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.store_table.setItemDelegateForColumn(1, AliasEditorDelegate(self.store_table))
        header = self.store_table.horizontalHeader()
        header.setMinimumSectionSize(260)
        self.store_table.setColumnWidth(0, 340)
        for account in self.accounts:
            row = self.store_table.rowCount()
            self.store_table.insertRow(row)
            original_item = QTableWidgetItem(original_store_identifier(account))
            original_item.setFlags(original_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            original_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
            original_item.setToolTip(original_store_identifier(account))
            name_item = QTableWidgetItem(account.store_name)
            name_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
            name_item.setToolTip(account.store_name)
            self.store_table.setItem(row, 0, original_item)
            self.store_table.setItem(row, 1, name_item)
        self.store_table.setSortingEnabled(True)
        header.setStretchLastSection(True)
        layout.addWidget(self.store_table, 1)
        self.site_list = QListWidget()
        operating = self._initial_operating_sites
        for entry in self.operating_rows:
            self._upsert_site_entry(entry)
        account_names = {account.account_id: account.store_name for account in self.accounts}
        for account_id, site_ids in operating.items():
            for site_id in site_ids:
                self._upsert_site_entry({
                    "account_id": account_id,
                    "site_id": site_id,
                    "store_name": account_names.get(account_id, "当前店铺"),
                    "operating": True,
                })
        self._sort_site_entries()
        self.site_list.itemChanged.connect(self._site_selection_changed)
        layout.addWidget(QLabel("经营站点"))
        layout.addWidget(self.site_list, 1)
        return page

    def _site_selection_changed(self, _item: QListWidgetItem) -> None:
        if not self._merging_sites:
            self._site_selection_dirty = True

    def _upsert_site_entry(self, entry: dict[str, Any]) -> None:
        account_id = str(entry.get("account_id") or entry.get("accountId") or "")
        site_id = str(entry.get("site_id") or entry.get("siteId") or "").upper()
        if not account_id or not site_id:
            return
        key = (account_id, site_id)
        existing = next(
            (self.site_list.item(index) for index in range(self.site_list.count())
             if self.site_list.item(index).data(Qt.ItemDataRole.UserRole) == key),
            None,
        )
        store = str(entry.get("store_name") or entry.get("storeName") or "当前店铺")
        if existing is not None:
            existing.setText(f"{store} / {site_name(site_id)}")
            return
        item = QListWidgetItem(f"{store} / {site_name(site_id)}")
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        configured = self._initial_operating_sites.get(account_id)
        suggested = bool(entry.get("operating") or entry.get("suggested_operating"))
        checked = site_id in configured if configured is not None else suggested
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.site_list.addItem(item)

    def _sort_site_entries(self) -> None:
        items = [self.site_list.takeItem(0) for _ in range(self.site_list.count())]

        def sort_key(item: QListWidgetItem) -> tuple[str, str, str]:
            account_id, site_id = item.data(Qt.ItemDataRole.UserRole)
            store_name = item.text().partition(" / ")[0].strip()
            return store_name.casefold(), str(account_id), str(site_id)

        for item in sorted(items, key=sort_key):
            self.site_list.addItem(item)

    def apply_background_context(
        self,
        accounts: list[Account],
        operating_rows: list[dict[str, Any]],
        benchmark_text: str,
    ) -> None:
        sorting_enabled = self.store_table.isSortingEnabled()
        self.store_table.setSortingEnabled(False)
        rows_by_account = {
            str(self.store_table.item(row, 1).data(Qt.ItemDataRole.UserRole) or ""): row
            for row in range(self.store_table.rowCount())
            if self.store_table.item(row, 1)
        }
        for account in accounts:
            row = rows_by_account.get(account.account_id)
            if row is None:
                row = self.store_table.rowCount()
                self.store_table.insertRow(row)
                original_item = QTableWidgetItem(original_store_identifier(account))
                original_item.setFlags(original_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                original_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
                original_item.setToolTip(original_store_identifier(account))
                name_item = QTableWidgetItem(account.store_name)
                name_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
                name_item.setToolTip(account.store_name)
                self.store_table.setItem(row, 0, original_item)
                self.store_table.setItem(row, 1, name_item)
                self._initial_store_names[account.account_id] = account.store_name
            else:
                self.store_table.item(row, 0).setText(original_store_identifier(account))
                self.store_table.item(row, 0).setToolTip(original_store_identifier(account))
        self.store_table.setSortingEnabled(sorting_enabled)
        self._merging_sites = True
        try:
            for entry in operating_rows:
                self._upsert_site_entry(entry)
            self._sort_site_entries()
        finally:
            self._merging_sites = False
        self.benchmark_note.setText(benchmark_text)

    def _auth_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(f"已授权账号：{len(self.accounts)} 个"))
        form = QFormLayout()
        self.oauth_client_id = QLineEdit(str(self.settings.get("oauthClientId") or ""))
        self.oauth_client_secret = QLineEdit()
        self.oauth_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        if bool(self.settings.get("oauthClientSecretConfigured")):
            self.oauth_client_secret.setPlaceholderText("已保存，留空不修改")
        self.oauth_redirect_uri = QLineEdit(str(self.settings.get("oauthRedirectUri") or ""))
        self.webhook_callback_url = QLineEdit(str(self.settings.get("webhookCallbackUrl") or DEFAULT_WEBHOOK_CALLBACK_URL))
        self.webhook_callback_url.setPlaceholderText("请输入独立回调服务的公网通知地址")
        self.activity_callback_enabled = QCheckBox("启用活动变化回调接收")
        self.activity_callback_enabled.setChecked(bool(self.settings.get("activityCallbackEnabled")))
        self.activity_callback_application_id = QLineEdit(str(self.settings.get("activityCallbackApplicationId") or ""))
        self.activity_callback_application_id.setPlaceholderText("Mercado 应用 Client ID")
        self.activity_callback_secret_file = QLineEdit(str(self.settings.get("activityCallbackSecretFile") or ""))
        self.activity_callback_secret_file.setPlaceholderText("消费密钥文件路径（discount-webhook-consumer.secret）")
        self.activity_callback_claim_url = QLineEdit(str(self.settings.get("activityCallbackClaimUrl") or DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL))
        self.activity_callback_claim_url.setPlaceholderText("领取通知地址")
        self.activity_callback_ack_url = QLineEdit(str(self.settings.get("activityCallbackAckUrl") or DEFAULT_ACTIVITY_CALLBACK_ACK_URL))
        self.activity_callback_ack_url.setPlaceholderText("确认处理地址")
        form.addRow("美客多应用 Client ID", self.oauth_client_id)
        form.addRow("美客多应用 Client Secret", self.oauth_client_secret)
        form.addRow("OAuth 回调地址", self.oauth_redirect_uri)
        form.addRow("Webhook 通知地址", self.webhook_callback_url)
        form.addRow("活动回调", self.activity_callback_enabled)
        form.addRow("回调应用标识", self.activity_callback_application_id)
        form.addRow("回调共享密钥文件", self.activity_callback_secret_file)
        form.addRow("领取地址", self.activity_callback_claim_url)
        form.addRow("确认地址", self.activity_callback_ack_url)
        layout.addLayout(form)
        callback_note = QLabel("启用活动变化回调后，桌面程序会每 2 秒从领取地址拉取平台通知并自动处理（重新核对活动/商品缓存），处理成功后确认。Webhook 通知地址仅作记录，不用于接收。")
        callback_note.setObjectName("muted")
        callback_note.setWordWrap(True)
        layout.addWidget(callback_note)
        actions = QHBoxLayout()
        authorize = QPushButton("新增账号授权")
        refresh = QPushButton("刷新账号")
        authorize.clicked.connect(self.authorize_requested.emit)
        refresh.clicked.connect(self.refresh_requested.emit)
        actions.addWidget(authorize)
        actions.addWidget(refresh)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.callback_edit = QLineEdit()
        self.callback_edit.setPlaceholderText("粘贴浏览器授权完成后的回调链接")
        complete = QPushButton("完成授权")
        complete.clicked.connect(lambda: self.complete_authorization_requested.emit(self.callback_edit.text().strip()))
        layout.addWidget(self.callback_edit)
        layout.addWidget(complete)
        layout.addStretch(1)
        return page

    def _advanced_tab(self, benchmark_text: str) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.auth_dir = QLineEdit(str(self.settings.get("authDir") or ""))
        self.read_concurrency = QSpinBox()
        self.activity_concurrency = QSpinBox()
        self.write_concurrency = QSpinBox()
        self.read_concurrency.setRange(1, 125)
        self.activity_concurrency.setRange(1, 192)
        self.write_concurrency.setRange(1, 160)
        self.read_concurrency.setValue(_bounded_int(self.settings.get("readConcurrency"), 125, 1, 125))
        self.activity_concurrency.setValue(_bounded_int(self.settings.get("previewConcurrency"), 192, 1, 192))
        self.write_concurrency.setValue(_bounded_int(self.settings.get("writeConcurrency"), 160, 1, 160))
        form.addRow("授权目录", self.auth_dir)
        form.addRow("读取全局上限", self.read_concurrency)
        form.addRow("活动目录并发上限", self.activity_concurrency)
        form.addRow("商品写入全局上限", self.write_concurrency)
        self.benchmark_note = QLabel(benchmark_text)
        self.benchmark_note.setWordWrap(True)
        self.benchmark_note.setObjectName("muted")
        form.addRow("并发说明", self.benchmark_note)
        self._initial_field_values.update({
            "sellerDefaultDiscount": self.seller_discount.value(),
            "officialDefaultDiscount": self.official_discount.value(),
            "sellerMaxDiscount": self.seller_max_discount.value(),
            "officialMaxDiscount": self.official_max_discount.value(),
            "authDir": self.auth_dir.text(),
            "readConcurrency": self.read_concurrency.value(),
            "previewConcurrency": self.activity_concurrency.value(),
            "writeConcurrency": self.write_concurrency.value(),
            "oauthClientId": self.oauth_client_id.text(),
            "oauthRedirectUri": self.oauth_redirect_uri.text(),
            "webhookCallbackUrl": self.webhook_callback_url.text(),
            "activityCallbackEnabled": self.activity_callback_enabled.isChecked(),
            "activityCallbackApplicationId": self.activity_callback_application_id.text().strip(),
            "activityCallbackSecretFile": self.activity_callback_secret_file.text().strip(),
            "activityCallbackClaimUrl": self.activity_callback_claim_url.text().strip(),
            "activityCallbackAckUrl": self.activity_callback_ack_url.text().strip(),
        })
        return page

    def apply_settings_context(self, settings: dict[str, Any]) -> None:
        """Apply the latest normalized settings without overwriting a live edit."""
        if not isinstance(settings, dict):
            return
        self.settings = {**self.settings, **settings}
        fields = (
            ("sellerDefaultDiscount", self.seller_discount, _bounded_int(settings.get("sellerDefaultDiscount"), 5, 1, 90)),
            ("officialDefaultDiscount", self.official_discount, _bounded_int(settings.get("officialDefaultDiscount"), 6, 1, 90)),
            ("sellerMaxDiscount", self.seller_max_discount, _bounded_int(settings.get("sellerMaxDiscount"), 0, 0, 90)),
            ("officialMaxDiscount", self.official_max_discount, _bounded_int(settings.get("officialMaxDiscount"), 0, 0, 90)),
            ("authDir", self.auth_dir, str(settings.get("authDir") or "")),
            ("readConcurrency", self.read_concurrency, _bounded_int(settings.get("readConcurrency"), 125, 1, 125)),
            ("previewConcurrency", self.activity_concurrency, _bounded_int(settings.get("previewConcurrency"), 192, 1, 192)),
            ("writeConcurrency", self.write_concurrency, _bounded_int(settings.get("writeConcurrency"), 160, 1, 160)),
            ("oauthClientId", self.oauth_client_id, str(settings.get("oauthClientId") or "")),
            ("oauthRedirectUri", self.oauth_redirect_uri, str(settings.get("oauthRedirectUri") or "")),
            ("webhookCallbackUrl", self.webhook_callback_url, str(settings.get("webhookCallbackUrl") or DEFAULT_WEBHOOK_CALLBACK_URL)),
            ("activityCallbackEnabled", self.activity_callback_enabled, bool(settings.get("activityCallbackEnabled"))),
            ("activityCallbackApplicationId", self.activity_callback_application_id, str(settings.get("activityCallbackApplicationId") or "")),
            ("activityCallbackSecretFile", self.activity_callback_secret_file, str(settings.get("activityCallbackSecretFile") or "")),
            ("activityCallbackClaimUrl", self.activity_callback_claim_url, str(settings.get("activityCallbackClaimUrl") or DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL)),
            ("activityCallbackAckUrl", self.activity_callback_ack_url, str(settings.get("activityCallbackAckUrl") or DEFAULT_ACTIVITY_CALLBACK_ACK_URL)),
        )
        for key, field, value in fields:
            if isinstance(field, QCheckBox):
                current = field.isChecked()
                if current != self._initial_field_values.get(key):
                    continue
                with QSignalBlocker(field):
                    field.setChecked(bool(value))
                self._initial_field_values[key] = value
                continue
            current = field.value() if isinstance(field, QSpinBox) else field.text()
            if current != self._initial_field_values.get(key):
                continue
            with QSignalBlocker(field):
                if isinstance(field, QSpinBox):
                    field.setValue(int(value))
                else:
                    field.setText(str(value))
            self._initial_field_values[key] = value
        if bool(settings.get("oauthClientSecretConfigured")) and not self.oauth_client_secret.text():
            self.oauth_client_secret.setPlaceholderText("已保存，留空不修改")

    def values(self) -> dict[str, Any]:
        aliases = dict(self._initial_aliases)
        for row in range(self.store_table.rowCount()):
            name_item = self.store_table.item(row, 1)
            if not name_item:
                continue
            account_id = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            current_name = name_item.text().strip()
            if account_id and current_name != self._initial_store_names.get(account_id, ""):
                aliases[account_id] = current_name
        operating: dict[str, list[str]] = {
            account_id: list(site_ids)
            for account_id, site_ids in self._initial_operating_sites.items()
        }
        if self._site_selection_dirty:
            operating = {}
            for index in range(self.site_list.count()):
                item = self.site_list.item(index)
                account_id, site_id = item.data(Qt.ItemDataRole.UserRole)
                operating.setdefault(str(account_id), [])
                if item.checkState() == Qt.CheckState.Checked:
                    operating[str(account_id)].append(str(site_id))
        return {
            "authDir": self.auth_dir.text().strip(),
            "outputDir": str(self.settings.get("outputDir") or ""),
            "sellerDefaultDiscount": self.seller_discount.value(),
            "officialDefaultDiscount": self.official_discount.value(),
            "sellerMaxDiscount": self.seller_max_discount.value() or None,
            "officialMaxDiscount": self.official_max_discount.value() or None,
            "readConcurrency": self.read_concurrency.value(),
            "previewConcurrency": self.activity_concurrency.value(),
            "writeConcurrency": self.write_concurrency.value(),
            "oauthClientId": self.oauth_client_id.text().strip(),
            "oauthClientSecret": self.oauth_client_secret.text(),
            "oauthRedirectUri": self.oauth_redirect_uri.text().strip(),
            "webhookCallbackUrl": self.webhook_callback_url.text().strip(),
            "activityCallbackEnabled": self.activity_callback_enabled.isChecked(),
            "activityCallbackApplicationId": self.activity_callback_application_id.text().strip(),
            "activityCallbackSecretFile": self.activity_callback_secret_file.text().strip(),
            "activityCallbackClaimUrl": self.activity_callback_claim_url.text().strip(),
            "activityCallbackAckUrl": self.activity_callback_ack_url.text().strip(),
            "storeAliases": aliases,
            "operatingSites": operating,
        }

    def accept(self) -> None:
        names: list[str] = []
        for row in range(self.store_table.rowCount()):
            item = self.store_table.item(row, 1)
            name = item.text().strip() if item else ""
            if not name:
                QMessageBox.warning(self, "店铺名称", "店铺名称不能为空，请填写后再保存。")
                return
            names.append(name)
        normalized = [name.casefold() for name in names]
        if len(set(normalized)) != len(normalized):
            QMessageBox.warning(self, "店铺名称", "店铺名称不能重复，请为每个账号填写不同名称。")
            return
        super().accept()


def original_store_identifier(account: Account) -> str:
    display_name = str(account.raw_display_name or "").strip()
    if display_name and not display_name.startswith("账号 ") and display_name != "未命名店铺":
        return display_name
    suffix = account.account_id[-4:] if account.account_id else "未知"
    return f"本地授权账号（尾号 {suffix}）"


def _bounded_int(value: object, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def target_account_id(target: dict[str, Any]) -> str:
    return str(target.get("account_id") or target.get("accountId") or "")


def target_site_id(target: dict[str, Any]) -> str:
    return str(target.get("site_id") or target.get("siteId") or "")


def target_label(target: dict[str, Any]) -> str:
    store = str(target.get("store_name") or target.get("storeName") or "当前店铺")
    site = str(target.get("site_name") or target.get("siteName") or site_name(target_site_id(target)))
    return f"{store} / {site}"
