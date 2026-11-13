from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from items.vault_batch_settings import (
    BaseVaultDialog,
    DARK_TIP_LABEL_QSS,
    style_cancel_button,
    style_primary_button,
)


_LANG_LABELS: Dict[str, str] = {
    "en": "英文",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "zh": "中文",
}

_LANG_OPTIONS: List[tuple[str, str, str]] = [
    ("en", "英文", "🇺🇸"),
    ("es", "西班牙语", "🇪🇸"),
    ("pt", "葡萄牙语", "🇧🇷"),
    ("zh", "中文", "🇨🇳"),
]

_AI_WEB_LINKS: List[tuple[str, str]] = [
    ("豆包", "https://doubao.com/chat"),
    ("DeepSeek", "https://chat.deepseek.com"),
    ("Kimi", "https://www.kimi.ai"),
    ("通义", "https://tongyi.aliyun.com"),
    ("ChatGPT", "https://chatgpt.com"),
]


class AIProductOptimizeDialog(BaseVaultDialog):
    """1:1 YuanYou Free AI Title/Description Optimize & Translate Dialog."""

    def __init__(self, items: List[Dict[str, Any]], parent: Optional[QWidget] = None):
        super().__init__(f"AI辅助优化 - 已选 {len(items)} 个产品", parent)
        self.items = [dict(it) for it in items]
        self.resize(920, 760)
        self.setMinimumSize(780, 620)

        self.target_lang: Optional[str] = None
        self.task_mode: str = "optimize"  # "translate" | "optimize"
        self.scope: str = "both"          # "both" | "title" | "description"
        self._details_expanded: bool = False
        self._prompt_custom_edited: bool = False
        self._syncing_prompt: bool = False
        self._fetched_data: List[Dict[str, str]] = []

        self._lang_cards: Dict[str, QPushButton] = {}
        self._task_cards: Dict[str, QPushButton] = {}
        self._scope_buttons: Dict[str, QPushButton] = {}

        self._init_ui()
        self._fetch_source_data(silent=True)
        self._refresh_prompt(force=True)
        self._update_validation_hint()

    def _card_frame(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("VaultAICard")
        frame.setStyleSheet(
            "QFrame#VaultAICard { background: #202820; border: 1px solid #4E472F; border-radius: 8px; }"
        )
        return frame

    def _make_step_header(self, num: str, title: str, right_widget: Optional[QWidget] = None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        badge = QLabel(num)
        badge.setFixedSize(20, 20)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "background: #2F6B3F; color: #FFFFFF; border-radius: 10px; "
            "font-size: 11px; font-weight: bold;"
        )
        row.addWidget(badge)

        lbl = QLabel(title)
        lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F6F3EA;")
        row.addWidget(lbl)

        if right_widget is not None:
            row.addSpacing(8)
            row.addWidget(right_widget)

        row.addStretch()
        return row

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 6, 4)
        layout.setSpacing(12)

        # Intro Banner
        intro = QFrame()
        intro.setObjectName("VaultAIIntro")
        intro.setStyleSheet(
            "QFrame#VaultAIIntro { background: rgba(47, 107, 63, 0.22); border: 1px solid #2F6B3F; border-radius: 8px; }"
        )
        intro_l = QHBoxLayout(intro)
        intro_l.setContentsMargins(12, 9, 12, 9)
        lbl_intro = QLabel(
            "💡 借助免费AI处理产品标题/描述，例如：<b>翻译为英文</b>、<b>优化标题差异化</b>、<b>改写描述风格</b> 等，完全免费。"
        )
        lbl_intro.setStyleSheet("color: #E6E2D8; font-size: 12px; border: none; background: transparent;")
        lbl_intro.setWordWrap(True)
        intro_l.addWidget(lbl_intro)
        layout.addWidget(intro)

        # ================= CARD 1: Fetch source data =================
        c1 = self._card_frame()
        c1_l = QVBoxLayout(c1)
        c1_l.setContentsMargins(14, 12, 14, 12)
        c1_l.setSpacing(10)
        c1_l.addLayout(self._make_step_header("1", "获取产品哪种语言的标题/描述？"))

        src_row = QHBoxLayout()
        src_row.setSpacing(10)
        self.combo_source = QComboBox()
        self.combo_source.setMinimumWidth(150)
        self.combo_source.view().setMinimumWidth(160)
        self.combo_source.addItem("西班牙语 (es)", "es")
        self.combo_source.addItem("葡萄牙语 (pt)", "pt")
        self.combo_source.addItem("英文 (en)", "en")
        self.combo_source.addItem("中文 (zh)", "zh")
        src_row.addWidget(self.combo_source)

        self.btn_fetch = QPushButton("获取产品数据")
        style_primary_button(self.btn_fetch)
        self.btn_fetch.clicked.connect(lambda: self._fetch_source_data(silent=False))
        src_row.addWidget(self.btn_fetch)
        src_row.addStretch()
        c1_l.addLayout(src_row)

        # Fetched status bar + expandable details
        self.fetch_bar = QFrame()
        self.fetch_bar.setObjectName("VaultAIFetchBar")
        self.fetch_bar.setStyleSheet(
            "QFrame#VaultAIFetchBar { background: rgba(47, 107, 63, 0.18); border: 1px solid #2F6B3F; border-radius: 6px; }"
        )
        fb_l = QHBoxLayout(self.fetch_bar)
        fb_l.setContentsMargins(10, 6, 10, 6)
        self.lbl_fetch_summary = QLabel("✓ 已获取 0 条产品数据")
        self.lbl_fetch_summary.setStyleSheet("color: #4ADE80; font-size: 12px; font-weight: 600; border: none;")
        fb_l.addWidget(self.lbl_fetch_summary)
        fb_l.addStretch()

        self.btn_toggle_detail = QPushButton("查看详情 ▼")
        self.btn_toggle_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_detail.setStyleSheet(
            "QPushButton { background: transparent; color: #C8C3B7; border: none; font-size: 12px; padding: 2px 6px; }"
            "QPushButton:hover { color: #F6F3EA; }"
        )
        self.btn_toggle_detail.clicked.connect(self._toggle_details)
        fb_l.addWidget(self.btn_toggle_detail)
        c1_l.addWidget(self.fetch_bar)

        self.detail_box = QPlainTextEdit()
        self.detail_box.setReadOnly(True)
        self.detail_box.setFixedHeight(130)
        self.detail_box.setVisible(False)
        self.detail_box.setStyleSheet(
            "QPlainTextEdit { background: #18201C; border: 1px solid #4E472F; border-radius: 6px; "
            "color: #C8C3B7; font-size: 11px; padding: 6px; }"
        )
        c1_l.addWidget(self.detail_box)
        layout.addWidget(c1)

        # ================= CARD 2: Target language cards =================
        c2 = self._card_frame()
        c2_l = QVBoxLayout(c2)
        c2_l.setContentsMargins(14, 12, 14, 12)
        c2_l.setSpacing(10)
        c2_l.addLayout(self._make_step_header("2", "选择要修改产品的哪种语言？"))

        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        for code, label, flag in _LANG_OPTIONS:
            btn = QPushButton(f"{flag}\n{label}")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumSize(100, 66)
            btn.clicked.connect(lambda _, c=code: self._select_target_lang(c))
            self._lang_cards[code] = btn
            lang_row.addWidget(btn)
        lang_row.addStretch()
        c2_l.addLayout(lang_row)
        layout.addWidget(c2)

        # ================= CARD 3: Task mode, scope, links, editable prompt =================
        c3 = self._card_frame()
        c3_l = QVBoxLayout(c3)
        c3_l.setContentsMargins(14, 12, 14, 12)
        c3_l.setSpacing(10)

        links_widget = QWidget()
        links_widget.setStyleSheet("background: transparent;")
        links_l = QHBoxLayout(links_widget)
        links_l.setContentsMargins(0, 0, 0, 0)
        links_l.setSpacing(10)
        for name, url in _AI_WEB_LINKS:
            lnk = QPushButton(name)
            lnk.setCursor(Qt.CursorShape.PointingHandCursor)
            lnk.setStyleSheet(
                "QPushButton { background: transparent; color: #60A5FA; border: none; "
                "font-size: 12px; padding: 1px 4px; }"
                "QPushButton:hover { color: #93C5FD; text-decoration: underline; }"
            )
            lnk.clicked.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))
            links_l.addWidget(lnk)

        c3_l.addLayout(self._make_step_header("3", "选择处理方式，复制到免费AI", links_widget))

        # Two large task mode cards
        task_row = QHBoxLayout()
        task_row.setSpacing(12)
        for mode_id, title_txt, desc_txt in [
            ("translate", "🌐 翻译", "翻译为目标语言"),
            ("optimize", "✨ 优化", "优化标题或描述的表达"),
        ]:
            tbtn = QPushButton(f"{title_txt}\n{desc_txt}")
            tbtn.setCheckable(True)
            tbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            tbtn.setMinimumHeight(62)
            tbtn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tbtn.clicked.connect(lambda _, m=mode_id: self._select_task_mode(m))
            self._task_cards[mode_id] = tbtn
            task_row.addWidget(tbtn)
        c3_l.addLayout(task_row)

        # Scope segmented pills
        scope_row = QHBoxLayout()
        scope_row.setSpacing(8)
        lbl_scope = QLabel("处理范围:")
        lbl_scope.setStyleSheet("color: #E6E2D8; font-size: 12px; font-weight: 600;")
        scope_row.addWidget(lbl_scope)
        for sc_id, sc_label in [
            ("both", "标题+描述"),
            ("title", "仅标题"),
            ("description", "仅描述"),
        ]:
            sbtn = QPushButton(sc_label)
            sbtn.setCheckable(True)
            sbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            sbtn.clicked.connect(lambda _, s=sc_id: self._select_scope(s))
            self._scope_buttons[sc_id] = sbtn
            scope_row.addWidget(sbtn)
        scope_row.addStretch()
        c3_l.addLayout(scope_row)

        # Prompt header + reset link
        p_hdr = QHBoxLayout()
        lbl_p = QLabel("提示词")
        lbl_p.setStyleSheet("color: #E6E2D8; font-size: 12px; font-weight: 600;")
        p_hdr.addWidget(lbl_p)
        lbl_p_hint = QLabel("可自行修改，复制时使用当前内容")
        lbl_p_hint.setStyleSheet(DARK_TIP_LABEL_QSS)
        p_hdr.addWidget(lbl_p_hint)
        p_hdr.addStretch()

        btn_reset_prompt = QPushButton("恢复默认模板")
        btn_reset_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_prompt.setStyleSheet(
            "QPushButton { background: transparent; color: #9A9589; border: none; font-size: 11px; }"
            "QPushButton:hover { color: #E6E2D8; text-decoration: underline; }"
        )
        btn_reset_prompt.clicked.connect(lambda: self._refresh_prompt(force=True))
        p_hdr.addWidget(btn_reset_prompt)
        c3_l.addLayout(p_hdr)

        self.txt_prompt = QPlainTextEdit()
        self.txt_prompt.setReadOnly(False)
        self.txt_prompt.setFixedHeight(138)
        self.txt_prompt.setStyleSheet(
            "QPlainTextEdit { background: #18201C; color: #E6E2D8; border: 1px solid #4E472F; "
            "border-radius: 6px; padding: 8px; font-family: monospace; font-size: 12px; }"
        )
        self.txt_prompt.textChanged.connect(self._on_prompt_edited)
        c3_l.addWidget(self.txt_prompt)

        self.btn_copy_prompt = QPushButton("📋 复制（提示词 + 数据）")
        style_primary_button(self.btn_copy_prompt)
        self.btn_copy_prompt.setMinimumHeight(34)
        self.btn_copy_prompt.clicked.connect(self._copy_prompt)
        c3_l.addWidget(self.btn_copy_prompt)

        self.lbl_copy_status = QLabel("✓ 已复制到剪贴板，请打开AI网站粘贴")
        self.lbl_copy_status.setStyleSheet("color: #4ADE80; font-size: 12px; font-weight: 600;")
        self.lbl_copy_status.setVisible(False)
        c3_l.addWidget(self.lbl_copy_status)
        layout.addWidget(c3)

        # ================= CARD 4: Paste AI JSON Result =================
        c4 = self._card_frame()
        c4_l = QVBoxLayout(c4)
        c4_l.setContentsMargins(14, 12, 14, 12)
        c4_l.setSpacing(8)
        c4_l.addLayout(self._make_step_header("4", "将AI处理后的JSON粘贴到下方"))

        self.txt_paste = QPlainTextEdit()
        self.txt_paste.setMinimumHeight(135)
        self.txt_paste.setPlaceholderText(
            '将AI返回的JSON粘贴到这里...\n示例: [{"productId": "PR001", "title": "...", "description": "..."}]'
        )
        self.txt_paste.setStyleSheet(
            "QPlainTextEdit { background: #18201C; color: #E6E2D8; border: 1px solid #4E472F; "
            "border-radius: 6px; padding: 8px; font-family: monospace; font-size: 12px; }"
        )
        self.txt_paste.textChanged.connect(self._update_validation_hint)
        c4_l.addWidget(self.txt_paste)

        self.lbl_parse_status = QLabel("")
        self.lbl_parse_status.setVisible(False)
        self.lbl_parse_status.setWordWrap(True)
        c4_l.addWidget(self.lbl_parse_status)
        layout.addWidget(c4)

        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        # Sticky bottom validation hint & action footer
        self.lbl_bottom_hint = QLabel("")
        self.lbl_bottom_hint.setStyleSheet("color: #F87171; font-size: 12px;")
        outer.addWidget(self.lbl_bottom_hint)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.btn_cancel = QPushButton("取 消")
        style_cancel_button(self.btn_cancel)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_update = QPushButton("确认更新")
        style_primary_button(self.btn_update)
        self.btn_update.setMinimumWidth(110)
        self.btn_update.clicked.connect(self._on_update)
        btn_box.addWidget(self.btn_update)

        outer.addLayout(btn_box)

        self._style_card_buttons()

    def _style_card_buttons(self) -> None:
        for code, btn in self._lang_cards.items():
            active = code == self.target_lang
            if active:
                btn.setChecked(True)
                btn.setStyleSheet(
                    "QPushButton { background: #203A2B; color: #FFFFFF; border: 1.5px solid #4ADE80; "
                    "border-radius: 8px; padding: 8px 14px; font-size: 12px; font-weight: 700; }"
                )
            else:
                btn.setChecked(False)
                btn.setStyleSheet(
                    "QPushButton { background: #18201C; color: #E6E2D8; border: 1px solid #4E472F; "
                    "border-radius: 8px; padding: 8px 14px; font-size: 12px; font-weight: 500; }"
                    "QPushButton:hover { border-color: #6E5D2A; background: #1C231F; }"
                )

        for m_id, btn in self._task_cards.items():
            active = m_id == self.task_mode
            if active:
                btn.setChecked(True)
                btn.setStyleSheet(
                    "QPushButton { background: rgba(47, 107, 63, 0.25); color: #FFFFFF; border: 1.5px solid #4ADE80; "
                    "border-radius: 8px; padding: 10px 14px; text-align: left; font-size: 12px; font-weight: 700; }"
                )
            else:
                btn.setChecked(False)
                btn.setStyleSheet(
                    "QPushButton { background: #18201C; color: #C8C3B7; border: 1px solid #4E472F; "
                    "border-radius: 8px; padding: 10px 14px; text-align: left; font-size: 12px; }"
                    "QPushButton:hover { border-color: #6E5D2A; color: #F6F3EA; }"
                )

        for sc_id, btn in self._scope_buttons.items():
            active = sc_id == self.scope
            if active:
                btn.setChecked(True)
                btn.setStyleSheet(
                    "QPushButton { background: #2F6B3F; color: #FFFFFF; border: 1px solid #4ADE80; "
                    "border-radius: 5px; padding: 4px 12px; font-size: 12px; font-weight: 700; }"
                )
            else:
                btn.setChecked(False)
                btn.setStyleSheet(
                    "QPushButton { background: #18201C; color: #C8C3B7; border: 1px solid #4E472F; "
                    "border-radius: 5px; padding: 4px 12px; font-size: 12px; }"
                    "QPushButton:hover { border-color: #6E5D2A; color: #F6F3EA; }"
                )

    def _extract_item_text(self, item: Dict[str, Any], source_lang: str) -> Dict[str, str]:
        attrs = item.get("attributes") or {}
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except Exception:
                attrs = {}

        lang_titles = attrs.get("_lang_titles") if isinstance(attrs, dict) else {}
        lang_descs = attrs.get("_lang_descs") if isinstance(attrs, dict) else {}

        title = ""
        if isinstance(lang_titles, dict) and lang_titles.get(source_lang):
            title = str(lang_titles[source_lang])
        elif source_lang in ("zh", "es", "pt") and item.get(f"title_{source_lang}"):
            title = str(item.get(f"title_{source_lang}") or "")
        if not title:
            title = str(item.get("title") or "")

        desc = ""
        if isinstance(lang_descs, dict) and lang_descs.get(source_lang):
            desc = str(lang_descs[source_lang])
        if not desc:
            desc = str(item.get("description") or "")

        return {
            "productId": str(item.get("id") or ""),
            "title": title,
            "description": desc,
        }

    def _fetch_source_data(self, silent: bool = False) -> None:
        source_lang = str(self.combo_source.currentData() or "es")
        self._fetched_data = [self._extract_item_text(it, source_lang) for it in self.items]
        self.lbl_fetch_summary.setText(f"✓ 已获取 {len(self._fetched_data)} 条产品数据")

        lines = []
        for idx, row in enumerate(self._fetched_data, 1):
            lines.append(
                f"{idx}. [{row['productId']}]\n"
                f"   标题: {row['title'] or '（空）'}\n"
                f"   描述: {row['description'] or '（空）'}"
            )
        self.detail_box.setPlainText("\n\n".join(lines))
        if not silent:
            self.lbl_copy_status.setVisible(False)
            self._update_validation_hint()

    def _toggle_details(self) -> None:
        self._details_expanded = not self._details_expanded
        self.detail_box.setVisible(self._details_expanded)
        self.btn_toggle_detail.setText("收起 ▲" if self._details_expanded else "查看详情 ▼")

    def _select_target_lang(self, code: str) -> None:
        self.target_lang = code
        self._style_card_buttons()
        self._refresh_prompt(force=not self._prompt_custom_edited)
        self._update_validation_hint()

    def _select_task_mode(self, mode_id: str) -> None:
        self.task_mode = mode_id
        self._style_card_buttons()
        self._refresh_prompt(force=not self._prompt_custom_edited)

    def _select_scope(self, scope_id: str) -> None:
        self.scope = scope_id
        self._style_card_buttons()
        self._refresh_prompt(force=not self._prompt_custom_edited)

    def _on_prompt_edited(self) -> None:
        if not self._syncing_prompt:
            self._prompt_custom_edited = True

    def _build_default_prompt_template(self) -> str:
        lang_name = _LANG_LABELS.get(self.target_lang or "es", "西班牙语")
        if self.task_mode == "translate":
            if self.scope == "title":
                return (
                    f"请将以下JSON数组中每个对象的 title 字段翻译为{lang_name}，严格遵守以下规则：\n"
                    f"1. 仅处理 title 字段，productId 保持原样不变\n"
                    f"2. 翻译后简洁有力，符合跨境电商商品标题风格\n"
                    f"3. 翻译后的 title 不得超过60个字符，若超出请精简\n"
                    f'4. 如果文本内容中包含英文双引号，请转义为 \\" 或改为单引号，确保返回内容是合法JSON\n'
                    f"5. 直接返回完整JSON数组，不要输出任何解释或其他文字\n\n"
                    f"需要处理的数据："
                )
            if self.scope == "description":
                return (
                    f"请将以下JSON数组中每个对象的 description 字段翻译为{lang_name}，严格遵守以下规则：\n"
                    f"1. 仅处理 description 字段，productId 保持原样不变\n"
                    f"2. 翻译后专业自然，符合跨境电商商品描述风格\n"
                    f'3. 如果文本内容中包含英文双引号，请转义为 \\" 或改为单引号，确保返回内容是合法JSON\n'
                    f"4. 直接返回完整JSON数组，不要输出任何解释或其他文字\n\n"
                    f"需要处理的数据："
                )
            return (
                f"请将以下JSON数组中每个对象的 title 和 description 字段翻译为{lang_name}，严格遵守以下规则：\n"
                f"1. 仅处理 title 和 description 字段，productId 保持原样不变\n"
                f"2. 翻译后风格专业自然，符合跨境电商商品描述\n"
                f"3. 翻译后的 title 不得超过60个字符，若超出请精简\n"
                f'4. 如果文本内容中包含英文双引号，请转义为 \\" 或改为单引号，确保返回内容是合法JSON\n'
                f"5. 直接返回完整JSON数组，不要输出任何解释或其他文字\n\n"
                f"需要处理的数据："
            )

        # Optimize mode
        target_clause = (
            "title 和 description 字段"
            if self.scope == "both"
            else ("title 字段" if self.scope == "title" else "description 字段")
        )
        return (
            "你是一名熟悉美客多跨境电商规范的资深商品文案与本地化专家。\n\n"
            f"请对以下JSON数组中每个对象的 {target_clause} 进行优化（输出语言：{lang_name}），严格遵守以下规则：\n"
            "1. 仅处理指定文案字段，productId 保持原样不变\n"
            "2. title 要突出核心卖点、具有差异化和竞争力，避免与其他商品标题雷同（不超过60个字符）\n"
            "3. 描述条理清晰、语言自然地道，符合美客多买家阅读习惯\n"
            '4. 如果文本内容中包含英文双引号，请转义为 \\" 或改为单引号，确保返回内容是合法JSON\n'
            "5. 直接返回完整JSON数组，不要输出任何解释或其他文字\n\n"
            "需要处理的数据："
        )

    def _refresh_prompt(self, force: bool = False) -> None:
        if not force and self._prompt_custom_edited:
            return
        self._syncing_prompt = True
        try:
            self.txt_prompt.setPlainText(self._build_default_prompt_template())
            self._prompt_custom_edited = False
        finally:
            self._syncing_prompt = False

    def build_data_json(self) -> str:
        if not self._fetched_data:
            self._fetch_source_data(silent=True)
        payload = []
        for row in self._fetched_data:
            if self.scope == "title":
                payload.append({"productId": row["productId"], "title": row["title"]})
            elif self.scope == "description":
                payload.append({"productId": row["productId"], "description": row["description"]})
            else:
                payload.append({
                    "productId": row["productId"],
                    "title": row["title"],
                    "description": row["description"],
                })
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def generate_prompt(self) -> str:
        prompt_head = self.txt_prompt.toPlainText().strip() or self._build_default_prompt_template()
        return f"{prompt_head}\n\n{self.build_data_json()}"

    def _copy_prompt(self) -> None:
        cb = QApplication.clipboard()
        if cb:
            cb.setText(self.generate_prompt())
        self.lbl_copy_status.setVisible(True)

    def _parse_raw_text(self, raw: str) -> tuple[List[Dict[str, str]], Optional[str]]:
        text = raw.strip()
        if not text:
            return [], None

        # 1. Strip markdown code fences and parse JSON array (YuanYou primary format)
        cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

        if cleaned.startswith("[") or cleaned.startswith("{"):
            try:
                parsed = json.loads(cleaned)
                if not isinstance(parsed, list):
                    return [], "格式错误：粘贴内容应为JSON数组（以 [ 开头，以 ] 结尾）"
                results: List[Dict[str, str]] = []
                for idx, entry in enumerate(parsed, 1):
                    if not isinstance(entry, dict):
                        return [], f"第 {idx} 条数据不是合法对象"
                    pid = str(entry.get("productId") or entry.get("id") or "").strip()
                    if not pid:
                        return [], f"第 {idx} 条数据缺少 productId 字段，请勿修改该字段"
                    row: Dict[str, str] = {"id": pid, "productId": pid}
                    if "title" in entry and entry["title"] is not None:
                        row["title"] = str(entry["title"]).strip()
                    if "description" in entry and entry["description"] is not None:
                        row["description"] = str(entry["description"]).strip()
                    results.append(row)
                if not results:
                    return [], "JSON 数组为空，请粘贴至少一条优化结果"
                return results, None
            except json.JSONDecodeError:
                return [], "JSON 解析失败：格式不正确，AI 可能返回了额外说明文字"

        # 2. Fallback compatibility: ID: / TITLE: / DESC: blocks
        if re.search(r"ID:\s*[^\n\r]+", text, re.IGNORECASE):
            results = []
            for b in text.split("---"):
                b = b.strip()
                if not b:
                    continue
                m_id = re.search(r"ID:\s*([^\n\r]+)", b, re.IGNORECASE)
                m_title = re.search(r"TITLE:\s*([^\n\r]+)", b, re.IGNORECASE)
                m_desc = re.search(r"DESC:\s*([\s\S]+)", b, re.IGNORECASE)
                if m_id and (m_title or m_desc):
                    pid = m_id.group(1).strip()
                    results.append({
                        "id": pid,
                        "productId": pid,
                        "title": m_title.group(1).strip() if m_title else "",
                        "description": m_desc.group(1).strip() if m_desc else "",
                    })
            if results:
                return results, None

        return [], "格式错误：请粘贴标准 JSON 数组（以 [ 开头，以 ] 结尾）"

    def parse_pasted_data(self) -> List[Dict[str, str]]:
        parsed, _err = self._parse_raw_text(self.txt_paste.toPlainText())
        return parsed

    def _update_validation_hint(self) -> None:
        raw = self.txt_paste.toPlainText().strip()
        if not raw:
            self.lbl_parse_status.setVisible(False)
        else:
            parsed, err = self._parse_raw_text(raw)
            if err:
                self.lbl_parse_status.setText(f"✕ {err}")
                self.lbl_parse_status.setStyleSheet("color: #F87171; font-size: 12px;")
                self.lbl_parse_status.setVisible(True)
            else:
                self.lbl_parse_status.setText(f"✓ 解析成功，共 {len(parsed)} 条，点击下方「确认更新」提交")
                self.lbl_parse_status.setStyleSheet("color: #4ADE80; font-size: 12px; font-weight: 600;")
                self.lbl_parse_status.setVisible(True)

        if not self.target_lang:
            self.lbl_bottom_hint.setText("请选择要修改的目标语言（第2步）")
            self.lbl_bottom_hint.setVisible(True)
        else:
            self.lbl_bottom_hint.setVisible(False)

    def _on_update(self) -> None:
        if not self.target_lang:
            self._update_validation_hint()
            QMessageBox.warning(self, "提示", "请先在第2步选择要修改的目标语言！")
            return
        parsed, err = self._parse_raw_text(self.txt_paste.toPlainText())
        if err or not parsed:
            QMessageBox.warning(self, "解析失败", err or "请先将 AI 处理后的 JSON 数组粘贴到第4步输入框！")
            return
        self.accept()

    def get_updates(self) -> List[Dict[str, str]]:
        return self.parse_pasted_data()

    def get_target_language(self) -> Optional[str]:
        return self.target_lang

    def get_scope(self) -> str:
        return self.scope
