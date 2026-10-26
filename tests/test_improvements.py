"""Normalisation du volume, destinations, choix des rushs (activation et plage)."""

from __future__ import annotations

import pytest

from cutweave.autoedit.modes import MODES_BY_KEY, VARIANTS
from cutweave.autoedit.planner import EditRequest, build_plan
from cutweave.autoedit.platforms import PLATFORMS, duration_warning, platform_by_key
from cutweave.autoedit.rush_settings import RushSettings, load_rush_settings, save_rush_settings
from cutweave.autoedit.analysis import trim_analysis
from tests.test_autoedit import _rush


def test_platforms_are_consistent():
    keys = [platform.key for platform in PLATFORMS]
    assert len(keys) == len(set(keys)) and "free" in keys
    assert platform_by_key("inconnue").key == "free"
    tiktok = platform_by_key("tiktok")
    assert tiktok.aspect == "9:16"
    assert duration_warning(tiktok, 20) == ""
    if tiktok.max_seconds:
        assert "conseillées" in duration_warning(tiktok, tiktok.max_seconds + 30)


def test_rush_settings_roundtrip(tmp_path):
    settings = RushSettings()
    settings.set_enabled(3, False)
    settings.set_range(4, (1.5, 9.0))
    save_rush_settings(tmp_path, settings)
    loaded = load_rush_settings(tmp_path)
    assert not loaded.enabled(3) and loaded.enabled(4)
    assert loaded.range_for(4) == (1.5, 9.0)
    assert loaded.range_for(4, 9.0) == (1.5, 9.0)
    loaded.set_range(4, (0.0, 9.0))
    assert loaded.range_for(4, 9.0) is None
    with pytest.raises(ValueError):
        loaded.set_range(4, (1.0, 1.2))
    assert load_rush_settings(tmp_path / "absent").disabled == set()


def test_trimmed_plan_stays_inside_the_chosen_range():
    rushes = [_rush("a.mp4", 40.0, seed=1, modified=1.0), _rush("b.mp4", 30.0, seed=2, modified=2.0)]
    window = (12.0, 26.0)
    trimmed = [trim_analysis(rushes[0], *window), rushes[1]]
    assert trimmed[0].offset == 12.0 and trimmed[0].duration == 14.0
    assert len(trimmed[0].motion) == len(rushes[0].motion[int(12 / 0.25):int(26 / 0.25)])
    for mode in ("edit", "vlog"):
        for variant in VARIANTS:
            plan = build_plan(trimmed, MODES_BY_KEY[mode], variant, EditRequest(mode, 540, 960), None)
            for shot in plan.shots:
                if shot.source == trimmed[0].path:
                    shifted = shot.source_start + trimmed[0].offset
                    assert shot.source_start + shot.source_duration <= trimmed[0].duration + 1e-6


def test_trim_rejects_tiny_ranges():
    with pytest.raises(ValueError):
        trim_analysis(_rush("a.mp4", 10.0), 3.0, 3.2)
    full = _rush("a.mp4", 10.0)
    assert trim_analysis(full, -1.0, 99.0) is full


def test_loudnorm_brings_a_quiet_track_to_the_target(tmp_path):
    import re
    import shutil
    import subprocess

    from cutweave.media.render import LOUDNORM, TARGET_LUFS

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg indisponible")
    out = tmp_path / "n.wav"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", "-af", f"volume=-35dB,{LOUDNORM}",
                    "-ar", "48000", str(out)], check=True)
    report = subprocess.run([ffmpeg, "-nostats", "-i", str(out), "-af", "ebur128", "-f", "null", "-"],
                            capture_output=True, text=True).stderr
    lufs = float(re.findall(r"I:\s+(-?\d+\.\d)\s+LUFS", report)[-1])
    assert abs(lufs - TARGET_LUFS) < 2.5


def test_timeline_edit_remaps_time_and_clamps_to_the_rush():
    from dataclasses import replace

    from cutweave.autoedit.planner import SfxCue
    from cutweave.autoedit.service import StudioError, edit_plan_shots

    rushes = [_rush("a.mp4", 40.0, seed=1, modified=1.0), _rush("b.mp4", 30.0, seed=2, modified=2.0)]
    plan = build_plan(rushes, MODES_BY_KEY["edit"], VARIANTS[0], EditRequest("edit", 540, 960), None)
    plan = replace(plan, sfx=tuple(SfxCue("whoosh", start, 0.5) for start in plan.shot_starts[1:4]))
    count = len(plan.shots)
    durations = [shot.duration for shot in plan.shots]
    durations[1] = durations[1] + 1.0
    edited, kept = edit_plan_shots(plan, durations, removed=(2,),
                                   available={rush.path: rush.duration for rush in rushes})
    assert kept == [i for i in range(count) if i != 2] and len(edited.shots) == count - 1
    assert edited.shots[0].transition_in.duration == 0.0
    assert all(abs(d * 30 - round(d * 30)) < 1e-4 for d in (s.duration for s in edited.shots))
    assert len(edited.sfx) == len(plan.sfx) - 1
    assert all(0 <= cue.time <= edited.duration for cue in edited.sfx)
    with pytest.raises(StudioError):
        edit_plan_shots(plan, durations, removed=range(count))
    huge = [1000.0] * count
    clamped, _ = edit_plan_shots(plan, huge, available={rush.path: rush.duration for rush in rushes})
    for shot in clamped.shots:
        limit = next(r.duration for r in rushes if r.path == shot.source)
        assert shot.source_start + shot.source_duration <= limit + 1e-3
