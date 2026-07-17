from __future__ import annotations


COLORS = {
    "main": "#171B19",
    "secondary": "#1C231F",
    "card": "#202820",
    "input": "#18201C",
    "table": "#171D1A",
    "green": "#2F6B3F",
    "green_hover": "#356F45",
    "green_selected": "#203A2B",
    "gold": "#4E472F",
    "gold_focus": "#8A7432",
    "text": "#E6E2D8",
    "muted": "#AFA89B",
    "weak": "#777266",
}


APP_QSS = f"""
QWidget {{
  background: {COLORS['main']};
  color: {COLORS['text']};
  font-family: "Microsoft YaHei UI";
  font-size: 10pt;
}}
QFrame#surface, QFrame#brandSurface {{
  background: {COLORS['card']};
  border: 1px solid {COLORS['gold']};
  border-radius: 8px;
}}
QFrame#brandSurface {{ background: {COLORS['secondary']}; }}
QWidget#controlContent {{ background: transparent; border: 0; }}
QFrame#controlSection {{
  background: {COLORS['table']};
  border: 1px solid {COLORS['gold']};
  border-radius: 8px;
}}
QLabel#brandTitle {{ font-size: 16pt; font-weight: 700; color: #F6F3EA; }}
QLabel#brandSubtitle, QLabel#muted {{ color: {COLORS['muted']}; }}
QLabel#sectionTitle {{ font-size: 11pt; font-weight: 700; color: #F6F3EA; }}
QComboBox, QSpinBox, QLineEdit, QDateEdit, QTextEdit, QListWidget, QTableWidget {{
  background: {COLORS['input']};
  color: {COLORS['text']};
  border: 1px solid {COLORS['gold']};
  border-radius: 6px;
  padding: 6px 9px;
  selection-background-color: {COLORS['green']};
  selection-color: #F6F3EA;
}}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus, QDateEdit:focus {{ border-color: {COLORS['gold_focus']}; }}
QComboBox::drop-down {{
  subcontrol-origin: padding;
  subcontrol-position: top right;
  width: 30px;
  border-left: 1px solid {COLORS['gold']};
  background: {COLORS['input']};
}}
QComboBox::down-arrow {{
  image: url("@CHEVRON_DOWN@");
  width: 9px;
  height: 6px;
}}
QComboBox QAbstractItemView {{
  background: {COLORS['card']};
  color: {COLORS['text']};
  border: 1px solid {COLORS['gold']};
  outline: none;
  padding: 3px;
}}
QSpinBox::up-button, QSpinBox::down-button, QDateEdit::drop-down {{
  background: {COLORS['input']};
  border-left: 1px solid {COLORS['gold']};
  width: 24px;
}}
QSpinBox::up-arrow {{ image: url("@CHEVRON_UP@"); width: 9px; height: 6px; }}
QSpinBox::down-arrow, QDateEdit::down-arrow {{ image: url("@CHEVRON_DOWN@"); width: 9px; height: 6px; }}
QPushButton {{
  background: #232C24;
  color: #F6F3EA;
  border: 1px solid {COLORS['gold']};
  border-radius: 7px;
  padding: 7px 14px;
}}
QPushButton:hover {{ background: #26352C; }}
QPushButton:pressed {{ background: #1F5A34; }}
QPushButton#primary {{ background: {COLORS['green']}; border-color: #6D5B2A; font-weight: 700; }}
QPushButton#primary:hover {{ background: {COLORS['green_hover']}; }}
QPushButton#nav[checked="true"] {{ background: {COLORS['green_selected']}; border-color: #3E7B4B; }}
QHeaderView::section {{
  background: {COLORS['card']};
  color: {COLORS['text']};
  border: 0;
  border-right: 1px solid {COLORS['gold']};
  border-bottom: 1px solid {COLORS['gold']};
  padding: 8px;
  font-weight: 700;
}}
QTableWidget {{
  gridline-color: {COLORS['gold']};
  alternate-background-color: {COLORS['card']};
  border-radius: 0;
}}
QTableWidget::item {{ border: 0; padding: 6px; }}
QTableWidget::item:selected {{ background: {COLORS['green']}; color: #F6F3EA; }}
QTabWidget::pane {{ border: 1px solid {COLORS['gold']}; border-radius: 6px; }}
QTabBar::tab {{ background: {COLORS['secondary']}; border: 1px solid {COLORS['gold']}; padding: 8px 14px; }}
QTabBar::tab:selected {{ background: {COLORS['green_selected']}; }}
QScrollBar:vertical {{ background: {COLORS['main']}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {COLORS['gold']}; min-height: 24px; border-radius: 5px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: {COLORS['main']}; }}
QScrollBar:horizontal {{ background: {COLORS['main']}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {COLORS['gold']}; min-width: 24px; border-radius: 5px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: {COLORS['main']}; }}
QToolTip {{ background: {COLORS['card']}; color: {COLORS['text']}; border: 1px solid {COLORS['gold']}; }}
"""
