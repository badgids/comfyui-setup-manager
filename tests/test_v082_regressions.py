from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

pytest.importorskip("textual")

from textual.containers import ScrollableContainer
from textual.widgets import Button, Input, Select, Static

from comfy_setup.selectable_widgets import SelectableLog as RichLog
from comfy_setup.app import (
    ComfySetupApp,
    DuplicateNodeChoiceScreen,
    InstallationOverviewScreen,
    LogBrowserScreen,
    ReviewScreen,
)
from comfy_setup.exporter import DuplicateNodeGroup
from comfy_setup.dependency_resolver import comfyui_startup_failures
from comfy_setup.discovery import ComfyInstallation, InstalledWorkflow
from comfy_setup.engine import InstallerEngine
from comfy_setup.log_management import (
    LogRetention,
    active_log_path,
    cleanup_runtime_logs,
    list_runtime_logs,
    load_log_retention,
    logs_directory,
    rotate_runtime_log,
)
from comfy_setup.models import InstallOptions, PlatformInfo


PROJECT = Path(__file__).resolve().parents[1]
PROFILE = PROJECT / "installer" / "src" / "comfy_setup" / "profiles" / "badgids-complete.yaml"


def platform_info() -> PlatformInfo:
    return PlatformInfo(
        os_name="linux",
        system="Linux",
        release="test",
        architecture="x86_64",
        is_wsl=False,
        package_manager="apt",
        accelerator="nvidia",
        gpu_name="Test GPU",
        cuda_version="13.0",
    )


def installation(path: Path) -> ComfyInstallation:
    return ComfyInstallation(
        path=path,
        name="Studio",
        description="Managed test installation.",
        repository="https://github.com/Comfy-Org/ComfyUI.git",
        branch="master",
        has_venv=True,
        node_count=100,
        workflow_count=100,
    )


def runtime_state(item: ComfyInstallation) -> dict:
    return {
        "schema_version": 1,
        "node_resolution_schema": 1,
        "initialized": True,
        "platform": {},
        "installations": [{
            "path": str(item.path),
            "name": item.name,
            "description": item.description,
            "profile_id": None,
            "profile_name": None,
            "version": None,
            "commit": None,
            "branch": item.branch,
            "repository": item.repository,
            "has_venv": True,
            "node_count": item.node_count,
            "workflow_count": item.workflow_count,
        }],
        "nodes": {str(item.path): []},
        "instance_workflows": {str(item.path): []},
        "assets": {"models": [], "loras": [], "workflows": []},
    }


def test_view_contents_uses_a_dedicated_visible_scroll_region(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "ComfyUI"
        root.mkdir()
        item = installation(root)
        workflows = [
            InstalledWorkflow(f"workflow-{index}", tmp_path / "shared" / f"workflow-{index}.json", "default")
            for index in range(100)
        ]
        with patch("comfy_setup.app.load_runtime_state", return_value=runtime_state(item)), patch(
            "comfy_setup.app.installed_nodes", return_value=[]
        ), patch("comfy_setup.app.installed_workflows", return_value=workflows):
            app = ComfySetupApp()
            async with app.run_test(size=(82, 20)) as pilot:
                app.push_screen(InstallationOverviewScreen(root))
                await pilot.pause(0.3)
                scroll = app.screen.query_one("#installation-overview-scroll", ScrollableContainer)
                assert scroll.max_scroll_y > 0
                assert app.screen.query_one("#back", Button).region.height == 3

    asyncio.run(exercise())


def test_review_installation_uses_a_dedicated_visible_scroll_region(tmp_path: Path) -> None:
    async def exercise() -> None:
        nodes = [
            {
                "id": f"node-{index}",
                "name": f"Node {index}",
                "platforms": ["linux"],
                "accelerators": ["nvidia"],
                "selected": True,
                "source": {"type": "remote", "repository": "https://github.com/example/node.git"},
            }
            for index in range(80)
        ]
        profile = {
            "id": "scroll-test",
            "name": "Scroll Test",
            "schema_version": 4,
            "comfyui": {"repository": "https://github.com/Comfy-Org/ComfyUI.git"},
            "python": {"preferred": "3.13"},
            "torch": {},
            "constraints": {},
            "nodes": nodes,
            "accelerated_packages": [],
        }
        app = ComfySetupApp(initial_profile=profile)
        app.active_profile = profile
        app.platform_info = platform_info()
        app.configuration = {"repository_mode": "official", "repository_branch": "master"}
        app.options = InstallOptions(
            target_dir=tmp_path / "ComfyUI",
            python_version="3.13",
            accelerator="nvidia",
            selected_nodes={node["id"] for node in nodes},
            selected_acceleration=set(),
            auto_install_system=False,
            configure_shared_assets=False,
        )
        async with app.run_test(size=(82, 20)) as pilot:
            app.push_screen(ReviewScreen())
            await pilot.pause(0.3)
            scroll = app.screen.query_one("#review-scroll", ScrollableContainer)
            assert scroll.max_scroll_y > 0
            assert app.screen.query_one("#install", Button).region.height == 3

    asyncio.run(exercise())


def test_duplicate_node_modal_shows_complete_paths_and_returns_selected_copy(tmp_path: Path) -> None:
    async def exercise() -> None:
        first = (tmp_path / "ComfyUI" / "custom_nodes" / "duplicate-node").resolve()
        second = (tmp_path / "shared" / "custom_nodes" / "duplicate-node").resolve()
        group = DuplicateNodeGroup("duplicate-node", (first, second))
        app = ComfySetupApp()
        result: list[Path | None] = []
        async with app.run_test(size=(100, 28)) as pilot:
            app.push_screen(DuplicateNodeChoiceScreen(group), result.append)
            await pilot.pause(0.2)
            text = app.screen.query_one("#duplicate-node-paths", Static).render()
            assert str(first) in str(text)
            assert str(second) in str(text)
            app.screen.query_one("#duplicate-node-keep", Select).value = str(second)
            app.screen.confirm()
            await pilot.pause(0.2)
            assert result == [second]

    asyncio.run(exercise())


def test_clear_output_rotates_log_without_deleting_it(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "ComfyUI"
        root.mkdir()
        item = installation(root)
        active = active_log_path(root)
        active.write_text("old output\n", encoding="utf-8")
        with patch("comfy_setup.app.load_runtime_state", return_value=runtime_state(item)), patch(
            "comfy_setup.app.ComfyUpdateManager.list_snapshots", return_value=[]
        ):
            app = ComfySetupApp()
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause(0.3)
                app.screen.clear_runtime_output()
                await pilot.pause(0.2)
                assert active.is_file()
                assert active.read_text(encoding="utf-8") == ""
                archives = list(logs_directory(root).glob("*.log"))
                assert len(archives) == 1
                assert archives[0].read_text(encoding="utf-8") == "old output\n"
                assert "old output" not in "\n".join(app.screen._runtime_lines)

    asyncio.run(exercise())


def test_clear_output_requests_rotation_for_cli_managed_process(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "ComfyUI"
        root.mkdir()
        item = installation(root)
        active = active_log_path(root)
        active.write_text("still being written\n", encoding="utf-8")
        with patch("comfy_setup.app.load_runtime_state", return_value=runtime_state(item)), patch(
            "comfy_setup.app.ComfyUpdateManager.list_snapshots", return_value=[]
        ), patch(
            "comfy_setup.app.instance_status", return_value={"running": True}
        ), patch(
            "comfy_setup.app.request_log_rotation"
        ) as request_rotation:
            app = ComfySetupApp()
            async with app.run_test(size=(110, 32)) as pilot:
                await pilot.pause(0.3)
                app.screen.clear_runtime_output()
                await pilot.pause(0.1)
                request_rotation.assert_called_once_with(root.resolve(), reason="manual-clear")
                assert active.read_text(encoding="utf-8") == "still being written\n"
                assert not list(logs_directory(root).glob("*.log"))

    asyncio.run(exercise())


def test_log_browser_lists_searches_and_protects_active_log(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "ComfyUI"
        root.mkdir()
        active_log_path(root).write_text("active line\n", encoding="utf-8")
        archived = logs_directory(root) / "runtime-old.log"
        archived.write_text("needle in archive\n", encoding="utf-8")
        app = ComfySetupApp()
        async with app.run_test(size=(105, 34)) as pilot:
            app.push_screen(LogBrowserScreen(root))
            await pilot.pause(0.3)
            assert len(app.screen.records) == 2
            app.screen.query_one("#log-search", Input).value = "needle"
            await pilot.pause(0.1)
            app.screen.refresh_logs()
            await pilot.pause(0.2)
            assert [record.path for record in app.screen.records] == [archived.resolve()]
            assert app.screen.query_one("#log-delete", Button).disabled is False
            assert "needle in archive" in "\n".join(
                strip.text for strip in app.screen.query_one("#log-file-view").lines
            )

    asyncio.run(exercise())


def test_log_retention_defaults_to_monthly_and_cleans_only_old_archives(tmp_path: Path) -> None:
    policy = load_log_retention()
    assert policy.mode == "monthly"
    assert policy.effective_days == 30

    root = tmp_path / "ComfyUI"
    active_log_path(root).write_text("active", encoding="utf-8")
    old = logs_directory(root) / "old.log"
    recent = logs_directory(root) / "recent.log"
    old.write_text("old", encoding="utf-8")
    recent.write_text("recent", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 10 * 86400, now - 10 * 86400))
    os.utime(recent, (now - 2 * 86400, now - 2 * 86400))

    removed = cleanup_runtime_logs(root, LogRetention(mode="weekly", days=7), now=now)
    assert removed == [old.resolve()]
    assert active_log_path(root).is_file()
    assert recent.is_file()


def test_rotation_creates_a_new_active_log_and_preserves_history(tmp_path: Path) -> None:
    root = tmp_path / "ComfyUI"
    active = active_log_path(root)
    active.write_text("session one\n", encoding="utf-8")
    archived = rotate_runtime_log(root, reason="manual-clear")
    assert archived is not None and archived.is_file()
    assert archived.read_text(encoding="utf-8") == "session one\n"
    assert active.is_file() and active.stat().st_size == 0
    records = list_runtime_logs(root)
    assert records[0].active is True
    assert any(record.path == archived.resolve() for record in records)


def test_badgids_profile_pins_numpy_and_installs_click() -> None:
    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    assert profile["constraints"]["numpy"] == "2.4.4"
    assert "click>=8.1,<9" in profile["extra_python_packages"]
    assert profile["dependency_repairs"]["click"] == "click>=8.1,<9"


def test_dependency_failure_parser_finds_observed_node_failures() -> None:
    output = """
    Cannot import /tmp/custom_nodes/ace-step module for custom nodes: No module named 'click'
    ❌ [Qwen3-TTS] Critical Import Error: Numba needs NumPy 2.4 or less. Got NumPy 2.5.
    0.1 seconds (IMPORT FAILED): /tmp/custom_nodes/was-ns
    """
    failures = comfyui_startup_failures(output)
    assert len(failures) == 3
    assert any("click" in failure for failure in failures)
    assert any("NumPy 2.5" in failure for failure in failures)


def test_core_install_includes_manager_requirements_and_constraints(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    (target / "requirements.txt").write_text("requests\n", encoding="utf-8")
    (target / "manager_requirements.txt").write_text("comfyui-manager\n", encoding="utf-8")
    profile = {
        "constraints": {"numpy": "2.4.4"},
        "nodes": [],
        "accelerated_packages": [],
        "comfyui": {},
        "torch": {},
    }
    options = InstallOptions(target, "3.13", "nvidia", set(), set(), auto_install_system=False)
    engine = InstallerEngine(profile, platform_info(), options)
    commands: list[tuple[str, ...]] = []
    engine._uv_pip = lambda *args, **kwargs: commands.append(tuple(args)) or 0  # type: ignore[method-assign]

    engine._install_core()

    manager_commands = [command for command in commands if any("requirements-comfyui-manager.txt" in part for part in command)]
    assert len(manager_commands) == 1
    assert "-c" in manager_commands[0]
    assert any(command[:3] == ("install", "--reinstall", "numpy==2.4.4") for command in commands)


def test_startup_validation_repairs_profile_declared_missing_module(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    (target / "main.py").write_text("", encoding="utf-8")
    profile = {
        "constraints": {"numpy": "2.4.4"},
        "dependency_repairs": {"click": "click>=8.1,<9"},
        "nodes": [],
        "accelerated_packages": [],
        "comfyui": {},
        "torch": {},
    }
    engine = InstallerEngine(
        profile,
        platform_info(),
        InstallOptions(target, "3.13", "nvidia", set(), set(), auto_install_system=False),
    )
    outputs = iter([
        subprocess.CompletedProcess([], 0, "Cannot import ace-step custom nodes: No module named 'click'\n", ""),
        subprocess.CompletedProcess([], 0, "quick test complete\n", ""),
    ])
    engine.runner.capture = lambda *args, **kwargs: next(outputs)  # type: ignore[method-assign]
    commands: list[tuple[str, ...]] = []
    engine._uv_pip = lambda *args, **kwargs: commands.append(tuple(args)) or 0  # type: ignore[method-assign]

    result = engine._validate_comfyui_startup()

    assert result["ok"] is True
    assert any("click>=8.1,<9" in command for command in commands)
    assert any("numpy==2.4.4" in command for command in commands)


def test_log_cli_lists_cleans_and_sets_custom_retention(tmp_path: Path) -> None:
    from comfy_setup.cli import _handle_command, build_parser

    root = tmp_path / "ComfyUI"
    active_log_path(root).write_text("active\n", encoding="utf-8")
    archived = logs_directory(root) / "archive.log"
    archived.write_text("searchable\n", encoding="utf-8")
    parser = build_parser()

    listed = _handle_command(parser.parse_args(["installations", "logs", str(root), "--list-files"]))
    assert len(listed) == 2
    searched = _handle_command(parser.parse_args(["installations", "logs", str(root), "--search", "searchable"]))
    assert [item.path for item in searched] == [archived.resolve()]
    result = _handle_command(parser.parse_args([
        "installations", "logs", str(root), "--retention", "custom", "--days", "45"
    ]))
    assert result["retention"].mode == "custom"
    assert result["retention"].effective_days == 45


def test_dynamic_package_installs_honor_profile_constraints(tmp_path: Path) -> None:
    from comfy_setup.runner import Runner
    from comfy_setup.wheels import WheelManager

    target = tmp_path / "ComfyUI"
    constraints = target / ".comfy-setup" / "constraints.txt"
    constraints.parent.mkdir(parents=True)
    constraints.write_text("numpy==2.4.4\n", encoding="utf-8")
    manager = WheelManager(
        runner=Runner(),
        uv=Path("uv"),
        target_python=target / ".venv" / "bin" / "python",
        target_dir=target,
        profile={"torch": {}},
        platform_info=platform_info(),
        options=InstallOptions(target, "3.13", "nvidia", set(), set()),
    )
    commands: list[tuple[str, ...]] = []
    manager._uv_pip = lambda *args, **kwargs: commands.append(tuple(args)) or 0  # type: ignore[method-assign]

    manager._install_package_from_indexes("onnxruntime-gpu==1.27.0", "onnxruntime")

    assert len(commands) == 1
    assert "-c" in commands[0]
    assert str(constraints) in commands[0]


def test_dependency_repairs_are_validated_as_safe_requirements() -> None:
    from comfy_setup.profile import ProfileError, validate_profile

    profile = {
        "schema_version": 4,
        "kind": "comfyui-setup-profile",
        "id": "unsafe-repair",
        "name": "Unsafe repair",
        "comfyui": {"repository": "https://github.com/Comfy-Org/ComfyUI.git"},
        "python": {"preferred": "3.13"},
        "torch": {},
        "nodes": [],
        "accelerated_packages": [],
        "dependency_repairs": {"click": "click @ http://private.example/click.whl"},
    }
    with pytest.raises(ProfileError):
        validate_profile(profile)


def test_log_browser_is_scrollable_in_a_compact_terminal(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "ComfyUI"
        root.mkdir()
        active_log_path(root).write_text("active\n", encoding="utf-8")
        app = ComfySetupApp()
        async with app.run_test(size=(72, 20)) as pilot:
            app.push_screen(LogBrowserScreen(root))
            await pilot.pause(0.3)
            shell = app.screen.query_one("#log-browser-shell", ScrollableContainer)
            assert shell.max_scroll_y > 0
            assert app.screen.query_one("#back", Button).region.y > app.screen.size.height
            shell.scroll_end(animate=False)
            await pilot.pause(0.2)
            assert app.screen.query_one("#back", Button).region.y < app.screen.size.height

    asyncio.run(exercise())
