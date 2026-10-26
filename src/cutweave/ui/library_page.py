"""Bibliothèque d'actifs intégrés : écoute des musiques et SFX, looks et styles de titres."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from cutweave.assets import library
from cutweave.media.probe import MediaProbeError, probe_audio
from cutweave.stages.finishing.caption_presets import BUILTIN_CAPTION_PRESETS
from cutweave.ui.studio import centered_column, scroll_area
from cutweave.ui.widgets import ReflowGrid, apply_icon, button, card, label, rgba


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
        body, column = centered_column()
        outer.addWidget(scroll_area(body))

        column.addWidget(label("Bibliothèque", "h1"))
        column.addWidget(label("Musiques, effets sonores, looks et styles de titres intégrés. Tout est généré "
                               "localement sur ta machine : libre de droits, aucun téléchargement.", "muted", wrap=True))
        status_row = QHBoxLayout()
        self.status = label("", "faint", wrap=True)
        status_row.addWidget(self.status, 1)
        self.generate_button = button("Préparer la bibliothèque audio", "primary", "refresh")
        self.generate_button.clicked.connect(self.prepare_assets)
        status_row.addWidget(self.generate_button)
        column.addLayout(status_row)

        column.addWidget(label("MUSIQUES", "step"))
        music_grid = ReflowGrid(min_item_width=270, max_columns=3)
        for track in library.music_catalog():
            panel, layout = card(padding=16, spacing=6)
            head = QHBoxLayout()
            head.addWidget(label(track.title, "h3"), 1)
            head.addWidget(label(f"{track.bpm:g} BPM", "chip"))
            layout.addLayout(head)
            layout.addWidget(label(f"{track.mood} · {int(track.duration_s)} s en boucle", "muted", wrap=True))
            layout.addWidget(label(track.description, "faint", wrap=True))
            actions = QHBoxLayout()
            play = button("Écouter", None, "play")
            play.clicked.connect(lambda _c=False, t=track: self.toggle(f"music:{t.key}"))
            self.play_buttons[f"music:{track.key}"] = play
            actions.addWidget(play)
            add = button("Au projet", "ghost", "plus")
            add.setToolTip("Ajoute ce morceau à la bibliothèque audio du projet (onglet Musique & SFX).")
            add.clicked.connect(lambda _c=False, t=track: self.add_to_project("music", t.key))
            actions.addWidget(add)
            actions.addStretch()
            layout.addLayout(actions)
            music_grid.add(panel)
        column.addWidget(music_grid)

        column.addWidget(label("EFFETS SONORES", "step"))
        sfx_grid = ReflowGrid(min_item_width=220, max_columns=4, spacing=10)
        for effect in library.sfx_catalog():
            panel, layout = card(padding=12, spacing=4)
            head = QHBoxLayout()
            head.addWidget(label(effect.title, "h3"), 1)
            head.addWidget(label(effect.category, "chip"))
            layout.addLayout(head)
            layout.addWidget(label(effect.description, "faint", wrap=True))
            actions = QHBoxLayout()
            play = button("", None, "play")
            play.setFixedWidth(46)
            play.clicked.connect(lambda _c=False, e=effect: self.toggle(f"sfx:{e.key}"))
            self.play_buttons[f"sfx:{effect.key}"] = play
            actions.addWidget(play)
            add = button("Au projet", "ghost", "plus")
            add.clicked.connect(lambda _c=False, e=effect: self.add_to_project("sfx", e.key))
            actions.addWidget(add)
            actions.addStretch()
            layout.addLayout(actions)
            sfx_grid.add(panel)
        column.addWidget(sfx_grid)

        column.addWidget(label("LOOKS (ÉTALONNAGE)", "step"))
        look_grid = ReflowGrid(min_item_width=200, max_columns=4, spacing=10)
        for look in library.LOOKS:
            panel, layout = card(padding=12, spacing=6)
            swatch = QLabel()
            swatch.setFixedHeight(46)
            swatch.setStyleSheet(f"border-radius: 9px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                                 f"stop:0 {look.swatch[0]}, stop:1 {look.swatch[1]});")
            layout.addWidget(swatch)
            layout.addWidget(label(look.title, "h3"))
            layout.addWidget(label(look.description, "faint", wrap=True))
            look_grid.add(panel)
        column.addWidget(look_grid)

        column.addWidget(label("STYLES DE TITRES", "step"))
        preset_grid = ReflowGrid(min_item_width=200, max_columns=4, spacing=10)
        for preset in BUILTIN_CAPTION_PRESETS:
            panel, layout = card(padding=12, spacing=6)
            sample = QLabel("Ton titre ici")
            sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sample.setFixedHeight(58)
            background = rgba(preset.background_color, preset.background_opacity / 255) \
                if preset.background_opacity else "#2b2f3b"
            sample.setStyleSheet(
                f"background: {background}; color: {preset.text_color}; border-radius: 9px;"
                f"font-family: '{preset.font_name}'; font-size: {max(11, preset.font_size // 4)}pt;"
                f"font-weight: {'800' if preset.bold else '500'};"
            )
            layout.addWidget(sample)
            animation = {"none": "statique", "fade": "fondu", "pop": "pop"}[preset.animation]
            layout.addWidget(label(preset.name, "h3"))
            layout.addWidget(label(f"{preset.font_name} · animation {animation}", "faint", wrap=True))
            preset_grid.add(panel)
        column.addWidget(preset_grid)
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
        for key, play_button in self.play_buttons.items():
            playing = key == self._playing and state == QMediaPlayer.PlaybackState.PlayingState
            if key.startswith("music:"):
                play_button.setText("Stop" if playing else "Écouter")
            apply_icon(play_button, "pause" if playing else "play")

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
