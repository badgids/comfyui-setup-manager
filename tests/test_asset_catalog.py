from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from comfy_setup.asset_catalog import (
    add_entry,
    clear_tasks,
    delete_asset,
    download_entry,
    export_asset,
    import_asset,
    list_installed_assets,
    list_tasks,
    retry_task,
)
from comfy_setup.shared_assets import SharedAssetPaths, save_shared_asset_paths


def test_local_catalog_download_import_delete_and_task_history() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config = root / "config"
        models = root / "shared" / "models"
        workflows = root / "shared" / "workflows"
        source = root / "source.safetensors"
        source.write_bytes(b"model-bytes")
        with patch.dict("os.environ", {"COMFYUI_SETUP_CONFIG_DIR": str(config)}):
            save_shared_asset_paths(SharedAssetPaths(models, workflows))
            add_entry(
                "models",
                {
                    "id": "local-test-model",
                    "name": "Local test model",
                    "source_id": "local",
                    "url": source.as_uri(),
                    "destination": "checkpoints/testing",
                },
            )
            task = download_entry("models", "local-test-model")
            assert task.status == "completed"
            destination = models / "checkpoints" / "testing" / source.name
            assert destination.read_bytes() == b"model-bytes"
            original_task = next(item for item in list_tasks() if item.asset_id == "local-test-model")
            assert original_task.status == "completed"
            retried = retry_task(original_task.id, overwrite=True)
            assert retried.status == "completed"
            assert clear_tasks("models") == 2
            assert not [item for item in list_tasks() if item.kind == "models"]

            imported_source = root / "loramix.safetensors"
            imported_source.write_bytes(b"lora")
            imported = import_asset("loras", imported_source)
            assert imported == models / "loras" / imported_source.name
            installed = list_installed_assets("models")
            assert {item["relative_path"] for item in installed} == {
                "checkpoints/testing/source.safetensors",
            }
            assert {item["relative_path"] for item in list_installed_assets("loras")} == {
                "loramix.safetensors",
            }
            exported = export_asset(
                "models",
                "checkpoints/testing/source.safetensors",
                root / "exports",
            )
            assert exported.read_bytes() == b"model-bytes"
            deleted = delete_asset("loras", "loramix.safetensors")
            assert not deleted.exists()


def test_catalog_files_are_yaml() -> None:
    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp)
        with patch.dict("os.environ", {"COMFYUI_SETUP_CONFIG_DIR": str(config)}):
            from comfy_setup.configuration import ensure_editable_config

            for name in ("asset-paths.yaml", "model-sources.yaml", "workflow-sources.yaml", "download-tasks.yaml"):
                path = ensure_editable_config(name)
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                assert isinstance(payload, dict)
