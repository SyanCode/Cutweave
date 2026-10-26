"""Orchestration du montage automatique dans un projet CUTWEAVE.

Rushs → analyse → plan par variante → master assemblé → média du projet configuré (look,
musique avec ducking, SFX, titres) → rendu d'aperçu par le pipeline standard. Chaque variante
reste ensuite entièrement modifiable dans l'éditeur (étapes Visuel, Composition, Finition).
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from cutweave.assets import library
from cutweave.autoedit.analysis import RushAnalysis, analyze_rush
from cutweave.autoedit.assemble import render_plan
from cutweave.autoedit.beats import tempo_of_file
from cutweave.autoedit.modes import MODES_BY_KEY, VARIANTS, EditMode, VariantProfile
from cutweave.autoedit.planner import EditPlan, EditRequest, MusicChoice, build_plan
from cutweave.core.pipeline import ProgressEvent, collect_job, run_job
from cutweave.media.probe import probe_audio, probe_media
from cutweave.media.render import VisualSettings
from cutweave.media.thumbnails import ThumbnailError, create_video_thumbnail
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.finishing.caption_presets import BUILTIN_BY_NAME
from cutweave.stages.finishing.music import DuckingSegment, MusicSettings, SfxPlacement
from cutweave.storage.project_store import ProjectMedia, ProjectStore

LOGGER = logging.getLogger("cutweave.autoedit")
MONTAGE_DIR = "montages"
STAGE = "autoedit"
ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1)}


class StudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class StudioRequest:
    rushes: tuple[str, ...]
    mode: str
    aspect: str = "9:16"
    quality: int = 1080
    duration: float | None = None
    music: str = "auto"
    look: str | None = None
    fit: str = "fill"
    title: str = ""
    outro: str = ""
    variants: int = 3

    def output_size(self) -> tuple[int, int]:
        if self.aspect not in ASPECTS:
            raise StudioError("Format inconnu : choisissez 9:16, 16:9 ou 1:1.")
        if self.quality < 120 or self.quality > 2160:
            raise StudioError("La qualité doit être comprise entre 120p et 2160p.")
        ratio_w, ratio_h = ASPECTS[self.aspect]
        short = self.quality
        if ratio_w <= ratio_h:
            width, height = short, short * ratio_h / ratio_w
        else:
            width, height = short * ratio_w / ratio_h, short
        return int(round(width / 2)) * 2, int(round(height / 2)) * 2


@dataclass(frozen=True)
class VariantResult:
    key: str
    mode: str
    letter: str
    name: str
    blurb: str
    media_id: int
    master: str
    preview: str
    thumbnail: str | None
    duration: float
    shots: int
    music_title: str
    look_title: str
    aspect: str
    created_at: str

    def to_json(self) -> dict:
        return asdict(self)


def is_montage(store: ProjectStore, media: ProjectMedia) -> bool:
    return (store.root / MONTAGE_DIR).resolve() in media.path.resolve().parents


def rush_media(store: ProjectStore) -> list[ProjectMedia]:
    return [media for media in store.list_media() if not is_montage(store, media)]


def list_variants(store: ProjectStore) -> list[VariantResult]:
    results = []
    folder = store.root / MONTAGE_DIR
    for path in sorted(folder.glob("*.result.json")) if folder.is_dir() else ():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(VariantResult(**data))
        except (OSError, ValueError, TypeError) as exc:
            LOGGER.warning("Variante illisible ignorée (%s) : %s", path.name, exc)
    return sorted(results, key=lambda item: (item.created_at, item.letter), reverse=True)


def resolve_music(choice: str, mode: EditMode, variant: VariantProfile, *, assets_root=None,
                  cancel_event=None) -> MusicChoice | None:
    if choice == "none":
        return None
    keys = {track.key for track in library.music_catalog(assets_root)}
    if choice == "auto" or choice in keys:
        key = mode.music[variant.rotation % len(mode.music)] if choice == "auto" else choice
        track = library.ensure_track(key, assets_root)
        return MusicChoice(key=track.key, path=str(track.path), title=track.title, bpm=track.bpm, offset=0.0,
                           volume=mode.music_volume)
    path = Path(choice).expanduser()
    if not path.is_file():
        raise StudioError(f"Musique introuvable : {path}")
    bpm, offset = tempo_of_file(path, cancel_event=cancel_event)
    LOGGER.info("Tempo estimé pour %s : %.1f BPM (premier temps à %.2f s)", path.name, bpm, offset)
    return MusicChoice(key=None, path=str(path.resolve()), title=path.stem, bpm=bpm, offset=offset,
                       volume=mode.music_volume)


def project_copy(store: ProjectStore, source: Path, kind: str) -> Path:
    """Copie un actif intégré dans le projet pour qu'il reste autonome et portable."""
    target = store.root / "assets" / kind / source.name
    if not target.is_file() or target.stat().st_size != source.stat().st_size:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return target


def _audio_asset(store: ProjectStore, path: Path, category: str, tags: Sequence[str]):
    for asset in store.list_audio_assets():
        if asset.path.resolve() == path.resolve():
            return asset
    return store.add_audio_asset(path, probe_audio(path), category=category, tags=tuple(tags))


def configure_media(store: ProjectStore, media: ProjectMedia, plan: EditPlan, *, aspect: str, assets_root=None) -> None:
    """Écrit les réglages éditables de la variante : chaque élément reste modifiable ensuite."""
    duration_ms = round((media.duration or plan.duration) * 1000)
    for module in ("captions", "music", "sfx", "sharpening"):
        store.set_module_enabled(module, True)
    store.set_visual_settings(media.id, VisualSettings(
        aspect, plan.width, plan.height, plan.sharpening, (), plan.sharpening > 0, 5, 0,
    ))
    store.set_framing_keyframes(media.id, ())
    store.set_masks(media.id, ())
    store.set_broll_placements(media.id, ())
    cues = []
    total = duration_ms / 1000
    if plan.title:
        cues.append(CaptionCue(250, round(min(2.8, max(1.0, total * 0.4)) * 1000), plan.title))
    if plan.outro and total > 4:
        start = max(cues[-1].end_ms + 200 if cues else 0, duration_ms - 2600)
        if duration_ms - 100 > start:
            cues.append(CaptionCue(start, duration_ms - 100, plan.outro))
    store.set_captions(media.id, tuple(cues))
    store.set_caption_preset(media.id, BUILTIN_BY_NAME.get(plan.caption_preset.casefold(), BUILTIN_BY_NAME["clean"]))

    store.set_music_mix(media.id, None)
    if plan.music is not None and plan.music.path:
        source = Path(plan.music.path)
        path = project_copy(store, source, "music") if plan.music.key else source
        asset = _audio_asset(store, path, "music", ("cutweave", plan.music.title))
        offset_ms = min(round(plan.music.offset * 1000), max(0, asset.duration_ms - 1000))
        fade_out = min(1800, duration_ms // 3)
        ducking = []
        if plan.duck_level < 1:
            for start, end in plan.duck_segments:
                start_ms, end_ms = round(start * 1000), min(round(end * 1000), duration_ms)
                if end_ms - start_ms >= 400:
                    ducking.append(DuckingSegment(start_ms, end_ms, plan.duck_level, 150))
        store.set_music_mix(media.id, MusicSettings(
            asset.id, 0, offset_ms, asset.duration_ms, plan.music.volume, 0 if plan.music.key else 200, fade_out,
            True, any(shot.audio_gain > 0 for shot in plan.shots), tuple(ducking),
        ))

    placements = []
    for cue in plan.sfx:
        start_ms = round(cue.time * 1000)
        if start_ms >= duration_ms - 50:
            continue
        effect = library.ensure_sfx(cue.key, assets_root)
        asset = _audio_asset(store, project_copy(store, effect.path, "sfx"), "sfx", ("cutweave", effect.category))
        length = min(asset.duration_ms, duration_ms - start_ms)
        placements.append(SfxPlacement(asset.id, start_ms, 0, length, cue.volume, 0, min(40, length // 2), "auto"))
    store.set_sfx_placements(media.id, tuple(placements))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def generate_variants(
    store: ProjectStore,
    request: StudioRequest,
    *,
    assets_root=None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    cancel_event=None,
) -> list[VariantResult]:
    mode = MODES_BY_KEY.get(request.mode)
    if mode is None:
        raise StudioError(f"Mode inconnu : {request.mode}")
    if not request.rushes:
        raise StudioError("Ajoutez au moins un rush avant de lancer le montage.")
    if not 1 <= request.variants <= len(VARIANTS):
        raise StudioError(f"Nombre de variantes entre 1 et {len(VARIANTS)}.")
    width, height = request.output_size()
    emit = on_progress or (lambda _event: None)

    def report(fraction: float, message: str | None = None):
        emit(ProgressEvent(STAGE, message, max(0.0, min(1.0, fraction))))

    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise StudioError("Montage annulé.")

    analysis_dir = store.cache_root / "analysis"
    analyses: list[RushAnalysis] = []
    for index, rush in enumerate(request.rushes):
        cancelled()
        report(0.12 * index / len(request.rushes), f"Analyse de {Path(rush).name}")
        analyses.append(analyze_rush(rush, cache_dir=analysis_dir, cancel_event=cancel_event))
    report(0.12, "Analyse terminée : construction des variantes")

    edit_request = EditRequest(request.mode, width, height, request.duration, request.look, request.music,
                               request.fit, request.title, request.outro)
    has_audio = {analysis.path: analysis.has_audio for analysis in analyses}
    results: list[VariantResult] = []
    share = 0.88 / request.variants
    created = datetime.now(timezone.utc).isoformat()
    for number, variant in enumerate(VARIANTS[: request.variants]):
        cancelled()
        base = 0.12 + number * share
        label = f"Variante {variant.letter} · {variant.name}"
        report(base, f"{label} : composition du montage")
        music = resolve_music(request.music, mode, variant, assets_root=assets_root, cancel_event=cancel_event)
        plan = build_plan(analyses, mode, variant, edit_request, music)
        key = f"{mode.key}_{variant.letter}"
        folder = store.root / MONTAGE_DIR
        _write_json(folder / f"{key}.plan.json", plan.to_json())
        master = render_plan(
            plan, folder / f"{key}.mp4", cache_dir=store.cache_root / "autoedit_shots", has_audio=has_audio,
            cancel_event=cancel_event, on_progress=lambda f, b=base: report(b + share * 0.55 * f),
        )
        cancelled()
        media = store.add_media(master, probe_media(master, cancel_event=cancel_event))
        configure_media(store, media, plan, aspect=request.aspect, assets_root=assets_root)
        report(base + share * 0.55, f"{label} : musique, effets et titres")
        job = collect_job(store, media)
        preview = run_job(
            store, job, destination=store.root / "exports" / f"{key}.mp4", cancel_event=cancel_event,
            on_progress=lambda event, b=base: report(b + share * (0.55 + 0.42 * (event.fraction or 0)))
            if event.fraction is not None else None,
        )
        thumbnail = None
        try:
            thumb_w, thumb_h = (216, 384) if height > width else (384, 216) if width > height else (300, 300)
            thumbnail = str(create_video_thumbnail(
                preview, store.cache_root / "thumbnails" / f"{key}.jpg",
                time_ms=round(min(plan.duration * 0.3, 3.0) * 1000), width=thumb_w, height=thumb_h,
                cancel_event=cancel_event,
            ))
        except ThumbnailError as exc:
            LOGGER.warning("Miniature impossible pour %s : %s", key, exc)
        look = library.LOOKS_BY_KEY.get(plan.look)
        result = VariantResult(
            key=key, mode=mode.key, letter=variant.letter, name=variant.name, blurb=variant.blurb,
            media_id=media.id, master=str(master), preview=str(preview), thumbnail=thumbnail,
            duration=round(media.duration or plan.duration, 3), shots=len(plan.shots),
            music_title=music.title if music else "Sans musique", look_title=look.title if look else plan.look,
            aspect=request.aspect, created_at=created,
        )
        _write_json(folder / f"{key}.result.json", result.to_json())
        results.append(result)
        report(base + share, f"{label} prête : {plan.summary}")
    return results


def export_variant(store: ProjectStore, result: VariantResult, destination: str | Path, *,
                   on_progress=None, cancel_event=None) -> Path:
    """Exporte une variante avec ses éventuelles retouches (le cache évite tout réencodage inutile)."""
    media = next((item for item in store.list_media() if item.id == result.media_id), None)
    if media is None:
        raise StudioError("Le montage de cette variante n'existe plus dans le projet.")
    return run_job(store, collect_job(store, media), destination=destination, cancel_event=cancel_event,
                   on_progress=on_progress)


def default_project_dir(projects_dir: str = "") -> Path:
    base = Path(projects_dir).expanduser() if projects_dir else Path.home() / "Videos" / "CUTWEAVE"
    stamp = datetime.now().strftime("%Y-%m-%d %Hh%M")
    candidate = base / f"Montage {stamp}"
    suffix = 2
    while candidate.exists():
        candidate = base / f"Montage {stamp} ({suffix})"
        suffix += 1
    return candidate
