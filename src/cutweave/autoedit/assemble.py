"""Rendu FFmpeg d'un plan de montage : plans normalisés puis assemblage en un master MP4.

1. Chaque plan est extrait, recadré au format de sortie, animé (punch, zoom, panoramique),
   normalisé à 30 images/s et stocké en cache (MKV + PCM pour un calage audio exact).
2. L'assemblage enchaîne les plans par coupes (concat) ou transitions (xfade / acrossfade),
   applique le look, les bandes cinéma et les fondus, puis encode le master.

Toutes les commandes sont des listes d'arguments (aucun shell).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Sequence

from cutweave.core.proc import run_hidden
from cutweave.assets.library import LOOKS_BY_KEY, tone
from cutweave.autoedit.analysis import file_fingerprint
from cutweave.autoedit.planner import FPS, EditPlan, Shot, shot_starts
from cutweave.media.render import (
    RenderError, _available_video_encoders, _run_ffmpeg, ffmpeg_path,
)
from cutweave.media.encoders import HARDWARE, choose_encoder, encoder_args, mark_unusable, tier_for

LOGGER = logging.getLogger("cutweave.autoedit")
SHOT_VERSION = "shot-v4"
ZOOM = 1.12
PUNCH = 1.16
PUNCH_SOFT = 1.08


# Filtres FFmpeg utilisés par le montage automatique : tous présents dans les builds LGPL.
REQUIRED_FILTERS = (
    "acrossfade", "aformat", "afade", "alimiter", "anull", "anullsrc", "apad", "aresample", "asetpts", "atempo",
    "atrim", "colorbalance", "concat", "crop", "curves", "drawbox", "fade", "format", "fps", "gblur", "hue",
    "lutyuv", "noise", "null", "overlay", "scale", "setpts", "setsar", "settb", "split", "tpad", "trim",
    "vignette", "volume", "xfade",
)


def available_filters(binary: str | None = None) -> set[str]:
    binary = binary or ffmpeg_path()
    if not binary:
        return set()
    try:
        output = run_hidden([binary, "-hide_banner", "-filters"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=20, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    # Le nombre de colonnes de drapeaux varie selon la version de FFmpeg (3 avant la 8, 2 ensuite).
    return set(re.findall(r"^\s*[A-Za-z.|]{2,4}\s+([A-Za-z0-9_]+)\s", output, re.M))


def missing_filters(binary: str | None = None) -> list[str]:
    present = available_filters(binary)
    return [name for name in REQUIRED_FILTERS if name not in present] if present else []


def filter_names(graph: str) -> set[str]:
    """Noms de filtres d'un graphe FFmpeg (expressions entre apostrophes ignorées)."""
    stripped = re.sub(r"'[^']*'", "''", graph)
    return set(re.findall(r"(?:^|[,;\]])\s*([a-z][a-z0-9_]*)(?==|,|;|\[|$)", stripped))


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def shot_video_filter(shot: Shot, width: int, height: int, fit: str) -> str:
    """Graphe vidéo d'un plan, de l'entrée ``[0:v]`` à la sortie ``[v]``."""
    zoom = (PUNCH if shot.effect == "punch" else PUNCH_SOFT if shot.effect == "punch_soft"
            else ZOOM if shot.effect != "none" else 1.0)
    canvas_w, canvas_h = _even(width * zoom), _even(height * zoom)
    length = shot.duration
    head = "[0:v]setpts=PTS-STARTPTS"
    if abs(shot.speed - 1.0) > 1e-6:
        head += f",setpts={1 / shot.speed:.6f}*PTS"
    head += f",fps={FPS}"
    if fit == "fit_blur":
        small_w, small_h = _even(canvas_w / 8), _even(canvas_h / 8)
        graph = [
            f"{head},split=2[bgsrc][fgsrc]",
            # Flou sur une version réduite (rapide, très doux) puis remise à la taille du canevas.
            f"[bgsrc]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,crop={small_w}:{small_h},"
            f"gblur=sigma=3:steps=2,scale={canvas_w}:{canvas_h}:flags=bilinear,{tone(brightness=-0.06)}[bg]",
            f"[fgsrc]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,setsar=1[fg]",
            "[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2,setsar=1[canvas]",
        ]
    else:
        graph = [f"{head},scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
                 f"crop={canvas_w}:{canvas_h},setsar=1[canvas]"]
    progress = f"min(t/{length:.4f},1)"
    if shot.effect in {"none", "punch", "punch_soft"}:
        motion = f"crop={width}:{height}"
    elif shot.effect in {"push_in", "pull_out"}:
        ratio = progress if shot.effect == "push_in" else f"(1-{progress})"
        factor = f"(1+{ZOOM - 1:.4f}*{ratio})"
        motion = (f"scale=w='2*trunc({width}*{factor}/2)':h='2*trunc({height}*{factor}/2)':eval=frame,"
                  f"crop={width}:{height}:x='(iw-{width})/2':y='(ih-{height})/2'")
    elif shot.effect in {"pan_left", "pan_right"}:
        ratio = progress if shot.effect == "pan_right" else f"(1-{progress})"
        motion = f"crop={width}:{height}:x='(iw-{width})*{ratio}':y='(ih-{height})/2'"
    else:
        raise RenderError(f"Effet de plan inconnu : {shot.effect}")
    graph.append(f"[canvas]{motion},setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration=2,trim=duration={length:.6f},setpts=PTS-STARTPTS[v]")
    return ";".join(graph)


def shot_audio_filter(shot: Shot, has_audio: bool) -> str:
    length = shot.duration
    if not has_audio or shot.audio_gain <= 0:
        return f"anullsrc=r=48000:cl=stereo,atrim=duration={length:.6f}[a]"
    chain = ["[0:a]asetpts=PTS-STARTPTS", "aresample=48000",
             "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
    if abs(shot.speed - 1.0) > 1e-6:
        chain.append(f"atempo={shot.speed:.4f}")
    chain.extend([f"volume={shot.audio_gain:.3f}", "apad", f"atrim=duration={length:.6f}",
                  "afade=t=in:st=0:d=0.02", f"afade=t=out:st={max(0.0, length - 0.03):.4f}:d=0.03[a]"])
    return ",".join(chain)


def shot_cache_key(shot: Shot, plan: EditPlan, has_audio: bool) -> str:
    raw = "|".join([SHOT_VERSION, file_fingerprint(Path(shot.source)), f"{shot.source_start:.3f}",
                    f"{shot.duration:.6f}", shot.effect, f"{shot.speed:.4f}", f"{shot.audio_gain:.3f}",
                    f"{plan.width}x{plan.height}", plan.fit, str(has_audio)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_shot_command(shot: Shot, plan: EditPlan, destination: Path, *, has_audio: bool, encoder: str) -> list[str]:
    binary = ffmpeg_path()
    if not binary:
        raise RenderError("FFmpeg est introuvable.")
    read = shot.source_duration + 0.25
    graph = shot_video_filter(shot, plan.width, plan.height, plan.fit) + ";" + shot_audio_filter(shot, has_audio)
    return [
        binary, "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-ss", f"{shot.source_start:.3f}", "-t", f"{read:.3f}", "-i", str(Path(shot.source).resolve()),
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", encoder, *encoder_args(encoder, tier_for(plan.width, plan.height), plan.width, plan.height, FPS),
        "-r", str(FPS), "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(destination),
    ]


def _letterbox_filter(width: int, height: int) -> str | None:
    target = 2.39 if width > height else None
    if target is None or width / height >= target:
        return None
    bar = _even((height - width / target) / 2)
    return (f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,"
            f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")


def build_assembly_graph(plan: EditPlan) -> tuple[str, str, str]:
    """Graphe d'assemblage des plans normalisés ; retourne (graphe, label vidéo, label audio)."""
    shots = plan.shots
    if not shots:
        raise RenderError("Le plan de montage est vide.")
    # Base de temps commune : xfade exige des entrées strictement identiques.
    filters: list[str] = [
        f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,fps={FPS},setsar=1[sv{index}];"
        f"[{index}:a]asetpts=PTS-STARTPTS[sa{index}]"
        for index in range(len(shots))
    ]
    # Regroupe les plans enchaînés par des coupes franches en séquences concaténées.
    runs: list[list[int]] = [[0]]
    for index in range(1, len(shots)):
        if shots[index].transition_in.kind == "cut":
            runs[-1].append(index)
        else:
            runs.append([index])
    run_labels: list[tuple[str, str, float]] = []
    for number, run in enumerate(runs):
        length = sum(shots[i].duration for i in run)
        if len(run) == 1:
            run_labels.append((f"sv{run[0]}", f"sa{run[0]}", length))
            continue
        inputs = "".join(f"[sv{i}][sa{i}]" for i in run)
        filters.append(f"{inputs}concat=n={len(run)}:v=1:a=1[rc{number}][ra{number}]")
        filters.append(f"[rc{number}]settb=AVTB,fps={FPS}[rv{number}]")
        run_labels.append((f"rv{number}", f"ra{number}", length))
    video, audio, total = run_labels[0]
    for number in range(1, len(runs)):
        transition = shots[runs[number][0]].transition_in
        next_video, next_audio, length = run_labels[number]
        offset = total - transition.duration
        filters.append(f"[{video}][{next_video}]xfade=transition={transition.kind}:"
                       f"duration={transition.duration:.6f}:offset={offset:.6f}[xv{number}]")
        filters.append(f"[{audio}][{next_audio}]acrossfade=d={transition.duration:.6f}:c1=tri:c2=tri[xa{number}]")
        video, audio = f"xv{number}", f"xa{number}"
        total = offset + length
    post: list[str] = []
    look = LOOKS_BY_KEY.get(plan.look)
    if look is not None:
        post.append(look.filters)
    if plan.letterbox:
        bars = _letterbox_filter(plan.width, plan.height)
        if bars:
            post.append(bars)
    if plan.fade_in > 0:
        post.append(f"fade=t=in:st=0:d={plan.fade_in:.3f}")
    if plan.fade_out > 0:
        post.append(f"fade=t=out:st={max(0.0, total - plan.fade_out):.3f}:d={plan.fade_out:.3f}")
    post.extend(["setsar=1", "format=yuv420p"])
    filters.append(f"[{video}]{','.join(post)}[vout]")
    audio_post = []
    if plan.fade_in > 0:
        audio_post.append(f"afade=t=in:st=0:d={min(plan.fade_in, 0.5):.3f}")
    if plan.fade_out > 0:
        audio_post.append(f"afade=t=out:st={max(0.0, total - plan.fade_out):.3f}:d={plan.fade_out:.3f}")
    audio_post.extend([f"atrim=duration={total:.6f}", "alimiter=limit=0.95"])
    filters.append(f"[{audio}]{','.join(audio_post)}[aout]")
    return ";".join(filters), "[vout]", "[aout]"


def build_assembly_command(plan: EditPlan, shot_files: Sequence[Path], destination: Path, *, encoder: str) -> list[str]:
    binary = ffmpeg_path()
    if not binary:
        raise RenderError("FFmpeg est introuvable.")
    if len(shot_files) != len(plan.shots):
        raise RenderError("Nombre de plans rendus incohérent avec le plan de montage.")
    command = [binary, "-hide_banner", "-loglevel", "error", "-nostats", "-y"]
    for path in shot_files:
        command.extend(["-i", str(Path(path).resolve())])
    graph, video, audio = build_assembly_graph(plan)
    command.extend([
        "-filter_complex", graph, "-map", video, "-map", audio, "-c:v", encoder,
        *encoder_args(encoder, tier_for(plan.width, plan.height), plan.width, plan.height, FPS), "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(destination),
    ])
    return command


class _AnyEvent:
    """Annulation demandée par l'utilisateur ou arrêt interne après l'échec d'un plan."""

    def __init__(self, outer):
        self.outer = outer
        self.local = threading.Event()

    def set(self):
        self.local.set()

    def is_set(self) -> bool:
        return self.local.is_set() or (self.outer is not None and self.outer.is_set())


def render_plan(
    plan: EditPlan,
    destination: str | Path,
    *,
    cache_dir: str | Path,
    has_audio: dict[str, bool],
    on_progress: Callable[[float], None] | None = None,
    cancel_event=None,
) -> Path:
    """Rend les plans (avec cache) puis assemble le master. ``on_progress`` reçoit 0..1."""
    binary = ffmpeg_path()
    if not binary:
        raise RenderError("FFmpeg est introuvable. Installez FFmpeg ou définissez CUTWEAVE_FFMPEG.")
    encoder = choose_encoder(binary, _available_video_encoders(binary), "fast")
    if encoder is None:
        raise RenderError("Aucun encodeur vidéo compatible n'est disponible dans cette installation de FFmpeg.")
    LOGGER.info("Encodeur du montage : %s", encoder)
    available_encoders = _available_video_encoders(binary)
    state = {"encoder": encoder}
    absent = missing_filters(binary)
    if absent:
        raise RenderError(
            "Cette version de FFmpeg ne contient pas les filtres nécessaires au montage automatique : "
            + ", ".join(absent) + ". Installez un build complet (par exemple BtbN FFmpeg LGPL) puis renseignez-le "
            "dans Paramètres."
        )
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    output = Path(destination).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = on_progress or (lambda _fraction: None)
    shot_weight = 0.7
    total_shots = len(plan.shots)
    files = [cache / f"{shot_cache_key(shot, plan, has_audio.get(shot.source, False))}.mkv" for shot in plan.shots]
    pending = [index for index, path in enumerate(files) if not path.is_file() or path.stat().st_size == 0]
    done = [0]
    lock = threading.Lock()
    stop = _AnyEvent(cancel_event)

    def run_with_fallback(make_command, **kwargs) -> None:
        """Exécute FFmpeg ; si un encodeur matériel échoue, bascule une fois sur l'encodeur suivant."""
        while True:
            current = state["encoder"]
            try:
                _run_ffmpeg(make_command(current), **kwargs)
                return
            except RenderError as exc:
                event = kwargs.get("cancel_event")
                cancelled = "annul" in str(exc).lower() or (event is not None and event.is_set())
                if cancelled or current not in HARDWARE:
                    raise
                mark_unusable(binary, current)
                alternative = choose_encoder(binary, available_encoders, "fast")
                if alternative is None or alternative == current:
                    raise
                LOGGER.warning("Encodeur %s en échec (%s) ; bascule sur %s.", current, str(exc)[-200:], alternative)
                state["encoder"] = alternative

    def render_shot(index: int) -> None:
        if stop.is_set():
            raise RenderError("Montage annulé.")
        shot, target = plan.shots[index], files[index]
        temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.part.mkv")
        try:
            run_with_fallback(
                lambda enc: build_shot_command(shot, plan, temporary, has_audio=has_audio.get(shot.source, False),
                                               encoder=enc),
                cwd=cache, duration_ms=round(shot.duration * 1000), on_progress=None,
                cancel_event=stop, cancel_message="Montage annulé.")
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RenderError(f"Le plan {index + 1} n'a produit aucun fichier.")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        with lock:
            done[0] += 1
            report(shot_weight * (total_shots - len(pending) + done[0]) / total_shots)

    # Plusieurs FFmpeg en parallèle : les plans sont courts et indépendants.
    unique = {}
    for index in pending:
        unique.setdefault(files[index], index)
    workers = max(1, min(4, (os.cpu_count() or 2) // 2 + 1))
    if encoder in HARDWARE:
        workers = min(workers, 2)  # le GPU est le goulot ; peu de sessions simultanées par carte
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(render_shot, index) for index in unique.values()]
        try:
            for future in futures:
                future.result()
        except BaseException:
            stop.set()  # arrête les autres plans en cours sans marquer la tâche comme annulée
            raise
    report(shot_weight)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.part.mp4")
    try:
        LOGGER.info("Assemblage de %d plans -> %s", len(files), output.name)
        duration_ms = round((shot_starts(plan.shots)[-1] + plan.shots[-1].duration) * 1000)
        run_with_fallback(lambda enc: build_assembly_command(plan, files, temporary, encoder=enc),
                          cwd=output.parent, duration_ms=duration_ms,
                          on_progress=lambda f: report(shot_weight + (1 - shot_weight) * (f or 0)),
                          cancel_event=cancel_event, cancel_message="Montage annulé.")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RenderError("L'assemblage n'a produit aucun fichier.")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    report(1.0)
    return output
