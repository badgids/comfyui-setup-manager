from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from comfy_setup.cli import build_parser, _handle_command


def test_new_cli_commands_parse() -> None:
    parser = build_parser()
    commands = [
        ["shared-paths", "status"],
        ["shared-paths", "configure", "--models-dir", "/tmp/models", "--workflows-dir", "/tmp/workflows"],
        ["models", "list"],
        ["models", "export", "checkpoints/model.safetensors", "/tmp/export"],
        ["models", "sources"],
        ["models", "tasks"],
        ["models", "clear-tasks"],
        ["models", "retry-task", "task-id"],
        ["models", "add-entry", "--id", "x", "--name", "X", "--source-id", "local", "--url", "file:///x"],
        ["workflows", "sources"],
        ["workflows", "tasks"],
        ["workflows", "clear-tasks"],
        ["workflows", "retry-task", "task-id"],
    ]
    for command in commands:
        assert parser.parse_args(command)


def test_shared_paths_configure_cli_creates_directories() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config = root / "config"
        parser = build_parser()
        args = parser.parse_args(
            [
                "shared-paths",
                "configure",
                "--models-dir",
                str(root / "models"),
                "--workflows-dir",
                str(root / "workflows"),
            ]
        )
        with patch.dict("os.environ", {"COMFYUI_SETUP_CONFIG_DIR": str(config)}):
            result = _handle_command(args)
        assert Path(result["paths"]["models"]).is_dir()
        assert Path(result["paths"]["workflows"]).is_dir()
