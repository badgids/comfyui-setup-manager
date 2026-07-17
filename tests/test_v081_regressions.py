from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

pytest.importorskip("textual")

from textual.widgets import Button, Select, Static

from comfy_setup.app import (
    ComfySetupApp,
    InstallationOverviewScreen,
    ProfileBundleEditorScreen,
    ProfileLibraryScreen,
    ScanFolderScreen,
    _display_path,
)
from comfy_setup.cli import _setup_output_path, build_parser
from comfy_setup.configuration import profiles_directory, setups_directory
from comfy_setup.discovery import ComfyInstallation, InstalledWorkflow
from comfy_setup.models import PlatformInfo
from comfy_setup.path_widgets import FilesystemPathSuggester, PathBrowserScreen
from comfy_setup.profile import ProfileRecord, read_profile_bundle, write_profile_bundle
from comfy_setup.wheel_sources import WheelSourceError, WheelSourceRegistry, infer_source_kind


def platform_info() -> PlatformInfo:
    return PlatformInfo(
        os_name="linux",
        system="Linux",
        release="test",
        architecture="x86_64",
        is_wsl=False,
        package_manager="apt",
        accelerator="nvidia",
    )


def installation(path: Path) -> ComfyInstallation:
    return ComfyInstallation(
        path=path,
        name="External Workflow Test",
        description="test",
        repository="https://github.com/Comfy-Org/ComfyUI.git",
        branch="master",
        has_venv=True,
    )


def runtime_state(item: ComfyInstallation) -> dict:
    return {
        "schema_version": 1,
        "initialized": True,
        "platform": {},
        "installations": [
            {
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
                "node_count": 0,
                "workflow_count": 1,
            }
        ],
        "nodes": {str(item.path): []},
        "instance_workflows": {str(item.path): []},
        "assets": {"models": [], "loras": [], "workflows": []},
    }


def test_local_wheel_path_is_registered_without_https(tmp_path: Path) -> None:
    wheel = tmp_path / "flash_attn-2.8.3.post1-cp313-cp313-linux_x86_64.whl"
    wheel.write_bytes(b"wheel")
    registry_path = tmp_path / "wheel-sources.yaml"
    registry_path.write_text(yaml.safe_dump({"metadata": {}, "sources": []}), encoding="utf-8")

    assert infer_source_kind(str(wheel)) == "direct-wheel"
    registry = WheelSourceRegistry(registry_path)
    source = registry.add_source(
        package_id="flash-attn",
        location=str(wheel),
        platform_info=platform_info(),
    )
    assert source.kind == "direct-wheel"
    assert source.location == str(wheel.resolve())

    with pytest.raises(WheelSourceError, match="does not exist"):
        registry.add_source(
            package_id="flash-attn",
            location=str(tmp_path / "missing.whl"),
            platform_info=platform_info(),
        )


def test_profile_exports_default_under_project_root_and_legacy_name_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMFYUI_SETUP_PROJECT_ROOT", str(tmp_path))
    legacy = tmp_path / "setups"
    legacy.mkdir()
    (legacy / "existing.comfyuisetup").write_bytes(b"legacy")
    assert profiles_directory() == tmp_path / "profiles"
    assert (tmp_path / "profiles" / "existing.comfyuisetup").read_bytes() == b"legacy"
    assert not legacy.exists()
    assert setups_directory() == tmp_path / "profiles"


def test_legacy_profile_migration_never_overwrites_a_name_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMFYUI_SETUP_PROJECT_ROOT", str(tmp_path))
    profiles = tmp_path / "profiles"
    legacy = tmp_path / "setups"
    profiles.mkdir()
    legacy.mkdir()
    (profiles / "studio.comfyuisetup").write_bytes(b"current")
    (legacy / "studio.comfyuisetup").write_bytes(b"legacy-conflict")
    (legacy / "portable.comfyuisetup").write_bytes(b"legacy-unique")

    assert profiles_directory() == profiles
    assert (profiles / "studio.comfyuisetup").read_bytes() == b"current"
    assert (profiles / "portable.comfyuisetup").read_bytes() == b"legacy-unique"
    assert (legacy / "studio.comfyuisetup").read_bytes() == b"legacy-conflict"
    assert legacy.is_dir()


def test_cli_setup_output_defaults_to_profiles_and_remains_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMFYUI_SETUP_PROJECT_ROOT", str(tmp_path))
    assert _setup_output_path(None, "Studio Setup") == tmp_path / "profiles" / "studio-setup.comfyuisetup"
    custom = tmp_path / "exports" / "custom.comfyuisetup"
    assert _setup_output_path(custom, "Studio Setup") == custom
    args = build_parser().parse_args(["profiles", "export", "vanilla-comfyui"])
    assert args.output is None


def test_filesystem_path_autocomplete(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    suggestion = asyncio.run(FilesystemPathSuggester().get_suggestion(str(tmp_path / "mod")))
    assert suggestion == str(tmp_path / "models") + "/"


def test_path_field_browse_button_opens_tui_browser() -> None:
    async def exercise() -> None:
        app = ComfySetupApp()
        async with app.run_test(size=(100, 34)) as pilot:
            app.push_screen(ScanFolderScreen())
            await pilot.pause()
            await pilot.click("#scan-root-browse")
            await pilot.pause()
            assert isinstance(app.screen, PathBrowserScreen)

    asyncio.run(exercise())


def test_view_contents_handles_external_shared_workflows(tmp_path: Path) -> None:
    async def exercise() -> None:
        comfy = tmp_path / "ComfyUI"
        comfy.mkdir()
        external = tmp_path / "shared-workflows" / "workflow.json"
        external.parent.mkdir()
        external.write_text('{"nodes": []}', encoding="utf-8")
        item = installation(comfy)
        workflow = InstalledWorkflow("workflow", external, "default")
        with patch("comfy_setup.app.load_runtime_state", return_value=runtime_state(item)), patch(
            "comfy_setup.app.installed_nodes", return_value=[]
        ), patch("comfy_setup.app.installed_workflows", return_value=[workflow]):
            app = ComfySetupApp()
            async with app.run_test(size=(110, 38)) as pilot:
                app.push_screen(InstallationOverviewScreen(comfy))
                await pilot.pause()
                assert isinstance(app.screen, InstallationOverviewScreen)
                display = _display_path(external, comfy)
                assert "external/shared" in display
                assert str(external) in display

    asyncio.run(exercise())


def test_profile_refresh_selects_newly_imported_or_exported_profile(tmp_path: Path) -> None:
    async def exercise() -> None:
        vanilla = ProfileRecord(
            {"id": "vanilla-comfyui", "name": "Vanilla", "schema_version": 4, "comfyui": {}, "nodes": []},
            tmp_path / "vanilla.yaml",
            "built-in",
        )
        exported = ProfileRecord(
            {"id": "new-export", "name": "New Export", "schema_version": 4, "comfyui": {}, "nodes": []},
            tmp_path / "new-export.comfyuisetup",
            "imported",
        )
        with patch("comfy_setup.app.list_profiles", return_value=[vanilla]):
            app = ComfySetupApp()
        async with app.run_test(size=(110, 38)) as pilot:
            await pilot.pause()
            with patch("comfy_setup.app.list_profiles", return_value=[vanilla, exported]):
                app.refresh_profile_views("new-export")
                await pilot.pause()
            assert app.screen.query_one("#profile-select", Select).value == "new-export"

    asyncio.run(exercise())


def test_profile_library_exposes_remove_for_imported_profiles(tmp_path: Path) -> None:
    async def exercise() -> None:
        vanilla = ProfileRecord(
            {"id": "vanilla-comfyui", "name": "Vanilla", "schema_version": 4, "comfyui": {}, "nodes": []},
            tmp_path / "vanilla.yaml",
            "built-in",
        )
        imported = ProfileRecord(
            {"id": "imported", "name": "Imported", "schema_version": 4, "comfyui": {}, "nodes": []},
            tmp_path / "imported.comfyuisetup",
            "imported",
        )
        with patch("comfy_setup.app.list_profiles", return_value=[vanilla, imported]):
            app = ComfySetupApp()
            app.selected_profile_id = "imported"
        async with app.run_test(size=(100, 34)) as pilot:
            app.push_screen(ProfileLibraryScreen())
            await pilot.pause()
            assert app.screen.query_one("#profile-remove", Button).disabled is False

    asyncio.run(exercise())


def test_profile_library_opens_internal_archive_editor(tmp_path: Path) -> None:
    async def exercise() -> None:
        source = Path(__file__).resolve().parents[1] / "installer" / "src" / "comfy_setup" / "profiles" / "vanilla.yaml"
        profile = yaml.safe_load(source.read_text(encoding="utf-8"))
        archive = write_profile_bundle(profile, tmp_path / "editable.comfyuisetup")
        imported = ProfileRecord(read_profile_bundle(archive), archive, "imported")
        with patch("comfy_setup.app.list_profiles", return_value=[imported]):
            app = ComfySetupApp()
            app.selected_profile_id = imported.id
        async with app.run_test(size=(110, 40)) as pilot:
            app.push_screen(ProfileLibraryScreen())
            await pilot.pause()
            assert app.screen.query_one("#profile-edit-files", Button).disabled is False
            await pilot.click("#profile-edit-files")
            await pilot.pause()
            assert isinstance(app.screen, ProfileBundleEditorScreen)
            assert app.screen.query_one("#profile-file-editor").language == "yaml"

    asyncio.run(exercise())
