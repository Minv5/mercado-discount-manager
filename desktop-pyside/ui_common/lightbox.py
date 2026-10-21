from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui_common.image_cache import load_image_async


class ImagePreviewDialog(QDialog):
    """High-resolution lightbox dialog for viewing product images and claim attachments.

    Supports multi-image gallery with Next / Prev buttons, page indicators, and keyboard shortcuts (Left/Right/ESC).
    Centered on parent window with smooth mouse dragging and instant close shortcuts.
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

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)

        self.card = QFrame(self)
        self.card.setObjectName("imagePreviewCard")
        self.card.setStyleSheet("""
            QFrame#imagePreviewCard {
                background-color: rgba(20, 24, 22, 0.96);
                border: 1px solid #4E472F;
                border-radius: 12px;
            }
        """)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(18, 14, 18, 16)
        card_layout.setSpacing(12)

        hdr = QHBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #F6F3EA; font-size: 14px; font-weight: bold; background: transparent; border: none;")
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
        self.shortcut_hint_lbl = QLabel("支持键盘 ← / → 翻页 · ESC 退出")
        self.shortcut_hint_lbl.setStyleSheet(
            "color: #78716C; font-size: 11px; background: transparent; border: none; margin-left: 8px;"
        )
        self.shortcut_hint_lbl.setVisible(len(self._images) > 1)
        hdr.addWidget(self.shortcut_hint_lbl)

        hdr.addStretch(0)

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

        self.img_lbl = QLabel("正在加载高清大图...")
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setStyleSheet("background: #121614; color: #C8C3B7; font-size: 13px; border: 1px solid #333333; border-radius: 8px;")
        preview_row.addWidget(self.img_lbl, 1)

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
        if pixmap.width() > 900 or pixmap.height() > 650:
            scaled = pixmap.scaled(
                QSize(860, 600),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        else:
            scaled = pixmap
        self.img_lbl.setText("")
        self.img_lbl.setPixmap(scaled)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.close()
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
            self._load_current_image()

    def _on_next_clicked(self) -> None:
        if self._current_index < len(self._images) - 1:
            self._current_index += 1
            self._load_current_image()

    def _load_current_image(self) -> None:
        if not self._images:
            return
        total = len(self._images)
        self.counter_lbl.setText(f"{self._current_index + 1} / {total}")
        self.counter_lbl.setVisible(total > 1)
        self.prev_btn.setVisible(total > 1)
        self.next_btn.setVisible(total > 1)
        self.prev_btn.setEnabled(self._current_index > 0)
        self.next_btn.setEnabled(self._current_index < total - 1)
        self.shortcut_hint_lbl.setVisible(total > 1)

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

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            card_rect = self.card.geometry()
            if not card_rect.contains(event.pos()):
                self.close()
                return
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
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
    dlg = ImagePreviewDialog(
        images=url_or_pixmap,
        initial_index=initial_index,
        title=title,
        parent=parent,
    )
    dlg.exec()
