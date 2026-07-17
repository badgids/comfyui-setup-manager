from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from comfy_setup.agent_resources import configured_agent_targets, install_skill, list_bundled_skills
from comfy_setup.discovery import ComfyInstallation, DiscoveryResult, InstalledNode, InstalledWorkflow
from comfy_setup.models import PlatformInfo
from comfy_setup.runtime_state import (
    cached_assets,
    cached_installations,
    cached_nodes,
    load_runtime_state,
    node_resolution_state_is_current,
    scan_runtime_state,
)


def test_full_scan_persists_reusable_yaml_cache(tmp_path: Path) -> None:
    root = tmp_path / "ComfyUI"
    installation = ComfyInstallation(
        path=root,
        name="Cached ComfyUI",
        description="Test",
        version="1.0",
        branch="master",
        has_venv=True,
        node_count=1,
        workflow_count=1,
    )
    discovery = DiscoveryResult(
        installations=[installation],
        nodes={str(root): [InstalledNode("ExampleNode", root / "custom_nodes" / "ExampleNode")]},
        workflows={str(root): [InstalledWorkflow("example.json", root / "user/default/workflows/example.json", "default")]},
    )
    info = PlatformInfo("linux", "Linux", "test", "x86_64", False, "apt", "cpu")
    progress: list[tuple[int, int, str]] = []
    with patch("comfy_setup.runtime_state.detect_platform", return_value=info), patch(
        "comfy_setup.runtime_state.discover_installations_detailed", return_value=discovery
    ), patch(
        "comfy_setup.runtime_state.list_installed_assets",
        side_effect=lambda kind: [{"relative_path": f"{kind}.bin", "size": 1}],
    ):
        state = scan_runtime_state(progress=lambda step, total, message: progress.append((step, total, message)))

    assert state["initialized"] is True
    assert node_resolution_state_is_current(state)
    assert len(progress) == 6
    loaded = load_runtime_state()
    assert cached_installations(loaded)[0].name == "Cached ComfyUI"
    assert cached_nodes(loaded)[str(root)][0]["name"] == "ExampleNode"
    assert cached_assets(loaded)["loras"][0]["relative_path"] == "loras.bin"


def test_bundled_skills_are_valid_and_installable(tmp_path: Path) -> None:
    skills = list_bundled_skills()
    assert {item.id for item in skills} == {
        "comfyui-setup-manager-developer",
        "comfyui-setup-manager-operator",
        "comfyui-setup-manager-release",
    }
    destination = install_skill(skills[0].id, "test-agent", target_root=tmp_path / "skills")
    assert (destination / "SKILL.md").is_file()


def test_default_agent_targets_cover_requested_agents() -> None:
    assert {"claude-code", "codex", "opencode", "openclaude"} <= configured_agent_targets().keys()


def test_daily_catalog_refresh_re_resolves_only_cached_installations(tmp_path: Path) -> None:
    from comfy_setup.runtime_state import refresh_cached_node_catalog_state

    root = tmp_path / "ComfyUI"
    root.mkdir()
    installation = ComfyInstallation(path=root, name="Cached", description="Test")
    state = {
        "schema_version": 1,
        "initialized": True,
        "platform": {"os_name": "linux", "system": "Linux", "release": "test", "architecture": "x86_64", "is_wsl": False, "package_manager": None, "accelerator": "cpu", "gpu_name": None, "notes": []},
        "installations": [{"path": str(root), "name": "Cached", "description": "Test", "profile_id": None, "profile_name": None, "version": None, "commit": None, "branch": None, "repository": None, "has_venv": False, "node_count": 1, "workflow_count": 0}],
        "nodes": {},
        "instance_workflows": {"keep": [{"name": "workflow", "path": "/tmp/workflow.json", "user_name": "default"}]},
        "assets": {"models": [{"relative_path": "keep"}], "loras": [], "workflows": []},
    }
    from comfy_setup.configuration import save_editable_config
    save_editable_config("runtime-state.yaml", state)

    resolved = InstalledNode(
        "ComfyUI-GGUF",
        root / "custom_nodes" / "ComfyUI-GGUF",
        repository="https://github.com/city96/ComfyUI-GGUF",
        manager_id="comfyui-gguf",
        resolution_kind="registry-cache",
        resolution_trust="Official",
    )
    progress: list[tuple[int, int, str]] = []
    with patch("comfy_setup.runtime_state.ensure_daily_node_catalog") as refresh, patch(
        "comfy_setup.runtime_state.installed_nodes", return_value=[resolved]
    ), patch("comfy_setup.runtime_state.detect_platform") as platform_probe, patch(
        "comfy_setup.runtime_state.discover_installations_detailed"
    ) as discovery_probe, patch("comfy_setup.runtime_state.list_installed_assets") as asset_probe:
        updated = refresh_cached_node_catalog_state(
            progress=lambda step, total, message: progress.append((step, total, message))
        )

    refresh.assert_called_once()
    platform_probe.assert_not_called()
    discovery_probe.assert_not_called()
    asset_probe.assert_not_called()
    assert updated["nodes"][str(root)][0]["manager_id"] == "comfyui-gguf"
    assert node_resolution_state_is_current(updated)
    assert updated["instance_workflows"] == state["instance_workflows"]
    assert updated["assets"] == state["assets"]
    assert progress[-1][0:2] == (3, 3)


def test_old_runtime_node_provenance_is_marked_for_one_time_upgrade() -> None:
    assert not node_resolution_state_is_current({"initialized": True, "nodes": {}})
    assert node_resolution_state_is_current({"node_resolution_schema": 1})
