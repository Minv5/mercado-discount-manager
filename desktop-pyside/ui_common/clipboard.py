from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor, QPixmap, QTextDocumentFragment
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QMenu,
    QTextEdit,
    QWidget,
)

logger = logging.getLogger(__name__)


def copy_pure_text(text: str, parent: QWidget | None = None) -> None:
    """Copy pure text to system clipboard silently without disturbing popups."""
    clean = str(text or "").strip()
    if clean:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(clean)


def copy_pixmap(pixmap: QPixmap) -> None:
    """Copy QPixmap to system clipboard silently."""
    if pixmap and not pixmap.isNull():
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setPixmap(pixmap)


def extract_copyable_text(widget: QWidget | None, fallback: str = "") -> str:
    """Extract selected text if user highlighted part of it, otherwise clean plain text."""
    if widget is None:
        return str(fallback or "").strip()

    if isinstance(widget, QLabel):
        if widget.hasSelectedText():
            sel = widget.selectedText().strip()
            if sel:
                return sel
        raw = widget.text() or ""
        plain = QTextDocumentFragment.fromHtml(raw).toPlainText().strip()
        return plain or str(fallback or "").strip()

    if isinstance(widget, QTextEdit):
        sel = widget.textCursor().selectedText().strip()
        if sel:
            return sel
        return widget.toPlainText().strip() or str(fallback or "").strip()

    if isinstance(widget, QLineEdit):
        if widget.hasSelectedText():
            sel = widget.selectedText().strip()
            if sel:
                return sel
        return widget.text().strip() or str(fallback or "").strip()

    if hasattr(widget, "text") and callable(widget.text):
        try:
            return str(widget.text() or "").strip()
        except Exception:
            pass

    return str(fallback or "").strip()


def label_copy_val(lbl: QLabel, fallback: str = "") -> str:
    """Backward-compatible helper: return highlighted text from label or fallback."""
    return extract_copyable_text(lbl, fallback=fallback)


def show_pure_copy_menu(
    parent: QWidget,
    pos: QPoint,
    getter: Callable[[], Any] | Any = None,
    popup: bool = False,
    action_text: str = "复制",
) -> QMenu | None:
    """Populate and show a minimalist context menu containing a single copy action.

    Args:
        parent: The source widget requesting the context menu.
        pos: Relative point inside parent.
        getter: A callable returning text/pixmap, or static text/pixmap. If None, extracts from parent.
        popup: When True, uses non-blocking menu.popup (useful for tests and animated UIs).
               When False, uses modal menu.exec.
        action_text: Text for the copy menu item, defaults to '复制'.
    """
    val = None
    if callable(getter):
        try:
            val = getter()
        except Exception:
            val = None
    elif getter is not None:
        val = getter
    else:
        val = extract_copyable_text(parent)

    if val is None or val == "":
        return None

    menu = QMenu(parent)
    act = menu.addAction(action_text)

    def _do_copy():
        if isinstance(val, QPixmap):
            copy_pixmap(val)
        else:
            copy_pure_text(str(val))

    act.triggered.connect(_do_copy)
    global_pos = parent.mapToGlobal(pos) if isinstance(parent, QWidget) else QCursor.pos()

    if popup:
        menu.popup(global_pos)
        return menu

    chosen = menu.exec(global_pos)
    if chosen == act:
        _do_copy()
    return menu


def make_click_copy_handler(widget: QWidget, get_val_fn: Callable[[], Any]):
    """Create a mousePressEvent handler that silently copies text on left click while preserving original behavior."""
    orig_press = getattr(widget, "mousePressEvent", None)

    def _handler(event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = str(get_val_fn() or "").strip()
            if val:
                copy_pure_text(val)
        if callable(orig_press):
            orig_press(event)

    return _handler


def attach_copy_menu(
    widget: QWidget,
    get_val_fn: Callable[[], Any] | None = None,
    popup: bool = False,
    action_text: str = "复制",
) -> None:
    """Attach right-click pure text copy menu to any QWidget."""
    widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _on_context_menu(pos: QPoint):
        show_pure_copy_menu(widget, pos, getter=get_val_fn, popup=popup, action_text=action_text)

    widget.customContextMenuRequested.connect(_on_context_menu)
