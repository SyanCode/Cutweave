import subprocess

import pytest

from cutweave.media.probe import ffprobe_path, probe_media
from cutweave.media.render import VisualSettings, extract_preview_frame, ffmpeg_path, render_visual
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.composition.masking import MaskKeyframe, MaskRegion
from cutweave.stages.composition.broll import BrollPlacement, BrollRenderClip
from cutweave.stages.finishing.music import DuckingSegment, MusicRenderTrack, MusicSettings, SfxPlacement, SfxRenderClip
from cutweave.stages.finishing.caption_presets import BUILTIN_BY_NAME
from cutweave.media.probe import probe_audio
from cutweave.stages.visual.framing import FramingKeyframe


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_synthetic_clip_is_probed_and_rendered_with_audio(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "source.mp4"
    output = tmp_path / "render.mp4"
    plain_output = tmp_path / "plain.mp4"
    output.write_bytes(b"previous output is replaced only after a successful encode")
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=320x240:rate=25", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=1", "-t", "1", "-c:v", "mpeg4",
         "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True,
    )

    source_info = probe_media(source)
    result = render_visual(
        source, output, VisualSettings("9:16", 90, 160, 0.2),
        captions=(CaptionCue(100, 800, "Synthetic subtitle"),),
        caption_preset=BUILTIN_BY_NAME["dynamic"],
        masks=(MaskRegion(20, 40, 30, 40, 1.0),),
    )
    output_info = probe_media(result)
    plain_result = render_visual(source, plain_output, VisualSettings("9:16", 90, 160, 0.2))

    def extract_frame(path):
        return subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.5", "-i", str(path),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True,
        ).stdout

    assert source_info.width == 320 and source_info.height == 240
    assert source_info.has_audio is True
    assert (output_info.width, output_info.height) == (90, 160)
    assert output_info.has_audio is True
    assert output.stat().st_size > 0
    caption_frame = extract_frame(result)
    plain_frame = extract_frame(plain_result)
    assert sum(a != b for a, b in zip(caption_frame, plain_frame)) > 1000
    mask_pixel = (60 * 90 + 35) * 3
    assert max(caption_frame[mask_pixel:mask_pixel + 3]) <= 8
    assert not list(tmp_path.glob("cutweave_*.ass"))
    assert not list(tmp_path.glob(".*.part.mp4"))


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_broll_is_inserted_at_its_timeline_position(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "blue.mp4"
    overlay = tmp_path / "red.mp4"
    output = tmp_path / "with_broll.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=blue:size=160x90:rate=25:duration=1", "-c:v", "mpeg4", str(source)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:size=160x90:rate=25:duration=0.5", "-c:v", "mpeg4", str(overlay)],
        check=True, capture_output=True,
    )
    render_visual(
        source, output, VisualSettings("16:9", 160, 90, 0),
        broll=(BrollRenderClip(overlay, BrollPlacement(1, 250, 0, 500)),),
        masks=(MaskRegion(5, 5, 20, 20, 1.0), MaskRegion(120, 30, 20, 20, effect="blur", blur_radius=5)),
    )

    def center_pixel(timestamp):
        raw = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(timestamp), "-i", str(output),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True,
        ).stdout
        index = (45 * 160 + 80) * 3
        return tuple(raw[index:index + 3])

    before = center_pixel(0.1)
    during = center_pixel(0.4)
    after = center_pixel(0.9)
    assert before[2] > before[0] * 2
    assert during[0] > during[2] * 2
    assert after[2] > after[0] * 2
    raw_during = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.4", "-i", str(output),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True, capture_output=True,
    ).stdout
    masked_pixel = (10 * 160 + 10) * 3
    assert max(raw_during[masked_pixel:masked_pixel + 3]) <= 8


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_music_is_mixed_looped_and_exported_with_video_duration(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "voice.mp4"
    music_file = tmp_path / "music.wav"
    output = tmp_path / "mixed.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x90:rate=25:duration=1", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=1", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=880:sample_rate=48000:duration=0.2", "-c:a", "pcm_s16le", str(music_file)],
        check=True, capture_output=True,
    )
    audio_info = probe_audio(music_file)
    assert audio_info.duration == pytest.approx(0.2, abs=0.02)
    assert audio_info.sample_rate == 48000
    settings = MusicSettings(1, 0, 0, 180, 0.5, 0, 0, True, True)
    render_visual(
        source, output, VisualSettings("16:9", 160, 90, 0),
        music=MusicRenderTrack(music_file, settings, 1000), source_has_audio=True,
    )
    result_info = probe_media(output)
    assert result_info.has_audio is True
    assert result_info.duration == pytest.approx(1.0, abs=0.08)

    audio = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.35", "-t", "0.3", "-i", str(output),
         "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"],
        check=True, capture_output=True,
    ).stdout
    import math
    import struct
    samples = struct.unpack(f"<{len(audio) // 4}f", audio)
    def amplitude_at(frequency):
        real = sum(sample * math.cos(2 * math.pi * frequency * index / 48000) for index, sample in enumerate(samples))
        imag = sum(sample * math.sin(2 * math.pi * frequency * index / 48000) for index, sample in enumerate(samples))
        return math.hypot(real, imag) / max(1, len(samples))
    assert amplitude_at(440) > 0.02
    assert amplitude_at(880) > 0.005


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_sfx_is_scheduled_at_manual_timeline_timestamp(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "silent.mp4"
    effect = tmp_path / "effect.wav"
    output = tmp_path / "with_sfx.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:size=160x90:rate=25:duration=1", "-c:v", "mpeg4", str(source)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=990:sample_rate=48000:duration=0.2", "-c:a", "pcm_s16le", str(effect)],
        check=True, capture_output=True,
    )
    render_visual(
        source, output, VisualSettings("16:9", 160, 90, 0),
        sfx=(SfxRenderClip(effect, SfxPlacement(1, 300, 0, 200, 1.0, 0, 0, "manual:marker"), 1000),),
        source_has_audio=False,
    )
    assert probe_media(output).has_audio is True

    def rms(start):
        raw = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", "0.1", "-i", str(output),
             "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"],
            check=True, capture_output=True,
        ).stdout
        import math
        import struct
        values = struct.unpack(f"<{len(raw) // 4}f", raw)
        return math.sqrt(sum(value * value for value in values) / max(1, len(values)))

    assert rms(0.1) < 0.001
    assert rms(0.35) > 0.02


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_manual_ducking_attenuates_music_during_configured_voice_interval(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "voice.mp4"
    music_file = tmp_path / "music.wav"
    output = tmp_path / "ducked.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x90:rate=25:duration=1", "-f", "lavfi", "-i",
         "anullsrc=r=48000:cl=stereo", "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=880:sample_rate=48000:duration=1", "-c:a", "pcm_s16le", str(music_file)],
        check=True, capture_output=True,
    )
    render_visual(
        source, output, VisualSettings("16:9", 160, 90, 0),
        music=MusicRenderTrack(
            music_file,
            MusicSettings(1, 0, 0, 1000, 1.0, 0, 0, False, True,
                          (DuckingSegment(300, 700, 0.1, 100),)),
            1000,
        ),
        source_has_audio=True,
    )

    def rms(start):
        raw = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", "0.1", "-i", str(output),
             "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"],
            check=True, capture_output=True,
        ).stdout
        import math
        import struct
        values = struct.unpack(f"<{len(raw) // 4}f", raw)
        return math.sqrt(sum(value * value for value in values) / max(1, len(values)))

    assert rms(0.1) > rms(0.45) * 1.8


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_ellipse_and_polygon_masks_have_distinct_geometric_coverage(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "red.mp4"
    output = tmp_path / "shapes.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:size=160x90:rate=25:duration=1", "-c:v", "mpeg4", str(source)],
        check=True, capture_output=True,
    )
    render_visual(
        source, output, VisualSettings("16:9", 160, 90, 0),
        masks=(
            MaskRegion(10, 10, 50, 50, 1, start_ms=500, end_ms=900, shape="ellipse"),
            MaskRegion(80, 10, 50, 50, 1, shape="polygon", points=((80, 10), (130, 10), (105, 60))),
        ),
    )
    raw = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(output), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], check=True, capture_output=True,
    ).stdout

    def pixel(x, y):
        index = (y * 160 + x) * 3
        return tuple(raw[index:index + 3])

    assert pixel(35, 35)[0] > 150  # ellipse is inactive before its start timestamp
    assert pixel(10, 10)[0] > 150  # corner outside ellipse
    assert max(pixel(100, 20)) < 20  # inside triangle
    assert pixel(82, 55)[0] > 150  # outside triangle
    late_raw = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.7", "-i", str(output),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True, capture_output=True,
    ).stdout
    center = (35 * 160 + 35) * 3
    assert max(late_raw[center:center + 3]) < 20


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_mask_keyframes_interpolate_geometry_over_time(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "red.mp4"
    output = tmp_path / "animated_mask.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:size=160x90:rate=25:duration=1", "-c:v", "mpeg4", str(source)],
        check=True, capture_output=True,
    )
    render_visual(
        source, output, VisualSettings("16:9", 160, 90, 0),
        masks=(MaskRegion(10, 10, 20, 20, keyframes=(
            MaskKeyframe(0, 10, 10, 20, 20, 1),
            MaskKeyframe(900, 100, 10, 20, 20, 1),
        )),),
    )

    def pixel(timestamp, x, y):
        raw = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(timestamp), "-i", str(output),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True,
        ).stdout
        index = (y * 160 + x) * 3
        return tuple(raw[index:index + 3])

    assert max(pixel(0.2, 40, 20)) < 20
    assert pixel(0.2, 110, 20)[0] > 150
    assert pixel(0.7, 40, 20)[0] > 150
    assert max(pixel(0.7, 90, 20)) < 20


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_local_blur_mask_changes_only_the_selected_region(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "pattern.mp4"
    plain = tmp_path / "plain.mp4"
    blurred = tmp_path / "blurred.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x90:rate=25:duration=1", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=1", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True,
    )
    render_visual(source, plain, VisualSettings("16:9", 160, 90, 0))
    render_visual(
        source, blurred, VisualSettings("16:9", 160, 90, 0),
        masks=(MaskRegion(40, 20, 50, 50, 1, start_ms=250, end_ms=750, effect="blur", blur_radius=10),),
    )

    def frame(path):
        return subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.5", "-i", str(path),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True,
        ).stdout

    original, result = frame(plain), frame(blurred)
    def differences(bounds):
        x0, y0, x1, y1 = bounds
        return sum(original[(y * 160 + x) * 3 + channel] != result[(y * 160 + x) * 3 + channel]
                   for y in range(y0, y1) for x in range(x0, x1) for channel in range(3))
    assert differences((45, 25, 85, 65)) > 100
    assert differences((0, 0, 30, 15)) == 0
    assert probe_media(blurred).has_audio is True


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_animated_framing_moves_crop_across_source(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "framing-source.mp4"
    output = tmp_path / "framing-output.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=blue:size=320x180:rate=25:duration=1,drawbox=x=0:y=40:w=110:h=100:color=red:t=fill,"
         "drawbox=x=210:y=40:w=110:h=100:color=green:t=fill", "-c:v", "mpeg4", str(source)],
        check=True, capture_output=True,
    )
    settings = VisualSettings(
        "9:16", 90, 160, 0,
        (FramingKeyframe(0, .1, .5), FramingKeyframe(900, .9, .5)),
    )
    render_visual(source, output, settings)

    def center_pixel(timestamp):
        data = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(timestamp), "-i", str(output),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True,
        ).stdout
        return tuple(data[(80 * 90 + 45) * 3:(80 * 90 + 45) * 3 + 3])

    early = center_pixel(.08)
    late = center_pixel(.82)
    assert early[0] > early[1] * 1.5
    assert late[1] > late[0] * 1.5


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_sharpening_threshold_renders_and_preview_compares(tmp_path):
    ffmpeg = ffmpeg_path()
    source = tmp_path / "sharpen-source.mp4"
    output = tmp_path / "sharpen-output.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x90:rate=25:duration=1", "-c:v", "mpeg4", str(source)],
        check=True, capture_output=True,
    )
    settings = VisualSettings("16:9", 160, 90, .8, (), True, 7, 12)
    render_visual(source, output, settings)
    before = extract_preview_frame(source, settings, 500, sharpening=False)
    after = extract_preview_frame(source, settings, 500, sharpening=True)

    assert output.is_file() and probe_media(output).width == 160
    assert before.startswith(b"\x89PNG\r\n\x1a\n")
    assert after.startswith(b"\x89PNG\r\n\x1a\n")
    assert before != after
