from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comfy_setup.cli import _handle_command, _resolve_duplicate_node_omissions, build_parser
from comfy_setup.exporter import DuplicateNodeGroup
from comfy_setup.instance_control import instance_status


class CLIEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_uninstall_requires_exact_confirmation(self) -> None:
        args = self.parser.parse_args(["installations", "uninstall", ".", "--confirm", "yes"])
        with self.assertRaises(ValueError):
            _handle_command(args)

    def test_uninstall_refuses_home_directory(self) -> None:
        args = self.parser.parse_args([
            "installations", "uninstall", str(Path.home()), "--confirm", "UNINSTALL"
        ])
        with patch("comfy_setup.cli.is_comfyui_directory", return_value=True):
            with self.assertRaises(ValueError):
                _handle_command(args)

    def test_custom_repository_requires_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.parser.parse_args([
                "install", "plan", "--target", str(Path(temporary) / "ComfyUI"),
                "--repository-mode", "custom",
            ])
            with self.assertRaises(ValueError):
                _handle_command(args)

    def test_unknown_node_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = self.parser.parse_args([
                "install", "plan", "--profile", "vanilla-comfyui",
                "--target", str(Path(temporary) / "ComfyUI"),
                "--nodes", "does-not-exist",
            ])
            with self.assertRaises(ValueError):
                _handle_command(args)

    def test_stale_process_record_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / ".comfy-setup" / "runtime-process.yaml"
            state.parent.mkdir(parents=True)
            state.write_text("pid: 99999999\nstarted_at: 0\ncommand: []\nlog_path: x\n", encoding="utf-8")
            result = instance_status(root)
            self.assertFalse(result["running"])
            self.assertFalse(state.exists())

    def test_interactive_duplicate_node_prompt_displays_full_paths_and_omits_choice(self) -> None:
        class TTYInput(io.StringIO):
            def isatty(self) -> bool:
                return True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = (root / "ComfyUI" / "custom_nodes" / "duplicate-node").resolve()
            second = (root / "shared" / "custom_nodes" / "duplicate-node").resolve()
            stderr = io.StringIO()
            with patch(
                "comfy_setup.cli.duplicate_custom_nodes",
                return_value=[DuplicateNodeGroup("duplicate-node", (first, second))],
            ), patch("sys.stdin", TTYInput("2\n")), patch("sys.stderr", stderr):
                omissions = _resolve_duplicate_node_omissions(root, [], interactive=True)

            self.assertEqual(omissions, {second})
            output = stderr.getvalue()
            self.assertIn(str(first), output)
            self.assertIn(str(second), output)
            self.assertIn(f"Keeping: {first}", output)
            self.assertIn(f"Omitting: {second}", output)

    def test_profiles_from_installation_accepts_repeatable_omit_node_option(self) -> None:
        args = self.parser.parse_args([
            "profiles", "from-installation", "/tmp/ComfyUI", "--name", "Portable",
            "--omit-node", "/tmp/one", "--omit-node", "/tmp/two",
        ])
        self.assertEqual(args.omit_node, [Path("/tmp/one"), Path("/tmp/two")])
