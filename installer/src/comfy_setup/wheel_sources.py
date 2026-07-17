from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.tags import Tag
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from .configuration import (
    ensure_editable_config,
    packaged_config_path,
    save_editable_config,
    user_config_file,
)
from .models import PlatformInfo

SOURCE_FILE_NAME = "wheel-sources.yaml"
TRUST_LABELS = {"Official", "3rd Party", "Custom/Local"}
SUPPORTED_KINDS = {
    "github-releases",
    "huggingface",
    "find-links",
    "direct-wheel",
    "local-directory",
    "package-index",
}
BLOCKED_HOST_MARKERS = (
    "open" + "ai.org",
    "internal",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    ".local",
    ".lan",
)


class WheelSourceError(RuntimeError):
    pass


@dataclass(slots=True)
class WheelSource:
    id: str
    enabled: bool
    label: str
    package: str
    kind: str
    location: str
    platforms: tuple[str, ...]
    architectures: tuple[str, ...]
    accelerators: tuple[str, ...]
    priority: int = 0
    notes: str = ""

    def matches(self, package_id: str, platform_info: PlatformInfo) -> bool:
        if not self.enabled or canonicalize_name(self.package) != canonicalize_name(package_id):
            return False
        if self.platforms and "all" not in self.platforms and platform_info.os_name not in self.platforms:
            return False
        architecture = _canonical_architecture(platform_info.architecture)
        if self.architectures and "all" not in self.architectures and architecture not in self.architectures:
            return False
        if self.accelerators and "all" not in self.accelerators and platform_info.accelerator not in self.accelerators:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "enabled": self.enabled,
            "label": self.label,
            "package": self.package,
            "kind": self.kind,
            "location": self.location,
            "platforms": list(self.platforms) or ["all"],
            "architectures": list(self.architectures) or ["all"],
            "accelerators": list(self.accelerators) or ["all"],
            "priority": self.priority,
            "notes": self.notes,
        }


@dataclass(slots=True)
class WheelCandidate:
    source: WheelSource
    name: str
    url: str
    sha256: str | None = None
    score: int = 0


@dataclass(slots=True)
class TargetWheelEnvironment:
    tags: frozenset[Tag]
    torch_version: Version | None
    torch_cuda: str | None
    torch_hip: str | None
    cxx11_abi: bool | None = None


def _sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        values = [str(part).strip() for part in value]
    else:
        values = [str(value).strip()]
    return tuple(part.lower() for part in values if part)


def _canonical_architecture(value: str) -> str:
    lowered = value.lower().replace("-", "_")
    aliases = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}
    return aliases.get(lowered, lowered)


def _public_https_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https":
        raise WheelSourceError("Remote wheel sources must use HTTPS.")
    host = (parsed.hostname or "").lower()
    if not host or any(marker in host for marker in BLOCKED_HOST_MARKERS):
        raise WheelSourceError(f"Private or unsupported wheel source host: {host or value}")
    return value


def _normalize_source_location(kind: str, value: str) -> str:
    raw = value.strip()
    if not raw:
        raise WheelSourceError("Wheel source location cannot be empty.")
    parsed = urllib.parse.urlparse(raw)

    if kind == "local-directory":
        if parsed.scheme in {"http", "https"}:
            raise WheelSourceError("A local-directory wheel source must be a filesystem directory.")
        local = Path(urllib.request.url2pathname(parsed.path) if parsed.scheme == "file" else raw).expanduser()
        if not local.exists():
            raise WheelSourceError(f"Local wheel directory does not exist: {local}")
        if not local.is_dir():
            raise WheelSourceError(f"Local wheel source is not a directory: {local}")
        return str(local.resolve())

    if kind == "direct-wheel":
        if parsed.scheme in {"http", "https"}:
            if not urllib.parse.unquote(parsed.path).lower().endswith(".whl"):
                raise WheelSourceError("A direct-wheel URL must point to a .whl file.")
            return _public_https_url(raw)
        local = Path(urllib.request.url2pathname(parsed.path) if parsed.scheme == "file" else raw).expanduser()
        if local.suffix.lower() != ".whl":
            raise WheelSourceError(f"Local direct-wheel source is not a .whl file: {local}")
        if not local.exists():
            raise WheelSourceError(f"Local wheel file does not exist: {local}")
        if not local.is_file():
            raise WheelSourceError(f"Local wheel source is not a file: {local}")
        return str(local.resolve())

    return _public_https_url(raw)


def default_source_path() -> Path:
    return packaged_config_path(SOURCE_FILE_NAME)


def user_source_path() -> Path:
    configured = os.environ.get("COMFYUI_SETUP_WHEEL_SOURCES")
    if configured:
        return Path(configured).expanduser().resolve()
    return user_config_file(SOURCE_FILE_NAME)


def ensure_user_source_file() -> Path:
    configured = os.environ.get("COMFYUI_SETUP_WHEEL_SOURCES")
    if not configured:
        return ensure_editable_config(SOURCE_FILE_NAME)
    destination = Path(configured).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(default_source_path(), destination)
    return destination


def _load_document(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise WheelSourceError(f"Could not read YAML wheel source registry {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WheelSourceError(f"Wheel source YAML must contain a mapping: {path}")
    return payload


def _write_document(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_source_file(path: Path) -> list[WheelSource]:
    payload = _load_document(path)
    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list):
        raise WheelSourceError(f"{path}: 'sources' must be a YAML list.")
    sources: list[WheelSource] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_sources, 1):
        if not isinstance(entry, dict):
            raise WheelSourceError(f"{path}: source #{index} must be a mapping.")
        source_id = str(entry.get("id") or "").strip()
        if not source_id:
            raise WheelSourceError(f"{path}: source #{index} has no id.")
        if source_id in seen:
            raise WheelSourceError(f"{path}: duplicate source id {source_id!r}.")
        seen.add(source_id)
        label = str(entry.get("label") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        package = str(entry.get("package") or "").strip()
        location = str(entry.get("location") or "").strip()
        if label not in TRUST_LABELS:
            raise WheelSourceError(f"{path}: source {source_id!r} has invalid label {label!r}.")
        if kind not in SUPPORTED_KINDS:
            raise WheelSourceError(f"{path}: source {source_id!r} has unsupported kind {kind!r}.")
        if not package:
            raise WheelSourceError(f"{path}: source {source_id!r} has no package.")
        sources.append(
            WheelSource(
                id=source_id,
                enabled=bool(entry.get("enabled", True)),
                label=label,
                package=package,
                kind=kind,
                location=_normalize_source_location(kind, location),
                platforms=_sequence(entry.get("platforms", ["all"])),
                architectures=_sequence(entry.get("architectures", ["all"])),
                accelerators=_sequence(entry.get("accelerators", ["all"])),
                priority=int(entry.get("priority", 0) or 0),
                notes=str(entry.get("notes") or "").strip(),
            )
        )
    return sources


def infer_source_kind(value: str) -> str:
    candidate = value.strip()
    parsed = urllib.parse.urlparse(candidate)
    lowered = candidate.lower()
    if parsed.scheme in {"http", "https"}:
        if urllib.parse.unquote(parsed.path).lower().endswith(".whl"):
            return "direct-wheel"
    else:
        path = Path(urllib.request.url2pathname(parsed.path) if parsed.scheme == "file" else candidate).expanduser()
        if path.suffix.lower() == ".whl":
            return "direct-wheel"
        if path.exists() or candidate.startswith(("./", "../", "~/", "/", "\\")):
            return "local-directory"
    if lowered.endswith(".whl"):
        return "direct-wheel"
    if "github.com" in lowered:
        return "github-releases"
    if "huggingface.co" in lowered or "hf.co" in lowered:
        return "huggingface"
    return "find-links"


def open_source_file(path: Path) -> None:
    path = path.expanduser().resolve()
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class WheelSourceRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or ensure_user_source_file()
        self.sources = parse_source_file(self.path)

    def reload(self) -> None:
        self.sources = parse_source_file(self.path)

    def matching(self, package_id: str, platform_info: PlatformInfo) -> list[WheelSource]:
        trust_rank = {"Custom/Local": 3, "Official": 2, "3rd Party": 1}
        return sorted(
            [source for source in self.sources if source.matches(package_id, platform_info)],
            key=lambda source: (
                trust_rank.get(source.label, 0),
                source.priority,
                source.kind in {"local-directory", "direct-wheel"},
                source.id,
            ),
            reverse=True,
        )

    def _payload(self) -> dict[str, Any]:
        return _load_document(self.path)

    def _save_sources(self, sources: list[WheelSource]) -> None:
        payload = self._payload()
        payload["sources"] = [source.to_dict() for source in sources]
        if self.path == user_config_file(SOURCE_FILE_NAME):
            save_editable_config(SOURCE_FILE_NAME, payload)
        else:
            _write_document(self.path, payload)
        self.reload()

    def add_source(
        self,
        *,
        package_id: str,
        location: str,
        platform_info: PlatformInfo,
        label: str = "Custom/Local",
        kind: str | None = None,
        notes: str = "Added from the ComfyUI Setup Manager UI.",
        priority: int = 60,
    ) -> WheelSource:
        kind = kind or infer_source_kind(location)
        if label not in TRUST_LABELS:
            raise WheelSourceError(f"Unknown source label: {label}")
        if kind not in SUPPORTED_KINDS:
            raise WheelSourceError(f"Unsupported source kind: {kind}")
        normalized_location = _normalize_source_location(kind, location)
        digest = hashlib.sha256(
            f"{canonicalize_name(package_id)}|{kind}|{normalized_location}".encode("utf-8")
        ).hexdigest()[:12]
        source = WheelSource(
            id=f"user-{canonicalize_name(package_id)}-{digest}",
            enabled=True,
            label=label,
            package=package_id,
            kind=kind,
            location=normalized_location,
            platforms=(platform_info.os_name,),
            architectures=(_canonical_architecture(platform_info.architecture),),
            accelerators=(platform_info.accelerator,),
            priority=priority,
            notes=notes,
        )
        sources = [existing for existing in self.sources if existing.id != source.id]
        sources.append(source)
        self._save_sources(sources)
        return source

    def register_local_backup(self, package_id: str, directory: Path, platform_info: PlatformInfo) -> None:
        self.add_source(
            package_id=package_id,
            location=str(directory.expanduser().resolve()),
            platform_info=platform_info,
            label="Custom/Local",
            kind="local-directory",
            notes="Locally built or downloaded wheels saved by ComfyUI Setup Manager.",
            priority=200,
        )


def target_environment(python_executable: Path) -> TargetWheelEnvironment:
    code = r'''
import json
from packaging.tags import sys_tags
payload = {"tags": [str(tag) for tag in sys_tags()], "torch": None, "cuda": None, "hip": None, "abi": None}
try:
    import torch
    payload["torch"] = torch.__version__.split("+")[0]
    payload["cuda"] = torch.version.cuda
    payload["hip"] = torch.version.hip
    probe = getattr(torch, "compiled_with_cxx11_abi", None)
    payload["abi"] = bool(probe()) if callable(probe) else None
except Exception:
    pass
print(json.dumps(payload))
'''
    completed = subprocess.run(
        [str(python_executable), "-c", code], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise WheelSourceError(f"Could not inspect target Python wheel tags: {completed.stderr.strip()}")
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    tags = frozenset(Tag(*value.split("-", 2)) for value in payload["tags"])
    try:
        torch_version = Version(payload["torch"]) if payload.get("torch") else None
    except InvalidVersion:
        torch_version = None
    return TargetWheelEnvironment(
        tags, torch_version, payload.get("cuda"), payload.get("hip"), payload.get("abi")
    )


def _torch_cuda_marker(name: str) -> tuple[int, int | None] | None:
    match = re.search(r"(?:^|[+_.-])cu(\d{2,3})(?:torch|[+_.-])", name.lower())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 2:
        return int(digits), None
    return int(digits[:-1]), int(digits[-1])


def _compact_torch_version(value: str) -> Version | None:
    if "." in value:
        try:
            return Version(value)
        except InvalidVersion:
            return None
    if len(value) < 2 or not value.isdigit():
        return None
    # Wheel collections commonly encode torch 2.12 as torch212 and 2.9 as torch29.
    major = value[0]
    minor = value[1:]
    try:
        return Version(f"{major}.{int(minor)}")
    except InvalidVersion:
        return None


def _torch_marker(name: str) -> tuple[Version, bool] | None:
    match = re.search(r"torch(\d+(?:\.\d+(?:\.\d+)?)?)(andhigher)?", name.lower())
    if not match:
        return None
    parsed = _compact_torch_version(match.group(1))
    return (parsed, bool(match.group(2))) if parsed else None


def _abi_marker(name: str) -> bool | None:
    match = re.search(r"cxx11abi(true|false)", name.lower())
    if not match:
        return None
    return match.group(1) == "true"


def wheel_compatibility_score(name: str, environment: TargetWheelEnvironment) -> int | None:
    decoded = urllib.parse.unquote(Path(name).name)
    try:
        _, _, _, tags = parse_wheel_filename(decoded)
    except Exception:
        return None
    if not environment.tags.intersection(tags):
        return None
    score = 100
    torch_marker = _torch_marker(decoded)
    if torch_marker:
        if not environment.torch_version:
            return None
        requested, and_higher = torch_marker
        target_pair = environment.torch_version.release[:2]
        requested_pair = requested.release[:2]
        if and_higher:
            if target_pair < requested_pair:
                return None
            score += 25
        elif target_pair != requested_pair:
            return None
        else:
            score += 40
    cuda_marker = _torch_cuda_marker(decoded)
    if cuda_marker:
        if not environment.torch_cuda:
            return None
        target_parts = environment.torch_cuda.split(".")
        target_major = int(target_parts[0])
        target_minor = int(target_parts[1]) if len(target_parts) > 1 else 0
        marker_major, marker_minor = cuda_marker
        if marker_major != target_major or (marker_minor is not None and marker_minor != target_minor):
            return None
        score += 40 if marker_minor is not None else 25
    abi_marker = _abi_marker(decoded)
    if abi_marker is not None and environment.cxx11_abi is not None:
        if abi_marker != environment.cxx11_abi:
            return None
        score += 20
    return score


def _json_request(url: str) -> Any:
    request = urllib.request.Request(
        _public_https_url(url),
        headers={"Accept": "application/vnd.github+json", "User-Agent": "comfyui-setup-manager/0.8.7"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _github_repo_parts(location: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(location)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise WheelSourceError(f"Invalid GitHub repository URL: {location}")
    return parts[0], parts[1].removesuffix(".git")


def _wheel_package_matches(name: str, package_id: str) -> bool:
    if not name.lower().endswith(".whl"):
        return False
    try:
        distribution, _, _, _ = parse_wheel_filename(urllib.parse.unquote(Path(name).name))
        return canonicalize_name(distribution) == canonicalize_name(package_id)
    except Exception:
        token = canonicalize_name(name.split("-", 1)[0])
        return token == canonicalize_name(package_id)


def _github_candidates(source: WheelSource, package_id: str) -> list[WheelCandidate]:
    owner, repo = _github_repo_parts(source.location)
    releases = _json_request(f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100")
    candidates: list[WheelCandidate] = []
    for release_index, release in enumerate(releases if isinstance(releases, list) else []):
        for asset in release.get("assets", []):
            name = str(asset.get("name", ""))
            if not _wheel_package_matches(name, package_id):
                continue
            digest = asset.get("digest")
            sha256 = digest.split(":", 1)[1] if isinstance(digest, str) and digest.startswith("sha256:") else None
            candidates.append(
                WheelCandidate(
                    source=source,
                    name=name,
                    url=str(asset.get("browser_download_url")),
                    sha256=sha256,
                    score=max(0, 20 - release_index),
                )
            )
    return candidates


def _huggingface_candidates(source: WheelSource, package_id: str) -> list[WheelCandidate]:
    parsed = urllib.parse.urlparse(source.location)
    parts = [part for part in parsed.path.strip("/").split("/") if part and part not in {"tree", "main"}]
    if len(parts) < 2:
        raise WheelSourceError(f"Invalid Hugging Face repository URL: {source.location}")
    repo_id = "/".join(parts[:2])
    payload = _json_request(f"https://huggingface.co/api/models/{repo_id}")
    candidates: list[WheelCandidate] = []
    for sibling in payload.get("siblings", []):
        filename = str(sibling.get("rfilename", ""))
        name = Path(filename).name
        if not _wheel_package_matches(name, package_id):
            continue
        url = f"https://huggingface.co/{repo_id}/resolve/main/{urllib.parse.quote(filename)}"
        candidates.append(WheelCandidate(source, name, url))
    return candidates


def _find_links_candidates(source: WheelSource, package_id: str) -> list[WheelCandidate]:
    request = urllib.request.Request(
        _public_https_url(source.location), headers={"User-Agent": "comfyui-setup-manager/0.8.7"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="ignore")
    candidates: list[WheelCandidate] = []
    for href in re.findall(r'href=["\']([^"\']+\.whl(?:#[^"\']*)?)["\']', html, flags=re.IGNORECASE):
        raw_href, _, fragment = href.partition("#")
        url = urllib.parse.urljoin(source.location, raw_href)
        name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)
        if not _wheel_package_matches(name, package_id):
            continue
        sha256 = fragment.removeprefix("sha256=") if fragment.startswith("sha256=") else None
        candidates.append(WheelCandidate(source, name, url, sha256=sha256))
    return candidates


def source_candidates(source: WheelSource, package_id: str) -> list[WheelCandidate]:
    if source.kind == "github-releases":
        return _github_candidates(source, package_id)
    if source.kind == "huggingface":
        return _huggingface_candidates(source, package_id)
    if source.kind == "find-links":
        return _find_links_candidates(source, package_id)
    if source.kind == "direct-wheel":
        location = source.location
        if Path(location).expanduser().is_file():
            return [WheelCandidate(source, Path(location).name, str(Path(location).expanduser().resolve()))]
        return [WheelCandidate(source, urllib.parse.unquote(Path(urllib.parse.urlparse(location).path).name), location)]
    if source.kind == "local-directory":
        return [WheelCandidate(source, wheel.name, str(wheel.resolve())) for wheel in Path(source.location).expanduser().glob("*.whl")]
    return []


def _candidate_satisfies_requirement(name: str, requirement_text: str | None) -> bool:
    if not requirement_text:
        return True
    try:
        requirement = Requirement(requirement_text)
        distribution, version, _, _ = parse_wheel_filename(
            urllib.parse.unquote(Path(name).name)
        )
    except (InvalidRequirement, InvalidWheelFilename, ValueError):
        # A malformed profile requirement must not make an unrelated wheel look
        # compatible. Let the source-build path report the profile error instead.
        return False
    if canonicalize_name(distribution) != canonicalize_name(requirement.name):
        return False
    return not requirement.specifier or requirement.specifier.contains(version, prereleases=True)


def select_candidates(
    sources: Iterable[WheelSource],
    package_id: str,
    environment: TargetWheelEnvironment,
    requirement: str | None = None,
) -> list[WheelCandidate]:
    trust_score = {"Official": 300, "3rd Party": 200, "Custom/Local": 100}
    candidates: list[WheelCandidate] = []
    for source in sources:
        try:
            discovered = source_candidates(source, package_id)
        except Exception:
            continue
        for candidate in discovered:
            if not _candidate_satisfies_requirement(candidate.name, requirement):
                continue
            compatibility = wheel_compatibility_score(candidate.name, environment)
            if compatibility is None:
                continue
            source_score = trust_score.get(source.label, 0) + source.priority
            if source.label == "Custom/Local" and source.kind in {"local-directory", "direct-wheel"}:
                source_score += 400
            candidate.score += source_score + compatibility
            candidates.append(candidate)
    return sorted(candidates, key=lambda candidate: (candidate.score, candidate.name), reverse=True)


def download_candidate(candidate: WheelCandidate, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / urllib.parse.unquote(candidate.name)
    source_path = Path(candidate.url).expanduser()
    if source_path.is_file():
        shutil.copy2(source_path, target)
    else:
        request = urllib.request.Request(
            _public_https_url(candidate.url), headers={"User-Agent": "comfyui-setup-manager/0.8.7"}
        )
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    if candidate.sha256:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest.lower() != candidate.sha256.lower():
            target.unlink(missing_ok=True)
            raise WheelSourceError(f"SHA-256 mismatch for {candidate.name}")
    return target
