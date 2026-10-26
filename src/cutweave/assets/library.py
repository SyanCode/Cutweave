"""Bibliothèque d'actifs intégrés : musiques, effets sonores, looks et presets.

Les fichiers audio sont synthétisés localement au premier besoin (aucun téléchargement) dans
``<config>/assets/<version>/`` puis réutilisés. La génération est déterministe.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from cutweave.assets import synth
from cutweave.core.settings import config_dir

LOGGER = logging.getLogger("cutweave.assets")
ASSET_VERSION = "v1"
_LOCK = threading.Lock()


@dataclass(frozen=True)
class MusicTrack:
    key: str
    title: str
    mood: str
    bpm: float
    bars: int
    description: str
    path: Path

    @property
    def beat_s(self) -> float:
        return 60.0 / self.bpm

    @property
    def duration_s(self) -> float:
        return self.bars * 4 * self.beat_s


@dataclass(frozen=True)
class SoundEffect:
    key: str
    title: str
    category: str
    description: str
    path: Path


def assets_root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else config_dir() / "assets" / ASSET_VERSION


def music_catalog(root: str | Path | None = None) -> tuple[MusicTrack, ...]:
    base = assets_root(root) / "music"
    return tuple(MusicTrack(spec.key, spec.title, spec.mood, spec.bpm, spec.bars, spec.description,
                            base / f"cutweave_{spec.key}.wav") for spec, _builder in synth.TRACKS)


def sfx_catalog(root: str | Path | None = None) -> tuple[SoundEffect, ...]:
    base = assets_root(root) / "sfx"
    return tuple(SoundEffect(spec.key, spec.title, spec.category, spec.description,
                             base / f"cutweave_{spec.key}.wav") for spec, _builder in synth.SFX)


def track_by_key(key: str, root: str | Path | None = None) -> MusicTrack:
    for track in music_catalog(root):
        if track.key == key:
            return track
    raise KeyError(f"Morceau intégré inconnu : {key}")


def sfx_by_key(key: str, root: str | Path | None = None) -> SoundEffect:
    for effect in sfx_catalog(root):
        if effect.key == key:
            return effect
    raise KeyError(f"Effet sonore intégré inconnu : {key}")


def ensure_track(key: str, root: str | Path | None = None) -> MusicTrack:
    track = track_by_key(key, root)
    with _LOCK:
        if not track.path.is_file():
            LOGGER.info("Synthèse du morceau intégré %s", track.title)
            _spec, left, right = synth.render_track(key)
            synth.write_wav(track.path, left, right)
    return track


def ensure_sfx(key: str, root: str | Path | None = None) -> SoundEffect:
    effect = sfx_by_key(key, root)
    with _LOCK:
        if not effect.path.is_file():
            _spec, mono = synth.render_sfx(key)
            synth.write_wav(effect.path, mono)
    return effect


def ensure_all(root: str | Path | None = None, *, progress=None, cancel_event=None) -> Path:
    base = assets_root(root)
    items = [("music", track.key) for track in music_catalog(root)] + [("sfx", fx.key) for fx in sfx_catalog(root)]
    for index, (kind, key) in enumerate(items):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Génération des actifs annulée.")
        if progress:
            progress(index / len(items))
        (ensure_track if kind == "music" else ensure_sfx)(key, root)
    manifest = {
        "version": ASSET_VERSION,
        "music": [{"key": t.key, "title": t.title, "bpm": t.bpm, "bars": t.bars} for t in music_catalog(root)],
        "sfx": [{"key": s.key, "title": s.title, "category": s.category} for s in sfx_catalog(root)],
    }
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if progress:
        progress(1.0)
    return base


# --- Looks (étalonnage) -------------------------------------------------------------------------

@dataclass(frozen=True)
class Look:
    key: str
    title: str
    description: str
    filters: str
    swatch: tuple[str, str]


LOOKS: tuple[Look, ...] = (
    Look("natural", "Naturel", "Léger contraste, couleurs fidèles.", "eq=contrast=1.04:saturation=1.06",
         ("#8a9bb0", "#d8c7a8")),
    Look("punchy", "Punchy", "Contraste et saturation marqués, idéal réseaux.",
         "eq=contrast=1.14:saturation=1.32:gamma=0.97", ("#ff3d7f", "#ffb800")),
    Look("warm", "Chaud", "Tons dorés, peau flatteuse.",
         "colorbalance=rs=0.06:gs=0.01:bs=-0.06:rm=0.04:bm=-0.04,eq=saturation=1.1:contrast=1.04",
         ("#ff9a3c", "#ffd59e")),
    Look("cool", "Froid", "Bleus profonds, ambiance urbaine.",
         "colorbalance=rs=-0.05:bs=0.07:rh=-0.03:bh=0.04,eq=saturation=1.04:contrast=1.06", ("#2f80ed", "#9be7ff")),
    Look("teal_orange", "Teal & Orange", "Le look blockbuster.",
         "colorbalance=rs=-0.08:gs=-0.02:bs=0.10:rh=0.10:gh=0.02:bh=-0.08,eq=contrast=1.1:saturation=1.18",
         ("#008c8c", "#ff8c42")),
    Look("vintage", "Vintage", "Pellicule délavée et grain.",
         "curves=preset=vintage,eq=saturation=0.88,noise=alls=7:allf=t", ("#c2a878", "#6f8f72")),
    Look("film", "Film", "Grain doux, noirs relevés, vignettage.",
         "curves=all='0/0.04 0.5/0.5 1/0.96',eq=saturation=0.94:contrast=1.05,noise=alls=5:allf=t,vignette=PI/6",
         ("#3a3a3a", "#d9cbb0")),
    Look("mono", "Noir & blanc", "Monochrome contrasté.", "hue=s=0,eq=contrast=1.2:brightness=0.01",
         ("#111111", "#eeeeee")),
)

LOOKS_BY_KEY = {look.key: look for look in LOOKS}
