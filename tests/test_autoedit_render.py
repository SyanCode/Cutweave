"""Montage automatique de bout en bout avec FFmpeg réel : variantes, projet configuré, export."""

import subprocess

import pytest

from cutweave.autoedit.analysis import analyze_rush
from cutweave.autoedit.service import (
    StudioRequest, export_variant, generate_variants, is_montage, list_variants, rush_media,
)
from cutweave.core.pipeline import collect_job
from cutweave.media.probe import ffprobe_path, probe_media
from cutweave.media.render import ffmpeg_path
from cutweave.storage.project_store import ProjectStore

pytestmark = pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")


def _make(path, video, audio=None, duration=6):
    command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", video]
    if audio:
        command += ["-f", "lavfi", "-i", audio]
    command += ["-t", str(duration), "-c:v", "mpeg4", "-q:v", "5"]
    command += ["-c:a", "aac", "-shortest"] if audio else []
    subprocess.run(command + [str(path)], check=True, capture_output=True)
    return path


@pytest.fixture()
def project(tmp_path):
    talk = "sine=frequency=220:duration=8,volume='if(lt(mod(t\\,2.5)\\,1.6)\\,1\\,0.002)':eval=frame"
    rushes = [
        _make(tmp_path / "talk.mp4", "testsrc2=size=320x180:rate=25", talk, 8),
        _make(tmp_path / "fractal.mp4", "mandelbrot=size=180x320:rate=25", None, 5),
        _make(tmp_path / "cells.mp4", "cellauto=size=240x240:rate=25:rule=30", "anoisesrc=d=5:a=0.1", 5),
    ]
    store = ProjectStore.create(tmp_path / "project")
    for path in rushes:
        store.add_media(path, probe_media(path))
    return store


def test_analysis_detects_voice_activity_and_is_cached(project, tmp_path):
    talk = next(media for media in project.list_media() if media.name == "talk.mp4")
    analysis = analyze_rush(talk.path, cache_dir=tmp_path / "cache")
    assert analysis.has_audio and 2 <= len(analysis.activity) <= 4
    assert 0.4 < analysis.speech_ratio < 0.9
    assert analyze_rush(talk.path, cache_dir=tmp_path / "cache") == analysis


def test_generate_variants_configures_editable_montages(project, tmp_path):
    rushes = tuple(str(media.path) for media in rush_media(project))
    request = StudioRequest(rushes, "edit", "9:16", 120, 8.0, "auto", None, "fill", "Bonjour", "", 2)
    events = []
    results = generate_variants(project, request, assets_root=tmp_path / "assets", on_progress=events.append)
    assert [result.letter for result in results] == ["A", "B"]
    assert events[-1].fraction == pytest.approx(1.0)
    for result in results:
        info = probe_media(result.preview)
        assert (info.width, info.height) == (120, 214)
        assert info.has_audio and 6 <= info.duration <= 10
        media = next(item for item in project.list_media() if item.id == result.media_id)
        assert is_montage(project, media)
        job = collect_job(project, media)
        assert job.music is not None and job.music.settings.loop
        assert not job.music.settings.mix_original_audio
        assert job.sfx and job.captions[0].text == "Bonjour"
        assert job.caption_preset.name == "Impact"
    assert {result.key for result in list_variants(project)} == {"edit_A", "edit_B"}
    assert len(rush_media(project)) == 3

    exported = export_variant(project, results[0], tmp_path / "final.mp4")
    assert probe_media(exported).duration == pytest.approx(results[0].duration, abs=0.1)


def test_vlog_keeps_original_voice_with_ducked_music(project, tmp_path):
    rushes = tuple(str(media.path) for media in rush_media(project))
    request = StudioRequest(rushes, "vlog", "16:9", 120, None, "sunday", "warm", "fit_blur", "", "", 1)
    (result,) = generate_variants(project, request, assets_root=tmp_path / "assets")
    media = next(item for item in project.list_media() if item.id == result.media_id)
    mix = project.get_music_mix(media.id)
    assert mix.mix_original_audio and mix.ducking_segments
    assert result.look_title == "Chaud" and result.music_title == "Sunday"
    assert probe_media(result.preview).width == 214


def test_every_look_runs_and_changes_the_picture(tmp_path):
    from cutweave.assets.library import LOOKS
    from cutweave.autoedit.assemble import filter_names

    assert filter_names("eq=contrast=1.1,scale=2:2") == {"eq", "scale"}
    for look in LOOKS:
        out = tmp_path / f"{look.key}.png"
        subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=1:duration=1", "-vf", look.filters, "-frames:v", "1", str(out)],
                       check=True, capture_output=True)
        assert out.stat().st_size > 500, look.key


def test_missing_filter_gives_a_clear_error(project, tmp_path, monkeypatch):
    from cutweave.autoedit import assemble

    monkeypatch.setattr(assemble, "available_filters", lambda binary=None: {"scale", "crop"})
    assert "xfade" in assemble.missing_filters() and "lutyuv" in assemble.missing_filters()
    rushes = tuple(str(media.path) for media in rush_media(project))
    with pytest.raises(Exception, match="filtres nécessaires"):
        generate_variants(project, StudioRequest(rushes, "edit", "9:16", 120, 6.0, "none", None, "fill", "", "", 1),
                          assets_root=tmp_path / "assets")


def test_draft_previews_are_finalized_at_export_quality(project, tmp_path, monkeypatch):
    from cutweave.autoedit import service

    monkeypatch.setattr(service, "DRAFT_SHORT", 120)
    rushes = tuple(str(media.path) for media in rush_media(project))
    request = StudioRequest(rushes, "highlights", "9:16", 240, 8.0, "none", None, "fill", "", "", 1, draft=True)
    assert request.is_draft and request.output_size(draft=True) == (120, 214)
    (result,) = generate_variants(project, request, assets_root=tmp_path / "assets")
    assert result.draft and (result.final_width, result.final_height) == (240, 426)
    assert probe_media(result.preview).width == 120
    media = next(item for item in project.list_media() if item.id == result.media_id)
    assert service.is_draft_montage(project, media.id) and probe_media(media.path).width == 120
    plan_before = (project.root / "montages" / f"{result.key}.plan.json").read_text(encoding="utf-8")

    exported = export_variant(project, result, tmp_path / "final.mp4")
    info = probe_media(exported)
    assert (info.width, info.height) == (240, 426)
    assert info.duration == pytest.approx(result.duration, abs=0.2)
    assert not service.is_draft_montage(project, media.id)
    refreshed = next(item for item in project.list_media() if item.id == result.media_id)
    assert (refreshed.width, refreshed.height) == (240, 426)
    assert project.get_visual_settings(media.id).width == 240
    assert (project.root / "montages" / f"{result.key}.plan.json").read_text(encoding="utf-8") == plan_before
    assert service.ensure_final(project, media.id) is False


def test_real_decoding_pipeline_removes_coughs(tmp_path):
    """Analyse d'un vrai fichier (décodage FFmpeg à 8 kHz) : les bruits ne comptent pas comme parole."""
    import wave

    import numpy as np
    from test_autoedit import _speech_and_noise

    signal, total = _speech_and_noise()
    wav = tmp_path / "voice.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())
    clip = tmp_path / "voice.mp4"
    subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=gray:s=160x90:r=10:d={total}", "-i", str(wav), "-c:v", "mpeg4", "-c:a", "aac",
                    "-shortest", str(clip)], check=True, capture_output=True)
    analysis = analyze_rush(clip, cache_dir=tmp_path / "cache")
    assert len(analysis.fine_voicing) == len(analysis.fine_loudness) > 500
    assert not any(a < 12.6 and b > 12.05 for a, b in analysis.activity), analysis.activity
    assert not any(a < 16.0 and b > 15.6 for a, b in analysis.activity), analysis.activity
    assert any(a <= 1.2 and b >= 3.8 for a, b in analysis.activity), analysis.activity


def test_studio_subtitles_from_a_script_end_to_end(tmp_path):
    """Voix synthétique dans un vrai MP4 : le script est calé et incrusté dans la variante montée."""
    import wave

    import numpy as np
    from synthetic_speech import speak

    from cutweave.subtitles.script import parse_script
    from test_subtitles import SCRIPT

    signal, truth = speak(parse_script(SCRIPT))
    wav = tmp_path / "voice.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())
    clip = tmp_path / "talk.mp4"
    subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x180:rate=10:duration={len(signal) / 8000:.2f}", "-i", str(wav),
                    "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(clip)], check=True, capture_output=True)
    store = ProjectStore.create(tmp_path / "project")
    store.add_media(clip, probe_media(clip))
    request = StudioRequest((str(clip),), "vlog", "16:9", 180, None, "none", None, "fill", "Mon titre", "", 1,
                            draft=False, script=SCRIPT, subtitles="phrase")
    (result,) = generate_variants(store, request, assets_root=tmp_path / "assets")
    assert result.subtitle_quality > 0.9
    media = next(item for item in store.list_media() if item.id == result.media_id)
    cues = store.get_captions(media.id)
    title = [cue for cue in cues if cue.text == "Mon titre"]
    subtitles = [cue for cue in cues if cue.text != "Mon titre"]
    assert title and title[0].placement == "top" and subtitles
    spoken = " ".join(cue.text.replace("\n", " ") for cue in subtitles).replace("\xa0", " ").split()
    assert spoken[:3] == ["Salut", "tout", "le"] and "propre" in spoken[-2]
    assert all(a.end_ms <= b.start_ms for a, b in zip(subtitles, subtitles[1:]))
    assert subtitles[-1].end_ms <= (media.duration + 0.1) * 1000
    assert store.get_caption_preset(media.id).name == "Vlog"
    assert probe_media(result.preview).has_audio


def test_wrong_script_stops_the_montage_with_an_explanation(tmp_path, project):
    from cutweave.subtitles.service import SubtitleError

    rushes = tuple(str(media.path) for media in rush_media(project))
    request = StudioRequest(rushes, "vlog", "16:9", 120, None, "none", None, "fill", "", "", 1, draft=False,
                            script="Un texte qui ne correspond à aucune voix de ces rushs.", subtitles="phrase")
    with pytest.raises(SubtitleError):
        generate_variants(project, request, assets_root=tmp_path / "assets")
