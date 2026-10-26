"""Du mot daté aux sous-titres : report sur la timeline du montage, regroupement et mise en forme.

Règles de lisibilité (usage courant des sous-titres) : 2 lignes au plus, une trentaine de caractères par ligne,
cartons coupés aux fins de phrase, aux virgules et aux pauses, vitesse de lecture limitée, aucun recouvrement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cutweave.autoedit.planner import EditPlan, shot_starts
from cutweave.stages.composition.captions import CaptionCue
from cutweave.subtitles.align import TimedWord

GROUPINGS = ("phrase", "words")


@dataclass(frozen=True)
class TimelineWord:
    text: str
    start: float
    end: float
    sentence_end: bool = False
    soft_break: bool = False


@dataclass(frozen=True)
class Layout:
    max_chars: int          # par ligne
    max_lines: int
    max_seconds: float
    min_seconds: float
    max_words: int
    break_gap: float        # silence (s) qui coupe un carton
    lead: float             # apparition anticipée (s)
    hold: float             # maintien après le dernier mot (s)


PHRASE = Layout(max_chars=30, max_lines=2, max_seconds=5.5, min_seconds=0.7, max_words=14, break_gap=0.45,
                lead=0.06, hold=0.25)
WORDS = Layout(max_chars=16, max_lines=1, max_seconds=1.6, min_seconds=0.25, max_words=2, break_gap=0.30,
               lead=0.03, hold=0.08)
LAYOUTS = {"phrase": PHRASE, "words": WORDS}


def map_to_timeline(words: tuple[TimedWord, ...] | list[TimedWord], plan: EditPlan) -> list[TimelineWord]:
    """Place les mots dans la timeline : seuls ceux qui tombent dans un plan conservé sont gardés."""
    starts = shot_starts(plan.shots)
    result: list[TimelineWord] = []
    for word in words:
        middle = (word.start + word.end) / 2
        for shot, shot_start in zip(plan.shots, starts):
            if shot.source != word.source:
                continue
            begin, finish = shot.source_start, shot.source_start + shot.source_duration
            if not (begin - 0.02 <= middle <= finish + 0.02):
                continue
            clamp_start, clamp_end = max(word.start, begin), min(word.end, finish)
            if clamp_end - clamp_start < 0.05:
                continue
            start = shot_start + (clamp_start - begin) / shot.speed
            end = shot_start + (clamp_end - begin) / shot.speed
            result.append(TimelineWord(word.text, start, end, word.sentence_end,
                                       bool(re.search(r"[,;:–—]$", word.text))))
            break
    result.sort(key=lambda item: (item.start, item.end))
    return result


def identity_timeline(words: tuple[TimedWord, ...] | list[TimedWord]) -> list[TimelineWord]:
    """Sous-titres d'un média utilisé tel quel (éditeur) : les instants source sont ceux de la timeline."""
    return [TimelineWord(word.text, word.start, word.end, word.sentence_end,
                         bool(re.search(r"[,;:–—]$", word.text))) for word in words]


def _wrap(text_words: list[str], layout: Layout) -> str:
    """Répartit les mots sur au plus ``max_lines`` lignes équilibrées."""
    text = " ".join(text_words)
    if layout.max_lines <= 1 or len(text) <= layout.max_chars:
        return text
    best, best_gap = text, None
    for cut in range(1, len(text_words)):
        first, second = " ".join(text_words[:cut]), " ".join(text_words[cut:])
        if len(first) > layout.max_chars + 4 or len(second) > layout.max_chars + 4:
            continue
        gap = abs(len(first) - len(second)) + (0 if len(second) <= len(first) else 2)
        if best_gap is None or gap < best_gap:
            best, best_gap = f"{first}\n{second}", gap
    return best


def group_words(words: list[TimelineWord], grouping: str = "phrase") -> list[CaptionCue]:
    layout = LAYOUTS[grouping]
    capacity = layout.max_chars * layout.max_lines
    groups: list[list[TimelineWord]] = []
    current: list[TimelineWord] = []

    def flush():
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for word in words:
        if current:
            characters = len(" ".join(item.text for item in current)) + 1 + len(word.text)
            too_long = characters > capacity or len(current) >= layout.max_words \
                or word.end - current[0].start > layout.max_seconds
            gap = word.start - current[-1].end
            if too_long or gap >= layout.break_gap:
                flush()
        current.append(word)
        last = current[-1]
        if last.sentence_end:
            flush()
        elif last.soft_break and grouping == "phrase" and len(" ".join(item.text for item in current)) >= capacity * 0.6:
            flush()
    flush()
    cues: list[CaptionCue] = []
    previous_end = 0.0
    for index, group in enumerate(groups):
        following = groups[index + 1][0].start if index + 1 < len(groups) else None
        start = max(previous_end, group[0].start - layout.lead)
        end = group[-1].end + layout.hold
        characters = len(" ".join(item.text for item in group))
        end = max(end, start + layout.min_seconds, start + characters / 20.0 if grouping == "phrase" else end)
        if following is not None:
            end = min(end, following - 0.04)
        end = max(end, group[-1].end if following is None else min(group[-1].end, following - 0.04))
        if end - start < 0.12:
            end = start + 0.12
        start_ms, end_ms = int(round(start * 1000)), int(round(end * 1000))
        if end_ms <= start_ms:
            continue
        text = _wrap([item.text for item in group], layout)
        cues.append(CaptionCue(start_ms, end_ms, text))
        previous_end = end_ms / 1000
    return cues


def cues_for_plan(words: tuple[TimedWord, ...] | list[TimedWord], plan: EditPlan, grouping: str = "phrase") -> list[CaptionCue]:
    return group_words(map_to_timeline(words, plan), grouping)


def cues_for_media(words: tuple[TimedWord, ...] | list[TimedWord], grouping: str = "phrase") -> list[CaptionCue]:
    return group_words(identity_timeline(words), grouping)
