from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from .configuration import load_editable_config
from .node_catalog import ensure_daily_node_catalog, load_node_catalog_cache

DEFAULT_REGISTRY_SEARCH_URL = "https://api.comfy.org/nodes/search"
USER_AGENT = "comfyui-setup-manager/0.8.7"
MAX_CATALOG_BYTES = 16 * 1024 * 1024

_GITHUB_RE = re.compile(
    r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CatalogEntry:
    manager_id: str | None
    repository: str | None
    title: str
    version: str | None
    source_id: str
    source_kind: str
    trust: str
    aliases: tuple[str, ...] = ()
    install_folder: str | None = None


@dataclass(frozen=True)
class NodeResolution:
    manager_id: str | None
    repository: str | None
    display_name: str | None
    manager_version: str | None
    ref: str | None
    source_id: str
    source_kind: str
    trust: str
    confidence: int
    install_folder: str | None = None


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _repository_basename(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    path = parsed.path if parsed.scheme else value
    name = Path(path.rstrip("/")).name
    return name[:-4] if name.lower().endswith(".git") else name


def _identity_forms(value: str) -> set[str]:
    raw = _compact(value)
    if not raw:
        return set()
    forms = {raw}
    # Manager-installed folders may use an abbreviated project name that is
    # not the Git repository basename (for example ``was-ns`` for
    # ``WAS Node Suite``). Preserve separator-aware acronym forms before the
    # normal prefix/suffix reduction so these identities can be matched
    # without hard-coded per-node aliases.
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    if len(tokens) >= 2:
        forms.add("".join(token[0] for token in tokens if token))
        forms.add(tokens[0] + "".join(token[0] for token in tokens[1:] if token))
    changed = True
    while changed:
        changed = False
        for item in list(forms):
            candidates = [item]
            for prefix in ("comfyui", "comfy"):
                if item.startswith(prefix) and len(item) > len(prefix) + 2:
                    candidates.append(item[len(prefix):])
            for suffix in ("comfyui", "customnodes", "customnode", "nodes", "node", "plugin"):
                if item.endswith(suffix) and len(item) > len(suffix) + 2:
                    candidates.append(item[:-len(suffix)])
            for candidate in candidates:
                if candidate and candidate not in forms:
                    forms.add(candidate)
                    changed = True
    return forms


def _normalized_repository(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().rstrip("/")
    if raw.lower().endswith(".git"):
        raw = raw[:-4]
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return f"https://github.com/{parts[0]}/{parts[1]}"


def _read_json_or_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return None


def _candidate_manager_dirs(comfy_dir: Path) -> list[Path]:
    roots = [
        comfy_dir / "user" / "__manager",
        comfy_dir / "user" / "default" / "ComfyUI-Manager",
    ]
    user_root = comfy_dir / "user"
    if user_root.is_dir():
        for child in user_root.iterdir():
            if child.is_dir() and child.name not in {"__manager", "default"}:
                roots.append(child / "ComfyUI-Manager")
    return roots




def _manager_channel_urls(comfy_dir: Path) -> list[str]:
    """Read HTTPS Manager channel URLs from the source installation.

    Manager accepts channel configuration in ``channels.list`` and older
    ``config.ini`` files.  We treat those URLs as metadata catalogs only; no
    code from a catalog is executed during export.
    """

    urls: list[str] = []
    for manager_root in _candidate_manager_dirs(comfy_dir):
        for candidate in (manager_root / "channels.list", manager_root / "config.ini"):
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in re.findall(r"https://[^\s,;]+", text):
                value = match.strip().strip("'\"[]()")
                if not value:
                    continue
                parsed = urllib.parse.urlparse(value)
                if parsed.scheme != "https" or not parsed.netloc:
                    continue
                if not parsed.path.lower().endswith(".json"):
                    value = value.rstrip("/") + "/custom-node-list.json"
                urls.append(value)
    return list(dict.fromkeys(urls))


def local_manager_catalog_paths(comfy_dir: Path, python: Path | None = None) -> list[Path]:
    """Return Manager/Registry catalog files already present in the installation."""

    candidates: list[Path] = []
    for manager_root in _candidate_manager_dirs(comfy_dir):
        cache = manager_root / "cache"
        if cache.is_dir():
            candidates.extend(sorted(cache.glob("*custom-node-list*.json")))
            candidates.extend(sorted(cache.glob("*nodes*.json")))
        candidates.extend(
            path for path in (
                manager_root / "custom-node-list.json",
                manager_root / "nodes.json",
            ) if path.is_file()
        )
    for manager in (
        comfy_dir / "custom_nodes" / "ComfyUI-Manager",
        comfy_dir / "custom_nodes" / "comfyui-manager",
    ):
        catalog = manager / "custom-node-list.json"
        if catalog.is_file():
            candidates.append(catalog)

    if python is not None and python.exists():
        script = (
            "import importlib.util, pathlib; "
            "s=importlib.util.find_spec('comfyui_manager'); "
            "print(pathlib.Path(s.origin).parent if s and s.origin else '')"
        )
        try:
            completed = subprocess.run(
                [str(python), "-c", script],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            package_dir = Path(completed.stdout.strip()) if completed.stdout.strip() else None
            if package_dir:
                catalog = package_dir / "custom-node-list.json"
                if catalog.is_file():
                    candidates.append(catalog)
        except (OSError, subprocess.TimeoutExpired):
            pass

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path.absolute())
        if key not in seen and path.is_file():
            unique.append(path)
            seen.add(key)
    return unique


def _entry_repository(item: dict[str, Any]) -> str | None:
    values: list[Any] = [item.get("repository"), item.get("reference")]
    files = item.get("files")
    if isinstance(files, list):
        values.extend(files)
    for value in values:
        if not isinstance(value, str):
            continue
        repository = _normalized_repository(value)
        if repository:
            return repository
    return None


def _extension_aliases(payload: Any, raw_entries: list[Any]) -> dict[int, list[str]]:
    """Map Manager extension class names back to custom-node entries.

    Legacy extension maps are keyed by repository URL, project/folder name, or
    another extension identifier.  Matching all normalized forms lets the
    resolver identify Manager-installed directories even when their folder
    name differs from the public project title.
    """

    if not isinstance(payload, dict):
        return {}
    extension_map = payload.get("extension_node_map")
    if not isinstance(extension_map, dict):
        return {}

    entry_forms: dict[int, set[str]] = {}
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            continue
        values: list[str] = []
        for key in ("title", "name", "id", "node_id", "manager_id", "folder", "install_folder"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        repository = _entry_repository(item)
        if repository:
            values.extend((repository, _repository_basename(repository)))
        entry_forms[index] = set().union(*(_identity_forms(value) for value in values)) if values else set()

    result: dict[int, list[str]] = {}
    for extension_id, details in extension_map.items():
        key = str(extension_id)
        key_repository = _normalized_repository(key)
        key_values = {key}
        if key_repository:
            key_values.update((key_repository, _repository_basename(key_repository)))
        key_forms = set().union(*(_identity_forms(value) for value in key_values))
        matches = [
            index for index, forms in entry_forms.items()
            if forms and key_forms and forms.intersection(key_forms)
        ]
        if len(matches) != 1:
            continue
        aliases: list[str] = [key]
        if isinstance(details, list) and details:
            classes = details[0]
            if isinstance(classes, list):
                aliases.extend(str(value) for value in classes if isinstance(value, str) and value.strip())
            elif isinstance(classes, str) and classes.strip():
                aliases.append(classes.strip())
            metadata = details[1] if len(details) > 1 and isinstance(details[1], dict) else {}
            pattern = metadata.get("nodename_pattern") if isinstance(metadata, dict) else None
            if isinstance(pattern, str) and pattern.strip():
                aliases.append(pattern.strip())
        result.setdefault(matches[0], []).extend(aliases)
    return result


def _entries_from_payload(
    payload: Any,
    *,
    source_id: str,
    source_kind: str,
    trust: str,
) -> list[CatalogEntry]:
    if isinstance(payload, dict):
        raw_entries = payload.get("custom_nodes")
        if not isinstance(raw_entries, list):
            raw_entries = payload.get("nodes")
        if not isinstance(raw_entries, list):
            raw_entries = payload.get("entries")
    elif isinstance(payload, list):
        raw_entries = payload
    else:
        raw_entries = None
    if not isinstance(raw_entries, list):
        return []

    extension_aliases = _extension_aliases(payload, raw_entries)
    output: list[CatalogEntry] = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            continue
        repository = _entry_repository(item)
        explicit_id = item.get("manager_id") or item.get("id") or item.get("node_id")
        manager_id = str(explicit_id).strip() if isinstance(explicit_id, str) and explicit_id.strip() else None
        title_value = item.get("title") or item.get("name") or manager_id or (repository and _repository_basename(repository))
        title = str(title_value or "").strip()
        latest = item.get("latest_version") if isinstance(item.get("latest_version"), dict) else {}
        version_value = (
            item.get("manager_version")
            or item.get("version")
            or latest.get("version")
            or latest.get("id")
        )
        version = str(version_value).strip() if version_value is not None and str(version_value).strip() else None
        aliases: list[str] = []
        for key in ("folder", "install_folder", "title", "name", "id", "node_id", "manager_id"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                aliases.append(value.strip())
        raw_aliases = item.get("aliases")
        if isinstance(raw_aliases, list):
            aliases.extend(str(value).strip() for value in raw_aliases if str(value).strip())
        aliases.extend(extension_aliases.get(index, []))
        if repository:
            aliases.append(_repository_basename(repository))
        install_folder_value = item.get("install_folder") or item.get("folder")
        install_folder = (
            str(install_folder_value).strip()
            if isinstance(install_folder_value, str) and install_folder_value.strip()
            else None
        )
        if manager_id or repository:
            output.append(
                CatalogEntry(
                    manager_id=manager_id,
                    repository=repository,
                    title=title or manager_id or repository or "Custom node",
                    version=version,
                    source_id=source_id,
                    source_kind=source_kind,
                    trust=trust,
                    aliases=tuple(dict.fromkeys(aliases)),
                    install_folder=install_folder,
                )
            )
    return output


def _node_file_hints(path: Path) -> tuple[set[str], set[str]]:
    aliases = {path.stem if path.is_file() else path.name}
    repositories: set[str] = set()
    if not path.is_dir():
        return aliases, repositories
    for name in (
        "pyproject.toml",
        "package.json",
        "setup.cfg",
        "setup.py",
        "README.md",
        "README.rst",
        "README.txt",
        "__init__.py",
        ".git/config",
    ):
        candidate = path / name
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")[:512_000]
        except OSError:
            continue
        for match in _GITHUB_RE.findall(text):
            repository = _normalized_repository(match)
            if repository:
                repositories.add(repository)
        if name == "pyproject.toml":
            try:
                import tomllib

                data = tomllib.loads(text)
                project = data.get("project", {}) if isinstance(data, dict) else {}
                tool = data.get("tool", {}) if isinstance(data, dict) else {}
                comfy = tool.get("comfy", {}) if isinstance(tool, dict) else {}
                for value in (
                    project.get("name") if isinstance(project, dict) else None,
                    comfy.get("DisplayName") if isinstance(comfy, dict) else None,
                ):
                    if isinstance(value, str) and value.strip():
                        aliases.add(value.strip())
            except Exception:
                pass
    return aliases, repositories


def _manager_snapshot_refs(comfy_dir: Path) -> dict[str, str]:
    """Read the newest Manager snapshots and map public repositories to commits."""

    result: dict[str, str] = {}
    snapshots: list[Path] = []
    for manager_root in _candidate_manager_dirs(comfy_dir):
        directory = manager_root / "snapshots"
        if directory.is_dir():
            snapshots.extend(path for path in directory.iterdir() if path.suffix.lower() in {".json", ".yaml", ".yml"})
    snapshots.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    for path in snapshots:
        payload = _read_json_or_yaml(path)
        if not isinstance(payload, dict):
            continue
        custom_nodes = payload.get("custom_nodes", payload)
        if not isinstance(custom_nodes, dict):
            continue
        git_nodes = custom_nodes.get("git_custom_nodes")
        if not isinstance(git_nodes, dict):
            continue
        for repository_value, details in git_nodes.items():
            repository = _normalized_repository(str(repository_value))
            if not repository or repository in result:
                continue
            commit = details.get("hash") if isinstance(details, dict) else details
            if isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{7,64}", commit.strip()):
                result[repository] = commit.strip()
    return result


class NodeSourceResolver:
    """Resolve installed custom nodes to trusted Manager, Registry, or Git sources.

    Resolution never executes catalog content. Local Manager caches and explicit
    YAML mappings are consulted before bounded HTTPS lookups. Ambiguous matches
    are rejected and left for the exporter's sanitized local-source fallback.
    """

    def __init__(
        self,
        comfy_dir: Path,
        *,
        python: Path | None = None,
        opener: Callable[..., Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.comfy_dir = comfy_dir
        self.python = python
        self.opener = opener or urllib.request.urlopen
        self.config = config if config is not None else load_editable_config("custom-node-sources.yaml")
        self.entries: list[CatalogEntry] = []
        self._loaded_source_ids: set[str] = set()
        self._snapshot_refs = _manager_snapshot_refs(comfy_dir)
        self._registry_queries: set[str] = set()
        self._load_explicit_mappings()
        self._load_manager_snapshot_entries()
        self._load_local_manager_catalogs()
        self._load_daily_catalog_cache()
        self._load_configured_local_catalogs()


    def _load_manager_snapshot_entries(self) -> None:
        """Treat Manager snapshots as first-party installed provenance.

        Manager snapshot keys are repository URLs.  They are especially useful
        after Manager has installed a node without retaining a nested ``.git``
        directory: the repository basename plus the recorded commit can still
        identify and reconstruct the public node without embedding its source.
        """

        for repository in sorted(self._snapshot_refs):
            title = _repository_basename(repository)
            self.entries.append(
                CatalogEntry(
                    manager_id=None,
                    repository=repository,
                    title=title,
                    version=None,
                    source_id="local-manager-snapshot",
                    source_kind="manager-snapshot",
                    trust="Installed ComfyUI Manager snapshot",
                    aliases=(title,),
                    install_folder=None,
                )
            )

    def _load_explicit_mappings(self) -> None:
        mappings = self.config.get("mappings", [])
        if isinstance(mappings, list):
            self.entries.extend(
                _entries_from_payload(
                    mappings,
                    source_id="user-mappings",
                    source_kind="mapping",
                    trust="Custom/Local",
                )
            )

    def _load_local_manager_catalogs(self) -> None:
        for index, path in enumerate(local_manager_catalog_paths(self.comfy_dir, self.python), start=1):
            payload = _read_json_or_yaml(path)
            self.entries.extend(
                _entries_from_payload(
                    payload,
                    source_id=f"local-manager-cache-{index}",
                    source_kind="manager-cache",
                    trust="Official",
                )
            )

    def _load_daily_catalog_cache(self) -> None:
        payload = load_node_catalog_cache()
        for source in payload.get("sources", []):
            if not isinstance(source, dict):
                continue
            source_id = str(source.get("id") or "daily-node-catalog")
            # A resolver may have loaded yesterday's cache during construction
            # and then refreshed it on demand. Replace entries from this source
            # so the current request sees today's data without accumulating
            # duplicate/ambiguous catalog records.
            self.entries = [entry for entry in self.entries if entry.source_id != source_id]
            self.entries.extend(
                _entries_from_payload(
                    source.get("payload"),
                    source_id=source_id,
                    source_kind=str(source.get("kind") or "daily-node-catalog"),
                    trust=str(source.get("trust") or "Official"),
                )
            )
            self._loaded_source_ids.add(source_id)

    def _load_configured_local_catalogs(self) -> None:
        sources = self.config.get("sources", [])
        if not isinstance(sources, list):
            return
        for source in sources:
            if not isinstance(source, dict) or not source.get("enabled", True):
                continue
            kind = str(source.get("kind") or "").strip()
            if kind not in {"file", "manager-list-file", "registry-file"}:
                continue
            source_id = str(source.get("id") or f"local-{len(self._loaded_source_ids) + 1}")
            location = source.get("path") or source.get("url")
            if not isinstance(location, str) or not location.strip():
                continue
            path = Path(os.path.expandvars(os.path.expanduser(location))).resolve()
            payload = _read_json_or_yaml(path)
            self.entries.extend(
                _entries_from_payload(
                    payload,
                    source_id=source_id,
                    source_kind=kind,
                    trust=str(source.get("trust") or "Custom/Local"),
                )
            )
            self._loaded_source_ids.add(source_id)

    def _network_enabled(self) -> bool:
        network = self.config.get("network", {})
        return bool(network.get("enabled", True)) if isinstance(network, dict) else True

    def _timeout(self) -> float:
        network = self.config.get("network", {})
        raw = network.get("timeout_seconds", 12) if isinstance(network, dict) else 12
        try:
            return max(1.0, min(30.0, float(raw)))
        except (TypeError, ValueError):
            return 12.0

    def _fetch_json(self, url: str) -> Any:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return None
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self.opener(request, timeout=self._timeout()) as response:
                data = response.read(MAX_CATALOG_BYTES + 1)
        except Exception:
            return None
        if len(data) > MAX_CATALOG_BYTES:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _configured_sources(self, kinds: set[str]) -> Iterable[dict[str, Any]]:
        sources = self.config.get("sources", [])
        if not isinstance(sources, list):
            return []
        return [
            source for source in sources
            if isinstance(source, dict)
            and source.get("enabled", True)
            and str(source.get("kind") or "") in kinds
        ]

    def _query_registry(self, aliases: Iterable[str], repositories: Iterable[str] = ()) -> None:
        if not self._network_enabled():
            return
        sources = list(self._configured_sources({"registry-search"}))
        if not sources:
            sources = [{"id": "comfy-registry", "kind": "registry-search", "url": DEFAULT_REGISTRY_SEARCH_URL, "trust": "Official"}]
        alias_values = list(dict.fromkeys(value.strip() for value in aliases if value and value.strip()))
        repository_values = list(dict.fromkeys(value.strip() for value in repositories if value and value.strip()))
        for source in sources:
            source_id = str(source.get("id") or "comfy-registry")
            base_url = str(source.get("url") or DEFAULT_REGISTRY_SEARCH_URL)
            requests: list[dict[str, Any]] = []
            requests.extend({"search": query, "limit": 50} for query in alias_values)
            requests.extend({"repository_url_search": repository, "limit": 50} for repository in repository_values)
            for parameters_dict in requests:
                cache_key = source_id + ":" + json.dumps(parameters_dict, sort_keys=True)
                if cache_key in self._registry_queries:
                    continue
                self._registry_queries.add(cache_key)
                parameters = urllib.parse.urlencode(parameters_dict)
                separator = "&" if "?" in base_url else "?"
                payload = self._fetch_json(base_url + separator + parameters)
                self.entries.extend(
                    _entries_from_payload(
                        payload,
                        source_id=source_id,
                        source_kind="registry-search",
                        trust=str(source.get("trust") or "Official"),
                    )
                )

    def _load_remote_manager_lists(self) -> None:
        if not self._network_enabled():
            return
        sources = list(self._configured_sources({"manager-list"}))
        if not sources:
            # Refresh the official Registry and maintained Manager node_db at
            # most once per UTC day. Explicit test/company Manager catalogs do
            # not trigger unrelated public requests.
            try:
                ensure_daily_node_catalog(timeout=self._timeout())
            except Exception:
                pass
            self._load_daily_catalog_cache()
        channel_sources = [
            {
                "id": f"manager-channel-{index}",
                "kind": "manager-list",
                "url": url,
                "trust": "Configured Manager Channel",
            }
            for index, url in enumerate(_manager_channel_urls(self.comfy_dir), start=1)
        ]
        for source in [*sources, *channel_sources]:
            source_id = str(source.get("id") or "comfyui-manager")
            if source_id in self._loaded_source_ids:
                continue
            payload = self._fetch_json(str(source.get("url") or DEFAULT_MANAGER_CATALOG_URL))
            self.entries.extend(
                _entries_from_payload(
                    payload,
                    source_id=source_id,
                    source_kind="manager-list",
                    trust=str(source.get("trust") or "Official"),
                )
            )
            self._loaded_source_ids.add(source_id)

    @staticmethod
    def _source_priority(entry: CatalogEntry) -> int:
        """Prefer installed/local provenance and the Registry over transition DB fallbacks.

        The same display name can exist in Registry, legacy, forked, and custom
        Manager channels.  Treating every equal text match as ambiguous caused
        well-known Manager-installed nodes to be labeled local/unrecorded.  A
        local Manager cache/snapshot describes the source installation most
        directly; otherwise the current Registry is authoritative, followed by
        maintained Manager catalogs.  Ambiguity remains an error only between
        equally authoritative candidates.
        """

        kind = entry.source_kind.lower()
        source_id = entry.source_id.lower()
        if kind == "mapping":
            return 100
        if kind in {"manager-cache", "manager-snapshot"}:
            return 95
        if kind in {"registry-cache", "registry-search", "registry-file"}:
            return 90
        if "aggregate" in source_id:
            return 85
        if "node-db-new" in source_id or "node-db-dev" in source_id:
            return 80
        if "node-db-legacy" in source_id:
            return 70
        if "node-db-forked" in source_id or "node-db-tutorial" in source_id:
            return 60
        if kind in {"manager-node-db-cache", "manager-list", "manager-list-file"}:
            return 75
        return 50

    @staticmethod
    def _score(entry: CatalogEntry, aliases: set[str], repositories: set[str]) -> int:
        if entry.repository and entry.repository in repositories:
            return 120
        entry_values = set(entry.aliases)
        entry_values.add(entry.title)
        if entry.manager_id:
            entry_values.add(entry.manager_id)
        if entry.repository:
            entry_values.add(_repository_basename(entry.repository))
        alias_raw = {_compact(value) for value in aliases if _compact(value)}
        entry_raw = {_compact(value) for value in entry_values if _compact(value)}
        if alias_raw & entry_raw:
            return 105
        alias_forms = set().union(*(_identity_forms(value) for value in aliases)) if aliases else set()
        entry_forms = set().union(*(_identity_forms(value) for value in entry_values)) if entry_values else set()
        common = alias_forms & entry_forms
        if common:
            longest = max(len(value) for value in common)
            return 96 if longest >= 8 else 90
        for left in alias_forms:
            for right in entry_forms:
                if min(len(left), len(right)) >= 7 and (left in right or right in left):
                    return 78
        return 0

    def _direct_hint_resolution(self, path: Path) -> NodeResolution | None:
        """Use an unambiguous repository declared inside the node itself.

        README/package metadata often survives Manager installs even when the
        checkout's ``.git`` directory does not.  We accept a repository only
        when its basename strongly matches the installed node identity; links
        to unrelated dependencies or examples are ignored.
        """

        aliases, repositories = _node_file_hints(path)
        alias_forms = set().union(*(_identity_forms(value) for value in aliases)) if aliases else set()
        matching: list[str] = []
        for repository in repositories:
            repository_forms = _identity_forms(_repository_basename(repository))
            common = alias_forms & repository_forms
            if common and max(len(value) for value in common) >= 7:
                matching.append(repository)
        matching = list(dict.fromkeys(matching))
        if len(matching) != 1:
            return None
        repository = matching[0]
        return NodeResolution(
            manager_id=None,
            repository=repository,
            display_name=None,
            manager_version=None,
            ref=self._snapshot_refs.get(repository),
            source_id="node-metadata",
            source_kind="node-metadata",
            trust="Installed node metadata",
            confidence=88,
            install_folder=None,
        )

    def _select(self, path: Path) -> NodeResolution | None:
        aliases, repositories = _node_file_hints(path)
        scored: list[tuple[int, CatalogEntry]] = []
        for entry in self.entries:
            score = self._score(entry, aliases, repositories)
            if score:
                scored.append((score, entry))
        if not scored:
            return None
        scored.sort(
            key=lambda item: (
                item[0],
                self._source_priority(item[1]),
                item[1].trust == "Official",
            ),
            reverse=True,
        )
        top_score, top = scored[0]
        if top_score < 78:
            return None
        top_repository = top.repository
        top_priority = self._source_priority(top)
        competing = [
            entry for score, entry in scored[1:]
            if score == top_score
            and self._source_priority(entry) == top_priority
            and (entry.repository or entry.manager_id) != (top_repository or top.manager_id)
        ]
        if competing:
            return None
        ref = self._snapshot_refs.get(top.repository or "") if top.repository else None
        return NodeResolution(
            manager_id=top.manager_id,
            repository=top.repository,
            display_name=top.title or None,
            manager_version=top.version,
            ref=ref,
            source_id=top.source_id,
            source_kind=top.source_kind,
            trust=top.trust,
            confidence=top_score,
            install_folder=top.install_folder,
        )

    def resolve(self, path: Path, *, allow_network: bool = True) -> NodeResolution | None:
        # Fast/offline sources first: explicit mappings and Manager caches.
        resolution = self._select(path)
        if resolution:
            return resolution
        direct = self._direct_hint_resolution(path)
        if direct or not allow_network:
            return direct

        # The complete Manager catalog is one bounded request and resolves most
        # legacy Git-installed nodes without one request per node.
        self._load_remote_manager_lists()
        resolution = self._select(path)
        if resolution:
            return resolution

        # Registry search is the final public lookup, especially for modern
        # Registry IDs whose install folder does not resemble the repository.
        aliases, repositories = _node_file_hints(path)
        self._query_registry(
            [*aliases, *(_repository_basename(value) for value in repositories)],
            repositories,
        )
        return self._select(path)
