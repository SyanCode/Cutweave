"""Cache de contenu local, versionné et publié de façon atomique."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable


class CacheError(RuntimeError):
    pass


class CacheCancelled(CacheError):
    pass


def _stable_value(value: Any):
    if is_dataclass(value):
        return _stable_value(asdict(value))
    if isinstance(value, Path):
        return str(value.resolve())
    if isinstance(value, dict):
        return {str(key): _stable_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_stable_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Valeur non sérialisable dans les paramètres du cache : {type(value).__name__}")


class StageCache:
    """Stocke des artefacts par empreinte source + paramètres + version du module."""

    schema_version = 1

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise CacheCancelled("Calcul de clé de cache annulé.")

    @classmethod
    def fingerprint_file(cls, path: str | Path, *, cancel_event=None) -> str:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise CacheError(f"Fichier source introuvable pour le cache : {source}")
        digest = hashlib.sha256()
        try:
            with source.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    cls._check_cancel(cancel_event)
                    digest.update(block)
        except OSError as exc:
            raise CacheError(f"Impossible de calculer l'empreinte de {source.name} : {exc}") from exc
        return digest.hexdigest()

    def make_key(
        self,
        namespace: str,
        inputs: Iterable[tuple[str, str | Path]],
        parameters: Any,
        *,
        module_version: str,
        dependencies: Iterable[str] = (),
        cancel_event=None,
    ) -> str:
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", namespace):
            raise CacheError("Espace de noms de cache invalide.")
        fingerprints = []
        for role, path in inputs:
            self._check_cancel(cancel_event)
            fingerprints.append({"role": str(role), "sha256": self.fingerprint_file(path, cancel_event=cancel_event)})
        payload = {
            "cache_schema": self.schema_version,
            "namespace": namespace,
            "module_version": module_version,
            "inputs": fingerprints,
            "parameters": _stable_value(parameters),
            "dependencies": sorted(dependencies),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def artifact_path(self, namespace: str, key: str, suffix: str) -> Path:
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", namespace):
            raise CacheError("Espace de noms de cache invalide.")
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise CacheError("Clé de cache invalide.")
        if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix.casefold()):
            raise CacheError("Extension d'artefact invalide.")
        return self.root / namespace / f"{key}{suffix.lower()}"

    def lookup(self, namespace: str, key: str, suffix: str) -> Path | None:
        path = self.artifact_path(namespace, key, suffix)
        return path if path.is_file() and path.stat().st_size > 0 else None

    def publish(self, source: str | Path, namespace: str, key: str, suffix: str) -> Path:
        original = Path(source).expanduser().resolve()
        if not original.is_file() or original.stat().st_size == 0:
            raise CacheError("Artefact vide ou absent ; il n'a pas été ajouté au cache.")
        target = self.artifact_path(namespace, key, suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.part{target.suffix}")
        try:
            shutil.copyfile(original, temporary)
            os.replace(temporary, target)
        except OSError as exc:
            raise CacheError(f"Impossible d'enregistrer l'artefact dans le cache : {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return target

    @staticmethod
    def copy_to(source: str | Path, destination: str | Path) -> Path:
        cached = Path(source).expanduser().resolve()
        output = Path(destination).expanduser().resolve()
        if cached == output:
            return output
        if not cached.is_file() or cached.stat().st_size == 0:
            raise CacheError("Artefact de cache introuvable ou vide.")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.part{output.suffix}")
        try:
            shutil.copyfile(cached, temporary)
            os.replace(temporary, output)
        except OSError as exc:
            raise CacheError(f"Impossible de copier l'artefact du cache : {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return output

    def get_or_create(
        self, namespace: str, key: str, suffix: str, producer, *,
        destination: str | Path | None = None, cancel_event=None,
    ) -> tuple[Path, bool]:
        """Retourne l'artefact en cache ou le produit, avec publication atomique."""
        self._check_cancel(cancel_event)
        cached = self.lookup(namespace, key, suffix)
        if cached is not None:
            artifact, hit = cached, True
        else:
            target = self.artifact_path(namespace, key, suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            produced = producer(target)
            self._check_cancel(cancel_event)
            candidate = Path(produced).expanduser().resolve() if produced is not None else target
            if candidate != target.resolve():
                artifact = self.publish(candidate, namespace, key, suffix)
            else:
                if not target.is_file() or target.stat().st_size == 0:
                    raise CacheError("Le producteur n'a pas créé d'artefact valide.")
                artifact = target
            hit = False
        if destination is not None:
            self.copy_to(artifact, destination)
        return artifact, hit

    def purge(self) -> int:
        """Supprime uniquement les sous-dossiers réservés au cache Cutweave."""
        removed = 0
        for namespace in ("render", "tracking"):
            directory = self.root / namespace
            if directory.is_symlink():
                directory.unlink()
                continue
            if directory.is_dir():
                removed += sum(1 for path in directory.rglob("*") if path.is_file())
                shutil.rmtree(directory)
        return removed

    @staticmethod
    def executable_version(binary: str | Path | None) -> str:
        if not binary:
            return "unavailable"
        try:
            result = subprocess.run([str(binary), "-version"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        return result.stdout.strip() if result.returncode == 0 and result.stdout else "unknown"
