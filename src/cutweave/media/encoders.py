"""Choix de l'encodeur H.264 et de ses réglages selon l'usage (aperçu rapide, assemblage, export final).

Les encodeurs matériels (NVENC, Quick Sync, AMF) sont de loin les plus rapides ; ils ne sont retenus
qu'après un encodage d'essai réussi, car un build FFmpeg peut les contenir sans que la machine ait le
matériel correspondant. Les builds LGPL n'ont pas libx264 : sans carte compatible, l'encodeur logiciel
libopenh264 est utilisé.
"""

from __future__ import annotations

import logging
import subprocess
import threading

from cutweave.core.proc import run_hidden

LOGGER = logging.getLogger("cutweave.encoders")

HARDWARE = ("h264_nvenc", "h264_qsv", "h264_amf")
# Ordre de préférence : « fast » privilégie la vitesse, « final » la qualité par octet.
ORDERS = {
    "fast": (*HARDWARE, "libx264", "libopenh264", "h264_mf", "mpeg4"),
    "final": ("libx264", *HARDWARE, "libopenh264", "h264_mf", "mpeg4"),
}
DRAFT_PIXELS = 600_000       # au plus : aperçu rapide (540p)
TINY_PIXELS = 250_000        # en dessous : vignettes et tests, déjà instantanés, réglages « qualité »
_PROBED: dict[tuple[str, str], bool] = {}
_LOCK = threading.Lock()


def tier_for(width: int, height: int, *, final: bool = False) -> str:
    """``draft`` pour les petites images (aperçus), ``final`` pour l'export, ``good`` sinon."""
    pixels = width * height
    if TINY_PIXELS < pixels <= DRAFT_PIXELS:
        return "draft"
    return "final" if final else "good"


def _bitrate(width: int, height: int, tier: str, fps: int) -> str:
    bits_per_pixel = {"draft": 0.10, "good": 0.16, "final": 0.2}[tier]
    rate = min(40_000_000, max(1_500_000, int(width * height * fps * bits_per_pixel)))
    return str(rate)


def encoder_args(encoder: str, tier: str, width: int, height: int, fps: int = 30) -> list[str]:
    """Options de sortie vidéo (codec exclu), pixel format compris."""
    pix = ["-pix_fmt", "yuv420p"]
    if encoder == "libx264":
        preset, crf = {"draft": ("veryfast", "22"), "good": ("veryfast", "18"), "final": ("medium", "20")}[tier]
        return ["-preset", preset, "-crf", crf, *pix]
    if encoder == "h264_nvenc":
        cq = {"draft": "27", "good": "22", "final": "20"}[tier]
        return ["-preset", "p4", "-rc", "vbr", "-cq", cq, "-b:v", "0", *pix]
    if encoder == "h264_qsv":
        quality = {"draft": "26", "good": "21", "final": "19"}[tier]
        return ["-preset", "veryfast" if tier == "draft" else "medium", "-global_quality", quality,
                "-pix_fmt", "nv12"]
    if encoder == "h264_amf":
        qp = {"draft": 27, "good": 22, "final": 20}[tier]
        return ["-quality", "speed" if tier == "draft" else "balanced", "-rc", "cqp",
                "-qp_i", str(qp), "-qp_p", str(qp + 2), *pix]
    if encoder == "mpeg4":
        return ["-q:v", "3" if tier == "draft" else "2", *pix]
    return ["-b:v", _bitrate(width, height, tier, fps), *pix]  # libopenh264, h264_mf


def probe_encoder(binary: str, encoder: str) -> bool:
    """Encodage d'essai de quelques images : vrai si l'encodeur fonctionne sur cette machine."""
    key = (binary, encoder)
    with _LOCK:
        if key in _PROBED:
            return _PROBED[key]
    command = [binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
               "testsrc2=size=1280x720:rate=30:duration=0.4", "-c:v", encoder,
               *encoder_args(encoder, "good", 1280, 720), "-frames:v", "8", "-f", "null", "-"]
    try:
        ok = run_hidden(command, capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    LOGGER.info("Encodeur %s : %s", encoder, "utilisable" if ok else "indisponible sur cette machine")
    with _LOCK:
        _PROBED[key] = ok
    return ok


def mark_unusable(binary: str, encoder: str) -> None:
    """Un encodeur matériel qui échoue en cours de rendu (limite de sessions, pilote) est écarté."""
    with _LOCK:
        _PROBED[(binary, encoder)] = False


def choose_encoder(binary: str, available: set[str], purpose: str = "final") -> str | None:
    """Premier encodeur de la liste d'usage présent dans ``available`` (matériel : après essai réussi)."""
    for name in ORDERS[purpose]:
        if name not in available:
            continue
        if name in HARDWARE and not probe_encoder(binary, name):
            continue
        return name
    return None
