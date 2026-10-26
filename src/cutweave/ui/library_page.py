"""Bibliothèque d'actifs intégrés : écoute des musiques et SFX, looks et styles de titres."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from cutweave.assets import library
from cutweave.media.probe import MediaProbeError, probe_audio
from cutweave.stages.finishing.caption_presets import BUILTIN_CAPTION_PRESETS
from cutweave.ui.studio import _button, _card, _label, _rgba


class LibraryPage(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.main = main
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self._playing: str | None = None
        self.player.playbackStateChanged.connect(self._state_changed)
        self.play_buttons: dict[str, QPushButton] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("scrollBody")
        scroll.setWidget(body)
        row = QHBoxLayout(body)
        row.setContentsMargins(36, 28, 36, 36)
        column = QVBoxLayout()
        column.setSpacing(16)
        row.addStretch(1)
        row.addLayout(column, 12)
        row.addStretch(1)

        column.addWidget(_label("Bibliothèque", "h1"))
        column.addWidget(_label("Musiques, effets sonores, looks et styles de titres intégrés. Tout est généré "
                                "localement sur ta machine : libre de droits, aucun téléchargement.", "muted", wrap=True))
        status_row = QHBoxLayout()
        self.status = _label("", "faint", wrap=True)
        status_row.addWidget(self.status, 1)
        self.generate_button = _button("Préparer la bibliothèque audio", "primary")
        self.generate_button.clicked.connect(self.prepare_assets)
        status_row.addWidget(self.generate_button)
        column.addLayout(status_row)

        column.addWidget(_label("MUSIQUES", "step"))
        music_grid = QGridLayout()
        music_grid.setSpacing(12)
        for index, track in enumerate(library.music_catalog()):
            card, layout = _card(padding=16, spacing=6)
            head = QHBoxLayout()
            head.addWidget(_label(f"♪  {track.title}", "h3"), 1)
            head.addWidget(_label(f"{track.bpm:g} BPM", "chip"))
            layout.addLayout(head)
            layout.addWidget(_label(f"{track.mood} · {int(track.duration_s)} s en boucle", "muted"))
            layout.addWidget(_label(track.description, "faint", wrap=True))
            actions = QHBoxLayout()
            play = _button("▶  Écouter")
            play.clicked.connect(lambda _c=False, t=track: self.toggle(f"music:{t.key}"))
            self.play_buttons[f"music:{track.key}"] = play
            actions.addWidget(play)
            add = _button("＋ Projet", "ghost")
            add.setToolTip("Ajoute ce morceau à la bibliothèque audio du projet (onglet Musique & SFX).")
            add.clicked.connect(lambda _c=False, t=track: self.add_to_project("music", t.key))
            actions.addWidget(add)
            actions.addStretch()
            layout.addLayout(actions)
            music_grid.addWidget(card, index // 3, index % 3)
        column.addLayout(music_grid)

        column.addWidget(_label("EFFETS SONORES", "step"))
        sfx_grid = QGridLayout()
        sfx_grid.setSpacing(10)
        for index, effect in enumerate(library.sfx_catalog()):
            card, layout = _card(padding=12, spacing=4)
            head = QHBoxLayout()
            head.addWidget(_label(effect.title, "h3"), 1)
            head.addWidget(_label(effect.category, "chip"))
            layout.addLayout(head)
            layout.addWidget(_label(effect.description, "faint", wrap=True))
            actions = QHBoxLayout()
            play = _button("▶")
            play.setFixedWidth(42)
            play.clicked.connect(lambda _c=False, e=effect: self.toggle(f"sfx:{e.key}"))
            self.play_buttons[f"sfx:{effect.key}"] = play
            actions.addWidget(play)
            add = _button("＋ Projet", "ghost")
            add.clicked.connect(lambda _c=False, e=effect: self.add_to_project("sfx", e.key))
            actions.addWidget(add)
            actions.addStretch()
            layout.addLayout(actions)
            sfx_grid.addWidget(card, index // 4, index % 4)
        column.addLayout(sfx_grid)

        column.addWidget(_label("LOOKS (ÉTALONNAGE)", "step"))
        look_grid = QGridLayout()
        look_grid.setSpacing(10)
        for index, look in enumerate(library.LOOKS):
            card, layout = _card(padding=12, spacing=6)
            swatch = QLabel()
            swatch.setFixedHeight(46)
            swatch.setStyleSheet(f"border-radius: 9px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                                 f"stop:0 {look.swatch[0]}, stop:1 {look.swatch[1]});")
            layout.addWidget(swatch)
            layout.addWidget(_label(look.title, "h3"))
            layout.addWidget(_label(look.description, "faint", wrap=True))
            look_grid.addWidget(card, index // 4, index % 4)
        column.addLayout(look_grid)

        column.addWidget(_label("STYLES DE TITRES", "step"))
        preset_grid = QGridLayout()
        preset_grid.setSpacing(10)
        for index, preset in enumerate(BUILTIN_CAPTION_PRESETS):
            card, layout = _card(padding=12, spacing=6)
            sample = QLabel("Ton titre ici")
            sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sample.setFixedHeight(58)
            background = _rgba(preset.background_color, preset.background_opacity / 255) \
                if preset.background_opacity else "#2b2f3b"
            sample.setStyleSheet(
                f"background: {background}; color: {preset.text_color}; border-radius: 9px;"
                f"font-family: '{preset.font_name}'; font-size: {max(11, preset.font_size // 4)}pt;"
                f"font-weight: {'800' if preset.bold else '500'};"
            )
            layout.addWidget(sample)
            animation = {"none": "statique", "fade": "fondu", "pop": "pop"}[preset.animation]
            layout.addWidget(_label(preset.name, "h3"))
            layout.addWidget(_label(f"{preset.font_name} · animation {animation}", "faint"))
            preset_grid.addWidget(card, index // 4, index % 4)
        column.addLayout(preset_grid)
        column.addStretch()
        self.refresh()

    def refresh(self):
        missing = [t for t in library.music_catalog() if not t.path.is_file()] + \
                  [s for s in library.sfx_catalog() if not s.path.is_file()]
        self.generate_button.setVisible(bool(missing))
        self.generate_button.setEnabled(self.main.thread is None)
        self.status.setText(
            f"{len(missing)} fichier(s) audio à synthétiser (quelques secondes, une seule fois)." if missing
            else f"Bibliothèque prête · {library.assets_root()}")

    def prepare_assets(self):
        if self.main.thread is not None:
            return

        def operation(progress, cancel_event):
            return library.ensure_all(cancel_event=cancel_event)

        self.main.run_task(operation, lambda _root: (self.refresh(), self.main.notify("Bibliothèque audio prête.")))

    def toggle(self, key: str):
        if self._playing == key and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.stop()
            return
        kind, name = key.split(":", 1)
        try:
            item = library.ensure_track(name) if kind == "music" else library.ensure_sfx(name)
        except OSError as exc:
            QMessageBox.warning(self, "Lecture impossible", str(exc))
            return
        self.refresh()
        self._playing = key
        self.player.setSource(QUrl.fromLocalFile(str(item.path)))
        self.player.play()

    def _state_changed(self, state):
        for key, button in self.play_buttons.items():
            playing = key == self._playing and state == QMediaPlayer.PlaybackState.PlayingState
            if key.startswith("music:"):
                button.setText("■  Stop" if playing else "▶  Écouter")
            else:
                button.setText("■" if playing else "▶")

    def add_to_project(self, kind: str, key: str):
        store = self.main.store
        if store is None:
            QMessageBox.information(self, "Projet requis", "Ouvre ou crée un projet (ou dépose des rushs dans le "
                                                           "Studio) pour y ajouter des actifs.")
            return
        try:
            item = library.ensure_track(key) if kind == "music" else library.ensure_sfx(key)
            from cutweave.autoedit.service import project_copy

            path = project_copy(store, item.path, kind)
            store.add_audio_asset(path, probe_audio(path), category="music" if kind == "music" else "sfx",
                                  tags=("cutweave", item.title))
        except (OSError, MediaProbeError, ValueError) as exc:
            QMessageBox.warning(self, "Ajout impossible", str(exc))
            return
        self.main.media_changed()
        self.main.notify(f"« {item.title} » ajouté à la bibliothèque audio du projet.")

    def stop_media(self):
        self.player.stop()
        self.player.setSource(QUrl())
