"""Dialogues de paramètres, d'historique des tâches et panneau de pilotage du pipeline."""

from __future__ import annotations

import shutil
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from cutweave.core.proc import run_hidden
from cutweave.core.pipeline import STAGE_LABELS, stage_statuses
from cutweave.core.settings import AppSettings
from cutweave.storage.project_store import MODULES

MODULE_LABELS = {
    "framing": "Cadrage par keyframes",
    "sharpening": "Netteté (unsharp)",
    "captions": "Incrustation des captions",
    "masks": "Masques",
    "broll": "B-roll (placements et règles)",
    "music": "Musique et ducking",
    "sfx": "SFX (placements et règles)",
}
MODULE_STAGE = {"framing": "visual", "sharpening": "visual", "masks": "composition", "broll": "composition",
                "captions": "finishing", "music": "finishing", "sfx": "finishing"}
STATUS_LABELS = {"succeeded": "terminée", "running": "interrompue", "failed": "échec", "cancelled": "annulée"}


def tool_report(ffmpeg: str | None, ffprobe: str | None) -> str:
    """Versions et capacités locales de FFmpeg/FFprobe (aucun accès réseau)."""
    lines = []
    for label, binary in (("FFmpeg", ffmpeg), ("FFprobe", ffprobe)):
        if not binary:
            lines.append(f"{label} : introuvable (PATH ou chemin configuré).")
            continue
        try:
            result = run_hidden([binary, "-hide_banner", "-version"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=15, check=False)
            first = result.stdout.splitlines()[0] if result.stdout else f"code {result.returncode}"
            lines.append(f"{label} : {first}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            lines.append(f"{label} : impossible de l'exécuter ({exc}).")
    if ffmpeg:
        try:
            filters = run_hidden([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=15, check=False).stdout
            encoders = run_hidden([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=15, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            filters = encoders = ""
        lines.append("Filtre ass (libass, captions) : " + ("disponible" if " ass " in filters else "ABSENT"))
        lines.append("Filtre unsharp : " + ("disponible" if " unsharp " in filters else "ABSENT"))
        from cutweave.autoedit.assemble import missing_filters

        absent = missing_filters(ffmpeg)
        lines.append("Filtres du montage automatique : " + ("tous disponibles" if not absent
                                                           else "ABSENTS : " + ", ".join(absent)))
        h264 = [name for name in ("libx264", "libopenh264", "h264_mf") if f" {name} " in encoders]
        lines.append("Encodeur H.264 : " + (", ".join(h264) if h264 else "aucun (repli MPEG-4)"))
    return "\n".join(lines)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paramètres de CUTWEAVE")
        self.resize(680, 520)
        self.setMinimumWidth(520)
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        heading = QLabel("Paramètres")
        heading.setProperty("role", "h2")
        layout.addWidget(heading)
        intro = QLabel("Chemins des outils et dossiers de travail. Tout reste sur ta machine.")
        intro.setProperty("role", "muted")
        layout.addWidget(intro)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setVerticalSpacing(12)
        self.ffmpeg = self._path_row(form, "FFmpeg", settings.ffmpeg_path, file=True)
        self.ffprobe = self._path_row(form, "FFprobe", settings.ffprobe_path, file=True)
        self.log_dir = self._path_row(form, "Dossier des journaux", settings.log_dir, file=False)
        self.projects_dir = self._path_row(form, "Dossier des projets", settings.projects_dir, file=False)
        layout.addLayout(form)
        hint = QLabel("Laissez vide pour utiliser FFmpeg/FFprobe du PATH et le dossier de journaux par défaut "
                      f"({settings.resolved_log_dir() if not settings.log_dir else settings.log_dir}).")
        hint.setWordWrap(True)
        hint.setProperty("role", "faint")
        layout.addWidget(hint)
        check = QPushButton("Vérifier FFmpeg et FFprobe")
        check.clicked.connect(self._check_tools)
        layout.addWidget(check)
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _path_row(self, form: QFormLayout, label: str, value: str, *, file: bool) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(value)
        browse = QPushButton("Parcourir…")

        def choose():
            if file:
                path, _ = QFileDialog.getOpenFileName(self, label, edit.text(), "Exécutable (*.exe);;Tous (*.*)")
            else:
                path = QFileDialog.getExistingDirectory(self, label, edit.text())
            if path:
                edit.setText(path)

        browse.clicked.connect(choose)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        form.addRow(label, row)
        return edit

    def _candidate(self) -> AppSettings:
        return AppSettings(self.ffmpeg.text().strip(), self.ffprobe.text().strip(),
                           self.log_dir.text().strip(), self.projects_dir.text().strip())

    def _check_tools(self):
        candidate = self._candidate()
        ffmpeg = candidate.ffmpeg_path or shutil.which("ffmpeg")
        ffprobe = candidate.ffprobe_path or shutil.which("ffprobe")
        self.report.setPlainText(tool_report(ffmpeg, ffprobe))

    def _accept(self):
        candidate = self._candidate()
        try:
            candidate.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Paramètres invalides", str(exc))
            return
        self.settings = candidate
        self.accept()


TASK_STATUS_LABELS = {"succeeded": "Terminée", "failed": "Échec", "cancelled": "Annulée", "running": "En cours"}
KIND_LABELS = {"pipeline_export": "Export complet", "pipeline_step_visual": "Étape 1 — Visuel",
               "pipeline_step_composition": "Étape 2 — Composition", "pipeline_step_finishing": "Étape 3 — Finition"}


class TaskHistoryDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Historique des tâches du projet")
        self.resize(960, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        heading = QLabel("Historique des tâches")
        heading.setProperty("role", "h2")
        layout.addWidget(heading)
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["#", "Type", "Statut", "Début", "Sortie", "Erreur"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        tasks = store.list_tasks()
        table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = [str(task["id"]), KIND_LABELS.get(task["kind"], task["kind"]),
                      TASK_STATUS_LABELS.get(task["status"], task["status"]),
                      (task["started_at"] or "")[:19].replace("T", " "),
                      task["output_path"] or "", (task["error"] or "").splitlines()[0] if task["error"] else ""]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 5 and task["error"]:
                    item.setToolTip(task["error"])
                table.setItem(row, column, item)
        table.resizeColumnsToContents()
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class PipelinePanel(QWidget):
    """Activation des modules, statut par étape et lancement du pipeline."""

    def __init__(self, get_store, get_media, run_stage, log, parent=None):
        super().__init__(parent)
        self._get_store, self._get_media, self._run_stage, self._log = get_store, get_media, run_stage, log
        layout = QVBoxLayout(self)
        modules_box = QGroupBox("Modules actifs (projet)")
        grid = QGridLayout(modules_box)
        self.module_checks: dict[str, QCheckBox] = {}
        for index, module in enumerate(MODULES):
            check = QCheckBox(MODULE_LABELS[module])
            check.setToolTip(f"Affecte l'étape « {STAGE_LABELS[MODULE_STAGE[module]]} » et les suivantes.")
            check.toggled.connect(lambda enabled, name=module: self._module_toggled(name, enabled))
            grid.addWidget(check, index // 2, index % 2)
            self.module_checks[module] = check
        layout.addWidget(modules_box)

        status_box = QGroupBox("Statut des étapes pour la vidéo sélectionnée")
        status_layout = QVBoxLayout(status_box)
        self.status_table = QTableWidget(len(STAGE_LABELS), 3)
        self.status_table.setHorizontalHeaderLabels(["Étape", "Statut", "Détail"])
        self.status_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.status_table.horizontalHeader().setStretchLastSection(True)
        self.status_table.verticalHeader().setVisible(False)
        status_layout.addWidget(self.status_table)
        layout.addWidget(status_box)

        actions = QGridLayout()
        self.full_button = QPushButton("Pipeline complet et export…")
        self.full_button.clicked.connect(lambda: self._run_stage(None))
        actions.addWidget(self.full_button, 0, 0, 1, 3)
        self.stage_buttons = {}
        for column, stage in enumerate(("visual", "composition", "finishing")):
            button = QPushButton(f"{STAGE_LABELS[stage]}")
            button.setToolTip("Exécute cette étape (et ses dépendances modifiées) avec un MP4 intermédiaire.")
            button.clicked.connect(lambda _checked=False, name=stage: self._run_stage(name))
            actions.addWidget(button, 1, column)
            self.stage_buttons[stage] = button
        self.force_check = QCheckBox("Forcer le recalcul des étapes")
        self.force_check.setToolTip("Le cache de rendu reste utilisé ; Projet > Purger le cache pour réencoder.")
        actions.addWidget(self.force_check, 2, 0, 1, 3)
        layout.addLayout(actions)
        note = QLabel("Le pipeline complet compile les instructions des trois étapes puis encode en une seule passe. "
                      "Seules les étapes dont les entrées ont changé sont recalculées.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self._refreshing = False

    def set_busy(self, busy: bool):
        has_media = self._get_store() is not None and self._get_media() is not None
        for button in (self.full_button, *self.stage_buttons.values()):
            button.setEnabled(has_media and not busy)
        for check in self.module_checks.values():
            check.setEnabled(self._get_store() is not None and not busy)

    def _module_toggled(self, module: str, enabled: bool):
        store = self._get_store()
        if self._refreshing or store is None:
            return
        try:
            store.set_module_enabled(module, enabled)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Réglage impossible", str(exc))
            return
        self._log(f"Module « {MODULE_LABELS[module]} » {'activé' if enabled else 'désactivé'}.")

    def refresh(self):
        store, media = self._get_store(), self._get_media()
        self._refreshing = True
        try:
            for module, check in self.module_checks.items():
                check.setChecked(store.module_enabled(module) if store else True)
        finally:
            self._refreshing = False
        statuses = stage_statuses(store, media.id) if store and media else {}
        for row, (stage, label) in enumerate(STAGE_LABELS.items()):
            entry = statuses.get(stage, {})
            status = STATUS_LABELS.get(entry.get("status"), "jamais exécutée")
            detail = entry.get("error") or entry.get("finished_at", "") or ""
            for column, value in enumerate((label, status, str(detail)[:160])):
                self.status_table.setItem(row, column, QTableWidgetItem(value))
        self.status_table.resizeColumnsToContents()
