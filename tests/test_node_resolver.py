from __future__ import annotations

import json
from pathlib import Path

from comfy_setup.engine import InstallerEngine
from comfy_setup.exporter import _nodes
from comfy_setup.models import InstallOptions, PlatformInfo
from comfy_setup.node_resolver import NodeSourceResolver


def _offline_config(*, mappings: list[dict] | None = None, sources: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "network": {"enabled": False, "timeout_seconds": 1},
        "sources": sources or [],
        "mappings": mappings or [],
    }


def _write_manager_cache(comfy: Path, entries: list[dict]) -> Path:
    cache = comfy / "user" / "__manager" / "cache" / "123_custom-node-list.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"custom_nodes": entries}), encoding="utf-8")
    return cache


def test_local_manager_cache_resolves_known_public_nodes_without_snapshots(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    custom_nodes = comfy / "custom_nodes"
    custom_nodes.mkdir(parents=True)
    entries = [
        {"title": "ACE-Step", "files": ["https://github.com/ace-step/ACE-Step-ComfyUI"]},
        {"title": "ComfyUI-GIMM-VFI", "files": ["https://github.com/kijai/ComfyUI-GIMM-VFI"]},
        {"id": "comfyui-gguf", "title": "ComfyUI-GGUF", "repository": "https://github.com/city96/ComfyUI-GGUF"},
        {"id": "kjnodes", "title": "ComfyUI-KJNodes", "repository": "https://github.com/kijai/ComfyUI-KJNodes"},
        {"id": "vhs", "title": "ComfyUI-VideoHelperSuite", "repository": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite"},
        {"id": "rife-tensorrt-auto", "title": "ComfyUI-RIFE-TensorRT-Auto", "repository": "https://github.com/huchukato/ComfyUI-RIFE-TensorRT-Auto"},
        {"title": "WAS Node Suite", "repository": "https://github.com/WASasquatch/was-node-suite-comfyui"},
        {"id": "qwen3-tts-comfyui", "name": "Qwen3 TTS", "repository": "https://github.com/example/qwen3-tts-comfyui"},
        {"title": "TeaCache HunyuanVideo", "repository": "https://github.com/facok/ComfyUI-TeaCacheHunyuanVideo"},
    ]
    _write_manager_cache(comfy, entries)
    folder_names = [
        "ace-step",
        "comfy-gimm-vfi",
        "ComfyUI-GGUF",
        "comfyui-kjnodes",
        "comfyui-videohelpersuite",
        "ComfyUI_RIFE_TensorRT_Auto",
        "was-ns",
        "qwen3-tts-comfyui",
        "teacachehunyuanvideo",
    ]
    for name in folder_names:
        node = custom_nodes / name
        node.mkdir()
        (node / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")

    resolver = NodeSourceResolver(comfy, config=_offline_config())
    resolutions = {name: resolver.resolve(custom_nodes / name) for name in folder_names}

    assert all(value is not None for value in resolutions.values())
    assert resolutions["ComfyUI-GGUF"].manager_id == "comfyui-gguf"
    assert resolutions["comfyui-kjnodes"].repository == "https://github.com/kijai/ComfyUI-KJNodes"
    assert resolutions["ace-step"].repository == "https://github.com/ace-step/ACE-Step-ComfyUI"
    assert resolutions["qwen3-tts-comfyui"].manager_id == "qwen3-tts-comfyui"


def test_matching_repository_in_node_readme_is_used_without_catalog(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    node = comfy / "custom_nodes" / "teacachehunyuanvideo"
    node.mkdir(parents=True)
    (node / "README.md").write_text(
        "Project source: https://github.com/facok/ComfyUI-TeaCacheHunyuanVideo\n"
        "Dependency reference: https://github.com/example/unrelated-library\n",
        encoding="utf-8",
    )

    resolution = NodeSourceResolver(comfy, config=_offline_config()).resolve(node)
    assert resolution is not None
    assert resolution.repository == "https://github.com/facok/ComfyUI-TeaCacheHunyuanVideo"
    assert resolution.source_kind == "node-metadata"


def test_manager_snapshot_commit_is_reused_as_fetchable_exact_ref(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    node = comfy / "custom_nodes" / "ComfyUI-GGUF"
    node.mkdir(parents=True)
    _write_manager_cache(
        comfy,
        [{"id": "comfyui-gguf", "title": "ComfyUI-GGUF", "repository": "https://github.com/city96/ComfyUI-GGUF"}],
    )
    snapshot = comfy / "user" / "__manager" / "snapshots" / "working.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        json.dumps(
            {
                "custom_nodes": {
                    "git_custom_nodes": {
                        "https://github.com/city96/ComfyUI-GGUF": {"hash": "a" * 40}
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    resolution = NodeSourceResolver(comfy, config=_offline_config()).resolve(node)
    assert resolution is not None
    assert resolution.ref == "a" * 40


def test_configured_local_catalog_and_ambiguous_matches_are_handled_safely(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    custom = comfy / "custom_nodes"
    custom.mkdir(parents=True)
    resolved_node = custom / "private-channel-node"
    resolved_node.mkdir()
    ambiguous_node = custom / "qwen-tts"
    ambiguous_node.mkdir()

    catalog = tmp_path / "company-node-list.json"
    catalog.write_text(
        json.dumps(
            {
                "custom_nodes": [
                    {
                        "id": "private-channel-node",
                        "title": "Private Channel Node",
                        "repository": "https://github.com/example/private-channel-node",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = _offline_config(
        sources=[
            {
                "id": "company-catalog",
                "kind": "manager-list-file",
                "path": str(catalog),
                "trust": "Organization",
                "enabled": True,
            }
        ],
        mappings=[
            {"id": "qwen-a", "title": "qwen-tts", "repository": "https://github.com/example/qwen-a"},
            {"id": "qwen-b", "title": "qwen-tts", "repository": "https://github.com/example/qwen-b"},
        ],
    )
    resolver = NodeSourceResolver(comfy, config=config)

    resolved = resolver.resolve(resolved_node)
    assert resolved is not None
    assert resolved.source_id == "company-catalog"
    assert resolved.manager_id == "private-channel-node"
    assert resolver.resolve(ambiguous_node) is None


def test_export_embeds_only_unresolved_nodes_and_stays_small(tmp_path: Path) -> None:
    comfy = tmp_path / "ComfyUI"
    custom = comfy / "custom_nodes"
    public_node = custom / "ComfyUI-GGUF"
    public_node.mkdir(parents=True)
    (public_node / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    (public_node / "large-test-payload.bin").write_bytes(b"x" * (8 * 1024 * 1024))
    local_plugin = custom / "websocket_image_save.py"
    local_plugin.write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    _write_manager_cache(
        comfy,
        [{"id": "comfyui-gguf", "title": "ComfyUI-GGUF", "repository": "https://github.com/city96/ComfyUI-GGUF"}],
    )
    resolver = NodeSourceResolver(comfy, config=_offline_config())

    nodes, warnings, embedded = _nodes(
        comfy,
        exact_refs=True,
        include_unpublished_plugins=True,
        resolver=resolver,
    )
    by_id = {node["id"]: node for node in nodes}

    assert by_id["comfyui-gguf"]["source"]["type"] == "remote"
    assert by_id["comfyui-gguf"]["source"]["manager_id"] == "comfyui-gguf"
    assert "comfyui-gguf" not in embedded
    assert by_id["websocket-image-save"]["source"]["type"] == "embedded"
    assert set(embedded) == {"websocket-image-save"}
    assert not any("ComfyUI-GGUF" in warning for warning in warnings)
    assert any("websocket_image_save.py" in warning for warning in warnings)


def test_remote_manager_catalog_and_registry_search_are_both_supported(tmp_path: Path) -> None:
    import io
    import urllib.parse

    comfy = tmp_path / "ComfyUI"
    gguf = comfy / "custom_nodes" / "ComfyUI-GGUF"
    qwen = comfy / "custom_nodes" / "qwen3-tts-comfyui"
    gguf.mkdir(parents=True)
    qwen.mkdir()

    calls: list[str] = []

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    def opener(request, timeout=0):
        url = request.full_url
        calls.append(url)
        if "custom-node-list.json" in url:
            payload = {
                "custom_nodes": [
                    {
                        "title": "ComfyUI-GGUF",
                        "files": ["https://github.com/city96/ComfyUI-GGUF"],
                    }
                ]
            }
        else:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            search = query.get("search", [""])[0]
            payload = {
                "nodes": ([{
                    "id": "qwen3-tts-comfyui",
                    "name": "Qwen3 TTS",
                    "repository": "https://github.com/example/qwen3-tts-comfyui",
                    "latest_version": {"version": "1.0.7"},
                }] if "qwen" in search.lower() else [])
            }
        return _Response(json.dumps(payload).encode("utf-8"))

    config = {
        "schema_version": 1,
        "network": {"enabled": True, "timeout_seconds": 1},
        "sources": [
            {
                "id": "comfyui-manager",
                "kind": "manager-list",
                "url": "https://example.test/custom-node-list.json",
                "trust": "Official",
                "enabled": True,
            },
            {
                "id": "comfy-registry",
                "kind": "registry-search",
                "url": "https://example.test/nodes/search",
                "trust": "Official",
                "enabled": True,
            },
        ],
        "mappings": [],
    }
    resolver = NodeSourceResolver(comfy, opener=opener, config=config)

    gguf_resolution = resolver.resolve(gguf)
    qwen_resolution = resolver.resolve(qwen)

    assert gguf_resolution is not None
    assert gguf_resolution.repository == "https://github.com/city96/ComfyUI-GGUF"
    assert gguf_resolution.source_kind == "manager-list"
    assert qwen_resolution is not None
    assert qwen_resolution.manager_id == "qwen3-tts-comfyui"
    assert qwen_resolution.manager_version == "1.0.7"
    assert qwen_resolution.source_kind == "registry-search"
    assert sum("custom-node-list.json" in url for url in calls) == 1
    assert any("nodes/search" in url for url in calls)


class _ManagerInstallRunner:
    def __init__(self, custom_nodes: Path) -> None:
        self.custom_nodes = custom_nodes

    def run(self, command, **kwargs):
        (self.custom_nodes / "comfyui_kjnodes").mkdir(parents=True, exist_ok=True)
        return 0


def test_manager_install_accepts_manager_normalized_folder_name(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    target.mkdir()
    options = InstallOptions(
        target_dir=target,
        python_version="3.13",
        accelerator="cpu",
        selected_nodes={"comfyui-kjnodes"},
        selected_acceleration=set(),
        auto_install_system=False,
        allow_source_builds=False,
        use_current_checkout=False,
    )
    info = PlatformInfo(
        os_name="linux",
        system="Linux",
        release="test",
        architecture="x86_64",
        is_wsl=False,
        package_manager=None,
        accelerator="cpu",
    )
    engine = InstallerEngine({}, info, options)
    engine.runner = _ManagerInstallRunner(target / "custom_nodes")
    node = {
        "id": "comfyui-kjnodes",
        "name": "ComfyUI-KJNodes",
        "folder": "comfyui-kjnodes",
        "source": {
            "type": "remote",
            "manager_id": "kjnodes",
            "repository": "https://github.com/kijai/ComfyUI-KJNodes",
            "preferred": "manager",
        },
    }

    installed = engine._install_manager_node(node, "kjnodes")
    assert installed == target / "custom_nodes" / "comfyui_kjnodes"


def test_inventory_collector_embeds_only_unresolved_custom_nodes(tmp_path: Path, monkeypatch) -> None:
    import csv
    import collect_comfyui_inventory as collector
    from comfy_setup.node_resolver import NodeResolution

    comfy = tmp_path / "ComfyUI"
    custom = comfy / "custom_nodes"
    public_node = custom / "ComfyUI-GGUF"
    public_node.mkdir(parents=True)
    (public_node / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    (public_node / "large.bin").write_bytes(b"x" * (8 * 1024 * 1024))
    local_node = custom / "private-local-node"
    local_node.mkdir()
    (local_node / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")

    class _Resolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, path: Path, *, allow_network: bool = True):
            if path.name == "ComfyUI-GGUF":
                return NodeResolution(
                    manager_id="comfyui-gguf",
                    repository="https://github.com/city96/ComfyUI-GGUF",
                    display_name="ComfyUI-GGUF",
                    manager_version=None,
                    ref=None,
                    source_id="comfyui-manager",
                    source_kind="manager-list",
                    trust="Official",
                    confidence=105,
                )
            return None

    monkeypatch.setattr(collector, "NodeSourceResolver", _Resolver)
    destination = tmp_path / "inventory"
    collector.collect_nodes(comfy, [custom], destination)

    with (destination / "inventory.tsv").open(encoding="utf-8", newline="") as stream:
        rows = {row["folder"]: row for row in csv.DictReader(stream, delimiter="\t")}

    assert rows["ComfyUI-GGUF"]["kind"] == "directory"
    assert rows["ComfyUI-GGUF"]["payload"] == ""
    assert rows["ComfyUI-GGUF"]["manager_id"] == "comfyui-gguf"
    assert not any((destination / path).exists() for path in ["node_1_ComfyUI-GGUF/embedded_plugin"])
    assert rows["private-local-node"]["kind"] == "local-unpublished"
    assert rows["private-local-node"]["payload"] == "embedded_plugin"


def test_current_registry_entry_wins_over_legacy_or_forked_name_collision(tmp_path: Path, monkeypatch) -> None:
    comfy = tmp_path / "ComfyUI"
    node = comfy / "custom_nodes" / "ComfyUI-GGUF"
    node.mkdir(parents=True)
    cache = tmp_path / "node-catalog.json"
    cache.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "refreshed_date_utc": "2099-01-01",
                "sources": [
                    {
                        "id": "comfy-registry-daily",
                        "kind": "registry-cache",
                        "trust": "Official",
                        "payload": {"nodes": [{
                            "id": "comfyui-gguf",
                            "name": "ComfyUI-GGUF",
                            "repository": "https://github.com/city96/ComfyUI-GGUF",
                        }]},
                    },
                    {
                        "id": "comfyui-manager-node-db-forked",
                        "kind": "manager-node-db-cache",
                        "trust": "Official",
                        "payload": {"custom_nodes": [{
                            "title": "ComfyUI-GGUF",
                            "reference": "https://github.com/example/old-gguf-fork",
                        }]},
                    },
                ],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_SETUP_MANAGER_NODE_CATALOG", str(cache))

    resolution = NodeSourceResolver(comfy, config=_offline_config()).resolve(node, allow_network=False)

    assert resolution is not None
    assert resolution.manager_id == "comfyui-gguf"
    assert resolution.repository == "https://github.com/city96/ComfyUI-GGUF"
    assert resolution.source_kind == "registry-cache"
