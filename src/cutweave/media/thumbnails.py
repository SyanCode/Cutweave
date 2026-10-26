"""Génération locale de miniatures vidéo via FFmpeg."""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

from cutweave.core.proc import popen_hidden
from cutweave.media.render import ffmpeg_path


class ThumbnailError(RuntimeError):
    """Une miniature n'a pas pu être extraite du média."""


def create_video_thumbnail(
    source: str | Path,
    destination: str | Path,
    *,
    time_ms: int = 0,
    width: int = 320,
    height: int = 180,
    cancel_event=None,
) -> Path:
    """Extrait une image redimensionnée, puis la publie atomiquement."""
    source_path = Path(source).expanduser().resolve()
    output_path = Path(destination).expanduser().resolve()
    if not source_path.is_file():
        raise ThumbnailError("Le média source de la miniature est introuvable.")
    if time_ms < 0 or width < 16 or height < 16:
        raise ValueError("Le timestamp et les dimensions de miniature sont invalides.")
    binary = ffmpeg_path()
    if not binary:
        raise ThumbnailError("FFmpeg est introuvable ; la miniature n'a pas été créée.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f"{output_path.stem}.{uuid.uuid4().hex}.tmp.jpg")
    command = [
        binary, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{time_ms / 1000:.3f}",
        "-i", str(source_path), "-frames:v", "1", "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-q:v", "3", str(temporary),
    ]
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise ThumbnailError("Création de miniature annulée.")
        try:
            process = popen_hidden(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            raise ThumbnailError(f"Impossible de démarrer FFmpeg pour la miniature : {exc}") from exc
        started = time.monotonic()
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise ThumbnailError("Création de miniature annulée.")
            if time.monotonic() - started > 60:
                process.kill()
                process.wait()
                raise ThumbnailError("L'extraction de la miniature a dépassé 60 secondes.")
            time.sleep(0.05)
        _stdout, stderr = process.communicate()
        if process.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            detail = stderr.strip()[-1000:] or "aucune image n'a été produite"
            raise ThumbnailError(f"Échec d'extraction de la miniature : {detail}")
        if cancel_event is not None and cancel_event.is_set():
            raise ThumbnailError("Création de miniature annulée.")
        os.replace(temporary, output_path)
        return output_path
    finally:
        temporary.unlink(missing_ok=True)
