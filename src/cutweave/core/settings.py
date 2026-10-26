"""Réglages locaux de l'application et journalisation fichier.

Les réglages sont stockés en JSON dans ``%APPDATA%\\CUTWEAVE`` (ou ``CUTWEAVE_CONFIG_DIR``).
Aucune donnée n'est transmise : les journaux restent sur le disque local.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, fields
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s : %(message)s"


def config_dir() -> Path:
    configured = os.environ.get("CUTWEAVE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "CUTWEAVE"


@dataclass
class AppSettings:
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    log_dir: str = ""
    projects_dir: str = ""
    recent_projects: str = ""      # chemins séparés par des retours à la ligne, du plus récent au plus ancien

    @classmethod
    def load(cls, path: Path | None = None) -> "AppSettings":
        source = path or config_dir() / "settings.json"
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            logging.getLogger("cutweave").warning("Réglages illisibles (%s) ; valeurs par défaut utilisées.", exc)
            return cls()
        known = {item.name for item in fields(cls)}
        return cls(**{key: str(value) for key, value in data.items() if key in known and value is not None})

    def save(self, path: Path | None = None) -> Path:
        target = path or config_dir() / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def recent(self) -> list[str]:
        return [line for line in self.recent_projects.splitlines() if line.strip()]

    def add_recent(self, path: str | Path, limit: int = 8) -> None:
        entry = str(Path(path))
        kept = [item for item in self.recent() if item != entry]
        self.recent_projects = "\n".join([entry, *kept][:limit])

    def forget_recent(self, path: str | Path) -> None:
        self.recent_projects = "\n".join(item for item in self.recent() if item != str(Path(path)))

    def validate(self) -> None:
        for label, value in (("FFmpeg", self.ffmpeg_path), ("FFprobe", self.ffprobe_path)):
            if value and not Path(value).is_file():
                raise ValueError(f"Exécutable {label} introuvable : {value}")
        for label, value in (("journaux", self.log_dir), ("projets", self.projects_dir)):
            if value and Path(value).exists() and not Path(value).is_dir():
                raise ValueError(f"Le dossier des {label} n'est pas un dossier : {value}")

    def apply(self) -> None:
        """Expose les chemins configurés aux modules FFmpeg/FFprobe via l'environnement du processus."""
        for variable, value in (("CUTWEAVE_FFMPEG", self.ffmpeg_path), ("CUTWEAVE_FFPROBE", self.ffprobe_path)):
            if value:
                os.environ[variable] = value
            else:
                os.environ.pop(variable, None)

    def resolved_log_dir(self) -> Path:
        return Path(self.log_dir).expanduser() if self.log_dir else config_dir() / "logs"


def configure_logging(log_dir: Path, *, level: int = logging.INFO) -> Path:
    """Journal tournant local (2 Mio × 5). Retourne le chemin du fichier de journal."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "cutweave.log"
    logger = logging.getLogger("cutweave")
    logger.setLevel(level)
    for handler in list(logger.handlers):
        if getattr(handler, "_cutweave_file", False):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler._cutweave_file = True
    logger.addHandler(handler)
    return log_file
