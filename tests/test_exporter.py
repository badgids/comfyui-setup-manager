from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

from comfy_setup.exporter import export_setup
from comfy_setup.models import InstallOptions, PlatformInfo
from comfy_setup.engine import InstallerEngine
from comfy_setup.profile import read_profile_bundle
from comfy_setup.configuration import save_editable_config


class ExporterTests(unittest.TestCase):
    def _git(self, path: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, stdout=subprocess.DEVNULL)

    def test_exact_export_pins_clean_nodes_and_uses_registry_for_public_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            comfy = root / "ComfyUI"
            custom = comfy / "custom_nodes"
            custom.mkdir(parents=True)
            (comfy / "comfy").mkdir()
            (comfy / "main.py").write_text("print('ok')\n")
            (comfy / "requirements.txt").write_text("\n")
            self._git(comfy, "init")
            self._git(comfy, "config", "user.email", "test@example.invalid")
            self._git(comfy, "config", "user.name", "Test")
            self._git(comfy, "add", ".")
            self._git(comfy, "commit", "-m", "base")
            self._git(comfy, "remote", "add", "origin", "https://github.com/example/ComfyUI.git")

            remote = custom / "remote-node"
            remote.mkdir()
            (remote / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n")
            (remote / "requirements.txt").write_text("requests\n")
            self._git(remote, "init")
            self._git(remote, "config", "user.email", "test@example.invalid")
            self._git(remote, "config", "user.name", "Test")
            self._git(remote, "add", ".")
            self._git(remote, "commit", "-m", "remote")
            self._git(remote, "remote", "add", "origin", "https://github.com/example/remote-node.git")

            manager_only = custom / "registry-node"
            manager_only.mkdir()
            (manager_only / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n")
            (manager_only / "large-public-cache.bin").write_bytes(b"x" * (8 * 1024 * 1024))
            (manager_only / "pyproject.toml").write_text(
                "[project]\nname = \"registry-node\"\nversion = \"1.2.3\"\n"
                "[tool.comfy]\nPublisherId = \"example-publisher\"\nDisplayName = \"Registry Node\"\n"
            )

            local = custom / "local-private-node"
            local.mkdir()
            (local / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n")
            (local / ".env").write_text("TOKEN=secret\n")
            (local / "models").mkdir()
            (local / "models" / "large.safetensors").write_bytes(b"model")

            config_dir = root / "manager-config"
            old_config = os.environ.get("COMFYUI_SETUP_CONFIG_DIR")
            os.environ["COMFYUI_SETUP_CONFIG_DIR"] = str(config_dir)
            try:
                save_editable_config(
                    "custom-node-sources.yaml",
                    {
                        "schema_version": 1,
                        "network": {"enabled": False, "timeout_seconds": 1},
                        "sources": [],
                        "mappings": [],
                    },
                )
                save_editable_config(
                    "model-sources.yaml",
                    {
                        "schema_version": 1,
                        "sources": [
                            {"id": "publisher", "name": "Publisher", "kind": "direct-url", "base_url": "https://example.com", "enabled": True},
                            {"id": "local", "name": "Local", "kind": "local", "base_url": None, "enabled": True},
                        ],
                        "entries": [
                            {"id": "model", "name": "Model", "source_id": "publisher", "url": "https://example.com/model.safetensors", "destination": "checkpoints"},
                            {"id": "private", "name": "Private", "source_id": "local", "url": str(root / "private.safetensors")},
                        ],
                    },
                )
                save_editable_config(
                    "workflow-sources.yaml",
                    {
                        "schema_version": 1,
                        "sources": [{"id": "github", "name": "GitHub", "kind": "direct-url", "base_url": "https://github.com", "enabled": True}],
                        "entries": [{"id": "workflow", "name": "Workflow", "source_id": "github", "url": "https://github.com/example/workflow.json"}],
                    },
                )
                output = root / "setup.comfyuisetup"
                fake_packages = {"click": "8.1.8", "numpy": "2.4.4", "torch": "2.12.1+cu130"}
                fake_distributions = [
                    {"name": "click", "version": "8.1.8", "direct_url": None},
                    {"name": "numpy", "version": "2.4.4", "direct_url": None},
                    {"name": "torch", "version": "2.12.1+cu130", "direct_url": None},
                ]
                with patch(
                    "comfy_setup.exporter._venv_python", return_value=Path("/fake/python")
                ), patch(
                    "comfy_setup.exporter._packages",
                    return_value=(
                        fake_packages,
                        ["click==8.1.8"],
                        "3.13",
                        fake_distributions,
                        {
                            "python": "3.13.5",
                            "implementation": "CPython",
                            "os": "linux",
                            "architecture": "x86_64",
                            "accelerator": "nvidia",
                            "torch_version": "2.12.1+cu130",
                            "torch_cuda": "13.0",
                            "torch_hip": None,
                        },
                    ),
                ), patch(
                    "comfy_setup.exporter._commit_is_known_on_origin", return_value=True
                ):
                    export_setup(comfy, output, name="Portable Test")
                profile = read_profile_bundle(output)
            finally:
                if old_config is None:
                    os.environ.pop("COMFYUI_SETUP_CONFIG_DIR", None)
                else:
                    os.environ["COMFYUI_SETUP_CONFIG_DIR"] = old_config
            nodes = {node["id"]: node for node in profile["nodes"]}
            self.assertTrue(profile["libraries"]["shared_models"]["enabled"])
            self.assertTrue(profile["libraries"]["shared_workflows"]["enabled"])
            self.assertFalse(profile["libraries"]["paths_embedded"])
            self.assertEqual([item["id"] for item in profile["models"]], ["model"])
            self.assertEqual([item["id"] for item in profile["workflows"]], ["workflow"])
            self.assertNotIn(str(root), json.dumps({k: v for k, v in profile.items() if k != "_bundle_path"}))
            self.assertEqual(nodes["remote-node"]["source"]["type"], "remote")
            self.assertEqual(nodes["registry-node"]["source"]["type"], "remote")
            self.assertEqual(nodes["registry-node"]["source"]["manager_id"], "registry-node")
            self.assertEqual(nodes["registry-node"]["source"]["preferred"], "manager")
            self.assertNotIn("repository", nodes["registry-node"]["source"])
            self.assertTrue(nodes["remote-node"]["source"]["exact"])
            self.assertTrue(profile["comfyui"]["exact"])
            self.assertEqual(profile["environment_lock"]["packages"][0]["name"], "click")
            self.assertEqual(profile["python"]["fallbacks"], ["3.13"])
            self.assertEqual(profile["extra_python_packages"], [])
            self.assertEqual(nodes["local-private-node"]["source"]["type"], "embedded")

            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertFalse(any(name.startswith("embedded_plugins/remote-node/") for name in names))
                self.assertIn("environment-lock.yaml", names)
                self.assertFalse(any(name.startswith("embedded_plugins/registry-node/") for name in names))
                self.assertNotIn("large-public-cache.bin", "\n".join(names))
                self.assertLess(output.stat().st_size, 2 * 1024 * 1024)
                self.assertIn(
                    "embedded_plugins/local-private-node/__init__.py",
                    names,
                )
                self.assertFalse(any(name.endswith(".safetensors") for name in names))
                self.assertFalse(any(name.endswith("/.env") for name in names))

            target = root / "TargetComfyUI"
            target.mkdir()
            info = PlatformInfo(
                os_name="linux",
                system="Linux",
                release="test",
                architecture="x86_64",
                is_wsl=False,
                package_manager=None,
                accelerator="cpu",
            )
            options = InstallOptions(
                target_dir=target,
                python_version="3.12",
                accelerator="cpu",
                selected_nodes={"local-private-node"},
                selected_acceleration=set(),
                auto_install_system=False,
                allow_source_builds=False,
                use_current_checkout=False,
            )
            engine = InstallerEngine(profile, info, options)
            extracted = engine._extract_embedded_node(nodes["local-private-node"])
            self.assertTrue((extracted / "__init__.py").is_file())


if __name__ == "__main__":
    unittest.main()
