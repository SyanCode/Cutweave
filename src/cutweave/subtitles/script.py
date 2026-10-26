"""Analyse d'un script : phrases, mots, pauses attendues et nombre de syllabes (règles, sans IA).

Le comptage de syllabes suit la prononciation française courante : groupes vocaliques, ``e`` muet final
ignoré, ``qu``/``gu`` devant e, i, y simplifiés, voyelles accentuées qui ouvrent une nouvelle syllabe, nombres
écrits en toutes lettres. Il sert à aligner le texte sur les syllabes détectées dans le son ; une erreur d'une
syllabe ici est absorbée par l'alignement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cutweave.stages.composition.captions import CaptionParseError, load_caption_file

VOWELS = "aeiouyàâäéèêëîïôöùûüœæ"
SPLITTING = "éèêëïü"             # une voyelle accentuée suivie d'une autre voyelle forme deux syllabes (vidéo, créer)
ELIDED = {"l", "d", "j", "n", "s", "c", "m", "t", "qu", "jusqu", "lorsqu", "puisqu", "quoiqu", "presqu"}
_UNITS = ("zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze",
          "treize", "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf")
_TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}


def french_number(value: int) -> str:
    """Nombre entier (0 à 999 999 999) en toutes lettres."""
    if value < 0:
        return "moins " + french_number(-value)
    if value < 20:
        return _UNITS[value]
    if value < 70:
        tens, unit = divmod(value, 10)
        return _TENS[tens] + ("" if unit == 0 else (" et un" if unit == 1 else "-" + _UNITS[unit]))
    if value < 80:
        return "soixante-" + _UNITS[value - 60] if value != 71 else "soixante et onze"
    if value < 100:
        return "quatre-vingt" + ("s" if value == 80 else "-" + _UNITS[value - 80])
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        head = "cent" if hundreds == 1 else _UNITS[hundreds] + " cent"
        return head + (" " + french_number(rest) if rest else "")
    if value < 1_000_000:
        thousands, rest = divmod(value, 1000)
        head = "mille" if thousands == 1 else french_number(thousands) + " mille"
        return head + (" " + french_number(rest) if rest else "")
    if value < 1_000_000_000:
        millions, rest = divmod(value, 1_000_000)
        head = "un million" if millions == 1 else french_number(millions) + " millions"
        return head + (" " + french_number(rest) if rest else "")
    return " ".join(_UNITS[int(digit)] for digit in str(value))


def _letters_only(text: str) -> str:
    return re.sub(r"[^a-zà-ÿœæ]", "", text.lower())


def _count_piece(piece: str) -> int:
    """Syllabes d'un mot simple (lettres seulement, minuscules)."""
    word = piece
    if not word:
        return 0
    word = re.sub(r"qu(?=[aeiouyéèêàâ])", "k", word)           # qu + voyelle : le u ne compte pas
    word = re.sub(r"gu(?=[eiyéèê])", "g", word)
    word = re.sub(r"(?<=[aeiouéèêàâîôû])y(?=[aeiouéèêàâîôû])", "j", word)   # croyable : y entre voyelles = consonne
    if not any(char in VOWELS for char in word):
        return 0
    # Groupes de voyelles ; une voyelle accentuée « ouvrante » coupe le groupe.
    groups = 0
    previous_vowel = False
    previous_char = ""
    for char in word:
        is_vowel = char in VOWELS
        if is_vowel and not (previous_vowel and previous_char not in SPLITTING):
            groups += 1
        elif is_vowel and previous_vowel and previous_char in SPLITTING:
            groups += 1
        previous_vowel, previous_char = is_vowel, char
    # e muet final (parle, monde, tables) : ignoré sauf dans un mot d'une seule syllabe (le, ce, de, que).
    if groups > 1 and (re.search(r"[^aeiouyàâäéèêëîïôöùûüœæ]es?$", word) or re.search(r"ées?$", word)):
        groups -= 1
    return max(1, groups)


def _split_pieces(token: str) -> list[str]:
    pieces: list[str] = []
    for part in re.split(r"['’\-]", token):
        if part:
            pieces.append(part)
    return pieces


def count_syllables(token: str) -> int:
    """Nombre de syllabes prononcées pour un mot du script (nombres et sigles compris)."""
    stripped = token.strip(" \t.,;:!?…«»\"()[]{}")
    if not stripped:
        return 0
    if re.fullmatch(r"[+-]?\d+([.,]\d+)?", stripped):
        integer, _, decimal = stripped.replace(",", ".").partition(".")
        spoken = french_number(int(integer.lstrip("+-") or 0))
        if decimal:
            spoken += " virgule " + " ".join(_UNITS[int(d)] for d in decimal)
        return sum(_count_piece(_letters_only(piece)) for piece in _split_pieces(spoken.replace(" ", "-")))
    if stripped in {"%", "€", "$", "&"}:
        return {"%": 3, "€": 2, "$": 2, "&": 1}[stripped]
    letters_raw = re.sub(r"[^A-Za-zÀ-ÿŒœÆæ]", "", stripped)
    if len(letters_raw) >= 2 and letters_raw.isupper() and len(letters_raw) <= 5 and not re.search(r"[AEIOUY]", letters_raw):
        return len(letters_raw)            # sigle sans voyelle (SNCF) : lettres épelées
    total = 0
    pieces = _split_pieces(stripped.lower())
    for index, piece in enumerate(pieces):
        letters = _letters_only(piece)
        if index < len(pieces) - 1 and letters in ELIDED:   # clitique élidé (l', d', qu') : pas de syllabe propre
            continue
        count = _count_piece(letters)
        total += count
    mixed = re.search(r"\d", stripped)
    if mixed:
        total += len(re.findall(r"\d+", stripped)) * 2
    return max(total, 1 if letters_raw else 0)


@dataclass(frozen=True)
class ScriptWord:
    text: str                     # tel qu'affiché (ponctuation comprise)
    syllables: int
    sentence: int                 # numéro de phrase
    pause_before: float           # pause attendue avant ce mot : 0 aucune, 0.5 virgule, 1 phrase, 1.5 paragraphe
    sentence_end: bool            # le mot termine une phrase


_SENTENCE_END = re.compile(r"[.!?…]+[\"»”)\]]*$")
_SOFT_PAUSE = re.compile(r"[,;:–—]+[\"»”)\]]*$")


def clean_script(text: str) -> str:
    """Retire ce qui ne se prononce pas : indications [entre crochets], balises, liens, symboles de mise en forme."""
    value = text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("﻿")
    value = re.sub(r"\[[^\]\n]*\]", " ", value)                 # [pause], [musique]…
    value = re.sub(r"<[^>\n]{1,40}>", " ", value)               # balises <b> …
    value = re.sub(r"https?://\S+|www\.\S+", " ", value)
    value = re.sub(r"^\s{0,3}#{1,6}\s*", "", value, flags=re.MULTILINE)   # titres markdown
    value = re.sub(r"[*_`~]{1,3}", "", value)
    value = re.sub(r"^\s*[-•>]\s+", "", value, flags=re.MULTILINE)
    return value


def parse_script(text: str) -> list[ScriptWord]:
    """Découpe un script en mots avec les pauses attendues (virgule, fin de phrase, nouveau paragraphe)."""
    paragraphs = [block for block in re.split(r"\n\s*\n", clean_script(text)) if block.strip()]
    words: list[ScriptWord] = []
    sentence = 0
    for paragraph_index, paragraph in enumerate(paragraphs):
        tokens = paragraph.replace("\n", " ").split()
        pending_pause = 1.5 if paragraph_index and words else 0.0
        for index, token in enumerate(tokens):
            syllables = count_syllables(token)
            if syllables == 0:
                # Ponctuation isolée (« ! », « ? », « … », « : » à la française) : rattachée au mot précédent.
                if words and re.fullmatch(r"[\W_]+", token):
                    last = words[-1]
                    ends_here = bool(_SENTENCE_END.search(token)) or last.sentence_end
                    words[-1] = ScriptWord(f"{last.text}\u00a0{token}" if token in "!?:;»" else f"{last.text}{token}",
                                           last.syllables, last.sentence, last.pause_before, ends_here)
                    if _SENTENCE_END.search(token):
                        pending_pause, sentence = 1.0, max(sentence, last.sentence + 1)
                    elif _SOFT_PAUSE.search(token):
                        pending_pause = max(pending_pause, 0.5)
                continue
            ends = bool(_SENTENCE_END.search(token))
            words.append(ScriptWord(token, syllables, sentence, pending_pause, ends))
            pending_pause = 1.0 if ends else (0.5 if _SOFT_PAUSE.search(token) else 0.0)
            if ends:
                sentence += 1
    return words


def script_from_file(path: str | Path) -> str:
    """Lit un script : texte brut, ou texte d'un fichier de sous-titres (.srt, .vtt, .ass) sans ses horodatages."""
    file = Path(path)
    suffix = file.suffix.lower()
    if suffix in {".srt", ".vtt", ".ass", ".ssa"}:
        document = load_caption_file(file)
        return "\n".join(cue.text.replace("\n", " ") for cue in document.cues)
    try:
        raw = file.read_bytes()
    except OSError as exc:
        raise CaptionParseError(f"Impossible de lire {file} : {exc}") from exc
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")
