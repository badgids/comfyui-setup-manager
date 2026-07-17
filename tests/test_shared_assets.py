from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from comfy_setup.shared_assets import (
    MODEL_DIRECTORY_MAP,
    SharedAssetError,
    SharedAssetPaths,
    configure_instance_shared_assets,
    create_shared_directories,
    inspect_instance_shared_assets,
    write_extra_model_paths,
)


def fake_comfy(root: Path) -> Path:
    comfy = root / "ComfyUI"
    comfy.mkdir()
    (comfy / "main.py").write_text("print('ok')\n", encoding="utf-8")
    return comfy


def test_create_shared_model_tree_and_extra_model_paths_preserves_user_sections() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        comfy = fake_comfy(root)
        models = root / "shared" / "models"
        workflows = root / "shared" / "workflows"
        create_shared_directories(SharedAssetPaths(models, workflows))
        assert all((models / relative).is_dir() for relative in set(MODEL_DIRECTORY_MAP.values()))

        (comfy / "extra_model_paths.yaml").write_text(
            yaml.safe_dump({"other_ui": {"base_path": "/somewhere", "checkpoints": "models"}}),
            encoding="utf-8",
        )
        path = write_extra_model_paths(comfy, models)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert payload["other_ui"]["base_path"] == "/somewhere"
        assert payload["comfyui_setup_manager_shared_models"]["base_path"] == str(models.resolve())
        assert payload["comfyui_setup_manager_shared_models"]["loras"] == "loras"


def test_configure_instance_migrates_workflows_and_links_shared_library() -> None:
    if os.name == "nt":
        # Junction behavior is covered through command generation on Windows CI.
        return
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        comfy = fake_comfy(root)
        local = comfy / "user" / "default" / "workflows" / "cars"
        local.mkdir(parents=True)
        (local / "build.json").write_text("{}", encoding="utf-8")
        paths = SharedAssetPaths(root / "shared-models", root / "shared-workflows", migrate_existing=True)
        result = configure_instance_shared_assets(comfy, paths, migrate_existing=True)
        link = comfy / "user" / "default" / "workflows"
        assert link.is_symlink()
        assert link.resolve() == paths.workflows.resolve()
        assert (paths.workflows / "cars" / "build.json").is_file()
        assert result["workflow"]["moved"]
        status = inspect_instance_shared_assets(comfy)
        assert status.model_configured is True
        assert status.workflow_configured is True


def test_nonempty_workflow_directory_requires_migration() -> None:
    if os.name == "nt":
        return
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        comfy = fake_comfy(root)
        local = comfy / "user" / "default" / "workflows"
        local.mkdir(parents=True)
        (local / "one.json").write_text("{}", encoding="utf-8")
        paths = SharedAssetPaths(root / "models", root / "workflows")
        try:
            configure_instance_shared_assets(comfy, paths, migrate_existing=False)
        except SharedAssetError as exc:
            assert "Enable migration" in str(exc)
        else:
            raise AssertionError("Expected a migration safety error")
