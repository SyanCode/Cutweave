"""Éditeur des champs d'un preset ASS déterministe."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLineEdit, QMessageBox, QSpinBox, QScrollArea, QVBoxLayout, QWidget,
)

from cutweave.stages.finishing.caption_presets import CaptionPreset, CaptionPresetError


class CaptionPresetDialog(QDialog):
    def __init__(self, preset: CaptionPreset, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Modifier le preset captions")
        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        self.name = QLineEdit(preset.name)
        self.font_name = QLineEdit(preset.font_name)
        self.font_size = QSpinBox(); self.font_size.setRange(8, 300); self.font_size.setValue(preset.font_size)
        self.text_color = QLineEdit(preset.text_color)
        self.highlight_color = QLineEdit(preset.highlight_color)
        self.outline_color = QLineEdit(preset.outline_color)
        self.shadow_color = QLineEdit(preset.shadow_color)
        self.background_color = QLineEdit(preset.background_color)
        self.outline_width = QDoubleSpinBox(); self.outline_width.setRange(0, 20); self.outline_width.setSingleStep(0.25); self.outline_width.setValue(preset.outline_width)
        self.shadow = QDoubleSpinBox(); self.shadow.setRange(0, 20); self.shadow.setSingleStep(0.25); self.shadow.setValue(preset.shadow)
        self.background_opacity = QSpinBox(); self.background_opacity.setRange(0, 255); self.background_opacity.setValue(preset.background_opacity)
        self.alignment = QComboBox()
        for value, label in ((1, "Bas gauche"), (2, "Bas centre"), (3, "Bas droite"),
                             (4, "Milieu gauche"), (5, "Milieu centre"), (6, "Milieu droite"),
                             (7, "Haut gauche"), (8, "Haut centre"), (9, "Haut droite")):
            self.alignment.addItem(label, value)
        self.alignment.setCurrentIndex(self.alignment.findData(preset.alignment))
        self.margin_horizontal = QDoubleSpinBox(); self.margin_horizontal.setRange(0, 30); self.margin_horizontal.setValue(preset.margin_horizontal_percent)
        self.margin_vertical = QDoubleSpinBox(); self.margin_vertical.setRange(0, 30); self.margin_vertical.setValue(preset.margin_vertical_percent)
        self.bold = QCheckBox(); self.bold.setChecked(preset.bold)
        self.animation = QComboBox()
        for value, label in (("none", "Aucune"), ("fade", "Fondu"), ("pop", "Apparition pop")):
            self.animation.addItem(label, value)
        self.animation.setCurrentIndex(self.animation.findData(preset.animation))
        self.animation_ms = QSpinBox(); self.animation_ms.setRange(0, 5000); self.animation_ms.setValue(preset.animation_ms)
        self.highlight_mode = QComboBox()
        self.highlight_mode.addItem("Aucun", "none")
        self.highlight_mode.addItem("Toute la caption", "cue")
        self.highlight_mode.setCurrentIndex(self.highlight_mode.findData(preset.highlight_mode))
        for label, widget in (
            ("Nom", self.name), ("Police", self.font_name), ("Taille (base 1080 px)", self.font_size),
            ("Couleur texte (#RRGGBB)", self.text_color), ("Couleur surbrillance", self.highlight_color),
            ("Couleur contour", self.outline_color), ("Couleur ombre", self.shadow_color),
            ("Couleur fond", self.background_color), ("Épaisseur contour", self.outline_width),
            ("Ombre", self.shadow), ("Opacité fond (0 transparent)", self.background_opacity),
            ("Alignement", self.alignment), ("Marge horizontale (%)", self.margin_horizontal),
            ("Marge verticale (%)", self.margin_vertical), ("Gras", self.bold),
            ("Animation", self.animation), ("Durée animation (ms)", self.animation_ms),
            ("Style surbrillance", self.highlight_mode),
        ):
            form.addRow(label, widget)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.buttons)
        self.resize(540, 720)

    def preset(self) -> CaptionPreset:
        result = CaptionPreset(
            self.name.text().strip(), self.font_name.text().strip(), self.font_size.value(),
            self.text_color.text().strip(), self.highlight_color.text().strip(),
            self.outline_color.text().strip(), self.shadow_color.text().strip(),
            self.outline_width.value(), self.shadow.value(), self.background_color.text().strip(),
            self.background_opacity.value(), self.alignment.currentData(),
            self.margin_horizontal.value(), self.margin_vertical.value(), self.bold.isChecked(),
            self.animation.currentData(), self.animation_ms.value(), self.highlight_mode.currentData(),
        )
        return result

    def _validate_and_accept(self):
        try:
            self.preset().validate()
        except CaptionPresetError as exc:
            QMessageBox.warning(self, "Preset invalide", str(exc))
            return
        self.accept()
