"""Modèles de bibliothèque audio et de mixage musical."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class MusicValidationError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class DuckingSegment:
    start_ms: int
    end_ms: int
    level: float = 0.25
    fade_ms: int = 80

    def validate(self, timeline_duration_ms: int | None = None) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise MusicValidationError("Un intervalle de ducking doit avoir une fin après son début.")
        if timeline_duration_ms is not None and self.end_ms > timeline_duration_ms:
            raise MusicValidationError("Un intervalle de ducking dépasse la durée de la vidéo.")
        if not 0 <= self.level <= 1 or self.fade_ms < 0:
            raise MusicValidationError("Le niveau de ducking doit être entre 0 et 1 et le fondu positif.")
        if self.fade_ms * 2 > self.end_ms - self.start_ms:
            raise MusicValidationError("Les fondus du ducking dépassent la durée de l'intervalle.")


@dataclass(frozen=True)
class AudioAsset:
    id: int
    path: Path
    name: str
    duration_ms: int
    category: str = "music"
    tags: tuple[str, ...] = ()

    @property
    def missing(self) -> bool:
        return not self.path.is_file()


@dataclass(frozen=True)
class MusicSettings:
    asset_id: int
    timeline_start_ms: int = 0
    source_start_ms: int = 0
    source_end_ms: int = 1
    volume: float = 0.25
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    loop: bool = False
    mix_original_audio: bool = True
    ducking_segments: tuple[DuckingSegment, ...] = ()
    duck_on_captions: bool = False
    caption_duck_level: float = 0.3
    caption_duck_fade_ms: int = 120

    @property
    def duration_ms(self) -> int:
        return self.source_end_ms - self.source_start_ms

    def validate(self, asset_duration_ms: int | None = None, play_duration_ms: int | None = None) -> None:
        if self.asset_id <= 0:
            raise MusicValidationError("L'identifiant du fichier audio doit être positif.")
        if min(self.timeline_start_ms, self.source_start_ms, self.fade_in_ms, self.fade_out_ms) < 0:
            raise MusicValidationError("Les positions et durées audio ne peuvent pas être négatives.")
        if self.source_end_ms <= self.source_start_ms:
            raise MusicValidationError("La sortie source doit suivre l'entrée source.")
        if asset_duration_ms is not None and self.source_end_ms > asset_duration_ms:
            raise MusicValidationError("La sortie demandée dépasse la durée du fichier audio.")
        if not 0 <= self.volume <= 4:
            raise MusicValidationError("Le volume doit être compris entre 0 et 4.")
        fade_limit = play_duration_ms if self.loop and play_duration_ms is not None else self.duration_ms
        if self.fade_in_ms + self.fade_out_ms > fade_limit:
            raise MusicValidationError("La somme des fondus ne peut pas dépasser la durée musicale.")
        if not 0 <= self.caption_duck_level <= 1 or not 0 <= self.caption_duck_fade_ms <= 5000:
            raise MusicValidationError("Ducking captions : niveau entre 0 et 1, fondu entre 0 et 5000 ms.")
        ordered_ducking = tuple(sorted(self.ducking_segments))
        for index, segment in enumerate(ordered_ducking):
            segment.validate()
            if index and ordered_ducking[index - 1].end_ms > segment.start_ms:
                raise MusicValidationError("Les intervalles de ducking ne peuvent pas se chevaucher.")


def parse_ducking_segments(value: str) -> tuple[DuckingSegment, ...]:
    """Format éditable : start_ms,end_ms,niveau[,fondu_ms]; ..."""
    segments = []
    if not value.strip():
        return ()
    try:
        for item in value.split(";"):
            fields = [field.strip() for field in item.split(",")]
            if len(fields) not in {3, 4}:
                raise ValueError
            segments.append(DuckingSegment(int(fields[0]), int(fields[1]), float(fields[2]),
                                           int(fields[3]) if len(fields) == 4 else 80))
    except (ValueError, TypeError) as exc:
        raise MusicValidationError("Format ducking invalide. Utilisez debut_ms,fin_ms,niveau[,fondu_ms].") from exc
    return tuple(sorted(segments))


def format_ducking_segments(segments: tuple[DuckingSegment, ...]) -> str:
    return "; ".join(f"{item.start_ms},{item.end_ms},{item.level:g},{item.fade_ms}" for item in segments)


def combine_ducking(
    segments, *, level: float | None = None, fade_ms: int | None = None, merge_gap_ms: int = 0,
    timeline_duration_ms: int | None = None,
) -> tuple[DuckingSegment, ...]:
    """Fusionne des intervalles (start_ms, end_ms[, level, fade_ms]) qui se chevauchent ou sont proches.

    Le niveau retenu est le plus bas des intervalles fusionnés ; les fondus sont réduits
    si nécessaire pour respecter la durée de chaque intervalle.
    """
    items = []
    for segment in segments:
        if isinstance(segment, DuckingSegment):
            start, end, seg_level, seg_fade = segment.start_ms, segment.end_ms, segment.level, segment.fade_ms
        else:
            start, end = int(segment[0]), int(segment[1])
            seg_level = level if level is not None else 0.25
            seg_fade = fade_ms if fade_ms is not None else 80
        if timeline_duration_ms is not None:
            end = min(end, timeline_duration_ms)
        if end > start >= 0:
            items.append([start, end, seg_level, seg_fade])
    items.sort()
    merged: list[list] = []
    for item in items:
        if merged and item[0] <= merged[-1][1] + merge_gap_ms:
            previous = merged[-1]
            previous[1] = max(previous[1], item[1])
            previous[2] = min(previous[2], item[2])
            previous[3] = max(previous[3], item[3])
        else:
            merged.append(item)
    return tuple(
        DuckingSegment(start, end, seg_level, min(seg_fade, (end - start) // 2))
        for start, end, seg_level, seg_fade in merged
    )


def ducking_from_captions(cues, level: float, fade_ms: int, timeline_duration_ms: int | None = None):
    """Intervalles de voix déclarés par les captions saisies ou importées par l'utilisateur."""
    return combine_ducking(
        ((cue.start_ms, cue.end_ms) for cue in cues), level=level, fade_ms=fade_ms,
        merge_gap_ms=fade_ms * 2, timeline_duration_ms=timeline_duration_ms,
    )


def effective_ducking(settings: MusicSettings, cues=(), timeline_duration_ms: int | None = None):
    """Ducking manuel et, si activé, dérivé des timestamps de captions."""
    segments = list(settings.ducking_segments)
    if settings.duck_on_captions:
        segments.extend(ducking_from_captions(cues, settings.caption_duck_level, settings.caption_duck_fade_ms,
                                              timeline_duration_ms))
    return combine_ducking(segments, timeline_duration_ms=timeline_duration_ms)


@dataclass(frozen=True)
class MusicRenderTrack:
    path: Path
    settings: MusicSettings
    timeline_duration_ms: int | None = None


def parse_audio_tags(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(tag.strip() for tag in value.split(",") if tag.strip()))


class SfxValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SfxPlacement:
    asset_id: int
    timeline_start_ms: int
    source_start_ms: int
    source_end_ms: int
    volume: float = 1.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    event_marker: str = ""

    @property
    def duration_ms(self) -> int:
        return self.source_end_ms - self.source_start_ms

    def validate(self, asset_duration_ms: int | None = None) -> None:
        if self.asset_id <= 0:
            raise SfxValidationError("L'identifiant du fichier audio doit être positif.")
        if min(self.timeline_start_ms, self.source_start_ms, self.fade_in_ms, self.fade_out_ms) < 0:
            raise SfxValidationError("Les positions et durées du SFX ne peuvent pas être négatives.")
        if self.source_end_ms <= self.source_start_ms:
            raise SfxValidationError("La sortie source doit suivre l'entrée source.")
        if asset_duration_ms is not None and self.source_end_ms > asset_duration_ms:
            raise SfxValidationError("La sortie demandée dépasse la durée du fichier audio.")
        if not 0 <= self.volume <= 4:
            raise SfxValidationError("Le volume doit être compris entre 0 et 4.")
        if self.fade_in_ms + self.fade_out_ms > self.duration_ms:
            raise SfxValidationError("La somme des fondus ne peut pas dépasser la durée du SFX.")


@dataclass(frozen=True)
class SfxRenderClip:
    path: Path
    placement: SfxPlacement
    timeline_duration_ms: int | None = None
