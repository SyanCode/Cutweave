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
