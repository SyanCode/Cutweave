import json
import threading

import cv2
import numpy as np
import pytest

from cutweave.stages.visual.tracking import (
    TrackingCancelled,
    TrackingError,
    available_trackers,
    load_trajectory,
    save_trajectory,
    track_region,
    trajectory_to_framing_keyframes,
)


def make_moving_target_video(path):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (128, 96))
    if not writer.isOpened():
        pytest.skip("Le build OpenCV local ne fournit pas de VideoWriter MJPG.")
    for frame_index in range(24):
        frame = np.full((96, 128, 3), (24, 24, 24), dtype=np.uint8)
        x = 20 + frame_index
        cv2.rectangle(frame, (x, 30), (x + 23, 53), (0, 0, 255), -1)
        cv2.line(frame, (0, 70), (127, 70), (80, 80, 80), 1)
        writer.write(frame)
    writer.release()


def test_classical_tracker_exports_manual_roi_trajectory(tmp_path):
    video = tmp_path / "moving_target.avi"
    make_moving_target_video(video)
    assert "KCF" in available_trackers()

    trajectory = track_region(video, (20, 30, 24, 24), method="KCF", frame_step=2)
    assert trajectory.source_name == video.name
    assert trajectory.method == "KCF"
    assert trajectory.initial_bbox == (20, 30, 24, 24)
    assert trajectory.samples[0].bbox == (20, 30, 24, 24)
    assert len(trajectory.samples) >= 10
    assert trajectory.samples[-1].bbox is not None
    assert trajectory.samples[-1].bbox[0] > trajectory.samples[0].bbox[0]

    output = save_trajectory(trajectory, tmp_path / "tracking" / "trajectory.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["method"] == "KCF"
    assert payload["samples"][0]["bbox"] == [20, 30, 24, 24]
    reloaded = load_trajectory(output)
    framing = trajectory_to_framing_keyframes(reloaded, "9:16")
    assert framing[0].time_ms == reloaded.samples[0].time_ms
    assert framing[0].center_x == pytest.approx((20 + 12) / 128)
    assert framing[0].center_y == pytest.approx((30 + 12) / 96)
    assert framing[0].zoom >= 1


def test_trajectory_conversion_skips_lost_samples_and_caps_keyframes():
    from cutweave.stages.visual.tracking import TrackSample, TrackingTrajectory

    samples = tuple(
        TrackSample(index, index * 40, (index, 20, 12, 16) if index != 7 else None)
        for index in range(20)
    )
    trajectory = TrackingTrajectory(1, "clip.mp4", "KCF", 25, 128, 96, 0, (0, 20, 12, 16), samples)
    frames = trajectory_to_framing_keyframes(trajectory, "16:9", max_keyframes=5)
    assert len(frames) == 5
    assert [frame.time_ms for frame in frames] == sorted(frame.time_ms for frame in frames)
    assert frames[0].center_x == pytest.approx(6 / 128)
    assert frames[-1].center_x == pytest.approx((19 + 6) / 128)


def test_load_trajectory_rejects_boxes_outside_source(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({
        "schema_version": 1, "source_name": "clip.mp4", "method": "KCF", "fps": 25,
        "frame_width": 100, "frame_height": 80, "start_frame": 0,
        "initial_bbox": [0, 0, 10, 10],
        "samples": [{"frame": 0, "time_ms": 0, "bbox": [95, 0, 10, 10]}],
    }), encoding="utf-8")
    with pytest.raises(TrackingError, match="dépasse"):
        load_trajectory(invalid)


def test_tracker_rejects_out_of_bounds_roi(tmp_path):
    video = tmp_path / "moving_target.avi"
    make_moving_target_video(video)
    with pytest.raises(TrackingError, match="sélection manuelle"):
        track_region(video, (120, 80, 20, 20), method="KCF")


def test_tracker_obeys_pre_requested_cancellation(tmp_path):
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(TrackingCancelled):
        track_region(tmp_path / "missing.avi", (1, 1, 10, 10), cancel_event=cancellation)


def test_tracked_target_drives_animated_mask_keyframes(tmp_path):
    from cutweave.stages.composition.masking import MaskRegion
    from cutweave.stages.visual.tracking import trajectory_to_mask_keyframes

    video = tmp_path / "moving_target.avi"
    make_moving_target_video(video)
    trajectory = track_region(video, (20, 30, 24, 24), method="KCF")
    keyframes = trajectory_to_mask_keyframes(trajectory, 128, 96, padding=0, smoothing_window=3)
    assert len(keyframes) >= 10
    assert keyframes[-1].x > keyframes[0].x + 10  # le masque suit la cible vers la droite
    first = keyframes[0]
    MaskRegion(first.x, first.y, first.width, first.height, keyframes=keyframes).validate(128, 96)


def test_trajectory_drawing_and_loss_summary(tmp_path):
    from cutweave.stages.visual.tracking import TrackSample, TrackingTrajectory, draw_trajectory, tracking_summary

    samples = (TrackSample(0, 0, (10, 10, 20, 20)), TrackSample(1, 40, (30, 10, 20, 20)), TrackSample(2, 80, None))
    trajectory = TrackingTrajectory(1, "x.avi", "KCF", 25.0, 128, 96, 0, (10, 10, 20, 20), samples)
    assert tracking_summary(trajectory) == {"samples": 3, "tracked": 2, "lost": 1, "first_loss_ms": 80}
    frame = np.zeros((96, 128, 3), dtype=np.uint8)
    drawn = draw_trajectory(frame, trajectory)
    assert frame.sum() == 0 and drawn.sum() > 0
    assert drawn[20, 30].tolist() != [0, 0, 0]  # chemin entre les deux centres
