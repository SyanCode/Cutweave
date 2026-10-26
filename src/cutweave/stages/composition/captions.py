"""Modèle commun et import déterministe SRT, WebVTT et ASS/SSA."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path


class CaptionParseError(ValueError):
    """Le fichier de captions contient une structure ou un timestamp invalide."""


@dataclass(frozen=True, order=True)
class CaptionCue:
    start_ms: int
    end_ms: int
    text: str
    placement: str = ""      # "" : position du preset (en bas) ; "top" : en haut (titres au-dessus de sous-titres)

    def validate(self) -> None:
        if self.start_ms < 0:
            raise CaptionParseError("Le début d'un segment ne peut pas être négatif.")
        if self.end_ms <= self.start_ms:
            raise CaptionParseError("La fin d'un segment doit être postérieure à son début.")
        if not self.text.strip():
            raise CaptionParseError("Un segment ne peut pas être vide.")
        if self.placement not in {"", "top"}:
            raise CaptionParseError("Le placement d'un segment doit être vide ou « top ».")


@dataclass(frozen=True)
class CaptionDocument:
    cues: tuple[CaptionCue, ...]
    source_format: str

    def __post_init__(self):
        for cue in self.cues:
            cue.validate()
        if tuple(sorted(self.cues, key=lambda cue: (cue.start_ms, cue.end_ms))) != self.cues:
            raise CaptionParseError("Les segments doivent être classés par heure de début.")


def split_cue(cue: CaptionCue, at_ms: int) -> tuple[CaptionCue, CaptionCue]:
    cue.validate()
    if not cue.start_ms < at_ms < cue.end_ms:
        raise CaptionParseError("Le point de découpe doit se trouver à l'intérieur du segment.")
    text = cue.text.strip()
    whitespace = list(re.finditer(r"\s+", text))
    if whitespace:
        middle = len(text) / 2
        split = min(whitespace, key=lambda match: abs((match.start() + match.end()) / 2 - middle))
        boundary = (split.start() + split.end()) // 2
        first, second = text[:boundary].strip(), text[boundary:].strip()
    else:
        boundary = max(1, len(text) // 2)
        first, second = text[:boundary].strip(), text[boundary:].strip()
    if not first or not second:
        raise CaptionParseError("Le texte est trop court pour être scindé.")
    return CaptionCue(cue.start_ms, at_ms, first, cue.placement), CaptionCue(at_ms, cue.end_ms, second, cue.placement)


def merge_cues(cues: tuple[CaptionCue, ...] | list[CaptionCue]) -> CaptionCue:
    if len(cues) != 2:
        raise CaptionParseError("Sélectionnez exactement deux segments à fusionner.")
    first, second = sorted(cues, key=lambda cue: cue.start_ms)
    first.validate()
    second.validate()
    return CaptionCue(first.start_ms, max(first.end_ms, second.end_ms), f"{first.text}\n{second.text}", first.placement)


_SRT_TIME = re.compile(
    r"^(?P<h>\d{2,}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})$"
)
_VTT_TIME = re.compile(
    r"^(?:(?P<h>\d{1,}):)?(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{1,3})$"
)
_ASS_TIME = re.compile(
    r"^(?P<h>\d{1,}):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<cs>\d{1,2})$"
)
_VTT_CUE = re.compile(r"^\s*(\S+)\s+-->\s+(\S+)(?:\s+.*)?$")


def _fraction_ms(raw: str) -> int:
    return int(raw.ljust(3, "0")[:3])


def _to_ms(hours: str | None, minutes: str, seconds: str, fraction: str) -> int:
    h, m, s = int(hours or 0), int(minutes), int(seconds)
    if m > 59 or s > 59:
        raise CaptionParseError("Un timestamp contient des minutes ou secondes hors limites.")
    return ((h * 60 + m) * 60 + s) * 1000 + _fraction_ms(fraction)


def parse_timestamp(value: str, *, format: str) -> int:
    value = value.strip()
    if format == "srt":
        match = _SRT_TIME.fullmatch(value)
        if match:
            return _to_ms(match["h"], match["m"], match["s"], match["ms"])
    elif format == "vtt":
        match = _VTT_TIME.fullmatch(value)
        if match:
            return _to_ms(match["h"], match["m"], match["s"], match["ms"])
    elif format == "ass":
        match = _ASS_TIME.fullmatch(value)
        if match:
            centiseconds = int(match["cs"].ljust(2, "0")[:2])
            return _to_ms(match["h"], match["m"], match["s"], str(centiseconds * 10))
    raise CaptionParseError(f"Timestamp invalide ({format}) : {value!r}")


def _make_cue(start: str, end: str, lines: list[str], *, format: str, line_number: int) -> CaptionCue:
    try:
        cue = CaptionCue(parse_timestamp(start, format=format), parse_timestamp(end, format=format), "\n".join(lines).strip())
        cue.validate()
        return cue
    except CaptionParseError as exc:
        raise CaptionParseError(f"Segment autour de la ligne {line_number} : {exc}") from exc


def parse_srt(text: str) -> CaptionDocument:
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    cues: list[CaptionCue] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        cue_line = i + 1
        if "-->" in lines[i]:
            timing = lines[i]
        else:
            i += 1
            if i >= len(lines):
                raise CaptionParseError(f"Timestamp manquant après la ligne {cue_line}.")
            timing = lines[i]
        match = re.fullmatch(r"\s*(\S+)\s+-->\s+(\S+)\s*", timing)
        if not match:
            raise CaptionParseError(f"Ligne de synchronisation SRT invalide à la ligne {i + 1}.")
        start, end = match.groups()
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip():
            body.append(lines[i])
            i += 1
        cues.append(_make_cue(start, end, body, format="srt", line_number=cue_line))
    return CaptionDocument(tuple(cues), "srt")


def parse_vtt(text: str) -> CaptionDocument:
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or not lines[0].lstrip().startswith("WEBVTT"):
        raise CaptionParseError("En-tête WebVTT manquant.")
    cues: list[CaptionCue] = []
    i = 1
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        if lines[i] in {"STYLE", "REGION"} or lines[i] == "NOTE" or lines[i].startswith("NOTE "):
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            continue
        cue_line = i + 1
        timing = lines[i]
        if "-->" not in timing and i + 1 < len(lines):
            i += 1  # optional cue identifier
            timing = lines[i]
        match = _VTT_CUE.fullmatch(timing)
        if not match:
            raise CaptionParseError(f"Ligne de synchronisation WebVTT invalide à la ligne {i + 1}.")
        start, end = match.groups()
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip():
            body.append(lines[i])
            i += 1
        cues.append(_make_cue(start, end, body, format="vtt", line_number=cue_line))
    return CaptionDocument(tuple(cues), "vtt")


def parse_ass(text: str) -> CaptionDocument:
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    in_events = False
    fields: list[str] | None = None
    cues: list[CaptionCue] = []
    for line_number, line in enumerate(normalized.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_events = stripped.casefold() == "[events]"
            continue
        if not in_events or not stripped:
            continue
        if stripped.casefold().startswith("format:"):
            fields = [part.strip().casefold() for part in stripped.split(":", 1)[1].split(",")]
            continue
        if not stripped.casefold().startswith("dialogue:"):
            continue
        if fields is None or "start" not in fields or "end" not in fields or "text" not in fields:
            raise CaptionParseError(f"Ligne Format ASS invalide avant la ligne {line_number}.")
        parts = stripped.split(":", 1)[1].lstrip().split(",", len(fields) - 1)
        if len(parts) != len(fields):
            raise CaptionParseError(f"Entrée Dialogue ASS invalide à la ligne {line_number}.")
        values = dict(zip(fields, parts))
        body = re.sub(r"\{[^}]*\}", "", values["text"])
        body = body.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
        cues.append(_make_cue(values["start"], values["end"], [body], format="ass", line_number=line_number))
    if not in_events and not cues:
        raise CaptionParseError("Section [Events] ASS manquante.")
    return CaptionDocument(tuple(cues), "ass")


def parse_caption_text(text: str, *, format: str) -> CaptionDocument:
    normalized_format = format.casefold().lstrip(".")
    if normalized_format == "ssa":
        normalized_format = "ass"
    parsers = {"srt": parse_srt, "vtt": parse_vtt, "ass": parse_ass}
    try:
        parser = parsers[normalized_format]
    except KeyError as exc:
        raise CaptionParseError(f"Format de captions non pris en charge : {format}") from exc
    return parser(text)


def load_caption_file(path: str | Path) -> CaptionDocument:
    caption_path = Path(path)
    try:
        raw = caption_path.read_bytes()
    except OSError as exc:
        raise CaptionParseError(f"Impossible de lire {caption_path}: {exc}") from exc
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
    return parse_caption_text(text, format=caption_path.suffix)


def sanitize_caption_markup(text: str) -> str:
    """Convertit les retours à la ligne et les échappements usuels vers ASS."""
    value = unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\\", "\\\\")
    value = value.replace("{", r"\{").replace("}", r"\}")
    value = re.sub(r"<br\s*/?>", lambda _: r"\N", value, flags=re.IGNORECASE)
    for tag, code in (("b", "b"), ("i", "i"), ("u", "u")):
        value = re.sub(rf"<{tag}\s*>", rf"{{\\{code}1}}", value, flags=re.IGNORECASE)
        value = re.sub(rf"</{tag}\s*>", rf"{{\\{code}0}}", value, flags=re.IGNORECASE)
    value = re.sub(r"</?[^>]+>", "", value)
    value = value.replace("\n", r"\N")
    return value
