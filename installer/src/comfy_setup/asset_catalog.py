from __future__ import annotations

import hashlib
import os
import shutil
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from .configuration import load_editable_config, save_editable_config
from .shared_assets import SharedAssetPaths, load_shared_asset_paths


class AssetCatalogError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AssetSource:
    id: str
    name: str
    kind: str
    base_url: str | None
    label: str
    enabled: bool = True
    notes: str = ""


@dataclass(frozen=True, slots=True)
class AssetEntry:
    id: str
    name: str
    source_id: str
    url: str
    destination: str
    filename: str | None = None
    sha256: str | None = None
    description: str = ""
    required: bool = False
    tags: tuple[str, ...] = ()


@dataclass(slots=True)
class DownloadTask:
    id: str
    kind: str
    asset_id: str
    source: str
    destination: str
    status: str = "queued"
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    error: str | None = None
    created_at: float = 0.0
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _config_name(kind: str) -> str:
    names = {
        "models": "model-sources.yaml",
        "loras": "lora-sources.yaml",
        "workflows": "workflow-sources.yaml",
    }
    try:
        return names[kind]
    except KeyError as exc:
        raise AssetCatalogError(f"Unsupported asset kind: {kind}") from exc


def _task_file() -> str:
    return "download-tasks.yaml"


def _catalog_root(kind: str, paths: SharedAssetPaths | None = None) -> Path:
    paths = paths or load_shared_asset_paths()
    if kind == "models":
        return paths.models
    if kind == "loras":
        return paths.models / "loras"
    if kind == "workflows":
        return paths.workflows
    raise AssetCatalogError(f"Unsupported asset kind: {kind}")


def _validate_relative_destination(kind: str, relative: str) -> str:
    value = relative.strip().replace("\\", "/").strip("/")
    if kind == "models" and value.split("/", 1)[0].lower() == "loras":
        raise AssetCatalogError("LoRA destinations belong in the dedicated LoRAs workspace.")
    return value


def load_catalog(kind: str) -> dict[str, Any]:
    return load_editable_config(_config_name(kind))


def save_catalog(kind: str, payload: dict[str, Any]) -> Path:
    return save_editable_config(_config_name(kind), payload)


def list_sources(kind: str) -> list[AssetSource]:
    payload = load_catalog(kind)
    output: list[AssetSource] = []
    for item in payload.get("sources", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        output.append(
            AssetSource(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                kind=str(item.get("kind") or "direct-url"),
                base_url=str(item["base_url"]) if item.get("base_url") else None,
                label=str(item.get("label") or "3rd Party"),
                enabled=bool(item.get("enabled", True)),
                notes=str(item.get("notes") or ""),
            )
        )
    return output


def list_entries(kind: str) -> list[AssetEntry]:
    payload = load_catalog(kind)
    output: list[AssetEntry] = []
    for item in payload.get("entries", []):
        if not isinstance(item, dict) or not item.get("id") or not item.get("url"):
            continue
        output.append(
            AssetEntry(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                source_id=str(item.get("source_id") or "custom"),
                url=str(item["url"]),
                destination=str(item.get("destination") or ""),
                filename=str(item["filename"]) if item.get("filename") else None,
                sha256=str(item["sha256"]) if item.get("sha256") else None,
                description=str(item.get("description") or ""),
                required=bool(item.get("required", False)),
                tags=tuple(str(value) for value in item.get("tags", []) if value),
            )
        )
    return output


def add_source(kind: str, source: dict[str, Any]) -> Path:
    payload = load_catalog(kind)
    sources = [item for item in payload.get("sources", []) if isinstance(item, dict)]
    source_id = str(source.get("id") or "").strip()
    if not source_id:
        raise AssetCatalogError("Source id is required.")
    sources = [item for item in sources if str(item.get("id")) != source_id]
    sources.append(source)
    payload["sources"] = sources
    return save_catalog(kind, payload)


def add_entry(kind: str, entry: dict[str, Any]) -> Path:
    payload = load_catalog(kind)
    entries = [item for item in payload.get("entries", []) if isinstance(item, dict)]
    entry_id = str(entry.get("id") or "").strip()
    if not entry_id:
        raise AssetCatalogError("Asset entry id is required.")
    entries = [item for item in entries if str(item.get("id")) != entry_id]
    entries.append(entry)
    payload["entries"] = entries
    return save_catalog(kind, payload)


def remove_entry(kind: str, entry_id: str) -> bool:
    payload = load_catalog(kind)
    entries = [item for item in payload.get("entries", []) if isinstance(item, dict)]
    filtered = [item for item in entries if str(item.get("id")) != entry_id]
    if len(filtered) == len(entries):
        return False
    payload["entries"] = filtered
    save_catalog(kind, payload)
    return True


def _load_tasks() -> dict[str, Any]:
    return load_editable_config(_task_file())


def _save_tasks(tasks: Iterable[DownloadTask]) -> Path:
    return save_editable_config(
        _task_file(),
        {"schema_version": 1, "tasks": [task.to_dict() for task in tasks]},
    )


def list_tasks() -> list[DownloadTask]:
    payload = _load_tasks()
    output: list[DownloadTask] = []
    for item in payload.get("tasks", []):
        if not isinstance(item, dict):
            continue
        try:
            output.append(DownloadTask(**item))
        except TypeError:
            continue
    return output


def _upsert_task(task: DownloadTask) -> None:
    tasks = [item for item in list_tasks() if item.id != task.id]
    tasks.append(task)
    _save_tasks(tasks[-500:])


def _resolve_source_url(entry: AssetEntry, sources: dict[str, AssetSource]) -> str:
    source = sources.get(entry.source_id)
    if source is None or not source.enabled:
        return entry.url
    parsed = urllib.parse.urlparse(entry.url)
    if parsed.scheme in {"http", "https", "file"}:
        return entry.url
    if source.kind == "huggingface" and source.base_url:
        return source.base_url.rstrip("/") + "/" + entry.url.lstrip("/")
    if source.base_url:
        return source.base_url.rstrip("/") + "/" + entry.url.lstrip("/")
    return entry.url


def _safe_destination(root: Path, relative: str, filename: str) -> Path:
    destination_dir = (root / relative).resolve()
    if root.resolve() not in {destination_dir, *destination_dir.parents}:
        raise AssetCatalogError("Asset destination escapes the shared library root.")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = (destination_dir / filename).resolve()
    if root.resolve() not in destination.parents:
        raise AssetCatalogError("Asset filename escapes the shared library root.")
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_entry(
    kind: str,
    entry_id: str,
    *,
    overwrite: bool = False,
    progress: Callable[[DownloadTask], None] | None = None,
    paths: SharedAssetPaths | None = None,
) -> DownloadTask:
    entries = {entry.id: entry for entry in list_entries(kind)}
    if entry_id not in entries:
        raise AssetCatalogError(f"Unknown {kind} entry: {entry_id}")
    entry = entries[entry_id]
    sources = {source.id: source for source in list_sources(kind)}
    url = _resolve_source_url(entry, sources)
    parsed = urllib.parse.urlparse(url)
    filename = entry.filename or Path(parsed.path).name or f"{entry.id}.bin"
    root = _catalog_root(kind, paths)
    root.mkdir(parents=True, exist_ok=True)
    destination = _safe_destination(root, _validate_relative_destination(kind, entry.destination), filename)
    if destination.exists() and not overwrite:
        raise AssetCatalogError(f"Destination already exists: {destination}")

    task = DownloadTask(
        id=f"{kind}-{entry.id}-{int(time.time() * 1000)}",
        kind=kind,
        asset_id=entry.id,
        source=url,
        destination=str(destination),
        status="running",
        created_at=time.time(),
    )
    _upsert_task(task)
    if progress:
        progress(task)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        if parsed.scheme == "file":
            source_path = Path(urllib.request.url2pathname(parsed.path))
            shutil.copy2(source_path, temporary)
            task.downloaded_bytes = temporary.stat().st_size
            task.total_bytes = task.downloaded_bytes
        elif parsed.scheme in {"http", "https"}:
            request = urllib.request.Request(url, headers={"User-Agent": "comfyui-setup-manager/0.8.7"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                length = response.headers.get("Content-Length")
                task.total_bytes = int(length) if length and length.isdigit() else None
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    task.downloaded_bytes += len(block)
                    _upsert_task(task)
                    if progress:
                        progress(task)
        else:
            source_path = Path(url).expanduser()
            if not source_path.is_file():
                raise AssetCatalogError(f"Unsupported or missing source: {url}")
            shutil.copy2(source_path, temporary)
            task.downloaded_bytes = temporary.stat().st_size
            task.total_bytes = task.downloaded_bytes
        if entry.sha256 and _sha256(temporary).lower() != entry.sha256.lower():
            raise AssetCatalogError(f"Checksum failed for {entry.name}.")
        temporary.replace(destination)
        task.status = "completed"
        task.completed_at = time.time()
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        task.status = "failed"
        task.error = str(exc)
        task.completed_at = time.time()
        _upsert_task(task)
        if progress:
            progress(task)
        raise
    _upsert_task(task)
    if progress:
        progress(task)
    return task


def import_asset(
    kind: str,
    source: Path,
    *,
    destination: str = "",
    overwrite: bool = False,
    paths: SharedAssetPaths | None = None,
) -> Path:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise AssetCatalogError(f"Asset file not found: {source}")
    root = _catalog_root(kind, paths)
    target = _safe_destination(root, _validate_relative_destination(kind, destination), source.name)
    if target.exists() and not overwrite:
        raise AssetCatalogError(f"Destination already exists: {target}")
    shutil.copy2(source, target)
    return target


def list_installed_assets(kind: str, *, paths: SharedAssetPaths | None = None) -> list[dict[str, Any]]:
    root = _catalog_root(kind, paths)
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for candidate in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
        if not candidate.is_file() or candidate.name.endswith(".part"):
            continue
        relative = candidate.relative_to(root)
        if kind == "models" and relative.parts and relative.parts[0].lower() == "loras":
            continue
        entries.append(
            {
                "path": str(candidate),
                "relative_path": relative.as_posix(),
                "size": candidate.stat().st_size,
                "modified": candidate.stat().st_mtime,
            }
        )
    return entries


def delete_asset(kind: str, relative_path: str, *, paths: SharedAssetPaths | None = None) -> Path:
    root = _catalog_root(kind, paths).resolve()
    target = (root / relative_path).resolve()
    if root not in target.parents or not target.is_file():
        raise AssetCatalogError(f"Asset not found inside shared {kind} library: {relative_path}")
    target.unlink()
    return target


def clear_tasks(
    kind: str | None = None,
    *,
    statuses: Iterable[str] = ("completed", "failed"),
) -> int:
    """Remove matching task-history records and return the removed count.

    Running or queued tasks are preserved unless their status is explicitly
    included. This function changes history only; it never deletes downloaded
    assets.
    """
    status_set = {str(value) for value in statuses}
    tasks = list_tasks()
    kept: list[DownloadTask] = []
    removed = 0
    for task in tasks:
        kind_matches = kind is None or task.kind == kind
        status_matches = task.status in status_set
        if kind_matches and status_matches:
            removed += 1
        else:
            kept.append(task)
    _save_tasks(kept)
    return removed


def retry_task(
    task_id: str,
    *,
    overwrite: bool = False,
    progress: Callable[[DownloadTask], None] | None = None,
    paths: SharedAssetPaths | None = None,
) -> DownloadTask:
    """Retry a previous model/workflow task using its catalog asset id."""
    task = next((item for item in list_tasks() if item.id == task_id), None)
    if task is None:
        raise AssetCatalogError(f"Download task not found: {task_id}")
    return download_entry(
        task.kind,
        task.asset_id,
        overwrite=overwrite,
        progress=progress,
        paths=paths,
    )


def export_asset(
    kind: str,
    relative_path: str,
    destination: Path,
    *,
    overwrite: bool = False,
    paths: SharedAssetPaths | None = None,
) -> Path:
    """Copy one shared-library file to a user-selected destination."""
    root = _catalog_root(kind, paths).resolve()
    source = (root / relative_path).resolve()
    if root not in source.parents or not source.is_file():
        raise AssetCatalogError(f"Asset not found inside shared {kind} library: {relative_path}")
    target = destination.expanduser()
    if target.exists() and target.is_dir():
        target = target / source.name
    elif not target.suffix and not target.exists():
        # An extensionless, non-existing target is treated as a directory.
        target.mkdir(parents=True, exist_ok=True)
        target = target / source.name
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise AssetCatalogError(f"Export destination already exists: {target}")
    shutil.copy2(source, target)
    return target
