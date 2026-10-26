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


def test_studio_is_the_home_page(window):
    _app, main, errors = window
    assert main.pages.currentWidget() is main.studio
    assert main.sidebar.item(0).text().strip().endswith("Studio")
    assert not main.studio.generate_button.isEnabled()
    assert len(main.studio.mode_cards) == 6
    assert errors == []


@needs_ffmpeg
def test_studio_drop_generate_and_refine(window, tmp_path, monkeypatch):
    app, main, errors = window
    from dataclasses import replace

    from cutweave.ui.studio import StudioPage

    main.app_settings.projects_dir = str(tmp_path / "projects")
    clips = tmp_path / "clips"
    clips.mkdir()
    for index, size in enumerate(("320x180", "180x320")):
        subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc2=size={size}:rate=25:duration=4", "-c:v", "mpeg4", str(clips / f"r{index}.mp4")],
                       check=True, capture_output=True)
    main.studio.add_paths([str(clips)])
    _wait(app, main)
    assert main.store is not None and main.store.root.parent == tmp_path / "projects"
    assert main.studio.rush_list.count() == 2 and main.studio.generate_button.isEnabled()

    original = StudioPage.build_request
    monkeypatch.setattr(StudioPage, "build_request",
                        lambda self: replace(original(self), quality=120, variants=1, duration=6.0))
    main.studio._select_mode("highlights")
    main.studio.generate()
    assert main.studio.pages.currentIndex() == 1
    _wait(app, main, timeout=180)
    assert errors == []
    assert main.studio.pages.currentIndex() == 2 and main.studio.current is not None
    assert main.studio.current.key == "highlights_A"
    main.studio.refine_current()
    app.processEvents()
    assert main.pages.currentIndex() == 1
    assert main._selected_media().name == "highlights_A.mp4"


def test_titles_are_opt_in_and_do_not_persist(window):
    _app, main, _errors = window
    studio = main.studio
    assert not studio.title_toggle.isChecked() and not studio.title_edit.isEnabled()
    studio.title_edit.setText("ne doit pas apparaître")
    assert studio.build_request().title == ""
    studio.title_toggle.setChecked(True)
    studio.title_edit.setText("Mon titre")
    studio.outro_toggle.setChecked(True)
    studio.outro_edit.setText("Abonne-toi")
    request = studio.build_request()
    assert (request.title, request.outro) == ("Mon titre", "Abonne-toi")
    studio.title_toggle.setChecked(False)
    assert studio.title_edit.text() == "" and studio.build_request().title == ""
    assert studio.build_request().clean_noise is True


@pytest.mark.parametrize("size", [(960, 600), (1100, 720), (1480, 940), (1920, 1080)])
def test_every_page_fits_without_horizontal_scrolling(window, size, tmp_path):
    from PySide6.QtWidgets import QAbstractScrollArea

    app, main, _errors = window
    store = ProjectStore.create(tmp_path / "project")
    main._load_project(store)
    main.resize(*size)
    main.show()
    app.processEvents()

    def overflowing(root):
        return [area.objectName() or type(area).__name__ for area in root.findChildren(QAbstractScrollArea)
                if area.isVisible() and area.horizontalScrollBar().maximum() > 0
                and area.objectName() in {"pageScroll", ""} and type(area).__name__ == "QScrollArea"]

    for index in range(3):
        main.pages.setCurrentIndex(index)
        if index == 0:
            for sub in (0, 2):
                main.studio.pages.setCurrentIndex(sub)
                app.processEvents()
                assert overflowing(main.studio) == [], (size, "studio", sub)
        elif index == 1:
            for tab in range(main.editor_tabs.count()):
                main.editor_tabs.setCurrentIndex(tab)
                app.processEvents()
                assert overflowing(main.editor_tabs) == [], (size, "editor", main.editor_tabs.tabText(tab))
        else:
            app.processEvents()
            assert overflowing(main.library_page) == [], (size, "library")
    compact = size[0] < 1180
    assert main.sidebar_frame.width() == (68 if compact else 232)


def test_layouts_reflow_with_the_window_width(window):
    app, main, _errors = window
    main.studio.pages.setCurrentIndex(0)
    main.pages.setCurrentIndex(0)
    columns = {}
    for width in (1500, 1000, 760):
        main.resize(width, 800)
        app.processEvents()
        columns[width] = (main.studio.mode_grid.columns, main.studio.settings_form.two_columns)
    assert columns[1500] == (3, True)
    assert columns[1000][0] in (2, 3) and columns[760][0] <= 2 and columns[760][1] is False


def test_legacy_buttons_get_icons_and_variants_automatically(window):
    app, main, _errors = window
    main.pages.setCurrentIndex(1)
    main.editor_tabs.setCurrentIndex(0)
    app.processEvents()
    assert not main.import_button.icon().isNull()
    assert main.framing_delete_button.property("variant") == "danger"
    assert main.export_button.property("variant") == "primary"
    for current in range(main.editor_tabs.count()):
        main.editor_tabs.setCurrentIndex(current)
        app.processEvents()
    assert not main.broll_delete_button.icon().isNull()


def test_all_icons_render_and_qt_texts_are_french(window):
    from PySide6.QtWidgets import QDialogButtonBox

    from cutweave.ui import icons

    for name in icons.available():
        assert not icons.pixmap(name).isNull(), name
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    assert {button.text().replace("&", "") for button in box.buttons()} == {"Enregistrer", "Annuler"}
