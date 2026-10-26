"""Lancement de processus enfants (FFmpeg, FFprobe) sans ouvrir de fenêtre console sous Windows.

L'application graphique n'a pas de console : sans ces options, chaque exécutable en ligne de
commande lancé en arrière-plan fait apparaître brièvement une fenêtre de terminal.
"""

from __future__ import annotations

import subprocess
import sys


def hidden_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startup}


def popen_hidden(*args, **kwargs) -> subprocess.Popen:
    return subprocess.Popen(*args, **{**hidden_kwargs(), **kwargs})


def run_hidden(*args, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(*args, **{**hidden_kwargs(), **kwargs})
