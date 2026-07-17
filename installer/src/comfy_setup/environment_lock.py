from __future__ import annotations

import os
import platform
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote, unquote, urlparse

from packaging.utils import canonicalize_name


ENVIRONMENT_LOCK_SCHEMA = 1
EMBEDDED_WHEELS_ROOT = "embedded_wheels"


def validate_source_tree_path(value: str) -> None:
    path = PurePosixPath(str(value or ""))
    if not str(value or "").strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe locked source-tree path: {value!r}")

_INSTALLER_TOOLS = {"pip", "setuptools", "wheel", "uv"}
_TORCH_MANAGED = {"torch", "torchvision", "torchaudio", "triton"}
_ACCELERATION_MANAGED = {
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
    "tensorrt-cu13-bindings",
    "tensorrt-cu13-libs",
    "tensorrt_cu13_bindings",
    "tensorrt_cu13_libs",
    "onnxruntime",
    "onnxruntime-gpu",
    "flash-attn",
    "flash-attn-3",
    "sageattention",
    "pyopengl-accelerate",
}


def normalized_name(value: str) -> str:
    return canonicalize_name(str(value or "").strip())


def source_runtime() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": platform.system().lower(),
        "architecture": platform.machine().lower(),
    }


def managed_package_reason(name: str, extra_managed: Iterable[str] = ()) -> str | None:
    canonical = normalized_name(name)
    managed = {normalized_name(item) for item in extra_managed}
    if canonical in _INSTALLER_TOOLS:
        return "installer-tool"
    if canonical in _TORCH_MANAGED or canonical.startswith("nvidia-"):
        return "torch-backend"
    if canonical in _ACCELERATION_MANAGED or canonical in managed:
        return "accelerated-package"
    return None


def _local_path_from_direct_url(direct_url: dict[str, Any]) -> Path | None:
    url = str(direct_url.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    path = unquote(parsed.path)
    if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser().absolute()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _github_requirement(name: str, direct_url: dict[str, Any]) -> str | None:
    url = str(direct_url.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
        return None
    vcs_info = direct_url.get("vcs_info")
    if isinstance(vcs_info, dict):
        vcs = str(vcs_info.get("vcs") or "git").strip() or "git"
        commit = str(vcs_info.get("commit_id") or "").strip()
        # requested_revision may be a mutable branch name, so it is not enough
        # for an exact lock unless pip also recorded the resolved commit_id.
        if not commit:
            return None
        requirement = f"{name} @ {vcs}+{url}@{commit}"
        subdirectory = str(direct_url.get("subdirectory") or "").strip().strip("/")
        if subdirectory:
            requirement += "#subdirectory=" + quote(subdirectory, safe="/._-")
        return requirement
    return None


def _safe_wheel_payload(filename: str, used: set[str]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.+-]+", "_", Path(filename).name)
    if not cleaned.lower().endswith(".whl"):
        cleaned += ".whl"
    candidate = f"{EMBEDDED_WHEELS_ROOT}/{cleaned}"
    index = 2
    while candidate.lower() in used:
        stem = cleaned[:-4]
        candidate = f"{EMBEDDED_WHEELS_ROOT}/{stem}-{index}.whl"
        index += 1
    used.add(candidate.lower())
    return candidate


def validate_wheel_payload(payload: str) -> None:
    path = PurePosixPath(str(payload or ""))
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 2
        or path.parts[0] != EMBEDDED_WHEELS_ROOT
        or not path.name.lower().endswith(".whl")
    ):
        raise ValueError(f"Unsafe embedded wheel payload path: {payload!r}")


def build_environment_lock(
    distributions: Iterable[dict[str, Any]],
    *,
    source: dict[str, Any] | None = None,
    comfy_dir: Path | None = None,
    source_roots: Iterable[Path] = (),
    excluded_source_paths: Iterable[Path] = (),
    extra_managed: Iterable[str] = (),
) -> tuple[dict[str, Any], list[str], dict[str, Path]]:
    """Build an exact, portable Python-environment manifest.

    Every installed distribution is recorded. Index packages are pinned exactly
    and immutable GitHub direct references retain their commit. Local wheel
    provenance is *not* treated as permission to archive the wheel: the portable
    profile first reconstructs it from a package index, public source repository,
    or the accelerated-package build lifecycle. Installer tooling,
    PyTorch/backend packages, and packages supplied by ComfyUI/custom-node source
    trees remain explicitly audited but are installed by their dedicated
    lifecycle.
    """

    resolved_comfy = comfy_dir.expanduser().resolve() if comfy_dir else None
    # Every source-root entry also records where that source will exist in a
    # fresh managed installation.  External custom-node roots are recreated
    # below target/custom_nodes, while paths from the main checkout retain
    # their checkout-relative location.  Sort most-specific roots first so
    # <ComfyUI>/custom_nodes/foo is mapped as a node source rather than merely
    # as an arbitrary path under the main checkout.
    source_targets: list[tuple[Path, PurePosixPath]] = []
    if resolved_comfy:
        source_targets.append((resolved_comfy, PurePosixPath(".")))
    for root in source_roots:
        try:
            resolved = Path(root).expanduser().resolve()
        except OSError:
            resolved = Path(root).expanduser().absolute()
        target_prefix = PurePosixPath("custom_nodes")
        if resolved_comfy and resolved == resolved_comfy:
            target_prefix = PurePosixPath(".")
        candidate = (resolved, target_prefix)
        if candidate not in source_targets:
            source_targets.append(candidate)
    source_targets.sort(key=lambda item: len(item[0].parts), reverse=True)
    excluded_sources: list[Path] = []
    for path in excluded_source_paths:
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            resolved = Path(path).expanduser().absolute()
        if resolved not in excluded_sources:
            excluded_sources.append(resolved)
    warnings: list[str] = []
    packages: list[dict[str, Any]] = []
    omitted_packages: list[dict[str, str]] = []
    wheel_paths: dict[str, Path] = {}
    seen: set[str] = set()
    ambiguous_packages: set[str] = set()
    used_payloads: set[str] = set()

    for raw in sorted(distributions, key=lambda item: normalized_name(str(item.get("name") or ""))):
        name = normalized_name(str(raw.get("name") or ""))
        version = str(raw.get("version") or "").strip()
        if not name or not version:
            continue

        direct_url = raw.get("direct_url")
        local_path = _local_path_from_direct_url(direct_url) if isinstance(direct_url, dict) else None
        if local_path is not None:
            excluded_match = next(
                (root for root in excluded_sources if _is_within(local_path, root)),
                None,
            )
            if excluded_match is not None:
                omitted_packages.append(
                    {
                        "name": name,
                        "version": version,
                        "reason": "source belongs to a custom node explicitly omitted during export",
                    }
                )
                warnings.append(
                    f"Omitted source-tree package {name}=={version} because its source is under the omitted node {excluded_match}."
                )
                continue

        if name in seen:
            ambiguous_packages.add(name)
            continue
        seen.add(name)

        item: dict[str, Any] = {
            "name": name,
            "version": version,
            "requirement": f"{name}=={version}",
            "install": True,
            "portable": True,
            "source": "index",
        }
        reason = managed_package_reason(name, extra_managed)

        if isinstance(direct_url, dict):
            if local_path is not None:
                source_match = next(
                    ((root, target_prefix) for root, target_prefix in source_targets if _is_within(local_path, root)),
                    None,
                )
                if source_match:
                    root, target_prefix = source_match
                    relative = local_path.relative_to(root)
                    target_relative = target_prefix / PurePosixPath(relative.as_posix())
                    source_path = target_relative.as_posix()
                    if source_path == ".":
                        source_path = "."
                    validate_source_tree_path(source_path)
                    item["install"] = False
                    item["managed_by"] = "comfyui-or-custom-node-source"
                    item["source"] = "source-tree"
                    item["source_path"] = source_path
                    dir_info = direct_url.get("dir_info")
                    item["editable"] = bool(
                        isinstance(dir_info, dict) and dir_info.get("editable")
                    )
                elif reason:
                    item["install"] = False
                    item["managed_by"] = reason
                    item["source"] = "managed"
                elif local_path.suffix.lower() == ".whl" and local_path.is_file():
                    item["source"] = "index"
                    item["original_source"] = "local-wheel"
                    item["original_filename"] = local_path.name
                    warnings.append(
                        f"{name}=={version} was installed from a local wheel. The wheel was not embedded; "
                        "the setup will resolve the same version from configured package indexes or a public source/build rule."
                    )
                else:
                    item["source"] = "index"
                    item["original_source"] = "local-path"
                    item["original_path_name"] = local_path.name
                    warnings.append(
                        f"{name}=={version} came from a machine-local path outside ComfyUI/custom nodes. "
                        "No local files were embedded; the installer will resolve the recorded version from public/configured sources."
                    )
            elif reason:
                item["install"] = False
                item["managed_by"] = reason
                item["source"] = "managed"
            else:
                requirement = _github_requirement(name, direct_url)
                if requirement:
                    item["requirement"] = requirement
                    item["source"] = "github"
                else:
                    item["install"] = False
                    item["portable"] = False
                    item["source"] = "unsupported-direct-url"
                    item["reason"] = "Only immutable public GitHub direct references are portable."
                    warnings.append(
                        f"{name}=={version} uses a direct package source that is not an immutable public GitHub URL."
                    )
        elif reason:
            item["install"] = False
            item["managed_by"] = reason
            item["source"] = "managed"

        packages.append(item)

    if ambiguous_packages:
        for item in packages:
            if item.get("name") not in ambiguous_packages:
                continue
            item["install"] = False
            item["portable"] = False
            item["source"] = "ambiguous-installed-distributions"
            item["reason"] = (
                "Multiple installed distributions use the same canonical package name, so the active source cannot be identified reliably."
            )
        for name in sorted(ambiguous_packages):
            warnings.append(
                f"Multiple installed distributions were found for {name}; exact export cannot determine which one supplied the working package."
            )
    nonportable = [item["name"] for item in packages if not item.get("portable", True)]
    lock = {
        "schema_version": ENVIRONMENT_LOCK_SCHEMA,
        "mode": "exact",
        "complete": not nonportable,
        "source": dict(source or source_runtime()),
        "packages": packages,
        "nonportable_packages": nonportable,
        "ambiguous_packages": sorted(ambiguous_packages),
        "explicitly_omitted_packages": omitted_packages,
    }
    return lock, warnings, wheel_paths


def lock_install_requirements(lock: dict[str, Any] | None) -> list[str]:
    if not isinstance(lock, dict):
        return []
    output: list[str] = []
    for item in lock.get("packages", []):
        if not isinstance(item, dict) or not item.get("install", True):
            continue
        if item.get("source") == "embedded-wheel":
            continue
        requirement = str(item.get("requirement") or "").strip()
        if requirement and requirement not in output:
            output.append(requirement)
    return output


def lock_embedded_wheels(lock: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(lock, dict):
        return []
    output: list[dict[str, Any]] = []
    for item in lock.get("packages", []):
        if not isinstance(item, dict) or not item.get("install", True):
            continue
        if item.get("source") != "embedded-wheel":
            continue
        payload = str(item.get("payload") or "")
        validate_wheel_payload(payload)
        output.append(item)
    return output


def lock_source_tree_packages(lock: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(lock, dict):
        return []
    output: list[dict[str, Any]] = []
    for item in lock.get("packages", []):
        if not isinstance(item, dict) or item.get("source") != "source-tree":
            continue
        source_path = str(item.get("source_path") or "")
        validate_source_tree_path(source_path)
        output.append(item)
    return output


def lock_has_installable(lock: dict[str, Any] | None) -> bool:
    return bool(lock_install_requirements(lock) or lock_embedded_wheels(lock))


def lock_has_packages(lock: dict[str, Any] | None) -> bool:
    return bool(isinstance(lock, dict) and lock.get("packages"))


def lock_version_map(lock: dict[str, Any] | None, *, install_only: bool = True) -> dict[str, str]:
    if not isinstance(lock, dict):
        return {}
    output: dict[str, str] = {}
    for item in lock.get("packages", []):
        if not isinstance(item, dict):
            continue
        if install_only and not item.get("install", True):
            continue
        name = normalized_name(str(item.get("name") or ""))
        version = str(item.get("version") or "").strip()
        if name and version:
            output[name] = version
    return output


def lock_reproducible_version_map(lock: dict[str, Any] | None) -> dict[str, str]:
    """Versions that must remain fixed across every installation phase.

    Dedicated lifecycles install PyTorch and acceleration packages, so those
    entries have ``install: false`` in the lock and are not part of the initial
    bulk requirement file. They still need to constrain transitive resolution
    and be verified at the end. Installer tooling and distributions supplied by
    a source tree are intentionally excluded because they are not target-runtime
    packages installed by the lock lifecycle.
    """

    if not isinstance(lock, dict):
        return {}
    output: dict[str, str] = {}
    for item in lock.get("packages", []):
        if not isinstance(item, dict) or not item.get("portable", True):
            continue
        if item.get("managed_by") == "installer-tool":
            continue
        if item.get("source") == "source-tree" and not item.get("source_path"):
            continue
        name = normalized_name(str(item.get("name") or ""))
        version = str(item.get("version") or "").strip()
        if name and version:
            output[name] = version
    return output


def compatibility_issues(
    lock: dict[str, Any] | None,
    *,
    os_name: str,
    architecture: str,
    python_version: str,
    accelerator: str | None = None,
    cuda_version: str | None = None,
    rocm_version: str | None = None,
) -> list[str]:
    if not isinstance(lock, dict) or not lock.get("packages"):
        return []
    source = lock.get("source", {})
    if not isinstance(source, dict):
        return []
    issues: list[str] = []
    expected_python = str(source.get("python") or "")
    if expected_python and ".".join(expected_python.split(".")[:2]) != ".".join(str(python_version).split(".")[:2]):
        issues.append(
            f"Python {expected_python} lock cannot be reproduced with selected Python {python_version}."
        )
    expected_os = str(source.get("os") or "").lower()
    if expected_os and expected_os != str(os_name).lower():
        issues.append(f"Environment lock was created on {expected_os}, not {os_name}.")
    expected_arch = str(source.get("architecture") or "").lower()
    actual_arch = str(architecture).lower()
    aliases = {"amd64": "x86_64", "x86-64": "x86_64", "aarch64": "arm64"}
    normalized_expected = aliases.get(expected_arch, expected_arch)
    normalized_actual = aliases.get(actual_arch, actual_arch)
    if normalized_expected and normalized_expected != normalized_actual:
        issues.append(f"Environment lock architecture is {expected_arch}, not {architecture}.")
    expected_accelerator = str(source.get("accelerator") or "").lower()
    if expected_accelerator and accelerator and expected_accelerator != str(accelerator).lower():
        issues.append(
            f"Environment lock used the {expected_accelerator} PyTorch backend, not {accelerator}."
        )
    expected_cuda = str(source.get("torch_cuda") or "").strip()
    if expected_accelerator == "nvidia" and expected_cuda and cuda_version:
        expected_major = expected_cuda.split(".", 1)[0]
        actual_major = str(cuda_version).split(".", 1)[0]
        if expected_major != actual_major:
            issues.append(
                f"Environment lock used PyTorch CUDA {expected_cuda}, but the selected target reports CUDA {cuda_version}."
            )
    expected_rocm = str(source.get("torch_hip") or "").strip()
    if expected_accelerator == "rocm" and expected_rocm and rocm_version:
        expected_major = expected_rocm.split(".", 1)[0]
        actual_major = str(rocm_version).split(".", 1)[0]
        if expected_major != actual_major:
            issues.append(
                f"Environment lock used PyTorch ROCm {expected_rocm}, but the selected target reports ROCm {rocm_version}."
            )
    if lock.get("complete") is False:
        names = ", ".join(str(item) for item in lock.get("nonportable_packages", [])) or "unknown packages"
        issues.append(f"Environment lock is incomplete because these packages were non-portable: {names}.")
    return issues
