"""Découverte déterministe des fichiers vidéo à importer."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpeg", ".mpg"}


def collect_video_files(paths: Iterable[str | Path]) -> list[Path]:
    """Résout des fichiers et dossiers sélectionnés en vidéos uniques et triées."""
    candidates: set[Path] = set()
    for selected in paths:
        path = Path(selected).expanduser().resolve()
        if path.is_file():
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                candidates.add(path)
        elif path.is_dir():
            candidates.update(
                child.resolve()
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS
            )
        else:
            raise FileNotFoundError(f"Chemin sélectionné introuvable : {path}")
    return sorted(candidates, key=lambda item: str(item).casefold())
