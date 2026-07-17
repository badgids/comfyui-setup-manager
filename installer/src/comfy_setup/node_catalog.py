from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from platformdirs import user_cache_path


CATALOG_SCHEMA = 2
REGISTRY_LIST_URL = "https://api.comfy.org/nodes"
LEGACY_MANAGER_BASE = "https://raw.githubusercontent.com/Comfy-Org/ComfyUI-Manager/main"
LEGACY_NODE_DB_BASE = f"{LEGACY_MANAGER_BASE}/node_db"
LEGACY_CHANNELS = ("dev", "new", "legacy", "forked", "tutorial")
USER_AGENT = "comfyui-setup-manager/0.8.7"
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
REGISTRY_PAGE_SIZE = 100
MAX_REGISTRY_PAGES = 1000
ProgressCallback = Callable[[str], None]


def node_catalog_cache_path() -> Path:
    override = os.environ.get("COMFYUI_SETUP_MANAGER_NODE_CATALOG")
    if override:
        return Path(override).expanduser()
    return user_cache_path("comfyui-setup-manager", appauthor=False) / "node-catalog.json"


def load_node_catalog_cache(path: Path | None = None) -> dict[str, Any]:
    cache_path = path or node_catalog_cache_path()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"schema_version": CATALOG_SCHEMA, "sources": [], "warnings": []}
    if not isinstance(payload, dict) or payload.get("schema_version") != CATALOG_SCHEMA:
        return {"schema_version": CATALOG_SCHEMA, "sources": [], "warnings": []}
    if not isinstance(payload.get("sources"), list):
        payload["sources"] = []
    return payload


def catalog_is_fresh(
    payload: dict[str, Any] | None = None,
    *,
    today: date | None = None,
    path: Path | None = None,
) -> bool:
    current = payload if payload is not None else load_node_catalog_cache(path)
    refreshed = str(current.get("refreshed_date_utc") or "")
    return refreshed == (today or datetime.now(timezone.utc).date()).isoformat()


def _fetch_json(
    url: str,
    *,
    opener: Callable[..., Any],
    timeout: float,
) -> Any:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Node catalog URL must use HTTPS: {url}")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with opener(request, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"Node catalog response exceeded {MAX_RESPONSE_BYTES} bytes: {url}")
    return json.loads(data.decode("utf-8"))


def _registry_payload(
    *,
    opener: Callable[..., Any],
    timeout: float,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    page = 1
    total_pages: int | None = None
    while page <= MAX_REGISTRY_PAGES and (total_pages is None or page <= total_pages):
        query = urllib.parse.urlencode(
            {
                "page": page,
                "limit": REGISTRY_PAGE_SIZE,
                "latest": "true",
            }
        )
        if progress:
            progress(f"Downloading Comfy Registry page {page}{f'/{total_pages}' if total_pages else ''}…")
        payload = _fetch_json(
            f"{REGISTRY_LIST_URL}?{query}", opener=opener, timeout=timeout
        )
        if not isinstance(payload, dict):
            raise ValueError("Comfy Registry returned a non-object response.")
        page_nodes = payload.get("nodes", [])
        if not isinstance(page_nodes, list):
            raise ValueError("Comfy Registry response is missing its node list.")
        nodes.extend(item for item in page_nodes if isinstance(item, dict))
        raw_total_pages = payload.get("totalPages")
        try:
            total_pages = max(1, int(raw_total_pages)) if raw_total_pages is not None else page
        except (TypeError, ValueError):
            total_pages = page
        if not page_nodes:
            break
        page += 1
    return {"nodes": nodes}


def _legacy_sources(
    *,
    opener: Callable[..., Any],
    timeout: float,
    progress: ProgressCallback | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Download the maintained Manager aggregate and node_db channels.

    The root Manager catalogs are the compatibility aggregate used by Manager
    itself, while ``node_db/*`` is the maintained transition database.  Both
    halves are parsed because ``custom-node-list.json`` identifies installable
    projects and ``extension-node-map.json`` supplies Comfy node class aliases.
    A source remains useful if only one half is temporarily available.
    """

    sources: list[dict[str, Any]] = []
    warnings: list[str] = []
    locations = [("aggregate", LEGACY_MANAGER_BASE)] + [
        (channel, f"{LEGACY_NODE_DB_BASE}/{channel}")
        for channel in LEGACY_CHANNELS
    ]
    for source_name, base_url in locations:
        custom_url = f"{base_url}/custom-node-list.json"
        extension_url = f"{base_url}/extension-node-map.json"
        custom_payload: Any = None
        extension_payload: Any = None
        try:
            if progress:
                progress(f"Downloading ComfyUI Manager node catalog: {source_name}…")
            custom_payload = _fetch_json(custom_url, opener=opener, timeout=timeout)
        except Exception as exc:  # Network failures preserve the last good daily cache.
            warnings.append(f"Could not refresh Manager catalog {source_name}: {exc}")
        try:
            if progress:
                progress(f"Downloading ComfyUI Manager extension map: {source_name}…")
            extension_payload = _fetch_json(extension_url, opener=opener, timeout=timeout)
        except Exception as exc:
            warnings.append(f"Could not refresh Manager extension map {source_name}: {exc}")

        custom_nodes = (
            custom_payload.get("custom_nodes", [])
            if isinstance(custom_payload, dict)
            else custom_payload if isinstance(custom_payload, list) else []
        )
        extension_map = extension_payload if isinstance(extension_payload, dict) else {}
        if not custom_nodes and not extension_map:
            continue
        sources.append(
            {
                "id": f"comfyui-manager-node-db-{source_name}",
                "kind": "manager-node-db-cache",
                "trust": "Official",
                "url": custom_url,
                "extension_map_url": extension_url,
                "payload": {
                    "custom_nodes": custom_nodes,
                    "extension_node_map": extension_map,
                },
            }
        )
    return sources, warnings


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh_node_catalog(
    *,
    force: bool = False,
    opener: Callable[..., Any] | None = None,
    timeout: float = 20.0,
    now: datetime | None = None,
    path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Refresh the official Registry and legacy Manager catalogs at most daily.

    The last usable source is retained when one endpoint is temporarily
    unavailable. A successful cache therefore remains useful offline and a
    partial network outage never erases known node provenance.
    """

    cache_path = path or node_catalog_cache_path()
    timestamp = now or datetime.now(timezone.utc)
    previous = load_node_catalog_cache(cache_path)
    if not force and catalog_is_fresh(previous, today=timestamp.date()):
        return previous

    source_by_id = {
        str(source.get("id")): source
        for source in previous.get("sources", [])
        if isinstance(source, dict) and source.get("id")
    }
    warnings: list[str] = []
    network = opener or urllib.request.urlopen

    try:
        registry = _registry_payload(opener=network, timeout=timeout, progress=progress)
        source_by_id["comfy-registry-daily"] = {
            "id": "comfy-registry-daily",
            "kind": "registry-cache",
            "trust": "Official",
            "url": REGISTRY_LIST_URL,
            "payload": registry,
        }
    except Exception as exc:
        warnings.append(f"Could not refresh the Comfy Registry: {exc}")

    legacy_sources, legacy_warnings = _legacy_sources(
        opener=network, timeout=timeout, progress=progress
    )
    warnings.extend(legacy_warnings)
    for source in legacy_sources:
        source_by_id[str(source["id"])] = source

    usable_sources = [source for source in source_by_id.values() if source.get("payload")]
    if not usable_sources:
        # Do not mark the day fresh when no source has ever been downloaded.
        previous["warnings"] = warnings
        return previous

    payload = {
        "schema_version": CATALOG_SCHEMA,
        "refreshed_at_utc": timestamp.astimezone(timezone.utc).isoformat(),
        "refreshed_date_utc": timestamp.astimezone(timezone.utc).date().isoformat(),
        "sources": sorted(usable_sources, key=lambda item: str(item.get("id"))),
        "warnings": warnings,
        "stats": {
            "source_count": len(usable_sources),
            "registry_node_count": sum(
                len(source.get("payload", {}).get("nodes", []))
                for source in usable_sources
                if source.get("kind") == "registry-cache"
                and isinstance(source.get("payload"), dict)
            ),
            "legacy_entry_count": sum(
                len(source.get("payload", {}).get("custom_nodes", []))
                for source in usable_sources
                if source.get("kind") == "manager-node-db-cache"
                and isinstance(source.get("payload"), dict)
            ),
            "legacy_extension_count": sum(
                len(source.get("payload", {}).get("extension_node_map", {}))
                for source in usable_sources
                if source.get("kind") == "manager-node-db-cache"
                and isinstance(source.get("payload"), dict)
                and isinstance(source.get("payload", {}).get("extension_node_map"), dict)
            ),
        },
    }
    _atomic_write(cache_path, payload)
    return payload


def ensure_daily_node_catalog(**kwargs: Any) -> dict[str, Any]:
    return refresh_node_catalog(force=False, **kwargs)
