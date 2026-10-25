import pytest

from cutweave.media.probe import MediaInfo
from cutweave.stages.composition.broll import BrollPlacement, BrollValidationError, parse_tags
from cutweave.storage.project_store import ProjectStore


def test_tags_are_trimmed_and_deduplicated():
    assert parse_tags("street, city, street, , night ") == ("street", "city", "night")


@pytest.mark.parametrize("placement", [
    BrollPlacement(0, 0, 0, 100),
    BrollPlacement(1, -1, 0, 100),
    BrollPlacement(1, 0, 100, 100),
    BrollPlacement(1, 0, 0, 100, -1),
])
def test_invalid_placement_is_rejected(placement):
    with pytest.raises(BrollValidationError):
        placement.validate()


def test_library_search_and_placements_persist(tmp_path):
    root = tmp_path / "project"
    store = ProjectStore.create(root)
    main_path = root / "main.mp4"
    broll_path = root / "broll.mp4"
    main_path.touch()
    broll_path.touch()
    main = store.add_media(main_path, MediaInfo(10, 640, 360, 30, False, "h264", None))
    asset = store.add_broll_asset(
        broll_path, MediaInfo(4, 320, 240, 25, False, "h264", None),
        category="travel", tags=("street", "night"),
    )
    insert = BrollPlacement(asset.id, 2500, 500, 2500, 1)
    store.set_broll_placements(main.id, (insert,))

    reopened = ProjectStore(root)
    assert reopened.list_broll_assets("NIGHT") == (asset,)
    assert reopened.get_broll_placements(main.id) == (insert,)
    with pytest.raises(BrollValidationError, match="dépasse"):
        reopened.set_broll_placements(main.id, (BrollPlacement(asset.id, 0, 0, 5000),))

