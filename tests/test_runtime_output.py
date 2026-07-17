from __future__ import annotations

import pytest
pytest.importorskip("textual")

import asyncio
import tempfile
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from textual.containers import ScrollableContainer
from textual.widgets import Button

from comfy_setup.selectable_widgets import SelectableLog as RichLog
from comfy_setup.app import ComfySetupApp, HomeScreen, InstallationScreen
from comfy_setup.discovery import ComfyInstallation
from comfy_setup.models import InstallOptions
from comfy_setup.launchers import launch_detached, stop_process
from comfy_setup.terminal_output import TerminalStreamDecoder


def fake_installation(path: Path) -> ComfyInstallation:
    return ComfyInstallation(
        path=path,
        name="Live Test",
        description="Runtime streaming test.",
        profile_id="test",
        profile_name="Test Profile",
        version="1.0",
        branch="master",
        repository="https://github.com/Comfy-Org/ComfyUI.git",
        has_venv=True,
        node_count=1,
        workflow_count=1,
    )


def cached_state(*installations: ComfyInstallation) -> dict:
    records = []
    for item in installations:
        records.append({
            "path": str(item.path),
            "name": item.name,
            "description": item.description,
            "profile_id": item.profile_id,
            "profile_name": item.profile_name,
            "version": item.version,
            "commit": item.commit,
            "branch": item.branch,
            "repository": item.repository,
            "has_venv": item.has_venv,
            "node_count": item.node_count,
            "workflow_count": item.workflow_count,
        })
    return {
        "schema_version": 1,
        "initialized": True,
        "platform": {},
        "installations": records,
        "nodes": {str(item.path): [] for item in installations},
        "instance_workflows": {str(item.path): [] for item in installations},
        "assets": {"models": [], "loras": [], "workflows": []},
    }


class TerminalOutputTests(unittest.TestCase):
    def test_decoder_removes_ansi_controls_and_carriage_artifacts(self) -> None:
        decoder = TerminalStreamDecoder()
        lines = decoder.feed(b"\x1b[31mERROR\x1b[0m first\rprogress 50%\rprogress 100%\nnext\b!\n")
        lines.extend(decoder.flush())
        self.assertEqual(lines, ["ERROR first", "progress 50%", "progress 100%", "nex!"])
        self.assertFalse(any("\x1b" in line for line in lines))

    def test_local_launcher_is_unbuffered_before_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ComfyUI"
            python_path = root / ".venv" / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.symlink_to(Path(sys.executable))
            (root / "main.py").write_text(
                "import time\nprint('first live line')\ntime.sleep(2)\nprint('second line')\n",
                encoding="utf-8",
            )
            process = launch_detached(root)
            runtime_log = root / ".comfy-setup" / "runtime.log"
            deadline = time.monotonic() + 1.5
            text = ""
            while time.monotonic() < deadline:
                if runtime_log.is_file():
                    text = runtime_log.read_text(encoding="utf-8", errors="replace")
                    if "first live line" in text:
                        break
                time.sleep(0.05)
            try:
                self.assertIn("first live line", text)
                self.assertIsNone(process.poll(), "The first line must be visible before process exit")
            finally:
                stop_process(process)

    def test_manager_runtime_log_streams_while_instance_is_running(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "ComfyUI"
                runtime_log = root / ".comfy-setup" / "runtime.log"
                runtime_log.parent.mkdir(parents=True)
                runtime_log.write_bytes(b"starting\n")
                installation = fake_installation(root)
                with patch("comfy_setup.app.load_runtime_state", return_value=cached_state(installation)):
                    app = ComfySetupApp()
                    async with app.run_test(size=(100, 28)) as pilot:
                        # Startup inventory refresh is deliberately presented in a
                        # progress screen.  Wait for that asynchronous transition
                        # instead of depending on a fixed machine-speed delay.
                        for _ in range(30):
                            await pilot.pause(0.1)
                            if isinstance(app.screen, HomeScreen):
                                break
                        screen = app.screen
                        self.assertIsInstance(screen, HomeScreen)
                        with runtime_log.open("ab") as handle:
                            handle.write(b"\x1b[32mlive line\x1b[0m\nprogress 10%\rprogress 100%\n")
                            handle.flush()
                        await pilot.pause(0.5)
                        self.assertIn("live line", screen._runtime_lines)
                        self.assertIn("progress 100%", screen._runtime_lines)
                        self.assertFalse(any("\x1b" in line for line in screen._runtime_lines))

        asyncio.run(exercise())

    def test_install_console_treats_bracketed_paths_as_literal_text(self) -> None:
        async def exercise() -> None:
            app = ComfySetupApp()
            with tempfile.TemporaryDirectory() as temporary:
                app.options = InstallOptions(
                    target_dir=Path(temporary) / "ComfyUI",
                    python_version="3.13",
                    accelerator="cpu",
                    selected_nodes=set(),
                    selected_acceleration=set(),
                    auto_install_system=False,
                    install_command_alias=False,
                )
                async with app.run_test(size=(90, 28)) as pilot:
                    screen = InstallationScreen()
                    screen.start_installation = lambda: None  # type: ignore[method-assign]
                    app.push_screen(screen)
                    await pilot.pause()
                    screen.append_log(
                        "ERROR: closing tag '[/home/example/.ce/pixi.toml:89:1]' does not match"
                    )
                    await pilot.pause()
                    self.assertIs(app.screen, screen)
                    self.assertFalse(screen.query_one("#install-log").markup)

        asyncio.run(exercise())

    def test_small_home_screen_is_scrollable_and_continue_is_not_clipped(self) -> None:
        async def exercise() -> None:
            with patch("comfy_setup.app.load_runtime_state", return_value=cached_state()):
                app = ComfySetupApp()
                async with app.run_test(size=(72, 20)) as pilot:
                    await pilot.pause()
                    screen = app.screen
                    self.assertIsInstance(screen, HomeScreen)
                    wizard = screen.query_one("#wizard", ScrollableContainer)
                    self.assertGreaterEqual(wizard.virtual_size.height, wizard.size.height)
                    button = screen.query_one("#continue", Button)
                    # Header and footer occupy one row each; the action button
                    # must remain entirely inside the usable screen area.
                    self.assertLessEqual(button.region.bottom, app.size.height - 1)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
