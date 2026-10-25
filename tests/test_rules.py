import pytest

from cutweave.stages.composition.broll import BrollAsset, BrollPlacement, BrollValidationError
from cutweave.stages.composition.broll_rules import BrollRule, apply_broll_rules
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.finishing.music import (
    DuckingSegment, MusicSettings, combine_ducking, ducking_from_captions, effective_ducking,
)
from cutweave.stages.finishing.sfx_rules import (
    SfxRule, SfxValidationError, TimelineMarker, apply_sfx_rules, timeline_events,
)
from cutweave.stages.visual.framing import FramingKeyframe, interpolate_keyframes, source_to_output_transform
from cutweave.stages.visual.tracking import (
    TrackSample, TrackingTrajectory, smooth_tracked_boxes, trajectory_to_mask_keyframes,
)


def asset(asset_id, name, tags=(), duration=5000):
    from pathlib import Path
    return BrollAsset(asset_id, Path(f"{name}.mp4"), name, duration, 320, 180, "", tags)


def test_broll_rules_match_whole_words_only_and_never_invent():
    cues = (CaptionCue(0, 1000, "La plage est belle"), CaptionCue(2000, 3000, "plagement faux"),
            CaptionCue(4000, 5000, "Rien à voir"))
    rules = (BrollRule("plage", asset_id=1),)
    placements = apply_broll_rules(rules, cues, (asset(1, "mer"),))
    assert placements == (BrollPlacement(1, 0, 0, 1000, 0),)
    assert apply_broll_rules((), cues, (asset(1, "mer"),)) == ()


def test_broll_tag_rules_rotate_deterministically_and_skip_overlaps():
    cues = (CaptionCue(0, 1000, "ville"), CaptionCue(1500, 2500, "VILLE"), CaptionCue(3000, 3500, "ville"))
    assets = (asset(2, "b_rue", ("ville",)), asset(1, "a_toits", ("ville",)), asset(3, "c", ("mer",)))
    existing = (BrollPlacement(9, 2800, 0, 1000, 0),)
    placements = apply_broll_rules((BrollRule("ville", tag="ville"),), cues, assets, existing)
    assert [p.asset_id for p in placements] == [1, 2]
    assert [p.timeline_start_ms for p in placements] == [0, 1500]


def test_broll_rule_requires_exactly_one_target():
    with pytest.raises(BrollValidationError):
        BrollRule("x").validate()
    with pytest.raises(BrollValidationError):
        BrollRule("x", asset_id=1, tag="y").validate()


def test_timeline_events_come_only_from_explicit_data():
    events = timeline_events(
        3000, captions=(CaptionCue(100, 900, "Salut"),), broll=(BrollPlacement(4, 1000, 0, 500),),
        markers=(TimelineMarker(2000, "drop"),), caption_animated=True,
    )
    kinds = [(event.time_ms, event.kind) for event in events]
    assert (0, "clip_start") in kinds and (3000, "clip_end") in kinds
    assert (1000, "cut") in kinds and (1500, "cut") in kinds
    assert (100, "caption_animation") in kinds
    assert (2000, "marker") in kinds
    assert all(kind != "cut" or time not in (0, 3000) for time, kind in kinds)


def test_sfx_rules_offset_filter_and_debounce():
    events = timeline_events(5000, markers=(TimelineMarker(1000, "hit"), TimelineMarker(1100, "hit"),
                                            TimelineMarker(3000, "other")))
    rules = (SfxRule("marker", 7, volume=0.5, offset_ms=-200, label_filter="HIT", min_interval_ms=500),)
    placements = apply_sfx_rules(rules, events, {7: 400}, 5000)
    assert [(p.timeline_start_ms, p.source_end_ms, p.volume) for p in placements] == [(800, 400, 0.5)]
    assert placements[0].event_marker == "marker:hit"
    with pytest.raises(SfxValidationError):
        apply_sfx_rules((SfxRule("marker", 8),), events, {7: 400}, 5000)
    with pytest.raises(SfxValidationError):
        SfxRule("explosion_detectee", 7).validate()


def test_caption_ducking_merges_close_cues_and_combines_with_manual():
    cues = (CaptionCue(1000, 2000, "a"), CaptionCue(2100, 3000, "b"), CaptionCue(6000, 6100, "c"))
    segments = ducking_from_captions(cues, 0.3, 100)
    assert [(s.start_ms, s.end_ms) for s in segments] == [(1000, 3000), (6000, 6100)]
    assert segments[1].fade_ms == 50
    settings = MusicSettings(1, source_end_ms=9000, ducking_segments=(DuckingSegment(2500, 4000, 0.1, 50),),
                             duck_on_captions=True)
    combined = effective_ducking(settings, cues, 6050)
    assert combined[0] == DuckingSegment(1000, 4000, 0.1, 120)
    assert combined[-1].end_ms == 6050
    assert combine_ducking(()) == ()


def test_framing_transform_matches_center_crop_without_keyframes():
    scale, offset_x, offset_y = source_to_output_transform((), 1080, 1920, 1920, 1080, 0)
    assert scale == pytest.approx(1920 / 1080)
    assert offset_x == pytest.approx((1920 * scale - 1080) / 2)
    assert offset_y == pytest.approx(0)
    frames = (FramingKeyframe(0, 0.0, 0.5, 1.0), FramingKeyframe(1000, 1.0, 0.5, 2.0))
    assert interpolate_keyframes(frames, 500) == pytest.approx((0.5, 0.5, 1.5))


def _trajectory(boxes):
    samples = tuple(TrackSample(index, index * 40, box) for index, box in enumerate(boxes))
    return TrackingTrajectory(1, "x.mp4", "CSRT", 25.0, 320, 180, 0, boxes[0], samples)


def test_tracking_smoothing_ignores_lost_samples():
    smoothed = smooth_tracked_boxes(_trajectory([(10, 10, 20, 20), None, (20, 10, 20, 20), (30, 10, 20, 20)]).samples, 3)
    assert [sample.bbox[0] for sample in smoothed] == [15, 20, 25]


def test_tracked_boxes_project_to_output_mask_keyframes():
    trajectory = _trajectory([(100, 50, 40, 40), (300, 50, 20, 20)])
    keyframes = trajectory_to_mask_keyframes(trajectory, 320, 180, padding=0)
    assert keyframes[0].x == 100 and keyframes[0].width == 40
    assert keyframes[1].x + keyframes[1].width == 320  # borné au cadre de sortie
    vertical = trajectory_to_mask_keyframes(_trajectory([(140, 70, 40, 40)]), 90, 160, padding=0)
    scale = 160 / 180
    assert vertical[0].x == round(140 * scale - (320 * scale - 90) / 2)
