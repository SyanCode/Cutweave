from cutweave.media.probe import parse_fraction, parse_probe


def test_parse_fraction_handles_missing_and_invalid_values():
    assert parse_fraction("30000/1001") == 30000 / 1001
    assert parse_fraction("0/0") is None
    assert parse_fraction("invalid") is None


def test_parse_probe_extracts_video_and_audio_metadata():
    info = parse_probe({
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "25/1"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    })
    assert info.duration == 12.5
    assert (info.width, info.height, info.frame_rate) == (1920, 1080, 25)
    assert info.has_audio is True
    assert info.resolution == "1920 × 1080"


def test_parse_probe_rejects_audio_only_files():
    import pytest
    from cutweave.media.probe import MediaProbeError

    with pytest.raises(MediaProbeError, match="flux vidéo"):
        parse_probe({"streams": [{"codec_type": "audio", "codec_name": "aac"}]})

