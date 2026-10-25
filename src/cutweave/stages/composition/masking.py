"""Masques géométriques déterministes appliqués au rendu."""

from __future__ import annotations

from dataclasses import dataclass


class MaskValidationError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class MaskKeyframe:
    time_ms: int
    x: int
    y: int
    width: int
    height: int
    opacity: float = 1.0


@dataclass(frozen=True)
class MaskRegion:
    x: int
    y: int
    width: int
    height: int
    opacity: float = 0.95
    start_ms: int | None = None
    end_ms: int | None = None
    shape: str = "rectangle"
    points: tuple[tuple[int, int], ...] = ()
    keyframes: tuple[MaskKeyframe, ...] = ()
    effect: str = "opaque"
    blur_radius: int = 12

    def validate(self, frame_width: int, frame_height: int) -> None:
        if frame_width <= 0 or frame_height <= 0:
            raise MaskValidationError("Les dimensions du rendu doivent être positives.")
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise MaskValidationError("Le masque doit avoir une position positive et une taille non nulle.")
        if self.x + self.width > frame_width or self.y + self.height > frame_height:
            raise MaskValidationError("Le masque dépasse les limites de la vidéo de sortie.")
        if not 0 < self.opacity <= 1:
            raise MaskValidationError("L'opacité du masque doit être comprise entre 0 et 1.")
        if self.start_ms is not None and self.start_ms < 0:
            raise MaskValidationError("Le début du masque ne peut pas être négatif.")
        if self.end_ms is not None and self.end_ms < 0:
            raise MaskValidationError("La fin du masque ne peut pas être négative.")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms <= self.start_ms:
            raise MaskValidationError("La fin du masque doit être postérieure à son début.")
        if self.shape not in {"rectangle", "ellipse", "polygon"}:
            raise MaskValidationError("La forme doit être rectangle, ellipse ou polygon.")
        if self.shape == "polygon":
            if not 3 <= len(self.points) <= 20:
                raise MaskValidationError("Un polygone doit contenir de 3 à 20 sommets.")
            if any(x < 0 or y < 0 or x >= frame_width or y >= frame_height for x, y in self.points):
                raise MaskValidationError("Un sommet du polygone dépasse les limites du rendu.")
            if any(x < self.x or y < self.y or x > self.x + self.width or y > self.y + self.height
                   for x, y in self.points):
                raise MaskValidationError("Un sommet du polygone dépasse son cadre de référence.")
            area = sum(self.points[index][0] * self.points[(index + 1) % len(self.points)][1]
                       - self.points[(index + 1) % len(self.points)][0] * self.points[index][1]
                       for index in range(len(self.points)))
            if area == 0:
                raise MaskValidationError("Les sommets du polygone doivent former une surface non nulle.")
        if len(self.keyframes) > 100:
            raise MaskValidationError("Un masque ne peut pas dépasser 100 keyframes.")
        previous_time = -1
        for keyframe in self.keyframes:
            if keyframe.time_ms <= previous_time:
                raise MaskValidationError("Les keyframes doivent être triées et avoir des timestamps uniques.")
            previous_time = keyframe.time_ms
            if keyframe.time_ms < 0 or keyframe.x < 0 or keyframe.y < 0 or keyframe.width <= 0 or keyframe.height <= 0:
                raise MaskValidationError("Une keyframe contient un timestamp ou une géométrie invalide.")
            if keyframe.x + keyframe.width > frame_width or keyframe.y + keyframe.height > frame_height:
                raise MaskValidationError("Une keyframe dépasse les limites du rendu.")
            if not 0 < keyframe.opacity <= 1:
                raise MaskValidationError("L'opacité d'une keyframe doit être comprise entre 0 et 1.")
        if self.effect not in {"opaque", "blur"}:
            raise MaskValidationError("L'effet du masque doit être opaque ou blur.")
        if not 1 <= self.blur_radius <= 50:
            raise MaskValidationError("Le rayon de flou doit être compris entre 1 et 50.")

    @staticmethod
    def _escape_expression(expression: str) -> str:
        return expression.replace("'", r"\'")

    def _inside_expression(self) -> str:
        if self.shape == "ellipse":
            x, y = self._geometry_expression("x"), self._geometry_expression("y")
            width, height = self._geometry_expression("width"), self._geometry_expression("height")
            cx, cy, rx, ry = f"({x}+{width}/2)", f"({y}+{height}/2)", f"({width}/2)", f"({height}/2)"
            return f"lte(pow((X-{cx})/{rx},2)+pow((Y-{cy})/{ry},2),1)"
        if self.shape == "polygon":
            edges = []
            for index, (x1, y1) in enumerate(self.points):
                x2, y2 = self.points[(index + 1) % len(self.points)]
                if y1 == y2:
                    continue
                if self.keyframes:
                    rx1, ry1 = (x1 - self.x) / self.width, (y1 - self.y) / self.height
                    rx2, ry2 = (x2 - self.x) / self.width, (y2 - self.y) / self.height
                    frame_x, frame_y = self._geometry_expression("x"), self._geometry_expression("y")
                    frame_w, frame_h = self._geometry_expression("width"), self._geometry_expression("height")
                    px1, py1 = f"({frame_x}+{rx1:.8f}*{frame_w})", f"({frame_y}+{ry1:.8f}*{frame_h})"
                    px2, py2 = f"({frame_x}+{rx2:.8f}*{frame_w})", f"({frame_y}+{ry2:.8f}*{frame_h})"
                else:
                    px1, py1, px2, py2 = str(x1), str(y1), str(x2), str(y2)
                edge_expr = f"(gte(Y,{py1})*lt(Y,{py2})+gte(Y,{py2})*lt(Y,{py1}))"
                cross_x = f"({px1}+((Y-{py1})*({px2}-{px1})/({py2}-{py1})))"
                edges.append(f"(({edge_expr})*lt(X,{cross_x}))")
            if not edges:
                raise MaskValidationError("Le polygone ne possède pas d'arête exploitable.")
            parity = edges[0]
            for edge in edges[1:]:
                parity = f"abs({parity}-{edge})"
            return f"gt({parity},0)"
        x, y = self._geometry_expression("x"), self._geometry_expression("y")
        width, height = self._geometry_expression("width"), self._geometry_expression("height")
        return f"(gte(X,{x})*lt(X,({x}+{width}))*gte(Y,{y})*lt(Y,({y}+{height})))"

    def _geometry_expression(self, attribute: str) -> str:
        if not self.keyframes:
            return str(getattr(self, attribute))
        frames = self.keyframes
        expression = str(getattr(frames[-1], attribute))
        for left, right in reversed(tuple(zip(frames, frames[1:]))):
            start, end = left.time_ms / 1000, right.time_ms / 1000
            first, second = getattr(left, attribute), getattr(right, attribute)
            linear = f"({first}+({second}-{first})*(T-{start:.3f})/{end - start:.3f})"
            expression = f"if(lt(T,{end:.3f}),{linear},{expression})"
        first_value = getattr(frames[0], attribute)
        return f"if(lt(T,{frames[0].time_ms / 1000:.3f}),{first_value},{expression})"

    def active_expression(self) -> str:
        expression = self._inside_expression()
        if self.start_ms is not None:
            expression = f"({expression}*gte(T,{self.start_ms / 1000:.3f}))"
        if self.end_ms is not None:
            expression = f"({expression}*lt(T,{self.end_ms / 1000:.3f}))"
        return expression

    def opacity_expression(self) -> str:
        return self._geometry_expression("opacity") if self.keyframes else f"{self.opacity:.6f}"

    def filter_fragment(self, frame_width: int, frame_height: int) -> str:
        self.validate(frame_width, frame_height)
        if self.effect == "blur":
            raise MaskValidationError("Un masque de flou doit être composé dans le graphe vidéo.")
        if self.shape == "rectangle" and not self.keyframes:
            fragment = (
                f"drawbox=x={self.x}:y={self.y}:w={self.width}:h={self.height}:"
                f"color=black@{self.opacity:.3f}:t=fill"
            )
            if self.start_ms is not None and self.end_ms is not None:
                fragment += f":enable='between(t\\,{self.start_ms / 1000:.3f}\\,{self.end_ms / 1000:.3f})'"
            elif self.start_ms is not None:
                fragment += f":enable='gte(t\\,{self.start_ms / 1000:.3f})'"
            elif self.end_ms is not None:
                fragment += f":enable='lt(t\\,{self.end_ms / 1000:.3f})'"
            return fragment
        inside = self.active_expression()
        opacity = self.opacity_expression()
        channels = []
        for channel in ("r", "g", "b"):
            value = f"if({inside},{channel}(X,Y)*(1-{opacity}),{channel}(X,Y))"
            channels.append(f"{channel}='{self._escape_expression(value)}'")
        return "geq=" + ":".join(channels)
