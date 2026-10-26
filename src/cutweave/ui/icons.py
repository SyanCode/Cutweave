"""Icônes vectorielles (SVG tracé, style trait fin) rendues à la demande, nettes sur écrans HiDPI.

Pas de police d'icônes ni d'emoji : l'apparence est identique sur toutes les machines.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_P = {
    "sparkles": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>'
                '<path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "settings": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "upload": '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/>',
    "download": '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>',
    "play": '<path d="M7 4.5l13 7.5-13 7.5z" fill="{c}"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1" fill="{c}"/><rect x="14" y="4" width="4" height="16" rx="1" fill="{c}"/>',
    "refresh": '<path d="M21 12a9 9 0 11-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    "trash": '<path d="M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6M10 11v6M14 11v6"/>',
    "arrow-left": '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    "film": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 12h18M3 16h4M17 8h4M17 16h4"/>',
    "zap": '<path d="M13 2L3 14h9l-1 8 10-12h-9z"/>',
    "mic": '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M19 10a7 7 0 01-14 0M12 17v5"/>',
    "flame": '<path d="M12 22c4 0 7-2.8 7-7 0-3-2-5-3-7-.5 2-2 3-3 3 0-3-1-6-4-8 0 4-4 6-4 12 0 4.2 3 7 7 7z"/>',
    "camera": '<path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z"/><circle cx="12" cy="13" r="4"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0116 0"/>',
    "layers": '<path d="M12 2l10 5-10 5L2 7z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>',
    "scissors": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "x": '<path d="M18 6L6 18M6 6l12 12"/>',
    "menu": '<path d="M3 6h18M3 12h18M3 18h18"/>',
    "crop": '<path d="M6 2v14a2 2 0 002 2h14"/><path d="M18 22V8a2 2 0 00-2-2H2"/>',
    "type": '<path d="M4 7V4h16v3M9 20h6M12 4v16"/>',
    "mask": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 000 18z" fill="{c}"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
    "rules": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    "activity": '<path d="M3 12h4l3-9 4 18 3-9h4"/>',
    "book": '<path d="M4 19.5A2.5 2.5 0 016.5 17H20V3H6.5A2.5 2.5 0 004 5.5z"/><path d="M4 19.5V22h16"/>',
    "palette": '<circle cx="12" cy="12" r="9"/><circle cx="8" cy="10" r="1" fill="{c}"/><circle cx="12" cy="7.5" r="1" fill="{c}"/>'
               '<circle cx="16" cy="10" r="1" fill="{c}"/><path d="M12 21a2 2 0 010-4h1a2 2 0 000-4"/>',
    "wand": '<path d="M15 4V2M15 16v-2M8 9H6M24 9h-2M17.8 6.2L19 5M17.8 11.8L19 13M3 21l9-9M12.2 6.2L11 5"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    "eye": '<path d="M2 12s4-8 10-8 10 8 10 8-4 8-10 8S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "save": '<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
    "chevron-left": '<path d="M15 18l-6-6 6-6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-up": '<path d="M18 15l-6-6-6 6"/>',
    "chevron-right": '<path d="M9 18l6-6-6-6"/>',
    "panel": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
    "volume": '<path d="M11 5L6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 010 7M19 5a10 10 0 010 14"/>',
    "alert": '<path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    "link": '<path d="M10 13a5 5 0 007.5.5l3-3a5 5 0 00-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 00-7.5-.5l-3 3a5 5 0 007 7l1.7-1.7"/>',
}


def available() -> tuple[str, ...]:
    return tuple(_P)


def svg_markup(name: str, color: str) -> str:
    body = _P[name].replace("{c}", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#eceef3", size: int = 18, ratio: float = 2.0) -> QPixmap:
    side = max(1, int(round(size * ratio)))
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg_markup(name, color).encode("utf-8")))
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(ratio)
    return result


@lru_cache(maxsize=512)
def icon(name: str, color: str = "#eceef3", size: int = 18, *, active: str = "#ffffff",
         disabled: str = "#5d6375") -> QIcon:
    """Icône avec états normal / survolé-sélectionné / désactivé."""
    result = QIcon()
    result.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal)
    result.addPixmap(pixmap(name, active, size), QIcon.Mode.Active)
    result.addPixmap(pixmap(name, active, size), QIcon.Mode.Selected)
    result.addPixmap(pixmap(name, disabled, size), QIcon.Mode.Disabled)
    return result


def icon_size(size: int = 18) -> QSize:
    return QSize(size, size)
