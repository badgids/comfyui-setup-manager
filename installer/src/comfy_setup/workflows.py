from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
import yaml
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

from platformdirs import user_data_path
from .shared_assets import load_shared_asset_paths


class WorkflowError(RuntimeError):
    """Raised when a workflow or workflow bundle cannot be validated safely."""


WORKFLOW_PACK_KIND = "comfyui-workflow-bundle"
LEGACY_WORKFLOW_KIND = "comfyui-workflow"
WORKFLOW_SCHEMA = 3
WORKFLOW_PACK_EXTENSION = ".comfyworkflows"
LEGACY_WORKFLOW_EXTENSION = ".comfyworkflow"

MODEL_SUFFIXES = {
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".gguf",
    ".bin",
    ".onnx",
    ".engine",
    ".plan",
    ".trt",
    ".vae",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".json",
}
SKIP_DIRECTORY_NAMES = {
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "__pycache__",
    ".cache",
    "models",
    "output",
    "input",
    "temp",
}
MAX_WORKFLOW_SIZE = 32 * 1024 * 1024
MAX_SUPPORT_SIZE = 32 * 1024 * 1024
MAX_BUNDLE_SIZE = 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class WorkflowSummary:
    format: str
    node_count: int
    node_types: tuple[str, ...]
    model_references: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowLibrary:
    path: Path
    user_name: str
    source: str


@dataclass(frozen=True, slots=True)
class WorkflowBundleSummary:
    name: str
    workflow_count: int
    support_file_count: int
    default_install_subdirectory: str
    setup_embedded: bool
    setup_reference: str | None
    readme_included: bool


@dataclass(frozen=True, slots=True)
class WorkflowInstallResult:
    installed: tuple[Path, ...]
    support_files: tuple[Path, ...]
    warnings: tuple[str, ...]
    destination: Path
    setup_profile: Path | None = None
    setup_reference: str | None = None


def user_workflow_library_dir() -> Path:
    """Return the manager's archive library, not ComfyUI's native workflow folder."""
    directory = user_data_path("comfyui-setup-manager", appauthor=False) / "workflow-bundles"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "workflow-bundle"


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def _safe_relative(value: str, *, allow_empty: bool = True) -> PurePosixPath:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized:
        if allow_empty:
            return PurePosixPath()
        raise WorkflowError("A relative path is required.")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise WorkflowError(f"Unsafe relative path: {value}")
    return path


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"Workflow file not found: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Invalid workflow JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError("Workflow JSON root must be an object.")
    return data


def _unwrap_workflow(data: dict[str, Any]) -> dict[str, Any]:
    workflow = data.get("workflow")
    if isinstance(workflow, dict):
        return workflow
    prompt = data.get("prompt")
    if isinstance(prompt, dict) and any(
        isinstance(value, dict) and "class_type" in value for value in prompt.values()
    ):
        return prompt
    return data


def _workflow_format(data: dict[str, Any]) -> str:
    if isinstance(data.get("nodes"), list):
        return "save"
    if data and any(
        isinstance(value, dict) and isinstance(value.get("class_type"), str)
        for value in data.values()
    ):
        return "api"
    raise WorkflowError(
        "The JSON is not a recognized ComfyUI workflow. Expected frontend save "
        "format with a nodes list or API format with class_type entries."
    )


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def _looks_absolute(value: str) -> bool:
    return bool(
        value.startswith(("/", "\\\\", "file://"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
        or value.startswith("~/")
    )


def summarize_workflow(data: dict[str, Any]) -> WorkflowSummary:
    workflow = _unwrap_workflow(data)
    workflow_format = _workflow_format(workflow)
    node_types: set[str] = set()
    node_count = 0

    if workflow_format == "save":
        nodes = workflow.get("nodes", [])
        node_count = len(nodes)
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if isinstance(node_type, str) and node_type:
                node_types.add(node_type)
    else:
        for value in workflow.values():
            if not isinstance(value, dict):
                continue
            node_type = value.get("class_type")
            if isinstance(node_type, str) and node_type:
                node_count += 1
                node_types.add(node_type)

    models: set[str] = set()
    warnings: set[str] = set()
    for text in _iter_strings(workflow):
        suffix = Path(text.replace("\\", "/")).suffix.lower()
        if suffix in MODEL_SUFFIXES:
            models.add(text)
        if _looks_absolute(text):
            warnings.add(
                "The workflow contains one or more absolute machine paths. "
                "Review them before sharing or importing on another system."
            )

    return WorkflowSummary(
        format=workflow_format,
        node_count=node_count,
        node_types=tuple(sorted(node_types, key=str.lower)),
        model_references=tuple(sorted(models, key=str.lower)),
        warnings=tuple(sorted(warnings)),
    )


def validate_workflow(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise WorkflowError("Workflow root must be a JSON object.")
    summarize_workflow(data)
    return data


def load_workflow_json(path: Path) -> dict[str, Any]:
    source = path.expanduser().resolve()
    if source.stat().st_size > MAX_WORKFLOW_SIZE:
        raise WorkflowError(f"Workflow JSON is too large: {source}")
    return validate_workflow(_load_json(source))


def _comfy_root(value: Path) -> Path:
    candidate = value.expanduser().resolve()
    if (candidate / "main.py").is_file():
        return candidate
    nested = candidate / "ComfyUI"
    if (nested / "main.py").is_file():
        return nested
    raise WorkflowError(f"Not a recognized ComfyUI source or portable installation: {candidate}")


def workflow_destination(
    comfy_dir: Path | None,
    user_name: str = "default",
    *,
    custom_directory: Path | None = None,
) -> Path:
    """Resolve a native or explicitly selected workflow directory."""
    if custom_directory is not None:
        custom = custom_directory.expanduser()
        if custom.is_absolute():
            destination = custom
        elif comfy_dir is not None:
            destination = _comfy_root(comfy_dir) / "user" / user_name / "workflows" / custom
        else:
            destination = Path.cwd() / custom
        destination.mkdir(parents=True, exist_ok=True)
        return destination.resolve()

    if comfy_dir is None:
        try:
            shared = load_shared_asset_paths().workflows
            shared.mkdir(parents=True, exist_ok=True)
            return shared.resolve()
        except Exception as exc:
            raise WorkflowError("Choose a ComfyUI installation or configure a shared workflow directory.") from exc
    root = _comfy_root(comfy_dir)
    destination = root / "user" / (user_name or "default") / "workflows"
    destination.mkdir(parents=True, exist_ok=True)
    return destination.resolve()


def discover_workflow_libraries(comfy_dir: Path | None = None) -> list[WorkflowLibrary]:
    libraries: list[WorkflowLibrary] = []
    seen: set[Path] = set()
    if comfy_dir is None:
        try:
            shared = load_shared_asset_paths().workflows.resolve()
            shared.mkdir(parents=True, exist_ok=True)
            libraries.append(WorkflowLibrary(shared, "shared", "external-shared"))
            seen.add(shared)
        except Exception:
            pass

    if comfy_dir is not None:
        try:
            root = _comfy_root(comfy_dir)
        except WorkflowError:
            root = None
        if root is not None:
            user_root = root / "user"
            if user_root.is_dir():
                for child in sorted(user_root.iterdir(), key=lambda item: item.name.lower()):
                    workflow_dir = child / "workflows"
                    if workflow_dir.is_dir() and workflow_dir.resolve() not in seen:
                        libraries.append(WorkflowLibrary(workflow_dir.resolve(), child.name, "installation"))
                        seen.add(workflow_dir.resolve())
            default_dir = root / "user" / "default" / "workflows"
            if default_dir.resolve() not in seen:
                libraries.append(WorkflowLibrary(default_dir.resolve(), "default", "installation"))
                seen.add(default_dir.resolve())
            legacy = root / "workflows"
            if legacy.is_dir() and legacy.resolve() not in seen:
                libraries.append(WorkflowLibrary(legacy.resolve(), "legacy", "installation-legacy"))
                seen.add(legacy.resolve())

    desktop_user_root = user_data_path("ComfyUI", appauthor=False) / "user"
    if desktop_user_root.is_dir():
        for child in sorted(desktop_user_root.iterdir(), key=lambda item: item.name.lower()):
            workflow_dir = child / "workflows"
            if workflow_dir.is_dir() and workflow_dir.resolve() not in seen:
                libraries.append(WorkflowLibrary(workflow_dir.resolve(), child.name, "desktop"))
                seen.add(workflow_dir.resolve())

    return libraries


def discover_workflow_files(comfy_dir: Path, user_name: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    for library in discover_workflow_libraries(comfy_dir):
        if user_name is not None and library.user_name != user_name:
            continue
        if not library.path.is_dir():
            continue
        for path in library.path.rglob("*.json"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                load_workflow_json(path)
            except (OSError, WorkflowError):
                continue
            candidates.append(path.resolve())
    return sorted(set(candidates), key=lambda path: str(path).lower())


def export_native_workflow(source_path: Path, output_path: Path) -> Path:
    """Export a workflow in ComfyUI's native JSON format without wrapping it."""
    source = source_path.expanduser().resolve()
    workflow = load_workflow_json(source)
    output = output_path.expanduser()
    if output.suffix.lower() != ".json":
        output = output.with_suffix(".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
    return output.resolve()


def _should_skip(path: Path) -> bool:
    return any(part.lower() in SKIP_DIRECTORY_NAMES for part in path.parts)


def _candidate_files(source: Path, recursive: bool) -> list[Path]:
    if source.is_file():
        return [source]
    iterator = source.rglob("*") if recursive else source.glob("*")
    return sorted(
        (path for path in iterator if path.is_file() and not path.is_symlink()),
        key=lambda path: str(path).lower(),
    )


def _determine_source_root(sources: list[Path], explicit_root: Path | None) -> tuple[Path | None, bool]:
    if explicit_root is not None:
        resolved = explicit_root.expanduser().resolve()
        if not resolved.is_dir():
            raise WorkflowError(f"Workflow source root is not a directory: {resolved}")
        return resolved, False
    if len(sources) == 1:
        source = sources[0]
        return (source if source.is_dir() else source.parent), False

    try:
        common = Path(os.path.commonpath([str(path) for path in sources]))
    except ValueError:
        return None, True
    if common == Path(common.anchor):
        return None, True
    if common.is_file():
        common = common.parent
    return common, False


def _relative_for_source(path: Path, source: Path, root: Path | None, split_roots: bool) -> PurePosixPath:
    if split_roots:
        prefix = source.name if source.is_dir() else source.parent.name
        if source.is_dir():
            relative = path.relative_to(source)
        else:
            relative = Path(path.name)
        return PurePosixPath(prefix, *relative.parts)
    if root is None:
        return PurePosixPath(path.name)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise WorkflowError(f"Source is outside the selected source root: {path}") from exc
    return PurePosixPath(*relative.parts)


def _collect_bundle_payload(
    sources: Iterable[Path],
    *,
    source_root: Path | None,
    recursive: bool,
    include_support_files: bool,
) -> tuple[list[tuple[Path, PurePosixPath, WorkflowSummary]], list[tuple[Path, PurePosixPath]]]:
    resolved_sources = [value.expanduser().resolve() for value in sources]
    if not resolved_sources:
        raise WorkflowError("Choose at least one workflow file or directory.")
    for source in resolved_sources:
        if not source.exists():
            raise WorkflowError(f"Workflow source does not exist: {source}")

    root, split_roots = _determine_source_root(resolved_sources, source_root)
    workflows: list[tuple[Path, PurePosixPath, WorkflowSummary]] = []
    support: list[tuple[Path, PurePosixPath]] = []
    used: set[PurePosixPath] = set()
    total_size = 0

    for source in resolved_sources:
        for candidate in _candidate_files(source, recursive):
            relative = _relative_for_source(candidate, source, root, split_roots)
            if _should_skip(relative):
                continue
            if not _safe_member(relative.as_posix()):
                raise WorkflowError(f"Unsafe bundle path: {relative}")
            if relative in used:
                raise WorkflowError(f"Two selected files resolve to the same bundle path: {relative}")

            is_workflow = False
            summary: WorkflowSummary | None = None
            if candidate.suffix.lower() == ".json":
                try:
                    workflow = load_workflow_json(candidate)
                    summary = summarize_workflow(workflow)
                    is_workflow = True
                except WorkflowError:
                    is_workflow = False

            if is_workflow and summary is not None:
                total_size += candidate.stat().st_size
                workflows.append((candidate, relative, summary))
                used.add(relative)
            elif include_support_files and candidate.suffix.lower() in SUPPORT_SUFFIXES:
                if candidate.stat().st_size > MAX_SUPPORT_SIZE:
                    continue
                total_size += candidate.stat().st_size
                support.append((candidate, relative))
                used.add(relative)

            if total_size > MAX_BUNDLE_SIZE:
                raise WorkflowError("Selected workflow bundle exceeds the 1 GiB safety limit.")

    if not workflows:
        raise WorkflowError("No valid ComfyUI workflow JSON files were found.")
    return workflows, support


def _generated_readme(name: str, description: str, default_subdirectory: str, setup_reference: str | None) -> str:
    lines = [f"# {name}", ""]
    if description:
        lines.extend([description, ""])
    lines.extend(
        [
            "This archive contains native ComfyUI workflow JSON files. The directory tree under `workflows/` is preserved when installed.",
            "",
            "## Installation",
            "",
            "Import this archive with **ComfyUI Setup Manager**. You may install it into the archive's default workflow subdirectory or choose another workflow directory.",
            "",
            f"Default install subdirectory: `{default_subdirectory or '(workflow library root)'}`",
            "",
            "The archive does not include models, LoRAs, checkpoints, credentials, or public plugin source repositories.",
        ]
    )
    if setup_reference:
        lines.extend(["", "## Recommended ComfyUI setup", "", setup_reference])
    return "\n".join(lines).rstrip() + "\n"


def write_workflow_pack(
    sources: Iterable[Path],
    output_path: Path,
    *,
    name: str,
    description: str = "",
    publisher: str = "",
    tags: Iterable[str] = (),
    default_install_subdirectory: str = "",
    setup_profile_path: Path | None = None,
    setup_reference: str | None = None,
    readme_path: Path | None = None,
    source_root: Path | None = None,
    include_support_files: bool = True,
    recursive: bool = True,
    companion_yaml_paths: Iterable[Path] = (),
) -> Path:
    """Create a native workflow archive while preserving the selected directory tree."""
    if not name.strip():
        raise WorkflowError("Workflow bundle name is required.")
    default_subdir = _safe_relative(default_install_subdirectory).as_posix()
    if default_subdir == ".":
        default_subdir = ""

    workflows, support = _collect_bundle_payload(
        sources,
        source_root=source_root,
        recursive=recursive,
        include_support_files=include_support_files,
    )

    workflow_entries: list[dict[str, Any]] = []
    for path, relative, summary in workflows:
        workflow_entries.append(
            {
                "path": relative.as_posix(),
                "sha256": _sha256_file(path),
                "format": summary.format,
                "node_count": summary.node_count,
                "required_node_types": list(summary.node_types),
                "model_references": list(summary.model_references),
                "warnings": list(summary.warnings),
            }
        )

    support_entries = [
        {"path": relative.as_posix(), "sha256": _sha256_file(path), "size": path.stat().st_size}
        for path, relative in support
    ]

    setup: dict[str, Any] = {
        "embedded_path": None,
        "embedded_sha256": None,
        "reference": setup_reference or None,
    }
    embedded_setup: tuple[str, bytes] | None = None
    if setup_profile_path is not None:
        profile_path = setup_profile_path.expanduser().resolve()
        if not profile_path.is_file() or not profile_path.name.lower().endswith((".comfyuisetup", ".comfysetup")):
            raise WorkflowError("Embedded setup must be an existing .comfyuisetup or legacy .comfysetup file.")
        archive_name = f"setup/{profile_path.name}"
        setup["embedded_path"] = archive_name
        profile_bytes = profile_path.read_bytes()
        setup["embedded_sha256"] = _sha256_bytes(profile_bytes)
        embedded_setup = (archive_name, profile_bytes)

    readme_text = (
        readme_path.expanduser().resolve().read_text(encoding="utf-8")
        if readme_path is not None
        else _generated_readme(name, description, default_subdir, setup_reference)
    )

    companion_files: list[tuple[str, bytes]] = []
    allowed_companion_names = {
        "models.yaml", "workflows.yaml", "nodes.yaml", "libraries.yaml",
        "model-sources.yaml", "workflow-sources.yaml", "wheel-sources.yaml",
        "requirements.yaml", "metadata.yaml",
    }
    for companion_path in companion_yaml_paths:
        candidate = companion_path.expanduser().resolve()
        if not candidate.is_file() or candidate.suffix.lower() not in {".yaml", ".yml"}:
            raise WorkflowError(f"Companion requirements file must be YAML: {candidate}")
        try:
            parsed = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise WorkflowError(f"Invalid companion YAML {candidate}: {exc}") from exc
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, (dict, list)):
            raise WorkflowError(f"Companion YAML must contain a mapping or list: {candidate}")
        name_candidate = candidate.name.lower()
        archive_name = name_candidate if name_candidate in allowed_companion_names else f"metadata/{candidate.name}"
        companion_files.append((archive_name, candidate.read_bytes()))

    manifest = {
        "schema_version": WORKFLOW_SCHEMA,
        "kind": WORKFLOW_PACK_KIND,
        "id": _slug(name),
        "name": name.strip(),
        "description": description,
        "publisher": publisher,
        "tags": sorted({tag.strip() for tag in tags if tag.strip()}, key=str.lower),
        "payload_root": "workflows",
        "default_install_subdirectory": default_subdir,
        "workflow_count": len(workflow_entries),
        "support_file_count": len(support_entries),
        "workflows": workflow_entries,
        "support_files": support_entries,
        "readme_path": "README.md",
        "readme_sha256": _sha256_bytes(readme_text.encode("utf-8")),
        "setup": setup,
        "models_included": False,
        "public_plugins_included": False,
        "directory_structure_preserved": True,
        "companion_yaml": [name for name, _ in companion_files],
        "companion_files": [
            {"path": name, "sha256": _sha256_bytes(payload), "size": len(payload)}
            for name, payload in companion_files
        ],
    }

    output = output_path.expanduser()
    if not output.name.lower().endswith(WORKFLOW_PACK_EXTENSION):
        output = output.with_name(output.name + WORKFLOW_PACK_EXTENSION)
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr("bundle.yaml", yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=110))
        archive.writestr("README.md", readme_text)
        for companion_name, companion_payload in companion_files:
            archive.writestr(companion_name, companion_payload)
        for path, relative, _ in workflows:
            archive.write(path, f"workflows/{relative.as_posix()}")
        for path, relative in support:
            archive.write(path, f"workflows/{relative.as_posix()}")
        if embedded_setup is not None:
            archive.writestr(embedded_setup[0], embedded_setup[1])
    return output.resolve()


def _read_bundle_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    names = archive.namelist()
    if any(not _safe_member(name) for name in names):
        raise WorkflowError("Unsafe path found in workflow archive.")
    try:
        if "bundle.yaml" in names:
            manifest = yaml.safe_load(archive.read("bundle.yaml").decode("utf-8"))
        else:
            manifest = json.loads(archive.read("bundle.json").decode("utf-8"))
    except KeyError as exc:
        raise WorkflowError("Workflow archive is missing bundle.yaml (or legacy bundle.json).") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise WorkflowError("Workflow archive manifest is invalid YAML/JSON.") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != WORKFLOW_PACK_KIND:
        raise WorkflowError("The archive is not a supported ComfyUI workflow bundle.")
    if manifest.get("schema_version") != WORKFLOW_SCHEMA:
        raise WorkflowError("Unsupported workflow archive schema.")
    _safe_relative(str(manifest.get("default_install_subdirectory") or ""))
    return manifest


def read_workflow_pack(path: Path) -> tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, Any]]]]:
    bundle = path.expanduser().resolve()
    entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    try:
        with zipfile.ZipFile(bundle) as archive:
            manifest = _read_bundle_manifest(archive)
            for entry in manifest.get("workflows", []):
                if not isinstance(entry, dict):
                    raise WorkflowError("Workflow bundle contains an invalid workflow entry.")
                relative = _safe_relative(str(entry.get("path") or ""), allow_empty=False)
                archive_path = f"workflows/{relative.as_posix()}"
                try:
                    payload = archive.read(archive_path)
                    workflow = json.loads(payload.decode("utf-8"))
                except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise WorkflowError(f"Invalid workflow entry: {relative}") from exc
                if _sha256_bytes(payload) != entry.get("sha256"):
                    raise WorkflowError(f"Workflow checksum failed: {relative}")
                validate_workflow(workflow)
                entries.append((entry, workflow))
    except FileNotFoundError as exc:
        raise WorkflowError(f"Workflow bundle not found: {bundle}") from exc
    except zipfile.BadZipFile as exc:
        raise WorkflowError(f"Invalid workflow bundle: {bundle}") from exc
    return manifest, entries


def inspect_workflow_artifact(path: Path) -> WorkflowBundleSummary | WorkflowSummary:
    artifact = path.expanduser().resolve()
    if artifact.suffix.lower() == ".json":
        return summarize_workflow(load_workflow_json(artifact))
    if artifact.name.lower().endswith(WORKFLOW_PACK_EXTENSION) or artifact.suffix.lower() == ".zip":
        with zipfile.ZipFile(artifact) as archive:
            manifest = _read_bundle_manifest(archive)
        setup = manifest.get("setup") if isinstance(manifest.get("setup"), dict) else {}
        return WorkflowBundleSummary(
            name=str(manifest.get("name") or artifact.stem),
            workflow_count=int(manifest.get("workflow_count") or 0),
            support_file_count=int(manifest.get("support_file_count") or 0),
            default_install_subdirectory=str(manifest.get("default_install_subdirectory") or ""),
            setup_embedded=bool(setup.get("embedded_path")),
            setup_reference=str(setup.get("reference")) if setup.get("reference") else None,
            readme_included=bool(manifest.get("readme_path")),
        )
    if artifact.name.lower().endswith(LEGACY_WORKFLOW_EXTENSION):
        manifest, workflow = read_legacy_workflow_bundle(artifact)
        return summarize_workflow(workflow)
    raise WorkflowError("Unsupported workflow artifact. Use .json, .comfyworkflows, or a compatible .zip bundle.")


def _unique_destination(path: Path, overwrite: bool) -> Path:
    if overwrite or not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _install_setup_from_bundle(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
    *,
    download_reference: bool,
) -> tuple[Path | None, str | None, list[str]]:
    warnings: list[str] = []
    setup = manifest.get("setup") if isinstance(manifest.get("setup"), dict) else {}
    embedded = setup.get("embedded_path")
    reference = str(setup.get("reference")) if setup.get("reference") else None

    if embedded:
        embedded_path = str(embedded)
        if not _safe_member(embedded_path) or not embedded_path.lower().endswith((".comfyuisetup", ".comfysetup")):
            raise WorkflowError("Workflow bundle contains an unsafe embedded setup path.")
        try:
            payload = archive.read(embedded_path)
        except KeyError as exc:
            raise WorkflowError("Embedded setup profile is missing from the workflow bundle.") from exc
        expected = setup.get("embedded_sha256")
        if expected and _sha256_bytes(payload) != expected:
            raise WorkflowError("Embedded setup-profile checksum validation failed.")
        with tempfile.TemporaryDirectory(prefix="comfyuisetup-workflow-") as temporary:
            candidate = Path(temporary) / Path(embedded_path).name
            candidate.write_bytes(payload)
            from .profile import import_profile

            record = import_profile(candidate)
            return record.path, reference, warnings

    if reference and download_reference:
        parsed = urllib.parse.urlparse(reference)
        if parsed.scheme not in {"https", "http"}:
            warnings.append(f"Setup reference is not an HTTP(S) URL: {reference}")
            return None, reference, warnings
        if not parsed.path.lower().endswith((".comfyuisetup", ".comfysetup")):
            warnings.append(
                "The setup reference does not point directly to a .comfyuisetup or legacy .comfysetup file. "
                "Open it manually from the workflow bundle details."
            )
            return None, reference, warnings
        try:
            request = urllib.request.Request(reference, headers={"User-Agent": "ComfyUI-Setup-Manager"})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read(128 * 1024 * 1024 + 1)
            if len(payload) > 128 * 1024 * 1024:
                raise WorkflowError("Referenced setup-profile file exceeds the 128 MiB safety limit.")
            with tempfile.TemporaryDirectory(prefix="comfyuisetup-workflow-") as temporary:
                candidate = Path(temporary) / Path(parsed.path).name
                candidate.write_bytes(payload)
                from .profile import import_profile

                record = import_profile(candidate)
                return record.path, reference, warnings
        except Exception as exc:
            warnings.append(f"Could not download the referenced setup-profile file: {exc}")
    return None, reference, warnings


def import_workflow_artifact(
    artifact_path: Path,
    comfy_dir: Path | None,
    *,
    user_name: str = "default",
    subfolder: str | None = None,
    use_bundle_default: bool = True,
    custom_directory: Path | None = None,
    overwrite: bool = False,
    install_support_files: bool = True,
    import_setup: bool = False,
    download_setup_reference: bool = False,
) -> WorkflowInstallResult:
    artifact = artifact_path.expanduser().resolve()
    base_destination = workflow_destination(
        comfy_dir,
        user_name=user_name,
        custom_directory=custom_directory,
    )
    installed: list[Path] = []
    support_installed: list[Path] = []
    warnings: list[str] = []
    setup_profile: Path | None = None
    setup_reference: str | None = None

    if artifact.suffix.lower() == ".json":
        workflow = load_workflow_json(artifact)
        summary = summarize_workflow(workflow)
        destination = base_destination
        if subfolder:
            destination = destination.joinpath(*_safe_relative(subfolder).parts)
        destination.mkdir(parents=True, exist_ok=True)
        target = _unique_destination(destination / artifact.name, overwrite)
        target.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
        installed.append(target)
        warnings.extend(summary.warnings)
        return WorkflowInstallResult(
            tuple(installed), tuple(), tuple(sorted(set(warnings))), destination.resolve()
        )

    lower_name = artifact.name.lower()
    if lower_name.endswith(LEGACY_WORKFLOW_EXTENSION):
        manifest, workflow = read_legacy_workflow_bundle(artifact)
        destination = base_destination
        if subfolder:
            destination = destination.joinpath(*_safe_relative(subfolder).parts)
        destination.mkdir(parents=True, exist_ok=True)
        filename = str(manifest.get("source_filename") or f"{manifest.get('id', 'workflow')}.json")
        target = _unique_destination(destination / Path(filename).name, overwrite)
        target.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
        installed.append(target)
        warnings.extend(str(value) for value in manifest.get("warnings", []) if value)
        return WorkflowInstallResult(
            tuple(installed), tuple(), tuple(sorted(set(warnings))), destination.resolve()
        )

    if not (lower_name.endswith(WORKFLOW_PACK_EXTENSION) or artifact.suffix.lower() == ".zip"):
        raise WorkflowError("Unsupported workflow artifact. Use .json, .comfyworkflows, or a compatible .zip bundle.")

    try:
        with zipfile.ZipFile(artifact) as archive:
            manifest = _read_bundle_manifest(archive)
            default_subdir = str(manifest.get("default_install_subdirectory") or "")
            effective_subfolder = subfolder
            if effective_subfolder is None and use_bundle_default:
                effective_subfolder = default_subdir
            destination = base_destination
            if effective_subfolder:
                destination = destination.joinpath(*_safe_relative(effective_subfolder).parts)
            destination.mkdir(parents=True, exist_ok=True)

            workflow_checksums = {
                str(_safe_relative(str(entry.get("path") or ""), allow_empty=False)): str(entry.get("sha256") or "")
                for entry in manifest.get("workflows", [])
                if isinstance(entry, dict)
            }
            support_checksums = {
                str(_safe_relative(str(entry.get("path") or ""), allow_empty=False)): str(entry.get("sha256") or "")
                for entry in manifest.get("support_files", [])
                if isinstance(entry, dict)
            }
            workflow_paths = set(workflow_checksums)
            support_paths = set(support_checksums)

            for relative_text in sorted(workflow_paths | (support_paths if install_support_files else set())):
                relative = _safe_relative(relative_text, allow_empty=False)
                archive_name = f"workflows/{relative.as_posix()}"
                try:
                    payload = archive.read(archive_name)
                except KeyError as exc:
                    raise WorkflowError(f"Workflow bundle is missing: {archive_name}") from exc
                expected_checksum = workflow_checksums.get(relative_text) or support_checksums.get(relative_text)
                if expected_checksum and _sha256_bytes(payload) != expected_checksum:
                    raise WorkflowError(f"Bundle checksum failed: {relative_text}")
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                final_target = _unique_destination(target, overwrite)
                final_target.write_bytes(payload)
                if relative_text in workflow_paths:
                    load_workflow_json(final_target)
                    installed.append(final_target)
                else:
                    support_installed.append(final_target)

            if install_support_files:
                companion_entries = manifest.get("companion_files", [])
                if not companion_entries:
                    companion_entries = [
                        {"path": name, "sha256": None}
                        for name in manifest.get("companion_yaml", [])
                        if isinstance(name, str)
                    ]
                requirements_root = destination / ".comfyworkflow-requirements"
                for companion in companion_entries:
                    if not isinstance(companion, dict):
                        continue
                    member = str(companion.get("path") or "")
                    if not member or not _safe_member(member) or Path(member).suffix.lower() not in {".yaml", ".yml"}:
                        raise WorkflowError(f"Unsafe companion YAML path: {member!r}")
                    try:
                        companion_payload = archive.read(member)
                    except KeyError as exc:
                        raise WorkflowError(f"Workflow bundle is missing companion YAML: {member}") from exc
                    expected = companion.get("sha256")
                    if expected and _sha256_bytes(companion_payload) != expected:
                        raise WorkflowError(f"Companion YAML checksum failed: {member}")
                    try:
                        parsed_companion = yaml.safe_load(companion_payload.decode("utf-8"))
                    except (UnicodeDecodeError, yaml.YAMLError) as exc:
                        raise WorkflowError(f"Companion YAML is invalid: {member}") from exc
                    if parsed_companion is not None and not isinstance(parsed_companion, (dict, list)):
                        raise WorkflowError(f"Companion YAML must contain a mapping or list: {member}")
                    companion_target = _unique_destination(requirements_root / Path(member).name, overwrite)
                    companion_target.parent.mkdir(parents=True, exist_ok=True)
                    companion_target.write_bytes(companion_payload)
                    support_installed.append(companion_target)

                readme_name = str(manifest.get("readme_path") or "")
                if readme_name and _safe_member(readme_name) and readme_name in archive.namelist():
                    readme_payload = archive.read(readme_name)
                    expected_readme = manifest.get("readme_sha256")
                    if expected_readme and _sha256_bytes(readme_payload) != expected_readme:
                        raise WorkflowError("Bundle README checksum validation failed.")
                    readme_target = _unique_destination(destination / "README.md", overwrite)
                    readme_target.write_bytes(readme_payload)
                    support_installed.append(readme_target)
                metadata_target = _unique_destination(destination / ".comfyworkflow-bundle.json", overwrite)
                metadata_target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                support_installed.append(metadata_target)

            if import_setup:
                setup_profile, setup_reference, setup_warnings = _install_setup_from_bundle(
                    archive,
                    manifest,
                    download_reference=download_setup_reference,
                )
                warnings.extend(setup_warnings)
            else:
                setup = manifest.get("setup") if isinstance(manifest.get("setup"), dict) else {}
                setup_reference = str(setup.get("reference")) if setup.get("reference") else None
                if setup.get("embedded_path"):
                    warnings.append(
                        "This workflow bundle includes a .comfyuisetup profile. Enable setup import to add it to the profile library."
                    )
                elif setup_reference:
                    warnings.append(f"Recommended ComfyUI setup: {setup_reference}")
    except zipfile.BadZipFile as exc:
        raise WorkflowError(f"Invalid workflow bundle: {artifact}") from exc

    return WorkflowInstallResult(
        tuple(installed),
        tuple(support_installed),
        tuple(sorted(set(warnings))),
        destination.resolve(),
        setup_profile,
        setup_reference,
    )


def read_legacy_workflow_bundle(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = path.expanduser().resolve()
    try:
        with zipfile.ZipFile(bundle) as archive:
            if any(not _safe_member(name) for name in archive.namelist()):
                raise WorkflowError("Unsafe path found in legacy workflow bundle.")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            workflow = json.loads(archive.read("workflow.json").decode("utf-8"))
    except (FileNotFoundError, zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Invalid legacy workflow bundle: {bundle}") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != LEGACY_WORKFLOW_KIND:
        raise WorkflowError("The file is not a legacy portable workflow.")
    validate_workflow(workflow)
    return manifest, workflow


def copy_artifact_to_library(path: Path) -> Path:
    source = path.expanduser().resolve()
    lower = source.name.lower()
    if not (
        source.suffix.lower() == ".json"
        or lower.endswith(WORKFLOW_PACK_EXTENSION)
        or lower.endswith(LEGACY_WORKFLOW_EXTENSION)
        or source.suffix.lower() == ".zip"
    ):
        raise WorkflowError("Unsupported workflow artifact.")
    destination = user_workflow_library_dir() / source.name
    if source != destination:
        shutil.copy2(source, destination)
    return destination
