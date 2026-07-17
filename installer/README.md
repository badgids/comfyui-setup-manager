# ComfyUI Setup Manager

**Author and creator:** [Alan Guice (Badgids)](https://github.com/badgids)  
**License:** [Apache License 2.0](LICENSE)  
**Version:** 0.8.7

ComfyUI Setup Manager is a standalone application for installing, launching, updating, repairing, inspecting, exporting, importing, and sharing ComfyUI installations, models, and workflows.

It provides:

- a compact Textual terminal interface for people;
- a complete non-TUI command-line interface for scripts and automation;
- portable YAML-based setup profiles;
- shared external model and workflow libraries;
- update snapshots and rollback;
- a YAML-backed runtime cache for fast startup and explicit rescans;
- dedicated workspaces for nodes, workflows, models, LoRAs, Agent Skills, and MCP definitions;
- editable source catalogs for models, LoRAs, workflows, Python wheels, and repositories;
- filesystem autocomplete and a built-in TUI file browser for path and file fields;
- a project-local `profiles/` directory for portable profile exports, with migration from the former `setups/` name and immediate profile-library refresh;
- managed runtime-log rotation, browsing, search, clipboard copy, deletion, and configurable retention;
- dependency reconciliation and ComfyUI custom-node startup validation before an installation is marked complete;
- selectable, scrollable, theme-derived syntax coloring in every path/input, console, inventory, review, and diagnostic output surface, with normal mouse and keyboard copy shortcuts and plain-text clipboard output.

Every important TUI action has a CLI command. CLI output can be text, JSON, or YAML.

## Table of contents

- [Requirements](#requirements)
- [Install the manager](#install-the-manager)
- [Important: use shared model and workflow libraries](#important-use-shared-model-and-workflow-libraries)
- [Start the TUI](#start-the-tui)
- [Use the CLI without the TUI](#use-the-cli-without-the-tui)
- [Main tabs](#main-tabs)
- [Common tasks](#common-tasks)
- [Portable setup and workflow files](#portable-setup-and-workflow-files)
- [Editable YAML configuration](#editable-yaml-configuration)
- [Documentation](#documentation)
- [Testing and development](#testing-and-development)
- [Security and privacy](#security-and-privacy)
- [License](#license)

## Requirements

The install scripts check for missing prerequisites and install them when the operating system has a supported package manager.

### Required

| Requirement | Minimum | Purpose |
|---|---:|---|
| Python | 3.10 | Runs the manager and creates managed environments |
| Git | Supported current release | Downloads and updates ComfyUI and public custom nodes |
| Internet access | When downloading | Gets public packages, repositories, models, workflows, and wheels |

### Required only for source builds

| Requirement | Purpose |
|---|---|
| C/C++ compiler | Builds native Python extensions |
| CMake and Ninja | Configure and build native projects |
| CUDA toolkit | Builds NVIDIA extensions when no matching wheel exists |
| ROCm development tools | Builds supported AMD extensions |
| Visual Studio Build Tools | Native builds on Windows |
| Xcode Command Line Tools | Native builds on macOS |

The bootstrap understands common package managers such as `apt-get`, `dnf`, `pacman`, Homebrew, and Windows `winget`. Unsupported systems receive exact instructions instead of hidden changes.

See [Getting started](docs/getting-started.md) and [Wheels and source builds](docs/wheels-and-builds.md).

## Install the manager

### Linux, WSL2, and macOS

```bash
chmod +x install.sh comfyui-setup-manager
./install.sh
```

### Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

### Windows Command Prompt

```bat
install.cmd
```

The manager creates its private environment in `installer/.venv`. It does not require a global Python package installation.

## Important: use shared model and workflow libraries

A checkpoint can use many gigabytes. Copying the same checkpoint, LoRA, VAE, text encoder, or workflow into every ComfyUI installation wastes storage and makes setups harder to maintain.

The manager strongly recommends one external models directory and one external workflows directory:

```text
ComfyUI-Shared/
├── models/
│   ├── checkpoints/
│   ├── loras/
│   ├── vae/
│   ├── text_encoders/
│   ├── controlnet/
│   └── ...
└── workflows/
    ├── image/
    ├── video/
    └── audio/
```

For each managed ComfyUI installation, the manager:

1. creates the shared directories if needed;
2. writes or updates ComfyUI's `extra_model_paths.yaml` file;
3. preserves unrelated user sections in that YAML file;
4. connects `user/default/workflows` to the shared workflow library;
5. records the relationship in `.comfy-setup/shared-assets.yaml`;
6. keeps the shared libraries when a ComfyUI installation is removed.

Shared paths are enabled by default during installation. The TUI walks the user through selecting paths and optionally migrating existing workflows.

Read this before managing several installations: [Shared models and workflows](docs/shared-assets.md).

CLI example:

```bash
./comfyui-setup-manager shared-paths configure \
  --models-dir /data/ComfyUI/models \
  --workflows-dir /data/ComfyUI/workflows

./comfyui-setup-manager shared-paths apply /path/to/ComfyUI
```

## Start the TUI

```bash
./comfyui-setup-manager
```

The main interface contains these tabs, in this order:

1. **Launch & Manage** — launch, stop, inspect, update, roll back, edit, or remove installations.
2. **Setup & Install** — install a new setup or apply a portable profile.
3. **Nodes & Plugins** — inspect installed custom nodes and plugins for one installation.
4. **Workflows** — manage workflow JSON, packs, catalogs, tasks, and sources.
5. **Models** — manage non-LoRA models, imports, downloads, exports, tasks, and sources.
6. **LoRAs** — manage LoRA files and catalogs independently from Models.
7. **Agents & Skills** — review and install bundled portable AI-agent skills.
8. **MCPs** — review and edit portable MCP server definitions.

On the first launch, the interface renders before the full system scan begins. A progress dialog explains each scan stage. Later launches read `runtime-state.yaml`; use **Rescan** after filesystem changes or **Reload YAML** after configuration-only changes.

When an installation is detected, **Launch & Manage** is the default tab. When none is detected, **Setup & Install** opens first.

See [Textual interface guide](docs/tui-guide.md).

## Use the CLI without the TUI

Show help:

```bash
./comfyui-setup-manager --help
```

Show the complete feature map:

```bash
./comfyui-setup-manager capabilities
```

Machine-readable output:

```bash
./comfyui-setup-manager --format json installations discover
./comfyui-setup-manager --format yaml models list
./comfyui-setup-manager installations logs /path/to/ComfyUI --list-files
./comfyui-setup-manager installations logs /path/to/ComfyUI --search "IMPORT FAILED"
./comfyui-setup-manager installations logs /path/to/ComfyUI --clear
./comfyui-setup-manager installations logs /path/to/ComfyUI --retention monthly
```

See [Complete CLI reference](docs/cli-reference.md) and [Automation and agents](docs/automation-and-agents.md).

## Main tabs

### Launch & Manage

Launch, stop, inspect, update, roll back, rename, rescan, or uninstall a selected ComfyUI instance. Runtime output stays in this workspace. **Clear output** rotates the active file without deleting previous output, while **Browse logs** searches, views, copies, and deletes archived logs and configures Daily, Weekly, Monthly (30-day default), or custom retention. Shared workflows outside the ComfyUI checkout are shown as external absolute paths. The long contents list has its own visible scrollbar so action buttons remain reachable.

### Setup & Install

Choose a setup profile and installation target, import/export profiles, edit UTF-8 files inside imported `.comfyuisetup` archives, remove imported profiles, reload YAML, and continue through the installation wizard. Profile exports default to `profiles/`; non-conflicting files in the former `setups/` directory migrate automatically, and imported or exported profiles become selectable immediately. Internal edits rebuild and validate a separate archive before replacement. The responsive action grid keeps **Continue** visible, and Review Installation has a dedicated scrollable viewport for long plans. Core, Manager, node, and acceleration requirements are reconciled under profile constraints, then selected custom nodes are loaded through ComfyUI's quick validation before success is reported.

### Nodes & Plugins

Choose one installation and inspect only its cached custom-node/plugin inventory. Open `custom_nodes`, view the instance, or run a rescan after external changes.

### Workflows

Browse nested workflow files, import/export native JSON, create/install `.comfyworkflows` packs, manage workflow catalogs and tasks, and edit `workflow-sources.yaml`.

### Models

Browse, import, export, download, delete, and catalog non-LoRA model files. The Models inventory explicitly excludes the `loras/` subtree.

See [Model library and downloads](docs/models.md).

### LoRAs

Browse, import, export, download, delete, and catalog LoRAs through `lora-sources.yaml` and the dedicated `loras` CLI command.

### Agents & Skills

List the bundled operator, developer, and release skills; install them into a configured agent directory; or edit `agents-skills.yaml`. The repository-level `skills/` directory follows the portable Agent Skills `SKILL.md` layout.

### MCPs

List portable MCP definitions and edit `mcps.yaml`. The manager stores definitions only; it does not automatically start MCP servers.

## Common tasks

### Find installations

```bash
./comfyui-setup-manager installations discover
```

### Inspect an installation

```bash
./comfyui-setup-manager --format yaml installations contents /path/to/ComfyUI
```

### Install a setup

```bash
./comfyui-setup-manager install run \
  --profile vanilla-comfyui \
  --target /path/to/ComfyUI \
  --repository-mode official
```

### List shared models, LoRAs, and workflows

```bash
./comfyui-setup-manager models list
./comfyui-setup-manager loras list
./comfyui-setup-manager workflows list
```

### Import a local model

```bash
./comfyui-setup-manager models import ./model.safetensors --destination checkpoints
```

### Export a working setup

```bash
./comfyui-setup-manager profiles from-installation /path/to/ComfyUI \
  --name "My Setup" \
  --publisher "Your Name"
```

The default exact export records a reconstruction plan from the working installation: repository references, a minimal core diff overlay, public custom-node identities, captured dependency manifests, and the verified final package versions. The source checkout may be dirty, private, local-only, or unpushed; only genuine core differences are included, while public custom nodes remain references and unresolved local plugins are the last-resort payload.

When the same custom-node destination identity exists in more than one configured custom-node root, an interactive export lists every complete path and asks which copy or copies to omit. Automation can make the same deterministic choice with repeatable `--omit-node /complete/path/to/node` arguments.

### Create a workflow pack

```bash
./comfyui-setup-manager workflows pack ./workflows \
  --name "My Workflow Pack" \
  --requirements-yaml ./models.yaml \
  --requirements-yaml ./nodes.yaml \
  --output ./my-workflows.comfyworkflows
```

### Update with rollback protection

```bash
./comfyui-setup-manager updates check /path/to/ComfyUI
./comfyui-setup-manager updates run /path/to/ComfyUI --strategy safe
./comfyui-setup-manager updates run /path/to/ComfyUI --strategy patch
```

The updater reviews changed core files and direct core libraries without treating normal ComfyUI evolution as a failure. It protects every installed non-core package and every unselected core package. It never runs upstream requirement files independently, never resolves packages for an already-current or file-only update, and installs only reviewed core libraries that actually need to change. Custom-node manifests are checked for direct conflicts but are not treated as a fresh global install recipe. Every candidate must pass `pip check` and full startup/import validation; any failed mutation is rolled back automatically. The fixed TUI action dock offers **Safe update**, **Try to patch current setup**, **Continue anyway**, **Create new install**, and **Abort update**. See [Updates and rollback](docs/updates-and-rollback.md).

### Profile versions and ABI identity

New `.comfyuisetup` exports store a normalized PEP 440 version and a combined Python/accelerator/PyTorch ABI tag such as `cp312-cuda124-torch2_6_0_cu124`. The profile selector and Profile Library show the creation date/version, exact compatibility values, and tag so two visually similar builds are not mistaken for binary-compatible environments. See [PEP 440 versions and ABI compatibility tags](docs/abi-compatibility-tags.md).

## Portable setup and workflow files

| File type | Purpose |
|---|---|
| `.comfyuisetup` | Portable YAML-based setup archive containing ComfyUI, nodes, Python requirements, shared-library policy, model/workflow manifests, and source catalogs |
| `.json` | One native ComfyUI workflow |
| `.comfyworkflows` | ZIP-compatible workflow archive preserving directories and optional README, metadata, requirements YAML, previews, and setup references |
| `.tgz` inventory | Sanitized inventory used to create a portable profile |

A portable setup can include these YAML documents:

```text
profile.yaml
environment-lock.yaml
custom_nodes.yml
dependency-manifests.yml
dependency_manifests/
comfyui_overlay/
models.yaml
workflows.yaml
asset-sources.yaml
libraries.yaml
metadata.yaml
embedded_plugins/        # unresolved plugins only
```

Exact exports are reconstruction manifests, not checkout or virtual-environment backups. The manager refreshes the official Comfy Registry and the maintained ComfyUI-Manager `node_db` at most once per UTC day, then resolves every installed plugin through local Manager metadata/snapshots, Registry IDs, legacy Manager records, configured catalogs, node metadata, and Git repositories. Resolved nodes are written only to `custom_nodes.yml` as compact Manager/Registry/Git descriptors. A node payload is sanitized and embedded only after every resolver source fails.

The ComfyUI checkout is recreated from its referenced repository. `git diff` is used to add a compact `comfyui_overlay/` containing only changed/new core files and deletions relative to the fetchable base revision; `custom_nodes`, environments, models, user/runtime data, generated launchers, shared-path files, and files supplied by node installation are excluded. `environment-lock.yaml` records every installed distribution and exact working versions. During reconstruction, a node with a validated Git repository is cloned directly; its Manager/Registry ID remains provenance and a fallback only when no repository is available. Manager-only acquisition uses `--no-deps`, never `--uv-compile`, so it cannot perform a second global solve or replace the exported PyTorch, NumPy, or other locked packages. Managed accelerator lifecycles preserve the exact distribution identity from the working lock (for example `cupy-cuda12x`, not an inferred replacement family), and native source-build environments install generic tools before reapplying the exact target PyTorch stack so build tooling cannot violate PyTorch's own constraints. A complete exact environment lock is installed once. Node manifests are still copied, reconciled, and audited, but are not installed again. Dependency-only `install.py` scripts are skipped for exact profiles, while required lifecycle scripts such as `comfy-env.toml` bootstrap continue to run. `requirements*.txt`, `pyproject.toml`, `uv.lock`, `manager_requirements.txt`, and related dependency manifests from ComfyUI and its nodes are copied under `dependency_manifests/` for comparison and audit, not as source backups. During installation, captured manifests are reconciled before they are submitted to uv: when a manifest requests a version that contradicts the package version proven to work in the exported environment, the generated install manifest uses the verified version and records the override in the console/report instead of passing an impossible requirement-plus-constraint pair. Local wheels and installed libraries are not archived by new exports; public indexes, configured wheel sources, public Git repositories, Manager/Registry installation, and source-build rules are preferred. Legacy profiles containing embedded wheels remain readable. Exact export never silently chooses between duplicate node copies: it requires an explicit keep/omit decision and records it. Large models and machine-specific paths are never exported. By default, profile files are written to `<project-root>/profiles/`; pass an explicit output path to use another directory.

See [Setup profiles](docs/setup-profiles.md), [Profile format](PROFILE_FORMAT.md), [Workflow format](WORKFLOW_FORMAT.md), [Manual profile and workflow authoring](docs/manual-profile-workflow-authoring.md), and [Inventory and export](docs/inventory-and-export.md).

## Editable YAML configuration

All manager-controlled configuration is YAML:

```text
asset-paths.yaml
model-sources.yaml
lora-sources.yaml
workflow-sources.yaml
download-tasks.yaml
runtime-state.yaml
agents-skills.yaml
mcps.yaml
wheel-sources.yaml
pytorch-releases.yaml
repositories.yaml
manager-config.yaml
custom-node-sources.yaml
installations.yaml
themes.yaml
```

Every YAML file can be edited inside the TUI or opened in the system's default editor. The manager validates YAML before using it.

See [YAML configuration](docs/configuration.md) and [Model/workflow source YAML](docs/asset-sources.md).

## Documentation

- [Documentation home](docs/index.md)
- [Getting started](docs/getting-started.md)
- [Textual interface](docs/tui-guide.md)
- [CLI reference](docs/cli-reference.md)
- [Shared models and workflows](docs/shared-assets.md)
- [Model management](docs/models.md)
- [Workflow management](docs/workflows.md)
- [Asset source YAML](docs/asset-sources.md)
- [Setup profiles](docs/setup-profiles.md)
- [Manual profile and workflow authoring](docs/manual-profile-workflow-authoring.md)
- [Updates and rollback](docs/updates-and-rollback.md)
- [PEP 440 versions and ABI compatibility tags](docs/abi-compatibility-tags.md)
- [Wheels and builds](docs/wheels-and-builds.md)
- [Architecture](docs/architecture.md)
- [Function reference](docs/function-reference.md)
- [Product requirements](PRD.md)

## Testing and development

```bash
PYTHONPATH=installer/src python -m pytest -q
bash -n install.sh comfyui-setup-manager collect_comfyui_inventory.sh
```

The project includes `.claude` development instructions and portable skills in `skills/` for Claude Code, Codex, OpenCode, OpenClaude, and compatible agents.

See [Development](docs/development.md), [Testing](docs/testing.md), [Contributing](CONTRIBUTING.md), and [Validation](VALIDATION.md).

## Security and privacy

- no model, LoRA, workflow, output, environment, or credential is committed by default;
- portable profiles omit machine-specific paths;
- public custom nodes are resolved through Manager/Registry metadata and/or a validated GitHub repository; a recovered Manager snapshot or public Git commit is retained when available; only unresolved local plugins are stored as sanitized payloads;
- archive paths are checked before extraction;
- destructive actions require explicit confirmation;
- package and repository sources are restricted by the source policy;
- shared external libraries are not removed when an installation is uninstalled.

Read [SECURITY.md](SECURITY.md).

## License

Copyright 2026 Alan Guice (Badgids).

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for project attribution.
