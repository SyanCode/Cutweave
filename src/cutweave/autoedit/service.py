"""Orchestration du montage automatique dans un projet CUTWEAVE.

Rushs → analyse → plan par variante → master assemblé → média du projet configuré (look,
musique avec ducking, SFX, titres) → rendu d'aperçu par le pipeline standard. Chaque variante
reste ensuite entièrement modifiable dans l'éditeur (étapes Visuel, Composition, Finition).
"""

from __future__ import annotations

import json
import math
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from cutweave.assets import library
from cutweave.autoedit.analysis import RushAnalysis, analyze_rush, trim_analysis
from cutweave.autoedit.assemble import render_plan
from cutweave.autoedit.beats import tempo_of_file
from cutweave.core.cache_tools import prune_oldest
from cutweave.autoedit.modes import MODES_BY_KEY, VARIANTS, EditMode, VariantProfile
from cutweave.autoedit.planner import FPS, EditPlan, EditRequest, MusicChoice, Transition, build_plan
from cutweave.autoedit.platforms import duration_warning, platform_by_key
from cutweave.core.pipeline import ProgressEvent, collect_job, run_job
from cutweave.media.probe import probe_audio, probe_media
from cutweave.media.render import VisualSettings
from cutweave.media.thumbnails import ThumbnailError, create_video_thumbnail
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.finishing.caption_presets import BUILTIN_BY_NAME
from cutweave.stages.finishing.music import DuckingSegment, MusicSettings, SfxPlacement
from cutweave.storage.project_store import ProjectMedia, ProjectStore
from cutweave.subtitles.align import TimedWord
from cutweave.subtitles.cues import cues_for_plan
from cutweave.subtitles.service import align_to_rushes, check_inputs, subtitle_preset

LOGGER = logging.getLogger("cutweave.autoedit")
MONTAGE_DIR = "montages"
STAGE = "autoedit"
ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1)}
DRAFT_SHORT = 540   # petit côté (px) des aperçus rapides ; l'export est rendu à la qualité demandée


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
    draft: bool = True
    clean_noise: bool = True
    normalize: bool = True       # volume final normalisé à -14 LUFS
    platform: str = "free"       # destination : zone de sécurité des sous-titres et avertissement de durée
    script: str = ""             # texte prononcé : permet de caler des sous-titres sur la voix (sans reconnaissance vocale)
    subtitles: str = "off"       # off | phrase | words
    ranges: tuple[tuple[float, float] | None, ...] = ()   # plage retenue par rush (None : rush entier), même ordre

    @property
    def is_draft(self) -> bool:
        """Aperçus rapides : montage rendu en petit, puis refait à la qualité demandée à l'export."""
        return self.draft and self.quality > DRAFT_SHORT

    def output_size(self, *, draft: bool = False) -> tuple[int, int]:
        if self.aspect not in ASPECTS:
            raise StudioError("Format inconnu : choisissez 9:16, 16:9 ou 1:1.")
        if self.quality < 120 or self.quality > 2160:
            raise StudioError("La qualité doit être comprise entre 120p et 2160p.")
        ratio_w, ratio_h = ASPECTS[self.aspect]
        short = min(self.quality, DRAFT_SHORT) if draft and self.is_draft else self.quality
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
    draft: bool = False          # aperçu rapide : le master sera refait en pleine qualité à l'export
    final_width: int = 0
    final_height: int = 0
    subtitle_quality: float = -1.0   # fiabilité 0..1 du calage du script ; -1 : pas de sous-titres
    platform: str = "free"
    warning: str = ""                # ex. durée supérieure à celle conseillée pour la plateforme
    subtitles: str = ""              # regroupement des sous-titres (phrase | words), vide : aucun
    subtitle_preset: str = ""

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


def configure_media(store: ProjectStore, media: ProjectMedia, plan: EditPlan, *, aspect: str, assets_root=None,
                    subtitle_cues=(), subtitle_preset: str | None = None, normalize: bool = True,
                    platform: str = "free") -> None:
    """Écrit les réglages éditables de la variante : chaque élément reste modifiable ensuite."""
    duration_ms = round((media.duration or plan.duration) * 1000)
    for module in ("captions", "music", "sfx", "sharpening"):
        store.set_module_enabled(module, True)
    store.set_module_enabled("loudness", normalize)
    store.set_visual_settings(media.id, VisualSettings(
        aspect, plan.width, plan.height, plan.sharpening, (), plan.sharpening > 0, 5, 0,
    ))
    store.set_framing_keyframes(media.id, ())
    store.set_masks(media.id, ())
    store.set_broll_placements(media.id, ())
    cues = []
    total = duration_ms / 1000
    placement = "top" if subtitle_cues else ""      # titres en haut quand des sous-titres occupent le bas
    if plan.title:
        cues.append(CaptionCue(250, round(min(2.8, max(1.0, total * 0.4)) * 1000), plan.title, placement))
    if plan.outro and total > 4:
        start = max(cues[-1].end_ms + 200 if cues else 0, duration_ms - 2600)
        if duration_ms - 100 > start:
            cues.append(CaptionCue(start, duration_ms - 100, plan.outro, placement))
    for cue in subtitle_cues:
        if cue.start_ms < duration_ms - 50:
            cues.append(CaptionCue(cue.start_ms, min(cue.end_ms, duration_ms), cue.text))
    store.set_captions(media.id, tuple(cues))
    preset_name = (subtitle_preset or plan.caption_preset).casefold()
    preset = BUILTIN_BY_NAME.get(preset_name, BUILTIN_BY_NAME["clean"])
    destination = platform_by_key(platform)
    if destination.caption_margin and preset.alignment in (1, 2, 3) and preset.margin_vertical_percent < destination.caption_margin:
        # Sous-titres relevés pour rester visibles au-dessus de l'interface de la plateforme.
        preset = replace(preset, name=f"{preset.name} ({destination.title})",
                         margin_vertical_percent=destination.caption_margin)
        store.save_custom_caption_preset(preset)
    store.set_caption_preset(media.id, preset)

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
    width, height = request.output_size(draft=True)
    final_width, final_height = request.output_size()
    emit = on_progress or (lambda _event: None)

    def report(fraction: float, message: str | None = None):
        emit(ProgressEvent(STAGE, message, max(0.0, min(1.0, fraction))))

    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise StudioError("Montage annulé.")

    analysis_dir = store.cache_root / "analysis"
    analyses: list[RushAnalysis] = [None] * len(request.rushes)  # type: ignore[list-item]
    finished = [0]

    def analyze(index: int):
        cancelled()
        analyses[index] = analyze_rush(request.rushes[index], cache_dir=analysis_dir, cancel_event=cancel_event)
        finished[0] += 1
        report(0.12 * finished[0] / len(request.rushes), f"Rush analysé : {Path(request.rushes[index]).name}")

    report(0.0, f"Analyse de {len(request.rushes)} rush(s)")
    with ThreadPoolExecutor(max_workers=min(3, len(request.rushes))) as pool:
        for future in [pool.submit(analyze, index) for index in range(len(request.rushes))]:
            future.result()
    for index, window in enumerate(request.ranges[: len(analyses)]):
        if window is not None:
            try:
                analyses[index] = trim_analysis(analyses[index], window[0], window[1])
            except ValueError as exc:
                raise StudioError(f"{Path(request.rushes[index]).name} : {exc}") from exc
    offsets = {analysis.path: analysis.offset for analysis in analyses}
    report(0.12, "Analyse terminée : construction des variantes")

    aligned = None
    if request.subtitles != "off":
        report(0.12, "Alignement du script sur la voix…")
        check_inputs(request.script, request.subtitles)
        aligned = align_to_rushes(request.script, analyses)
        report(0.13, f"Script calé sur la voix ({aligned.result.quality:.0%} de fiabilité, "
                     f"{aligned.result.matched_ratio:.0%} des syllabes appariées)")

    edit_request = EditRequest(request.mode, width, height, request.duration, request.look, request.music,
                               request.fit, request.title, request.outro, request.clean_noise)
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
        if any(offsets.values()):
            plan = replace(plan, shots=tuple(replace(shot, source_start=round(shot.source_start + offsets[shot.source], 3))
                                             for shot in plan.shots))
        key = f"{mode.key}_{variant.letter}"
        words = tuple(replace(word, start=word.start + offsets.get(word.source, 0.0),
                              end=word.end + offsets.get(word.source, 0.0))
                      for word in aligned.result.words) if aligned else ()
        subtitle_cues = cues_for_plan(words, plan, request.subtitles) if aligned else ()
        preset_key = subtitle_preset(mode.key, request.subtitles, mode.caption_preset) if aligned else ""
        if aligned:
            _write_json(store.root / MONTAGE_DIR / f"{key}.words.json", [asdict(word) for word in words])
        folder = store.root / MONTAGE_DIR
        _write_json(folder / f"{key}.plan.json", plan.to_json())
        master = render_plan(
            plan, folder / f"{key}.mp4", cache_dir=store.cache_root / "autoedit_shots", has_audio=has_audio,
            cancel_event=cancel_event, on_progress=lambda f, b=base: report(b + share * 0.55 * f),
        )
        cancelled()
        media = store.add_media(master, probe_media(master, cancel_event=cancel_event))
        configure_media(store, media, plan, aspect=request.aspect, assets_root=assets_root,
                        subtitle_cues=subtitle_cues,
                        subtitle_preset=preset_key or None, normalize=request.normalize, platform=request.platform)
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
            aspect=request.aspect, created_at=created, draft=request.is_draft,
            final_width=final_width, final_height=final_height,
            subtitle_quality=round(aligned.result.quality, 3) if aligned else -1.0,
            platform=request.platform, warning=duration_warning(platform_by_key(request.platform), plan.duration),
            subtitles=request.subtitles if aligned else "", subtitle_preset=preset_key,
        )
        _write_json(folder / f"{key}.result.json", result.to_json())
        results.append(result)
        report(base + share, f"{label} prête : {plan.summary}")
    try:
        prune_oldest(store.cache_root)
    except OSError as exc:
        LOGGER.warning("Élagage du cache impossible : %s", exc)
    return results


def _result_for_media(store: ProjectStore, media_id: int) -> VariantResult | None:
    return next((item for item in list_variants(store) if item.media_id == media_id), None)


def is_draft_montage(store: ProjectStore, media_id: int) -> bool:
    result = _result_for_media(store, media_id)
    return bool(result and result.draft)


def finalize_variant(store: ProjectStore, result: VariantResult, *, on_progress=None,
                     cancel_event=None) -> VariantResult:
    """Refait le master d'un aperçu rapide à la qualité demandée ; réglages et retouches conservés.

    Le plan est identique (même graine, mêmes extraits) : seule la résolution change. Les plans déjà
    rendus à cette résolution sont réutilisés depuis le cache.
    """
    if not result.draft:
        return result
    emit = on_progress or (lambda _event: None)
    folder = store.root / MONTAGE_DIR
    try:
        plan = EditPlan.from_json(json.loads((folder / f"{result.key}.plan.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StudioError(f"Plan de montage introuvable pour {result.key} : {exc}") from exc
    plan = replace(plan, width=result.final_width, height=result.final_height)
    sources = {shot.source for shot in plan.shots}
    has_audio = {source: analyze_rush(source, cache_dir=store.cache_root / "analysis",
                                      cancel_event=cancel_event).has_audio for source in sources}
    emit(ProgressEvent(STAGE, f"Rendu final {result.final_width}×{result.final_height} de la variante {result.letter}…", 0.0))
    master = render_plan(
        plan, folder / f"{result.key}.mp4", cache_dir=store.cache_root / "autoedit_shots", has_audio=has_audio,
        cancel_event=cancel_event,
        on_progress=lambda fraction: emit(ProgressEvent(STAGE, None, 0.6 * fraction)),
    )
    media = store.add_media(master, probe_media(master, cancel_event=cancel_event))
    settings = store.get_visual_settings(media.id)
    store.set_visual_settings(media.id, replace(settings, width=result.final_width, height=result.final_height))
    final = replace(result, draft=False)
    _write_json(folder / f"{result.key}.result.json", final.to_json())
    return final


def load_plan(store: ProjectStore, result: VariantResult) -> EditPlan:
    try:
        return EditPlan.from_json(json.loads((store.root / MONTAGE_DIR / f"{result.key}.plan.json")
                                             .read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StudioError(f"Plan de montage introuvable pour {result.key} : {exc}") from exc


def _remap(old_plan: EditPlan, new_plan: EditPlan, kept: list[int], time: float) -> float | None:
    """Reporte un instant de l'ancienne timeline sur la nouvelle ; None si son plan a été retiré."""
    old_starts, new_starts = old_plan.shot_starts, new_plan.shot_starts
    for new_index, old_index in enumerate(kept):
        begin = old_starts[old_index]
        length = old_plan.shots[old_index].duration
        if begin - 1e-6 <= time < begin + length:
            ratio = (time - begin) / length if length else 0.0
            return new_starts[new_index] + ratio * new_plan.shots[new_index].duration
    return None


def edit_plan_shots(plan: EditPlan, durations: Sequence[float], removed: Sequence[int] = (), *,
                    available: dict[str, float] | None = None) -> tuple[EditPlan, list[int]]:
    """Plan avec des durées de plans modifiées et/ou des plans retirés. Retourne (plan, indices d'origine gardés).

    La durée source d'un plan ne peut pas dépasser ce qui reste du rush : elle est bornée. Les musiques, effets sonores
    et coupes sont reportés sur la nouvelle timeline.
    """
    if len(durations) != len(plan.shots):
        raise StudioError("Nombre de durées incohérent avec le montage.")
    kept = [index for index in range(len(plan.shots)) if index not in set(removed)]
    if not kept:
        raise StudioError("Garde au moins un plan dans le montage.")
    shots = []
    for position, index in enumerate(kept):
        shot = plan.shots[index]
        duration = max(0.4, float(durations[index]))
        frames = round(duration * FPS)
        if available and shot.source in available:
            room = (available[shot.source] - shot.source_start) / shot.speed
            if frames / FPS > room:
                frames = max(int(0.4 * FPS), math.floor(room * FPS))
        duration = frames / FPS
        transition = shot.transition_in
        if position == 0:
            transition = Transition()
        elif transition.duration >= duration * 0.9:
            transition = replace(transition, duration=round(duration * 0.4, 3))
        shots.append(replace(shot, duration=round(duration, 6), transition_in=transition))
    new_plan = replace(plan, shots=tuple(shots))
    cut_times = tuple(new_plan.shot_starts[1:])
    duck = []
    for start, end in plan.duck_segments:
        a, b = _remap(plan, new_plan, kept, start), _remap(plan, new_plan, kept, max(start, end - 1e-3))
        if a is not None and b is not None and b - a > 0.2:
            duck.append((round(a, 3), round(b, 3)))
    sfx = []
    for cue in plan.sfx:
        moved = _remap(plan, new_plan, kept, cue.time)
        if moved is not None:
            sfx.append(replace(cue, time=round(moved, 3)))
    return replace(new_plan, cut_times=cut_times, duck_segments=tuple(duck), sfx=tuple(sfx)), kept


def apply_timeline_edit(store: ProjectStore, result: VariantResult, durations: Sequence[float],
                        removed: Sequence[int] = (), *, assets_root=None, on_progress=None,
                        cancel_event=None) -> VariantResult:
    """Applique des durées de plans choisies à la main, refait le montage et ses réglages (rendus déjà en cache réutilisés)."""
    emit = on_progress or (lambda _event: None)
    plan = load_plan(store, result)
    analysis_dir = store.cache_root / "analysis"
    analyses = {source: analyze_rush(source, cache_dir=analysis_dir, cancel_event=cancel_event)
                for source in {shot.source for shot in plan.shots}}
    new_plan, _kept = edit_plan_shots(plan, durations, removed,
                                      available={path: item.duration for path, item in analyses.items()})
    folder = store.root / MONTAGE_DIR
    emit(ProgressEvent(STAGE, "Nouveau montage…", 0.0))
    master = render_plan(
        new_plan, folder / f"{result.key}.mp4", cache_dir=store.cache_root / "autoedit_shots",
        has_audio={path: item.has_audio for path, item in analyses.items()}, cancel_event=cancel_event,
        on_progress=lambda fraction: emit(ProgressEvent(STAGE, None, 0.6 * fraction)),
    )
    media = store.add_media(master, probe_media(master, cancel_event=cancel_event))
    cues = ()
    words_path = folder / f"{result.key}.words.json"
    if result.subtitles and words_path.is_file():
        words = tuple(TimedWord(**item) for item in json.loads(words_path.read_text(encoding="utf-8")))
        cues = cues_for_plan(words, new_plan, result.subtitles)
    configure_media(store, media, new_plan, aspect=result.aspect, assets_root=assets_root, subtitle_cues=cues,
                    subtitle_preset=result.subtitle_preset or None, normalize=store.module_enabled("loudness"),
                    platform=result.platform)
    final_size = (result.final_width, result.final_height) if not result.draft else None
    if final_size:
        settings = store.get_visual_settings(media.id)
        store.set_visual_settings(media.id, replace(settings, width=final_size[0], height=final_size[1]))
    preview = run_job(store, collect_job(store, media), destination=store.root / "exports" / f"{result.key}.mp4",
                      cancel_event=cancel_event,
                      on_progress=lambda event: emit(ProgressEvent(STAGE, None, 0.6 + 0.4 * (event.fraction or 0)))
                      if event.fraction is not None else None)
    thumbnail = result.thumbnail
    try:
        thumb_w, thumb_h = (216, 384) if new_plan.height > new_plan.width else (384, 216) if new_plan.width > new_plan.height else (300, 300)
        thumbnail = str(create_video_thumbnail(preview, store.cache_root / "thumbnails" / f"{result.key}.jpg",
                                               time_ms=round(min(new_plan.duration * 0.3, 3.0) * 1000),
                                               width=thumb_w, height=thumb_h, cancel_event=cancel_event))
    except ThumbnailError as exc:
        LOGGER.warning("Miniature impossible pour %s : %s", result.key, exc)
    _write_json(folder / f"{result.key}.plan.json", new_plan.to_json())
    updated = replace(result, media_id=media.id, master=str(master), preview=str(preview), thumbnail=thumbnail,
                      duration=round(media.duration or new_plan.duration, 3), shots=len(new_plan.shots),
                      warning=duration_warning(platform_by_key(result.platform), new_plan.duration))
    _write_json(folder / f"{result.key}.result.json", updated.to_json())
    return updated


def ensure_final(store: ProjectStore, media_id: int, *, on_progress=None, cancel_event=None) -> bool:
    """Si ce média est un aperçu rapide du Studio, le refait en pleine qualité. Retourne vrai si fait."""
    result = _result_for_media(store, media_id)
    if result is None or not result.draft:
        return False
    finalize_variant(store, result, on_progress=on_progress, cancel_event=cancel_event)
    return True


def export_variant(store: ProjectStore, result: VariantResult, destination: str | Path, *,
                   on_progress=None, cancel_event=None) -> Path:
    """Exporte une variante avec ses éventuelles retouches (le cache évite tout réencodage inutile)."""
    media = next((item for item in store.list_media() if item.id == result.media_id), None)
    if media is None:
        raise StudioError("Le montage de cette variante n'existe plus dans le projet.")
    if result.draft:
        finalize_variant(store, result, on_progress=on_progress, cancel_event=cancel_event)
        media = next(item for item in store.list_media() if item.id == result.media_id)
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
