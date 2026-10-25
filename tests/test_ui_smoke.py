"""Démarrage réel de l'interface en mode hors écran et exécution du pipeline depuis l'UI."""

import os
import subprocess
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutweave.media.probe import ffprobe_path, probe_media  # noqa: E402
from cutweave.media.render import VisualSettings, ffmpeg_path  # noqa: E402
from cutweave.stages.composition.captions import CaptionCue  # noqa: E402
from cutweave.storage.project_store import ProjectStore  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")


@pytest.fixture()
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTWEAVE_CONFIG_DIR", str(tmp_path / "config"))
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    errors = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *args, _n=name: errors.append((_n, args[2]))))
    from cutweave.ui.main_window import MainWindow

    main = MainWindow()
    main.show()
    yield app, main, errors
    main.close()


def _wait(app, main, timeout=60):
    deadline = time.monotonic() + timeout
    while main.thread is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert main.thread is None, "la tâche n'est pas terminée"


def test_window_starts_without_project(window):
    _app, main, errors = window
    assert main.windowTitle().startswith("CUTWEAVE")
    assert main.sidebar.count() > 10
    assert not main.export_button.isEnabled()
    assert errors == []


@needs_ffmpeg
def test_full_pipeline_and_single_stage_from_ui(window, tmp_path, monkeypatch):
    app, main, errors = window
    source = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25:duration=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(source)],
                   check=True, capture_output=True)
    store = ProjectStore.create(tmp_path / "project")
    media = store.add_media(source, probe_media(source))
    store.set_visual_settings(media.id, VisualSettings("16:9", 320, 180, 0.3))
    store.set_captions(media.id, (CaptionCue(100, 800, "Bonjour"),))
    main._load_project(ProjectStore(store.root))
    main.table.selectRow(0)
    app.processEvents()
    assert main.export_button.isEnabled()

    from PySide6.QtWidgets import QFileDialog
    destination = tmp_path / "export.mp4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *args, **kwargs: (str(destination), "")))
    main.export_selected()
    _wait(app, main)
    assert errors == []
    assert destination.is_file() and probe_media(destination).width == 320
    assert "Pipeline terminé" in main.log_view.toPlainText()

    main.export_selected(target_stage="finishing")
    _wait(app, main)
    assert errors == []
    assert "Étape 3 — Finition terminée" in main.log_view.toPlainText()
    statuses = [main.pipeline_panel.status_table.item(row, 1).text() for row in range(3)]
    assert statuses == ["terminée", "terminée", "terminée"]

    main.close_project()
    assert main.store is None
