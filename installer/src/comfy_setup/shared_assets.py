from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .configuration import load_editable_config, save_editable_config


class SharedAssetError(RuntimeError):
    """Raised when shared model/workflow paths cannot be configured safely."""


MODEL_DIRECTORY_MAP: dict[str, str] = {
    "checkpoints": "checkpoints",
    "configs": "configs",
    "loras": "loras",
    "vae": "vae",
    "text_encoders": "text_encoders",
    "clip": "clip",
    "diffusion_models": "diffusion_models",
    "unet": "unet",
    "clip_vision": "clip_vision",
    "style_models": "style_models",
    "embeddings": "embeddings",
    "diffusers": "diffusers",
    "vae_approx": "vae_approx",
    "controlnet": "controlnet",
    "t2i_adapter": "t2i_adapter",
    "gligen": "gligen",
    "upscale_models": "upscale_models",
    "latent_upscale_models": "latent_upscale_models",
    "hypernetworks": "hypernetworks",
    "photomaker": "photomaker",
    "classifiers": "classifiers",
    "model_patches": "model_patches",
    "audio_encoders": "audio_encoders",
    "background_removal": "background_removal",
    "frame_interpolation": "frame_interpolation",
    "geometry_estimation": "geometry_estimation",
    "optical_flow": "optical_flow",
    "detection": "detection",
}


@dataclass(frozen=True, slots=True)
class SharedAssetPaths:
    models: Path
    workflows: Path
    migrate_existing: bool = False
    conflict_policy: str = "preserve"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["models"] = str(self.models)
        payload["workflows"] = str(self.workflows)
        return payload


@dataclass(frozen=True, slots=True)
class InstanceAssetStatus:
    comfyui_dir: Path
    models_root: Path | None
    workflows_root: Path | None
    extra_model_paths_file: Path
    workflow_link: Path
    model_configured: bool
    workflow_configured: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "comfyui_dir": str(self.comfyui_dir),
            "models_root": str(self.models_root) if self.models_root else None,
            "workflows_root": str(self.workflows_root) if self.workflows_root else None,
            "extra_model_paths_file": str(self.extra_model_paths_file),
            "workflow_link": str(self.workflow_link),
            "model_configured": self.model_configured,
            "workflow_configured": self.workflow_configured,
        }


def default_shared_root() -> Path:
    """Return a portable, user-owned default. The value is not written until approved."""
    return (Path.home() / "ComfyUI-Shared").resolve()


def load_shared_asset_paths() -> SharedAssetPaths:
    payload = load_editable_config("asset-paths.yaml")
    section = payload.get("shared_assets", {}) if isinstance(payload, dict) else {}
    root = default_shared_root()
    models = Path(str(section.get("models_path") or root / "models")).expanduser().resolve()
    workflows = Path(str(section.get("workflows_path") or root / "workflows")).expanduser().resolve()
    return SharedAssetPaths(
        models=models,
        workflows=workflows,
        migrate_existing=bool(section.get("migrate_existing", False)),
        conflict_policy=str(section.get("conflict_policy") or "preserve"),
    )


def save_shared_asset_paths(paths: SharedAssetPaths) -> Path:
    payload = load_editable_config("asset-paths.yaml")
    payload["schema_version"] = 1
    payload["shared_assets"] = {
        "models_path": str(paths.models),
        "workflows_path": str(paths.workflows),
        "migrate_existing": paths.migrate_existing,
        "conflict_policy": paths.conflict_policy,
        "model_subdirectories": MODEL_DIRECTORY_MAP,
        "workflow_user": "default",
    }
    return save_editable_config("asset-paths.yaml", payload)


def create_shared_directories(paths: SharedAssetPaths) -> dict[str, list[str]]:
    paths.models.mkdir(parents=True, exist_ok=True)
    paths.workflows.mkdir(parents=True, exist_ok=True)
    model_dirs: list[str] = []
    for relative in dict.fromkeys(MODEL_DIRECTORY_MAP.values()):
        destination = paths.models / relative
        destination.mkdir(parents=True, exist_ok=True)
        model_dirs.append(str(destination))
    return {"models": model_dirs, "workflows": [str(paths.workflows)]}


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SharedAssetError(f"Could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SharedAssetError(f"Expected a YAML mapping in {path}.")
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8",
    )
    temporary.replace(path)


def write_extra_model_paths(comfyui_dir: Path, models_root: Path) -> Path:
    """Create/update ComfyUI's official ``extra_model_paths.yaml`` file.

    Existing unrelated sections are preserved. Only the manager-owned section is replaced.
    """
    root = comfyui_dir.expanduser().resolve()
    if not (root / "main.py").is_file():
        raise SharedAssetError(f"Not a ComfyUI installation: {root}")
    models_root = models_root.expanduser().resolve()
    models_root.mkdir(parents=True, exist_ok=True)
    config_path = root / "extra_model_paths.yaml"
    payload = _load_yaml_mapping(config_path)
    section: dict[str, Any] = {
        "base_path": str(models_root),
        "is_default": True,
    }
    section.update(MODEL_DIRECTORY_MAP)
    payload["comfyui_setup_manager_shared_models"] = section
    _write_yaml(config_path, payload)
    return config_path


def _merge_tree(source: Path, destination: Path, *, conflict_policy: str) -> list[str]:
    moved: list[str] = []
    if not source.exists():
        return moved
    for candidate in sorted(source.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if conflict_policy == "overwrite":
                target.unlink()
            elif conflict_policy == "rename":
                index = 2
                while target.exists():
                    target = target.with_name(f"{target.stem}-{index}{target.suffix}")
                    index += 1
            else:
                continue
        shutil.move(str(candidate), str(target))
        moved.append(str(target))
    return moved


def _remove_empty_tree(path: Path) -> None:
    if not path.exists() or path.is_symlink():
        return
    for candidate in sorted(path.rglob("*"), reverse=True):
        if candidate.is_dir():
            try:
                candidate.rmdir()
            except OSError:
                pass
    try:
        path.rmdir()
    except OSError:
        pass


def _make_directory_link(link: Path, target: Path) -> str:
    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
        return "symlink"
    except OSError as first_error:
        if os.name != "nt":
            raise SharedAssetError(f"Could not create workflow symlink {link} -> {target}: {first_error}") from first_error
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SharedAssetError(
                f"Could not create workflow junction {link} -> {target}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            ) from first_error
        return "junction"


def configure_shared_workflows(
    comfyui_dir: Path,
    workflows_root: Path,
    *,
    user_name: str = "default",
    migrate_existing: bool = False,
    conflict_policy: str = "preserve",
) -> dict[str, Any]:
    root = comfyui_dir.expanduser().resolve()
    if not (root / "main.py").is_file():
        raise SharedAssetError(f"Not a ComfyUI installation: {root}")
    workflows_root = workflows_root.expanduser().resolve()
    workflows_root.mkdir(parents=True, exist_ok=True)
    link = root / "user" / (user_name or "default") / "workflows"
    moved: list[str] = []
    backup: Path | None = None

    if link.is_symlink():
        if link.resolve() == workflows_root:
            return {"link": str(link), "target": str(workflows_root), "strategy": "existing", "moved": []}
        link.unlink()
    elif link.exists():
        contents = list(link.iterdir()) if link.is_dir() else [link]
        if contents and not migrate_existing:
            raise SharedAssetError(
                f"The local workflow directory is not empty: {link}. Enable migration to merge it into {workflows_root}."
            )
        if link.is_dir() and contents:
            moved = _merge_tree(link, workflows_root, conflict_policy=conflict_policy)
            backup = link.with_name(f"workflows.local-backup-{time.strftime('%Y%m%d-%H%M%S')}")
            if link.exists():
                shutil.move(str(link), str(backup))
        elif link.is_dir():
            _remove_empty_tree(link)
        else:
            raise SharedAssetError(f"Workflow path exists and is not a directory: {link}")

    strategy = _make_directory_link(link, workflows_root)
    metadata_dir = root / ".comfy-setup"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        metadata_dir / "shared-assets.yaml",
        {
            "schema_version": 1,
            "models_root": None,
            "workflows_root": str(workflows_root),
            "workflow_user": user_name or "default",
            "workflow_link": str(link),
            "link_strategy": strategy,
            "migration_backup": str(backup) if backup else None,
        },
    )
    return {
        "link": str(link),
        "target": str(workflows_root),
        "strategy": strategy,
        "moved": moved,
        "backup": str(backup) if backup else None,
    }


def configure_instance_shared_assets(
    comfyui_dir: Path,
    paths: SharedAssetPaths,
    *,
    user_name: str = "default",
    migrate_existing: bool | None = None,
) -> dict[str, Any]:
    create_shared_directories(paths)
    config_file = write_extra_model_paths(comfyui_dir, paths.models)
    workflow_result = configure_shared_workflows(
        comfyui_dir,
        paths.workflows,
        user_name=user_name,
        migrate_existing=paths.migrate_existing if migrate_existing is None else migrate_existing,
        conflict_policy=paths.conflict_policy,
    )
    state_file = comfyui_dir.expanduser().resolve() / ".comfy-setup" / "shared-assets.yaml"
    state = _load_yaml_mapping(state_file)
    state.update(
        {
            "schema_version": 1,
            "models_root": str(paths.models),
            "workflows_root": str(paths.workflows),
            "extra_model_paths_file": str(config_file),
            "workflow_user": user_name,
            "workflow_link": workflow_result["link"],
            "link_strategy": workflow_result["strategy"],
        }
    )
    _write_yaml(state_file, state)
    return {
        "comfyui_dir": str(comfyui_dir.expanduser().resolve()),
        "models": str(paths.models),
        "workflows": str(paths.workflows),
        "extra_model_paths": str(config_file),
        "workflow": workflow_result,
        "state_file": str(state_file),
    }


def apply_shared_assets_to_installations(
    installations: Iterable[Path],
    paths: SharedAssetPaths,
    *,
    migrate_existing: bool | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for installation in installations:
        try:
            results.append(
                {
                    "success": True,
                    **configure_instance_shared_assets(
                        installation,
                        paths,
                        migrate_existing=migrate_existing,
                    ),
                }
            )
        except Exception as exc:
            results.append({"success": False, "comfyui_dir": str(installation), "error": str(exc)})
    return results


def inspect_instance_shared_assets(comfyui_dir: Path) -> InstanceAssetStatus:
    root = comfyui_dir.expanduser().resolve()
    config_file = root / "extra_model_paths.yaml"
    workflow_link = root / "user" / "default" / "workflows"
    models_root: Path | None = None
    workflows_root: Path | None = None
    model_configured = False
    workflow_configured = False

    if config_file.exists():
        payload = _load_yaml_mapping(config_file)
        section = payload.get("comfyui_setup_manager_shared_models")
        if isinstance(section, dict) and section.get("base_path"):
            models_root = Path(str(section["base_path"])).expanduser()
            model_configured = True
    if workflow_link.is_symlink():
        workflows_root = workflow_link.resolve()
        workflow_configured = True
    else:
        state_file = root / ".comfy-setup" / "shared-assets.yaml"
        if state_file.exists():
            state = _load_yaml_mapping(state_file)
            if state.get("workflows_root"):
                workflows_root = Path(str(state["workflows_root"])).expanduser()
                workflow_configured = workflow_link.exists()

    return InstanceAssetStatus(
        comfyui_dir=root,
        models_root=models_root,
        workflows_root=workflows_root,
        extra_model_paths_file=config_file,
        workflow_link=workflow_link,
        model_configured=model_configured,
        workflow_configured=workflow_configured,
    )
