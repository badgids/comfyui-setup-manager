# Release notes — 0.8.7

## Prerequisite and Badgids profile documentation

- Added a canonical cross-platform prerequisite guide covering Python/venv/pip, Git, internet/write access, package-manager behavior, source-build compilers, CMake, Ninja, CUDA/ROCm toolkits, Rust, `pkg-config`, and verification commands.
- Clarified that the Windows bootstrap requires WinGet only when `install.ps1` must install missing Python or Git, and documented App Installer repair plus the manual Python/Git alternative.
- Added a dedicated Badgids Complete profile guide so its NVIDIA CUDA 13/PyTorch ABI, FFmpeg requirement, conditional build toolchain, optional SoX/Tesseract/Rust tools, included nodes, acceleration packages, and asset policy remain profile-local rather than global manager requirements.


## Theme-aware readable output with plain-text copying

- Runtime follow logs, installation/update/snapshot consoles, task output, node/plugin inventories, workflows, models, LoRAs, skills, MCPs, YAML, JSON, and supported source editors now use semantic syntax roles derived from the active built-in or imported theme.
- Logs recognize errors, warnings, successful steps, commands, timestamps, requirements, paths, and URLs. Inventories distinguish names, paths, sizes, sources, and state labels.
- Coloring exists only in Textual's render-time highlight map. The selectable document, copied selection, clipboard/cache, and persisted logs contain sanitized plain text with ANSI, OSC hyperlink, Rich markup, and color escapes removed.
- Large cached inventories and runtime-log tails are inserted in batches so syntax highlighting does not introduce per-line redraw lag.

## Transactional profile file editor

- Profile Library adds **Edit profile files** for imported `.comfyuisetup` archives.
- UTF-8 YAML, JSON, source, README, dependency-manifest, embedded-plugin, and forward-compatible text members can be edited with line numbers and language-aware highlighting; generated metadata, binary payloads, and text over 2 MiB remain protected.
- Save creates a separate candidate, rebuilds the canonical profile, recalculates profile/payload/manifest/wheel integrity data, preserves unknown safe members, and replaces the original only after complete validation.
- Invalid syntax, schema, payload, checksum, or in-library ID changes leave the original bytes untouched.
- CLI parity is available through `profiles files`, `profiles read-file`, and `profiles edit-file`.

## Consistent profile directory and manual authoring

- Project-local portable exports now use `profiles/` instead of `setups/`.
- On first use, non-conflicting legacy files move into `profiles/`; existing same-named destinations are never overwritten, and the old directory is removed only when empty. `setups_directory()` remains a compatibility alias for integrations.
- New linked documentation explains by-hand `.comfyuisetup`, native workflow JSON, and `.comfyworkflows` creation/editing, canonical extensions, authoritative companion files, checksum formulas, safe paths, validation commands, and the difference between a `workflows` directory and a workflow-pack extension.

## Release hardening

- Source-tree managed launches resolve inherited relative Python package paths before switching into the ComfyUI checkout, so the detached runtime-log relay starts reliably from development and portable source environments.
- A required core update package is included automatically when callers use the checked default selection, even if an integration mutates the preflight requirement flag after selection state was built.

## 0.8.6 feature set

## Current-install update and action-accessibility correction

- Already-current installations no longer perform an unnecessary uv dependency dry run. A passing current `pip check` is reported with protected resolution **not needed**.
- File-only updates and compatible manifest-only changes do not invoke pip. Only reviewed core packages whose installed versions fail the target requirement are submitted to the resolver.
- Custom-node requirement files remain audit and direct-conflict evidence, but are not installed as a new global recipe. Installed metadata, `pip check`, and full ComfyUI/custom-node startup validation remain authoritative.
- Duplicate or stale `dist-info` records that normalize to the same project, such as two visible `tensorrt-cu13-libs` versions, are reduced to Python's effective installed distribution before constraints are written.
- Every installed non-core package and every unselected core package is protected during a package change.
- **Try to patch current setup** and **Continue anyway** remain enabled for a real pending update after a protected dry-run failure. They still snapshot first and roll back automatically on failure.
- Update controls now live in a fixed action dock above the footer, independent of the scrolling review and console.

## 0.8.5 feature set

## Transactional customized-install updates

- Official ComfyUI core file and direct core-library changes are now displayed for review and are allowed unless they break a protected part of the environment.
- Preflight reports changed files, selectable direct package changes, protected packages, Python support, current `pip check`, custom-node conflicts, compiled/accelerator ABI risk, profile locks, Git customization, and a protected uv dry run.
- The updater no longer invokes the checkout's `requirements.txt` and `manager_requirements.txt` as separate install commands. It parses selected core requirements and custom-node manifests into one audited resolver input.
- Non-core compiled packages, profile-owned packages, local/direct artifacts, and accelerator packages are constrained at their known-working versions. Core libraries remain eligible to move when the complete plan stays compatible.
- The TUI provides **Safe update**, **Try to patch current setup**, **Continue anyway**, **Create new install**, and **Abort update**, with individual selection of reviewed core package changes.
- Resolver, `pip check`, server-ready, module-import, or custom-node startup failure after mutation automatically restores the source/package snapshot and reports rollback validation.
- Creating a separate install from an unmanaged customized checkout first captures an exact portable profile. Successful-update inventory refresh uses a visible background progress screen.

## PEP 440 profile versions and ABI identity

- New `.comfyuisetup` exports normalize their profile version according to PEP 440.
- Exact profiles record and display complete Python, CUDA/ROCm/CPU/MPS, and PyTorch build identity plus a combined tag such as `cp312-cuda124-torch2_6_0_cu124`.
- The tagged name, readable compatibility values, version, and creation data appear in Setup & Install, Profile Library, `profile.yaml`, `metadata.yaml`, the archive README, and installation reports.
- Legacy profiles remain readable; compatibility metadata is derived when possible and marked as a compatible target when it is not an exact captured ABI.

This patch prevents installation failures and post-install inventory refreshes
from blocking the Textual interface while retaining the exact-profile fixes
from 0.8.3.

## TUI responsiveness correction

- Unexpected installation-worker failures now become an ordinary failed
  `InstallResult`; the recovery shell, Retry, Return Home, and Quit controls are
  restored even when failure happens outside the engine's normal error boundary.
- Closing an installation screen releases pending administrator-password and
  wheel-source prompts so a background worker cannot strand application exit.
- Return Home no longer invokes installation discovery synchronously on the UI
  thread. The dashboard appears immediately and a progress modal reports the
  background inventory refresh.
- The newly installed target is selected only after discovery completes, so the
  dashboard and Launch & Manage tab use the refreshed cache consistently.
- Added TUI regressions for exceptional worker failure, restored controls,
  visible scan progress, asynchronous Home navigation, and final target selection.

## Exact-profile installation correction

- Final environment verification now treats CPU/GPU variants that expose the
  same import package as mutually exclusive providers. An exported NVIDIA
  profile using `onnxruntime-gpu` no longer incorrectly demands a second
  overlapping `onnxruntime` installation merely because stale metadata for both
  existed in the long-lived source environment.
- Existing 0.8.2 exact profiles that recorded `PyOpenGL-accelerate` in the
  authoritative environment lock but omitted its acceleration descriptor are
  repaired automatically during planning and installation.
- New exports include a portable, version-pinned `PyOpenGL-accelerate`
  descriptor for Windows, Linux, and macOS across supported accelerators.
- Regression coverage exercises old-profile recovery, exact exporter output,
  provider-family verification, and rejection of genuinely unexpected packages.

## Same-version node-lifecycle and acceleration-build correction

- Repository-backed custom nodes are now cloned directly instead of first invoking Comfy Manager's global `--uv-compile` resolver.
- Manager/Registry-only acquisition uses `--no-deps`; the verified profile lock and captured manifests remain authoritative.
- A complete exact environment lock is installed once. Custom-node requirements are reconciled and written for audit but are not reinstalled node by node.
- Export records an `install_py_policy`. Dependency-only installers are skipped for exact profiles, preventing scripts such as CuPy bootstrap helpers from replacing a working CUDA-major wheel. Required `comfy-env`/bootstrap lifecycles continue to run.
- Fixed malformed generated Python used to probe torch versions in isolated build environments.
- Exact SageAttention and FlashAttention source fallbacks now build from their configured public repositories, not from a missing package-index source release. Captured Git commits are used when available, build flags match the target CUDA/PyTorch environment, and built wheel versions/imports are verified.
- Exact managed accelerators now preserve the source lock's package family and version, including CuPy, TensorRT, and ONNX Runtime; older 0.8.2 profiles recover this information directly from `environment-lock.yaml`.
- Isolated source-build environments install generic tools first and exact target PyTorch last, preventing an unconstrained setuptools update from invalidating the ABI-compatible build stack.
- Added regression tests for Git-first node acquisition, Manager `--no-deps`, one-time exact-lock application, install-script policy, exact accelerator package identity, source-build preparation order, source provenance, and the SageAttention 2.2.0 failure path.

## Same-version reconstruction correction

The corrected 0.8.6 profile format is reconstruction-first rather than a local
backup format. It records how to obtain ComfyUI, custom nodes, and Python
packages, then includes only the small artifacts that cannot be recreated from
those references.

- the official Comfy Registry node catalog and every maintained legacy
  ComfyUI-Manager `node_db` channel are cached once per UTC day after the TUI
  renders; an explicit Nodes & Plugins rescan forces an immediate refresh;
- installed custom nodes are resolved through local Manager metadata and
  snapshots, the daily Registry/Manager cache, configured catalogs and mappings,
  node package metadata, and Git provenance before export;
- resolved nodes are listed in `custom_nodes.yml` by Registry/Manager id, public
  repository, available version, and immutable ref when one is actually
  recoverable; their source directories are never copied into the setup file;
- only a genuinely unresolved standalone/local plugin can appear beneath
  `embedded_plugins/`, and its payload is sanitized and checksummed;
- the main ComfyUI checkout is reconstructed from its stated repository. A
  compact `comfyui_overlay/` contains only changed/new core files and deletions
  relative to the fetchable base revision; `custom_nodes`, environments, models,
  user/runtime data, generated launchers, and shared-path files are excluded;
- `environment-lock.yaml` records every installed distribution and exact working
  version without copying `.venv` or site-packages;
- ComfyUI and custom-node `requirements*.txt`, `pyproject.toml`, `uv.lock`,
  `manager_requirements.txt`, and related manifests are retained under
  `dependency_manifests/`, compared together, and reconciled against the known
  working package set;
- normal package indexes, configured wheel sources, public Git repositories,
  Registry/Manager installation, and source-build lifecycles are preferred over
  embedded artifacts. Legacy profiles containing embedded wheels or old source
  snapshots remain readable for compatibility, but new exports do not create
  them as a normal path;
- duplicate custom-node identities open an explicit full-path keep/omit choice
  rather than failing or silently selecting one copy.

Re-export profiles created by early 0.8.4 builds so known Manager/Registry
nodes are stored as references and oversized source payloads are removed.

## Same-version text-selection and manifest-conflict correction

- All editable path, file, command, and text inputs use normal desktop selection behavior.
- Console output, runtime logs, inventories, review pages, installation diagnostics, and other long textual output use a read-only selectable text document instead of a non-selectable renderer.
- Mouse dragging and `Shift+Arrow` select text; `Ctrl+A`/`Cmd+A` selects the focused field; `Ctrl+C`/`Cmd+C` copies the selected text.
- Core, Manager, and custom-node dependency manifests captured from the source installation are used during reconstruction.
- Before uv resolution, a manifest pin/range that rejects the package version verified in the working source environment is replaced in the generated installation copy with that verified exact version. The override is shown in the installation console and warning report.
- This fixes the observed `comfy-kitchen==0.2.21` versus verified `comfy-kitchen==0.2.20` unsatisfiable pair without weakening the final exact package audit.


## Scrollable review and contents pages

- **Launch & Manage → View contents** now uses a dedicated, always-scrollable content viewport with a stable scrollbar while keeping action buttons visible.
- **Setup & Install → Review installation** uses the same scroll-safe layout so long plans, paths, and component lists remain accessible in compact terminals.

## Runtime log management

- Added **Clear output** on Launch & Manage. Clearing rotates the active `runtime.log` into `.comfy-setup/logs/` and immediately starts a fresh active log; archived output is preserved.
- Added **Browse logs** with filename/content search, log viewing, clipboard copy, archived-log deletion, refresh, and active-log protection.
- Added automatic archived-log cleanup with **Daily**, **Weekly**, **Monthly (30 days default)**, and custom day retention.
- Managed launches now use a persistent relay so log rotation works while ComfyUI is running and live output remains unbuffered.
- Added CLI parity for listing, searching, clearing, deleting, cleaning, following, and configuring retention through `installations logs`.

## Dependency reconciliation and validation

- Core installation now installs `manager_requirements.txt` when present so launchers using `--enable-manager` have the required Manager package.
- The installer re-applies profile constraints after custom-node and accelerated-package installation, audits installed package metadata, repairs missing/incompatible transitive dependencies, and refuses to report success while selected nodes still fail ComfyUI's quick startup validation.
- Dynamic CuPy, ONNX Runtime, TensorRT, and other package-index installs now honor the profile constraints file instead of upgrading compatibility-sensitive packages such as NumPy.
- The Badgids profile pins NumPy `2.4.4`, installs `click`, and declares a repair mapping for missing `click`, addressing the observed ACE-Step, Qwen3-TTS, and WAS node failures.

## Validation

- Added regression coverage for both scroll viewports, log rotation/search/retention/browser controls, CLI log management, Manager requirements, NumPy/click profile repair, dependency-failure parsing, and startup repair.

---

# Release notes — 0.8.1

This patch fixes local-file selection, portable setup refresh, profile-library management, and shared-workflow display regressions found after the 0.8.0 workspace release.

## Local wheel files

- Existing `.whl` files can be selected from any local directory and registered as `Custom/Local` direct-wheel sources.
- Local file and directory validation now reports an exact filesystem error instead of incorrectly requiring HTTPS.
- The no-wheel recovery panel includes the reusable TUI path browser and filesystem autocomplete.

## Paths and file browsing

- Every filesystem input now provides local path autocomplete.
- File and directory fields include a **Browse…** button that opens a keyboard-friendly Textual file browser.
- The browser supports home, project-root, and parent-directory navigation plus typed save paths.

## Portable setup exports and refresh

- Setup exports default to `<comfyui-setup-manager>/setups/` while still accepting any user-selected destination.
- CLI setup export arguments are optional; omitted outputs use the project `setups/` directory.
- Imported and exported `.comfyuisetup` profiles are reloaded and selected immediately without restarting or manually reloading YAML.
- Successful exports are also added to the manager profile library.

## Profile library and Launch & Manage fixes

- Imported profiles can now be removed from the Profile Library after confirmation. Built-in profiles remain protected.
- **View contents** no longer crashes when a workflow is stored in an external/shared workflow library. External paths are displayed as absolute paths with an `external/shared` marker.

## Validation

- Regression coverage was added for local wheel registration, default setup paths, path autocomplete, the TUI file browser, immediate profile refresh, profile removal controls, and external workflow paths.

---

# Release notes — 0.8.0

This release intentionally reset the pre-1.0 version line from 0.9.4 to 0.8.0 while the project continues substantial development.

## Faster startup and cached discovery

- The Textual shell renders before platform probing, Git inspection, installation discovery, or asset indexing.
- First launch and manual rescans use a visible staged progress dialog.
- Completed scan results are atomically stored in `runtime-state.yaml`.
- Later launches read YAML cache state; **Rescan** rebuilds filesystem state and **Reload YAML** applies configuration-only changes.

## Correct workspace order and separation

- The fixed tab order is Launch & Manage, Setup & Install, Nodes & Plugins, Workflows, Models, LoRAs, Agents & Skills, MCPs.
- Each tab now contains only its own domain content and actions.
- Nodes & Plugins has a dedicated cached inventory and installation selector.
- Models excludes the shared `loras/` subtree.
- LoRAs has an independent inventory, source catalog, downloads, tasks, imports, exports, deletes, and CLI group.

## Setup layout repair

- Corrected the quick-action grid cell count and responsive heights.
- Setup actions reflow at medium and compact widths.
- The **Continue** button remains in a dedicated action bar and is no longer clipped in supported small terminals.

## Agent Skills and MCP definitions

- Added portable operator, developer, and release skills under `skills/` and in the wheel.
- Added `agents-skills.yaml`, configured targets for Claude Code, Codex, OpenCode, OpenClaude, and compatible agents, plus TUI/CLI skill installation.
- Added `mcps.yaml` with a dedicated definition workspace and CLI commands. Definitions are not started automatically.

## CLI, tests, and documentation

- Added `nodes`, `loras`, `skills`, and `mcps` command groups.
- Updated capability parity, PRD, README, TUI/CLI/configuration/architecture/automation documentation, tests, launchers, and package metadata.
