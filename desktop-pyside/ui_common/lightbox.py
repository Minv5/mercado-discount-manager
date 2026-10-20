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

    Centered on parent window with smooth mouse dragging and instant close shortcuts.
    """

    def __init__(self, pixmap: QPixmap | None = None, title: str = "查看大图", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(920, 720)
        self._is_dragging = False
        self._drag_pos = QPoint()

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
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #F6F3EA; font-size: 14px; font-weight: bold; background: transparent; border: none;")
        hdr.addWidget(title_lbl, 1)

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

        self.img_lbl = QLabel("正在加载高清大图...")
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setStyleSheet("background: #121614; color: #C8C3B7; font-size: 13px; border: 1px solid #333333; border-radius: 8px;")
        card_layout.addWidget(self.img_lbl, 1)

        main_layout.addWidget(self.card)

        if pixmap and not pixmap.isNull():
            self.set_pixmap(pixmap)

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
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.close()
        else:
            super().keyPressEvent(event)

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


def show_image_preview(url_or_pixmap: str | QPixmap, title: str = "查看大图", parent: QWidget | None = None) -> None:
    """Convenient entry point to display full-size lightbox for a local path, URL, or QPixmap."""
    dlg = ImagePreviewDialog(title=title, parent=parent)
    if isinstance(url_or_pixmap, QPixmap):
        dlg.set_pixmap(url_or_pixmap)
        dlg.exec()
    elif isinstance(url_or_pixmap, str) and url_or_pixmap.strip():
        url = url_or_pixmap.strip()
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
                dlg.set_pixmap(pix)
            dlg.exec()
            return

        high_res_url = re.sub(r"-[IV]\.(jpg|jpeg|png)", r"-O.\1", url, flags=re.IGNORECASE)
        load_image_async(
            high_res_url,
            QSize(860, 600),
            on_loaded=lambda p: dlg.set_pixmap(p) if not p.isNull() else None,
            radius=0,
            crop=False,
        )
        dlg.exec()
