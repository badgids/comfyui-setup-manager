from __future__ import annotations

import pytest
pytest.importorskip("textual")

import asyncio
import unittest

from textual.widgets import Input, Select, Static

from comfy_setup.app import ComfySetupApp, DestinationScreen, HomeScreen, OFFICIAL_COMFYUI_REPOSITORY


class AppRepositoryUITests(unittest.TestCase):
    def test_dashboard_selects_render_current_labels(self) -> None:
        async def exercise() -> None:
            app = ComfySetupApp()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                self.assertIsInstance(app.screen, HomeScreen)
                profile = app.screen.query_one("#profile-select", Select)
                installation = app.screen.query_one("#installation-select", Select)
                self.assertEqual(profile.value, "vanilla-comfyui")
                self.assertIn("Vanilla ComfyUI", str(profile.query_one("#label", Static).content))
                self.assertEqual(installation.value, "__new__")
                self.assertIn("New installation", str(installation.query_one("#label", Static).content))
                self.assertGreater(profile.query_one("#label", Static).region.width, 0)
                self.assertGreater(installation.query_one("#label", Static).region.width, 0)

        asyncio.run(exercise())

    def test_destination_always_offers_official_repository_and_updates_preview(self) -> None:
        async def exercise() -> None:
            app = ComfySetupApp()
            async with app.run_test(size=(120, 44)) as pilot:
                await pilot.pause()
                app.active_profile = app.profile_record("badgids-comfyui-complete").profile.copy()
                app.push_screen(DestinationScreen(None))
                await pilot.pause()
                screen = app.screen
                repository_select = screen.query_one("#repository-mode", Select)
                values = {value for _, value in repository_select._options}
                self.assertIn("official", values)
                self.assertIn("profile", values)
                self.assertIn("custom", values)
                self.assertEqual(repository_select.value, "official")
                self.assertIn("Official ComfyUI", str(repository_select.query_one("#label", Static).content))
                self.assertIn(OFFICIAL_COMFYUI_REPOSITORY, str(screen.query_one("#repository-preview", Static).content))

                repository_select.value = "custom"
                custom = screen.query_one("#custom-repository", Input)
                custom.value = "https://github.com/example/ComfyUI.git"
                await pilot.pause()
                self.assertFalse(custom.disabled)
                self.assertIn("https://github.com/example/ComfyUI.git", str(screen.query_one("#repository-preview", Static).content))

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
