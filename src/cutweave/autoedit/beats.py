"""Estimation classique du tempo d'une musique fournie par l'utilisateur.

Enveloppe d'attaques par flux spectral, autocorrélation sur 70–180 BPM, puis recherche de la
phase du premier temps. Pur traitement du signal, déterministe.
"""

from __future__ import annotations

import numpy as np

from cutweave.autoedit.analysis import AnalysisError, _run_pipe
from cutweave.media.render import ffmpeg_path

RATE = 11025
HOP = 256
FRAME = 1024


def decode_mono(path, *, seconds: float = 90.0, cancel_event=None) -> np.ndarray:
    binary = ffmpeg_path()
    if not binary:
        raise AnalysisError("FFmpeg est introuvable.")
    command = [binary, "-hide_banner", "-loglevel", "error", "-t", f"{seconds:g}", "-i", str(path), "-vn",
               "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"]
    raw = _run_pipe(command, cancel_event)
    return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def onset_envelope(signal: np.ndarray) -> np.ndarray:
    if len(signal) < FRAME * 4:
        return np.zeros(1)
    count = 1 + (len(signal) - FRAME) // HOP
    index = np.arange(FRAME)[None, :] + HOP * np.arange(count)[:, None]
    frames = signal[index] * np.hanning(FRAME)[None, :]
    magnitude = np.log1p(100 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(np.diff(magnitude, axis=0), 0).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")
    return np.maximum(flux, 0)


def estimate_tempo(signal: np.ndarray) -> tuple[float, float]:
    """Retourne (bpm, décalage du premier temps en secondes)."""
    envelope = onset_envelope(signal)
    if len(envelope) < 64 or not np.any(envelope):
        return 120.0, 0.0
    frame_rate = RATE / HOP
    centered = envelope - envelope.mean()
    size = 1 << int(np.ceil(np.log2(2 * len(centered))))
    spectrum = np.fft.rfft(centered, size)
    autocorr = np.fft.irfft(spectrum * np.conj(spectrum), size)[: len(centered)]
    best_bpm, best_value = 120.0, -np.inf
    for bpm in np.arange(70.0, 180.01, 0.5):
        lag = frame_rate * 60.0 / bpm
        low, high = int(np.floor(lag)), int(np.ceil(lag))
        if high >= len(autocorr):
            continue
        value = np.interp(lag, [low, high], [autocorr[low], autocorr[high]]) if high != low else autocorr[low]
        double = 2 * lag
        if int(np.ceil(double)) < len(autocorr):
            value += 0.5 * np.interp(double, [np.floor(double), np.ceil(double)],
                                     [autocorr[int(np.floor(double))], autocorr[int(np.ceil(double))]])
        value *= np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
        if value > best_value:
            best_bpm, best_value = float(bpm), value
    # Affinage conjoint tempo/phase par peigne : moyenne de l'enveloppe sur tous les temps.
    frames = np.arange(len(envelope))
    best = (best_bpm, 0.0, -np.inf)
    for bpm in np.arange(best_bpm - 1.5, best_bpm + 1.501, 0.05):
        period = frame_rate * 60.0 / bpm
        for phase in np.arange(0, period, 0.25):
            positions = np.arange(phase, len(envelope) - 1, period)
            score = np.interp(positions, frames, envelope).mean()
            if score > best[2]:
                best = (float(bpm), float(phase), score)
    bpm, phase, _score = best
    # Le flux spectral culmine quand l'attaque atteint les trois quarts de la fenêtre d'analyse.
    offset = (phase * HOP + 0.75 * FRAME) / RATE % (60.0 / bpm)
    return round(bpm, 2), round(offset, 3)


def tempo_of_file(path, *, cancel_event=None) -> tuple[float, float]:
    return estimate_tempo(decode_mono(path, cancel_event=cancel_event))
