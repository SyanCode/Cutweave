import threading

import pytest

from cutweave.core.cache import CacheCancelled, CacheError, StageCache


def test_cache_key_tracks_input_content_parameters_and_module_version(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"source-v1")
    cache = StageCache(tmp_path / "cache")
    key = cache.make_key("render", (("source", source),), {"quality": 20}, module_version="v1")
    assert key != cache.make_key("render", (("source", source),), {"quality": 21}, module_version="v1")
    assert key != cache.make_key("render", (("source", source),), {"quality": 20}, module_version="v2")
    source.write_bytes(b"source-v2")
    assert key != cache.make_key("render", (("source", source),), {"quality": 20}, module_version="v1")


def test_cache_artifacts_copy_atomically_and_purge_only_reserved_namespaces(tmp_path):
    cache = StageCache(tmp_path / "project" / ".cutweave_cache")
    key = "a" * 64
    created = tmp_path / "render.mp4"
    created.write_bytes(b"verified artifact")
    stored = cache.publish(created, "render", key, ".mp4")
    destination = tmp_path / "exports" / "result.mp4"
    destination.parent.mkdir()
    destination.write_bytes(b"old destination")
    assert cache.copy_to(stored, destination) == destination.resolve()
    assert destination.read_bytes() == b"verified artifact"
    other = cache.root / "other" / "keep.txt"
    other.parent.mkdir(parents=True)
    other.write_text("keep")

    assert cache.purge() == 1
    assert not stored.exists()
    assert other.read_text() == "keep"


def test_cache_refuses_path_traversal_and_empty_artifacts(tmp_path):
    cache = StageCache(tmp_path / "cache")
    with pytest.raises(CacheError):
        cache.artifact_path("../outside", "a" * 64, ".mp4")
    with pytest.raises(CacheError):
        cache.artifact_path("render", "../outside", ".mp4")
    empty = tmp_path / "empty.json"
    empty.touch()
    with pytest.raises(CacheError, match="vide"):
        cache.publish(empty, "tracking", "b" * 64, ".json")


def test_cache_hashing_can_be_cancelled(tmp_path):
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * 1024)
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(CacheCancelled):
        StageCache(tmp_path / "cache").make_key(
            "tracking", (("source", source),), {}, module_version="v1", cancel_event=cancellation,
        )


def test_get_or_create_reuses_nonempty_artifact_and_copies_destination(tmp_path):
    cache = StageCache(tmp_path / "cache")
    destination = tmp_path / "exports" / "final.mp4"
    produced = []

    def producer(path):
        produced.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"rendered")

    artifact, hit = cache.get_or_create("render", "c" * 64, ".mp4", producer, destination=destination)
    assert not hit
    assert artifact.read_bytes() == b"rendered"
    assert destination.read_bytes() == b"rendered"

    destination.write_bytes(b"replace me")
    reused, hit = cache.get_or_create("render", "c" * 64, ".mp4", producer, destination=destination)
    assert hit
    assert reused == artifact
    assert destination.read_bytes() == b"rendered"
    assert len(produced) == 1
