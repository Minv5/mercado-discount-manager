from __future__ import annotations

import calendar
import dataclasses
import json
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from PySide6.QtCore import QDate, QModelIndex, QObject, QSettings, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QPainter, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
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
    DEFAULT_OAUTH_REDIRECT_URI,
    DEFAULT_WEBHOOK_CALLBACK_URL,
    migrate_oauth_redirect_uri,
)
from core import Account, parse_targeted_cancel_item_ids, site_name
from reason_text import business_reason_text


class AliasEditorDelegate(QStyledItemDelegate):
    """Keeps the compact table editor readable without ghosting or double text."""

    def __init__(self, table: QTableWidget):
        super().__init__(table)
        self._table = table

    def createEditor(self, parent: QWidget, option: Any, _index: Any) -> QLineEdit:
        editor = QLineEdit(parent)
        editor.setFont(self._table.font())
        editor.setFrame(False)
        is_selected = bool(hasattr(option, "state") and option.state & QStyle.StateFlag.State_Selected)
        bg = "#2E6930" if is_selected else "#14120F"
        editor.setStyleSheet(
            f"QLineEdit {{ padding: 0 4px; border: 0; background: {bg}; color: #F6F3EA; "
            f"selection-background-color: #1E4620; selection-color: #F6F3EA; }}"
        )
        return editor

    def updateEditorGeometry(self, editor: QWidget, option: Any, _index: Any) -> None:
        editor.setGeometry(option.rect.adjusted(1, 1, -1, -1))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # Avoid double-rendering / ghosting: when this cell is actively being edited, suppress
        # painting the underlying item text so it does not overlap with the QLineEdit editor widget.
        is_editing = (
            self._table.state() == QAbstractItemView.State.EditingState
            and self._table.currentIndex() == index
        ) or any(
            c.isVisible() and option.rect.intersects(c.geometry())
            for c in self._table.viewport().findChildren(QLineEdit)
        )
        if is_editing:
            opt.text = ""
        super().paint(painter, opt, index)


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


TARGETED_ITEM_HISTORY_KEY = "targeted_item_history_v1"
TARGETED_LAST_CANCELED_KEY = "targeted_last_canceled_v1"


def _is_mock_test_batch(entry: dict[str, Any]) -> bool:
    item_ids = entry.get("item_ids") or []
    if not item_ids:
        return True
    return False


def _discover_batches_from_job_states() -> list[dict[str, Any]]:
    import os
    from pathlib import Path
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "Library" / "Application Support"))
    job_dir = local / "MercadoDiscountManagerStandalone" / "data" / "execution-job-states"
    if not job_dir.exists():
        return []
    batches: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for f in sorted(job_dir.glob("*.json"), key=os.path.getmtime, reverse=True):
        try:
            d = json.loads(f.read_text("utf-8"))
            req = d.get("request") or {}
            action = req.get("action")
            items = req.get("itemIds") or []
            if action and items and isinstance(items, list):
                clean_items = [str(x).strip().upper() for x in items if len(str(x).strip()) >= 8]
                if not clean_items:
                    continue
                tuple_key = tuple(clean_items)
                if tuple_key in seen:
                    continue
                seen.add(tuple_key)
                mtime = f.stat().st_mtime
                t_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                batches.append({
                    "time": t_str,
                    "action": action,
                    "action_label": "报名" if action == "enroll" else "取消",
                    "count": len(clean_items),
                    "item_ids": clean_items,
                })
        except Exception:
            continue
    return batches


def load_targeted_item_history() -> list[dict[str, Any]]:
    settings = QSettings("MercadoDiscountManager", "TargetedItemAction")
    raw = settings.value(TARGETED_ITEM_HISTORY_KEY, "[]")
    history: list[dict[str, Any]] = []
    try:
        data = json.loads(str(raw))
        if isinstance(data, list):
            history = [h for h in data if isinstance(h, dict) and not _is_mock_test_batch(h)]
    except Exception:
        history = []

    job_batches = _discover_batches_from_job_states()
    if job_batches:
        existing_signatures = {tuple(h.get("item_ids", [])) for h in history}
        for b in job_batches:
            sig = tuple(b.get("item_ids", []))
            if sig not in existing_signatures:
                history.append(b)
                existing_signatures.add(sig)
    return history[:20]


def save_targeted_item_batch(action: str, item_ids: list[str]) -> None:
    if not item_ids:
        return
    history = load_targeted_item_history()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "time": now_str,
        "action": action,
        "action_label": "报名" if action == "enroll" else ("刷新" if action == "refresh_cache" else "取消"),
        "count": len(item_ids),
        "item_ids": item_ids,
    }
    history = [h for h in history if h.get("item_ids") != item_ids and not _is_mock_test_batch(h)]
    history.insert(0, entry)
    history = history[:20]
    settings = QSettings("MercadoDiscountManager", "TargetedItemAction")
    settings.setValue(TARGETED_ITEM_HISTORY_KEY, json.dumps(history, ensure_ascii=False))
    if action == "cancel":
        settings.setValue(TARGETED_LAST_CANCELED_KEY, json.dumps(entry, ensure_ascii=False))


def get_last_canceled_batch() -> dict[str, Any] | None:
    settings = QSettings("MercadoDiscountManager", "TargetedItemAction")
    raw = settings.value(TARGETED_LAST_CANCELED_KEY, "")
    if raw:
        try:
            data = json.loads(str(raw))
            if isinstance(data, dict) and not _is_mock_test_batch(data) and data.get("item_ids"):
                return data
        except Exception:
            pass
    for h in load_targeted_item_history():
        if h.get("action") == "cancel" and h.get("item_ids") and not _is_mock_test_batch(h):
            return h
    return None


class MultiBatchHistoryDialog(QDialog):
    """Allows user to select multiple historical batches to merge and load."""

    def __init__(
        self,
        history: list[dict[str, Any]],
        existing_items: list[str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("多选 / 合并历史批次")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMinimumHeight(440)
        self.history = history
        self.existing_items = existing_items or []
        self.selected_item_ids: list[str] = []
        self.selected_batch_count: int = 0
        self.all_cancel: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        heading = QLabel("选择需要载入的历史批次（支持多选）")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        hint = QLabel("勾选多个历史批次后将自动合并并去重商品 ID，方便批量重新报名或处理。")
        hint.setStyleSheet("color: #AFA89B;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(10)
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setFixedHeight(28)
        self.select_all_btn.clicked.connect(self._select_all)
        quick_row.addWidget(self.select_all_btn)

        self.clear_sel_btn = QPushButton("清空选择")
        self.clear_sel_btn.setFixedHeight(28)
        self.clear_sel_btn.clicked.connect(self._clear_selection)
        quick_row.addWidget(self.clear_sel_btn)
        quick_row.addStretch(1)
        layout.addLayout(quick_row)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget { background: #18201C; border: 1px solid #4E472F; border-radius: 6px; padding: 4px; }"
            "QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #2A3630; }"
            "QListWidget::item:hover { background: #233029; }"
        )
        for h in self.history:
            time_part = str(h.get("time", ""))[:16]
            action_label = h.get("action_label", "处理")
            count = h.get("count", len(h.get("item_ids", [])))
            item = QListWidgetItem(f"{time_part}   [{action_label}]   {count} 个商品")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, h)
            self.list_widget.addItem(item)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list_widget, 1)

        self.summary_label = QLabel("已选择：0 个批次｜合并商品：0 个")
        self.summary_label.setStyleSheet("color: #AFA89B; font-weight: 500;")
        layout.addWidget(self.summary_label)

        if self.existing_items:
            self.append_check = QCheckBox(f"追加合并到当前输入框已有的 {len(self.existing_items)} 个商品（去重）")
            self.append_check.setChecked(True)
            self.append_check.toggled.connect(self._update_summary)
            layout.addWidget(self.append_check)
        else:
            self.append_check = None

        buttons = QDialogButtonBox()
        self.submit_btn = buttons.addButton("确认载入", QDialogButtonBox.ButtonRole.AcceptRole)
        self.submit_btn.setObjectName("primary")
        self.submit_btn.setEnabled(False)
        self.cancel_btn = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.reject)

    def _select_all(self) -> None:
        blocker = QSignalBlocker(self.list_widget)
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Checked)
        del blocker
        self._update_summary()

    def _clear_selection(self) -> None:
        blocker = QSignalBlocker(self.list_widget)
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        del blocker
        self._update_summary()

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_summary()

    def _get_checked_batches(self) -> list[dict[str, Any]]:
        batches = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                b = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(b, dict):
                    batches.append(b)
        return batches

    def _compute_unique_items(self) -> list[str]:
        batches = self._get_checked_batches()
        all_ids: list[str] = []
        if self.append_check and self.append_check.isChecked():
            all_ids.extend(self.existing_items)
        for b in batches:
            all_ids.extend(b.get("item_ids", []))
        seen = set()
        unique_ids = []
        for iid in all_ids:
            clean = str(iid).strip().upper()
            if clean and clean not in seen:
                seen.add(clean)
                unique_ids.append(clean)
        return unique_ids

    def _update_summary(self) -> None:
        batches = self._get_checked_batches()
        unique_ids = self._compute_unique_items()
        count = len(unique_ids)
        batch_count = len(batches)
        self.submit_btn.setEnabled(batch_count > 0)
        self.submit_btn.setText(f"确认载入 ({count} 个商品)" if count > 0 else "确认载入")

        if count > 0:
            self.summary_label.setText(f"已选 {batch_count} 个批次，去重后共 {count} 个商品 ID")
            self.summary_label.setStyleSheet("color: #81C784; font-weight: 500;")
        else:
            self.summary_label.setText("已选择：0 个批次｜合并商品：0 个")
            self.summary_label.setStyleSheet("color: #AFA89B; font-weight: 500;")

    def _confirm(self) -> None:
        batches = self._get_checked_batches()
        if not batches:
            self.reject()
            return
        self.selected_item_ids = self._compute_unique_items()
        self.selected_batch_count = len(batches)
        self.all_cancel = all(b.get("action") == "cancel" for b in batches)
        self.accept()


class LogViewer(QTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.auto_scroll = True
        self.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

    def _on_scroll_changed(self, value: int) -> None:
        scrollbar = self.verticalScrollBar()
        if scrollbar.isSliderDown() or value < (scrollbar.maximum() - 15):
            self.auto_scroll = False
        elif value >= (scrollbar.maximum() - 5):
            self.auto_scroll = True

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() > 0:
            self.auto_scroll = False
        super().wheelEvent(event)

    def append_log_line(self, line: str) -> None:
        scrollbar = self.verticalScrollBar()
        was_at_bottom = self.auto_scroll and not scrollbar.isSliderDown()

        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.document().isEmpty():
            cursor.insertText(line)
        else:
            cursor.insertBlock()
            cursor.insertText(line)

        # 仅在吸底状态下安全修剪超出 1000 行的旧记录；用户向上翻阅时绝对不削减，防止视口跳动
        if was_at_bottom:
            excess = self.document().blockCount() - 1000
            if excess > 0:
                prune_cursor = QTextCursor(self.document())
                prune_cursor.movePosition(QTextCursor.MoveOperation.Start)
                for _ in range(excess):
                    prune_cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                    prune_cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor)
                prune_cursor.removeSelectedText()
            scrollbar.setValue(scrollbar.maximum())


class TargetedCancelDialog(QDialog):
    submitted = Signal(list, str)
    back_requested = Signal()

    def __init__(
        self,
        scope_text: str,
        parent: QWidget | None = None,
        submission_ready: Callable[[], bool] | None = None,
        seller_discount: int = 0,
        official_discount: int = 0,
        embedded: bool = False,
    ):
        self.embedded = embedded
        if embedded:
            super().__init__(parent, Qt.WindowType.Widget)
        else:
            super().__init__(parent)
            self.setWindowTitle("按商品 ID 操作活动")
            self.setModal(True)
            self.resize(760, 620)
            self.setMinimumSize(660, 500)
        self._item_ids: list[str] = []

        layout = QVBoxLayout(self)
        if self.embedded:
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(10)
        else:
            layout.setContentsMargins(20, 18, 20, 16)
            layout.setSpacing(12)

        # 1. 顶部操作控制卡片
        control_card = QFrame()
        control_card.setObjectName("settingsSection")
        card_layout = QVBoxLayout(control_card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        # 第一行：标题 + 操作类型 + 折扣状态 + 主执行按钮
        row1 = QHBoxLayout()
        row1.setSpacing(12)

        heading = QLabel("按商品 ID 操作活动")
        heading.setObjectName("sectionTitle")
        row1.addWidget(heading)

        row1.addSpacing(6)
        action_label = QLabel("操作类型")
        action_label.setObjectName("muted")
        row1.addWidget(action_label)

        self.action_combo = QComboBox()
        self.action_combo.addItem("报名活动", "enroll")
        self.action_combo.addItem("取消活动", "cancel")
        self.action_combo.addItem("刷新商品缓存", "refresh_cache")
        self.action_combo.setFixedHeight(34)
        self.action_combo.setMinimumWidth(130)
        self.action_combo.currentIndexChanged.connect(self._sync_submit_state)
        row1.addWidget(self.action_combo)

        self.discount_note = QLabel()
        self.discount_note.setWordWrap(False)
        self.discount_note.setStyleSheet("color: #E6E2D8; font-weight: 500;")
        row1.addWidget(self.discount_note)
        self._seller_discount = int(seller_discount)
        self._official_discount = int(official_discount)

        row1.addStretch(1)

        self.submit_button = QPushButton("开始核对并报名")
        self.submit_button.setObjectName("primary")
        self.submit_button.setFixedHeight(34)
        self.submit_button.setMinimumWidth(140)
        self.submit_button.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.submit_button.clicked.connect(self._validate_and_accept)
        row1.addWidget(self.submit_button)

        card_layout.addLayout(row1)

        # 第二行：核对范围 + 历史批次快速工具栏
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        self.scope_label = QLabel(f"核对范围：{scope_text}")
        self.scope_label.setWordWrap(True)
        self.scope_label.setStyleSheet("color: #C8C3B7; font-size: 13px; line-height: 1.4;")
        row2.addWidget(self.scope_label, 1)

        self.count_label = QLabel("已输入：0 个商品")
        self.count_label.setStyleSheet("color: #C8C3B7; font-size: 13px;")
        row2.addWidget(self.count_label)

        last_canceled = get_last_canceled_batch()
        last_count_text = f" ({last_canceled['count']}个)" if last_canceled and last_canceled.get("count") else ""
        self.load_last_canceled_btn = QPushButton(f"载入上次取消{last_count_text}")
        self.load_last_canceled_btn.setFixedHeight(30)
        self.load_last_canceled_btn.setEnabled(bool(last_canceled and last_canceled.get("item_ids")))
        self.load_last_canceled_btn.clicked.connect(self._load_last_canceled_items)
        row2.addWidget(self.load_last_canceled_btn)

        self.history_combo = QComboBox()
        self.history_combo.setFixedHeight(30)
        self.history_combo.setMinimumWidth(160)
        self.history_combo.view().setMinimumWidth(240)
        self._refresh_history_combo()
        self.history_combo.currentIndexChanged.connect(self._on_history_selected)
        row2.addWidget(self.history_combo)

        self.multi_batch_btn = QPushButton("多选批次...")
        self.multi_batch_btn.setFixedHeight(30)
        self.multi_batch_btn.clicked.connect(self._open_multi_batch_dialog)
        row2.addWidget(self.multi_batch_btn)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.setFixedHeight(30)
        self.clear_btn.clicked.connect(self._clear_input)
        row2.addWidget(self.clear_btn)

        card_layout.addLayout(row2)
        layout.addWidget(control_card)

        # 2. 中部与下部：垂直切分输入区与专属运行日志区
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        input_container = QWidget()
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(0, 2, 0, 2)
        input_layout.setSpacing(6)

        self.item_input = QPlainTextEdit()
        font = QFont("Menlo", 12)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.item_input.setFont(font)
        self.item_input.setPlaceholderText("每行一个商品 ID，例如：\nMLB4730089499\nMLM5615657734")
        self.item_input.setStyleSheet(
            "QPlainTextEdit { "
            "padding: 10px 12px; "
            "font-family: Menlo, Monaco, Consolas, monospace; "
            "font-size: 13px; "
            "line-height: 1.5; "
            "background: #18201C; "
            "color: #E6E2D8; "
            "border: 1px solid #4E472F; "
            "border-radius: 6px; "
            "}"
        )
        self.item_input.textChanged.connect(self._sync_item_count)
        input_layout.addWidget(self.item_input, 1)

        bottom_hint_layout = QHBoxLayout()
        bottom_hint_layout.setContentsMargins(2, 0, 2, 0)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName("muted")
        self.note.setStyleSheet("color: #C8C3B7; font-size: 13px; line-height: 1.4;")
        bottom_hint_layout.addWidget(self.note, 1)

        self.operation_hint = QLabel()
        self.operation_hint.setWordWrap(True)
        self.operation_hint.setStyleSheet("color: #81C784; font-size: 13px;")
        self.operation_hint.setVisible(False)
        bottom_hint_layout.addWidget(self.operation_hint)

        input_layout.addLayout(bottom_hint_layout)
        splitter.addWidget(input_container)

        # 专属运行日志区（平铺无嵌套边框，与主工作区完全一致）
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(6)

        log_header = QHBoxLayout()
        log_title = QLabel("按ID操作运行日志")
        log_title.setObjectName("sectionTitle")
        clear_log_btn = QPushButton("清空日志")
        clear_log_btn.setFixedHeight(28)
        clear_log_btn.setStyleSheet("padding: 2px 12px; font-size: 12px;")
        clear_log_btn.clicked.connect(lambda: self.log_box.clear())
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)

        self.log_box = LogViewer()
        log_layout.addWidget(self.log_box, 1)
        splitter.addWidget(log_frame)

        splitter.setSizes([320, 200])
        layout.addWidget(splitter, 1)

        # 隐藏保留 cancel_button 以兼容已有测试与信号调用
        self.cancel_button = QPushButton("返回工作台", self)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.back_requested.emit if self.embedded else self.reject)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.cancel_button.click)

        self._submission_ready = submission_ready
        self._readiness_timer = QTimer(self)
        self._readiness_timer.setInterval(500)
        self._readiness_timer.timeout.connect(self._sync_submit_state)
        if submission_ready is not None:
            self._readiness_timer.start()
        self._sync_submit_state()
        self._sync_item_count()

    def _refresh_history_combo(self) -> None:
        history = load_targeted_item_history()
        blocker = QSignalBlocker(self.history_combo)
        self.history_combo.clear()
        self.history_combo.addItem(f"历史批次 ({len(history)}条)...", None)
        if len(history) > 1:
            self.history_combo.addItem("☑️ 多选 / 合并批次...", "__multi_select__")
        for h in history:
            time_part = str(h.get("time", ""))[5:16]
            action_label = h.get("action_label", "处理")
            count = h.get("count", len(h.get("item_ids", [])))
            self.history_combo.addItem(f"{time_part} {action_label} {count}个", h)
        del blocker
        if hasattr(self, "multi_batch_btn"):
            self.multi_batch_btn.setEnabled(len(history) > 0)

    def _open_multi_batch_dialog(self) -> None:
        history = load_targeted_item_history()
        if not history:
            QMessageBox.information(self, "多选历史批次", "暂无历史批次记录。")
            return
        current_text = self.item_input.toPlainText().strip()
        existing_items = []
        if current_text:
            try:
                existing_items = parse_targeted_cancel_item_ids(current_text, max_items=999999)
            except Exception:
                existing_items = [line.strip().upper() for line in current_text.splitlines() if line.strip()]

        dlg = MultiBatchHistoryDialog(history, existing_items=existing_items, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_item_ids:
            self.item_input.setPlainText("\n".join(dlg.selected_item_ids))
            if dlg.all_cancel:
                enroll_idx = self.action_combo.findData("enroll")
                if enroll_idx >= 0:
                    self.action_combo.setCurrentIndex(enroll_idx)
            count = len(dlg.selected_item_ids)
            b_count = dlg.selected_batch_count
            self.operation_hint.setText(f"已成功合并载入 {b_count} 个历史批次，去重后共 {count} 个商品 ID。")
            self.operation_hint.setVisible(True)

    def _sync_item_count(self) -> None:
        text = self.item_input.toPlainText().strip()
        if not text:
            self.count_label.setText("已输入：0 个商品")
            self.count_label.setStyleSheet("color: #C8C3B7; font-size: 13px;")
            return
        try:
            items = parse_targeted_cancel_item_ids(text, max_items=999999)
            count = len(items)
            self.count_label.setText(f"已输入：{count} 个商品 ID")
            self.count_label.setStyleSheet("color: #81C784; font-weight: 500; font-size: 13px;")
        except ValueError as err:
            err_text = str(err)
            if "格式不正确" in err_text:
                self.count_label.setText("输入包含无效字符或格式错误")
            else:
                self.count_label.setText("输入内容有误")
            self.count_label.setStyleSheet("color: #E57373; font-size: 13px;")
        except Exception:
            self.count_label.setText("输入包含无效字符")
            self.count_label.setStyleSheet("color: #E57373; font-size: 13px;")

    def _load_last_canceled_items(self) -> None:
        last = get_last_canceled_batch()
        if not last or not last.get("item_ids"):
            QMessageBox.information(self, "按商品 ID 操作活动", "暂无上次取消的商品记录。")
            return
        item_ids = list(last["item_ids"])
        self.item_input.setPlainText("\n".join(item_ids))
        enroll_idx = self.action_combo.findData("enroll")
        if enroll_idx >= 0:
            self.action_combo.setCurrentIndex(enroll_idx)
        count = len(item_ids)
        time_str = last.get("time", "")
        self.operation_hint.setText(f"已自动载入上次（{time_str}）取消的 {count} 个商品 ID，并已自动切换操作类型为「报名活动」。")
        self.operation_hint.setVisible(True)

    def _on_history_selected(self, index: int) -> None:
        if index <= 0:
            return
        data = self.history_combo.itemData(index)
        blocker = QSignalBlocker(self.history_combo)
        self.history_combo.setCurrentIndex(0)
        del blocker

        if data == "__multi_select__":
            self._open_multi_batch_dialog()
            return

        batch = data
        if not isinstance(batch, dict) or not batch.get("item_ids"):
            return

        new_ids = list(batch["item_ids"])
        current_text = self.item_input.toPlainText().strip()
        if current_text:
            try:
                curr_ids = parse_targeted_cancel_item_ids(current_text, max_items=999999)
            except Exception:
                curr_ids = [line.strip().upper() for line in current_text.splitlines() if line.strip()]

            reply = QMessageBox.question(
                self,
                "载入历史批次",
                f"当前输入框已有 {len(curr_ids)} 个商品。\n是否将本次选择的 {len(new_ids)} 个商品追加合并到现有输入？\n\n・选择「Yes」追加合并（自动去重）\n・选择「No」替换当前输入",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes:
                seen = set(curr_ids)
                merged = list(curr_ids)
                for nid in new_ids:
                    clean = str(nid).strip().upper()
                    if clean and clean not in seen:
                        seen.add(clean)
                        merged.append(clean)
                self.item_input.setPlainText("\n".join(merged))
                count = len(merged)
                time_str = batch.get("time", "")
                self.operation_hint.setText(f"已追加载入批次（{time_str}），合并去重后共 {count} 个商品。")
                self.operation_hint.setVisible(True)
                return

        self.item_input.setPlainText("\n".join(new_ids))
        batch_action = batch.get("action")
        if batch_action == "cancel":
            enroll_idx = self.action_combo.findData("enroll")
            if enroll_idx >= 0:
                self.action_combo.setCurrentIndex(enroll_idx)
        count = len(new_ids)
        time_str = batch.get("time", "")
        action_label = batch.get("action_label", "处理")
        self.operation_hint.setText(f"已载入历史批次（{time_str}，原{action_label} {count} 个商品）。")
        self.operation_hint.setVisible(True)

    def _clear_input(self) -> None:
        self.item_input.clear()
        self.operation_hint.setText("输入已清空。")
        self.operation_hint.setVisible(True)

    def _sync_submit_state(self) -> None:
        ready = True if self._submission_ready is None else bool(self._submission_ready())
        act = self.action()
        if act == "enroll":
            self.discount_note.setVisible(True)
            self.discount_note.setText(
                f"本次报名折扣已锁定：自建活动 {self._seller_discount}%｜官方活动 {self._official_discount}%"
            )
            self.submit_button.setText("开始核对并报名")
            self.note.setText(
                "点击后只读取命中的活动；存在匹配商品时会直接提交报名，不再弹出第二个确认框。"
                if ready else
                "缓存补偿仍在运行。可以先填写商品 ID；补偿结束后本按钮会自动启用，也可以关闭窗口后用主按钮停止补偿。"
            )
            self.submit_button.setEnabled(ready)
        elif act == "cancel":
            self.discount_note.setVisible(False)
            self.discount_note.setText("取消不使用折扣。")
            self.submit_button.setText("开始核对并取消")
            self.note.setText(
                "点击后只读取命中的活动；存在匹配商品时会直接提交取消，不再弹出第二个确认框。"
                if ready else
                "缓存补偿仍在运行。可以先填写商品 ID；补偿结束后本按钮会自动启用，也可以关闭窗口后用主按钮停止补偿。"
            )
            self.submit_button.setEnabled(ready)
        else:
            self.discount_note.setVisible(False)
            self.discount_note.setText("刷新商品缓存不使用折扣。")
            self.submit_button.setText("开始刷新商品缓存")
            self.note.setText(
                "点击后将直接向美客多官方接口读取这些商品的最新价格、重量、尺寸及可报名活动资格，并立即写入本地数据缓存。"
            )
            self.submit_button.setEnabled(True)

    def _validate_and_accept(self) -> None:
        try:
            self._item_ids = parse_targeted_cancel_item_ids(self.item_input.toPlainText())
        except ValueError as error:
            QMessageBox.information(self, "按商品 ID 操作活动", str(error))
            return
        save_targeted_item_batch(self.action(), self._item_ids)
        if self.embedded:
            self.submitted.emit(self._item_ids, self.action())
        else:
            self.accept()

    def update_scope(self, scope_text: str, seller_discount: int, official_discount: int) -> None:
        if hasattr(self, "scope_label"):
            self.scope_label.setText(f"核对范围：{scope_text}")
        self._seller_discount = int(seller_discount)
        self._official_discount = int(official_discount)
        self._refresh_history_combo()
        self._sync_submit_state()

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
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None, embedded: bool = False):
        self.embedded = embedded
        if embedded:
            super().__init__(parent, Qt.WindowType.Widget)
        else:
            super().__init__(parent)
            self.setWindowTitle("商品查询")
            self.resize(840, 620)
            self.setMinimumSize(680, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 1. 顶部查询控制卡片
        control_card = QFrame()
        control_card.setObjectName("settingsSection")
        card_layout = QVBoxLayout(control_card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        heading = QLabel("商品查询与操作溯源")
        heading.setObjectName("sectionTitle")
        header_row.addWidget(heading)

        sub_label = QLabel("输入商品 ID 即时查询当前参与活动、价格快照及历史报名/取消日志")
        sub_label.setObjectName("muted")
        header_row.addWidget(sub_label)
        header_row.addStretch(1)
        card_layout.addLayout(header_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.item_input = QLineEdit()
        self.item_input.setPlaceholderText("输入商品 ID，如 MLB7258072116（按回车或点击查询）")
        self.item_input.setFixedHeight(34)
        self.item_input.setStyleSheet("font-family: Menlo, Monaco, Consolas, monospace; font-size: 10.5pt;")
        self.item_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self.item_input, 1)

        self.search_button = QPushButton("查  询")
        self.search_button.setObjectName("primary")
        self.search_button.setFixedSize(90, 34)
        self.search_button.clicked.connect(self._on_search)
        search_row.addWidget(self.search_button)

        self.clear_button = QPushButton("清  空")
        self.clear_button.setFixedSize(80, 34)
        self.clear_button.clicked.connect(self._clear_search)
        search_row.addWidget(self.clear_button)
        card_layout.addLayout(search_row)

        root.addWidget(control_card)

        # 2. 中部与下部垂直分割：操作历史明细表与专属价格运行日志
        splitter = QSplitter(Qt.Orientation.Vertical)

        history_card = QFrame()
        history_card.setObjectName("settingsSection")
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(16, 12, 16, 12)
        history_layout.setSpacing(8)

        table_header = QHBoxLayout()
        history_label = QLabel("操作历史明细")
        history_label.setObjectName("sectionTitle")
        table_header.addWidget(history_label)

        self.history_count_label = QLabel("0 条记录")
        self.history_count_label.setObjectName("muted")
        table_header.addWidget(self.history_count_label)
        table_header.addStretch(1)
        history_layout.addLayout(table_header)

        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(["时间", "店铺", "活动", "动作", "状态", "价格", "说明"])
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setShowGrid(True)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.verticalHeader().setDefaultSectionSize(32)
        header = self.history_table.horizontalHeader()
        header.setMinimumSectionSize(54)
        for col in range(6):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.history_table.setColumnWidth(0, 150)
        self.history_table.setColumnWidth(1, 140)
        self.history_table.setColumnWidth(2, 220)
        self.history_table.setColumnWidth(3, 80)
        self.history_table.setColumnWidth(4, 80)
        self.history_table.setColumnWidth(5, 90)
        history_layout.addWidget(self.history_table, 1)

        splitter.addWidget(history_card)

        # 3. 专属价格查询运行日志
        log_card = QFrame()
        log_card.setObjectName("settingsSection")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 12, 16, 12)
        log_layout.setSpacing(8)

        log_header = QHBoxLayout()
        log_title = QLabel("价格查询运行日志")
        log_title.setObjectName("sectionTitle")
        clear_log_btn = QPushButton("清空日志")
        clear_log_btn.setFixedHeight(28)
        clear_log_btn.setStyleSheet("padding: 2px 12px; font-size: 12px;")
        clear_log_btn.clicked.connect(self._clear_logs)
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(clear_log_btn)
        log_layout.addLayout(log_header)

        self.log_box = LogViewer()
        self.status_box = self.log_box  # 兼容 status_box 访问与测试断言
        log_layout.addWidget(self.log_box, 1)

        splitter.addWidget(log_card)
        splitter.setSizes([300, 200])

        root.addWidget(splitter, 1)

        if self.embedded:
            # 保持隐藏属性以兼容已有测试调用与信号接口，不占底部任何可见空间
            self.back_button = QPushButton("返回工作台")
            self.back_button.setVisible(False)
            self.back_button.clicked.connect(self.back_requested.emit)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.reject)
            buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
            root.addWidget(buttons)
            QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.reject)

    def _clear_logs(self) -> None:
        self.log_box.clear()

    def _clear_search(self) -> None:
        self.item_input.clear()
        self.log_box.clear()
        self.history_table.setRowCount(0)
        if hasattr(self, "history_count_label"):
            self.history_count_label.setText("0 条记录")
        self.search_button.setEnabled(True)
        self.item_input.setFocus()

    def _on_search(self) -> None:
        item_id = self.item_input.text().strip().upper()
        if not item_id:
            QMessageBox.information(self, "商品查询", "请输入商品 ID。")
            return
        now_str = datetime.now().strftime("%H:%M:%S")
        self.log_box.append_log_line(f"[{now_str}] 正在查询商品 {item_id} ...")
        self.history_table.setRowCount(0)
        if hasattr(self, "history_count_label"):
            self.history_count_label.setText("查询中...")
        self.search_button.setEnabled(False)
        self.query_requested.emit(item_id)

    def show_result(self, payload: object) -> None:
        if not self.isVisible() and not self.embedded:
            return
        self.search_button.setEnabled(True)
        data = dict(payload or {})
        item_id = str(data.get("item_id") or self.item_input.text().strip().upper())
        actions = list(data.get("actions") or [])
        items = list(data.get("items") or [])
        price_cache = list(data.get("price_cache") or [])
        if hasattr(self, "history_count_label"):
            self.history_count_label.setText(f"{len(actions)} 条历史记录")
        now_str = datetime.now().strftime("%H:%M:%S")
        if not actions and not items and not price_cache:
            self.log_box.append_log_line(f"[{now_str}] {item_id}：未找到该商品的任何记录，可能从未参与过活动。")
            return
        rendered = render_item_status_text(item_id, actions, items, price_cache)
        self.log_box.append_log_line(f"[{now_str}] 查询结果 [{item_id}]：\n{rendered}")
        self._fill_history(actions)

    def show_error(self, message: object) -> None:
        if not self.isVisible() and not self.embedded:
            return
        self.search_button.setEnabled(True)
        if hasattr(self, "history_count_label"):
            self.history_count_label.setText("查询失败")
        now_str = datetime.now().strftime("%H:%M:%S")
        err_text = business_reason_text(message or "未知错误")
        self.log_box.append_log_line(f"[{now_str}] 查询失败：{err_text}")

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


class AppEditDialog(QDialog):
    """Dialog for creating or editing a Mercado Libre Developer App credential entry."""

    def __init__(
        self,
        app: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("编辑美客多应用" if app else "添加美客多应用凭据")
        self.resize(500, 260)
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.name_edit = QLineEdit(str(app.get("name") or "") if app else "")
        self.name_edit.setPlaceholderText("如：应用 1 / 广东店铺应用")

        self.client_id_edit = QLineEdit(str(app.get("clientId") or "") if app else "")
        self.client_id_edit.setPlaceholderText("美客多开发者后台的 App ID (Client ID)")

        self.client_secret_edit = QLineEdit()
        self.client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if app and app.get("clientSecretConfigured"):
            self.client_secret_edit.setPlaceholderText("已加密保存，留空不修改")
        else:
            self.client_secret_edit.setPlaceholderText("美客多开发者后台的 Client Secret")

        default_redirect = DEFAULT_OAUTH_REDIRECT_URI
        initial_redirect = migrate_oauth_redirect_uri(app.get("redirectUri")) if app else default_redirect
        self.redirect_edit = QLineEdit(initial_redirect)
        self.redirect_edit.setPlaceholderText("例如: https://127.0.0.1/callback 或您的自定义回调网址")

        form.addRow("应用名称 / 备注", self.name_edit)
        form.addRow("美客多 Client ID", self.client_id_edit)
        form.addRow("美客多 Client Secret", self.client_secret_edit)
        form.addRow("OAuth 回调地址", self.redirect_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.app_data: dict[str, Any] = dict(app or {})

    def _validate_and_accept(self) -> None:
        client_id = self.client_id_edit.text().strip()
        if not client_id:
            QMessageBox.warning(self, "应用凭据", "美客多 Client ID 不能为空，请填写。")
            return
        name = self.name_edit.text().strip() or f"应用 {client_id[:6]}"
        redirect_uri = migrate_oauth_redirect_uri(self.redirect_edit.text().strip())
        if not redirect_uri:
            QMessageBox.warning(self, "应用凭据", "OAuth 回调地址不能为空，请填写在美客多后台登记的 Redirect URI。")
            return
        new_secret = self.client_secret_edit.text()

        self.app_data["id"] = client_id
        self.app_data["name"] = name
        self.app_data["clientId"] = client_id
        self.app_data["redirectUri"] = redirect_uri
        if new_secret:
            self.app_data["clientSecret"] = new_secret
            self.app_data["clientSecretConfigured"] = True
        elif not self.app_data.get("clientSecretConfigured"):
            self.app_data["clientSecret"] = ""
            self.app_data["clientSecretConfigured"] = False
        self.accept()


class SettingsDialog(QDialog):
    authorize_requested = Signal()
    complete_authorization_requested = Signal(str)
    refresh_requested = Signal()
    save_requested = Signal()
    back_requested = Signal()
    check_update_requested = Signal()

    def __init__(
        self,
        settings: dict[str, Any],
        accounts: list[Account],
        operating_rows: list[dict[str, Any]],
        benchmark_text: str,
        parent: QWidget | None = None,
        initial_tab: str = "",
        embedded: bool = False,
    ):
        self.embedded = embedded
        if embedded:
            super().__init__(parent, Qt.WindowType.Widget)
        else:
            super().__init__(parent)
            self.setWindowTitle("设置")
            self.resize(800, 660)
            self.setMinimumSize(740, 580)
        self.settings = settings
        self.accounts = accounts
        self.operating_rows = operating_rows
        self.oauth_apps: list[dict[str, Any]] = [
            {
                **dict(a),
                "redirectUri": migrate_oauth_redirect_uri(a.get("redirectUri")),
            }
            for a in list(settings.get("oauthApps") or [])
            if isinstance(a, dict)
        ]
        single_client_id = str(settings.get("oauthClientId") or "").strip()
        if not self.oauth_apps and single_client_id:
            self.oauth_apps.append({
                "id": single_client_id,
                "name": "应用 1",
                "clientId": single_client_id,
                "clientSecretConfigured": bool(settings.get("oauthClientSecretConfigured")),
                "redirectUri": migrate_oauth_redirect_uri(settings.get("oauthRedirectUri")),
            })
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
        root = QVBoxLayout(self)
        if self.embedded:
            root.setContentsMargins(10, 6, 10, 8)
            root.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._daily_tab(), "日常设置")
        self.tabs.addTab(self._stores_tab(), "店铺与站点")
        self.tabs.addTab(self._auth_tab(), "账号授权")
        self.tabs.addTab(self._advanced_tab(benchmark_text), "高  级")
        if initial_tab == "auth":
            self.tabs.setCurrentIndex(2)
        elif initial_tab == "stores":
            self.tabs.setCurrentIndex(1)
        elif initial_tab == "advanced":
            self.tabs.setCurrentIndex(3)
        root.addWidget(self.tabs)
        if self.embedded:
            corner_container = QWidget()
            corner_layout = QHBoxLayout(corner_container)
            corner_layout.setContentsMargins(0, 0, 4, 6)
            corner_layout.setSpacing(0)
            self.save_button = QPushButton("保存设置")
            self.save_button.setObjectName("primary")
            self.save_button.setFixedSize(100, 32)
            self.save_button.setStyleSheet("font-weight: bold;")
            self.save_button.clicked.connect(self.save_requested.emit)
            corner_layout.addWidget(self.save_button)
            self.tabs.setCornerWidget(corner_container, Qt.Corner.TopRightCorner)

            # 保持隐藏属性以兼容已有测试调用与信号接口
            self.back_button = QPushButton("返回工作台")
            self.back_button.setVisible(False)
            self.back_button.clicked.connect(self.back_requested.emit)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
            buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)

    def switch_tab(self, tab_name: str) -> None:
        if tab_name == "daily":
            self.tabs.setCurrentIndex(0)
        elif tab_name == "stores":
            self.tabs.setCurrentIndex(1)
        elif tab_name == "auth":
            self.tabs.setCurrentIndex(2)
        elif tab_name == "advanced":
            self.tabs.setCurrentIndex(3)

    def _daily_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # 左右双栏卡片：左侧为折扣参数核心设置，右侧为自动周期与执行说明
        top_cards = QHBoxLayout()
        top_cards.setSpacing(14)

        settings_card = QFrame()
        settings_card.setObjectName("settingsSection")
        card_layout = QVBoxLayout(settings_card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(16)

        card_title = QLabel("折扣参数设置")
        card_title.setObjectName("sectionTitle")
        card_layout.addWidget(card_title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)

        self.seller_discount = QSpinBox()
        self.official_discount = QSpinBox()
        for field in (self.seller_discount, self.official_discount):
            field.setRange(1, 90)
            field.setSuffix(" %")
            field.setFixedWidth(140)
            field.setFixedHeight(34)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seller_discount.setValue(int(self.settings.get("sellerDefaultDiscount", 5)))
        self.official_discount.setValue(int(self.settings.get("officialDefaultDiscount", 6)))
        self.seller_max_discount = QSpinBox()
        self.official_max_discount = QSpinBox()
        for field in (self.seller_max_discount, self.official_max_discount):
            field.setRange(0, 90)
            field.setSpecialValueText("未设置")
            field.setSuffix(" %")
            field.setFixedWidth(140)
            field.setFixedHeight(34)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seller_max_discount.setValue(_bounded_int(self.settings.get("sellerMaxDiscount"), 0, 0, 90))
        self.official_max_discount.setValue(_bounded_int(self.settings.get("officialMaxDiscount"), 0, 0, 90))

        grid.addWidget(QLabel("自建默认折扣:"), 0, 0)
        grid.addWidget(self.seller_discount, 0, 1)
        grid.addWidget(QLabel("官方默认折扣:"), 0, 2)
        grid.addWidget(self.official_discount, 0, 3)

        grid.addWidget(QLabel("自建最高折扣:"), 1, 0)
        grid.addWidget(self.seller_max_discount, 1, 1)
        grid.addWidget(QLabel("官方最高折扣:"), 1, 2)
        grid.addWidget(self.official_max_discount, 1, 3)
        grid.setColumnStretch(4, 1)

        card_layout.addLayout(grid)

        self.auto_reprice_checkbox = QCheckBox("Webhook 收到商品改价时，自动取消旧活动并以新价格重新报名")
        self.auto_reprice_checkbox.setChecked(bool(self.settings.get("autoRepriceOnWebhook", True)))
        card_layout.addWidget(self.auto_reprice_checkbox)
        card_layout.addStretch(1)
        top_cards.addWidget(settings_card, 1)

        note_card = QFrame()
        note_card.setObjectName("settingsSection")
        note_layout = QVBoxLayout(note_card)
        note_layout.setContentsMargins(20, 18, 20, 18)
        note_layout.setSpacing(12)
        note_title = QLabel("自动周期与安全规则说明")
        note_title.setObjectName("sectionTitle")
        note_layout.addWidget(note_title)

        rule1 = QLabel("<b>• 自动周期判定：</b>系统自动判断达到两项最高折扣后，本次调价完成；下一周期有已报名商品时批量取消。未设置最高折扣时不会自动判定。")
        rule1.setObjectName("muted")
        rule1.setWordWrap(True)
        note_layout.addWidget(rule1)

        rule2 = QLabel("<b>• Webhook 自动改价联动：</b>收到平台商品原价变更通知后，自动下线当前旧活动并以新价格基准重新计算折扣提交，确保折扣力度与利润边界精准一致。")
        rule2.setObjectName("muted")
        rule2.setWordWrap(True)
        note_layout.addWidget(rule2)

        rule3 = QLabel("<b>• 利润保护防线：</b>最高折扣为硬阻断红线，超出设定阈值的活动无论平台推荐如何，系统均坚决拦截，杜绝亏损爆单风险。")
        rule3.setObjectName("muted")
        rule3.setWordWrap(True)
        note_layout.addWidget(rule3)

        note_layout.addStretch(1)
        top_cards.addWidget(note_card, 1)

        layout.addLayout(top_cards, 1)
        return page

    def _stores_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        # 左栏：店铺识别与别名管理
        left_card = QFrame()
        left_card.setObjectName("settingsSection")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(18, 16, 18, 16)
        left_layout.setSpacing(10)

        left_title = QLabel("店铺识别与显示名称")
        left_title.setObjectName("sectionTitle")
        left_layout.addWidget(left_title)

        left_hint = QLabel("原始名称用于识别账号；店铺名称用于日常显示与操作记录，双击右列可修改别名。")
        left_hint.setObjectName("muted")
        left_hint.setWordWrap(True)
        left_layout.addWidget(left_hint)

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
        self.store_table.itemChanged.connect(self._on_store_name_changed)
        header.setStretchLastSection(True)
        left_layout.addWidget(self.store_table, 1)
        layout.addWidget(left_card, 55)

        # 右栏：经营站点配置
        right_card = QFrame()
        right_card.setObjectName("settingsSection")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(18, 16, 18, 16)
        right_layout.setSpacing(10)

        right_header = QHBoxLayout()
        right_title = QLabel("经营站点配置")
        right_title.setObjectName("sectionTitle")
        right_header.addWidget(right_title)
        right_header.addStretch(1)

        self.select_all_sites_btn = QPushButton("全 选")
        self.select_all_sites_btn.setFixedHeight(26)
        self.select_all_sites_btn.setStyleSheet("padding: 2px 12px; font-size: 12px;")
        self.select_all_sites_btn.clicked.connect(lambda: self._set_all_sites_checked(True))

        self.select_none_sites_btn = QPushButton("全不选")
        self.select_none_sites_btn.setFixedHeight(26)
        self.select_none_sites_btn.setStyleSheet("padding: 2px 12px; font-size: 12px;")
        self.select_none_sites_btn.clicked.connect(lambda: self._set_all_sites_checked(False))

        right_header.addWidget(self.select_all_sites_btn)
        right_header.addWidget(self.select_none_sites_btn)
        right_layout.addLayout(right_header)

        right_hint = QLabel("只勾选实际经营的站点；未勾选的站点将不在日常活动中加载。")
        right_hint.setObjectName("muted")
        right_hint.setWordWrap(True)
        right_layout.addWidget(right_hint)

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
        right_layout.addWidget(self.site_list, 1)
        layout.addWidget(right_card, 45)

        return page

    def _get_store_alias(self, account_id: str) -> str:
        aid = str(account_id or "").strip()
        if hasattr(self, "store_table"):
            for r in range(self.store_table.rowCount()):
                it = self.store_table.item(r, 1)
                if it and str(it.data(Qt.ItemDataRole.UserRole) or "").strip() == aid:
                    val = it.text().strip()
                    if val:
                        return val
        if aid in self._initial_aliases and self._initial_aliases[aid]:
            return self._initial_aliases[aid]
        if hasattr(self, "accounts"):
            for acc in self.accounts:
                if str(acc.account_id).strip() == aid and acc.store_name:
                    return acc.store_name
        return ""

    def _on_store_name_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        account_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        new_name = item.text().strip()
        if not account_id or not new_name:
            return
        self._initial_aliases[account_id] = new_name
        self._initial_store_names[account_id] = new_name
        if hasattr(self, "accounts"):
            for idx, acc in enumerate(self.accounts):
                if acc.account_id == account_id:
                    self.accounts[idx] = dataclasses.replace(acc, store_name=new_name)
        if hasattr(self, "site_list"):
            for row in range(self.site_list.count()):
                s_item = self.site_list.item(row)
                data = s_item.data(Qt.ItemDataRole.UserRole)
                if data and str(data[0]) == account_id:
                    s_id = str(data[1])
                    s_item.setText(f"{new_name} / {site_name(s_id)}")
        if hasattr(self, "apps_table"):
            self._render_apps_table()

    def _site_selection_changed(self, _item: QListWidgetItem) -> None:
        if not self._merging_sites:
            self._site_selection_dirty = True

    def _set_all_sites_checked(self, checked: bool) -> None:
        self._site_selection_dirty = True
        target_state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.site_list.count()):
            item = self.site_list.item(index)
            if item:
                item.setCheckState(target_state)

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
        resolved_alias = self._get_store_alias(account_id)
        store = resolved_alias or str(entry.get("store_name") or entry.get("storeName") or "当前店铺")
        if existing is not None:
            existing.setText(f"{store} / {site_name(site_id)}")
            configured = self._initial_operating_sites.get(account_id)
            if configured is not None and not self._site_selection_dirty:
                checked = site_id in configured
                existing.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
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
                if account.store_name and account.account_id in self._initial_aliases:
                    with QSignalBlocker(self.store_table):
                        self.store_table.item(row, 1).setText(self._initial_aliases[account.account_id])
                        self._initial_store_names[account.account_id] = self._initial_aliases[account.account_id]
        self.store_table.setSortingEnabled(sorting_enabled)
        self._merging_sites = True
        try:
            for entry in operating_rows:
                self._upsert_site_entry(entry)
            self._sort_site_entries()
        finally:
            self._merging_sites = False
        self.accounts = list(accounts)
        if hasattr(self, "apps_table"):
            self._render_apps_table()
        if hasattr(self, "oauth_account_count_badge"):
            self.oauth_account_count_badge.setText(f"已授权账号：{len(self.accounts)} 个")
        self.benchmark_note.setText(benchmark_text)

    def _auth_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # 1. Multi-App Management Card
        apps_card = QFrame()
        apps_card.setObjectName("settingsSection")
        apps_layout = QVBoxLayout(apps_card)
        apps_layout.setContentsMargins(20, 18, 20, 18)
        apps_layout.setSpacing(12)

        apps_header = QHBoxLayout()
        apps_title = QLabel("美客多应用凭据管理 (Developer Apps)")
        apps_title.setObjectName("sectionTitle")
        self.apps_count_badge = QLabel(f"已配置：{len(self.oauth_apps)} 个应用")
        self.apps_count_badge.setObjectName("muted")
        add_app_btn = QPushButton("＋ 添加应用")
        add_app_btn.clicked.connect(self._add_app)
        apps_header.addWidget(apps_title)
        apps_header.addStretch(1)
        apps_header.addWidget(self.apps_count_badge)
        apps_header.addWidget(add_app_btn)
        apps_layout.addLayout(apps_header)

        apps_hint = QLabel("可在此添加并维护多个美客多开发者应用（App ID / Secret）。授权时选择指定应用，即可分别绑定不同店铺。")
        apps_hint.setObjectName("muted")
        apps_hint.setWordWrap(True)
        apps_layout.addWidget(apps_hint)

        self.apps_table = QTableWidget(0, 5)
        self.apps_table.setHorizontalHeaderLabels(["开发者应用 (App)", "Client ID", "密钥状态", "已绑定店铺", "操作"])
        self.apps_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.apps_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.apps_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.apps_table.setMinimumHeight(130)
        self.apps_table.setMaximumHeight(210)
        apps_layout.addWidget(self.apps_table)
        self._render_apps_table()

        layout.addWidget(apps_card)

        # 2. OAuth Authorization Card
        oauth_card = QFrame()
        oauth_card.setObjectName("settingsSection")
        oauth_layout = QVBoxLayout(oauth_card)
        oauth_layout.setContentsMargins(20, 18, 20, 18)
        oauth_layout.setSpacing(12)

        oauth_header = QHBoxLayout()
        oauth_title = QLabel("美客多应用与授权 (OAuth)")
        oauth_title.setObjectName("sectionTitle")
        self.oauth_account_count_badge = QLabel(f"已授权账号：{len(self.accounts)} 个")
        self.oauth_account_count_badge.setObjectName("muted")
        oauth_header.addWidget(oauth_title)
        oauth_header.addStretch(1)
        oauth_header.addWidget(self.oauth_account_count_badge)
        oauth_layout.addLayout(oauth_header)

        step_banner = QLabel(
            "💡 <b>授权流程指引：</b>① 在上方表格或下拉框选用应用 ➔ ② 点击「新增账号授权」在美客多网页登录店铺并同意 ➔ ③ 复制跳转网址粘贴至下方完成绑定"
        )
        step_banner.setObjectName("muted")
        step_banner.setStyleSheet("color: #D4AF37; padding: 2px 0 6px 0;")
        step_banner.setWordWrap(True)
        oauth_layout.addWidget(step_banner)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.app_selector = QComboBox()
        self.app_selector.currentIndexChanged.connect(self._on_app_selected)
        form.addRow("选择授权应用", self.app_selector)

        creds_hint = QLabel("（选中上方应用时以下凭据将自动同步填充；修改上方应用凭据即可自动更新）")
        creds_hint.setObjectName("muted")
        form.addRow("", creds_hint)

        self.oauth_client_id = QLineEdit(str(self.settings.get("oauthClientId") or ""))
        self.oauth_client_secret = QLineEdit()
        self.oauth_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        if bool(self.settings.get("oauthClientSecretConfigured")):
            self.oauth_client_secret.setPlaceholderText("已保存，留空不修改")
        self.oauth_redirect_uri = QLineEdit(migrate_oauth_redirect_uri(self.settings.get("oauthRedirectUri")))
        self.oauth_redirect_uri.setPlaceholderText("例如: https://127.0.0.1/callback 或您的自定义回调网址")
        self.webhook_callback_url = QLineEdit(str(self.settings.get("webhookCallbackUrl") or DEFAULT_WEBHOOK_CALLBACK_URL))
        self.webhook_callback_url.setPlaceholderText("选填：独立回调服务的公网通知地址")

        form.addRow("美客多应用 Client ID", self.oauth_client_id)
        form.addRow("美客多应用 Client Secret", self.oauth_client_secret)
        form.addRow("OAuth 回调地址", self.oauth_redirect_uri)
        form.addRow("Webhook 通知地址", self.webhook_callback_url)
        oauth_layout.addLayout(form)

        self._sync_app_selector()

        actions = QHBoxLayout()
        authorize = QPushButton("新增账号授权")
        authorize.setObjectName("primary")
        authorize.setFixedHeight(34)
        authorize.setMinimumWidth(130)
        authorize.setToolTip("在浏览器中打开美客多官方登录授权页面")
        refresh = QPushButton("刷新账号")
        refresh.setFixedHeight(34)
        refresh.setMinimumWidth(100)
        authorize.clicked.connect(self.authorize_requested.emit)
        refresh.clicked.connect(self.refresh_requested.emit)
        actions.addWidget(authorize)
        actions.addWidget(refresh)
        actions.addStretch(1)
        oauth_layout.addLayout(actions)

        auth_finish_layout = QHBoxLayout()
        self.callback_edit = QLineEdit()
        self.callback_edit.setFixedHeight(34)
        self.callback_edit.setPlaceholderText("在浏览器同意授权后，复制地址栏跳转的完整回调网址粘贴在此")
        complete = QPushButton("完成授权")
        complete.setObjectName("primary")
        complete.setFixedHeight(34)
        complete.setMinimumWidth(110)
        complete.clicked.connect(lambda: self.complete_authorization_requested.emit(self.callback_edit.text().strip()))
        self.callback_edit.returnPressed.connect(lambda: self.complete_authorization_requested.emit(self.callback_edit.text().strip()))
        auth_finish_layout.addWidget(self.callback_edit, 1)
        auth_finish_layout.addWidget(complete)
        oauth_layout.addLayout(auth_finish_layout)

        layout.addWidget(oauth_card)

        cb_card = QFrame()
        cb_card.setObjectName("settingsSection")
        cb_layout = QVBoxLayout(cb_card)
        cb_layout.setContentsMargins(20, 18, 20, 18)
        cb_layout.setSpacing(12)

        cb_title = QLabel("活动变化通知接收 (Webhook)")
        cb_title.setObjectName("sectionTitle")
        cb_layout.addWidget(cb_title)

        cb_form = QFormLayout()
        cb_form.setSpacing(10)
        cb_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cb_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.activity_callback_enabled = QCheckBox("启用活动变化回调接收")
        self.activity_callback_enabled.setChecked(bool(self.settings.get("activityCallbackEnabled")))
        self.activity_callback_application_id = QLineEdit(str(self.settings.get("activityCallbackApplicationId") or ""))
        self.activity_callback_application_id.setPlaceholderText("Mercado 应用 Client ID（多个用逗号隔开）")
        self.activity_callback_secret_file = QLineEdit(str(self.settings.get("activityCallbackSecretFile") or ""))
        self.activity_callback_secret_file.setPlaceholderText("消费密钥文本（32位以上字符串）或密钥文件路径")
        self.activity_callback_claim_url = QLineEdit(str(self.settings.get("activityCallbackClaimUrl") or DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL))
        self.activity_callback_claim_url.setPlaceholderText("领取通知地址")
        self.activity_callback_ack_url = QLineEdit(str(self.settings.get("activityCallbackAckUrl") or DEFAULT_ACTIVITY_CALLBACK_ACK_URL))
        self.activity_callback_ack_url.setPlaceholderText("确认处理地址")

        cb_form.addRow("活动回调", self.activity_callback_enabled)
        cb_form.addRow("回调应用标识", self.activity_callback_application_id)
        cb_form.addRow("回调共享密钥", self.activity_callback_secret_file)
        cb_form.addRow("领取地址", self.activity_callback_claim_url)
        cb_form.addRow("确认地址", self.activity_callback_ack_url)
        cb_layout.addLayout(cb_form)

        for edit in (
            self.oauth_client_id,
            self.oauth_client_secret,
            self.oauth_redirect_uri,
            self.webhook_callback_url,
            self.activity_callback_application_id,
            self.activity_callback_secret_file,
            self.activity_callback_claim_url,
            self.activity_callback_ack_url,
        ):
            edit.setFixedHeight(32)
            edit.setMaximumWidth(640)
        self.app_selector.setMaximumWidth(640)
        self.app_selector.setFixedHeight(32)

        callback_note = QLabel("启用活动变化回调后，桌面程序会每 2 秒从领取地址拉取平台通知并自动处理（重新核对活动/商品缓存），处理成功后确认。Webhook 通知地址仅作记录，不用于接收。")
        callback_note.setObjectName("muted")
        callback_note.setWordWrap(True)
        cb_layout.addWidget(callback_note)

        layout.addWidget(cb_card)
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _render_apps_table(self) -> None:
        if not hasattr(self, "apps_table"):
            return
        self.apps_table.setRowCount(0)
        self.apps_count_badge.setText(f"已配置：{len(self.oauth_apps)} 个应用")
        if hasattr(self, "oauth_account_count_badge"):
            self.oauth_account_count_badge.setText(f"已授权账号：{len(self.accounts)} 个")
        header = self.apps_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.apps_table.verticalHeader().setDefaultSectionSize(40)

        btn_style = (
            "QPushButton { "
            "height: 26px; "
            "padding: 2px 10px; "
            "font-size: 12px; "
            "border-radius: 4px; "
            "border: 1px solid #4E472F; "
            "background-color: #262116; "
            "color: #E6E2D8; "
            "} "
            "QPushButton:hover { "
            "background-color: #383120; "
            "border-color: #7A6F4E; "
            "color: #FFFFFF; "
            "}"
        )

        for index, app in enumerate(self.oauth_apps):
            row = self.apps_table.rowCount()
            self.apps_table.insertRow(row)

            name_item = QTableWidgetItem(str(app.get("name") or f"应用 {index + 1}"))
            client_id = str(app.get("clientId") or "")
            id_item = QTableWidgetItem(client_id)

            secret_text = "● 已加密保存" if app.get("clientSecretConfigured") else "未设置"
            secret_item = QTableWidgetItem(secret_text)

            matching = [
                (self._get_store_alias(a.account_id) or a.store_name) for a in self.accounts
                if str(getattr(a, "client_id", "") or "").strip() == client_id
            ]
            if matching:
                bound_text = f"✅ 已绑定：{'、'.join(matching)}"
                bound_item = QTableWidgetItem(bound_text)
                bound_item.setForeground(QColor("#81C784"))
                bound_item.setToolTip(f"该应用凭据已成功绑定并授权至店铺：{'、'.join(matching)}")
            else:
                bound_text = "⚠️ 待登录授权"
                bound_item = QTableWidgetItem(bound_text)
                bound_item.setForeground(QColor("#FFD54F"))
                bound_item.setToolTip("该应用凭据尚未完成店铺授权。请选用该应用并在下方发起浏览器登录授权。")

            self.apps_table.setItem(row, 0, name_item)
            self.apps_table.setItem(row, 1, id_item)
            self.apps_table.setItem(row, 2, secret_item)
            self.apps_table.setItem(row, 3, bound_item)

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(6)

            use_btn = QPushButton("选用")
            use_btn.setStyleSheet(btn_style)
            use_btn.setToolTip("选择此应用以发起授权")
            use_btn.clicked.connect(lambda _, idx=index: self._select_app_by_index(idx))

            edit_btn = QPushButton("编辑")
            edit_btn.setStyleSheet(btn_style)
            edit_btn.clicked.connect(lambda _, idx=index: self._edit_app_by_index(idx))

            del_btn = QPushButton("删除")
            del_btn.setStyleSheet(btn_style)
            del_btn.clicked.connect(lambda _, idx=index: self._delete_app_by_index(idx))

            action_layout.addWidget(use_btn)
            action_layout.addWidget(edit_btn)
            action_layout.addWidget(del_btn)
            self.apps_table.setCellWidget(row, 4, action_widget)

    def _sync_app_selector(self) -> None:
        if not hasattr(self, "app_selector"):
            return
        with QSignalBlocker(self.app_selector):
            prev_idx = self.app_selector.currentIndex()
            self.app_selector.clear()
            for idx, app in enumerate(self.oauth_apps):
                name = str(app.get("name") or f"应用 {idx + 1}")
                cid = str(app.get("clientId") or "")
                self.app_selector.addItem(f"{name} ({cid})", idx)
            if not self.oauth_apps:
                self.app_selector.addItem("（尚未添加应用凭据）", -1)
            elif 0 <= prev_idx < len(self.oauth_apps):
                self.app_selector.setCurrentIndex(prev_idx)
            else:
                self.app_selector.setCurrentIndex(0)
        if self.oauth_apps:
            idx = self.app_selector.currentIndex()
            if 0 <= idx < len(self.oauth_apps):
                self._on_app_selected(idx)

    def _on_app_selected(self, index: int) -> None:
        if 0 <= index < len(self.oauth_apps):
            app = self.oauth_apps[index]
            self.oauth_client_id.setText(str(app.get("clientId") or ""))
            if app.get("clientSecretConfigured"):
                self.oauth_client_secret.clear()
                self.oauth_client_secret.setPlaceholderText("已保存，留空不修改")
            else:
                self.oauth_client_secret.clear()
                self.oauth_client_secret.setPlaceholderText("请输入 Client Secret")
            self.oauth_redirect_uri.setText(migrate_oauth_redirect_uri(app.get("redirectUri")))

    def _select_app_by_index(self, index: int) -> None:
        if hasattr(self, "app_selector") and 0 <= index < self.app_selector.count():
            self.app_selector.setCurrentIndex(index)
            self._on_app_selected(index)

    def _add_app(self) -> None:
        dlg = AppEditDialog(None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.oauth_apps.append(dlg.app_data)
            self._render_apps_table()
            self._sync_app_selector()
            self._select_app_by_index(len(self.oauth_apps) - 1)

    def _edit_app_by_index(self, index: int) -> None:
        if 0 <= index < len(self.oauth_apps):
            dlg = AppEditDialog(self.oauth_apps[index], self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.oauth_apps[index] = dlg.app_data
                self._render_apps_table()
                self._sync_app_selector()
                self._select_app_by_index(index)

    def _delete_app_by_index(self, index: int) -> None:
        if 0 <= index < len(self.oauth_apps):
            app = self.oauth_apps[index]
            name = app.get("name") or app.get("clientId")
            reply = QMessageBox.question(
                self,
                "删除应用",
                f"确定要删除应用「{name}」吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.oauth_apps.pop(index)
                self._render_apps_table()
                self._sync_app_selector()

    def _advanced_tab(self, benchmark_text: str) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        concurrency_card = QFrame()
        concurrency_card.setObjectName("settingsSection")
        c_layout = QVBoxLayout(concurrency_card)
        c_layout.setContentsMargins(20, 18, 20, 18)
        c_layout.setSpacing(14)

        c_title = QLabel("运行与并发安全机制")
        c_title.setObjectName("sectionTitle")
        c_layout.addWidget(c_title)

        form = QFormLayout()
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.auth_dir = QLineEdit(str(self.settings.get("authDir") or ""))
        self.auth_dir.setVisible(False)

        storage_info_box = QWidget()
        storage_info_layout = QHBoxLayout(storage_info_box)
        storage_info_layout.setContentsMargins(0, 0, 0, 0)
        storage_info_layout.setSpacing(8)
        storage_status = QLabel("本地安全数据库加密存储（SQLite）")
        storage_status.setStyleSheet("color: #81C784; font-weight: bold;")
        storage_hint = QLabel("（所有开发者凭据与店铺 Token 均在本地数据库统一加密，无需手动维护目录）")
        storage_hint.setObjectName("muted")
        storage_info_layout.addWidget(storage_status)
        storage_info_layout.addWidget(storage_hint)
        storage_info_layout.addStretch(1)

        concurrency_info_box = QWidget()
        concurrency_info_layout = QHBoxLayout(concurrency_info_box)
        concurrency_info_layout.setContentsMargins(0, 0, 0, 0)
        concurrency_info_layout.setSpacing(8)
        concurrency_status = QLabel("官方防封保护模式（自适应 18 线程安全并发）")
        concurrency_status.setStyleSheet("color: #81C784; font-weight: bold;")
        concurrency_hint = QLabel("（系统已全自动内置防封频控与信号量保护，避免触发美客多官方 HTTP 429 请求超限，无需手动干预）")
        concurrency_hint.setObjectName("muted")
        concurrency_info_layout.addWidget(concurrency_status)
        concurrency_info_layout.addWidget(concurrency_hint)
        concurrency_info_layout.addStretch(1)

        self.read_concurrency = QSpinBox()
        self.activity_concurrency = QSpinBox()
        self.write_concurrency = QSpinBox()
        for spin in (self.read_concurrency, self.activity_concurrency, self.write_concurrency):
            spin.setFixedWidth(160)
            spin.setFixedHeight(34)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setVisible(False)
        self.read_concurrency.setRange(1, 125)
        self.activity_concurrency.setRange(1, 192)
        self.write_concurrency.setRange(1, 160)
        self.read_concurrency.setValue(_bounded_int(self.settings.get("readConcurrency"), 125, 1, 125))
        self.activity_concurrency.setValue(_bounded_int(self.settings.get("previewConcurrency"), 192, 1, 192))
        self.write_concurrency.setValue(_bounded_int(self.settings.get("writeConcurrency"), 160, 1, 160))

        form.addRow("凭据存储机制", storage_info_box)
        form.addRow("并发调度机制", concurrency_info_box)
        c_layout.addLayout(form)
        c_layout.addStretch(1)
        layout.addWidget(concurrency_card, 1)

        note_card = QFrame()
        note_card.setObjectName("settingsSection")
        note_layout = QVBoxLayout(note_card)
        note_layout.setContentsMargins(20, 18, 20, 18)
        note_layout.setSpacing(12)
        note_title = QLabel("并发安全说明与系统基准")
        note_title.setObjectName("sectionTitle")
        note_layout.addWidget(note_title)
        self.benchmark_note = QLabel(benchmark_text)
        self.benchmark_note.setWordWrap(True)
        self.benchmark_note.setObjectName("muted")
        note_layout.addWidget(self.benchmark_note)

        perf_hint = QLabel("提示：底层请求已启用美客多官方防封限流保护（安全持久连接池与每账号 Semaphore 信号量控制）。系统已自动根据接口与网络状态自适应调度，保障店铺安全。")
        perf_hint.setObjectName("muted")
        perf_hint.setWordWrap(True)
        note_layout.addWidget(perf_hint)

        update_box = QWidget()
        update_box_layout = QHBoxLayout(update_box)
        update_box_layout.setContentsMargins(0, 8, 0, 0)
        update_box_layout.setSpacing(10)
        update_title = QLabel("客户端软件更新：")
        update_title.setStyleSheet("font-weight: bold; color: #F6F3EA;")
        update_box_layout.addWidget(update_title)
        self.check_update_btn = QPushButton("检查新版本")
        self.check_update_btn.setFixedWidth(110)
        self.check_update_btn.clicked.connect(self.check_update_requested.emit)
        update_box_layout.addWidget(self.check_update_btn)
        update_box_layout.addStretch(1)
        note_layout.addWidget(update_box)

        note_layout.addStretch(1)
        layout.addWidget(note_card, 1)

        self._initial_field_values.update({
            "sellerDefaultDiscount": self.seller_discount.value(),
            "officialDefaultDiscount": self.official_discount.value(),
            "sellerMaxDiscount": self.seller_max_discount.value(),
            "officialMaxDiscount": self.official_max_discount.value(),
            "autoRepriceOnWebhook": self.auto_reprice_checkbox.isChecked(),
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
            ("autoRepriceOnWebhook", self.auto_reprice_checkbox, bool(settings.get("autoRepriceOnWebhook", True))),
            ("authDir", self.auth_dir, str(settings.get("authDir") or "")),
            ("readConcurrency", self.read_concurrency, _bounded_int(settings.get("readConcurrency"), 125, 1, 125)),
            ("previewConcurrency", self.activity_concurrency, _bounded_int(settings.get("previewConcurrency"), 192, 1, 192)),
            ("writeConcurrency", self.write_concurrency, _bounded_int(settings.get("writeConcurrency"), 160, 1, 160)),
            ("oauthClientId", self.oauth_client_id, str(settings.get("oauthClientId") or "")),
            ("oauthRedirectUri", self.oauth_redirect_uri, migrate_oauth_redirect_uri(settings.get("oauthRedirectUri"))),
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
        if "oauthApps" in settings:
            self.oauth_apps = [
                {
                    **dict(a),
                    "redirectUri": migrate_oauth_redirect_uri(a.get("redirectUri")),
                }
                for a in list(settings.get("oauthApps") or [])
                if isinstance(a, dict)
            ]
            if hasattr(self, "apps_table"):
                self._render_apps_table()
                self._sync_app_selector()
        if "storeAliases" in settings:
            new_aliases = {
                str(k): str(v).strip()
                for k, v in dict(settings.get("storeAliases") or {}).items()
                if str(v).strip()
            }
            self._initial_aliases.update(new_aliases)
            if hasattr(self, "store_table"):
                with QSignalBlocker(self.store_table):
                    for row in range(self.store_table.rowCount()):
                        name_item = self.store_table.item(row, 1)
                        if name_item:
                            acc_id = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
                            if acc_id in new_aliases:
                                name_item.setText(new_aliases[acc_id])
                                self._initial_store_names[acc_id] = new_aliases[acc_id]
                if hasattr(self, "site_list"):
                    for idx in range(self.site_list.count()):
                        s_item = self.site_list.item(idx)
                        data = s_item.data(Qt.ItemDataRole.UserRole)
                        if data:
                            a_id, s_id = str(data[0]), str(data[1])
                            alias_lbl = self._get_store_alias(a_id)
                            if alias_lbl:
                                s_item.setText(f"{alias_lbl} / {site_name(s_id)}")
        if "operatingSites" in settings:
            self._initial_operating_sites = {
                str(account_id): [str(site_id).upper() for site_id in site_ids]
                for account_id, site_ids in dict(settings.get("operatingSites") or {}).items()
                if isinstance(site_ids, list)
            }
            if hasattr(self, "site_list") and not self._site_selection_dirty:
                for idx in range(self.site_list.count()):
                    s_item = self.site_list.item(idx)
                    data = s_item.data(Qt.ItemDataRole.UserRole)
                    if data:
                        a_id, s_id = str(data[0]), str(data[1]).upper()
                        cfg = self._initial_operating_sites.get(a_id)
                        if cfg is not None:
                            s_item.setCheckState(Qt.CheckState.Checked if s_id in cfg else Qt.CheckState.Unchecked)

    def values(self) -> dict[str, Any]:
        aliases = dict(self._initial_aliases)
        for row in range(self.store_table.rowCount()):
            name_item = self.store_table.item(row, 1)
            if not name_item:
                continue
            account_id = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            current_name = name_item.text().strip()
            if not account_id or not current_name:
                continue
            if account_id in aliases or current_name != self._initial_store_names.get(account_id, ""):
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
            self._initial_operating_sites = dict(operating)
            self._site_selection_dirty = False
        return {
            "authDir": self.auth_dir.text().strip(),
            "outputDir": str(self.settings.get("outputDir") or ""),
            "sellerDefaultDiscount": self.seller_discount.value(),
            "officialDefaultDiscount": self.official_discount.value(),
            "sellerMaxDiscount": self.seller_max_discount.value() or None,
            "officialMaxDiscount": self.official_max_discount.value() or None,
            "autoRepriceOnWebhook": self.auto_reprice_checkbox.isChecked(),
            "readConcurrency": self.read_concurrency.value(),
            "previewConcurrency": self.activity_concurrency.value(),
            "writeConcurrency": self.write_concurrency.value(),
            "oauthClientId": self.oauth_client_id.text().strip(),
            "oauthClientSecret": self.oauth_client_secret.text(),
            "oauthRedirectUri": migrate_oauth_redirect_uri(self.oauth_redirect_uri.text().strip()),
            "oauthApps": [
                {**app, "redirectUri": migrate_oauth_redirect_uri(app.get("redirectUri"))}
                for app in self.oauth_apps
            ],
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


def execute_system_shutdown() -> None:
    """Execute graceful system shutdown across macOS, Windows and Linux."""
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'tell application "System Events" to shut down'], check=False)
    elif sys.platform == "win32":
        subprocess.run(["shutdown.exe", "/s", "/t", "0"], check=False)
    else:
        subprocess.run(["shutdown", "-h", "now"], check=False)


class AutoShutdownCountdownDialog(QDialog):
    def __init__(self, seconds: int = 60, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("执行完自动关机")
        self.setFixedSize(380, 170)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.remaining_seconds = seconds
        self.cancelled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        self.title_label = QLabel("任务已全部完成！")
        self.title_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #1f2937;")
        layout.addWidget(self.title_label)

        self.message_label = QLabel(f"系统将在 {self.remaining_seconds} 秒后自动关机...")
        self.message_label.setStyleSheet("font-size: 13px; color: #4b5563;")
        layout.addWidget(self.message_label)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("取消关机")
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.cancel_btn)

        self.shutdown_now_btn = QPushButton("立即关机")
        self.shutdown_now_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.shutdown_now_btn.clicked.connect(self._on_shutdown_now)
        btn_layout.addWidget(self.shutdown_now_btn)

        layout.addLayout(btn_layout)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()

    def _on_tick(self) -> None:
        self.remaining_seconds -= 1
        if self.remaining_seconds <= 0:
            self.timer.stop()
            self.accept()
        else:
            self.message_label.setText(f"系统将在 {self.remaining_seconds} 秒后自动关机...")

    def _on_cancel(self) -> None:
        self.cancelled = True
        self.timer.stop()
        self.reject()

    def _on_shutdown_now(self) -> None:
        self.timer.stop()
        self.accept()

    def closeEvent(self, event) -> None:
        self.cancelled = True
        self.timer.stop()
        super().closeEvent(event)


class CopyZeroVisitIdsDialog(QDialog):
    """500个一组展示0浏览待删除商品ID，点击整行直接复制以空格隔开的纯ID"""

    def __init__(self, batches: list[list[str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("复制0浏览待删除ID")
        self.resize(760, 520)
        self.batches = batches

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        total_ids = sum(len(b) for b in batches)
        header_label = QLabel(
            f"共筛选出 {total_ids:,} 个 0 浏览待删除商品，已按 500 个一组切分为 {len(batches)} 个批次。\n"
            "直接点击任意一行即可一键复制该批次的所有 ID（纯空格隔开），可直接粘贴至 ERP 中执行删除。"
        )
        header_label.setStyleSheet("color: #333; font-size: 13px; line-height: 1.4;")
        layout.addWidget(header_label)

        self.table = QTableWidget(len(batches), 2)
        self.table.setHorizontalHeaderLabels(["批次与 ID 缩略预览", "状态"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setCursor(Qt.CursorShape.PointingHandCursor)

        h_header = self.table.horizontalHeader()
        h_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 100)

        for row_idx, batch in enumerate(batches):
            preview_ids = " ".join(batch[:4]) + (" ..." if len(batch) > 4 else "")
            text_preview = f"批次 {row_idx + 1:02d} ({len(batch)} 个) : {preview_ids}"
            item_preview = QTableWidgetItem(text_preview)
            item_preview.setToolTip(f"点击复制此批次全部 {len(batch)} 个 ID（以空格隔开）")

            item_status = QTableWidgetItem("点击复制")
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_status.setForeground(QColor("#888888"))

            self.table.setItem(row_idx, 0, item_preview)
            self.table.setItem(row_idx, 1, item_status)
            self.table.setRowHeight(row_idx, 38)

        self.table.cellClicked.connect(self._on_row_clicked)
        layout.addWidget(self.table, 1)

        self.tip_label = QLabel("")
        self.tip_label.setStyleSheet("color: #2e7d32; font-weight: bold; font-size: 13px;")
        layout.addWidget(self.tip_label)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        close_btn = btn_box.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("关闭")
        layout.addWidget(btn_box)

    def _on_row_clicked(self, row: int, _col: int):
        if row < 0 or row >= len(self.batches):
            return
        batch = self.batches[row]
        copied_text = " ".join(batch)
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(copied_text)

        status_item = self.table.item(row, 1)
        if status_item:
            status_item.setText("已复制")
            status_item.setForeground(QColor("#2e7d32"))
            font = status_item.font()
            font.setBold(True)
            status_item.setFont(font)

        self.tip_label.setText(f"✅ 批次 {row + 1:02d} 的 {len(batch)} 个 ID 已成功复制到剪贴板！可直接在 ERP 中 Ctrl+V 粘贴。")


class UpdateDialog(QDialog):
    """一键自动原地更新对话框。展示更新说明、下载进度条、完整性解压与跳板重启。"""

    update_progress = Signal(int, int)
    update_status = Signal(str)
    update_finished = Signal()
    update_error = Signal(str)

    def __init__(
        self,
        release_info: Any,
        current_version: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.release_info = release_info
        self.current_version = current_version
        self.setWindowTitle("软件更新 - 美客多活动管家")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMaximumWidth(700)
        self.resize(600, 480)

        self._stop_event = threading.Event()
        self._is_updating = False
        self.update_progress.connect(self._on_progress)
        self.update_status.connect(self._on_status)
        self.update_finished.connect(self._on_finished)
        self.update_error.connect(self._on_error)

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        heading = QLabel(f"🚀 发现新版本：v{self.release_info.version}")
        heading.setStyleSheet("font-size: 16px; font-weight: bold; color: #10B981;")
        title_box.addWidget(heading)

        size_mb = (self.release_info.asset_size or 0) / (1024 * 1024)
        date_str = str(self.release_info.published_at or "")[:10] or "近期"
        sub_text = f"当前版本：v{self.current_version}  |  发布日期：{date_str}"
        if size_mb > 0:
            sub_text += f"  |  更新大小：约 {size_mb:.1f} MB"
        sub_label = QLabel(sub_text)
        sub_label.setObjectName("muted")
        title_box.addWidget(sub_label)
        layout.addLayout(title_box)

        notes_label = QLabel("更新说明与改进项：")
        notes_label.setStyleSheet("font-weight: bold; color: #F6F3EA; margin-top: 4px;")
        layout.addWidget(notes_label)

        self.notes_edit = QTextEdit()
        self.notes_edit.setReadOnly(True)
        notes = str(self.release_info.release_notes or "").strip() or "常规性能优化与问题修复。"
        self.notes_edit.setPlainText(notes)
        self.notes_edit.setStyleSheet(
            "background: #141816; border: 1px solid #3E3827; border-radius: 6px; padding: 10px; color: #D8D4CA; font-size: 12px; line-height: 1.5;"
        )
        layout.addWidget(self.notes_edit, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar { background: #18201C; border: 1px solid #4E472F; border-radius: 6px; text-align: center; color: #F6F3EA; font-weight: bold; height: 20px; }"
            "QProgressBar::chunk { background-color: #10B981; border-radius: 5px; }"
        )
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("点击下方按钮即可一键自动下载并重启生效")
        self.status_label.setObjectName("muted")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch(1)

        self.cancel_btn = QPushButton("稍后提醒")
        self.cancel_btn.setFixedWidth(100)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_layout.addWidget(self.cancel_btn)

        self.update_btn = QPushButton("立即更新并重启")
        self.update_btn.setObjectName("primary")
        self.update_btn.setMinimumWidth(150)
        self.update_btn.setStyleSheet(
            "QPushButton { background: #10B981; color: #FFFFFF; font-weight: bold; border: 1px solid #059669; border-radius: 6px; padding: 7px 16px; }"
            "QPushButton:hover { background: #059669; }"
            "QPushButton:disabled { background: #233529; color: #66776C; border-color: #2D4234; }"
        )
        self.update_btn.clicked.connect(self._start_update)
        btn_layout.addWidget(self.update_btn)

        layout.addLayout(btn_layout)

    def _start_update(self) -> None:
        if self._is_updating:
            return
        if not self.release_info.download_url:
            self.status_label.setStyleSheet("color: #EF4444; font-weight: bold;")
            self.status_label.setText("❌ 未能匹配到适合当前操作系统的更新包，请前往 GitHub 手动下载。")
            return

        self._is_updating = True
        self.update_btn.setEnabled(False)
        self.update_btn.setText("正在准备下载...")
        self.cancel_btn.setText("取消")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setStyleSheet("color: #C8C3B7;")
        self.status_label.setText("正在建立高速下载连接...")

        threading.Thread(target=self._run_update_worker, daemon=True).start()

    def _run_update_worker(self) -> None:
        import tempfile
        from pathlib import Path
        from engine.updater import (
            download_release_archive,
            verify_and_extract_update,
            launch_in_place_update,
        )

        zip_dest = Path(tempfile.gettempdir()) / f"mdm_update_{self.release_info.version}.zip"
        extract_dir = Path(tempfile.gettempdir()) / f"mdm_update_{self.release_info.version}_extracted"

        def _prog_cb(downloaded: int, total: int) -> None:
            self.update_progress.emit(downloaded, total)

        ok = download_release_archive(
            url=self.release_info.download_url,
            dest_path=zip_dest,
            on_progress=_prog_cb,
            stop_event=self._stop_event,
        )
        if self._stop_event.is_set():
            return

        if not ok or not zip_dest.exists():
            self.update_error.emit("下载更新安装包失败，请检查网络后重试。")
            return

        self.update_status.emit("正在校验并解压安装包...")
        try:
            extracted_target = verify_and_extract_update(zip_dest, extract_dir)
        except Exception as e:
            self.update_error.emit(f"解压安装包校验失败: {e}")
            return

        self.update_status.emit("更新就绪，正在准备重启应用...")
        try:
            launch_in_place_update(extracted_target)
        except Exception as e:
            self.update_error.emit(f"拉起更新跳板失败: {e}")
            return

        self.update_finished.emit()

    def _on_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            pct = int(downloaded * 100 / total)
            self.progress_bar.setValue(min(100, max(0, pct)))
            d_mb = downloaded / (1024 * 1024)
            t_mb = total / (1024 * 1024)
            self.status_label.setText(f"正在下载安装包... {d_mb:.1f} MB / {t_mb:.1f} MB ({pct}%)")
        else:
            d_mb = downloaded / (1024 * 1024)
            self.status_label.setText(f"正在下载安装包... {d_mb:.1f} MB")

    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_error(self, err_msg: str) -> None:
        self._is_updating = False
        self.update_btn.setEnabled(True)
        self.update_btn.setText("重试更新")
        self.cancel_btn.setText("关闭")
        self.status_label.setStyleSheet("color: #EF4444; font-weight: bold;")
        self.status_label.setText(f"❌ {err_msg}")

    def _on_finished(self) -> None:
        self.status_label.setStyleSheet("color: #10B981; font-weight: bold;")
        self.status_label.setText("✅ 更新包已就绪！程序即将退出并重启生效...")
        self.update_btn.setText("即将重启...")
        QTimer.singleShot(1200, lambda: QApplication.quit())

    def _on_cancel_clicked(self) -> None:
        if self._is_updating:
            self._stop_event.set()
        self.reject()

    def closeEvent(self, event: Any) -> None:
        if self._is_updating:
            self._stop_event.set()
        super().closeEvent(event)



