"""Montage automatique : analyse, tempo, planification déterministe et actifs intégrés (sans FFmpeg)."""

import wave

import numpy as np
import pytest

from cutweave.assets import library, synth
from cutweave.autoedit.analysis import STEP, RushAnalysis, activity_segments, interest_curve
from cutweave.autoedit.assemble import build_assembly_graph, shot_video_filter
from cutweave.autoedit.beats import RATE, estimate_tempo
from cutweave.autoedit.modes import MODES, MODES_BY_KEY, VARIANTS
from cutweave.autoedit.planner import (
    FPS, EditPlan, EditRequest, MusicChoice, PlanError, build_plan, shot_starts, timeline_duration,
)


def _rush(name, duration, *, speech=(), seed=0, modified=0.0, has_audio=True):
    rng = np.random.default_rng(seed)
    samples = int(np.ceil(duration / STEP))
    loudness = np.full(samples, -60.0)
    for start, end in speech:
        loudness[int(start / STEP):int(end / STEP)] = -18.0
    return RushAnalysis(
        path=f"/rushes/{name}", name=name, duration=duration, width=1920, height=1080, frame_rate=30.0,
        has_audio=has_audio, modified=modified,
        motion=tuple(float(v) for v in rng.uniform(0.01, 0.2, samples)),
        brightness=tuple(float(v) for v in rng.uniform(0.3, 0.7, samples)),
        sharpness=tuple(float(v) for v in rng.uniform(50, 400, samples)),
        loudness=tuple(float(v) for v in loudness), scene_cuts=(),
        activity=activity_segments(loudness, duration=duration) if has_audio else (),
    )


RUSHES = (
    _rush("a.mp4", 24.0, seed=1, modified=1.0),
    _rush("b.mp4", 15.0, seed=2, modified=2.0),
    _rush("c.mp4", 9.5, seed=3, modified=3.0),
)
MUSIC = MusicChoice("pulse", None, "Pulse", 124.0)


def _check_bounds(plan: EditPlan, rushes):
    by_path = {rush.path: rush for rush in rushes}
    for shot in plan.shots:
        source = by_path[shot.source]
        assert shot.source_start >= 0
        assert shot.source_start + shot.source_duration <= source.duration + 1e-6
        assert abs(shot.duration * FPS - round(shot.duration * FPS)) < 1e-4
        assert shot.transition_in.duration < shot.duration


@pytest.mark.parametrize("mode", [mode.key for mode in MODES])
def test_every_mode_builds_a_valid_deterministic_plan(mode):
    rushes = RUSHES if MODES_BY_KEY[mode].strategy == "beats" else (
        _rush("talk.mp4", 30.0, speech=((1, 6), (8, 14), (16, 25)), seed=4),
        *RUSHES,
    )
    request = EditRequest(mode, 540, 960, title="Titre")
    for variant in VARIANTS:
        first = build_plan(rushes, MODES_BY_KEY[mode], variant, request, MUSIC)
        second = build_plan(rushes, MODES_BY_KEY[mode], variant, request, MUSIC)
        assert first == second
        assert first.shots and first.duration > 1
        _check_bounds(first, rushes)
        assert EditPlan.from_json(first.to_json()) == first


def test_beat_modes_cut_on_the_music_grid():
    plan = build_plan(RUSHES, MODES_BY_KEY["edit"], VARIANTS[0], EditRequest("edit", duration=20), MUSIC)
    beat = 60 / MUSIC.bpm
    assert abs(plan.duration - round(plan.duration / beat / 4) * 4 * beat) < 0.05
    for start in shot_starts(plan.shots):
        # Chaque plan (et sa transition d'entrée) démarre sur un temps, à l'image près.
        assert abs(start / beat - round(start / beat)) * beat <= 1.5 / FPS
    assert len({shot.rush_index for shot in plan.shots}) == 3


def test_speech_modes_keep_voice_and_drop_silences():
    talk = _rush("talk.mp4", 30.0, speech=((1, 6), (8, 14), (16, 25)), seed=4)
    plan = build_plan((talk,), MODES_BY_KEY["talking"], VARIANTS[0], EditRequest("talking"), None)
    kept = sum(shot.duration for shot in plan.shots)
    assert 19 <= kept <= 23
    assert all(shot.speech for shot in plan.shots)
    effects = [shot.effect for shot in plan.shots]
    assert "punch" in effects and "none" in effects
    vlog = build_plan((talk, *RUSHES), MODES_BY_KEY["vlog"], VARIANTS[0], EditRequest("vlog"), MUSIC)
    assert vlog.duck_segments and vlog.duck_level < 1
    assert any(not shot.speech for shot in vlog.shots)


def test_requested_duration_is_respected_and_short_rushes_fail_cleanly():
    plan = build_plan(RUSHES, MODES_BY_KEY["story"], VARIANTS[0], EditRequest("story", duration=15), MUSIC)
    assert 12 <= plan.duration <= 18
    with pytest.raises(PlanError):
        build_plan((), MODES_BY_KEY["edit"], VARIANTS[0], EditRequest("edit"), MUSIC)


def test_shot_filters_and_assembly_graph():
    plan = build_plan(RUSHES, MODES_BY_KEY["cinematic"], VARIANTS[0], EditRequest("cinematic", 1280, 720), MUSIC)
    assert plan.letterbox
    graph, video, audio = build_assembly_graph(plan)
    assert video == "[vout]" and audio == "[aout]"
    assert "drawbox" in graph and "fade=t=out" in graph
    if any(shot.transition_in.kind != "cut" for shot in plan.shots):
        assert "xfade=transition=" in graph and "acrossfade" in graph
    for shot in plan.shots:
        assert shot_video_filter(shot, 1280, 720, "fit_blur").endswith("[v]")
    starts = shot_starts(plan.shots)
    assert timeline_duration(plan.shots) == pytest.approx(starts[-1] + plan.shots[-1].duration)


def test_activity_segments_merge_short_gaps():
    loudness = np.full(80, -60.0)
    loudness[4:20] = -15
    loudness[21:30] = -15   # trou de 0,25 s : fusionné
    loudness[50:51] = -15   # trop court : ignoré
    assert activity_segments(loudness) == ((0.85, 7.65),)
    assert activity_segments(np.full(40, -70.0)) == ()


def test_interest_curve_penalizes_black_frames():
    rush = _rush("dark.mp4", 10, seed=5)
    dark = RushAnalysis(**{**rush.__dict__, "brightness": tuple(0.01 for _ in rush.brightness)})
    bright = interest_curve(rush, motion_weight=1, audio_weight=0, sharpness_weight=1)
    black = interest_curve(dark, motion_weight=1, audio_weight=0, sharpness_weight=1)
    assert black.mean() < bright.mean() * 0.5


def test_tempo_estimation_on_click_track():
    bpm, offset = 128.0, 0.2
    signal = np.zeros(int(RATE * 20), dtype=np.float32)
    click = (np.random.default_rng(0).standard_normal(200) * np.exp(-np.arange(200) / 30)).astype(np.float32)
    position = offset
    while position * RATE + 200 < len(signal):
        start = int(position * RATE)
        signal[start:start + 200] += click
        position += 60 / bpm
    found_bpm, found_offset = estimate_tempo(signal)
    assert abs(found_bpm - bpm) < 1.5
    period = 60 / bpm
    assert min(abs(found_offset - offset), period - abs(found_offset - offset)) < 0.03


def test_builtin_assets_are_synthesized_deterministically(tmp_path):
    _spec, first = synth.render_sfx("pop")
    _spec, second = synth.render_sfx("pop")
    assert np.array_equal(first, second)
    effect = library.ensure_sfx("whoosh", tmp_path)
    with wave.open(str(effect.path)) as stream:
        assert stream.getnchannels() == 2 and stream.getframerate() == synth.SAMPLE_RATE
    track = library.ensure_track("sunday", tmp_path)
    with wave.open(str(track.path)) as stream:
        assert stream.getnframes() == pytest.approx(track.duration_s * synth.SAMPLE_RATE, abs=2)
    assert {look.key for look in library.LOOKS} >= {"natural", "punchy", "teal_orange", "mono"}
    assert all(look.filters for look in library.LOOKS)
    keys = {track.key for track in library.music_catalog()}
    assert all(set(mode.music) <= keys for mode in MODES)
