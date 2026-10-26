"""Taille et élagage du cache d'un projet (plans rendus, analyses, rendus finaux) : le disque ne se remplit pas sans fin."""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOGGER = logging.getLogger("cutweave.cache")
DEFAULT_LIMIT_BYTES = 8 * 1024 ** 3


def directory_size(root: Path) -> int:
    total = 0
    for folder, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += (Path(folder) / name).stat().st_size
            except OSError:
                continue
    return total


def prune_oldest(root: Path, max_bytes: int = DEFAULT_LIMIT_BYTES, *, keep_newer_than: float = 3600.0) -> int:
    """Supprime les fichiers de cache les moins récemment utilisés jusqu'à passer sous ``max_bytes``.

    Les fichiers modifiés depuis moins d'une heure sont conservés (montage en cours). Retourne les octets libérés.
    """
    if not root.is_dir():
        return 0
    files: list[tuple[float, int, Path]] = []
    total = 0
    for folder, _dirs, names in os.walk(root):
        for name in names:
            path = Path(folder) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            files.append((max(stat.st_atime, stat.st_mtime), stat.st_size, path))
    if total <= max_bytes:
        return 0
    import time

    freed = 0
    for used_at, size, path in sorted(files):
        if total - freed <= max_bytes:
            break
        if time.time() - used_at < keep_newer_than or path.suffix in {".json"} and "analysis" in path.parts:
            continue
        try:
            path.unlink()
            freed += size
        except OSError:
            continue
    LOGGER.info("Cache élagué : %.1f Mo libérés dans %s", freed / 1024 ** 2, root)
    return freed


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if size < 1024 or unit == "To":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} o"
