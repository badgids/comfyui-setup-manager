from __future__ import annotations

import pytest
pytest.importorskip("textual")

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Select, Static

from comfy_setup.app import CompleteScreen, ComfySetupApp, HomeScreen, ScanProgressScreen
from comfy_setup.models import InstallResult


class ReturnHomeTests(unittest.TestCase):
    def test_complete_screen_returns_to_dashboard_and_selects_new_installation(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / "ComfyUI"
                (target / "comfy").mkdir(parents=True)
                (target / "main.py").write_text("# test\n", encoding="utf-8")
                (target / "requirements.txt").write_text("", encoding="utf-8")

                app = ComfySetupApp()
                scan_started = threading.Event()
                scan_release = threading.Event()
                state = dict(app.runtime_state)
                state.update({
                    "initialized": True,
                    "installations": [{
                        "path": str(target.resolve()), "name": "ComfyUI", "description": "Test",
                        "profile_id": None, "profile_name": None, "version": None, "commit": None,
                        "branch": None, "repository": None, "has_venv": False,
                        "node_count": 0, "workflow_count": 0,
                    }],
                    "nodes": {}, "instance_workflows": {},
                    "assets": {"models": [], "loras": [], "workflows": []},
                })

                def delayed_scan(*args, **kwargs):
                    progress = kwargs.get("progress")
                    if progress:
                        progress(1, 6, "Discovering the completed installation…")
                    scan_started.set()
                    scan_release.wait(timeout=5)
                    return state

                with patch("comfy_setup.app.scan_runtime_state", side_effect=delayed_scan):
                    async with app.run_test(size=(120, 40)) as pilot:
                        await pilot.pause()
                        self.assertIsInstance(app.screen, HomeScreen)

                        app.install_result = InstallResult(success=True, target_dir=target)
                        app.push_screen(CompleteScreen())
                        await pilot.pause()
                        self.assertIsInstance(app.screen, CompleteScreen)

                        await pilot.click("#home")
                        await pilot.pause()

                        self.assertTrue(scan_started.wait(timeout=2))
                        self.assertIsInstance(app.screen, ScanProgressScreen)
                        scan_release.set()
                        for _ in range(20):
                            await pilot.pause(0.05)
                            if isinstance(app.screen, HomeScreen):
                                break

                        self.assertIsInstance(app.screen, HomeScreen)
                        self.assertEqual(app.selected_installation, str(target.resolve()))
                        installation = app.screen.query_one("#installation-select", Select)
                        self.assertEqual(installation.value, str(target.resolve()))
                        status = str(app.screen.query_one("#home-status", Static).content)
                        self.assertIn("Installation ready", status)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
