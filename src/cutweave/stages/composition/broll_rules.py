"""Règles explicites d'insertion B-roll associées au texte des captions.

Une règle relie un mot-clé saisi par l'utilisateur à un clip précis ou à un tag de la
bibliothèque. Aucune interprétation sémantique : seule une correspondance littérale du mot
(insensible à la casse) déclenche une insertion. Sans règle correspondante, rien n'est inséré.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from cutweave.stages.composition.broll import BrollAsset, BrollPlacement, BrollValidationError
from cutweave.stages.composition.captions import CaptionCue


@dataclass(frozen=True)
class BrollRule:
    keyword: str
    asset_id: int | None = None
    tag: str = ""
    track: int = 0
    source_start_ms: int = 0
    max_duration_ms: int = 0
    enabled: bool = True

    def validate(self) -> None:
        if not self.keyword.strip() or len(self.keyword) > 80 or any(ch in self.keyword for ch in "\r\n"):
            raise BrollValidationError("Le mot-clé d'une règle B-roll doit contenir de 1 à 80 caractères.")
        if (self.asset_id is None) == (not self.tag.strip()):
            raise BrollValidationError("Une règle B-roll cible soit un clip précis, soit un tag (pas les deux).")
        if self.asset_id is not None and self.asset_id <= 0:
            raise BrollValidationError("L'identifiant du clip doit être positif.")
        if self.track < 0 or self.source_start_ms < 0 or self.max_duration_ms < 0:
            raise BrollValidationError("Piste, entrée source et durée maximale doivent être positives.")


def _matches(keyword: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(keyword.strip().casefold())}(?!\w)", text.casefold()) is not None


def _overlaps(candidate: BrollPlacement, existing: Sequence[BrollPlacement]) -> bool:
    return any(
        item.track == candidate.track
        and candidate.timeline_start_ms < item.timeline_end_ms
        and item.timeline_start_ms < candidate.timeline_end_ms
        for item in existing
    )


def apply_broll_rules(
    rules: Sequence[BrollRule],
    captions: Sequence[CaptionCue],
    assets: Sequence[BrollAsset],
    existing: Sequence[BrollPlacement] = (),
    duration_ms: int | None = None,
) -> tuple[BrollPlacement, ...]:
    """Retourne les placements générés, sans chevaucher les placements existants sur la même piste."""
    by_id = {asset.id: asset for asset in assets}
    placed: list[BrollPlacement] = list(existing)
    generated: list[BrollPlacement] = []
    tag_cursor: dict[str, int] = {}
    for rule in rules:
        rule.validate()
    for cue in sorted(captions, key=lambda item: (item.start_ms, item.end_ms)):
        for rule in rules:
            if not rule.enabled or not _matches(rule.keyword, cue.text):
                continue
            if rule.asset_id is not None:
                asset = by_id.get(rule.asset_id)
                if asset is None:
                    raise BrollValidationError(f"Règle « {rule.keyword} » : clip B-roll introuvable ({rule.asset_id}).")
            else:
                tag = rule.tag.strip().casefold()
                candidates = sorted(
                    (item for item in assets if tag in {value.casefold() for value in item.tags}),
                    key=lambda item: (item.name.casefold(), item.id),
                )
                if not candidates:
                    continue
                index = tag_cursor.get(tag, 0)
                asset = candidates[index % len(candidates)]
                tag_cursor[tag] = index + 1
            length = min(cue.end_ms - cue.start_ms, asset.duration_ms - rule.source_start_ms)
            if rule.max_duration_ms:
                length = min(length, rule.max_duration_ms)
            if duration_ms is not None:
                length = min(length, duration_ms - cue.start_ms)
            if length <= 0:
                continue
            placement = BrollPlacement(asset.id, cue.start_ms, rule.source_start_ms,
                                       rule.source_start_ms + length, rule.track)
            if _overlaps(placement, placed):
                continue
            placed.append(placement)
            generated.append(placement)
            break  # une seule insertion par caption : la première règle applicable gagne
    return tuple(generated)
