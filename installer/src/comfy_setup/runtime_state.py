from __future__ import annotations

import copy
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .asset_catalog import list_installed_assets
from .configuration import load_editable_config, save_editable_config
from .discovery import (
    ComfyInstallation,
    InstalledNode,
    InstalledWorkflow,
    discover_installations_detailed,
    installed_nodes,
)
from .models import PlatformInfo
from .node_catalog import ensure_daily_node_catalog, refresh_node_catalog
from .platforms import detect_platform

RUNTIME_STATE_FILE = "runtime-state.yaml"
RUNTIME_STATE_VERSION = 1
NODE_RESOLUTION_SCHEMA = 1
ProgressCallback = Callable[[int, int, str], None]


def placeholder_platform_info() -> PlatformInfo:
    """Return cheap, non-probing platform information for immediate TUI render."""
    system = platform.system() or "Unknown"
    release = platform.release() or ""
    return PlatformInfo(
        os_name=system.lower(),
        system=system,
        release=release,
        architecture=platform.machine() or "unknown",
        is_wsl=False,
        package_manager=None,
        accelerator="pending",
        gpu_name=None,
        notes=["System scan has not completed yet."],
    )


def _platform_to_dict(info: PlatformInfo) -> dict[str, Any]:
    return asdict(info)


def _platform_from_dict(payload: Any) -> PlatformInfo | None:
    if not isinstance(payload, dict):
        return None
    required = {"os_name", "system", "release", "architecture", "is_wsl", "accelerator"}
    if not required <= payload.keys():
        return None
    try:
        return PlatformInfo(
            os_name=str(payload["os_name"]),
            system=str(payload["system"]),
            release=str(payload["release"]),
            architecture=str(payload["architecture"]),
            is_wsl=bool(payload["is_wsl"]),
            package_manager=str(payload["package_manager"]) if payload.get("package_manager") else None,
            accelerator=str(payload["accelerator"]),
            gpu_name=str(payload["gpu_name"]) if payload.get("gpu_name") else None,
            compute_capability=float(payload["compute_capability"]) if payload.get("compute_capability") is not None else None,
            cuda_version=str(payload["cuda_version"]) if payload.get("cuda_version") else None,
            rocm_version=str(payload["rocm_version"]) if payload.get("rocm_version") else None,
            notes=[str(item) for item in payload.get("notes", [])],
        )
    except (TypeError, ValueError):
        return None


def _installation_to_dict(item: ComfyInstallation) -> dict[str, Any]:
    payload = asdict(item)
    payload["path"] = str(item.path)
    return payload


def _installation_from_dict(payload: Any) -> ComfyInstallation | None:
    if not isinstance(payload, dict) or not payload.get("path"):
        return None
    try:
        return ComfyInstallation(
            path=Path(str(payload["path"])).expanduser().resolve(),
            name=str(payload.get("name") or Path(str(payload["path"])).name),
            description=str(payload.get("description") or "Detected ComfyUI installation."),
            profile_id=str(payload["profile_id"]) if payload.get("profile_id") else None,
            profile_name=str(payload["profile_name"]) if payload.get("profile_name") else None,
            version=str(payload["version"]) if payload.get("version") else None,
            commit=str(payload["commit"]) if payload.get("commit") else None,
            branch=str(payload["branch"]) if payload.get("branch") else None,
            repository=str(payload["repository"]) if payload.get("repository") else None,
            has_venv=bool(payload.get("has_venv", False)),
            node_count=int(payload.get("node_count", 0)),
            workflow_count=int(payload.get("workflow_count", 0)),
        )
    except (TypeError, ValueError, OSError):
        return None


def _node_to_dict(item: InstalledNode) -> dict[str, Any]:
    return {
        "name": item.name,
        "path": str(item.path),
        "repository": item.repository,
        "commit": item.commit,
        "manager_id": item.manager_id,
        "manager_version": item.manager_version,
        "display_name": item.display_name,
        "resolution_kind": item.resolution_kind,
        "resolution_trust": item.resolution_trust,
    }


def _workflow_to_dict(item: InstalledWorkflow) -> dict[str, Any]:
    return {"name": item.name, "path": str(item.path), "user_name": item.user_name}


def load_runtime_state() -> dict[str, Any]:
    payload = load_editable_config(RUNTIME_STATE_FILE)
    if not isinstance(payload, dict):
        return {"schema_version": RUNTIME_STATE_VERSION, "initialized": False}
    return payload




def node_resolution_state_is_current(state: dict[str, Any] | None = None) -> bool:
    """Return whether cached node provenance uses the current resolver contract."""

    current = state or load_runtime_state()
    try:
        return int(current.get("node_resolution_schema", 0)) == NODE_RESOLUTION_SCHEMA
    except (TypeError, ValueError):
        return False

def cached_platform_info(state: dict[str, Any] | None = None) -> PlatformInfo:
    state = state or load_runtime_state()
    return _platform_from_dict(state.get("platform")) or placeholder_platform_info()


def cached_installations(state: dict[str, Any] | None = None) -> list[ComfyInstallation]:
    state = state or load_runtime_state()
    output: list[ComfyInstallation] = []
    for payload in state.get("installations", []):
        item = _installation_from_dict(payload)
        if item is not None:
            output.append(item)
    return sorted(output, key=lambda item: str(item.path).lower())


def cached_nodes(state: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    state = state or load_runtime_state()
    payload = state.get("nodes", {})
    if not isinstance(payload, dict):
        return {}
    return {
        str(path): [dict(item) for item in items if isinstance(item, dict)]
        for path, items in payload.items()
        if isinstance(items, list)
    }


def cached_instance_workflows(state: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    state = state or load_runtime_state()
    payload = state.get("instance_workflows", {})
    if not isinstance(payload, dict):
        return {}
    return {
        str(path): [dict(item) for item in items if isinstance(item, dict)]
        for path, items in payload.items()
        if isinstance(items, list)
    }


def cached_assets(state: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    state = state or load_runtime_state()
    payload = state.get("assets", {})
    output: dict[str, list[dict[str, Any]]] = {"models": [], "loras": [], "workflows": []}
    if not isinstance(payload, dict):
        return output
    for kind in output:
        items = payload.get(kind, [])
        if isinstance(items, list):
            output[kind] = [dict(item) for item in items if isinstance(item, dict)]
    return output




def refresh_cached_node_catalog_state(
    *,
    progress: ProgressCallback | None = None,
    force: bool = False,
    base_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh public node metadata and re-resolve cached installations only.

    Daily startup must not repeat the expensive platform, filesystem, workflow,
    and asset scans.  It refreshes the Registry/Manager catalogs, re-resolves
    custom-node provenance for already cached installations, and persists only
    that updated node inventory.
    """

    def report(step: int, total: int, message: str) -> None:
        if progress is not None:
            progress(step, total, message)

    report(1, 3, "Refreshing the Comfy Registry and Manager node database cache…")
    if force:
        refresh_node_catalog(force=True, progress=lambda message: report(1, 3, message))
    else:
        ensure_daily_node_catalog(progress=lambda message: report(1, 3, message))

    # Re-resolve the exact cache currently displayed by the caller.  Reloading
    # runtime-state.yaml here can race tests, external edits, or another manager
    # session and unexpectedly replace the visible installation selection.
    state = copy.deepcopy(base_state) if base_state is not None else load_runtime_state()
    installations = cached_installations(state)
    report(2, 3, "Resolving cached installations against the refreshed node catalogs…")
    state["nodes"] = {
        str(installation.path): [_node_to_dict(item) for item in installed_nodes(installation.path)]
        for installation in installations
        if installation.path.is_dir()
    }
    state["node_catalog_refreshed_utc"] = datetime.now(timezone.utc).isoformat()
    state["node_resolution_schema"] = NODE_RESOLUTION_SCHEMA
    report(3, 3, "Saving refreshed node provenance to runtime-state.yaml…")
    save_editable_config(RUNTIME_STATE_FILE, state)
    return state

def scan_runtime_state(
    extra_roots: Iterable[Path] = (),
    *,
    progress: ProgressCallback | None = None,
    refresh_catalog: bool = False,
    force_catalog_refresh: bool = False,
) -> dict[str, Any]:
    """Perform the explicit full scan and persist a complete YAML cache.

    The daily startup scan uses ``refresh_catalog``.  An explicit Nodes &
    Plugins rescan may set ``force_catalog_refresh`` so users can immediately
    pick up a newly published Registry or Manager node without deleting cache
    files or waiting until the next UTC day.
    """
    catalog_step = refresh_catalog or force_catalog_refresh
    total = 7 if catalog_step else 6
    offset = 1 if catalog_step else 0

    def report(step: int, message: str) -> None:
        if progress is not None:
            progress(step, total, message)

    if catalog_step:
        report(1, "Refreshing the Comfy Registry and Manager node database cache…")
        if force_catalog_refresh:
            refresh_node_catalog(
                force=True, progress=lambda message: report(1, message)
            )
        else:
            ensure_daily_node_catalog(progress=lambda message: report(1, message))

    report(1 + offset, "Detecting operating system, GPU, and accelerator…")
    platform_info = detect_platform()

    report(2 + offset, "Finding ComfyUI installations and reading instance metadata…")
    discovered = discover_installations_detailed(extra_roots)

    report(3 + offset, "Resolving installed custom nodes against Registry, Manager, and Git sources…")
    nodes = {
        path: [_node_to_dict(item) for item in items]
        for path, items in discovered.nodes.items()
    }

    report(4 + offset, "Indexing native workflows for each installation…")
    instance_workflows = {
        path: [_workflow_to_dict(item) for item in items]
        for path, items in discovered.workflows.items()
    }

    report(5 + offset, "Indexing shared models, LoRAs, and workflows…")
    assets = {
        "models": list_installed_assets("models"),
        "loras": list_installed_assets("loras"),
        "workflows": list_installed_assets("workflows"),
    }

    report(6 + offset, "Saving the scan to runtime-state.yaml…")
    payload: dict[str, Any] = {
        "schema_version": RUNTIME_STATE_VERSION,
        "initialized": True,
        "last_scan_utc": datetime.now(timezone.utc).isoformat(),
        "node_resolution_schema": NODE_RESOLUTION_SCHEMA,
        "platform": _platform_to_dict(platform_info),
        "installations": [_installation_to_dict(item) for item in discovered.installations],
        "nodes": nodes,
        "instance_workflows": instance_workflows,
        "assets": assets,
    }
    save_editable_config(RUNTIME_STATE_FILE, payload)
    return payload
