from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml

from comfy_setup.exporter import build_profile_from_installation
from comfy_setup.profile import read_profile_bundle, write_profile_bundle


def _init_core_repo(comfy: Path) -> None:
    (comfy / "comfy").mkdir(parents=True)
    (comfy / "main.py").write_text("print('base')\n", encoding="utf-8")
    (comfy / "requirements.txt").write_text("click>=8\nnumpy<2.5\n", encoding="utf-8")
    (comfy / "pyproject.toml").write_text(
        "[project]\nname='comfyui-test'\nversion='1.0'\ndependencies=['click>=8']\n",
        encoding="utf-8",
    )
    (comfy / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", str(comfy)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(comfy), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(comfy), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(comfy), "add", "comfy", "main.py", "requirements.txt", "pyproject.toml", "uv.lock"], check=True)
    subprocess.run(["git", "-C", str(comfy), "commit", "-m", "base"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(comfy), "remote", "add", "origin", "https://github.com/Comfy-Org/ComfyUI.git"], check=True)


def test_profile_is_reconstruction_manifest_not_checkout_backup(tmp_path: Path, monkeypatch) -> None:
    comfy = tmp_path / "ComfyUI"
    _init_core_repo(comfy)
    (comfy / "main.py").write_text("print('local patch')\n", encoding="utf-8")
    (comfy / "extra_model_paths.yaml").write_text("machine: /private/path\n", encoding="utf-8")
    (comfy / ".comfy-setup").mkdir()
    (comfy / ".comfy-setup" / "shared-assets.yaml").write_text("models: /private/models\n", encoding="utf-8")
    (comfy / ".venv").mkdir()
    (comfy / ".venv" / "huge.bin").write_bytes(b"v" * (8 * 1024 * 1024))

    known = comfy / "custom_nodes" / "ComfyUI-GGUF"
    known.mkdir(parents=True)
    (known / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    (known / "requirements.txt").write_text("numpy>=1.26\n", encoding="utf-8")
    (known / "backend").mkdir()
    (known / "backend" / "requirements-cuda.txt").write_text("packaging>=24\n", encoding="utf-8")
    (known / ".venv").mkdir()
    (known / ".venv" / "requirements-hidden.txt").write_text("secret-package\n", encoding="utf-8")
    (known / "huge-public-node.bin").write_bytes(b"n" * (8 * 1024 * 1024))
    unresolved = comfy / "custom_nodes" / "private_local_plugin.py"
    unresolved.write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")

    manager_cache = comfy / "user" / "__manager" / "cache" / "daily_custom-node-list.json"
    manager_cache.parent.mkdir(parents=True)
    manager_cache.write_text(
        json.dumps(
            {
                "custom_nodes": [
                    {
                        "id": "comfyui-gguf",
                        "title": "ComfyUI-GGUF",
                        "repository": "https://github.com/city96/ComfyUI-GGUF",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "custom-node-sources.yaml").write_text(
        "schema_version: 2\nnetwork:\n  enabled: false\nsources: []\nmappings: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_SETUP_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("COMFYUI_SETUP_MANAGER_NODE_CATALOG", str(tmp_path / "missing-catalog.json"))

    distributions = [
        {"name": "click", "version": "8.1.8", "direct_url": None},
        {"name": "numpy", "version": "2.4.4", "direct_url": None},
        {
            "name": "comfyui-gguf",
            "version": "1.0",
            "direct_url": {"url": known.as_uri(), "dir_info": {"editable": True}},
        },
        {"name": "torch", "version": "2.12.1+cu130", "direct_url": None},
    ]
    package_result = (
        {"click": "8.1.8", "numpy": "2.4.4", "comfyui-gguf": "1.0", "torch": "2.12.1+cu130"},
        ["click==8.1.8"],
        "3.13",
        distributions,
        {
            "python": "3.13.5",
            "implementation": "CPython",
            "os": "linux",
            "architecture": "x86_64",
            "accelerator": "nvidia",
            "torch_version": "2.12.1+cu130",
            "torch_cuda": "13.0",
        },
    )
    with patch("comfy_setup.exporter._venv_python", return_value=Path("/fake/python")), patch(
        "comfy_setup.exporter._packages", return_value=package_result
    ), patch("comfy_setup.exporter._commit_is_known_on_origin", return_value=True):
        profile, warnings = build_profile_from_installation(comfy, name="Reference First")

    by_id = {node["id"]: node for node in profile["nodes"]}
    assert by_id["comfyui-gguf"]["source"]["type"] == "remote"
    assert by_id["comfyui-gguf"]["source"]["manager_id"] == "comfyui-gguf"
    assert by_id["private-local-plugin"]["source"]["type"] == "embedded"
    assert set(profile["_embedded_plugin_paths"]) == {"private-local-plugin"}
    assert profile["comfyui"]["source"]["type"] == "remote"
    assert profile["comfyui"]["source"]["overlay"]["changed_files"] == ["main.py"]
    assert not any("ComfyUI-GGUF" in warning and "embedded" in warning for warning in warnings)

    output = write_profile_bundle(profile, tmp_path / "reference-first.comfyuisetup")
    assert output.stat().st_size < 1024 * 1024
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "custom_nodes.yml" in names
        assert "environment-lock.yaml" in names
        assert "dependency-manifests.yml" in names
        assert "dependency_manifests/comfyui/requirements.txt" in names
        assert "dependency_manifests/comfyui/pyproject.toml" in names
        assert "dependency_manifests/comfyui/uv.lock" in names
        assert "dependency_manifests/custom_nodes/comfyui-gguf/requirements.txt" in names
        assert "dependency_manifests/custom_nodes/comfyui-gguf/backend/requirements-cuda.txt" in names
        assert not any("requirements-hidden.txt" in name for name in names)
        assert "comfyui_overlay/main.py" in names
        assert not any(name.startswith("embedded_comfyui/") for name in names)
        assert not any("huge-public-node.bin" in name for name in names)
        assert not any("huge.bin" in name for name in names)
        assert not any("extra_model_paths.yaml" in name for name in names)
        assert not any("shared-assets.yaml" in name for name in names)
        assert any(name.startswith("embedded_plugins/private-local-plugin/") for name in names)
        profile_yaml = yaml.safe_load(archive.read("profile.yaml"))
        assert "nodes" not in profile_yaml
        assert "dependency_manifests" not in profile_yaml
        custom_nodes = yaml.safe_load(archive.read("custom_nodes.yml"))
        public = next(node for node in custom_nodes["nodes"] if node["id"] == "comfyui-gguf")
        assert public["source"]["repository"] == "https://github.com/city96/ComfyUI-GGUF"

    loaded = read_profile_bundle(output)
    second = write_profile_bundle(loaded, tmp_path / "reference-first-copy.comfyuisetup")
    with zipfile.ZipFile(second) as archive:
        assert archive.read("comfyui_overlay/main.py") == b"print('local patch')\n"
        assert "dependency_manifests/comfyui/uv.lock" in archive.namelist()
