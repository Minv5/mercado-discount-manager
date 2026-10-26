#!/usr/bin/env python3
"""美客多活动管家 · 官方授权激活码生成器 (商家专属离线算号器)

支持命令行与图形化双重操作，使用 RSA 私钥对客户机器码进行不可伪造的非对称加密签名。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def get_private_key_path() -> Path:
    # 优先查找 tools/license_private_key.pem
    root = Path(__file__).resolve().parent
    priv_file = root / "license_private_key.pem"
    if not priv_file.exists():
        # Fallback to current working directory
        priv_file = Path("tools/license_private_key.pem")
    return priv_file


def generate_license_code(
    machine_id: str,
    days: int | None = 30,
    is_permanent: bool = False,
    customer_name: str = "",
    custom_date_str: str | None = None,
) -> str:
    """Generate an RSA-signed license code."""
    priv_path = get_private_key_path()
    if not priv_path.exists():
        raise FileNotFoundError(f"未找到授权私钥文件：{priv_path}，无法生成激活码！")

    clean_mid = machine_id.strip().upper().replace(" ", "")
    if not clean_mid.startswith("MDM-") or len(clean_mid) < 15:
        raise ValueError(f"机器码格式不正确：{machine_id}，标准格式形如 MDM-XXXX-XXXX-XXXX-XXXX")

    now = datetime.now()
    if is_permanent:
        exp_str = "PERMANENT"
    elif custom_date_str:
        exp_str = custom_date_str.strip()
    else:
        exp_date = (now + timedelta(days=days or 30)).date()
        exp_str = exp_date.strftime("%Y-%m-%d")

    payload: dict[str, Any] = {
        "mid": clean_mid,
        "exp": exp_str,
        "type": "commercial",
        "name": customer_name.strip() or "商业客户",
        "created": now.strftime("%Y-%m-%d"),
    }

    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    priv_key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    sig_bytes = priv_key.sign(payload_bytes, padding.PKCS1v15(), hashes.SHA256())

    code = base64.urlsafe_b64encode(payload_bytes).decode("utf-8") + "." + base64.urlsafe_b64encode(sig_bytes).decode("utf-8")
    return code


def run_gui() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDateEdit,
        QFormLayout,
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

    app = QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle("美客多活动管家 · 官方授权发码器 (商家专用)")
    window.setFixedSize(560, 520)

    window.setStyleSheet("""
        QWidget {
            background-color: #121412;
            color: #F6F3EA;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        QLabel {
            color: #E2E8F0;
        }
        QLineEdit, QComboBox, QPlainTextEdit, QDateEdit {
            background-color: #1A1D1A;
            color: #F6F3EA;
            border: 1px solid #3B382C;
            border-radius: 4px;
            padding: 6px 10px;
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
            font-size: 14px;
            padding: 8px 18px;
        }
        QPushButton#primary:hover {
            background-color: #387D4A;
            border-color: #D4AF37;
        }
        QPushButton#copy {
            background-color: #262B26;
            color: #68D391;
            border: 1px solid #3B382C;
            border-radius: 4px;
            font-weight: bold;
            padding: 6px 14px;
        }
        QPushButton#copy:hover {
            background-color: #323A32;
        }
    """)

    layout = QVBoxLayout(window)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(14)

    # Header
    header = QHBoxLayout()
    icon = QLabel("🔑")
    icon.setStyleSheet("font-size: 28px;")
    title_box = QVBoxLayout()
    title_box.setSpacing(2)
    t1 = QLabel("美客多活动管家 · 官方激活码生成器")
    t1.setStyleSheet("font-size: 16px; font-weight: bold; color: #D4AF37;")
    t2 = QLabel("仅供软件开发者与授权管理者使用 · 非对称加密防破解")
    t2.setStyleSheet("font-size: 12px; color: #94A3B8;")
    title_box.addWidget(t1)
    title_box.addWidget(t2)
    header.addWidget(icon)
    header.addLayout(title_box)
    header.addStretch(1)
    layout.addLayout(header)

    # Form
    form = QFormLayout()
    form.setSpacing(10)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

    mid_input = QLineEdit()
    mid_input.setPlaceholderText("例如: MDM-6F57-29F3-CEB8-2BC5")
    form.addRow("客户机器码:", mid_input)

    name_input = QLineEdit()
    name_input.setPlaceholderText("选填，例如：张总湖北店")
    form.addRow("客户备注名:", name_input)

    type_combo = QComboBox()
    type_combo.addItem("月卡 · 30 天", 30)
    type_combo.addItem("季卡 · 90 天", 90)
    type_combo.addItem("半年卡 · 180 天", 180)
    type_combo.addItem("年卡 · 365 天", 365)
    type_combo.addItem("终身永久版 (Permanent)", "PERMANENT")
    form.addRow("授权期限:", type_combo)

    layout.addLayout(form)

    # Generate Button
    gen_btn = QPushButton("⚡ 一键生成专属激活码")
    gen_btn.setObjectName("primary")
    layout.addWidget(gen_btn)

    # Output Result
    out_lbl = QLabel("生成的激活码 (可直接复制发给客户):")
    out_lbl.setStyleSheet("font-weight: bold; font-size: 12px; color: #D4AF37; margin-top: 6px;")
    layout.addWidget(out_lbl)

    out_text = QPlainTextEdit()
    out_text.setReadOnly(True)
    out_text.setFixedHeight(110)
    out_text.setPlaceholderText("生成的激活码将展示在此处...")
    layout.addWidget(out_text)

    # Copy Button
    copy_btn = QPushButton("📋 一键复制激活码")
    copy_btn.setObjectName("copy")
    copy_btn.setFixedHeight(36)

    def do_copy():
        txt = out_text.toPlainText().strip()
        if txt:
            QApplication.clipboard().setText(txt)
            copy_btn.setText("✓ 激活码已复制到剪贴板！可以直接发送给客户")
            copy_btn.setStyleSheet("color: #68D391; font-weight: bold; background: #1C3322;")

    copy_btn.clicked.connect(do_copy)
    layout.addWidget(copy_btn)

    def do_generate():
        mid = mid_input.text().strip()
        if not mid:
            QMessageBox.warning(window, "提示", "请先输入客户的电脑机器码！")
            return
        cname = name_input.text().strip()
        val = type_combo.currentData()
        try:
            if val == "PERMANENT":
                code = generate_license_code(mid, is_permanent=True, customer_name=cname)
            else:
                code = generate_license_code(mid, days=int(val), customer_name=cname)
            out_text.setPlainText(code)
            copy_btn.setText("📋 一键复制激活码")
            copy_btn.setStyleSheet("")
        except Exception as e:
            QMessageBox.critical(window, "生成失败", str(e))

    gen_btn.clicked.connect(do_generate)

    window.show()
    sys.exit(app.exec())


def main() -> None:
    parser = argparse.ArgumentParser(description="美客多活动管家 · 官方授权发码器")
    parser.add_argument("--mid", help="客户电脑机器码 (MDM-XXXX-XXXX-XXXX-XXXX)")
    parser.add_argument("--days", type=int, default=365, help="授权天数，默认 365 天")
    parser.add_argument("--permanent", action="store_true", help="生成终身永久授权")
    parser.add_argument("--name", default="", help="客户备注名称")
    parser.add_argument("--cli", action="store_true", help="强制仅命令行模式")

    args = parser.parse_args()

    if args.mid:
        try:
            code = generate_license_code(
                machine_id=args.mid,
                days=args.days,
                is_permanent=args.permanent,
                customer_name=args.name,
            )
            print("=========================================")
            print("【生成的激活码如下，请复制发给客户】：")
            print("=========================================")
            print(code)
            print("=========================================")
            return
        except Exception as e:
            print(f"生成失败：{e}", file=sys.stderr)
            sys.exit(1)

    if not args.cli:
        run_gui()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
