from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from items.vault_batch_settings import (
    BaseVaultDialog,
    DARK_TIP_LABEL_QSS,
    style_cancel_button,
    style_primary_button,
)
from ui_common.image_cache import get_placeholder_pixmap, load_image_async


def _apply_inline_numeric_rounding(value: float, rounding: str, precision: int, tail: str) -> float:
    scale = 10 ** precision
    scaled = round(value * scale, 8)
    if rounding == "ceil":
        res = math.ceil(scaled) / scale
    elif rounding == "floor":
        res = math.floor(scaled) / scale
    else:
        res = round(scaled) / scale

    clean_tail = (tail or "").strip()
    if clean_tail:
        if res < 0:
            raise ValueError("负数不能设置尾数")
        if not clean_tail.isdigit() or len(clean_tail) > 12:
            raise ValueError("尾数请输入 1 至 12 位数字")
        mod = 10 ** len(clean_tail)
        frac = round(res % 1, 2)
        res = (math.floor(math.floor(res) / mod) * mod) + int(clean_tail) + frac
    return round(res, precision if precision > 0 else 2)


def compute_batch_sku_updates(
    sku_rows: List[Dict[str, Any]],
    field: str,
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Pure computation helper implementing YuanYou BatchVariantModal rules."""
    if field == "sku":
        mode = cfg.get("mode", "set")
        text = str(cfg.get("text") or "")
        search = str(cfg.get("search") or "")
        if mode == "replace" and not search:
            raise ValueError("请输入要替换的文字")
        if mode != "replace" and not text.strip():
            raise ValueError("请输入 SKU 文字")
        out: List[Dict[str, Any]] = []
        for row in sku_rows:
            cur = str(row.get("sku") or "")
            if mode == "replace":
                nxt = cur.replace(search, text)
            elif mode == "prefix":
                nxt = text + cur
            elif mode == "suffix":
                nxt = cur + text
            else:
                nxt = text
            out.append({"sku": nxt})
        return out

    if field == "size":
        dims = cfg.get("size") or [None, None, None]
        parsed: List[Optional[float]] = []
        for val in dims[:3]:
            if val is None or str(val).strip() == "":
                parsed.append(None)
            else:
                v = float(val)
                if v < 0:
                    raise ValueError("尺寸不能小于 0")
                parsed.append(v)
        if all(x is None for x in parsed):
            raise ValueError("请至少填写一项尺寸")
        out = []
        for row in sku_rows:
            cur_dims = [
                float(row.get("length") or 0.0),
                float(row.get("width") or 0.0),
                float(row.get("height") or 0.0),
            ]
            new_dims = [parsed[i] if parsed[i] is not None else cur_dims[i] for i in range(3)]
            out.append({"length": new_dims[0], "width": new_dims[1], "height": new_dims[2]})
        return out

    mode = cfg.get("mode", "set")
    is_non_neg = field in ("weight", "stock")
    if mode == "formula":
        base_val = 0.0
    else:
        raw_val = cfg.get("value")
        if raw_val is None or str(raw_val).strip() == "":
            raise ValueError("请填写需要设置的数值")
        base_val = float(raw_val)
        if is_non_neg and base_val < 0:
            raise ValueError("不能小于 0")
        if field == "stock" and not float(base_val).is_integer():
            raise ValueError("库存必须为整数")

    multiplier = float(cfg.get("multiplier") if cfg.get("multiplier") is not None else 1.0)
    addend = float(cfg.get("addend") or 0.0)
    subtrahend = float(cfg.get("subtrahend") or 0.0)
    minimum = int(cfg.get("minimum") or 0)
    rounding = str(cfg.get("rounding") or "round")
    precision = int(cfg.get("precision") if cfg.get("precision") is not None else 2)
    tail = str(cfg.get("tail") or "")

    out = []
    for row in sku_rows:
        cur_raw = row.get(field)
        cur_num = float(cur_raw or 0.0)
        if mode == "formula":
            nxt = cur_num * multiplier + addend - subtrahend
            nxt = _apply_inline_numeric_rounding(nxt, rounding, precision, tail)
        elif mode == "add":
            nxt = cur_num + base_val
        elif mode == "subtract":
            nxt = max(float(minimum if field == "stock" else 0.0), cur_num - base_val)
        else:
            nxt = base_val

        if is_non_neg and nxt < 0:
            nxt = 0.0
        if field == "stock":
            out.append({"stock": int(round(nxt))})
        elif field == "weight":
            out.append({"weight": round(nxt, 1)})
        else:
            out.append({field: round(nxt, 2)})
    return out


class VariantImagePickerDialog(BaseVaultDialog):
    """1:1 YuanYou GlobalChooseImage dialog for picking ordered images for a SKU."""

    def __init__(
        self,
        available_images: List[str],
        selected_images: Optional[List[str]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__("选择变体图片", parent)
        self.resize(740, 520)
        self._available = list(dict.fromkeys([u for u in available_images if str(u).strip()]))
        self._selected: List[str] = [u for u in (selected_images or []) if u in self._available]
        self._cards: Dict[str, QPushButton] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        lbl = QLabel(f"商品图片 <b>{len(self._available)}</b> 张  <span style='color:#9A9589'>点击按选择顺序排序，再次点击取消</span>")
        hdr.addWidget(lbl)
        hdr.addStretch()

        btn_all = QPushButton("全选")
        style_cancel_button(btn_all)
        btn_all.clicked.connect(self._select_all)
        hdr.addWidget(btn_all)

        btn_clear = QPushButton("清空选择")
        style_cancel_button(btn_clear)
        btn_clear.clicked.connect(self._clear_all)
        hdr.addWidget(btn_clear)
        root.addLayout(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(body)
        self._grid.setSpacing(10)

        if not self._available:
            empty = QLabel("暂无可选商品图片，请先在下方「图片管理」中添加图片。")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(DARK_TIP_LABEL_QSS)
            self._grid.addWidget(empty, 0, 0)
        else:
            for idx, src in enumerate(self._available):
                btn = QPushButton()
                btn.setCheckable(True)
                btn.setFixedSize(124, 134)
                btn.setIconSize(QSize(92, 92))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _, s=src: self._toggle_image(s))
                self._set_button_thumb(btn, src)
                self._cards[src] = btn
                r, c = divmod(idx, 5)
                self._grid.addWidget(btn, r, c)

        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        footer = QHBoxLayout()
        self.lbl_count = QLabel("")
        footer.addWidget(self.lbl_count)
        footer.addStretch()

        btn_cancel = QPushButton("取 消")
        style_cancel_button(btn_cancel)
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        btn_ok = QPushButton("确定选择")
        style_primary_button(btn_ok)
        btn_ok.clicked.connect(self.accept)
        footer.addWidget(btn_ok)
        root.addLayout(footer)

        self._refresh_selection_badges()

    @staticmethod
    def _set_button_thumb(btn: QPushButton, src: str) -> None:
        if src.startswith(("http://", "https://")):
            btn.setIcon(QIcon(get_placeholder_pixmap(QSize(92, 92), "🖼️", radius=6)))
            load_image_async(
                src,
                QSize(92, 92),
                on_loaded=lambda pm, b=btn: b.setIcon(QIcon(pm)) if not pm.isNull() else None,
                radius=6,
            )
        elif Path(src).exists():
            pm = QPixmap(src)
            if not pm.isNull():
                btn.setIcon(
                    QIcon(pm.scaled(92, 92, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                )
            else:
                btn.setIcon(QIcon(get_placeholder_pixmap(QSize(92, 92), "🖼️", radius=6)))
        else:
            btn.setIcon(QIcon(get_placeholder_pixmap(QSize(92, 92), "🖼️", radius=6)))

    def _toggle_image(self, src: str) -> None:
        if src in self._selected:
            self._selected.remove(src)
        else:
            self._selected.append(src)
        self._refresh_selection_badges()

    def _select_all(self) -> None:
        self._selected = list(self._available)
        self._refresh_selection_badges()

    def _clear_all(self) -> None:
        self._selected.clear()
        self._refresh_selection_badges()

    def _refresh_selection_badges(self) -> None:
        for idx, src in enumerate(self._available):
            btn = self._cards.get(src)
            if not btn:
                continue
            if src in self._selected:
                order = self._selected.index(src) + 1
                btn.setChecked(True)
                btn.setText(f"#{order}")
                btn.setStyleSheet(
                    "QPushButton { background: #203A2B; color: #4ADE80; border: 2px solid #4ADE80; "
                    "border-radius: 8px; padding: 4px; font-size: 11px; font-weight: 700; }"
                )
            else:
                btn.setChecked(False)
                btn.setText(f"图{idx + 1}")
                btn.setStyleSheet(
                    "QPushButton { background: #18201C; color: #C8C3B7; border: 1px solid #4E472F; "
                    "border-radius: 8px; padding: 4px; font-size: 11px; }"
                    "QPushButton:hover { border-color: #6E5D2A; color: #FFFFFF; }"
                )
        self.lbl_count.setText(f"已选择 <b>{len(self._selected)}</b> 张  <span style='color:#9A9589'>按选择顺序排列</span>")

    def get_selected_images(self) -> List[str]:
        return list(self._selected)


class BatchVariantColumnDialog(BaseVaultDialog):
    """1:1 YuanYou BatchVariantModal supporting formulas, integer tailing, L/W/H partial update, and scoped SKU targeting."""

    _FIELD_LABELS = {
        "netProceed": "净收益 (USD)",
        "weight": "重量 (g)",
        "size": "尺寸 (cm)",
        "sku": "SKU",
        "stock": "库存",
    }

    def __init__(
        self,
        field: str,
        sku_rows: List[Dict[str, Any]],
        parent: Optional[QWidget] = None,
    ):
        self.field = field
        self.sku_rows = [dict(r) for r in sku_rows]
        super().__init__(f"批量修改{self._FIELD_LABELS.get(field, field)}", parent)
        self.resize(620, 480)
        self._result_updates: Dict[int, Dict[str, Any]] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        lbl_hint = QLabel(f"应用后将在下方「SKU 列表」生效。共 {len(self.sku_rows)} 个变体。")
        lbl_hint.setStyleSheet(DARK_TIP_LABEL_QSS)
        root.addWidget(lbl_hint)

        # Scope Selector (全部 SKU vs 指定 SKU)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("修改范围:"))
        self.bg_scope = QButtonGroup(self)
        self.radio_all = QRadioButton(f"全部 SKU ({len(self.sku_rows)})")
        self.radio_selected = QRadioButton("指定 SKU")
        self.radio_all.setChecked(True)
        self.bg_scope.addButton(self.radio_all, 1)
        self.bg_scope.addButton(self.radio_selected, 2)
        scope_row.addWidget(self.radio_all)
        scope_row.addWidget(self.radio_selected)
        scope_row.addStretch()
        root.addLayout(scope_row)

        # Scoped SKU Multi-Select Checklist
        self.scope_box = QFrame()
        self.scope_box.setObjectName("BatchScopeBox")
        self.scope_box.setStyleSheet(
            "QFrame#BatchScopeBox { background: #202820; border: 1px solid #4E472F; border-radius: 6px; }"
        )
        sb_l = QVBoxLayout(self.scope_box)
        sb_l.setContentsMargins(10, 8, 10, 8)
        sb_l.setSpacing(6)

        filter_row = QHBoxLayout()
        self.edt_scope_search = QLineEdit()
        self.edt_scope_search.setPlaceholderText("搜索 SKU 编码或规格名称...")
        self.edt_scope_search.textChanged.connect(self._filter_scope_list)
        filter_row.addWidget(self.edt_scope_search, stretch=1)

        btn_check_vis = QPushButton("选中筛选结果")
        style_cancel_button(btn_check_vis)
        btn_check_vis.clicked.connect(self._check_visible_scope)
        filter_row.addWidget(btn_check_vis)

        btn_clear_scope = QPushButton("清空")
        style_cancel_button(btn_clear_scope)
        btn_clear_scope.clicked.connect(self._clear_scope)
        filter_row.addWidget(btn_clear_scope)
        sb_l.addLayout(filter_row)

        self.scope_list = QListWidget()
        self.scope_list.setMaximumHeight(120)
        self.scope_list.setStyleSheet(
            "QListWidget { background: #18201C; border: 1px solid #4E472F; border-radius: 4px; color: #E6E2D8; }"
        )
        for idx, row in enumerate(self.sku_rows):
            it = QListWidgetItem(f"{row.get('kvId') or '—'}   |   SKU: {row.get('sku') or '—'}")
            it.setData(Qt.ItemDataRole.UserRole, idx)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.scope_list.addItem(it)
        sb_l.addWidget(self.scope_list)
        root.addWidget(self.scope_box)
        self.scope_box.setVisible(False)
        self.radio_selected.toggled.connect(self.scope_box.setVisible)

        # Field-specific editor panel
        self._init_field_controls(root)

        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("color: #F87171; font-size: 12px;")
        self.lbl_error.setVisible(False)
        root.addWidget(self.lbl_error)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("取 消")
        style_cancel_button(btn_cancel)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        self.btn_apply = QPushButton("应用修改")
        style_primary_button(self.btn_apply)
        self.btn_apply.clicked.connect(self._on_apply)
        btn_box.addWidget(self.btn_apply)
        root.addLayout(btn_box)

    def _init_field_controls(self, root: QVBoxLayout) -> None:
        if self.field == "size":
            row = QHBoxLayout()
            row.setSpacing(10)
            self.edt_l = QLineEdit()
            self.edt_l.setPlaceholderText("长 (cm) 留空保持原值")
            self.edt_w = QLineEdit()
            self.edt_w.setPlaceholderText("宽 (cm) 留空保持原值")
            self.edt_h = QLineEdit()
            self.edt_h.setPlaceholderText("高 (cm) 留空保持原值")
            for lbl_txt, ed in [("长:", self.edt_l), ("宽:", self.edt_w), ("高:", self.edt_h)]:
                row.addWidget(QLabel(lbl_txt))
                row.addWidget(ed, stretch=1)
            root.addLayout(row)
            return

        # Mode selector
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("调整方式:"))
        self.bg_mode = QButtonGroup(self)
        modes = (
            [("set", "统一设置"), ("replace", "替换文字"), ("prefix", "添加前缀"), ("suffix", "添加后缀")]
            if self.field == "sku"
            else ([("set", "统一设置"), ("formula", "使用公式")] if self.field == "netProceed" else [("set", "统一设置"), ("add", "原值增加"), ("subtract", "原值减少")])
        )
        self._mode_ids: Dict[int, str] = {}
        for mid, (m_key, m_label) in enumerate(modes, 1):
            rb = QRadioButton(m_label)
            if mid == 1:
                rb.setChecked(True)
            self.bg_mode.addButton(rb, mid)
            self._mode_ids[mid] = m_key
            mode_row.addWidget(rb)
        mode_row.addStretch()
        root.addLayout(mode_row)

        # Standard value container
        self.val_box = QWidget()
        vl = QVBoxLayout(self.val_box)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(6)

        if self.field == "sku":
            self.edt_sku_search = QLineEdit()
            self.edt_sku_search.setPlaceholderText("查找文字")
            self.edt_sku_search.setVisible(False)
            vl.addWidget(self.edt_sku_search)

            self.edt_sku_text = QLineEdit()
            self.edt_sku_text.setPlaceholderText("输入 SKU 文字")
            vl.addWidget(self.edt_sku_text)
        else:
            self.edt_val = QLineEdit()
            self.edt_val.setPlaceholderText("请输入目标数值")
            vl.addWidget(self.edt_val)

            self.edt_min_stock = QLineEdit("0")
            self.edt_min_stock.setPlaceholderText("减少后不低于 (整数)")
            self.edt_min_stock.setVisible(False)
            vl.addWidget(self.edt_min_stock)
        root.addWidget(self.val_box)

        # Formula box for netProceed
        self.formula_box = QWidget()
        if self.field == "netProceed":
            fl = QVBoxLayout(self.formula_box)
            fl.setContentsMargins(0, 0, 0, 0)
            fl.setSpacing(8)
            r1 = QHBoxLayout()
            r1.addWidget(QLabel("原净收益 ×"))
            self.edt_mult = QLineEdit("1")
            self.edt_add = QLineEdit("0")
            self.edt_sub = QLineEdit("0")
            r1.addWidget(self.edt_mult)
            r1.addWidget(QLabel("＋"))
            r1.addWidget(self.edt_add)
            r1.addWidget(QLabel("－"))
            r1.addWidget(self.edt_sub)
            fl.addLayout(r1)

            r2 = QHBoxLayout()
            r2.addWidget(QLabel("取整:"))
            self.combo_round = QComboBox()
            self.combo_round.addItem("四舍五入", "round")
            self.combo_round.addItem("向上取整", "ceil")
            self.combo_round.addItem("向下取整", "floor")
            r2.addWidget(self.combo_round)

            r2.addWidget(QLabel("小数:"))
            self.combo_prec = QComboBox()
            self.combo_prec.addItem("2 位小数", 2)
            self.combo_prec.addItem("1 位小数", 1)
            self.combo_prec.addItem("整数", 0)
            r2.addWidget(self.combo_prec)

            r2.addWidget(QLabel("尾数:"))
            self.edt_tail = QLineEdit()
            self.edt_tail.setPlaceholderText("可选如 0 或 99")
            r2.addWidget(self.edt_tail)
            fl.addLayout(r2)
        root.addWidget(self.formula_box)
        self.formula_box.setVisible(False)

        self.bg_mode.idToggled.connect(self._on_mode_switched)

    def _on_mode_switched(self, mid: int, checked: bool) -> None:
        if not checked:
            return
        mode = self._mode_ids.get(mid, "set")
        if self.field == "netProceed":
            self.formula_box.setVisible(mode == "formula")
            self.val_box.setVisible(mode != "formula")
        elif self.field == "sku":
            self.edt_sku_search.setVisible(mode == "replace")
            self.edt_sku_text.setPlaceholderText(
                "替换为（留空即删除）" if mode == "replace" else "输入 SKU 文字"
            )
        elif self.field == "stock":
            self.edt_min_stock.setVisible(mode == "subtract")

    def _filter_scope_list(self) -> None:
        kw = self.edt_scope_search.text().strip().lower()
        for i in range(self.scope_list.count()):
            it = self.scope_list.item(i)
            it.setHidden(bool(kw and kw not in it.text().lower()))

    def _check_visible_scope(self) -> None:
        for i in range(self.scope_list.count()):
            it = self.scope_list.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.CheckState.Checked)

    def _clear_scope(self) -> None:
        for i in range(self.scope_list.count()):
            self.scope_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _target_indices(self) -> List[int]:
        if self.radio_all.isChecked():
            return list(range(len(self.sku_rows)))
        indices = []
        for i in range(self.scope_list.count()):
            it = self.scope_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                indices.append(int(it.data(Qt.ItemDataRole.UserRole)))
        return indices

    def _on_apply(self) -> None:
        self.lbl_error.setVisible(False)
        indices = self._target_indices()
        if not indices:
            self.lbl_error.setText("请选择至少 1 个需要修改的 SKU")
            self.lbl_error.setVisible(True)
            return

        target_rows = [self.sku_rows[i] for i in indices]
        try:
            if self.field == "size":
                cfg: Dict[str, Any] = {
                    "size": [self.edt_l.text().strip(), self.edt_w.text().strip(), self.edt_h.text().strip()]
                }
            else:
                mode = self._mode_ids.get(self.bg_mode.checkedId(), "set")
                if self.field == "sku":
                    cfg = {
                        "mode": mode,
                        "search": self.edt_sku_search.text(),
                        "text": self.edt_sku_text.text(),
                    }
                elif self.field == "netProceed" and mode == "formula":
                    cfg = {
                        "mode": "formula",
                        "multiplier": float(self.edt_mult.text().strip() or "1"),
                        "addend": float(self.edt_add.text().strip() or "0"),
                        "subtrahend": float(self.edt_sub.text().strip() or "0"),
                        "rounding": self.combo_round.currentData(),
                        "precision": self.combo_prec.currentData(),
                        "tail": self.edt_tail.text().strip(),
                    }
                else:
                    cfg = {
                        "mode": mode,
                        "value": self.edt_val.text().strip(),
                        "minimum": int(self.edt_min_stock.text().strip() or "0") if self.field == "stock" else 0,
                    }

            updates = compute_batch_sku_updates(target_rows, self.field, cfg)
            self._result_updates = {idx: upd for idx, upd in zip(indices, updates)}
            self.accept()
        except Exception as exc:
            self.lbl_error.setText(str(exc))
            self.lbl_error.setVisible(True)

    def get_row_updates(self) -> Dict[int, Dict[str, Any]]:
        return self._result_updates


class BatchHeaderView(QHeaderView):
    """Table header rendering an inline clickable '批量' link right next to batchable column titles."""

    batch_clicked = Signal(int)

    def __init__(self, batch_columns: set[int], parent: Optional[QWidget] = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._batch_cols = set(batch_columns)
        self._badge_rects: Dict[int, QRect] = {}
        self.setMouseTracking(True)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:
        if logicalIndex not in self._batch_cols:
            self._badge_rects.pop(logicalIndex, None)
            super().paintSection(painter, rect, logicalIndex)
            return

        # Paint background/border cleanly via base header, keeping text suppressed by clipping or redraw
        painter.save()
        painter.fillRect(rect, QColor("#202820"))
        painter.setPen(QColor("#2E362F"))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.setPen(QColor("#4E472F"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        # Column title on left side (clear of badge on right)
        text = str(self.model().headerData(logicalIndex, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
        text_rect = rect.adjusted(6, 0, -44, 0)
        hf = QFont(self.font())
        hf.setBold(True)
        hf.setPointSize(11)
        painter.setFont(hf)
        painter.setPen(QColor("#C8C3B7"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

        badge_w = 34
        badge_h = 18
        badge_rect = QRect(
            rect.right() - badge_w - 6,
            rect.center().y() - badge_h // 2,
            badge_w,
            badge_h,
        )
        self._badge_rects[logicalIndex] = badge_rect
        painter.setPen(QPen(QColor("#2F6B3F"), 1))
        painter.setBrush(QColor(47, 107, 63, 70))
        painter.drawRoundedRect(badge_rect, 4, 4)

        f = QFont(self.font())
        f.setPointSize(10)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor("#4ADE80"))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "批量")
        painter.restore()

    def mousePressEvent(self, event) -> None:
        idx = self.logicalIndexAt(event.position().toPoint())
        if idx in self._batch_cols and idx in self._badge_rects:
            if self._badge_rects[idx].contains(event.position().toPoint()):
                self.batch_clicked.emit(idx)
                return
        super().mousePressEvent(event)


class VariantDimensionCard(QFrame):
    """1:1 YuanYou VariantHeader editor with editable dimension name + multi-column value inputs + '+ 新增规格'."""

    changed = Signal()
    delete_requested = Signal(object)

    def __init__(self, dim_name: str, initial_values: Optional[List[str]] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("VaultDimBox")
        self.setStyleSheet(
            "QFrame#VaultDimBox { background: #18201C; border: 1px solid #4E472F; border-radius: 6px; }"
        )
        self._value_rows: List[tuple[QLineEdit, QWidget]] = []
        self._init_ui(dim_name, initial_values or [])

    def _init_ui(self, dim_name: str, initial_values: List[str]) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Header row: 变体名称 [Editable Combo] [x Delete]
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        lbl = QLabel("变体名称:")
        lbl.setStyleSheet("color: #E6E2D8; font-weight: 600;")
        hdr.addWidget(lbl)

        self.combo_name = QComboBox()
        self.combo_name.setEditable(True)
        self.combo_name.setMinimumWidth(180)
        for sug in ["颜色", "尺码", "规格", "款式", "材质"]:
            self.combo_name.addItem(sug)
        self.combo_name.setCurrentText(dim_name)
        self.combo_name.currentTextChanged.connect(lambda _: self.changed.emit())
        hdr.addWidget(self.combo_name)

        btn_rm = QPushButton("✕")
        btn_rm.setFixedSize(26, 26)
        btn_rm.setToolTip("删除此变体维度")
        btn_rm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_rm.setStyleSheet(
            "QPushButton { background: transparent; color: #F87171; border: none; font-size: 13px; font-weight: bold; }"
            "QPushButton:hover { color: #FCA5A5; }"
        )
        btn_rm.clicked.connect(lambda: self.delete_requested.emit(self))
        hdr.addWidget(btn_rm)
        hdr.addStretch()
        root.addLayout(hdr)

        # Multi-column grid for spec value inputs (3 columns per row)
        self.grid_wrap = QWidget()
        self.grid_wrap.setStyleSheet("background: transparent;")
        self.val_grid = QGridLayout(self.grid_wrap)
        self.val_grid.setContentsMargins(0, 0, 0, 0)
        self.val_grid.setHorizontalSpacing(10)
        self.val_grid.setVerticalSpacing(8)
        root.addWidget(self.grid_wrap)

        for v in initial_values:
            self._create_value_cell(v, emit_change=False)
        self._relayout_grid()

        # Full-width '+ 新增规格' button
        btn_add_spec = QPushButton("＋ 新增规格")
        style_primary_button(btn_add_spec)
        btn_add_spec.clicked.connect(lambda: self.add_spec_value(""))
        root.addWidget(btn_add_spec)

    def _create_value_cell(self, text: str, emit_change: bool = True) -> QLineEdit:
        cell = QWidget()
        cell.setStyleSheet("background: transparent;")
        cl = QHBoxLayout(cell)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)

        ed = QLineEdit(text)
        ed.setPlaceholderText("输入规格值...")
        ed.textChanged.connect(lambda _: self._on_spec_edited())
        cl.addWidget(ed, stretch=1)

        btn_del = QPushButton("🗑")
        btn_del.setFixedSize(28, 28)
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.setToolTip("删除该规格值")
        btn_del.setStyleSheet(
            "QPushButton { background: #202820; color: #C8C3B7; border: 1px solid #4E472F; border-radius: 5px; font-size: 12px; }"
            "QPushButton:hover { color: #F87171; border-color: #EF4444; }"
        )
        btn_del.clicked.connect(lambda _, c=cell, e=ed: self._remove_spec_cell(c, e))
        cl.addWidget(btn_del)

        self._value_rows.append((ed, cell))
        if emit_change:
            self.changed.emit()
        return ed

    def _relayout_grid(self) -> None:
        while self.val_grid.count():
            self.val_grid.takeAt(0)
        for idx, (_, cell) in enumerate(self._value_rows):
            r, c = divmod(idx, 3)
            self.val_grid.addWidget(cell, r, c)
        for col_i in range(3):
            self.val_grid.setColumnStretch(col_i, 1)

    def _on_spec_edited(self) -> None:
        vals = [ed.text().strip() for ed, _ in self._value_rows]
        for ed, _ in self._value_rows:
            cur = ed.text().strip()
            dup = bool(cur and vals.count(cur) > 1)
            ed.setStyleSheet(
                "QLineEdit { border: 1px solid #EF4444; background: #2A1616; color: #FCA5A5; }"
                if dup
                else ""
            )
        self.changed.emit()

    def add_spec_value(self, text: str = "") -> QLineEdit:
        ed = self._create_value_cell(text, emit_change=True)
        self._relayout_grid()
        ed.setFocus()
        return ed

    def _remove_spec_cell(self, cell: QWidget, ed: QLineEdit) -> None:
        self._value_rows = [(e, w) for e, w in self._value_rows if e is not ed]
        cell.setParent(None)
        cell.deleteLater()
        self._relayout_grid()
        self.changed.emit()

    def dimension_name(self) -> str:
        return self.combo_name.currentText().strip()

    def values(self) -> List[str]:
        seen = set()
        out = []
        for ed, _ in self._value_rows:
            v = ed.text().strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out


class VariantSectionWidget(QWidget):
    """Complete 1:1 YuanYou Variant Dimension + Interactive SKU Table widget."""

    _COL_BATCH_FIELDS = {
        1: "netProceed",
        2: "weight",
        3: "size",
        4: "sku",
        5: "stock",
    }

    def __init__(
        self,
        get_defaults_fn,
        get_images_fn,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._get_defaults = get_defaults_fn
        self._get_images = get_images_fn
        self._dim_cards: List[VariantDimensionCard] = []
        self._row_images: Dict[int, List[str]] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # Top Add Dimension Bar
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.combo_var_dim = QComboBox()
        self.combo_var_dim.setEditable(True)
        self.combo_var_dim.setMinimumWidth(330)
        for sug in ["颜色", "尺码", "规格", "款式", "材质"]:
            self.combo_var_dim.addItem(sug)
        self.combo_var_dim.setCurrentText("")
        if self.combo_var_dim.lineEdit():
            self.combo_var_dim.lineEdit().setPlaceholderText("输入或选择分类建议的变体名称（如 颜色 / 尺码）")
        add_row.addWidget(self.combo_var_dim)

        btn_add_dim = QPushButton("新增变体")
        style_primary_button(btn_add_dim)
        btn_add_dim.clicked.connect(self.add_dimension_from_input)
        add_row.addWidget(btn_add_dim)

        btn_direct_sku = QPushButton("＋ 直接添加 SKU 行")
        style_cancel_button(btn_direct_sku)
        btn_direct_sku.clicked.connect(lambda: self._append_sku_row("默认规格"))
        add_row.addWidget(btn_direct_sku)
        add_row.addStretch()
        root.addLayout(add_row)

        # Container for VariantDimensionCards
        self.dims_layout = QVBoxLayout()
        self.dims_layout.setContentsMargins(0, 0, 0, 0)
        self.dims_layout.setSpacing(8)
        root.addLayout(self.dims_layout)

        # SKU List Header
        sku_hdr = QHBoxLayout()
        self.lbl_sku_count = QLabel("<b>SKU 列表</b>  <span style='color:#9A9589'>0 个变体</span>")
        self.lbl_sku_count.setStyleSheet("color: #F6F3EA; font-size: 13px;")
        sku_hdr.addWidget(self.lbl_sku_count)
        sku_hdr.addStretch()
        lbl_tip = QLabel("💡 点击表头绿色「批量」可按公式或指定 SKU 批量修改")
        lbl_tip.setStyleSheet(DARK_TIP_LABEL_QSS)
        sku_hdr.addWidget(lbl_tip)
        root.addLayout(sku_hdr)

        # SKU Table with clickable BatchHeaderView & fixed row height 44px
        self.sku_table = QTableWidget()
        self.header = BatchHeaderView(set(self._COL_BATCH_FIELDS.keys()), self.sku_table)
        self.header.batch_clicked.connect(self._open_column_batch_modal)
        self.sku_table.setHorizontalHeader(self.header)
        self.sku_table.verticalHeader().setVisible(False)
        self.sku_table.verticalHeader().setDefaultSectionSize(44)
        self.sku_table.verticalHeader().setMinimumSectionSize(44)

        cols = ["变体", "净收益(USD)", "重量(g)", "尺寸(cm)", "SKU", "库存", "变体图片", "操作"]
        self.sku_table.setColumnCount(len(cols))
        self.sku_table.setHorizontalHeaderLabels(cols)
        self.header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sku_table.setColumnWidth(0, 120)
        for ci, w in [(1, 146), (2, 108), (3, 198), (4, 120), (5, 92), (6, 110), (7, 130)]:
            self.header.setSectionResizeMode(ci, QHeaderView.ResizeMode.Fixed)
            self.sku_table.setColumnWidth(ci, w)
        self.sku_table.setMinimumHeight(232)
        root.addWidget(self.sku_table)

        self.lbl_empty = QLabel("暂无变体数据（上方新增变体并填入规格值，或点击「直接添加 SKU 行」）")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setStyleSheet(
            "background: #18201C; color: #9A9589; border: 1px dashed #4E472F; "
            "border-radius: 6px; padding: 14px; font-size: 12px;"
        )
        root.addWidget(self.lbl_empty)
        self._refresh_empty_state()

    def add_dimension_from_input(self) -> None:
        name = self.combo_var_dim.currentText().strip()
        if not name:
            QMessageBox.warning(self, "提示", "变体名称不能为空")
            return
        if any(c.dimension_name().lower() == name.lower() for c in self._dim_cards):
            QMessageBox.warning(self, "提示", "变体名称已存在，不能重复添加")
            return
        if self.combo_var_dim.lineEdit():
            self.combo_var_dim.lineEdit().clear()
        self.add_dimension(name, [""])

    def add_dimension(self, name: str, values: Optional[List[str]] = None) -> VariantDimensionCard:
        card = VariantDimensionCard(name, values or [], self)
        card.changed.connect(self.rebuild_sku_rows)
        card.delete_requested.connect(self._remove_dimension_card)
        self._dim_cards.append(card)
        self.dims_layout.addWidget(card)
        return card

    def _remove_dimension_card(self, card: VariantDimensionCard) -> None:
        if card in self._dim_cards:
            self._dim_cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self.rebuild_sku_rows()

    def _snapshot_rows(self) -> Dict[str, Dict[str, Any]]:
        snap: Dict[str, Dict[str, Any]] = {}
        for row_dict in self.get_variants():
            snap[row_dict["kvId"]] = row_dict
        return snap

    def rebuild_sku_rows(self) -> None:
        dim_lists = [c.values() for c in self._dim_cards if c.values()]
        prev = self._snapshot_rows()
        prev_list = self.get_variants()
        self.sku_table.setRowCount(0)
        self._row_images.clear()
        if not dim_lists:
            self._refresh_empty_state()
            return

        combos = ["_".join(pair) for pair in itertools.product(*dim_lists)]
        positional_reuse = len(prev_list) == len(combos) and len(combos) > 0
        for idx, kv_id in enumerate(combos):
            existing = prev.get(kv_id) or (prev_list[idx] if positional_reuse else None)
            self._append_sku_row(kv_id, existing=existing)
        self._refresh_empty_state()

    def _append_sku_row(self, kv_name: str, existing: Optional[Dict[str, Any]] = None) -> None:
        r = self.sku_table.rowCount()
        self.sku_table.insertRow(r)
        defaults = self._get_defaults()

        net_v = float(existing.get("netProceed") if existing and existing.get("netProceed") is not None else (existing.get("profit") if existing and existing.get("profit") is not None else (existing.get("price") if existing and existing.get("price") is not None else defaults.get("net_proceed", 0.0))))
        w_v = float(existing.get("weight") if existing and existing.get("weight") is not None else defaults.get("weight", 500.0))
        l_v = float(existing.get("length") if existing and existing.get("length") is not None else defaults.get("length", 0.0))
        wi_v = float(existing.get("width") if existing and existing.get("width") is not None else defaults.get("width", 0.0))
        h_v = float(existing.get("height") if existing and existing.get("height") is not None else defaults.get("height", 0.0))
        sku_v = str((existing or {}).get("sku") or f"{kv_name}")
        stock_v = int((existing or {}).get("stock") if existing and existing.get("stock") is not None else defaults.get("stock", 50))
        imgs_v = list((existing or {}).get("images") or [])

        # Col 0: Variant Name
        lbl_var = QLabel(kv_name)
        lbl_var.setStyleSheet("color: #F6F3EA; font-size: 12px; padding: 0 6px; background: transparent;")
        self.sku_table.setCellWidget(r, 0, lbl_var)

        # Col 1: Net Proceed
        spn_net = QDoubleSpinBox()
        spn_net.setRange(-9999.0, 99999.0)
        spn_net.setDecimals(2)
        spn_net.setValue(net_v)
        spn_net.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spn_net.setStyleSheet("min-height: 26px; max-height: 30px; padding: 2px 6px;")
        self.sku_table.setCellWidget(r, 1, spn_net)

        # Col 2: Weight
        spn_w = QSpinBox()
        spn_w.setRange(0, 999999)
        spn_w.setValue(int(round(w_v)))
        spn_w.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spn_w.setStyleSheet("min-height: 26px; max-height: 30px; padding: 2px 6px;")
        self.sku_table.setCellWidget(r, 2, spn_w)

        # Col 3: 3 Independent Inline Size Boxes (长 / 宽 / 高)
        sz_wrap = QWidget()
        sz_wrap.setStyleSheet("background: transparent;")
        sz_l = QHBoxLayout(sz_wrap)
        sz_l.setContentsMargins(3, 4, 3, 4)
        sz_l.setSpacing(3)
        for val, ph in ((l_v, "长"), (wi_v, "宽"), (h_v, "高")):
            d_spn = QDoubleSpinBox()
            d_spn.setRange(0.0, 999.0)
            d_spn.setDecimals(0)
            d_spn.setValue(val)
            d_spn.setPrefix(f"{ph}:")
            d_spn.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            d_spn.setStyleSheet("min-height: 24px; max-height: 28px; padding: 1px 4px; font-size: 11px;")
            sz_l.addWidget(d_spn, stretch=1)
        self.sku_table.setCellWidget(r, 3, sz_wrap)

        # Col 4: SKU
        edt_sku = QLineEdit(sku_v)
        edt_sku.setStyleSheet("min-height: 26px; max-height: 30px; padding: 2px 6px;")
        self.sku_table.setCellWidget(r, 4, edt_sku)

        # Col 5: Stock
        spn_stk = QSpinBox()
        spn_stk.setRange(0, 999999)
        spn_stk.setValue(stock_v)
        spn_stk.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spn_stk.setStyleSheet("min-height: 26px; max-height: 30px; padding: 2px 6px;")
        self.sku_table.setCellWidget(r, 5, spn_stk)

        # Col 6: Variant Image Picker Button
        btn_img = QPushButton()
        btn_img.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_img.clicked.connect(lambda _, b=btn_img: self._pick_row_images(b))
        self._row_images[r] = imgs_v
        self._style_row_img_button(btn_img, imgs_v)
        self.sku_table.setCellWidget(r, 6, btn_img)

        # Col 7: Action ('▼ 图片应用到' + compact delete button)
        act_wrap = QWidget()
        act_wrap.setStyleSheet("background: transparent;")
        al = QHBoxLayout(act_wrap)
        al.setContentsMargins(4, 4, 4, 4)
        al.setSpacing(4)
        btn_apply = QPushButton("▼ 图片应用到")
        btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_apply.setStyleSheet(
            "QPushButton { background: #18201C; color: #60A5FA; border: 1px solid #4E472F; "
            "border-radius: 5px; padding: 3px 6px; font-size: 11px; }"
            "QPushButton:hover { border-color: #60A5FA; background: #202820; }"
        )
        btn_apply.clicked.connect(lambda _, w=btn_apply: self._show_apply_images_menu(w))
        al.addWidget(btn_apply, stretch=1)

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(22, 24)
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.setStyleSheet(
            "QPushButton { background: transparent; color: #F87171; border: none; font-size: 12px; font-weight: bold; padding: 0px; }"
            "QPushButton:hover { color: #FCA5A5; }"
        )
        btn_del.clicked.connect(lambda _, aw=act_wrap: self._delete_row_by_widget(aw))
        al.addWidget(btn_del)
        self.sku_table.setCellWidget(r, 7, act_wrap)
        self._refresh_empty_state()

    def _find_row_of_widget(self, col: int, widget: QWidget) -> int:
        for r in range(self.sku_table.rowCount()):
            if self.sku_table.cellWidget(r, col) is widget:
                return r
        return -1

    def _style_row_img_button(self, btn: QPushButton, imgs: List[str]) -> None:
        if imgs:
            btn.setText(f"🖼 已选 {len(imgs)} 张")
            btn.setStyleSheet(
                "QPushButton { background: #203A2B; color: #4ADE80; border: 1px solid #2F6B3F; "
                "border-radius: 5px; padding: 3px 8px; font-size: 11px; font-weight: 600; }"
            )
        else:
            btn.setText("＋ 选择图片")
            btn.setStyleSheet(
                "QPushButton { background: #18201C; color: #C8C3B7; border: 1px dashed #4E472F; "
                "border-radius: 5px; padding: 3px 8px; font-size: 11px; }"
                "QPushButton:hover { border-color: #4ADE80; color: #4ADE80; }"
            )

    def _pick_row_images(self, btn: QPushButton) -> None:
        row_idx = self._find_row_of_widget(6, btn)
        if row_idx < 0:
            return
        available = self._get_images()
        current = self._row_images.get(row_idx, [])
        dlg = VariantImagePickerDialog(available, current, self)
        if dlg.exec():
            chosen = dlg.get_selected_images()
            self._row_images[row_idx] = chosen
            self._style_row_img_button(btn, chosen)

    def _show_apply_images_menu(self, btn_apply: QPushButton) -> None:
        row_idx = -1
        for r in range(self.sku_table.rowCount()):
            w = self.sku_table.cellWidget(r, 7)
            if w and btn_apply in w.findChildren(QPushButton):
                row_idx = r
                break
        if row_idx < 0:
            return
        src_imgs = list(self._row_images.get(row_idx) or [])
        if not src_imgs:
            QMessageBox.warning(self, "提示", "当前变体还没有选择图片，请先点击左侧「＋ 选择图片」。")
            return

        kv_lbl = self.sku_table.cellWidget(row_idx, 0)
        kv_id = kv_lbl.text() if isinstance(kv_lbl, QLabel) else ""
        tokens = [t for t in kv_id.split("_") if t]

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1C231F; border: 1px solid #4E472F; color: #E6E2D8; padding: 4px; }"
            "QMenu::item { padding: 5px 14px; border-radius: 4px; }"
            "QMenu::item:selected { background: #203A2B; color: #FFFFFF; }"
        )
        act_all = menu.addAction("所有变种")
        act_all.triggered.connect(lambda: self._apply_images_to_targets(row_idx, src_imgs, None))
        for tok in tokens:
            act_tok = menu.addAction(f"同「{tok}」的变种")
            act_tok.triggered.connect(lambda _, tk=tok: self._apply_images_to_targets(row_idx, src_imgs, tk))
        menu.exec(btn_apply.mapToGlobal(btn_apply.rect().bottomLeft()))

    def _apply_images_to_targets(self, source_row: int, imgs: List[str], match_token: Optional[str], notify: bool = True) -> None:
        applied = 0
        for r in range(self.sku_table.rowCount()):
            if r == source_row:
                continue
            kv_lbl = self.sku_table.cellWidget(r, 0)
            kv_id = kv_lbl.text() if isinstance(kv_lbl, QLabel) else ""
            if match_token is None or match_token in kv_id.split("_"):
                self._row_images[r] = list(imgs)
                btn_img = self.sku_table.cellWidget(r, 6)
                if isinstance(btn_img, QPushButton):
                    self._style_row_img_button(btn_img, imgs)
                applied += 1
        if notify:
            if applied > 0:
                QMessageBox.information(self, "应用成功", f"已将变体图片应用到 {applied} 个目标变体。")
            else:
                QMessageBox.warning(self, "提示", "没有找到匹配的其他变体。")

    def _delete_row_by_widget(self, act_wrap: QWidget) -> None:
        r = self._find_row_of_widget(7, act_wrap)
        if r >= 0:
            self.sku_table.removeRow(r)
            # Re-index _row_images cleanly
            new_map: Dict[int, List[str]] = {}
            for old_idx, imgs in self._row_images.items():
                if old_idx < r:
                    new_map[old_idx] = imgs
                elif old_idx > r:
                    new_map[old_idx - 1] = imgs
            self._row_images = new_map
            self._refresh_empty_state()

    def _open_column_batch_modal(self, col_idx: int) -> None:
        field = self._COL_BATCH_FIELDS.get(col_idx)
        if not field:
            return
        rows = self.get_variants()
        if not rows:
            QMessageBox.information(self, "提示", "SKU 列表为空，请先新增变体或 SKU 行。")
            return
        dlg = BatchVariantColumnDialog(field, rows, self)
        if dlg.exec():
            updates = dlg.get_row_updates()
            self._apply_batch_updates_to_table(updates)

    def _apply_batch_updates_to_table(self, updates: Dict[int, Dict[str, Any]]) -> None:
        for r, patch in updates.items():
            if not (0 <= r < self.sku_table.rowCount()):
                continue
            if "netProceed" in patch:
                w = self.sku_table.cellWidget(r, 1)
                if isinstance(w, QDoubleSpinBox):
                    w.setValue(float(patch["netProceed"]))
            if "weight" in patch:
                w = self.sku_table.cellWidget(r, 2)
                if isinstance(w, QSpinBox):
                    w.setValue(int(round(float(patch["weight"]))))
            if any(k in patch for k in ("length", "width", "height")):
                wrap = self.sku_table.cellWidget(r, 3)
                if wrap:
                    spins = wrap.findChildren(QDoubleSpinBox)
                    if len(spins) == 3:
                        spins[0].setValue(float(patch.get("length", spins[0].value())))
                        spins[1].setValue(float(patch.get("width", spins[1].value())))
                        spins[2].setValue(float(patch.get("height", spins[2].value())))
            if "sku" in patch:
                w = self.sku_table.cellWidget(r, 4)
                if isinstance(w, QLineEdit):
                    w.setText(str(patch["sku"]))
            if "stock" in patch:
                w = self.sku_table.cellWidget(r, 5)
                if isinstance(w, QSpinBox):
                    w.setValue(int(patch["stock"]))

    def _refresh_empty_state(self) -> None:
        cnt = self.sku_table.rowCount()
        self.lbl_sku_count.setText(f"<b>SKU 列表</b>  <span style='color:#9A9589'>{cnt} 个变体</span>")
        self.lbl_empty.setVisible(cnt == 0)

    def sync_available_images(self, valid_images: List[str]) -> None:
        valid_set = set(valid_images)
        for r in range(self.sku_table.rowCount()):
            imgs = [u for u in (self._row_images.get(r) or []) if u in valid_set]
            self._row_images[r] = imgs
            btn = self.sku_table.cellWidget(r, 6)
            if isinstance(btn, QPushButton):
                self._style_row_img_button(btn, imgs)

    def load_variants(self, variants: List[Dict[str, Any]], headers_meta: Optional[List[Dict[str, Any]]] = None) -> None:
        """Restores variant dimensions & SKU rows when opening in EDIT mode."""
        for c in list(self._dim_cards):
            c.setParent(None)
            c.deleteLater()
        self._dim_cards.clear()
        self.sku_table.setRowCount(0)
        self._row_images.clear()

        if headers_meta:
            for h in headers_meta:
                key = str(h.get("key") or h.get("vairantKey") or "").strip()
                vals = [str(v).strip() for v in (h.get("values") or h.get("vairantValues") or []) if str(v).strip()]
                if key:
                    self.add_dimension(key, vals)

        for v in variants:
            if not isinstance(v, dict):
                continue
            kv_id = str(v.get("kvId") or v.get("name") or v.get("spec") or f"SKU-{self.sku_table.rowCount() + 1}")
            self._append_sku_row(kv_id, existing=v)
        self._refresh_empty_state()

    def get_dimension_headers(self) -> List[Dict[str, Any]]:
        out = []
        for c in self._dim_cards:
            k = c.dimension_name()
            vals = c.values()
            if k:
                out.append({"key": k, "values": vals})
        return out

    def get_variants(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for r in range(self.sku_table.rowCount()):
            lbl = self.sku_table.cellWidget(r, 0)
            kv_id = lbl.text().strip() if isinstance(lbl, QLabel) else f"SKU-{r + 1}"

            spn_net = self.sku_table.cellWidget(r, 1)
            net_v = round(float(spn_net.value()), 2) if isinstance(spn_net, QDoubleSpinBox) else 0.0

            spn_w = self.sku_table.cellWidget(r, 2)
            w_v = float(spn_w.value()) if isinstance(spn_w, QSpinBox) else 0.0

            wrap_sz = self.sku_table.cellWidget(r, 3)
            spins = wrap_sz.findChildren(QDoubleSpinBox) if wrap_sz else []
            l_v = float(spins[0].value()) if len(spins) > 0 else 0.0
            wi_v = float(spins[1].value()) if len(spins) > 1 else 0.0
            h_v = float(spins[2].value()) if len(spins) > 2 else 0.0

            edt_sku = self.sku_table.cellWidget(r, 4)
            sku_v = edt_sku.text().strip() if isinstance(edt_sku, QLineEdit) else ""

            spn_stk = self.sku_table.cellWidget(r, 5)
            stk_v = int(spn_stk.value()) if isinstance(spn_stk, QSpinBox) else 0

            imgs = list(self._row_images.get(r) or [])
            rows.append({
                "kvId": kv_id,
                "name": kv_id,
                "sku": sku_v,
                "price": net_v,
                "netProceed": net_v,
                "profit": net_v,
                "weight": w_v,
                "length": l_v,
                "width": wi_v,
                "height": h_v,
                "size": [l_v, wi_v, h_v],
                "stock": stk_v,
                "images": imgs,
            })
        return rows
