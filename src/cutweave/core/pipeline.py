"""Assemblage du pipeline de montage d'un média de projet, indépendant de l'interface.

Le pipeline comporte trois étapes ordonnées puis un rendu final :

* ``visual`` : cadrage (keyframes) et netteté ;
* ``composition`` : masques, B-roll (manuel et règles explicites) et données de captions ;
* ``finishing`` : preset captions, musique (avec ducking) et SFX (manuels et règles) ;
* ``render`` : rendu FFmpeg final en une seule passe à partir des instructions.

Le pipeline complet n'écrit que des instructions JSON pour les étapes 1 à 3, puis encode une
seule fois (pas de réencodage intermédiaire). Lancer une étape seule produit en revanche un
MP4 intermédiaire vérifiable pour cette étape.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from pathlib import Path
from typing import Any, Callable

from cutweave.core.cache import StageCache
from cutweave.core.orchestrator import PipelineArtifact, PipelineOrchestrator, PipelineStage
from cutweave.media.render import (
    VisualSettings, build_ass_content, build_visual_filter, ffmpeg_path, render_visual,
)
from cutweave.stages.composition.broll import BrollRenderClip
from cutweave.stages.composition.broll_rules import apply_broll_rules
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.composition.masking import MaskRegion
from cutweave.stages.finishing.caption_presets import BUILTIN_BY_NAME, CaptionPreset
from cutweave.stages.finishing.music import MusicRenderTrack, SfxRenderClip, effective_ducking
from cutweave.stages.finishing.sfx_rules import apply_sfx_rules, timeline_events
from cutweave.storage.project_store import ProjectMedia, ProjectStore

LOGGER = logging.getLogger("cutweave.pipeline")

STAGE_IDS = ("visual", "composition", "finishing", "render")
STAGE_LABELS = {
    "visual": "Étape 1 — Visuel",
    "composition": "Étape 2 — Composition",
    "finishing": "Étape 3 — Finition",
    "render": "Rendu final",
}
RENDER_MODULE_VERSION = "visual-render-v9"


def state_file_name(media_id: int) -> str:
    """Un checkpoint par média : la reprise d'une vidéo n'écrase pas celle d'une autre."""
    return f"state_media_{int(media_id)}.json"


def stage_statuses(store: ProjectStore, media_id: int) -> dict[str, dict]:
    """Statut enregistré de chaque étape (pour l'affichage) ; vide si jamais exécuté."""
    path = store.root / ".cutweave_pipeline" / state_file_name(media_id)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stages = state.get("stages")
    return stages if isinstance(stages, dict) else {}


class PipelineInputError(ValueError):
    """Configuration du projet incomplète ou incohérente, à corriger par l'utilisateur."""


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str | None = None
    fraction: float | None = None


@dataclass(frozen=True)
class PipelineJob:
    media: ProjectMedia
    settings: VisualSettings
    captions: tuple[CaptionCue, ...]
    caption_preset: CaptionPreset
    masks: tuple[MaskRegion, ...]
    broll: tuple[BrollRenderClip, ...]
    music: MusicRenderTrack | None
    sfx: tuple[SfxRenderClip, ...]
    source_has_audio: bool | None
    duration_ms: int | None
    modules: dict[str, bool] = field(default_factory=dict)
    generated_broll: int = 0
    generated_sfx: int = 0

    @property
    def visual_parameters(self) -> dict[str, Any]:
        return {"settings": self.settings}

    @property
    def composition_parameters(self) -> dict[str, Any]:
        return {"captions": self.captions, "masks": self.masks,
                "broll": [clip.placement for clip in self.broll]}

    @property
    def finishing_parameters(self) -> dict[str, Any]:
        return {
            "captions": self.captions,
            "caption_preset": self.caption_preset,
            "music": self.music.settings if self.music is not None else None,
            "music_duration_ms": self.music.timeline_duration_ms if self.music is not None else None,
            "sfx": [clip.placement for clip in self.sfx],
            "source_has_audio": self.source_has_audio,
        }

    def input_files(self, *, source=True, broll=True, audio=True) -> tuple[tuple[str, Path], ...]:
        files: list[tuple[str, Path]] = [("source", self.media.path)] if source else []
        if broll:
            files.extend((f"broll_{index}", clip.path) for index, clip in enumerate(self.broll))
        if audio:
            if self.music is not None:
                files.append(("music", self.music.path))
            files.extend((f"sfx_{index}", clip.path) for index, clip in enumerate(self.sfx))
        return tuple(files)


def collect_job(store: ProjectStore, media: ProjectMedia) -> PipelineJob:
    """Lit et valide toutes les instructions de montage d'un média ; aucune écriture."""
    if media.missing:
        raise PipelineInputError(f"Le média source est introuvable : {media.path}")
    modules = store.modules()
    duration_ms = round(media.duration * 1000) if media.duration else None
    settings = replace(store.get_visual_settings(media.id),
                       framing_keyframes=store.get_framing_keyframes(media.id))
    if not modules["framing"]:
        settings = replace(settings, framing_keyframes=())
    if not modules["sharpening"]:
        settings = replace(settings, sharpening_enabled=False)
    try:
        settings.validate()
    except ValueError as exc:
        raise PipelineInputError(f"Réglages visuels invalides : {exc}") from exc

    # Les captions servent aussi de données de synchronisation (règles B-roll, SFX, ducking)
    # même si leur incrustation est désactivée.
    caption_data = store.get_captions(media.id)
    captions = caption_data if modules["captions"] else ()
    caption_preset = store.get_caption_preset(media.id) or BUILTIN_BY_NAME["clean"]

    masks = store.get_masks(media.id) if modules["masks"] else ()
    for index, mask in enumerate(masks, start=1):
        try:
            mask.validate(settings.width, settings.height)
        except ValueError as exc:
            raise PipelineInputError(f"Masque {index} incompatible avec la sortie "
                                     f"{settings.width}×{settings.height} : {exc}") from exc

    broll: list[BrollRenderClip] = []
    generated_broll = 0
    if modules["broll"]:
        assets = store.list_broll_assets()
        assets_by_id = {asset.id: asset for asset in assets}
        placements = list(store.get_broll_placements(media.id))
        try:
            generated = apply_broll_rules(store.get_broll_rules(), caption_data, assets, placements, duration_ms)
        except ValueError as exc:
            raise PipelineInputError(str(exc)) from exc
        generated_broll = len(generated)
        for placement in (*placements, *generated):
            asset = assets_by_id.get(placement.asset_id)
            if asset is None or asset.missing:
                name = asset.name if asset else placement.asset_id
                raise PipelineInputError(f"Un clip B-roll référencé est introuvable : {name}")
            broll.append(BrollRenderClip(asset.path, placement))

    music = None
    source_has_audio = media.has_audio
    if modules["music"]:
        music_settings = store.get_music_mix(media.id)
        if music_settings is not None:
            audio_asset = store.get_audio_asset(music_settings.asset_id)
            if audio_asset is None or audio_asset.missing:
                name = audio_asset.name if audio_asset else music_settings.asset_id
                raise PipelineInputError(f"Fichier musical introuvable : {name}")
            ducking = effective_ducking(music_settings, caption_data, duration_ms)
            music = MusicRenderTrack(audio_asset.path, replace(music_settings, ducking_segments=ducking), duration_ms)
            source_has_audio = bool(media.has_audio and music_settings.mix_original_audio)

    sfx: list[SfxRenderClip] = []
    generated_sfx = 0
    if modules["sfx"]:
        audio_assets = {asset.id: asset for asset in store.list_audio_assets()}
        placements = list(store.get_sfx_placements(media.id))
        events = timeline_events(
            duration_ms, captions=caption_data, broll=[clip.placement for clip in broll],
            markers=store.get_markers(media.id),
            caption_animated=bool(captions) and caption_preset.animation != "none",
        )
        try:
            generated = apply_sfx_rules(store.get_sfx_rules(), events,
                                        {key: asset.duration_ms for key, asset in audio_assets.items()}, duration_ms)
        except ValueError as exc:
            raise PipelineInputError(str(exc)) from exc
        generated_sfx = len(generated)
        for placement in (*placements, *generated):
            asset = audio_assets.get(placement.asset_id)
            if asset is None or asset.missing:
                name = asset.name if asset else placement.asset_id
                raise PipelineInputError(f"Fichier SFX introuvable : {name}")
            sfx.append(SfxRenderClip(asset.path, placement, duration_ms))

    return PipelineJob(
        media=media, settings=settings, captions=tuple(captions), caption_preset=caption_preset,
        masks=tuple(masks), broll=tuple(broll), music=music, sfx=tuple(sfx),
        source_has_audio=source_has_audio, duration_ms=duration_ms, modules=modules,
        generated_broll=generated_broll, generated_sfx=generated_sfx,
    )


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Paramètre non sérialisable : {type(value).__name__}")


def _write_json_atomic(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, default=_json_default,
                                        sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def build_stages(
    job: PipelineJob,
    orchestrator: PipelineOrchestrator,
    cache: StageCache,
    *,
    step_mode: bool,
    emit: Callable[[ProgressEvent], None],
) -> tuple[PipelineStage, ...]:
    """Construit les quatre étapes du pipeline.

    ``step_mode`` : chaque étape produit un MP4 intermédiaire (lancement d'une étape seule).
    Sinon, les étapes 1 à 3 compilent leurs instructions et seul ``render`` encode.
    """
    media = job.media
    stage_dir = orchestrator.state_root / "stages"
    neutral = VisualSettings(job.settings.aspect, job.settings.width, job.settings.height, 0, (), False,
                             job.settings.sharpening_radius, job.settings.sharpening_threshold)

    def fraction_reporter(stage_id):
        return lambda fraction: emit(ProgressEvent(stage_id, None, fraction))

    def snapshot(stage_name, parameters, compile_instructions):
        def execute(_context, _dependencies, _cancel, stage_progress):
            stage_progress("Compilation des instructions de montage.")
            target = stage_dir / f"{stage_name}_{media.id}.json"
            _write_json_atomic(target, {"stage": stage_name, "media": media.name, "parameters": parameters,
                                        "instructions": compile_instructions()})
            return PipelineArtifact(target, {"kind": "stage_instructions"})
        return execute

    def render_step(stage_name, source_of, settings, **render_kwargs):
        def execute(_context, dependencies, stage_cancel, _stage_progress):
            target = stage_dir / f"{stage_name}_{media.id}.mp4"
            render_visual(source_of(dependencies), target, settings, cancel_event=stage_cancel,
                          on_progress=fraction_reporter(stage_name), duration_ms=job.duration_ms,
                          **render_kwargs)
            return PipelineArtifact(target, {"kind": "video/mp4", "stage": stage_name})
        return execute

    if step_mode:
        visual_execute = render_step("visual", lambda _deps: media.path, job.settings,
                                     source_has_audio=media.has_audio)
        composition_execute = render_step(
            "composition", lambda deps: deps["visual"].path, neutral, masks=job.masks, broll=job.broll,
            source_has_audio=media.has_audio,
        )
        finishing_execute = render_step(
            "finishing", lambda deps: deps["composition"].path, neutral, captions=job.captions,
            caption_preset=job.caption_preset, music=job.music, sfx=job.sfx,
            source_has_audio=job.source_has_audio,
        )
        suffix = "step-v3"
    else:
        visual_execute = snapshot("visual", job.visual_parameters, lambda: {
            "filter_chain": build_visual_filter(job.settings),
            "sharpening": {
                "enabled": job.settings.sharpening_enabled, "intensity": job.settings.sharpening,
                "radius": job.settings.sharpening_radius, "threshold": job.settings.sharpening_threshold,
            },
        })
        composition_execute = snapshot("composition", job.composition_parameters, lambda: {
            "caption_cues": [asdict(cue) for cue in job.captions],
            "masks": [{"shape": mask.shape, "effect": mask.effect, "opacity": mask.opacity,
                       "active_expression": mask.active_expression(), "blur_radius": mask.blur_radius}
                      for mask in job.masks],
            "broll_placements": [asdict(clip.placement) for clip in job.broll],
            "broll_generated_by_rules": job.generated_broll,
        })
        finishing_execute = snapshot("finishing", job.finishing_parameters, lambda: {
            "ass_script": build_ass_content(job.captions, job.settings, job.caption_preset) if job.captions else None,
            "music": asdict(job.music.settings) if job.music is not None else None,
            "sfx": [asdict(clip.placement) for clip in job.sfx],
            "sfx_generated_by_rules": job.generated_sfx,
        })
        suffix = "instructions-v3"

    render_parameters = {
        **job.visual_parameters, **job.composition_parameters, **job.finishing_parameters,
        "ffmpeg_version": StageCache.executable_version(ffmpeg_path()),
    }

    def render_execute(_context, _dependencies, stage_cancel, stage_progress):
        key = cache.make_key("render", job.input_files(), render_parameters,
                             module_version=RENDER_MODULE_VERSION, cancel_event=stage_cancel)
        output, hit = cache.get_or_create(
            "render", key, ".mp4",
            lambda path: render_visual(
                media.path, path, job.settings, cancel_event=stage_cancel,
                on_progress=fraction_reporter("render"), captions=job.captions,
                caption_preset=job.caption_preset, masks=job.masks, broll=job.broll, music=job.music,
                sfx=job.sfx, source_has_audio=job.source_has_audio, duration_ms=job.duration_ms,
            ),
            cancel_event=stage_cancel,
        )
        stage_progress("Rendu repris depuis le cache." if hit else "Rendu final encodé.")
        return PipelineArtifact(output, {"cache_hit": hit})

    return (
        PipelineStage("visual", f"visual-{suffix}", visual_execute,
                      input_files=lambda _ctx: job.input_files(broll=False, audio=False),
                      parameters=lambda _ctx: job.visual_parameters),
        PipelineStage("composition", f"composition-{suffix}", composition_execute, ("visual",),
                      input_files=lambda _ctx: job.input_files(source=False, audio=False),
                      parameters=lambda _ctx: job.composition_parameters),
        PipelineStage("finishing", f"finishing-{suffix}", finishing_execute, ("composition",),
                      input_files=lambda _ctx: job.input_files(source=False, broll=False),
                      parameters=lambda _ctx: job.finishing_parameters),
        PipelineStage("render", RENDER_MODULE_VERSION, render_execute, ("finishing",),
                      input_files=lambda _ctx: job.input_files(),
                      parameters=lambda _ctx: render_parameters),
    )


def run_job(
    store: ProjectStore,
    job: PipelineJob,
    *,
    target_stage: str | None = None,
    destination: str | Path | None = None,
    cancel_event=None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    force: tuple[str, ...] = (),
) -> Path:
    """Exécute une étape (avec ses dépendances) ou le pipeline complet jusqu'à l'export.

    Le statut ``succeeded`` n'est enregistré qu'après la copie effective du fichier final.
    """
    if target_stage is not None and target_stage not in STAGE_IDS[:3]:
        raise PipelineInputError(f"Étape inconnue : {target_stage}")
    if target_stage is None:
        if destination is None:
            raise PipelineInputError("Un fichier de destination est requis pour le pipeline complet.")
        destination = Path(destination).expanduser().resolve()
        if destination.suffix.lower() != ".mp4":
            raise PipelineInputError("Le fichier exporté doit porter l'extension .mp4.")
        if destination == job.media.path.resolve():
            raise PipelineInputError("Le fichier source ne peut pas être remplacé par son export.")
    emit = on_progress or (lambda _event: None)
    task_kind = "pipeline_export" if target_stage is None else f"pipeline_step_{target_stage}"
    task_id = store.record_task(task_kind, "running", input_path=job.media.path, output_path=destination)
    cache = StageCache(store.cache_root)
    orchestrator = PipelineOrchestrator(store.root, cache, state_name=state_file_name(job.media.id))
    LOGGER.info("Pipeline %s démarré pour %s", target_stage or "complet", job.media.name)
    try:
        stages = build_stages(job, orchestrator, cache, step_mode=target_stage is not None, emit=emit)
        results = orchestrator.run(
            stages, {}, targets=(target_stage,) if target_stage else None, force=force,
            cancel_event=cancel_event,
            on_progress=lambda stage_id, message: emit(ProgressEvent(stage_id, message)),
        )
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Pipeline annulé.")
        if target_stage is not None:
            output = results[target_stage].path
        else:
            output = StageCache.copy_to(results["render"].path, destination)
        store.record_task(task_kind, "succeeded", output_path=output, task_id=task_id)
        LOGGER.info("Pipeline %s terminé : %s", target_stage or "complet", output)
        return output
    except Exception as exc:
        status = "cancelled" if cancel_event is not None and cancel_event.is_set() else "failed"
        store.record_task(task_kind, status, output_path=destination, error=str(exc), task_id=task_id)
        if status == "failed":
            LOGGER.exception("Échec du pipeline pour %s", job.media.name)
        else:
            LOGGER.info("Pipeline annulé pour %s", job.media.name)
        raise
