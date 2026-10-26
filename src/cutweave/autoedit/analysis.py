"""Analyse déterministe des rushs par traitement du signal classique.

Pour chaque rush, FFmpeg décode une version très réduite (images en niveaux de gris, audio mono
basse fréquence) et numpy calcule des courbes échantillonnées toutes les ``STEP`` secondes :

* mouvement : différence absolue moyenne entre images successives ;
* luminosité : moyenne des pixels ;
* netteté : variance du laplacien ;
* niveau sonore : RMS en dBFS ;
* changements de plan : pics de différence d'image ;
* zones de parole/activité sonore : niveau au-dessus d'un seuil adaptatif (aucune reconnaissance).

Aucun modèle, aucune détection de personnes : uniquement des mesures de pixels et d'échantillons.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from cutweave.core.proc import popen_hidden
from cutweave.media.probe import MediaInfo, probe_media
from cutweave.media.render import ffmpeg_path

LOGGER = logging.getLogger("cutweave.autoedit")
STEP = 0.25
ANALYSIS_VERSION = 3
FINE_STEP = 0.05
FRAME_W, FRAME_H = 96, 54
AUDIO_RATE = 8000


class AnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class RushAnalysis:
    path: str
    name: str
    duration: float
    width: int
    height: int
    frame_rate: float
    has_audio: bool
    modified: float
    motion: tuple[float, ...]
    brightness: tuple[float, ...]
    sharpness: tuple[float, ...]
    loudness: tuple[float, ...]
    scene_cuts: tuple[float, ...]
    activity: tuple[tuple[float, float], ...]
    fine_loudness: tuple[float, ...] = ()     # niveau sonore par pas de FINE_STEP (précision des coupes)

    @property
    def samples(self) -> int:
        return len(self.motion)

    @property
    def portrait(self) -> bool:
        return self.height > self.width

    @property
    def speech_ratio(self) -> float:
        if self.duration <= 0:
            return 0.0
        return sum(end - start for start, end in self.activity) / self.duration

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "RushAnalysis":
        data = dict(data)
        for key in ("motion", "brightness", "sharpness", "loudness", "scene_cuts"):
            data[key] = tuple(float(value) for value in data[key])
        data["activity"] = tuple((float(a), float(b)) for a, b in data["activity"])
        data["fine_loudness"] = tuple(float(value) for value in data.get("fine_loudness", ()))
        return cls(**data)


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{ANALYSIS_VERSION}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _run_pipe(command: list[str], cancel_event, timeout: float = 1800) -> bytes:
    process = popen_hidden(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    chunks: list[bytes] = []
    errors: list[bytes] = []

    def drain_err():
        errors.append(process.stderr.read())

    reader = threading.Thread(target=drain_err, daemon=True)
    reader.start()
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                raise AnalysisError("Analyse annulée.")
            block = process.stdout.read(1 << 20)
            if not block:
                break
            chunks.append(block)
        process.wait(timeout=timeout)
        reader.join(timeout=5)
        if process.returncode:
            detail = b"".join(errors).decode("utf-8", "replace")[-600:]
            raise AnalysisError(f"Échec FFmpeg pendant l'analyse : {detail}")
        return b"".join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _video_curves(path: Path, samples: int, cancel_event):
    binary = ffmpeg_path()
    command = [binary, "-hide_banner", "-loglevel", "error", "-i", str(path), "-an", "-sn", "-dn",
               "-vf", f"fps={1 / STEP:g},scale={FRAME_W}:{FRAME_H}:flags=area,format=gray",
               "-f", "rawvideo", "pipe:1"]
    raw = _run_pipe(command, cancel_event)
    frame_size = FRAME_W * FRAME_H
    count = len(raw) // frame_size
    if count == 0:
        raise AnalysisError(f"Aucune image décodée pour {path.name}.")
    frames = np.frombuffer(raw[: count * frame_size], dtype=np.uint8).reshape(count, FRAME_H, FRAME_W).astype(np.float32)
    brightness = frames.mean(axis=(1, 2)) / 255.0
    diffs = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2)) / 255.0
    motion = np.concatenate([[diffs[0] if len(diffs) else 0.0], diffs])
    lap = (-4 * frames[:, 1:-1, 1:-1] + frames[:, :-2, 1:-1] + frames[:, 2:, 1:-1]
           + frames[:, 1:-1, :-2] + frames[:, 1:-1, 2:])
    sharpness = lap.reshape(count, -1).var(axis=1)
    # Changements de plan : pic de différence nettement au-dessus du voisinage.
    cuts = []
    if len(diffs) > 4:
        local = np.convolve(diffs, np.ones(9) / 9, mode="same")
        threshold = max(0.08, float(np.percentile(diffs, 95)))
        for index in range(1, len(diffs) - 1):
            value = diffs[index]
            if value > threshold and value > 1.8 * local[index] and value >= diffs[index - 1] and value >= diffs[index + 1]:
                cuts.append(round((index + 1) * STEP, 3))
    return _fit(motion, samples), _fit(brightness, samples), _fit(sharpness, samples), tuple(cuts)


def _audio_curve(path: Path, samples: int, cancel_event):
    """Retourne (niveau grossier par STEP, niveau fin par FINE_STEP) en dBFS."""
    binary = ffmpeg_path()
    command = [binary, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-sn", "-dn",
               "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "pipe:1"]
    raw = _run_pipe(command, cancel_event)
    pcm = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    window = int(AUDIO_RATE * FINE_STEP)
    count = len(pcm) // window
    fine_samples = samples * int(round(STEP / FINE_STEP))
    if count == 0:
        return np.full(samples, -90.0), np.full(fine_samples, -90.0)
    power = np.mean(pcm[: count * window].reshape(count, window) ** 2, axis=1)
    fine = 10 * np.log10(np.maximum(power, 1e-10))
    group = int(round(STEP / FINE_STEP))
    usable = len(power) // group * group
    coarse = 10 * np.log10(np.maximum(power[:usable].reshape(-1, group).mean(axis=1), 1e-10))
    return _fit(coarse, samples, fill=-90.0), _fit(fine, fine_samples, fill=-90.0)


def _fit(values, samples: int, fill: float | None = None) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if len(array) >= samples:
        return array[:samples]
    pad_value = fill if fill is not None else (array[-1] if len(array) else 0.0)
    return np.concatenate([array, np.full(samples - len(array), pad_value)])


def activity_segments(loudness, *, step: float = STEP, min_len: float = 0.5, merge_gap: float = 0.45,
                      pad: float = 0.15, duration: float | None = None, snap: bool = False,
                      short_len: float = 0.0, short_gap: float = 0.0) -> tuple[tuple[float, float], ...]:
    """Intervalles où le niveau sonore dépasse un seuil adaptatif (plancher de bruit + 9 dB).

    ``snap`` déplace chaque coupe vers le point le plus calme de la marge (respiration plutôt que
    milieu de mot). ``short_len``/``short_gap`` recollent un segment très court à son voisin quand
    la pause qui les sépare est brève : on garde alors la pause naturelle au lieu d'un micro-plan.
    """
    levels = np.asarray(loudness, dtype=np.float64)
    if not len(levels):
        return ()
    floor = float(np.percentile(levels, 15))
    peak = float(np.percentile(levels, 95))
    if peak - floor < 8 or peak < -50:
        return ()
    threshold = max(-48.0, floor + max(9.0, (peak - floor) * 0.35))
    active = levels > threshold
    segments: list[list[float]] = []
    start = None
    for index, flag in enumerate(active):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            segments.append([start * step, index * step])
            start = None
    if start is not None:
        segments.append([start * step, len(active) * step])
    merged: list[list[float]] = []
    for segment in segments:
        if merged and segment[0] - merged[-1][1] <= merge_gap:
            merged[-1][1] = segment[1]
        else:
            merged.append(segment)
    if short_len > 0:
        glued: list[list[float]] = []
        for segment in merged:
            if glued and segment[0] - glued[-1][1] <= short_gap and \
                    (segment[1] - segment[0] < short_len or glued[-1][1] - glued[-1][0] < short_len):
                glued[-1][1] = segment[1]
            else:
                glued.append(segment)
        merged = glued
    limit = duration if duration is not None else len(levels) * step

    def quietest(center: float, low: float, high: float) -> float:
        a, b = max(0, int(round(low / step))), min(len(levels) - 1, int(round(high / step)))
        if b < a:
            return center
        return (a + int(np.argmin(levels[a:b + 1]))) * step

    result = []
    for begin, end in merged:
        if end - begin < min_len:
            continue
        start_cut, end_cut = max(0.0, begin - pad), min(limit, end + pad)
        if snap and pad > 0.04:
            reach = min(pad / 2, 0.1)
            # La coupe reste dans la marge : jamais à l'intérieur de la parole détectée.
            start_cut = quietest(start_cut, start_cut - reach, min(start_cut + reach, begin - 0.03))
            end_cut = quietest(end_cut, max(end_cut - reach, end + 0.03), end_cut + reach)
            start_cut, end_cut = max(0.0, start_cut), min(limit, end_cut)
        result.append((round(start_cut, 3), round(end_cut, 3)))
    # Le padding peut faire se chevaucher deux segments voisins : on les recolle.
    joined: list[tuple[float, float]] = []
    for begin, end in result:
        if joined and begin <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(joined[-1][1], end))
        else:
            joined.append((begin, end))
    return tuple(joined)


def analyze_rush(path: str | Path, *, cache_dir: str | Path | None = None, info: MediaInfo | None = None,
                 cancel_event=None) -> RushAnalysis:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise AnalysisError(f"Rush introuvable : {source}")
    if not ffmpeg_path():
        raise AnalysisError("FFmpeg est introuvable.")
    cache_file = None
    if cache_dir is not None:
        cache_file = Path(cache_dir) / f"{file_fingerprint(source)}.json"
        if cache_file.is_file():
            try:
                return RushAnalysis.from_json(json.loads(cache_file.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, KeyError):
                LOGGER.warning("Analyse en cache illisible, recalcul : %s", cache_file)
    info = info or probe_media(source, cancel_event=cancel_event)
    if not info.duration or info.duration <= 0:
        raise AnalysisError(f"Durée inconnue pour {source.name}.")
    samples = max(1, int(np.ceil(info.duration / STEP)))
    # Image et son sont décodés en parallèle (deux processus FFmpeg indépendants).
    with ThreadPoolExecutor(max_workers=2) as pool:
        video_job = pool.submit(_video_curves, source, samples, cancel_event)
        audio_job = pool.submit(_audio_curve, source, samples, cancel_event) if info.has_audio else None
        motion, brightness, sharpness, cuts = video_job.result()
        loudness, fine = audio_job.result() if audio_job else (np.full(samples, -90.0), np.zeros(0))
    activity = activity_segments(fine, step=FINE_STEP, duration=info.duration) if info.has_audio else ()
    analysis = RushAnalysis(
        path=str(source), name=source.name, duration=float(info.duration),
        width=int(info.width or 0), height=int(info.height or 0), frame_rate=float(info.frame_rate or 30.0),
        has_audio=bool(info.has_audio), modified=source.stat().st_mtime,
        motion=tuple(round(float(v), 5) for v in motion),
        brightness=tuple(round(float(v), 4) for v in brightness),
        sharpness=tuple(round(float(v), 2) for v in sharpness),
        loudness=tuple(round(float(v), 2) for v in loudness),
        scene_cuts=cuts, activity=activity, fine_loudness=tuple(round(float(v), 1) for v in fine),
    )
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_suffix(".part")
        temporary.write_text(json.dumps(analysis.to_json()), encoding="utf-8")
        temporary.replace(cache_file)
    LOGGER.info("Rush analysé : %s (%.1f s, %d changement(s) de plan, %.0f %% d'activité sonore)",
                source.name, analysis.duration, len(cuts), analysis.speech_ratio * 100)
    return analysis


def interest_curve(analysis: RushAnalysis, *, motion_weight: float, audio_weight: float,
                   sharpness_weight: float, calm_weight: float = 0.0) -> np.ndarray:
    """Score 0..1 par échantillon ; les images trop sombres, brûlées ou floues sont pénalisées."""
    motion = np.asarray(analysis.motion)
    brightness = np.asarray(analysis.brightness)
    sharp = np.asarray(analysis.sharpness)
    loud = np.asarray(analysis.loudness)

    def norm(values):
        low, high = np.percentile(values, 5), np.percentile(values, 95)
        if high - low < 1e-9:
            return np.full(len(values), 0.5)
        return np.clip((values - low) / (high - low), 0, 1)

    motion_n = norm(np.convolve(motion, np.ones(3) / 3, mode="same"))
    sharp_n = norm(np.log1p(sharp))
    loud_n = norm(loud) if analysis.has_audio else np.zeros(len(loud))
    calm = 1 - np.abs(motion_n - 0.3)
    total_weight = motion_weight + audio_weight + sharpness_weight + calm_weight or 1
    score = (motion_weight * motion_n + audio_weight * loud_n + sharpness_weight * sharp_n
             + calm_weight * calm) / total_weight
    exposure = np.clip(np.minimum((brightness - 0.04) / 0.12, (0.985 - brightness) / 0.05), 0, 1)
    sharp_median = np.median(sharp) if len(sharp) else 0
    focus = np.clip(sharp / (0.35 * sharp_median + 1e-6), 0, 1) if sharp_median > 0 else np.ones(len(sharp))
    return score * (0.35 + 0.65 * exposure) * (0.5 + 0.5 * focus)
