from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import tempfile
import zipfile

import yaml
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from platformdirs import user_data_path

from .compatibility_tags import compatibility_from_profile, compatibility_label, pep440_version
from .environment_lock import (
    EMBEDDED_WHEELS_ROOT,
    ENVIRONMENT_LOCK_SCHEMA,
    lock_embedded_wheels,
    normalized_name,
    validate_source_tree_path,
    validate_wheel_payload,
)
from .package_sources import (
    PackageSourceError,
    validate_github_repository,
    validate_package_index,
    validate_requirement_line,
)


class ProfileError(RuntimeError):
    pass


PROFILE_KIND = "comfyui-setup-profile"
PROFILE_SCHEMA = 4
PROFILE_EXTENSION = ".comfyuisetup"
LEGACY_PROFILE_EXTENSIONS = (".comfysetup",)
EMBEDDED_ROOT = "embedded_plugins"
EMBEDDED_COMFYUI_ROOT = "embedded_comfyui"
COMFYUI_OVERLAY_ROOT = "comfyui_overlay"
DEPENDENCY_MANIFESTS_ROOT = "dependency_manifests"

# Never embed runtime data, models, secrets, environments, or build caches.
EMBEDDED_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "build",
    "dist",
    "models",
    "model",
    "checkpoints",
    "loras",
    "embeddings",
    "vae",
    "unet",
    "diffusion_models",
    "controlnet",
    "clip",
    "clip_vision",
    "text_encoders",
    "input",
    "output",
    "temp",
    "user",
    "tessdata",
}
EMBEDDED_EXCLUDED_SUFFIXES = {
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
    ".traineddata",
    ".npz",
    ".npy",
    ".pkl",
    ".pickle",
    ".pyc",
    ".pyo",
    ".log",
}
EMBEDDED_EXCLUDED_NAMES = {
    ".env",
    ".git",
    "extra_model_paths.yaml",
    "config.ini",
    "cookies.txt",
    "credentials.json",
    "secrets.json",
}
MAX_EMBEDDED_FILE_SIZE = 64 * 1024 * 1024
MAX_EDITABLE_MEMBER_SIZE = 2 * 1024 * 1024
GENERATED_PROFILE_MEMBERS = {"metadata.yaml"}
TEXT_MEMBER_SUFFIXES = {
    ".cfg", ".css", ".csv", ".html", ".ini", ".java", ".js", ".json",
    ".md", ".py", ".ps1", ".rst", ".rs", ".scss", ".sh", ".sql", ".toml",
    ".ts", ".txt", ".xml", ".yaml", ".yml",
}


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    profile: dict[str, Any]
    path: Path
    source: str

    @property
    def id(self) -> str:
        return str(self.profile["id"])

    @property
    def name(self) -> str:
        return str(self.profile["name"])

    @property
    def abi_tag(self) -> str:
        return str(self.compatibility.get("abi_tag") or "abi-unknown")

    @property
    def compatibility(self) -> dict[str, Any]:
        return compatibility_from_profile(self.profile)

    @property
    def compatibility_label(self) -> str:
        return compatibility_label(self.profile)

    @property
    def label(self) -> str:
        version = pep440_version(str(self.profile.get("version") or ""))
        display_name = str(self.profile.get("display_name") or self.name)
        if self.abi_tag not in display_name:
            display_name += f" [{self.abi_tag}]"
        suffix = f" — v{version}" if version else ""
        return f"{display_name}{suffix} [{self.source}]"


@dataclass(frozen=True, slots=True)
class ProfileBundleFile:
    """One archive member exposed by the safe profile editor."""

    name: str
    size: int
    language: str | None
    editable: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProfileBundleEditResult:
    path: Path
    member: str
    profile_id: str
    profile_name: str


def package_dir() -> Path:
    """Return the installed comfy_setup package directory."""
    return Path(__file__).resolve().parent


def installer_dir() -> Path:
    """Return the local installer project when running from source.

    Kept for compatibility with older internal callers. Built-in runtime data
    must live inside the Python package and must not depend on this path.
    """
    return package_dir().parents[1]


def project_root() -> Path:
    return installer_dir().parent


def builtin_profiles_dir() -> Path:
    """Return profiles bundled inside the installed wheel/package."""
    return package_dir() / "profiles"


def user_profiles_dir() -> Path:
    directory = user_data_path("comfyui-setup-manager", appauthor=False) / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _upgrade_node(node: dict[str, Any]) -> dict[str, Any]:
    upgraded = dict(node)
    source = upgraded.get("source")
    if isinstance(source, dict):
        return upgraded

    repository = upgraded.pop("repository", None)
    ref = upgraded.pop("ref", None)
    manager_id = upgraded.pop("manager_id", None)
    manager_version = upgraded.pop("manager_version", None)
    embedded_payload = upgraded.pop("embedded_payload", None)

    if embedded_payload:
        upgraded["source"] = {
            "type": "embedded",
            "payload": embedded_payload,
            "layout": "directory",
        }
    elif repository or manager_id:
        remote: dict[str, Any] = {"type": "remote"}
        if manager_id:
            remote["manager_id"] = manager_id
        if manager_version:
            remote["manager_version"] = manager_version
        if repository:
            remote["repository"] = repository
        if ref:
            remote["ref"] = ref
        upgraded["source"] = remote
    return upgraded


def _upgrade_profile(data: dict[str, Any]) -> dict[str, Any]:
    schema = data.get("schema_version")
    if schema not in {1, 2, 3, PROFILE_SCHEMA}:
        return data

    upgraded = dict(data)
    if schema in {1, 2, 3}:
        upgraded["schema_version"] = PROFILE_SCHEMA
        upgraded["kind"] = PROFILE_KIND
        upgraded.setdefault("extra_python_packages", [])
        upgraded.setdefault("dependency_repairs", {})
        upgraded.setdefault("environment_lock", {})
        upgraded.setdefault("dependency_manifests", [])
        upgraded.setdefault("dependency_resolution", {})
        upgraded.setdefault("export_metadata", {})
        upgraded.setdefault("models", [])
        upgraded.setdefault("workflows", [])
        upgraded.setdefault("asset_sources", {})

    upgraded.setdefault("models", [])
    upgraded.setdefault("workflows", [])
    upgraded.setdefault("dependency_repairs", {})
    upgraded.setdefault("environment_lock", {})
    upgraded.setdefault("dependency_manifests", [])
    upgraded.setdefault("dependency_resolution", {})
    upgraded.setdefault("asset_sources", {})

    upgraded["nodes"] = [
        _upgrade_node(node) if isinstance(node, dict) else node
        for node in upgraded.get("nodes", [])
    ]
    return upgraded


def _validate_embedded_payload(payload: str, node_id: str) -> None:
    path = PurePosixPath(payload)
    expected = PurePosixPath(EMBEDDED_ROOT) / node_id
    if path.is_absolute() or ".." in path.parts:
        raise ProfileError(f"Unsafe embedded payload path for node {node_id!r}.")
    if path != expected:
        raise ProfileError(
            f"Embedded node {node_id!r} must use payload {expected.as_posix()!r}."
        )


def validate_profile(data: dict[str, Any]) -> dict[str, Any]:
    data = _upgrade_profile(data)
    if data.get("schema_version") != PROFILE_SCHEMA:
        raise ProfileError(
            f"Unsupported profile schema: {data.get('schema_version')!r}; "
            f"expected {PROFILE_SCHEMA}."
        )
    if data.get("kind") != PROFILE_KIND:
        raise ProfileError("The file is not a ComfyUI Setup Manager profile.")
    if not data.get("id") or not data.get("name"):
        raise ProfileError("Profile is missing an id or name.")
    compatibility = data.get("compatibility")
    raw_version = str(data.get("version") or "").strip()
    if raw_version:
        normalized_version = pep440_version(raw_version)
        if normalized_version is None:
            if compatibility is not None:
                raise ProfileError(f"Profile version is not PEP 440 compliant: {raw_version!r}")
            data["legacy_version"] = raw_version
            normalized_version = "0+legacy"
        data["version"] = normalized_version
    if compatibility is not None:
        if not isinstance(compatibility, dict):
            raise ProfileError("Profile compatibility metadata must be a mapping.")
        abi_tag = str(compatibility.get("abi_tag") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", abi_tag):
            raise ProfileError(f"Profile ABI compatibility tag is invalid: {abi_tag!r}")
        compatibility_version = pep440_version(str(compatibility.get("profile_version") or ""))
        if compatibility_version is None:
            raise ProfileError("Profile compatibility metadata needs a PEP 440 profile_version.")
        if raw_version and compatibility_version != data["version"]:
            raise ProfileError("Profile version and compatibility.profile_version do not match.")
    comfy = data.get("comfyui")
    if not isinstance(comfy, dict) or not comfy.get("repository"):
        raise ProfileError("Profile is missing the ComfyUI repository.")
    try:
        validate_github_repository(str(comfy["repository"]))
        comfy_source = comfy.get("source", {})
        if isinstance(comfy_source, dict) and comfy_source.get("type") == "snapshot":
            payload = PurePosixPath(str(comfy_source.get("payload") or EMBEDDED_COMFYUI_ROOT))
            if payload != PurePosixPath(EMBEDDED_COMFYUI_ROOT):
                raise ProfileError("Embedded ComfyUI source must use the canonical payload path.")
        if isinstance(comfy_source, dict) and comfy_source.get("type") == "remote":
            overlay = comfy_source.get("overlay")
            if overlay is not None:
                if not isinstance(overlay, dict):
                    raise ProfileError("ComfyUI source overlay must be a mapping.")
                payload = PurePosixPath(str(overlay.get("payload") or COMFYUI_OVERLAY_ROOT))
                if payload != PurePosixPath(COMFYUI_OVERLAY_ROOT):
                    raise ProfileError("ComfyUI source overlay must use the canonical payload path.")
                for key in ("changed_files", "deleted_files"):
                    values = overlay.get(key, [])
                    if not isinstance(values, list):
                        raise ProfileError(f"ComfyUI overlay {key} must be a list.")
                    for value in values:
                        relative = PurePosixPath(str(value))
                        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                            raise ProfileError(f"Unsafe ComfyUI overlay path: {value!r}")
        official_repository = comfy.get("official_repository")
        if official_repository:
            validate_github_repository(str(official_repository))
        torch = data.get("torch", {})
        if isinstance(torch, dict):
            indexes = torch.get("indexes", {})
            if isinstance(indexes, dict):
                for index in indexes.values():
                    if index:
                        validate_package_index(str(index))
        for requirement in data.get("extra_python_packages", []):
            if isinstance(requirement, str):
                validate_requirement_line(requirement)
        environment_lock = data.get("environment_lock", {})
        if not isinstance(environment_lock, dict):
            raise ProfileError("Profile environment_lock must be a mapping.")
        if environment_lock:
            if environment_lock.get("schema_version") != ENVIRONMENT_LOCK_SCHEMA:
                raise ProfileError(
                    f"Unsupported environment lock schema: {environment_lock.get('schema_version')!r}; "
                    f"expected {ENVIRONMENT_LOCK_SCHEMA}."
                )
            if environment_lock.get("mode") == "exact" and environment_lock.get("complete") is not True:
                names = ", ".join(
                    str(value) for value in environment_lock.get("nonportable_packages", [])
                ) or "unknown packages"
                raise ProfileError(
                    "Exact environment lock is incomplete; nonportable packages: " + names
                )
            packages = environment_lock.get("packages", [])
            if not isinstance(packages, list):
                raise ProfileError("Profile environment_lock packages must be a list.")
            if environment_lock.get("mode") == "exact" and not packages:
                raise ProfileError("Exact environment lock must contain at least one installed distribution.")
            seen_locked: set[str] = set()
            for package in packages:
                if not isinstance(package, dict):
                    raise ProfileError("Every environment lock package must be a mapping.")
                name = normalized_name(str(package.get("name") or ""))
                version = str(package.get("version") or "").strip()
                if not name or not version:
                    raise ProfileError("Every environment lock package needs name and version fields.")
                if name in seen_locked:
                    raise ProfileError(f"Duplicate package in environment lock: {name}")
                seen_locked.add(name)
                requirement = str(package.get("requirement") or "").strip()
                if package.get("source") == "embedded-wheel":
                    try:
                        validate_wheel_payload(str(package.get("payload") or ""))
                    except ValueError as exc:
                        raise ProfileError(str(exc)) from exc
                    checksum = str(package.get("sha256") or "")
                    if checksum and not re.fullmatch(r"[0-9a-f]{64}", checksum):
                        raise ProfileError(f"Embedded wheel {name!r} has an invalid SHA-256 checksum.")
                elif package.get("source") == "source-tree":
                    try:
                        validate_source_tree_path(str(package.get("source_path") or ""))
                    except ValueError as exc:
                        raise ProfileError(str(exc)) from exc
                elif package.get("install", True):
                    if not requirement:
                        raise ProfileError(f"Installable locked package {name!r} needs a requirement.")
                    validate_requirement_line(requirement)
        repairs = data.get("dependency_repairs", {})
        if not isinstance(repairs, dict):
            raise ProfileError("Profile dependency_repairs must be a mapping.")
        for module_name, requirement in repairs.items():
            if not isinstance(module_name, str) or not module_name.strip():
                raise ProfileError("Every dependency repair needs a non-empty module name.")
            if not isinstance(requirement, str) or not requirement.strip():
                raise ProfileError(f"Dependency repair {module_name!r} needs a package requirement.")
            validate_requirement_line(requirement)
    except PackageSourceError as exc:
        raise ProfileError(str(exc)) from exc

    nodes = data.get("nodes", [])
    if not isinstance(nodes, list):
        raise ProfileError("Profile nodes must be a list.")

    seen_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ProfileError("Every profile node must be a JSON object.")
        node_id = str(node.get("id") or "")
        if not node_id or not node.get("name") or not node.get("folder"):
            raise ProfileError("Every node needs id, name, and folder fields.")
        if node_id in seen_ids:
            raise ProfileError(f"Duplicate node id in profile: {node_id}")
        seen_ids.add(node_id)

        source = node.get("source")
        if not isinstance(source, dict):
            raise ProfileError(f"Node {node_id!r} is missing its source descriptor.")
        source_type = source.get("type")
        if source_type == "remote":
            if not source.get("repository") and not source.get("manager_id"):
                raise ProfileError(
                    f"Remote node {node_id!r} needs a Git repository and/or Manager/Registry id."
                )
            repository = source.get("repository")
            if repository:
                try:
                    validate_github_repository(str(repository))
                except PackageSourceError as exc:
                    raise ProfileError(f"Node {node_id!r}: {exc}") from exc
        elif source_type == "embedded":
            payload = source.get("payload")
            if not isinstance(payload, str) or not payload:
                raise ProfileError(f"Embedded node {node_id!r} is missing a payload path.")
            _validate_embedded_payload(payload, node_id)
            if source.get("repository") or source.get("manager_id"):
                raise ProfileError(
                    f"Embedded node {node_id!r} cannot also declare a remote installer."
                )
            if source.get("layout", "directory") not in {"directory", "file"}:
                raise ProfileError(f"Embedded node {node_id!r} has an invalid layout.")
        elif source_type == "snapshot":
            payload = source.get("payload")
            if not isinstance(payload, str) or not payload:
                raise ProfileError(f"Snapshot node {node_id!r} is missing a payload path.")
            _validate_embedded_payload(payload, node_id)
            repository = source.get("repository")
            if repository:
                try:
                    validate_github_repository(str(repository))
                except PackageSourceError as exc:
                    raise ProfileError(f"Node {node_id!r}: {exc}") from exc
            if source.get("layout", "directory") not in {"directory", "file"}:
                raise ProfileError(f"Snapshot node {node_id!r} has an invalid layout.")
        else:
            raise ProfileError(
                f"Node {node_id!r} has unsupported source type {source_type!r}."
            )

    manifests = data.get("dependency_manifests", [])
    if not isinstance(manifests, list):
        raise ProfileError("Profile dependency_manifests must be a list.")
    for item in manifests:
        if not isinstance(item, dict):
            raise ProfileError("Every dependency manifest must be a mapping.")
        payload = PurePosixPath(str(item.get("payload") or ""))
        if (
            payload.is_absolute()
            or ".." in payload.parts
            or not payload.parts
            or payload.parts[0] != DEPENDENCY_MANIFESTS_ROOT
        ):
            raise ProfileError(f"Unsafe dependency manifest payload: {item.get('payload')!r}")

    for asset_key in ("models", "workflows"):
        assets = data.get(asset_key, [])
        if not isinstance(assets, list):
            raise ProfileError(f"Profile {asset_key} must be a list.")
        seen_assets: set[str] = set()
        for asset in assets:
            if not isinstance(asset, dict):
                raise ProfileError(f"Every profile {asset_key[:-1]} must be a YAML mapping.")
            asset_id = str(asset.get("id") or "")
            if not asset_id or not asset.get("name"):
                raise ProfileError(f"Every profile {asset_key[:-1]} needs id and name fields.")
            if asset_id in seen_assets:
                raise ProfileError(f"Duplicate {asset_key[:-1]} id in profile: {asset_id}")
            seen_assets.add(asset_id)
            url = asset.get("url")
            if url and not str(url).startswith(("https://", "http://", "file://")):
                raise ProfileError(f"Profile {asset_key[:-1]} {asset_id!r} has an unsupported source URL.")
    libraries = data.get("libraries", {})
    if not isinstance(libraries, dict):
        raise ProfileError("Profile libraries must be a YAML mapping.")

    for item in data.get("accelerated_packages", []):
        if not isinstance(item, dict):
            raise ProfileError("Every accelerated package must be a JSON object.")
        try:
            repository = item.get("source_repository")
            if repository:
                validate_github_repository(str(repository))
            package = item.get("package")
            if isinstance(package, str):
                validate_requirement_line(package)
            for requirement in item.get("build_requirements", []):
                if isinstance(requirement, str):
                    validate_requirement_line(requirement)
        except PackageSourceError as exc:
            raise ProfileError(f"Accelerated package {item.get('id', '<unknown>')!r}: {exc}") from exc
    return data


def _serializable_profile(profile: dict[str, Any]) -> dict[str, Any]:
    clean = copy.deepcopy(profile)
    for key in list(clean):
        if key.startswith("_"):
            clean.pop(key, None)
    return validate_profile(clean)


def _load_profile_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProfileError(f"Profile not found: {path}") from exc
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Invalid profile document: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("Profile root must be a mapping/object.")
    return data


def load_profile(path: Path) -> dict[str, Any]:
    profile_path = path.expanduser().resolve()
    if profile_path.name.lower().endswith((PROFILE_EXTENSION, *LEGACY_PROFILE_EXTENSIONS)):
        return read_profile_bundle(profile_path)
    if profile_path.suffix.lower() not in {".yaml", ".yml", ".json"}:
        raise ProfileError(
            f"Unsupported profile file: {profile_path.name}. Use .comfyuisetup, legacy .comfysetup, .yaml, or .json."
        )
    return validate_profile(_load_profile_document(profile_path))


def list_profiles() -> list[ProfileRecord]:
    records: list[ProfileRecord] = []
    seen: set[str] = set()
    for source, directory in (
        ("built-in", builtin_profiles_dir()),
        ("imported", user_profiles_dir()),
    ):
        if not directory.is_dir():
            continue
        paths = sorted([
            *directory.glob("*.yaml"),
            *directory.glob("*.yml"),
            *directory.glob("*.json"),
            *directory.glob(f"*{PROFILE_EXTENSION}"),
            *[path for ext in LEGACY_PROFILE_EXTENSIONS for path in directory.glob(f"*{ext}")],
        ])
        for path in paths:
            try:
                profile = load_profile(path)
            except ProfileError:
                continue
            profile_id = str(profile["id"])
            if profile_id in seen and source == "built-in":
                continue
            if profile_id in seen:
                records = [item for item in records if item.id != profile_id]
            records.append(ProfileRecord(profile, path, source))
            seen.add(profile_id)
    return sorted(records, key=lambda item: (item.source != "built-in", item.name.lower()))


def default_profile() -> dict[str, Any]:
    records = list_profiles()
    for record in records:
        if record.id == "vanilla-comfyui":
            return record.profile
    if records:
        return records[0].profile
    raise ProfileError("No profiles are installed.")


def get_profile(profile_id: str) -> dict[str, Any]:
    requested = profile_id.strip().lower()
    aliases = {
        "vanilla": "vanilla-comfyui",
        "default": "vanilla-comfyui",
        "badgids": "badgids-comfyui-complete",
        "badgids-complete": "badgids-comfyui-complete",
    }
    requested = aliases.get(requested, requested)

    records = list_profiles()
    for record in records:
        if record.id.lower() == requested:
            return record.profile

    # Friendly fallback for a displayed profile name.
    for record in records:
        if record.name.strip().lower() == requested:
            return record.profile

    available = ", ".join(record.id for record in records) or "none"
    raise ProfileError(
        f"Profile not found: {profile_id}. Available profile IDs: {available}"
    )


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def profile_member_language(name: str) -> str | None:
    """Return Textual's syntax language name for an archive member."""

    suffix = PurePosixPath(name).suffix.lower()
    return {
        ".bash": "bash", ".css": "css", ".go": "go", ".html": "html",
        ".java": "java", ".js": "javascript", ".json": "json", ".md": "markdown",
        ".py": "python", ".rs": "rust", ".sh": "bash", ".sql": "sql",
        ".toml": "toml", ".ts": "javascript", ".xml": "xml", ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix)


def _profile_bundle_infos(path: Path) -> tuple[Path, list[zipfile.ZipInfo]]:
    bundle = path.expanduser().resolve()
    try:
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ProfileError("Profile bundle contains duplicate archive members.")
            if any(not _safe_member(name) for name in names):
                raise ProfileError("Unsafe path found in profile bundle.")
            return bundle, infos
    except FileNotFoundError as exc:
        raise ProfileError(f"Profile bundle not found: {bundle}") from exc
    except zipfile.BadZipFile as exc:
        raise ProfileError(f"Invalid profile bundle: {bundle}") from exc


def list_profile_bundle_files(path: Path) -> list[ProfileBundleFile]:
    """List archive files and identify the UTF-8 members safe to edit."""

    bundle, infos = _profile_bundle_infos(path)
    result: list[ProfileBundleFile] = []
    with zipfile.ZipFile(bundle) as archive:
        for info in infos:
            if info.is_dir():
                continue
            reason = ""
            editable = True
            if info.filename in GENERATED_PROFILE_MEMBERS:
                editable = False
                reason = "generated automatically from the validated profile"
            elif info.file_size > MAX_EDITABLE_MEMBER_SIZE:
                editable = False
                reason = f"larger than the {MAX_EDITABLE_MEMBER_SIZE // (1024 * 1024)} MiB text-editor limit"
            else:
                try:
                    payload = archive.read(info)
                    text = payload.decode("utf-8")
                    if "\x00" in text:
                        raise UnicodeDecodeError("utf-8", payload, 0, 1, "NUL byte")
                except UnicodeDecodeError:
                    editable = False
                    reason = "binary or not UTF-8 text"
            result.append(ProfileBundleFile(
                name=info.filename,
                size=info.file_size,
                language=profile_member_language(info.filename),
                editable=editable,
                reason=reason,
            ))
    return result


def read_profile_bundle_file(path: Path, member: str) -> str:
    """Read one safe UTF-8 profile member without extracting the archive."""

    bundle, _ = _profile_bundle_infos(path)
    record = next((item for item in list_profile_bundle_files(bundle) if item.name == member), None)
    if record is None:
        raise ProfileError(f"Profile member was not found: {member!r}")
    if not record.editable and record.reason.startswith("binary"):
        raise ProfileError(f"Profile member is not editable text: {member!r}")
    with zipfile.ZipFile(bundle) as archive:
        try:
            return archive.read(member).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProfileError(f"Profile member is not valid UTF-8: {member!r}") from exc


def _payload_digest(entries: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, payload in sorted(entries, key=lambda item: item[0]):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_member_digest(archive: zipfile.ZipFile, member: str) -> str:
    digest = hashlib.sha256()
    with archive.open(member) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_safe_embedded_file(path: Path, relative: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if any(part.lower() in EMBEDDED_EXCLUDED_DIRS for part in relative.parts[:-1]):
        return False
    if path.name.lower() in EMBEDDED_EXCLUDED_NAMES:
        return False
    if path.suffix.lower() in EMBEDDED_EXCLUDED_SUFFIXES:
        return False
    try:
        return path.stat().st_size <= MAX_EMBEDDED_FILE_SIZE
    except OSError:
        return False


def _entries_from_path(source: Path) -> list[tuple[str, bytes]]:
    source = source.expanduser().resolve()
    if not source.exists():
        raise ProfileError(f"Embedded plugin source does not exist: {source}")
    entries: list[tuple[str, bytes]] = []
    if source.is_file():
        if not _is_safe_embedded_file(source, Path(source.name)):
            raise ProfileError(f"Embedded plugin file is not safe to include: {source.name}")
        entries.append((source.name, source.read_bytes()))
        return entries

    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not _is_safe_embedded_file(path, relative):
            continue
        entries.append((relative.as_posix(), path.read_bytes()))
    if not entries:
        raise ProfileError(f"Embedded plugin contains no portable source files: {source}")
    return entries


def _entries_from_data(data: dict[str, bytes]) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    for relative, payload in data.items():
        member = PurePosixPath(relative)
        if member.is_absolute() or ".." in member.parts or not member.parts:
            raise ProfileError(f"Unsafe embedded plugin member: {relative}")
        if any(part.lower() in EMBEDDED_EXCLUDED_DIRS for part in member.parts[:-1]):
            continue
        if member.name.lower() in EMBEDDED_EXCLUDED_NAMES:
            continue
        if Path(member.name).suffix.lower() in EMBEDDED_EXCLUDED_SUFFIXES:
            continue
        if len(payload) > MAX_EMBEDDED_FILE_SIZE:
            continue
        entries.append((member.as_posix(), bytes(payload)))
    if not entries:
        raise ProfileError("Embedded plugin data contains no portable source files.")
    return entries


def _verify_embedded_payloads(profile: dict[str, Any], archive: zipfile.ZipFile) -> None:
    names = set(archive.namelist())
    comfy_source = profile.get("comfyui", {}).get("source", {})
    if isinstance(comfy_source, dict) and comfy_source.get("type") == "snapshot":
        prefix = str(comfy_source.get("payload") or EMBEDDED_COMFYUI_ROOT).rstrip("/") + "/"
        members = [name for name in names if name.startswith(prefix) and not name.endswith("/")]
        if not members:
            raise ProfileError("Embedded ComfyUI source snapshot is missing.")
        entries = [(name[len(prefix):], archive.read(name)) for name in members]
        expected = comfy_source.get("sha256")
        if expected and _payload_digest(entries) != expected:
            raise ProfileError("Embedded ComfyUI source snapshot checksum failed.")
    if isinstance(comfy_source, dict) and comfy_source.get("type") == "remote":
        overlay = comfy_source.get("overlay")
        if isinstance(overlay, dict):
            prefix = str(overlay.get("payload") or COMFYUI_OVERLAY_ROOT).rstrip("/") + "/"
            expected_files = {
                str(value) for value in overlay.get("changed_files", []) if str(value)
            }
            members = {
                name[len(prefix):]
                for name in names
                if name.startswith(prefix) and not name.endswith("/")
            }
            if members != expected_files:
                raise ProfileError(
                    "ComfyUI source overlay members do not match its changed_files manifest."
                )
            entries = [(name, archive.read(prefix + name)) for name in sorted(members)]
            expected = str(overlay.get("sha256") or "")
            if expected and _payload_digest(entries) != expected:
                raise ProfileError("ComfyUI source overlay checksum failed.")
    for node in profile.get("nodes", []):
        source = node.get("source", {})
        if source.get("type") not in {"embedded", "snapshot"}:
            continue
        prefix = str(source["payload"]).rstrip("/") + "/"
        members = [name for name in names if name.startswith(prefix) and not name.endswith("/")]
        if not members:
            raise ProfileError(f"Embedded payload is missing for node {node['id']!r}.")
        entries = [(name[len(prefix):], archive.read(name)) for name in members]
        expected = source.get("sha256")
        if expected and _payload_digest(entries) != expected:
            raise ProfileError(f"Embedded payload checksum failed for node {node['id']!r}.")


def _verify_embedded_wheels(profile: dict[str, Any], archive: zipfile.ZipFile) -> None:
    names = set(archive.namelist())
    try:
        wheels = lock_embedded_wheels(profile.get("environment_lock"))
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc
    for package in wheels:
        payload = str(package["payload"])
        if payload not in names or payload.endswith("/"):
            raise ProfileError(f"Embedded wheel payload is missing for package {package['name']!r}.")
        expected = str(package.get("sha256") or "")
        if not expected:
            raise ProfileError(f"Embedded wheel {package['name']!r} is missing its SHA-256 checksum.")
        if _archive_member_digest(archive, payload) != expected:
            raise ProfileError(f"Embedded wheel checksum failed for package {package['name']!r}.")


def _verify_dependency_manifests(profile: dict[str, Any], archive: zipfile.ZipFile) -> None:
    names = set(archive.namelist())
    for item in profile.get("dependency_manifests", []):
        if not isinstance(item, dict):
            continue
        payload = str(item.get("payload") or "")
        if payload not in names or payload.endswith("/"):
            raise ProfileError(f"Dependency manifest payload is missing: {payload!r}")
        expected = str(item.get("sha256") or "")
        if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ProfileError(f"Dependency manifest {payload!r} is missing a valid SHA-256 checksum.")
        if _archive_member_digest(archive, payload) != expected:
            raise ProfileError(f"Dependency manifest checksum failed: {payload!r}")


def read_profile_bundle(path: Path, *, verify_payloads: bool = True) -> dict[str, Any]:
    bundle = path.expanduser().resolve()
    try:
        with zipfile.ZipFile(bundle) as archive:
            if any(not _safe_member(name) for name in archive.namelist()):
                raise ProfileError("Unsafe path found in profile bundle.")
            names = set(archive.namelist())
            if "profile.yaml" in names:
                document_name = "profile.yaml"
                try:
                    data = yaml.safe_load(archive.read(document_name).decode("utf-8"))
                except (UnicodeDecodeError, yaml.YAMLError) as exc:
                    raise ProfileError("profile.yaml is not valid UTF-8 YAML.") from exc
            elif "profile.json" in names:  # Legacy profile bundle compatibility.
                document_name = "profile.json"
                try:
                    data = json.loads(archive.read(document_name).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProfileError("profile.json is not valid UTF-8 JSON.") from exc
            else:
                raise ProfileError("Profile bundle does not contain profile.yaml.")
            if not isinstance(data, dict):
                raise ProfileError("Profile root must be a mapping/object.")
            profile = validate_profile(data)
            companion_map = {
                "environment-lock.yaml": "environment_lock",
                "custom_nodes.yml": "nodes",
                "dependency-manifests.yml": "dependency_manifests",
                "models.yaml": "models",
                "workflows.yaml": "workflows",
                "asset-sources.yaml": "asset_sources",
                "libraries.yaml": "libraries",
            }
            for member, key in companion_map.items():
                if member not in names:
                    continue
                try:
                    companion = yaml.safe_load(archive.read(member).decode("utf-8")) or {}
                except (UnicodeDecodeError, yaml.YAMLError) as exc:
                    raise ProfileError(f"{member} is not valid UTF-8 YAML.") from exc
                if isinstance(companion, dict) and key in companion:
                    profile[key] = companion[key]
                elif key in {"nodes", "dependency_manifests", "models", "workflows"} and isinstance(companion, list):
                    profile[key] = companion
                elif isinstance(companion, dict):
                    profile[key] = companion
            profile = validate_profile(profile)
            if verify_payloads:
                _verify_embedded_payloads(profile, archive)
                _verify_embedded_wheels(profile, archive)
                _verify_dependency_manifests(profile, archive)
    except FileNotFoundError as exc:
        raise ProfileError(f"Profile bundle not found: {bundle}") from exc
    except zipfile.BadZipFile as exc:
        raise ProfileError(f"Invalid profile bundle: {bundle}") from exc
    profile["_bundle_path"] = str(bundle)
    return profile


def import_profile(path: Path) -> ProfileRecord:
    source = path.expanduser().resolve()
    profile_id: str
    if source.suffix.lower() in {".yaml", ".yml", ".json"}:
        profile = load_profile(source)
        profile_id = str(profile["id"])
        destination = user_profiles_dir() / f"{profile_id}.yaml"
        for obsolete in (
            user_profiles_dir() / f"{profile_id}.yml",
            user_profiles_dir() / f"{profile_id}.json",
            user_profiles_dir() / f"{profile_id}{PROFILE_EXTENSION}",
            *[user_profiles_dir() / f"{profile_id}{ext}" for ext in LEGACY_PROFILE_EXTENSIONS],
        ):
            obsolete.unlink(missing_ok=True)
        destination.write_text(
            yaml.safe_dump(_serializable_profile(profile), sort_keys=False, allow_unicode=True, width=110),
            encoding="utf-8",
        )
        return ProfileRecord(profile, destination, "imported")
    if source.name.lower().endswith((PROFILE_EXTENSION, *LEGACY_PROFILE_EXTENSIONS)):
        profile = read_profile_bundle(source)
        profile_id = str(profile["id"])
        destination = user_profiles_dir() / f"{profile_id}{PROFILE_EXTENSION}"
        for obsolete in (
            user_profiles_dir() / f"{profile_id}.yaml",
            user_profiles_dir() / f"{profile_id}.yml",
            user_profiles_dir() / f"{profile_id}.json",
        ):
            obsolete.unlink(missing_ok=True)
        if source != destination:
            shutil.copy2(source, destination)
        profile = read_profile_bundle(destination)
        return ProfileRecord(profile, destination, "imported")
    raise ProfileError(
        f"Unsupported profile file: {source.name}. Use .comfyuisetup, legacy .comfysetup, .yaml, or .json."
    )


def remove_imported_profile(profile_id: str) -> bool:
    removed = False
    for path in (
        user_profiles_dir() / f"{profile_id}.yaml",
        user_profiles_dir() / f"{profile_id}.yml",
        user_profiles_dir() / f"{profile_id}.json",
        user_profiles_dir() / f"{profile_id}{PROFILE_EXTENSION}",
        *[user_profiles_dir() / f"{profile_id}{extension}" for extension in LEGACY_PROFILE_EXTENSIONS],
    ):
        if path.exists():
            path.unlink()
            removed = True
    return removed


def write_profile_bundle(
    profile: dict[str, Any],
    output_path: Path,
    *,
    notes: str | None = None,
) -> Path:
    working = copy.deepcopy(profile)
    embedded_paths = working.pop("_embedded_plugin_paths", {}) or {}
    embedded_comfyui_path = working.pop("_embedded_comfyui_path", None)
    comfyui_overlay_paths = working.pop("_comfyui_overlay_paths", {}) or {}
    dependency_manifest_paths = working.pop("_dependency_manifest_paths", {}) or {}
    embedded_data = working.pop("_embedded_plugin_data", {}) or {}
    embedded_wheel_paths = working.pop("_embedded_wheel_paths", {}) or {}
    embedded_wheel_data = working.pop("_embedded_wheel_data", {}) or {}
    source_bundle_value = working.pop("_bundle_path", None)
    source_bundle = Path(str(source_bundle_value)).expanduser().resolve() if source_bundle_value else None
    source_archive: zipfile.ZipFile | None = None
    if source_bundle and source_bundle.is_file():
        source_archive = zipfile.ZipFile(source_bundle)

    nodes_by_id = {
        str(node["id"]): node
        for node in working.get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    }
    payload_entries: dict[str, list[tuple[str, bytes]]] = {}
    comfyui_entries: list[tuple[str, bytes]] = []
    overlay_entries: list[tuple[str, bytes]] = []
    manifest_entries: dict[str, bytes] = {}
    comfy_source = working.get("comfyui", {}).get("source", {})
    if isinstance(comfy_source, dict) and comfy_source.get("type") == "snapshot":
        if not embedded_comfyui_path:
            raise ProfileError("Embedded ComfyUI source snapshot has no source path to write.")
        comfyui_entries = _entries_from_path(Path(str(embedded_comfyui_path)))
        comfy_source["payload"] = EMBEDDED_COMFYUI_ROOT
        comfy_source["sha256"] = _payload_digest(comfyui_entries)
    if isinstance(comfy_source, dict) and comfy_source.get("type") == "remote":
        overlay = comfy_source.get("overlay")
        if isinstance(overlay, dict):
            changed_files = [str(value) for value in overlay.get("changed_files", [])]
            for relative in changed_files:
                source_value = comfyui_overlay_paths.get(relative)
                if source_value:
                    source_path = Path(str(source_value)).expanduser().resolve()
                    if not source_path.is_file() or source_path.is_symlink():
                        raise ProfileError(f"ComfyUI overlay source is not a regular file: {source_path}")
                    content = source_path.read_bytes()
                else:
                    archive_member = f"{COMFYUI_OVERLAY_ROOT}/{relative}"
                    if source_archive is None or archive_member not in source_archive.namelist():
                        raise ProfileError(f"ComfyUI overlay file has no source payload: {relative}")
                    content = source_archive.read(archive_member)
                overlay_entries.append((relative, content))
            overlay["payload"] = COMFYUI_OVERLAY_ROOT
            overlay["sha256"] = _payload_digest(overlay_entries)

    manifests_by_payload = {
        str(item.get("payload")): item
        for item in working.get("dependency_manifests", [])
        if isinstance(item, dict) and item.get("payload")
    }
    for payload, item in manifests_by_payload.items():
        source_value = dependency_manifest_paths.get(payload)
        if source_value:
            source_path = Path(str(source_value)).expanduser().resolve()
            if not source_path.is_file() or source_path.is_symlink():
                raise ProfileError(f"Dependency manifest is not a regular file: {source_path}")
            content = source_path.read_bytes()
        elif source_archive is not None and payload in source_archive.namelist():
            content = source_archive.read(payload)
        else:
            raise ProfileError(f"Dependency manifest has no source payload: {payload}")
        item["sha256"] = hashlib.sha256(content).hexdigest()
        manifest_entries[payload] = content
    for node_id, node in nodes_by_id.items():
        source = node.get("source", {})
        if source.get("type") not in {"embedded", "snapshot"}:
            continue
        if node_id in embedded_paths:
            entries = _entries_from_path(Path(embedded_paths[node_id]))
        elif node_id in embedded_data:
            entries = _entries_from_data(embedded_data[node_id])
        elif source_archive is not None:
            prefix = str(source.get("payload") or f"{EMBEDDED_ROOT}/{node_id}").rstrip("/") + "/"
            members = [
                name for name in source_archive.namelist()
                if name.startswith(prefix) and not name.endswith("/")
            ]
            if not members:
                raise ProfileError(f"Embedded node {node_id!r} has no source payload to write.")
            entries = _entries_from_data(
                {name[len(prefix):]: source_archive.read(name) for name in members}
            )
        else:
            raise ProfileError(
                f"Embedded node {node_id!r} has no source payload to write."
            )
        source["payload"] = f"{EMBEDDED_ROOT}/{node_id}"
        source["sha256"] = _payload_digest(entries)
        payload_entries[node_id] = entries

    wheel_sources: dict[str, Path | bytes] = {}
    try:
        for package in lock_embedded_wheels(working.get("environment_lock")):
            payload = str(package["payload"])
            source_value = embedded_wheel_paths.get(payload)
            if source_value:
                source_path = Path(str(source_value)).expanduser().resolve()
                if not source_path.is_file() or source_path.suffix.lower() != ".whl":
                    raise ProfileError(f"Embedded wheel source does not exist or is not a wheel: {source_path}")
                package["sha256"] = _file_digest(source_path)
                wheel_sources[payload] = source_path
            elif payload in embedded_wheel_data:
                payload_bytes = bytes(embedded_wheel_data[payload])
                package["sha256"] = hashlib.sha256(payload_bytes).hexdigest()
                wheel_sources[payload] = payload_bytes
            elif source_archive is not None and payload in source_archive.namelist():
                payload_bytes = source_archive.read(payload)
                package["sha256"] = hashlib.sha256(payload_bytes).hexdigest()
                wheel_sources[payload] = payload_bytes
            else:
                raise ProfileError(f"Embedded wheel {package['name']!r} has no payload to write.")

        serializable = _serializable_profile(working)
    finally:
        if source_archive is not None:
            source_archive.close()
    output = output_path.expanduser()
    if not output.name.lower().endswith((PROFILE_EXTENSION, *LEGACY_PROFILE_EXTENSIONS)):
        output = output.with_name(output.name + PROFILE_EXTENSION)
    output.parent.mkdir(parents=True, exist_ok=True)

    environment_lock = serializable.get("environment_lock", {})
    profile_document = copy.deepcopy(serializable)
    # Large/authoritative companion documents live in one place only.  In
    # particular, custom nodes are declared exclusively in custom_nodes.yml so
    # the profile never drifts between duplicate node inventories.
    profile_document.pop("environment_lock", None)
    profile_document.pop("nodes", None)
    profile_document.pop("dependency_manifests", None)
    profile_bytes = yaml.safe_dump(
        profile_document, sort_keys=False, allow_unicode=True, width=110
    ).encode("utf-8")
    digest = hashlib.sha256(profile_bytes).hexdigest()
    metadata = {
        "format": PROFILE_KIND,
        "schema_version": PROFILE_SCHEMA,
        "profile_id": serializable["id"],
        "profile_name": serializable["name"],
        "profile_version": serializable.get("version"),
        "compatibility": compatibility_from_profile(serializable),
        "sha256": digest,
        "remote_node_count": sum(
            1 for node in serializable.get("nodes", [])
            if node.get("source", {}).get("type") == "remote"
        ),
        "embedded_node_count": len(payload_entries),
        "embedded_comfyui_source": bool(comfyui_entries),
        "comfyui_overlay_file_count": len(overlay_entries),
        "dependency_manifest_count": len(manifest_entries),
        "environment_lock_package_count": len(environment_lock.get("packages", [])) if isinstance(environment_lock, dict) else 0,
        "embedded_wheel_count": len(wheel_sources),
    }
    readme = notes if notes is not None else (
        f"{serializable['name']}\n\n"
        f"Compatibility: {compatibility_label(serializable)}\n"
        f"ABI tag: {compatibility_from_profile(serializable).get('abi_tag')}\n\n"
        "Import this portable .comfyuisetup profile into ComfyUI Setup Manager. Public custom nodes "
        "are represented by Comfy Registry/Manager IDs and Git repositories in custom_nodes.yml. "
        "The ComfyUI checkout is reconstructed from its repository plus a compact overlay containing "
        "only changed core files. Dependency manifests and an exact environment lock are included; "
        "virtual environments, installed libraries, public node source trees, models, credentials, "
        "outputs, and machine-specific paths are not archived.\n"
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("profile.yaml", profile_bytes)
        archive.writestr(
            "environment-lock.yaml",
            yaml.safe_dump(
                {"environment_lock": environment_lock},
                sort_keys=False,
                allow_unicode=True,
                width=110,
            ),
        )
        archive.writestr(
            "custom_nodes.yml",
            yaml.safe_dump({"nodes": serializable.get("nodes", [])}, sort_keys=False, allow_unicode=True, width=110),
        )
        archive.writestr(
            "dependency-manifests.yml",
            yaml.safe_dump(
                {"dependency_manifests": serializable.get("dependency_manifests", [])},
                sort_keys=False,
                allow_unicode=True,
                width=110,
            ),
        )
        archive.writestr(
            "models.yaml",
            yaml.safe_dump({"models": serializable.get("models", [])}, sort_keys=False, allow_unicode=True, width=110),
        )
        archive.writestr(
            "workflows.yaml",
            yaml.safe_dump({"workflows": serializable.get("workflows", [])}, sort_keys=False, allow_unicode=True, width=110),
        )
        archive.writestr(
            "asset-sources.yaml",
            yaml.safe_dump({"asset_sources": serializable.get("asset_sources", {})}, sort_keys=False, allow_unicode=True, width=110),
        )
        archive.writestr(
            "libraries.yaml",
            yaml.safe_dump({"libraries": serializable.get("libraries", {})}, sort_keys=False, allow_unicode=True, width=110),
        )
        archive.writestr(
            "metadata.yaml",
            yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True, width=110),
        )
        archive.writestr("README.txt", readme)
        if comfyui_entries:
            prefix = f"{EMBEDDED_COMFYUI_ROOT}/"
            for relative, payload in comfyui_entries:
                archive.writestr(prefix + relative, payload)
        if overlay_entries:
            prefix = f"{COMFYUI_OVERLAY_ROOT}/"
            for relative, payload in overlay_entries:
                archive.writestr(prefix + relative, payload)
        for payload, content in manifest_entries.items():
            archive.writestr(payload, content)
        for node_id, entries in payload_entries.items():
            prefix = f"{EMBEDDED_ROOT}/{node_id}/"
            for relative, payload in entries:
                archive.writestr(prefix + relative, payload)
        for payload, source in wheel_sources.items():
            validate_wheel_payload(payload)
            if isinstance(source, Path):
                archive.write(source, arcname=payload)
            else:
                archive.writestr(payload, source)
    return output.resolve()


def _copy_profile_archive_with_replacement(
    source_path: Path,
    destination_path: Path,
    member: str,
    payload: bytes,
) -> None:
    """Create a raw edited archive without extracting any member to disk."""

    replaced = False
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(destination_path, "w") as destination:
        destination.comment = source.comment
        for info in source.infolist():
            if info.filename == member:
                destination.writestr(copy.copy(info), payload)
                replaced = True
                continue
            if info.is_dir():
                destination.writestr(copy.copy(info), b"")
                continue
            with source.open(info) as input_stream, destination.open(copy.copy(info), "w", force_zip64=True) as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
    if not replaced:
        raise ProfileError(f"Profile member was not found: {member!r}")


def _preserve_additional_profile_members(source_path: Path, rebuilt_path: Path) -> None:
    """Keep forward-compatible/support files unknown to the canonical writer."""

    with zipfile.ZipFile(rebuilt_path) as rebuilt:
        generated = set(rebuilt.namelist())
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(rebuilt_path, "a") as destination:
        for info in source.infolist():
            if info.filename in generated:
                continue
            if info.is_dir():
                destination.writestr(copy.copy(info), b"")
                continue
            with source.open(info) as input_stream, destination.open(copy.copy(info), "w", force_zip64=True) as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)


def edit_profile_bundle_file(
    path: Path,
    member: str,
    text: str,
    *,
    allow_profile_id_change: bool = False,
) -> ProfileBundleEditResult:
    """Transactionally replace one text member and rebuild bundle integrity."""

    bundle, _ = _profile_bundle_infos(path)
    original = read_profile_bundle(bundle)
    record = next((item for item in list_profile_bundle_files(bundle) if item.name == member), None)
    if record is None:
        raise ProfileError(f"Profile member was not found: {member!r}")
    if not record.editable:
        raise ProfileError(f"Profile member cannot be edited: {member!r} ({record.reason}).")
    payload = text.encode("utf-8")
    if len(payload) > MAX_EDITABLE_MEMBER_SIZE:
        raise ProfileError(
            f"Edited profile member exceeds the {MAX_EDITABLE_MEMBER_SIZE // (1024 * 1024)} MiB text-editor limit."
        )
    suffix = PurePosixPath(member).suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            yaml.safe_load(text)
        elif suffix == ".json":
            json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Edited {member} is not valid {suffix.lstrip('.').upper()}: {exc}") from exc

    mode = bundle.stat().st_mode
    with tempfile.TemporaryDirectory(prefix=".comfy-profile-edit-", dir=bundle.parent) as temporary:
        work = Path(temporary)
        raw = work / "edited.comfyuisetup"
        rebuilt = work / "rebuilt.comfyuisetup"
        _copy_profile_archive_with_replacement(bundle, raw, member, payload)
        edited_profile = read_profile_bundle(raw, verify_payloads=False)
        if not allow_profile_id_change and edited_profile["id"] != original["id"]:
            raise ProfileError(
                "The profile id cannot be changed in place because the library filename and removal key depend on it. "
                "Edit an external copy with the CLI --allow-id-change option, then import it as a new profile."
            )
        notes = None
        with zipfile.ZipFile(raw) as archive:
            if "README.txt" in archive.namelist():
                notes = archive.read("README.txt").decode("utf-8")
        write_profile_bundle(edited_profile, rebuilt, notes=notes)
        _preserve_additional_profile_members(raw, rebuilt)
        validated = read_profile_bundle(rebuilt)
        rebuilt.chmod(mode)
        rebuilt.replace(bundle)

    return ProfileBundleEditResult(
        path=bundle,
        member=member,
        profile_id=str(validated["id"]),
        profile_name=str(validated["name"]),
    )


def export_builtin_profile(profile_id: str, output_path: Path) -> Path:
    return write_profile_bundle(get_profile(profile_id), output_path)


def copy_profile_yaml(profile: dict[str, Any], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.lower() not in {".yaml", ".yml"}:
        destination = destination.with_suffix(".yaml")
    destination.write_text(
        yaml.safe_dump(_serializable_profile(profile), sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8",
    )
    return destination


def copy_profile_json(profile: dict[str, Any], destination: Path) -> Path:
    """Legacy API name; profile configuration is now written as YAML."""
    return copy_profile_yaml(profile, destination)
