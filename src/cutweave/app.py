"""Point d'entrée de l'application de bureau."""

import logging
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from cutweave import __version__
from cutweave.core.settings import AppSettings, configure_logging

LOGGER = logging.getLogger("cutweave")


def _install_excepthook() -> None:
    """Toute erreur non gérée est journalisée et affichée au lieu de fermer silencieusement l'application."""

    def hook(exc_type, exc_value, traceback):
        LOGGER.critical("Erreur non gérée", exc_info=(exc_type, exc_value, traceback))
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "Erreur inattendue", f"{exc_type.__name__} : {exc_value}\n\n"
                                 "Le détail est enregistré dans le journal (Outils > Ouvrir le dossier des journaux).")

    sys.excepthook = hook


def _set_windows_app_id() -> None:
    """Sous Windows, sans identifiant propre la barre des tâches regroupe la fenêtre sous l'icône de Python."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CUTWEAVE.Cutweave.App")
    except (AttributeError, OSError):
        pass


def main() -> int:
    settings = AppSettings.load()
    settings.apply()
    try:
        log_file = configure_logging(settings.resolved_log_dir())
    except OSError:
        log_file = None
    LOGGER.info("CUTWEAVE %s démarré (journal : %s)", __version__, log_file)
    _install_excepthook()
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("CUTWEAVE")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("CUTWEAVE")
    from cutweave.ui.branding import app_icon
    from cutweave.ui.main_window import MainWindow

    app.setWindowIcon(app_icon())

    window = MainWindow()
    window.show()
    smoke_ms = os.environ.get("CUTWEAVE_SMOKE_EXIT_MS")
    if smoke_ms:
        # Vérification d'un exécutable construit : démarre l'interface puis quitte proprement.
        QTimer.singleShot(int(smoke_ms), app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
