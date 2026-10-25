import pytest

from cutweave.stages.composition.captions import (
    CaptionParseError,
    load_caption_file,
    merge_cues,
    parse_ass,
    parse_caption_text,
    parse_srt,
    parse_vtt,
    sanitize_caption_markup,
    split_cue,
)


def test_srt_parses_milliseconds_and_multiline_text():
    document = parse_srt("""\ufeff1
00:00:01,250 --> 00:00:03,000
Bonjour
le monde

2
00:00:04.5 --> 00:00:05.000
Fin
""")
    assert document.source_format == "srt"
    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in document.cues] == [
        (1250, 3000, "Bonjour\nle monde"), (4500, 5000, "Fin")
    ]


def test_webvtt_parses_optional_ids_settings_and_skips_notes():
    document = parse_vtt("""WEBVTT

NOTE this is not a caption
ignored

chapter-1
00:01.200 --> 00:02.000 align:start
<i>Texte</i>
""")
    assert len(document.cues) == 1
    assert (document.cues[0].start_ms, document.cues[0].end_ms) == (1200, 2000)
    assert document.cues[0].text == "<i>Texte</i>"


def test_ass_uses_declared_event_fields_and_preserves_commas_in_text():
    document = parse_ass("""[Script Info]
Title: Demo
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.25,0:00:02.50,Default,,0,0,0,,Bonjour, tout le monde\\NDeuxième ligne
""")
    assert document.source_format == "ass"
    assert document.cues[0].start_ms == 1250
    assert document.cues[0].end_ms == 2500
    assert document.cues[0].text == "Bonjour, tout le monde\nDeuxième ligne"


@pytest.mark.parametrize("source,format", [
    ("00:00:02,000 --> 00:00:01,000\nRenversé", "srt"),
    ("00:00:01.000 --> 00:00:01.000\nVide", "vtt"),
    ("invalid", "srt"),
    ("WEBVTT\n\n00:00:03.000 --> 00:00:02.000\nBad", "vtt"),
])
def test_invalid_caption_timestamps_are_reported(source, format):
    with pytest.raises(CaptionParseError):
        parse_caption_text(source, format=format)


def test_file_loader_supports_legacy_windows_text(tmp_path):
    path = tmp_path / "captions.srt"
    path.write_bytes("1\r\n00:00:00,000 --> 00:00:01,000\r\nCaf\xe9\r\n".encode("cp1252"))
    assert load_caption_file(path).cues[0].text == "Café"


def test_markup_is_converted_without_exposing_ass_override_blocks():
    assert sanitize_caption_markup("<b>Salut</b><br>monde &amp; {\u005cpos(1,2)}") == r"{\b1}Salut{\b0}\Nmonde & \{\\pos(1,2)\}"


def test_split_and_merge_keep_caption_times_and_text():
    cue = parse_srt("00:00:01,000 --> 00:00:05,000\nBonjour tout le monde").cues[0]
    first, second = split_cue(cue, 3000)
    assert (first.start_ms, first.end_ms, first.text) == (1000, 3000, "Bonjour tout")
    assert (second.start_ms, second.end_ms, second.text) == (3000, 5000, "le monde")
    merged = merge_cues((first, second))
    assert (merged.start_ms, merged.end_ms, merged.text) == (1000, 5000, "Bonjour tout\nle monde")
