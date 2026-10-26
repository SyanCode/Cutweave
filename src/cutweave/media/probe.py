"""Analyse technique des médias avec FFprobe, sans shell."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cutweave.core.proc import popen_hidden


class MediaProbeError(RuntimeError):
    """Une erreur exploitable est survenue pendant l'analyse du média."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int | None
    height: int | None
    frame_rate: float | None
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None

    @property
    def resolution(self) -> str:
        if self.width is None or self.height is None:
            return "—"
        return f"{self.width} × {self.height}"


@dataclass(frozen=True)
class AudioInfo:
    duration: float
    sample_rate: int | None
    channels: int | None
    codec: str | None


def ffprobe_path() -> str | None:
    configured = os.environ.get("CUTWEAVE_FFPROBE")
    return configured if configured else shutil.which("ffprobe")


def parse_fraction(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else None
    except (ValueError, ZeroDivisionError):
        return None


def parse_probe(payload: dict[str, Any]) -> MediaInfo:
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaProbeError("Le fichier ne contient aucun flux vidéo.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration_raw = payload.get("format", {}).get("duration")
    if duration_raw is None:
        duration_raw = video.get("duration", 0)
    try:
        duration = float(duration_raw or 0)
    except (ValueError, TypeError):
        duration = 0.0
    return MediaInfo(
        duration=max(0.0, duration),
        width=int(video["width"]) if video.get("width") else None,
        height=int(video["height"]) if video.get("height") else None,
        frame_rate=parse_fraction(video.get("avg_frame_rate")) or parse_fraction(video.get("r_frame_rate")),
        has_audio=audio is not None,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
    )


def probe_media(path: str | Path, *, timeout: float = 30, cancel_event=None) -> MediaInfo:
    media_path = Path(path).expanduser().resolve()
    if not media_path.is_file():
        raise MediaProbeError(f"Fichier introuvable : {media_path}")
    binary = ffprobe_path()
    if not binary:
        raise MediaProbeError("FFprobe est introuvable. Installez FFmpeg ou définissez CUTWEAVE_FFPROBE.")
    command = [binary, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(media_path)]
    try:
        process = popen_hidden(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MediaProbeError(f"Impossible d'analyser {media_path.name} : {exc}") from exc
    start = time.monotonic()
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    raise MediaProbeError("Analyse annulée.")
                if time.monotonic() - start > timeout:
                    process.kill()
                    process.communicate()
                    raise MediaProbeError(f"Délai dépassé pendant l'analyse de {media_path.name}.")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    if process.returncode:
        detail = stderr.strip() or "FFprobe a échoué sans détail."
        raise MediaProbeError(f"Analyse impossible pour {media_path.name} : {detail}")
    try:
        return parse_probe(json.loads(stdout))
    except MediaProbeError:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise MediaProbeError(f"Réponse FFprobe invalide pour {media_path.name}.") from exc


def probe_audio(path: str | Path, *, timeout: float = 30, cancel_event=None) -> AudioInfo:
    """Analyse un fichier audio local sans exiger de flux vidéo."""
    media_path = Path(path).expanduser().resolve()
    if not media_path.is_file():
        raise MediaProbeError(f"Fichier introuvable : {media_path}")
    binary = ffprobe_path()
    if not binary:
        raise MediaProbeError("FFprobe est introuvable. Installez FFmpeg ou définissez CUTWEAVE_FFPROBE.")
    command = [binary, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(media_path)]
    try:
        process = popen_hidden(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MediaProbeError(f"Impossible d'analyser {media_path.name} : {exc}") from exc
    started = time.monotonic()
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    raise MediaProbeError("Analyse audio annulée.")
                if time.monotonic() - started > timeout:
                    process.kill()
                    process.communicate()
                    raise MediaProbeError(f"Délai dépassé pendant l'analyse de {media_path.name}.")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    if process.returncode:
        raise MediaProbeError(f"Analyse impossible pour {media_path.name} : {stderr.strip() or 'FFprobe a échoué.'}")
    try:
        payload = json.loads(stdout)
        streams = payload.get("streams", [])
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if audio is None:
            raise MediaProbeError("Le fichier ne contient aucun flux audio.")
        duration_value = payload.get("format", {}).get("duration", audio.get("duration", 0))
        duration = max(0.0, float(duration_value or 0))
        return AudioInfo(duration,
                         int(audio["sample_rate"]) if audio.get("sample_rate") else None,
                         int(audio["channels"]) if audio.get("channels") else None,
                         audio.get("codec_name"))
    except MediaProbeError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise MediaProbeError(f"Réponse FFprobe invalide pour {media_path.name}.") from exc

