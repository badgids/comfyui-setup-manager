from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comfy_setup.runner import Runner
from comfy_setup.updates import (
    ComfyUpdateManager,
    UpdateError,
    UpdateIssue,
    _parse_requirements,
    _requirement_conflicts,
)


def git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return completed.stdout.strip()


def commit_all(path: Path, message: str) -> str:
    git(path, "add", ".")
    git(path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", message)
    return git(path, "rev-parse", "HEAD")


class RequirementConflictTests(unittest.TestCase):
    def test_detects_disjoint_non_exact_ranges(self) -> None:
        core = _parse_requirements("numpy>=2.0")["numpy"]
        node = _parse_requirements("numpy<2.0")["numpy"]
        self.assertTrue(_requirement_conflicts(core, node))

    def test_accepts_overlapping_non_exact_ranges(self) -> None:
        core = _parse_requirements("numpy>=2.0,<3")["numpy"]
        node = _parse_requirements("numpy>=2.2,<2.5")["numpy"]
        self.assertFalse(_requirement_conflicts(core, node))


class UpdateManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.official_work = self.root / "official-work"
        self.official_bare = self.root / "official.git"
        self.installation = self.root / "ComfyUI"

        self.official_work.mkdir()
        git(self.official_work, "init", "-b", "master")
        (self.official_work / "main.py").write_text("print('ready')\n", encoding="utf-8")
        (self.official_work / "requirements.txt").write_text("packaging>=24\n", encoding="utf-8")
        (self.official_work / "manager_requirements.txt").write_text("rich>=14\n", encoding="utf-8")
        (self.official_work / "nodes.py").write_text("# nodes\n", encoding="utf-8")
        self.first_commit = commit_all(self.official_work, "initial")
        subprocess.run(["git", "clone", "--bare", str(self.official_work), str(self.official_bare)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "clone", str(self.official_bare), str(self.installation)], check=True, stdout=subprocess.DEVNULL)

        venv_bin = self.installation / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        venv_bin.mkdir(parents=True)
        python_name = "python.exe" if os.name == "nt" else "python"
        try:
            os.symlink(sys.executable, venv_bin / python_name)
        except OSError:
            shutil.copy2(sys.executable, venv_bin / python_name)

        (self.installation / "custom_nodes" / "LocalNode").mkdir(parents=True)
        (self.installation / "custom_nodes" / "LocalNode" / "__init__.py").write_text("VALUE=1\n", encoding="utf-8")
        (self.installation / "models").mkdir()
        (self.installation / "models" / "do-not-copy.bin").write_bytes(b"model")

        (self.official_work / "main.py").write_text("print('updated')\n", encoding="utf-8")
        (self.official_work / "requirements.txt").write_text("packaging>=25\n", encoding="utf-8")
        (self.official_work / "manager_requirements.txt").write_text("rich>=15\n", encoding="utf-8")
        self.second_commit = commit_all(self.official_work, "update")
        git(self.official_work, "remote", "add", "origin", str(self.official_bare))
        git(self.official_work, "push", "origin", "master")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def manager(self) -> ComfyUpdateManager:
        return ComfyUpdateManager(
            self.installation,
            runner=Runner(),
            official_repository=str(self.official_bare),
        )

    def checked_preflight(self, manager: ComfyUpdateManager | None = None):
        manager = manager or self.manager()
        with (
            patch.object(manager, "_pip_check", return_value=(True, "")),
            patch.object(manager, "_dry_run_resolution", return_value=(True, "compatible plan")),
            patch.object(manager, "_protected_package_names", return_value=set()),
        ):
            return manager.preflight(fetch=True)

    def test_preflight_finds_update_and_preserves_custom_node_manifest(self) -> None:
        preflight = self.checked_preflight()
        self.assertTrue(preflight.update_available)
        self.assertEqual(preflight.current_commit, self.first_commit)
        self.assertEqual(preflight.target_commit, self.second_commit)
        self.assertEqual(preflight.custom_node_count, 1)
        self.assertEqual(preflight.dirty_custom_nodes, 0)
        self.assertTrue(any("main.py" in item for item in preflight.core_file_changes))
        self.assertEqual({item.name for item in preflight.core_package_changes}, {"packaging", "rich"})

    def test_snapshot_is_lightweight_and_excludes_models_venv_and_nodes(self) -> None:
        manager = self.manager()
        with patch.object(manager, "_freeze_environment", return_value=["packaging==25.0"]):
            snapshot = manager.create_snapshot(target_commit=self.second_commit)
        self.assertTrue((snapshot.path / "snapshot.json").is_file())
        self.assertTrue((snapshot.path / "custom-nodes.json").is_file())
        all_names = {path.relative_to(snapshot.path).as_posix() for path in snapshot.path.rglob("*")}
        self.assertFalse(any(name.startswith("models/") for name in all_names))
        self.assertFalse(any(name.startswith(".venv/") for name in all_names))
        self.assertFalse(any(name.startswith("custom_nodes/") for name in all_names))
        manifest = json.loads((snapshot.path / "snapshot.json").read_text())
        self.assertEqual(manifest["commit"], self.first_commit)
        self.assertEqual(manifest["custom_node_count"], 1)

    def test_preflight_reads_pyproject_node_dependencies(self) -> None:
        node = self.installation / "custom_nodes" / "LocalNode"
        (node / "pyproject.toml").write_text(
            "[project]\nname='local-node'\nversion='1.0'\ndependencies=['packaging<25']\n",
            encoding="utf-8",
        )
        preflight = self.checked_preflight()
        conflicts = [issue for issue in preflight.issues if issue.title == "Custom-node dependency conflict"]
        self.assertTrue(conflicts)

    def test_update_installs_core_and_manager_requirements(self) -> None:
        manager = self.manager()
        self.checked_preflight(manager)
        with patch.object(manager, "_freeze_environment", return_value=["packaging==25.0"]):
            snapshot = manager.create_snapshot(target_commit=self.second_commit)
        git(self.installation, "checkout", "-B", "comfyui-managed", self.second_commit)
        commands: list[list[str]] = []

        class RecordingRunner:
            def run(self, command, **kwargs):
                commands.append(list(command))
                return 0

            def log(self, line):
                return None

        manager.runner = RecordingRunner()  # type: ignore[assignment]
        manager._install_reconciled_requirements(
            snapshot,
            target_ref=self.second_commit,
            strategy="patch",
        )
        command = commands[-1]
        requirements_indexes = [index for index, value in enumerate(command) if value == "-r"]
        self.assertEqual(len(requirements_indexes), 1)
        installed_file = Path(command[requirements_indexes[0] + 1])
        self.assertEqual(installed_file.name, "update-apply-reconciled-requirements.txt")
        self.assertNotEqual(installed_file, self.installation / "requirements.txt")
        plan = installed_file.read_text(encoding="utf-8")
        self.assertIn("packaging>=25", plan)
        self.assertIn("rich>=15", plan)
        self.assertIn("upstream requirements.txt is never executed directly", plan)

    def test_update_leaves_custom_nodes_and_models_in_place(self) -> None:
        manager = self.manager()
        preflight = self.checked_preflight(manager)
        for change in preflight.core_package_changes:
            change.required = False
        with (
            patch.object(manager, "_freeze_environment", return_value=["packaging==25.0"]),
            patch.object(manager, "_install_reconciled_requirements") as package_install,
            patch.object(manager, "_pip_check", return_value=(True, "")),
            patch.object(manager, "_startup_validation", return_value=(True, self.root / "validation.log", [])),
        ):
            result = manager.update(preflight)
        self.assertTrue(result.success, result.error)
        self.assertEqual(git(self.installation, "rev-parse", "HEAD"), self.second_commit)
        self.assertTrue((self.installation / "custom_nodes" / "LocalNode" / "__init__.py").is_file())
        self.assertTrue((self.installation / "models" / "do-not-copy.bin").is_file())
        self.assertIsNotNone(result.snapshot)
        package_install.assert_not_called()

    def test_rollback_restores_previous_commit_without_touching_custom_nodes(self) -> None:
        manager = self.manager()
        preflight = self.checked_preflight(manager)
        with (
            patch.object(manager, "_freeze_environment", return_value=["packaging==25.0"]),
            patch.object(manager, "_install_reconciled_requirements"),
            patch.object(manager, "_pip_check", return_value=(True, "")),
            patch.object(manager, "_startup_validation", return_value=(True, self.root / "validation.log", [])),
        ):
            updated = manager.update(preflight)
        assert updated.snapshot is not None
        with (
            patch.object(manager, "_restore_environment"),
            patch.object(manager, "_pip_check", return_value=(True, "")),
            patch.object(manager, "_startup_validation", return_value=(True, self.root / "validation.log", [])),
        ):
            result = manager.rollback(updated.snapshot.snapshot_id)
        self.assertTrue(result.success, result.error)
        self.assertEqual(git(self.installation, "rev-parse", "HEAD"), self.first_commit)
        self.assertTrue((self.installation / "custom_nodes" / "LocalNode" / "__init__.py").is_file())
        self.assertTrue((self.installation / "models" / "do-not-copy.bin").is_file())

    def test_preflight_keeps_snapshotted_patch_available_when_resolution_fails(self) -> None:
        manager = self.manager()
        with (
            patch.object(manager, "_pip_check", return_value=(True, "")),
            patch.object(manager, "_installed_versions", return_value={"packaging": "1.0", "rich": "1.0"}),
            patch.object(manager, "_dry_run_resolution", return_value=(False, "No solution for protected torch")),
            patch.object(manager, "_protected_package_names", return_value={"torch"}),
        ):
            preflight = manager.preflight(fetch=True)
        self.assertFalse(preflight.resolution_possible)
        self.assertTrue(preflight.resolution_checked)
        self.assertTrue(preflight.patchable)
        self.assertTrue(any(issue.title.startswith("No compatible") for issue in preflight.issues))

    def test_current_install_skips_unnecessary_dependency_resolution(self) -> None:
        git(self.installation, "fetch", "origin", "master")
        git(self.installation, "checkout", "--detach", self.second_commit)
        manager = self.manager()
        with (
            patch.object(manager, "_pip_check", return_value=(True, "")),
            patch.object(manager, "_dry_run_resolution") as resolver,
        ):
            preflight = manager.preflight(fetch=True)
        self.assertFalse(preflight.update_available)
        self.assertFalse(preflight.resolution_checked)
        self.assertTrue(preflight.resolution_possible)
        resolver.assert_not_called()
        self.assertTrue(any(issue.title == "No dependency mutation is required" for issue in preflight.issues))

    def test_duplicate_normalized_freeze_entries_use_effective_installed_version(self) -> None:
        manager = self.manager()
        with patch.object(
            manager,
            "_installed_versions",
            return_value={"tensorrt-cu13-libs": "10.14.1.48.post1"},
        ):
            frozen = manager._deduplicate_freeze_lines([
                "tensorrt-cu13-libs==10.14.1.48",
                "tensorrt_cu13_libs==10.14.1.48.post1",
                "torch==2.12.1+cu130",
            ])
        self.assertEqual(
            [line for line in frozen if line.lower().startswith("tensorrt")],
            ["tensorrt_cu13_libs==10.14.1.48.post1"],
        )
        self.assertEqual(manager._freeze_duplicate_packages, {"tensorrt-cu13-libs"})

    def test_all_noncore_and_unselected_core_packages_are_constrained(self) -> None:
        manager = self.manager()
        self.checked_preflight(manager)
        with (
            patch.object(
                manager,
                "_installed_versions",
                return_value={"packaging": "25.0", "rich": "15.0", "numpy": "2.2.0", "local-addon": "1.0"},
            ),
            patch.object(manager, "_native_package_names", return_value=set()),
            patch.object(manager, "_profile_protected_names", return_value=set()),
        ):
            protected = manager._protected_package_names(self.second_commit)
        self.assertIn("numpy", protected)
        self.assertIn("local-addon", protected)
        self.assertNotIn("packaging", protected)
        self.assertNotIn("rich", protected)

        with (
            patch.object(manager, "_protected_package_names", return_value={"numpy", "local-addon"}),
            patch.object(
                manager,
                "_installed_versions",
                return_value={"packaging": "25.0", "rich": "15.0", "numpy": "2.2.0", "local-addon": "1.0"},
            ),
        ):
            requirements, constraints = manager._write_update_inputs(
                self.second_commit,
                include_custom_nodes=False,
                freeze_lines=["packaging==25.0", "rich==15.0", "numpy==2.2.0", "local-addon==1.0"],
                selected_core_packages={"rich"},
                selectable_core_packages={"packaging", "rich"},
                additional_protected_names={"packaging"},
                prefix="protected-selection",
            )
        self.assertIn("rich>=15", requirements.read_text(encoding="utf-8"))
        constraint_text = constraints.read_text(encoding="utf-8")
        self.assertIn("packaging==25.0", constraint_text)
        self.assertNotIn("rich==15.0", constraint_text)
        self.assertIn("numpy==2.2.0", constraint_text)
        self.assertIn("local-addon==1.0", constraint_text)

    def test_reconciled_plan_includes_custom_node_and_protects_native_packages(self) -> None:
        node = self.installation / "custom_nodes" / "LocalNode"
        (node / "requirements.txt").write_text("numpy<3\n", encoding="utf-8")
        manager = self.manager()
        self.checked_preflight(manager)
        with patch.object(manager, "_protected_package_names", return_value={"torch", "flash-attn"}):
            requirements, constraints = manager._write_update_inputs(
                self.second_commit,
                include_custom_nodes=True,
                freeze_lines=[
                    "torch==2.6.0+cu124",
                    "flash-attn @ file:///opt/wheels/flash_attn.whl",
                    "numpy==2.2.0",
                ],
                prefix="test-plan",
            )
        plan = requirements.read_text(encoding="utf-8")
        protected = constraints.read_text(encoding="utf-8")
        self.assertIn("packaging>=25", plan)
        self.assertIn("rich>=15", plan)
        self.assertIn("numpy<3", plan)
        self.assertIn("torch==2.6.0+cu124", protected)
        self.assertIn("flash-attn @ file:///opt/wheels/flash_attn.whl", protected)
        self.assertNotIn("numpy==2.2.0", protected)

    def test_package_selection_omits_unselected_changed_core_library(self) -> None:
        manager = self.manager()
        self.checked_preflight(manager)
        with patch.object(manager, "_protected_package_names", return_value=set()):
            requirements, _constraints = manager._write_update_inputs(
                self.second_commit,
                include_custom_nodes=True,
                freeze_lines=[],
                selected_core_packages={"rich"},
                selectable_core_packages={"packaging", "rich"},
                prefix="selected-plan",
            )
        plan = requirements.read_text(encoding="utf-8")
        self.assertIn("rich>=15", plan)
        self.assertNotIn("packaging>=25", plan)

    def test_failed_candidate_is_rolled_back_automatically(self) -> None:
        manager = self.manager()
        preflight = self.checked_preflight(manager)
        preflight.core_package_changes[0].required = True
        with (
            patch.object(manager, "_freeze_environment", return_value=["packaging==25.0"]),
            patch.object(manager, "_install_reconciled_requirements", side_effect=RuntimeError("resolver failed")),
            patch.object(manager, "_restore_environment"),
            patch.object(manager, "_pip_check", return_value=(True, "")),
            patch.object(manager, "_startup_validation", return_value=(True, self.root / "rollback.log", [])),
        ):
            result = manager.update(preflight, strategy="patch")
        self.assertFalse(result.success)
        self.assertTrue(result.rolled_back, result.rollback_error)
        self.assertIn("restored automatically", result.error or "")
        self.assertEqual(git(self.installation, "rev-parse", "HEAD"), self.first_commit)

    def test_failed_pip_check_is_rolled_back_automatically(self) -> None:
        manager = self.manager()
        preflight = self.checked_preflight(manager)
        with (
            patch.object(manager, "_freeze_environment", return_value=["packaging==25.0"]),
            patch.object(manager, "_install_reconciled_requirements"),
            patch.object(manager, "_restore_environment"),
            patch.object(manager, "_pip_check", side_effect=[(False, "broken dependency"), (True, "")]),
            patch.object(manager, "_startup_validation", return_value=(True, self.root / "rollback.log", [])),
        ):
            result = manager.update(preflight, strategy="patch")
        self.assertFalse(result.success)
        self.assertTrue(result.rolled_back, result.rollback_error)
        self.assertIn("broken dependency", result.error or "")
        self.assertEqual(git(self.installation, "rev-parse", "HEAD"), self.first_commit)

    def test_safe_strategy_refuses_risks_before_snapshot(self) -> None:
        manager = self.manager()
        preflight = self.checked_preflight(manager)
        preflight.issues.append(UpdateIssue("warning", "customized", "local source changes"))
        with patch.object(manager, "create_snapshot") as snapshot:
            with self.assertRaises(UpdateError):
                manager.update(preflight, strategy="safe")
        snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
