from __future__ import annotations

import asyncio
import hashlib
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from comfy_setup.dependency_resolver import reconcile_requirements_text
from comfy_setup.engine import InstallerEngine
from comfy_setup.models import InstallOptions, PlatformInfo
from comfy_setup.selectable_widgets import Input, Markdown, RichLog


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


def test_conflicting_manifest_is_rewritten_to_verified_working_version() -> None:
    text, overrides = reconcile_requirements_text(
        "comfy-kitchen==0.2.21\nnumpy>=2.0\n",
        {"comfy-kitchen": "0.2.20", "numpy": "2.4.4"},
    )
    assert "comfy-kitchen==0.2.20" in text
    assert "comfy-kitchen==0.2.21\n" not in text
    assert "numpy>=2.0" in text
    assert overrides == [
        {
            "name": "comfy-kitchen",
            "requested": "comfy-kitchen==0.2.21",
            "verified_version": "0.2.20",
            "replacement": "comfy-kitchen==0.2.20",
        }
    ]


def test_core_install_does_not_submit_unsatisfiable_manifest_and_lock_pair(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    (target / "requirements.txt").write_text(
        "comfy-kitchen==0.2.21\nrequests>=2\n", encoding="utf-8"
    )
    profile = {
        "constraints": {},
        "dependency_resolution": {
            "resolved_versions": {"comfy-kitchen": "0.2.20"},
        },
        "environment_lock": {
            "packages": [
                {
                    "name": "comfy-kitchen",
                    "version": "0.2.20",
                    "requirement": "comfy-kitchen==0.2.20",
                    "install": True,
                    "portable": True,
                    "source": "index",
                }
            ]
        },
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
    commands: list[tuple[str, ...]] = []
    engine._uv_pip = lambda *args, **kwargs: commands.append(tuple(args)) or 0  # type: ignore[method-assign]

    engine._install_core()

    resolved = (target / ".comfy-setup" / "requirements-comfyui.txt").read_text(encoding="utf-8")
    constraints = (target / ".comfy-setup" / "constraints.txt").read_text(encoding="utf-8")
    assert "comfy-kitchen==0.2.20" in resolved
    assert "comfy-kitchen==0.2.21\n" not in resolved
    assert "comfy-kitchen==0.2.20" in constraints
    assert any("requirements-comfyui.txt" in part for command in commands for part in command)


class SelectionHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield Input("/tmp/example path", id="path")
        yield RichLog(id="output", max_lines=100)
        yield Markdown("review line one\nreview line two", id="review")


def test_input_and_output_use_normal_select_all_and_copy_shortcuts() -> None:
    async def exercise() -> None:
        app = SelectionHarness()
        clipboard = Mock()
        app.copy_to_clipboard = clipboard  # type: ignore[method-assign]
        async with app.run_test(size=(80, 20)) as pilot:
            field = app.query_one("#path", Input)
            field.focus()
            await pilot.press("ctrl+a")
            assert field.selected_text == "/tmp/example path"
            await pilot.press("ctrl+c")
            clipboard.assert_called_with("/tmp/example path")

            output = app.query_one("#output", RichLog)
            output.write("first line")
            output.write("second line")
            output.focus()
            await pilot.press("ctrl+a")
            assert output.selected_text == "first line\nsecond line"
            await pilot.press("ctrl+c")
            clipboard.assert_called_with("first line\nsecond line")

            output.move_cursor(output.document.end)
            await pilot.press("shift+left")
            assert output.selected_text == "e"

            output.clear()
            output.write("\x1b[31mERROR package==1.0 at /tmp/example.log\x1b[0m")
            assert output.text == "ERROR package==1.0 at /tmp/example.log"
            assert "\x1b" not in output.text
            assert any(
                highlight[2] == "log.error"
                for highlights in output._highlights.values()
                for highlight in highlights
            )

            review = app.query_one("#review", Markdown)
            assert review.language == "markdown"
            review.focus()
            await pilot.press("ctrl+a")
            assert review.selected_text == "review line one\nreview line two"
            await pilot.press("ctrl+c")
            clipboard.assert_called_with("review line one\nreview line two")

            review.update("\x1b[33m## Warning\x1b[0m")
            assert review.text == "## Warning"
            assert "\x1b" not in review.text

    asyncio.run(exercise())


def test_captured_source_manifest_wins_over_new_checkout_and_is_reconciled(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    # Simulate a repository that advanced after the working setup was exported.
    (target / "requirements.txt").write_text("comfy-kitchen==9.9.9\n", encoding="utf-8")
    captured = b"comfy-kitchen==0.2.21\nrequests>=2\n"
    bundle = tmp_path / "working.comfyuisetup"
    payload = "dependency_manifests/comfyui/requirements.txt"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(payload, captured)

    profile = {
        "_bundle_path": str(bundle),
        "constraints": {},
        "dependency_manifests": [
            {
                "scope": "comfyui",
                "relative_path": "requirements.txt",
                "payload": payload,
                "sha256": hashlib.sha256(captured).hexdigest(),
            }
        ],
        "dependency_resolution": {"resolved_versions": {}},
        "environment_lock": {
            "packages": [
                {
                    "name": "comfy-kitchen",
                    "version": "0.2.20",
                    "requirement": "comfy-kitchen==0.2.20",
                    "install": True,
                    "portable": True,
                    "source": "index",
                }
            ]
        },
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
    engine._uv_pip = lambda *args, **kwargs: 0  # type: ignore[method-assign]

    engine._install_core()

    resolved = (target / ".comfy-setup" / "requirements-comfyui.txt").read_text(encoding="utf-8")
    assert "comfy-kitchen==0.2.20" in resolved
    assert "comfy-kitchen==0.2.21\n" not in resolved
    assert "comfy-kitchen==9.9.9" not in resolved
    assert "requests>=2" in resolved
    assert any("0.2.21" in warning and "0.2.20" in warning for warning in engine.warnings)
