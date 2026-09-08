"""Restrained cream and moss palette for clear native widgets."""

BG = "#f8f8fc"
INK = "#303959"
MOSS = "#527a60"
MUTED = "#7b8199"
AMBER = "#b98242"
STATE_COLORS = {"Focused": "#527a60", "Normal": "#a0b89e", "Distracted": "#c3976a", "Rest": "#87aeb4", "Unknown": "#bdc2bb", "Paused": "#b8b8ba"}

STYLE = """
QWidget { color: #303959; font-size: 13px; }
QDialog, QMainWindow { background: #f8f8fc; }
QLabel { background: transparent; }
QLabel[role='eyebrow'] { color: #7b8199; font-size: 10px; font-weight: 650; letter-spacing: 2px; }
QLabel[role='title'] { font-size: 25px; font-weight: 650; color: #303959; }
QLabel[role='subtitle'] { font-size: 12px; color: #7b8199; }
QLabel[role='metric'] { font-size: 37px; font-weight: 600; }
QLabel[role='section'] { font-size: 15px; font-weight: 650; }
QFrame[card='true'] { background: #fffefa; border: 1px solid #dedfed; border-radius: 13px; }
QPushButton, QToolButton { background: #fffefa; border: 1px solid #d9ddec; border-radius: 7px; padding: 9px 13px; }
QPushButton:hover, QToolButton:hover { background: #eaf0e4; border-color: #92ab91; }
QPushButton:pressed { background: #dce7d7; }
QPushButton:disabled { color: #abb2a9; background: #eeeee7; }
QPushButton[primary='true'] { background: #527a60; color: white; border-color: #527a60; font-weight: 600; }
QPushButton[primary='true']:hover { background: #426950; }
QPushButton[primary='true']:disabled { background: #dfe4df; color: #88938b; border-color: #d7ded6; }
QPushButton[danger='true'] { color: #99644f; }
QToolButton:checked { background: #e8efdf; border: 2px solid #527a60; }
QLineEdit, QComboBox, QSpinBox { background: #fffefa; border: 1px solid #d9ddec; border-radius: 5px; padding: 7px; selection-background-color: #b9d0b6; }
QComboBox::drop-down { border: 0px; width: 22px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QTabWidget::pane { border: 0; border-top: 1px solid #d9ddec; background: #f8f8fc; }
QTabBar::tab { padding: 12px 18px; background: transparent; color: #7b8199; }
QTabBar::tab:selected { color: #303959; border-bottom: 2px solid #527a60; }
QTableWidget { background: #fffefa; border: 1px solid #dedfed; border-radius: 6px; gridline-color: #f0f0e8; selection-background-color: #e3ecdc; selection-color: #303959; }
QHeaderView::section { background: #eeeee5; border: 0; padding: 8px; color: #7b8199; font-size: 11px; }
QScrollArea { border: 0; background: transparent; }
QScrollArea > QWidget > QWidget { background: #f8f8fc; }
QMenu { background: #fffefa; border: 1px solid #d9ddec; padding: 5px; }
QMenu::item { padding: 7px 22px; }
QMenu::item:selected { background: #e3ecdc; }
QGroupBox { border: 1px solid #d9ddec; border-radius: 8px; margin-top: 18px; padding: 15px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0px 5px; }
QToolTip { background: #fffefa; color: #303959; border: 1px solid #d9ddec; padding: 7px; }
"""

STYLE += """
QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
QScrollBar::handle:vertical { background: #d5d9e6; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
