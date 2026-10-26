"""Notifications éphémères, non bloquantes, en bas à droite de la fenêtre."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from cutweave.ui import icons, theme

LEVELS = {"info": ("sparkles", theme.ACCENT), "success": ("check", theme.SUCCESS), "error": ("alert", theme.DANGER)}


class Toast(QFrame):
    def __init__(self, parent: QWidget, message: str, level: str = "info", duration_ms: int = 4500):
        super().__init__(parent)
        name, color = LEVELS.get(level, LEVELS["info"])
        self.setObjectName("toast")
        self.setStyleSheet(f"QFrame#toast {{ background: {theme.SURFACE_3}; border: 1px solid {theme.BORDER_STRONG};"
                           f"border-left: 3px solid {color}; border-radius: 10px; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 16, 10)
        row.setSpacing(10)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(name, color, 18))
        glyph.setFixedSize(20, 20)
        row.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel(message)
        text.setWordWrap(True)
        text.setMaximumWidth(380)
        row.addWidget(text, 1)
        self.adjustSize()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(220)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QTimer.singleShot(duration_ms, self.dismiss)
        self.show()

    def dismiss(self):
        try:
            self._fade.stop()
            self._fade.setStartValue(self._effect.opacity())
            self._fade.setEndValue(0.0)
            self._fade.finished.connect(self.deleteLater)
            self._fade.start()
        except RuntimeError:  # déjà détruit avec la fenêtre
            pass

    def mousePressEvent(self, event):
        self.dismiss()


class ToastHost:
    """Empile jusqu'à trois notifications au-dessus de la barre de progression."""

    def __init__(self, window: QWidget, bottom_margin: int = 64):
        self.window = window
        self.bottom_margin = bottom_margin
        self.toasts: list[Toast] = []

    def show(self, message: str, level: str = "info", duration_ms: int = 4500) -> Toast:
        self.toasts = [toast for toast in self.toasts if toast.isVisible()]
        while len(self.toasts) >= 3:
            self.toasts.pop(0).dismiss()
        toast = Toast(self.window, message, level, duration_ms)
        self.toasts.append(toast)
        toast.destroyed.connect(self.relayout)
        self.relayout()
        return toast

    def relayout(self, *_args):
        self.toasts = [toast for toast in self.toasts if _alive(toast)]
        try:
            y = self.window.height() - self.bottom_margin
            width = self.window.width()
            for toast in reversed(self.toasts):
                toast.adjustSize()
                y -= toast.height() + 10
                toast.move(width - toast.width() - 24, y)
                toast.raise_()
        except RuntimeError:   # fenêtre déjà détruite pendant la fermeture
            self.toasts = []


def _alive(widget: QWidget) -> bool:
    try:
        return widget.isVisible()
    except RuntimeError:
        return False
