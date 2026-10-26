"""Alignement d'un script sur la voix, sans reconnaissance vocale.

Principe (traitement du signal classique) :

1. Les syllabes du son sont repérées comme des pics d'énergie dans la bande de la voix (250–2500 Hz), uniquement
   là où le son est voisé (voir ``voicing_curve``) : toux et bruits n'en produisent pas.
2. Le script est découpé en syllabes (``script.count_syllables``) avec les pauses que la ponctuation laisse attendre.
3. Une programmation dynamique aligne les deux suites : coût de saut pour une syllabe non détectée ou parasite,
   coût de désaccord entre la pause attendue (virgule, point, paragraphe) et la pause mesurée avant chaque pic.
4. Les frontières de mots sont placées au creux d'énergie entre deux syllabes, ou au début/à la fin du souffle
   quand une pause les sépare.

Le texte vient de l'utilisateur : rien n'est « deviné » depuis le son, seuls les instants sont déterminés.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cutweave.autoedit.analysis import ENV_STEP, FINE_STEP, RushAnalysis, activity_segments
from cutweave.subtitles.script import ScriptWord

PEAK_OFFSET = 0.016            # centre de la fenêtre d'analyse (256 échantillons à 8 kHz) par rapport à l'indice
MIN_PROMINENCE_DB = 2.4
MIN_SYLLABLE_GAP = 0.085
PAUSE_SHORT, PAUSE_LONG = 0.22, 0.45
SKIP_SCRIPT, SKIP_NUCLEUS = 0.7, 0.45
# Coût d'un désaccord entre pause attendue (ligne) et pause observée (colonne : aucune, courte, longue).
PAUSE_COST = {0.0: (0.0, 0.05, 0.35), 0.5: (0.12, 0.0, 0.1), 1.0: (0.9, 0.4, 0.0), 1.5: (1.0, 0.45, 0.0)}


@dataclass(frozen=True)
class Nucleus:
    time: float
    level: float
    gap_before: float
    rush: int


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float
    source: str                 # fichier d'où vient la voix
    rush: int                   # indice du rush dans la liste fournie
    score: float                # part des syllabes du mot appariées à un pic détecté (0..1)
    sentence_end: bool = False
    pause_before: float = 0.0


@dataclass(frozen=True)
class AlignmentResult:
    words: tuple[TimedWord, ...]
    matched_ratio: float        # syllabes du script appariées / syllabes du script
    unmatched_nuclei: int       # pics de voix sans équivalent dans le script (texte incomplet, ad-lib…)
    syllables_script: int
    syllables_audio: int

    @property
    def quality(self) -> float:
        """Estimation 0..1 de la fiabilité : appariement et équilibre entre syllabes entendues et écrites."""
        if not self.syllables_script or not self.syllables_audio:
            return 0.0
        balance = min(self.syllables_script, self.syllables_audio) / max(self.syllables_script, self.syllables_audio)
        return float(min(1.0, self.matched_ratio) * (0.35 + 0.65 * balance))


# --- Détection des syllabes -----------------------------------------------------------------------------

def smooth_envelope(analysis: RushAnalysis) -> np.ndarray:
    env = np.asarray(analysis.syllable_env, dtype=np.float64)
    if len(env) < 5:
        return env
    return np.convolve(env, np.array([1, 2, 3, 2, 1], dtype=np.float64) / 9, mode="same")


def speech_mask(analysis: RushAnalysis, frames: int) -> np.ndarray:
    """Vrai pour les pas d'enveloppe situés dans de la voix (niveau et périodicité), faux pour silences et bruits."""
    if analysis.fine_loudness:
        voicing = np.asarray(analysis.fine_voicing) if len(analysis.fine_voicing) == len(analysis.fine_loudness) else None
        segments = activity_segments(analysis.fine_loudness, step=FINE_STEP, merge_gap=0.3, pad=0.06, min_len=0.15,
                                     duration=analysis.duration, voicing=voicing)
    else:
        segments = analysis.activity
    mask = np.zeros(frames, dtype=bool)
    for start, end in segments:
        mask[max(0, int(start / ENV_STEP)):min(frames, int(end / ENV_STEP) + 1)] = True
    return mask


def detect_nuclei(analysis: RushAnalysis, rush: int = 0) -> list[Nucleus]:
    smooth = smooth_envelope(analysis)
    if len(smooth) < 10:
        return []
    mask = speech_mask(analysis, len(smooth))
    if not mask.any():
        return []
    reference = float(np.percentile(smooth[mask], 90))
    window = int(round(0.25 / ENV_STEP))
    candidates: list[tuple[float, int]] = []
    for index in np.nonzero(mask[1:-1])[0] + 1:
        value = smooth[index]
        if not (value > smooth[index - 1] and value >= smooth[index + 1]) or value < reference - 22:
            continue
        left = smooth[max(0, index - window):index].min() if index > 0 else value
        right = smooth[index + 1:index + window + 1].min() if index + 1 < len(smooth) else value
        prominence = value - max(left, right)
        if prominence >= MIN_PROMINENCE_DB:
            candidates.append((prominence, int(index)))
    kept: list[int] = []
    min_frames = max(1, int(round(MIN_SYLLABLE_GAP / ENV_STEP)))
    taken = np.zeros(len(smooth), dtype=bool)
    for _prominence, index in sorted(candidates, reverse=True):
        if taken[max(0, index - min_frames):index + min_frames + 1].any():
            continue
        taken[index] = True
        kept.append(index)
    kept.sort()
    nuclei: list[Nucleus] = []
    previous = None
    for index in kept:
        time = index * ENV_STEP + PEAK_OFFSET
        nuclei.append(Nucleus(time, float(smooth[index]), 9.9 if previous is None else time - previous, rush))
        previous = time
    return nuclei


# --- Alignement ----------------------------------------------------------------------------------------

def _pause_class(gap: float) -> int:
    return 0 if gap < PAUSE_SHORT else (1 if gap < PAUSE_LONG else 2)


def dynamic_alignment(expected: np.ndarray, gaps: np.ndarray) -> np.ndarray:
    """Appariement monotone entre n syllabes écrites et m pics ; retourne pour chaque syllabe l'indice du pic ou -1."""
    n, m = len(expected), len(gaps)
    if n == 0 or m == 0:
        return np.full(n, -1, dtype=np.int64)
    observed = np.array([_pause_class(float(gap)) for gap in gaps])
    rows = {value: np.array(costs)[observed] for value, costs in PAUSE_COST.items()}
    band = max(30, int(0.3 * max(n, m)))
    infinity = 1e12
    previous = np.full(m + 1, infinity)
    ramp = np.arange(m + 1) * SKIP_NUCLEUS
    lo0, hi0 = 0, min(m, band)
    previous[lo0:hi0 + 1] = ramp[lo0:hi0 + 1]
    origin = np.zeros((n + 1, m + 1), dtype=np.int8)       # 0 diagonale, 1 saut de syllabe, 2 saut de pic
    columns = np.arange(m + 1)
    for i in range(1, n + 1):
        center = i * m / n
        lo, hi = max(0, int(center) - band), min(m, int(center) + band)
        current = np.full(m + 1, infinity)
        span = columns[lo:hi + 1]
        diag = np.full(len(span), infinity)
        valid = span >= 1
        match = rows[float(expected[i - 1])][np.maximum(span[valid] - 1, 0)]
        diag[valid] = previous[span[valid] - 1] + match
        up = previous[lo:hi + 1] + SKIP_SCRIPT
        base = np.minimum(diag, up) + 0.002 * np.abs(span - center)
        shifted = base - span * SKIP_NUCLEUS
        accumulated = np.minimum.accumulate(shifted) + span * SKIP_NUCLEUS
        current[lo:hi + 1] = accumulated
        choose_diag = diag <= up
        for_origin = np.where(choose_diag, 0, 1).astype(np.int8)
        from_left = accumulated < base - 1e-9
        origin[i, lo:hi + 1] = np.where(from_left, 2, for_origin)
        previous = current
    tail = previous + (m - columns) * SKIP_NUCLEUS
    j = int(np.argmin(tail))
    result = np.full(n, -1, dtype=np.int64)
    i = n
    while i > 0:
        step = origin[i, j]
        if step == 2 and j > 0:
            j -= 1
        elif step == 0 and j > 0:
            result[i - 1] = j - 1
            i, j = i - 1, j - 1
        else:
            i -= 1
    return result


def _interpolate(times: np.ndarray, matched: np.ndarray, typical: float = 0.2) -> np.ndarray:
    """Remplit les syllabes non appariées par interpolation entre leurs voisines appariées."""
    result = times.copy()
    indices = np.nonzero(matched)[0]
    if not len(indices):
        return result
    first, last = indices[0], indices[-1]
    for i in range(len(result)):
        if matched[i]:
            continue
        if i < first:
            result[i] = times[first] - (first - i) * typical
        elif i > last:
            result[i] = times[last] + (i - last) * typical
        else:
            before = indices[indices < i][-1]
            after = indices[indices > i][0]
            ratio = (i - before) / (after - before)
            result[i] = times[before] + ratio * (times[after] - times[before])
    return np.maximum.accumulate(result)


def _rise_point(env: np.ndarray, center: int, drop_db: float = 12.0, limit: float = 0.45) -> int:
    """Indice où l'énergie commence à monter avant un pic (début de souffle)."""
    floor = env[center] - drop_db
    index = center
    for k in range(center, max(0, center - int(limit / ENV_STEP)) - 1, -1):
        if env[k] < floor:
            return k
        index = k
    return index


def _fall_point(env: np.ndarray, center: int, drop_db: float = 12.0, limit: float = 0.45) -> int:
    floor = env[center] - drop_db
    index = center
    for k in range(center, min(len(env) - 1, center + int(limit / ENV_STEP)) + 1):
        if env[k] < floor:
            return k
        index = k
    return index


def align_script(
    words: list[ScriptWord],
    analyses: list[RushAnalysis],
) -> AlignmentResult:
    """Cale chaque mot du script sur la voix des rushs, pris dans l'ordre donné. Les instants sont dans le temps source."""
    syllable_expect: list[float] = []
    owner: list[int] = []
    for word_index, word in enumerate(words):
        for k in range(word.syllables):
            syllable_expect.append(word.pause_before if k == 0 else 0.0)
            owner.append(word_index)
    nuclei: list[Nucleus] = []
    envelopes: list[np.ndarray] = []
    for rush, analysis in enumerate(analyses):
        found = detect_nuclei(analysis, rush)
        nuclei.extend(found)
        envelopes.append(smooth_envelope(analysis))
    expected = np.array(syllable_expect, dtype=np.float64)
    gaps = np.array([nucleus.gap_before for nucleus in nuclei], dtype=np.float64)
    mapping = dynamic_alignment(expected, gaps)
    matched = mapping >= 0
    if not matched.any():
        return AlignmentResult((), 0.0, len(nuclei), len(expected), len(nuclei))
    times = np.array([nuclei[j].time if j >= 0 else 0.0 for j in mapping])
    rush_of = np.array([nuclei[j].rush if j >= 0 else -1 for j in mapping])
    # Un script suit les rushs dans l'ordre : on remplit les trous avec le rush des voisines.
    for i in range(len(rush_of)):
        if rush_of[i] < 0:
            neighbours = np.nonzero(rush_of >= 0)[0]
            rush_of[i] = rush_of[neighbours[np.argmin(np.abs(neighbours - i))]]
    syllable_times = np.zeros(len(times))
    for rush in sorted(set(rush_of.tolist())):
        selector = rush_of == rush
        syllable_times[selector] = _interpolate(times[selector], matched[selector])
    # Frontières de mots.
    word_slices: list[tuple[int, int]] = []
    position = 0
    for word in words:
        word_slices.append((position, position + word.syllables))
        position += word.syllables
    first_time = np.array([syllable_times[a] for a, _ in word_slices])
    last_time = np.array([syllable_times[b - 1] for _, b in word_slices])
    word_rush = np.array([int(rush_of[a]) for a, _ in word_slices])
    starts = np.zeros(len(words))
    ends = np.zeros(len(words))
    for w in range(len(words)):
        env = envelopes[word_rush[w]]
        previous_same = w > 0 and word_rush[w - 1] == word_rush[w]
        next_same = w + 1 < len(words) and word_rush[w + 1] == word_rush[w]
        tf, tl = first_time[w], last_time[w]
        if previous_same and tf - last_time[w - 1] <= PAUSE_LONG:
            a, b = int((last_time[w - 1] + 0.02) / ENV_STEP), int((tf - 0.02) / ENV_STEP)
            starts[w] = (a + int(np.argmin(env[a:b + 1]))) * ENV_STEP + PEAK_OFFSET if b > a else tf - 0.06
        else:
            index = min(len(env) - 1, max(0, int((tf - PEAK_OFFSET) / ENV_STEP)))
            starts[w] = _rise_point(env, index) * ENV_STEP + PEAK_OFFSET
            if previous_same:
                starts[w] = max(starts[w], last_time[w - 1] + 0.05)
        if next_same and first_time[w + 1] - tl <= PAUSE_LONG:
            ends[w] = 0.0   # fixé par le début du mot suivant
        else:
            index = min(len(env) - 1, max(0, int((tl - PEAK_OFFSET) / ENV_STEP)))
            ends[w] = _fall_point(env, index) * ENV_STEP + PEAK_OFFSET + 0.03
    for w in range(len(words) - 1):
        if ends[w] == 0.0:
            ends[w] = starts[w + 1]
    if ends[-1] == 0.0:
        ends[-1] = last_time[-1] + 0.2
    # Cohérence : durée minimale, ordre, pas de recouvrement dans un même rush.
    for w in range(len(words)):
        starts[w] = min(starts[w], first_time[w])
        ends[w] = max(ends[w], last_time[w] + 0.06, starts[w] + 0.08)
        if w and word_rush[w] == word_rush[w - 1]:
            starts[w] = max(starts[w], min(ends[w - 1], first_time[w]))
            ends[w - 1] = min(ends[w - 1], max(starts[w], starts[w - 1] + 0.05))
    scores = []
    for a, b in word_slices:
        scores.append(float(matched[a:b].mean()))
    timed = tuple(
        TimedWord(word.text, float(max(0.0, starts[w])), float(ends[w]), analyses[word_rush[w]].path,
                  int(word_rush[w]), scores[w], word.sentence_end, word.pause_before)
        for w, word in enumerate(words)
    )
    used = {int(j) for j in mapping if j >= 0}
    return AlignmentResult(timed, float(matched.mean()), len(nuclei) - len(used), len(expected), len(nuclei))
