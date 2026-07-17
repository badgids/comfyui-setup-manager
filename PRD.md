# Product Requirements Document: ComfyUI Setup Manager

**Product owner and creator:** Alan Guice (Badgids)  
**Repository:** <https://github.com/badgids/comfyui-setup-manager>  
**License:** Apache License 2.0  
**Document version:** 1.0  
**Target release:** 0.8.7

## 1. Product summary

ComfyUI Setup Manager is a standalone cross-platform program that makes ComfyUI installations portable, reproducible, inspectable, updateable, and easy to share.

The product serves two equal user groups:

1. people using a modern Textual terminal interface;
2. scripts, automation systems, and coding agents using a complete non-TUI CLI.

The two interfaces must provide the same capabilities and use the same underlying service modules.

## 2. Problem statement

A complex ComfyUI installation can include core source, a Python environment, platform-specific PyTorch, custom nodes, compiled acceleration packages, models, workflows, external paths, and local changes. Reproducing or updating that setup manually is difficult and risky.

Users need one manager that can:

- find existing installations;
- create new installations;
- reproduce a shared setup;
- preserve plugin and workflow choices;
- select compatible wheels or build missing packages;
- update core safely;
- roll back after failure;
- expose every operation to automation.

## 3. Goals

### 3.1 Primary goals

- Complete TUI and CLI feature parity.
- Cross-platform operation on Windows, WSL2, Linux, and macOS.
- Portable setup profiles and workflow packs.
- Official accelerated PyTorch selection.
- Extensible YAML configuration.
- Safe updates with lightweight snapshots and rollback.
- Clear UI/UX for beginners and experts.
- Machine-readable JSON and YAML outputs.
- Thorough tests and linked documentation.

### 3.2 Non-goals

- Hosting models, LoRAs, checkpoints, or third-party plugin repositories.
- Replacing ComfyUI's native workflow format.
- Claiming unsupported GPU libraries work on every platform.
- Silently changing unrelated system repositories or shell configuration.
- Guaranteeing compatibility of every future third-party node.

## 4. Personas

### 4.1 New user

Needs to install ComfyUI and a known setup without understanding Python packaging.

### 4.2 Experienced creator

Maintains several installations and wants separate stable, experimental, and project-specific environments.

### 4.3 Setup publisher

Wants to export a reproducible setup or workflow pack for other users.

### 4.4 Automation user

Needs deterministic commands, structured output, stable exit codes, and no TUI dependency.

### 4.5 Developer

Needs architecture documentation, test coverage, safe development instructions, and release tooling.

## 5. Functional requirements

### FR-1: Installation discovery

- Detect common ComfyUI locations.
- Accept additional scan roots.
- Use bounded scanning.
- Inspect installations without importing custom nodes.
- Report Git source, version, environment, nodes, workflows, and metadata.

### FR-2: Instance management

- Launch in foreground or background.
- Create launchers inside each installation only.
- Stream logs live.
- Rotate the active runtime log without deleting prior output.
- Browse, search, view, copy, and delete archived runtime logs while protecting the active file.
- Automatically remove archived logs after a configurable Daily, Weekly, Monthly (30-day default), or custom retention period.
- Stop a recorded process.
- Edit name and description.
- Safely uninstall with explicit confirmation.

### FR-3: Setup installation

- Choose existing, official, profile, or custom repository policy.
- Select Python and accelerator.
- Select custom nodes and compiled packages.
- Install system prerequisites when permitted.
- Create the ComfyUI `.venv` with uv.
- Install core and Manager requirements.
- Reconcile captured core, Manager, and custom-node requirement manifests against the package versions verified in the working source environment before invoking uv; never submit a manifest pin and exact constraint that cannot both be satisfied.
- Re-apply profile constraints after node and acceleration installs.
- Audit and repair missing or incompatible transitive Python dependencies.
- Run ComfyUI's custom-node quick validation and fail the installation if selected nodes still cannot import.
- Generate local launchers.
- Validate and write an installation report.

### FR-4: PyTorch

- Non-CPU installs must use an appropriate official accelerated PyTorch distribution.
- Verify the installed backend.
- Reject silent CPU fallback for a GPU setup.
- Keep release choices editable in YAML.

### FR-5: Wheel resolution

- Use editable YAML source definitions.
- Label sources Official, 3rd Party, or Custom/Local.
- Match Python, OS, architecture, Torch, accelerator, CUDA/ROCm, ABI, and version.
- Prefer compatible local backups.
- Offer add source, edit sources, source build, or cancel when unresolved.
- Register saved local builds automatically.

### FR-6: Setup profiles

- Exact exports record the immutable ComfyUI commit and every installed Python distribution, including transitive packages.
- Custom-node export is reference-first. At first startup each UTC day, refresh the official Comfy Registry and every maintained ComfyUI-Manager `node_db` channel (`dev`, `new`, `legacy`, `forked`, and `tutorial`), including `custom-node-list.json` and `extension-node-map.json`. Resolution also uses local Manager caches/snapshots, configured catalogs, node metadata, and Git. Known public nodes are represented only by Manager/Registry IDs and/or validated repositories in `custom_nodes.yml`; only genuinely unresolved plugins may use sanitized embedded source.
- New exports do not archive local wheels, installed libraries, or virtual environments. Exact versions and provenance are recorded in `environment-lock.yaml`; installation resolves them through approved package indexes, configured wheel sources, public Git repositories, Manager/Registry lifecycles, or source-build rules. Legacy embedded-wheel profiles remain installable.
- The ComfyUI core is reconstructed from its referenced repository plus a minimal diff overlay. Export compares the working tree with a fetchable upstream/base revision and stores only allowed changed/new files and deletions. Custom nodes, models, environments, runtime data, generated launchers, shared-path configuration, and node-created content are excluded from the core overlay.
- Exact export fails on an incomplete/ambiguous Python inventory, unresolved node when fallback embedding is disabled, unsafe unresolved payload, or unmade duplicate-path choice. A known public node without local Git metadata is represented by its Manager/Registry/Git identity rather than treated as an export failure or copied into the archive.
- Installation validates exact runtime compatibility before modifying the target, installs the exact exported PyTorch backend, reconciles captured dependency manifests with the verified source-environment versions, applies the full environment lock as constraints for every package operation, reinstalls locked source-tree distributions after their ComfyUI/custom-node sources are acquired, reapplies the lock afterward, and verifies the final versions. A stale or stricter repository manifest must not create an unsatisfiable pair with a version known to work in the exported environment; every override is visible and auditable.
- Support built-in, imported, YAML, JSON legacy, and `.comfyuisetup` inputs.
- Export working installations and inventories.
- Let users inspect and transactionally edit UTF-8 files inside an imported `.comfyuisetup`; regenerate integrity data and replace the archive only after complete validation.
- Default project exports to `profiles/`, migrate non-conflicting files from the former `setups/` name, and retain a compatibility API alias.

### FR-7: Workflows

- Preserve native JSON for individual workflows.
- Preserve complete directory structures in packs.
- Support README, previews, metadata, and setup references.
- Validate archive paths and checksums.
- Never silently install models or public plugin code.
- Document by-hand creation and checksum-safe editing of native JSON workflows and `.comfyworkflows` packs.

### FR-8: Updates and rollback

- Fetch official ComfyUI through a dedicated remote.
- Treat normal ComfyUI core files and direct core-library evolution as reviewable changes, not blockers by themselves.
- Preflight Python ABI, package metadata, compiled non-core/system libraries, accelerator stack, profile-owned packages, custom nodes, Git history, local changes, and baseline `pip check`. Run a protected resolver dry run only when a core package actually needs to change.
- Show every changed core file and direct core package requirement; let users select reviewed package changes.
- Offer Safe update, Try to patch current setup, Continue anyway, Create new install, and Abort update.
- Recommend a separate ABI-tagged installation when protected compatibility cannot be proven.
- Create a lightweight snapshot before the first mutation.
- Never resolve packages for an already-current or file-only update. Parse only selected core requirements whose installed versions must change; never run upstream manifests independently as blind pip installs.
- Protect every installed non-core distribution and every unselected core distribution. Normalize duplicate canonical installed metadata to Python's effective version before writing constraints.
- Treat custom-node manifests as audit and direct-conflict evidence, not as a fresh global install recipe. Use installed distribution metadata, `pip check`, and startup/import validation as the authoritative compatibility checks.
- Keep Patch and Continue available for a pending update after a protected dry-run failure; snapshot and rollback rules still apply.
- Keep all update actions visible and focusable above the footer independently of review scrolling.
- Require candidate `pip check`, server-ready startup, and custom-node/import validation.
- Automatically restore the snapshot after every failed mutated candidate and report rollback validation separately.

### FR-8A: Profile version and ABI identity

- Every new `.comfyuisetup` export records a normalized PEP 440 profile version.
- Exact profiles record full Python, accelerator-runtime, and PyTorch build versions plus a stable combined ABI tag.
- The tag and readable values appear in profile names, profile selectors, library details, archive metadata/readme, and installation reports.
- Legacy profiles remain readable; missing ABI metadata is derived when possible and clearly marked as a compatible target rather than exact identity.

### FR-9: TUI

- The top-level tab order is fixed: Launch & Manage, Setup & Install, Nodes & Plugins, Workflows, Models, LoRAs, Agents & Skills, MCPs.
- Each tab contains only controls, inventory, actions, and configuration for its named domain.
- The application shell must render before hardware probing, Git commands, bounded filesystem discovery, or asset indexing begins.
- A visible progress dialog must identify the current stage of first-launch and manual scans.
- Completed scans are cached in `runtime-state.yaml`; ordinary startup reads YAML instead of rescanning.
- Users can explicitly **Rescan** filesystem state or **Reload YAML** after configuration-only changes.
- Compact responsive layout with no clipped controls in supported terminal sizes, including the Setup **Continue** action.
- Gruvbox default plus Dark and Light, with Vim/Neovim theme import.
- Mouse, arrows, scrollbars, and Vim navigation.
- Long View Contents and Review Installation pages use dedicated visible scroll regions while action buttons remain reachable.
- Embedded command output rendered literally.

### FR-10: CLI and automation

- Every TUI action has a CLI command.
- Text, JSON, and YAML output.
- No prompt required for ordinary automation.
- Explicit confirmations for destructive actions.
- Stable exit codes.
- Progress on stderr and result on stdout for long operations.
- Capability discovery command.

### FR-11: Configuration

- All manager-editable configuration and cached runtime state use YAML.
- Native workflows remain JSON.
- Atomic writes.
- User edits survive default updates when merge policy permits.
- CLI can list, show, edit, set, and validate configuration.
- Dedicated YAML files exist for LoRA sources, runtime scan state, agent-skill targets, and MCP definitions.

### FR-12: Documentation and development

- GitHub-compatible README with clickable table of contents.
- Linked detailed documentation.
- Function reference, PRD, architecture, testing, troubleshooting, and security documents.
- `.claude` development workspace.
- A portable `skills/` directory containing operator, developer, and release skills that compatible AI agents can install.
- Apache 2.0 licensing and creator attribution.

## 6. Non-functional requirements

### NFR-1: Safety

No destructive operation may rely on an ambiguous path or implicit confirmation.

### NFR-2: Portability

No hardcoded username, home directory, drive letter, ComfyUI path, or fixed toolkit location.

### NFR-3: Observability

Long operations must stream commands and output. Reports must identify completed steps, warnings, and failures.

### NFR-4: Testability

Service logic must be callable without Textual. Tests must use temporary fixtures and mocks instead of a real user installation.

### NFR-5: Accessibility

Language must be direct and explain technical terms. Controls must be keyboard and mouse accessible.

All long text/output widgets must derive semantic syntax colors from the active theme while retaining a plain-text document model. Copying or caching selected output must never include ANSI/OSC escapes, Rich markup, or color codes.

### NFR-6: Maintainability

Shared behavior belongs in service modules. TUI event handlers and CLI handlers should remain thin.

## 7. Data formats

- YAML: manager configuration and current setup profiles.
- JSON: native ComfyUI workflows and machine reports where compatibility requires JSON.
- ZIP-compatible `.comfyuisetup`: portable setup profile.
- ZIP-compatible `.comfyworkflows`: workflow pack.
- TGZ: sanitized inventory.

## 8. Acceptance criteria for 0.8.7

- Version metadata, launchers, documentation, tests, and package metadata report `0.8.7`.
- The eight top-level tabs appear in the exact required order and each tab is domain-specific.
- Constructing the TUI does not call platform detection, installation discovery, Git probing, node/workflow scans, or asset indexing.
- First launch renders the TUI and then displays staged scan progress.
- A completed scan writes `runtime-state.yaml`; subsequent startup reads it without a full scan.
- Manual **Rescan** and **Reload YAML** actions are both available and have distinct behavior.
- The Setup action grid renders all buttons and **Continue** remains visible at supported compact sizes.
- A local `.whl` file or local wheel directory can be registered from the recovery panel without HTTPS validation; remote sources still require public HTTPS.
- Every real filesystem path/file field provides lightweight autocomplete and a keyboard-accessible TUI **Browse…** picker.
- Portable profile exports default to `<project-root>/profiles/`, while an explicit destination remains supported in the TUI and CLI. Non-conflicting files migrate from the former `setups/` directory without overwriting conflicts.
- Successful setup imports and exports refresh every mounted profile selector and select the new profile immediately.
- The Profile Library can remove imported profiles after confirmation, while built-in and command-line profiles remain protected.
- The Profile Library can edit UTF-8 members inside imported `.comfyuisetup` archives; a separate rebuilt candidate regenerates checksums and replaces the original only after validation. CLI file-list/read/edit commands call the same service.
- **View contents** renders shared/external workflow paths safely instead of assuming every workflow is under the selected ComfyUI root.
- **View contents** and **Review installation** provide dedicated stable-scrollbar content viewports that expose all content at compact terminal sizes.
- Every text entry and textual output surface—including paths, commands, inventories, reviews, diagnostics, logs, and installation consoles—is focusable, scrollable where needed, mouse-selectable, and keyboard-selectable. `Ctrl+A`/`Cmd+A` selects all text in the focused field, `Ctrl+C`/`Cmd+C` copies the selection, and `Shift+Arrow` extends or contracts the selection.
- Logs, commands, requirements, paths, URLs, inventories, YAML, JSON, and supported source files use syntax roles derived from the active theme. Clipboard/cache text remains sanitized plain text without styling escape codes.
- Linked documentation explains manual creation and editing of `.comfyuisetup`, native workflow JSON, and `.comfyworkflows`, including canonical extensions, authoritative members, checksums, validation, and safe archive paths.
- Launch & Manage provides **Clear output** and **Browse logs**; clearing archives the previous active log and starts a fresh file without deleting history.
- The log browser can search, view, copy, refresh, and delete archived logs while protecting the active log.
- Archived log cleanup defaults to Monthly/30 days and supports Daily, Weekly, and custom-day retention in both TUI and CLI.
- Managed background launches preserve live unbuffered output across active-log rotations.
- Core setup installs `manager_requirements.txt` when present, package-index installs honor profile constraints, captured manifests are reconciled against the verified working package set before uv resolution, and dependency reconciliation runs before validation.
- Exact profile creation records all installed distributions rather than only top-level packages or a critical-package shortlist.
- Exact profiles include `environment-lock.yaml`, `custom_nodes.yml`, and dependency-manifest metadata. New exports do not package local wheels or installed libraries; they record exact versions and resolve artifacts through configured/public sources.
- Dirty, private, untracked, local-only, or unpushed ComfyUI core changes are represented as a minimal repository-relative overlay, never as a full checkout snapshot. Custom nodes are first resolved through daily Registry/Manager data, Manager IDs, public repositories, local Manager snapshots/caches, configured catalogs, and node metadata. Only nodes that remain unresolved after all sources are exhausted are embedded; a known public node is never copied merely because its local folder lacks Git metadata.
- Exact environment locks reject a different Python minor, operating-system family, architecture, or PyTorch accelerator/runtime family; preserve the source official PyTorch runtime/index and managed accelerator distribution identity; and produce a zero-difference package-name/version audit before success.
- Exact profile creation detects duplicate node identities across configured roots, displays every complete source path, and requires an explicit keep/omit choice instead of failing or silently discarding one. The CLI supports repeatable `--omit-node` paths for deterministic automation. Ambiguous duplicate installed Python distributions still fail because no reliable active distribution can be selected.
- Distributions installed from ComfyUI or custom-node source trees retain safe target-relative source paths and editable-install semantics.
- Exact inventory collection includes unpublished local plugins by default, recognizes configured external custom-node roots, resolves public nodes before embedding, and refuses conversion only when a required unresolved local payload is missing.
- Exact ComfyUI and recoverable node refs remain mandatory during installation even when a general compatibility option requests unpinned source refs. Public nodes without a recoverable ref still use their recorded Manager/Registry id or GitHub repository.
- The Badgids profile restores NumPy compatibility and the missing `click` package; the installer does not report success while selected custom nodes still fail startup import validation.
- Models excludes the `loras/` subtree; LoRAs uses its own inventory, source YAML, tasks, and CLI command.
- Nodes & Plugins and Workflows show only their own inventory and actions.
- Agents & Skills can list and install bundled `SKILL.md` directories into configured agent targets.
- MCPs can list and edit portable definitions without automatically starting servers.
- `comfyui-setup-manager capabilities` lists every TUI feature and CLI equivalent.
- All listed CLI commands parse and provide help; JSON and YAML output remain valid.
- Update/rollback, workflow/profile round trips, wheel-source validation, packaging, documentation links, and safety tests pass.
- Update review distinguishes core changes from protected-environment breakage, supports selective core packages, and automatically restores the previous working snapshot after resolver, `pip check`, or startup/import failure.
- New profiles validate PEP 440 versions and display Python/CUDA-or-ROCm/PyTorch ABI tags everywhere profiles are selected or inspected.
- The wheel contains profiles, stylesheet, all editable YAML defaults, and bundled Agent Skills.
- No prohibited private source reference, personal path, generated cache, or stale release artifact appears in the source archive.
- Apache 2.0 license and Alan Guice (Badgids) attribution are present.

## 9. Roadmap

### 1.0

- Native validation on Windows, WSL2, Linux, and macOS.
- Stable public profile schema.
- Stable CLI contract and shell completion.
- Signed release artifacts.

### Later

- Optional profile registry indexing.
- Richer dependency compatibility reports.
- Remote machine orchestration through an external automation layer.
- Plugin health history and update-channel policies.

## Shared asset libraries

### Problem

Models, LoRAs, VAEs, encoders, and workflows are often copied into every ComfyUI installation. Large duplicate files waste storage and make upgrades or testing separate installations difficult.

### Required behavior

1. The manager recommends one external models library and one external workflows library.
2. The user can choose existing directories or ask the manager to create them.
3. New managed installations use shared libraries by default unless the user disables the feature.
4. The manager writes or updates `extra_model_paths.yaml` while preserving unrelated user sections.
5. The manager connects `user/default/workflows` to the external workflow directory using a supported platform link.
6. Existing workflows can be migrated after explicit approval.
7. Uninstalling a ComfyUI instance must not remove external libraries.
8. Portable setup profiles describe shared-library policy without embedding machine-specific paths.

### Acceptance criteria

- Two managed ComfyUI installations can load the same external checkpoint and workflow without duplicate copies.
- Applying shared paths twice is idempotent.
- Existing YAML sections survive updates.
- Empty and populated workflow directories follow the selected migration/conflict policy.
- Windows, WSL2, Linux, and macOS path behavior has test coverage or an explicit platform-test note.

## Models workspace

The TUI and CLI must support:

- browsing shared model files;
- imports, downloads, deletion, and task history;
- catalog source and entry management;
- editable YAML inside the TUI and through the system editor;
- safe relative destinations and optional checksums;
- model categories including checkpoints, VAEs, encoders, ControlNet, upscalers, diffusion models, and audio/geometry assets; LoRAs are explicitly excluded and owned by the dedicated LoRAs workspace.

## Workflows workspace

The TUI and CLI must support:

- browsing nested workflow directories;
- native JSON import/export;
- directory-preserving workflow packs;
- workflow source catalogs and downloads;
- delete and task management;
- companion YAML for models, nodes, libraries, and public sources;
- optional embedded or referenced `.comfyuisetup` profiles.

## LoRAs workspace

The Models and LoRAs workspaces are separate. Models must never display or mutate files below the shared `loras/` directory. LoRAs owns `lora-sources.yaml`, its download tasks, imports, exports, deletes, and CLI command group.

## Nodes & Plugins workspace

The workspace selects one ComfyUI installation, displays its cached custom-node/plugin inventory, opens `custom_nodes`, and links to instance inspection. It must not contain model, LoRA, workflow, setup, agent, or MCP controls.

## Agents & Skills workspace

The project bundles Agent Skills under `skills/<skill-id>/SKILL.md`. `agents-skills.yaml` maps compatible agents to configurable skill directories. Installation copies one validated skill directory and requires explicit overwrite when a destination exists.

## MCP workspace

`mcps.yaml` stores portable MCP server definitions. The workspace lists and edits definitions only; starting or supervising MCP servers is outside this release.

## Portable manifest requirements

A `.comfyuisetup` archive may include `profile.yaml`, `environment-lock.yaml`, `custom_nodes.yml`, `dependency-manifests.yml`, copied dependency manifests, a compact `comfyui_overlay/`, model/workflow/source/library YAML, metadata, documentation, and sanitized source only for unresolved local plugins. Public Manager/Registry/Git nodes remain compact source descriptors. New profiles do not archive virtual environments, installed libraries, public node trees, or local wheels.

A `.comfyworkflows` archive may include workflows, README files, previews, metadata, dependency YAML, and setup references.

Neither format may silently embed large models, complete virtual environments, outputs, credentials, or machine-specific shared-library paths. New exports may embed only genuinely unresolved local-plugin payloads after Registry, Manager, Git, configured-catalog, and public-source resolution has failed; every such payload must be explicitly identified in the manifest and pass safety/checksum validation. New exports do not embed local wheels or site-packages. Legacy imported profiles may still contain checksum-verified embedded wheels for backward compatibility. Known Manager/Registry/Git nodes must not be embedded solely because their installed directory is unpinned or lacks `.git` metadata.
### Dependency reconstruction acceptance criteria

- Repository-backed custom nodes must be acquired directly without Comfy Manager performing a second dependency solve; Manager-only acquisition must disable dependency installation.
- A complete exact environment lock must be installed once. Node manifests remain auditable, but dependency-only requirements/install scripts must not mutate the reconstructed environment.
- Source-built acceleration packages must use recorded/public source provenance, the target Python/PyTorch/CUDA ABI, and post-build version/import verification.
