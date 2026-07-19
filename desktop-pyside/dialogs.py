from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import Account, site_name


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
        note = QLabel("只创建 SELLER_CAMPAIGN 自建活动；所选目标会合并到唯一的最终执行摘要中确认。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("加入最终摘要")
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
        form.addRow("自建默认折扣", self.seller_discount)
        form.addRow("官方默认折扣", self.official_discount)
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
        for account in self.accounts:
            row = self.store_table.rowCount()
            self.store_table.insertRow(row)
            original_item = QTableWidgetItem(original_store_identifier(account))
            original_item.setFlags(original_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            original_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
            name_item = QTableWidgetItem(account.store_name)
            name_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
            self.store_table.setItem(row, 0, original_item)
            self.store_table.setItem(row, 1, name_item)
        self.store_table.setSortingEnabled(True)
        self.store_table.horizontalHeader().setStretchLastSection(True)
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
                name_item = QTableWidgetItem(account.store_name)
                name_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
                self.store_table.setItem(row, 0, original_item)
                self.store_table.setItem(row, 1, name_item)
                self._initial_store_names[account.account_id] = account.store_name
            else:
                self.store_table.item(row, 0).setText(original_store_identifier(account))
        self.store_table.setSortingEnabled(sorting_enabled)
        self._merging_sites = True
        try:
            for entry in operating_rows:
                self._upsert_site_entry(entry)
        finally:
            self._merging_sites = False
        self.benchmark_note.setText(benchmark_text)

    def _auth_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(f"已授权账号：{len(self.accounts)} 个"))
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
        self.read_concurrency.setRange(1, 1000)
        self.activity_concurrency.setRange(1, 1000)
        self.write_concurrency.setRange(1, 10000)
        self.read_concurrency.setValue(int(self.settings.get("readConcurrency", 2)))
        self.activity_concurrency.setValue(int(self.settings.get("previewConcurrency", 2)))
        self.write_concurrency.setValue(int(self.settings.get("writeConcurrency", 2)))
        form.addRow("授权目录", self.auth_dir)
        form.addRow("读取并发（当前使用值）", self.read_concurrency)
        form.addRow("活动并发（当前使用值）", self.activity_concurrency)
        form.addRow("商品写入并发（当前使用值）", self.write_concurrency)
        self.benchmark_note = QLabel(benchmark_text)
        self.benchmark_note.setWordWrap(True)
        self.benchmark_note.setObjectName("muted")
        form.addRow("并发说明", self.benchmark_note)
        return page

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
            "readConcurrency": self.read_concurrency.value(),
            "previewConcurrency": self.activity_concurrency.value(),
            "writeConcurrency": self.write_concurrency.value(),
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


def target_account_id(target: dict[str, Any]) -> str:
    return str(target.get("account_id") or target.get("accountId") or "")


def target_site_id(target: dict[str, Any]) -> str:
    return str(target.get("site_id") or target.get("siteId") or "")


def target_label(target: dict[str, Any]) -> str:
    store = str(target.get("store_name") or target.get("storeName") or "当前店铺")
    site = str(target.get("site_name") or target.get("siteName") or site_name(target_site_id(target)))
    return f"{store} / {site}"
