from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from .models import InstallOptions, PlatformInfo
from .platforms import cuda_home, windows_msvc_environment
from .pytorch_install import build_torch_install_plan
from .package_sources import (
    PYPI_SIMPLE,
    clean_package_environment,
    validate_github_repository,
    validate_package_index,
)
from .runner import Runner, executable_for_venv
from .wheel_sources import (
    WheelSourceRegistry,
    download_candidate,
    open_source_file,
    select_candidates,
    target_environment,
)


class WheelInstallError(RuntimeError):
    pass


WheelDecisionProvider = Callable[[dict[str, Any], str, Path], str]


def module_available(python_executable: Path, module_name: str, runner: Runner) -> bool:
    code = (
        "import importlib.util,sys;"
        f"sys.exit(0 if importlib.util.find_spec({module_name!r}) else 1)"
    )
    completed = runner.capture([str(python_executable), "-c", code])
    return completed.returncode == 0


def _safe_package_name(specification: str) -> str:
    value = specification.split("@", 1)[0]
    for token in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        value = value.split(token, 1)[0]
    return value.strip().replace("_", "-").lower()


class WheelManager:
    def __init__(
        self,
        *,
        runner: Runner,
        uv: Path,
        target_python: Path,
        target_dir: Path,
        profile: dict[str, Any],
        platform_info: PlatformInfo,
        options: InstallOptions,
        decision_provider: WheelDecisionProvider | None = None,
    ) -> None:
        self.runner = runner
        self.uv = uv
        self.target_python = target_python
        self.target_dir = target_dir
        self.profile = profile
        self.platform_info = platform_info
        self.options = options
        self.decision_provider = decision_provider
        self.source_registry = WheelSourceRegistry()
        self.build_root = target_dir / ".installer-build"
        self.local_wheelhouse = target_dir / ".installer-wheelhouse"
        self.build_root.mkdir(parents=True, exist_ok=True)
        self.local_wheelhouse.mkdir(parents=True, exist_ok=True)

    def _uv_pip(self, *args: str, check: bool = True) -> int:
        command = [
            str(self.uv),
            "pip",
            *args,
            "--python",
            str(self.target_python),
            "--no-config",
        ]
        has_index = any(flag in args for flag in ("--default-index", "--index-url", "--no-index"))
        if args and args[0] in {"install", "download", "compile"} and not has_index:
            command.extend(["--default-index", PYPI_SIMPLE])
        return self.runner.run(
            command,
            check=check,
            env=clean_package_environment(),
        )

    def _build_environment(self, package_id: str) -> tuple[Path, Path]:
        build_dir = self.build_root / package_id
        build_venv = build_dir / "venv"
        source_dir = build_dir / "source"
        build_dir.mkdir(parents=True, exist_ok=True)

        if not executable_for_venv(build_venv, "python").exists():
            self.runner.run(
                [
                    str(self.uv),
                    "venv",
                    "--no-config",
                    "--python",
                    str(self.target_python),
                    str(build_venv),
                ],
                env=clean_package_environment(),
            )
        return build_venv, source_dir

    def _install_build_torch(self, build_python: Path) -> None:
        # Match the target runtime from the official PyTorch distribution source so
        # compiled extensions use the same ABI and acceleration backend.
        probe = (
            "import importlib.util,importlib.metadata as m;"
            "names=('torch','torchvision','torchaudio');"
            "print('\\n'.join(f'{name}=={m.version(name)}' for name in names "
            "if importlib.util.find_spec(name) is not None))"
        )
        completed = self.runner.capture([str(self.target_python), "-c", probe])
        packages = [line.strip() for line in completed.stdout.splitlines() if "==" in line]
        plan = build_torch_install_plan(
            self.profile["torch"], self.platform_info, self.options.accelerator
        )
        if not packages:
            packages = plan.packages
        command = [
            str(self.uv), "pip", "install", "--python", str(build_python),
            "--no-config", "--reinstall", *packages,
            "--default-index", validate_package_index(plan.index_url) if plan.index_url else PYPI_SIMPLE,
        ]
        self.runner.run(command, env=clean_package_environment())

    def _build_env_vars(self) -> dict[str, str]:
        env: dict[str, str] = clean_package_environment({
            "MAX_JOBS": os.environ.get("MAX_JOBS", str(max(1, min(8, (os.cpu_count() or 2) // 2)))),
        })
        if self.platform_info.compute_capability:
            capability = f"{self.platform_info.compute_capability:.1f}"
            env["TORCH_CUDA_ARCH_LIST"] = capability
        if self.platform_info.os_name == "windows":
            env.update(windows_msvc_environment())
        detected_cuda = cuda_home()
        if detected_cuda:
            env["CUDA_HOME"] = str(detected_cuda)
            env["CUDA_PATH"] = str(detected_cuda)
            env["PATH"] = str(detected_cuda / "bin") + os.pathsep + os.environ.get("PATH", "")
            if self.platform_info.os_name != "windows":
                env["LD_LIBRARY_PATH"] = (
                    str(detected_cuda / "lib64")
                    + os.pathsep
                    + os.environ.get("LD_LIBRARY_PATH", "")
                )
        return env

    def _backup(self, wheels: list[Path], package_id: str, origin: str) -> None:
        if not self.options.backup_builds or not self.options.backup_dir:
            return
        backup_root = self.options.backup_dir.expanduser().resolve()
        destination = backup_root / package_id
        destination.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for wheel in wheels:
            target = destination / wheel.name
            shutil.copy2(wheel, target)
            copied.append(target.name)

        metadata = {
            "package_id": package_id,
            "origin": origin,
            "wheels": copied,
            "system": platform.system(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python": self.runner.capture(
                [str(self.target_python), "-c", "import sys;print(sys.version)"]
            ).stdout.strip(),
            "accelerator": self.options.accelerator,
            "gpu": self.platform_info.gpu_name,
            "cuda": self.platform_info.cuda_version,
            "compute_capability": self.platform_info.compute_capability,
            "profile": self.profile.get("id"),
        }
        (destination / "build-metadata.yaml").write_text(
            yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        self.source_registry.register_local_backup(package_id, destination, self.platform_info)
        self.runner.log(f"Backed up {len(copied)} wheel(s) to {destination}")
        self.runner.log(f"Registered the backup as a Custom/Local wheel source in {self.source_registry.path}")

    def _download_prebuilt(
        self,
        package: str,
        package_id: str,
        *,
        index_url: str = PYPI_SIMPLE,
        origin: str = "Official PyPI",
    ) -> list[Path]:
        index_url = validate_package_index(index_url)
        download_dir = self.local_wheelhouse / package_id / "prebuilt"
        shutil.rmtree(download_dir, ignore_errors=True)
        download_dir.mkdir(parents=True, exist_ok=True)

        # Ask pip to resolve exact wheel tags for the target interpreter. This
        # phase is binary-only and is never allowed to compile a source archive.
        self._uv_pip("install", "pip")
        command = [
            str(self.target_python),
            "-m",
            "pip",
            "download",
            "--index-url",
            index_url,
        ]
        if index_url != PYPI_SIMPLE:
            command.extend(["--extra-index-url", PYPI_SIMPLE])
        command.extend(
            [
                "--only-binary=:all:",
                "--no-deps",
                "--dest",
                str(download_dir),
                package,
            ]
        )
        self.runner.run(command, env=clean_package_environment())
        wheels = sorted(download_dir.glob("*.whl"))
        if not wheels:
            raise WheelInstallError(f"No compatible binary wheel was downloaded for {package} from {origin}.")

        for wheel in wheels:
            self._uv_pip("install", "--upgrade", "--no-deps", str(wheel))
        self._backup(wheels, package_id, f"prebuilt: {origin}")
        return wheels

    def _constraint_args(self) -> list[str]:
        constraints = self.target_dir / ".comfy-setup" / "constraints.txt"
        if constraints.is_file() and constraints.read_text(encoding="utf-8").strip():
            return ["-c", str(constraints)]
        return []

    def _configured_package_indexes(self, package_id: str) -> list[tuple[str, str]]:
        indexes: list[tuple[str, str]] = []
        for source in self.source_registry.matching(package_id, self.platform_info):
            if source.kind != "package-index":
                continue
            indexes.append((source.label, validate_package_index(source.location)))
        return indexes

    def _install_package_from_indexes(
        self,
        package: str,
        package_id: str,
        *,
        exact_fallback: str | None = None,
    ) -> None:
        """Install a normal binary package from curated indexes without compiling."""
        attempts = self._configured_package_indexes(package_id)
        if not attempts:
            attempts = [("Official PyPI", PYPI_SIMPLE)]
        failures: list[str] = []
        for label, index in attempts:
            command = ["install", "--upgrade", package, *self._constraint_args(), "--default-index", PYPI_SIMPLE]
            if index != PYPI_SIMPLE:
                command.extend(["--index", index])
            result = self._uv_pip(*command, check=False)
            if result == 0:
                self.runner.log(f"Installed {package_id} from {label}: {index}")
                return
            failures.append(f"{label}: {index}")
        if exact_fallback:
            command = ["install", "--upgrade", exact_fallback, *self._constraint_args(), "--default-index", PYPI_SIMPLE]
            for _, index in attempts:
                if index != PYPI_SIMPLE:
                    command.extend(["--index", index])
            if self._uv_pip(*command, check=False) == 0:
                self.runner.log(f"Exact {package} was unavailable; installed compatible current {exact_fallback}.")
                return
        raise WheelInstallError(
            f"No compatible precompiled package was found for {package}. Tried: " + ", ".join(failures)
        )

    def _download_from_known_sources(self, item: dict[str, Any]) -> list[Path]:
        package_id = item["id"]
        sources = self.source_registry.matching(package_id, self.platform_info)
        if not sources:
            raise WheelInstallError(
                f"No enabled wheel sources match {item.get('name', package_id)} on "
                f"{self.platform_info.os_name}/{self.platform_info.architecture}/{self.options.accelerator}."
            )

        failures: list[str] = []
        package = item.get("package")
        for source in sources:
            if source.kind != "package-index" or not package:
                continue
            try:
                self.runner.log(
                    f"Searching {source.label} package index for {item.get('name', package_id)}: {source.location}"
                )
                return self._download_prebuilt(
                    package,
                    package_id,
                    index_url=source.location,
                    origin=f"{source.label}: {source.location}",
                )
            except Exception as exc:
                failures.append(f"{source.label} index: {exc}")

        asset_sources = [source for source in sources if source.kind != "package-index"]
        environment = target_environment(self.target_python)
        candidates = select_candidates(asset_sources, package_id, environment, requirement=item.get("package"))
        if not candidates:
            labels = ", ".join(f"{source.label}: {source.location}" for source in asset_sources)
            detail = f"No compatible wheel assets were found in: {labels}" if labels else "No asset repositories are enabled."
            if failures:
                detail += " | " + " | ".join(failures)
            raise WheelInstallError(detail)
        download_dir = self.local_wheelhouse / package_id / "known-sources"
        shutil.rmtree(download_dir, ignore_errors=True)
        download_dir.mkdir(parents=True, exist_ok=True)
        for candidate in candidates:
            self.runner.log(
                f"Trying {candidate.source.label} wheel source for {item.get('name', package_id)}: "
                f"{candidate.name}"
            )
            try:
                wheel = download_candidate(candidate, download_dir)
                result = self._uv_pip(
                    "install", "--reinstall", "--no-deps", str(wheel), check=False
                )
                import_name = item.get("import_name")
                if result == 0 and (
                    not import_name
                    or module_available(self.target_python, import_name, self.runner)
                ):
                    self._backup([wheel], package_id, f"{candidate.source.label}: {candidate.source.location}")
                    return [wheel]
                failures.append(f"{candidate.name}: installer rejected the wheel")
            except Exception as exc:
                failures.append(f"{candidate.name}: {exc}")
        raise WheelInstallError("; ".join(failures[-8:]) or "All compatible wheel candidates failed.")

    def _wheel_failure_decision(self, item: dict[str, Any], message: str) -> str:
        if self.decision_provider is None:
            return "build" if self.options.allow_source_builds else "cancel"
        return self.decision_provider(item, message, self.source_registry.path)

    def _resolve_or_build(self, item: dict[str, Any], initial_error: Exception) -> None:
        name = item.get("name", item["id"])
        message = str(initial_error)
        while True:
            decision = self._wheel_failure_decision(item, message)
            if decision == "retry":
                self.source_registry.reload()
                errors: list[str] = []
                try:
                    self._download_prebuilt(item["package"], item["id"])
                    return
                except Exception as exc:
                    errors.append(f"PyPI/uv: {exc}")
                try:
                    self._download_from_known_sources(item)
                    return
                except Exception as exc:
                    errors.append(f"known sources: {exc}")
                message = "No compatible precompiled wheel was found after retry. " + " | ".join(errors)
                continue
            if decision == "build":
                if not self.options.allow_source_builds:
                    raise WheelInstallError(
                        f"{name} needs a source build, but source builds are disabled."
                    ) from initial_error
                if item.get("requires_cuda") and not cuda_home():
                    raise WheelInstallError(
                        f"{name} needs a CUDA toolkit with nvcc for a source build. "
                        "Install the toolkit matching the CUDA version used by PyTorch and rerun."
                    ) from initial_error
                self.runner.log(f"Creating an isolated development environment for {name}.")
                # A configured source repository is authoritative for packages
                # such as SageAttention 2.x whose published source distribution may
                # disappear from, or never be available on, the active package index.
                # Exact mode pins an immutable ref when one was captured and always
                # verifies that the wheel built from source has the recorded version.
                if item.get("source_repository"):
                    self._build_from_source(item)
                else:
                    self._build_package_from_index(item)
                self.runner.log(f"Built and installed {name} from source.")
                return
            raise WheelInstallError(f"Installation cancelled because no compatible wheel was available for {name}.")

    def _build_package_from_index(self, item: dict[str, Any]) -> list[Path]:
        """Build a source distribution from the package index in isolation."""
        package_id = item["id"]
        package = item.get("package")
        if not package:
            raise WheelInstallError(f"No package is configured for {item.get('name', package_id)}.")

        build_venv, _ = self._build_environment(package_id)
        build_python = executable_for_venv(build_venv, "python")
        requirements = [
            "pip",
            "build",
            "packaging",
            "setuptools",
            "wheel",
            "ninja",
            *item.get("build_requirements", []),
        ]
        self.runner.run(
            [
                str(self.uv),
                "pip",
                "install",
                "--python",
                str(build_python),
                "--no-config",
                "--default-index",
                PYPI_SIMPLE,
                *list(dict.fromkeys(requirements)),
            ],
            env=clean_package_environment(),
        )
        if item.get("build_with_torch"):
            self._install_build_torch(build_python)

        wheel_dir = self.local_wheelhouse / package_id / "built"
        shutil.rmtree(wheel_dir, ignore_errors=True)
        wheel_dir.mkdir(parents=True, exist_ok=True)
        self.runner.run(
            [
                str(build_python),
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                str(wheel_dir),
                package,
            ],
            env=self._build_env_vars(),
        )
        wheels = sorted(wheel_dir.glob("*.whl"))
        if not wheels:
            raise WheelInstallError(f"Source build produced no wheel for {item.get('name', package_id)}.")
        self._verify_built_wheels(item, wheels)
        for wheel in wheels:
            self._uv_pip("install", "--reinstall", "--no-deps", str(wheel))
        self._verify_installed_item(item)
        self._backup(wheels, package_id, "source-build-from-index")
        return wheels

    def _install_from_backup(self, item: dict[str, Any]) -> bool:
        if not self.options.backup_dir:
            return False
        folder = self.options.backup_dir.expanduser().resolve() / item["id"]
        if not folder.is_dir():
            return False
        wheels = sorted(folder.glob("*.whl"))
        if not wheels:
            return False
        self.runner.log(f"Trying {len(wheels)} backed-up wheel(s) for {item.get('name', item['id'])}...")
        result = self._uv_pip(
            "install", "--reinstall", "--no-deps", *map(str, wheels), check=False
        )
        import_name = item.get("import_name")
        if result == 0 and (
            not import_name or module_available(self.target_python, import_name, self.runner)
        ):
            self.runner.log(f"Installed {item.get('name', item['id'])} from the selected wheel backup.")
            return True
        self.runner.log("Backed-up wheels were not compatible with this environment; continuing with index discovery.")
        return False

    def _checkout_source(self, item: dict[str, Any], source_dir: Path) -> Path:
        repository = item.get("source_repository")
        if not repository:
            raise WheelInstallError(
                f"No source repository is configured for {item.get('name', item['id'])}."
            )
        repository = validate_github_repository(str(repository))

        if source_dir.exists():
            shutil.rmtree(source_dir)

        self.runner.run(["git", "clone", "--filter=blob:none", repository, str(source_dir)])
        source_ref = item.get("source_ref")
        if source_ref:
            result = self.runner.run(
                ["git", "checkout", source_ref],
                cwd=source_dir,
                check=False,
            )
            if result != 0:
                fallback = item.get("source_ref_fallback")
                verify_version = bool(item.get("verify_source_version", True))
                if fallback and (not item.get("exact") or verify_version):
                    self.runner.log(
                        f"Source ref {source_ref!r} was unavailable for {item.get('name', item['id'])}; "
                        f"checking out {fallback!r} and verifying the built package version before installation."
                    )
                    self.runner.run(["git", "checkout", str(fallback)], cwd=source_dir)
                elif item.get("exact"):
                    raise WheelInstallError(
                        f"Exact source ref {source_ref!r} is unavailable for {item.get('name', item['id'])}; "
                        "no verified source fallback is configured."
                    )
                else:
                    self.runner.log(
                        f"Ref {source_ref!r} was unavailable; building the repository default branch."
                    )
        return source_dir / item.get("source_subdir", ".")

    @staticmethod
    def _expected_version(item: dict[str, Any]) -> str | None:
        package = str(item.get("package") or "")
        if "==" not in package:
            return None
        return package.split("==", 1)[1].strip() or None

    def _verify_built_wheels(self, item: dict[str, Any], wheels: list[Path]) -> None:
        expected = self._expected_version(item)
        if not expected:
            return
        from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

        expected_name = canonicalize_name(_safe_package_name(str(item.get("package") or item["id"])))
        matched = False
        discovered: list[str] = []
        for wheel in wheels:
            try:
                name, version, _build, _tags = parse_wheel_filename(wheel.name)
            except InvalidWheelFilename:
                continue
            discovered.append(f"{canonicalize_name(name)}=={version}")
            if canonicalize_name(name) == expected_name and str(version) == expected:
                matched = True
        if not matched:
            rendered = ", ".join(discovered) or "no parseable wheel metadata"
            raise WheelInstallError(
                f"The source build for {item.get('name', item['id'])} did not reproduce "
                f"the recorded package {expected_name}=={expected}; built: {rendered}."
            )

    def _verify_installed_item(self, item: dict[str, Any]) -> None:
        import_name = str(item.get("import_name") or "").strip()
        if import_name:
            completed = self.runner.capture(
                [str(self.target_python), "-c", f"import {import_name}"],
                env=self._build_env_vars(),
            )
            if completed.returncode != 0:
                raise WheelInstallError(
                    f"{item.get('name', item['id'])} installed, but importing {import_name!r} failed. "
                    f"Last output: {completed.stdout[-2000:]}"
                )

    def _build_from_source(self, item: dict[str, Any]) -> list[Path]:
        package_id = item["id"]
        build_venv, source_dir = self._build_environment(package_id)
        build_python = executable_for_venv(build_venv, "python")

        build_requirements = [
            "pip",
            "build",
            "packaging",
            "setuptools",
            "wheel",
            "ninja",
            *item.get("build_requirements", []),
        ]
        self.runner.run(
            [
                str(self.uv),
                "pip",
                "install",
                "--python",
                str(build_python),
                "--no-config",
                "--default-index",
                PYPI_SIMPLE,
                *list(dict.fromkeys(build_requirements)),
            ],
            env=clean_package_environment(),
        )
        # Install the target's exact PyTorch stack *after* generic build tools.
        # PyTorch may constrain setuptools and related build dependencies; doing
        # this last prevents a later unconstrained build-tool install from
        # replacing the ABI-compatible toolchain selected by the torch wheels.
        self._install_build_torch(build_python)

        source_target = self._checkout_source(item, source_dir)
        wheel_dir = self.local_wheelhouse / package_id / "built"
        shutil.rmtree(wheel_dir, ignore_errors=True)
        wheel_dir.mkdir(parents=True, exist_ok=True)

        env = self._build_env_vars()
        if package_id == "flash-attn":
            env["FLASH_ATTENTION_FORCE_BUILD"] = "TRUE"
            env["FORCE_CXX11_ABI"] = "TRUE"
        if package_id == "sageattention":
            # Match the official SageAttention source-build recipe while still
            # respecting explicit user overrides.  EXT_PARALLEL controls
            # extension compilation and NVCC threads controls each CUDA compile.
            jobs = max(1, int(env.get("MAX_JOBS", "4")))
            env.setdefault("EXT_PARALLEL", str(min(4, jobs)))
            env.setdefault("NVCC_APPEND_FLAGS", "--threads 8")
            env.setdefault("CMAKE_BUILD_PARALLEL_LEVEL", str(jobs))

        self.runner.run(
            [
                str(build_python),
                "-m",
                "pip",
                "wheel",
                "--no-build-isolation",
                "--no-deps",
                "--wheel-dir",
                str(wheel_dir),
                str(source_target),
            ],
            env=env,
        )

        wheels = sorted(wheel_dir.glob("*.whl"))
        if not wheels:
            raise WheelInstallError(f"Source build produced no wheel for {item['name']}.")
        self._verify_built_wheels(item, wheels)

        for wheel in wheels:
            self._uv_pip("install", "--reinstall", "--no-deps", str(wheel))
        self._verify_installed_item(item)

        self._backup(wheels, package_id, "source-build")
        return wheels

    def _locked_acceleration_package(
        self,
        *,
        exact_names: tuple[str, ...],
    ) -> tuple[str, str] | None:
        """Return the exact source-environment package identity for an accelerator.

        Managed accelerator packages are installed by this class instead of the
        bulk lock. Their distribution *name* is still part of reproducibility:
        for example, a working environment using ``cupy-cuda12x`` must not be
        silently replaced with ``cupy-cuda13x`` merely because the receiving
        machine advertises a CUDA 13 toolkit.
        """

        lock = self.profile.get("environment_lock", {})
        if not isinstance(lock, dict):
            return None
        wanted = [_safe_package_name(name) for name in exact_names]
        packages = lock.get("packages", [])
        if not isinstance(packages, list):
            return None
        for preferred in wanted:
            for raw in packages:
                if not isinstance(raw, dict):
                    continue
                name = _safe_package_name(str(raw.get("name") or ""))
                version = str(raw.get("version") or "").strip()
                if name == preferred and version:
                    return name, version
        return None

    def install_item(self, item: dict[str, Any]) -> None:
        name = item.get("name", item["id"])
        import_name = item.get("import_name")
        if self._install_from_backup(item):
            return
        if import_name and module_available(self.target_python, import_name, self.runner):
            self.runner.log(f"{name} is already importable; checking for requested upgrade.")

        minimum = item.get("minimum_compute_capability")
        if minimum and (
            not self.platform_info.compute_capability
            or self.platform_info.compute_capability < float(minimum)
        ):
            self.runner.log(
                f"Skipping {name}: compute capability {minimum:.1f}+ is required."
            )
            return

        kind = item.get("kind")
        if kind == "dynamic-cupy":
            major = (self.platform_info.cuda_version or "13").split(".", 1)[0]
            locked = self._locked_acceleration_package(
                exact_names=("cupy-cuda13x", "cupy-cuda12x", "cupy-cuda11x", "cupy"),
            )
            configured_name = str(item.get("package_name") or "").strip()
            if item.get("exact") and locked:
                base, version = locked
                self.runner.log(
                    f"Reproducing exact CuPy distribution from the working environment: {base}=={version}."
                )
            else:
                base = configured_name or f"cupy-cuda{major}x"
                version = str(item.get("version") or "13.6.0")
            package = f"{base}=={version}"
            self._uv_pip(
                "uninstall",
                "cupy",
                "cupy-wheel",
                "cupy-cuda11x",
                "cupy-cuda12x",
                "cupy-cuda13x",
                check=False,
            )
            self._install_package_from_indexes(
                package,
                "cupy",
                exact_fallback=None if item.get("exact") else base,
            )
            return

        if kind == "dynamic-onnxruntime":
            locked = self._locked_acceleration_package(
                exact_names=("onnxruntime-gpu", "onnxruntime"),
            )
            configured_name = str(item.get("package_name") or "").strip()
            if item.get("exact") and locked:
                base, version = locked
                self.runner.log(
                    f"Reproducing exact ONNX Runtime distribution from the working environment: {base}=={version}."
                )
            else:
                base = configured_name or (
                    "onnxruntime-gpu" if self.options.accelerator == "nvidia" else "onnxruntime"
                )
                version = str(item["version"])
            package = f"{base}=={version}"
            self._install_package_from_indexes(
                package,
                "onnxruntime",
                exact_fallback=None if item.get("exact") else base,
            )
            return

        if kind == "dynamic-tensorrt":
            major = (self.platform_info.cuda_version or "13").split(".", 1)[0]
            locked = self._locked_acceleration_package(
                exact_names=("tensorrt-cu13", "tensorrt-cu12", "tensorrt"),
            )
            configured_name = str(item.get("package_name") or "").strip()
            if item.get("exact") and locked:
                base, version = locked
                self.runner.log(
                    f"Reproducing exact TensorRT distribution from the working environment: {base}=={version}."
                )
            else:
                base = configured_name or (
                    f"tensorrt-cu{major}" if major in {"12", "13"} else "tensorrt"
                )
                version = str(item.get("versions", {}).get(major) or item.get("version") or "")
            package = f"{base}=={version}" if version else base
            self._install_package_from_indexes(
                package,
                "tensorrt",
                exact_fallback=None if item.get("exact") else base,
            )
            return

        package = item.get("package")
        if not package:
            raise WheelInstallError(f"No package is configured for {name}.")

        if kind == "pip":
            self._uv_pip("install", "--upgrade", package, *self._constraint_args())
            return

        if kind in {"pip-wheel-preferred", "wheel-or-source"}:
            errors: list[str] = []
            try:
                self.runner.log(f"Searching official package indexes for a compatible {name} wheel...")
                self._download_prebuilt(package, item["id"])
                self.runner.log(f"Installed a package-index {name} wheel.")
                return
            except Exception as wheel_error:
                errors.append(f"PyPI/uv: {wheel_error}")
            try:
                self.runner.log(f"Searching the editable wheel-source list for {name}...")
                self._download_from_known_sources(item)
                self.runner.log(f"Installed {name} from a configured precompiled-wheel source.")
                return
            except Exception as source_error:
                errors.append(f"known sources: {source_error}")
            message = "No compatible precompiled wheel was found. " + " | ".join(errors)
            self.runner.log(message)
            self._resolve_or_build(item, WheelInstallError(message))
            return

        raise WheelInstallError(f"Unsupported package installation kind: {kind!r}")
