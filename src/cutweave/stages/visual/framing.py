"""Keyframes de cadrage déterministes en coordonnées normalisées."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


class FramingValidationError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class FramingKeyframe:
    time_ms: int
    center_x: float = 0.5
    center_y: float = 0.5
    zoom: float = 1.0

    def validate(self) -> None:
        if self.time_ms < 0:
            raise FramingValidationError("Le timestamp du cadrage ne peut pas être négatif.")
        if not 0 <= self.center_x <= 1 or not 0 <= self.center_y <= 1:
            raise FramingValidationError("Le centre du cadrage doit rester entre 0 et 1.")
        if not 1 <= self.zoom <= 4:
            raise FramingValidationError("Le zoom doit être compris entre 1 et 4.")


def validate_keyframes(keyframes: Sequence[FramingKeyframe]) -> None:
    if len(keyframes) > 500:
        raise FramingValidationError("Un cadrage ne peut pas contenir plus de 500 keyframes.")
    previous = -1
    for keyframe in keyframes:
        keyframe.validate()
        if keyframe.time_ms <= previous:
            raise FramingValidationError("Les keyframes de cadrage doivent être triées et avoir des timestamps uniques.")
        previous = keyframe.time_ms


def interpolate_keyframes(keyframes: Sequence[FramingKeyframe], time_ms: int) -> tuple[float, float, float]:
    """Équivalent Python de :func:`interpolation_expression` : (center_x, center_y, zoom)."""
    if not keyframes:
        return 0.5, 0.5, 1.0
    if time_ms <= keyframes[0].time_ms:
        first = keyframes[0]
        return first.center_x, first.center_y, first.zoom
    for left, right in zip(keyframes, keyframes[1:]):
        if time_ms < right.time_ms:
            u = (time_ms - left.time_ms) / (right.time_ms - left.time_ms)
            smooth = u * u * (3 - 2 * u)
            return tuple(
                getattr(left, name) + (getattr(right, name) - getattr(left, name)) * smooth
                for name in ("center_x", "center_y", "zoom")
            )
    last = keyframes[-1]
    return last.center_x, last.center_y, last.zoom


def source_to_output_transform(
    keyframes: Sequence[FramingKeyframe], output_width: int, output_height: int,
    source_width: int, source_height: int, time_ms: int,
) -> tuple[float, float, float]:
    """Retourne (échelle, décalage_x, décalage_y) : sortie = source * échelle - décalage.

    Reproduit le couple scale(force_original_aspect_ratio=increase)+crop du rendu FFmpeg.
    """
    if source_width <= 0 or source_height <= 0:
        raise FramingValidationError("Dimensions source invalides.")
    center_x, center_y, zoom = interpolate_keyframes(keyframes, time_ms)
    scale = max(output_width * zoom / source_width, output_height * zoom / source_height)
    scaled_width, scaled_height = source_width * scale, source_height * scale
    if keyframes:
        offset_x = min(max((scaled_width - output_width) * center_x, 0), scaled_width - output_width)
        offset_y = min(max((scaled_height - output_height) * center_y, 0), scaled_height - output_height)
    else:
        offset_x = (scaled_width - output_width) / 2
        offset_y = (scaled_height - output_height) / 2
    return scale, offset_x, offset_y


def interpolation_expression(keyframes: Sequence[FramingKeyframe], attribute: str) -> str:
    """Construit une interpolation smoothstep entre les valeurs keyframées."""
    validate_keyframes(keyframes)
    if attribute not in {"center_x", "center_y", "zoom"}:
        raise FramingValidationError(f"Attribut de cadrage inconnu : {attribute}")
    if not keyframes:
        return "0.5" if attribute in {"center_x", "center_y"} else "1"
    expression = f"{getattr(keyframes[-1], attribute):.8f}"
    for left, right in reversed(tuple(zip(keyframes, keyframes[1:]))):
        start = left.time_ms / 1000
        end = right.time_ms / 1000
        first = getattr(left, attribute)
        second = getattr(right, attribute)
        u = f"((t-{start:.3f})/{end - start:.3f})"
        smooth_u = f"({u}*{u}*(3-2*{u}))"
        between = f"({first:.8f}+({second - first:.8f})*{smooth_u})"
        expression = f"if(lt(t,{end:.3f}),{between},{expression})"
    first = getattr(keyframes[0], attribute)
    return f"if(lt(t,{keyframes[0].time_ms / 1000:.3f}),{first:.8f},{expression})"
