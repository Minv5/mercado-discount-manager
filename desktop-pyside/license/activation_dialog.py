from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .manager import LicenseInfo, LicenseManager, LicenseStatus

logger = logging.getLogger(__name__)


class ActivationDialog(QDialog):
    """Modern dark-gold styled software license activation dialog."""

    activated = Signal()

    def __init__(self, manager: LicenseManager | None = None, parent: QWidget | None = None, force_modal: bool = False):
        super().__init__(parent)
        self.manager = manager or LicenseManager.get_instance()
        self.force_modal = force_modal

        self.setWindowTitle("美客多活动管家 · 软件授权激活")
        self.setFixedSize(540, 480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        if force_modal:
            self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self.setStyleSheet("""
            QDialog {
                background-color: #121412;
                color: #F6F3EA;
            }
            QLabel {
                color: #E2E8F0;
            }
            QFrame#card {
                background-color: #1A1D1A;
                border: 1px solid #3B382C;
                border-radius: 8px;
            }
            QLineEdit, QPlainTextEdit {
                background-color: #0E100E;
                color: #F6F3EA;
                border: 1px solid #3B382C;
                border-radius: 4px;
                padding: 6px 10px;
                font-family: monospace;
                font-size: 13px;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                border-color: #D4AF37;
            }
            QPushButton#primary {
                background-color: #2F6B3F;
                color: #FFFFFF;
                border: 1px solid #4E472F;
                border-radius: 5px;
                font-weight: bold;
                font-size: 13px;
                padding: 6px 16px;
            }
            QPushButton#primary:hover {
                background-color: #387D4A;
                border-color: #D4AF37;
            }
            QPushButton#secondary {
                background-color: #222622;
                color: #CBD5E1;
                border: 1px solid #3B382C;
                border-radius: 5px;
                font-size: 12px;
                padding: 6px 14px;
            }
            QPushButton#secondary:hover {
                background-color: #2D332D;
                color: #F8FAFC;
            }
        """)

        self._build_ui()
        self._refresh_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        icon_label = QLabel("🛡️")
        icon_label.setStyleSheet("font-size: 32px;")
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_lbl = QLabel("美客多活动管家 · 商业授权激活")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #D4AF37;")
        sub_lbl = QLabel("一机一码安全授权体系 · 正版商业授权认证")
        sub_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        header.addWidget(icon_label)
        header.addLayout(title_box)
        header.addStretch(1)
        layout.addLayout(header)

        # Status Banner
        self.status_banner = QLabel()
        self.status_banner.setWordWrap(True)
        self.status_banner.setStyleSheet("""
            background-color: #22261E;
            color: #E2E8F0;
            border: 1px solid #4E472F;
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 12px;
        """)
        layout.addWidget(self.status_banner)

        # Machine ID Card
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        mid_lbl = QLabel("本机电脑机器码 (Machine ID):")
        mid_lbl.setStyleSheet("font-weight: bold; font-size: 12px; color: #D4AF37;")
        card_layout.addWidget(mid_lbl)

        mid_row = QHBoxLayout()
        self.mid_edit = QLineEdit(self.manager.machine_id)
        self.mid_edit.setReadOnly(True)
        self.mid_edit.setStyleSheet("font-size: 14px; font-weight: bold; color: #68D391; background: #0E100E;")
        self.copy_btn = QPushButton("📋 复制机器码")
        self.copy_btn.setObjectName("secondary")
        self.copy_btn.setFixedHeight(32)
        self.copy_btn.clicked.connect(self._copy_machine_id)
        mid_row.addWidget(self.mid_edit, 1)
        mid_row.addWidget(self.copy_btn)
        card_layout.addLayout(mid_row)

        hint = QLabel("💡 购买或续费时，请将上方机器码发送给客服，即可获取专属激活码。")
        hint.setStyleSheet("font-size: 11px; color: #8C827A;")
        card_layout.addWidget(hint)
        layout.addWidget(card)

        # Activation Code Input Area
        code_lbl = QLabel("输入激活码 (Activation Key):")
        code_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(code_lbl)

        self.code_edit = QPlainTextEdit()
        self.code_edit.setPlaceholderText("请在此粘贴客服发送给您的长串激活码...")
        self.code_edit.setFixedHeight(85)
        layout.addWidget(self.code_edit)

        # Actions
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.cancel_btn = QPushButton("关闭")
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.clicked.connect(self._on_cancel)

        self.activate_btn = QPushButton("立即激活")
        self.activate_btn.setObjectName("primary")
        self.activate_btn.setFixedHeight(36)
        self.activate_btn.clicked.connect(self._do_activate)

        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.activate_btn)
        layout.addLayout(btn_row)

    def _copy_machine_id(self) -> None:
        QApplication.clipboard().setText(self.manager.machine_id)
        self.copy_btn.setText("✓ 已复制")
        self.copy_btn.setStyleSheet("color: #68D391; font-weight: bold;")

    def _refresh_state(self) -> None:
        info: LicenseInfo = self.manager.check_license()

        if info.status == LicenseStatus.LICENSED:
            self.status_banner.setText(f"✅ <b>授权正常</b>：{info.license_type}（{info.expiration_str}）。如需更换或续费可直接输入新激活码。")
            self.status_banner.setStyleSheet("background-color: #172D1E; border: 1px solid #2F6B3F; color: #86EFAC; border-radius: 6px; padding: 8px 12px;")
            self.cancel_btn.setText("完成返回")
        elif info.status == LicenseStatus.TRIAL:
            self.status_banner.setText(f"⏳ <b>试用中</b>：当前为 3 天免费试用期，{info.expiration_str}。试用到期前可无限制体验全功能。")
            self.status_banner.setStyleSheet("background-color: #2A2415; border: 1px solid #8C6D23; color: #FDE047; border-radius: 6px; padding: 8px 12px;")
            self.cancel_btn.setText(f"继续试用 (剩 {info.remaining_days} 天)")
        else:
            self.status_banner.setText(f"⚠️ <b>授权已到期</b>：{info.message}")
            self.status_banner.setStyleSheet("background-color: #2D1A1A; border: 1px solid #7F2D2D; color: #FCA5A5; border-radius: 6px; padding: 8px 12px;")
            self.cancel_btn.setText("退出软件" if self.force_modal else "关闭")

    def _do_activate(self) -> None:
        raw = self.code_edit.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "提示", "请先粘贴激活码后再点击激活。")
            return

        ok, msg = self.manager.activate(raw)
        if ok:
            QMessageBox.information(self, "授权成功", msg)
            self._refresh_state()
            self.activated.emit()
            self.accept()
        else:
            QMessageBox.critical(self, "激活失败", msg)

    def _on_cancel(self) -> None:
        info = self.manager.check_license()
        if self.force_modal and info.is_expired:
            # Force modal cannot bypass when expired
            self.reject()
            return
        self.accept()
