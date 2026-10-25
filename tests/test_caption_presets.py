import pytest

from cutweave.media.probe import MediaInfo
from cutweave.media.render import VisualSettings, build_ass_content
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.finishing.caption_presets import (
    BUILTIN_CAPTION_PRESETS, CaptionPreset, CaptionPresetError,
)
from cutweave.storage.project_store import ProjectStore


def test_three_builtins_are_available_and_valid():
    assert [preset.name for preset in BUILTIN_CAPTION_PRESETS] == ["Clean", "Dynamic", "Cinematic"]
    for preset in BUILTIN_CAPTION_PRESETS:
        preset.validate()


def test_custom_preset_json_round_trip(tmp_path):
    preset = CaptionPreset("Studio", font_name="Georgia", font_size=52,
                           text_color="#EEDDBB", background_opacity=100,
                           animation="fade", animation_ms=250)
    path = preset.save(tmp_path / "studio.json")
    assert CaptionPreset.load(path) == preset
    assert '"schema_version": 1' in path.read_text(encoding="utf-8")


def test_rejects_invalid_caption_style_fields():
    with pytest.raises(CaptionPresetError, match="Couleur"):
        CaptionPreset("Bad", text_color="red").validate()
    with pytest.raises(CaptionPresetError, match="alignement"):
        CaptionPreset("Bad", alignment=10).validate()


def test_dynamic_and_cinematic_styles_render_to_ass():
    cue = CaptionCue(1000, 1800, "Move!")
    dynamic = next(preset for preset in BUILTIN_CAPTION_PRESETS if preset.name == "Dynamic")
    cinematic = next(preset for preset in BUILTIN_CAPTION_PRESETS if preset.name == "Cinematic")
    dynamic_ass = build_ass_content((cue,), VisualSettings("16:9", 1920, 1080, 0), dynamic)
    cinematic_ass = build_ass_content((cue,), VisualSettings("16:9", 1920, 1080, 0), cinematic)
    assert "Style: Default,Arial,58" in dynamic_ass
    assert r"{\1c&H00FFE500&\fscx75\fscy75\t(0,150,\fscx100\fscy100)}Move!" in dynamic_ass
    assert "Style: Default,Georgia,44" in cinematic_ass
    assert ",3,1.20,0.80,2," in cinematic_ass
    assert r"{\fad(220,220)}Move!" in cinematic_ass


def test_project_persists_custom_and_per_video_caption_presets(tmp_path):
    store = ProjectStore.create(tmp_path / "project")
    video = tmp_path / "project" / "clip.mp4"
    video.touch()
    media = store.add_media(video, MediaInfo(5, 320, 180, 25, False, "h264", None))
    second_video = tmp_path / "project" / "clip2.mp4"
    second_video.touch()
    second_media = store.add_media(second_video, MediaInfo(5, 320, 180, 25, False, "h264", None))
    preset = CaptionPreset("My Style", text_color="#FFEEDD", animation="fade")
    store.save_custom_caption_preset(preset)
    store.set_caption_preset(media.id, preset)
    store.set_caption_preset(second_media.id, preset)
    updated = CaptionPreset("My Style", text_color="#AACCEE", animation="pop")
    store.save_custom_caption_preset(updated)

    reopened = ProjectStore(tmp_path / "project")
    assert reopened.list_custom_caption_presets() == (updated,)
    assert reopened.get_caption_preset(media.id) == updated
    assert reopened.get_caption_preset(second_media.id) == updated
