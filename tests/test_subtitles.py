"""Sous-titres calés sur la voix à partir d'un script : syllabes, alignement, regroupement, report sur le montage."""

import numpy as np
import pytest
from synthetic_speech import analysis_from_signal, speak

from cutweave.autoedit.modes import MODES_BY_KEY, VARIANTS
from cutweave.autoedit.planner import EditRequest, MusicChoice, build_plan
from cutweave.media.render import VisualSettings, build_ass_content
from cutweave.stages.composition.captions import CaptionCue
from cutweave.storage.project_store import ProjectStore
from cutweave.subtitles.align import align_script, detect_nuclei
from cutweave.subtitles.cues import TimelineWord, cues_for_media, cues_for_plan, group_words, map_to_timeline
from cutweave.subtitles.script import ScriptWord, clean_script, count_syllables, french_number, parse_script
from cutweave.subtitles.service import SubtitleError, align_to_rushes, check_inputs, subtitle_preset

SCRIPT = ("Salut tout le monde ! Aujourd'hui, on va monter une vidéo ensemble, très simplement. "
          "Premièrement, on dépose les rushs dans le logiciel.\n\nEnsuite, on choisit un style et on lance le montage. "
          "C'est vraiment rapide, et le résultat est propre !")


@pytest.mark.parametrize("word,expected", [
    ("bonjour", 2), ("monde", 1), ("école", 2), ("beaucoup", 2), ("aujourd'hui", 3), ("présentation", 4),
    ("vidéo", 3), ("le", 1), ("que", 1), ("créée", 2), ("l'homme", 1), ("qu'il", 1), ("SNCF", 4), ("peut-être", 2),
    ("2024", 4), ("génial", 2), ("ordinateur", 4), ("mercredi", 3), ("incroyable", 3),
])
def test_french_syllable_counts(word, expected):
    assert count_syllables(word) == expected


def test_numbers_are_spelled_in_french():
    assert french_number(2024) == "deux mille vingt-quatre"
    assert french_number(71) == "soixante et onze"
    assert french_number(91) == "quatre-vingt-onze"
    assert french_number(1999) == "mille neuf cent quatre-vingt-dix-neuf"


def test_script_parsing_keeps_expected_pauses_and_ignores_directions():
    words = parse_script("Bonjour, tout le monde. [pause] On commence !\n\nMerci à tous.")
    assert [word.text for word in words][:3] == ["Bonjour,", "tout", "le"]
    by_text = {word.text: word for word in words}
    assert by_text["tout"].pause_before == 0.5          # après une virgule
    assert by_text["On"].pause_before == 1.0            # après un point
    assert by_text["Merci"].pause_before == 1.5         # nouveau paragraphe
    assert by_text["monde."].sentence_end and "[pause]" not in clean_script("a [pause] b")


def test_voiced_syllables_are_detected_and_noise_is_not():
    words = parse_script(SCRIPT)
    signal, _truth = speak(words)
    analysis = analysis_from_signal(signal)
    nuclei = detect_nuclei(analysis)
    assert len(nuclei) == sum(word.syllables for word in words)


def _errors(result, truth, origin=None):
    origin = origin if origin is not None else range(len(result.words))
    starts = [abs(word.start - truth[o][1]) for word, o in zip(result.words, origin) if o is not None]
    ends = [abs(word.end - truth[o][2]) for word, o in zip(result.words, origin) if o is not None]
    return np.mean(starts), np.percentile(starts, 90), np.mean(ends)


def test_alignment_is_precise_on_an_exact_script():
    words = parse_script(SCRIPT)
    signal, truth = speak(words)
    result = align_script(words, [analysis_from_signal(signal)])
    assert result.matched_ratio == pytest.approx(1.0) and result.quality > 0.95
    start_mean, start_p90, end_mean = _errors(result, truth)
    assert start_mean < 0.05 and start_p90 < 0.09 and end_mean < 0.05


def test_alignment_degrades_gracefully_with_script_errors():
    words = parse_script(SCRIPT)
    signal, truth = speak(words)
    analysis = analysis_from_signal(signal)
    index = list(range(len(words)))
    miscounted = [ScriptWord(w.text, max(1, w.syllables + (1 if i % 5 == 0 else -1 if i % 7 == 0 and w.syllables > 1 else 0)),
                             w.sentence, w.pause_before, w.sentence_end) for i, w in enumerate(words)]
    assert _errors(align_script(miscounted, [analysis]), truth)[0] < 0.2
    kept = [i for i in index if i not in (9, 10)]
    missing = align_script([words[i] for i in kept], [analysis])
    assert _errors(missing, truth, kept)[0] < 0.15
    extra = words[:12] + [ScriptWord("bla", 1, 1, 0.0, False), ScriptWord("blabla", 2, 1, 0.0, False)] + words[12:]
    origin = index[:12] + [None, None] + index[12:]
    assert _errors(align_script(extra, [analysis]), truth, origin)[0] < 0.1


def test_script_follows_the_rushes_in_chronological_order():
    words = parse_script(SCRIPT)
    signal, truth = speak(words)
    cut = truth[[word.text for word in words].index("Ensuite,")][1] - 0.3
    first = analysis_from_signal(signal[:int(cut * 8000)], "a.mp4", modified=1.0)
    second = analysis_from_signal(signal[int(cut * 8000):], "b.mp4", modified=2.0)
    aligned = align_to_rushes(SCRIPT, [second, first])        # fournis dans le désordre : la date les remet en ordre
    assert {word.source for word in aligned.result.words} == {first.path, second.path}
    for word, (_text, start, _end) in zip(aligned.result.words, truth):
        expected = start if start < cut else start - cut
        assert abs(word.start - expected) < 0.2


def test_wrong_or_missing_script_is_refused_with_a_clear_message():
    signal, _truth = speak(parse_script(SCRIPT))
    analysis = analysis_from_signal(signal)
    with pytest.raises(SubtitleError, match="aucune IA"):
        check_inputs("   ", "phrase")
    with pytest.raises(SubtitleError, match="ne correspond pas"):
        align_to_rushes("Un tout petit texte.", [analysis])
    with pytest.raises(SubtitleError, match="Regroupement"):
        check_inputs("texte", "karaoke")


def test_cues_are_readable_and_never_overlap():
    words = parse_script(SCRIPT)
    signal, _truth = speak(words)
    result = align_script(words, [analysis_from_signal(signal)])
    for grouping, max_lines in (("phrase", 2), ("words", 1)):
        cues = cues_for_media(result.words, grouping)
        assert cues and all(cue.end_ms > cue.start_ms for cue in cues)
        assert all(a.end_ms <= b.start_ms for a, b in zip(cues, cues[1:]))
        assert all(len(cue.text.split("\n")) <= max_lines for cue in cues)
        assert all(len(line) <= 36 for cue in cues for line in cue.text.split("\n"))
    phrase = cues_for_media(result.words, "phrase")
    assert any(cue.text.rstrip().endswith("!") for cue in phrase)
    assert " ".join(cue.text.replace("\n", " ") for cue in phrase).split() == " ".join(
        word.text for word in result.words).split()
    assert len(cues_for_media(result.words, "words")) > len(phrase)


def test_words_are_mapped_through_cuts_and_dropped_when_cut_out():
    words = parse_script(SCRIPT)
    signal, truth = speak(words)
    analysis = analysis_from_signal(signal)
    result = align_script(words, [analysis])
    mode = MODES_BY_KEY["vlog"]
    plan = build_plan((analysis,), mode, VARIANTS[0], EditRequest("vlog"), MusicChoice("sunday", None, "Sunday", 84.0))
    timeline = map_to_timeline(result.words, plan)
    assert timeline and all(a.start <= b.start for a, b in zip(timeline, timeline[1:]))
    assert timeline[-1].end <= plan.duration + 0.3
    kept = {word.text for word in timeline}
    assert len(kept) > 5
    cues = cues_for_plan(result.words, plan, "phrase")
    assert cues and cues[-1].end_ms <= (plan.duration + 0.3) * 1000


def test_timeline_grouping_breaks_on_silence_and_sentence_end():
    words = [TimelineWord("Un", 0.0, 0.2), TimelineWord("deux.", 0.25, 0.6, sentence_end=True),
             TimelineWord("Trois", 0.7, 1.0), TimelineWord("quatre", 2.0, 2.3)]
    cues = group_words(words, "phrase")
    assert [cue.text for cue in cues] == ["Un deux.", "Trois", "quatre"]
    assert all(a.end_ms <= b.start_ms for a, b in zip(cues, cues[1:]))


def test_titles_go_on_top_when_subtitles_are_present(tmp_path):
    from cutweave.media.probe import MediaInfo

    store = ProjectStore.create(tmp_path / "p")
    media = store.add_media(tmp_path / "a.mp4", MediaInfo(5.0, 320, 180, 25.0, True, "h264", "aac"))
    cues = (CaptionCue(0, 1000, "Titre", "top"), CaptionCue(1200, 2500, "Sous-titre"))
    store.set_captions(media.id, cues)
    assert store.get_captions(media.id) == cues
    ass = build_ass_content(cues, VisualSettings("16:9", 320, 180))
    lines = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    assert r"\an8" in lines[0] and r"\an8" not in lines[1]
    with pytest.raises(Exception):
        CaptionCue(0, 100, "x", "middle").validate()


def test_subtitle_presets_follow_the_style():
    assert subtitle_preset("edit", "phrase", "Impact") == "Dynamic"
    assert subtitle_preset("edit", "words", "Impact") == "Impact"
    assert subtitle_preset("vlog", "phrase", "Vlog") == "Vlog"
