from pathlib import Path
from tempfile import TemporaryDirectory

from cutweave.media.probe import MediaInfo
from cutweave.media.render import VisualSettings
from cutweave.storage.project_store import ProjectStore
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.composition.masking import MaskKeyframe, MaskRegion
from cutweave.stages.visual.framing import FramingKeyframe


def test_project_reopens_and_tracks_relative_media(tmp_path):
    project_root = tmp_path / "project"
    store = ProjectStore.create(project_root, "Demo")
    source = project_root / "assets" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"synthetic placeholder")

    item = store.add_media(source, MediaInfo(3.25, 640, 360, 30.0, False, "h264", None))
    reopened = ProjectStore(project_root)
    listed = reopened.list_media()

    assert reopened.manifest["name"] == "Demo"
    assert item.path == source.resolve()
    assert listed[0].path == source.resolve()
    assert listed[0].duration == 3.25
    assert listed[0].missing is False
    assert str(source.resolve()) not in (project_root / "cutweave.sqlite3").read_bytes().decode("latin1")


def test_project_persists_framing_keyframes(tmp_path):
    root = tmp_path / "project"
    store = ProjectStore.create(root)
    source = root / "clip.mp4"
    source.write_bytes(b"placeholder")
    media = store.add_media(source, MediaInfo(2.0, 640, 360, 30.0, False, "h264", None))
    frames = (FramingKeyframe(0, .2, .4, 1.0), FramingKeyframe(1500, .8, .6, 1.5))
    store.set_framing_keyframes(media.id, frames)

    assert ProjectStore(root).get_framing_keyframes(media.id) == frames


def test_project_persists_visual_settings_per_media(tmp_path):
    root = tmp_path / "project"
    store = ProjectStore.create(root)
    source = root / "clip.mp4"
    source.write_bytes(b"placeholder")
    media = store.add_media(source, MediaInfo(2.0, 640, 360, 30.0, False, "h264", None))
    settings = VisualSettings("16:9", 1920, 1080, .65, (), True, 9, 22)
    store.set_visual_settings(media.id, settings)

    assert ProjectStore(root).get_visual_settings(media.id) == settings


def test_visual_settings_default_for_existing_media_without_customization(tmp_path):
    store = ProjectStore.create(tmp_path / "project")
    source = store.root / "clip.mp4"
    source.write_bytes(b"placeholder")
    media = store.add_media(source, MediaInfo(2.0, 640, 360, 30.0, False, "h264", None))

    assert store.get_visual_settings(media.id) == VisualSettings()


def test_project_records_task_completion(tmp_path):
    store = ProjectStore.create(tmp_path / "project")
    task_id = store.record_task("visual_export", "running", input_path="in.mp4")
    store.record_task("visual_export", "succeeded", output_path=Path("out.mp4"), task_id=task_id)
    with store._connect() as connection:
        row = connection.execute("SELECT status, output_path, finished_at FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert row["status"] == "succeeded"
    assert row["output_path"] == "out.mp4"
    assert row["finished_at"]


def test_project_database_handles_release_for_windows_file_operations():
    with TemporaryDirectory() as temporary:
        store = ProjectStore.create(Path(temporary) / "project")
        store.list_media()
        store.list_broll_assets()
        store.list_audio_assets()
    assert not Path(temporary).exists()


def test_project_saves_and_reopens_caption_cues(tmp_path):
    project_root = tmp_path / "project"
    store = ProjectStore.create(project_root)
    source = project_root / "clip.mp4"
    source.touch()
    media = store.add_media(source, MediaInfo(5, 320, 240, 25, False, "mpeg4", None))
    cues = (CaptionCue(100, 800, "Première ligne"), CaptionCue(900, 1400, "Deuxième ligne"))
    store.set_captions(media.id, cues)

    assert ProjectStore(project_root).get_captions(media.id) == cues


def test_project_saves_and_reopens_masks(tmp_path):
    project_root = tmp_path / "project"
    store = ProjectStore.create(project_root)
    source = project_root / "clip.mp4"
    source.touch()
    media = store.add_media(source, MediaInfo(5, 320, 240, 25, False, "mpeg4", None))
    masks = (
        MaskRegion(20, 40, 120, 80, 0.75, 500, 1500, keyframes=(
            MaskKeyframe(0, 20, 40, 120, 80, 0.75),
            MaskKeyframe(1500, 40, 50, 100, 60, 1.0),
        )),
        MaskRegion(30, 50, 60, 40, 1, shape="ellipse", effect="blur", blur_radius=8),
        MaskRegion(10, 10, 100, 80, 0.6, shape="polygon", points=((10, 10), (110, 10), (60, 90))),
    )
    store.set_masks(media.id, masks)

    assert ProjectStore(project_root).get_masks(media.id) == masks


def test_pre_shape_projects_migrate_mask_columns_on_open(tmp_path):
    root = tmp_path / "project"
    store = ProjectStore.create(root)
    with store._connect() as connection:
        connection.execute("ALTER TABLE mask_regions DROP COLUMN keyframes_json")
        connection.execute("ALTER TABLE mask_regions DROP COLUMN blur_radius")
        connection.execute("ALTER TABLE mask_regions DROP COLUMN effect")
        connection.execute("ALTER TABLE mask_regions DROP COLUMN points_json")
        connection.execute("ALTER TABLE mask_regions DROP COLUMN shape")
    reopened = ProjectStore(root)
    with reopened._connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(mask_regions)")}
    assert {"shape", "points_json", "keyframes_json", "effect", "blur_radius"} <= columns

