from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import time
import zipfile

import yaml
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .compatibility_tags import compatibility_from_profile
from .models import InstallOptions, InstallResult, InstallStep, PlatformInfo
from .launchers import write_local_launchers
from .profile import project_root
from .platforms import cuda_home, windows_msvc_environment
from .package_sources import (
    PYPI_SIMPLE,
    PackageSourceError,
    clean_package_environment,
    validate_github_repository,
    validate_package_index,
    validate_requirement_line,
)
from .runner import Runner, executable_for_venv
from .system_deps import install_commands, manual_instructions, missing_dependencies
from .pytorch_install import build_torch_install_plan, verification_code
from .wheels import WheelDecisionProvider, WheelManager
from .shared_assets import SharedAssetPaths, configure_instance_shared_assets, load_shared_asset_paths
from .asset_catalog import add_entry, add_source, download_entry
from .dependency_resolver import (
    comfyui_startup_failures, inspect_dependency_issues, missing_module_names,
    reconcile_requirements_text,
)
from .environment_lock import (
    compatibility_issues as environment_lock_compatibility_issues,
    lock_embedded_wheels,
    lock_has_packages,
    lock_install_requirements,
    lock_reproducible_version_map,
    lock_source_tree_packages,
    managed_package_reason,
    normalized_name,
)

ProgressCallback = Callable[[int, int, str], None]


class InstallerError(RuntimeError):
    pass


CONFLICT_PACKAGES = {
    "torch",
    "torchvision",
    "torchaudio",
    "numpy",
    "transformers",
    "cupy",
    "cupy-wheel",
    "cupy-cuda11x",
    "cupy-cuda12x",
    "cupy-cuda13x",
    "tensorrt",
    "tensorrt-cu12",
    "tensorrt-cu13",
    "tensorrt_cu12",
    "tensorrt_cu13",
    "tensorrt_cu13_bindings",
    "tensorrt_cu13_libs",
    "cuda-toolkit",
    "onnxruntime",
    "onnxruntime-gpu",
}


def _requirement_name(line: str) -> str:
    value = line.strip()
    if not value or value.startswith("#") or value.startswith(("-r", "--")):
        return ""
    if value.startswith(("git+", "http://", "https://")):
        return ""
    value = value.split(";", 1)[0].strip()
    value = re.split(r"[\[<>=!~ @]", value, maxsplit=1)[0]
    return value.strip().lower().replace("_", "-")


def _write_filtered_requirements(source: Path, destination: Path) -> None:
    managed = {item.replace("_", "-") for item in CONFLICT_PACKAGES}
    lines: list[str] = []
    for raw in source.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            validate_requirement_line(raw)
        except PackageSourceError as exc:
            raise InstallerError(f"Unsafe package source in {source}: {exc}") from exc
        name = _requirement_name(raw)
        if name in managed:
            lines.append(f"# Managed by installer: {raw}")
        else:
            lines.append(raw)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")




def _write_source_safe_requirements(source: Path, destination: Path) -> None:
    lines: list[str] = []
    for raw in source.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            validate_requirement_line(raw)
        except PackageSourceError as exc:
            raise InstallerError(f"Unsafe package source in {source}: {exc}") from exc
        lines.append(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


class InstallerEngine:
    def __init__(
        self,
        profile: dict[str, Any],
        platform_info: PlatformInfo,
        options: InstallOptions,
        *,
        log: Callable[[str], None] | None = None,
        progress: ProgressCallback | None = None,
        secret_provider: Callable[[str], str | None] | None = None,
        wheel_decision_provider: WheelDecisionProvider | None = None,
        uv_executable: Path | None = None,
    ) -> None:
        self.profile = profile
        self.platform_info = platform_info
        self.options = options
        self.log = log or (lambda line: None)
        self.progress = progress or (lambda current, total, title: None)
        configured_uv = os.environ.get("COMFY_INSTALLER_UV")
        self.uv = uv_executable or (Path(configured_uv) if configured_uv else None)
        if self.uv is None:
            self.uv = Path(shutil.which("uv") or "uv")
        self.runner = Runner(self.log, secret_provider)
        self.wheel_decision_provider = wheel_decision_provider
        self.target = options.target_dir.expanduser().resolve()
        self.venv = self.target / ".venv"
        self.python = executable_for_venv(self.venv, "python")
        self.state_dir = self.target / ".comfy-setup"
        self.completed: list[str] = []
        self.warnings: list[str] = []

    def selected_nodes(self) -> list[dict[str, Any]]:
        output = []
        for node in self.profile.get("nodes", []):
            if node["id"] not in self.options.selected_nodes:
                continue
            if not self.platform_info.supports(
                node.get("platforms", []), node.get("accelerators", [])
            ):
                continue
            output.append(node)
        return output

    def selected_acceleration(self) -> list[dict[str, Any]]:
        output = []
        described_ids: set[str] = set()
        for item in self.profile.get("accelerated_packages", []):
            described_ids.add(str(item.get("id") or ""))
            if item["id"] not in self.options.selected_acceleration:
                continue
            if not self.platform_info.supports(
                item.get("platforms", []), item.get("accelerators", [])
            ):
                continue
            output.append(item)
        # v0.8.4 exact exports classified PyOpenGL-accelerate as installer
        # managed but accidentally omitted its acceleration descriptor. Recover
        # the descriptor from the authoritative lock so an existing profile is
        # repairable without re-exporting the source installation.
        lock = self.profile.get("environment_lock", {})
        if isinstance(lock, dict) and lock.get("mode") == "exact" and "pyopengl-accelerate" not in described_ids:
            version = lock_reproducible_version_map(lock).get("pyopengl-accelerate")
            recovered = {
                "id": "pyopengl-accelerate", "name": "PyOpenGL Accelerate",
                "import_name": "OpenGL_accelerate", "kind": "pip-wheel-preferred",
                "package": f"PyOpenGL-accelerate=={version}" if version else "",
                "platforms": ["windows", "linux", "macos"],
                "accelerators": ["nvidia", "rocm", "mps", "cpu"],
                "selected": True, "exact": True,
            }
            if version and self.platform_info.supports(recovered["platforms"], recovered["accelerators"]):
                output.append(recovered)
        return output

    def _requires_exact_source(self) -> bool:
        lock = self.profile.get("environment_lock", {})
        return bool(
            self.profile.get("comfyui", {}).get("exact")
            or (isinstance(lock, dict) and lock.get("mode") == "exact" and lock.get("packages"))
        )

    def plan(self) -> list[InstallStep]:
        repository_detail = (
            "Keep the selected existing checkout and its current Git origin."
            if self.options.use_current_checkout
            else f"Apply {self.profile.get('comfyui', {}).get('repository')} to the target checkout or clone it when the target is new."
        )
        steps = [
            InstallStep("system", "System prerequisites", "Check and optionally install operating-system tools."),
            InstallStep("repo", "ComfyUI checkout", repository_detail),
            InstallStep("venv", "Python environment", f"Create a Python {self.options.python_version} virtual environment with uv."),
            InstallStep("torch", "PyTorch", f"Install the {self.options.accelerator} PyTorch build."),
            InstallStep("core", "ComfyUI dependencies", "Install core requirements under compatibility constraints."),
        ]
        if lock_has_packages(self.profile.get("environment_lock")):
            steps.append(
                InstallStep(
                    "environment-lock",
                    "Exact Python environment",
                    "Install every portable distribution version captured from the working source environment.",
                )
            )
        steps.append(
            InstallStep("extra", "Profile Python packages", "Install additional portable Python packages declared by the profile.")
        )
        for node in self.selected_nodes():
            source = node.get("source", {})
            if source.get("type") == "embedded":
                detail = "Install the unpublished local plugin embedded in this portable profile."
            elif source.get("type") == "snapshot":
                detail = "Install the sanitized source snapshot captured from the working installation."
            else:
                manager_id = source.get("manager_id")
                repository = source.get("repository")
                if manager_id and repository:
                    detail = f"Clone {repository} directly; retain Registry/Manager id {manager_id} as provenance."
                elif manager_id:
                    detail = f"Acquire through Comfy Registry/Manager ({manager_id}) without installing dependencies."
                else:
                    detail = f"Clone and install {repository}."
            steps.append(
                InstallStep(
                    f"node:{node['id']}",
                    node["name"],
                    detail,
                )
            )
        if lock_source_tree_packages(self.profile.get("environment_lock")):
            steps.append(
                InstallStep(
                    "source-packages",
                    "Locked source-tree packages",
                    "Install editable or local Python distributions from the exact ComfyUI/custom-node source captured by the profile.",
                )
            )
        for item in self.selected_acceleration():
            steps.append(
                InstallStep(
                    f"accel:{item['id']}",
                    item["name"],
                    "Use a compatible wheel when available; otherwise build from source.",
                    optional=True,
                )
            )
        steps.extend(
            [
                InstallStep("reconcile", "Dependency reconciliation", "Restore profile constraints, install missing transitive requirements, and verify the package environment."),
                InstallStep("assets", "Shared models and workflows", "Create or reuse shared external libraries, write extra_model_paths.yaml, and connect the native workflow library."),
                InstallStep("launchers", "Local ComfyUI launchers", "Create self-contained launchers inside this ComfyUI installation."),
                InstallStep("validate", "Validation", "Import-test the environment and load every selected custom node with ComfyUI's quick test."),
            ]
        )
        return steps

    def _mark(self, key: str, title: str, index: int, total: int) -> None:
        self.completed.append(key)
        self.progress(index, total, title)

    def _install_system_dependencies(self) -> None:
        missing = missing_dependencies(
            self.profile,
            self.platform_info,
            source_builds=self.options.allow_source_builds,
            selected_nodes=self.options.selected_nodes,
        )
        if not missing:
            self.log("All selected operating-system prerequisites are present.")
            return

        self.log("Missing system dependencies:")
        for dependency in missing:
            level = "required" if dependency.required else "recommended"
            self.log(f"  • {dependency.name} ({level}) — {dependency.why}")

        commands = install_commands(self.platform_info, missing)
        if self.options.auto_install_system and commands:
            self.log("Installing operating-system packages inside the embedded manager console.")
            for command in commands:
                # APT update may fail because of an unrelated stale source, such
                # as an old local CUDA repository. Do not discard the rest of
                # the installation before trying the actual package install.
                is_apt_update = "apt-get" in command and command[-1:] == ["update"]
                if is_apt_update:
                    result = self.runner.run(command, check=False)
                    if result != 0:
                        warning = (
                            "apt-get update failed, usually because an unrelated configured repository is stale. "
                            "The manager will attempt the requested package installation using the existing package indexes. "
                            "The full error and recovery shell remain available in this screen."
                        )
                        self.warnings.append(warning)
                        self.log(f"WARNING: {warning}")
                    continue
                self.runner.run(command)

            remaining = missing_dependencies(
                self.profile,
                self.platform_info,
                source_builds=self.options.allow_source_builds,
                selected_nodes=self.options.selected_nodes,
            )
            if remaining:
                instructions = manual_instructions(self.platform_info, remaining)
                if any(item.required for item in remaining):
                    raise InstallerError(instructions)
                self.warnings.append(instructions)
                self.log(f"WARNING: {instructions}")
            return

        instructions = manual_instructions(self.platform_info, missing)
        if any(item.required for item in missing):
            raise InstallerError(instructions)
        self.warnings.append(instructions)
        self.log(f"WARNING: {instructions}")

    @staticmethod
    def _is_comfy_checkout(path: Path) -> bool:
        return (path / "main.py").is_file() and (path / "requirements.txt").is_file()

    @staticmethod
    def _normalized_repository(value: str | None) -> str:
        if not value:
            return ""
        text = value.strip().rstrip("/")
        if text.startswith("git@github.com:"):
            text = "https://github.com/" + text.split(":", 1)[1]
        if text.endswith(".git"):
            text = text[:-4]
        return text.lower()

    def _git_output(self, *arguments: str, check: bool = False) -> str:
        completed = self.runner.capture(["git", *arguments], cwd=self.target, check=check)
        return completed.stdout.strip()

    def _apply_repository_to_existing_checkout(self) -> None:
        if not (self.target / ".git").exists():
            raise InstallerError(
                "The selected target looks like ComfyUI but is not a Git checkout, so a different repository cannot be applied safely. "
                "Choose 'Keep existing checkout' or select an empty directory for a new clone."
            )

        dirty = self._git_output("status", "--porcelain")
        if dirty:
            raise InstallerError(
                "The selected repository cannot be applied because the existing ComfyUI checkout has local changes. "
                "Commit or stash them, then retry. Nothing was changed."
            )

        repository = validate_github_repository(self.profile["comfyui"]["repository"])
        branch = str(self.profile["comfyui"].get("branch") or "master")
        current_origin = self._git_output("remote", "get-url", "origin")
        head = self._git_output("rev-parse", "--short", "HEAD") or "head"
        backup_branch = f"comfy-setup-backup-{time.strftime('%Y%m%d-%H%M%S')}-{head}"

        # Preserve the exact pre-change commit before changing remotes or branches.
        self.runner.run(["git", "branch", backup_branch, "HEAD"], cwd=self.target)
        self.log(f"Created safety branch: {backup_branch}")

        if current_origin:
            if self._normalized_repository(current_origin) != self._normalized_repository(repository):
                self.runner.run(["git", "remote", "set-url", "origin", repository], cwd=self.target)
                self.log(f"Updated origin from {current_origin} to {repository}")
            else:
                self.log(f"Existing origin already matches the selected repository: {repository}")
        else:
            self.runner.run(["git", "remote", "add", "origin", repository], cwd=self.target)
            self.log(f"Added origin: {repository}")

        self.runner.run(["git", "fetch", "origin", "--prune"], cwd=self.target)
        preferred_commit = self.profile["comfyui"].get("preferred_commit")
        if preferred_commit and (self.options.pin_exact_refs or self._requires_exact_source()):
            # Fetching the selected origin above makes the profile commit available
            # when it belongs to that repository.
            self.runner.run(["git", "checkout", "--detach", str(preferred_commit)], cwd=self.target)
            self.log(f"Checked out profile commit {preferred_commit}")
            return

        remote_branch = f"origin/{branch}"
        exists = self.runner.capture(
            ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{remote_branch}"],
            cwd=self.target,
            check=False,
        )
        if exists.returncode != 0:
            raise InstallerError(
                f"The selected repository does not provide branch {branch!r}: {repository}"
            )
        self.runner.run(["git", "checkout", "-B", branch, remote_branch], cwd=self.target)
        self.log(f"Applied repository {repository} on branch {branch}")

    def _verify_existing_exact_checkout(self, checkout: Path) -> None:
        preferred_commit = str(self.profile.get("comfyui", {}).get("preferred_commit") or "").strip()
        if not preferred_commit:
            raise InstallerError(
                "This profile requires exact source reproduction, but it does not contain an immutable ComfyUI commit."
            )
        dirty = self.runner.capture(["git", "status", "--porcelain"], cwd=checkout)
        if dirty.stdout.strip():
            raise InstallerError(
                "The selected existing ComfyUI checkout has local changes and cannot be used as the exact exported source."
            )
        head = self.runner.capture(["git", "rev-parse", "HEAD"], cwd=checkout)
        if head.stdout.strip() != preferred_commit:
            raise InstallerError(
                f"The selected existing ComfyUI checkout is at {head.stdout.strip() or 'an unknown commit'}, "
                f"but this exact profile requires {preferred_commit}. Disable 'keep existing checkout' so the installer "
                "can apply the exported commit, or choose a matching checkout."
            )
        expected_repository = validate_github_repository(str(self.profile["comfyui"]["repository"]))
        origin = self.runner.capture(["git", "remote", "get-url", "origin"], cwd=checkout, check=False)
        if origin.returncode != 0 or self._normalized_repository(origin.stdout.strip()) != self._normalized_repository(expected_repository):
            raise InstallerError(
                "The selected existing ComfyUI checkout does not use the repository recorded by this exact profile."
            )


    def _extract_comfyui_snapshot(self) -> None:
        source = self.profile.get("comfyui", {}).get("source", {})
        bundle_value = self.profile.get("_bundle_path")
        if not bundle_value:
            raise InstallerError("This profile embeds the ComfyUI source, but the .comfyuisetup bundle is unavailable.")
        bundle = Path(str(bundle_value)).expanduser().resolve()
        payload = str(source.get("payload") or "embedded_comfyui").rstrip("/")
        prefix = payload + "/"
        if self.target.exists() and any(self.target.iterdir()):
            raise InstallerError(
                f"The embedded ComfyUI source requires an empty installation directory: {self.target}. "
                "Choose a new directory or explicitly keep an already matching installation."
            )
        self.target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(bundle) as archive:
                members = [name for name in archive.namelist() if name.startswith(prefix) and not name.endswith("/")]
                if not members:
                    raise InstallerError("The embedded ComfyUI source snapshot is missing from the profile bundle.")
                for member_name in members:
                    relative = PurePosixPath(member_name[len(prefix):])
                    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                        raise InstallerError(f"Unsafe embedded ComfyUI source member: {member_name}")
                    output = self.target.joinpath(*relative.parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(archive.read(member_name))
        except zipfile.BadZipFile as exc:
            raise InstallerError(f"Invalid .comfyuisetup bundle: {bundle}") from exc
        if not self._is_comfy_checkout(self.target):
            raise InstallerError("The embedded source snapshot did not produce a valid ComfyUI checkout.")
        self.log("Installed sanitized ComfyUI source snapshot captured from the working installation.")

    def _apply_comfyui_overlay(self) -> None:
        source = self.profile.get("comfyui", {}).get("source", {})
        if not isinstance(source, dict) or source.get("type") != "remote":
            return
        overlay = source.get("overlay")
        if not isinstance(overlay, dict):
            return
        bundle_value = self.profile.get("_bundle_path")
        if not bundle_value:
            raise InstallerError("This profile references a ComfyUI source overlay, but its bundle is unavailable.")
        bundle = Path(str(bundle_value)).expanduser().resolve()
        prefix = str(overlay.get("payload") or "comfyui_overlay").rstrip("/") + "/"
        changed = [str(value) for value in overlay.get("changed_files", [])]
        deleted = [str(value) for value in overlay.get("deleted_files", [])]
        try:
            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                for relative_value in changed:
                    relative = PurePosixPath(relative_value)
                    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                        raise InstallerError(f"Unsafe ComfyUI overlay path: {relative_value}")
                    member = prefix + relative.as_posix()
                    if member not in names:
                        raise InstallerError(f"ComfyUI overlay file is missing: {relative_value}")
                    output = self.target.joinpath(*relative.parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(archive.read(member))
        except zipfile.BadZipFile as exc:
            raise InstallerError(f"Invalid .comfyuisetup bundle: {bundle}") from exc

        for relative_value in deleted:
            relative = PurePosixPath(relative_value)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise InstallerError(f"Unsafe ComfyUI overlay deletion: {relative_value}")
            target = self.target.joinpath(*relative.parts)
            if target.is_file() or target.is_symlink():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                shutil.rmtree(target)
        self.log(
            f"Applied compact ComfyUI overlay: {len(changed)} changed/new file(s), "
            f"{len(deleted)} deletion(s)."
        )

    def _prepare_repository(self) -> None:
        comfy_source = self.profile.get("comfyui", {}).get("source", {})
        if isinstance(comfy_source, dict) and comfy_source.get("type") == "snapshot":
            if self.options.use_current_checkout and self._is_comfy_checkout(self.target):
                self.log(f"Using the selected existing ComfyUI checkout unchanged: {self.target}")
                return
            self._extract_comfyui_snapshot()
            return
        current_root = project_root()
        use_current = (
            self.options.use_current_checkout
            and self._is_comfy_checkout(current_root)
            and self.target == current_root.resolve()
        )
        if use_current:
            if self._requires_exact_source():
                self._verify_existing_exact_checkout(current_root)
            self.log(f"Using the current ComfyUI checkout and its existing origin: {current_root}")
            return

        if self._is_comfy_checkout(self.target):
            if self.options.use_current_checkout:
                if self._requires_exact_source():
                    self._verify_existing_exact_checkout(self.target)
                current_origin = self._git_output("remote", "get-url", "origin")
                self.log(
                    f"Using the existing ComfyUI checkout unchanged: {self.target}"
                    + (f" (origin: {current_origin})" if current_origin else "")
                )
                return
            self._apply_repository_to_existing_checkout()
            return

        if self.target.exists() and any(self.target.iterdir()):
            raise InstallerError(
                f"The install directory is not empty and is not a ComfyUI checkout: {self.target}"
            )

        self.target.parent.mkdir(parents=True, exist_ok=True)
        repository = validate_github_repository(self.profile["comfyui"]["repository"])
        branch = self.profile["comfyui"].get("branch", "master")
        preferred_commit = self.profile["comfyui"].get("preferred_commit")
        exact_source = self._requires_exact_source()
        if preferred_commit and (self.options.pin_exact_refs or exact_source):
            self.runner.run(["git", "clone", "--filter=blob:none", repository, str(self.target)])
            result = self.runner.run(["git", "checkout", str(preferred_commit)], cwd=self.target, check=False)
            if result != 0:
                raise InstallerError(
                    f"The exact exported ComfyUI commit {preferred_commit!r} is unavailable from {repository}. "
                    "Refusing to substitute a branch tip because that would not reproduce the source installation."
                )
        else:
            self.runner.run(
                ["git", "clone", "--branch", branch, "--single-branch", repository, str(self.target)]
            )

    def _prepare_venv(self) -> None:
        if self.python.exists():
            self.log(f"Reusing virtual environment: {self.venv}")
            return
        self.runner.run(
            [
                str(self.uv),
                "venv",
                "--no-config",
                "--python",
                self.options.python_version,
                str(self.venv),
            ],
            env=clean_package_environment(),
        )
        if not self.python.exists():
            raise InstallerError(f"uv did not create the expected Python executable: {self.python}")

    def _uv_pip(self, *args: str, check: bool = True) -> int:
        command = [str(self.uv), "pip", *args, "--python", str(self.python), "--no-config"]
        has_index = any(flag in args for flag in ("--default-index", "--index-url", "--no-index"))
        if args and args[0] in {"install", "download", "compile"} and not has_index:
            command.extend(["--default-index", PYPI_SIMPLE])
        return self.runner.run(
            command,
            check=check,
            env=clean_package_environment(),
        )

    def _torch_plan(self):
        try:
            return build_torch_install_plan(
                self.profile["torch"], self.platform_info, self.options.accelerator
            )
        except ValueError as exc:
            raise InstallerError(str(exc)) from exc

    def _torch_index(self) -> str:
        return self._torch_plan().index_url or ""

    def _install_torch(self) -> None:
        plan = self._torch_plan()
        self.log(f"Installing {plan.description} from the official PyTorch distribution source.")
        command = [
            str(self.uv), "pip", "install", "--python", str(self.python),
            "--no-config", "--reinstall", *plan.packages,
        ]
        if plan.index_url:
            command.extend(["--default-index", validate_package_index(plan.index_url)])
        else:
            command.extend(["--default-index", PYPI_SIMPLE])
        result = self.runner.run(command, check=False, env=clean_package_environment())
        if result != 0:
            if not self.profile["torch"].get("fallback_unpinned", True):
                raise InstallerError(
                    "The profile's exact official PyTorch build is unavailable for this platform."
                )
            warning = (
                "The profile's exact official PyTorch versions were unavailable. "
                "Installing the newest mutually compatible packages from the same official backend source."
            )
            self.warnings.append(warning)
            self.log(f"WARNING: {warning}")
            fallback_packages = ["torch", "torchvision"]
            if self.profile["torch"].get("torchaudio"):
                fallback_packages.append("torchaudio")
            fallback = [
                str(self.uv), "pip", "install", "--python", str(self.python),
                "--no-config", "--reinstall", *fallback_packages,
            ]
            fallback.extend([
                "--default-index",
                validate_package_index(plan.index_url) if plan.index_url else PYPI_SIMPLE,
            ])
            self.runner.run(fallback, env=clean_package_environment())

        verification = self.runner.capture(
            [str(self.python), "-c", verification_code(self.options.accelerator)]
        )
        if verification.returncode != 0:
            raise InstallerError(
                "PyTorch installed, but it does not contain the requested acceleration backend. "
                f"Verification output: {verification.stdout.strip()} {verification.stderr.strip()}"
            )
        self.log(f"Verified PyTorch backend: {verification.stdout.strip()}")

    def _constraint_file(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "constraints.txt"
        lines = [f"{name}=={version}" for name, version in sorted(self._constraint_versions().items())]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _constraint_versions(self) -> dict[str, str]:
        versions: dict[str, str] = {}
        for name, version in self.profile.get("constraints", {}).items():
            if isinstance(name, str) and isinstance(version, (str, int, float)):
                versions[normalized_name(name)] = str(version)
        resolution = self.profile.get("dependency_resolution", {})
        if isinstance(resolution, dict):
            resolved_versions = resolution.get("resolved_versions", {})
            if isinstance(resolved_versions, dict):
                for name, version in resolved_versions.items():
                    if isinstance(name, str) and isinstance(version, (str, int, float)):
                        versions[normalized_name(name)] = str(version)
        # The complete exported environment remains the highest-authority
        # record when both legacy constraints and manifest resolution exist.
        versions.update(lock_reproducible_version_map(self.profile.get("environment_lock")))
        torch = self.profile.get("torch", {})
        if isinstance(torch, dict):
            for name, key in (
                ("torch", "version"),
                ("torchvision", "torchvision"),
                ("torchaudio", "torchaudio"),
            ):
                value = str(torch.get(key) or "").strip()
                if value:
                    versions[name] = value.split("+", 1)[0]
        return versions

    def _captured_dependency_manifest(
        self,
        *,
        scope: str,
        relative_path: str,
        node_ids: tuple[str, ...] = (),
    ) -> Path | None:
        """Extract one verified manifest from a .comfyuisetup bundle on demand."""

        records = self.profile.get("dependency_manifests", [])
        bundle_value = self.profile.get("_bundle_path")
        if not isinstance(records, list) or not bundle_value:
            return None
        wanted_ids = {normalized_name(value) for value in node_ids if value}
        selected: dict[str, Any] | None = None
        for item in records:
            if not isinstance(item, dict) or str(item.get("scope") or "") != scope:
                continue
            if str(item.get("relative_path") or "") != relative_path:
                continue
            if scope == "custom-node":
                item_id = normalized_name(str(item.get("node_id") or ""))
                if wanted_ids and item_id not in wanted_ids:
                    continue
            selected = item
            break
        if selected is None:
            return None

        payload = str(selected.get("payload") or "")
        if not payload:
            return None
        bundle = Path(str(bundle_value)).expanduser().resolve()
        destination = self.state_dir / "captured-manifests" / Path(*PurePosixPath(payload).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle) as archive:
            try:
                content = archive.read(payload)
            except KeyError as exc:
                raise InstallerError(f"Captured dependency manifest is missing: {payload}") from exc
        expected = str(selected.get("sha256") or "")
        if expected and hashlib.sha256(content).hexdigest() != expected:
            raise InstallerError(f"Captured dependency manifest checksum failed: {payload}")
        destination.write_bytes(content)
        return destination

    def _write_reconciled_requirements(
        self,
        source: Path,
        destination: Path,
        *,
        label: str,
        filter_acceleration: bool = False,
    ) -> list[dict[str, str]]:
        text = source.read_text(encoding="utf-8", errors="ignore")
        for raw in text.splitlines():
            try:
                validate_requirement_line(raw)
            except PackageSourceError as exc:
                raise InstallerError(f"Unsafe package source in {source}: {exc}") from exc
        if filter_acceleration:
            managed = {item.replace("_", "-") for item in CONFLICT_PACKAGES}
            filtered_lines = []
            for raw in text.splitlines():
                name = _requirement_name(raw)
                filtered_lines.append(f"# Managed by installer: {raw}" if name in managed else raw)
            text = "\n".join(filtered_lines) + "\n"
        reconciled, overrides = reconcile_requirements_text(text, self._constraint_versions())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(reconciled, encoding="utf-8")
        for override in overrides:
            message = (
                f"Dependency manifest override ({label}): {override['requested']} -> "
                f"{override['replacement']} from the verified working source environment."
            )
            self.log(message)
            if message not in self.warnings:
                self.warnings.append(message)
        return overrides

    def _environment_lock_file(self) -> Path | None:
        requirements = lock_install_requirements(self.profile.get("environment_lock"))
        if not requirements:
            return None
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "environment-lock.txt"
        path.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        return path

    def _extract_environment_wheels(self) -> list[Path]:
        wheels = lock_embedded_wheels(self.profile.get("environment_lock"))
        if not wheels:
            return []
        bundle_value = self.profile.get("_bundle_path")
        if not bundle_value:
            raise InstallerError(
                "The profile requires embedded Python wheels, but the source .comfyuisetup bundle is unavailable."
            )
        bundle = Path(str(bundle_value)).expanduser().resolve()
        destination = self.state_dir / "environment-wheels"
        destination.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            for package in wheels:
                payload = str(package["payload"])
                if payload not in names:
                    raise InstallerError(f"Embedded wheel payload is missing for {package['name']}: {payload}")
                target = destination / PurePosixPath(payload).name
                with archive.open(payload) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                expected = str(package.get("sha256") or "")
                if expected:
                    digest_object = hashlib.sha256()
                    with target.open("rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            digest_object.update(chunk)
                    if digest_object.hexdigest() != expected:
                        target.unlink(missing_ok=True)
                        raise InstallerError(f"Embedded wheel checksum failed for {package['name']}.")
                extracted.append(target)
        return extracted

    def _environment_lock_compatibility_issues(self) -> list[str]:
        return environment_lock_compatibility_issues(
            self.profile.get("environment_lock", {}),
            os_name=self.platform_info.os_name,
            architecture=self.platform_info.architecture,
            python_version=self.options.python_version,
            accelerator=self.options.accelerator,
            cuda_version=self.platform_info.cuda_version,
            rocm_version=self.platform_info.rocm_version,
        )

    def _validate_exact_target(self) -> None:
        issues = self._environment_lock_compatibility_issues()
        if issues:
            raise InstallerError(
                "The exact source environment cannot be reproduced on the selected target:\n- "
                + "\n- ".join(issues)
            )

    def _install_environment_lock(self) -> None:
        lock = self.profile.get("environment_lock", {})
        lock_file = self._environment_lock_file()
        wheel_paths = self._extract_environment_wheels()
        self._validate_exact_target()
        if lock_file is None and not wheel_paths:
            self.log(
                "The exact environment lock contains only packages handled by dedicated PyTorch, "
                "acceleration, or source-tree lifecycles; their versions remain constrained and verified."
            )
            return
        command = [
            "install",
            "--index-strategy",
            "unsafe-best-match",
        ]
        command.extend(str(path) for path in wheel_paths)
        if lock_file is not None:
            command.extend(["-r", str(lock_file)])
        constraints = self._constraint_file()
        if constraints.read_text(encoding="utf-8").strip():
            command.extend(["-c", str(constraints)])
        self.log(
            f"Applying exact Python environment lock with {len(lock_install_requirements(lock))} index/Git distributions and {len(wheel_paths)} embedded wheels."
        )
        self._uv_pip(*command)

    def _install_core(self) -> None:
        constraints = self._constraint_file()
        captured_core = self._captured_dependency_manifest(
            scope="comfyui", relative_path="requirements.txt"
        )
        core_requirements = captured_core or (self.target / "requirements.txt")
        safe_requirements = self.state_dir / "requirements-comfyui.txt"
        self._write_reconciled_requirements(
            core_requirements,
            safe_requirements,
            label="ComfyUI requirements.txt",
        )
        if captured_core:
            self.log("Using the requirements.txt captured from the verified source installation.")
        command = [
            "install",
            "--index-strategy",
            "unsafe-best-match",
            "-r",
            str(safe_requirements),
        ]
        if constraints.read_text(encoding="utf-8").strip():
            command.extend(["-c", str(constraints)])
        self._uv_pip(*command)

        pinned = self._constraint_versions()
        if pinned.get("numpy"):
            self._uv_pip("install", "--reinstall", f"numpy=={pinned['numpy']}")
        if pinned.get("transformers"):
            self._uv_pip(
                "install",
                "--no-deps",
                f"transformers=={pinned['transformers']}",
            )

        captured_manager = self._captured_dependency_manifest(
            scope="comfyui", relative_path="manager_requirements.txt"
        )
        manager_requirements = captured_manager or (self.target / "manager_requirements.txt")
        if manager_requirements.is_file():
            safe_manager_requirements = self.state_dir / "requirements-comfyui-manager.txt"
            self._write_reconciled_requirements(
                manager_requirements,
                safe_manager_requirements,
                label="ComfyUI manager_requirements.txt",
            )
            manager_command = [
                "install",
                "--index-strategy",
                "unsafe-best-match",
                "-r",
                str(safe_manager_requirements),
            ]
            if constraints.read_text(encoding="utf-8").strip():
                manager_command.extend(["-c", str(constraints)])
            self._uv_pip(*manager_command)
            self.log("Installed ComfyUI-Manager requirements for the --enable-manager launcher.")

    def _install_extra_python_packages(self) -> None:
        packages = [
            value
            for value in self.profile.get("extra_python_packages", [])
            if isinstance(value, str) and value.strip()
        ]
        if not packages:
            self.log("No additional top-level Python packages are requested by this profile.")
            return
        command = [
            "install",
            "--index-strategy",
            "unsafe-best-match",
            *packages,
        ]
        constraints = self._constraint_file()
        if constraints.read_text(encoding="utf-8").strip():
            command.extend(["-c", str(constraints)])
        self._uv_pip(*command)

    def _install_manager_node(self, node: dict[str, Any], manager_id: str) -> Path | None:
        self.log(f"Installing {node['name']} through Comfy Registry/Manager: {manager_id}")
        custom_nodes = self.target / "custom_nodes"
        custom_nodes.mkdir(parents=True, exist_ok=True)
        before = {entry.resolve() for entry in custom_nodes.iterdir()}
        command = [
            str(self.uv),
            "tool",
            "run",
            "--no-config",
            "--default-index",
            PYPI_SIMPLE,
            "--from",
            "comfy-cli",
            "comfy",
            f"--workspace={self.target}",
            "node",
            "install",
            manager_id,
            "--no-deps",
        ]
        # Manager/Registry is an acquisition mechanism here, not a second
        # dependency resolver.  The exported lock and captured manifests are
        # authoritative; allowing Manager to compile a global dependency set can
        # upgrade torch/numpy or combine requirements from unrelated nodes.
        result = self.runner.run(command, check=False, env=clean_package_environment())
        if result != 0:
            return None

        source = node.get("source", {}) if isinstance(node.get("source"), dict) else {}
        exact_candidates = [
            custom_nodes / str(node["folder"]),
        ]
        install_folder = source.get("install_folder")
        if isinstance(install_folder, str) and install_folder.strip():
            exact_candidates.append(custom_nodes / install_folder.strip())
        for candidate in exact_candidates:
            if candidate.exists():
                return candidate

        after = [entry for entry in custom_nodes.iterdir() if entry.resolve() not in before]
        if len(after) == 1:
            self.log(
                f"Manager installed {node['name']} as {after[0].name}; preserving the Manager-selected folder name."
            )
            return after[0]

        def identity_forms(value: str) -> set[str]:
            compact = re.sub(r"[^a-z0-9]+", "", value.lower())
            forms = {compact} if compact else set()
            changed = True
            while changed:
                changed = False
                for item in list(forms):
                    values = [item]
                    for prefix in ("comfyui", "comfy"):
                        if item.startswith(prefix) and len(item) > len(prefix) + 2:
                            values.append(item[len(prefix):])
                    for suffix in ("comfyui", "customnodes", "customnode", "nodes", "node", "plugin"):
                        if item.endswith(suffix) and len(item) > len(suffix) + 2:
                            values.append(item[:-len(suffix)])
                    for value in values:
                        if value and value not in forms:
                            forms.add(value)
                            changed = True
            return forms

        target_values = {str(node.get("folder") or ""), manager_id, str(node.get("name") or "")}
        repository = source.get("repository")
        if isinstance(repository, str) and repository.strip():
            target_values.add(Path(repository.rstrip("/")).stem.removesuffix(".git"))
        target_forms: set[str] = set()
        for value in target_values:
            target_forms.update(identity_forms(value))

        matching: list[Path] = []
        for candidate in [*after, *custom_nodes.iterdir()]:
            if candidate.name.startswith("."):
                continue
            if identity_forms(candidate.name) & target_forms:
                if candidate not in matching:
                    matching.append(candidate)
        if len(matching) == 1:
            self.log(
                f"Manager installed {node['name']} as {matching[0].name}; preserving the Manager-selected folder name."
            )
            return matching[0]

        self.warnings.append(
            f"{node['name']}: Manager completed without an error, but no newly installed folder could be identified safely."
        )
        self.log(f"WARNING: {self.warnings[-1]}")
        return None

    def _clone_or_update_remote_node(self, node: dict[str, Any]) -> tuple[Path | None, bool]:
        source = node.get("source", {})
        repository = source.get("repository")
        manager_id = source.get("manager_id")
        ref = source.get("ref")
        profile_exact = self._requires_exact_source()
        pin_ref = bool(self.options.pin_exact_refs or source.get("exact") or profile_exact)

        # A validated repository is deterministic and does not run a second,
        # unconstrained dependency resolver.  Registry/Manager metadata remains
        # useful provenance, but Manager acquisition is reserved for entries
        # that genuinely have no repository URL.
        if not repository:
            if not manager_id:
                raise InstallerError(
                    f"{node['name']} has neither a Manager/Registry id nor a Git repository."
                )
            installed = self._install_manager_node(node, str(manager_id))
            if installed is None:
                raise InstallerError(
                    f"Comfy Registry/Manager could not acquire {node['name']} ({manager_id}) without changing dependencies."
                )
            # --no-deps deliberately leaves manifest reconciliation and any
            # required lifecycle script to this installer.
            return installed, False

        repository = validate_github_repository(str(repository))

        custom_nodes = self.target / "custom_nodes"
        custom_nodes.mkdir(parents=True, exist_ok=True)
        destination = custom_nodes / node["folder"]

        exact_ref_required = bool(ref and (source.get("exact") or profile_exact))
        if destination.exists():
            if not (destination / ".git").exists():
                if exact_ref_required:
                    raise InstallerError(
                        f"{node['name']}: an existing non-Git folder blocks installation of the exact exported node source: {destination}"
                    )
                self.log(f"Node folder already exists; preserving it: {destination}")
                return destination, False
            if exact_ref_required:
                dirty = self.runner.capture(["git", "status", "--porcelain"], cwd=destination)
                if dirty.stdout.strip():
                    raise InstallerError(
                        f"{node['name']}: the existing node checkout has local changes and cannot be moved to the exact exported ref."
                    )
                self.runner.run(["git", "fetch", "--tags", "origin"], cwd=destination)
            elif self.options.update_existing_nodes:
                self.runner.run(["git", "fetch", "--tags", "origin"], cwd=destination)
            else:
                self.log(f"Node folder already exists; preserving it: {destination}")
                return destination, False
        else:
            self.runner.run(
                ["git", "clone", "--filter=blob:none", str(repository), str(destination)]
            )

        if ref and pin_ref:
            result = self.runner.run(["git", "checkout", str(ref)], cwd=destination, check=False)
            if result != 0:
                if exact_ref_required:
                    raise InstallerError(
                        f"{node['name']}: exact exported ref {ref!r} is unavailable. "
                        "Refusing to substitute the repository default branch because that would not reproduce the source installation."
                    )
                self.warnings.append(
                    f"{node['name']}: ref {ref!r} was unavailable; repository default branch retained."
                )
                self.log(self.warnings[-1])
        if (destination / ".gitmodules").is_file():
            self.runner.run(["git", "submodule", "sync", "--recursive"], cwd=destination)
            self.runner.run(["git", "submodule", "update", "--init", "--recursive"], cwd=destination)
        return destination, False

    def _extract_embedded_node(self, node: dict[str, Any]) -> Path:
        source = node.get("source", {})
        bundle_value = self.profile.get("_bundle_path")
        if not bundle_value:
            raise InstallerError(
                f"{node['name']} is an embedded plugin, but the source .comfyuisetup bundle is unavailable."
            )
        bundle = Path(str(bundle_value)).expanduser().resolve()
        payload = str(source.get("payload", "")).rstrip("/")
        prefix = payload + "/"
        custom_nodes = self.target / "custom_nodes"
        custom_nodes.mkdir(parents=True, exist_ok=True)
        destination = custom_nodes / node["folder"]
        if destination.exists():
            self.log(f"Embedded node already exists; preserving it: {destination}")
            return destination

        try:
            with zipfile.ZipFile(bundle) as archive:
                members = [
                    name for name in archive.namelist()
                    if name.startswith(prefix) and not name.endswith("/")
                ]
                if not members:
                    raise InstallerError(
                        f"Embedded payload is missing for {node['name']}: {payload}"
                    )
                layout = source.get("layout", "directory")
                if layout == "file":
                    if len(members) != 1:
                        raise InstallerError(
                            f"Embedded single-file node {node['name']} contains {len(members)} files."
                        )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(members[0]))
                    return destination

                destination.mkdir(parents=True, exist_ok=True)
                for member_name in members:
                    relative = PurePosixPath(member_name[len(prefix):])
                    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                        raise InstallerError(
                            f"Unsafe embedded member for {node['name']}: {member_name}"
                        )
                    output = destination.joinpath(*relative.parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(archive.read(member_name))
        except zipfile.BadZipFile as exc:
            raise InstallerError(f"Invalid .comfyuisetup bundle: {bundle}") from exc
        self.log(f"Installed unpublished embedded plugin: {node['name']}")
        return destination

    def _acquire_node(self, node: dict[str, Any]) -> tuple[Path | None, bool]:
        source = node.get("source", {})
        source_type = source.get("type")
        if source_type == "embedded":
            return self._extract_embedded_node(node), False
        if source_type == "snapshot":
            self.log(f"Installing exact source snapshot for {node['name']}.")
            return self._extract_embedded_node(node), False
        if source_type == "remote":
            return self._clone_or_update_remote_node(node)
        raise InstallerError(
            f"Unsupported source type for {node['name']}: {source_type!r}"
        )

    def _node_build_environment(self) -> dict[str, str]:
        env = clean_package_environment()
        env["MAX_JOBS"] = os.environ.get(
            "MAX_JOBS", str(max(1, min(8, (os.cpu_count() or 2) // 2)))
        )
        if self.platform_info.compute_capability:
            env["TORCH_CUDA_ARCH_LIST"] = f"{self.platform_info.compute_capability:.1f}"
        detected_cuda = cuda_home()
        if detected_cuda:
            env["CUDA_HOME"] = str(detected_cuda)
            env["CUDA_PATH"] = str(detected_cuda)
            env["PATH"] = str(detected_cuda / "bin") + os.pathsep + env.get("PATH", "")
            if self.platform_info.os_name != "windows":
                env["LD_LIBRARY_PATH"] = (
                    str(detected_cuda / "lib64")
                    + os.pathsep
                    + env.get("LD_LIBRARY_PATH", "")
                )
        if self.platform_info.os_name == "windows":
            env.update(windows_msvc_environment())
        return env

    def _target_torch_requirements(self) -> list[str]:
        code = (
            "import importlib.util,importlib.metadata as m;"
            "names=('torch','torchvision','torchaudio');"
            "print('\\n'.join(f'{n}=={m.version(n)}' for n in names "
            "if importlib.util.find_spec(n) is not None))"
        )
        completed = self.runner.capture([str(self.python), "-c", code])
        lines = [line.strip() for line in completed.stdout.splitlines() if "==" in line]
        if lines:
            return lines
        torch = self.profile["torch"]
        return [
            f"{name}=={version}"
            for name, version in (
                ("torch", torch.get("version")),
                ("torchvision", torch.get("torchvision")),
                ("torchaudio", torch.get("torchaudio")),
            )
            if version
        ]

    def _fallback_build_requirements(self, requirements: Path, node_id: str) -> None:
        if not self.options.allow_source_builds:
            raise InstallerError(
                f"{requirements} could not be installed and source builds are disabled."
            )

        self.log(f"Creating an isolated source-build environment for {node_id}.")
        build_root = self.target / ".installer-build" / "node-dependencies" / node_id
        build_venv = build_root / "venv"
        build_python = executable_for_venv(build_venv, "python")
        if not build_python.exists():
            build_root.mkdir(parents=True, exist_ok=True)
            self.runner.run(
                [str(self.uv), "venv", "--no-config", "--python", str(self.python), str(build_venv)],
                env=clean_package_environment(),
            )

        self.runner.run(
            [
                str(self.uv), "pip", "install", "--python", str(build_python),
                "--no-config", "--default-index", PYPI_SIMPLE,
                "pip", "build", "packaging", "setuptools", "wheel", "ninja", "cmake",
            ],
            env=clean_package_environment(),
        )
        torch_packages = self._target_torch_requirements()
        torch_command = [
            str(self.uv), "pip", "install", "--python", str(build_python),
            "--no-config", "--reinstall", *torch_packages,
            "--default-index", validate_package_index(self._torch_index()) if self._torch_index() else PYPI_SIMPLE,
        ]
        self.runner.run(torch_command, env=clean_package_environment())

        wheelhouse = self.target / ".installer-wheelhouse" / "node-dependencies" / node_id
        shutil.rmtree(wheelhouse, ignore_errors=True)
        wheelhouse.mkdir(parents=True, exist_ok=True)
        constraints = self._constraint_file()
        self.runner.run(
            [
                str(build_python), "-m", "pip", "wheel",
                "--wheel-dir", str(wheelhouse),
                "-r", str(requirements),
                "-c", str(constraints),
            ],
            env=self._node_build_environment(),
        )
        self._uv_pip(
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "-r",
            str(requirements),
            "-c",
            str(constraints),
        )
        if self.options.backup_builds and self.options.backup_dir:
            destination = self.options.backup_dir.expanduser().resolve() / "node-dependencies" / node_id
            destination.mkdir(parents=True, exist_ok=True)
            copied = []
            for wheel in wheelhouse.glob("*.whl"):
                shutil.copy2(wheel, destination / wheel.name)
                copied.append(wheel.name)
            metadata = {
                "node_id": node_id,
                "source_requirements": requirements.name,
                "wheels": sorted(copied),
                "python": self.options.python_version,
                "accelerator": self.options.accelerator,
                "cuda": self.platform_info.cuda_version,
                "compute_capability": self.platform_info.compute_capability,
            }
            (destination / "build-metadata.yaml").write_text(
                yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True, width=100),
                encoding="utf-8",
            )
            self.log(f"Backed up node dependency wheels to {destination}")

    def _has_complete_exact_environment_lock(self) -> bool:
        lock = self.profile.get("environment_lock", {})
        return bool(
            isinstance(lock, dict)
            and lock.get("mode") == "exact"
            and lock.get("complete") is not False
            and lock.get("packages")
        )

    @staticmethod
    def _dependency_only_install_script(script: Path) -> bool:
        try:
            text = script.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            return False
        dependency_markers = (
            "pip install", "pip uninstall", "uv pip", "-m pip", "pip_main(",
            "subprocess.call([sys.executable", "subprocess.run([sys.executable",
            "check_call([sys.executable", "install_requirements",
        )
        side_effect_markers = (
            "urlretrieve", "requests.get", "httpx.get", "git clone", "download_file",
            "shutil.copy", "shutil.move", "write_text(", "write_bytes(",
            "git apply", "patch ", "cmake", "ninja",
        )
        return any(marker in text for marker in dependency_markers) and not any(
            marker in text for marker in side_effect_markers
        )

    def _install_py_policy(self, node: dict[str, Any], node_dir: Path) -> str:
        configured = str(node.get("install_py_policy") or "").strip().lower()
        if configured in {"none", "dependencies-only", "required"}:
            return configured
        if any(node_dir.rglob("comfy-env.toml")):
            return "required"
        script = node_dir / "install.py"
        if script.is_file() and self._dependency_only_install_script(script):
            return "dependencies-only"
        return "required" if script.is_file() else "none"

    def _install_node_requirements(self, node: dict[str, Any], node_dir: Path) -> None:
        requirement_name = node.get("requirements")
        if not requirement_name:
            return
        captured = self._captured_dependency_manifest(
            scope="custom-node",
            relative_path=str(requirement_name),
            node_ids=(
                str(node.get("id") or ""),
                str(node.get("folder") or ""),
                str(node.get("name") or ""),
                node_dir.name,
            ),
        )
        source = captured or (node_dir / requirement_name)
        if not source.exists():
            self.warnings.append(
                f"{node['name']}: requirements file was not found: {source.name}"
            )
            self.log(f"WARNING: {self.warnings[-1]}")
            return

        self.state_dir.mkdir(parents=True, exist_ok=True)
        filtered = self.state_dir / f"requirements-{node['id']}.txt"
        self._write_reconciled_requirements(
            source,
            filtered,
            label=f"custom node {node['name']} / {requirement_name}",
            filter_acceleration=True,
        )
        if captured:
            self.log(f"Using captured dependency manifest for {node['name']}: {requirement_name}")
        if self._has_complete_exact_environment_lock():
            self.log(
                f"Audited and reconciled {node['name']} requirements; the complete exact environment lock already supplies these distributions, so they will not be installed a second time."
            )
            return
        constraints = self._constraint_file()

        result = self._uv_pip(
            "install",
            "--index-strategy",
            "unsafe-best-match",
            "-r",
            str(filtered),
            "-c",
            str(constraints),
            check=False,
        )
        if result != 0:
            self._fallback_build_requirements(filtered, node["id"])

    def _install_node(self, node: dict[str, Any]) -> None:
        node_dir, lifecycle_handled = self._acquire_node(node)
        if lifecycle_handled or node_dir is None:
            return
        self._install_node_requirements(node, node_dir)
        script = node_dir / "install.py"
        if node.get("run_install_py") and node_dir.is_dir() and script.exists():
            policy = self._install_py_policy(node, node_dir)
            if policy == "dependencies-only" and self._has_complete_exact_environment_lock():
                self.log(
                    f"Skipping dependency-only install.py for {node['name']}; its verified package set is already supplied by the exact environment lock."
                )
                return
            if policy == "none":
                return
            self.log(f"Running required custom-node lifecycle script for {node['name']}.")
            lifecycle_env = self._node_build_environment()
            # Required lifecycle scripts may perform setup beyond dependencies.
            # When they also invoke pip, preserve the verified package solution
            # unless the node deliberately manages an isolated comfy-env/pixi
            # environment of its own.
            if self._has_complete_exact_environment_lock() and not any(node_dir.rglob("comfy-env.toml")):
                constraints = self._constraint_file()
                if constraints.is_file() and constraints.read_text(encoding="utf-8").strip():
                    lifecycle_env["PIP_CONSTRAINT"] = str(constraints)
            self.runner.run(
                [str(self.python), "install.py"],
                cwd=node_dir,
                env=clean_package_environment(lifecycle_env),
            )

    def _install_locked_source_packages(self) -> None:
        packages = lock_source_tree_packages(self.profile.get("environment_lock"))
        if not packages:
            return
        self.log(
            f"Installing {len(packages)} Python distribution(s) from the exact exported ComfyUI/custom-node source trees."
        )
        for package in packages:
            relative = PurePosixPath(str(package["source_path"]))
            source_path = (self.target / Path(*relative.parts)).resolve()
            try:
                source_path.relative_to(self.target)
            except ValueError as exc:
                raise InstallerError(
                    f"Locked source path escapes the managed ComfyUI checkout: {package['source_path']}"
                ) from exc
            if not source_path.exists():
                raise InstallerError(
                    f"Locked source-tree package {package['name']} expects {source_path}, but that source was not installed."
                )
            command = ["install", "--no-deps", "--reinstall"]
            if package.get("editable"):
                command.extend(["--editable", str(source_path)])
            else:
                command.append(str(source_path))
            self._uv_pip(*command)

    def _reapply_profile_constraints(self) -> None:
        lock_file = self._environment_lock_file()
        wheel_paths = self._extract_environment_wheels()
        pins = [f"{name}=={version}" for name, version in sorted(self._constraint_versions().items())]
        if not pins and lock_file is None and not wheel_paths:
            return
        self.log("Reapplying the exported environment lock after node and acceleration packages.")
        command = [
            "install",
            "--index-strategy",
            "unsafe-best-match",
        ]
        command.extend(str(path) for path in wheel_paths)
        if lock_file is not None:
            command.extend(["-r", str(lock_file)])
        elif not wheel_paths:
            command.extend(pins)
        constraints = self._constraint_file()
        if constraints.read_text(encoding="utf-8").strip():
            command.extend(["-c", str(constraints)])
        self._uv_pip(*command)

    def _repair_dependency_issues(self) -> list[dict[str, str]]:
        constraints = self._constraint_versions()
        for attempt in range(1, 4):
            try:
                issues = inspect_dependency_issues(self.python, self.runner)
            except RuntimeError as exc:
                raise InstallerError(str(exc)) from exc
            if not issues:
                self.log("Verified installed package metadata: no missing or incompatible dependencies.")
                return []

            requested: list[str] = []
            for issue in issues:
                name = issue.get("name", "").strip()
                if not name:
                    continue
                pinned = constraints.get(normalized_name(name))
                specification = f"{name}=={pinned}" if pinned else issue.get("requirement", name)
                if specification not in requested:
                    requested.append(specification)
                self.log(
                    f"Dependency repair needed: {issue.get('owner')} requires {issue.get('requirement')} "
                    f"(installed: {issue.get('installed') or 'missing'})."
                )

            if not requested:
                return issues
            command = [
                "install",
                "--index-strategy",
                "unsafe-best-match",
                *requested,
            ]
            constraints_file = self._constraint_file()
            if constraints_file.read_text(encoding="utf-8").strip():
                command.extend(["-c", str(constraints_file)])
            self.log(f"Automatic dependency repair pass {attempt}: {', '.join(requested)}")
            self._uv_pip(*command)
            self._reapply_profile_constraints()

        try:
            return inspect_dependency_issues(self.python, self.runner)
        except RuntimeError as exc:
            raise InstallerError(str(exc)) from exc

    def _repair_declared_missing_modules(self, output: str) -> bool:
        repairs = self.profile.get("dependency_repairs", {})
        if not isinstance(repairs, dict):
            return False
        packages: list[str] = []
        for module in missing_module_names(output):
            top_level = module.split(".", 1)[0]
            specification = repairs.get(module) or repairs.get(top_level)
            if isinstance(specification, str) and specification.strip():
                packages.append(specification.strip())
        packages = list(dict.fromkeys(packages))
        if not packages:
            return False

        command = ["install", "--index-strategy", "unsafe-best-match", *packages]
        constraints_file = self._constraint_file()
        if constraints_file.read_text(encoding="utf-8").strip():
            command.extend(["-c", str(constraints_file)])
        self.log("Repairing profile-declared missing modules: " + ", ".join(packages))
        self._uv_pip(*command)
        return True

    def _validate_comfyui_startup(self) -> dict[str, Any]:
        command = [
            str(self.python),
            str(self.target / "main.py"),
            "--quick-test-for-ci",
            "--dont-print-server",
        ]
        completed = self.runner.capture(command, cwd=self.target)
        output = completed.stdout
        failures = comfyui_startup_failures(output)

        if (completed.returncode != 0 or failures) and self._repair_declared_missing_modules(output):
            self._reapply_profile_constraints()
            completed = self.runner.capture(command, cwd=self.target)
            output = completed.stdout
            failures = comfyui_startup_failures(output)

        if completed.returncode != 0:
            raise InstallerError(
                "ComfyUI quick validation exited with an error. Last output:\n" + output[-4000:]
            )
        if failures:
            raise InstallerError(
                "One or more selected custom nodes still failed to import after dependency repair:\n- "
                + "\n- ".join(failures[-20:])
            )
        self.log("✓ ComfyUI quick test loaded the selected custom nodes without import failures.")
        return {"ok": True, "returncode": completed.returncode, "failures": []}

    def _reconcile_dependencies(self) -> None:
        self._reapply_profile_constraints()
        unresolved = self._repair_dependency_issues()
        if unresolved:
            details = "\n".join(
                f"- {item.get('owner')} requires {item.get('requirement')} "
                f"(installed: {item.get('installed') or 'missing'})"
                for item in unresolved[:30]
            )
            raise InstallerError(
                "Automatic dependency reconciliation could not produce a consistent environment:\n" + details
            )
        self._verify_environment_lock()

    def _verify_environment_lock(self) -> None:
        expected = lock_reproducible_version_map(self.profile.get("environment_lock"))
        if not expected:
            return
        # Accelerated providers in these families install the same import
        # package and cannot safely coexist. A long-lived source environment
        # may retain stale sibling metadata, so verify the provider selected
        # for reconstruction rather than requiring overlapping wheels.
        acceleration_families = {
            "onnxruntime": {"onnxruntime", "onnxruntime-gpu"},
            "cupy": {"cupy", "cupy-wheel", "cupy-cuda11x", "cupy-cuda12x", "cupy-cuda13x"},
        }
        for item in self.selected_acceleration():
            family = acceleration_families.get(normalized_name(str(item.get("id") or "")))
            if not family:
                continue
            provider = normalized_name(str(item.get("package_name") or ""))
            version = str(item.get("version") or "").strip()
            if provider and version:
                for sibling in family:
                    expected.pop(sibling, None)
                expected[provider] = version
        # Query every installed distribution, not only the expected names. A
        # node requirement or post-startup repair may otherwise add packages
        # that were absent from the working source environment while all
        # expected versions still appear correct. Exact means the final audited
        # distribution set must match as well as the individual versions.
        code = (
            "import importlib.metadata as m,json;"
            "out=[];"
            "\nfor dist in m.distributions():\n"
            "  name=(dist.metadata.get('Name') or getattr(dist,'name','') or '').strip()\n"
            "  if name: out.append([name,str(dist.version)])\n"
            "print(json.dumps(out))"
        )
        completed = self.runner.capture([str(self.python), "-c", code])
        try:
            rows = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise InstallerError("Could not verify the exported Python environment lock.") from exc
        if not isinstance(rows, list):
            raise InstallerError("Could not verify the exported Python environment lock.")

        installed: dict[str, str] = {}
        duplicate_names: set[str] = set()
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                continue
            name = normalized_name(str(row[0]))
            version = str(row[1])
            if not name or managed_package_reason(name) == "installer-tool":
                continue
            if name in installed:
                duplicate_names.add(name)
            installed[name] = version

        mismatches = [
            f"{name}: expected {version}, installed {installed.get(name) or 'missing'}"
            for name, version in expected.items()
            if installed.get(name) != version
        ]
        unexpected = [
            f"{name}=={version}: not present in the exported working environment"
            for name, version in sorted(installed.items())
            if name not in expected
        ]
        duplicates = [
            f"{name}: multiple installed distributions use this canonical name"
            for name in sorted(duplicate_names)
        ]
        differences = [*mismatches, *unexpected, *duplicates]
        if differences:
            raise InstallerError(
                "The installed Python environment does not exactly match the exported working environment:\n- "
                + "\n- ".join(differences[:75])
            )
        self.log(
            f"✓ Verified the exact set and versions of {len(expected)} locked Python distributions."
        )

    def _copy_websocket_example(self) -> None:
        if not self.profile.get("post_install", {}).get("copy_websocket_example"):
            return
        custom_nodes = self.target / "custom_nodes"
        destination = custom_nodes / "websocket_image_save.py"
        if destination.exists():
            return
        candidates = [
            custom_nodes / "websocket_image_save.py.example",
            self.target / "script_examples" / "websockets_api_example_ws_images.py",
        ]
        for candidate in candidates:
            if candidate.exists():
                shutil.copy2(candidate, destination)
                self.log(f"Enabled the included WebSocket image-save example: {destination.name}")
                return

    def _generate_launchers(self) -> Path:
        launcher = write_local_launchers(self.target)
        self.log(f"Created local launcher: {launcher}")
        self.log("No global PATH command or shell profile was modified.")
        return launcher

    def _validate(self) -> dict[str, Any]:
        imports = list(self.profile.get("post_install", {}).get("validate_imports", []))
        for item in self.selected_acceleration():
            if item.get("import_name"):
                imports.append(item["import_name"])
        imports = list(dict.fromkeys(imports))

        code = (
            "import importlib,json;"
            f"mods={imports!r};"
            "result={};"
            "\nfor name in mods:\n"
            "  try:\n"
            "    mod=importlib.import_module(name); result[name]={'ok':True,'file':getattr(mod,'__file__','')}\n"
            "  except Exception as exc:\n"
            "    result[name]={'ok':False,'error':repr(exc)}\n"
            "print(json.dumps(result))"
        )
        completed = self.runner.capture([str(self.python), "-c", code])
        try:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
        except Exception:
            result = {"validation": {"ok": False, "error": completed.stdout[-2000:]}}
        import_failures: list[str] = []
        for name, status in result.items():
            if status.get("ok"):
                self.log(f"✓ import {name}")
            else:
                message = f"Import validation failed for {name}: {status.get('error')}"
                import_failures.append(message)
                self.log(f"ERROR: {message}")
        if import_failures:
            raise InstallerError("Required profile imports failed:\n- " + "\n- ".join(import_failures))
        result["comfyui_startup"] = self._validate_comfyui_startup()
        # Startup validation may perform a declared missing-module repair. Run
        # the complete exact audit again afterward so no repair or node import
        # can leave the environment with an unrecorded package or version.
        self._verify_environment_lock()
        return result

    def _write_report(self, validation: dict[str, Any]) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        environment_lock = self.profile.get("environment_lock", {})
        protected_packages = sorted({
            str(item.get("name"))
            for item in environment_lock.get("packages", [])
            if isinstance(environment_lock, dict)
            and isinstance(item, dict)
            and item.get("name")
        }) if isinstance(environment_lock, dict) else []
        report = {
            "profile": {
                "id": self.profile.get("id"),
                "name": self.profile.get("name"),
                "display_name": self.profile.get("display_name", self.profile.get("name")),
                "description": self.profile.get("description", ""),
                "version": self.profile.get("version"),
                "compatibility": compatibility_from_profile(self.profile),
                "protected_packages": protected_packages,
            },
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "platform": {
                "os": self.platform_info.os_name,
                "display": self.platform_info.display_os,
                "architecture": self.platform_info.architecture,
                "wsl": self.platform_info.is_wsl,
                "accelerator": self.options.accelerator,
                "gpu": self.platform_info.gpu_name,
                "cuda": self.platform_info.cuda_version,
                "compute_capability": self.platform_info.compute_capability,
            },
            "options": self.options.to_json(),
            "completed_steps": self.completed,
            "warnings": self.warnings,
            "validation": validation,
        }
        path = self.state_dir / "install-report.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return path


    def _configure_shared_assets(self) -> dict[str, Any] | None:
        if not self.options.configure_shared_assets:
            self.log("Shared external model/workflow libraries were disabled for this installation.")
            return None
        defaults = load_shared_asset_paths()
        paths = SharedAssetPaths(
            models=(self.options.shared_models_dir or defaults.models).expanduser().resolve(),
            workflows=(self.options.shared_workflows_dir or defaults.workflows).expanduser().resolve(),
            migrate_existing=self.options.migrate_existing_assets or defaults.migrate_existing,
            conflict_policy=defaults.conflict_policy,
        )
        self.log(f"Shared models: {paths.models}")
        self.log(f"Shared workflows: {paths.workflows}")
        result = configure_instance_shared_assets(
            self.target,
            paths,
            migrate_existing=paths.migrate_existing,
        )
        profile_sources = self.profile.get("asset_sources", {})
        for kind, entries_key in (("models", "models"), ("workflows", "workflows")):
            kind_sources = profile_sources.get(kind, {}) if isinstance(profile_sources, dict) else {}
            for source in kind_sources.get("sources", []) if isinstance(kind_sources, dict) else []:
                if isinstance(source, dict) and source.get("id"):
                    add_source(kind, source)
            for entry in self.profile.get(entries_key, []):
                if not isinstance(entry, dict) or not entry.get("id") or not entry.get("selected", entry.get("required", False)):
                    continue
                if entry.get("url"):
                    try:
                        add_entry(kind, entry)
                        download_entry(kind, str(entry["id"]), paths=paths)
                    except Exception as exc:
                        if entry.get("required", False):
                            raise InstallerError(f"Required {kind[:-1]} {entry.get('name', entry['id'])} could not be installed: {exc}") from exc
                        self.warnings.append(f"Suggested {kind[:-1]} {entry.get('name', entry['id'])} was not installed: {exc}")
        return result

    def install(self) -> InstallResult:
        steps = self.plan()
        total = len(steps)
        current = 0
        try:
            self.log(f"Installing profile: {self.profile['name']}")
            self.log(f"Detected: {self.platform_info.display_os} / {self.options.accelerator}")
            self._validate_exact_target()
            self._install_system_dependencies()
            current += 1
            self._mark("system", "System prerequisites", current, total)

            self._prepare_repository()
            self._apply_comfyui_overlay()
            current += 1
            self._mark("repo", "ComfyUI checkout", current, total)

            self._prepare_venv()
            current += 1
            self._mark("venv", "Python environment", current, total)

            self._install_torch()
            current += 1
            self._mark("torch", "PyTorch", current, total)

            self._install_core()
            current += 1
            self._mark("core", "ComfyUI dependencies", current, total)

            if lock_has_packages(self.profile.get("environment_lock")):
                self._install_environment_lock()
                current += 1
                self._mark("environment-lock", "Exact Python environment", current, total)

            self._install_extra_python_packages()
            current += 1
            self._mark("extra", "Profile Python packages", current, total)

            for node in self.selected_nodes():
                self._install_node(node)
                current += 1
                self._mark(f"node:{node['id']}", node["name"], current, total)

            if lock_source_tree_packages(self.profile.get("environment_lock")):
                self._install_locked_source_packages()
                current += 1
                self._mark("source-packages", "Locked source-tree packages", current, total)

            wheel_manager = WheelManager(
                runner=self.runner,
                uv=self.uv,
                target_python=self.python,
                target_dir=self.target,
                profile=self.profile,
                platform_info=self.platform_info,
                options=self.options,
                decision_provider=self.wheel_decision_provider,
            )
            for item in self.selected_acceleration():
                wheel_manager.install_item(item)
                current += 1
                self._mark(f"accel:{item['id']}", item["name"], current, total)

            self._reconcile_dependencies()
            current += 1
            self._mark("reconcile", "Dependency reconciliation", current, total)

            self._copy_websocket_example()
            self._configure_shared_assets()
            current += 1
            self._mark("assets", "Shared models and workflows", current, total)

            launcher_path = self._generate_launchers()
            current += 1
            self._mark("launchers", "Launchers", current, total)

            validation = self._validate()
            report_path = self._write_report(validation)
            current += 1
            self._mark("validate", "Validation", current, total)

            return InstallResult(
                success=True,
                target_dir=self.target,
                completed_steps=self.completed,
                warnings=self.warnings,
                report_path=report_path,
                launcher_path=launcher_path,
            )
        except Exception as exc:
            self.log(f"ERROR: {exc}")
            failed = steps[min(current, len(steps) - 1)].key if steps else None
            return InstallResult(
                success=False,
                target_dir=self.target,
                completed_steps=self.completed,
                warnings=self.warnings,
                failed_step=failed,
                error=str(exc),
            )
