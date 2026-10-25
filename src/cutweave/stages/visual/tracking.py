"""Suivi manuel avec les trackers classiques intégrés à OpenCV contrib."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2

from cutweave.stages.composition.masking import MaskKeyframe
from cutweave.stages.visual.framing import FramingKeyframe, source_to_output_transform, validate_keyframes


class TrackingError(RuntimeError):
    pass


class TrackingCancelled(TrackingError):
    pass


@dataclass(frozen=True)
class TrackSample:
    frame: int
    time_ms: int
    bbox: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class TrackingTrajectory:
    schema_version: int
    source_name: str
    method: str
    fps: float
    frame_width: int
    frame_height: int
    start_frame: int
    initial_bbox: tuple[int, int, int, int]
    samples: tuple[TrackSample, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "source_name": self.source_name,
            "method": self.method,
            "fps": self.fps,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "start_frame": self.start_frame,
            "initial_bbox": list(self.initial_bbox),
            "samples": [
                {"frame": sample.frame, "time_ms": sample.time_ms,
                 "bbox": list(sample.bbox) if sample.bbox is not None else None}
                for sample in self.samples
            ],
        }


def _tracker_factory(method: str):
    normalized = method.strip().upper()
    factories = {
        "CSRT": (getattr(cv2, "TrackerCSRT_create", None), getattr(getattr(cv2, "legacy", None), "TrackerCSRT_create", None)),
        "KCF": (getattr(cv2, "TrackerKCF_create", None), getattr(getattr(cv2, "legacy", None), "TrackerKCF_create", None)),
        "MOSSE": (getattr(getattr(cv2, "legacy", None), "TrackerMOSSE_create", None), getattr(cv2, "TrackerMOSSE_create", None)),
    }
    if normalized not in factories:
        raise TrackingError(f"Tracker inconnu : {method}. Choisissez CSRT, KCF ou MOSSE.")
    return next((factory for factory in factories[normalized] if factory is not None), None)


def available_trackers() -> tuple[str, ...]:
    return tuple(method for method in ("CSRT", "KCF", "MOSSE") if _tracker_factory(method) is not None)


def _open_video(path: str | Path):
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise TrackingError(f"Fichier vidéo introuvable : {source}")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        raise TrackingError(f"OpenCV ne peut pas lire cette vidéo : {source.name}")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if width <= 0 or height <= 0 or fps <= 0:
        capture.release()
        raise TrackingError("Les métadonnées vidéo nécessaires au tracking sont invalides.")
    return source, capture, width, height, fps, frame_count


def extract_frame_at(path: str | Path, time_ms: int):
    if time_ms < 0:
        raise TrackingError("La position de lecture ne peut pas être négative.")
    source, capture, width, height, fps, _ = _open_video(path)
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, float(time_ms))
        success, frame = capture.read()
        if not success or frame is None:
            raise TrackingError(f"Impossible de lire une image de {source.name} à {time_ms} ms.")
        actual_frame = max(0, int(capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
        actual_time_ms = round(actual_frame * 1000 / fps)
        return frame, {"width": width, "height": height, "fps": fps,
                       "frame": actual_frame, "time_ms": actual_time_ms}
    finally:
        capture.release()


def track_region(
    path: str | Path,
    bbox: tuple[int, int, int, int],
    *,
    method: str = "CSRT",
    start_ms: int = 0,
    end_ms: int | None = None,
    frame_step: int = 1,
    cancel_event=None,
    on_progress: Callable[[float], None] | None = None,
) -> TrackingTrajectory:
    if cancel_event is not None and cancel_event.is_set():
        raise TrackingCancelled("Tracking annulé.")
    if start_ms < 0 or (end_ms is not None and end_ms <= start_ms):
        raise TrackingError("L'intervalle de tracking est invalide.")
    if frame_step < 1:
        raise TrackingError("Le pas d'échantillonnage doit être au moins égal à 1.")
    factory = _tracker_factory(method)
    if factory is None:
        raise TrackingError(f"Le tracker {method.upper()} n'est pas disponible dans cette version d'OpenCV.")
    source, capture, width, height, fps, frame_count = _open_video(path)
    tracker = factory()
    samples: list[TrackSample] = []
    try:
        start_frame = max(0, round(start_ms * fps / 1000))
        if frame_count > 0 and start_frame >= frame_count:
            raise TrackingError("La position de départ dépasse la durée de la vidéo.")
        if end_ms is not None:
            end_frame = round(end_ms * fps / 1000)
            if end_frame <= start_frame:
                raise TrackingError("La plage de tracking ne contient aucune image.")
        else:
            end_frame = frame_count - 1 if frame_count > 0 else None
        x, y, box_width, box_height = (int(value) for value in bbox)
        if x < 0 or y < 0 or box_width <= 0 or box_height <= 0 or x + box_width > width or y + box_height > height:
            raise TrackingError("La sélection manuelle dépasse les limites de l'image source.")
        capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        success, frame = capture.read()
        if not success or frame is None:
            raise TrackingError("Impossible de lire l'image de départ du tracking.")
        initialized = tracker.init(frame, (x, y, box_width, box_height))
        if initialized is False:
            raise TrackingError("OpenCV n'a pas pu initialiser le tracker sur cette sélection.")
        samples.append(TrackSample(start_frame, round(start_frame * 1000 / fps), (x, y, box_width, box_height)))
        current_frame = start_frame
        while end_frame is None or current_frame < end_frame:
            if cancel_event is not None and cancel_event.is_set():
                raise TrackingCancelled("Tracking annulé.")
            success, frame = capture.read()
            if not success or frame is None:
                break
            current_frame += 1
            tracked, found = tracker.update(frame)
            if (current_frame - start_frame) % frame_step == 0:
                sample_box = None
                if tracked:
                    sample_box = tuple(int(round(value)) for value in found)
                samples.append(TrackSample(current_frame, round(current_frame * 1000 / fps), sample_box))
            if on_progress and (current_frame - start_frame) % 10 == 0 and end_frame is not None:
                span = max(1, end_frame - start_frame)
                on_progress(min(1.0, (current_frame - start_frame) / span))
        if len(samples) < 2:
            raise TrackingError("La vidéo ne contient pas assez d'images après le point de départ.")
        return TrackingTrajectory(1, source.name, method.upper(), fps, width, height,
                                  start_frame, (x, y, box_width, box_height), tuple(samples))
    finally:
        capture.release()


def save_trajectory(trajectory: TrackingTrajectory, path: str | Path) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(trajectory.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def load_trajectory(path: str | Path) -> TrackingTrajectory:
    """Charge et valide un fichier JSON produit par CUTWEAVE."""
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
        raise TrackingError("Le fichier de trajectoire est absent ou dépasse 16 Mio.")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("version de schéma non prise en charge")
        width, height = int(data["frame_width"]), int(data["frame_height"])
        fps = float(data["fps"])
        initial_bbox = tuple(int(value) for value in data["initial_bbox"])
        raw_samples = data["samples"]
        if width <= 0 or height <= 0 or fps <= 0 or len(initial_bbox) != 4:
            raise ValueError("métadonnées de vidéo invalides")
        if not isinstance(raw_samples, list) or not 1 <= len(raw_samples) <= 500_000:
            raise ValueError("nombre d'échantillons invalide")
        samples = []
        previous_time = -1
        for raw in raw_samples:
            frame, time_ms = int(raw["frame"]), int(raw["time_ms"])
            raw_bbox = raw["bbox"]
            bbox = tuple(int(value) for value in raw_bbox) if raw_bbox is not None else None
            if frame < 0 or time_ms <= previous_time or (bbox is not None and len(bbox) != 4):
                raise ValueError("échantillons non triés ou invalides")
            if bbox is not None:
                x, y, box_width, box_height = bbox
                if x < 0 or y < 0 or box_width <= 0 or box_height <= 0 or x + box_width > width or y + box_height > height:
                    raise ValueError("une boîte suivie dépasse les limites de l'image")
            samples.append(TrackSample(frame, time_ms, bbox))
            previous_time = time_ms
        x, y, box_width, box_height = initial_bbox
        if x < 0 or y < 0 or box_width <= 0 or box_height <= 0 or x + box_width > width or y + box_height > height:
            raise ValueError("la sélection initiale dépasse les limites de l'image")
        return TrackingTrajectory(
            1, str(data["source_name"]), str(data["method"]).upper(), fps, width, height,
            int(data["start_frame"]), initial_bbox, tuple(samples),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise TrackingError(f"Fichier de trajectoire invalide : {exc}") from exc


def tracking_summary(trajectory: TrackingTrajectory) -> dict[str, int]:
    """Nombre d'échantillons suivis, perdus, et premier instant de perte (ms, -1 si aucun)."""
    lost = [sample for sample in trajectory.samples if sample.bbox is None]
    return {
        "samples": len(trajectory.samples),
        "tracked": len(trajectory.samples) - len(lost),
        "lost": len(lost),
        "first_loss_ms": lost[0].time_ms if lost else -1,
    }


def draw_trajectory(frame, trajectory: TrackingTrajectory):
    """Dessine sur une copie de l'image : sélection initiale, chemin des centres et pertes de suivi."""
    canvas = frame.copy()
    x, y, width, height = trajectory.initial_bbox
    cv2.rectangle(canvas, (x, y), (x + width, y + height), (255, 200, 0), 2)
    centers = []
    previous = None
    for sample in trajectory.samples:
        if sample.bbox is None:
            if previous is not None:
                cv2.drawMarker(canvas, previous, (0, 0, 255), cv2.MARKER_TILTED_CROSS, 14, 2)
            continue
        bx, by, bw, bh = sample.bbox
        previous = (bx + bw // 2, by + bh // 2)
        centers.append(previous)
    for start, end in zip(centers, centers[1:]):
        cv2.line(canvas, start, end, (0, 255, 120), 2, cv2.LINE_AA)
    if centers:
        cv2.circle(canvas, centers[-1], 5, (0, 255, 120), -1)
        last = next(sample.bbox for sample in reversed(trajectory.samples) if sample.bbox is not None)
        cv2.rectangle(canvas, (last[0], last[1]), (last[0] + last[2], last[1] + last[3]), (0, 255, 120), 1)
    return canvas


def smooth_tracked_boxes(samples, window: int = 1) -> list[TrackSample]:
    """Moyenne glissante centrée des boîtes suivies ; les pertes de suivi sont écartées.

    ``window`` est le nombre d'échantillons suivis pris en compte (impair conseillé).
    Un lissage plus large réduit le tremblement du tracker mais retarde les mouvements rapides.
    """
    if window < 1 or window > 301:
        raise TrackingError("La fenêtre de lissage doit être comprise entre 1 et 301 échantillons.")
    tracked = [sample for sample in samples if sample.bbox is not None]
    if window == 1 or len(tracked) < 3:
        return tracked
    half = window // 2
    smoothed = []
    for index, sample in enumerate(tracked):
        neighbours = tracked[max(0, index - half):index + half + 1]
        averaged = tuple(
            round(sum(item.bbox[axis] for item in neighbours) / len(neighbours)) for axis in range(4)
        )
        smoothed.append(TrackSample(sample.frame, sample.time_ms, averaged))
    return smoothed


def _downsample(items: list, count: int) -> list:
    if len(items) <= count:
        return items
    if count == 1:
        return [items[0]]
    return [items[round(index * (len(items) - 1) / (count - 1))] for index in range(count)]


def trajectory_to_mask_keyframes(
    trajectory: TrackingTrajectory,
    output_width: int,
    output_height: int,
    *,
    framing_keyframes: tuple[FramingKeyframe, ...] = (),
    padding: float = 0.1,
    opacity: float = 1.0,
    smoothing_window: int = 1,
    max_keyframes: int = 100,
) -> tuple[MaskKeyframe, ...]:
    """Projette les boîtes suivies (pixels source) dans l'image de sortie recadrée.

    Les boîtes sont agrandies de ``padding`` et bornées à l'image de sortie ; les instants où la
    cible sort entièrement du cadre sont ignorés (le masque conserve alors l'interpolation voisine).
    """
    if not 0 <= padding <= 2 or not 0 < opacity <= 1:
        raise TrackingError("Marge ou opacité de masque invalide.")
    if not 1 <= max_keyframes <= 100:
        raise TrackingError("Un masque accepte de 1 à 100 keyframes.")
    validate_keyframes(framing_keyframes)
    keyframes: list[MaskKeyframe] = []
    for sample in smooth_tracked_boxes(trajectory.samples, smoothing_window):
        scale, offset_x, offset_y = source_to_output_transform(
            framing_keyframes, output_width, output_height,
            trajectory.frame_width, trajectory.frame_height, sample.time_ms,
        )
        x, y, width, height = sample.bbox
        pad_x, pad_y = width * padding, height * padding
        left = max(0, round((x - pad_x) * scale - offset_x))
        top = max(0, round((y - pad_y) * scale - offset_y))
        right = min(output_width, round((x + width + pad_x) * scale - offset_x))
        bottom = min(output_height, round((y + height + pad_y) * scale - offset_y))
        if right - left < 2 or bottom - top < 2:
            continue
        keyframes.append(MaskKeyframe(sample.time_ms, left, top, right - left, bottom - top, opacity))
    if not keyframes:
        raise TrackingError("La cible suivie ne se trouve jamais dans le cadre de sortie.")
    return tuple(_downsample(keyframes, max_keyframes))


def trajectory_to_framing_keyframes(
    trajectory: TrackingTrajectory,
    aspect: str,
    *,
    target_fill: float = 0.4,
    margin: float = 0.15,
    max_keyframes: int = 500,
    smoothing_window: int = 1,
) -> tuple[FramingKeyframe, ...]:
    """Convertit les boîtes suivies en centres et zooms de cadrage interpolés."""
    aspects = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0}
    if aspect not in aspects:
        raise TrackingError("Le format de cadrage doit être 9:16, 16:9 ou 1:1.")
    if not 0.05 <= target_fill <= 0.9 or not 0 <= margin <= 1:
        raise TrackingError("Les paramètres de remplissage de cible sont invalides.")
    if not 1 <= max_keyframes <= 500:
        raise TrackingError("Le nombre de keyframes doit être compris entre 1 et 500.")
    tracked = smooth_tracked_boxes(trajectory.samples, smoothing_window)
    if not tracked:
        raise TrackingError("La trajectoire ne contient aucune position suivie valide.")
    tracked = _downsample(tracked, max_keyframes)
    aspect_ratio = aspects[aspect]
    view_width = min(trajectory.frame_width, trajectory.frame_height * aspect_ratio)
    view_height = min(trajectory.frame_height, trajectory.frame_width / aspect_ratio)
    zooms = []
    for sample in tracked:
        x, y, width, height = sample.bbox
        zoom = min(
            4.0,
            view_width * target_fill / (width * (1 + margin * 2)),
            view_height * target_fill / (height * (1 + margin * 2)),
        )
        zooms.append(FramingKeyframe(
            sample.time_ms,
            (x + width / 2) / trajectory.frame_width,
            (y + height / 2) / trajectory.frame_height,
            max(1.0, zoom),
        ))
    validate_keyframes(zooms)
    return tuple(zooms)
