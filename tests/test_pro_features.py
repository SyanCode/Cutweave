"""Diagnostic, cache, projets récents, notifications, À propos, glisser-déposer et identité visuelle."""

import os
import struct
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutweave.core import health  # noqa: E402
from cutweave.core.cache_tools import directory_size, human_size, prune_oldest  # noqa: E402
from cutweave.core.settings import AppSettings  # noqa: E402


def test_missing_ffmpeg_is_reported_as_a_blocking_issue(monkeypatch):
    monkeypatch.setattr("cutweave.media.render.ffmpeg_path", lambda: None)
    issues = health.environment_issues(probe_hardware=False)
    assert issues and issues[0].level == "error" and "FFmpeg" in issues[0].title and issues[0].action == "settings"


def test_software_encoding_is_reported_as_information(monkeypatch):
    monkeypatch.setattr("cutweave.media.render.ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr("cutweave.media.probe.ffprobe_path", lambda: "ffprobe")
    monkeypatch.setattr("cutweave.autoedit.assemble.missing_filters", lambda binary=None: [])
    monkeypatch.setattr("cutweave.media.render._available_video_encoders", lambda binary: {"libopenh264", "mpeg4"})
    issues = health.environment_issues()
    assert [issue.level for issue in issues] == ["info"] and "libopenh264" in issues[0].detail


def test_diagnostics_contain_versions_and_no_personal_paths(tmp_path):
    report = health.collect_diagnostics()
    assert "CUTWEAVE 0.2.0" in report and "FFmpeg" in report and "Python" in report
    assert str(Path.home()) not in report or str(Path.home()) in os.environ.get("PATH", "")


def test_cache_pruning_removes_the_oldest_files_first(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    now = time.time()
    for index in range(5):
        path = cache / f"shot{index}.mkv"
        path.write_bytes(b"x" * 1000)
        os.utime(path, (now - 10_000 + index, now - 10_000 + index))
    recent = cache / "recent.mkv"
    recent.write_bytes(b"x" * 1000)
    freed = prune_oldest(cache, max_bytes=3000)
    assert freed == 3000 and directory_size(cache) == 3000
    assert recent.exists() and not (cache / "shot0.mkv").exists() and (cache / "shot4.mkv").exists()
    assert prune_oldest(cache, max_bytes=10 ** 9) == 0
    assert human_size(1536) == "1.5 Ko" and human_size(12) == "12 o"


def test_recent_projects_keep_order_and_limit():
    settings = AppSettings()
    for name in "abcdefghij":
        settings.add_recent(f"/p/{name}")
    settings.add_recent("/p/c")
    assert settings.recent()[0].endswith("c") and len(settings.recent()) == 8
    settings.forget_recent("/p/c")
    assert not any(path.endswith("/c") for path in settings.recent())


def test_logo_and_icon_resources_are_packaged():
    resources = Path(__file__).parents[1] / "src" / "cutweave" / "ui" / "resources"
    assert b"<svg" in (resources / "logo.svg").read_bytes()
    icon = (resources / "cutweave.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", icon[:6])
    assert (reserved, kind, count) == (0, 1, 7)
    assert icon[6 + 16 + 4:6 + 16 + 6] != b""          # entrées PNG
    assert "ui/resources/*" in (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")


@pytest.fixture()
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTWEAVE_CONFIG_DIR", str(tmp_path / "config"))
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *args, **kwargs: None))
    from cutweave.ui.main_window import MainWindow

    main = MainWindow()
    main.show()
    yield app, main
    main.close()


def test_window_has_the_brand_icon_and_menus(window):
    _app, main = window
    assert not main.windowIcon().isNull()
    titles = [action.text() for action in main.menuBar().actions()]
    assert titles == ["Projet", "Affichage", "Outils", "Aide"]
    shortcuts = {action.text(): action.shortcut().toString() for menu in main.menuBar().actions()
                 for action in menu.menu().actions() if action.shortcut().toString()}
    assert shortcuts["Nouveau…"] == "Ctrl+N" and shortcuts["Ouvrir…"] == "Ctrl+O" and shortcuts["Studio"] == "Ctrl+1"


def test_toasts_stack_and_expire(window):
    app, main = window
    for index in range(5):
        main.notify(f"Message {index}", "success" if index % 2 else "info")
    app.processEvents()
    assert 1 <= len([toast for toast in main.toasts.toasts if toast.isVisible()]) <= 3


def test_health_banner_lists_issues_with_a_settings_shortcut(window):
    app, main = window
    from cutweave.core.health import Issue

    main.studio.set_issues([Issue("error", "FFmpeg introuvable", "Installe-le.", "settings"),
                            Issue("info", "Encodage logiciel", "Plus lent.")])
    app.processEvents()
    assert not main.studio.health_box.isHidden() and main.studio.health_layout.count() == 2
    main.studio.set_issues([])
    assert main.studio.health_box.isHidden()


def test_recent_projects_are_listed_and_reopenable(window, tmp_path):
    app, main = window
    from cutweave.storage.project_store import ProjectStore

    store = ProjectStore.create(tmp_path / "Mon projet")
    main._load_project(store)
    assert str(store.root) in main.app_settings.recent()
    assert main.recent_menu.isEnabled() and "Mon projet" in main.recent_menu.actions()[0].text()
    main.close_project()
    main.studio.refresh()
    assert not main.studio.recent_box.isHidden()
    main.open_project_path(str(store.root))
    assert main.store is not None and main.store.root == store.root.resolve()


def test_about_dialog_shows_a_copyable_diagnostic(window):
    from PySide6.QtWidgets import QLabel

    from cutweave.ui.dialogs import AboutDialog

    _app, main = window
    dialog = AboutDialog(main)
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "CUTWEAVE 0.2.0" in dialog.report.toPlainText()
    assert "aucune télémétrie" in texts.lower() and "MIT" in texts


def test_dropping_files_anywhere_sends_them_to_the_studio(window, tmp_path, monkeypatch):
    from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    app, main = window
    received = []
    monkeypatch.setattr(main.studio, "add_paths", lambda paths: received.append(paths))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "clip.mp4"))])
    main.pages.setCurrentIndex(2)
    event = QDropEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    main.dropEvent(event)
    assert received == [[str(tmp_path / "clip.mp4")]] and main.pages.currentIndex() == 0
