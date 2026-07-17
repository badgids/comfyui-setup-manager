# Architecture

[Documentation home](index.md) · [Function reference](function-reference.md) · [PRD](../PRD.md)

## Layers

1. **CLI and TUI** collect user choices.
2. **Profiles and configuration** validate portable manifests and YAML policy.
3. **Installer engine** plans and performs installation steps.
4. **Resolvers** choose PyTorch distributions, wheels, or isolated source builds.
5. **Instance management** discovers, launches, stops, inspects, and removes installations.
6. **Update manager** performs preflight, snapshots, updates, validation, and rollback.
7. **Workflow manager** validates and moves native workflows and workflow packs.

## Important modules

| Module | Responsibility |
|---|---|
| `cli.py` | Complete non-TUI command interface |
| `app.py` | Textual interface |
| `engine.py` | Installation planning and execution |
| `profile.py` | Profile validation, import, export, and bundles |
| `discovery.py` | Installation, custom-node, and workflow discovery |
| `runtime_state.py` | Explicit full scans and YAML-backed startup cache |
| `asset_catalog.py` | Separate model, LoRA, and workflow catalogs and inventories |
| `agent_resources.py` | Bundled skill discovery, target resolution, and installation |
| `mcp_config.py` | Portable MCP definition loading |
| `instance_control.py` | Persistent background launch and stop operations |
| `log_relay.py` | Unbuffered subprocess relay and live active-log rotation |
| `log_management.py` | Active/archive log paths, search, deletion, rotation, and retention cleanup |
| `dependency_resolver.py` | Captured-manifest reconciliation, installed-package audit, and ComfyUI startup-failure parsing |
| `selectable_widgets.py` | Shared selectable input, document, and console-output widgets with desktop copy shortcuts |
| `environment_lock.py` | Complete distribution lock, source provenance, runtime compatibility, legacy embedded-wheel compatibility, and lock helpers |
| `updates.py` | Update preflight, snapshots, update, and rollback |
| `workflows.py` | Native workflow and pack handling |
| `wheel_sources.py` | Editable wheel source registry and compatibility matching |
| `wheels.py` | Public/configured wheel resolution, legacy embedded-wheel installation, and isolated builds of recorded package versions |
| `pytorch_install.py` | Official PyTorch backend selection |
| `configuration.py` | Editable YAML configuration |
| `package_sources.py` | Download-source trust policy |
| `runner.py` | Logged command execution and privilege handling |

## Shared logic

The CLI and TUI call the same engine, profile, workflow, update, resolver, log-management, and discovery modules. The interfaces do not implement separate installers.

`engine.py` validates exact target compatibility before changing the installation, installs the exported PyTorch backend, installs core plus Manager requirements, and applies the complete environment lock once. It then acquires repository-backed nodes directly with Git; Manager-only records are acquired with `--no-deps`. Captured node manifests are reconciled and audited without repeating an already-complete package solve, dependency-only install scripts are suppressed, required lifecycle scripts still run, locked editable/source-tree distributions are restored, acceleration packages are built against the target ABI, and the final package/startup audits run before success.

`exporter.py` and `inventory.py` do not derive portability from a shortlist of "important" packages. They record every installed distribution and its direct source, including distributions supplied from configured external custom-node roots. Before copying any custom-node source, both paths resolve the installed folder against node metadata, local Manager/Registry caches and snapshots, configured catalogs/channels, the official Manager catalog, the Comfy Registry, and GitHub. Known public nodes remain compact remote descriptors; only genuinely unresolved local plugins are sanitized and embedded. New exports never copy virtual environments, installed libraries, public node trees, or local wheels. Package provenance and exact versions are recorded and resolved through configured/public indexes, immutable Git sources, or source-build rules.

`updates.py` treats official core source and direct core-package changes as reviewable inputs. It protects all installed non-core distributions plus unselected core packages and normalizes duplicate metadata to Python's effective distribution. Only selected core packages that actually require a version change are submitted to uv. Custom-node manifests are inspected for direct core conflicts but are not installed as a new global recipe; installed metadata, `pip check`, and startup/import validation are authoritative. Already-current and file-only updates skip uv. The source commit and any package plan are transactional: failure in resolution, `pip check`, server readiness, or custom-node imports invokes snapshot rollback and validates the restored state.

`compatibility_tags.py` normalizes profile versions with PEP 440 and records a readable Python/accelerator/PyTorch ABI identity. Export, import, selection, archive metadata, and installation reports share the same representation.

## Safety boundaries

- archive paths are validated before extraction;
- destructive operations validate target paths;
- known public plugins are acquired through a recorded Manager/Registry id or validated GitHub repository, with an immutable Manager snapshot/Git ref retained when available; missing `.git` metadata or an unpinned local folder never causes a known public node to be bundled;
- package sources are checked before use;
- source builds are isolated, use the target Python/PyTorch/CUDA ABI, prefer a captured source commit when available, and verify that the built wheel has the recorded package version;
- updates take a lightweight snapshot before changes and automatically restore it after every failed mutated candidate;
- configuration writes use temporary files and atomic replacement.

## Shared asset services

`shared_assets.py` owns external model/workflow path configuration, ComfyUI `extra_model_paths.yaml` generation, workflow link creation, migration, and per-instance status inspection.

`asset_catalog.py` owns separate model/LoRA/workflow source YAML, catalog entries, safe destinations, downloads, imports, deletes, and persisted task state.


`selectable_widgets.py` is the required text surface for paths, commands, logs, inventories, reviews, and diagnostics. RichLog-style append calls are adapted to a read-only TextArea document so mouse selection, Shift+Arrow, Ctrl/Cmd+A, and Ctrl/Cmd+C work consistently. New screens must not introduce non-selectable console or document renderers.

Before package installation, `engine.py` obtains the captured manifest for the matching ComfyUI or custom-node source and calls `dependency_resolver.reconcile_requirements_text()`. The exact environment lock is the highest-authority record of what worked. Generated reconciled manifests are auditable state files; source manifests in the profile are never rewritten in place.

Both the TUI and CLI call these service modules. UI code must not implement a second copy of asset behavior.


## Startup boundary

`ComfySetupApp.__init__` may read YAML and package resources only. Hardware probing, Git commands, bounded installation discovery, node/workflow walks, and shared-asset indexing run through `runtime_state.scan_runtime_state()` after the first frame is rendered. The completed result is atomically saved to `runtime-state.yaml`.

The same boundary applies after installation. Home navigation is immediate;
inventory discovery runs in the `runtime-scan` worker behind
`ScanProgressScreen`, and target selection happens only after the refreshed
state is applied. Installation workers must translate unexpected exceptions to
`InstallResult` and release any thread-waiting UI prompts when their screen is
unmounted.
