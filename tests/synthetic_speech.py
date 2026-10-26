"""Voix synthétique pour tester l'alignement : une salve voisée par syllabe, pauses selon la ponctuation."""

import numpy as np

from cutweave.autoedit.analysis import AUDIO_RATE, RushAnalysis, STEP, audio_features, activity_segments
from cutweave.subtitles.script import ScriptWord, parse_script

SR = AUDIO_RATE


def burst(duration, f0, rng):
    n = int(duration * SR)
    t = np.arange(n) / SR
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.03 * np.sin(2 * np.pi * 4 * t))) / SR
    wave = sum(np.sin(k * phase) / k for k in range(1, 22))
    envelope = np.sin(np.pi * np.arange(n) / n) ** 1.5
    return 0.3 * wave / np.max(np.abs(wave)) * envelope


def speak(words: list[ScriptWord], *, syllable=0.21, lead=0.8, seed=1, noise_between_sentences=True):
    """Retourne (signal, vérité) ; vérité = [(mot, début, fin)] en secondes."""
    rng = np.random.default_rng(seed)
    total = lead + sum(w.syllables * syllable + 0.9 for w in words) + 1.0
    signal = 0.002 * rng.standard_normal(int(total * SR))
    position = lead
    truth = []
    for index, word in enumerate(words):
        if index:
            position += {0.0: 0.03, 0.5: 0.22, 1.0: 0.65, 1.5: 0.95}[word.pause_before]
        start = position
        for k in range(word.syllables):
            f0 = 105 + 25 * rng.random()
            sound = burst(syllable * 0.78, f0, rng)
            at = int((position + 0.02) * SR)
            signal[at:at + len(sound)] += sound
            position += syllable
        truth.append((word.text, start, position))
        if noise_between_sentences and word.sentence_end:
            cough = rng.standard_normal(int(0.25 * SR)) * np.exp(-np.arange(int(0.25 * SR)) / (0.06 * SR))
            at = int((position + 0.25) * SR)
            signal[at:at + len(cough)] += 0.5 * cough / np.max(np.abs(cough))
    return signal[:int((position + 1.0) * SR)], truth


def analysis_from_signal(signal: np.ndarray, name="synthetic.mp4", modified=0.0) -> RushAnalysis:
    duration = len(signal) / SR
    samples = int(np.ceil(duration / STEP))
    coarse, fine, voicing, envelope = audio_features(signal.astype(np.float32), samples)
    return RushAnalysis(
        path=f"/rushes/{name}", name=name, duration=duration, width=1920, height=1080, frame_rate=30.0,
        has_audio=True, modified=modified, motion=(0.1,) * samples, brightness=(0.5,) * samples,
        sharpness=(100.0,) * samples, loudness=tuple(float(v) for v in coarse), scene_cuts=(),
        activity=activity_segments(fine, step=0.05, duration=duration, voicing=voicing),
        fine_loudness=tuple(float(v) for v in fine), fine_voicing=tuple(float(v) for v in voicing),
        syllable_env=tuple(float(v) for v in envelope),
    )
