"""Génère ``cutweave.ico`` (icône Windows multi-tailles, entrées PNG) depuis ``logo.svg``.

Usage : ``python scripts/make_icon.py`` (nécessite PySide6, hors écran).
"""

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from cutweave.ui.branding import logo_pixmap  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    data = QByteArray()          # doit survivre au QBuffer qui écrit dedans
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    logo_pixmap(size, 1.0).save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (doit rester vivante pendant le rendu)
    images = [(size, png_bytes(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    target = Path(__file__).resolve().parents[1] / "src" / "cutweave" / "ui" / "resources" / "cutweave.ico"
    target.write_bytes(header + entries + payload)
    print(f"Icône écrite : {target} ({target.stat().st_size} octets)")
    logo_pixmap.cache_clear()   # libère les pixmaps avant la fin de l'application Qt


if __name__ == "__main__":
    main()
