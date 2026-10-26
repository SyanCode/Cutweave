"""Composants d'interface partagés : boutons, cartes, grilles adaptatives et stylage automatique."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLayout, QLayoutItem, QPushButton, QSizePolicy, QSpinBox, QTableView,
    QVBoxLayout, QWidget,
)

from cutweave.ui import icons, theme
from cutweave.ui.theme import set_role

ICON_SIZE = 18


def rgba(color: str, alpha: float) -> str:
    value = QColor(color)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {int(alpha * 255)})"


def label(text: str, role: str | None = None, *, wrap: bool = False) -> QLabel:
    result = QLabel(text)
    if role:
        result.setProperty("role", role)
    result.setWordWrap(wrap)
    return result


def _icon_color(variant: str | None) -> str:
    return "#ffffff" if variant in {"primary", "cta"} else theme.DANGER if variant == "danger" \
        else theme.MUTED if variant in {"ghost", "segment"} else theme.TEXT


def apply_icon(button: QAbstractButton, name: str | None, variant: str | None = None, size: int = ICON_SIZE) -> None:
    if not name:
        return
    color = _icon_color(variant)
    active = "#ffffff" if variant != "danger" else theme.DANGER
    button.setIcon(icons.icon(name, color, size, active=active))
    button.setIconSize(icons.icon_size(size))


def button(text: str = "", variant: str | None = None, icon: str | None = None, *, checkable: bool = False,
           tooltip: str | None = None) -> QPushButton:
    result = QPushButton(text)
    if variant:
        result.setProperty("variant", variant)
    result.setCheckable(checkable)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    result.setProperty("cw_styled", True)
    result.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    apply_icon(result, icon, variant)
    if tooltip:
        result.setToolTip(tooltip)
    return result


def card(*, padding: int = 18, spacing: int = 12) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("card", "true")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(spacing)
    return frame, layout


def icon_badge(name: str, color: str, *, size: int = 40, glyph: int = 20, radius: int = 12) -> QLabel:
    """Pastille colorée avec une icône vectorielle (remplace les emoji)."""
    badge = QLabel()
    badge.setFixedSize(size, size)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(f"background: {rgba(color, 0.18)}; border-radius: {radius}px;")
    badge.setPixmap(icons.pixmap(name, color, glyph))
    return badge


class Segmented(QWidget):
    """Choix exclusif présenté en pastilles, avec icônes facultatives."""

    changed = Signal(object)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._values = []
        for index, option in enumerate(options):
            text, value, *rest = option
            item = button(text, "segment", rest[0] if rest else None, checkable=True)
            self.group.addButton(item, index)
            layout.addWidget(item)
            self._values.append(value)
        layout.addStretch()
        self.group.idClicked.connect(lambda index: self.changed.emit(self._values[index]))
        if self._values:
            self.group.button(0).setChecked(True)

    def value(self):
        index = self.group.checkedId()
        return self._values[index] if index >= 0 else None

    def set_value(self, value):
        if value in self._values:
            self.group.button(self._values.index(value)).setChecked(True)


class ReflowGrid(QWidget):
    """Grille dont le nombre de colonnes s'adapte à la largeur disponible (cartes de taille minimale fixe)."""

    def __init__(self, min_item_width: int = 260, max_columns: int = 4, spacing: int = 12, parent=None):
        super().__init__(parent)
        self.min_item_width = min_item_width
        self.max_columns = max_columns
        self.items: list[QWidget] = []
        self.columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)

    def add(self, widget: QWidget) -> None:
        self.items.append(widget)
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, widget.sizePolicy().verticalPolicy())
        self._relayout(force=True)

    def _columns_for(self, width: int) -> int:
        spacing = self._grid.horizontalSpacing()
        fit = (width + spacing) // (self.min_item_width + spacing)
        return max(1, min(self.max_columns, len(self.items) or 1, int(fit)))

    def _relayout(self, force: bool = False) -> None:
        columns = self._columns_for(self.width() if self.width() > 50 else self.min_item_width * 3)
        if columns == self.columns and not force:
            return
        self.columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, widget in enumerate(self.items):
            self._grid.addWidget(widget, index // columns, index % columns)
        for column in range(self.max_columns):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(self.min_item_width)
        return hint


class FormGrid(QWidget):
    """Formulaire libellé / contrôle sur 2 colonnes de paires quand il y a la place, sinon sur 1 colonne."""

    def __init__(self, pairs, *, breakpoint: int = 860, label_width: int = 130, parent=None):
        super().__init__(parent)
        self.pairs = [(left if isinstance(left, QWidget) else label(left, "muted"), right) for left, right in pairs]
        self.breakpoint = breakpoint
        self.label_width = label_width
        self.two_columns: bool | None = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(12)
        for left, right in self.pairs:
            right.setSizePolicy(QSizePolicy.Policy.Preferred, right.sizePolicy().verticalPolicy())
        self._relayout(force=True)

    def _relayout(self, force: bool = False) -> None:
        wide = self.width() >= self.breakpoint if self.width() > 50 else True
        if wide == self.two_columns and not force:
            return
        self.two_columns = wide
        while self._grid.count():
            self._grid.takeAt(0)
        per_row = 2 if wide else 1
        for index, (left, right) in enumerate(self.pairs):
            row, slot = divmod(index, per_row)
            base = slot * 3
            single_tail = wide and index == len(self.pairs) - 1 and len(self.pairs) % 2 == 1
            self._grid.addWidget(left, row, base, Qt.AlignmentFlag.AlignVCenter)
            self._grid.addWidget(right, row, base + 1, 1, 4 if single_tail else 1)
        for column in range(6):
            self._grid.setColumnStretch(column, 0)
        self._grid.setColumnStretch(1, 1)
        if wide:
            self._grid.setColumnStretch(4, 1)
        for column in (0, 3):
            self._grid.setColumnMinimumWidth(column, self.label_width if column == 0 or wide else 0)
        self._grid.setColumnMinimumWidth(2, 24 if wide else 0)
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(320)
        return hint


# --- Stylage automatique des boutons hérités (éditeur, dialogues, boîtes de message) --------------------

_RULES = (
    ("importer", "upload", None), ("ajouter", "plus", None), ("supprimer", "trash", "danger"),
    ("retirer", "trash", "danger"), ("purger", "trash", "danger"), ("exporter", "download", None),
    ("parcourir", "folder", None), ("lancer", "play", "primary"), ("démarrer", "play", "primary"),
    ("exécuter", "play", None), ("reprendre", "play", None), ("annuler", "x", None), ("enregistrer", "save", "primary"),
    ("appliquer", "check", "primary"), ("prévisualiser", "play", None), ("lecture", "play", None),
    ("réinitialiser", "refresh", None), ("dupliquer", "layers", None), ("modifier", "edit", None),
    ("fusionner", "layers", None), ("scinder", "scissors", None), ("comparer", "eye", None),
    ("sélectionner", "crop", None), ("voir ", "eye", None), ("vérifier", "check", None), ("relier", "link", None),
    ("aller", "chevron-right", None), ("insérer", "plus", None), ("sauvegarder", "save", "primary"), ("fermer", "x", None),
)
_STANDARD = {  # boutons standards des boîtes de dialogue (textes traduits par Qt)
    "ok": ("check", "primary"), "enregistrer": ("save", "primary"), "oui": ("check", "primary"),
    "non": (None, None), "annuler": ("x", None), "fermer": ("x", None), "appliquer": ("check", "primary"),
    "réessayer": ("refresh", None), "ignorer": (None, None), "enregistrer sous…": ("save", "primary"),
}


def style_button(widget: QPushButton) -> None:
    """Applique curseur, variante, icône et largeur minimale à un bouton qui n'a pas été créé par ``button()``."""
    if widget.property("cw_styled"):
        return
    widget.setProperty("cw_styled", True)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setFocusPolicy(Qt.FocusPolicy.TabFocus)   # pas d'anneau de focus persistant après un clic
    text = widget.text().replace("&", "").strip().lower()
    variant = widget.property("variant")
    name = None
    if text in _STANDARD:
        name, standard_variant = _STANDARD[text]
        variant = variant or standard_variant
    elif text:
        for keyword, icon_name, rule_variant in _RULES:
            if text.startswith(keyword):
                name, variant = icon_name, variant or rule_variant
                break
    if variant:
        set_role(widget, "variant", variant)
    if widget.icon().isNull() and name and widget.text():
        apply_icon(widget, name, variant)
    if widget.text() and not widget.property("variant") == "segment":
        widget.setMinimumWidth(max(widget.minimumWidth(), widget.sizeHint().width()))


def style_table(table: QTableView) -> None:
    """Tableaux plus lisibles : lignes aérées, alternance de fond, sans grille ni numéros de ligne."""
    if table.property("cw_styled"):
        return
    table.setProperty("cw_styled", True)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setMinimumSectionSize(48)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)


def style_field(widget: QWidget) -> None:
    """Champs numériques : largeur raisonnable au lieu d'une barre pleine largeur."""
    if widget.property("cw_styled"):
        return
    widget.setProperty("cw_styled", True)
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.setMaximumWidth(170)
        widget.setAlignment(Qt.AlignmentFlag.AlignRight)
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class ButtonStyler(QObject):
    """Filtre global : toute nouvelle fenêtre ou boîte de dialogue reçoit le même style de boutons et de tableaux."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show:
            if isinstance(obj, QPushButton):
                style_button(obj)
            elif isinstance(obj, QTableView):
                style_table(obj)
            elif isinstance(obj, (QSpinBox, QDoubleSpinBox)):
                style_field(obj)
        return False


def install_button_styler(app) -> ButtonStyler:
    styler = ButtonStyler(app)
    app.installEventFilter(styler)
    app._cw_button_styler = styler
    return styler


# --- Rangées qui passent à la ligne -------------------------------------------------------------------------

class FlowLayout(QLayout):
    """Disposition en flux : les éléments passent à la ligne quand la largeur manque (au lieu de déborder)."""

    def __init__(self, parent=None, *, hspacing: int = 8, vspacing: int = 8):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _layout(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line_height > 0:
                x, y, line_height = area.x(), y + line_height + self._v, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


_FIELDS = (QSpinBox, QDoubleSpinBox, QComboBox)
_FLOWABLE = (QPushButton, QCheckBox, QLabel, *_FIELDS)


def _group_row(layout: QHBoxLayout) -> list[QWidget] | None:
    """Éléments d'une rangée convertibles en flux ; un libellé reste collé au champ qui le suit."""
    widgets: list[QWidget] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is None:
            if item.layout() is not None:
                return None
            continue                      # espaceur
        if not isinstance(widget, _FLOWABLE):
            return None
        widgets.append(widget)
    if len(widgets) < 2:
        return None
    groups: list[QWidget] = []
    index = 0
    while index < len(widgets):
        current = widgets[index]
        following = widgets[index + 1] if index + 1 < len(widgets) else None
        if isinstance(current, QLabel) and following is not None and isinstance(following, _FIELDS):
            pair = QWidget()
            row = QHBoxLayout(pair)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(current)
            row.addWidget(following)
            groups.append(pair)
            index += 2
        else:
            groups.append(current)
            index += 1
    return groups


def flowify(root: QWidget) -> int:
    """Convertit les rangées horizontales de boutons/champs en dispositions à retour à la ligne."""
    converted = 0
    for layout in root.findChildren(QHBoxLayout):
        parent_layout = layout.parent()
        if not isinstance(parent_layout, QLayout):
            continue
        groups = _group_row(layout)
        if groups is None:
            continue
        index = parent_layout.indexOf(layout)
        if index < 0:
            continue
        flow = FlowLayout()
        for group in groups:
            flow.addWidget(group)
        while layout.count():
            layout.takeAt(0)
        if isinstance(parent_layout, QFormLayout):
            row, role = parent_layout.getLayoutPosition(layout)
            parent_layout.takeAt(index)
            parent_layout.setLayout(row, role, flow)
        elif hasattr(parent_layout, "insertLayout"):
            parent_layout.takeAt(index)
            parent_layout.insertLayout(index, flow)
        else:
            continue
        layout.deleteLater()
        converted += 1
    return converted


def tidy_page(root: QWidget) -> None:
    """Rend une page héritée adaptable : libellés longs repliés, formulaires empilés, rangées en flux."""
    for text in root.findChildren(QLabel):
        if len(text.text()) > 34 and not text.wordWrap() and text.pixmap().isNull() \
                and not text.property("cw_nowrap"):
            text.setWordWrap(True)
    for form in root.findChildren(QFormLayout):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    flowify(root)
