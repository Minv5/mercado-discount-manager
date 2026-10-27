from __future__ import annotations

from typing import Any, Callable
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QWidget

from engine.item_cleaner import ScannedItemRecord, is_item_confirmed_deleted

CLEANER_HEADERS = ["选择", "店铺", "商品ID", "站点", "商品标题", "刊登评分", "浏览量", "销售量", "上架时间", "状态", "不达标原因"]


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
