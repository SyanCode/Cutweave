"""Choix de l'utilisateur sur ses rushs avant le montage : lesquels garder, et quelle plage de chacun.

Les rushs ne sont jamais modifiés : on mémorise seulement, par rush, s'il est écarté et la plage retenue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

FILE_NAME = "rush_settings.json"
MIN_RANGE = 0.5


@dataclass
class RushSettings:
    disabled: set[int] = field(default_factory=set)
    ranges: dict[int, tuple[float, float]] = field(default_factory=dict)

    def enabled(self, media_id: int) -> bool:
        return media_id not in self.disabled

    def set_enabled(self, media_id: int, enabled: bool) -> None:
        (self.disabled.discard if enabled else self.disabled.add)(media_id)

    def range_for(self, media_id: int, duration: float | None = None) -> tuple[float, float] | None:
        window = self.ranges.get(media_id)
        if window is None:
            return None
        if duration is not None and window[0] <= 0.0 and window[1] >= duration - 0.01:
            return None
        return window

    def set_range(self, media_id: int, window: tuple[float, float] | None) -> None:
        if window is None:
            self.ranges.pop(media_id, None)
            return
        start, end = float(window[0]), float(window[1])
        if end - start < MIN_RANGE:
            raise ValueError(f"La plage retenue doit durer au moins {MIN_RANGE} s.")
        self.ranges[media_id] = (round(start, 3), round(end, 3))


def load_rush_settings(project_root: str | Path) -> RushSettings:
    path = Path(project_root) / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RushSettings(
            {int(value) for value in data.get("disabled", ())},
            {int(key): (float(a), float(b)) for key, (a, b) in data.get("ranges", {}).items()})
    except (OSError, ValueError, TypeError):
        return RushSettings()


def save_rush_settings(project_root: str | Path, settings: RushSettings) -> None:
    path = Path(project_root) / FILE_NAME
    payload = {"disabled": sorted(settings.disabled),
               "ranges": {str(key): list(value) for key, value in sorted(settings.ranges.items())}}
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
