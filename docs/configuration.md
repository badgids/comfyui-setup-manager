# YAML configuration

[Documentation home](index.md) · [CLI reference](cli-reference.md)

All manager-owned mutable configuration and cached runtime state use YAML with atomic writes.

## Files

| File | Purpose |
|---|---|
| `manager-config.yaml` | Manager behavior and runtime-log retention policy |
| `custom-node-sources.yaml` | Official/custom Manager catalogs, Registry search, and explicit custom-node source mappings |
| `repositories.yaml` | Official and allowed repository definitions |
| `pytorch-releases.yaml` | Accelerated PyTorch release choices |
| `wheel-sources.yaml` | Precompiled wheel sources |
| `themes.yaml` | Theme selection and imported themes |
| `installations.yaml` | User-edited installation metadata |
| `asset-paths.yaml` | Shared models and workflows roots |
| `model-sources.yaml` | Non-LoRA model sources and entries |
| `lora-sources.yaml` | LoRA sources and entries |
| `workflow-sources.yaml` | Workflow sources and entries |
| `download-tasks.yaml` | Persistent asset task history |
| `runtime-state.yaml` | Last completed system/install/node/workflow/model/LoRA scan |
| `agents-skills.yaml` | Agent IDs and skill installation directories |
| `mcps.yaml` | Portable MCP server definitions |

`runtime-state.yaml` is written by **Rescan**. Ordinary startup reads it. Editing it manually is possible but a new scan is the authoritative way to rebuild it.

`manager-config.yaml` stores `logs.auto_cleanup`, `logs.retention_mode`, and `logs.retention_days`. The default is automatic Monthly cleanup after 30 days; the TUI log browser and `installations logs --retention` update these values.

## Custom-node source resolution

`custom-node-sources.yaml` controls the provenance resolver used during profile export and the Nodes & Plugins inventory. The bundled defaults enable the official Comfy Registry and the maintained ComfyUI-Manager `node_db`. Once per UTC day the manager caches the complete paginated Registry list plus `custom-node-list.json` and `extension-node-map.json` from every maintained legacy channel. Export also reads local Manager caches/snapshots, `channels.list`, configured mappings/catalogs, node metadata, and Git hints. A manual **Rescan nodes** forces an immediate catalog refresh.

Organizations and offline users may add local catalog files or explicit mappings:

```yaml
network:
  enabled: false
sources:
  - id: studio-manager-cache
    kind: manager-list-file
    path: /srv/comfy-catalogs/custom-node-list.json
    trust: Organization
    enabled: true
mappings:
  - id: private-node-id
    title: Private Node
    repository: https://github.com/example/private-node
    aliases: [private_node_folder]
```

Catalog content is treated only as metadata. Export accepts only HTTPS catalog endpoints and validated GitHub repository identities; it never executes catalog data. Known public/configured nodes remain compact profile descriptors. Only unresolved local nodes are sanitized and embedded.

## CLI

```bash
./comfyui-setup-manager config list
./comfyui-setup-manager config show runtime-state.yaml
./comfyui-setup-manager config edit agents-skills.yaml
./comfyui-setup-manager config validate
```

Use **Reload YAML** in the TUI after a configuration-only edit. Use **Rescan** when the filesystem changed.

## Project root and profile exports

The bundled Linux/macOS and Windows launchers set `COMFYUI_SETUP_PROJECT_ROOT` to the directory containing `comfyui-setup-manager`. Portable profile exports therefore default to:

```text
<project-root>/profiles/
```

The first call moves non-conflicting files from the former `<project-root>/setups/` directory and removes that old directory when it becomes empty. Existing same-named files are never overwritten. Set `COMFYUI_SETUP_PROJECT_ROOT` explicitly when invoking the installed Python package directly and you need the same stable location. An output path selected in the TUI or supplied to the CLI always overrides the default.

Filesystem fields in the TUI provide local path completion and a **Browse…** button. These controls do not scan recursively; completion reads only the currently typed directory.
