"""Interface de bureau pour gérer un projet et lancer les premières opérations."""

from __future__ import annotations

import logging
import json
import threading
import uuid
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, QUrl, QRect, QPoint, QSize, QTimer
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QMainWindow, QDialog,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QDoubleSpinBox, QInputDialog,
    QSlider, QSplitter, QStatusBar, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QRubberBand,
    QVBoxLayout, QWidget, QListWidget, QListWidgetItem, QLineEdit, QCheckBox, QApplication, QFrame,
    QStackedWidget,
)

from cutweave.media.files import collect_video_files
from cutweave.media.probe import MediaProbeError, probe_audio, probe_media
from cutweave.media.thumbnails import ThumbnailError, create_video_thumbnail
from cutweave.media.render import (
    VisualSettings, extract_preview_frame, ffmpeg_path,
)
from cutweave.core.cache import StageCache
from cutweave.core.pipeline import STAGE_LABELS, PipelineInputError, ProgressEvent, collect_job, run_job
from cutweave.core.settings import AppSettings, configure_logging
from cutweave.media.probe import ffprobe_path
from cutweave.stages.composition.captions import (
    CaptionCue, CaptionParseError, load_caption_file, merge_cues, parse_timestamp, split_cue,
)
from cutweave.stages.composition.broll import BrollPlacement, BrollValidationError, parse_tags
from cutweave.stages.finishing.music import (
    MusicSettings, MusicValidationError, SfxPlacement,
    SfxValidationError, format_ducking_segments, parse_audio_tags, parse_ducking_segments,
)
from cutweave.stages.finishing.caption_presets import (
    BUILTIN_CAPTION_PRESETS, BUILTIN_BY_NAME, CaptionPreset, CaptionPresetError,
)
from cutweave.autoedit.service import default_project_dir
from cutweave.ui.caption_preset_dialog import CaptionPresetDialog
from cutweave.ui.library_page import LibraryPage
from cutweave.ui.studio import StudioPage
from cutweave.ui.theme import apply_theme
from cutweave.ui.dialogs import PipelinePanel, SettingsDialog, TaskHistoryDialog
from cutweave.ui.rules_panel import RulesPanel
from cutweave.stages.composition.masking import MaskKeyframe, MaskRegion, MaskValidationError
from cutweave.stages.visual.tracking import (
    TrackingError, available_trackers, extract_frame_at, load_trajectory, save_trajectory,
    draw_trajectory, track_region, tracking_summary, trajectory_to_framing_keyframes, trajectory_to_mask_keyframes,
)
from cutweave.stages.visual.framing import FramingKeyframe, FramingValidationError
from cutweave.storage.project_store import ProjectMedia, ProjectStore

LOGGER = logging.getLogger("cutweave")


class RoiLabel(QLabel):
    """Label d'image qui renvoie une sélection manuelle en pixels source."""

    def __init__(self, source_size, parent=None):
        super().__init__(parent)
        self.source_width, self.source_height = source_size
        self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = QPoint()
        self.selection = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.rubber_band.setGeometry(QRect(self.origin, self.origin))
            self.rubber_band.show()

    def mouseMoveEvent(self, event):
        if self.rubber_band.isVisible():
            self.rubber_band.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self.rubber_band.geometry().normalized().intersected(self.rect())
        self.rubber_band.hide()
        if rect.width() < 4 or rect.height() < 4:
            self.selection = None
            return
        sx = self.source_width / self.width()
        sy = self.source_height / self.height()
        x, y = round(rect.x() * sx), round(rect.y() * sy)
        right, bottom = round((rect.x() + rect.width()) * sx), round((rect.y() + rect.height()) * sy)
        self.selection = (x, y, max(1, right - x), max(1, bottom - y))


class RoiDialog(QDialog):
    def __init__(self, frame, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sélectionner la cible du suivi")
        height, width = frame.shape[:2]
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_BGR888).copy()
        pixmap = QPixmap.fromImage(image)
        pixmap = pixmap.scaled(900, 600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.image_label = RoiLabel((width, height), self)
        self.image_label.setPixmap(pixmap)
        self.image_label.setFixedSize(pixmap.size())
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Tracez un rectangle autour de la cible à suivre."))
        layout.addWidget(self.image_label, 0, Qt.AlignmentFlag.AlignCenter)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Démarrer le suivi")
        self.start_button.clicked.connect(self.accept)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.start_button)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    @property
    def bbox(self):
        return self.image_label.selection


class LogBridge(QObject):
    """Relaye les journaux Python (tous threads) vers la vue des journaux, via un signal Qt."""

    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: LogBridge):
        super().__init__(logging.WARNING)
        self.bridge = bridge
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s : %(message)s"))

    def emit(self, record):
        try:
            self.bridge.message.emit(self.format(record).splitlines()[0])
        except RuntimeError:
            pass


class TaskWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(self, operation, cancel_event):
        super().__init__()
        self.operation = operation
        self.cancel_event = cancel_event

    def run(self):
        try:
            self.finished.emit(self.operation(self.progress.emit, self.cancel_event))
        except Exception as exc:  # message utilisateur et journal détaillé
            if self.cancel_event is not None and self.cancel_event.is_set():
                LOGGER.info("Tâche annulée : %s", exc)
            else:
                LOGGER.exception("Échec de tâche")
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.store: ProjectStore | None = None
        self.media: list[ProjectMedia] = []
        self.thread: QThread | None = None
        self.worker: TaskWorker | None = None
        self.cancel_event: threading.Event | None = None
        self._success_handler = None
        self._pending_frame = None
        self._visual_refreshing = False
        self.app_settings = AppSettings.load()
        self.log_bridge = LogBridge(self)
        self.log_bridge.message.connect(self._append_log)
        self._log_handler = QtLogHandler(self.log_bridge)
        logging.getLogger("cutweave").addHandler(self._log_handler)
        self.setWindowTitle("CUTWEAVE — Montage vidéo automatique")
        self.resize(1480, 940)
        self.setMinimumSize(1100, 720)
        if QApplication.instance() is not None:
            apply_theme(QApplication.instance())
        self._build_ui()
        self._build_menu()
        self._set_project_state(False)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("appRoot")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.sidebar_frame = QFrame()
        self.sidebar_frame.setObjectName("sidebar")
        self.sidebar_frame.setFixedWidth(232)
        outer.addWidget(self.sidebar_frame)
        main_column = QVBoxLayout()
        main_column.setContentsMargins(0, 0, 0, 0)
        main_column.setSpacing(0)
        outer.addLayout(main_column, 1)
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        main_column.addWidget(self.pages, 1)
        self.studio = StudioPage(self)
        self.pages.addWidget(self.studio)
        content = QWidget()
        content.setObjectName("page")
        self.pages.addWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 14, 20, 10)
        header = QHBoxLayout()
        self.project_label = QLabel("Aucun projet ouvert")
        self.project_label.setProperty("role", "h2")
        header.addWidget(self.project_label, 1)
        self.import_button = QPushButton("Importer des médias…")
        self.import_button.clicked.connect(self.import_media)
        header.addWidget(self.import_button)
        self.import_folder_button = QPushButton("Importer un dossier…")
        self.import_folder_button.clicked.connect(self.import_folder)
        header.addWidget(self.import_folder_button)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Médias du projet"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Nom", "Durée", "Résolution", "Images/s", "Audio"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        left_layout.addWidget(self.table, 1)
        caption_header = QHBoxLayout()
        caption_header.addWidget(QLabel("Captions"), 1)
        self.caption_import_button = QPushButton("Importer…")
        self.caption_import_button.clicked.connect(self.import_captions)
        caption_header.addWidget(self.caption_import_button)
        self.caption_add_button = QPushButton("Ajouter au curseur")
        self.caption_add_button.clicked.connect(self.add_caption)
        caption_header.addWidget(self.caption_add_button)
        self.caption_seek_button = QPushButton("Aller au segment")
        self.caption_seek_button.clicked.connect(self.seek_to_caption)
        caption_header.addWidget(self.caption_seek_button)
        caption_actions = QHBoxLayout()
        self.caption_split_button = QPushButton("Scinder")
        self.caption_split_button.clicked.connect(self.split_caption)
        caption_actions.addWidget(self.caption_split_button)
        self.caption_merge_button = QPushButton("Fusionner")
        self.caption_merge_button.clicked.connect(self.merge_caption_rows)
        caption_actions.addWidget(self.caption_merge_button)
        self.caption_delete_button = QPushButton("Supprimer")
        self.caption_delete_button.clicked.connect(self.delete_caption)
        caption_actions.addWidget(self.caption_delete_button)
        left_layout.addLayout(caption_header)
        left_layout.addLayout(caption_actions)
        self.caption_table = QTableWidget(0, 3)
        self.caption_table.setHorizontalHeaderLabels(["Début", "Fin", "Texte"])
        self.caption_table.horizontalHeader().setStretchLastSection(True)
        self.caption_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.caption_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.caption_table.cellChanged.connect(self._caption_cell_changed)
        self.caption_table.itemSelectionChanged.connect(self._caption_selection_changed)
        left_layout.addWidget(self.caption_table, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Aperçu"))
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(400, 260)
        self.video_widget.setStyleSheet("background: #050608;")
        right_layout.addWidget(self.video_widget, 1)
        player_row = QHBoxLayout()
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.play_button = QPushButton("Lecture")
        self.play_button.clicked.connect(self._toggle_play)
        player_row.addWidget(self.play_button)
        self.player_status = QLabel("Sélectionnez une vidéo")
        player_row.addWidget(self.player_status, 1)
        right_layout.addLayout(player_row)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.seek_slider.setValue)
        self.player.durationChanged.connect(lambda duration: self.seek_slider.setRange(0, duration))
        self.player.errorOccurred.connect(self._playback_error)
        right_layout.addWidget(self.seek_slider)

        self.editor_tabs = editor_tabs = QTabWidget()
        visual_page = QWidget()
        visual_layout = QVBoxLayout(visual_page)
        visual_layout.addWidget(QLabel("Étape 1 — Cadrage et netteté"))
        form = QFormLayout()
        self.aspect = QComboBox()
        self.aspect.addItems(["9:16", "16:9", "1:1"])
        self.aspect.currentTextChanged.connect(self._aspect_changed)
        form.addRow("Format", self.aspect)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(16, 7680)
        self.width_spin.setValue(1080)
        form.addRow("Largeur", self.width_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(16, 7680)
        self.height_spin.setValue(1920)
        form.addRow("Hauteur", self.height_spin)
        self.sharpness = QDoubleSpinBox()
        self.sharpness.setRange(0, 2)
        self.sharpness.setSingleStep(0.05)
        self.sharpness.setValue(0.35)
        form.addRow("Netteté", self.sharpness)
        self.sharpening_enabled = QCheckBox("Activer le filtre de netteté")
        self.sharpening_enabled.setChecked(True)
        form.addRow("Filtre", self.sharpening_enabled)
        self.sharpening_radius = QSpinBox()
        self.sharpening_radius.setRange(3, 23)
        self.sharpening_radius.setSingleStep(2)
        self.sharpening_radius.setValue(5)
        form.addRow("Rayon (taille du noyau)", self.sharpening_radius)
        self.sharpening_threshold = QSpinBox()
        self.sharpening_threshold.setRange(0, 255)
        self.sharpening_threshold.setValue(0)
        form.addRow("Seuil", self.sharpening_threshold)
        self.aspect.currentTextChanged.connect(self._visual_settings_changed)
        for control in (self.width_spin, self.height_spin, self.sharpness,
                        self.sharpening_radius, self.sharpening_threshold):
            control.valueChanged.connect(self._visual_settings_changed)
        self.sharpening_enabled.stateChanged.connect(self._visual_settings_changed)
        visual_layout.addLayout(form)
        sharpening_actions = QHBoxLayout()
        self.sharpening_reset_button = QPushButton("Réinitialiser la netteté")
        self.sharpening_reset_button.clicked.connect(self.reset_sharpening)
        sharpening_actions.addWidget(self.sharpening_reset_button)
        self.sharpening_compare_button = QPushButton("Comparer avant / après au curseur")
        self.sharpening_compare_button.clicked.connect(self.compare_sharpening)
        sharpening_actions.addWidget(self.sharpening_compare_button)
        visual_layout.addLayout(sharpening_actions)
        visual_layout.addWidget(QLabel("Keyframes de cadrage lissées : centre X/Y normalisé et zoom (1–4)."))
        self.framing_table = QTableWidget(0, 4)
        self.framing_table.setHorizontalHeaderLabels(["Temps ms", "Centre X", "Centre Y", "Zoom"])
        self.framing_table.setMaximumHeight(150)
        self.framing_table.cellChanged.connect(self._framing_cell_changed)
        visual_layout.addWidget(self.framing_table)
        framing_actions = QHBoxLayout()
        self.framing_add_button = QPushButton("Ajouter au curseur…")
        self.framing_add_button.clicked.connect(self.add_framing_keyframe)
        framing_actions.addWidget(self.framing_add_button)
        self.framing_delete_button = QPushButton("Supprimer")
        self.framing_delete_button.clicked.connect(self.delete_framing_keyframes)
        framing_actions.addWidget(self.framing_delete_button)
        self.framing_import_track_button = QPushButton("Importer une trajectoire de tracking…")
        self.framing_import_track_button.clicked.connect(self.import_tracking_as_framing)
        framing_actions.addWidget(self.framing_import_track_button)
        visual_layout.addLayout(framing_actions)
        smoothing_row = QHBoxLayout()
        smoothing_row.addWidget(QLabel("Lissage des trajectoires (échantillons)"))
        self.track_smoothing = QSpinBox()
        self.track_smoothing.setRange(1, 301)
        self.track_smoothing.setSingleStep(2)
        self.track_smoothing.setValue(9)
        self.track_smoothing.setToolTip("Moyenne glissante appliquée aux boîtes suivies avant conversion en keyframes "
                                        "de cadrage ou de masque. 1 = aucun lissage.")
        smoothing_row.addWidget(self.track_smoothing)
        self.track_margin = QDoubleSpinBox()
        self.track_margin.setRange(0, 1)
        self.track_margin.setSingleStep(0.05)
        self.track_margin.setValue(0.15)
        self.track_margin.setToolTip("Marge de sécurité autour de la cible, en fraction de sa taille.")
        smoothing_row.addWidget(QLabel("Marge de sécurité"))
        smoothing_row.addWidget(self.track_margin)
        smoothing_row.addStretch(1)
        visual_layout.addLayout(smoothing_row)
        self.export_button = QPushButton("Lancer / reprendre le pipeline complet…")
        self.export_button.clicked.connect(self.export_selected)
        self.export_button.setToolTip("Exécute les étapes visuelle, composition et finition, puis réalise le rendu final.")
        visual_layout.addWidget(self.export_button)
        self.visual_stage_button = QPushButton("Exécuter l’étape visuelle")
        self.visual_stage_button.clicked.connect(lambda: self.export_selected(target_stage="visual"))
        visual_layout.addWidget(self.visual_stage_button)
        tracking_row = QHBoxLayout()
        self.tracker_combo = QComboBox()
        self.tracker_combo.addItems(available_trackers())
        tracking_row.addWidget(self.tracker_combo, 1)
        self.track_button = QPushButton("Sélectionner une cible et suivre")
        self.track_button.clicked.connect(self.begin_tracking)
        tracking_row.addWidget(self.track_button)
        self.track_view_button = QPushButton("Voir une trajectoire…")
        self.track_view_button.clicked.connect(self.view_trajectory)
        tracking_row.addWidget(self.track_view_button)
        visual_layout.addLayout(tracking_row)
        visual_layout.addWidget(QLabel("Suivi classique d'une zone choisie manuellement, sans détection automatique."))
        visual_layout.addStretch(1)
        editor_tabs.addTab(visual_page, "Visuel")

        preset_page = QWidget()
        preset_layout = QVBoxLayout(preset_page)
        preset_layout.addWidget(QLabel("Style ASS appliqué aux captions de la vidéo sélectionnée"))
        self.caption_preset_combo = QComboBox()
        self.caption_preset_combo.currentTextChanged.connect(self._caption_preset_changed)
        preset_layout.addWidget(self.caption_preset_combo)
        self.caption_preset_status = QLabel("Les animations suivent des timestamps fixes. Le mode cue colore toute la caption, sans synchronisation mot à mot.")
        self.caption_preset_status.setWordWrap(True)
        preset_layout.addWidget(self.caption_preset_status)
        preset_actions = QHBoxLayout()
        self.caption_preset_edit_button = QPushButton("Modifier")
        self.caption_preset_edit_button.clicked.connect(self.edit_caption_preset)
        preset_actions.addWidget(self.caption_preset_edit_button)
        self.caption_preset_duplicate_button = QPushButton("Dupliquer")
        self.caption_preset_duplicate_button.clicked.connect(self.duplicate_caption_preset)
        preset_actions.addWidget(self.caption_preset_duplicate_button)
        self.caption_preset_import_button = QPushButton("Importer JSON…")
        self.caption_preset_import_button.clicked.connect(self.import_caption_preset)
        preset_actions.addWidget(self.caption_preset_import_button)
        self.caption_preset_export_button = QPushButton("Exporter JSON…")
        self.caption_preset_export_button.clicked.connect(self.export_caption_preset)
        preset_actions.addWidget(self.caption_preset_export_button)
        preset_layout.addLayout(preset_actions)
        self.caption_preset_delete_button = QPushButton("Supprimer le preset personnalisé")
        self.caption_preset_delete_button.clicked.connect(self.delete_caption_preset)
        preset_layout.addWidget(self.caption_preset_delete_button)
        self.finishing_stage_button = QPushButton("Exécuter l’étape de finition")
        self.finishing_stage_button.clicked.connect(lambda: self.export_selected(target_stage="finishing"))
        preset_layout.addWidget(self.finishing_stage_button)
        preset_layout.addStretch(1)
        editor_tabs.addTab(preset_page, "Presets captions")

        mask_page = QWidget()
        mask_layout = QVBoxLayout(mask_page)
        mask_layout.addWidget(QLabel("Masques géométriques dans les coordonnées du rendu"))
        self.mask_table = QTableWidget(0, 12)
        self.mask_table.setHorizontalHeaderLabels(["X", "Y", "Largeur", "Hauteur", "Opacité", "Début", "Fin", "Forme", "Sommets X:Y", "Keyframes ms:x:y:l:h:opacité", "Effet", "Rayon flou"])
        self.mask_table.setToolTip("Keyframes séparées par ; au format ms:x:y:largeur:hauteur:opacité. Effet opaque ou blur ; rayon de flou en pixels.")
        self.mask_table.horizontalHeader().setStretchLastSection(True)
        self.mask_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.mask_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.mask_table.cellChanged.connect(self._mask_cell_changed)
        self.mask_table.itemSelectionChanged.connect(self._mask_selection_changed)
        mask_layout.addWidget(self.mask_table, 1)
        mask_actions = QHBoxLayout()
        self.mask_add_button = QPushButton("Ajouter un masque…")
        self.mask_add_button.clicked.connect(self.add_mask)
        mask_actions.addWidget(self.mask_add_button)
        self.mask_delete_button = QPushButton("Supprimer")
        self.mask_delete_button.clicked.connect(self.delete_masks)
        mask_actions.addWidget(self.mask_delete_button)
        self.mask_from_track_button = QPushButton("Masque suivant une trajectoire…")
        self.mask_from_track_button.setToolTip("Crée un masque animé dont les keyframes suivent une trajectoire "
                                               "de tracking classique, projetée dans le cadre de sortie.")
        self.mask_from_track_button.clicked.connect(self.add_mask_from_trajectory)
        mask_actions.addWidget(self.mask_from_track_button)
        mask_layout.addLayout(mask_actions)
        self.composition_stage_button = QPushButton("Exécuter l’étape de composition")
        self.composition_stage_button.clicked.connect(lambda: self.export_selected(target_stage="composition"))
        mask_layout.addWidget(self.composition_stage_button)
        editor_tabs.addTab(mask_page, "Masques")

        broll_page = QWidget()
        broll_layout = QVBoxLayout(broll_page)
        broll_layout.addWidget(QLabel("Bibliothèque locale : clips vidéo, catégories et tags manuels"))
        self.broll_search = QLineEdit()
        self.broll_search.setPlaceholderText("Rechercher par nom, catégorie ou tag")
        self.broll_search.textChanged.connect(self._populate_broll_assets)
        broll_layout.addWidget(self.broll_search)
        self.broll_assets = QListWidget()
        self.broll_assets.setIconSize(QSize(160, 90))
        self.broll_assets.itemDoubleClicked.connect(self.preview_broll_asset)
        self.broll_assets.itemSelectionChanged.connect(self._broll_selection_changed)
        broll_layout.addWidget(self.broll_assets, 1)
        broll_actions = QHBoxLayout()
        self.broll_import_button = QPushButton("Importer des clips…")
        self.broll_import_button.clicked.connect(self.import_broll_assets)
        broll_actions.addWidget(self.broll_import_button)
        self.broll_preview_button = QPushButton("Prévisualiser")
        self.broll_preview_button.clicked.connect(self.preview_broll_asset)
        broll_actions.addWidget(self.broll_preview_button)
        self.broll_place_button = QPushButton("Insérer au curseur…")
        self.broll_place_button.clicked.connect(self.add_broll_placement)
        broll_actions.addWidget(self.broll_place_button)
        broll_layout.addLayout(broll_actions)
        self.broll_placements = QTableWidget(0, 5)
        self.broll_placements.setHorizontalHeaderLabels(["Clip", "Début timeline", "Entrée source", "Sortie source", "Piste"])
        self.broll_placements.horizontalHeader().setStretchLastSection(True)
        self.broll_placements.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.broll_placements.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.broll_placements.itemSelectionChanged.connect(self._broll_selection_changed)
        self.broll_placements.cellChanged.connect(self._broll_placement_cell_changed)
        broll_layout.addWidget(self.broll_placements, 1)
        self.broll_delete_button = QPushButton("Supprimer les insertions sélectionnées")
        self.broll_delete_button.clicked.connect(self.delete_broll_placements)
        broll_layout.addWidget(self.broll_delete_button)
        editor_tabs.addTab(broll_page, "B-roll")

        audio_page = QWidget()
        audio_layout = QVBoxLayout(audio_page)
        audio_layout.addWidget(QLabel("Bibliothèque audio locale et piste musicale de la vidéo sélectionnée"))
        self.audio_search = QLineEdit()
        self.audio_search.setPlaceholderText("Rechercher par nom, catégorie ou tag")
        self.audio_search.textChanged.connect(self._populate_audio_assets)
        audio_layout.addWidget(self.audio_search)
        self.audio_assets = QListWidget()
        self.audio_assets.itemSelectionChanged.connect(self._audio_selection_changed)
        self.audio_assets.itemDoubleClicked.connect(self.preview_audio_asset)
        audio_layout.addWidget(self.audio_assets, 1)
        audio_actions = QHBoxLayout()
        self.audio_import_button = QPushButton("Importer une piste audio…")
        self.audio_import_button.clicked.connect(self.import_audio_assets)
        audio_actions.addWidget(self.audio_import_button)
        self.audio_preview_button = QPushButton("Prévisualiser")
        self.audio_preview_button.clicked.connect(self.preview_audio_asset)
        audio_actions.addWidget(self.audio_preview_button)
        audio_layout.addLayout(audio_actions)
        music_form = QFormLayout()
        self.music_start = QSpinBox(); self.music_start.setRange(0, 86_400_000); self.music_start.setSuffix(" ms")
        self.music_source_in = QSpinBox(); self.music_source_in.setRange(0, 86_400_000); self.music_source_in.setSuffix(" ms")
        self.music_source_out = QSpinBox(); self.music_source_out.setRange(1, 86_400_000); self.music_source_out.setSuffix(" ms")
        self.music_volume = QDoubleSpinBox(); self.music_volume.setRange(0, 4); self.music_volume.setSingleStep(0.05); self.music_volume.setValue(0.25)
        self.music_fade_in = QSpinBox(); self.music_fade_in.setRange(0, 86_400_000); self.music_fade_in.setSuffix(" ms")
        self.music_fade_out = QSpinBox(); self.music_fade_out.setRange(0, 86_400_000); self.music_fade_out.setSuffix(" ms")
        self.music_loop = QCheckBox("Répéter la plage source jusqu'à la fin de la vidéo")
        self.keep_source_audio = QCheckBox("Conserver et mixer l'audio du rush")
        self.music_ducking = QLineEdit()
        self.music_ducking.setPlaceholderText("ex. 1200,3400,0.2,120; 8000,9500,0.15,80")
        self.music_ducking.setToolTip("Intervalles manuels en ms : début,fin,niveau,fondu ; séparés par des points-virgules.")
        self.keep_source_audio.setChecked(True)
        music_form.addRow("Départ timeline", self.music_start)
        music_form.addRow("Entrée source", self.music_source_in)
        music_form.addRow("Sortie source", self.music_source_out)
        music_form.addRow("Volume", self.music_volume)
        music_form.addRow("Fondu entrant", self.music_fade_in)
        music_form.addRow("Fondu sortant", self.music_fade_out)
        music_form.addRow("Bouclage", self.music_loop)
        music_form.addRow("Audio original", self.keep_source_audio)
        music_form.addRow("Ducking manuel", self.music_ducking)
        caption_duck_row = QHBoxLayout()
        self.music_duck_captions = QCheckBox("Atténuer pendant les captions")
        self.music_duck_captions.setToolTip("Utilise les timestamps des captions (saisies ou importées) comme "
                                            "segments de voix. Aucune détection automatique de la parole.")
        caption_duck_row.addWidget(self.music_duck_captions)
        self.music_duck_level = QDoubleSpinBox(); self.music_duck_level.setRange(0, 1); self.music_duck_level.setSingleStep(0.05)
        self.music_duck_level.setValue(0.3)
        caption_duck_row.addWidget(QLabel("niveau"))
        caption_duck_row.addWidget(self.music_duck_level)
        self.music_duck_fade = QSpinBox(); self.music_duck_fade.setRange(0, 5000); self.music_duck_fade.setSuffix(" ms")
        self.music_duck_fade.setValue(120)
        caption_duck_row.addWidget(QLabel("fondu"))
        caption_duck_row.addWidget(self.music_duck_fade)
        music_form.addRow("Ducking captions", caption_duck_row)
        audio_layout.addLayout(music_form)
        music_actions = QHBoxLayout()
        self.music_apply_button = QPushButton("Affecter à la vidéo sélectionnée")
        self.music_apply_button.clicked.connect(self.save_music_mix)
        music_actions.addWidget(self.music_apply_button)
        self.music_remove_button = QPushButton("Retirer la musique")
        self.music_remove_button.clicked.connect(self.remove_music_mix)
        music_actions.addWidget(self.music_remove_button)
        audio_layout.addLayout(music_actions)
        self.music_status = QLabel("Aucune musique affectée")
        audio_layout.addWidget(self.music_status)
        audio_layout.addWidget(QLabel("Effets ponctuels — choisissez un fichier audio et insérez-le au curseur ou sur un marqueur manuel."))
        sfx_actions = QHBoxLayout()
        self.sfx_add_button = QPushButton("Ajouter un SFX au curseur…")
        self.sfx_add_button.clicked.connect(self.add_sfx_placement)
        sfx_actions.addWidget(self.sfx_add_button)
        self.sfx_delete_button = QPushButton("Supprimer les SFX sélectionnés")
        self.sfx_delete_button.clicked.connect(self.delete_sfx_placements)
        sfx_actions.addWidget(self.sfx_delete_button)
        audio_layout.addLayout(sfx_actions)
        self.sfx_table = QTableWidget(0, 8)
        self.sfx_table.setHorizontalHeaderLabels(["Effet", "Timestamp", "Entrée", "Sortie", "Volume", "Fondu in", "Fondu out", "Événement / marqueur"])
        self.sfx_table.horizontalHeader().setStretchLastSection(True)
        self.sfx_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.sfx_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.sfx_table.itemSelectionChanged.connect(self._sfx_selection_changed)
        self.sfx_table.cellChanged.connect(self._sfx_cell_changed)
        audio_layout.addWidget(self.sfx_table, 1)
        editor_tabs.addTab(audio_page, "Musique && SFX")
        self.rules_panel = RulesPanel(
            lambda: self.store, self._selected_media, self.player.position,
            self._seek_to, self._append_log,
        )
        editor_tabs.addTab(self.rules_panel, "Règles && marqueurs")
        self.pipeline_panel = PipelinePanel(
            lambda: self.store, self._selected_media, self._run_pipeline_from_panel, self._append_log,
        )
        editor_tabs.addTab(self.pipeline_panel, "Pipeline")
        right_layout.addWidget(editor_tabs, 1)
        splitter.addWidget(right)
        splitter.setSizes([560, 620])
        layout.addWidget(splitter, 1)
        self.library_page = LibraryPage(self)
        self.pages.addWidget(self.library_page)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(140)
        self.log_view.setPlaceholderText("Les opérations et les erreurs apparaîtront ici.")
        self.log_view.setVisible(False)
        main_column.addWidget(self.log_view)
        bottom = QFrame()
        bottom.setObjectName("bottomBar")
        progress_row = QHBoxLayout(bottom)
        progress_row.setContentsMargins(16, 7, 12, 7)
        self.stage_label = QLabel("Prêt")
        self.stage_label.setProperty("role", "muted")
        self.stage_label.setMinimumWidth(300)
        progress_row.addWidget(self.stage_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        progress_row.addWidget(self.progress, 1)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.clicked.connect(self.cancel_task)
        progress_row.addWidget(self.cancel_button)
        self.log_toggle = QPushButton("Journal")
        self.log_toggle.setProperty("variant", "ghost")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.log_view.setVisible)
        progress_row.addWidget(self.log_toggle)
        main_column.addWidget(bottom)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self._build_sidebar()
        self._set_task_state(False)

    def _build_sidebar(self):
        tabs = {self.editor_tabs.tabText(index).replace("&&", "&"): index for index in range(self.editor_tabs.count())}
        layout = QVBoxLayout(self.sidebar_frame)
        layout.setContentsMargins(0, 18, 0, 14)
        layout.setSpacing(6)
        brand = QLabel("CUTWEAVE")
        brand.setStyleSheet("font-size: 15pt; font-weight: 800; padding-left: 22px; color: #ffffff;")
        layout.addWidget(brand)
        tagline = QLabel("Montage automatique local")
        tagline.setProperty("role", "faint")
        tagline.setStyleSheet("padding-left: 22px; padding-bottom: 10px;")
        layout.addWidget(tagline)

        def editor_tab(name):
            return lambda: (self.pages.setCurrentIndex(1), self.editor_tabs.setCurrentIndex(tabs[name]))

        self._sidebar_actions = [
            ("✦   Studio", lambda: self.pages.setCurrentIndex(0)),
            ("✎   Éditeur", lambda: self.pages.setCurrentIndex(1)),
            ("      Visuel", editor_tab("Visuel")),
            ("      Captions", editor_tab("Presets captions")),
            ("      Masques", editor_tab("Masques")),
            ("      B-roll", editor_tab("B-roll")),
            ("      Musique & SFX", editor_tab("Musique & SFX")),
            ("      Règles & marqueurs", editor_tab("Règles & marqueurs")),
            ("      Pipeline", editor_tab("Pipeline")),
            ("♫   Bibliothèque", lambda: (self.pages.setCurrentIndex(2), self.library_page.refresh())),
            ("⏱   Historique", self.show_task_history),
            ("☰   Journaux", self.open_log_folder),
            ("⚙   Paramètres", self.show_settings),
        ]
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("nav")
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setTextElideMode(Qt.TextElideMode.ElideRight)
        for label, _action in self._sidebar_actions:
            self.sidebar.addItem(QListWidgetItem(label))
        self.sidebar.itemClicked.connect(self._sidebar_clicked)
        self.sidebar.setCurrentRow(0)
        layout.addWidget(self.sidebar, 1)
        self.pages.currentChanged.connect(self._page_changed)

        project_box = QFrame()
        project_box.setProperty("card", "true")
        box = QVBoxLayout(project_box)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(6)
        caption = QLabel("PROJET")
        caption.setProperty("role", "step")
        box.addWidget(caption)
        self.sidebar_project = QLabel("Aucun projet")
        self.sidebar_project.setWordWrap(True)
        box.addWidget(self.sidebar_project)
        buttons = QHBoxLayout()
        new_button = QPushButton("Nouveau")
        new_button.clicked.connect(self.new_project)
        open_button = QPushButton("Ouvrir…")
        open_button.clicked.connect(self.open_project)
        buttons.addWidget(new_button)
        buttons.addWidget(open_button)
        box.addLayout(buttons)
        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(12, 0, 12, 0)
        wrapper.addWidget(project_box)
        layout.addLayout(wrapper)

    def _sidebar_clicked(self, item):
        row = self.sidebar.row(item)
        if 0 <= row < len(self._sidebar_actions):
            self._sidebar_actions[row][1]()

    def _page_changed(self, index: int):
        if index != 0:
            self.studio.player.pause()
        if index != 1:
            self.player.pause()
        row = {0: 0, 1: 1, 2: 9}.get(index)
        current = self.sidebar.currentRow()
        if row is not None and not (index == 1 and 1 <= current <= 8):
            self.sidebar.setCurrentRow(row)

    def _build_menu(self):
        project_menu = self.menuBar().addMenu("Projet")
        new_action = QAction("Nouveau…", self)
        new_action.triggered.connect(self.new_project)
        open_action = QAction("Ouvrir…", self)
        open_action.triggered.connect(self.open_project)
        project_menu.addAction(new_action)
        project_menu.addAction(open_action)
        project_menu.addSeparator()
        close_action = QAction("Fermer le projet", self)
        close_action.triggered.connect(self.close_project)
        project_menu.addAction(close_action)
        relink_action = QAction("Relier les médias manquants…", self)
        relink_action.triggered.connect(self.relink_missing_media)
        project_menu.addAction(relink_action)
        project_menu.addSeparator()
        purge_action = QAction("Purger le cache du projet", self)
        purge_action.triggered.connect(self.purge_cache)
        project_menu.addAction(purge_action)
        tools_menu = self.menuBar().addMenu("Outils")
        for label, handler in (("Paramètres…", self.show_settings), ("Historique des tâches…", self.show_task_history),
                               ("Ouvrir le dossier des journaux", self.open_log_folder)):
            action = QAction(label, self)
            action.triggered.connect(handler)
            tools_menu.addAction(action)

    def _set_project_state(self, active: bool):
        self.import_button.setEnabled(active)
        self.import_folder_button.setEnabled(active)
        self.export_button.setEnabled(active)
        self.visual_stage_button.setEnabled(active)
        self.framing_add_button.setEnabled(active)
        self.framing_delete_button.setEnabled(active)
        self.framing_import_track_button.setEnabled(active)
        for control in (self.sharpening_enabled, self.sharpness, self.sharpening_radius,
                        self.sharpening_threshold, self.sharpening_reset_button):
            control.setEnabled(active)
        self.sharpening_compare_button.setEnabled(active and bool(self.media))
        self.composition_stage_button.setEnabled(active)
        self.finishing_stage_button.setEnabled(active)
        self.track_button.setEnabled(active and bool(available_trackers()))
        self.caption_import_button.setEnabled(active)
        self.caption_add_button.setEnabled(active)
        self.caption_seek_button.setEnabled(active)
        self.caption_split_button.setEnabled(active)
        self.caption_merge_button.setEnabled(active)
        self.mask_add_button.setEnabled(active)
        self.broll_import_button.setEnabled(active)
        self.broll_place_button.setEnabled(active)
        self.broll_delete_button.setEnabled(active)
        self.audio_import_button.setEnabled(active)
        self.music_apply_button.setEnabled(active)
        self.music_remove_button.setEnabled(active)
        self.sfx_add_button.setEnabled(active)
        self.sfx_delete_button.setEnabled(active)
        self._update_caption_actions()
        self.caption_preset_combo.setEnabled(active)
        self.caption_preset_edit_button.setEnabled(active)
        self.caption_preset_duplicate_button.setEnabled(active)
        self.caption_preset_import_button.setEnabled(active)
        self.caption_preset_export_button.setEnabled(active)
        self.caption_preset_delete_button.setEnabled(active)
        self._update_mask_actions()
        self._broll_selection_changed()

    def _set_task_state(self, active: bool):
        self.cancel_button.setEnabled(active)
        self.cancel_button.setVisible(active)
        self.progress.setRange(0, 0 if active else 100)
        if not active:
            self.progress.setValue(0)
        self.import_button.setEnabled(self.store is not None and not active)
        self.import_folder_button.setEnabled(self.store is not None and not active)
        self.export_button.setEnabled(self.store is not None and bool(self.media) and not active)
        for button in (self.visual_stage_button, self.composition_stage_button, self.finishing_stage_button):
            button.setEnabled(self.store is not None and bool(self.media) and not active)
        self.framing_add_button.setEnabled(self.store is not None and bool(self.media) and not active)
        self.framing_import_track_button.setEnabled(self.store is not None and bool(self.media) and not active)
        self.framing_delete_button.setEnabled(self.store is not None and not active
                                              and bool(self.framing_table.selectionModel().selectedRows()))
        self.framing_table.setEnabled(not active)
        for control in (self.sharpening_enabled, self.sharpness, self.sharpening_radius,
                        self.sharpening_threshold, self.sharpening_reset_button):
            control.setEnabled(self.store is not None and not active)
        self.sharpening_compare_button.setEnabled(self.store is not None and bool(self.media) and not active)
        self.track_button.setEnabled(self.store is not None and bool(self.media) and bool(available_trackers()) and not active)
        self.caption_import_button.setEnabled(self.store is not None and not active)
        self.caption_add_button.setEnabled(self.store is not None and not active)
        self.caption_seek_button.setEnabled(self.store is not None and not active)
        self.caption_split_button.setEnabled(self.store is not None and not active)
        self.caption_merge_button.setEnabled(self.store is not None and not active)
        self.caption_preset_combo.setEnabled(self.store is not None and not active)
        self.caption_preset_edit_button.setEnabled(self.store is not None and not active)
        self.caption_preset_duplicate_button.setEnabled(self.store is not None and not active)
        self.caption_preset_import_button.setEnabled(self.store is not None and not active)
        self.caption_preset_export_button.setEnabled(self.store is not None and not active)
        self.caption_preset_delete_button.setEnabled(
            self.store is not None and not active and self._current_caption_preset() is not None
            and self._current_caption_preset().name.casefold() not in BUILTIN_BY_NAME
        )
        self.caption_table.setEnabled(not active)
        self.mask_table.setEnabled(not active)
        self.mask_add_button.setEnabled(self.store is not None and not active)
        self.broll_import_button.setEnabled(self.store is not None and not active)
        self.broll_place_button.setEnabled(self.store is not None and not active and self._selected_broll_asset() is not None)
        self.broll_delete_button.setEnabled(self.store is not None and not active and bool(self.broll_placements.selectionModel().selectedRows()))
        self.audio_import_button.setEnabled(self.store is not None and not active)
        self.music_apply_button.setEnabled(self.store is not None and not active and self._selected_audio_asset() is not None)
        media = self._selected_media()
        has_music = bool(self.store and media and self.store.get_music_mix(media.id))
        self.music_remove_button.setEnabled(self.store is not None and not active and has_music)
        self.sfx_add_button.setEnabled(self.store is not None and not active and self._selected_audio_asset() is not None)
        self.sfx_table.setEnabled(not active)
        self.sfx_delete_button.setEnabled(self.store is not None and not active and bool(self.sfx_table.selectionModel().selectedRows()))
        self._update_caption_actions()
        self._update_mask_actions()
        self._broll_selection_changed()
        self.mask_from_track_button.setEnabled(self.store is not None and bool(self.media) and not active)
        self.rules_panel.set_active(self.store is not None and not active)
        self.pipeline_panel.set_busy(active)
        self.studio.set_busy(active)
        self.library_page.refresh()
        if not active:
            self.stage_label.setText("Prêt")
            self.pipeline_panel.refresh()

    def _append_log(self, message: str):
        self.log_view.append(message)
        self.statusBar().showMessage(message, 7000)

    def new_project(self):
        path = QFileDialog.getExistingDirectory(self, "Choisir le dossier du projet")
        if not path:
            return
        try:
            store = ProjectStore.create(path)
        except Exception as exc:
            QMessageBox.critical(self, "Création impossible", str(exc))
            return
        self._load_project(store)

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ouvrir un projet CUTWEAVE", "", "Projet CUTWEAVE (project.json)")
        if not path:
            return
        try:
            store = ProjectStore(Path(path).parent)
        except Exception as exc:
            QMessageBox.critical(self, "Ouverture impossible", str(exc))
            return
        self._load_project(store)

    def purge_cache(self):
        if not self.store:
            QMessageBox.information(self, "Aucun projet", "Ouvrez un projet avant de purger son cache.")
            return
        try:
            removed = StageCache(self.store.cache_root).purge()
            self._append_log(f"Cache purgé : {removed} artefact(s) supprimé(s).")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Purge impossible", str(exc))

    def _load_project(self, store: ProjectStore):
        self.player.stop()
        self.player.setSource(QUrl())
        self.store = store
        self.media = store.list_media()
        self.project_label.setText(store.manifest["name"])
        self.project_label.setToolTip(str(store.root))
        self.sidebar_project.setText(store.manifest["name"])
        self.sidebar_project.setToolTip(str(store.root))
        self._populate_table()
        self._populate_captions()
        self._populate_masks()
        self._populate_caption_presets()
        self._populate_broll_assets()
        self._populate_broll_placements()
        self._populate_audio_assets()
        self._load_music_mix_to_ui()
        self._populate_sfx_placements()
        self.rules_panel.refresh()
        self.pipeline_panel.refresh()
        self._set_project_state(True)
        self._set_task_state(False)
        missing = [item.name for item in self.media if item.missing]
        if missing:
            self._append_log(f"{len(missing)} média(s) introuvable(s) : {', '.join(missing[:5])}. "
                             "Utilisez Projet > Relier les médias manquants.")
        self._append_log(f"Projet ouvert : {store.root}")
        self.studio.refresh()
        self.studio.show_results_if_any()
        self.pages.setCurrentIndex(0)

    def import_media(self):
        if not self.store:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Importer des vidéos", "", "Vidéos (*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mpeg *.mpg);;Tous les fichiers (*.*)")
        if not paths:
            return
        self._start_import(paths)

    def import_folder(self):
        if not self.store:
            return
        path = QFileDialog.getExistingDirectory(self, "Importer les vidéos d'un dossier")
        if not path:
            return
        try:
            paths = collect_video_files([path])
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import impossible", str(exc))
            return
        if not paths:
            QMessageBox.information(self, "Aucune vidéo", "Aucun fichier vidéo pris en charge n'a été trouvé dans ce dossier.")
            return
        self._start_import(paths)

    def _start_import(self, paths):
        try:
            paths = collect_video_files(paths)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Import impossible", str(exc))
            return
        if not paths:
            QMessageBox.information(self, "Aucune vidéo", "Aucun fichier vidéo pris en charge n'a été sélectionné.")
            return
        store = self.store

        def operation(progress, cancel_event):
            imported = []
            errors = []
            cancelled = False
            for path in paths:
                if cancel_event.is_set():
                    cancelled = True
                    break
                progress(f"Analyse : {Path(path).name}")
                try:
                    info = probe_media(path, cancel_event=cancel_event)
                    media = store.add_media(path, info)
                    imported.append(media)
                    try:
                        create_video_thumbnail(
                            path, store.cache_root / "thumbnails" / f"rush-{media.id}.jpg",
                            time_ms=round(min(info.duration * 0.3, 5.0) * 1000), width=288, height=162,
                            cancel_event=cancel_event)
                    except ThumbnailError as exc:
                        LOGGER.warning("Miniature impossible pour %s : %s", Path(path).name, exc)
                except MediaProbeError as exc:
                    if cancel_event.is_set():
                        cancelled = True
                        break
                    errors.append(f"{Path(path).name} : {exc}")
            return imported, errors, cancelled

        self._run_task(operation, self._import_done)

    def _import_done(self, result):
        imported, errors, cancelled = result
        self.media = self.store.list_media() if self.store else []
        self._populate_table()
        self._populate_captions()
        self._populate_masks()
        self._refresh_caption_preset_for_media()
        self._populate_broll_placements()
        self._load_music_mix_to_ui()
        self._populate_sfx_placements()
        self.studio.refresh()
        state = "interrompu" if cancelled else "terminé"
        self._append_log(f"Import {state} : {len(imported)} média(s) analysé(s), {len(errors)} erreur(s).")
        for error in errors:
            self._append_log(error)

    def _populate_table(self):
        self.table.setRowCount(len(self.media))
        for row, item in enumerate(self.media):
            values = [
                item.name + ("  [manquant]" if item.missing else ""),
                self._format_duration(item.duration),
                f"{item.width or '—'} × {item.height or '—'}",
                f"{item.frame_rate:.3g}" if item.frame_rate else "—",
                "Oui" if item.has_audio else "Non" if item.has_audio is not None else "—",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.id)
                self.table.setItem(row, column, cell)
        self.table.resizeColumnsToContents()
        self.export_button.setEnabled(self.store is not None and bool(self.media) and self.thread is None)
        for button in (self.visual_stage_button, self.composition_stage_button, self.finishing_stage_button):
            button.setEnabled(self.store is not None and bool(self.media) and self.thread is None)
        self.framing_add_button.setEnabled(self.store is not None and bool(self.media) and self.thread is None)
        self.framing_import_track_button.setEnabled(self.store is not None and bool(self.media) and self.thread is None)
        self.sharpening_compare_button.setEnabled(self.store is not None and bool(self.media) and self.thread is None)

    @staticmethod
    def _format_duration(value):
        if value is None:
            return "—"
        seconds = int(value)
        return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"

    def _selected_media(self) -> ProjectMedia | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        return self.media[row] if 0 <= row < len(self.media) else None

    def _selection_changed(self):
        item = self._selected_media()
        self._populate_visual_settings()
        self._populate_framing_keyframes()
        self._populate_captions()
        self._populate_masks()
        self._refresh_caption_preset_for_media()
        self._populate_broll_placements()
        self._load_music_mix_to_ui()
        self._populate_sfx_placements()
        self.rules_panel.refresh()
        self.pipeline_panel.refresh()
        self.pipeline_panel.set_busy(self.thread is not None)
        if not item:
            self.player.stop()
            self.seek_slider.setRange(0, 0)
            self.player_status.setText("Sélectionnez une vidéo")
            return
        if item.missing:
            self.player.stop()
            self.seek_slider.setRange(0, 0)
            self.player_status.setText("Fichier manquant — réimportez-le après l'avoir localisé")
            return
        self.player.setSource(QUrl.fromLocalFile(str(item.path)))
        self.player_status.setText(item.name)

    def add_framing_keyframe(self):
        media = self._selected_media()
        if not media or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo avant d’ajouter un cadrage.")
            return
        timestamp = max(0, self.player.position())
        value, accepted = QInputDialog.getText(
            self, "Keyframe de cadrage", "Centre X, centre Y, zoom (X/Y entre 0 et 1)",
            text="0.5,0.5,1.0",
        )
        if not accepted:
            return
        try:
            center_x, center_y, zoom = (float(part.strip()) for part in value.split(","))
            frames = [frame for frame in self.store.get_framing_keyframes(media.id)
                      if frame.time_ms != timestamp]
            frames.append(FramingKeyframe(timestamp, center_x, center_y, zoom))
            frames.sort(key=lambda frame: frame.time_ms)
            self.store.set_framing_keyframes(media.id, frames)
            self._populate_framing_keyframes()
        except (ValueError, FramingValidationError) as exc:
            QMessageBox.warning(self, "Cadrage invalide", str(exc))

    def delete_framing_keyframes(self):
        media = self._selected_media()
        rows = sorted(index.row() for index in self.framing_table.selectionModel().selectedRows())
        if not media or not self.store or not rows:
            return
        frames = list(self.store.get_framing_keyframes(media.id))
        for row in reversed(rows):
            if 0 <= row < len(frames):
                del frames[row]
        self.store.set_framing_keyframes(media.id, frames)
        self._populate_framing_keyframes()

    def import_tracking_as_framing(self):
        media = self._selected_media()
        if not media or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo avant d'importer une trajectoire.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer une trajectoire de tracking", str(self.store.root / "tracking"),
            "Trajectoire CUTWEAVE (*.json)",
        )
        if not path:
            return
        try:
            trajectory = load_trajectory(path)
            if trajectory.source_name.casefold() != media.path.name.casefold():
                raise ValueError("La trajectoire a été créée depuis un autre fichier source.")
            frames = trajectory_to_framing_keyframes(
                trajectory, self.aspect.currentText(), margin=self.track_margin.value(),
                smoothing_window=self.track_smoothing.value(),
            )
        except (TrackingError, ValueError, OSError) as exc:
            QMessageBox.warning(self, "Trajectoire invalide", str(exc))
            return
        answer = QMessageBox.question(
            self, "Remplacer le cadrage ?",
            f"Importer {len(frames)} positions suivies dans le cadrage de {media.name} ?\n"
            "Les keyframes de cadrage actuelles seront remplacées. Vous pourrez ensuite corriger chaque ligne.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.set_framing_keyframes(media.id, frames)
            self._populate_framing_keyframes()
            self._append_log(f"Trajectoire importée dans le cadrage : {len(frames)} keyframe(s).")
        except ValueError as exc:
            QMessageBox.warning(self, "Import du cadrage impossible", str(exc))

    def _populate_framing_keyframes(self):
        if not hasattr(self, "framing_table"):
            return
        self._framing_refreshing = True
        try:
            media = self._selected_media()
            frames = self.store.get_framing_keyframes(media.id) if self.store and media else ()
            self.framing_table.setRowCount(len(frames))
            for row, frame in enumerate(frames):
                for column, value in enumerate((str(frame.time_ms), f"{frame.center_x:.4f}",
                                                f"{frame.center_y:.4f}", f"{frame.zoom:.3f}")):
                    self.framing_table.setItem(row, column, QTableWidgetItem(value))
            self.framing_table.resizeColumnsToContents()
            self.framing_delete_button.setEnabled(bool(frames) and self.thread is None)
        finally:
            self._framing_refreshing = False

    def _framing_cell_changed(self, _row: int, _column: int):
        if getattr(self, "_framing_refreshing", False):
            return
        media = self._selected_media()
        if not media or not self.store:
            return
        try:
            frames = [FramingKeyframe(
                int(self.framing_table.item(row, 0).text()),
                float(self.framing_table.item(row, 1).text()),
                float(self.framing_table.item(row, 2).text()),
                float(self.framing_table.item(row, 3).text()),
            ) for row in range(self.framing_table.rowCount())]
            frames.sort(key=lambda frame: frame.time_ms)
            self.store.set_framing_keyframes(media.id, frames)
            self._populate_framing_keyframes()
        except (AttributeError, ValueError, FramingValidationError) as exc:
            self._append_log(f"Modification du cadrage ignorée : {exc}")
            self._populate_framing_keyframes()

    def import_captions(self):
        media = self._selected_media()
        if not media or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez d’abord la vidéo à sous-titrer.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer des captions", "", "Captions (*.srt *.vtt *.ass *.ssa)"
        )
        if not path:
            return
        try:
            document = load_caption_file(path)
            existing = self.store.get_captions(media.id)
            if existing and QMessageBox.question(
                self, "Remplacer les captions", "Remplacer les captions déjà enregistrées pour cette vidéo ?"
            ) != QMessageBox.StandardButton.Yes:
                return
            self.store.set_captions(media.id, document.cues)
            self._populate_captions()
            self._append_log(f"{len(document.cues)} segment(s) importé(s) depuis {Path(path).name} ({document.source_format.upper()}).")
        except (CaptionParseError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import captions impossible", str(exc))
            self._append_log(f"Erreur captions : {exc}")

    def add_caption(self):
        media = self._selected_media()
        if not media or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo avant d’ajouter un segment.")
            return
        start_ms = max(0, self.player.position())
        proposed_end = start_ms + 2000
        if media.duration:
            proposed_end = min(proposed_end, int(media.duration * 1000))
        if proposed_end <= start_ms:
            QMessageBox.warning(self, "Position invalide", "Le curseur est à la fin de la vidéo.")
            return
        text, accepted = QInputDialog.getMultiLineText(self, "Nouveau segment", "Texte", "")
        if not accepted:
            return
        try:
            cue = CaptionCue(start_ms, proposed_end, text)
            cue.validate()
            cues = (*self.store.get_captions(media.id), cue)
            self.store.set_captions(media.id, cues)
            self._populate_captions()
        except CaptionParseError as exc:
            QMessageBox.warning(self, "Segment invalide", str(exc))

    def delete_caption(self):
        media = self._selected_media()
        rows = [index.row() for index in self.caption_table.selectionModel().selectedRows()]
        if not media or not self.store or not rows:
            return
        cues = list(self.store.get_captions(media.id))
        for row in sorted(rows, reverse=True):
            if 0 <= row < len(cues):
                del cues[row]
        self.store.set_captions(media.id, cues)
        self._populate_captions()

    def seek_to_caption(self):
        media = self._selected_media()
        row = self.caption_table.currentRow()
        if not media or not self.store or row < 0:
            return
        cues = self.store.get_captions(media.id)
        if row < len(cues):
            self.player.setPosition(cues[row].start_ms)

    def split_caption(self):
        media = self._selected_media()
        rows = self.caption_table.selectionModel().selectedRows()
        if not media or not self.store or len(rows) != 1:
            return
        row = rows[0].row()
        cues = list(self.store.get_captions(media.id))
        if row >= len(cues):
            return
        cue = cues[row]
        default_time = self._caption_time((cue.start_ms + cue.end_ms) // 2)
        value, accepted = QInputDialog.getText(
            self, "Scinder le segment", "Timestamp de coupure (HH:MM:SS.mmm)", text=default_time
        )
        if not accepted:
            return
        try:
            parts = split_cue(cue, parse_timestamp(value, format="vtt"))
            cues[row:row + 1] = parts
            self.store.set_captions(media.id, cues)
            self._populate_captions()
        except CaptionParseError as exc:
            QMessageBox.warning(self, "Scission impossible", str(exc))

    def merge_caption_rows(self):
        media = self._selected_media()
        rows = sorted(index.row() for index in self.caption_table.selectionModel().selectedRows())
        if not media or not self.store or len(rows) != 2 or rows[1] != rows[0] + 1:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez deux segments adjacents.")
            return
        cues = list(self.store.get_captions(media.id))
        try:
            merged = merge_cues(cues[rows[0]:rows[1] + 1])
            cues[rows[0]:rows[1] + 1] = [merged]
            self.store.set_captions(media.id, cues)
            self._populate_captions()
        except CaptionParseError as exc:
            QMessageBox.warning(self, "Fusion impossible", str(exc))

    def _populate_captions(self):
        self._caption_refreshing = True
        try:
            media = self._selected_media()
            cues = self.store.get_captions(media.id) if self.store and media else ()
            self.caption_table.setRowCount(len(cues))
            for row, cue in enumerate(cues):
                values = [self._caption_time(cue.start_ms), self._caption_time(cue.end_ms), cue.text]
                for column, value in enumerate(values):
                    self.caption_table.setItem(row, column, QTableWidgetItem(value))
            self.caption_table.resizeColumnsToContents()
            self._update_caption_actions()
        finally:
            self._caption_refreshing = False

    def _caption_selection_changed(self):
        self._update_caption_actions()

    def _update_caption_actions(self):
        selected_rows = self.caption_table.selectionModel().selectedRows()
        self.caption_delete_button.setEnabled(self.store is not None and self.thread is None and bool(selected_rows))
        self.caption_seek_button.setEnabled(
            self.store is not None and self.thread is None and self.caption_table.currentRow() >= 0
        )
        self.caption_split_button.setEnabled(
            self.store is not None and self.thread is None and len(selected_rows) == 1
        )
        indexes = sorted(index.row() for index in selected_rows)
        self.caption_merge_button.setEnabled(
            self.store is not None and self.thread is None and len(indexes) == 2 and indexes[1] == indexes[0] + 1
        )

    @staticmethod
    def _caption_time(milliseconds: int) -> str:
        seconds, millis = divmod(milliseconds, 1000)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"

    def _caption_cell_changed(self, _row: int, _column: int):
        if getattr(self, "_caption_refreshing", False):
            return
        media = self._selected_media()
        if not media or not self.store:
            return
        cues = []
        try:
            for row in range(self.caption_table.rowCount()):
                start = parse_timestamp(self.caption_table.item(row, 0).text(), format="vtt")
                end = parse_timestamp(self.caption_table.item(row, 1).text(), format="vtt")
                cue = CaptionCue(start, end, self.caption_table.item(row, 2).text())
                cue.validate()
                cues.append(cue)
            self.store.set_captions(media.id, cues)
            self._populate_captions()
        except (AttributeError, CaptionParseError, ValueError) as exc:
            self._append_log(f"Modification ignorée : {exc}")
            self._populate_captions()

    def _all_caption_presets(self) -> tuple[CaptionPreset, ...]:
        custom = self.store.list_custom_caption_presets() if self.store else ()
        return (*BUILTIN_CAPTION_PRESETS, *custom)

    def _current_caption_preset(self) -> CaptionPreset | None:
        name = self.caption_preset_combo.currentText() if hasattr(self, "caption_preset_combo") else ""
        if not name:
            return None
        return next((preset for preset in self._all_caption_presets() if preset.name.casefold() == name.casefold()), None)

    def _populate_caption_presets(self, selected_name: str | None = None):
        if not hasattr(self, "caption_preset_combo"):
            return
        media = self._selected_media()
        stored = self.store.get_caption_preset(media.id) if self.store and media else None
        desired = selected_name or (stored.name if stored else "Clean")
        self._caption_preset_refreshing = True
        try:
            self.caption_preset_combo.clear()
            for preset in self._all_caption_presets():
                self.caption_preset_combo.addItem(preset.name)
            index = self.caption_preset_combo.findText(desired, Qt.MatchFlag.MatchFixedString)
            if index < 0:
                index = self.caption_preset_combo.findText("Clean", Qt.MatchFlag.MatchFixedString)
            self.caption_preset_combo.setCurrentIndex(max(0, index))
        finally:
            self._caption_preset_refreshing = False
        self._update_caption_preset_status()

    def _refresh_caption_preset_for_media(self):
        self._populate_caption_presets()

    def _update_caption_preset_status(self):
        preset = self._current_caption_preset()
        if preset is None:
            self.caption_preset_status.setText("Choisissez un preset.")
            return
        animation = {"none": "aucune animation", "fade": "fondu", "pop": "pop"}[preset.animation]
        highlight = "surlignage de toute la caption" if preset.highlight_mode == "cue" else "sans surlignage"
        self.caption_preset_status.setText(
            f"{preset.font_name}, {preset.font_size}px à 1080p · {animation} {preset.animation_ms} ms · {highlight}. "
            "Les animations sont déterministes ; le surlignage n'est pas synchronisé mot à mot."
        )
        self.caption_preset_delete_button.setEnabled(
            self.store is not None and self.thread is None and preset.name.casefold() not in BUILTIN_BY_NAME
        )

    def _caption_preset_changed(self, name: str):
        if getattr(self, "_caption_preset_refreshing", False):
            return
        preset = next((item for item in self._all_caption_presets() if item.name.casefold() == name.casefold()), None)
        media = self._selected_media()
        if preset is not None and self.store is not None and media is not None:
            self.store.set_caption_preset(media.id, preset)
        self._update_caption_preset_status()

    def edit_caption_preset(self):
        preset = self._current_caption_preset()
        if preset is None or not self.store:
            return
        if preset.name.casefold() in BUILTIN_BY_NAME:
            preset = replace(preset, name=f"{preset.name} Custom")
        dialog = CaptionPresetDialog(preset, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        edited = dialog.preset()
        try:
            self.store.save_custom_caption_preset(edited)
            if preset.name.casefold() not in BUILTIN_BY_NAME and preset.name.casefold() != edited.name.casefold():
                self.store.delete_custom_caption_preset(preset.name)
            self._populate_caption_presets(edited.name)
            self._caption_preset_changed(edited.name)
        except CaptionPresetError as exc:
            QMessageBox.warning(self, "Preset non enregistré", str(exc))

    def duplicate_caption_preset(self):
        preset = self._current_caption_preset()
        if preset is None or not self.store:
            return
        name, accepted = QInputDialog.getText(self, "Dupliquer le preset", "Nom de la copie", text=f"{preset.name} Copy")
        if not accepted:
            return
        try:
            duplicate = replace(preset, name=name.strip())
            duplicate.validate()
            self.store.save_custom_caption_preset(duplicate)
            self._populate_caption_presets(duplicate.name)
            self._caption_preset_changed(duplicate.name)
        except CaptionPresetError as exc:
            QMessageBox.warning(self, "Duplication impossible", str(exc))

    def import_caption_preset(self):
        if not self.store:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Importer un preset captions", "", "Preset JSON (*.json)")
        if not path:
            return
        try:
            preset = CaptionPreset.load(path)
            self.store.save_custom_caption_preset(preset)
            self._populate_caption_presets(preset.name)
            self._caption_preset_changed(preset.name)
            self._append_log(f"Preset captions importé : {preset.name}")
        except (CaptionPresetError, OSError) as exc:
            QMessageBox.warning(self, "Import impossible", str(exc))

    def export_caption_preset(self):
        preset = self._current_caption_preset()
        if preset is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporter un preset captions", f"{preset.name}.json", "Preset JSON (*.json)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".json"
        try:
            saved = preset.save(path)
            self._append_log(f"Preset captions exporté : {saved}")
        except (CaptionPresetError, OSError) as exc:
            QMessageBox.warning(self, "Export impossible", str(exc))

    def delete_caption_preset(self):
        preset = self._current_caption_preset()
        if preset is None or not self.store or preset.name.casefold() in BUILTIN_BY_NAME:
            return
        self.store.delete_custom_caption_preset(preset.name)
        self._populate_caption_presets("Clean")
        self._caption_preset_changed("Clean")

    def add_mask(self):
        media = self._selected_media()
        if not media or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo avant d’ajouter un masque.")
            return
        shape, accepted = QInputDialog.getItem(
            self, "Forme du masque", "Forme", ["rectangle", "ellipse", "polygon"], 0, False,
        )
        if not accepted:
            return
        default = f"0,0,{max(16, self.width_spin.value() // 4)},{max(16, self.height_spin.value() // 4)}"
        value, accepted = QInputDialog.getText(
            self, "Ajouter un masque", "X, Y, largeur, hauteur (pixels du rendu)", text=default
        )
        if not accepted:
            return
        try:
            coordinates = [int(part.strip()) for part in value.split(",")]
            if len(coordinates) != 4:
                raise ValueError("Saisissez quatre valeurs : X, Y, largeur et hauteur.")
            points = ()
            if shape == "polygon":
                points_text, points_accepted = QInputDialog.getText(
                    self, "Sommets du polygone", "Coordonnées X:Y séparées par des points-virgules",
                    text="20:20;120:20;70:100",
                )
                if not points_accepted:
                    return
                points = tuple(tuple(int(part.strip()) for part in point.split(":"))
                               for point in points_text.split(";") if point.strip())
                if any(len(point) != 2 for point in points):
                    raise ValueError("Chaque sommet doit être écrit X:Y.")
            mask = MaskRegion(*coordinates, shape=shape, points=points)
            mask.validate(self.width_spin.value(), self.height_spin.value())
            masks = (*self.store.get_masks(media.id), mask)
            self.store.set_masks(media.id, masks)
            self._populate_masks()
        except (ValueError, MaskValidationError) as exc:
            QMessageBox.warning(self, "Masque invalide", str(exc))

    def delete_masks(self):
        media = self._selected_media()
        rows = [index.row() for index in self.mask_table.selectionModel().selectedRows()]
        if not media or not self.store or not rows:
            return
        masks = list(self.store.get_masks(media.id))
        for row in sorted(rows, reverse=True):
            if 0 <= row < len(masks):
                del masks[row]
        self.store.set_masks(media.id, masks)
        self._populate_masks()

    def _populate_masks(self):
        self._mask_refreshing = True
        try:
            media = self._selected_media()
            masks = self.store.get_masks(media.id) if self.store and media else ()
            self.mask_table.setRowCount(len(masks))
            for row, mask in enumerate(masks):
                values = [
                    str(mask.x), str(mask.y), str(mask.width), str(mask.height), f"{mask.opacity:.3f}",
                    self._caption_time(mask.start_ms) if mask.start_ms is not None else "",
                    self._caption_time(mask.end_ms) if mask.end_ms is not None else "",
                    mask.shape, ";".join(f"{x}:{y}" for x, y in mask.points),
                    ";".join(f"{frame.time_ms}:{frame.x}:{frame.y}:{frame.width}:{frame.height}:{frame.opacity:g}"
                             for frame in mask.keyframes),
                    mask.effect, str(mask.blur_radius),
                ]
                for column, value in enumerate(values):
                    self.mask_table.setItem(row, column, QTableWidgetItem(value))
            self.mask_table.resizeColumnsToContents()
            self._update_mask_actions()
        finally:
            self._mask_refreshing = False

    def _mask_selection_changed(self):
        self._update_mask_actions()

    def _update_mask_actions(self):
        selected = self.mask_table.selectionModel().selectedRows()
        self.mask_delete_button.setEnabled(self.store is not None and self.thread is None and bool(selected))

    def import_audio_assets(self):
        if not self.store:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Importer des fichiers audio", "",
            "Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma);;Tous les fichiers (*.*)",
        )
        if not paths:
            return
        supported = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
        paths = [path for path in paths if Path(path).suffix.lower() in supported]
        if not paths:
            QMessageBox.information(self, "Aucun fichier audio", "Aucun format audio pris en charge n'a été sélectionné.")
            return
        category, accepted = QInputDialog.getText(self, "Catégorie audio", "Catégorie manuelle (par défaut : music)", text="music")
        if not accepted:
            return
        tags_text, accepted = QInputDialog.getText(self, "Tags audio", "Tags séparés par des virgules (facultatif)")
        if not accepted:
            return
        store = self.store
        tags = parse_audio_tags(tags_text)
        category = category.strip() or "music"

        def operation(progress, cancel_event):
            imported, errors = [], []
            for path in paths:
                if cancel_event.is_set():
                    break
                progress(f"Analyse audio : {Path(path).name}")
                try:
                    info = probe_audio(path, cancel_event=cancel_event)
                    imported.append(store.add_audio_asset(path, info, category=category, tags=tags))
                except MediaProbeError as exc:
                    if cancel_event.is_set():
                        break
                    errors.append(f"{Path(path).name} : {exc}")
            return imported, errors

        def done(result):
            imported, errors = result
            self._populate_audio_assets()
            self._append_log(f"Bibliothèque audio : {len(imported)} fichier(s) ajouté(s), {len(errors)} erreur(s).")
            for error in errors:
                self._append_log(error)

        self._run_task(operation, done)

    def _populate_audio_assets(self):
        if not hasattr(self, "audio_assets"):
            return
        self.audio_assets.clear()
        assets = self.store.list_audio_assets(self.audio_search.text()) if self.store else ()
        for asset in assets:
            parts = [asset.category, ", ".join(asset.tags)]
            details = ", ".join(part for part in parts if part)
            missing = " [manquant]" if not asset.path.is_file() else ""
            item = QListWidgetItem(f"{asset.name}{missing}  ·  {self._format_duration(asset.duration_ms / 1000)}  ·  {details}")
            item.setData(Qt.ItemDataRole.UserRole, asset.id)
            self.audio_assets.addItem(item)
        self._audio_selection_changed()

    def _selected_audio_asset(self):
        if not self.store or not self.audio_assets.currentItem():
            return None
        asset_id = self.audio_assets.currentItem().data(Qt.ItemDataRole.UserRole)
        return self.store.get_audio_asset(asset_id)

    def _audio_selection_changed(self):
        if not hasattr(self, "music_apply_button"):
            return
        asset = self._selected_audio_asset()
        self.music_apply_button.setEnabled(self.store is not None and self.thread is None and asset is not None)
        if asset is not None and self.music_source_in.value() == 0:
            self.music_source_out.setMaximum(max(1, asset.duration_ms))
            self.music_source_out.setValue(max(1, asset.duration_ms))

    def _load_music_mix_to_ui(self):
        if not hasattr(self, "music_status"):
            return
        media = self._selected_media()
        settings = self.store.get_music_mix(media.id) if self.store and media else None
        if settings is None:
            self.music_status.setText("Aucune musique affectée")
            self.music_remove_button.setEnabled(False)
            self.music_start.setValue(0)
            self.music_source_in.setValue(0)
            self.music_fade_in.setValue(0)
            self.music_fade_out.setValue(0)
            self.music_loop.setChecked(False)
            self.keep_source_audio.setChecked(True)
            self.music_ducking.clear()
            self.music_duck_captions.setChecked(False)
            return
        asset = self.store.get_audio_asset(settings.asset_id)
        if asset and not any(self.audio_assets.item(row).data(Qt.ItemDataRole.UserRole) == asset.id
                             for row in range(self.audio_assets.count())):
            self.audio_search.clear()
        for row in range(self.audio_assets.count()):
            if self.audio_assets.item(row).data(Qt.ItemDataRole.UserRole) == settings.asset_id:
                self.audio_assets.setCurrentRow(row)
                break
        self.music_start.setValue(settings.timeline_start_ms)
        self.music_source_in.setValue(settings.source_start_ms)
        self.music_source_out.setMaximum(max(1, asset.duration_ms if asset else settings.source_end_ms))
        self.music_source_out.setValue(settings.source_end_ms)
        self.music_volume.setValue(settings.volume)
        self.music_fade_in.setValue(settings.fade_in_ms)
        self.music_fade_out.setValue(settings.fade_out_ms)
        self.music_loop.setChecked(settings.loop)
        self.keep_source_audio.setChecked(settings.mix_original_audio)
        self.music_ducking.setText(format_ducking_segments(settings.ducking_segments))
        self.music_duck_captions.setChecked(settings.duck_on_captions)
        self.music_duck_level.setValue(settings.caption_duck_level)
        self.music_duck_fade.setValue(settings.caption_duck_fade_ms)
        self.music_status.setText(f"Musique : {asset.name if asset else 'fichier manquant'}")
        self.music_remove_button.setEnabled(self.thread is None)

    def preview_audio_asset(self, *_args):
        asset = self._selected_audio_asset()
        if not asset:
            return
        if not asset.path.is_file():
            QMessageBox.warning(self, "Fichier manquant", "Le fichier audio est introuvable.")
            return
        self.player.setSource(QUrl.fromLocalFile(str(asset.path)))
        self.player_status.setText(f"Prévisualisation audio : {asset.name}")
        self.player.play()
        self.play_button.setText("Pause")

    def save_music_mix(self):
        media = self._selected_media()
        asset = self._selected_audio_asset()
        if not media or not self.store or not asset:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo et un fichier de la bibliothèque audio.")
            return
        if asset.missing:
            QMessageBox.warning(self, "Fichier manquant", "Le fichier audio source est introuvable.")
            return
        try:
            ducking_segments = parse_ducking_segments(self.music_ducking.text())
        except MusicValidationError as exc:
            QMessageBox.warning(self, "Paramètres audio invalides", str(exc))
            return
        settings = MusicSettings(
            asset.id, self.music_start.value(), self.music_source_in.value(), self.music_source_out.value(),
            self.music_volume.value(), self.music_fade_in.value(), self.music_fade_out.value(),
            self.music_loop.isChecked(), self.keep_source_audio.isChecked(),
            ducking_segments, self.music_duck_captions.isChecked(), self.music_duck_level.value(),
            self.music_duck_fade.value(),
        )
        try:
            settings.validate(asset.duration_ms, max(0, round((media.duration or 0) * 1000) - settings.timeline_start_ms))
            self.store.set_music_mix(media.id, settings)
            self.music_status.setText(f"Musique : {asset.name}")
            self.music_remove_button.setEnabled(True)
            self._append_log(f"Piste musicale enregistrée pour {media.name}.")
        except MusicValidationError as exc:
            QMessageBox.warning(self, "Paramètres audio invalides", str(exc))

    def remove_music_mix(self):
        media = self._selected_media()
        if not media or not self.store:
            return
        self.store.set_music_mix(media.id, None)
        self._load_music_mix_to_ui()
        self._append_log(f"Piste musicale retirée de {media.name}.")

    def _populate_sfx_placements(self):
        if not hasattr(self, "sfx_table"):
            return
        self._sfx_refreshing = True
        try:
            media = self._selected_media()
            placements = self.store.get_sfx_placements(media.id) if self.store and media else ()
            self.sfx_table.setRowCount(len(placements))
            for row, placement in enumerate(placements):
                asset = self.store.get_audio_asset(placement.asset_id) if self.store else None
                values = [asset.name if asset else f"Audio {placement.asset_id}",
                          self._caption_time(placement.timeline_start_ms),
                          self._caption_time(placement.source_start_ms),
                          self._caption_time(placement.source_end_ms), f"{placement.volume:.3f}",
                          str(placement.fade_in_ms), str(placement.fade_out_ms), placement.event_marker]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    if column == 0:
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.sfx_table.setItem(row, column, cell)
            self.sfx_table.resizeColumnsToContents()
        finally:
            self._sfx_refreshing = False
        self._sfx_selection_changed()

    def _sfx_selection_changed(self):
        if not hasattr(self, "sfx_delete_button"):
            return
        selected = self.sfx_table.selectionModel().selectedRows()
        self.sfx_delete_button.setEnabled(self.store is not None and self.thread is None and bool(selected))

    def add_sfx_placement(self):
        media = self._selected_media()
        asset = self._selected_audio_asset()
        if not media or not self.store or not asset:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo et un fichier audio dans la bibliothèque.")
            return
        if asset.missing:
            QMessageBox.warning(self, "Fichier manquant", "Le fichier audio sélectionné est introuvable.")
            return
        if asset.duration_ms < 1:
            QMessageBox.warning(self, "Durée invalide", "La durée de ce fichier audio n'a pas pu être déterminée.")
            return
        source_in, accepted = QInputDialog.getInt(self, "Entrée du SFX", "Entrée source (millisecondes)", 0, 0, asset.duration_ms - 1)
        if not accepted:
            return
        source_out, accepted = QInputDialog.getInt(self, "Sortie du SFX", "Sortie source (millisecondes)", asset.duration_ms, source_in + 1, asset.duration_ms)
        if not accepted:
            return
        volume, accepted = QInputDialog.getDouble(self, "Volume du SFX", "Gain (0 à 4)", 1.0, 0, 4, 3)
        if not accepted:
            return
        fade_in, accepted = QInputDialog.getInt(self, "Fondu entrant", "Durée en millisecondes", 0, 0, source_out - source_in)
        if not accepted:
            return
        fade_out, accepted = QInputDialog.getInt(self, "Fondu sortant", "Durée en millisecondes", 0, 0, source_out - source_in - fade_in)
        if not accepted:
            return
        marker, accepted = QInputDialog.getText(self, "Événement ou marqueur", "Libellé manuel facultatif (ex. cut_01)")
        if not accepted:
            return
        placement = SfxPlacement(asset.id, max(0, self.player.position()), source_in, source_out,
                                 volume, fade_in, fade_out, marker.strip())
        try:
            self.store.set_sfx_placements(media.id, (*self.store.get_sfx_placements(media.id), placement))
            self._populate_sfx_placements()
            self._append_log(f"SFX ajouté à {self._caption_time(placement.timeline_start_ms)}.")
        except SfxValidationError as exc:
            QMessageBox.warning(self, "SFX invalide", str(exc))

    def delete_sfx_placements(self):
        media = self._selected_media()
        rows = sorted(index.row() for index in self.sfx_table.selectionModel().selectedRows())
        if not media or not self.store or not rows:
            return
        placements = list(self.store.get_sfx_placements(media.id))
        for row in reversed(rows):
            if 0 <= row < len(placements):
                del placements[row]
        self.store.set_sfx_placements(media.id, placements)
        self._populate_sfx_placements()

    def _sfx_cell_changed(self, _row: int, _column: int):
        if getattr(self, "_sfx_refreshing", False):
            return
        media = self._selected_media()
        if not media or not self.store:
            return
        placements = list(self.store.get_sfx_placements(media.id))
        try:
            edited = []
            for row, placement in enumerate(placements):
                edited.append(SfxPlacement(
                    placement.asset_id,
                    parse_timestamp(self.sfx_table.item(row, 1).text(), format="vtt"),
                    parse_timestamp(self.sfx_table.item(row, 2).text(), format="vtt"),
                    parse_timestamp(self.sfx_table.item(row, 3).text(), format="vtt"),
                    float(self.sfx_table.item(row, 4).text()),
                    int(self.sfx_table.item(row, 5).text()),
                    int(self.sfx_table.item(row, 6).text()),
                    self.sfx_table.item(row, 7).text(),
                ))
            self.store.set_sfx_placements(media.id, edited)
            self._populate_sfx_placements()
        except (ValueError, SfxValidationError) as exc:
            self._append_log(f"Modification du SFX ignorée : {exc}")
            self._populate_sfx_placements()

    def _mask_cell_changed(self, _row: int, _column: int):
        if getattr(self, "_mask_refreshing", False):
            return
        media = self._selected_media()
        if not media or not self.store:
            return
        masks = []
        try:
            for row in range(self.mask_table.rowCount()):
                values = [self.mask_table.item(row, col).text().strip() for col in range(12)]
                start = parse_timestamp(values[5], format="vtt") if values[5] else None
                end = parse_timestamp(values[6], format="vtt") if values[6] else None
                mask = MaskRegion(int(values[0]), int(values[1]), int(values[2]), int(values[3]),
                                  float(values[4]), start, end, values[7] or "rectangle",
                                  tuple(tuple(int(part.strip()) for part in point.split(":"))
                                        for point in values[8].split(";") if point.strip()))
                if any(len(point) != 2 for point in mask.points):
                    raise ValueError("Chaque sommet doit être écrit X:Y.")
                keyframes = []
                for item in values[9].split(";"):
                    if not item.strip():
                        continue
                    parts = [part.strip() for part in item.split(":")]
                    if len(parts) != 6:
                        raise ValueError("Chaque keyframe doit être écrite ms:X:Y:largeur:hauteur:opacité.")
                    keyframes.append(MaskKeyframe(*(int(part) for part in parts[:5]), float(parts[5])))
                mask = replace(mask, keyframes=tuple(keyframes), effect=values[10] or "opaque",
                               blur_radius=int(values[11] or 12))
                mask.validate(self.width_spin.value(), self.height_spin.value())
                masks.append(mask)
            self.store.set_masks(media.id, masks)
            self._populate_masks()
        except (AttributeError, ValueError, MaskValidationError) as exc:
            self._append_log(f"Modification du masque ignorée : {exc}")
            self._populate_masks()

    def import_broll_assets(self):
        if not self.store:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Importer des clips B-roll", "",
            "Vidéos (*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mpeg *.mpg);;Tous les fichiers (*.*)",
        )
        if not paths:
            return
        category, accepted = QInputDialog.getText(self, "Catégorie B-roll", "Catégorie manuelle (facultatif)")
        if not accepted:
            return
        tags_text, accepted = QInputDialog.getText(self, "Tags B-roll", "Tags séparés par des virgules (facultatif)")
        if not accepted:
            return
        store = self.store
        tags = parse_tags(tags_text)

        def operation(progress, cancel_event):
            imported, errors, thumbnail_errors = [], [], []
            for path in collect_video_files(paths):
                if cancel_event.is_set():
                    break
                progress(f"Analyse B-roll : {Path(path).name}")
                try:
                    info = probe_media(path, cancel_event=cancel_event)
                    asset = store.add_broll_asset(path, info, category=category, tags=tags)
                    imported.append(asset)
                    if cancel_event.is_set():
                        break
                    try:
                        duration_ms = max(0, round(info.duration * 1000))
                        thumbnail_time = min(1000, max(0, duration_ms // 3))
                        create_video_thumbnail(
                            asset.path, store.broll_thumbnail_path(asset.id),
                            time_ms=thumbnail_time, cancel_event=cancel_event,
                        )
                    except ThumbnailError as exc:
                        if cancel_event.is_set():
                            break
                        thumbnail_errors.append(f"Miniature {asset.name} : {exc}")
                except MediaProbeError as exc:
                    if cancel_event.is_set():
                        break
                    errors.append(f"{Path(path).name} : {exc}")
            return imported, errors, thumbnail_errors

        def done(result):
            imported, errors, thumbnail_errors = result
            self._populate_broll_assets()
            self._append_log(f"Bibliothèque B-roll : {len(imported)} clip(s) ajouté(s), {len(errors)} erreur(s).")
            for error in errors:
                self._append_log(error)
            for error in thumbnail_errors:
                self._append_log(error)

        self._run_task(operation, done)

    def _populate_broll_assets(self):
        if not hasattr(self, "broll_assets"):
            return
        self.broll_assets.clear()
        assets = self.store.list_broll_assets(self.broll_search.text()) if self.store else ()
        for asset in assets:
            duration = self._format_duration(asset.duration_ms / 1000)
            details = ", ".join(part for part in (asset.category, ", ".join(asset.tags)) if part)
            missing = " [manquant]" if not asset.path.is_file() else ""
            item = QListWidgetItem(f"{asset.name}{missing}  ·  {duration}" + (f"  ·  {details}" if details else ""))
            item.setData(Qt.ItemDataRole.UserRole, asset.id)
            thumbnail = self.store.broll_thumbnail_path(asset.id) if self.store else None
            if thumbnail and thumbnail.is_file():
                pixmap = QPixmap(str(thumbnail))
                if not pixmap.isNull():
                    item.setIcon(QIcon(pixmap))
                    item.setSizeHint(pixmap.size().scaled(360, 92, Qt.AspectRatioMode.KeepAspectRatio))
            self.broll_assets.addItem(item)
        self._broll_selection_changed()

    def _selected_broll_asset(self):
        if not self.store or not self.broll_assets.currentItem():
            return None
        asset_id = self.broll_assets.currentItem().data(Qt.ItemDataRole.UserRole)
        return next((asset for asset in self.store.list_broll_assets() if asset.id == asset_id), None)

    def _broll_selection_changed(self):
        if not hasattr(self, "broll_place_button"):
            return
        idle = self.thread is None
        self.broll_place_button.setEnabled(self.store is not None and idle and self._selected_broll_asset() is not None)
        selected = self.broll_placements.selectionModel().selectedRows() if hasattr(self, "broll_placements") else ()
        self.broll_delete_button.setEnabled(self.store is not None and idle and bool(selected))

    def _populate_broll_placements(self):
        if not hasattr(self, "broll_placements"):
            return
        self._broll_refreshing = True
        try:
            media = self._selected_media()
            placements = self.store.get_broll_placements(media.id) if self.store and media else ()
            assets = {asset.id: asset for asset in self.store.list_broll_assets()} if self.store else {}
            self.broll_placements.setRowCount(len(placements))
            for row, placement in enumerate(placements):
                asset = assets.get(placement.asset_id)
                values = [asset.name if asset else f"Clip {placement.asset_id}",
                          self._caption_time(placement.timeline_start_ms),
                          self._caption_time(placement.source_start_ms),
                          self._caption_time(placement.source_end_ms), str(placement.track)]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    if column == 0:
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.broll_placements.setItem(row, column, cell)
            self.broll_placements.resizeColumnsToContents()
        finally:
            self._broll_refreshing = False
        self._broll_selection_changed()

    def add_broll_placement(self):
        media = self._selected_media()
        asset = self._selected_broll_asset()
        if not media or not self.store or not asset:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo de projet et un clip de la bibliothèque.")
            return
        if asset.missing:
            QMessageBox.warning(self, "Clip manquant", "Le fichier B-roll est introuvable à son emplacement enregistré.")
            return
        duration = asset.duration_ms
        if duration <= 0:
            QMessageBox.warning(self, "Durée invalide", "La durée du clip B-roll n'a pas pu être déterminée.")
            return
        source_in, accepted = QInputDialog.getInt(self, "Entrée source", "Image de départ (millisecondes)", 0, 0, duration - 1)
        if not accepted:
            return
        source_out, accepted = QInputDialog.getInt(self, "Sortie source", "Image de fin (millisecondes)", duration, source_in + 1, duration)
        if not accepted:
            return
        track, accepted = QInputDialog.getInt(self, "Piste B-roll", "Numéro de piste (0 est la piste principale)", 0, 0, 99)
        if not accepted:
            return
        placement = BrollPlacement(asset.id, max(0, self.player.position()), source_in, source_out, track)
        try:
            self.store.set_broll_placements(media.id, (*self.store.get_broll_placements(media.id), placement))
            self._populate_broll_placements()
        except BrollValidationError as exc:
            QMessageBox.warning(self, "Insertion B-roll impossible", str(exc))

    def delete_broll_placements(self):
        media = self._selected_media()
        rows = sorted(index.row() for index in self.broll_placements.selectionModel().selectedRows())
        if not media or not self.store or not rows:
            return
        placements = list(self.store.get_broll_placements(media.id))
        for row in reversed(rows):
            if 0 <= row < len(placements):
                del placements[row]
        self.store.set_broll_placements(media.id, placements)
        self._populate_broll_placements()

    def _broll_placement_cell_changed(self, _row: int, _column: int):
        if getattr(self, "_broll_refreshing", False):
            return
        media = self._selected_media()
        if not media or not self.store:
            return
        placements = list(self.store.get_broll_placements(media.id))
        try:
            updated = []
            for row, placement in enumerate(placements):
                start = parse_timestamp(self.broll_placements.item(row, 1).text(), format="vtt")
                source_in = parse_timestamp(self.broll_placements.item(row, 2).text(), format="vtt")
                source_out = parse_timestamp(self.broll_placements.item(row, 3).text(), format="vtt")
                track = int(self.broll_placements.item(row, 4).text())
                updated.append(BrollPlacement(placement.asset_id, start, source_in, source_out, track))
            self.store.set_broll_placements(media.id, updated)
            self._populate_broll_placements()
        except (ValueError, BrollValidationError) as exc:
            self._append_log(f"Modification du placement ignorée : {exc}")
            self._populate_broll_placements()

    def preview_broll_asset(self, *_args):
        asset = self._selected_broll_asset()
        if not asset:
            return
        if asset.missing:
            QMessageBox.warning(self, "Clip manquant", "Le fichier B-roll est introuvable.")
            return
        self.player.setSource(QUrl.fromLocalFile(str(asset.path)))
        self.player_status.setText(f"Prévisualisation B-roll : {asset.name}")
        self.player.play()
        self.play_button.setText("Pause")

    def _playback_error(self, error, error_string: str):
        if error != QMediaPlayer.Error.NoError:
            self.player_status.setText(f"Lecture indisponible : {error_string}")
            self._append_log(f"Erreur de lecture : {error_string}")

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_button.setText("Lecture")
        else:
            self.player.play()
            self.play_button.setText("Pause")

    def _aspect_changed(self, value: str):
        dimensions = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
        width, height = dimensions[value]
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)

    def _visual_settings(self, media: ProjectMedia) -> VisualSettings:
        return VisualSettings(
            aspect=self.aspect.currentText(), width=self.width_spin.value(), height=self.height_spin.value(),
            sharpening=self.sharpness.value(),
            framing_keyframes=self.store.get_framing_keyframes(media.id) if self.store else (),
            sharpening_enabled=self.sharpening_enabled.isChecked(),
            sharpening_radius=self.sharpening_radius.value(),
            sharpening_threshold=self.sharpening_threshold.value(),
        )

    def _populate_visual_settings(self):
        media = self._selected_media()
        if not self.store or not media:
            return
        settings = self.store.get_visual_settings(media.id)
        self._visual_refreshing = True
        try:
            self.aspect.setCurrentText(settings.aspect)
            self.width_spin.setValue(settings.width)
            self.height_spin.setValue(settings.height)
            self.sharpness.setValue(settings.sharpening)
            self.sharpening_enabled.setChecked(settings.sharpening_enabled)
            self.sharpening_radius.setValue(settings.sharpening_radius)
            self.sharpening_threshold.setValue(settings.sharpening_threshold)
        finally:
            self._visual_refreshing = False

    def _visual_settings_changed(self, *_args):
        media = self._selected_media()
        if self._visual_refreshing or not self.store or not media:
            return
        try:
            self.store.set_visual_settings(media.id, self._visual_settings(media))
        except ValueError:
            # Les champs de format et de résolution peuvent être temporairement incohérents
            # pendant leur édition ; la prochaine modification valide sera enregistrée.
            return

    def reset_sharpening(self):
        self.sharpening_enabled.setChecked(True)
        self.sharpness.setValue(0.35)
        self.sharpening_radius.setValue(5)
        self.sharpening_threshold.setValue(0)

    def compare_sharpening(self):
        media = self._selected_media()
        if not media or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo avant de comparer la netteté.")
            return
        if media.missing:
            QMessageBox.warning(self, "Fichier manquant", "Le média source est introuvable.")
            return
        try:
            settings = self._visual_settings(media)
            settings.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Paramètres invalides", str(exc))
            return
        timestamp = max(0, self.player.position())

        def operation(progress, cancel_event):
            progress("Extraction de l'image avant/après…")
            before = extract_preview_frame(media.path, settings, timestamp, sharpening=False,
                                           cancel_event=cancel_event)
            after = extract_preview_frame(media.path, settings, timestamp, sharpening=True,
                                          cancel_event=cancel_event)
            return before, after, timestamp

        def show_comparison(result):
            before_data, after_data, at_ms = result
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Comparaison de netteté — {self._caption_time(at_ms)}")
            dialog.resize(1100, 700)
            layout = QVBoxLayout(dialog)
            images = QHBoxLayout()
            for title, data in (("Sans netteté", before_data), ("Avec netteté", after_data)):
                column = QVBoxLayout()
                column.addWidget(QLabel(title))
                image_label = QLabel()
                image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image = QImage.fromData(data, "PNG")
                if image.isNull():
                    raise RuntimeError("L'image de comparaison est illisible.")
                pixmap = QPixmap.fromImage(image).scaled(
                    520, 580, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
                )
                image_label.setPixmap(pixmap)
                column.addWidget(image_label)
                images.addLayout(column)
            layout.addLayout(images)
            dialog.exec()

        self._run_task(operation, show_comparison)

    def export_selected(self, _checked=False, *, target_stage=None, force=False):
        item = self._selected_media()
        if not item or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo à traiter.")
            return
        try:
            # Les valeurs affichées font foi : elles sont validées et enregistrées avant le rendu.
            self.store.set_visual_settings(item.id, self._visual_settings(item))
        except ValueError as exc:
            QMessageBox.warning(self, "Paramètres visuels invalides", str(exc))
            return
        try:
            job = collect_job(self.store, item)
        except PipelineInputError as exc:
            QMessageBox.warning(self, "Configuration incomplète", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Paramètres invalides", str(exc))
            return
        destination = None
        if target_stage is None:
            destination, _ = QFileDialog.getSaveFileName(
                self, "Exporter le rendu", str(self.store.root / "exports" / f"{item.path.stem}_cutweave.mp4"),
                "Vidéo MP4 (*.mp4)",
            )
            if not destination:
                return
            if Path(destination).suffix.lower() != ".mp4":
                destination += ".mp4"
            if Path(destination).resolve() == item.path.resolve():
                QMessageBox.warning(self, "Chemin invalide", "Le fichier source ne peut pas être remplacé.")
                return
        if job.generated_broll or job.generated_sfx:
            self._append_log(f"Règles appliquées : {job.generated_broll} B-roll, {job.generated_sfx} SFX générés.")
        store = self.store
        self.cancel_event = threading.Event()
        forced = ("visual", "composition", "finishing", "render") if force else ()

        def operation(progress, cancel_event):
            return run_job(store, job, target_stage=target_stage, destination=destination,
                           cancel_event=cancel_event, on_progress=progress, force=forced)

        if target_stage is None:
            success = lambda result: self._append_log(f"Pipeline terminé, export vérifié : {result}")
        else:
            success = lambda result: self._append_log(f"{STAGE_LABELS[target_stage]} terminée : {result}")
        self._run_task(operation, success)

    def _run_pipeline_from_panel(self, target_stage):
        self.export_selected(target_stage=target_stage, force=self.pipeline_panel.force_check.isChecked())

    def _on_progress(self, event):
        if self.studio.handle_progress(event):
            if event.fraction is not None:
                if self.progress.maximum() == 0:
                    self.progress.setRange(0, 100)
                self.progress.setValue(round(event.fraction * 100))
            if event.message:
                self.stage_label.setText(f"Montage auto — {event.message}")
                self._append_log(event.message)
            return
        if isinstance(event, ProgressEvent):
            label = STAGE_LABELS.get(event.stage, event.stage)
            if event.fraction is not None:
                if self.progress.maximum() == 0:
                    self.progress.setRange(0, 100)
                self.progress.setValue(round(event.fraction * 100))
                self.stage_label.setText(f"{label} — {event.fraction:.0%}")
            if event.message:
                self.stage_label.setText(label)
                self._append_log(f"{label} : {event.message}")
            return
        self._append_log(str(event))

    def _seek_to(self, time_ms: int):
        self.player.setPosition(max(0, int(time_ms)))

    def add_mask_from_trajectory(self):
        media = self._selected_media()
        if not media or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo avant de créer un masque.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Trajectoire à suivre par le masque", str(self.store.root / "tracking"), "Trajectoire CUTWEAVE (*.json)",
        )
        if not path:
            return
        effect, accepted = QInputDialog.getItem(self, "Effet du masque", "Effet", ["blur", "opaque"], 0, False)
        if not accepted:
            return
        try:
            trajectory = load_trajectory(path)
            if trajectory.source_name.casefold() != media.path.name.casefold():
                raise ValueError("La trajectoire a été créée depuis un autre fichier source.")
            settings = self._visual_settings(media)
            keyframes = trajectory_to_mask_keyframes(
                trajectory, settings.width, settings.height, framing_keyframes=settings.framing_keyframes,
                padding=self.track_margin.value(), smoothing_window=self.track_smoothing.value(),
            )
            first = keyframes[0]
            mask = MaskRegion(first.x, first.y, first.width, first.height, 1.0,
                              start_ms=keyframes[0].time_ms, end_ms=keyframes[-1].time_ms + 1,
                              keyframes=keyframes, effect=effect, blur_radius=18)
            mask.validate(settings.width, settings.height)
            self.store.set_masks(media.id, (*self.store.get_masks(media.id), mask))
        except (TrackingError, MaskValidationError, ValueError, OSError) as exc:
            QMessageBox.warning(self, "Masque impossible", str(exc))
            return
        self._populate_masks()
        self._append_log(f"Masque {effect} créé depuis la trajectoire : {len(keyframes)} keyframe(s), corrigeables dans la table.")

    def close_project(self):
        if self.thread is not None:
            QMessageBox.information(self, "Traitement en cours", "Annulez ou attendez la fin du traitement avant de fermer.")
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self.store = None
        self.media = []
        self.project_label.setText("Aucun projet ouvert")
        self.sidebar_project.setText("Aucun projet")
        self.studio.stop_media()
        self.studio.refresh()
        self.studio.pages.setCurrentIndex(0)
        self._populate_table()
        self._populate_captions()
        self._populate_masks()
        self._populate_broll_assets()
        self._populate_broll_placements()
        self._populate_audio_assets()
        self._populate_sfx_placements()
        self.rules_panel.refresh()
        self.pipeline_panel.refresh()
        self._set_project_state(False)
        self._set_task_state(False)
        self._append_log("Projet fermé. Toutes les modifications étaient déjà enregistrées.")

    def relink_missing_media(self):
        if not self.store:
            return
        missing = [item for item in self.media if item.missing]
        if not missing:
            QMessageBox.information(self, "Aucun média manquant", "Tous les médias du projet sont accessibles.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Dossier contenant les médias déplacés")
        if not folder:
            return
        relinked, errors = 0, []
        candidates = {}
        for path in collect_video_files([folder]):
            candidates.setdefault(Path(path).name.casefold(), Path(path))
        for item in missing:
            candidate = candidates.get(item.name.casefold())
            if candidate is None:
                continue
            try:
                self.store.relink_media(item.id, candidate, probe_media(candidate))
                relinked += 1
            except (MediaProbeError, OSError, ValueError) as exc:
                errors.append(f"{item.name} : {exc}")
        self.media = self.store.list_media()
        self._populate_table()
        self._append_log(f"Médias reliés : {relinked} sur {len(missing)}.")
        for error in errors:
            self._append_log(error)

    def show_settings(self):
        dialog = SettingsDialog(self.app_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.app_settings = dialog.settings
        try:
            self.app_settings.save()
            self.app_settings.apply()
            log_file = configure_logging(self.app_settings.resolved_log_dir())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Paramètres non enregistrés", str(exc))
            return
        self._append_log(f"Paramètres enregistrés. FFmpeg : {ffmpeg_path() or 'introuvable'} ; "
                         f"FFprobe : {ffprobe_path() or 'introuvable'} ; journal : {log_file}")

    def show_task_history(self):
        if not self.store:
            QMessageBox.information(self, "Aucun projet", "Ouvrez un projet pour consulter son historique.")
            return
        TaskHistoryDialog(self.store, self).exec()

    def open_log_folder(self):
        folder = self.app_settings.resolved_log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def begin_tracking(self):
        media = self._selected_media()
        if not media or not self.store:
            QMessageBox.information(self, "Sélection requise", "Sélectionnez une vidéo avant de démarrer le suivi.")
            return
        if media.missing:
            QMessageBox.warning(self, "Fichier manquant", "Le média source est introuvable.")
            return
        source = media.path
        time_ms = max(0, self.player.position())
        self._append_log(f"Lecture de l'image à {time_ms} ms pour la sélection manuelle…")
        self._run_task(
            lambda _progress, _cancel: extract_frame_at(source, time_ms),
            lambda result: setattr(self, "_pending_frame", (source, time_ms, result[0], result[1])),
        )

    def _show_tracking_roi(self):
        pending = self._pending_frame
        self._pending_frame = None
        if pending is None or self.store is None:
            return
        source, start_ms, frame, _metadata = pending
        dialog = RoiDialog(frame, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        bbox = dialog.bbox
        if bbox is None:
            QMessageBox.information(self, "Zone requise", "Tracez un rectangle autour de la cible avant de démarrer.")
            return
        method = self.tracker_combo.currentText()
        store = self.store
        output = store.root / "tracking" / f"{source.stem}_{method.lower()}_{start_ms}ms_{uuid.uuid4().hex[:8]}.json"
        task_id = store.record_task("classical_tracking", "running", input_path=source, output_path=output)
        cache = StageCache(store.cache_root)
        self.cancel_event = threading.Event()

        def operation(progress, cancel_event):
            try:
                key = cache.make_key(
                    "tracking", (("source", source),),
                    {"method": method, "bbox": bbox, "start_ms": start_ms, "source_name": source.name,
                     "frame_step": 1},
                    module_version="classical-tracking-v1",
                    cancel_event=cancel_event,
                )
                cached = cache.lookup("tracking", key, ".json")
                cache_output = cache.artifact_path("tracking", key, ".json")
                if cached is not None:
                    progress("Cache hit : trajectoire classique réutilisée.")
                    cache.copy_to(cached, output)
                    store.record_task("classical_tracking", "succeeded", output_path=output, task_id=task_id)
                    return output
                trajectory = track_region(
                    source, bbox, method=method, start_ms=start_ms,
                    cancel_event=cancel_event,
                    on_progress=lambda fraction: progress(f"Suivi : {fraction:.0%}"),
                )
                save_trajectory(trajectory, cache_output)
                if cancel_event.is_set():
                    raise RuntimeError("Tracking annulé.")
                cache.copy_to(cache_output, output)
                store.record_task("classical_tracking", "succeeded", output_path=output, task_id=task_id)
                return output
            except Exception as exc:
                status = "cancelled" if cancel_event.is_set() else "failed"
                store.record_task("classical_tracking", status, error=str(exc), task_id=task_id)
                raise

        self._run_task(operation, self._tracking_done)

    def _tracking_done(self, path):
        try:
            trajectory = load_trajectory(path)
        except TrackingError as exc:
            self._append_log(f"Trajectoire illisible : {exc}")
            return
        summary = tracking_summary(trajectory)
        message = (f"Trajectoire enregistrée : {path} — {summary['tracked']}/{summary['samples']} images suivies")
        if summary["lost"]:
            message += (f", {summary['lost']} perte(s) de suivi dès {summary['first_loss_ms']} ms. "
                        "Relancez une sélection à cet instant pour réinitialiser le tracker")
        self._append_log(message + ".")
        self._show_trajectory(trajectory)

    def view_trajectory(self):
        if not self.store:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Visualiser une trajectoire", str(self.store.root / "tracking"),
                                              "Trajectoire CUTWEAVE (*.json)")
        if not path:
            return
        try:
            self._show_trajectory(load_trajectory(path))
        except TrackingError as exc:
            QMessageBox.warning(self, "Trajectoire invalide", str(exc))

    def _show_trajectory(self, trajectory):
        media = next((item for item in self.media if item.name.casefold() == trajectory.source_name.casefold()), None)
        if media is None or media.missing:
            self._append_log("Visualisation impossible : média source de la trajectoire absent du projet.")
            return
        try:
            frame, _ = extract_frame_at(media.path, round(trajectory.start_frame * 1000 / trajectory.fps))
        except TrackingError as exc:
            self._append_log(f"Visualisation impossible : {exc}")
            return
        drawn = draw_trajectory(frame, trajectory)
        height, width = drawn.shape[:2]
        image = QImage(drawn.data, width, height, drawn.strides[0], QImage.Format.Format_BGR888).copy()
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Trajectoire {trajectory.method} — {trajectory.source_name}")
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setPixmap(QPixmap.fromImage(image).scaled(960, 600, Qt.AspectRatioMode.KeepAspectRatio,
                                                        Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(label)
        summary = tracking_summary(trajectory)
        layout.addWidget(QLabel(f"Cadre bleu : sélection initiale. Ligne verte : centres suivis. Croix rouges : "
                                f"pertes de suivi ({summary['lost']}). Suivi d'une zone choisie, sans reconnaissance."))
        dialog.exec()

    # --- API utilisée par le Studio et la bibliothèque ------------------------------------------

    def run_task(self, operation, on_success):
        self._run_task(operation, on_success)

    def notify(self, message: str):
        self._append_log(message)
        self.statusBar().showMessage(message, 9000)

    def ensure_project(self) -> bool:
        """Crée automatiquement un projet dans le dossier des projets si aucun n'est ouvert."""
        if self.store is not None:
            return True
        target = default_project_dir(self.app_settings.projects_dir)
        try:
            store = ProjectStore.create(target, name=target.name)
        except Exception as exc:
            QMessageBox.critical(self, "Création du projet impossible",
                                 f"{exc}\n\nChoisissez un dossier de projets dans Paramètres.")
            return False
        self._load_project(store)
        self.notify(f"Projet créé automatiquement : {target}")
        return True

    def import_paths(self, paths):
        if self.thread is not None:
            QMessageBox.information(self, "Traitement en cours", "Attendez la fin du traitement en cours.")
            return
        if self.ensure_project():
            self._start_import(paths)

    def media_changed(self):
        """Recharge les vues après une modification du projet faite hors de l'éditeur."""
        if not self.store:
            return
        selected = self._selected_media()
        self.media = self.store.list_media()
        self._populate_table()
        self._populate_audio_assets()
        if selected:
            self._select_media_id(selected.id)
        self._selection_changed()
        self.studio.refresh()

    def _select_media_id(self, media_id: int) -> bool:
        for row, item in enumerate(self.media):
            if item.id == media_id:
                self.table.selectRow(row)
                return True
        return False

    def open_in_editor(self, media_id: int):
        self.media = self.store.list_media() if self.store else []
        self._populate_table()
        if self._select_media_id(media_id):
            self.pages.setCurrentIndex(1)
            self.notify("Variante ouverte dans l'éditeur : modifie ce que tu veux puis relance le pipeline complet.")

    def cancel_task(self):
        if self.cancel_event:
            self.cancel_event.set()
            self._append_log("Annulation demandée…")

    def _run_task(self, operation, on_success):
        if self.thread is not None:
            return
        if self.cancel_event is None:
            self.cancel_event = threading.Event()
        self._success_handler = on_success
        self.thread = QThread(self)
        self.worker = TaskWorker(operation, self.cancel_event)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._handle_task_success)
        self.worker.failed.connect(self._handle_task_failure)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._task_finished)
        self._set_task_state(True)
        self.thread.start()

    def _handle_task_success(self, result):
        if self._success_handler:
            self._success_handler(result)

    def _handle_task_failure(self, message: str):
        if self.cancel_event is not None and self.cancel_event.is_set() and "annul" in message.lower():
            self._append_log("Traitement annulé.")
            return
        self._append_log(f"Erreur : {message}")
        QMessageBox.critical(self, "Opération impossible", message)

    def _task_finished(self):
        if self.worker:
            self.worker.deleteLater()
        if self.thread:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.cancel_event = None
        self._success_handler = None
        self._set_task_state(False)
        self.studio.task_finished()
        if self._pending_frame is not None:
            QTimer.singleShot(0, self._show_tracking_roi)

    def closeEvent(self, event):
        if self.cancel_event:
            self.cancel_event.set()
        if self.thread:
            self.thread.quit()
            self.thread.wait(35000)
        self.player.stop()
        self.player.setSource(QUrl())
        self.studio.stop_media()
        self.library_page.stop_media()
        logging.getLogger("cutweave").removeHandler(self._log_handler)
        event.accept()
