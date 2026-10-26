"""Presets captions versionnés, déterministes et sérialisables en JSON."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


class CaptionPresetError(ValueError):
    pass


@dataclass(frozen=True)
class CaptionPreset:
    name: str
    font_name: str = "Arial"
    font_size: int = 48
    text_color: str = "#FFFFFF"
    highlight_color: str = "#FFD54A"
    outline_color: str = "#000000"
    shadow_color: str = "#000000"
    outline_width: float = 2.0
    shadow: float = 1.0
    background_color: str = "#000000"
    background_opacity: int = 0
    alignment: int = 2
    margin_horizontal_percent: float = 5.0
    margin_vertical_percent: float = 8.0
    bold: bool = False
    animation: str = "none"
    animation_ms: int = 180
    highlight_mode: str = "none"

    def validate(self) -> None:
        if not self.name.strip() or len(self.name) > 80:
            raise CaptionPresetError("Le preset doit avoir un nom de 1 à 80 caractères.")
        if self.name != self.name.strip():
            raise CaptionPresetError("Le nom du preset ne peut pas commencer ou finir par un espace.")
        if not self.font_name.strip() or len(self.font_name) > 80 or any(ch in self.font_name for ch in ",\r\n"):
            raise CaptionPresetError("Le nom de police est invalide.")
        if not 8 <= self.font_size <= 300:
            raise CaptionPresetError("La taille de police doit être comprise entre 8 et 300.")
        for color in (self.text_color, self.highlight_color, self.outline_color,
                      self.shadow_color, self.background_color):
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                raise CaptionPresetError(f"Couleur hexadécimale invalide : {color}")
        if not 0 <= self.outline_width <= 20 or not 0 <= self.shadow <= 20:
            raise CaptionPresetError("Contour et ombre doivent être compris entre 0 et 20.")
        if not 0 <= self.background_opacity <= 255:
            raise CaptionPresetError("L'opacité du fond doit être comprise entre 0 et 255.")
        if not 1 <= self.alignment <= 9:
            raise CaptionPresetError("L'alignement ASS doit être compris entre 1 et 9.")
        if not 0 <= self.margin_horizontal_percent <= 30 or not 0 <= self.margin_vertical_percent <= 30:
            raise CaptionPresetError("Les marges doivent être comprises entre 0 et 30 %.")
        if self.animation not in {"none", "fade", "pop"}:
            raise CaptionPresetError("Animation autorisée : none, fade ou pop.")
        if not 0 <= self.animation_ms <= 5000:
            raise CaptionPresetError("La durée d'animation doit être comprise entre 0 et 5000 ms.")
        if self.highlight_mode not in {"none", "cue"}:
            raise CaptionPresetError("Le surlignage doit s'appliquer à none ou à toute la cue.")

    def to_json(self) -> str:
        self.validate()
        return json.dumps({"schema_version": 1, **asdict(self)}, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, payload: str) -> "CaptionPreset":
        try:
            data = json.loads(payload)
            if data.pop("schema_version", None) != 1:
                raise CaptionPresetError("Version de preset captions non prise en charge.")
            preset = cls(**data)
            preset.validate()
            return preset
        except CaptionPresetError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CaptionPresetError(f"Fichier de preset captions invalide : {exc}") from exc

    @classmethod
    def load(cls, path: str | Path) -> "CaptionPreset":
        try:
            return cls.from_json(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            raise CaptionPresetError(f"Impossible de lire le preset : {exc}") from exc

    def save(self, path: str | Path) -> Path:
        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_json(), encoding="utf-8", newline="\n")
        return output


BUILTIN_CAPTION_PRESETS: tuple[CaptionPreset, ...] = (
    CaptionPreset("Clean", font_name="Arial", font_size=48, text_color="#FFFFFF",
                  highlight_color="#FFD54A", outline_width=2, shadow=1, alignment=2),
    CaptionPreset("Dynamic", font_name="Arial", font_size=58, text_color="#FFFFFF",
                  highlight_color="#00E5FF", outline_width=3, shadow=2, alignment=2,
                  bold=True, animation="pop", animation_ms=150, highlight_mode="cue"),
    CaptionPreset("Cinematic", font_name="Georgia", font_size=44, text_color="#F2EDE2",
                  highlight_color="#D9B96E", outline_color="#14110C", shadow_color="#14110C",
                  outline_width=1.2, shadow=0.8, background_color="#000000", background_opacity=210,
                  alignment=2, margin_horizontal_percent=8, margin_vertical_percent=10,
                  animation="fade", animation_ms=220),
    CaptionPreset("Impact", font_name="Arial Black", font_size=78, text_color="#FFFFFF",
                  highlight_color="#FFE14D", outline_color="#000000", outline_width=5, shadow=0,
                  alignment=5, margin_horizontal_percent=7, bold=True, animation="pop", animation_ms=140,
                  highlight_mode="cue"),
    CaptionPreset("Vlog", font_name="Arial", font_size=50, text_color="#FFFFFF", highlight_color="#FFB020",
                  outline_color="#101010", outline_width=0, shadow=0, background_color="#111111",
                  background_opacity=185, alignment=2, margin_vertical_percent=12, bold=True,
                  animation="fade", animation_ms=180),
    CaptionPreset("Néon", font_name="Arial", font_size=64, text_color="#FFFFFF", highlight_color="#00E5FF",
                  outline_color="#00B3FF", outline_width=4, shadow=2, shadow_color="#3A00FF", alignment=5,
                  bold=True, animation="pop", animation_ms=120, highlight_mode="cue"),
    CaptionPreset("Souvenir", font_name="Georgia", font_size=58, text_color="#FFF4E0", highlight_color="#FFD9A0",
                  outline_color="#3B2A1A", outline_width=1.5, shadow=1.5, shadow_color="#3B2A1A", alignment=5,
                  animation="fade", animation_ms=400),
)

BUILTIN_BY_NAME = {preset.name.casefold(): preset for preset in BUILTIN_CAPTION_PRESETS}
