from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from comfy_setup.node_catalog import (
    CATALOG_SCHEMA,
    catalog_is_fresh,
    load_node_catalog_cache,
    refresh_node_catalog,
)
from comfy_setup.node_resolver import NodeSourceResolver


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_daily_catalog_refresh_fetches_registry_and_both_legacy_files_once_per_day(
    tmp_path: Path, monkeypatch
) -> None:
    cache = tmp_path / "node-catalog.json"
    calls: list[str] = []

    def opener(request, timeout=0):
        url = request.full_url
        calls.append(url)
        if "api.comfy.org/nodes?" in url:
            query = parse_qs(urlparse(url).query)
            assert query["latest"] == ["true"]
            payload = {
                "page": 1,
                "totalPages": 1,
                "nodes": [
                    {
                        "id": "comfyui-gguf",
                        "name": "ComfyUI-GGUF",
                        "repository": "https://github.com/city96/ComfyUI-GGUF",
                        "latest_version": {"version": "1.2.3"},
                    }
                ],
            }
        elif url.endswith("custom-node-list.json"):
            payload = {
                "custom_nodes": [
                    {
                        "title": "WAS Node Suite",
                        "files": ["https://github.com/WASasquatch/was-node-suite-comfyui"],
                    }
                ]
            }
        elif url.endswith("extension-node-map.json"):
            payload = {
                "https://github.com/WASasquatch/was-node-suite-comfyui": [
                    ["WAS_Image_Batch", "WAS_Text"],
                    {"author": "WAS"},
                ]
            }
        else:  # pragma: no cover - catches accidental new public endpoints
            raise AssertionError(url)
        return Response(json.dumps(payload).encode("utf-8"))

    now = datetime(2026, 7, 17, 8, tzinfo=timezone.utc)
    payload = refresh_node_catalog(
        force=False, opener=opener, now=now, path=cache, timeout=1
    )
    assert payload["schema_version"] == CATALOG_SCHEMA
    assert payload["stats"]["registry_node_count"] == 1
    assert payload["stats"]["legacy_entry_count"] == 6
    assert payload["stats"]["legacy_extension_count"] == 6
    assert sum("api.comfy.org/nodes?" in call for call in calls) == 1
    assert sum(call.endswith("custom-node-list.json") for call in calls) == 6
    assert sum(call.endswith("extension-node-map.json") for call in calls) == 6

    first_call_count = len(calls)
    same_day = refresh_node_catalog(
        force=False,
        opener=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called")),
        now=now + timedelta(hours=5),
        path=cache,
        timeout=1,
    )
    assert same_day == payload
    assert len(calls) == first_call_count
    assert catalog_is_fresh(same_day, today=now.date())


def test_daily_catalog_preserves_last_good_sources_during_partial_outage(tmp_path: Path) -> None:
    cache = tmp_path / "node-catalog.json"
    prior = {
        "schema_version": CATALOG_SCHEMA,
        "refreshed_at_utc": "2026-07-16T08:00:00+00:00",
        "refreshed_date_utc": "2026-07-16",
        "sources": [
            {
                "id": "comfy-registry-daily",
                "kind": "registry-cache",
                "trust": "Official",
                "url": "https://api.comfy.org/nodes",
                "payload": {
                    "nodes": [
                        {
                            "id": "kjnodes",
                            "name": "ComfyUI-KJNodes",
                            "repository": "https://github.com/kijai/ComfyUI-KJNodes",
                        }
                    ]
                },
            }
        ],
        "warnings": [],
    }
    cache.write_text(json.dumps(prior), encoding="utf-8")

    def failing(*args, **kwargs):
        raise OSError("offline")

    refreshed = refresh_node_catalog(
        force=True,
        opener=failing,
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        path=cache,
        timeout=1,
    )
    assert refreshed["refreshed_date_utc"] == "2026-07-17"
    source = next(item for item in refreshed["sources"] if item["id"] == "comfy-registry-daily")
    assert source["payload"]["nodes"][0]["id"] == "kjnodes"
    assert refreshed["warnings"]


def test_resolver_uses_daily_cache_for_manager_and_registry_installed_folders(
    tmp_path: Path, monkeypatch
) -> None:
    cache = tmp_path / "node-catalog.json"
    payload = {
        "schema_version": CATALOG_SCHEMA,
        "refreshed_at_utc": "2026-07-17T08:00:00+00:00",
        "refreshed_date_utc": "2026-07-17",
        "sources": [
            {
                "id": "comfy-registry-daily",
                "kind": "registry-cache",
                "trust": "Official",
                "payload": {
                    "nodes": [
                        {
                            "id": "comfyui-gguf",
                            "name": "ComfyUI-GGUF",
                            "repository": "https://github.com/city96/ComfyUI-GGUF",
                            "latest_version": {"version": "1.2.3"},
                        },
                        {
                            "id": "qwen3-tts-comfyui",
                            "name": "qwen3-tts-comfyui",
                            "repository": "https://github.com/1038lab/ComfyUI-Qwen3-TTS",
                        },
                    ]
                },
            },
            {
                "id": "comfyui-manager-node-db-legacy",
                "kind": "manager-node-db-cache",
                "trust": "Official",
                "payload": {
                    "custom_nodes": [
                        {
                            "title": "WAS Node Suite",
                            "files": ["https://github.com/WASasquatch/was-node-suite-comfyui"],
                        }
                    ],
                    "extension_node_map": {
                        "https://github.com/WASasquatch/was-node-suite-comfyui": [
                            ["WAS_Image_Batch"],
                            {},
                        ]
                    },
                },
            },
        ],
        "warnings": [],
    }
    cache.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("COMFYUI_SETUP_MANAGER_NODE_CATALOG", str(cache))

    comfy = tmp_path / "ComfyUI"
    custom_nodes = comfy / "custom_nodes"
    for folder in ("ComfyUI-GGUF", "qwen3-tts-comfyui", "was-ns"):
        (custom_nodes / folder).mkdir(parents=True, exist_ok=True)

    resolver = NodeSourceResolver(
        comfy,
        config={"schema_version": 2, "network": {"enabled": False}, "sources": [], "mappings": []},
    )
    gguf = resolver.resolve(custom_nodes / "ComfyUI-GGUF", allow_network=False)
    qwen = resolver.resolve(custom_nodes / "qwen3-tts-comfyui", allow_network=False)
    was = resolver.resolve(custom_nodes / "was-ns", allow_network=False)
    assert gguf and gguf.manager_id == "comfyui-gguf"
    assert gguf.manager_version == "1.2.3"
    assert qwen and qwen.manager_id == "qwen3-tts-comfyui"
    assert was and was.repository == "https://github.com/WASasquatch/was-node-suite-comfyui"


def test_resolver_replaces_stale_daily_entries_after_on_demand_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    cache = tmp_path / "node-catalog.json"
    cache.write_text(
        json.dumps(
            {
                "schema_version": CATALOG_SCHEMA,
                "refreshed_at_utc": "2026-07-16T08:00:00+00:00",
                "refreshed_date_utc": "2026-07-16",
                "sources": [
                    {
                        "id": "comfy-registry-daily",
                        "kind": "registry-cache",
                        "trust": "Official",
                        "payload": {"nodes": []},
                    }
                ],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_SETUP_MANAGER_NODE_CATALOG", str(cache))
    comfy = tmp_path / "ComfyUI"
    node = comfy / "custom_nodes" / "ComfyUI-GGUF"
    node.mkdir(parents=True)

    resolver = NodeSourceResolver(
        comfy,
        config={"schema_version": 2, "network": {"enabled": True}, "sources": [], "mappings": []},
    )

    refreshed = {
        "schema_version": CATALOG_SCHEMA,
        "refreshed_at_utc": "2026-07-17T08:00:00+00:00",
        "refreshed_date_utc": "2026-07-17",
        "sources": [
            {
                "id": "comfy-registry-daily",
                "kind": "registry-cache",
                "trust": "Official",
                "payload": {
                    "nodes": [
                        {
                            "id": "comfyui-gguf",
                            "name": "ComfyUI-GGUF",
                            "repository": "https://github.com/city96/ComfyUI-GGUF",
                        }
                    ]
                },
            }
        ],
        "warnings": [],
    }

    with monkeypatch.context() as patcher:
        patcher.setattr("comfy_setup.node_resolver.ensure_daily_node_catalog", lambda **kwargs: cache.write_text(json.dumps(refreshed), encoding="utf-8") or refreshed)
        resolution = resolver.resolve(node, allow_network=True)

    assert resolution is not None
    assert resolution.manager_id == "comfyui-gguf"
    assert resolution.repository == "https://github.com/city96/ComfyUI-GGUF"
