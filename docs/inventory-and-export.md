# Inventory collection and export

[Documentation home](index.md) · [Setup profiles](setup-profiles.md)

The inventory collector records enough information to recreate the **final working state**, rather than trying to reconstruct it later from incomplete node requirement files. It excludes models, outputs, credentials, and personal configuration.

## Linux, WSL2, macOS

```bash
./collect_comfyui_inventory.sh /path/to/ComfyUI ./inventory.tgz
```

## Windows PowerShell

```powershell
.\collect_comfyui_inventory.ps1 -ComfyUIPath C:\AI\ComfyUI -OutputPath .\inventory.tgz
```

## What is collected

- ComfyUI Git repository, branch, immutable commit, and local status;
- the complete installed Python distribution inventory and direct-source metadata;
- exact package/version and direct-source provenance from `direct_url.json`, without copying local wheels or installed libraries;
- custom-node Manager/Registry ids, validated GitHub repositories, recoverable commits, and resolution provenance;
- sanitized source payloads only for plugins that remain unresolved after local Manager caches/snapshots, configured catalogs/channels, the official Manager catalog, the Comfy Registry, node metadata, and Git are exhausted;
- node requirement and installer metadata;
- detected accelerator and development tools;
- recent sanitized startup information.

The collector writes `python/environment-lock.json`. It records every installed distribution and exact version, including packages originally installed from a local wheel, but new inventories do not archive wheels or site-packages. Installed editable/local distributions under ComfyUI or a custom-node root record the safe path where that public/reconstructed source will exist. Unsupported direct sources and ambiguous duplicate distributions mark the lock incomplete instead of disappearing.

Unpublished local plugins are included by default because silently omitting one would make the inventory non-portable. `--skip-local-plugins` is available only for a deliberately partial diagnostic inventory; conversion refuses to call that inventory exact when an omitted local node is detected.

## What is excluded

- models, LoRAs, checkpoints, embeddings, GGUF files, and engines;
- input and output media;
- workflows unless separately exported;
- API credentials, cookies, SSH keys, and environment variables;
- the real `extra_model_paths.yaml` file;
- virtual environments and caches.

## Convert inventory to a setup

```bash
comfyui-setup-manager profiles from-inventory ./inventory.tgz \
  --name "Portable Setup" \
  --repository https://github.com/Comfy-Org/ComfyUI.git
```

The conversion carries the complete environment lock, `custom_nodes.yml`, copied dependency manifests, compact Manager/Registry/Git node descriptors, source-tree install metadata, exact PyTorch backend metadata, and source only for genuinely unresolved plugins. The ComfyUI core is represented by its repository plus a compact Git-diff overlay, never a full checkout snapshot. Conversion refuses only when required metadata is unsafe or ambiguous, such as an unmade duplicate-node choice, missing unresolved-plugin payload, or ambiguous package ownership.

By default the profile is written to `<project-root>/profiles/portable-setup.comfyuisetup`, imported into the Profile Library, and immediately available to select. Add `--output PATH` to choose another file or directory. The former project `setups/` directory migrates automatically without overwriting conflicts.

## Direct export versus inventory export

`profiles from-installation` reads the live working environment directly. The inventory path is intended for another machine, offline review, or cases where the manager cannot run in the source installation. Both paths produce the same reconstruction-first semantics: refresh/consult Registry and Manager metadata, write public nodes as references, copy dependency manifests, compute a minimal core overlay, and embed only unresolved plugin source.

A live direct export can resolve duplicate custom-node destination identities interactively because the manager can show every current absolute source path and let the user explicitly omit all but one. The same choice can be scripted with repeatable `--omit-node` arguments. Inventory conversion remains strict for duplicate identities because an archived inventory may no longer have accessible source trees to inspect safely.

## Asset manifests in exported setups

A current export may copy public model/workflow source metadata and catalog entries into `models.yaml`, `workflows.yaml`, and `asset-sources.yaml`. It never exports the source machine's shared-library path or local-only catalog entries.

The receiving user selects local shared paths during installation.
