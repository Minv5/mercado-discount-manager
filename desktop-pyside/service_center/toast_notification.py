from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ToastNotification(QFrame):
    """Floating notification toast displayed at the bottom right corner."""

    clicked = Signal(str, str)  # (session_id, session_type)
    closed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        title: str = "",
        message: str = "",
        store_name: str = "",
        site_name: str = "",
        session_id: str = "",
        session_type: str = "question",
        duration_ms: int = 6000,
    ):
        super().__init__(parent)
        self.session_id = session_id
        self.session_type = session_type

        self.setWindowFlags(Qt.WindowType.SubWindow | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(340)

        # Style
        if session_type == "claim":
            accent_color = "#f59e0b"
            icon_str = "⚠️ 索赔提醒"
        elif session_type == "message":
            accent_color = "#10b981"
            icon_str = "✉️ 售后私信"
        else:
            accent_color = "#3b82f6"
            icon_str = "💬 售前咨询"
        self.setStyleSheet(f"""
            ToastNotification {{
                background-color: rgba(28, 35, 31, 0.95);
                border: 1px solid #4E472F;
                border-left: 4px solid {accent_color};
                border-radius: 8px;
            }}
            QLabel {{
                color: #E6E2D8;
                background: transparent;
            }}
            QPushButton#actionBtn {{
                background-color: #2F6B3F;
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton#actionBtn:hover {{
                background-color: #356F45;
            }}
            QPushButton#closeBtn {{
                background: transparent;
                color: #777266;
                border: none;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton#closeBtn:hover {{
                color: #E6E2D8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Top row: icon + title + store pill + close
        top_layout = QHBoxLayout()
        icon_lbl = QLabel(f"<b>{icon_str}</b>")
        icon_lbl.setStyleSheet(f"color: {accent_color}; font-size: 12px;")

        pill_lbl = QLabel(f" {store_name} · {site_name} ")
        pill_lbl.setStyleSheet("background: rgba(78, 71, 47, 0.4); border-radius: 3px; color: #C8C3B7; font-size: 10px; padding: 2px 4px;")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(18, 18)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_close)

        top_layout.addWidget(icon_lbl)
        top_layout.addWidget(pill_lbl)
        top_layout.addStretch()
        top_layout.addWidget(close_btn)
        layout.addLayout(top_layout)

        # Message snippet
        msg_lbl = QLabel(message[:90] + ("..." if len(message) > 90 else ""))
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("color: #E6E2D8; font-size: 12px; line-height: 1.3;")
        layout.addWidget(msg_lbl)

        # Bottom row: action button
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        view_btn = QPushButton("立即查看 →")
        view_btn.setObjectName("actionBtn")
        view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_btn.clicked.connect(self._on_click)
        bottom_layout.addWidget(view_btn)
        layout.addLayout(bottom_layout)

        # Auto-dismiss timer
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_close)
        if duration_ms > 0:
            self.timer.start(duration_ms)

    def _on_click(self) -> None:
        self.timer.stop()
        self.clicked.emit(self.session_id, self.session_type)
        self.deleteLater()

    def _on_close(self) -> None:
        self.timer.stop()
        self.closed.emit()
        self.deleteLater()

    def show_at_corner(self, parent_widget: QWidget) -> None:
        if not parent_widget:
            return
        self.show()
        margin = 24
        x = max(10, parent_widget.width() - self.width() - margin)
        y = max(10, parent_widget.height() - self.height() - margin)
        self.move(x, y)
        self.raise_()
