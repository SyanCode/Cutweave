import pytest

from cutweave.media.probe import AudioInfo, MediaInfo
from cutweave.media.render import VisualSettings, build_render_command
from cutweave.stages.finishing.music import SfxPlacement, SfxRenderClip, SfxValidationError
from cutweave.storage.project_store import ProjectStore


@pytest.mark.parametrize("placement", [
    SfxPlacement(0, 0, 0, 100),
    SfxPlacement(1, -1, 0, 100),
    SfxPlacement(1, 0, 0, 100, volume=4.1),
    SfxPlacement(1, 0, 0, 100, fade_in_ms=80, fade_out_ms=30),
])
def test_invalid_sfx_placements_are_rejected(placement):
    with pytest.raises(SfxValidationError):
        placement.validate(100)


def test_sfx_timeline_and_event_marker_persist(tmp_path):
    root = tmp_path / "project"
    store = ProjectStore.create(root)
    video, audio = root / "main.mp4", root / "click.wav"
    video.touch(); audio.touch()
    media = store.add_media(video, MediaInfo(5, 320, 180, 25, False, "h264", None))
    asset = store.add_audio_asset(audio, AudioInfo(1, 48000, 1, "pcm_s16le"), category="sfx", tags=("click",))
    cue = SfxPlacement(asset.id, 1250, 100, 900, 0.7, 50, 100, "manual:cut_01")
    store.set_sfx_placements(media.id, (cue,))

    reopened = ProjectStore(root)
    assert reopened.get_sfx_placements(media.id) == (cue,)
    with pytest.raises(SfxValidationError, match="dépasse"):
        reopened.set_sfx_placements(media.id, (SfxPlacement(asset.id, 0, 0, 1500),))


def test_sfx_render_command_maps_effect_without_source_audio(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTWEAVE_FFMPEG", "C:/media tools/ffmpeg.exe")
    monkeypatch.setattr("cutweave.media.render._available_video_encoders", lambda binary: {"libx264"})
    path = tmp_path / "effect.wav"; path.touch()
    sfx = SfxRenderClip(path, SfxPlacement(2, 300, 100, 600, 0.8, 50, 100, "marker"), 2000)
    command = build_render_command(
        tmp_path / "silent.mp4", tmp_path / "out.mp4", VisualSettings("16:9", 160, 90, 0),
        sfx=(sfx,), source_has_audio=False,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "[1:a]atrim=start=0.100:end=0.600" in graph
    assert "volume=0.8000" in graph
    assert "afade=t=in:st=0:d=0.050" in graph
    assert "afade=t=out:st=0.400:d=0.100" in graph
    assert "adelay=300:all=1" in graph
    assert "[sfx_0]amix=inputs=1:duration=longest" in graph
    assert "[0:a]" not in graph
