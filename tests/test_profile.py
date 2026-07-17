from __future__ import annotations

import hashlib
import json

import yaml
import tempfile
import unittest
import zipfile
from pathlib import Path

from comfy_setup.profile import (
    PROFILE_SCHEMA,
    ProfileError,
    edit_profile_bundle_file,
    list_profile_bundle_files,
    read_profile_bundle,
    read_profile_bundle_file,
    validate_profile,
    write_profile_bundle,
)

PROJECT = Path(__file__).resolve().parents[1]
PROFILE = PROJECT / "installer" / "src" / "comfy_setup" / "profiles" / "badgids-complete.yaml"
VANILLA = PROJECT / "installer" / "src" / "comfy_setup" / "profiles" / "vanilla.yaml"


class ProfileTests(unittest.TestCase):
    def test_profiles_are_schema_three(self) -> None:
        for path in (PROFILE, VANILLA):
            profile = validate_profile(yaml.safe_load(path.read_text()))
            self.assertEqual(profile["schema_version"], PROFILE_SCHEMA)
            self.assertEqual(profile["kind"], "comfyui-setup-profile")

    def test_inventory_nodes_are_unique_and_remote(self) -> None:
        profile = validate_profile(yaml.safe_load(PROFILE.read_text()))
        node_ids = [node["id"] for node in profile["nodes"]]
        folders = [node["folder"].lower() for node in profile["nodes"]]
        self.assertEqual(len(node_ids), len(set(node_ids)))
        self.assertEqual(len(folders), len(set(folders)))
        self.assertIn("ace-step", node_ids)
        self.assertIn("qwen3-tts", node_ids)
        self.assertIn("trellis2", node_ids)
        for node in profile["nodes"]:
            self.assertEqual(node["source"]["type"], "remote")
            self.assertTrue(
                node["source"].get("repository")
                or node["source"].get("manager_id")
            )

    def test_no_machine_specific_paths(self) -> None:
        text = PROFILE.read_text()
        forbidden = ["/home/", "/Users/", "\\\\Users\\\\", "/" + "mnt" + "/"]
        for token in forbidden:
            self.assertNotIn(token, text)

    def test_remote_bundle_contains_no_plugin_source(self) -> None:
        profile = yaml.safe_load(PROFILE.read_text())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "remote.comfysetup"
            write_profile_bundle(profile, output)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("profile.yaml", names)
                self.assertIn("metadata.yaml", names)
                self.assertNotIn("profile.json", names)
                self.assertFalse(
                    any(name.startswith("embedded_plugins/") for name in names)
                )

    def test_embeds_only_explicit_unpublished_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "my-private-node"
            plugin.mkdir()
            (plugin / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n")
            (plugin / "models").mkdir()
            (plugin / "models" / "secret.safetensors").write_bytes(b"model")
            (plugin / ".env").write_text("TOKEN=secret\n")

            profile = yaml.safe_load(VANILLA.read_text())
            profile["id"] = "embedded-test"
            profile["name"] = "Embedded Test"
            profile["nodes"] = [
                {
                    "id": "my-private-node",
                    "name": "My Private Node",
                    "folder": "my-private-node",
                    "source": {
                        "type": "embedded",
                        "payload": "embedded_plugins/my-private-node",
                        "layout": "directory",
                    },
                    "platforms": ["windows", "linux", "macos"],
                    "accelerators": ["nvidia", "rocm", "mps", "cpu"],
                    "selected": True,
                }
            ]
            profile["_embedded_plugin_paths"] = {"my-private-node": str(plugin)}
            output = root / "embedded.comfysetup"
            write_profile_bundle(profile, output)

            loaded = read_profile_bundle(output)
            self.assertEqual(loaded["nodes"][0]["source"]["type"], "embedded")
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn(
                    "embedded_plugins/my-private-node/__init__.py",
                    names,
                )
                self.assertFalse(any("safetensors" in name for name in names))
                self.assertFalse(any(name.endswith("/.env") for name in names))

    def test_schema_two_profile_upgrades_to_remote_source(self) -> None:
        profile = yaml.safe_load(VANILLA.read_text())
        profile["schema_version"] = 2
        profile["nodes"] = [
            {
                "id": "legacy",
                "name": "Legacy",
                "folder": "legacy",
                "repository": "https://github.com/example/legacy.git",
                "ref": "main",
            }
        ]
        upgraded = validate_profile(profile)
        self.assertEqual(upgraded["schema_version"], PROFILE_SCHEMA)
        self.assertEqual(upgraded["nodes"][0]["source"]["type"], "remote")

    def test_portable_bundle_round_trip(self) -> None:
        profile = yaml.safe_load(VANILLA.read_text())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "vanilla.comfysetup"
            write_profile_bundle(profile, output)
            loaded = read_profile_bundle(output)
            self.assertEqual(loaded["id"], "vanilla-comfyui")
            self.assertEqual(Path(loaded["_bundle_path"]), output.resolve())

    def test_profile_member_edit_is_transactional_and_regenerates_integrity(self) -> None:
        profile = yaml.safe_load(VANILLA.read_text())
        with tempfile.TemporaryDirectory() as temp:
            output = write_profile_bundle(profile, Path(temp) / "editable.comfyuisetup")
            with zipfile.ZipFile(output, "a") as archive:
                archive.writestr("notes/custom.txt", "forward-compatible support file\n")

            files = {item.name: item for item in list_profile_bundle_files(output)}
            self.assertTrue(files["profile.yaml"].editable)
            self.assertEqual(files["profile.yaml"].language, "yaml")
            self.assertFalse(files["metadata.yaml"].editable)

            document = yaml.safe_load(read_profile_bundle_file(output, "profile.yaml"))
            document["description"] = "Edited safely inside the archive."
            result = edit_profile_bundle_file(
                output,
                "profile.yaml",
                yaml.safe_dump(document, sort_keys=False),
            )
            self.assertEqual(result.profile_id, "vanilla-comfyui")
            self.assertEqual(read_profile_bundle(output)["description"], "Edited safely inside the archive.")
            with zipfile.ZipFile(output) as archive:
                metadata = yaml.safe_load(archive.read("metadata.yaml"))
                self.assertEqual(metadata["sha256"], hashlib.sha256(archive.read("profile.yaml")).hexdigest())
                self.assertEqual(archive.read("notes/custom.txt"), b"forward-compatible support file\n")

    def test_invalid_profile_member_edit_never_replaces_original(self) -> None:
        profile = yaml.safe_load(VANILLA.read_text())
        with tempfile.TemporaryDirectory() as temp:
            output = write_profile_bundle(profile, Path(temp) / "editable.comfyuisetup")
            before = output.read_bytes()
            with self.assertRaises(ProfileError):
                edit_profile_bundle_file(output, "profile.yaml", "schema_version: [broken")
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(read_profile_bundle(output)["id"], "vanilla-comfyui")


if __name__ == "__main__":
    unittest.main()
