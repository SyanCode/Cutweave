"""Choix de la plage d'un rush : lecture du fichier d'origine, repères début/fin posés à la main."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QDialog, QHBoxLayout, QSizePolicy, QSlider, QVBoxLayout

from cutweave.autoedit.rush_settings import MIN_RANGE
from cutweave.ui.widgets import apply_icon, button, label


def clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


class RushRangeDialog(QDialog):
    """Retourne la plage [début, fin] (s) ; ``None`` après « Tout garder »."""

    def __init__(self, path: str, name: str, duration: float, window: tuple[float, float] | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Plage retenue : {name}")
        self.resize(720, 560)
        self.duration = duration
        self.start, self.end = window if window else (0.0, duration)
        self.result_window: tuple[float, float] | None = window
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(label("Place le début et la fin de la partie à utiliser. Le reste du rush est ignoré par le "
                               "montage ; le fichier n'est jamais modifié.", "muted", wrap=True))
        self.video = QVideoWidget()
        self.video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video.setMinimumHeight(260)
        layout.addWidget(self.video, 1)
        transport = QHBoxLayout()
        self.play = button("", "ghost", "play")
        self.play.setFixedSize(40, 36)
        self.play.clicked.connect(self._toggle)
        transport.addWidget(self.play)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, int(duration * 1000))
        transport.addWidget(self.seek, 1)
        self.time = label(clock(0), "faint")
        transport.addWidget(self.time)
        layout.addLayout(transport)
        marks = QHBoxLayout()
        self.start_button = button("Début ici", None, "chevron-left")
        self.start_button.clicked.connect(lambda: self._mark(True))
        self.end_button = button("Fin ici", None, "chevron-right")
        self.end_button.clicked.connect(lambda: self._mark(False))
        marks.addWidget(self.start_button)
        marks.addWidget(self.end_button)
        self.summary = label("", "h3")
        marks.addWidget(self.summary, 1)
        layout.addLayout(marks)
        footer = QHBoxLayout()
        reset = button("Tout garder", "ghost")
        reset.clicked.connect(self._reset)
        footer.addWidget(reset)
        footer.addStretch()
        cancel = button("Annuler", "ghost")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.ok = button("Valider", "primary")
        self.ok.clicked.connect(self._accept)
        footer.addWidget(self.ok)
        layout.addLayout(footer)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._position)
        self.player.playbackStateChanged.connect(
            lambda state: apply_icon(self.play, "pause" if state == QMediaPlayer.PlaybackState.PlayingState
                                     else "play", "ghost"))
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.setPosition(int(self.start * 1000))
        self._refresh()

    def _toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if not self.start <= self.player.position() / 1000 < self.end:
                self.player.setPosition(int(self.start * 1000))
            self.player.play()

    def _position(self, position: int):
        seconds = position / 1000
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.time.setText(f"{clock(seconds)} / {clock(self.duration)}")
        if seconds >= self.end and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()

    def current(self) -> float:
        return min(self.duration, max(0.0, self.player.position() / 1000))

    def _mark(self, is_start: bool):
        now = self.current()
        if is_start:
            self.start = min(now, self.end - MIN_RANGE)
        else:
            self.end = max(now, self.start + MIN_RANGE)
        self._refresh()

    def _reset(self):
        self.start, self.end = 0.0, self.duration
        self._refresh()

    def _refresh(self):
        self.summary.setText(f"{clock(self.start)} → {clock(self.end)}  ({self.end - self.start:.1f} s)")
        self.ok.setEnabled(self.end - self.start >= MIN_RANGE)

    def _accept(self):
        full = self.start <= 0.0 and self.end >= self.duration - 0.01
        self.result_window = None if full else (round(self.start, 3), round(self.end, 3))
        self.player.stop()
        self.accept()

    def done(self, code):
        self.player.stop()
        super().done(code)
