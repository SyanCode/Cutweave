import json
import subprocess
import threading

import pytest

from cutweave.core.orchestrator import PipelineCancelled
from cutweave.core.pipeline import PipelineInputError, collect_job, run_job, stage_statuses
from cutweave.media.probe import ffprobe_path, probe_audio, probe_media
from cutweave.media.render import VisualSettings, ffmpeg_path
from cutweave.stages.composition.broll_rules import BrollRule
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.composition.masking import MaskRegion
from cutweave.stages.finishing.music import MusicSettings
from cutweave.stages.finishing.sfx_rules import SfxRule, TimelineMarker
from cutweave.storage.project_store import ProjectStore

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")


def _make(args):
    subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True, capture_output=True)


@pytest.fixture()
def project(tmp_path):
    rushes = tmp_path / "rushes"
    rushes.mkdir()
    source = rushes / "main.mp4"
    broll = rushes / "red.mp4"
    music = rushes / "bed.wav"
    whoosh = rushes / "whoosh.wav"
    _make(["-f", "lavfi", "-i", "color=c=blue:size=320x180:rate=25:duration=2", "-f", "lavfi", "-i",
           "sine=frequency=440:sample_rate=48000:duration=2", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(source)])
    _make(["-f", "lavfi", "-i", "color=c=red:size=320x180:rate=25:duration=1", "-c:v", "mpeg4", str(broll)])
    _make(["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=3", "-c:a", "pcm_s16le", str(music)])
    _make(["-f", "lavfi", "-i", "sine=frequency=1200:sample_rate=48000:duration=0.2", "-c:a", "pcm_s16le", str(whoosh)])
    store = ProjectStore.create(tmp_path / "project")
    media = store.add_media(source, probe_media(source))
    store.set_visual_settings(media.id, VisualSettings("16:9", 320, 180, 0.3))
    store.set_captions(media.id, (CaptionCue(200, 900, "Voici la plage"), CaptionCue(1100, 1700, "Fin du test")))
    store.set_masks(media.id, (MaskRegion(10, 10, 40, 40, 1.0),))
    broll_asset = store.add_broll_asset(broll, probe_media(broll), category="décor", tags=("plage",))
    store.set_broll_rules((BrollRule("plage", tag="plage", max_duration_ms=500),))
    music_asset = store.add_audio_asset(music, probe_audio(music))
    store.set_music_mix(media.id, MusicSettings(music_asset.id, 0, 0, 2000, 0.5, duck_on_captions=True))
    sfx_asset = store.add_audio_asset(whoosh, probe_audio(whoosh), category="sfx")
    store.set_markers(media.id, (TimelineMarker(1500, "boom"),))
    store.set_sfx_rules((SfxRule("caption_start", sfx_asset.id), SfxRule("marker", sfx_asset.id, label_filter="boom")))
    return store, store.list_media()[0], broll_asset


def _frame(path, at, width=320, height=180):
    return subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], check=True, capture_output=True,
    ).stdout


@needs_ffmpeg
def test_collect_job_applies_explicit_rules(project):
    store, media, broll_asset = project
    job = collect_job(store, media)
    assert job.generated_broll == 1
    assert job.broll[0].placement.asset_id == broll_asset.id
    assert job.broll[0].placement.timeline_start_ms == 200
    assert job.broll[0].placement.duration_ms == 500
    # 2 débuts de caption + 1 marqueur « boom »
    assert job.generated_sfx == 3
    assert [clip.placement.timeline_start_ms for clip in job.sfx] == [200, 1100, 1500]
    # Écart de 200 ms < 2 × fondu (120 ms) : les deux captions forment un seul intervalle de ducking.
    assert [(s.start_ms, s.end_ms) for s in job.music.settings.ducking_segments] == [(200, 1700)]


@needs_ffmpeg
def test_full_pipeline_renders_playable_mp4_and_reuses_cache(project, tmp_path):
    store, media, _ = project
    destination = tmp_path / "out" / "final.mp4"
    events = []
    result = run_job(store, collect_job(store, media), destination=destination, on_progress=events.append)
    info = probe_media(result)
    assert result == destination.resolve()
    assert (info.width, info.height) == (320, 180)
    assert info.has_audio is True
    assert abs(info.duration - 2.0) < 0.2
    assert any(event.fraction == 1.0 and event.stage == "render" for event in events)

    broll_frame = _frame(result, 0.4)
    assert broll_frame[(90 * 320 + 160) * 3] > 150  # B-roll rouge inséré par la règle « plage »
    assert max(broll_frame[(30 * 320 + 30) * 3:(30 * 320 + 30) * 3 + 3]) <= 10  # masque opaque
    main_frame = _frame(result, 1.0)
    assert main_frame[(90 * 320 + 160) * 3 + 2] > 150  # rush bleu hors B-roll

    tasks = store.list_tasks()
    assert tasks[0]["status"] == "succeeded"
    second = []
    run_job(store, collect_job(store, media), destination=tmp_path / "out" / "again.mp4", on_progress=second.append)
    assert not any(event.fraction is not None for event in second)  # aucun nouvel encodage


@needs_ffmpeg
def test_preset_change_does_not_invalidate_visual_or_composition(project, tmp_path):
    store, media, _ = project
    run_job(store, collect_job(store, media), destination=tmp_path / "a.mp4")
    from cutweave.stages.finishing.caption_presets import BUILTIN_BY_NAME
    store.set_caption_preset(media.id, BUILTIN_BY_NAME["cinematic"])
    messages = []
    run_job(store, collect_job(store, media), destination=tmp_path / "b.mp4",
            on_progress=lambda event: messages.append((event.stage, event.message)))
    resumed = {stage for stage, message in messages if message and "checkpoint" in message}
    assert resumed == {"visual", "composition"}


@needs_ffmpeg
def test_single_stage_produces_intermediate_video(project):
    store, media, _ = project
    output = run_job(store, collect_job(store, media), target_stage="visual")
    assert output.suffix == ".mp4"
    assert probe_media(output).width == 320
    state = json.loads((store.root / ".cutweave_pipeline" / f"state_media_{media.id}.json").read_text(encoding="utf-8"))
    assert state["stages"]["visual"]["status"] == "succeeded"
    assert "render" not in state["stages"]
    assert stage_statuses(store, media.id)["visual"]["status"] == "succeeded"


@needs_ffmpeg
def test_cancelled_pipeline_is_never_recorded_as_succeeded(project, tmp_path):
    store, media, _ = project
    cancel = threading.Event()

    def on_progress(event):
        if event.stage == "render" and event.message and "démarrée" in event.message:
            cancel.set()

    with pytest.raises((PipelineCancelled, RuntimeError)):
        run_job(store, collect_job(store, media), destination=tmp_path / "x.mp4",
                cancel_event=cancel, on_progress=on_progress)
    assert store.list_tasks()[0]["status"] == "cancelled"
    assert not (tmp_path / "x.mp4").exists()


@needs_ffmpeg
def test_disabled_modules_are_skipped(project):
    store, media, _ = project
    for module in ("broll", "sfx", "masks"):
        store.set_module_enabled(module, False)
    job = collect_job(ProjectStore(store.root), media)
    assert job.broll == () and job.sfx == () and job.masks == ()


def test_missing_media_is_reported_clearly(tmp_path):
    store = ProjectStore.create(tmp_path / "p")

    class Info:
        duration, width, height, frame_rate, has_audio = 1.0, 16, 16, 25.0, False

    ghost = tmp_path / "ghost.mp4"
    ghost.write_bytes(b"x")
    media = store.add_media(ghost, Info)
    ghost.unlink()
    with pytest.raises(PipelineInputError, match="introuvable"):
        collect_job(store, media)
