from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QWidget,
)


class ElidedLabel(QLabel):
    """A QLabel that automatically elides overflowing text with '...' without expanding parent layout."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._raw_text = str(text or "")

    def setText(self, text: str) -> None:
        self._raw_text = str(text or "")
        self._update_elided()

    def text(self) -> str:
        return self._raw_text

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self) -> None:
        if not self._raw_text:
            super().setText("")
            return
        w = self.width()
        if w <= 0:
            super().setText(self._raw_text)
            return
        fm = QFontMetrics(self.font())
        elided = fm.elidedText(self._raw_text, Qt.TextElideMode.ElideRight, w)
        super().setText(elided)


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
                    new_state = (
                        Qt.CheckState.Unchecked
                        if item.checkState() == Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
                    self._updating = True
                    for r in range(self._model.rowCount()):
                        it = self._model.item(r)
                        if it:
                            it.setCheckState(new_state)
                    self._updating = False
                    self._update_display_text()
                else:
                    new_state = (
                        Qt.CheckState.Unchecked
                        if item.checkState() == Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
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


def make_table(headers: list[str]) -> QTableWidget:
    """Create a standardized dark-themed QTableWidget with clean headers and row-selection behavior."""
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
