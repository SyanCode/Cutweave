import pytest

from cutweave.media.probe import AudioInfo, MediaInfo
from cutweave.media.render import VisualSettings, build_render_command
from cutweave.stages.finishing.music import (
    DuckingSegment, MusicSettings, MusicValidationError, MusicRenderTrack, parse_audio_tags,
    parse_ducking_segments, format_ducking_segments,
)
from cutweave.storage.project_store import ProjectStore


def test_audio_tags_are_trimmed_and_deduplicated():
    assert parse_audio_tags("music, calm, music, , ambient ") == ("music", "calm", "ambient")


def test_manual_ducking_segments_parse_format_validate_and_reject_overlap():
    segments = parse_ducking_segments("1000,2200,0.2,100; 4000,5000,0.35")
    assert segments == (DuckingSegment(1000, 2200, 0.2, 100), DuckingSegment(4000, 5000, 0.35, 80))
    assert parse_ducking_segments(format_ducking_segments(segments)) == segments
    with pytest.raises(MusicValidationError, match="chevaucher"):
        MusicSettings(1, source_end_ms=10000, ducking_segments=(
            DuckingSegment(1000, 3000), DuckingSegment(2500, 4000),
        )).validate()
    with pytest.raises(MusicValidationError, match="Format ducking"):
        parse_ducking_segments("1,2")


@pytest.mark.parametrize("settings", [
    MusicSettings(0, source_end_ms=100),
    MusicSettings(1, timeline_start_ms=-1, source_end_ms=100),
    MusicSettings(1, source_start_ms=100, source_end_ms=100),
    MusicSettings(1, source_end_ms=100, volume=4.1),
    MusicSettings(1, source_end_ms=100, fade_in_ms=80, fade_out_ms=30),
])
def test_invalid_music_settings_are_rejected(settings):
    with pytest.raises(MusicValidationError):
        settings.validate(100)


def test_music_loop_and_fades_are_checked_against_video_play_duration():
    settings = MusicSettings(1, timeline_start_ms=1000, source_end_ms=300, fade_out_ms=1000, loop=True)
    settings.validate(300, play_duration_ms=9000)


def test_music_library_mix_persists_and_validates(tmp_path):
    root = tmp_path / "project"
    store = ProjectStore.create(root)
    video = root / "main.mp4"
    audio = root / "bed.wav"
    video.touch(); audio.touch()
    media = store.add_media(video, MediaInfo(8, 320, 180, 25, True, "h264", "aac"))
    asset = store.add_audio_asset(audio, AudioInfo(3, 48000, 2, "pcm_s16le"), tags=("calm",))
    settings = MusicSettings(asset.id, 500, 250, 2500, 0.3, 200, 300, True, False,
                             (DuckingSegment(1000, 3000, 0.18, 120),))
    store.set_music_mix(media.id, settings)

    reopened = ProjectStore(root)
    assert reopened.list_audio_assets("CALM") == (asset,)
    assert reopened.get_music_mix(media.id) == settings
    with pytest.raises(MusicValidationError, match="dépasse la durée"):
        reopened.set_music_mix(media.id, MusicSettings(
            asset.id, source_end_ms=2500, ducking_segments=(DuckingSegment(7000, 9000, 0.2, 100),),
        ))
    with pytest.raises(MusicValidationError, match="fondus"):
        reopened.set_music_mix(media.id, MusicSettings(asset.id, 0, 0, 2500, 0.3, 5000, 4000, True))


def test_existing_projects_gain_ducking_column_on_open(tmp_path):
    root = tmp_path / "project"
    store = ProjectStore.create(root)
    with store._connect() as connection:
        connection.execute("ALTER TABLE music_mixes DROP COLUMN ducking_json")
    reopened = ProjectStore(root)
    with reopened._connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(music_mixes)")}
    assert "ducking_json" in columns


def test_music_render_command_loops_selected_range_and_mixes_source(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTWEAVE_FFMPEG", "C:/media tools/ffmpeg.exe")
    monkeypatch.setattr("cutweave.media.render._available_video_encoders", lambda binary: {"libx264"})
    music_path = tmp_path / "music bed.wav"
    music_path.touch()
    settings = MusicSettings(1, 500, 250, 1250, 0.2, 100, 200, True, True,
                             (DuckingSegment(1000, 3000, 0.18, 120),))
    command = build_render_command(
        tmp_path / "main.mp4", tmp_path / "out.mp4", VisualSettings("16:9", 160, 90, 0),
        music=MusicRenderTrack(music_path, settings, timeline_duration_ms=5000), source_has_audio=True,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "[1:a]atrim=start=0.250:end=1.250" in graph
    assert "aloop=loop=-1:size=48000" in graph
    assert "adelay=500:all=1" in graph
    assert "[main_a][music_a]amix=inputs=2" in graph
    assert "volume='if(lt(t\\,1.000)" in graph
    assert "0.1800" in graph
    assert "[audio_out]" in command


def test_music_only_command_does_not_map_missing_source_audio(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTWEAVE_FFMPEG", "C:/media tools/ffmpeg.exe")
    monkeypatch.setattr("cutweave.media.render._available_video_encoders", lambda binary: {"libx264"})
    audio = tmp_path / "music.wav"; audio.touch()
    command = build_render_command(
        tmp_path / "silent.mp4", tmp_path / "out.mp4", VisualSettings("16:9", 160, 90, 0),
        music=MusicRenderTrack(audio, MusicSettings(1, source_end_ms=1000), 2000), source_has_audio=False,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "[0:a]" not in graph
    assert "[music_a]amix=inputs=1:duration=longest" in graph
