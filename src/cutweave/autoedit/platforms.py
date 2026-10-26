"""Destinations de publication : format, qualité, durée conseillée et zone de sécurité des sous-titres.

Les valeurs reflètent les usages courants des plateformes (format vertical, durées maximales des formats courts) ;
elles servent de réglages de départ et d'avertissements, jamais de contraintes bloquantes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    key: str
    title: str
    aspect: str
    quality: int
    max_seconds: float | None      # durée au-delà de laquelle un avertissement est affiché
    default_seconds: float | None  # durée visée quand le réglage « Durée » est sur Auto
    caption_margin: float          # marge basse (en % de la hauteur) qui évite l'interface de l'application
    note: str


PLATFORMS: tuple[Platform, ...] = (
    Platform("free", "Libre", "9:16", 1080, None, None, 0.0, "Aucune contrainte : tous les réglages sont à toi."),
    Platform("tiktok", "TikTok", "9:16", 1080, 60.0, 30.0, 18.0,
             "Vertical 1080 × 1920. Les sous-titres restent au-dessus de la zone de légende de l'application."),
    Platform("reels", "Instagram Reels", "9:16", 1080, 90.0, 30.0, 20.0,
             "Vertical 1080 × 1920, jusqu'à 90 s. Marge basse pour la légende et les boutons."),
    Platform("shorts", "YouTube Shorts", "9:16", 1080, 60.0, 45.0, 16.0,
             "Vertical 1080 × 1920, 60 s au plus pour rester dans le format Shorts."),
    Platform("youtube", "YouTube", "16:9", 1080, None, None, 8.0,
             "Horizontal 1920 × 1080 ; passe en 4K pour une meilleure qualité."),
    Platform("instagram", "Instagram carré", "1:1", 1080, 60.0, 30.0, 10.0, "Carré 1080 × 1080 pour le fil."),
)
PLATFORMS_BY_KEY = {platform.key: platform for platform in PLATFORMS}


def platform_by_key(key: str | None) -> Platform:
    return PLATFORMS_BY_KEY.get(key or "free", PLATFORMS_BY_KEY["free"])


def duration_warning(platform: Platform, seconds: float) -> str:
    if platform.max_seconds is not None and seconds > platform.max_seconds + 0.5:
        return f"{seconds:.0f} s : dépasse les {platform.max_seconds:.0f} s conseillées pour {platform.title}"
    return ""
