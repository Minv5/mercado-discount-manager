from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from items.vault_batch_settings import BaseVaultDialog


class AIProductOptimizeDialog(BaseVaultDialog):
    """AI Title Translation & Differentiation Optimization Dialog (4-Step Pipeline)."""

    def __init__(self, items: List[Dict[str, Any]], parent: Optional[QWidget] = None):
        super().__init__(f"AI辅助优化 - 已选 {len(items)} 个产品", parent)
        self.items = items
        self.setFixedSize(850, 600)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        # 4 Steps visual header
        step_box = QHBoxLayout()
        step_box.setSpacing(8)
        for s_num, s_title in [
            ("1", "选择当前语言"),
            ("2", "指定目标语言"),
            ("3", "复制AI提示词"),
            ("4", "回填并解析更新"),
        ]:
            lbl = QLabel(f"<b>步骤 {s_num}</b>: {s_title}")
            lbl.setStyleSheet("background: #f0f5ff; color: #1d39c4; border: 1px solid #adc6ff; border-radius: 4px; padding: 4px 8px; font-size: 11px;")
            step_box.addWidget(lbl)
        layout.addLayout(step_box)

        # Config Row
        cfg_row = QHBoxLayout()
        cfg_row.setSpacing(12)

        cfg_row.addWidget(QLabel("当前源语言:"))
        self.combo_source = QComboBox()
        self.combo_source.addItem("中文 (zh)", "zh")
        self.combo_source.addItem("英语 (en)", "en")
        cfg_row.addWidget(self.combo_source)

        cfg_row.addWidget(QLabel("目标刊登语言:"))
        self.combo_target = QComboBox()
        self.combo_target.addItem("🇪🇸 西班牙语 (es)", "es")
        self.combo_target.addItem("🇧🇷 葡萄牙语 (pt)", "pt")
        self.combo_target.addItem("🇺🇸 英语 (en)", "en")
        cfg_row.addWidget(self.combo_target)

        cfg_row.addWidget(QLabel("优化策略:"))
        self.bg_strategy = QButtonGroup(self)
        self.radio_translate = QRadioButton("标准直译")
        self.radio_diff = QRadioButton("差异化优化降重")
        self.radio_diff.setChecked(True)
        self.bg_strategy.addButton(self.radio_translate, 1)
        self.bg_strategy.addButton(self.radio_diff, 2)
        cfg_row.addWidget(self.radio_translate)
        cfg_row.addWidget(self.radio_diff)

        cfg_row.addStretch()
        layout.addLayout(cfg_row)

        # Copy Prompt Section
        prompt_hdr = QHBoxLayout()
        prompt_hdr.addWidget(QLabel("<b>步骤 3：生成的 AI 提示词与商品数据包</b>"))
        prompt_hdr.addStretch()
        btn_copy_prompt = QPushButton("📋 复制 Prompt 与数据")
        btn_copy_prompt.setStyleSheet("background: #1677ff; color: #ffffff; border: none; font-weight: bold; padding: 4px 12px;")
        btn_copy_prompt.clicked.connect(self._copy_prompt)
        prompt_hdr.addWidget(btn_copy_prompt)
        layout.addLayout(prompt_hdr)

        self.txt_prompt = QPlainTextEdit()
        self.txt_prompt.setReadOnly(True)
        self.txt_prompt.setFixedHeight(140)
        self.txt_prompt.setStyleSheet("background: #fafafa; font-family: monospace; font-size: 11px;")
        self.txt_prompt.setPlainText(self.generate_prompt())
        layout.addWidget(self.txt_prompt)

        # Refresh prompt when options change
        self.combo_target.currentIndexChanged.connect(self._refresh_prompt)
        self.radio_diff.toggled.connect(self._refresh_prompt)

        # Paste AI Result Section
        layout.addWidget(QLabel("<b>步骤 4：将 AI 返回的新数据粘贴在下方并解析更新</b>"))
        self.txt_paste = QPlainTextEdit()
        self.txt_paste.setPlaceholderText(
            "请将 AI 生成的结果直接粘贴在此处，格式示例：\n"
            "ID: PR001\n"
            "TITLE: Nuevo Carpa Impermeable para Exteriores...\n"
            "DESC: Descripción optimizada y puntos clave...\n"
            "---\n"
            "ID: PR002\n"
            "TITLE: ..."
        )
        self.txt_paste.setStyleSheet("font-family: monospace; font-size: 12px;")
        layout.addWidget(self.txt_paste)

        # Bottom Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("取 消")
        btn_cancel.setStyleSheet("background: #ffffff; color: #595959; border: 1px solid #d9d9d9;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_update = QPushButton("确认批量更新")
        btn_update.setStyleSheet("background: #52c41a; color: #ffffff; border: none; font-weight: bold; padding: 6px 18px;")
        btn_update.clicked.connect(self._on_update)
        btn_box.addWidget(btn_update)

        layout.addLayout(btn_box)

    def _refresh_prompt(self):
        self.txt_prompt.setPlainText(self.generate_prompt())

    def generate_prompt(self) -> str:
        target_lang = self.combo_target.currentText()
        is_diff = self.radio_diff.isChecked()

        strategy_instruction = (
            "【差异化降重优化】：在翻译的同时重新组织修饰词顺序、加入电商高频搜索词、生成本土化具有吸引力的标题与5点描述，彻底消除同质化。"
            if is_diff else
            "【标准电商直译】：精准地道直译为当地电商常用词汇。"
        )

        lines = [
            f"你是一名专业的美客多跨境电商 Listing 专家。请将以下商品翻译优化为【{target_lang}】。",
            f"处理要求：{strategy_instruction}",
            "请严格保持以下格式输出，每件商品之间用 --- 分隔：",
            "ID: <商品ID>",
            "TITLE: <优化后的新标题 (建议60字符以内)>",
            "DESC: <优化后的商品描述>",
            "---",
            "\n待处理商品清单："
        ]

        for it in self.items:
            lines.append(f"ID: {it.get('id')}")
            lines.append(f"TITLE: {it.get('title') or ''}")
            lines.append(f"DESC: {it.get('description') or ''}")
            lines.append("---")

        return "\n".join(lines)

    def _copy_prompt(self):
        cb = QApplication.clipboard()
        if cb:
            cb.setText(self.txt_prompt.toPlainText())
            QMessageBox.information(self, "已复制", "AI提示词与数据包已复制到剪贴板！可以直接发送给大模型。")

    def parse_pasted_data(self) -> List[Dict[str, str]]:
        raw = self.txt_paste.toPlainText().strip()
        if not raw:
            return []

        results = []
        blocks = raw.split("---")
        for b in blocks:
            b = b.strip()
            if not b:
                continue
            m_id = re.search(r"ID:\s*([^\n\r]+)", b, re.IGNORECASE)
            m_title = re.search(r"TITLE:\s*([^\n\r]+)", b, re.IGNORECASE)
            m_desc = re.search(r"DESC:\s*([\s\S]+)", b, re.IGNORECASE)

            if m_id and m_title:
                results.append({
                    "id": m_id.group(1).strip(),
                    "title": m_title.group(1).strip(),
                    "description": m_desc.group(1).strip() if m_desc else ""
                })

        return results

    def _on_update(self):
        parsed = self.parse_pasted_data()
        if not parsed:
            QMessageBox.warning(self, "解析失败", "未在粘贴内容中识别到有效的 ID 和 TITLE 格式，请检查格式后重试！")
            return
        self.accept()

    def get_updates(self) -> List[Dict[str, str]]:
        return self.parse_pasted_data()
