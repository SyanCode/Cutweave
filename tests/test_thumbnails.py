import subprocess
import threading

import pytest

from cutweave.media.probe import ffprobe_path
from cutweave.media.render import ffmpeg_path
from cutweave.media.thumbnails import ThumbnailError, create_video_thumbnail


@pytest.mark.skipif(not ffmpeg_path() or not ffprobe_path(), reason="FFmpeg/FFprobe non installés")
def test_video_thumbnail_is_generated_and_decodable(tmp_path):
    ffmpeg = ffmpeg_path()
    ffprobe = ffprobe_path()
    source = tmp_path / "source.mp4"
    output = tmp_path / "cache" / "thumbnail.jpg"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=320x180:rate=25:duration=1", "-c:v", "mpeg4", str(source)],
        check=True, capture_output=True,
    )

    result = create_video_thumbnail(source, output, time_ms=500, width=160, height=90)
    metadata = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", str(result)], check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert output.is_file() and output.stat().st_size > 100
    assert metadata == "160x90"


def test_thumbnail_rejects_missing_source_and_pre_cancelled_task(tmp_path):
    with pytest.raises(ThumbnailError, match="introuvable"):
        create_video_thumbnail(tmp_path / "missing.mp4", tmp_path / "thumb.jpg")

    source = tmp_path / "source.mp4"
    source.write_bytes(b"placeholder")
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ThumbnailError, match="annulée"):
        create_video_thumbnail(source, tmp_path / "thumb.jpg", cancel_event=cancelled)
