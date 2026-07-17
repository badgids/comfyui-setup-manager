# Installer design

## Product boundary

ComfyUI Setup Manager is a standalone application. It does not live inside or
depend on a specific ComfyUI checkout.

## Core domains

1. **Discovery** — locate and describe existing ComfyUI installations.
2. **Profile library** — load built-in profiles and platform-specific user imports.
3. **Portable export** — inspect a working setup and create a `.comfyuisetup` manifest.
4. **Installation engine** — install or reuse ComfyUI, apply nodes and dependencies, manage compiled packages, and validate.
5. **Inventory bridge** — collect a sanitized archive on one machine and convert it to a portable profile elsewhere.

## Plugin-source model

Every custom-node entry is reference-first. The exporter must prove that a node cannot be reconstructed before it is allowed to carry source payloads.

### Daily provenance catalog

Once per UTC day, after the TUI has rendered, the manager refreshes the official Comfy Registry plus every maintained ComfyUI-Manager `node_db` channel. Both `custom-node-list.json` and `extension-node-map.json` are cached. The cache is preserved during partial outages, and an explicit Nodes & Plugins rescan may force a refresh.

### Resolved remote node

A normal node stores a Registry/Manager ID, validated repository, or both. Local Manager caches and snapshots, configured catalogs, project metadata, Git metadata, the daily Registry cache, the legacy Manager database, and direct Registry search all participate in resolution. Installed folder names are only one alias because Manager may normalize them from `pyproject.toml`.

The installation engine:

1. Clones a validated Git repository directly whenever one is known; Registry/Manager metadata remains provenance rather than a second dependency solver.
2. Uses Registry/Manager acquisition only when no repository URL is available, and invokes it with `--no-deps`.
3. Applies an immutable ref when one is known and publicly fetchable.
4. Reconciles and audits every captured node manifest against the verified working package set. With a complete exact lock, those dependencies are not installed again.
5. Skips dependency-only `install.py` scripts for exact profiles, but runs required lifecycle/bootstrap scripts such as `comfy-env` and constrains any main-environment pip activity.
6. Preserves the exact accelerator distribution family from the lock (`cupy-cuda12x` versus `cupy-cuda13x`, CPU versus GPU ONNX Runtime, and the recorded TensorRT family) instead of deriving a replacement from target toolkit detection.
7. Prepares generic native-build tools first and reapplies the exact target PyTorch stack last inside isolated compiler environments so PyTorch's ABI and build-tool constraints remain authoritative.

### Embedded unresolved plugin

Embedding is the final fallback only. It is permitted when no Manager/Registry record, configured source, node metadata, Manager snapshot, or cloneable repository identifies the node. The payload is stored under `embedded_plugins/<node-id>/` with a checksum and excludes models, environments, caches, build output, user data, logs, credentials, local-path configuration, symlinks, and oversized files.

A known public node is never copied merely because the installed folder lacks `.git`, is dirty, or uses a Manager-normalized name.

## Core source reconstruction

The main ComfyUI checkout is reconstructed from its configured repository. When no custom repository is declared, the official ComfyUI repository is the reference. Export finds a fetchable base commit and computes a Git name-status diff. Only allowed changed/new files and deletions are written under `comfyui_overlay/`.

The overlay explicitly excludes custom nodes, environments, models, user/runtime data, generated launchers, shared-library path configuration, and roots reconstructed by another installation lifecycle. The manager never converts a dirty checkout into a full source archive. If a reliable base cannot be identified, it records the repository/branch and warns that unidentified core edits cannot be transferred.

## Installation safety

- Non-empty non-ComfyUI targets are rejected.
- Existing custom-node directories are preserved unless explicit updates are enabled.
- Public nodes are acquired from Manager/Registry or validated repositories, not from local backups.
- Core overlays and unresolved fallback payloads are path-validated and checksum-verified.
- Native builds happen in isolated workspaces.
- Models and user data are outside the profile format.
- Commands and output remain visible in the Textual log.
- A failing step does not delete completed work.

## Profile portability

The profile is a declarative reconstruction plan. It contains `custom_nodes.yml`, a complete final package/version lock, copied dependency manifests, exact PyTorch backend metadata, a PEP 440 profile version, a Python/accelerator/PyTorch ABI compatibility tag, and a minimal core overlay. It does not contain absolute installation paths, a virtual environment, installed site-packages, public node source trees, or a complete ComfyUI checkout.

`environment-lock.yaml` records transitive and manually installed packages, direct-source provenance, Python minor, OS, architecture, and exact accelerator/runtime/index. Public index and immutable Git requirements are pinned. Distributions supplied by the reconstructed ComfyUI/custom-node trees retain a safe target-relative source path and editable state. New exports do not create embedded wheel payloads; local-wheel provenance is resolved through configured/public package sources or dedicated build lifecycles. Legacy embedded-wheel profiles remain supported.

## Transactional core updates

Core ComfyUI files and direct core library requirements are displayed for review and are not treated as compatibility failures by themselves. The update manager constrains every installed non-core package and every unselected core package, normalizes duplicate installed metadata to Python's effective distribution, and submits only selected core packages that actually require a version change. Custom-node manifests are checked for direct core conflicts but are not installed as a global recipe. Already-current and file-only updates skip the resolver. A lightweight source/package snapshot is created before checkout; `pip check` and ComfyUI/custom-node startup validation remain mandatory after mutation, and any failure automatically restores and validates the snapshot. Safe, protected-patch, continue-anyway, new-install, and abort choices call the same service implementation from TUI and CLI.

The exporter also carries `requirements*.txt`, `pyproject.toml`, `uv.lock`, `manager_requirements.txt`, and related manifests from ComfyUI and selected nodes. It compares declarations and records conflicts, while the exact versions from the already-working source environment remain authoritative constraints.

Installation validates target compatibility before modifying it, installs the exact PyTorch backend, extracts the captured dependency manifests, reconciles any manifest specifier that rejects the verified working version, applies the lock to core/Manager/node/acceleration operations, installs source-tree distributions after node acquisition, reapplies the lock, repairs dependency metadata, runs ComfyUI quick validation, and verifies the complete reproducible package set before success. Reconciliation is written to generated files under `.comfy-setup/` and logged; the original archive payload remains unchanged.

## Package source boundaries

The manager bootstrap is local-project-first: system Python creates `installer/.venv`, then pip installs the local `installer/` project with official PyPI as the only default package index. No generated lockfile is distributed. Runtime uv and pip subprocesses receive sanitized environments and explicit public indexes. Public source clones are restricted to HTTPS GitHub URLs.

## Responsive Textual interface

The UI uses Textual screen breakpoints rather than fixed terminal dimensions.

- Wide terminals show System, Setup Profile, and ComfyUI Installation in one row.
- Medium terminals reflow the cards without changing the action order.
- Narrow terminals use one-column content with bounded scrolling.
- The dashboard action bar is outside the content scroller so the primary action remains visible.
- Buttons use content-aware or fractional widths instead of fixed minimum widths.
- Wizard headings are compact and reserve screen height for useful controls.

## Theme architecture

The manager registers three built-in Textual themes: Gruvbox, Dark, and Light. Theme preferences and imported palettes are stored in the platform-specific user configuration directory through `platformdirs`.

Vim/Neovim imports produce color-only Textual `Theme` objects. A colorscheme name is resolved through Neovim or Vim when available; a standard `.vim` colors file can also be parsed directly. The importer maps editor highlight groups to application roles such as background, foreground, primary, accent, success, warning, error, surface, panel, and selection.

## v0.7 embedded command execution

`Runner` is the sole process-execution path for installation. It streams stdout/stderr into the Textual console and handles POSIX privilege escalation through a masked in-app secret provider. Installation no longer suspends the application.

The installation screen is both a progress view and a recovery console. After a failure, users can run repair commands in the selected target directory with the target `.venv` on `PATH`, then retry the idempotent installation plan.

## Managed ComfyUI launch command

Each target receives local `comfyui`, `comfyui.ps1`, and `comfyui.cmd` launchers. No global shim is installed, no PATH entry is changed, and no shell profile is edited.


## Primary application tabs

The base Textual screen contains eight strictly separated tabs in fixed order:

1. **Launch & Manage** — instance lifecycle, updates, rollback, metadata, runtime logs, and offline contents.
2. **Setup & Install** — profiles, targets, import/export, and the installation wizard.
3. **Nodes & Plugins** — one installation's custom-node/plugin inventory and actions.
4. **Workflows** — workflow inventory, packs, sources, tasks, import, and export.
5. **Models** — non-LoRA model inventory and operations.
6. **LoRAs** — dedicated LoRA inventory and operations.
7. **Agents & Skills** — bundled skills and configured installation targets.
8. **MCPs** — portable MCP definitions.

Launch & Manage opens first when cached installations exist; Setup & Install opens otherwise. The shell renders from YAML cache before first-launch or explicit scans begin. Long Review Installation and View Contents data is placed in dedicated scrollable viewports so action controls remain visible.

## Instance inventory

The offline overview scans normal and configured `custom_nodes` roots plus native workflow libraries under `user/<name>/workflows`. It reports Git origins and commits where available without importing or executing the plugins.

## Runtime log architecture

Managed background launches run through `log_relay.py`. The relay streams unbuffered combined output to the protected active `.comfy-setup/runtime.log`, responds to safe rotation requests, and archives prior files under `.comfy-setup/logs/`. `log_management.py` owns listing, content search, clipboard-facing reads, safe archived-log deletion, and Daily/Weekly/Monthly/custom retention cleanup. The active log is never deleted by the browser.

## Dependency reconciliation

After core, Manager, custom-node, and acceleration package installation, the engine re-applies profile constraints and audits installed distribution metadata for missing or incompatible requirements. Profile-declared missing-module repairs are validated as package requirements. Final validation executes ComfyUI's quick custom-node loader; selected node import failures make the installation fail instead of producing a misleading success state.

## Accelerated package resolution

The resolver is data-driven through YAML. `pytorch-releases.yaml` maps pinned
PyTorch releases to their official CUDA, ROCm, CPU, and macOS/MPS wheel sources.
`wheel-sources.yaml` contains labeled official, third-party, and local wheel
repositories. The resolver prefers a compatible precompiled wheel, validates its
wheel tags and accelerator markers, then falls back to an isolated source build
only after an explicit user decision. Backed-up builds are written back to the
YAML registry as Custom/Local sources.

## Shared asset installation stage

The installation plan includes a shared-assets stage after node and package installation and before final validation.

1. Load `asset-paths.yaml` or the paths chosen in the wizard/CLI.
2. Create the models root, workflow root, and standard model categories.
3. Write the manager-owned section of `extra_model_paths.yaml` without replacing unrelated sections.
4. Connect the native workflow library to the shared root.
5. Merge public source metadata from the selected profile.
6. Download selected required/suggested model and workflow entries.
7. Record per-instance state in `.comfy-setup/shared-assets.yaml`.
8. Continue to launcher generation and validation.

Failure of a required asset stops installation with recovery choices. Failure of a suggested asset records a warning and allows installation to continue.
