"""Synthèse procédurale déterministe de musiques et d'effets sonores (numpy uniquement).

Aucun échantillon n'est téléchargé : chaque son est calculé à partir d'oscillateurs, de bruit
pseudo-aléatoire à graine fixe, d'enveloppes et de filtres dans le domaine fréquentiel. Le même
appel produit toujours exactement les mêmes échantillons.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44_100


# --- Briques de base -------------------------------------------------------------------------

def midi_hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _time(duration: float) -> np.ndarray:
    return np.arange(max(1, int(round(duration * SAMPLE_RATE)))) / SAMPLE_RATE


def _phase(frequency) -> np.ndarray:
    """Phase cumulée pour une fréquence constante ou variable (tableau)."""
    return 2 * np.pi * np.cumsum(np.asarray(frequency, dtype=np.float64)) / SAMPLE_RATE


def saw(frequency, length: int) -> np.ndarray:
    freq = np.broadcast_to(np.asarray(frequency, dtype=np.float64), (length,))
    phase = np.cumsum(freq) / SAMPLE_RATE
    return 2.0 * (phase - np.floor(phase + 0.5))


def square(frequency, length: int, duty: float = 0.5) -> np.ndarray:
    freq = np.broadcast_to(np.asarray(frequency, dtype=np.float64), (length,))
    phase = np.cumsum(freq) / SAMPLE_RATE
    return np.where((phase % 1.0) < duty, 1.0, -1.0)


def triangle(frequency, length: int) -> np.ndarray:
    return 2.0 * np.abs(saw(frequency, length)) - 1.0


def envelope(length: int, attack: float, decay: float, sustain: float, release: float, hold: float | None = None) -> np.ndarray:
    """ADSR en secondes ; ``hold`` = durée de maintien avant relâchement (défaut : jusqu'à la fin)."""
    a = max(1, int(attack * SAMPLE_RATE))
    d = max(1, int(decay * SAMPLE_RATE))
    r = max(1, int(release * SAMPLE_RATE))
    held = length - r if hold is None else max(a + d, int(hold * SAMPLE_RATE))
    env = np.full(length, sustain, dtype=np.float64)
    env[:min(a, length)] = np.linspace(0, 1, a, endpoint=False)[:length]
    if a < length:
        end = min(a + d, length)
        env[a:end] = np.linspace(1, sustain, d, endpoint=False)[:end - a]
    if held < length:
        start = max(0, held)
        env[start:] = np.linspace(env[start - 1] if start else sustain, 0, length - start)
    return env


def exp_decay(length: int, seconds: float) -> np.ndarray:
    return np.exp(-np.arange(length) / (max(1e-4, seconds) * SAMPLE_RATE))


def _spectral_filter(signal: np.ndarray, gain_of_freq) -> np.ndarray:
    size = len(signal)
    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(size, 1 / SAMPLE_RATE)
    spectrum *= gain_of_freq(freqs)
    return np.fft.irfft(spectrum, size)


def lowpass(signal: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    return _spectral_filter(signal, lambda f: 1 / np.sqrt(1 + (f / cutoff) ** (2 * order)))


def highpass(signal: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    return _spectral_filter(signal, lambda f: 1 / np.sqrt(1 + (cutoff / np.maximum(f, 1e-3)) ** (2 * order)))


def bandpass(signal: np.ndarray, low: float, high: float) -> np.ndarray:
    return highpass(lowpass(signal, high), low)


def swept_filter(signal: np.ndarray, start_hz: float, end_hz: float, *, mode: str = "low", block: int = 2048) -> np.ndarray:
    """Filtre à fréquence de coupure glissante (traitement par blocs recouvrants)."""
    size = len(signal)
    hop = block // 2
    window = np.hanning(block)
    padded = np.concatenate([np.zeros(hop), signal, np.zeros(block)])
    output = np.zeros_like(padded)
    norm = np.zeros_like(padded)
    freqs = np.fft.rfftfreq(block, 1 / SAMPLE_RATE)
    positions = range(0, size + hop, hop)
    count = max(1, len(positions) - 1)
    for index, start in enumerate(positions):
        ratio = index / count
        cutoff = start_hz * (end_hz / start_hz) ** ratio
        frame = padded[start:start + block] * window
        spectrum = np.fft.rfft(frame)
        if mode == "low":
            gain = 1 / np.sqrt(1 + (freqs / cutoff) ** 4)
        elif mode == "band":
            gain = np.exp(-0.5 * (np.log2(np.maximum(freqs, 1) / cutoff) / 0.6) ** 2)
        else:
            gain = 1 / np.sqrt(1 + (cutoff / np.maximum(freqs, 1e-3)) ** 4)
        output[start:start + block] += np.fft.irfft(spectrum * gain, block) * window
        norm[start:start + block] += window ** 2
    output = output / np.maximum(norm, 1e-6)
    return output[hop:hop + size]


def reverb(signal: np.ndarray, seconds: float = 1.6, mix: float = 0.25, seed: int = 7, damping: float = 5000) -> np.ndarray:
    """Réverbération par convolution avec une réponse impulsionnelle de bruit décroissant."""
    rng = np.random.default_rng(seed)
    length = int(seconds * SAMPLE_RATE)
    impulse = rng.standard_normal(length) * exp_decay(length, seconds / 6.9)
    impulse = lowpass(impulse, damping)
    impulse[: int(0.012 * SAMPLE_RATE)] = 0
    impulse /= np.sqrt(np.sum(impulse ** 2)) + 1e-9
    size = 1 << int(np.ceil(np.log2(len(signal) + length)))
    wet = np.fft.irfft(np.fft.rfft(signal, size) * np.fft.rfft(impulse, size), size)[: len(signal)]
    return signal * (1 - mix) + wet * mix * 0.9


def _place(buffer: np.ndarray, sound: np.ndarray, start: int) -> None:
    if start >= len(buffer):
        return
    end = min(len(buffer), start + len(sound))
    buffer[start:end] += sound[: end - start]


def normalize(signal: np.ndarray, peak_db: float = -1.0) -> np.ndarray:
    peak = np.max(np.abs(signal)) or 1.0
    return signal / peak * 10 ** (peak_db / 20)


def soft_clip(signal: np.ndarray, drive: float = 1.2) -> np.ndarray:
    return np.tanh(signal * drive) / np.tanh(drive)


def write_wav(path: str | Path, left: np.ndarray, right: np.ndarray | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    right = left if right is None else right
    stereo = np.stack([left, right], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    temporary = target.with_name(f".{target.name}.part")
    with wave.open(str(temporary), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(pcm.tobytes())
    temporary.replace(target)
    return target


# --- Instruments ------------------------------------------------------------------------------

def kick(punch: float = 1.0, length_s: float = 0.45, low_hz: float = 46.0) -> np.ndarray:
    n = int(length_s * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    freq = low_hz + (150 * punch) * np.exp(-t / 0.035)
    body = np.sin(_phase(freq)) * exp_decay(n, length_s / 3.2)
    click = np.random.default_rng(1).standard_normal(n) * exp_decay(n, 0.003) * 0.25
    return soft_clip(body + click, 1.6)


def snare(seed: int = 2, tone_hz: float = 185.0, length_s: float = 0.28) -> np.ndarray:
    n = int(length_s * SAMPLE_RATE)
    noise = bandpass(np.random.default_rng(seed).standard_normal(n), 900, 9000) * exp_decay(n, 0.07)
    tone = np.sin(_phase(np.full(n, tone_hz))) * exp_decay(n, 0.05)
    return normalize(noise * 0.9 + tone * 0.6, -3)


def clap(seed: int = 3) -> np.ndarray:
    n = int(0.3 * SAMPLE_RATE)
    noise = bandpass(np.random.default_rng(seed).standard_normal(n), 1000, 7000)
    env = np.zeros(n)
    for offset in (0.0, 0.011, 0.022):
        start = int(offset * SAMPLE_RATE)
        env[start:] += exp_decay(n - start, 0.008 if offset < 0.02 else 0.09)
    return normalize(noise * env, -3)


def hat(seed: int = 4, open_hat: bool = False) -> np.ndarray:
    n = int((0.35 if open_hat else 0.06) * SAMPLE_RATE)
    noise = highpass(np.random.default_rng(seed).standard_normal(n), 7000, 3)
    return normalize(noise * exp_decay(n, 0.11 if open_hat else 0.014), -6)


def tom(freq_hz: float = 90.0, length_s: float = 0.9) -> np.ndarray:
    n = int(length_s * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    body = np.sin(_phase(freq_hz * (1 + 0.6 * np.exp(-t / 0.05)))) * exp_decay(n, length_s / 4)
    noise = lowpass(np.random.default_rng(9).standard_normal(n), 1500) * exp_decay(n, 0.04) * 0.4
    return soft_clip(body + noise, 1.4)


def pluck(note: float, length_s: float, *, bright: float = 3000, decay: float = 0.25, wave_shape: str = "saw") -> np.ndarray:
    n = int(length_s * SAMPLE_RATE)
    freq = midi_hz(note)
    osc = saw(freq, n) if wave_shape == "saw" else square(freq, n, 0.35) if wave_shape == "square" else triangle(freq, n)
    return lowpass(osc, bright) * exp_decay(n, decay) * envelope(n, 0.003, 0.01, 1, 0.03)


def keys(note: float, length_s: float, *, decay: float = 0.9) -> np.ndarray:
    """Piano électrique simplifié : sinusoïdes harmoniques à décroissance différenciée."""
    n = int(length_s * SAMPLE_RATE)
    f = midi_hz(note)
    tone = (np.sin(_phase(np.full(n, f))) * exp_decay(n, decay)
            + 0.35 * np.sin(_phase(np.full(n, 2 * f))) * exp_decay(n, decay / 2.5)
            + 0.12 * np.sin(_phase(np.full(n, 3.01 * f))) * exp_decay(n, decay / 5))
    return tone * envelope(n, 0.004, 0.05, 1, min(0.2, length_s / 3))


def pad(notes, length_s: float, *, cutoff: float = 1800, attack: float = 0.4, release: float = 0.6, detune_cents: float = 9) -> np.ndarray:
    n = int(length_s * SAMPLE_RATE)
    voice = np.zeros(n)
    for note in notes:
        for cents in (-detune_cents, 0, detune_cents):
            voice += saw(midi_hz(note) * 2 ** (cents / 1200), n)
    voice = lowpass(voice / (3 * max(1, len(notes))), cutoff)
    return voice * envelope(n, attack, 0.2, 0.85, release)


def bass(note: float, length_s: float, *, cutoff: float = 420, shape: str = "saw", decay: float | None = None) -> np.ndarray:
    n = int(length_s * SAMPLE_RATE)
    freq = midi_hz(note)
    osc = saw(freq, n) * 0.6 + np.sin(_phase(np.full(n, freq))) * 0.8 if shape == "saw" else np.sin(_phase(np.full(n, freq)))
    osc = lowpass(osc, cutoff)
    env = envelope(n, 0.005, 0.08, 0.8, 0.04) if decay is None else exp_decay(n, decay) * envelope(n, 0.004, 0.01, 1, 0.02)
    return soft_clip(osc * env, 1.3)


# --- Morceaux ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackSpec:
    key: str
    title: str
    mood: str
    bpm: float
    bars: int
    description: str


CHORDS = {  # notes MIDI (accords en position serrée)
    "Am": (57, 60, 64), "F": (53, 57, 60), "C": (48, 52, 55), "G": (55, 59, 62), "Em": (52, 55, 59),
    "Dm": (50, 53, 57), "Fmaj7": (53, 57, 60, 64), "Em7": (52, 55, 59, 62), "Dm7": (50, 53, 57, 60),
    "Cmaj7": (48, 52, 55, 59), "Bb": (58, 62, 65), "Gm": (55, 58, 62), "Eb": (51, 55, 58), "D": (50, 54, 57),
    "Bm": (47, 50, 54), "A": (57, 61, 64), "E": (52, 56, 59),
}


def _grid(spec: TrackSpec):
    beat = 60.0 / spec.bpm
    length = int(round(spec.bars * 4 * beat * SAMPLE_RATE))
    return beat, length


def _at(seconds: float) -> int:
    return int(round(seconds * SAMPLE_RATE))


def _sidechain(length: int, beat: float, depth: float = 0.65, release: float = 0.22) -> np.ndarray:
    env = np.ones(length)
    step = beat * SAMPLE_RATE
    shape = 1 - depth * np.exp(-np.arange(int(step)) / (release * step))
    position = 0.0
    while int(position) < length:
        start = int(position)
        end = min(length, start + len(shape))
        env[start:end] = shape[: end - start]
        position += step
    return env


def _stereo(mono: np.ndarray, width: float = 0.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    if width <= 0:
        return mono, mono
    delay = int(0.012 * SAMPLE_RATE)
    delayed = np.concatenate([np.zeros(delay), mono[:-delay]])
    return mono * (1 - width / 2) + delayed * width / 2, mono * (1 - width / 2) - delayed * width / 4 + mono * width / 4


def track_pulse(spec: TrackSpec):
    """Électro rythmée 4/4 avec sidechain, arpège et basse en croches."""
    beat, n = _grid(spec)
    progression = ["Am", "F", "C", "G"]
    drums, music, bass_line = np.zeros(n), np.zeros(n), np.zeros(n)
    k, s, h, oh = kick(1.0), clap(), hat(), hat(open_hat=True)
    for bar in range(spec.bars):
        chord = CHORDS[progression[bar % 4]]
        bar_start = bar * 4 * beat
        intro = bar < 2
        for b in range(4):
            t = bar_start + b * beat
            if not intro:
                _place(drums, k * 0.95, _at(t))
            if b in (1, 3) and not intro:
                _place(drums, s * 0.55, _at(t))
            _place(drums, oh * 0.22, _at(t + beat / 2))
            for sub in (0.25, 0.75):
                _place(drums, h * 0.18, _at(t + beat * sub))
        _place(music, pad(chord, 4 * beat, cutoff=2400, attack=0.05, release=0.3) * 0.32, _at(bar_start))
        arp = [chord[i % len(chord)] + 12 * (i // len(chord) % 2) for i in range(16)]
        for i, note in enumerate(arp):
            _place(music, pluck(note + 12, beat / 2, bright=4200, decay=0.12) * 0.16, _at(bar_start + i * beat / 4))
        root = chord[0] - 24
        for i in range(8):
            _place(bass_line, bass(root, beat / 2 * 0.9, cutoff=500) * 0.5, _at(bar_start + i * beat / 2))
        if bar % 4 == 3:
            for i in range(4):
                _place(drums, s * (0.25 + 0.1 * i), _at(bar_start + 3 * beat + i * beat / 4))
    side = _sidechain(n, beat)
    mix = drums + (music + bass_line) * side
    left, right = _stereo(reverb(mix, 1.2, 0.12), 0.35)
    return left, right


def track_overdrive(spec: TrackSpec):
    """Énergie sport : 140 BPM, basse décalée, stabs saturés, roulements."""
    beat, n = _grid(spec)
    progression = ["Dm", "Bb", "F", "C"]
    drums, music, low = np.zeros(n), np.zeros(n), np.zeros(n)
    k, s, h = kick(1.3, low_hz=50), snare(5, 200), hat(6)
    for bar in range(spec.bars):
        chord = CHORDS[progression[bar % 4]]
        bar_start = bar * 4 * beat
        for b in range(4):
            t = bar_start + b * beat
            _place(drums, k, _at(t))
            if b in (1, 3):
                _place(drums, s * 0.7, _at(t))
            for sub in range(4):
                _place(drums, h * (0.22 if sub % 2 else 0.12), _at(t + sub * beat / 4))
            _place(low, bass(chord[0] - 24, beat / 2 * 0.85, cutoff=700) * 0.6, _at(t + beat / 2))
        for hit in (0, 1.5, 3):
            stab = pad([note + 12 for note in chord], beat * 0.45, cutoff=3500, attack=0.005, release=0.08)
            _place(music, soft_clip(stab * 2.2, 2.0) * 0.18, _at(bar_start + hit * beat))
        if bar % 8 == 7:
            for i in range(8):
                _place(drums, s * (0.2 + 0.08 * i), _at(bar_start + 2 * beat + i * beat / 4))
    side = _sidechain(n, beat, 0.5, 0.18)
    return _stereo(reverb(drums + (music + low) * side, 0.9, 0.1), 0.3)


def track_sunday(spec: TrackSpec):
    """Lo-fi chill : accords jazzy, batterie feutrée avec swing."""
    beat, n = _grid(spec)
    progression = ["Fmaj7", "Em7", "Dm7", "Cmaj7"]
    drums, music, low = np.zeros(n), np.zeros(n), np.zeros(n)
    k, s, h = kick(0.6, 0.35, 52), snare(8, 170, 0.22), hat(10)
    swing = beat * 0.08
    for bar in range(spec.bars):
        chord = CHORDS[progression[bar % 4]]
        bar_start = bar * 4 * beat
        for b in range(4):
            t = bar_start + b * beat
            if b in (0, 2) or (b == 3 and bar % 2):
                _place(drums, k * 0.7, _at(t + (beat / 2 if b == 3 else 0)))
            if b in (1, 3):
                _place(drums, s * 0.4, _at(t))
            _place(drums, h * 0.16, _at(t))
            _place(drums, h * 0.1, _at(t + beat / 2 + swing))
        for idx, offset in enumerate((0, 1.5, 2.5)):
            voicing = [note + 12 for note in chord]
            for j, note in enumerate(voicing):
                _place(music, keys(note, beat * 1.6) * 0.1, _at(bar_start + offset * beat + j * 0.012 + idx * 0.004))
        _place(low, bass(chord[0] - 12, beat * 1.8, cutoff=300, shape="sine") * 0.55, _at(bar_start))
        _place(low, bass(chord[0] - 12 + 7, beat * 0.9, cutoff=300, shape="sine") * 0.4, _at(bar_start + 2.5 * beat))
    mix = lowpass(drums, 6500) + reverb(music, 1.8, 0.3) + low
    noise = lowpass(np.random.default_rng(11).standard_normal(n), 3000) * 0.004
    left, right = _stereo(mix + noise, 0.4)
    return left, right


def track_horizon(spec: TrackSpec):
    """Cinématique : nappes amples, drone grave, toms et piano espacé."""
    beat, n = _grid(spec)
    progression = ["Am", "F", "C", "G", "Am", "F", "C", "Em"]
    music, perc, drone = np.zeros(n), np.zeros(n), np.zeros(n)
    big = tom(70, 1.4)
    for bar in range(spec.bars):
        chord = CHORDS[progression[bar % len(progression)]]
        bar_start = bar * 4 * beat
        _place(music, pad(chord, 4 * beat + 0.8, cutoff=1400, attack=1.2, release=1.4, detune_cents=12) * 0.42, _at(bar_start))
        _place(drone, bass(chord[0] - 24, 4 * beat, cutoff=180, shape="sine") * 0.35, _at(bar_start))
        melody = [chord[-1] + 12, chord[1] + 12, chord[0] + 24, chord[1] + 12]
        for i, note in enumerate(melody):
            _place(music, keys(note, beat * 2, decay=1.4) * 0.12, _at(bar_start + i * beat))
        if bar >= 2:
            _place(perc, big * 0.55, _at(bar_start))
            _place(perc, tom(110, 0.6) * 0.25, _at(bar_start + 2.5 * beat))
            _place(perc, tom(95, 0.6) * 0.28, _at(bar_start + 3 * beat))
    mix = reverb(music, 3.2, 0.45, damping=3500) + reverb(perc, 2.2, 0.3) + drone
    fade = np.ones(n)
    head = _at(beat * 2)
    fade[:head] = np.linspace(0.2, 1, head)
    return _stereo(mix * fade, 0.5)


def track_daylight(spec: TrackSpec):
    """Pop solaire : claps sur 2 et 4, basse sautillante, guitare-pluck."""
    beat, n = _grid(spec)
    progression = ["C", "G", "Am", "F"]
    drums, music, low = np.zeros(n), np.zeros(n), np.zeros(n)
    k, c, h, sh = kick(0.9), clap(12), hat(13), hat(14, True)
    for bar in range(spec.bars):
        chord = CHORDS[progression[bar % 4]]
        bar_start = bar * 4 * beat
        for b in range(4):
            t = bar_start + b * beat
            if b in (0, 2) or (b == 1 and bar % 2):
                _place(drums, k * 0.85, _at(t + (beat / 2 if b == 1 else 0)))
            if b in (1, 3):
                _place(drums, c * 0.5, _at(t))
            _place(drums, h * 0.15, _at(t + beat / 2))
            _place(drums, sh * 0.06, _at(t + beat * 0.75))
        pattern = (0, 0.75, 1.5, 2, 2.75, 3.5)
        for offset in pattern:
            for j, note in enumerate(chord):
                _place(music, pluck(note + 12, beat * 0.6, bright=5000, decay=0.18, wave_shape="square") * 0.07,
                       _at(bar_start + offset * beat + j * 0.008))
        for offset in (0, 1, 1.5, 2, 3, 3.5):
            note = chord[0] - 24 + (12 if offset in (1.5, 3.5) else 0)
            _place(low, bass(note, beat * 0.45, cutoff=600, decay=0.2) * 0.55, _at(bar_start + offset * beat))
    mix = drums + reverb(music, 1.1, 0.2) + low
    return _stereo(mix, 0.35)


def track_nocturne(spec: TrackSpec):
    """Ambiance minimale pour voix : nappe douce et pulsation légère, peu de médiums."""
    beat, n = _grid(spec)
    progression = ["Dm7", "Bb", "F", "C"]
    music, perc = np.zeros(n), np.zeros(n)
    soft = kick(0.4, 0.3, 55)
    h = hat(15)
    for bar in range(spec.bars):
        chord = CHORDS[progression[bar % 4]]
        bar_start = bar * 4 * beat
        _place(music, pad(chord, 4 * beat + 0.5, cutoff=900, attack=0.8, release=0.9) * 0.35, _at(bar_start))
        for b in range(4):
            if b in (0, 2):
                _place(perc, soft * 0.35, _at(bar_start + b * beat))
            _place(perc, h * 0.06, _at(bar_start + b * beat + beat / 2))
        for i in range(8):
            if (i + bar) % 3 == 0:
                _place(music, pluck(chord[i % len(chord)] + 24, beat, bright=2500, decay=0.4, wave_shape="tri") * 0.06,
                       _at(bar_start + i * beat / 2))
    return _stereo(reverb(music, 2.5, 0.35) + perc, 0.45)


TRACKS: tuple[tuple[TrackSpec, object], ...] = (
    (TrackSpec("pulse", "Pulse", "Électro rythmée", 124, 16, "Montage dynamique, cuts sur le beat."), track_pulse),
    (TrackSpec("overdrive", "Overdrive", "Énergie / sport", 140, 16, "Highlights intenses et action."), track_overdrive),
    (TrackSpec("sunday", "Sunday", "Lo-fi chill", 84, 12, "Vlog détendu, ambiance café."), track_sunday),
    (TrackSpec("horizon", "Horizon", "Cinématique", 70, 12, "Nappes amples, toms épiques."), track_horizon),
    (TrackSpec("daylight", "Daylight", "Pop solaire", 112, 16, "Story, voyage, souvenirs joyeux."), track_daylight),
    (TrackSpec("nocturne", "Nocturne", "Ambiance discrète", 90, 12, "Fond sonore sous la voix."), track_nocturne),
)


def render_track(key: str) -> tuple[TrackSpec, np.ndarray, np.ndarray]:
    for spec, builder in TRACKS:
        if spec.key == key:
            left, right = builder(spec)
            # Courts fondus anti-clic aux bords de boucle ; la longueur reste exactement N mesures.
            edge = _at(0.004)
            ramp = np.linspace(0, 1, edge)
            for channel in (left, right):
                channel[:edge] *= ramp
                channel[-edge:] *= ramp[::-1]
            peak = max(np.max(np.abs(left)), np.max(np.abs(right))) or 1.0
            gain = 10 ** (-1.5 / 20) / peak
            return spec, soft_clip(left * gain, 1.05), soft_clip(right * gain, 1.05)
    raise KeyError(f"Morceau inconnu : {key}")


# --- Effets sonores ---------------------------------------------------------------------------

def sfx_whoosh(seed: int = 21, length_s: float = 0.7, rising: bool = True) -> np.ndarray:
    n = _at(length_s)
    noise = np.random.default_rng(seed).standard_normal(n)
    swept = swept_filter(noise, 300 if rising else 6000, 6000 if rising else 300, mode="band")
    shape = np.sin(np.linspace(0, np.pi, n)) ** 2
    peak_at = 0.62 if rising else 0.38
    tilt = np.interp(np.linspace(0, 1, n), [0, peak_at, 1], [0.2, 1, 0.1])
    return normalize(swept * shape * tilt, -2)


def sfx_impact(seed: int = 22) -> np.ndarray:
    n = _at(1.6)
    t = np.arange(n) / SAMPLE_RATE
    sub = np.sin(_phase(38 + 70 * np.exp(-t / 0.08))) * exp_decay(n, 0.45)
    crack = lowpass(np.random.default_rng(seed).standard_normal(n), 2500) * exp_decay(n, 0.06)
    return normalize(reverb(soft_clip(sub * 1.2 + crack * 0.6, 1.8), 1.8, 0.3), -1)


def sfx_riser(seed: int = 23, length_s: float = 2.0) -> np.ndarray:
    n = _at(length_s)
    noise = swept_filter(np.random.default_rng(seed).standard_normal(n), 400, 9000, mode="band")
    t = np.arange(n) / SAMPLE_RATE
    tone = saw(220 * 2 ** (2 * t / length_s), n) * 0.15
    ramp = (np.arange(n) / n) ** 2
    cut = np.ones(n)
    cut[-_at(0.02):] = np.linspace(1, 0, _at(0.02))
    return normalize((noise + lowpass(tone, 4000)) * ramp * cut, -2)


def sfx_pop() -> np.ndarray:
    n = _at(0.12)
    t = np.arange(n) / SAMPLE_RATE
    return normalize(np.sin(_phase(900 * np.exp(-t / 0.02) + 250)) * exp_decay(n, 0.025), -3)


def sfx_click() -> np.ndarray:
    n = _at(0.05)
    noise = highpass(np.random.default_rng(24).standard_normal(n), 2500)
    return normalize(noise * exp_decay(n, 0.004) + np.sin(_phase(np.full(n, 3200))) * exp_decay(n, 0.006) * 0.5, -4)


def sfx_ding() -> np.ndarray:
    n = _at(1.8)
    tone = np.zeros(n)
    for ratio, amp, decay in ((1, 1, 0.9), (2.76, 0.5, 0.5), (5.4, 0.25, 0.25), (8.93, 0.12, 0.12)):
        tone += amp * np.sin(_phase(np.full(n, 1046.5 * ratio))) * exp_decay(n, decay)
    return normalize(tone * envelope(n, 0.002, 0.01, 1, 0.1), -4)


def sfx_shutter() -> np.ndarray:
    n = _at(0.25)
    noise = bandpass(np.random.default_rng(25).standard_normal(n), 1500, 9000)
    env = np.zeros(n)
    for offset, decay in ((0.0, 0.006), (0.09, 0.012)):
        start = _at(offset)
        env[start:] += exp_decay(n - start, decay)
    return normalize(noise * env, -3)


def sfx_glitch(seed: int = 26) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = _at(0.45)
    signal = np.zeros(n)
    position = 0
    while position < n:
        length = int(rng.integers(_at(0.01), _at(0.05)))
        freq = float(rng.choice([180, 360, 720, 1440, 2880]))
        chunk = square(freq, length) * float(rng.uniform(0.3, 1))
        if rng.random() < 0.4:
            chunk = rng.standard_normal(length) * 0.7
        signal[position:position + length] = chunk[: max(0, min(length, n - position))]
        position += length + int(rng.integers(0, _at(0.02)))
    crushed = np.round(signal * 6) / 6
    return normalize(lowpass(crushed, 7000), -4)


def sfx_swipe() -> np.ndarray:
    return sfx_whoosh(seed=27, length_s=0.32)


def sfx_boom() -> np.ndarray:
    n = _at(2.5)
    t = np.arange(n) / SAMPLE_RATE
    sub = np.sin(_phase(32 + 40 * np.exp(-t / 0.15))) * exp_decay(n, 0.8)
    return normalize(reverb(sub, 2.0, 0.2, damping=800), -1)


@dataclass(frozen=True)
class SfxSpec:
    key: str
    title: str
    category: str
    description: str


SFX: tuple[tuple[SfxSpec, object], ...] = (
    (SfxSpec("whoosh", "Whoosh", "transition", "Souffle montant pour les transitions."), sfx_whoosh),
    (SfxSpec("swipe", "Swipe", "transition", "Whoosh court pour les coupes rapides."), sfx_swipe),
    (SfxSpec("whoosh_out", "Whoosh sortant", "transition", "Souffle descendant."), lambda: sfx_whoosh(28, 0.8, False)),
    (SfxSpec("impact", "Impact", "accent", "Coup grave pour ouvrir un montage."), sfx_impact),
    (SfxSpec("boom", "Boom", "accent", "Sub-basse cinématique."), sfx_boom),
    (SfxSpec("riser", "Riser", "tension", "Montée de tension de 2 s."), sfx_riser),
    (SfxSpec("pop", "Pop", "interface", "Apparition d'un titre."), sfx_pop),
    (SfxSpec("click", "Clic", "interface", "Petit clic sec."), sfx_click),
    (SfxSpec("ding", "Ding", "interface", "Clochette claire."), sfx_ding),
    (SfxSpec("shutter", "Déclencheur", "photo", "Obturateur d'appareil photo."), sfx_shutter),
    (SfxSpec("glitch", "Glitch", "transition", "Parasites numériques."), sfx_glitch),
)


def render_sfx(key: str) -> tuple[SfxSpec, np.ndarray]:
    for spec, builder in SFX:
        if spec.key == key:
            return spec, builder()
    raise KeyError(f"Effet sonore inconnu : {key}")
