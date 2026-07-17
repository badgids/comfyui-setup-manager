from __future__ import annotations

import pytest
pytest.importorskip("textual")

import asyncio
import json

import yaml
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Button, Input, Static, Switch

from comfy_setup.app import ComponentsScreen, ComfySetupApp, InstallationScreen
from comfy_setup.models import InstallOptions, InstallResult

PROJECT = Path(__file__).resolve().parents[1]
PROFILE = PROJECT / "installer" / "src" / "comfy_setup" / "profiles" / "badgids-complete.yaml"


class ComponentsAndConsoleTests(unittest.TestCase):
    def test_component_names_and_sources_are_visible(self) -> None:
        async def exercise() -> None:
            profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
            app = ComfySetupApp(initial_profile=profile)
            app.active_profile = profile
            app.configuration = {
                "accelerator": "nvidia",
                "target_dir": Path.cwd() / "ComfyUI",
                "python_version": "3.13",
                "auto_install_system": True,
                "allow_source_builds": True,
                "pin_exact_refs": True,
                "update_existing_nodes": False,
                "install_command_alias": True,
                "backup_builds": False,
                "backup_dir": None,
                "use_current_checkout": False,
            }
            async with app.run_test(size=(120, 44)) as pilot:
                app.push_screen(ComponentsScreen())
                await pilot.pause()
                names = [str(widget.content) for widget in app.screen.query(".component-name").nodes]
                sources = [str(widget.content) for widget in app.screen.query(".component-source").nodes]
                self.assertIn("TRELLIS2", names)
                self.assertIn("ACE-Step", names)
                self.assertEqual(len(names), len(profile["nodes"]) + len(profile["accelerated_packages"]))
                self.assertTrue(any("github.com" in source for source in sources))

        asyncio.run(exercise())

    def test_installation_screen_contains_embedded_console_controls(self) -> None:
        async def exercise() -> None:
            profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
            app = ComfySetupApp(initial_profile=profile)
            app.active_profile = profile
            with tempfile.TemporaryDirectory() as temporary:
                app.options = InstallOptions(
                    target_dir=Path(temporary) / "ComfyUI",
                    python_version="3.13",
                    accelerator="nvidia",
                    selected_nodes=set(),
                    selected_acceleration=set(),
                    auto_install_system=False,
                    install_command_alias=False,
                )
                async with app.run_test(size=(120, 40)) as pilot:
                    screen = InstallationScreen()
                    # Avoid starting a real installation in this structural UI test.
                    screen.start_installation = lambda: None  # type: ignore[method-assign]
                    app.push_screen(screen)
                    await pilot.pause()
                    self.assertIsNotNone(app.screen.query_one("#install-log"))
                    self.assertIsInstance(app.screen.query_one("#shell-command"), Input)
                    self.assertIsInstance(app.screen.query_one("#shell-run"), Button)
                    self.assertIsInstance(app.screen.query_one("#secret-input"), Input)
                    self.assertTrue(app.screen.query_one("#secret-input", Input).password)
                    subtitle = str(app.screen.query_one(".screen-subtitle", Static).content)
                    self.assertIn("stays inside", subtitle)

        asyncio.run(exercise())

    def test_install_worker_finishes_inside_same_console_screen(self) -> None:
        async def exercise() -> None:
            profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
            app = ComfySetupApp(initial_profile=profile)
            app.active_profile = profile
            with tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / "ComfyUI"
                app.options = InstallOptions(
                    target_dir=target,
                    python_version="3.13",
                    accelerator="nvidia",
                    selected_nodes=set(),
                    selected_acceleration=set(),
                    auto_install_system=False,
                    install_command_alias=False,
                )
                result = InstallResult(success=True, target_dir=target)
                with patch("comfy_setup.app.InstallerEngine.install", return_value=result):
                    async with app.run_test(size=(120, 40)) as pilot:
                        screen = InstallationScreen()
                        app.push_screen(screen)
                        await pilot.pause(0.2)
                        self.assertIs(app.screen, screen)
                        self.assertFalse(app.screen.query_one("#shell-command", Input).disabled)
                        status = str(app.screen.query_one("#install-status", Static).content)
                        self.assertIn("Complete", status)

        asyncio.run(exercise())

    def test_unexpected_install_worker_error_restores_tui_controls(self) -> None:
        async def exercise() -> None:
            profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
            app = ComfySetupApp(initial_profile=profile)
            app.active_profile = profile
            with tempfile.TemporaryDirectory() as temporary:
                app.options = InstallOptions(
                    target_dir=Path(temporary) / "ComfyUI",
                    python_version="3.13", accelerator="nvidia",
                    selected_nodes=set(), selected_acceleration=set(),
                    auto_install_system=False, install_command_alias=False,
                )
                with patch("comfy_setup.app.InstallerEngine", side_effect=RuntimeError("worker exploded")):
                    async with app.run_test(size=(120, 40)) as pilot:
                        screen = InstallationScreen()
                        app.push_screen(screen)
                        for _ in range(20):
                            await pilot.pause(0.05)
                            if not screen._installing:
                                break

                        self.assertFalse(screen._installing)
                        self.assertFalse(screen.query_one("#shell-command", Input).disabled)
                        self.assertFalse(screen.query_one("#retry", Button).disabled)
                        self.assertFalse(screen.query_one("#home", Button).disabled)
                        self.assertIn("Needs attention", str(screen.query_one("#install-status", Static).content))
                        self.assertIn("worker exploded", app.install_result.error or "")

        asyncio.run(exercise())



if __name__ == "__main__":
    unittest.main()
