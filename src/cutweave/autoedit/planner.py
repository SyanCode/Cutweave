"""Construction déterministe d'un plan de montage à partir de l'analyse des rushs.

Le plan est une liste de plans (extrait source, durée timeline, effet, transition d'entrée) et
les indications de finition (musique, look, SFX, ducking, titres). Toute décision découle des
règles du mode, du profil de variante et d'un générateur pseudo-aléatoire à graine fixe : les
mêmes rushs et les mêmes réglages produisent toujours le même montage.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass, field, replace
from typing import Sequence

import numpy as np

from cutweave.autoedit.analysis import STEP, RushAnalysis, activity_segments, interest_curve
from cutweave.autoedit.modes import EditMode, VariantProfile

FPS = 30


class PlanError(ValueError):
    pass


def quantize(seconds: float) -> float:
    """Durée arrondie à l'image (1/30 s) pour que les décalages de transitions restent exacts."""
    return round(round(seconds * FPS) / FPS, 6)


@dataclass(frozen=True)
class Transition:
    kind: str = "cut"
    duration: float = 0.0


@dataclass(frozen=True)
class Shot:
    source: str
    rush_index: int
    source_start: float
    duration: float                 # durée timeline, transition sortante incluse
    effect: str = "none"
    speed: float = 1.0
    audio_gain: float = 0.0
    transition_in: Transition = Transition()
    score: float = 0.0
    speech: bool = False

    @property
    def source_duration(self) -> float:
        return self.duration * self.speed


@dataclass(frozen=True)
class SfxCue:
    key: str
    time: float
    volume: float


@dataclass(frozen=True)
class MusicChoice:
    key: str | None = None          # morceau intégré
    path: str | None = None         # fichier de l'utilisateur
    title: str = ""
    bpm: float = 120.0
    offset: float = 0.0             # position du premier temps dans le fichier musical
    volume: float = 0.85


@dataclass(frozen=True)
class EditRequest:
    mode: str
    width: int = 1080
    height: int = 1920
    duration: float | None = None
    look: str | None = None         # None = choix du mode
    music: str = "auto"             # auto | none | clé intégrée | chemin de fichier
    fit: str = "fill"               # fill | fit_blur
    title: str = ""
    outro: str = ""


@dataclass(frozen=True)
class EditPlan:
    mode: str
    variant: str
    variant_name: str
    width: int
    height: int
    shots: tuple[Shot, ...]
    look: str
    fit: str
    letterbox: bool
    fade_in: float
    fade_out: float
    music: MusicChoice | None
    duck_segments: tuple[tuple[float, float], ...]
    duck_level: float
    sfx: tuple[SfxCue, ...]
    caption_preset: str
    title: str
    outro: str
    sharpening: float
    seed: int
    summary: str = ""
    cut_times: tuple[float, ...] = field(default=())

    @property
    def duration(self) -> float:
        return timeline_duration(self.shots)

    @property
    def shot_starts(self) -> tuple[float, ...]:
        return shot_starts(self.shots)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "EditPlan":
        data = dict(data)
        data["shots"] = tuple(
            Shot(**{**shot, "transition_in": Transition(**shot["transition_in"])}) for shot in data["shots"]
        )
        data["music"] = MusicChoice(**data["music"]) if data.get("music") else None
        data["duck_segments"] = tuple(tuple(item) for item in data["duck_segments"])
        data["sfx"] = tuple(SfxCue(**item) for item in data["sfx"])
        data["cut_times"] = tuple(data.get("cut_times", ()))
        return cls(**data)


def shot_starts(shots: Sequence[Shot]) -> tuple[float, ...]:
    starts, position = [], 0.0
    for index, shot in enumerate(shots):
        if index:
            position -= shot.transition_in.duration
        starts.append(round(position, 6))
        position += shot.duration
    return tuple(starts)


def timeline_duration(shots: Sequence[Shot]) -> float:
    if not shots:
        return 0.0
    return round(shot_starts(shots)[-1] + shots[-1].duration, 6)


def plan_seed(analyses: Sequence[RushAnalysis], request: EditRequest, variant: VariantProfile) -> int:
    raw = "|".join([*(f"{a.name}:{a.duration:.3f}:{len(a.motion)}" for a in analyses), request.mode,
                    variant.letter, f"{request.duration}", f"{request.width}x{request.height}"])
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12], 16)


def _weighted(rng: random.Random, items):
    total = sum(weight for *_rest, weight in items)
    pick = rng.uniform(0, total)
    for item in items:
        pick -= item[-1]
        if pick <= 0:
            return item
    return items[-1]


def _rotate(values: tuple[str, ...], offset: int) -> str:
    return values[offset % len(values)]


# --- Fenêtres candidates ------------------------------------------------------------------------

@dataclass
class _Rush:
    index: int
    analysis: RushAnalysis
    curve: np.ndarray
    cumulative: np.ndarray
    cuts: np.ndarray
    used: list[tuple[float, float]] = field(default_factory=list)
    picks: int = 0

    def window_scores(self, length: float, *, start_min: float = 0.0, start_max: float | None = None):
        """Scores moyens de toutes les fenêtres de ``length`` secondes (pas STEP)."""
        width = max(1, int(math.ceil(length / STEP)))
        duration = self.analysis.duration
        last_start = duration - length - 0.05
        if start_max is not None:
            last_start = min(last_start, start_max)
        if last_start < start_min:
            return np.array([]), np.array([])
        first = int(math.ceil(start_min / STEP))
        last = int(math.floor(last_start / STEP))
        if last < first:
            return np.array([]), np.array([])
        starts = np.arange(first, last + 1)
        ends = np.minimum(starts + width, len(self.curve))
        sums = self.cumulative[ends] - self.cumulative[starts]
        scores = sums / np.maximum(ends - starts, 1)
        times = starts * STEP
        if len(self.cuts):
            # Pénalité si un changement de plan tombe à l'intérieur de la fenêtre (hors bords).
            inside = np.zeros(len(times), dtype=bool)
            for cut in self.cuts:
                inside |= (cut > times + 0.3) & (cut < times + length - 0.3)
            scores = scores - 0.25 * inside
        return times, scores

    def overlaps(self, start: float, end: float, margin: float = 0.4) -> bool:
        return any(start < used_end + margin and end > used_start - margin for used_start, used_end in self.used)


def _prepare(analyses: Sequence[RushAnalysis], mode: EditMode) -> list[_Rush]:
    rushes = []
    for index, analysis in enumerate(analyses):
        motion_w, audio_w, sharp_w, calm_w = mode.weights
        curve = interest_curve(analysis, motion_weight=motion_w, audio_weight=audio_w,
                               sharpness_weight=sharp_w, calm_weight=calm_w)
        cumulative = np.concatenate([[0.0], np.cumsum(curve)])
        rushes.append(_Rush(index, analysis, curve, cumulative, np.asarray(analysis.scene_cuts)))
    return rushes


def _chronological(analyses: Sequence[RushAnalysis]) -> list[int]:
    return sorted(range(len(analyses)), key=lambda i: (analyses[i].modified, analyses[i].name.casefold()))


# --- Stratégie « beats » --------------------------------------------------------------------------

def _beat_slots(rng: random.Random, mode: EditMode, variant: VariantProfile, beat: float, target: float,
                longest_source: float) -> list[float]:
    allowed = (1, 2, 3, 4, 6, 8, 12, 16)
    max_beats = max(1, int((longest_source / mode.speed - 0.6) / beat))
    total_beats = max(4, int(round(target / beat / 4)) * 4)
    slots: list[int] = []
    while sum(slots) < total_beats:
        beats, _weight = _weighted(rng, mode.shot_beats)
        scaled = beats * variant.pacing
        beats = min(allowed, key=lambda value: abs(value - scaled))
        while beats * beat < mode.min_shot and beats < 16:
            beats = next(value for value in allowed if value > beats)
        beats = max(1, min(beats, max_beats))
        slots.append(beats)
    overflow = sum(slots) - total_beats
    while overflow > 0 and slots:
        if slots[-1] > overflow:
            slots[-1] -= overflow
            overflow = 0
        else:
            overflow -= slots.pop()
    # Bornes quantifiées en cumulé : aucune dérive par rapport à la grille des temps.
    boundaries, position = [0.0], 0
    for value in slots:
        if value > 0:
            position += value
            boundaries.append(quantize(position * beat))
    return [round(boundaries[i + 1] - boundaries[i], 6) for i in range(len(boundaries) - 1)]


def _pick_best(rushes: list[_Rush], length: float, reserve: float, previous_rush: int | None,
               *, allow_overlap: bool = False) -> tuple[_Rush, float, float] | None:
    best = None
    for rush in rushes:
        times, scores = rush.window_scores(length + reserve)
        if not len(times):
            continue
        adjusted = scores - 0.1 * rush.picks - (0.12 if rush.index == previous_rush else 0.0)
        for order in np.argsort(-adjusted):
            start = float(times[order])
            if allow_overlap or not rush.overlaps(start, start + length):
                if best is None or adjusted[order] > best[2]:
                    best = (rush, start, float(adjusted[order]))
                break
    return best


def _select_best(rushes: list[_Rush], slots: list[float], speed: float, reserve: float):
    """Meilleurs moments sans chevauchement ; les plans longs sont placés en premier.

    Chaque rush reçoit un quota proportionnel à sa durée pour garantir la variété.
    """
    chosen: dict[int, tuple[_Rush, float, float]] = {}
    previous = None
    total = sum(rush.analysis.duration for rush in rushes) or 1
    quota = {rush.index: int(math.ceil(len(slots) * rush.analysis.duration / total * 1.5)) + 1 for rush in rushes}
    for slot_index in sorted(range(len(slots)), key=lambda i: -slots[i]):
        need = slots[slot_index] * speed
        open_rushes = [rush for rush in rushes if rush.picks < quota[rush.index]] or rushes
        pick = _pick_best(open_rushes, need, reserve, previous) or _pick_best(rushes, need, reserve, previous) \
            or _pick_best(rushes, need, reserve, previous, allow_overlap=True)
        if pick is None:
            pick = _pick_best(rushes, need, 0.0, previous, allow_overlap=True)
        if pick is None:
            raise PlanError("Les rushs sont trop courts pour ce montage.")
        rush, start, score = pick
        rush.used.append((start, start + need))
        rush.picks += 1
        previous = rush.index
        chosen[slot_index] = (rush, start, score)
    return [chosen[index] for index in range(len(slots))]


def _select_spread(rushes: list[_Rush], slots: list[float], speed: float, reserve: float, order: list[int]):
    """Répartition chronologique : chaque rush reçoit des plans proportionnels à sa durée."""
    durations = {r.index: r.analysis.duration for r in rushes}
    total = sum(durations.values()) or 1
    count = len(slots)
    if count >= len(rushes):
        quotas = {index: 1 for index in order}
        remaining = count - len(rushes)
        shares = {index: durations[index] / total * remaining for index in order}
        for index in order:
            quotas[index] += int(shares[index])
        leftovers = count - sum(quotas.values())
        for index in sorted(order, key=lambda i: -(shares[i] - int(shares[i])))[:leftovers]:
            quotas[index] += 1
    else:
        ranked = sorted(order, key=lambda i: -float(np.mean(rushes[i].curve)))[:count]
        quotas = {index: (1 if index in ranked else 0) for index in order}
    picks = []
    slot_iter = iter(range(count))
    for rush_index in order:
        rush = rushes[rush_index]
        k = quotas[rush_index]
        for part in range(k):
            slot_index = next(slot_iter)
            need = slots[slot_index] * speed
            region = rush.analysis.duration / k
            times, scores = rush.window_scores(need + reserve, start_min=part * region,
                                               start_max=(part + 1) * region - (need + reserve) * 0.5)
            if not len(times):
                times, scores = rush.window_scores(need + reserve)
            if not len(times):
                times, scores = rush.window_scores(need)
            if len(times):
                best = int(np.argmax(scores))
                picks.append((rush, float(times[best]), float(scores[best])))
            else:
                fallback = _pick_best(rushes, need, 0.0, None, allow_overlap=True)
                if fallback is None:
                    raise PlanError("Les rushs sont trop courts pour ce montage.")
                picks.append(fallback)
    return picks


def _order_shots(picks, slots, order: str, chronology: list[int]):
    rank = {index: position for position, index in enumerate(chronology)}
    items = list(zip(picks, slots))
    chrono_key = lambda item: (rank[item[0][0].index], item[0][1])  # noqa: E731
    if order == "chrono" or len(items) < 3:
        return sorted(items, key=chrono_key)
    by_score = sorted(items, key=lambda item: -item[0][2])
    hook = by_score[0]
    rest = [item for item in items if item is not hook]
    if order == "hook":
        return [hook, *sorted(rest, key=chrono_key)]
    # build : accroche, montée progressive, meilleur moment restant à la fin.
    finale = max(rest, key=lambda item: item[0][2])
    middle = sorted((item for item in rest if item is not finale), key=lambda item: item[0][2])
    return [hook, *middle, finale]


def _plan_beats(analyses, mode: EditMode, variant: VariantProfile, request: EditRequest, music: MusicChoice | None,
                rng: random.Random):
    rushes = _prepare(analyses, mode)
    chronology = _chronological(analyses)
    bpm = music.bpm if music else 120.0
    beat = 60.0 / bpm
    target = request.duration or mode.default_duration or 30.0
    target *= 1.0 if request.duration else (0.85 if variant.pacing < 1 else 1.15 if variant.pacing > 1 else 1.0)
    footage = sum(a.duration for a in analyses) / mode.speed
    target = max(min(target, footage * 0.8), 4 * beat, 2.0)
    longest = max(a.duration for a in analyses)
    slots = _beat_slots(rng, mode, variant, beat, target, longest)
    reserve = max((duration for _kind, duration, _w in mode.transitions), default=0.0) * mode.speed
    if mode.selection == "best":
        picks = _select_best(rushes, slots, mode.speed, reserve)
    else:
        picks = _select_spread(rushes, slots, mode.speed, reserve, chronology)
    ordered = _order_shots(picks, slots, variant.order or mode.order, chronology)
    shots: list[Shot] = []
    position = 0.0
    previous_effect = None
    for index, ((rush, start, score), slot) in enumerate(ordered):
        transition = Transition()
        if index:
            kind, duration, _weight = _weighted(rng, mode.transitions)
            on_bar = abs((position / beat) % 4) < 1e-6 or abs((position / beat) % 4 - 4) < 1e-6
            if kind == "fadewhite" and not on_bar and rng.random() < 0.6:
                kind, duration = "cut", 0.0
            duration = quantize(duration)
            prev = shots[-1]
            limit = 0.45 * min(prev.duration, slot)
            if kind != "cut" and duration <= limit:
                prev_rush = analyses[prev.rush_index]
                room = prev_rush.duration - (prev.source_start + (prev.duration + duration) * prev.speed)
                if room >= 0.02:
                    shots[-1] = replace(prev, duration=quantize(prev.duration + duration))
                    transition = Transition(kind, duration)
        effect = _weighted(rng, mode.effects)[0]
        if effect == previous_effect and effect != "none" and rng.random() < 0.5:
            effect = "none"
        previous_effect = effect
        audio = mode.original_audio if analyses[rush.index].has_audio else 0.0
        shots.append(Shot(rush.analysis.path, rush.index, round(start, 3), slot, effect, mode.speed, audio,
                          transition, round(score, 4)))
        position += slot
    return shots, ()


# --- Stratégie « parole » ---------------------------------------------------------------------------

def _split_long(segment: tuple[float, float], longest: float, loudness, rng: random.Random):
    start, end = segment
    if end - start <= longest:
        return [segment]
    parts = max(2, int(math.ceil((end - start) / longest)))
    boundaries = [start]
    for part in range(1, parts):
        ideal = start + (end - start) * part / parts
        # Coupe au point le plus calme à ±0,8 s de la position idéale.
        low, high = int((ideal - 0.8) / STEP), int((ideal + 0.8) / STEP)
        window = loudness[max(0, low):max(low + 1, high)]
        offset = int(np.argmin(window)) if len(window) else 0
        boundaries.append(max(boundaries[-1] + 1.0, (max(0, low) + offset) * STEP))
    boundaries.append(end)
    return [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1) if boundaries[i + 1] - boundaries[i] > 0.4]


def _plan_speech(analyses, mode: EditMode, variant: VariantProfile, request: EditRequest, rng: random.Random):
    rushes = _prepare(analyses, mode)
    order = _chronological(analyses)
    # Variante énergique : coupes serrées ; posée : davantage de respiration autour de la voix.
    gap, pad = (0.3, 0.08) if variant.pacing < 1 else (0.45, 0.15) if variant.pacing == 1 else (0.75, 0.28)
    entries: list[tuple[_Rush, float, float, bool, float]] = []
    for rush_index in order:
        rush = rushes[rush_index]
        analysis = rush.analysis
        segments = activity_segments(analysis.loudness, merge_gap=gap, pad=pad, duration=analysis.duration) \
            if analysis.has_audio else ()
        if segments and sum(e - s for s, e in segments) >= 0.12 * analysis.duration:
            longest = 7.0 if mode.key == "talking" else 14.0
            for segment in segments:
                for start, end in _split_long(segment, longest * variant.pacing, analysis.loudness, rng):
                    a, b = int(start / STEP), max(int(start / STEP) + 1, int(end / STEP))
                    score = float(np.mean(rush.curve[a:b])) if b <= len(rush.curve) else 0.5
                    entries.append((rush, start, end - start, True, score))
        elif mode.key != "talking":
            # Pas de voix : plans d'illustration (moments les plus vivants) sous la musique.
            length = min(analysis.duration - 0.1, 2.6 * variant.pacing)
            if length < 0.6:
                continue
            count = max(1, min(3, int(analysis.duration // 8) + 1))
            region = analysis.duration / count
            for part in range(count):
                times, scores = rush.window_scores(length, start_min=part * region,
                                                   start_max=(part + 1) * region - length * 0.5)
                if not len(times):
                    times, scores = rush.window_scores(length)
                if len(times):
                    best = int(np.argmax(scores))
                    entries.append((rush, float(times[best]), length, False, float(scores[best])))
    if not entries:
        raise PlanError("Aucune parole ni plan exploitable n'a été trouvé dans les rushs.")
    target = request.duration
    if target is None and variant.pacing < 1:
        target = sum(entry[2] for entry in entries) * 0.8
    if target:
        total = sum(entry[2] for entry in entries)
        while total > target * 1.05 and len(entries) > 1:
            weakest = min(range(len(entries)), key=lambda i: entries[i][4])
            total -= entries[weakest][2]
            entries.pop(weakest)
        if total > target * 1.05:
            rush, start, length, speech, score = entries[0]
            entries[0] = (rush, start, target, speech, score)
    shots: list[Shot] = []
    duck: list[tuple[float, float]] = []
    position = 0.0
    zoom_toggle = False
    for index, (rush, start, length, speech, score) in enumerate(entries):
        length = quantize(max(length, 0.4))
        transition = Transition()
        effect = "none"
        if index:
            prev = shots[-1]
            same_rush = prev.rush_index == rush.index
            if same_rush:
                zoom_toggle = not zoom_toggle if mode.key == "talking" or rng.random() < 0.5 else zoom_toggle
                effect = "punch" if zoom_toggle else "none"
            else:
                zoom_toggle = False
                kind, duration, _w = _weighted(rng, mode.transitions)
                duration = quantize(duration)
                prev_rush = analyses[prev.rush_index]
                room = prev_rush.duration - (prev.source_start + prev.duration + duration)
                if kind != "cut" and duration <= 0.45 * min(prev.duration, length) and room >= 0.02:
                    shots[-1] = replace(prev, duration=quantize(prev.duration + duration))
                    transition = Transition(kind, duration)
                    position += duration
        gain = mode.original_audio if speech else (0.25 if rush.analysis.has_audio else 0.0)
        shots.append(Shot(rush.analysis.path, rush.index, round(start, 3), length, effect, 1.0, gain,
                          transition, round(score, 4), speech))
        start_on_timeline = position - transition.duration
        if speech:
            duck.append((start_on_timeline, start_on_timeline + length))
        position = start_on_timeline + length
    return shots, tuple(duck)


# --- Assemblage final du plan ---------------------------------------------------------------------

def _sfx_cues(mode: EditMode, shots: Sequence[Shot], title: str) -> tuple[SfxCue, ...]:
    cues: list[SfxCue] = []
    if mode.sfx_intro:
        cues.append(SfxCue(mode.sfx_intro, 0.0, 0.7))
    starts = shot_starts(shots)
    for shot, start in zip(shots[1:], starts[1:]):
        kind = shot.transition_in.kind
        if kind == "cut":
            continue
        middle = start + shot.transition_in.duration / 2
        if kind in {"fadewhite", "fadeblack"} and mode.sfx_flash:
            cues.append(SfxCue(mode.sfx_flash, max(0.0, middle - 0.35), 0.5))
        elif mode.sfx_transition:
            cues.append(SfxCue(mode.sfx_transition, max(0.0, middle - 0.18), 0.4))
    if title.strip():
        cues.append(SfxCue("pop", 0.25, 0.5))
    # Au plus un effet par demi-seconde pour éviter l'empilement.
    cues.sort(key=lambda cue: cue.time)
    spaced: list[SfxCue] = []
    for cue in cues:
        if not spaced or cue.time - spaced[-1].time >= 0.5:
            spaced.append(SfxCue(cue.key, round(cue.time, 3), cue.volume))
    return tuple(spaced)


def _merge_duck(segments, total: float) -> tuple[tuple[float, float], ...]:
    merged: list[list[float]] = []
    for start, end in sorted(segments):
        start, end = max(0.0, start), min(total, end)
        if end - start < 0.3:
            continue
        if merged and start - merged[-1][1] < 0.6:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((round(a, 3), round(b, 3)) for a, b in merged)


def build_plan(
    analyses: Sequence[RushAnalysis],
    mode: EditMode,
    variant: VariantProfile,
    request: EditRequest,
    music: MusicChoice | None,
) -> EditPlan:
    if not analyses:
        raise PlanError("Ajoutez au moins un rush.")
    if request.width % 2 or request.height % 2:
        raise PlanError("La résolution de sortie doit être paire.")
    seed = plan_seed(analyses, request, variant)
    rng = random.Random(seed)
    if mode.strategy == "speech":
        shots, duck = _plan_speech(analyses, mode, variant, request, rng)
    else:
        shots, duck = _plan_beats(analyses, mode, variant, request, music, rng)
    if not shots:
        raise PlanError("Aucun plan n'a pu être sélectionné.")
    total = timeline_duration(shots)
    look = request.look or _rotate(mode.looks, variant.rotation)
    starts = shot_starts(shots)
    letterbox = mode.letterbox and request.width > request.height
    summary = (f"{len(shots)} plans · {int(total // 60)}:{int(total % 60):02d}"
               f" · {music.title if music else 'sans musique'}")
    return EditPlan(
        mode=mode.key, variant=variant.letter, variant_name=variant.name, width=request.width,
        height=request.height, shots=tuple(shots), look=look, fit=request.fit, letterbox=letterbox,
        fade_in=mode.fade_in, fade_out=min(mode.fade_out, total / 4), music=music,
        duck_segments=_merge_duck(duck, total), duck_level=mode.duck_level,
        sfx=_sfx_cues(mode, shots, request.title), caption_preset=mode.caption_preset,
        title=request.title.strip(), outro=request.outro.strip(), sharpening=mode.sharpening, seed=seed,
        summary=summary, cut_times=tuple(starts[1:]),
    )
