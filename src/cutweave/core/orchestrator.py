"""Exécution ordonnée, mise en cache et reprise des étapes d'un pipeline local."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from cutweave.core.cache import CacheCancelled, CacheError, StageCache


class PipelineError(RuntimeError):
    pass


class PipelineCancelled(PipelineError):
    pass


def _json_value(value: Any):
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_value(item) for item in value), key=repr)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Valeur de pipeline non sérialisable : {type(value).__name__}")


@dataclass(frozen=True)
class PipelineArtifact:
    path: Path
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PipelineStage:
    stage_id: str
    version: str
    execute: Callable[[Mapping[str, Any], Mapping[str, PipelineArtifact], Any, Callable[[str], None]], Path | PipelineArtifact]
    dependencies: tuple[str, ...] = ()
    input_files: Callable[[Mapping[str, Any]], Iterable[tuple[str, str | Path]]] = lambda _context: ()
    parameters: Callable[[Mapping[str, Any]], Any] = lambda _context: {}


class PipelineOrchestrator:
    """Checkpoint par étape ; une étape valide est réutilisée après relance."""

    state_version = 1

    def __init__(self, project_root: str | Path, cache: StageCache | None = None, *, state_name: str = "state.json"):
        if not re.fullmatch(r"[a-z0-9_.-]{1,60}\.json", state_name):
            raise PipelineError("Nom de checkpoint invalide.")
        self.project_root = Path(project_root).expanduser().resolve()
        self.state_root = self.project_root / ".cutweave_pipeline"
        self.state_path = self.state_root / state_name
        self.cache = cache or StageCache(self.project_root / ".cutweave_cache")
        self._lock = threading.RLock()

    @staticmethod
    def _cancelled(cancel_event) -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"version": self.state_version, "stages": {}}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineError(f"Checkpoint de pipeline illisible : {exc}") from exc
        if state.get("version") != self.state_version or not isinstance(state.get("stages"), dict):
            raise PipelineError("Version ou structure de checkpoint non prise en charge.")
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(f".{self.state_path.name}.{uuid.uuid4().hex}.part")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(state, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except OSError as exc:
            raise PipelineError(f"Impossible d'écrire le checkpoint du pipeline : {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _artifact_digest(path: Path) -> str:
        if not path.is_file() or path.stat().st_size == 0:
            raise PipelineError(f"L'étape n'a pas produit d'artefact valide : {path}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _validate_stages(stages: Iterable[PipelineStage]) -> dict[str, PipelineStage]:
        stage_map = {}
        for stage in stages:
            if not re.fullmatch(r"[a-z0-9_-]{1,40}", stage.stage_id):
                raise PipelineError(f"Identifiant d'étape invalide : {stage.stage_id}")
            if stage.stage_id in stage_map:
                raise PipelineError(f"Identifiant d'étape dupliqué : {stage.stage_id}")
            stage_map[stage.stage_id] = stage
        for stage in stage_map.values():
            missing = set(stage.dependencies) - stage_map.keys()
            if missing:
                raise PipelineError(f"Dépendances absentes pour {stage.stage_id} : {', '.join(sorted(missing))}")
        return stage_map

    @staticmethod
    def _execution_order(stage_map: Mapping[str, PipelineStage], targets: Iterable[str] | None) -> list[str]:
        requested = list(stage_map) if targets is None else list(targets)
        if not requested:
            raise PipelineError("Aucune étape demandée.")
        selected: set[str] = set()
        visiting: set[str] = set()
        ordered: list[str] = []

        def visit(stage_id: str) -> None:
            if stage_id not in stage_map:
                raise PipelineError(f"Étape inconnue : {stage_id}")
            if stage_id in visiting:
                raise PipelineError(f"Dépendance cyclique détectée autour de {stage_id}.")
            if stage_id in selected:
                return
            visiting.add(stage_id)
            for dependency in stage_map[stage_id].dependencies:
                visit(dependency)
            visiting.remove(stage_id)
            selected.add(stage_id)
            ordered.append(stage_id)

        for stage_id in requested:
            visit(stage_id)
        return ordered

    def run(
        self,
        stages: Iterable[PipelineStage],
        context: Mapping[str, Any],
        *,
        targets: Iterable[str] | None = None,
        force: Iterable[str] = (),
        cancel_event=None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> dict[str, PipelineArtifact]:
        stage_map = self._validate_stages(stages)
        order = self._execution_order(stage_map, targets)
        force_set = set(force)
        unknown_forced = force_set - stage_map.keys()
        if unknown_forced:
            raise PipelineError(f"Étape à forcer inconnue : {', '.join(sorted(unknown_forced))}")
        report = on_progress or (lambda _stage, _message: None)
        artifacts: dict[str, PipelineArtifact] = {}
        keys: dict[str, str] = {}
        with self._lock:
            state = self._load_state()
            for stage_id in order:
                if self._cancelled(cancel_event):
                    raise PipelineCancelled("Pipeline annulé avant le démarrage de l'étape suivante.")
                stage = stage_map[stage_id]
                dependency_keys = [f"{dep}:{keys[dep]}:{state['stages'][dep]['sha256']}" for dep in stage.dependencies]
                try:
                    key = self.cache.make_key(
                        "pipeline", stage.input_files(context), stage.parameters(context),
                        module_version=f"{stage.stage_id}:{stage.version}", dependencies=dependency_keys,
                        cancel_event=cancel_event,
                    )
                except (CacheError, TypeError) as exc:
                    raise PipelineError(f"Impossible de calculer la clé de l'étape {stage_id} : {exc}") from exc
                keys[stage_id] = key
                previous = state["stages"].get(stage_id, {})
                if stage_id not in force_set and previous.get("status") == "succeeded" and previous.get("key") == key:
                    old_path = Path(previous.get("artifact", ""))
                    try:
                        digest = self._artifact_digest(old_path)
                    except (OSError, PipelineError):
                        digest = ""
                    if digest and digest == previous.get("sha256"):
                        artifacts[stage_id] = PipelineArtifact(old_path, previous.get("metadata"))
                        report(stage_id, "Étape reprise depuis son checkpoint validé.")
                        continue

                state["stages"][stage_id] = {
                    "status": "running", "key": key, "started_at": datetime.now(timezone.utc).isoformat(),
                    "artifact": None, "sha256": None, "metadata": None, "error": None,
                }
                self._save_state(state)
                report(stage_id, "Étape démarrée.")
                try:
                    result = stage.execute(
                        context, {dependency: artifacts[dependency] for dependency in stage.dependencies},
                        cancel_event, lambda message: report(stage_id, message),
                    )
                    artifact = result if isinstance(result, PipelineArtifact) else PipelineArtifact(Path(result))
                    artifact_path = artifact.path.expanduser().resolve()
                    if self._cancelled(cancel_event):
                        raise PipelineCancelled("Pipeline annulé pendant l'étape.")
                    digest = self._artifact_digest(artifact_path)
                    metadata = _json_value(artifact.metadata or {})
                    artifacts[stage_id] = PipelineArtifact(artifact_path, metadata)
                    state["stages"][stage_id].update({
                        "status": "succeeded", "finished_at": datetime.now(timezone.utc).isoformat(),
                        "artifact": str(artifact_path), "sha256": digest, "metadata": metadata,
                    })
                    self._save_state(state)
                    report(stage_id, "Étape terminée.")
                except Exception as exc:
                    cancelled = self._cancelled(cancel_event) or isinstance(exc, (PipelineCancelled, CacheCancelled))
                    state["stages"][stage_id].update({
                        "status": "cancelled" if cancelled else "failed",
                        "finished_at": datetime.now(timezone.utc).isoformat(), "error": str(exc),
                    })
                    self._save_state(state)
                    if cancelled and not isinstance(exc, PipelineCancelled):
                        raise PipelineCancelled(f"Pipeline annulé pendant l'étape {stage_id}.") from exc
                    raise
        return artifacts
