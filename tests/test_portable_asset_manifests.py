from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import yaml

from comfy_setup.profile import default_profile, read_profile_bundle, write_profile_bundle
from comfy_setup.workflows import read_workflow_pack, write_workflow_pack


def workflow(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"nodes": [{"id": 1, "type": "CheckpointLoaderSimple"}]}', encoding="utf-8")
    return path


def test_comfyuisetup_contains_separate_yaml_manifests() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        profile = default_profile().copy()
        profile["models"] = [
            {"id": "sample-model", "name": "Sample model", "url": "https://example.invalid/model.safetensors", "destination": "checkpoints", "required": False}
        ]
        profile["workflows"] = [
            {"id": "sample-workflow", "name": "Sample workflow", "url": "https://example.invalid/workflow.json", "destination": "examples", "required": False}
        ]
        profile["libraries"] = {"external_models": True, "external_workflows": True}
        output = write_profile_bundle(profile, root / "portable")
        assert output.name.endswith(".comfyuisetup")
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            assert {"profile.yaml", "models.yaml", "workflows.yaml", "asset-sources.yaml", "libraries.yaml"} <= names
            assert yaml.safe_load(archive.read("models.yaml"))["models"][0]["id"] == "sample-model"
        loaded = read_profile_bundle(output)
        assert loaded["models"][0]["id"] == "sample-model"
        assert loaded["libraries"]["external_workflows"] is True


def test_workflow_pack_contains_bundle_yaml_and_companion_requirements() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = workflow(root / "source" / "nested" / "one.json")
        models_yaml = root / "models.yaml"
        models_yaml.write_text("models:\n  - id: required-model\n    name: Required model\n", encoding="utf-8")
        nodes_yaml = root / "nodes.yaml"
        nodes_yaml.write_text("nodes:\n  - id: required-node\n    repository: https://github.com/example/example.git\n", encoding="utf-8")
        output = write_workflow_pack(
            [source],
            root / "pack",
            name="Portable pack",
            source_root=root / "source",
            companion_yaml_paths=[models_yaml, nodes_yaml],
        )
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            assert "bundle.yaml" in names
            assert "bundle.json" not in names
            assert "models.yaml" in names
            assert "nodes.yaml" in names
            manifest = yaml.safe_load(archive.read("bundle.yaml"))
            assert manifest["companion_yaml"] == ["models.yaml", "nodes.yaml"]
        manifest, entries = read_workflow_pack(output)
        assert manifest["workflow_count"] == 1
        assert len(entries) == 1
