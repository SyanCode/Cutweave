"""Vérification de l'environnement (FFmpeg, filtres, encodeurs) et rapport de diagnostic copiable pour un ticket."""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from cutweave import __version__
from cutweave.core.proc import run_hidden

LOGGER = logging.getLogger("cutweave.health")


@dataclass(frozen=True)
class Issue:
    level: str          # "error" | "warning" | "info"
    title: str
    detail: str
    action: str = ""    # "settings" : ouvrir les paramètres


def _first_line(command: list[str]) -> str:
    try:
        result = run_hidden(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=15, check=False)
        return (result.stdout.splitlines() or [f"code {result.returncode}"])[0]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"impossible à exécuter ({exc})"


def environment_issues(*, probe_hardware: bool = True) -> list[Issue]:
    """Problèmes bloquants ou dégradants pour le montage ; liste vide si tout est prêt."""
    from cutweave.autoedit.assemble import missing_filters
    from cutweave.media.encoders import HARDWARE, choose_encoder
    from cutweave.media.probe import ffprobe_path
    from cutweave.media.render import _available_video_encoders, ffmpeg_path

    issues: list[Issue] = []
    ffmpeg, ffprobe = ffmpeg_path(), ffprobe_path()
    if not ffmpeg or not ffprobe:
        absent = " et ".join(name for name, path in (("FFmpeg", ffmpeg), ("FFprobe", ffprobe)) if not path)
        issues.append(Issue("error", f"{absent} introuvable",
                            "CUTWEAVE s'appuie sur FFmpeg, installé séparément (par exemple « winget install "
                            "BtbN.FFmpeg.LGPL.9.0 »). Installe-le puis indique son chemin dans les paramètres.", "settings"))
        return issues
    absent_filters = missing_filters(ffmpeg)
    if absent_filters:
        issues.append(Issue("error", "Ce FFmpeg est incomplet",
                            "Filtres manquants : " + ", ".join(absent_filters) + ". Installe un build complet.", "settings"))
    try:
        available = _available_video_encoders(ffmpeg)
    except Exception as exc:  # FFmpeg illisible : on le signale sans bloquer l'application
        issues.append(Issue("error", "FFmpeg ne répond pas", str(exc), "settings"))
        return issues
    if probe_hardware:
        chosen = choose_encoder(ffmpeg, available, "fast")
        if chosen is None:
            issues.append(Issue("error", "Aucun encodeur vidéo", "Ce FFmpeg n'a aucun encodeur H.264 ni MPEG-4.", "settings"))
        elif chosen not in HARDWARE and chosen != "libx264":
            issues.append(Issue("info", "Encodage logiciel",
                                f"Aucun encodeur de carte graphique utilisable : {chosen} sera utilisé, plus lent. "
                                "Un FFmpeg avec libx264 ou une carte NVIDIA/AMD/Intel récente accélère le montage."))
    return issues


def collect_diagnostics(project_root: Path | None = None) -> str:
    """Texte à coller dans un ticket : versions, FFmpeg, encodeurs, chemins. Aucune donnée personnelle de projet."""
    lines = [f"CUTWEAVE {__version__}", f"Python {sys.version.split()[0]} — {platform.platform()}"]
    for module in ("PySide6", "cv2", "numpy"):
        try:
            imported = __import__(module)
            lines.append(f"{module} {getattr(imported, '__version__', '?')}")
        except Exception:  # module absent : signalé dans le rapport
            lines.append(f"{module} : non importable")
    from cutweave.autoedit.assemble import missing_filters
    from cutweave.media.encoders import choose_encoder
    from cutweave.media.probe import ffprobe_path
    from cutweave.media.render import _available_video_encoders, ffmpeg_path

    ffmpeg, ffprobe = ffmpeg_path(), ffprobe_path()
    lines.append(f"FFmpeg : {ffmpeg or 'introuvable'}")
    if ffmpeg:
        lines.append("  " + _first_line([ffmpeg, "-hide_banner", "-version"]))
        absent = missing_filters(ffmpeg)
        lines.append("  filtres du montage : " + ("tous présents" if not absent else "manquants : " + ", ".join(absent)))
        try:
            available = _available_video_encoders(ffmpeg)
            lines.append("  encodeur rapide : " + str(choose_encoder(ffmpeg, available, "fast"))
                         + " · export : " + str(choose_encoder(ffmpeg, available, "final")))
            lines.append("  encodeurs H.264 présents : " + (", ".join(sorted(
                name for name in available if "264" in name or "nvenc" in name or "qsv" in name or "amf" in name)) or "aucun"))
        except Exception as exc:
            lines.append(f"  encodeurs illisibles : {exc}")
    lines.append(f"FFprobe : {ffprobe or 'introuvable'}")
    if project_root is not None:
        lines.append(f"Projet : {project_root}")
    return "\n".join(lines)
