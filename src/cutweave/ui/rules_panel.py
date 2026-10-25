"""Panneau des marqueurs manuels et des règles déterministes (SFX et B-roll)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from cutweave.stages.composition.broll_rules import BrollRule
from cutweave.stages.finishing.sfx_rules import EVENT_KINDS, SfxRule, TimelineMarker


def _readonly_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setStretchLastSection(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    return table


def _selected_rows(table: QTableWidget) -> list[int]:
    return sorted({index.row() for index in table.selectionModel().selectedRows()})


class SfxRuleDialog(QDialog):
    def __init__(self, audio_assets, rule: SfxRule | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Règle SFX")
        self.assets = list(audio_assets)
        form = QFormLayout(self)
        self.event = QComboBox()
        for key, label in EVENT_KINDS.items():
            self.event.addItem(label, key)
        self.asset = QComboBox()
        for asset in self.assets:
            self.asset.addItem(f"{asset.name} ({asset.category})", asset.id)
        self.volume = QDoubleSpinBox(); self.volume.setRange(0, 4); self.volume.setSingleStep(0.05); self.volume.setValue(1)
        self.offset = QSpinBox(); self.offset.setRange(-10_000, 10_000); self.offset.setSuffix(" ms")
        self.source_in = QSpinBox(); self.source_in.setRange(0, 86_400_000); self.source_in.setSuffix(" ms")
        self.source_out = QSpinBox(); self.source_out.setRange(0, 86_400_000); self.source_out.setSuffix(" ms")
        self.source_out.setSpecialValueText("fin du fichier")
        self.fade_in = QSpinBox(); self.fade_in.setRange(0, 60_000); self.fade_in.setSuffix(" ms")
        self.fade_out = QSpinBox(); self.fade_out.setRange(0, 60_000); self.fade_out.setSuffix(" ms")
        self.label_filter = QLineEdit()
        self.label_filter.setPlaceholderText("libellé exact du marqueur, ou mot présent dans la caption")
        self.min_interval = QSpinBox(); self.min_interval.setRange(0, 600_000); self.min_interval.setSuffix(" ms")
        self.enabled = QCheckBox("Règle active"); self.enabled.setChecked(True)
        form.addRow("Événement", self.event)
        form.addRow("Son", self.asset)
        form.addRow("Volume", self.volume)
        form.addRow("Décalage", self.offset)
        form.addRow("Entrée source", self.source_in)
        form.addRow("Sortie source", self.source_out)
        form.addRow("Fondu entrant", self.fade_in)
        form.addRow("Fondu sortant", self.fade_out)
        form.addRow("Filtre", self.label_filter)
        form.addRow("Intervalle minimal", self.min_interval)
        form.addRow("", self.enabled)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.rule = None
        if rule is not None:
            self.event.setCurrentIndex(max(0, self.event.findData(rule.event)))
            self.asset.setCurrentIndex(max(0, self.asset.findData(rule.asset_id)))
            self.volume.setValue(rule.volume)
            self.offset.setValue(rule.offset_ms)
            self.source_in.setValue(rule.source_start_ms)
            self.source_out.setValue(rule.source_end_ms or 0)
            self.fade_in.setValue(rule.fade_in_ms)
            self.fade_out.setValue(rule.fade_out_ms)
            self.label_filter.setText(rule.label_filter)
            self.min_interval.setValue(rule.min_interval_ms)
            self.enabled.setChecked(rule.enabled)

    def _accept(self):
        asset_id = self.asset.currentData()
        if asset_id is None:
            QMessageBox.warning(self, "Son requis", "Importez un fichier audio dans la bibliothèque.")
            return
        rule = SfxRule(
            self.event.currentData(), int(asset_id), self.volume.value(), self.offset.value(),
            self.source_in.value(), self.source_out.value() or None, self.fade_in.value(), self.fade_out.value(),
            self.label_filter.text().strip(), self.min_interval.value(), self.enabled.isChecked(),
        )
        duration = next((asset.duration_ms for asset in self.assets if asset.id == rule.asset_id), None)
        try:
            rule.validate(duration)
        except ValueError as exc:
            QMessageBox.warning(self, "Règle invalide", str(exc))
            return
        self.rule = rule
        self.accept()


class BrollRuleDialog(QDialog):
    def __init__(self, broll_assets, rule: BrollRule | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Règle B-roll")
        form = QFormLayout(self)
        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("mot présent dans le texte d'une caption")
        self.target = QComboBox()
        self.target.addItem("Par tag (rotation alphabétique des clips taggés)", None)
        for asset in broll_assets:
            self.target.addItem(f"Clip : {asset.name}", asset.id)
        self.tag = QLineEdit()
        self.tag.setPlaceholderText("tag saisi dans la bibliothèque B-roll")
        self.track = QSpinBox(); self.track.setRange(0, 99)
        self.source_in = QSpinBox(); self.source_in.setRange(0, 86_400_000); self.source_in.setSuffix(" ms")
        self.max_duration = QSpinBox(); self.max_duration.setRange(0, 86_400_000); self.max_duration.setSuffix(" ms")
        self.max_duration.setSpecialValueText("durée de la caption")
        self.enabled = QCheckBox("Règle active"); self.enabled.setChecked(True)
        form.addRow("Mot-clé", self.keyword)
        form.addRow("Cible", self.target)
        form.addRow("Tag", self.tag)
        form.addRow("Piste", self.track)
        form.addRow("Entrée source", self.source_in)
        form.addRow("Durée maximale", self.max_duration)
        form.addRow("", self.enabled)
        self.target.currentIndexChanged.connect(lambda _index: self.tag.setEnabled(self.target.currentData() is None))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.rule = None
        if rule is not None:
            self.keyword.setText(rule.keyword)
            self.target.setCurrentIndex(max(0, self.target.findData(rule.asset_id)))
            self.tag.setText(rule.tag)
            self.track.setValue(rule.track)
            self.source_in.setValue(rule.source_start_ms)
            self.max_duration.setValue(rule.max_duration_ms)
            self.enabled.setChecked(rule.enabled)
        self.tag.setEnabled(self.target.currentData() is None)

    def _accept(self):
        asset_id = self.target.currentData()
        rule = BrollRule(self.keyword.text().strip(), asset_id, "" if asset_id is not None else self.tag.text().strip(),
                         self.track.value(), self.source_in.value(), self.max_duration.value(), self.enabled.isChecked())
        try:
            rule.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Règle invalide", str(exc))
            return
        self.rule = rule
        self.accept()


class RulesPanel(QWidget):
    """Marqueurs de la vidéo sélectionnée et règles du projet. Aucune détection automatique."""

    def __init__(self, get_store, get_media, get_position, seek, log, parent=None):
        super().__init__(parent)
        self._get_store, self._get_media, self._get_position = get_store, get_media, get_position
        self._seek, self._log = seek, log
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Les règles s'appliquent à chaque rendu, uniquement à partir d'éléments explicites : "
            "marqueurs, captions saisies ou importées, placements B-roll et bornes du rush. "
            "Sans correspondance, rien n'est inséré."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(QLabel("Marqueurs manuels de la vidéo sélectionnée"))
        self.markers = _readonly_table(["Temps ms", "Libellé"])
        self.markers.setMaximumHeight(140)
        self.markers.doubleClicked.connect(lambda _index: self._seek_marker())
        layout.addWidget(self.markers)
        marker_actions = QHBoxLayout()
        self.marker_add = QPushButton("Ajouter au curseur…")
        self.marker_add.clicked.connect(self.add_marker)
        self.marker_delete = QPushButton("Supprimer")
        self.marker_delete.clicked.connect(self.delete_markers)
        self.marker_seek = QPushButton("Aller au marqueur")
        self.marker_seek.clicked.connect(self._seek_marker)
        for button in (self.marker_add, self.marker_delete, self.marker_seek):
            marker_actions.addWidget(button)
        layout.addLayout(marker_actions)

        layout.addWidget(QLabel("Règles SFX : événement → effet sonore"))
        self.sfx_rules = _readonly_table(["Active", "Événement", "Son", "Volume", "Décalage", "Filtre", "Intervalle"])
        self.sfx_rules.doubleClicked.connect(lambda _index: self.edit_sfx_rule())
        layout.addWidget(self.sfx_rules, 1)
        sfx_actions = QHBoxLayout()
        self.sfx_add = QPushButton("Ajouter…")
        self.sfx_add.clicked.connect(self.add_sfx_rule)
        self.sfx_edit = QPushButton("Modifier…")
        self.sfx_edit.clicked.connect(self.edit_sfx_rule)
        self.sfx_delete = QPushButton("Supprimer")
        self.sfx_delete.clicked.connect(self.delete_sfx_rules)
        for button in (self.sfx_add, self.sfx_edit, self.sfx_delete):
            sfx_actions.addWidget(button)
        layout.addLayout(sfx_actions)

        layout.addWidget(QLabel("Règles B-roll : mot-clé de caption → clip ou tag"))
        self.broll_rules = _readonly_table(["Active", "Mot-clé", "Cible", "Piste", "Entrée", "Durée max"])
        self.broll_rules.doubleClicked.connect(lambda _index: self.edit_broll_rule())
        layout.addWidget(self.broll_rules, 1)
        broll_actions = QHBoxLayout()
        self.broll_add = QPushButton("Ajouter…")
        self.broll_add.clicked.connect(self.add_broll_rule)
        self.broll_edit = QPushButton("Modifier…")
        self.broll_edit.clicked.connect(self.edit_broll_rule)
        self.broll_delete = QPushButton("Supprimer")
        self.broll_delete.clicked.connect(self.delete_broll_rules)
        for button in (self.broll_add, self.broll_edit, self.broll_delete):
            broll_actions.addWidget(button)
        layout.addLayout(broll_actions)
        self.set_active(False)

    def set_active(self, active: bool):
        for widget in (self.marker_add, self.marker_delete, self.marker_seek, self.sfx_add, self.sfx_edit,
                       self.sfx_delete, self.broll_add, self.broll_edit, self.broll_delete,
                       self.markers, self.sfx_rules, self.broll_rules):
            widget.setEnabled(active and self._get_store() is not None)

    def refresh(self):
        store = self._get_store()
        media = self._get_media()
        markers = store.get_markers(media.id) if store and media else ()
        self.markers.setRowCount(len(markers))
        for row, marker in enumerate(markers):
            self.markers.setItem(row, 0, QTableWidgetItem(str(marker.time_ms)))
            self.markers.setItem(row, 1, QTableWidgetItem(marker.label))
        audio_names = {asset.id: asset.name for asset in store.list_audio_assets()} if store else {}
        sfx_rules = store.get_sfx_rules() if store else ()
        self.sfx_rules.setRowCount(len(sfx_rules))
        for row, rule in enumerate(sfx_rules):
            values = ["Oui" if rule.enabled else "Non", EVENT_KINDS.get(rule.event, rule.event),
                      audio_names.get(rule.asset_id, f"#{rule.asset_id} manquant"), f"{rule.volume:g}",
                      f"{rule.offset_ms} ms", rule.label_filter or "—", f"{rule.min_interval_ms} ms"]
            for column, value in enumerate(values):
                self.sfx_rules.setItem(row, column, QTableWidgetItem(value))
        broll_names = {asset.id: asset.name for asset in store.list_broll_assets()} if store else {}
        broll_rules = store.get_broll_rules() if store else ()
        self.broll_rules.setRowCount(len(broll_rules))
        for row, rule in enumerate(broll_rules):
            target = (f"Clip : {broll_names.get(rule.asset_id, f'#{rule.asset_id} manquant')}"
                      if rule.asset_id is not None else f"Tag : {rule.tag}")
            values = ["Oui" if rule.enabled else "Non", rule.keyword, target, str(rule.track),
                      f"{rule.source_start_ms} ms", f"{rule.max_duration_ms} ms" if rule.max_duration_ms else "caption"]
            for column, value in enumerate(values):
                self.broll_rules.setItem(row, column, QTableWidgetItem(value))
        for table in (self.markers, self.sfx_rules, self.broll_rules):
            table.resizeColumnsToContents()

    # --- Marqueurs ---------------------------------------------------------------------
    def add_marker(self):
        store, media = self._get_store(), self._get_media()
        if not store or not media:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo avant d'ajouter un marqueur.")
            return
        position = max(0, int(self._get_position()))
        label, accepted = QInputDialog.getText(self, "Marqueur", f"Libellé du marqueur à {position} ms")
        if not accepted:
            return
        markers = [marker for marker in store.get_markers(media.id) if marker.time_ms != position]
        markers.append(TimelineMarker(position, label.strip()))
        try:
            store.set_markers(media.id, markers)
        except ValueError as exc:
            QMessageBox.warning(self, "Marqueur invalide", str(exc))
            return
        self._log(f"Marqueur ajouté à {position} ms.")
        self.refresh()

    def delete_markers(self):
        store, media = self._get_store(), self._get_media()
        rows = _selected_rows(self.markers)
        if not store or not media or not rows:
            return
        markers = [marker for index, marker in enumerate(store.get_markers(media.id)) if index not in rows]
        store.set_markers(media.id, markers)
        self.refresh()

    def _seek_marker(self):
        store, media = self._get_store(), self._get_media()
        rows = _selected_rows(self.markers)
        if store and media and rows:
            markers = store.get_markers(media.id)
            if rows[0] < len(markers):
                self._seek(markers[rows[0]].time_ms)

    # --- Règles SFX --------------------------------------------------------------------
    def _save_sfx_rules(self, rules) -> None:
        try:
            self._get_store().set_sfx_rules(rules)
        except ValueError as exc:
            QMessageBox.warning(self, "Règles SFX invalides", str(exc))
            return
        self.refresh()

    def add_sfx_rule(self):
        store = self._get_store()
        if not store:
            return
        assets = store.list_audio_assets()
        if not assets:
            QMessageBox.information(self, "Bibliothèque vide", "Importez d'abord un son dans l'onglet Musique & SFX.")
            return
        dialog = SfxRuleDialog(assets, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.rule:
            self._save_sfx_rules((*store.get_sfx_rules(), dialog.rule))

    def edit_sfx_rule(self):
        store = self._get_store()
        rows = _selected_rows(self.sfx_rules)
        if not store or not rows:
            return
        rules = list(store.get_sfx_rules())
        dialog = SfxRuleDialog(store.list_audio_assets(), rules[rows[0]], parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.rule:
            rules[rows[0]] = dialog.rule
            self._save_sfx_rules(rules)

    def delete_sfx_rules(self):
        store = self._get_store()
        rows = _selected_rows(self.sfx_rules)
        if store and rows:
            self._save_sfx_rules([rule for index, rule in enumerate(store.get_sfx_rules()) if index not in rows])

    # --- Règles B-roll -----------------------------------------------------------------
    def _save_broll_rules(self, rules) -> None:
        try:
            self._get_store().set_broll_rules(rules)
        except ValueError as exc:
            QMessageBox.warning(self, "Règles B-roll invalides", str(exc))
            return
        self.refresh()

    def add_broll_rule(self):
        store = self._get_store()
        if not store:
            return
        dialog = BrollRuleDialog(store.list_broll_assets(), parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.rule:
            self._save_broll_rules((*store.get_broll_rules(), dialog.rule))

    def edit_broll_rule(self):
        store = self._get_store()
        rows = _selected_rows(self.broll_rules)
        if not store or not rows:
            return
        rules = list(store.get_broll_rules())
        dialog = BrollRuleDialog(store.list_broll_assets(), rules[rows[0]], parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.rule:
            rules[rows[0]] = dialog.rule
            self._save_broll_rules(rules)

    def delete_broll_rules(self):
        store = self._get_store()
        rows = _selected_rows(self.broll_rules)
        if store and rows:
            self._save_broll_rules([rule for index, rule in enumerate(store.get_broll_rules()) if index not in rows])
