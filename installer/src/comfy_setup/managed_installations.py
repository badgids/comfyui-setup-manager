from __future__ import annotations

import json

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_config_path

from .configuration import ensure_editable_config


STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class InstanceMetadata:
    name: str
    description: str
    profile_id: str | None = None
    profile_name: str | None = None


def _state_path() -> Path:
    path = user_config_path("comfyui-setup-manager", appauthor=False) / "installations.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _legacy_state_path() -> Path:
    return user_config_path("comfyui-setup-manager", appauthor=False) / "installations.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    legacy = _legacy_state_path()
    if not path.is_file() and legacy.is_file():
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass
    if not path.is_file():
        try:
            path = ensure_editable_config("installations.yaml")
        except Exception:
            return {"schema_version": STATE_VERSION, "instances": {}}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"schema_version": STATE_VERSION, "instances": {}}
    if not isinstance(data, dict) or not isinstance(data.get("instances"), dict):
        return {"schema_version": STATE_VERSION, "instances": {}}
    return data


def _save_state(data: dict[str, Any]) -> None:
    path = _state_path()
    temp = path.with_suffix(path.suffix + ".tmp")
    data["schema_version"] = STATE_VERSION
    data.pop("version", None)
    temp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    temp.replace(path)


def _key(path: Path) -> str:
    return str(path.expanduser().resolve())


def load_instance_metadata(path: Path) -> InstanceMetadata:
    root = path.expanduser().resolve()
    state = _load_state()
    record = state.get("instances", {}).get(_key(root), {})
    if isinstance(record, dict) and record.get("name"):
        return InstanceMetadata(
            name=str(record.get("name")),
            description=str(record.get("description") or ""),
            profile_id=str(record.get("profile_id")) if record.get("profile_id") else None,
            profile_name=str(record.get("profile_name")) if record.get("profile_name") else None,
        )

    report_path = root / ".comfy-setup" / "install-report.json"
    report: dict[str, Any] = {}
    if report_path.is_file():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                report = loaded
        except (OSError, json.JSONDecodeError):
            pass
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    profile_name = str(profile.get("name")) if profile.get("name") else None
    description = str(profile.get("description") or "")
    if not description:
        repository = _git_origin(root)
        description = (
            f"Managed ComfyUI installation using {profile_name}."
            if profile_name
            else (f"ComfyUI checkout from {repository}." if repository else "Detected ComfyUI installation.")
        )
    return InstanceMetadata(
        name=profile_name or root.name,
        description=description,
        profile_id=str(profile.get("id")) if profile.get("id") else None,
        profile_name=profile_name,
    )


def save_instance_metadata(
    path: Path,
    *,
    name: str,
    description: str,
    profile_id: str | None = None,
    profile_name: str | None = None,
) -> InstanceMetadata:
    root = path.expanduser().resolve()
    data = _load_state()
    instances = data.setdefault("instances", {})
    instances[_key(root)] = {
        "name": name.strip() or root.name,
        "description": description.strip(),
        "profile_id": profile_id,
        "profile_name": profile_name,
    }
    _save_state(data)
    return load_instance_metadata(root)


def remove_instance_metadata(path: Path) -> None:
    data = _load_state()
    instances = data.setdefault("instances", {})
    instances.pop(_key(path), None)
    _save_state(data)


def _git_origin(path: Path) -> str | None:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None
