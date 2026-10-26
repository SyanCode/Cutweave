"""Fonctions de haut niveau des sous-titres : alignement d'un script sur des rushs ou sur un média du projet."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cutweave.autoedit.analysis import RushAnalysis, analyze_rush
from cutweave.autoedit.planner import _chronological
from cutweave.stages.composition.captions import CaptionCue, CaptionParseError
from cutweave.subtitles.align import AlignmentResult, align_script, detect_nuclei
from cutweave.subtitles.cues import GROUPINGS, cues_for_media
from cutweave.subtitles.script import ScriptWord, parse_script

SUBTITLE_PRESETS = {  # (style de montage, regroupement) -> preset de captions
    ("edit", "phrase"): "Dynamic", ("highlights", "phrase"): "Dynamic", ("vlog", "phrase"): "Vlog",
    ("talking", "phrase"): "Clean", ("cinematic", "phrase"): "Cinematic", ("story", "phrase"): "Souvenir",
    ("cinematic", "words"): "Cinematic",
}
MIN_QUALITY = 0.45


class SubtitleError(ValueError):
    """Script absent, vide ou impossible à caler sur la voix."""


def subtitle_preset(mode_key: str, grouping: str, mode_preset: str) -> str:
    return SUBTITLE_PRESETS.get((mode_key, grouping), mode_preset)


@dataclass(frozen=True)
class SubtitleAlignment:
    result: AlignmentResult
    words: tuple[ScriptWord, ...]
    order: tuple[int, ...]              # ordre chronologique des rushs utilisé pour suivre le script


def check_inputs(script: str, grouping: str) -> list[ScriptWord]:
    if grouping not in GROUPINGS:
        raise SubtitleError(f"Regroupement inconnu : {grouping}")
    if not script.strip():
        raise SubtitleError("Sans script, CUTWEAVE ne peut pas deviner les paroles (aucune reconnaissance vocale, "
                            "aucune IA) : colle ou importe le texte prononcé.")
    words = parse_script(script)
    if not words:
        raise SubtitleError("Le script ne contient aucun mot exploitable.")
    return words


def align_to_rushes(script: str, analyses: list[RushAnalysis]) -> SubtitleAlignment:
    """Cale le script sur la voix des rushs, lus dans l'ordre chronologique (date de modification puis nom)."""
    words = check_inputs(script, "phrase")
    voiced = [index for index in _chronological(analyses) if analyses[index].has_audio and analyses[index].syllable_env]
    if not voiced:
        raise SubtitleError("Aucun rush ne contient de voix exploitable pour caler le script.")
    result = align_script(words, [analyses[index] for index in voiced])
    if not result.words:
        raise SubtitleError("Le script n'a pas pu être calé : aucune syllabe de voix n'a été détectée.")
    if result.quality < MIN_QUALITY:
        raise SubtitleError(
            f"Le script ne correspond pas à la voix : {result.syllables_script} syllabes écrites contre "
            f"{result.syllables_audio} entendues ({result.quality:.0%} de fiabilité). Vérifie que le texte est celui "
            "qui est prononcé, dans l'ordre des rushs.")
    return SubtitleAlignment(result, tuple(words), tuple(voiced))


def cues_for_media_file(path: str | Path, script: str, grouping: str, *, cache_dir=None,
                        cancel_event=None) -> tuple[list[CaptionCue], AlignmentResult]:
    """Sous-titres d'un fichier utilisé tel quel dans l'éditeur (instants source = instants de la timeline)."""
    words = check_inputs(script, grouping)
    analysis = analyze_rush(path, cache_dir=cache_dir, cancel_event=cancel_event)
    if not analysis.has_audio or not analysis.syllable_env:
        raise SubtitleError("Ce média n'a pas de piste audio exploitable.")
    result = align_script(words, [analysis])
    if not result.words:
        raise SubtitleError("Aucune syllabe de voix n'a été détectée dans ce média.")
    if result.quality < MIN_QUALITY:
        raise SubtitleError(
            f"Le script ne correspond pas à la voix de ce média : {result.syllables_script} syllabes écrites contre "
            f"{result.syllables_audio} entendues ({result.quality:.0%} de fiabilité).")
    return cues_for_media(result.words, grouping), result


def speech_skeleton(path: str | Path, *, cache_dir=None, cancel_event=None, placeholder: str = "…") -> list[CaptionCue]:
    """Plages de parole détectées, avec un texte provisoire à remplacer : les temps sont prêts, il ne reste qu'à écrire."""
    analysis = analyze_rush(path, cache_dir=cache_dir, cancel_event=cancel_event)
    cues = [CaptionCue(int(round(start * 1000)), int(round(end * 1000)), placeholder)
            for start, end in analysis.activity if end - start >= 0.4]
    if not cues:
        raise SubtitleError("Aucune parole détectée dans ce média.")
    return cues


def syllable_count(analysis: RushAnalysis) -> int:
    return len(detect_nuclei(analysis))


__all__ = ["SubtitleAlignment", "SubtitleError", "align_to_rushes", "check_inputs", "cues_for_media_file",
           "speech_skeleton", "subtitle_preset", "CaptionParseError"]
