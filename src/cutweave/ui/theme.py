"""Thème sombre de l'application (palette, typographie et feuille de style Qt)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

BG = "#0d0e12"
SURFACE = "#15171d"
SURFACE_2 = "#1c1f27"
SURFACE_3 = "#252935"
BORDER = "#2a2e3a"
BORDER_STRONG = "#3a3f4e"
TEXT = "#eceef3"
MUTED = "#8b92a5"
FAINT = "#5d6375"
ACCENT = "#8b5cff"
ACCENT_2 = "#ff3d7f"
SUCCESS = "#22c55e"
DANGER = "#ff5470"
GRADIENT = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT_2})"
GRADIENT_HOVER = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #9a70ff, stop:1 #ff5a92)"

STYLESHEET = f"""
* {{ font-family: "Inter", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue", Arial, sans-serif; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; font-size: 10pt; }}
QWidget#appRoot, QWidget#page, QStackedWidget#pages, QScrollArea#pageScroll, QWidget#scrollBody {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-size: 21pt; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 13pt; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="faint"] {{ color: {FAINT}; font-size: 9pt; }}
QLabel[role="step"] {{ color: {ACCENT}; font-weight: 700; font-size: 9pt; }}
QLabel[role="chip"] {{ background: {SURFACE_3}; color: {MUTED}; border-radius: 9px; padding: 2px 9px; font-size: 9pt; }}
QLabel[role="badge"] {{ background: {GRADIENT}; color: white; border-radius: 13px; font-weight: 800; font-size: 11pt; }}

QFrame[card="true"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame[card="true"][selected="true"] {{ border: 2px solid {ACCENT}; background: #1b1830; }}
QFrame[card="true"][hover="true"] {{ border-color: {BORDER_STRONG}; background: {SURFACE_2}; }}
QFrame#dropZone {{ background: {SURFACE}; border: 2px dashed {BORDER_STRONG}; border-radius: 16px; }}
QFrame#dropZone[active="true"] {{ border-color: {ACCENT}; background: #1b1830; }}
QFrame#sidebar {{ background: #0a0b0e; border-right: 1px solid #1d2028; }}
QFrame#bottomBar {{ background: #0a0b0e; border-top: 1px solid #1d2028; }}
QFrame#playerFrame {{ background: #050608; border: 1px solid {BORDER}; border-radius: 14px; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 7px 14px; border-radius: 9px; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: #2d3240; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {SURFACE_2}; }}
QPushButton:checked {{ background: #2a2347; border-color: {ACCENT}; color: white; }}
QPushButton[variant="primary"] {{ background: {GRADIENT}; border: 0; color: white; font-weight: 700; padding: 10px 20px; }}
QPushButton[variant="primary"]:hover {{ background: {GRADIENT_HOVER}; }}
QPushButton[variant="primary"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[variant="cta"] {{ background: {GRADIENT}; border: 0; color: white; font-weight: 800; font-size: 12pt;
    padding: 14px 28px; border-radius: 12px; }}
QPushButton[variant="cta"]:hover {{ background: {GRADIENT_HOVER}; }}
QPushButton[variant="cta"]:disabled {{ background: {SURFACE_3}; color: {FAINT}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton[variant="ghost"]:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton[variant="danger"] {{ background: transparent; border: 1px solid #5a2433; color: {DANGER}; }}
QPushButton[variant="danger"]:hover {{ background: #2a1219; }}
QPushButton[variant="segment"] {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 12px; color: {MUTED}; }}
QPushButton[variant="segment"]:checked {{ background: #2a2347; border-color: {ACCENT}; color: white; font-weight: 600; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 7px 10px;
    border-radius: 8px; selection-background-color: {ACCENT}; }}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{ border-color: {BORDER_STRONG}; }}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; selection-background-color: #2a2347;
    outline: 0; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {BORDER_STRONG}; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QTableWidget, QTextEdit, QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: {BORDER}; alternate-background-color: {SURFACE_2}; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: #2a2347; color: white; }}
QHeaderView::section {{ background: {SURFACE_2}; color: {MUTED}; padding: 7px; border: 0; border-bottom: 1px solid {BORDER};
    font-weight: 600; }}
QTableCornerButton::section {{ background: {SURFACE_2}; border: 0; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 12px; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; margin-right: 2px; border-radius: 8px; }}
QTabBar::tab:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QTabBar::tab:selected {{ background: #2a2347; color: white; font-weight: 600; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding-top: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}

QProgressBar {{ background: {SURFACE_2}; border: 0; border-radius: 6px; text-align: center; color: {TEXT};
    min-height: 12px; max-height: 12px; font-size: 8pt; }}
QProgressBar::chunk {{ background: {GRADIENT}; border-radius: 6px; }}
QProgressBar[size="large"] {{ min-height: 16px; max-height: 16px; border-radius: 8px; }}
QProgressBar[size="large"]::chunk {{ border-radius: 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {GRADIENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QListWidget#nav {{ background: transparent; border: 0; outline: 0; padding: 4px 0; }}
QListWidget#nav::item {{ padding: 9px 12px; margin: 1px 10px; border-radius: 8px; color: {MUTED}; }}
QListWidget#nav::item:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#nav::item:selected {{ background: #221d3a; color: white; }}
QListWidget#thumbs {{ background: transparent; border: 0; }}
QListWidget#thumbs::item {{ background: {SURFACE_2}; border-radius: 10px; padding: 6px; margin: 4px; color: {MUTED}; }}
QListWidget#thumbs::item:selected {{ background: #2a2347; color: white; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {BORDER_STRONG}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QMenuBar {{ background: #0a0b0e; color: {MUTED}; }}
QMenuBar::item:selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2a2347; }}
QStatusBar {{ background: #0a0b0e; color: {MUTED}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 5px; border-radius: 6px; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, SURFACE), (QPalette.ColorRole.AlternateBase, SURFACE_2),
        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Button, SURFACE_2),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.PlaceholderText, FAINT),
        (QPalette.ColorRole.ToolTipBase, SURFACE_3), (QPalette.ColorRole.ToolTipText, TEXT),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)


def set_role(widget: QWidget, name: str, value: str) -> QWidget:
    """Définit une propriété dynamique utilisée par la feuille de style et réapplique le style."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget
