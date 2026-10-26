"""Studio : montage automatique en trois gestes (rushs → style → variantes)."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget,
    QVBoxLayout, QWidget,
)

from cutweave.assets.library import LOOKS, music_catalog
from cutweave.autoedit.modes import MODES, MODES_BY_KEY, VARIANTS
from cutweave.autoedit.service import (
    STAGE, StudioRequest, VariantResult, export_variant, generate_variants, list_variants, rush_media,
)
from cutweave.media.files import collect_video_files
from cutweave.media.thumbnails import ThumbnailError, create_video_thumbnail
from cutweave.ui import theme
from cutweave.ui.theme import set_role

DURATIONS = (("Auto (selon le style)", None), ("15 s", 15.0), ("30 s", 30.0), ("45 s", 45.0), ("1 min", 60.0),
             ("1 min 30", 90.0), ("3 min", 180.0))


def _label(text: str, role: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


def _button(text: str, variant: str | None = None, *, checkable: bool = False) -> QPushButton:
    button = QPushButton(text)
    if variant:
        button.setProperty("variant", variant)
    button.setCheckable(checkable)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _card(*, padding: int = 18, spacing: int = 12) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("card", "true")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(spacing)
    return frame, layout


def _rgba(color: str, alpha: float) -> str:
    value = QColor(color)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {int(alpha * 255)})"


def _rounded_pixmap(path: str | None, size: QSize, radius: int = 10, placeholder: str = "🎞") -> QPixmap:
    canvas = QPixmap(size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    painter.setClipPath(clip)
    source = QPixmap(path) if path and Path(path).is_file() else QPixmap()
    if source.isNull():
        painter.fillRect(canvas.rect(), QColor(theme.SURFACE_3))
        painter.setPen(QColor(theme.MUTED))
        font = QFont()
        font.setPointSize(max(12, size.height() // 5))
        painter.setFont(font)
        painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, placeholder)
    else:
        scaled = source.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


class ClickableCard(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("card", "true")
        self.setProperty("selected", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_selected(self, selected: bool):
        set_role(self, "selected", "true" if selected else "false")

    def enterEvent(self, event):
        if self.property("selected") != "true":
            set_role(self, "hover", "true")
        super().enterEvent(event)

    def leaveEvent(self, event):
        set_role(self, "hover", "false")
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class DropZone(QFrame):
    """Zone de dépôt des rushs : fichiers vidéo ou dossiers entiers, ou clic pour parcourir."""

    filesDropped = Signal(list)
    browseRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(170)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(6)
        layout.addStretch()
        icon = QLabel("⇪")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(56, 56)
        icon.setStyleSheet(f"background: {_rgba(theme.ACCENT, 0.18)}; color: {theme.ACCENT}; border-radius: 28px;"
                           "font-size: 22pt; font-weight: 700;")
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(icon)
        row.addStretch()
        layout.addLayout(row)
        self.title = _label("Glisse tes rushs ici", "h2")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        hint = _label("Vidéos ou dossiers entiers · MP4, MOV, MKV, AVI… · ou clique pour parcourir", "muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        layout.addStretch()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.browseRequested.emit()
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            set_role(self, "active", "true")

    def dragLeaveEvent(self, event):
        set_role(self, "active", "false")

    def dropEvent(self, event):
        set_role(self, "active", "false")
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()


class ModeCard(ClickableCard):
    def __init__(self, mode, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.setMinimumHeight(132)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        top = QHBoxLayout()
        glyph = QLabel(mode.glyph)
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setFixedSize(40, 40)
        glyph.setStyleSheet(f"background: {_rgba(mode.accent, 0.18)}; border-radius: 12px; font-size: 16pt;"
                            f"color: {mode.accent};")
        top.addWidget(glyph)
        title = _label(mode.title, "h3")
        top.addWidget(title, 1)
        layout.addLayout(top)
        layout.addWidget(_label(mode.tagline, "muted", wrap=True), 1)


class Segmented(QWidget):
    changed = Signal(object)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._values = []
        for index, (text, value) in enumerate(options):
            button = _button(text, "segment", checkable=True)
            self.group.addButton(button, index)
            layout.addWidget(button)
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


class VariantCard(ClickableCard):
    def __init__(self, result: VariantResult, parent=None):
        super().__init__(parent)
        self.result = result
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 14, 12)
        layout.setSpacing(14)
        portrait = result.aspect == "9:16"
        size = QSize(72, 128) if portrait else QSize(128, 72) if result.aspect == "16:9" else QSize(96, 96)
        thumb = QLabel()
        thumb.setFixedSize(size)
        thumb.setPixmap(_rounded_pixmap(result.thumbnail, size))
        layout.addWidget(thumb)
        text = QVBoxLayout()
        text.setSpacing(4)
        head = QHBoxLayout()
        badge = _label(result.letter, "badge")
        badge.setFixedSize(26, 26)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(badge)
        mode = MODES_BY_KEY.get(result.mode)
        head.addWidget(_label(f"{result.name}", "h3"))
        head.addStretch()
        text.addLayout(head)
        text.addWidget(_label(f"{mode.title if mode else result.mode} · {result.blurb}", "muted", wrap=True))
        chips = QHBoxLayout()
        chips.setSpacing(6)
        minutes, seconds = divmod(int(round(result.duration)), 60)
        chips_list = [f"{minutes}:{seconds:02d}", f"{result.shots} plans", f"♪ {result.music_title}", result.look_title]
        if result.draft:
            chips_list.append("⚡ aperçu 540p")
        for chip in chips_list:
            chips.addWidget(_label(chip, "chip"))
        chips.addStretch()
        text.addLayout(chips)
        text.addStretch()
        layout.addLayout(text, 1)


class StudioPage(QWidget):
    """Écran principal : l'utilisateur dépose ses rushs, choisit un style et reçoit des variantes."""

    thumbnailsReady = Signal()

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self._thumb_thread: threading.Thread | None = None
        self.thumbnailsReady.connect(self.refresh)
        self.setObjectName("page")
        self.main = main
        self.selected_mode = MODES[0].key
        self.results: list[VariantResult] = []
        self.current: VariantResult | None = None
        self._custom_music: str | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        root.addWidget(self.pages)
        self.pages.addWidget(self._build_create())
        self.pages.addWidget(self._build_progress())
        self.pages.addWidget(self._build_results())
        self._select_mode(self.selected_mode)
        self._update_cta()

    # --- Construction ---------------------------------------------------------------------------

    def _scroll(self, body: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        return scroll

    def _build_create(self) -> QWidget:
        body = QWidget()
        body.setObjectName("scrollBody")
        outer = QHBoxLayout(body)
        outer.setContentsMargins(36, 28, 36, 36)
        column = QVBoxLayout()
        column.setSpacing(18)
        outer.addStretch(1)
        outer.addLayout(column, 12)
        outer.addStretch(1)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("Nouveau montage", "h1"))
        self.project_hint = _label("Dépose tes rushs, choisis un style : CUTWEAVE monte tout seul, en local.", "muted")
        titles.addWidget(self.project_hint)
        header.addLayout(titles, 1)
        self.history_button = _button("Mes variantes →", "ghost")
        self.history_button.clicked.connect(lambda: self.pages.setCurrentIndex(2))
        self.history_button.setVisible(False)
        header.addWidget(self.history_button, 0, Qt.AlignmentFlag.AlignBottom)
        column.addLayout(header)

        # 1 · Rushs
        column.addWidget(_label("ÉTAPE 1 · TES RUSHS", "step"))
        self.drop_zone = DropZone()
        self.drop_zone.filesDropped.connect(self.add_paths)
        self.drop_zone.browseRequested.connect(self.browse_rushes)
        column.addWidget(self.drop_zone)
        self.rush_panel, rush_layout = _card(padding=14, spacing=8)
        rush_head = QHBoxLayout()
        self.rush_count = _label("", "h3")
        rush_head.addWidget(self.rush_count, 1)
        add_more = _button("＋ Ajouter")
        add_more.clicked.connect(self.browse_rushes)
        rush_head.addWidget(add_more)
        add_folder = _button("Dossier…")
        add_folder.clicked.connect(self.browse_folder)
        rush_head.addWidget(add_folder)
        rush_layout.addLayout(rush_head)
        self.rush_list = QListWidget()
        self.rush_list.setObjectName("thumbs")
        self.rush_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.rush_list.setFlow(QListWidget.Flow.LeftToRight)
        self.rush_list.setWrapping(False)
        self.rush_list.setIconSize(QSize(144, 81))
        self.rush_list.setFixedHeight(142)
        self.rush_list.setMovement(QListWidget.Movement.Static)
        self.rush_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.rush_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.rush_list.setAcceptDrops(False)
        rush_layout.addWidget(self.rush_list)
        hint = _label("Astuce : sélectionne des rushs et appuie sur Suppr pour les écarter du montage "
                      "(les fichiers d'origine ne sont jamais modifiés).", "faint", wrap=True)
        rush_layout.addWidget(hint)
        self.rush_panel.setVisible(False)
        column.addWidget(self.rush_panel)

        # 2 · Style
        column.addWidget(_label("ÉTAPE 2 · LE STYLE", "step"))
        grid = QGridLayout()
        grid.setSpacing(12)
        self.mode_cards: dict[str, ModeCard] = {}
        for index, mode in enumerate(MODES):
            card = ModeCard(mode)
            card.clicked.connect(lambda key=mode.key: self._select_mode(key))
            grid.addWidget(card, index // 3, index % 3)
            self.mode_cards[mode.key] = card
        column.addLayout(grid)

        # 3 · Réglages
        column.addWidget(_label("ÉTAPE 3 · LES RÉGLAGES (FACULTATIF)", "step"))
        settings, settings_layout = _card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.aspect = Segmented((("▯  9:16", "9:16"), ("▭  16:9", "16:9"), ("□  1:1", "1:1")))
        self.duration = QComboBox()
        for text, value in DURATIONS:
            self.duration.addItem(text, value)
        self.music = QComboBox()
        self.music.addItem("✦ Auto (selon le style)", "auto")
        for track in music_catalog():
            self.music.addItem(f"♪ {track.title} — {track.mood} · {track.bpm:g} BPM", track.key)
        self.music.addItem("Sans musique", "none")
        self.music.addItem("Ma musique…", "custom")
        self.music.activated.connect(self._music_activated)
        self.look = QComboBox()
        self.look.addItem("✦ Auto (selon le style)", None)
        for look in LOOKS:
            self.look.addItem(f"{look.title} — {look.description}", look.key)
        self.fit = Segmented((("Remplir le cadre", "fill"), ("Fond flou", "fit_blur")))
        self.variant_count = Segmented((("1", 1), ("2", 2), ("3", 3)))
        self.variant_count.set_value(3)
        self.variant_count.changed.connect(lambda _value: self._update_cta())
        self.quality = Segmented((("1080p", 1080), ("720p", 720), ("4K", 2160)))
        self.quality.set_value(1080)
        self.preview_mode = Segmented((("⚡ Aperçus rapides", True), ("Qualité finale", False)))
        self.preview_mode.setToolTip("Aperçus rapides : les variantes sont montées en 540p (plusieurs fois plus vite) ; "
                                     "la variante choisie est refaite à la qualité d'export au moment d'exporter.")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Ex. : Week-end à Lisbonne")
        self.title_edit.setMaxLength(80)
        self.outro_edit = QLineEdit()
        self.outro_edit.setPlaceholderText("Ex. : Abonne-toi !")
        self.outro_edit.setMaxLength(80)
        rows = (
            ("Format", self.aspect, "Durée", self.duration),
            ("Musique", self.music, "Look", self.look),
            ("Cadrage", self.fit, "Variantes", self.variant_count),
            ("Titre d'ouverture", self.title_edit, "Texte de fin", self.outro_edit),
            ("Aperçu", self.preview_mode, "Qualité d'export", self.quality),
        )
        for row, (label_a, widget_a, label_b, widget_b) in enumerate(rows):
            grid.addWidget(_label(label_a, "muted"), row, 0)
            grid.addWidget(widget_a, row, 1)
            if label_b:
                grid.addWidget(_label(label_b, "muted"), row, 2)
                grid.addWidget(widget_b, row, 3)
        settings_layout.addLayout(grid)
        column.addWidget(settings)

        cta_row = QHBoxLayout()
        self.cta_hint = _label("", "muted", wrap=True)
        cta_row.addWidget(self.cta_hint, 1)
        self.generate_button = _button("✦  Générer le montage", "cta")
        self.generate_button.clicked.connect(self.generate)
        cta_row.addWidget(self.generate_button)
        column.addLayout(cta_row)
        column.addStretch()
        return self._scroll(body)

    def _build_progress(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(page)
        outer.addStretch(2)
        row = QHBoxLayout()
        row.addStretch(1)
        card, layout = _card(padding=32, spacing=14)
        card.setMinimumWidth(520)
        card.setMaximumWidth(640)
        glyph = QLabel("✦")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setStyleSheet(f"font-size: 30pt; color: {theme.ACCENT};")
        layout.addWidget(glyph)
        self.progress_title = _label("Montage en cours…", "h2")
        self.progress_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_title)
        self.progress_message = _label("Préparation", "muted", wrap=True)
        self.progress_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_message)
        self.progress_bar = QProgressBar()
        self.progress_bar.setProperty("size", "large")
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        self.progress_steps = _label("", "faint", wrap=True)
        self.progress_steps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_steps)
        cancel_row = QHBoxLayout()
        cancel_row.addStretch()
        self.cancel_button = _button("Annuler", "danger")
        self.cancel_button.clicked.connect(self.main.cancel_task)
        cancel_row.addWidget(self.cancel_button)
        cancel_row.addStretch()
        layout.addLayout(cancel_row)
        row.addWidget(card, 2)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)
        return page

    def _build_results(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(16)
        header = QHBoxLayout()
        back = _button("←  Nouveau montage", "ghost")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        header.addWidget(back)
        header.addStretch()
        titles = QVBoxLayout()
        self.results_title = _label("Tes variantes", "h1")
        self.results_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titles.addWidget(self.results_title)
        subtitle = _label("Choisis ta préférée, exporte-la ou peaufine-la dans l'éditeur.", "muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.regenerate_button = _button("↻  Régénérer", "ghost")
        self.regenerate_button.clicked.connect(self.generate)
        header.addWidget(self.regenerate_button)
        layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(20)
        player = QFrame()
        player.setObjectName("playerFrame")
        player_layout = QVBoxLayout(player)
        player_layout.setContentsMargins(10, 10, 10, 12)
        self.video = QVideoWidget()
        self.video.setMinimumSize(360, 320)
        self.video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        player_layout.addWidget(self.video, 1)
        transport = QHBoxLayout()
        self.play_button = _button("▶", "ghost")
        self.play_button.setFixedWidth(44)
        self.play_button.clicked.connect(self.toggle_play)
        transport.addWidget(self.play_button)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        transport.addWidget(self.seek, 1)
        self.time_label = _label("0:00 / 0:00", "faint")
        transport.addWidget(self.time_label)
        player_layout.addLayout(transport)
        body.addWidget(player, 3)

        side = QVBoxLayout()
        side.setSpacing(10)
        self.variant_list = QVBoxLayout()
        self.variant_list.setSpacing(10)
        side.addLayout(self.variant_list)
        side.addStretch()
        actions, actions_layout = _card(padding=14, spacing=10)
        self.selection_label = _label("Aucune variante sélectionnée", "h3")
        actions_layout.addWidget(self.selection_label)
        self.export_button = _button("⤓  Exporter la vidéo…", "primary")
        self.export_button.clicked.connect(self.export_current)
        actions_layout.addWidget(self.export_button)
        self.refine_button = _button("✎  Peaufiner dans l'éditeur")
        self.refine_button.clicked.connect(self.refine_current)
        actions_layout.addWidget(self.refine_button)
        actions_layout.addWidget(_label("Dans l'éditeur, tout reste modifiable : titres, musique, volume, SFX, "
                                        "cadrage, masques, B-roll…", "faint", wrap=True))
        side.addWidget(actions)
        side_widget = QWidget()
        side_widget.setLayout(side)
        side_widget.setMinimumWidth(380)
        side_widget.setMaximumWidth(520)
        body.addWidget(side_widget, 2)
        layout.addLayout(body, 1)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(lambda duration: self.seek.setRange(0, duration))
        self.player.playbackStateChanged.connect(self._playback_state)
        self.seek.sliderMoved.connect(self.player.setPosition)
        return page

    # --- État ---------------------------------------------------------------------------------

    def _select_mode(self, key: str):
        self.selected_mode = key
        for mode_key, card in self.mode_cards.items():
            card.set_selected(mode_key == key)
        self._update_cta()

    def _music_activated(self, index: int):
        if self.music.itemData(index) != "custom":
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choisir une musique", "",
                                              "Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus);;Tous les fichiers (*.*)")
        if not path:
            self.music.setCurrentIndex(0)
            return
        self._custom_music = path
        existing = self.music.findData(path)
        if existing < 0:
            self.music.insertItem(self.music.count() - 1, f"♪ {Path(path).name} (tempo détecté)", path)
            existing = self.music.findData(path)
        self.music.setCurrentIndex(existing)

    def _rushes(self):
        store = self.main.store
        return rush_media(store) if store else []

    def _update_cta(self):
        rushes = [media for media in self._rushes() if not media.missing]
        mode = MODES_BY_KEY[self.selected_mode]
        count = self.variant_count.value() if hasattr(self, "variant_count") else 3
        if hasattr(self, "generate_button"):
            self.generate_button.setText(f"✦  Générer {count} variante{'s' if count > 1 else ''}")
            busy = self.main.thread is not None
            self.generate_button.setEnabled(bool(rushes) and not busy)
            self.regenerate_button.setEnabled(bool(rushes) and not busy)
            if not rushes:
                self.cta_hint.setText("Ajoute au moins un rush pour lancer le montage.")
            else:
                total = sum(media.duration or 0 for media in rushes)
                self.cta_hint.setText(f"{len(rushes)} rush{'s' if len(rushes) > 1 else ''} · "
                                      f"{int(total // 60)} min {int(total % 60):02d} de matière · style {mode.title}"
                                      " · 100 % local, sans IA")

    def refresh(self):
        store = self.main.store
        self.project_hint.setText(
            f"Projet « {store.manifest['name']} » · Dépose tes rushs, choisis un style." if store
            else "Dépose tes rushs, choisis un style : CUTWEAVE monte tout seul, en local.")
        self.rush_list.clear()
        rushes = self._rushes()
        for media in rushes:
            thumb = str(store.cache_root / "thumbnails" / f"rush-{media.id}.jpg") if store else None
            duration = f"{int((media.duration or 0) // 60)}:{int((media.duration or 0) % 60):02d}"
            item = QListWidgetItem(QIcon(_rounded_pixmap(thumb, QSize(144, 81), 8)),
                                   f"{media.name[:20]}{'…' if len(media.name) > 20 else ''}\n{duration}"
                                   + ("  ⚠ manquant" if media.missing else ""))
            item.setData(Qt.ItemDataRole.UserRole, media.id)
            item.setToolTip(str(media.path))
            item.setSizeHint(QSize(160, 120))
            self.rush_list.addItem(item)
        self.rush_panel.setVisible(bool(rushes))
        self._ensure_thumbnails(store, rushes)
        self.drop_zone.setMinimumHeight(110 if rushes else 170)
        self.drop_zone.title.setText("Ajoute d'autres rushs" if rushes else "Glisse tes rushs ici")
        self.rush_count.setText(f"{len(rushes)} rush{'s' if len(rushes) > 1 else ''} prêt{'s' if len(rushes) > 1 else ''}")
        self.results = list_variants(store) if store else []
        self.history_button.setVisible(bool(self.results))
        self._populate_results()
        self._update_cta()

    def _ensure_thumbnails(self, store, rushes):
        """Miniatures manquantes (projets anciens) créées en arrière-plan, sans bloquer l'interface."""
        if store is None or (self._thumb_thread is not None and self._thumb_thread.is_alive()):
            return
        todo = [(media.path, store.cache_root / "thumbnails" / f"rush-{media.id}.jpg", media.duration or 0)
                for media in rushes if not media.missing]
        todo = [item for item in todo if not item[1].is_file()]
        if not todo:
            return

        def work():
            created = False
            for source, target, duration in todo:
                try:
                    create_video_thumbnail(source, target, time_ms=round(min(duration * 0.3, 5.0) * 1000),
                                           width=288, height=162)
                    created = True
                except (ThumbnailError, OSError, ValueError):
                    continue
            if created:
                self.thumbnailsReady.emit()

        self._thumb_thread = threading.Thread(target=work, daemon=True)
        self._thumb_thread.start()

    def show_results_if_any(self):
        self.pages.setCurrentIndex(2 if self.results else 0)

    def set_busy(self, busy: bool):
        self._update_cta()
        self.export_button.setEnabled(not busy and self.current is not None)
        self.refine_button.setEnabled(not busy and self.current is not None)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.rush_list.hasFocus():
            self.remove_selected_rushes()
            return
        super().keyPressEvent(event)

    # --- Rushs --------------------------------------------------------------------------------

    def browse_rushes(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Ajouter des rushs", "",
            "Vidéos (*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mpeg *.mpg *.mts *.m2ts);;Tous les fichiers (*.*)")
        if paths:
            self.add_paths(paths)

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Ajouter un dossier de rushs")
        if path:
            self.add_paths([path])

    def add_paths(self, paths):
        try:
            videos = collect_video_files(paths)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import impossible", str(exc))
            return
        if not videos:
            QMessageBox.information(self, "Aucune vidéo", "Aucun fichier vidéo pris en charge n'a été trouvé.")
            return
        self.main.import_paths(videos)

    def remove_selected_rushes(self):
        store = self.main.store
        ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.rush_list.selectedItems()}
        if not store or not ids or self.main.thread is not None:
            return
        store.remove_media(ids)
        self.main.media_changed()

    # --- Génération ---------------------------------------------------------------------------

    def build_request(self) -> StudioRequest:
        music = self.music.currentData()
        if music == "custom":
            music = self._custom_music or "auto"
        rushes = tuple(str(media.path) for media in self._rushes() if not media.missing)
        return StudioRequest(
            rushes=rushes, mode=self.selected_mode, aspect=self.aspect.value(), quality=self.quality.value(),
            duration=self.duration.currentData(), music=music, look=self.look.currentData(), fit=self.fit.value(),
            title=self.title_edit.text().strip(), outro=self.outro_edit.text().strip(),
            variants=self.variant_count.value(), draft=bool(self.preview_mode.value()),
        )

    def generate(self):
        if self.main.store is None or self.main.thread is not None:
            return
        request = self.build_request()
        if not request.rushes:
            QMessageBox.information(self, "Rushs requis", "Ajoute au moins un rush disponible avant de générer.")
            return
        self.player.stop()
        self.player.setSource(QUrl())
        store = self.main.store
        mode = MODES_BY_KEY[request.mode]
        self.progress_title.setText(f"Montage « {mode.title} » en cours…")
        self.progress_message.setText("Analyse des rushs (mouvement, son, netteté)…")
        steps = ["Analyse"] + [f"Variante {variant.letter}" for variant in VARIANTS[: request.variants]]
        self.progress_steps.setText("  ·  ".join(steps))
        self.progress_bar.setValue(0)
        self.pages.setCurrentIndex(1)
        self.main.cancel_event = threading.Event()

        def operation(progress, cancel_event):
            return generate_variants(store, request, on_progress=progress, cancel_event=cancel_event)

        self.main.run_task(operation, self._generation_done)

    def handle_progress(self, event) -> bool:
        if getattr(event, "stage", None) != STAGE:
            return False
        if event.fraction is not None:
            self.progress_bar.setValue(round(event.fraction * 1000))
        if event.message:
            self.progress_message.setText(event.message)
        return True

    def _generation_done(self, results):
        self.main.media_changed()
        self.results = list_variants(self.main.store)
        self._populate_results(select=results[0].key if results else None)
        self.pages.setCurrentIndex(2)
        self.main.notify(f"{len(results)} variante(s) prête(s) — choisis ta préférée.")

    def task_finished(self):
        if self.pages.currentIndex() == 1:
            self.pages.setCurrentIndex(2 if self.results else 0)
        self.set_busy(False)

    # --- Résultats ----------------------------------------------------------------------------

    def _populate_results(self, select: str | None = None):
        while self.variant_list.count():
            item = self.variant_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards: dict[str, VariantCard] = {}
        latest = self.results[0].created_at if self.results else None
        recent = [result for result in self.results if result.created_at == latest]
        recent.sort(key=lambda result: result.letter)
        for result in recent:
            card = VariantCard(result)
            card.clicked.connect(lambda r=result: self.select_variant(r))
            self.variant_list.addWidget(card)
            self.cards[result.key] = card
        older = [result for result in self.results if result.created_at != latest]
        if older:
            self.variant_list.addWidget(_label(f"{len(older)} variante(s) plus ancienne(s) disponible(s) dans "
                                               "l'éditeur (dossier « montages » du projet).", "faint", wrap=True))
        if recent:
            target = next((r for r in recent if r.key == select), recent[0])
            self.select_variant(target, autoplay=select is not None)
        else:
            self.current = None
            self.selection_label.setText("Aucune variante pour l'instant")
            self.set_busy(self.main.thread is not None)

    def select_variant(self, result: VariantResult, autoplay: bool = True):
        self.current = result
        for key, card in self.cards.items():
            card.set_selected(key == result.key)
        mode = MODES_BY_KEY.get(result.mode)
        self.selection_label.setText(f"Variante {result.letter} · {result.name}"
                                     + (f" — {mode.title}" if mode else ""))
        self.results_title.setText(f"Tes variantes {mode.title if mode else ''}".strip())
        path = Path(result.preview)
        if path.is_file():
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            if autoplay:
                self.player.play()
        self.set_busy(self.main.thread is not None)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _playback_state(self, state):
        self.play_button.setText("❚❚" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

    def _position_changed(self, position: int):
        self.seek.setValue(position)
        duration = self.player.duration()
        self.time_label.setText(f"{position // 60000}:{position // 1000 % 60:02d} / "
                                f"{duration // 60000}:{duration // 1000 % 60:02d}")

    def export_current(self):
        result = self.current
        store = self.main.store
        if result is None or store is None or self.main.thread is not None:
            return
        default = Path.home() / "Videos" / f"{store.manifest['name']} - {result.key}.mp4"
        destination, _ = QFileDialog.getSaveFileName(self, "Exporter la vidéo", str(default), "Vidéo MP4 (*.mp4)")
        if not destination:
            return
        if Path(destination).suffix.lower() != ".mp4":
            destination += ".mp4"
        if result.draft:
            self.main.release_media()
        else:
            self.player.pause()
        self.main.cancel_event = threading.Event()

        def operation(progress, cancel_event):
            return export_variant(store, result, destination, on_progress=progress, cancel_event=cancel_event)

        def done(path):
            self.main.notify(f"Vidéo exportée : {path}")
            if result.draft:
                self.main.media_changed()

        self.main.run_task(operation, done)

    def refine_current(self):
        if self.current is not None:
            self.player.pause()
            self.main.open_in_editor(self.current.media_id)

    def stop_media(self):
        self.player.stop()
        self.player.setSource(QUrl())
