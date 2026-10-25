import pytest
import threading

from cutweave.media.render import RenderError, VisualSettings, build_ass_content, build_render_command, build_visual_filter, render_visual, select_video_encoder
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.composition.broll import BrollPlacement, BrollRenderClip
from cutweave.stages.visual.framing import FramingKeyframe


@pytest.mark.parametrize("settings", [
    VisualSettings("9:16", 1080, 1920, 0.35),
    VisualSettings("16:9", 1920, 1080, 0),
    VisualSettings("1:1", 1080, 1080, 1.0),
])
def test_filtergraph_respects_format_and_optional_sharpen(settings):
    graph = build_visual_filter(settings)
    assert f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase" in graph
    assert f"crop={settings.width}:{settings.height}" in graph
    assert ("unsharp=" in graph) is (settings.sharpening > 0)


def test_animated_framing_builds_dynamic_scale_and_crop():
    settings = VisualSettings(
        "9:16", 90, 160, 0,
        (FramingKeyframe(0, .1, .5, 1), FramingKeyframe(1000, .9, .5, 1.5)),
    )
    graph = build_visual_filter(settings)
    assert "eval=frame" in graph
    assert "crop=90:160:x=" in graph
    assert "if(lt(t,1.000)" in graph
    assert "0.10000000+(0.80000000)*" in graph


def test_sharpening_controls_validate_and_threshold_uses_selective_graph(monkeypatch, tmp_path):
    settings = VisualSettings("16:9", 160, 90, 0.7, (), True, 7, 18)
    settings.validate()
    monkeypatch.setenv("CUTWEAVE_FFMPEG", "C:/tools/ffmpeg.exe")
    monkeypatch.setattr("cutweave.media.render._available_video_encoders", lambda _binary: {"mpeg4"})
    command = build_render_command(tmp_path / "source.mp4", tmp_path / "output.mp4", settings)
    graph = command[command.index("-filter_complex") + 1]
    assert "blend=all_expr='if(gt(abs(A-B),18),B,A)'" in graph
    assert "unsharp=7:7:0.700" in graph
    with pytest.raises(ValueError, match="impair"):
        VisualSettings("16:9", 160, 90, .5, (), True, 6, 0).validate()
    with pytest.raises(ValueError, match="seuil"):
        VisualSettings("16:9", 160, 90, .5, (), True, 5, 256).validate()


def test_rejects_inconsistent_aspect_ratio():
    with pytest.raises(ValueError, match="résolution"):
        build_visual_filter(VisualSettings("16:9", 1080, 1920))


def test_rejects_odd_dimensions_for_mp4_chroma():
    with pytest.raises(ValueError, match="paires"):
        build_visual_filter(VisualSettings("9:16", 91, 162))


def test_ffmpeg_command_is_an_argument_list_and_uses_local_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTWEAVE_FFMPEG", "C:/media tools/ffmpeg.exe")
    monkeypatch.setattr("cutweave.media.render._available_video_encoders", lambda binary: {"libopenh264", "aac"})
    command = build_render_command(tmp_path / "source clip.mp4", tmp_path / "output.mp4", VisualSettings())
    assert command[0] == "C:/media tools/ffmpeg.exe"
    assert command[command.index("-i") + 1].endswith("source clip.mp4")
    assert "-vf" in command
    assert command[command.index("-c:v") + 1] == "libopenh264"


def test_broll_builds_an_overlay_graph_and_keeps_source_audio_mapping(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTWEAVE_FFMPEG", "C:/media tools/ffmpeg.exe")
    monkeypatch.setattr("cutweave.media.render._available_video_encoders", lambda binary: {"libx264"})
    clip_path = tmp_path / "b roll.mp4"
    clip_path.touch()
    placement = BrollPlacement(1, 1250, 500, 2250, 2)
    command = build_render_command(
        tmp_path / "main.mp4", tmp_path / "output.mp4", VisualSettings("16:9", 160, 90, 0),
        broll=(BrollRenderClip(clip_path, placement),),
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "[1:v]trim=start=0.500:end=2.250" in graph
    assert "overlay=x=0:y=0:eof_action=pass:enable='between(t,1.250,3.000)'" in graph
    assert command[command.index("-map") + 1] == "[v0]"
    second_map = command.index("-map", command.index("-map") + 1)
    assert command[second_map + 1] == "0:a?"


def test_encoder_selection_prefers_h264_then_uses_mpeg4_fallback():
    assert select_video_encoder({"mpeg4", "h264_mf", "libopenh264"}) == "libopenh264"
    assert select_video_encoder({"mpeg4"}) == "mpeg4"
    with pytest.raises(RenderError, match="Aucun encodeur"):
        select_video_encoder({"vp9"})


def test_ass_export_has_deterministic_style_and_escaped_caption_markup():
    content = build_ass_content((CaptionCue(1250, 2175, "<b>Hello</b>\n{\\pos(1,2)}"),), VisualSettings())
    assert "PlayResX: 1080" in content
    assert "Dialogue: 0,0:00:01.25,0:00:02.18,Default" in content
    assert "{" + chr(92) + "b1}Hello{" + chr(92) + "b0}" in content
    assert chr(92) + "{" in content
    assert "pos(1,2)" in content


def test_caption_filter_only_accepts_generated_sidecar_names():
    graph = build_visual_filter(VisualSettings(), caption_filename="cutweave_0123456789abcdef0123456789abcdef.ass")
    assert "ass=filename='cutweave_0123456789abcdef0123456789abcdef.ass'" in graph
    with pytest.raises(ValueError, match="Nom de fichier"):
        build_visual_filter(VisualSettings(), caption_filename="C:/private captions.ass")


def test_cancelled_export_preserves_existing_output(tmp_path):
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"previous export")
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(RenderError, match="annul"):
        render_visual(tmp_path / "source.mp4", output, VisualSettings(), cancel_event=cancellation)
    assert output.read_bytes() == b"previous export"


def test_render_never_replaces_the_original_source(tmp_path):
    source = tmp_path / "rush.mp4"
    source.write_bytes(b"original rush")
    with pytest.raises(RenderError, match="source"):
        render_visual(source, source, VisualSettings())
    assert source.read_bytes() == b"original rush"

