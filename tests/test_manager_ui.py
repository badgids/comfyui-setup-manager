from __future__ import annotations

import pytest
pytest.importorskip("textual")

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Button, Footer, Select, SelectionList, Static, TabbedContent

from comfy_setup.selectable_widgets import SelectableLog as RichLog
from comfy_setup.app import ComfySetupApp, HomeScreen, InstallationOverviewScreen, UpdateInstallationScreen
from comfy_setup.discovery import ComfyInstallation, installed_nodes, installed_workflows
from comfy_setup.launchers import write_local_launchers
from comfy_setup.updates import UpdateIssue, UpdatePreflight


def fake_installation(path: Path) -> ComfyInstallation:
    return ComfyInstallation(
        path=path,
        name="Test Studio",
        description="A portable test installation.",
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
        "node_resolution_schema": 1,
        "platform": {},
        "installations": records,
        "nodes": {str(item.path): [] for item in installations},
        "instance_workflows": {str(item.path): [] for item in installations},
        "assets": {"models": [], "loras": [], "workflows": []},
    }


class ManagerUITests(unittest.TestCase):
    def test_manage_tab_is_default_when_installation_exists(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "ComfyUI"
                installation = fake_installation(root)
                with patch("comfy_setup.app.load_runtime_state", return_value=cached_state(installation)):
                    app = ComfySetupApp()
                    async with app.run_test(size=(120, 40)) as pilot:
                        await pilot.pause()
                        self.assertIsInstance(app.screen, HomeScreen)
                        self.assertEqual(app.screen.query_one("#main-tabs", TabbedContent).active, "manage-tab")
                        self.assertEqual(
                            app.screen.query_one("#manage-installation-select", Select).value,
                            str(root),
                        )
                        self.assertIn("Test Studio", str(app.screen.query_one("#manager-name", Static).content))
                        self.assertFalse(app.screen.query_one("#manager-view", Button).disabled)

        asyncio.run(exercise())


    def test_nodes_tab_displays_registry_manager_resolution(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "ComfyUI"
                installation = fake_installation(root)
                state = cached_state(installation)
                state["nodes"][str(root)] = [
                    {
                        "name": "ComfyUI-GGUF",
                        "path": str(root / "custom_nodes" / "ComfyUI-GGUF"),
                        "repository": "https://github.com/city96/ComfyUI-GGUF",
                        "commit": None,
                        "manager_id": "comfyui-gguf",
                        "manager_version": "1.2.3",
                        "display_name": "ComfyUI-GGUF",
                        "resolution_kind": "registry-cache",
                        "resolution_trust": "Official",
                    }
                ]
                with patch("comfy_setup.app.load_runtime_state", return_value=state):
                    app = ComfySetupApp()
                    async with app.run_test(size=(120, 40)) as pilot:
                        await pilot.pause()
                        app.screen.query_one("#main-tabs", TabbedContent).active = "nodes-tab"
                        await pilot.pause()
                        log = app.screen.query_one("#nodes-list")
                        rendered = "\n".join(line.text for line in log.lines)
                        self.assertIn("Registry/Manager: comfyui-gguf", rendered)
                        self.assertIn("https://github.com/city96/ComfyUI-GGUF", rendered)
                        self.assertNotIn("unresolved local source", rendered)

        asyncio.run(exercise())

    def test_setup_tab_is_default_when_no_installation_exists(self) -> None:
        async def exercise() -> None:
            with patch("comfy_setup.app.load_runtime_state", return_value=cached_state()):
                app = ComfySetupApp()
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause()
                    self.assertEqual(app.screen.query_one("#main-tabs", TabbedContent).active, "setup-tab")

        asyncio.run(exercise())

    def test_inventory_discovers_nodes_and_nested_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ComfyUI"
            node = root / "custom_nodes" / "ExampleNode"
            node.mkdir(parents=True)
            workflow = root / "user" / "default" / "workflows" / "cars" / "build.json"
            workflow.parent.mkdir(parents=True)
            workflow.write_text('{"nodes": []}\n', encoding="utf-8")
            self.assertEqual([item.name for item in installed_nodes(root)], ["ExampleNode"])
            workflows = installed_workflows(root)
            self.assertEqual(len(workflows), 1)
            self.assertEqual(workflows[0].user_name, "default")

    def test_local_launcher_stays_inside_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ComfyUI"
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
            launcher = write_local_launchers(root)
            self.assertEqual(launcher, root / "comfyui")
            self.assertTrue((root / "comfyui.ps1").is_file())
            self.assertTrue((root / "comfyui.cmd").is_file())
            self.assertIn("PYTHONUNBUFFERED=1", launcher.read_text(encoding="utf-8"))
            self.assertIn('exec "$PY" -u', launcher.read_text(encoding="utf-8"))
            self.assertIn("PYTHONUNBUFFERED", (root / "comfyui.ps1").read_text(encoding="utf-8"))
            self.assertIn(" -u ", (root / "comfyui.cmd").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class UpdateDashboardUITests(unittest.TestCase):

    def test_update_worker_exception_restores_controls(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "ComfyUI"
                installation = fake_installation(root)
                preflight = UpdatePreflight(
                    installation=root,
                    current_commit="1" * 40,
                    current_branch="master",
                    current_repository="https://github.com/Comfy-Org/ComfyUI.git",
                    official_branch="master",
                    target_commit="2" * 40,
                    commits_behind=1,
                    commits_ahead=0,
                    requirements_changed=True,
                    python_supported=True,
                    custom_node_count=0,
                    dirty_custom_nodes=0,
                    issues=[],
                )
                with patch("comfy_setup.app.load_runtime_state", return_value=cached_state(installation)), patch(
                    "comfy_setup.app.ComfyUpdateManager.preflight", return_value=preflight
                ), patch(
                    "comfy_setup.app.ComfyUpdateManager.update", side_effect=RuntimeError("candidate setup changed")
                ):
                    app = ComfySetupApp()
                    async with app.run_test(size=(120, 44)) as pilot:
                        app.push_screen(UpdateInstallationScreen(root))
                        await pilot.pause(0.3)
                        screen = app.screen
                        self.assertIsInstance(screen, UpdateInstallationScreen)
                        screen.run_update()
                        await pilot.pause(0.4)
                        screen = app.screen
                        self.assertIsInstance(screen, UpdateInstallationScreen)
                        self.assertFalse(screen._busy)
                        self.assertFalse(screen.query_one("#update-back", Button).disabled)
                        self.assertIn("Update was not started", str(screen.query_one("#update-summary", Static).content))

        asyncio.run(exercise())

    def test_update_screen_mounts_with_explicit_strategies_and_package_review(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "ComfyUI"
                installation = fake_installation(root)
                preflight = UpdatePreflight(
                    installation=root,
                    current_commit="1" * 40,
                    current_branch="master",
                    current_repository="https://github.com/Comfy-Org/ComfyUI.git",
                    official_branch="master",
                    target_commit="2" * 40,
                    commits_behind=1,
                    commits_ahead=0,
                    requirements_changed=True,
                    python_supported=True,
                    custom_node_count=1,
                    dirty_custom_nodes=0,
                    issues=[],
                )
                with patch("comfy_setup.app.load_runtime_state", return_value=cached_state(installation)), patch(
                    "comfy_setup.app.ComfyUpdateManager.preflight", return_value=preflight
                ):
                    app = ComfySetupApp()
                    async with app.run_test(size=(120, 40)) as pilot:
                        app.push_screen(UpdateInstallationScreen(root))
                        await pilot.pause(0.3)
                        self.assertIsInstance(app.screen, UpdateInstallationScreen)
                        self.assertEqual(str(app.screen.query_one("#update-run", Button).label), "Safe update")
                        self.assertEqual(str(app.screen.query_one("#update-patch", Button).label), "Try to patch current setup")
                        self.assertEqual(str(app.screen.query_one("#update-anyway", Button).label), "Continue anyway")
                        self.assertIsInstance(app.screen.query_one("#update-packages"), SelectionList)
                        footer = app.screen.query_one(Footer)
                        for selector in ("#update-back", "#update-patch", "#update-anyway"):
                            button = app.screen.query_one(selector, Button)
                            self.assertTrue(button.display)
                            self.assertLessEqual(button.region.bottom, footer.region.y)
                        self.assertFalse(app.screen.query_one("#update-patch", Button).disabled)
                        self.assertFalse(app.screen.query_one("#update-anyway", Button).disabled)

        asyncio.run(exercise())

    def test_update_screen_keeps_patch_and_force_available_after_failed_plan(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "ComfyUI"
                installation = fake_installation(root)
                preflight = UpdatePreflight(
                    installation=root,
                    current_commit="1" * 40,
                    current_branch="master",
                    current_repository="https://github.com/Comfy-Org/ComfyUI.git",
                    official_branch="master",
                    target_commit="2" * 40,
                    commits_behind=1,
                    commits_ahead=0,
                    requirements_changed=True,
                    python_supported=True,
                    custom_node_count=1,
                    dirty_custom_nodes=0,
                    issues=[UpdateIssue("blocking", "Resolver", "No protected plan")],
                    resolution_checked=True,
                    resolution_possible=False,
                )
                with patch("comfy_setup.app.load_runtime_state", return_value=cached_state(installation)), patch(
                    "comfy_setup.app.ComfyUpdateManager.preflight", return_value=preflight
                ):
                    app = ComfySetupApp()
                    async with app.run_test(size=(100, 30)) as pilot:
                        app.push_screen(UpdateInstallationScreen(root))
                        await pilot.pause(0.3)
                        screen = app.screen
                        self.assertTrue(screen.query_one("#update-run", Button).disabled)
                        self.assertFalse(screen.query_one("#update-patch", Button).disabled)
                        self.assertFalse(screen.query_one("#update-anyway", Button).disabled)
                        footer = screen.query_one(Footer)
                        self.assertLessEqual(screen.query_one("#update-anyway", Button).region.bottom, footer.region.y)

        asyncio.run(exercise())

    def test_current_install_hides_inapplicable_update_strategies(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "ComfyUI"
                installation = fake_installation(root)
                preflight = UpdatePreflight(
                    installation=root,
                    current_commit="1" * 40,
                    current_branch="master",
                    current_repository="https://github.com/Comfy-Org/ComfyUI.git",
                    official_branch="master",
                    target_commit="1" * 40,
                    commits_behind=0,
                    commits_ahead=0,
                    requirements_changed=False,
                    python_supported=True,
                    custom_node_count=1,
                    dirty_custom_nodes=0,
                    issues=[],
                    baseline_pip_ok=True,
                    resolution_checked=False,
                )
                with patch("comfy_setup.app.load_runtime_state", return_value=cached_state(installation)), patch(
                    "comfy_setup.app.ComfyUpdateManager.preflight", return_value=preflight
                ):
                    app = ComfySetupApp()
                    async with app.run_test(size=(100, 30)) as pilot:
                        app.push_screen(UpdateInstallationScreen(root))
                        await pilot.pause(0.3)
                        screen = app.screen
                        self.assertFalse(screen.query_one("#update-strategy-actions").display)
                        self.assertIn("protected resolution not needed", str(screen.query_one("#update-summary", Static).content))
                        self.assertFalse(screen.query_one("#update-check", Button).disabled)

        asyncio.run(exercise())
    def test_manage_tab_exposes_update_and_rollback_actions(self) -> None:
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "ComfyUI"
                root.mkdir()
                app = ComfySetupApp()
                app.installations = [fake_installation(root)]
                app.selected_manager_installation = str(root)
                with patch("comfy_setup.app.ComfyUpdateManager.list_snapshots", return_value=[]):
                    async with app.run_test(size=(120, 40)) as pilot:
                        await pilot.pause()
                        self.assertEqual(str(app.screen.query_one("#manager-update", Button).label), "Update")
                        self.assertEqual(str(app.screen.query_one("#manager-rollback", Button).label), "Rollback")
                        self.assertTrue(app.screen.query_one("#manager-rollback", Button).disabled)

        asyncio.run(exercise())
