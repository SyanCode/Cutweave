"""Format de projet versionné avec manifeste JSON et métadonnées SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from cutweave.media.render import VisualSettings
from cutweave.stages.composition.captions import CaptionCue
from cutweave.stages.composition.broll import BrollAsset, BrollPlacement, BrollValidationError
from cutweave.stages.composition.masking import MaskKeyframe, MaskRegion
from cutweave.stages.visual.framing import FramingKeyframe, validate_keyframes
from cutweave.stages.finishing.music import (
    AudioAsset, DuckingSegment, MusicSettings, MusicValidationError, SfxPlacement, SfxValidationError,
)
from cutweave.stages.finishing.caption_presets import (
    BUILTIN_BY_NAME, CaptionPreset, CaptionPresetError,
)
from cutweave.stages.composition.broll_rules import BrollRule
from cutweave.stages.finishing.sfx_rules import SfxRule, TimelineMarker

PROJECT_FILE = "project.json"
DATABASE_FILE = "cutweave.sqlite3"
SCHEMA_VERSION = 1
MODULES = ("framing", "sharpening", "captions", "masks", "broll", "music", "sfx")


@dataclass(frozen=True)
class ProjectMedia:
    id: int
    path: Path
    name: str
    duration: float | None
    width: int | None
    height: int | None
    frame_rate: float | None
    has_audio: bool | None

    @property
    def missing(self) -> bool:
        return not self.path.is_file()


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.manifest_path = self.root / PROJECT_FILE
        self.database_path = self.root / DATABASE_FILE
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"Projet CUTWEAVE introuvable : {self.manifest_path}")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Version de projet non prise en charge.")
        self._initialize_database()

    @classmethod
    def create(cls, root: str | Path, name: str | None = None) -> "ProjectStore":
        project_root = Path(root).expanduser().resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        manifest_path = project_root / PROJECT_FILE
        if manifest_path.exists():
            raise FileExistsError(f"Un projet existe déjà dans {project_root}")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "id": str(uuid.uuid4()),
            "name": name or project_root.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "settings": {"output_dir": "exports", "cache_dir": ".cutweave_cache"},
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (project_root / "exports").mkdir(exist_ok=True)
        return cls(project_root)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @property
    def cache_root(self) -> Path:
        configured = self.manifest.get("settings", {}).get("cache_dir", ".cutweave_cache")
        relative = Path(configured)
        if relative.is_absolute():
            raise ValueError("Le cache du projet doit rester dans son dossier local.")
        root = (self.root / relative).resolve()
        if root != self.root and self.root not in root.parents:
            raise ValueError("Le dossier de cache doit rester dans le dossier du projet.")
        return root

    def broll_thumbnail_path(self, asset_id: int) -> Path:
        if asset_id <= 0:
            raise ValueError("L'identifiant du clip B-roll doit être positif.")
        return self.cache_root / "thumbnails" / f"broll-{asset_id}.jpg"

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY,
                    stored_path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    duration REAL,
                    width INTEGER,
                    height INTEGER,
                    frame_rate REAL,
                    has_audio INTEGER,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_path TEXT,
                    output_path TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS captions (
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
                    end_ms INTEGER NOT NULL CHECK(end_ms > start_ms),
                    text TEXT NOT NULL,
                    PRIMARY KEY(media_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS framing_keyframes (
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    time_ms INTEGER NOT NULL CHECK(time_ms >= 0),
                    center_x REAL NOT NULL CHECK(center_x >= 0 AND center_x <= 1),
                    center_y REAL NOT NULL CHECK(center_y >= 0 AND center_y <= 1),
                    zoom REAL NOT NULL CHECK(zoom >= 1 AND zoom <= 4),
                    PRIMARY KEY(media_id, ordinal),
                    UNIQUE(media_id, time_ms)
                );
                CREATE TABLE IF NOT EXISTS visual_settings (
                    media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
                    aspect TEXT NOT NULL CHECK(aspect IN ('9:16','16:9','1:1')),
                    width INTEGER NOT NULL CHECK(width >= 16 AND width <= 7680 AND width % 2 = 0),
                    height INTEGER NOT NULL CHECK(height >= 16 AND height <= 7680 AND height % 2 = 0),
                    sharpening REAL NOT NULL CHECK(sharpening >= 0 AND sharpening <= 2),
                    sharpening_enabled INTEGER NOT NULL CHECK(sharpening_enabled IN (0,1)),
                    sharpening_radius INTEGER NOT NULL CHECK(sharpening_radius >= 3 AND sharpening_radius <= 23 AND sharpening_radius % 2 = 1),
                    sharpening_threshold INTEGER NOT NULL CHECK(sharpening_threshold >= 0 AND sharpening_threshold <= 255)
                );
                CREATE TABLE IF NOT EXISTS mask_regions (
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    x INTEGER NOT NULL CHECK(x >= 0),
                    y INTEGER NOT NULL CHECK(y >= 0),
                    width INTEGER NOT NULL CHECK(width > 0),
                    height INTEGER NOT NULL CHECK(height > 0),
                    opacity REAL NOT NULL CHECK(opacity > 0 AND opacity <= 1),
                    start_ms INTEGER CHECK(start_ms IS NULL OR start_ms >= 0),
                    end_ms INTEGER CHECK(end_ms IS NULL OR end_ms >= 0),
                    shape TEXT NOT NULL DEFAULT 'rectangle',
                    points_json TEXT NOT NULL DEFAULT '[]',
                    keyframes_json TEXT NOT NULL DEFAULT '[]',
                    effect TEXT NOT NULL DEFAULT 'opaque',
                    blur_radius INTEGER NOT NULL DEFAULT 12,
                    PRIMARY KEY(media_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS broll_assets (
                    id INTEGER PRIMARY KEY,
                    stored_path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                    width INTEGER,
                    height INTEGER,
                    category TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS broll_placements (
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL REFERENCES broll_assets(id) ON DELETE CASCADE,
                    timeline_start_ms INTEGER NOT NULL CHECK(timeline_start_ms >= 0),
                    source_start_ms INTEGER NOT NULL CHECK(source_start_ms >= 0),
                    source_end_ms INTEGER NOT NULL CHECK(source_end_ms > source_start_ms),
                    track INTEGER NOT NULL DEFAULT 0 CHECK(track >= 0),
                    PRIMARY KEY(media_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS audio_assets (
                    id INTEGER PRIMARY KEY,
                    stored_path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                    category TEXT NOT NULL DEFAULT 'music',
                    tags_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS music_mixes (
                    media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
                    asset_id INTEGER NOT NULL REFERENCES audio_assets(id) ON DELETE RESTRICT,
                    timeline_start_ms INTEGER NOT NULL CHECK(timeline_start_ms >= 0),
                    source_start_ms INTEGER NOT NULL CHECK(source_start_ms >= 0),
                    source_end_ms INTEGER NOT NULL CHECK(source_end_ms > source_start_ms),
                    volume REAL NOT NULL CHECK(volume >= 0 AND volume <= 4),
                    fade_in_ms INTEGER NOT NULL DEFAULT 0 CHECK(fade_in_ms >= 0),
                    fade_out_ms INTEGER NOT NULL DEFAULT 0 CHECK(fade_out_ms >= 0),
                    loop INTEGER NOT NULL DEFAULT 0 CHECK(loop IN (0,1)),
                    mix_original_audio INTEGER NOT NULL DEFAULT 1 CHECK(mix_original_audio IN (0,1)),
                    ducking_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS sfx_placements (
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL REFERENCES audio_assets(id) ON DELETE RESTRICT,
                    timeline_start_ms INTEGER NOT NULL CHECK(timeline_start_ms >= 0),
                    source_start_ms INTEGER NOT NULL CHECK(source_start_ms >= 0),
                    source_end_ms INTEGER NOT NULL CHECK(source_end_ms > source_start_ms),
                    volume REAL NOT NULL CHECK(volume >= 0 AND volume <= 4),
                    fade_in_ms INTEGER NOT NULL DEFAULT 0 CHECK(fade_in_ms >= 0),
                    fade_out_ms INTEGER NOT NULL DEFAULT 0 CHECK(fade_out_ms >= 0),
                    event_marker TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(media_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS caption_presets (
                    name TEXT PRIMARY KEY COLLATE NOCASE,
                    preset_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS caption_settings (
                    media_id INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
                    preset_name TEXT NOT NULL DEFAULT '',
                    preset_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS timeline_markers (
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    time_ms INTEGER NOT NULL CHECK(time_ms >= 0),
                    label TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(media_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS sfx_rules (
                    ordinal INTEGER PRIMARY KEY,
                    rule_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broll_rules (
                    ordinal INTEGER PRIMARY KEY,
                    rule_json TEXT NOT NULL
                );
                """
            )
            music_columns = {row["name"] for row in connection.execute("PRAGMA table_info(music_mixes)")}
            if "mix_original_audio" not in music_columns:
                connection.execute("ALTER TABLE music_mixes ADD COLUMN mix_original_audio INTEGER NOT NULL DEFAULT 1")
            if "ducking_json" not in music_columns:
                connection.execute("ALTER TABLE music_mixes ADD COLUMN ducking_json TEXT NOT NULL DEFAULT '[]'")
            for column, ddl in (
                ("duck_captions", "INTEGER NOT NULL DEFAULT 0"),
                ("caption_duck_level", "REAL NOT NULL DEFAULT 0.3"),
                ("caption_duck_fade_ms", "INTEGER NOT NULL DEFAULT 120"),
            ):
                if column not in music_columns:
                    connection.execute(f"ALTER TABLE music_mixes ADD COLUMN {column} {ddl}")
            caption_setting_columns = {row["name"] for row in connection.execute("PRAGMA table_info(caption_settings)")}
            if "preset_name" not in caption_setting_columns:
                connection.execute("ALTER TABLE caption_settings ADD COLUMN preset_name TEXT NOT NULL DEFAULT ''")
            mask_columns = {row["name"] for row in connection.execute("PRAGMA table_info(mask_regions)")}
            if "shape" not in mask_columns:
                connection.execute("ALTER TABLE mask_regions ADD COLUMN shape TEXT NOT NULL DEFAULT 'rectangle'")
            if "points_json" not in mask_columns:
                connection.execute("ALTER TABLE mask_regions ADD COLUMN points_json TEXT NOT NULL DEFAULT '[]'")
            if "keyframes_json" not in mask_columns:
                connection.execute("ALTER TABLE mask_regions ADD COLUMN keyframes_json TEXT NOT NULL DEFAULT '[]'")
            if "effect" not in mask_columns:
                connection.execute("ALTER TABLE mask_regions ADD COLUMN effect TEXT NOT NULL DEFAULT 'opaque'")
            if "blur_radius" not in mask_columns:
                connection.execute("ALTER TABLE mask_regions ADD COLUMN blur_radius INTEGER NOT NULL DEFAULT 12")

    def _stored_path(self, media_path: Path) -> str:
        try:
            return media_path.relative_to(self.root).as_posix()
        except ValueError:
            return str(media_path)

    def _resolved_path(self, stored_path: str) -> Path:
        candidate = Path(stored_path)
        return candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()

    def add_media(self, path: str | Path, info) -> ProjectMedia:
        media_path = Path(path).expanduser().resolve()
        stored = self._stored_path(media_path)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO media(stored_path,name,duration,width,height,frame_rate,has_audio,imported_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(stored_path) DO UPDATE SET
                   name=excluded.name,duration=excluded.duration,width=excluded.width,height=excluded.height,
                   frame_rate=excluded.frame_rate,has_audio=excluded.has_audio""",
                (stored, media_path.name, info.duration, info.width, info.height, info.frame_rate,
                 int(info.has_audio), datetime.now(timezone.utc).isoformat()),
            )
            row = connection.execute("SELECT * FROM media WHERE stored_path=?", (stored,)).fetchone()
        return self._media_from_row(row)

    def list_media(self) -> list[ProjectMedia]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM media ORDER BY id").fetchall()
        return [self._media_from_row(row) for row in rows]

    def remove_media(self, media_ids) -> int:
        """Retire des médias du projet (et leurs réglages) ; les fichiers sur disque ne sont pas touchés."""
        ids = [int(media_id) for media_id in media_ids]
        if not ids:
            return 0
        with self._connect() as connection:
            cursor = connection.executemany("DELETE FROM media WHERE id=?", [(media_id,) for media_id in ids])
            return cursor.rowcount

    def set_captions(self, media_id: int, cues: tuple[CaptionCue, ...] | list[CaptionCue]) -> None:
        for cue in cues:
            cue.validate()
        ordered = tuple(sorted(cues, key=lambda cue: (cue.start_ms, cue.end_ms)))
        with self._connect() as connection:
            connection.execute("DELETE FROM captions WHERE media_id=?", (media_id,))
            connection.executemany(
                "INSERT INTO captions(media_id,ordinal,start_ms,end_ms,text) VALUES(?,?,?,?,?)",
                [(media_id, ordinal, cue.start_ms, cue.end_ms, cue.text)
                 for ordinal, cue in enumerate(ordered)],
            )

    def get_captions(self, media_id: int) -> tuple[CaptionCue, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT start_ms,end_ms,text FROM captions WHERE media_id=? ORDER BY ordinal", (media_id,)
            ).fetchall()
        return tuple(CaptionCue(row["start_ms"], row["end_ms"], row["text"]) for row in rows)

    def set_framing_keyframes(self, media_id: int, keyframes: tuple[FramingKeyframe, ...] | list[FramingKeyframe]) -> None:
        ordered = tuple(keyframes)
        validate_keyframes(ordered)
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM media WHERE id=?", (media_id,)).fetchone() is None:
                raise ValueError(f"Média de cadrage introuvable : {media_id}.")
            connection.execute("DELETE FROM framing_keyframes WHERE media_id=?", (media_id,))
            connection.executemany(
                "INSERT INTO framing_keyframes(media_id,ordinal,time_ms,center_x,center_y,zoom) VALUES(?,?,?,?,?,?)",
                [(media_id, ordinal, frame.time_ms, frame.center_x, frame.center_y, frame.zoom)
                 for ordinal, frame in enumerate(ordered)],
            )

    def get_framing_keyframes(self, media_id: int) -> tuple[FramingKeyframe, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT time_ms,center_x,center_y,zoom FROM framing_keyframes WHERE media_id=? ORDER BY ordinal",
                (media_id,),
            ).fetchall()
        return tuple(FramingKeyframe(row["time_ms"], row["center_x"], row["center_y"], row["zoom"])
                     for row in rows)

    def set_visual_settings(self, media_id: int, settings: VisualSettings) -> None:
        settings.validate()
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM media WHERE id=?", (media_id,)).fetchone() is None:
                raise ValueError(f"Média de réglages visuels introuvable : {media_id}.")
            connection.execute(
                """INSERT INTO visual_settings(media_id,aspect,width,height,sharpening,sharpening_enabled,
                   sharpening_radius,sharpening_threshold) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(media_id) DO UPDATE SET aspect=excluded.aspect,width=excluded.width,
                   height=excluded.height,sharpening=excluded.sharpening,
                   sharpening_enabled=excluded.sharpening_enabled,sharpening_radius=excluded.sharpening_radius,
                   sharpening_threshold=excluded.sharpening_threshold""",
                (media_id, settings.aspect, settings.width, settings.height, settings.sharpening,
                 int(settings.sharpening_enabled), settings.sharpening_radius, settings.sharpening_threshold),
            )

    def get_visual_settings(self, media_id: int) -> VisualSettings:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM visual_settings WHERE media_id=?", (media_id,)).fetchone()
        if row is None:
            return VisualSettings()
        return VisualSettings(
            aspect=row["aspect"], width=row["width"], height=row["height"], sharpening=row["sharpening"],
            sharpening_enabled=bool(row["sharpening_enabled"]), sharpening_radius=row["sharpening_radius"],
            sharpening_threshold=row["sharpening_threshold"],
        )

    def save_custom_caption_preset(self, preset: CaptionPreset) -> None:
        preset.validate()
        if preset.name.casefold() in BUILTIN_BY_NAME:
            raise CaptionPresetError("Un preset intégré ne peut pas être remplacé ; choisissez un autre nom.")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO caption_presets(name,preset_json) VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET preset_json=excluded.preset_json",
                (preset.name.strip(), preset.to_json()),
            )

    def list_custom_caption_presets(self) -> tuple[CaptionPreset, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT preset_json FROM caption_presets ORDER BY name COLLATE NOCASE").fetchall()
        return tuple(CaptionPreset.from_json(row["preset_json"]) for row in rows)

    def delete_custom_caption_preset(self, name: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM caption_presets WHERE name=?", (name,))

    def set_caption_preset(self, media_id: int, preset: CaptionPreset | None) -> None:
        with self._connect() as connection:
            if preset is None:
                connection.execute("DELETE FROM caption_settings WHERE media_id=?", (media_id,))
                return
            preset.validate()
            if connection.execute("SELECT 1 FROM media WHERE id=?", (media_id,)).fetchone() is None:
                raise CaptionPresetError(f"Média de captions introuvable : {media_id}.")
            connection.execute(
                "INSERT INTO caption_settings(media_id,preset_name,preset_json) VALUES(?,?,?) "
                "ON CONFLICT(media_id) DO UPDATE SET preset_name=excluded.preset_name,preset_json=excluded.preset_json",
                (media_id, preset.name, preset.to_json()),
            )

    def get_caption_preset(self, media_id: int) -> CaptionPreset | None:
        with self._connect() as connection:
            row = connection.execute("SELECT preset_name,preset_json FROM caption_settings WHERE media_id=?", (media_id,)).fetchone()
            if row is None:
                return None
            preset_name = row["preset_name"]
            if preset_name.casefold() in BUILTIN_BY_NAME:
                return BUILTIN_BY_NAME[preset_name.casefold()]
            custom = connection.execute("SELECT preset_json FROM caption_presets WHERE name=?", (preset_name,)).fetchone()
        return CaptionPreset.from_json(custom["preset_json"] if custom else row["preset_json"])

    def set_masks(self, media_id: int, masks: tuple[MaskRegion, ...] | list[MaskRegion]) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM mask_regions WHERE media_id=?", (media_id,))
            connection.executemany(
                """INSERT INTO mask_regions(media_id,ordinal,x,y,width,height,opacity,start_ms,end_ms,shape,points_json,keyframes_json,effect,blur_radius)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(media_id, ordinal, mask.x, mask.y, mask.width, mask.height, mask.opacity,
                  mask.start_ms, mask.end_ms, mask.shape, json.dumps(mask.points, separators=(",", ":")),
                  json.dumps([frame.__dict__ for frame in mask.keyframes], separators=(",", ":")),
                  mask.effect, mask.blur_radius)
                 for ordinal, mask in enumerate(masks)],
            )

    def get_masks(self, media_id: int) -> tuple[MaskRegion, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT x,y,width,height,opacity,start_ms,end_ms,shape,points_json,keyframes_json,effect,blur_radius FROM mask_regions WHERE media_id=? ORDER BY ordinal",
                (media_id,),
            ).fetchall()
        return tuple(MaskRegion(row["x"], row["y"], row["width"], row["height"], row["opacity"],
                                row["start_ms"], row["end_ms"], row["shape"],
                                tuple(tuple(point) for point in json.loads(row["points_json"] or "[]")),
                                tuple(MaskKeyframe(**frame) for frame in json.loads(row["keyframes_json"] or "[]")),
                                row["effect"], row["blur_radius"])
                     for row in rows)

    def add_broll_asset(self, path: str | Path, info, *, category: str = "", tags=()) -> BrollAsset:
        media_path = Path(path).expanduser().resolve()
        stored = self._stored_path(media_path)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO broll_assets(stored_path,name,duration_ms,width,height,category,tags_json)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(stored_path) DO UPDATE SET
                   name=excluded.name,duration_ms=excluded.duration_ms,width=excluded.width,
                   height=excluded.height,category=excluded.category,tags_json=excluded.tags_json""",
                (stored, media_path.name, max(0, round(info.duration * 1000)), info.width, info.height,
                 category.strip(), json.dumps(list(dict.fromkeys(tags)), ensure_ascii=False)),
            )
            row = connection.execute("SELECT * FROM broll_assets WHERE stored_path=?", (stored,)).fetchone()
        return self._broll_asset_from_row(row)

    def list_broll_assets(self, query: str = "") -> tuple[BrollAsset, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM broll_assets ORDER BY name COLLATE NOCASE").fetchall()
        assets = tuple(self._broll_asset_from_row(row) for row in rows)
        needle = query.strip().casefold()
        if not needle:
            return assets
        return tuple(asset for asset in assets if needle in " ".join((asset.name, asset.category, *asset.tags)).casefold())

    def set_broll_placements(self, media_id: int, placements: tuple[BrollPlacement, ...] | list[BrollPlacement]) -> None:
        with self._connect() as connection:
            asset_rows = connection.execute("SELECT id,duration_ms FROM broll_assets").fetchall()
            durations = {row["id"]: row["duration_ms"] for row in asset_rows}
            media_row = connection.execute("SELECT duration FROM media WHERE id=?", (media_id,)).fetchone()
            if media_row is None:
                raise BrollValidationError(f"Média de timeline introuvable : {media_id}.")
            timeline_duration = round(media_row["duration"] * 1000) if media_row["duration"] else None
            ordered = tuple(sorted(placements, key=lambda p: (p.timeline_start_ms, p.track, p.asset_id)))
            for placement in ordered:
                placement.validate()
                if placement.asset_id not in durations:
                    raise BrollValidationError(f"Clip B-roll introuvable : {placement.asset_id}.")
                if placement.source_end_ms > durations[placement.asset_id]:
                    raise BrollValidationError("La sortie demandée dépasse la durée du clip B-roll.")
                if timeline_duration is not None and placement.timeline_end_ms > timeline_duration:
                    raise BrollValidationError("L'insertion B-roll dépasse la durée de la vidéo principale.")
            connection.execute("DELETE FROM broll_placements WHERE media_id=?", (media_id,))
            connection.executemany(
                """INSERT INTO broll_placements(media_id,ordinal,asset_id,timeline_start_ms,
                   source_start_ms,source_end_ms,track) VALUES(?,?,?,?,?,?,?)""",
                [(media_id, ordinal, p.asset_id, p.timeline_start_ms, p.source_start_ms,
                  p.source_end_ms, p.track) for ordinal, p in enumerate(ordered)],
            )

    def get_broll_placements(self, media_id: int) -> tuple[BrollPlacement, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT asset_id,timeline_start_ms,source_start_ms,source_end_ms,track FROM broll_placements "
                "WHERE media_id=? ORDER BY ordinal", (media_id,),
            ).fetchall()
        return tuple(BrollPlacement(row["asset_id"], row["timeline_start_ms"], row["source_start_ms"],
                                    row["source_end_ms"], row["track"]) for row in rows)

    def _broll_asset_from_row(self, row: sqlite3.Row) -> BrollAsset:
        return BrollAsset(row["id"], self._resolved_path(row["stored_path"]), row["name"], row["duration_ms"],
                          row["width"], row["height"], row["category"], tuple(json.loads(row["tags_json"])))

    def add_audio_asset(self, path: str | Path, info, *, category: str = "music", tags=()) -> AudioAsset:
        media_path = Path(path).expanduser().resolve()
        stored = self._stored_path(media_path)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO audio_assets(stored_path,name,duration_ms,category,tags_json) VALUES(?,?,?,?,?)
                   ON CONFLICT(stored_path) DO UPDATE SET name=excluded.name,duration_ms=excluded.duration_ms,
                   category=excluded.category,tags_json=excluded.tags_json""",
                (stored, media_path.name, max(0, round(info.duration * 1000)), category.strip(),
                 json.dumps(list(dict.fromkeys(tags)), ensure_ascii=False)),
            )
            row = connection.execute("SELECT * FROM audio_assets WHERE stored_path=?", (stored,)).fetchone()
        return self._audio_asset_from_row(row)

    def list_audio_assets(self, query: str = "") -> tuple[AudioAsset, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM audio_assets ORDER BY name COLLATE NOCASE").fetchall()
        assets = tuple(self._audio_asset_from_row(row) for row in rows)
        needle = query.strip().casefold()
        if not needle:
            return assets
        return tuple(asset for asset in assets if needle in " ".join((asset.name, asset.category, *asset.tags)).casefold())

    def set_music_mix(self, media_id: int, settings: MusicSettings | None) -> None:
        with self._connect() as connection:
            if settings is None:
                connection.execute("DELETE FROM music_mixes WHERE media_id=?", (media_id,))
                return
            row = connection.execute("SELECT duration_ms FROM audio_assets WHERE id=?", (settings.asset_id,)).fetchone()
            if row is None:
                raise MusicValidationError(f"Fichier audio introuvable : {settings.asset_id}.")
            media = connection.execute("SELECT duration FROM media WHERE id=?", (media_id,)).fetchone()
            if media is None:
                raise MusicValidationError(f"Média de timeline introuvable : {media_id}.")
            media_duration = round(media["duration"] * 1000) if media["duration"] else None
            play_duration = max(0, media_duration - settings.timeline_start_ms) if media_duration is not None else None
            if play_duration == 0:
                raise MusicValidationError("Le départ de la musique dépasse la durée de la vidéo.")
            settings.validate(row["duration_ms"], play_duration)
            for segment in settings.ducking_segments:
                segment.validate(media_duration)
            connection.execute(
                """INSERT INTO music_mixes(media_id,asset_id,timeline_start_ms,source_start_ms,source_end_ms,
                   volume,fade_in_ms,fade_out_ms,loop,mix_original_audio,ducking_json,duck_captions,
                   caption_duck_level,caption_duck_fade_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(media_id) DO UPDATE SET
                   asset_id=excluded.asset_id,timeline_start_ms=excluded.timeline_start_ms,
                   source_start_ms=excluded.source_start_ms,source_end_ms=excluded.source_end_ms,
                   volume=excluded.volume,fade_in_ms=excluded.fade_in_ms,fade_out_ms=excluded.fade_out_ms,
                   loop=excluded.loop,mix_original_audio=excluded.mix_original_audio,ducking_json=excluded.ducking_json,
                   duck_captions=excluded.duck_captions,caption_duck_level=excluded.caption_duck_level,
                   caption_duck_fade_ms=excluded.caption_duck_fade_ms""",
                (media_id, settings.asset_id, settings.timeline_start_ms, settings.source_start_ms,
                 settings.source_end_ms, settings.volume, settings.fade_in_ms, settings.fade_out_ms,
                 int(settings.loop), int(settings.mix_original_audio),
                 json.dumps([segment.__dict__ for segment in settings.ducking_segments], separators=(",", ":")),
                 int(settings.duck_on_captions), settings.caption_duck_level, settings.caption_duck_fade_ms),
            )

    def get_music_mix(self, media_id: int) -> MusicSettings | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT asset_id,timeline_start_ms,source_start_ms,source_end_ms,volume,fade_in_ms,fade_out_ms,loop,mix_original_audio,ducking_json,"
                "duck_captions,caption_duck_level,caption_duck_fade_ms FROM music_mixes WHERE media_id=?", (media_id,),
            ).fetchone()
        if row is None:
            return None
        return MusicSettings(row["asset_id"], row["timeline_start_ms"], row["source_start_ms"],
                             row["source_end_ms"], row["volume"], row["fade_in_ms"], row["fade_out_ms"],
                             bool(row["loop"]), bool(row["mix_original_audio"]),
                             tuple(DuckingSegment(**item) for item in json.loads(row["ducking_json"] or "[]")),
                             bool(row["duck_captions"]), row["caption_duck_level"], row["caption_duck_fade_ms"])

    def get_audio_asset(self, asset_id: int) -> AudioAsset | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM audio_assets WHERE id=?", (asset_id,)).fetchone()
        return self._audio_asset_from_row(row) if row else None

    def set_sfx_placements(self, media_id: int, placements: tuple[SfxPlacement, ...] | list[SfxPlacement]) -> None:
        with self._connect() as connection:
            media = connection.execute("SELECT duration FROM media WHERE id=?", (media_id,)).fetchone()
            if media is None:
                raise SfxValidationError(f"Média de timeline introuvable : {media_id}.")
            timeline_duration = round(media["duration"] * 1000) if media["duration"] else None
            asset_rows = connection.execute("SELECT id,duration_ms FROM audio_assets").fetchall()
            durations = {row["id"]: row["duration_ms"] for row in asset_rows}
            ordered = tuple(sorted(placements, key=lambda item: (item.timeline_start_ms, item.asset_id)))
            for placement in ordered:
                placement.validate(durations.get(placement.asset_id))
                if placement.asset_id not in durations:
                    raise SfxValidationError(f"Fichier SFX introuvable : {placement.asset_id}.")
                if timeline_duration is not None and placement.timeline_start_ms >= timeline_duration:
                    raise SfxValidationError("Le placement SFX commence après la fin de la vidéo.")
            connection.execute("DELETE FROM sfx_placements WHERE media_id=?", (media_id,))
            connection.executemany(
                """INSERT INTO sfx_placements(media_id,ordinal,asset_id,timeline_start_ms,source_start_ms,
                   source_end_ms,volume,fade_in_ms,fade_out_ms,event_marker) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [(media_id, ordinal, item.asset_id, item.timeline_start_ms, item.source_start_ms,
                  item.source_end_ms, item.volume, item.fade_in_ms, item.fade_out_ms, item.event_marker)
                 for ordinal, item in enumerate(ordered)],
            )

    def get_sfx_placements(self, media_id: int) -> tuple[SfxPlacement, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT asset_id,timeline_start_ms,source_start_ms,source_end_ms,volume,fade_in_ms,fade_out_ms,event_marker "
                "FROM sfx_placements WHERE media_id=? ORDER BY ordinal", (media_id,),
            ).fetchall()
        return tuple(SfxPlacement(row["asset_id"], row["timeline_start_ms"], row["source_start_ms"],
                                  row["source_end_ms"], row["volume"], row["fade_in_ms"],
                                  row["fade_out_ms"], row["event_marker"]) for row in rows)

    # --- Modules, marqueurs et règles déterministes -------------------------------------

    def _write_manifest(self) -> None:
        temporary = self.manifest_path.with_name(f".{self.manifest_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.manifest_path)
        finally:
            temporary.unlink(missing_ok=True)

    def module_enabled(self, module: str) -> bool:
        if module not in MODULES:
            raise ValueError(f"Module inconnu : {module}")
        return bool(self.manifest.get("settings", {}).get("modules", {}).get(module, True))

    def modules(self) -> dict[str, bool]:
        return {module: self.module_enabled(module) for module in MODULES}

    def set_module_enabled(self, module: str, enabled: bool) -> None:
        if module not in MODULES:
            raise ValueError(f"Module inconnu : {module}")
        self.manifest.setdefault("settings", {}).setdefault("modules", {})[module] = bool(enabled)
        self._write_manifest()

    def set_markers(self, media_id: int, markers) -> None:
        ordered = tuple(sorted(markers))
        for marker in ordered:
            marker.validate()
        with self._connect() as connection:
            connection.execute("DELETE FROM timeline_markers WHERE media_id=?", (media_id,))
            connection.executemany(
                "INSERT INTO timeline_markers(media_id,ordinal,time_ms,label) VALUES(?,?,?,?)",
                [(media_id, ordinal, marker.time_ms, marker.label) for ordinal, marker in enumerate(ordered)],
            )

    def get_markers(self, media_id: int) -> tuple[TimelineMarker, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT time_ms,label FROM timeline_markers WHERE media_id=? ORDER BY ordinal", (media_id,),
            ).fetchall()
        return tuple(TimelineMarker(row["time_ms"], row["label"]) for row in rows)

    def set_sfx_rules(self, rules) -> None:
        durations = {asset.id: asset.duration_ms for asset in self.list_audio_assets()}
        for rule in rules:
            if rule.asset_id not in durations:
                raise SfxValidationError(f"Règle SFX : fichier audio introuvable ({rule.asset_id}).")
            rule.validate(durations[rule.asset_id])
        with self._connect() as connection:
            connection.execute("DELETE FROM sfx_rules")
            connection.executemany(
                "INSERT INTO sfx_rules(ordinal,rule_json) VALUES(?,?)",
                [(ordinal, json.dumps(asdict(rule), ensure_ascii=False)) for ordinal, rule in enumerate(rules)],
            )

    def get_sfx_rules(self) -> tuple[SfxRule, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT rule_json FROM sfx_rules ORDER BY ordinal").fetchall()
        return tuple(SfxRule(**json.loads(row["rule_json"])) for row in rows)

    def set_broll_rules(self, rules) -> None:
        known = {asset.id for asset in self.list_broll_assets()}
        for rule in rules:
            rule.validate()
            if rule.asset_id is not None and rule.asset_id not in known:
                raise BrollValidationError(f"Règle B-roll : clip introuvable ({rule.asset_id}).")
        with self._connect() as connection:
            connection.execute("DELETE FROM broll_rules")
            connection.executemany(
                "INSERT INTO broll_rules(ordinal,rule_json) VALUES(?,?)",
                [(ordinal, json.dumps(asdict(rule), ensure_ascii=False)) for ordinal, rule in enumerate(rules)],
            )

    def get_broll_rules(self) -> tuple[BrollRule, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT rule_json FROM broll_rules ORDER BY ordinal").fetchall()
        return tuple(BrollRule(**json.loads(row["rule_json"])) for row in rows)

    def list_tasks(self, limit: int = 200) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def relink_media(self, media_id: int, new_path: str | Path, info) -> ProjectMedia:
        """Remplace la référence d'un média manquant sans toucher au fichier d'origine."""
        media_path = Path(new_path).expanduser().resolve()
        if not media_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {media_path}")
        with self._connect() as connection:
            connection.execute(
                "UPDATE media SET stored_path=?,name=?,duration=?,width=?,height=?,frame_rate=?,has_audio=? WHERE id=?",
                (self._stored_path(media_path), media_path.name, info.duration, info.width, info.height,
                 info.frame_rate, int(info.has_audio), media_id),
            )
            row = connection.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        if row is None:
            raise ValueError(f"Média introuvable : {media_id}")
        return self._media_from_row(row)

    def _audio_asset_from_row(self, row: sqlite3.Row) -> AudioAsset:
        return AudioAsset(row["id"], self._resolved_path(row["stored_path"]), row["name"], row["duration_ms"],
                          row["category"], tuple(json.loads(row["tags_json"])))

    def _media_from_row(self, row: sqlite3.Row) -> ProjectMedia:
        return ProjectMedia(
            id=row["id"], path=self._resolved_path(row["stored_path"]), name=row["name"],
            duration=row["duration"], width=row["width"], height=row["height"],
            frame_rate=row["frame_rate"], has_audio=bool(row["has_audio"]) if row["has_audio"] is not None else None,
        )

    def record_task(self, kind: str, status: str, *, input_path=None, output_path=None, error=None, task_id=None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            if task_id is None:
                cursor = connection.execute(
                    "INSERT INTO tasks(kind,status,input_path,output_path,error,started_at,finished_at) VALUES(?,?,?,?,?,?,?)",
                    (kind, status, str(input_path) if input_path else None, str(output_path) if output_path else None,
                     error, now, now if status in {"succeeded", "failed", "cancelled"} else None),
                )
                return int(cursor.lastrowid)
            connection.execute(
                "UPDATE tasks SET status=?,output_path=?,error=?,finished_at=? WHERE id=?",
                (status, str(output_path) if output_path else None, error,
                 now if status in {"succeeded", "failed", "cancelled"} else None, task_id),
            )
            return task_id

