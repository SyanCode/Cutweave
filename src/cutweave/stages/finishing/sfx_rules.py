"""Événements explicites de la timeline et règles de déclenchement des SFX.

Les événements sont uniquement dérivés de données saisies par l'utilisateur : bornes du rush,
placements B-roll, captions et marqueurs manuels. Aucun événement n'est « détecté » dans
l'image ou le son.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from cutweave.stages.composition.broll import BrollPlacement
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.finishing.music import SfxPlacement, SfxValidationError

EVENT_KINDS = {
    "clip_start": "Début d'un clip (rush ou B-roll)",
    "clip_end": "Fin d'un clip (rush ou B-roll)",
    "cut": "Coupe entre le rush et un B-roll",
    "caption_start": "Début d'une caption",
    "caption_end": "Fin d'une caption",
    "caption_animation": "Animation d'une caption (preset animé)",
    "marker": "Marqueur manuel",
}


@dataclass(frozen=True, order=True)
class TimelineMarker:
    time_ms: int
    label: str = ""

    def validate(self) -> None:
        if self.time_ms < 0:
            raise SfxValidationError("Un marqueur ne peut pas être placé avant 0 ms.")
        if len(self.label) > 80 or any(ch in self.label for ch in "\r\n"):
            raise SfxValidationError("Le libellé d'un marqueur doit tenir sur une ligne de 80 caractères maximum.")


@dataclass(frozen=True, order=True)
class TimelineEvent:
    time_ms: int
    kind: str
    label: str = ""


@dataclass(frozen=True)
class SfxRule:
    event: str
    asset_id: int
    volume: float = 1.0
    offset_ms: int = 0
    source_start_ms: int = 0
    source_end_ms: int | None = None
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    label_filter: str = ""
    min_interval_ms: int = 0
    enabled: bool = True

    def validate(self, asset_duration_ms: int | None = None) -> None:
        if self.event not in EVENT_KINDS:
            raise SfxValidationError(f"Événement inconnu : {self.event}. Valeurs : {', '.join(EVENT_KINDS)}.")
        if self.asset_id <= 0:
            raise SfxValidationError("La règle doit référencer un fichier audio de la bibliothèque.")
        if not 0 <= self.volume <= 4:
            raise SfxValidationError("Le volume doit être compris entre 0 et 4.")
        if not -10_000 <= self.offset_ms <= 10_000:
            raise SfxValidationError("Le décalage doit être compris entre -10 000 et 10 000 ms.")
        if self.source_start_ms < 0 or min(self.fade_in_ms, self.fade_out_ms, self.min_interval_ms) < 0:
            raise SfxValidationError("Les positions, fondus et intervalles ne peuvent pas être négatifs.")
        end = self.source_end_ms if self.source_end_ms is not None else asset_duration_ms
        if end is not None:
            if end <= self.source_start_ms:
                raise SfxValidationError("La sortie source doit suivre l'entrée source.")
            if asset_duration_ms is not None and end > asset_duration_ms:
                raise SfxValidationError("La sortie demandée dépasse la durée du fichier audio.")
            if self.fade_in_ms + self.fade_out_ms > end - self.source_start_ms:
                raise SfxValidationError("La somme des fondus dépasse la durée de l'extrait.")
        if len(self.label_filter) > 80:
            raise SfxValidationError("Le filtre de libellé est limité à 80 caractères.")


def timeline_events(
    duration_ms: int | None,
    *,
    captions: Sequence[CaptionCue] = (),
    broll: Sequence[BrollPlacement] = (),
    markers: Sequence[TimelineMarker] = (),
    caption_animated: bool = False,
) -> tuple[TimelineEvent, ...]:
    """Liste triée des événements explicitement présents dans la timeline."""
    events: list[TimelineEvent] = [TimelineEvent(0, "clip_start", "rush")]
    if duration_ms is not None and duration_ms > 0:
        events.append(TimelineEvent(duration_ms, "clip_end", "rush"))
    for placement in broll:
        events.append(TimelineEvent(placement.timeline_start_ms, "clip_start", f"broll:{placement.asset_id}"))
        events.append(TimelineEvent(placement.timeline_end_ms, "clip_end", f"broll:{placement.asset_id}"))
        for boundary in (placement.timeline_start_ms, placement.timeline_end_ms):
            if boundary > 0 and (duration_ms is None or boundary < duration_ms):
                events.append(TimelineEvent(boundary, "cut", f"broll:{placement.asset_id}"))
    for cue in captions:
        events.append(TimelineEvent(cue.start_ms, "caption_start", cue.text))
        events.append(TimelineEvent(cue.end_ms, "caption_end", cue.text))
        if caption_animated:
            events.append(TimelineEvent(cue.start_ms, "caption_animation", cue.text))
    for marker in markers:
        events.append(TimelineEvent(marker.time_ms, "marker", marker.label))
    unique = sorted(set(events))
    if duration_ms is not None:
        unique = [event for event in unique if event.time_ms <= duration_ms]
    return tuple(unique)


def _label_matches(rule: SfxRule, event: TimelineEvent) -> bool:
    needle = rule.label_filter.strip().casefold()
    if not needle:
        return True
    if event.kind == "marker":
        return event.label.strip().casefold() == needle
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", event.label.casefold()) is not None


def apply_sfx_rules(
    rules: Sequence[SfxRule],
    events: Sequence[TimelineEvent],
    asset_durations: dict[int, int],
    duration_ms: int | None,
) -> tuple[SfxPlacement, ...]:
    """Produit des placements SFX déterministes ; l'ordre ne dépend que des entrées."""
    placements: list[SfxPlacement] = []
    for rule in rules:
        if not rule.enabled:
            continue
        asset_duration = asset_durations.get(rule.asset_id)
        if asset_duration is None:
            raise SfxValidationError(f"Règle SFX : fichier audio introuvable ({rule.asset_id}).")
        rule.validate(asset_duration)
        source_end = rule.source_end_ms if rule.source_end_ms is not None else asset_duration
        last_time = None
        for event in events:
            if event.kind != rule.event or not _label_matches(rule, event):
                continue
            start = max(0, event.time_ms + rule.offset_ms)
            if duration_ms is not None and start >= duration_ms:
                continue
            if last_time is not None and start - last_time < rule.min_interval_ms:
                continue
            last_time = start
            label = f"{event.kind}:{event.label}"[:120]
            placements.append(SfxPlacement(
                rule.asset_id, start, rule.source_start_ms, source_end, rule.volume,
                rule.fade_in_ms, rule.fade_out_ms, label,
            ))
    return tuple(sorted(placements, key=lambda item: (item.timeline_start_ms, item.asset_id, item.event_marker)))
