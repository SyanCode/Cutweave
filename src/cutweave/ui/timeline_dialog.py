"""Frise visuelle du montage : un bloc par plan, durée réglable à la souris ou au clavier."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from cutweave.autoedit.planner import EditPlan, shot_starts, timeline_duration
from cutweave.ui import theme
from cutweave.ui.widgets import button, label

MIN_SHOT = 0.4
HANDLE = 7


class TimelineStrip(QWidget):
    """Blocs proportionnels ; on tire le bord droit d'un plan pour l'allonger ou le raccourcir."""

    selectionChanged = Signal(int)
    durationsChanged = Signal()

    def __init__(self, plan: EditPlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.original = [shot.duration for shot in plan.shots]
        self.durations = list(self.original)
        self.removed: set[int] = set()
        self.selected = 0
        self._drag: int | None = None
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # --- Géométrie ----------------------------------------------------------------------------

    def total(self) -> float:
        return max(0.1, sum(d for i, d in enumerate(self.durations) if i not in self.removed))

    def _layout(self) -> list[tuple[int, float, float]]:
        """[(indice, x, largeur)] pour les plans conservés, remplissant la largeur disponible."""
        width = max(1, self.width() - 2 * 8)
        scale = width / self.total()
        x, blocks = 8.0, []
        for index, duration in enumerate(self.durations):
            if index in self.removed:
                continue
            blocks.append((index, x, duration * scale))
            x += duration * scale
        return blocks

    def seconds_per_pixel(self) -> float:
        return self.total() / max(1, self.width() - 16)

    def set_duration(self, index: int, value: float) -> None:
        self.durations[index] = max(MIN_SHOT, value)
        self.update()
        self.durationsChanged.emit()

    def select(self, index: int) -> None:
        self.selected = index
        self.update()
        self.selectionChanged.emit(index)

    def remove_selected(self) -> None:
        if len(self.durations) - len(self.removed) <= 1:
            return
        self.removed.add(self.selected)
        remaining = [i for i, *_ in self._layout()]
        self.select(remaining[0] if remaining else 0)
        self.durationsChanged.emit()

    def reset(self) -> None:
        self.durations, self.removed = list(self.original), set()
        self.update()
        self.durationsChanged.emit()

    def changed(self) -> bool:
        return bool(self.removed) or any(abs(a - b) > 1e-3 for a, b in zip(self.durations, self.original))

    # --- Souris -------------------------------------------------------------------------------

    def _hit(self, x: float):
        for index, left, width in self._layout():
            if abs(x - (left + width)) <= HANDLE:
                return index, True
            if left <= x < left + width:
                return index, False
        return None, False

    def mousePressEvent(self, event):
        index, on_handle = self._hit(event.position().x())
        if index is not None:
            self.select(index)
            if on_handle:
                self._drag = index
        self.setFocus()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            block = next((b for b in self._layout() if b[0] == self._drag), None)
            if block:
                scale = self.seconds_per_pixel()
                self.set_duration(self._drag, (event.position().x() - block[1]) * scale)
            return
        _index, on_handle = self._hit(event.position().x())
        self.setCursor(Qt.CursorShape.SizeHorCursor if on_handle else Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_selected()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            order = [i for i, *_ in self._layout()]
            if self.selected in order:
                step = -1 if key == Qt.Key.Key_Left else 1
                self.select(order[max(0, min(len(order) - 1, order.index(self.selected) + step))])
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Minus, Qt.Key.Key_Equal):
            delta = -0.1 if key == Qt.Key.Key_Minus else 0.1
            self.set_duration(self.selected, self.durations[self.selected] + delta)
        else:
            super().keyPressEvent(event)

    # --- Dessin -------------------------------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(theme.ACCENT)
        base = QColor(theme.SURFACE)
        text = QColor(theme.TEXT)
        painter.fillRect(self.rect(), base)
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
        painter.setFont(font)
        for index, left, width in self._layout():
            rect = QRectF(left + 1, 14, max(2.0, width - 2), self.height() - 28)
            shot = self.plan.shots[index]
            fill = QColor(accent)
            fill.setAlpha(150 if shot.speech else 95)
            painter.setPen(QPen(accent if index == self.selected else QColor(0, 0, 0, 0), 2))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 7, 7)
            if rect.width() > 34:
                painter.setPen(text)
                painter.drawText(rect.adjusted(6, 6, -4, -4), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                                 f"{index + 1}")
                painter.drawText(rect.adjusted(6, 4, -4, -8), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                 f"{self.durations[index]:.1f} s")
            if index == self.selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawRoundedRect(QRectF(rect.right() - 3, rect.top() + 8, 5, rect.height() - 16), 2, 2)
        painter.end()

    def sizeHint(self):
        return super().sizeHint().expandedTo(self.minimumSize())


class TimelineDialog(QDialog):
    def __init__(self, plan: EditPlan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajuster la frise")
        self.resize(860, 330)
        self.plan = plan
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(label("Tire le bord droit d'un plan pour l'allonger ou le raccourcir (±0,1 s avec + et −), "
                               "Suppr pour le retirer. Musique, effets et sous-titres suivent automatiquement. "
                               "Les retouches faites dans l'éditeur sur ce montage seront refaites.", "muted",
                               wrap=True))
        self.strip = TimelineStrip(plan)
        layout.addWidget(self.strip)
        controls = QHBoxLayout()
        self.title = label("", "h3")
        controls.addWidget(self.title, 1)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(MIN_SHOT, 60.0)
        self.spin.setSingleStep(0.1)
        self.spin.setSuffix(" s")
        self.spin.valueChanged.connect(self._spin_changed)
        controls.addWidget(self.spin)
        self.remove = button("Retirer ce plan", "ghost", "trash")
        self.remove.clicked.connect(self.strip.remove_selected)
        controls.addWidget(self.remove)
        reset = button("Tout rétablir", "ghost")
        reset.clicked.connect(self.strip.reset)
        controls.addWidget(reset)
        layout.addLayout(controls)
        footer = QHBoxLayout()
        self.total = label("", "faint")
        footer.addWidget(self.total, 1)
        cancel = button("Annuler", "ghost")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.apply = button("Appliquer", "primary", "check")
        self.apply.clicked.connect(self.accept)
        footer.addWidget(self.apply)
        layout.addLayout(footer)
        self.strip.selectionChanged.connect(lambda _i: self._sync())
        self.strip.durationsChanged.connect(self._sync)
        self._sync()

    def _spin_changed(self, value: float):
        if abs(self.strip.durations[self.strip.selected] - value) > 1e-3:
            self.strip.set_duration(self.strip.selected, value)

    def _sync(self):
        index = self.strip.selected
        self.title.setText(f"Plan {index + 1} sur {len(self.strip.durations)}"
                           + (" · voix" if self.plan.shots[index].speech else ""))
        self.spin.blockSignals(True)
        self.spin.setValue(self.strip.durations[index])
        self.spin.blockSignals(False)
        self.remove.setEnabled(len(self.strip.durations) - len(self.strip.removed) > 1)
        self.total.setText(f"Durée totale : {self.strip.total():.1f} s"
                           + (f" (avant : {sum(self.strip.original):.1f} s)" if self.strip.changed() else ""))
        self.apply.setEnabled(self.strip.changed())

    def result_edit(self) -> tuple[list[float], list[int]]:
        return list(self.strip.durations), sorted(self.strip.removed)
