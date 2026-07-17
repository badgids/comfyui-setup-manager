from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from . import __version__
from .agent_resources import configured_agent_targets, install_skill, list_bundled_skills
from .capabilities import capability_payload
from .configuration import (
    EDITABLE_CONFIG_FILES,
    ensure_editable_config,
    load_editable_config,
    official_comfyui_repository,
    save_editable_config,
    profiles_directory,
)
from .discovery import (
    describe_installation,
    discover_installations,
    installed_nodes,
    installed_workflows,
    is_comfyui_directory,
)
from .engine import InstallerEngine
from .exporter import DuplicateNodeIdentityError, ExportError, duplicate_custom_nodes, export_setup
from .instance_control import instance_status, launch_instance, stop_instance
from .log_management import (
    LogRetention,
    cleanup_runtime_logs,
    delete_runtime_log,
    list_runtime_logs,
    load_log_retention,
    logs_directory,
    read_log_text,
    request_log_rotation,
    rotate_runtime_log,
    save_log_retention,
    search_runtime_logs,
)
from .inventory import load_inventory, profile_from_inventory
from .managed_installations import (
    load_instance_metadata,
    remove_instance_metadata,
    save_instance_metadata,
)
from .mcp_config import list_mcp_servers
from .models import InstallOptions
from .shared_assets import (
    SharedAssetPaths, apply_shared_assets_to_installations, configure_instance_shared_assets,
    create_shared_directories, inspect_instance_shared_assets, load_shared_asset_paths,
    save_shared_asset_paths,
)
from .asset_catalog import (
    add_entry as add_asset_entry, add_source as add_asset_source, delete_asset,
    download_entry as download_asset_entry, export_asset, import_asset, list_entries as list_asset_entries,
    list_installed_assets, list_sources as list_asset_sources, list_tasks as list_download_tasks,
    remove_entry as remove_asset_entry, clear_tasks as clear_download_tasks,
    retry_task as retry_download_task,
)
from .package_sources import enforce_package_environment
from .platforms import detect_platform
from .prerequisites import check_prerequisites, install_prerequisites
from .profile import (
    PROFILE_EXTENSION,
    default_profile,
    edit_profile_bundle_file,
    export_builtin_profile,
    get_profile,
    import_profile,
    list_profile_bundle_files,
    list_profiles,
    load_profile,
    read_profile_bundle,
    read_profile_bundle_file,
    remove_imported_profile,
    write_profile_bundle,
)
from .runner import Runner
from .updates import ComfyUpdateManager
from .wheel_sources import WheelSourceRegistry, parse_source_file, user_source_path
from .workflows import (
    WorkflowBundleSummary,
    discover_workflow_files,
    discover_workflow_libraries,
    export_native_workflow,
    import_workflow_artifact,
    inspect_workflow_artifact,
    write_workflow_pack,
)


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONFLICT = 4
EXIT_OPERATION_FAILED = 5


def _setup_output_path(requested: Path | None, label: str) -> Path:
    safe = "-".join(part for part in __import__("re").split(r"[^A-Za-z0-9]+", label.strip()) if part).lower()
    filename = (safe or "comfyui-setup") + PROFILE_EXTENSION
    if requested is None:
        return profiles_directory() / filename
    output = requested.expanduser()
    if output.exists() and output.is_dir():
        return output / filename
    return output


def _resolve_duplicate_node_omissions(
    comfyui_dir: Path,
    explicit_omissions: list[Path] | None,
    *,
    interactive: bool,
) -> set[Path]:
    """Resolve duplicate custom-node destinations without silently choosing one."""

    omissions = {
        path.expanduser().resolve(strict=False)
        for path in (explicit_omissions or [])
    }
    groups = duplicate_custom_nodes(comfyui_dir, omitted_node_paths=omissions)
    if not groups:
        return omissions
    if not interactive or not sys.stdin.isatty():
        raise DuplicateNodeIdentityError(groups)

    print(
        "Duplicate custom nodes were found in configured custom-node roots.\n"
        "A portable installation can place only one copy at each destination identity.\n"
        "Choose the source path(s) to omit; no copy will be selected silently.",
        file=sys.stderr,
    )
    for group in groups:
        paths = list(group.paths)
        while True:
            print(f"\nDuplicate identity: {group.identity}", file=sys.stderr)
            for index, path in enumerate(paths, start=1):
                print(f"  {index}. {path}", file=sys.stderr)
            print(
                f"Enter {len(paths) - 1} path number(s) to OMIT, comma-separated, leaving exactly one copy. "
                "Enter q to cancel:",
                file=sys.stderr,
            )
            response = sys.stdin.readline()
            if response == "":
                raise DuplicateNodeIdentityError(groups)
            answer = response.strip().lower()
            if answer in {"q", "quit", "cancel"}:
                raise ExportError("Profile export cancelled while resolving duplicate custom nodes.")
            try:
                selected = {
                    int(part.strip())
                    for part in answer.split(",")
                    if part.strip()
                }
            except ValueError:
                print("Enter only the displayed path numbers, separated by commas.", file=sys.stderr)
                continue
            valid = set(range(1, len(paths) + 1))
            if not selected <= valid or len(selected) != len(paths) - 1:
                print(
                    f"Choose exactly {len(paths) - 1} valid path number(s) so one copy remains.",
                    file=sys.stderr,
                )
                continue
            kept = next(index for index in valid if index not in selected)
            for index in selected:
                omissions.add(paths[index - 1].resolve(strict=False))
            print(f"Keeping: {paths[kept - 1]}", file=sys.stderr)
            for index in sorted(selected):
                print(f"Omitting: {paths[index - 1]}", file=sys.stderr)
            break
    return omissions


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _text(value: Any, *, indent: int = 0) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list, tuple)):
                lines.append(f"{prefix}{key}:")
                lines.append(_text(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}{key}: {item}")
        return "\n".join(lines)
    if isinstance(value, (list, tuple)):
        lines = []
        for item in value:
            if isinstance(item, (dict, list, tuple)):
                rendered = _text(item, indent=indent + 2)
                first, *rest = rendered.splitlines() or [""]
                lines.append(f"{prefix}- {first.lstrip()}")
                lines.extend(rest)
            else:
                lines.append(f"{prefix}- {item}")
        return "\n".join(lines)
    return f"{prefix}{value}"


def emit(value: Any, args: argparse.Namespace, *, stream: Any | None = None) -> None:
    stream = stream or sys.stdout
    payload = _jsonable(value)
    output_format = getattr(args, "format", "text")
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=False), file=stream)
    elif output_format == "yaml":
        print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).rstrip(), file=stream)
    else:
        print(_text(payload), file=stream)


def _profile_argument(value: str | None) -> dict[str, Any]:
    if not value:
        return default_profile()
    candidate = Path(value).expanduser()
    if candidate.exists():
        if candidate.name.lower().endswith(PROFILE_EXTENSION):
            return read_profile_bundle(candidate)
        return load_profile(candidate)
    return get_profile(value)


def _profile_bundle_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.exists():
        resolved = candidate.resolve()
        if not resolved.name.lower().endswith(PROFILE_EXTENSION):
            raise ValueError("Profile archive editing requires a .comfyuisetup file.")
        return resolved
    record = next((item for item in list_profiles() if item.id == value or item.name == value), None)
    if record is None:
        raise FileNotFoundError(f"Profile was not found: {value}")
    if not record.path.name.lower().endswith(PROFILE_EXTENSION):
        raise ValueError("The selected profile is not a .comfyuisetup archive.")
    return record.path.resolve()


def _installation_object_payload(item: Any, *, include_contents: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(item.path),
        "name": item.name,
        "description": item.description,
        "version": item.version,
        "branch": item.branch,
        "commit": item.commit,
        "repository": item.repository,
        "has_venv": item.has_venv,
        "node_count": item.node_count,
        "workflow_count": item.workflow_count,
        "profile_id": item.profile_id,
        "profile_name": item.profile_name,
        "process": instance_status(item.path),
    }
    if include_contents:
        payload["nodes"] = [_jsonable(node) for node in installed_nodes(item.path)]
        payload["workflows"] = [_jsonable(workflow) for workflow in installed_workflows(item.path)]
    return payload


def _installation_payload(path: Path, *, include_contents: bool = False) -> dict[str, Any]:
    return _installation_object_payload(describe_installation(path), include_contents=include_contents)


def _selection(profile: dict[str, Any], raw: str | None, excludes: list[str], key: str) -> set[str]:
    available = [str(item["id"]) for item in profile.get(key, [])]
    selected_default = {str(item["id"]) for item in profile.get(key, []) if item.get("selected", True)}
    if raw is None or raw == "default":
        selected = selected_default
    elif raw == "all":
        selected = set(available)
    elif raw == "none":
        selected = set()
    else:
        selected = {value.strip() for value in raw.split(",") if value.strip()}
    unknown = selected - set(available)
    if unknown:
        raise ValueError(f"Unknown {key} IDs: {', '.join(sorted(unknown))}")
    selected.difference_update(excludes)
    return selected


def _install_options(args: argparse.Namespace, profile: dict[str, Any]) -> tuple[InstallOptions, dict[str, Any]]:
    info = detect_platform()
    accelerator = info.accelerator if args.accelerator == "auto" else args.accelerator
    profile_copy = copy.deepcopy(profile)
    mode = args.repository_mode
    if mode == "existing":
        use_current = True
        repository = None
    elif mode == "official":
        use_current = False
        repository = official_comfyui_repository()
    elif mode == "profile":
        use_current = False
        repository = str(profile_copy.get("comfyui", {}).get("repository") or official_comfyui_repository())
    else:
        use_current = False
        if not args.repository:
            raise ValueError("--repository is required with --repository-mode custom.")
        repository = args.repository
    if repository:
        comfy = profile_copy.setdefault("comfyui", {})
        comfy["repository"] = repository
        comfy["branch"] = args.branch
        if mode != "profile":
            comfy["preferred_commit"] = None

    preferred = str(profile_copy["python"]["preferred"])
    options = InstallOptions(
        target_dir=args.target,
        python_version=args.python or preferred,
        accelerator=accelerator,
        selected_nodes=_selection(profile_copy, args.nodes, args.exclude_node, "nodes"),
        selected_acceleration=_selection(
            profile_copy, args.acceleration_packages, args.exclude_acceleration_package, "accelerated_packages"
        ),
        auto_install_system=args.install_system,
        allow_source_builds=args.source_builds,
        backup_builds=args.backup_builds,
        backup_dir=args.backup_dir,
        pin_exact_refs=not args.no_pin_refs,
        use_current_checkout=use_current,
        update_existing_nodes=args.update_nodes,
        install_command_alias=False,
        configure_shared_assets=args.shared_assets,
        shared_models_dir=args.models_dir,
        shared_workflows_dir=args.workflows_dir,
        migrate_existing_assets=args.migrate_existing_assets,
    )
    return options, profile_copy


def _add_install_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default=None, help="Built-in profile ID, imported profile ID, YAML/JSON profile, or .comfyuisetup.")
    parser.add_argument("--target", type=Path, required=True, help="ComfyUI installation directory.")
    parser.add_argument("--python", help="Python version requested for the ComfyUI environment.")
    parser.add_argument("--accelerator", choices=["auto", "nvidia", "rocm", "mps", "cpu"], default="auto")
    parser.add_argument("--repository-mode", choices=["existing", "official", "profile", "custom"], default="official")
    parser.add_argument("--repository", help="Custom public GitHub repository URL.")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--nodes", default="default", help="default, all, none, or comma-separated node IDs.")
    parser.add_argument("--exclude-node", action="append", default=[])
    parser.add_argument("--acceleration-packages", default="default", help="default, all, none, or comma-separated package IDs.")
    parser.add_argument("--exclude-acceleration-package", action="append", default=[])
    parser.add_argument("--install-system", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--source-builds", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--backup-builds", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--no-pin-refs", action="store_true")
    parser.add_argument("--update-nodes", action="store_true")
    parser.add_argument("--sudo-password-env", help="Read an administrator password from this environment variable.")
    parser.add_argument("--shared-assets", action=argparse.BooleanOptionalAction, default=True, help="Configure shared external models and workflows libraries.")
    parser.add_argument("--models-dir", type=Path, help="Shared external models root.")
    parser.add_argument("--workflows-dir", type=Path, help="Shared external workflows root.")
    parser.add_argument("--migrate-existing-assets", action="store_true", help="Move existing local workflows into the shared library before linking it.")


def _add_asset_management_arguments(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    kind: str,
    help_text: str,
    default_destination: str,
) -> None:
    command = subparsers.add_parser(kind, help=help_text)
    commands = command.add_subparsers(dest=f"{kind}_command", required=True)
    commands.add_parser("list")
    commands.add_parser("sources")
    commands.add_parser("edit-sources")
    commands.add_parser("tasks")
    clear_tasks = commands.add_parser("clear-tasks")
    clear_tasks.add_argument("--all", action="store_true")
    retry_task = commands.add_parser("retry-task")
    retry_task.add_argument("task_id")
    retry_task.add_argument("--overwrite", action="store_true")
    add_source = commands.add_parser("add-source")
    add_source.add_argument("--id", required=True)
    add_source.add_argument("--name", required=True)
    add_source.add_argument("--kind", default="direct-url")
    add_source.add_argument("--base-url")
    add_source.add_argument("--label", default="Custom/Local")
    add_entry = commands.add_parser("add-entry")
    add_entry.add_argument("--id", required=True)
    add_entry.add_argument("--name", required=True)
    add_entry.add_argument("--source-id", required=True)
    add_entry.add_argument("--url", required=True)
    add_entry.add_argument("--destination", default=default_destination)
    add_entry.add_argument("--filename")
    add_entry.add_argument("--sha256")
    download = commands.add_parser("download")
    download.add_argument("entry_id")
    download.add_argument("--overwrite", action="store_true")
    import_command = commands.add_parser("import")
    import_command.add_argument("source", type=Path)
    import_command.add_argument("--destination", default=default_destination)
    import_command.add_argument("--overwrite", action="store_true")
    export_command = commands.add_parser("export")
    export_command.add_argument("relative_path")
    export_command.add_argument("output", type=Path)
    export_command.add_argument("--overwrite", action="store_true")
    delete = commands.add_parser("delete")
    delete.add_argument("relative_path")
    delete.add_argument("--yes", action="store_true")
    remove_entry = commands.add_parser("remove-entry")
    remove_entry.add_argument("entry_id")
    remove_entry.add_argument("--yes", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comfyui-setup-manager",
        description="Manage ComfyUI installations, setup profiles, workflows, updates, wheels, and configuration.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--format", choices=["text", "json", "yaml"], default="text", help="Machine-readable output format.")
    parser.add_argument("--config-dir", type=Path, help="Override the editable YAML configuration directory.")
    parser.add_argument("--traceback", action="store_true", help="Show Python tracebacks on errors.")

    sub = parser.add_subparsers(dest="command")

    tui = sub.add_parser("tui", help="Open the interactive Textual interface.")
    tui.add_argument("--theme")
    tui.add_argument("--profile")

    system = sub.add_parser("system", help="Inspect the current operating system and prerequisites.")
    system_sub = system.add_subparsers(dest="system_command", required=True)
    system_sub.add_parser("info")
    check = system_sub.add_parser("check")
    check.add_argument("--source-builds", action="store_true")
    prereq = system_sub.add_parser("install-prerequisites")
    prereq.add_argument("--source-builds", action="store_true")
    prereq.add_argument("--yes", action="store_true")
    prereq.add_argument("--dry-run", action="store_true")

    installations = sub.add_parser("installations", aliases=["instances"], help="Discover, inspect, launch, edit, and remove ComfyUI instances.")
    inst_sub = installations.add_subparsers(dest="installations_command", required=True)
    discover = inst_sub.add_parser("discover", aliases=["list"])
    discover.add_argument("--scan-root", action="append", type=Path, default=[])
    show = inst_sub.add_parser("show")
    show.add_argument("path", type=Path)
    contents = inst_sub.add_parser("contents")
    contents.add_argument("path", type=Path)
    status = inst_sub.add_parser("status")
    status.add_argument("path", type=Path)
    launch = inst_sub.add_parser("launch")
    launch.add_argument("path", type=Path)
    launch.add_argument("--foreground", action="store_true")
    launch.add_argument("--arg", action="append", default=[], help="Extra argument passed to ComfyUI; may be repeated.")
    launch.add_argument("--log", type=Path)
    stop = inst_sub.add_parser("stop")
    stop.add_argument("path", type=Path)
    stop.add_argument("--timeout", type=float, default=10.0)
    stop.add_argument("--force", action="store_true")
    logs = inst_sub.add_parser("logs")
    logs.add_argument("path", type=Path)
    logs.add_argument("--lines", type=int, default=200)
    logs.add_argument("--follow", action="store_true")
    logs.add_argument("--list-files", action="store_true", help="List active and archived runtime log files.")
    logs.add_argument("--search", help="Search log filenames and recent contents.")
    logs.add_argument("--delete", type=Path, help="Delete one archived log file (requires --yes).")
    logs.add_argument("--clear", action="store_true", help="Rotate the active log and begin a fresh file.")
    logs.add_argument("--cleanup", action="store_true", help="Remove archived logs older than the configured retention period.")
    logs.add_argument("--retention", choices=["daily", "weekly", "monthly", "custom"], help="Set automatic archived-log retention.")
    logs.add_argument("--days", type=int, help="Number of days used with --retention custom.")
    logs.add_argument("--yes", action="store_true", help="Confirm archived-log deletion.")
    edit = inst_sub.add_parser("edit")
    edit.add_argument("path", type=Path)
    edit.add_argument("--name")
    edit.add_argument("--description")
    edit.add_argument("--profile-id")
    edit.add_argument("--profile-name")
    uninstall = inst_sub.add_parser("uninstall")
    uninstall.add_argument("path", type=Path)
    uninstall.add_argument("--confirm", required=True, help="Must be exactly UNINSTALL.")

    nodes = sub.add_parser("nodes", help="Inspect custom nodes and plugins for one ComfyUI installation.")
    nodes_sub = nodes.add_subparsers(dest="nodes_command", required=True)
    nodes_list = nodes_sub.add_parser("list")
    nodes_list.add_argument("path", type=Path)

    install = sub.add_parser("install", help="Plan or perform a complete ComfyUI setup installation.")
    install_sub = install.add_subparsers(dest="install_command", required=True)
    plan = install_sub.add_parser("plan")
    _add_install_arguments(plan)
    run = install_sub.add_parser("run")
    _add_install_arguments(run)

    profiles = sub.add_parser("profiles", help="List, inspect, import, export, and remove portable setup profiles.")
    profile_sub = profiles.add_subparsers(dest="profiles_command", required=True)
    profile_sub.add_parser("list")
    profile_show = profile_sub.add_parser("show")
    profile_show.add_argument("profile")
    profile_import = profile_sub.add_parser("import")
    profile_import.add_argument("path", type=Path)
    profile_export = profile_sub.add_parser("export")
    profile_export.add_argument("profile")
    profile_export.add_argument("output", type=Path, nargs="?", help="Output file (default: project profiles/ directory).")
    profile_remove = profile_sub.add_parser("remove")
    profile_remove.add_argument("profile")
    profile_remove.add_argument("--yes", action="store_true")
    profile_files = profile_sub.add_parser("files", help="List files inside a .comfyuisetup archive.")
    profile_files.add_argument("profile")
    profile_read_file = profile_sub.add_parser("read-file", help="Print one UTF-8 file from a profile archive.")
    profile_read_file.add_argument("profile")
    profile_read_file.add_argument("member")
    profile_edit_file = profile_sub.add_parser("edit-file", help="Replace one UTF-8 file and transactionally validate the archive.")
    profile_edit_file.add_argument("profile")
    profile_edit_file.add_argument("member")
    profile_edit_file.add_argument("--from-file", required=True, help="Read replacement text from this path, or - for stdin.")
    profile_edit_file.add_argument(
        "--allow-id-change",
        action="store_true",
        help="Allow profile.yaml to change id; use only on an external copy before importing it.",
    )
    from_install = profile_sub.add_parser("from-installation")
    from_install.add_argument("comfyui_dir", type=Path)
    from_install.add_argument("--name", required=True)
    from_install.add_argument("--publisher", default="")
    from_install.add_argument("--output", type=Path, help="Output file (default: project profiles/ directory).")
    from_install.add_argument("--skip-local-plugins", action="store_true")
    from_install.add_argument(
        "--omit-node",
        action="append",
        type=Path,
        default=[],
        help=(
            "Explicitly omit one complete custom-node path. Repeat for multiple paths. "
            "Interactive terminals prompt for unresolved duplicate identities."
        ),
    )
    from_inventory = profile_sub.add_parser("from-inventory")
    from_inventory.add_argument("archive", type=Path)
    from_inventory.add_argument("--name", required=True)
    from_inventory.add_argument("--output", type=Path, help="Output file (default: project profiles/ directory).")
    from_inventory.add_argument("--repository", default=official_comfyui_repository())

    workflows = sub.add_parser("workflows", help="Browse, inspect, import, export, and bundle native workflows.")
    workflow_sub = workflows.add_subparsers(dest="workflows_command", required=True)
    libraries = workflow_sub.add_parser("libraries")
    libraries.add_argument("--comfyui-dir", type=Path)
    workflow_list = workflow_sub.add_parser("list")
    workflow_list.add_argument("comfyui_dir", type=Path)
    workflow_list.add_argument("--user")
    inspect = workflow_sub.add_parser("inspect")
    inspect.add_argument("artifact", type=Path)
    workflow_import = workflow_sub.add_parser("import")
    workflow_import.add_argument("artifact", type=Path)
    workflow_import.add_argument("--comfyui-dir", type=Path)
    workflow_import.add_argument("--workflow-dir", type=Path)
    workflow_import.add_argument("--user", default="default")
    workflow_import.add_argument("--subfolder")
    workflow_import.add_argument("--no-bundle-default", action="store_true")
    workflow_import.add_argument("--overwrite", action="store_true")
    workflow_import.add_argument("--skip-support-files", action="store_true")
    workflow_import.add_argument("--import-setup", action="store_true")
    workflow_import.add_argument("--download-setup-reference", action="store_true")
    workflow_export = workflow_sub.add_parser("export")
    workflow_export.add_argument("source", type=Path)
    workflow_export.add_argument("output", type=Path)
    pack = workflow_sub.add_parser("pack")
    pack.add_argument("sources", type=Path, nargs="+")
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--name", required=True)
    pack.add_argument("--publisher", default="")
    pack.add_argument("--description", default="")
    pack.add_argument("--tags", default="")
    pack.add_argument("--source-root", type=Path)
    pack.add_argument("--default-subdirectory", default="")
    pack.add_argument("--readme", type=Path)
    pack.add_argument("--setup", type=Path)
    pack.add_argument("--setup-reference", default="")
    pack.add_argument("--no-support-files", action="store_true")
    pack.add_argument("--no-recursive", action="store_true")
    pack.add_argument("--requirements-yaml", type=Path, action="append", default=[], help="Companion YAML requirements/source file; may be repeated.")
    workflow_sub.add_parser("sources")
    workflow_sub.add_parser("edit-sources")
    workflow_sub.add_parser("tasks")
    workflow_clear_tasks = workflow_sub.add_parser("clear-tasks")
    workflow_clear_tasks.add_argument("--all", action="store_true")
    workflow_retry_task = workflow_sub.add_parser("retry-task")
    workflow_retry_task.add_argument("task_id")
    workflow_retry_task.add_argument("--overwrite", action="store_true")
    workflow_add_source = workflow_sub.add_parser("add-source")
    workflow_add_source.add_argument("--id", required=True)
    workflow_add_source.add_argument("--name", required=True)
    workflow_add_source.add_argument("--kind", default="direct-url")
    workflow_add_source.add_argument("--base-url")
    workflow_add_source.add_argument("--label", default="Custom/Local")
    workflow_add_entry = workflow_sub.add_parser("add-entry")
    workflow_add_entry.add_argument("--id", required=True)
    workflow_add_entry.add_argument("--name", required=True)
    workflow_add_entry.add_argument("--source-id", required=True)
    workflow_add_entry.add_argument("--url", required=True)
    workflow_add_entry.add_argument("--destination", default="")
    workflow_add_entry.add_argument("--filename")
    workflow_download = workflow_sub.add_parser("download")
    workflow_download.add_argument("entry_id")
    workflow_download.add_argument("--overwrite", action="store_true")
    workflow_delete = workflow_sub.add_parser("delete")
    workflow_delete.add_argument("relative_path")
    workflow_delete.add_argument("--yes", action="store_true")

    shared = sub.add_parser("shared-paths", aliases=["assets"], help="Configure shared external models and workflows libraries.")
    shared_sub = shared.add_subparsers(dest="shared_command", required=True)
    shared_sub.add_parser("status")
    shared_configure = shared_sub.add_parser("configure")
    shared_configure.add_argument("--models-dir", type=Path, required=True)
    shared_configure.add_argument("--workflows-dir", type=Path, required=True)
    shared_configure.add_argument("--migrate-existing", action="store_true")
    shared_configure.add_argument("--conflict-policy", choices=["preserve", "overwrite", "rename"], default="preserve")
    shared_configure.add_argument("--apply-to", type=Path, action="append", default=[])
    shared_configure.add_argument("--apply-discovered", action="store_true")
    shared_apply = shared_sub.add_parser("apply")
    shared_apply.add_argument("comfyui_dir", type=Path, nargs="+")
    shared_apply.add_argument("--migrate-existing", action="store_true")
    shared_inspect = shared_sub.add_parser("inspect")
    shared_inspect.add_argument("comfyui_dir", type=Path)
    shared_sub.add_parser("edit")

    _add_asset_management_arguments(
        sub,
        kind="models",
        help_text="Manage the shared model library and model download catalog (LoRAs excluded).",
        default_destination="checkpoints",
    )
    _add_asset_management_arguments(
        sub,
        kind="loras",
        help_text="Manage the dedicated shared LoRA library and LoRA download catalog.",
        default_destination="",
    )

    updates = sub.add_parser("updates", help="Check and apply official ComfyUI updates.")
    update_sub = updates.add_subparsers(dest="updates_command", required=True)
    update_check = update_sub.add_parser("check")
    update_check.add_argument("path", type=Path)
    update_check.add_argument("--no-fetch", action="store_true")
    update_run = update_sub.add_parser("run")
    update_run.add_argument("path", type=Path)
    update_strategy = update_run.add_mutually_exclusive_group()
    update_strategy.add_argument(
        "--strategy",
        choices=("safe", "patch", "force", "new", "abort"),
        default="safe",
        help="safe blocks on risks; patch reconciles the protected current setup; force continues through preflight blockers; new and abort leave it unchanged.",
    )
    update_strategy.add_argument(
        "--force",
        action="store_true",
        help="Deprecated alias for --strategy force.",
    )
    package_selection = update_run.add_mutually_exclusive_group()
    package_selection.add_argument(
        "--package",
        dest="selected_packages",
        action="append",
        help="Select a reviewed core package change; repeat for multiple packages.",
    )
    package_selection.add_argument(
        "--all-core-packages",
        action="store_true",
        help="Select every changed direct ComfyUI package manifest entry.",
    )

    snapshots = sub.add_parser("snapshots", help="List, restore, and delete update snapshots.")
    snapshot_sub = snapshots.add_subparsers(dest="snapshots_command", required=True)
    snapshot_list = snapshot_sub.add_parser("list")
    snapshot_list.add_argument("path", type=Path)
    snapshot_show = snapshot_sub.add_parser("show")
    snapshot_show.add_argument("path", type=Path)
    snapshot_show.add_argument("snapshot_id")
    snapshot_rollback = snapshot_sub.add_parser("rollback")
    snapshot_rollback.add_argument("path", type=Path)
    snapshot_rollback.add_argument("snapshot_id")
    snapshot_delete = snapshot_sub.add_parser("delete")
    snapshot_delete.add_argument("path", type=Path)
    snapshot_delete.add_argument("snapshot_id")
    snapshot_delete.add_argument("--yes", action="store_true")

    themes = sub.add_parser("themes", help="List, select, and import themes.")
    theme_sub = themes.add_subparsers(dest="themes_command", required=True)
    theme_sub.add_parser("list")
    theme_sub.add_parser("current")
    theme_select = theme_sub.add_parser("select")
    theme_select.add_argument("name")
    theme_import = theme_sub.add_parser("import-vim")
    theme_import.add_argument("source")
    theme_import.add_argument("--select", action="store_true")

    sources = sub.add_parser("wheel-sources", aliases=["sources"], help="Manage editable precompiled-wheel sources.")
    source_sub = sources.add_subparsers(dest="sources_command", required=True)
    source_sub.add_parser("path")
    source_sub.add_parser("edit")
    source_sub.add_parser("list")
    source_show = source_sub.add_parser("show")
    source_show.add_argument("source_id")
    source_add = source_sub.add_parser("add")
    source_add.add_argument("--package", required=True)
    source_add.add_argument("--label", required=True, choices=["Official", "3rd Party", "Custom/Local"])
    source_add.add_argument("--kind", required=True)
    source_add.add_argument("--location", required=True)
    source_add.add_argument("--priority", type=int, default=100)
    source_remove = source_sub.add_parser("remove")
    source_remove.add_argument("source_id")
    source_remove.add_argument("--yes", action="store_true")
    source_toggle = source_sub.add_parser("enable")
    source_toggle.add_argument("source_id")
    source_disable = source_sub.add_parser("disable")
    source_disable.add_argument("source_id")
    source_register = source_sub.add_parser("register-local")
    source_register.add_argument("package")
    source_register.add_argument("directory", type=Path)
    source_sub.add_parser("validate")

    skills = sub.add_parser("skills", help="List and install bundled AI-agent skills.")
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    skills_sub.add_parser("list")
    skills_sub.add_parser("targets")
    skills_sub.add_parser("edit-config")
    skill_install = skills_sub.add_parser("install")
    skill_install.add_argument("skill_id")
    skill_install.add_argument("--agent", required=True, help="Configured agent ID, such as claude-code or codex.")
    skill_install.add_argument("--target", type=Path, help="Override the configured skills directory.")
    skill_install.add_argument("--overwrite", action="store_true")

    mcps = sub.add_parser("mcps", help="Inspect and edit manager MCP server definitions.")
    mcps_sub = mcps.add_subparsers(dest="mcps_command", required=True)
    mcps_sub.add_parser("list")
    mcps_sub.add_parser("edit-config")

    capabilities = sub.add_parser("capabilities", help="List the complete TUI-to-CLI feature map.")
    capabilities.add_argument("--area", help="Filter by feature area.")

    config = sub.add_parser("config", help="Inspect and edit manager YAML configuration.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("list")
    config_path = config_sub.add_parser("path")
    config_path.add_argument("name", choices=EDITABLE_CONFIG_FILES)
    config_edit = config_sub.add_parser("edit")
    config_edit.add_argument("name", choices=EDITABLE_CONFIG_FILES)
    config_show = config_sub.add_parser("show")
    config_show.add_argument("name", choices=EDITABLE_CONFIG_FILES)
    config_set = config_sub.add_parser("set")
    config_set.add_argument("name", choices=EDITABLE_CONFIG_FILES)
    config_set.add_argument("key", help="Dot-separated mapping key.")
    config_set.add_argument("value", help="YAML scalar/list/mapping value.")
    config_validate = config_sub.add_parser("validate")
    config_validate.add_argument("name", nargs="?", choices=EDITABLE_CONFIG_FILES)

    return parser


def _set_nested(payload: dict[str, Any], dotted: str, value: Any) -> None:
    keys = [part for part in dotted.split(".") if part]
    if not keys:
        raise ValueError("Configuration key cannot be empty.")
    current = payload
    for key in keys[:-1]:
        child = current.setdefault(key, {})
        if not isinstance(child, dict):
            raise ValueError(f"{key!r} is not a mapping.")
        current = child
    current[keys[-1]] = value


def _source_payload(registry: WheelSourceRegistry) -> list[dict[str, Any]]:
    return [source.to_dict() for source in registry.sources]


def _open_in_editor(path: Path) -> dict[str, Any]:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        command = [*__import__("shlex").split(editor), str(path)]
    elif os.name == "nt":
        command = ["notepad.exe", str(path)]
    elif sys.platform == "darwin":
        command = ["open", "-t", str(path)]
    else:
        command = ["xdg-open", str(path)]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Editor exited with code {completed.returncode}: {' '.join(command)}")
    return {"path": str(path), "editor": command[0]}


def _read_runtime_log(path: Path, lines: int) -> list[str]:
    if not path.is_file():
        return []
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return content[-max(0, lines):] if lines else content


def _follow_runtime_log(path: Path, lines: int) -> None:
    for line in _read_runtime_log(path, lines):
        print(line)
    offset = path.stat().st_size if path.exists() else 0
    while True:
        if not path.exists():
            time.sleep(0.2)
            continue
        size = path.stat().st_size
        if size < offset:
            offset = 0
        if size > offset:
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
                offset = handle.tell()
            text = data.decode("utf-8", errors="replace")
            print(text, end="", flush=True)
        time.sleep(0.2)


def _handle_asset_management_command(kind: str, action: str, args: argparse.Namespace) -> Any:
    config_name = "model-sources.yaml" if kind == "models" else "lora-sources.yaml"
    singular = "model" if kind == "models" else "LoRA"
    if action == "list":
        return list_installed_assets(kind)
    if action == "sources":
        return {"sources": list_asset_sources(kind), "entries": list_asset_entries(kind)}
    if action == "edit-sources":
        return _open_in_editor(ensure_editable_config(config_name))
    if action == "tasks":
        return [task for task in list_download_tasks() if task.kind == kind]
    if action == "clear-tasks":
        statuses = ("queued", "running", "completed", "failed") if args.all else ("completed", "failed")
        return {"removed": clear_download_tasks(kind, statuses=statuses)}
    if action == "retry-task":
        return retry_download_task(args.task_id, overwrite=args.overwrite)
    if action == "add-source":
        path = add_asset_source(kind, {
            "id": args.id, "name": args.name, "kind": args.kind, "base_url": args.base_url,
            "label": args.label, "enabled": True, "notes": "Added from the CLI.",
        })
        return {"path": path, "id": args.id}
    if action == "add-entry":
        path = add_asset_entry(kind, {
            "id": args.id, "name": args.name, "source_id": args.source_id, "url": args.url,
            "destination": args.destination, "filename": args.filename, "sha256": args.sha256,
        })
        return {"path": path, "id": args.id}
    if action == "download":
        return download_asset_entry(kind, args.entry_id, overwrite=args.overwrite)
    if action == "import":
        return {"path": import_asset(kind, args.source, destination=args.destination, overwrite=args.overwrite)}
    if action == "export":
        return {"path": export_asset(kind, args.relative_path, args.output, overwrite=args.overwrite)}
    if action == "remove-entry":
        if not args.yes:
            raise ValueError(f"Removing a {singular} catalog entry requires --yes.")
        return {"removed": remove_asset_entry(kind, args.entry_id), "entry_id": args.entry_id}
    if not args.yes:
        raise ValueError(f"Deleting a {singular} file requires --yes.")
    return {"deleted": delete_asset(kind, args.relative_path)}


def _handle_command(args: argparse.Namespace) -> Any:
    command = args.command
    if command == "skills":
        action = args.skills_command
        if action == "list":
            return list_bundled_skills()
        if action == "targets":
            return configured_agent_targets()
        if action == "edit-config":
            return _open_in_editor(ensure_editable_config("agents-skills.yaml"))
        return {
            "skill_id": args.skill_id,
            "agent": args.agent,
            "path": install_skill(args.skill_id, args.agent, target_root=args.target, overwrite=args.overwrite),
        }
    if command == "mcps":
        if args.mcps_command == "edit-config":
            return _open_in_editor(ensure_editable_config("mcps.yaml"))
        return list_mcp_servers()
    if command == "capabilities":
        values = capability_payload()
        return [item for item in values if not args.area or item["area"] == args.area]
    if command == "system":
        if args.system_command == "info":
            return detect_platform()
        if args.system_command == "check":
            return [item.to_dict() for item in check_prerequisites(source_builds=args.source_builds)]
        return install_prerequisites(source_builds=args.source_builds, yes=args.yes, dry_run=args.dry_run)

    if command in {"installations", "instances"}:
        action = args.installations_command
        if action in {"discover", "list"}:
            return [_installation_object_payload(item) for item in discover_installations(args.scan_root)]
        if action == "show":
            return _installation_payload(args.path)
        if action == "contents":
            return _installation_payload(args.path, include_contents=True)
        if action == "status":
            return instance_status(args.path)
        if action == "launch":
            return launch_instance(args.path, foreground=args.foreground, extra_args=args.arg, log_path=args.log)
        if action == "stop":
            return stop_instance(args.path, timeout=args.timeout, force=args.force)
        if action == "logs":
            root = args.path.expanduser().resolve()
            log_path = root / ".comfy-setup" / "runtime.log"
            actions = sum(bool(value) for value in (
                args.follow, args.list_files, args.search, args.delete, args.clear, args.cleanup, args.retention
            ))
            if actions > 1:
                raise ValueError("Choose only one log action at a time.")
            if args.retention:
                days = args.days if args.retention == "custom" else {"daily": 1, "weekly": 7, "monthly": 30}[args.retention]
                if days is None or days < 1:
                    raise ValueError("--retention custom requires --days with a positive whole number.")
                path = save_log_retention(LogRetention(mode=args.retention, days=days, auto_cleanup=True))
                return {"path": str(path), "retention": load_log_retention()}
            if args.cleanup:
                removed = cleanup_runtime_logs(root)
                return {"removed": [str(path) for path in removed], "retention": load_log_retention()}
            if args.clear:
                status = instance_status(root)
                if status["running"]:
                    request_log_rotation(root, reason="cli-clear")
                    return {"path": str(log_path), "rotation_requested": True}
                archived = rotate_runtime_log(root, reason="cli-clear")
                return {"path": str(log_path), "archived": str(archived) if archived else None}
            if args.delete:
                if not args.yes:
                    raise ValueError("Deleting an archived log requires --yes.")
                candidate = args.delete.expanduser()
                if not candidate.is_absolute():
                    candidate = logs_directory(root) / candidate
                return {"deleted": delete_runtime_log(root, candidate), "path": str(candidate.resolve())}
            if args.list_files or args.search is not None:
                records = search_runtime_logs(root, args.search or "")
                return [record for record in records]
            if args.follow:
                if args.format != "text":
                    raise ValueError("--follow requires --format text.")
                _follow_runtime_log(log_path, args.lines)
                return {"path": str(log_path), "following": False}
            return {"path": str(log_path), "lines": _read_runtime_log(log_path, args.lines)}
        if action == "edit":
            existing = load_instance_metadata(args.path)
            return save_instance_metadata(
                args.path,
                name=args.name if args.name is not None else existing.name,
                description=args.description if args.description is not None else existing.description,
                profile_id=args.profile_id if args.profile_id is not None else existing.profile_id,
                profile_name=args.profile_name if args.profile_name is not None else existing.profile_name,
            )
        if action == "uninstall":
            root = args.path.expanduser().resolve()
            if args.confirm != "UNINSTALL":
                raise ValueError("--confirm must be exactly UNINSTALL.")
            if not is_comfyui_directory(root) or root in {Path(root.anchor), Path.home().resolve()} or len(root.parts) < 3:
                raise ValueError(f"Refusing to remove an unsafe or invalid ComfyUI directory: {root}")
            try:
                stop_instance(root, force=True)
            except Exception:
                pass
            shutil.rmtree(root)
            remove_instance_metadata(root)
            return {"removed": str(root)}

    if command == "nodes":
        return [_jsonable(node) for node in installed_nodes(args.path)]

    if command == "install":
        profile = _profile_argument(args.profile)
        options, profile = _install_options(args, profile)
        info = detect_platform()
        engine = InstallerEngine(profile, info, options)
        if args.install_command == "plan":
            return {
                "profile": profile.get("name"),
                "platform": _jsonable(info),
                "options": options.to_json(),
                "steps": [_jsonable(step) for step in engine.plan()],
            }
        secret = None
        if args.sudo_password_env:
            secret = os.environ.get(args.sudo_password_env)
            if secret is None:
                raise ValueError(f"Environment variable is not set: {args.sudo_password_env}")
        wheel_choice: Callable[[dict[str, Any], str, Path], str] = (
            lambda item, reason, path: "build" if options.allow_source_builds else "cancel"
        )
        engine = InstallerEngine(
            profile,
            info,
            options,
            log=lambda line: print(line, file=sys.stderr, flush=True),
            progress=lambda current, total, title: print(f"[{current}/{total}] {title}", file=sys.stderr, flush=True),
            secret_provider=(lambda prompt: secret),
            wheel_decision_provider=wheel_choice,
        )
        result = engine.install()
        if result.success:
            save_instance_metadata(
                result.target_dir,
                name=str(profile.get("name") or result.target_dir.name),
                description=str(profile.get("description") or "Managed ComfyUI installation."),
                profile_id=str(profile.get("id") or "") or None,
                profile_name=str(profile.get("name") or "") or None,
            )
        if not result.success:
            raise RuntimeError(result.error or f"Installation failed at {result.failed_step}.")
        return result

    if command == "profiles":
        action = args.profiles_command
        if action == "list":
            return [
                {
                    "id": record.id,
                    "name": record.name,
                    "label": record.label,
                    "version": record.profile.get("version"),
                    "abi_tag": record.abi_tag,
                    "compatibility": record.compatibility,
                    "source": record.source,
                    "path": record.path,
                }
                for record in list_profiles()
            ]
        if action == "show":
            return _profile_argument(args.profile)
        if action == "files":
            return list_profile_bundle_files(_profile_bundle_path(args.profile))
        if action == "read-file":
            return read_profile_bundle_file(_profile_bundle_path(args.profile), args.member)
        if action == "edit-file":
            replacement = sys.stdin.read() if args.from_file == "-" else Path(args.from_file).expanduser().read_text(encoding="utf-8")
            return edit_profile_bundle_file(
                _profile_bundle_path(args.profile),
                args.member,
                replacement,
                allow_profile_id_change=args.allow_id_change,
            )
        if action == "import":
            return import_profile(args.path)
        if action == "export":
            output_path = _setup_output_path(args.output, args.profile)
            candidate = Path(args.profile).expanduser()
            if candidate.exists():
                output = write_profile_bundle(_profile_argument(args.profile), output_path)
            else:
                try:
                    output = export_builtin_profile(args.profile, output_path)
                except Exception:
                    output = write_profile_bundle(_profile_argument(args.profile), output_path)
            imported = import_profile(output)
            return {"output": output, "profile": imported.id, "library_path": imported.path}
        if action == "remove":
            if not args.yes:
                raise ValueError("Removing a profile requires --yes.")
            return {"removed": remove_imported_profile(args.profile), "profile": args.profile}
        if action == "from-installation":
            omitted_node_paths = _resolve_duplicate_node_omissions(
                args.comfyui_dir,
                args.omit_node,
                interactive=getattr(args, "format", "text") == "text",
            )
            output, warnings = export_setup(
                args.comfyui_dir,
                _setup_output_path(args.output, args.name),
                name=args.name,
                publisher=args.publisher,
                include_unpublished_plugins=not args.skip_local_plugins,
                omitted_node_paths=omitted_node_paths,
            )
            imported = import_profile(output)
            return {"output": output, "warnings": warnings, "profile": imported.id, "library_path": imported.path}
        if action == "from-inventory":
            inventory = load_inventory(args.archive)
            import re
            profile_id = "-".join(filter(None, re.split(r"[^a-z0-9]+", args.name.lower()))) or "custom-comfyui"
            profile = profile_from_inventory(inventory, profile_name=args.name, profile_id=profile_id, repository=args.repository)
            output = write_profile_bundle(profile, _setup_output_path(args.output, args.name))
            imported = import_profile(output)
            return {"output": output, "profile": imported.id, "library_path": imported.path}

    if command == "workflows":
        action = args.workflows_command
        if action == "sources":
            return {"sources": list_asset_sources("workflows"), "entries": list_asset_entries("workflows")}
        if action == "edit-sources":
            return _open_in_editor(ensure_editable_config("workflow-sources.yaml"))
        if action == "tasks":
            return [task for task in list_download_tasks() if task.kind == "workflows"]
        if action == "clear-tasks":
            statuses = ("queued", "running", "completed", "failed") if args.all else ("completed", "failed")
            return {"removed": clear_download_tasks("workflows", statuses=statuses)}
        if action == "retry-task":
            return retry_download_task(args.task_id, overwrite=args.overwrite)
        if action == "add-source":
            path = add_asset_source("workflows", {
                "id": args.id, "name": args.name, "kind": args.kind, "base_url": args.base_url,
                "label": args.label, "enabled": True, "notes": "Added from the CLI.",
            })
            return {"path": path, "id": args.id}
        if action == "add-entry":
            path = add_asset_entry("workflows", {
                "id": args.id, "name": args.name, "source_id": args.source_id, "url": args.url,
                "destination": args.destination, "filename": args.filename,
            })
            return {"path": path, "id": args.id}
        if action == "download":
            return download_asset_entry("workflows", args.entry_id, overwrite=args.overwrite)
        if action == "delete":
            if not args.yes:
                raise ValueError("Deleting a workflow file requires --yes.")
            return {"deleted": delete_asset("workflows", args.relative_path)}
        if action == "libraries":
            return discover_workflow_libraries(args.comfyui_dir)
        if action == "list":
            return discover_workflow_files(args.comfyui_dir, args.user)
        if action == "inspect":
            return inspect_workflow_artifact(args.artifact)
        if action == "import":
            if not args.comfyui_dir and not args.workflow_dir:
                raise ValueError("Workflow import requires --comfyui-dir or --workflow-dir.")
            return import_workflow_artifact(
                args.artifact,
                args.comfyui_dir,
                user_name=args.user,
                subfolder=args.subfolder,
                use_bundle_default=not args.no_bundle_default,
                custom_directory=args.workflow_dir,
                overwrite=args.overwrite,
                install_support_files=not args.skip_support_files,
                import_setup=args.import_setup,
                download_setup_reference=args.download_setup_reference,
            )
        if action == "export":
            return export_native_workflow(args.source, args.output)
        if action == "pack":
            return write_workflow_pack(
                args.sources,
                args.output,
                name=args.name,
                description=args.description,
                publisher=args.publisher,
                tags=[item.strip() for item in args.tags.split(",") if item.strip()],
                default_install_subdirectory=args.default_subdirectory,
                setup_profile_path=args.setup,
                setup_reference=args.setup_reference or None,
                readme_path=args.readme,
                source_root=args.source_root,
                include_support_files=not args.no_support_files,
                recursive=not args.no_recursive,
                companion_yaml_paths=args.requirements_yaml,
            )

    if command in {"shared-paths", "assets"}:
        action = args.shared_command
        paths = load_shared_asset_paths()
        if action == "status":
            return {
                "paths": paths.to_dict(),
                "directories": create_shared_directories(paths),
                "installations": [inspect_instance_shared_assets(item.path).to_dict() for item in discover_installations()],
            }
        if action == "edit":
            return _open_in_editor(ensure_editable_config("asset-paths.yaml"))
        if action == "inspect":
            return inspect_instance_shared_assets(args.comfyui_dir).to_dict()
        if action == "configure":
            paths = SharedAssetPaths(
                models=args.models_dir.expanduser().resolve(),
                workflows=args.workflows_dir.expanduser().resolve(),
                migrate_existing=args.migrate_existing,
                conflict_policy=args.conflict_policy,
            )
            config_path = save_shared_asset_paths(paths)
            created = create_shared_directories(paths)
            installations_to_apply = list(args.apply_to)
            if args.apply_discovered:
                installations_to_apply.extend(item.path for item in discover_installations())
            applied = apply_shared_assets_to_installations(installations_to_apply, paths, migrate_existing=args.migrate_existing)
            return {"config": str(config_path), "paths": paths.to_dict(), "created": created, "applied": applied}
        return apply_shared_assets_to_installations(args.comfyui_dir, paths, migrate_existing=args.migrate_existing)

    if command in {"models", "loras"}:
        return _handle_asset_management_command(command, getattr(args, f"{command}_command"), args)

    if command == "updates":
        manager = ComfyUpdateManager(args.path, runner=Runner(log=lambda line: print(line, file=sys.stderr, flush=True)))
        preflight = manager.preflight(fetch=not getattr(args, "no_fetch", False))
        if args.updates_command == "check":
            return preflight
        strategy = "force" if args.force else args.strategy
        selected_packages = None
        if args.all_core_packages:
            selected_packages = {
                item.name for item in preflight.core_package_changes
                if item.target_requirement is not None
            }
        elif args.selected_packages:
            selected_packages = set(args.selected_packages)
        return manager.update(
            preflight,
            strategy=strategy,
            selected_packages=selected_packages,
        )

    if command == "snapshots":
        manager = ComfyUpdateManager(args.path, runner=Runner(log=lambda line: print(line, file=sys.stderr, flush=True)))
        records = manager.list_snapshots()
        if args.snapshots_command == "list":
            return records
        record = next((item for item in records if item.snapshot_id == args.snapshot_id), None)
        if record is None:
            raise FileNotFoundError(f"Snapshot was not found: {args.snapshot_id}")
        if args.snapshots_command == "show":
            return record
        if args.snapshots_command == "rollback":
            return manager.rollback(args.snapshot_id)
        if not args.yes:
            raise ValueError("Deleting a snapshot requires --yes.")
        manager.delete_snapshot(args.snapshot_id)
        return {"deleted": args.snapshot_id}

    if command == "themes":
        from .themes import (
            builtin_themes,
            display_name as theme_display_name,
            import_vim_theme,
            load_theme_state,
            save_theme_state,
        )

        selected, custom = load_theme_state()
        all_themes = {theme.name: theme for theme in builtin_themes()}
        all_themes.update({theme.name: theme for theme in custom})
        if args.themes_command == "list":
            return [{"name": name, "display_name": theme_display_name(name), "selected": name == selected} for name in all_themes]
        if args.themes_command == "current":
            return {"name": selected, "display_name": theme_display_name(selected)}
        if args.themes_command == "select":
            if args.name not in all_themes:
                raise ValueError(f"Unknown theme: {args.name}")
            path = save_theme_state(args.name, custom)
            return {"selected": args.name, "path": path}
        theme = import_vim_theme(args.source)
        all_custom = {item.name: item for item in custom}
        all_custom[theme.name] = theme
        path = save_theme_state(theme.name if args.select else selected, all_custom.values())
        return {"imported": theme.name, "selected": bool(args.select), "path": path}

    if command in {"wheel-sources", "sources"}:
        registry = WheelSourceRegistry()
        action = args.sources_command
        if action == "path":
            return {"path": registry.path}
        if action == "edit":
            return _open_in_editor(registry.path)
        if action == "list":
            return _source_payload(registry)
        if action == "show":
            source = next((item for item in registry.sources if item.id == args.source_id), None)
            if source is None:
                raise FileNotFoundError(args.source_id)
            return source
        if action == "add":
            return registry.add_source(
                package_id=args.package,
                label=args.label,
                kind=args.kind,
                location=args.location,
                platform_info=detect_platform(),
                priority=args.priority,
                notes="Added from the comfyui-setup-manager CLI.",
            )
        if action in {"remove", "enable", "disable"}:
            payload = load_editable_config("wheel-sources.yaml")
            sources_list = payload.get("sources", [])
            found = False
            result: list[dict[str, Any]] = []
            for item in sources_list:
                if isinstance(item, dict) and str(item.get("id")) == args.source_id:
                    found = True
                    if action == "remove":
                        continue
                    item = dict(item)
                    item["enabled"] = action == "enable"
                result.append(item)
            if not found:
                raise FileNotFoundError(args.source_id)
            if action == "remove" and not args.yes:
                raise ValueError("Removing a wheel source requires --yes.")
            payload["sources"] = result
            path = save_editable_config("wheel-sources.yaml", payload)
            return {"action": action, "source": args.source_id, "path": path}
        if action == "register-local":
            registry.register_local_backup(args.package, args.directory, detect_platform())
            return {"package": args.package, "directory": args.directory, "path": registry.path}
        parse_source_file(registry.path)
        return {"valid": True, "count": len(registry.sources), "path": registry.path}

    if command == "config":
        action = args.config_command
        if action == "list":
            return {name: str(ensure_editable_config(name)) for name in EDITABLE_CONFIG_FILES}
        if action == "path":
            return {"name": args.name, "path": ensure_editable_config(args.name)}
        if action == "edit":
            return _open_in_editor(ensure_editable_config(args.name))
        if action == "show":
            return load_editable_config(args.name)
        if action == "set":
            payload = load_editable_config(args.name)
            _set_nested(payload, args.key, yaml.safe_load(args.value))
            path = save_editable_config(args.name, payload)
            return {"path": path, "key": args.key, "value": yaml.safe_load(args.value)}
        names = [args.name] if args.name else list(EDITABLE_CONFIG_FILES)
        validated = {}
        for name in names:
            validated[name] = {"path": str(ensure_editable_config(name)), "valid": isinstance(load_editable_config(name), dict)}
        return validated

    raise ValueError(f"Unsupported command: {command}")


def _run_tui(profile: str | None = None, theme: str | None = None) -> None:
    from .app import ComfySetupApp

    theme_aliases = {"dark": "setup-dark", "light": "setup-light"}
    initial_theme = theme_aliases.get(theme, theme) if theme else None
    ComfySetupApp(initial_profile=_profile_argument(profile) if profile else None, initial_theme=initial_theme).run()


def main(argv: list[str] | None = None) -> None:
    enforce_package_environment()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config_dir:
        os.environ["COMFYUI_SETUP_CONFIG_DIR"] = str(args.config_dir.expanduser().resolve())

    try:
        if args.command is None:
            _run_tui()
            return
        if args.command == "tui":
            _run_tui(args.profile, args.theme)
            return
        result = _handle_command(args)
        emit(result, args)
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        if args.traceback:
            raise
        error = {"error": str(exc), "type": type(exc).__name__}
        emit(error, args, stream=sys.stderr)
        if isinstance(exc, FileNotFoundError):
            raise SystemExit(EXIT_NOT_FOUND) from exc
        if isinstance(exc, (ValueError, argparse.ArgumentError)):
            raise SystemExit(EXIT_USAGE) from exc
        raise SystemExit(EXIT_OPERATION_FAILED) from exc
