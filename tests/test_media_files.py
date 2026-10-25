from cutweave.media.files import collect_video_files


def test_collect_video_files_recurses_deduplicates_and_sorts(tmp_path):
    folder = tmp_path / "media"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    first = folder / "Clip.MP4"
    second = nested / "other.mkv"
    ignored = nested / "notes.txt"
    first.touch()
    second.touch()
    ignored.touch()

    assert collect_video_files([folder, first]) == [first.resolve(), second.resolve()]


def test_collect_video_files_rejects_missing_paths(tmp_path):
    missing = tmp_path / "missing"

    try:
        collect_video_files([missing])
    except FileNotFoundError as error:
        assert str(missing) in str(error)
    else:
        raise AssertionError("A missing selected path must be reported.")
