# Function and class reference

[Documentation home](index.md) · [Architecture](architecture.md)

This page describes the main public project functions. Internal helpers may change between patch releases.

## `comfy_setup.cli`

- `build_parser()` creates the complete TUI-parity command tree.
- `main(argv=None)` sanitizes package-source variables, parses arguments, runs a command, formats output, and returns stable exit behavior.
- `emit(value, args)` writes text, JSON, or YAML.

## `comfy_setup.engine`

### `InstallerEngine`

- `plan()` returns ordered `InstallStep` objects without changing the system.
- `selected_nodes()` returns compatible selected custom nodes.
- `selected_acceleration()` returns compatible selected compiled packages.
- `install()` performs the complete setup and returns `InstallResult`.

## `comfy_setup.discovery`

- `discover_installations_detailed(extra_roots=...)` performs one bounded installation scan and returns installations plus per-instance node and workflow inventories.
- `discover_installations(extra_roots=...)` provides the compatibility list-only wrapper.
- `describe_installation(path, nodes=None, workflows=None)` returns source, environment, node, workflow, and metadata information while accepting pre-scanned inventories.
- `installed_nodes(path)` inspects normal and configured node roots without importing them.
- `installed_workflows(path)` lists native workflow files.

## `comfy_setup.runtime_state`

- `load_runtime_state()` reads the fast-start YAML cache without scanning the machine.
- `save_runtime_state(state)` atomically persists normalized scan state.
- `scan_runtime_state(extra_roots=..., progress=...)` performs the explicit staged platform, installation, node, workflow, and asset scan.
- `cached_installations()`, `cached_nodes()`, `cached_instance_workflows()`, and `cached_assets()` expose domain-specific cached data for the TUI.
- `runtime_state_is_initialized()` determines whether first-launch discovery is still required.

## `comfy_setup.instance_control`

- `launch_instance(path, foreground=False, extra_args=None, log_path=None)` launches one instance and records background process metadata.
- `stop_instance(path, timeout=10, force=False)` safely stops a recorded process.
- `instance_status(path)` reports persistent runtime state.

## `comfy_setup.log_management`

- `active_log_path(path)` returns the protected active runtime log.
- `rotate_runtime_log(path, reason=...)` archives active output and creates a fresh file.
- `request_log_rotation(path, reason=...)` asks a running relay to rotate safely.
- `list_runtime_logs(path)` / `search_runtime_logs(path, query)` enumerate and search active and archived logs.
- `delete_runtime_log(path, log)` removes only an archived log under the managed archive directory.
- `load_log_retention()` / `save_log_retention(policy)` manage Daily, Weekly, Monthly, or custom retention.
- `cleanup_runtime_logs(path, policy=None)` removes expired archives while preserving the active log.

## `comfy_setup.dependency_resolver`

- `reconcile_requirements_text(text, verified_versions)` rewrites only manifest specifiers that reject the exact version proven to work in the exported source environment and returns auditable override records.
- `inspect_dependency_issues(python, runner)` audits installed distribution metadata for missing or incompatible requirements.
- `missing_module_names(output)` extracts missing imports from validation output.
- `comfyui_startup_failures(output)` identifies selected custom-node import failures.

## `comfy_setup.environment_lock`

- `build_environment_lock(distributions, ...)` converts the complete installed distribution inventory into an exact portable lock and records exact package provenance without archiving local wheels in new profiles.
- `lock_install_requirements(lock)` returns exact index and immutable Git requirements.
- `lock_embedded_wheels(lock)` validates and returns bundled local wheel entries.
- `lock_source_tree_packages(lock)` returns editable/local distributions that must be reinstalled from reproduced ComfyUI or custom-node source paths.
- `lock_reproducible_version_map(lock)` returns every runtime package version that must remain fixed and appear in the final exact audit.
- `compatibility_issues(lock, ...)` rejects incompatible Python minors, operating systems, architectures, or accelerator/runtime families before installation changes begin.

## `comfy_setup.profile`

- `list_profiles()` lists built-in and imported profiles.
- `get_profile(profile_id)` loads a profile by ID or alias.
- `validate_profile(data)` upgrades and validates a profile.
- `read_profile_bundle(path)` verifies and reads `.comfyuisetup`.
- `write_profile_bundle(profile, output)` writes a portable bundle.
- `list_profile_bundle_files(path)` lists safe archive members and whether each one can be edited.
- `read_profile_bundle_file(path, member)` returns one bounded UTF-8 member.
- `edit_profile_bundle_file(path, member, text)` rebuilds checksums and atomically replaces the archive only after full validation.
- `import_profile(path)` imports a profile into the user library.
- `remove_imported_profile(profile_id)` removes only user-imported profiles.

## `comfy_setup.workflows`

- `discover_workflow_libraries(comfy_dir=None)` finds native workflow roots.
- `inspect_workflow_artifact(path)` summarizes JSON or a workflow pack.
- `import_workflow_artifact(...)` validates and installs a workflow artifact.
- `export_native_workflow(source, output)` copies one validated native workflow.
- `write_workflow_pack(...)` creates a directory-preserving archive.

## `comfy_setup.updates`

### `ComfyUpdateManager`

- `preflight(fetch=True)` checks official update availability, changed core files/packages, custom-node direct conflicts, current `pip check`, and protected resolver compatibility only when a core package must change. Current/file-only candidates skip uv, and duplicate normalized metadata is reduced to Python's effective version.
- `create_snapshot(target_commit=None, baseline_pip_ok=None)` records lightweight rollback state.
- `list_snapshots()` lists restorable snapshots.
- `update(preflight, strategy="safe", selected_packages=None)` snapshots, installs only selected required core packages while constraining every non-core/unselected package, requires `pip check` and startup/import validation, and automatically rolls back a failed mutated candidate.
- `rollback(snapshot_id)` restores source and package state.
- `delete_snapshot(snapshot_id)` removes one snapshot.

## `comfy_setup.wheel_sources`

### `WheelSourceRegistry`

- `matching(package_id, platform_info)` returns ordered compatible sources.
- `add_source(...)` validates and writes a custom source.
- `register_local_backup(package_id, directory, platform_info)` records a local wheelhouse.

- `wheel_compatibility_score(filename, environment)` checks wheel tags and accelerator markers.
- `select_candidates(...)` ranks compatible candidates.

## `comfy_setup.prerequisites`

- `check_prerequisites(source_builds=False)` reports required tools.
- `install_prerequisites(..., dry_run=True)` displays platform package commands.
- `install_prerequisites(..., yes=True)` runs supported package-manager commands.

## `shared_assets.py`

- `load_shared_asset_paths()` — reads the saved external library paths.
- `save_shared_asset_paths()` — validates and saves path configuration.
- `create_shared_directories()` — creates the standard model category folders and workflow root.
- `write_extra_model_paths()` — preserves existing YAML sections and writes the manager-owned shared-model section.
- `configure_shared_workflows()` — migrates optional existing workflows and creates the native workflow link.
- `configure_instance_shared_assets()` — applies both models and workflows to one installation.
- `apply_shared_assets_to_installations()` — applies configuration to several installations and returns per-instance results.
- `inspect_instance_shared_assets()` — reports whether one installation is connected correctly.

## `asset_catalog.py`

- `list_sources()` / `add_source()` — manage model, LoRA, or workflow download sources.
- `list_entries()` / `add_entry()` / `remove_entry()` — manage domain-specific catalog records.
- `download_entry()` — download, checksum, track, and atomically install an entry.
- `import_asset()` — copy a local file into a safe relative library destination.
- `list_installed_assets()` — return installed files with relative path, size, and modification time.
- `delete_asset()` — delete one confirmed file inside the shared root.
- `list_tasks()` — return persisted download state.

## `comfy_setup.agent_resources`

- `list_bundled_skills()` discovers and validates portable `SKILL.md` directories from a source checkout or installed wheel.
- `load_agent_skill_config()` reads configured agent destinations.
- `list_agent_targets()` resolves configured destinations for Claude Code, Codex, OpenCode, OpenClaude, and the universal Agent Skills directory.
- `install_skill(skill_id, target_id, overwrite=False)` safely copies one bundled skill into a configured target.

## `comfy_setup.mcp_config`

- `load_mcp_config()` reads portable MCP server definitions.
- `list_mcp_servers()` returns normalized definitions for the MCPs workspace and CLI.

## `comfy_setup.path_widgets`

- `PathInput` adds non-recursive filesystem completion to a Textual input.
- `PathField` pairs a path input with a reusable **Browse…** button.
- `PathBrowserScreen` selects existing files/directories or a new export filename without leaving the TUI.

## Profile export paths

- `configuration.project_root_directory()` resolves the launcher-provided project root.
- `configuration.profiles_directory()` creates and returns `<project-root>/profiles/`, migrating non-conflicting files from the former `setups/` name.
- `configuration.setups_directory()` remains a compatibility alias that returns the same `profiles/` path.
