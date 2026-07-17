from __future__ import annotations

import copy
import os
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_path

APP_NAME = "comfyui-setup-manager"
CONFIG_RESOURCE_DIR = "config"
EDITABLE_CONFIG_FILES = (
    "manager-config.yaml",
    "custom-node-sources.yaml",
    "repositories.yaml",
    "pytorch-releases.yaml",
    "wheel-sources.yaml",
    "themes.yaml",
    "installations.yaml",
    "asset-paths.yaml",
    "model-sources.yaml",
    "workflow-sources.yaml",
    "download-tasks.yaml",
    "runtime-state.yaml",
    "lora-sources.yaml",
    "agents-skills.yaml",
    "mcps.yaml",
)


class ConfigurationError(RuntimeError):
    pass


def project_root_directory() -> Path:
    """Return the active ComfyUI Setup Manager project root.

    The bundled launchers set ``COMFYUI_SETUP_PROJECT_ROOT``. Direct package
    invocations fall back to the current working directory so exports remain
    predictable without depending on site-packages layout.
    """
    configured = os.environ.get("COMFYUI_SETUP_PROJECT_ROOT")
    root = Path(configured).expanduser() if configured else Path.cwd()
    return root.resolve()


def profiles_directory() -> Path:
    """Return the project profile directory and migrate the old ``setups`` name.

    Migration never overwrites a same-named file already present in
    ``profiles``.  Any conflicting legacy files remain in ``setups`` for the
    user to reconcile manually; an emptied legacy directory is removed.
    """

    root = project_root_directory()
    directory = root / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    legacy = root / "setups"
    if legacy.is_dir() and legacy != directory:
        for source in legacy.iterdir():
            destination = directory / source.name
            if destination.exists():
                continue
            try:
                shutil.move(str(source), str(destination))
            except OSError:
                continue
        try:
            legacy.rmdir()
        except OSError:
            pass
    return directory


def setups_directory() -> Path:
    """Compatibility alias for integrations written before v0.8.7."""

    return profiles_directory()


def config_directory() -> Path:
    configured = os.environ.get("COMFYUI_SETUP_CONFIG_DIR")
    if configured:
        path = Path(configured).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return user_config_path(APP_NAME, appauthor=False, ensure_exists=True)


def packaged_config_path(filename: str) -> Path:
    return Path(resources.files("comfy_setup").joinpath(CONFIG_RESOURCE_DIR, filename))


def user_config_file(filename: str) -> Path:
    return config_directory() / filename


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Could not read YAML configuration {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError(f"YAML configuration must contain a mapping: {path}")
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    temporary.replace(path)


def _merge_mapping(defaults: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(defaults)
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_mapping(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _merge_sources(defaults: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """Merge newly bundled sources without replacing user edits or local entries."""
    merged = _merge_mapping(defaults, {k: v for k, v in user.items() if k != "sources"})
    default_sources = [entry for entry in defaults.get("sources", []) if isinstance(entry, dict)]
    user_sources = [entry for entry in user.get("sources", []) if isinstance(entry, dict)]
    user_by_id = {str(entry.get("id")): entry for entry in user_sources if entry.get("id")}
    result_sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in default_sources:
        source_id = str(entry.get("id") or "")
        if source_id and source_id in user_by_id:
            result_sources.append(copy.deepcopy(user_by_id[source_id]))
            seen.add(source_id)
        else:
            result_sources.append(copy.deepcopy(entry))
            if source_id:
                seen.add(source_id)
    for entry in user_sources:
        source_id = str(entry.get("id") or "")
        if not source_id or source_id not in seen:
            result_sources.append(copy.deepcopy(entry))
    merged["sources"] = result_sources
    return merged


def ensure_editable_config(filename: str) -> Path:
    if filename not in EDITABLE_CONFIG_FILES:
        raise ConfigurationError(f"Unknown editable configuration file: {filename}")
    source = packaged_config_path(filename)
    destination = user_config_file(filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source, destination)
        return destination

    defaults = _load_yaml(source)
    user = _load_yaml(destination)
    merge_defaults = True
    if filename == "manager-config.yaml":
        merge_defaults = bool(user.get("resolver", {}).get("merge_new_default_sources", True))
    if filename in {"wheel-sources.yaml", "custom-node-sources.yaml"} and merge_defaults:
        merged = _merge_sources(defaults, user)
    else:
        merged = _merge_mapping(defaults, user)
    if merged != user:
        _write_yaml(destination, merged)
    return destination


def load_editable_config(filename: str) -> dict[str, Any]:
    return _load_yaml(ensure_editable_config(filename))


def save_editable_config(filename: str, payload: dict[str, Any]) -> Path:
    path = user_config_file(filename)
    _write_yaml(path, payload)
    return path


def manager_config() -> dict[str, Any]:
    return load_editable_config("manager-config.yaml")


def repository_config() -> dict[str, Any]:
    return load_editable_config("repositories.yaml")


def pytorch_release_config() -> dict[str, Any]:
    return load_editable_config("pytorch-releases.yaml")


def official_comfyui_repository() -> str:
    payload = repository_config()
    return str(
        payload.get("repositories", {})
        .get("comfyui_official", {})
        .get("url", "https://github.com/Comfy-Org/ComfyUI.git")
    )
