from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui_common.clipboard import copy_pixmap, make_selectable_label
from ui_common.image_cache import load_image_async


class ImagePreviewDialog(QDialog):
    """High-resolution lightbox dialog for viewing product images and claim attachments.

    Features:
      - Smooth mouse wheel zoom with anchor at cursor position (20% to 500%).
      - Drag-to-pan when zoomed in, double-click to toggle fit / zoom.
      - Clean zoom controls toolbar (+, -, fit, 1:1, zoom percentage badge).
      - Multi-image gallery with Next / Prev buttons, indicators, and arrow shortcuts.
      - Right-click pure "复制" menu and Cmd/Ctrl+C shortcut.
      - Frameless dark translucent card centered on active window.
    """

    def __init__(
        self,
        images: list[str | QPixmap] | str | QPixmap | None = None,
        initial_index: int = 0,
        title: str = "查看大图",
        parent: QWidget | None = None,
        pixmap: QPixmap | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(920, 720)
        self._is_dragging = False
        self._drag_pos = QPoint()

        # Parse images list
        self._images: list[str | QPixmap] = []
        if images is not None:
            if isinstance(images, (list, tuple)):
                self._images = [img for img in images if img]
            else:
                self._images = [images]
        elif pixmap is not None:
            self._images = [pixmap]

        self._current_index = max(0, min(initial_index, len(self._images) - 1)) if self._images else 0
        self._base_title = title
        self._current_pixmap: QPixmap | None = None
        self._base_size: QSize | None = None
        self._zoom_factor: float = 1.0
        self._is_panning: bool = False
        self._pan_start: QPoint = QPoint()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.card = QFrame(self)
        self.card.setObjectName("imagePreviewCard")
        self.card.setMaximumSize(1020, 780)
        self.card.setMinimumSize(600, 480)
        self.card.setStyleSheet("""
            QFrame#imagePreviewCard {
                background-color: rgba(20, 24, 22, 0.98);
                border: 1px solid #4E472F;
                border-radius: 12px;
            }
        """)
        self.card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.card.customContextMenuRequested.connect(self._show_card_context_menu)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 14, 18, 16)
        card_layout.setSpacing(12)

        hdr = QHBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #F6F3EA; font-size: 14px; font-weight: bold; background: transparent; border: none;")
        make_selectable_label(self.title_lbl)
        hdr.addWidget(self.title_lbl, 1)

        # Counter / page indicator chip
        self.counter_lbl = QLabel("")
        self.counter_lbl.setStyleSheet(
            "color: #FBBF24; font-size: 12px; font-weight: bold; background: rgba(35, 44, 36, 0.8); "
            "border: 1px solid #4E472F; border-radius: 10px; padding: 2px 10px;"
        )
        self.counter_lbl.setVisible(len(self._images) > 1)
        hdr.addWidget(self.counter_lbl)

        # Shortcut hint
        self.shortcut_hint_lbl = QLabel("支持鼠标滚轮缩放 · 拖拽平移 · ESC退出")
        self.shortcut_hint_lbl.setStyleSheet(
            "color: #78716C; font-size: 11px; background: transparent; border: none; margin-left: 8px;"
        )
        self.shortcut_hint_lbl.setVisible(True)
        hdr.addWidget(self.shortcut_hint_lbl)

        hdr.addStretch(1)

        # Zoom controls toolbar
        self.zoom_out_btn = QPushButton("－")
        self.zoom_out_btn.setFixedSize(26, 24)
        self.zoom_out_btn.setToolTip("缩小 (滚轮向下)")
        self.zoom_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_out_btn.setStyleSheet("""
            QPushButton {
                background: rgba(35, 44, 36, 0.85);
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                background: #2D3A30;
                border-color: #FBBF24;
                color: #FBBF24;
            }
        """)
        self.zoom_out_btn.clicked.connect(lambda: self.zoom_out())
        hdr.addWidget(self.zoom_out_btn)

        self.zoom_lbl = QLabel("100%")
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_lbl.setToolTip("当前缩放比例 (滚轮缩放 · 双击还原)")
        self.zoom_lbl.setStyleSheet(
            "color: #FBBF24; font-size: 11px; font-weight: bold; background: rgba(35, 44, 36, 0.85); "
            "border: 1px solid #4E472F; border-radius: 4px; padding: 2px 8px; min-width: 44px;"
        )
        hdr.addWidget(self.zoom_lbl)

        self.zoom_in_btn = QPushButton("＋")
        self.zoom_in_btn.setFixedSize(26, 24)
        self.zoom_in_btn.setToolTip("放大 (滚轮向上)")
        self.zoom_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_in_btn.setStyleSheet(self.zoom_out_btn.styleSheet())
        self.zoom_in_btn.clicked.connect(lambda: self.zoom_in())
        hdr.addWidget(self.zoom_in_btn)

        self.zoom_reset_btn = QPushButton("适应")
        self.zoom_reset_btn.setFixedSize(38, 24)
        self.zoom_reset_btn.setToolTip("还原为最佳适应尺寸 (双击图片也可还原)")
        self.zoom_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_reset_btn.setStyleSheet("""
            QPushButton {
                background: rgba(35, 44, 36, 0.85);
                color: #D4AF37;
                border: 1px solid #4E472F;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 500;
                padding: 0;
            }
            QPushButton:hover {
                background: #2D3A30;
                border-color: #FBBF24;
                color: #FFFFFF;
            }
        """)
        self.zoom_reset_btn.clicked.connect(self.reset_zoom)
        hdr.addWidget(self.zoom_reset_btn)

        self.zoom_100_btn = QPushButton("1:1")
        self.zoom_100_btn.setFixedSize(34, 24)
        self.zoom_100_btn.setToolTip("原始像素 100% 查看")
        self.zoom_100_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_100_btn.setStyleSheet(self.zoom_reset_btn.styleSheet())
        self.zoom_100_btn.clicked.connect(self.zoom_original)
        hdr.addWidget(self.zoom_100_btn)

        hdr.addSpacing(6)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("关闭 (ESC)")
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.10);
                color: #C8C3B7;
                border: none;
                border-radius: 13px;
                font-size: 13px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.22);
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.30);
            }
        """)
        close_btn.clicked.connect(self.close)
        hdr.addWidget(close_btn)
        card_layout.addLayout(hdr)

        # Central preview area with prev/next buttons flanking the image
        preview_row = QHBoxLayout()
        preview_row.setSpacing(10)

        # Left / Prev Button
        self.prev_btn = QPushButton("‹")
        self.prev_btn.setFixedSize(40, 72)
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.setToolTip("上一张 (← 键盘左键)")
        self.prev_btn.setStyleSheet("""
            QPushButton {
                background: rgba(35, 44, 36, 0.85);
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 8px;
                font-size: 26px;
                font-weight: bold;
                padding-bottom: 4px;
            }
            QPushButton:hover {
                background: #2D3A30;
                border-color: #FBBF24;
                color: #FBBF24;
            }
            QPushButton:disabled {
                background: rgba(25, 30, 27, 0.35);
                color: #4A4A4A;
                border-color: #2D332F;
            }
        """)
        self.prev_btn.clicked.connect(self._on_prev_clicked)
        self.prev_btn.setVisible(len(self._images) > 1)
        preview_row.addWidget(self.prev_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # ScrollArea hosting the zoomable image label
        self.scroll_area = QScrollArea(self.card)
        self.scroll_area.setObjectName("lightboxScrollArea")
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setStyleSheet("""
            QScrollArea#lightboxScrollArea {
                background: #121614;
                border: 1px solid #333333;
                border-radius: 8px;
            }
            QScrollArea#lightboxScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.2);
                min-height: 24px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.35);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 8px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(255, 255, 255, 0.2);
                min-width: 24px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(255, 255, 255, 0.35);
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
        """)

        self.img_lbl = QLabel("正在加载高清大图...")
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setStyleSheet("background: transparent; color: #C8C3B7; font-size: 13px; border: none;")
        self.img_lbl.setCursor(Qt.CursorShape.ArrowCursor)
        self.img_lbl.setToolTip("右键或快捷键 Ctrl/Cmd+C 复制 · 滚轮可缩放")
        self.img_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.img_lbl.customContextMenuRequested.connect(self._show_image_context_menu)

        self.scroll_area.setWidget(self.img_lbl)
        preview_row.addWidget(self.scroll_area, 1)

        # Right / Next Button
        self.next_btn = QPushButton("›")
        self.next_btn.setFixedSize(40, 72)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setToolTip("下一张 (→ 键盘右键)")
        self.next_btn.setStyleSheet(self.prev_btn.styleSheet())
        self.next_btn.clicked.connect(self._on_next_clicked)
        self.next_btn.setVisible(len(self._images) > 1)
        preview_row.addWidget(self.next_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        card_layout.addLayout(preview_row, 1)
        main_layout.addWidget(self.card)

        # Event filter for wheel zooming, panning, and double clicking
        self.img_lbl.installEventFilter(self)
        self.scroll_area.viewport().installEventFilter(self)
        self.scroll_area.installEventFilter(self)

        if self._images:
            self._load_current_image()

        self.center_on_parent()

    def center_on_parent(self) -> None:
        """Position this dialog exactly at the center of the main application window."""
        target_win = None
        p = self.parent()
        if p:
            target_win = p.window() if hasattr(p, "window") else p
        if not target_win:
            target_win = QApplication.activeWindow()

        if target_win and target_win.isVisible():
            p_geo = target_win.frameGeometry()
            x = p_geo.x() + (p_geo.width() - self.width()) // 2
            y = p_geo.y() + (p_geo.height() - self.height()) // 2
            self.move(max(20, x), max(20, y))
        else:
            screen = QApplication.primaryScreen()
            if screen:
                s_geo = screen.availableGeometry()
                x = s_geo.left() + (s_geo.width() - self.width()) // 2
                y = s_geo.top() + (s_geo.height() - self.height()) // 2
                self.move(max(20, x), max(20, y))

    def showEvent(self, event) -> None:
        self.center_on_parent()
        super().showEvent(event)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self._current_pixmap = pixmap
        vp = self.scroll_area.viewport()
        vp_w = vp.width() if vp.width() > 100 else 840
        vp_h = vp.height() if vp.height() > 100 else 580
        max_w = max(400, vp_w - 20)
        max_h = max(300, vp_h - 20)

        if pixmap.width() > max_w or pixmap.height() > max_h:
            base_size = pixmap.size().scaled(QSize(max_w, max_h), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            base_size = pixmap.size()

        self._base_size = base_size
        self._zoom_factor = 1.0
        self._apply_zoom_at(1.0)

    def _apply_zoom_at(self, new_factor: float, cursor_pos: QPoint | None = None) -> None:
        if not self._current_pixmap or self._current_pixmap.isNull():
            return
        if not self._base_size or self._base_size.width() <= 0:
            return

        hbar = self.scroll_area.horizontalScrollBar()
        vbar = self.scroll_area.verticalScrollBar()
        vp = self.scroll_area.viewport()

        old_w = self.img_lbl.width()
        old_h = self.img_lbl.height()
        view_pos = cursor_pos if cursor_pos is not None else QPoint(vp.width() // 2, vp.height() // 2)

        rel_x = 0.5
        rel_y = 0.5
        if old_w > 0 and old_h > 0:
            lbl_pos = self.img_lbl.mapFrom(vp, view_pos)
            rel_x = max(0.0, min(1.0, lbl_pos.x() / old_w))
            rel_y = max(0.0, min(1.0, lbl_pos.y() / old_h))

        self._zoom_factor = max(0.2, min(5.0, new_factor))

        target_w = max(10, int(self._base_size.width() * self._zoom_factor))
        target_h = max(10, int(self._base_size.height() * self._zoom_factor))
        scaled = self._current_pixmap.scaled(
            QSize(target_w, target_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.img_lbl.setText("")
        self.img_lbl.setPixmap(scaled)
        self.img_lbl.setFixedSize(scaled.size())
        self.zoom_lbl.setText(f"{int(round(self._zoom_factor * 100))}%")

        new_w = scaled.width()
        new_h = scaled.height()
        if new_w > vp.width():
            target_h_val = int(rel_x * new_w - view_pos.x())
            hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), target_h_val)))
        if new_h > vp.height():
            target_v_val = int(rel_y * new_h - view_pos.y())
            vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), target_v_val)))

        is_zoomed = self._is_content_zoomed()
        cur = Qt.CursorShape.OpenHandCursor if is_zoomed else Qt.CursorShape.ArrowCursor
        self.img_lbl.setCursor(cur)
        self.scroll_area.viewport().setCursor(cur)

    def _is_content_zoomed(self) -> bool:
        if self._zoom_factor > 1.05:
            return True
        vp = self.scroll_area.viewport()
        return self.img_lbl.width() > vp.width() or self.img_lbl.height() > vp.height()

    def zoom_in(self, step: float = 1.15, cursor_pos: QPoint | None = None) -> None:
        self._apply_zoom_at(self._zoom_factor * step, cursor_pos=cursor_pos)

    def zoom_out(self, step: float = 1.15, cursor_pos: QPoint | None = None) -> None:
        self._apply_zoom_at(self._zoom_factor / step, cursor_pos=cursor_pos)

    def reset_zoom(self) -> None:
        self._apply_zoom_at(1.0)

    def zoom_original(self) -> None:
        if self._current_pixmap and self._base_size and self._base_size.width() > 0:
            target_factor = self._current_pixmap.width() / self._base_size.width()
            self._apply_zoom_at(target_factor)

    def copy_current_image(self) -> bool:
        """Copy current high-resolution image to system clipboard."""
        pix = self._current_pixmap
        if not pix or pix.isNull():
            pix = self.img_lbl.pixmap()
        if pix and not pix.isNull():
            copy_pixmap(pix)
            return True
        return False

    def _show_image_context_menu(self, pos: QPoint, popup: bool = False) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1F2421;
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 4px 0;
            }
            QMenu::item {
                padding: 6px 20px;
            }
            QMenu::item:selected {
                background-color: #2D3A30;
                color: #FBBF24;
            }
        """)
        act = menu.addAction("复制")
        has_img = bool(
            (self._current_pixmap and not self._current_pixmap.isNull())
            or (self.img_lbl.pixmap() and not self.img_lbl.pixmap().isNull())
        )
        act.setEnabled(has_img)
        act.triggered.connect(self.copy_current_image)
        if popup:
            menu.popup(self.img_lbl.mapToGlobal(pos))
        else:
            menu.exec(self.img_lbl.mapToGlobal(pos))
        return menu

    def _show_card_context_menu(self, pos: QPoint, popup: bool = False) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1F2421;
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 4px 0;
            }
            QMenu::item {
                padding: 6px 20px;
            }
            QMenu::item:selected {
                background-color: #2D3A30;
                color: #FBBF24;
            }
        """)
        act = menu.addAction("复制")
        has_img = bool(
            (self._current_pixmap and not self._current_pixmap.isNull())
            or (self.img_lbl.pixmap() and not self.img_lbl.pixmap().isNull())
        )
        act.setEnabled(has_img)
        act.triggered.connect(self.copy_current_image)
        if popup:
            menu.popup(self.card.mapToGlobal(pos))
        else:
            menu.exec(self.card.mapToGlobal(pos))
        return menu

    def eventFilter(self, watched, event: QEvent) -> bool:
        if watched in (self.img_lbl, self.scroll_area.viewport(), self.scroll_area):
            ev_type = event.type()
            if ev_type == QEvent.Type.Wheel:
                delta = event.angleDelta().y()
                if delta == 0:
                    delta = event.angleDelta().x()
                if delta != 0:
                    vp_pos = self.scroll_area.viewport().mapFromGlobal(event.globalPosition().toPoint())
                    if delta > 0:
                        self.zoom_in(cursor_pos=vp_pos)
                    else:
                        self.zoom_out(cursor_pos=vp_pos)
                    return True
            elif ev_type == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    if self._is_content_zoomed():
                        self._is_panning = True
                        self._pan_start = event.globalPosition().toPoint()
                        self.img_lbl.setCursor(Qt.CursorShape.ClosedHandCursor)
                        self.scroll_area.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                        return True
                    else:
                        vp = self.scroll_area.viewport()
                        lbl_rect = self.img_lbl.geometry()
                        pos = vp.mapFromGlobal(event.globalPosition().toPoint())
                        if not lbl_rect.contains(pos):
                            self.close()
                            return True
            elif ev_type == QEvent.Type.MouseMove:
                if getattr(self, "_is_panning", False) and (event.buttons() & Qt.MouseButton.LeftButton):
                    cur_pos = event.globalPosition().toPoint()
                    delta = cur_pos - self._pan_start
                    self._pan_start = cur_pos
                    hbar = self.scroll_area.horizontalScrollBar()
                    vbar = self.scroll_area.verticalScrollBar()
                    hbar.setValue(hbar.value() - delta.x())
                    vbar.setValue(vbar.value() - delta.y())
                    return True
            elif ev_type == QEvent.Type.MouseButtonRelease:
                if getattr(self, "_is_panning", False):
                    self._is_panning = False
                    cur = Qt.CursorShape.OpenHandCursor if self._is_content_zoomed() else Qt.CursorShape.ArrowCursor
                    self.img_lbl.setCursor(cur)
                    self.scroll_area.viewport().setCursor(cur)
                    return True
            elif ev_type == QEvent.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    if self._zoom_factor > 1.05 or self._is_content_zoomed():
                        self.reset_zoom()
                    else:
                        vp_pos = self.scroll_area.viewport().mapFromGlobal(event.globalPosition().toPoint())
                        self.zoom_in(step=2.0, cursor_pos=vp_pos)
                    return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.angleDelta().x()
        if delta != 0:
            vp_pos = self.scroll_area.viewport().mapFromGlobal(event.globalPosition().toPoint())
            if delta > 0:
                self.zoom_in(cursor_pos=vp_pos)
            else:
                self.zoom_out(cursor_pos=vp_pos)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        modifiers = event.modifiers()
        # Handle Ctrl+C (Windows/Linux) or Cmd+C (macOS)
        if key == Qt.Key.Key_C and (
            modifiers & Qt.KeyboardModifier.ControlModifier
            or modifiers & Qt.KeyboardModifier.MetaModifier
        ):
            if self.copy_current_image():
                event.accept()
                return
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.close()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_out()
        elif key == Qt.Key.Key_0:
            self.reset_zoom()
        elif key == Qt.Key.Key_1:
            self.zoom_original()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_A, Qt.Key.Key_PageUp):
            if len(self._images) > 1 and self._current_index > 0:
                self._on_prev_clicked()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_D, Qt.Key.Key_PageDown):
            if len(self._images) > 1 and self._current_index < len(self._images) - 1:
                self._on_next_clicked()
        else:
            super().keyPressEvent(event)

    def _on_prev_clicked(self) -> None:
        if self._current_index > 0:
            self._current_index -= 1
            self._zoom_factor = 1.0
            self._load_current_image()

    def _on_next_clicked(self) -> None:
        if self._current_index < len(self._images) - 1:
            self._current_index += 1
            self._zoom_factor = 1.0
            self._load_current_image()

    def _load_current_image(self) -> None:
        if not self._images:
            return
        self._current_pixmap = None
        self._base_size = None
        self._zoom_factor = 1.0
        total = len(self._images)
        self.counter_lbl.setText(f"{self._current_index + 1} / {total}")
        self.counter_lbl.setVisible(total > 1)
        self.prev_btn.setVisible(total > 1)
        self.next_btn.setVisible(total > 1)
        self.prev_btn.setEnabled(self._current_index > 0)
        self.next_btn.setEnabled(self._current_index < total - 1)

        item = self._images[self._current_index]
        if isinstance(item, QPixmap):
            self.set_pixmap(item)
            return

        url = str(item).strip()
        local_p = Path(url)
        is_local = False
        try:
            if local_p.is_file():
                is_local = True
        except Exception:
            is_local = False

        if is_local:
            pix = QPixmap(url)
            if not pix.isNull():
                self.set_pixmap(pix)
            else:
                self.img_lbl.setText("图片无法加载或已失效")
            return

        self.img_lbl.setText("正在加载高清大图...")
        self.img_lbl.setPixmap(QPixmap())
        high_res_url = re.sub(r"-[IV]\.(jpg|jpeg|png)", r"-O.\1", url, flags=re.IGNORECASE)
        req_idx = self._current_index

        def _on_loaded(pix: QPixmap, target_idx=req_idx):
            if self._current_index == target_idx:
                if not pix.isNull():
                    self.set_pixmap(pix)
                else:
                    self.img_lbl.setText("图片无法加载或网络超时")

        load_image_async(
            high_res_url,
            QSize(860, 600),
            on_loaded=_on_loaded,
            radius=0,
            crop=False,
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        if parent:
            top_win = parent.window()
            if top_win and top_win.isVisible():
                self.setGeometry(top_win.geometry())

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPainter, QColor
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 185))
        super().paintEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            card_rect = self.card.geometry()
            evt_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if not card_rect.contains(evt_pos):
                self.close()
                return
            if hasattr(self, "title_lbl") and self.title_lbl.parentWidget():
                hdr_geo = self.title_lbl.parentWidget().geometry()
                local_pos = self.card.mapFromParent(evt_pos)
                if hdr_geo.contains(local_pos):
                    self._drag_pos = event.globalPosition().toPoint() - self.card.pos()
                    self._is_dragging = True
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if getattr(self, "_is_dragging", False) and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._is_dragging = False
        super().mouseReleaseEvent(event)


def show_image_preview(
    url_or_pixmap: str | QPixmap | list[str | QPixmap],
    title: str = "查看大图",
    parent: QWidget | None = None,
    initial_index: int = 0,
) -> None:
    """Convenient entry point to display full-size lightbox for a local path, URL, QPixmap, or image list."""
    top_parent = parent.window() if parent else None
    dlg = ImagePreviewDialog(
        images=url_or_pixmap,
        initial_index=initial_index,
        title=title,
        parent=top_parent or parent,
    )
    dlg.exec()
