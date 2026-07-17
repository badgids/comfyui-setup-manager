from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

from comfy_setup.cli import _handle_command, build_parser, emit
from comfy_setup.discovery import ComfyInstallation
from comfy_setup.profile import read_profile_bundle, write_profile_bundle


ROOT = Path(__file__).resolve().parents[1]


class CLICommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_json_output_is_machine_readable(self) -> None:
        args = argparse.Namespace(format="json")
        output = io.StringIO()
        with redirect_stdout(output):
            emit({"ok": True, "items": [1, 2]}, args)
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "items": [1, 2]})

    def test_yaml_output_is_machine_readable(self) -> None:
        args = argparse.Namespace(format="yaml")
        output = io.StringIO()
        with redirect_stdout(output):
            emit({"ok": True}, args)
        self.assertEqual(yaml.safe_load(output.getvalue()), {"ok": True})

    def test_profiles_list_contains_builtins(self) -> None:
        args = self.parser.parse_args(["profiles", "list"])
        result = _handle_command(args)
        ids = {item["id"] for item in result}
        self.assertIn("vanilla-comfyui", ids)
        self.assertIn("badgids-comfyui-complete", ids)

    def test_profile_archive_files_read_and_transactional_edit_commands(self) -> None:
        profile = yaml.safe_load(
            (ROOT / "installer/src/comfy_setup/profiles/vanilla.yaml").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temporary:
            archive = write_profile_bundle(profile, Path(temporary) / "editable.comfyuisetup")
            files = _handle_command(self.parser.parse_args(["profiles", "files", str(archive)]))
            self.assertIn("profile.yaml", {item.name for item in files})

            text = _handle_command(
                self.parser.parse_args(["profiles", "read-file", str(archive), "profile.yaml"])
            )
            replacement = yaml.safe_load(text)
            replacement["description"] = "Edited through CLI parity."
            source = Path(temporary) / "replacement.yaml"
            source.write_text(yaml.safe_dump(replacement, sort_keys=False), encoding="utf-8")
            result = _handle_command(
                self.parser.parse_args([
                    "profiles", "edit-file", str(archive), "profile.yaml",
                    "--from-file", str(source),
                ])
            )

            self.assertEqual(result.profile_id, "vanilla-comfyui")
            self.assertEqual(read_profile_bundle(archive)["description"], "Edited through CLI parity.")

    def test_install_plan_is_complete_without_running_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.parser.parse_args([
                "install", "plan", "--profile", "vanilla-comfyui",
                "--target", str(Path(temporary) / "ComfyUI"),
                "--repository-mode", "official",
            ])
            result = _handle_command(args)
        keys = [step["key"] for step in result["steps"]]
        self.assertEqual(keys[:4], ["system", "repo", "venv", "torch"])
        self.assertIn("validate", keys)

    def test_installation_discovery_serializes_results(self) -> None:
        fake = ComfyInstallation(
            path=Path("/tmp/TestComfy"), name="Test", description="Description",
            profile_id=None, profile_name=None, version="1.0", branch="master",
            repository="https://github.com/Comfy-Org/ComfyUI.git", has_venv=True,
            node_count=2, workflow_count=3,
        )
        args = self.parser.parse_args(["installations", "discover"])
        with patch("comfy_setup.cli.discover_installations", return_value=[fake]), \
             patch("comfy_setup.cli.instance_status", return_value={"running": False}):
            result = _handle_command(args)
        self.assertEqual(result[0]["name"], "Test")
        self.assertEqual(result[0]["node_count"], 2)

    def test_config_set_writes_yaml_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict("os.environ", {"COMFYUI_SETUP_CONFIG_DIR": temporary}):
                args = self.parser.parse_args([
                    "config", "set", "manager-config.yaml",
                    "resolver.merge_new_default_sources", "false",
                ])
                _handle_command(args)
                path = Path(temporary) / "manager-config.yaml"
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertFalse(payload["resolver"]["merge_new_default_sources"])
