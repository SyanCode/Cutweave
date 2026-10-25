"""Modèles validés pour la bibliothèque locale et les placements B-roll."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class BrollValidationError(ValueError):
    pass


@dataclass(frozen=True)
class BrollAsset:
    id: int
    path: Path
    name: str
    duration_ms: int
    width: int | None
    height: int | None
    category: str = ""
    tags: tuple[str, ...] = ()

    @property
    def missing(self) -> bool:
        return not self.path.is_file()


@dataclass(frozen=True)
class BrollPlacement:
    asset_id: int
    timeline_start_ms: int
    source_start_ms: int
    source_end_ms: int
    track: int = 0

    @property
    def duration_ms(self) -> int:
        return self.source_end_ms - self.source_start_ms

    @property
    def timeline_end_ms(self) -> int:
        return self.timeline_start_ms + self.duration_ms

    def validate(self) -> None:
        if self.asset_id <= 0:
            raise BrollValidationError("L'identifiant du clip B-roll doit être positif.")
        if self.timeline_start_ms < 0 or self.source_start_ms < 0:
            raise BrollValidationError("Les positions temporelles ne peuvent pas être négatives.")
        if self.source_end_ms <= self.source_start_ms:
            raise BrollValidationError("La sortie source doit suivre l'entrée source.")
        if self.track < 0:
            raise BrollValidationError("La piste B-roll doit être positive ou nulle.")


@dataclass(frozen=True)
class BrollRenderClip:
    path: Path
    placement: BrollPlacement


def parse_tags(value: str) -> tuple[str, ...]:
    """Parse les tags séparés par des virgules, en supprimant doublons et espaces."""
    return tuple(dict.fromkeys(tag.strip() for tag in value.split(",") if tag.strip()))
