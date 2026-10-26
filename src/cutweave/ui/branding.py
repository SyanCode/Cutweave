"""Identité visuelle : logo vectoriel (fichier SVG du dépôt) et icône d'application multi-tailles."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap, Qt
from PySide6.QtSvg import QSvgRenderer

LOGO_PATH = Path(__file__).with_name("resources") / "logo.svg"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def logo_svg() -> bytes:
    return LOGO_PATH.read_bytes()


@lru_cache(maxsize=16)
def logo_pixmap(size: int = 64, ratio: float = 2.0) -> QPixmap:
    side = max(1, int(round(size * ratio)))
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(logo_svg()))
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(logo_pixmap(size, 1.0))
    return icon
