from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from PySide6.QtCore import QDate, QModelIndex, QSettings, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
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
        self.resize(720, 580)
        self.setMinimumSize(620, 460)
        self._item_ids: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        heading = QLabel("批量处理指定商品的活动")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        scope = QLabel(f"核对范围：{scope_text}\n只会处理该范围内与输入商品 ID 精确匹配的已报名活动关系。")
        scope.setWordWrap(True)
        scope.setStyleSheet("color: #AFA89B;")
        layout.addWidget(scope)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_label = QLabel("操作类型")
        action_label.setFixedWidth(64)
        action_row.addWidget(action_label)
        self.action_combo = QComboBox()
        self.action_combo.addItem("报名活动", "enroll")
        self.action_combo.addItem("取消活动", "cancel")
        self.action_combo.addItem("刷新商品缓存", "refresh_cache")
        self.action_combo.currentIndexChanged.connect(self._sync_submit_state)
        action_row.addWidget(self.action_combo, 1)
        layout.addLayout(action_row)

        self.discount_note = QLabel()
        self.discount_note.setWordWrap(True)
        layout.addWidget(self.discount_note)
        self._seller_discount = int(seller_discount)
        self._official_discount = int(official_discount)

        toolbar_row = QHBoxLayout()
        toolbar_row.setSpacing(10)

        self.count_label = QLabel("已输入：0 个商品")
        self.count_label.setStyleSheet("color: #AFA89B;")
        toolbar_row.addWidget(self.count_label, 1)

        last_canceled = get_last_canceled_batch()
        last_count_text = f" ({last_canceled['count']}个)" if last_canceled and last_canceled.get("count") else ""
        self.load_last_canceled_btn = QPushButton(f"载入上次取消{last_count_text}")
        self.load_last_canceled_btn.setFixedHeight(30)
        self.load_last_canceled_btn.setEnabled(bool(last_canceled and last_canceled.get("item_ids")))
        self.load_last_canceled_btn.clicked.connect(self._load_last_canceled_items)
        toolbar_row.addWidget(self.load_last_canceled_btn)

        self.history_combo = QComboBox()
        self.history_combo.setFixedHeight(30)
        self.history_combo.setMinimumWidth(180)
        self.history_combo.view().setMinimumWidth(240)
        self._refresh_history_combo()
        self.history_combo.currentIndexChanged.connect(self._on_history_selected)
        toolbar_row.addWidget(self.history_combo)

        self.multi_batch_btn = QPushButton("多选批次...")
        self.multi_batch_btn.setFixedHeight(30)
        self.multi_batch_btn.clicked.connect(self._open_multi_batch_dialog)
        toolbar_row.addWidget(self.multi_batch_btn)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.setFixedHeight(30)
        self.clear_btn.clicked.connect(self._clear_input)
        toolbar_row.addWidget(self.clear_btn)

        layout.addLayout(toolbar_row)

        self.item_input = QPlainTextEdit()
        self.item_input.setPlaceholderText("每行一个商品 ID，例如：\nMLB4730089499\nMLM5615657734")
        self.item_input.setStyleSheet(
            "QPlainTextEdit { "
            "padding: 10px 12px; "
            "font-family: Menlo, Monaco, Consolas, monospace; "
            "font-size: 11pt; "
            "line-height: 1.4; "
            "background: #18201C; "
            "color: #E6E2D8; "
            "border: 1px solid #4E472F; "
            "border-radius: 6px; "
            "}"
        )
        self.item_input.textChanged.connect(self._sync_item_count)
        layout.addWidget(self.item_input, 1)

        self.operation_hint = QLabel()
        self.operation_hint.setWordWrap(True)
        self.operation_hint.setStyleSheet("color: #81C784;")
        self.operation_hint.setVisible(False)
        layout.addWidget(self.operation_hint)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName("muted")
        layout.addWidget(self.note)

        buttons = QDialogButtonBox()
        self.submit_button = buttons.addButton("开始核对并报名", QDialogButtonBox.ButtonRole.AcceptRole)
        self.submit_button.setObjectName("primary")
        self.cancel_button = buttons.addButton("返回", QDialogButtonBox.ButtonRole.RejectRole)
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
            self.count_label.setStyleSheet("color: #AFA89B;")
            return
        try:
            items = parse_targeted_cancel_item_ids(text, max_items=999999)
            count = len(items)
            self.count_label.setText(f"已输入：{count} 个商品 ID")
            self.count_label.setStyleSheet("color: #81C784; font-weight: 500;")
        except ValueError as err:
            err_text = str(err)
            if "格式不正确" in err_text:
                self.count_label.setText("输入包含无效字符或格式错误")
            else:
                self.count_label.setText("输入内容有误")
            self.count_label.setStyleSheet("color: #E57373;")
        except Exception:
            self.count_label.setText("输入包含无效字符")
            self.count_label.setStyleSheet("color: #E57373;")

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
        self.redirect_edit.setPlaceholderText("通常保持默认回调地址即可")

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

    def __init__(
        self,
        settings: dict[str, Any],
        accounts: list[Account],
        operating_rows: list[dict[str, Any]],
        benchmark_text: str,
        parent: QWidget | None = None,
        initial_tab: str = "",
    ):
        super().__init__(parent)
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
        self.setWindowTitle("设置")
        self.resize(800, 660)
        self.setMinimumSize(740, 580)
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._daily_tab(), "日常设置")
        tabs.addTab(self._stores_tab(), "店铺与站点")
        tabs.addTab(self._auth_tab(), "账号授权")
        tabs.addTab(self._advanced_tab(benchmark_text), "高级")
        if initial_tab == "auth":
            tabs.setCurrentIndex(2)
        elif initial_tab == "stores":
            tabs.setCurrentIndex(1)
        elif initial_tab == "advanced":
            tabs.setCurrentIndex(3)
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
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        settings_card = QFrame()
        settings_card.setObjectName("controlSection")
        card_layout = QVBoxLayout(settings_card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(14)

        card_title = QLabel("折扣参数设置")
        card_title.setObjectName("sectionTitle")
        card_layout.addWidget(card_title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.seller_discount = QSpinBox()
        self.official_discount = QSpinBox()
        for field in (self.seller_discount, self.official_discount):
            field.setRange(1, 90)
            field.setSuffix(" %")
            field.setFixedWidth(160)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seller_discount.setValue(int(self.settings.get("sellerDefaultDiscount", 5)))
        self.official_discount.setValue(int(self.settings.get("officialDefaultDiscount", 6)))
        self.seller_max_discount = QSpinBox()
        self.official_max_discount = QSpinBox()
        for field in (self.seller_max_discount, self.official_max_discount):
            field.setRange(0, 90)
            field.setSpecialValueText("未设置")
            field.setSuffix(" %")
            field.setFixedWidth(160)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seller_max_discount.setValue(_bounded_int(self.settings.get("sellerMaxDiscount"), 0, 0, 90))
        self.official_max_discount.setValue(_bounded_int(self.settings.get("officialMaxDiscount"), 0, 0, 90))
        form.addRow("自建默认折扣", self.seller_discount)
        form.addRow("官方默认折扣", self.official_discount)
        form.addRow("自建最高折扣", self.seller_max_discount)
        form.addRow("官方最高折扣", self.official_max_discount)
        self.auto_reprice_checkbox = QCheckBox("Webhook 收到商品改价时，自动取消旧活动并以新价格重新报名")
        self.auto_reprice_checkbox.setChecked(bool(self.settings.get("autoRepriceOnWebhook", True)))
        form.addRow("改价自动重报", self.auto_reprice_checkbox)
        card_layout.addLayout(form)
        layout.addWidget(settings_card)

        note_card = QFrame()
        note_card.setObjectName("controlSection")
        note_layout = QVBoxLayout(note_card)
        note_layout.setContentsMargins(20, 16, 20, 16)
        note_layout.setSpacing(8)
        note_title = QLabel("自动周期说明")
        note_title.setObjectName("sectionTitle")
        note = QLabel("自动判断达到两项最高折扣后，本次完成；下一执行周期有已报名商品时批量取消。未设置时自动判断不会执行。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        note_layout.addWidget(note_title)
        note_layout.addWidget(note)
        layout.addWidget(note_card)

        layout.addStretch(1)
        return page

    def _stores_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
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
        self.accounts = list(accounts)
        if hasattr(self, "apps_table"):
            self._render_apps_table()
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
        apps_card.setObjectName("controlSection")
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
        self.apps_table.setHorizontalHeaderLabels(["应用名称", "Client ID", "密钥状态", "已绑定店铺", "操作"])
        self.apps_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.apps_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.apps_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.apps_table.setMinimumHeight(120)
        self.apps_table.setMaximumHeight(190)
        apps_layout.addWidget(self.apps_table)
        self._render_apps_table()

        layout.addWidget(apps_card)

        # 2. OAuth Authorization Card
        oauth_card = QFrame()
        oauth_card.setObjectName("controlSection")
        oauth_layout = QVBoxLayout(oauth_card)
        oauth_layout.setContentsMargins(20, 18, 20, 18)
        oauth_layout.setSpacing(12)

        oauth_header = QHBoxLayout()
        oauth_title = QLabel("美客多应用与授权 (OAuth)")
        oauth_title.setObjectName("sectionTitle")
        account_count = QLabel(f"已授权账号：{len(self.accounts)} 个")
        account_count.setObjectName("muted")
        oauth_header.addWidget(oauth_title)
        oauth_header.addStretch(1)
        oauth_header.addWidget(account_count)
        oauth_layout.addLayout(oauth_header)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.app_selector = QComboBox()
        self.app_selector.currentIndexChanged.connect(self._on_app_selected)
        form.addRow("选择授权应用", self.app_selector)

        self.oauth_client_id = QLineEdit(str(self.settings.get("oauthClientId") or ""))
        self.oauth_client_secret = QLineEdit()
        self.oauth_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        if bool(self.settings.get("oauthClientSecretConfigured")):
            self.oauth_client_secret.setPlaceholderText("已保存，留空不修改")
        self.oauth_redirect_uri = QLineEdit(migrate_oauth_redirect_uri(self.settings.get("oauthRedirectUri")))
        self.webhook_callback_url = QLineEdit(str(self.settings.get("webhookCallbackUrl") or DEFAULT_WEBHOOK_CALLBACK_URL))
        self.webhook_callback_url.setPlaceholderText("请输入独立回调服务的公网通知地址")

        form.addRow("美客多应用 Client ID", self.oauth_client_id)
        form.addRow("美客多应用 Client Secret", self.oauth_client_secret)
        form.addRow("OAuth 回调地址", self.oauth_redirect_uri)
        form.addRow("Webhook 通知地址", self.webhook_callback_url)
        oauth_layout.addLayout(form)

        self._sync_app_selector()

        actions = QHBoxLayout()
        authorize = QPushButton("新增账号授权")
        refresh = QPushButton("刷新账号")
        authorize.clicked.connect(self.authorize_requested.emit)
        refresh.clicked.connect(self.refresh_requested.emit)
        actions.addWidget(authorize)
        actions.addWidget(refresh)
        actions.addStretch(1)
        oauth_layout.addLayout(actions)

        auth_finish_layout = QHBoxLayout()
        self.callback_edit = QLineEdit()
        self.callback_edit.setPlaceholderText("粘贴浏览器授权完成后的回调链接")
        complete = QPushButton("完成授权")
        complete.setObjectName("primary")
        complete.clicked.connect(lambda: self.complete_authorization_requested.emit(self.callback_edit.text().strip()))
        self.callback_edit.returnPressed.connect(lambda: self.complete_authorization_requested.emit(self.callback_edit.text().strip()))
        auth_finish_layout.addWidget(self.callback_edit, 1)
        auth_finish_layout.addWidget(complete)
        oauth_layout.addLayout(auth_finish_layout)

        layout.addWidget(oauth_card)

        cb_card = QFrame()
        cb_card.setObjectName("controlSection")
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
        header = self.apps_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        for index, app in enumerate(self.oauth_apps):
            row = self.apps_table.rowCount()
            self.apps_table.insertRow(row)

            name_item = QTableWidgetItem(str(app.get("name") or f"应用 {index + 1}"))
            client_id = str(app.get("clientId") or "")
            id_item = QTableWidgetItem(client_id)

            secret_text = "● 已加密保存" if app.get("clientSecretConfigured") else "未设置"
            secret_item = QTableWidgetItem(secret_text)

            matching = [
                a.store_name for a in self.accounts
                if str(getattr(a, "client_id", "") or "").strip() == client_id
            ]
            bound_text = "、".join(matching) if matching else "尚未绑定店铺"
            bound_item = QTableWidgetItem(bound_text)
            bound_item.setToolTip(bound_text)

            self.apps_table.setItem(row, 0, name_item)
            self.apps_table.setItem(row, 1, id_item)
            self.apps_table.setItem(row, 2, secret_item)
            self.apps_table.setItem(row, 3, bound_item)

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(6)

            use_btn = QPushButton("选用")
            use_btn.setToolTip("选择此应用以发起授权")
            use_btn.clicked.connect(lambda _, idx=index: self._select_app_by_index(idx))

            edit_btn = QPushButton("编辑")
            edit_btn.clicked.connect(lambda _, idx=index: self._edit_app_by_index(idx))

            del_btn = QPushButton("删除")
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        concurrency_card = QFrame()
        concurrency_card.setObjectName("controlSection")
        c_layout = QVBoxLayout(concurrency_card)
        c_layout.setContentsMargins(20, 18, 20, 18)
        c_layout.setSpacing(12)

        c_title = QLabel("运行与并发参数")
        c_title.setObjectName("sectionTitle")
        c_layout.addWidget(c_title)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.auth_dir = QLineEdit(str(self.settings.get("authDir") or ""))
        self.auth_dir.setPlaceholderText("留空使用默认授权目录")

        self.read_concurrency = QSpinBox()
        self.activity_concurrency = QSpinBox()
        self.write_concurrency = QSpinBox()
        for spin in (self.read_concurrency, self.activity_concurrency, self.write_concurrency):
            spin.setFixedWidth(160)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        c_layout.addLayout(form)
        layout.addWidget(concurrency_card)

        note_card = QFrame()
        note_card.setObjectName("controlSection")
        note_layout = QVBoxLayout(note_card)
        note_layout.setContentsMargins(20, 16, 20, 16)
        note_layout.setSpacing(8)
        note_title = QLabel("并发说明")
        note_title.setObjectName("sectionTitle")
        self.benchmark_note = QLabel(benchmark_text)
        self.benchmark_note.setWordWrap(True)
        self.benchmark_note.setObjectName("muted")
        note_layout.addWidget(note_title)
        note_layout.addWidget(self.benchmark_note)
        layout.addWidget(note_card)

        layout.addStretch(1)

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
        scroll.setWidget(page)
        return scroll

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
