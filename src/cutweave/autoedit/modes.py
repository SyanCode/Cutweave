"""Recettes de montage automatique : chaque mode est un jeu de règles explicites."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EditMode:
    key: str
    title: str
    tagline: str
    glyph: str
    accent: str
    strategy: str                       # "beats" (plans calés sur le tempo) ou "speech" (activité sonore)
    default_duration: float | None      # secondes ; None = selon le contenu
    music: tuple[str, ...]              # morceaux intégrés par ordre de préférence
    looks: tuple[str, ...]
    caption_preset: str
    shot_beats: tuple[tuple[int, float], ...] = ((2, 1.0),)   # (nombre de temps, poids)
    min_shot: float = 0.4
    transitions: tuple[tuple[str, float, float], ...] = (("cut", 0.0, 1.0),)  # (type, durée s, poids)
    effects: tuple[tuple[str, float], ...] = (("none", 1.0),)
    speed: float = 1.0
    order: str = "chrono"               # chrono | hook | build
    selection: str = "spread"           # spread (répartition chronologique) | best (meilleurs moments)
    weights: tuple[float, float, float, float] = (0.4, 0.2, 0.4, 0.0)   # mouvement, son, netteté, calme
    original_audio: float = 0.0         # gain de l'audio des rushs dans le master
    music_volume: float = 0.85
    duck_level: float = 1.0             # niveau de la musique pendant la parole (1 = pas de ducking)
    letterbox: bool = False
    fade_in: float = 0.0
    fade_out: float = 0.6
    sharpening: float = 0.25
    sfx_intro: str | None = None
    sfx_transition: str | None = None
    sfx_flash: str | None = None


MODES: tuple[EditMode, ...] = (
    EditMode(
        "edit", "Edit", "Montage nerveux calé sur le beat : zooms punch, flashs et coupes au rythme.",
        "⚡", "#ff3d7f", "beats", 20.0, ("pulse", "overdrive", "daylight"), ("punchy", "teal_orange", "cool"),
        "Impact",
        shot_beats=((1, 2.0), (2, 4.0), (4, 1.2)), min_shot=0.35,
        transitions=(("cut", 0.0, 7.0), ("fadewhite", 0.1, 1.4), ("zoomin", 0.27, 0.8), ("slideleft", 0.23, 0.8),
                     ("slideup", 0.23, 0.5)),
        effects=(("none", 3.0), ("punch", 2.5), ("push_in", 2.0), ("pan_right", 0.7), ("pan_left", 0.7)),
        order="hook", selection="best", weights=(0.55, 0.15, 0.3, 0.0), music_volume=0.95, fade_out=0.4,
        sharpening=0.35, sfx_intro="impact", sfx_transition="swipe", sfx_flash="whoosh",
    ),
    EditMode(
        "vlog", "Vlog", "Garde ta voix, coupe les blancs, musique douce qui s'efface quand tu parles.",
        "🎙", "#ffb020", "speech", None, ("sunday", "daylight", "nocturne"), ("warm", "natural", "film"),
        "Vlog",
        transitions=(("fade", 0.3, 1.0),), effects=(("none", 1.0),),
        weights=(0.35, 0.35, 0.3, 0.0), original_audio=1.0, music_volume=0.3, duck_level=0.28, fade_in=0.3,
        fade_out=0.8, sharpening=0.25, sfx_transition="swipe",
    ),
    EditMode(
        "cinematic", "Cinématique", "Plans longs au ralenti, bandes noires, fondus et étalonnage film.",
        "🎬", "#00b3a4", "beats", 30.0, ("horizon", "nocturne"), ("teal_orange", "film", "mono"),
        "Cinematic",
        shot_beats=((4, 3.0), (8, 1.0), (2, 0.6)), min_shot=1.5,
        transitions=(("fade", 0.6, 2.0), ("fadeblack", 0.8, 1.2), ("cut", 0.0, 1.5), ("dissolve", 0.7, 0.8)),
        effects=(("push_in", 3.0), ("pull_out", 1.0), ("pan_right", 1.0), ("pan_left", 1.0)),
        speed=0.8, order="chrono", selection="spread", weights=(0.15, 0.0, 0.55, 0.3), music_volume=0.9,
        letterbox=True, fade_in=1.0, fade_out=1.4, sharpening=0.15, sfx_intro="boom", sfx_flash="whoosh_out",
    ),
    EditMode(
        "highlights", "Highlights", "Les moments les plus intenses (mouvement + son) enchaînés à fond.",
        "🔥", "#ff6a00", "beats", 30.0, ("overdrive", "pulse"), ("punchy", "cool", "teal_orange"),
        "Néon",
        shot_beats=((2, 3.0), (4, 2.0), (1, 1.0)), min_shot=0.4,
        transitions=(("cut", 0.0, 6.0), ("fadewhite", 0.1, 1.5), ("pixelize", 0.2, 0.6), ("zoomin", 0.25, 0.8)),
        effects=(("punch", 2.5), ("none", 2.0), ("push_in", 1.5)),
        order="build", selection="best", weights=(0.6, 0.4, 0.2, 0.0), original_audio=0.3, music_volume=0.9,
        fade_out=0.5, sharpening=0.35, sfx_intro="impact", sfx_transition="swipe", sfx_flash="impact",
    ),
    EditMode(
        "story", "Souvenirs", "Récap chronologique doux : fondus, glissés et tons chauds.",
        "📷", "#7c5cff", "beats", 30.0, ("daylight", "sunday"), ("warm", "vintage", "natural"),
        "Souvenir",
        shot_beats=((4, 3.0), (2, 1.5), (6, 0.6)), min_shot=1.0,
        transitions=(("fade", 0.4, 3.0), ("cut", 0.0, 2.5), ("slideleft", 0.35, 0.8), ("circleopen", 0.45, 0.5)),
        effects=(("push_in", 2.0), ("pan_right", 1.0), ("pan_left", 1.0), ("none", 1.0)),
        order="chrono", selection="spread", weights=(0.3, 0.1, 0.45, 0.15), music_volume=0.85, fade_in=0.5,
        fade_out=1.2, sharpening=0.2, sfx_intro="shutter", sfx_transition="swipe",
    ),
    EditMode(
        "talking", "Face cam", "Interview ou face caméra : silences retirés et zooms alternés à chaque coupe.",
        "🗣", "#2f80ed", "speech", None, ("nocturne", "sunday"), ("natural", "warm", "cool"),
        "Clean",
        transitions=(("cut", 0.0, 1.0),), effects=(("none", 1.0), ("punch", 1.0)),
        weights=(0.2, 0.6, 0.2, 0.0), original_audio=1.0, music_volume=0.14, duck_level=0.45, fade_in=0.0,
        fade_out=0.5, sharpening=0.3,
    ),
)

MODES_BY_KEY = {mode.key: mode for mode in MODES}


@dataclass(frozen=True)
class VariantProfile:
    letter: str
    name: str
    pacing: float          # >1 : plans plus longs ; <1 : plus courts
    rotation: int          # décalage dans les listes de musiques/looks
    order: str | None      # None = ordre du mode
    blurb: str


VARIANTS: tuple[VariantProfile, ...] = (
    VariantProfile("A", "Signature", 1.0, 0, None, "La recette du mode, équilibrée."),
    VariantProfile("B", "Énergique", 0.7, 1, "hook", "Plus court, plus rythmé, accroche dès la 1re seconde."),
    VariantProfile("C", "Posée", 1.4, 2, "chrono", "Plans plus longs et respiration, ordre chronologique."),
)
