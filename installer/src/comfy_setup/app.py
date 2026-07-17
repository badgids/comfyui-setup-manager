from __future__ import annotations

import copy
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Label,
    ProgressBar,
    Select,
    SelectionList,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from .configuration import official_comfyui_repository, profiles_directory
from .discovery import (
    ComfyInstallation,
    installed_nodes,
    installed_workflows,
    is_comfyui_directory,
)
from .engine import InstallerEngine
from .launchers import LauncherError, launch_detached, stop_process
from .instance_control import instance_status
from .log_management import (
    LogRetention, RuntimeLogFile, cleanup_all_installation_logs, cleanup_runtime_logs, delete_runtime_log,
    list_runtime_logs, load_log_retention, read_log_text, request_log_rotation,
    rotate_runtime_log, save_log_retention, search_runtime_logs,
)
from .managed_installations import remove_instance_metadata, save_instance_metadata
from .runner import Runner
from .updates import (
    ComfyUpdateManager,
    RollbackResult,
    SnapshotRecord,
    UpdateError,
    UpdatePreflight,
    UpdateResult,
)
from .exporter import (
    DuplicateNodeGroup,
    build_profile_from_installation,
    duplicate_custom_nodes,
    export_setup,
)
from .inventory import load_inventory, profile_from_inventory
from .models import InstallOptions, InstallResult, PlatformInfo
from .profile import (
    PROFILE_EXTENSION,
    ProfileRecord,
    edit_profile_bundle_file,
    import_profile,
    list_profile_bundle_files,
    list_profiles,
    read_profile_bundle_file,
    remove_imported_profile,
    user_profiles_dir,
    write_profile_bundle,
)
from .terminal_output import TerminalStreamDecoder, clean_terminal_lines, read_log_tail
from .selectable_widgets import Input, Markdown, RichLog, TextArea
from .path_widgets import PathField, PathInput
from .themes import (
    BUILTIN_THEME_LABELS,
    ThemeImportError,
    builtin_themes,
    display_name as theme_display_name,
    import_vim_theme,
    load_theme_state,
    save_theme_state,
)

from .wheel_sources import WheelSourceRegistry, WheelSourceError, open_source_file
from .shared_assets import (
    SharedAssetPaths, apply_shared_assets_to_installations, create_shared_directories,
    inspect_instance_shared_assets, load_shared_asset_paths, save_shared_asset_paths,
)
from .asset_catalog import (
    add_entry as add_asset_entry, delete_asset, download_entry as download_asset_entry,
    export_asset, import_asset, list_entries as list_asset_entries, list_installed_assets,
    list_sources as list_asset_sources, list_tasks as list_download_tasks,
    clear_tasks as clear_download_tasks, retry_task as retry_download_task,
)
from .configuration import EDITABLE_CONFIG_FILES, ensure_editable_config
from .agent_resources import (
    AgentSkillError, bundled_skills_root, configured_agent_targets, install_skill, list_bundled_skills,
)
from .mcp_config import list_mcp_servers
from .runtime_state import (
    cached_assets, cached_installations, cached_instance_workflows, cached_nodes,
    cached_platform_info, load_runtime_state, node_resolution_state_is_current, refresh_cached_node_catalog_state,
    scan_runtime_state,
)
from .node_catalog import catalog_is_fresh

from .workflows import (
    WORKFLOW_PACK_EXTENSION,
    WorkflowBundleSummary,
    WorkflowError,
    discover_workflow_files,
    discover_workflow_libraries,
    export_native_workflow,
    import_workflow_artifact,
    inspect_workflow_artifact,
    load_workflow_json,
    summarize_workflow,
    write_workflow_pack,
)


OFFICIAL_COMFYUI_REPOSITORY = official_comfyui_repository()


def _display_path(path: Path, root: Path) -> str:
    """Return a relative path when possible and a labeled absolute path otherwise."""
    resolved_path = path.expanduser().resolve(strict=False)
    resolved_root = root.expanduser().resolve(strict=False)
    try:
        return str(resolved_path.relative_to(resolved_root))
    except ValueError:
        return f"{resolved_path} (external/shared)"


def _write_raw_terminal(log: RichLog, line: str) -> None:
    """Write untrusted subprocess output without Rich markup interpretation."""
    lines = clean_terminal_lines(line)
    if not lines and not line:
        lines = [""]
    for clean in lines:
        log.write(Text(clean), scroll_end=True)


def _write_notice(log: RichLog, markup: str) -> None:
    """Write manager-owned markup; never use this for subprocess output."""
    log.write(Text.from_markup(markup), scroll_end=True)


class WizardScreen(Screen[None]):
    HORIZONTAL_BREAKPOINTS = [(0, "-compact"), (76, "-medium"), (110, "-wide")]
    BINDINGS = [("escape", "back", "Back"), ("ctrl+q", "quit", "Quit")]

    def compose_header(self, title: str, subtitle: str) -> ComposeResult:
        with Container(classes="screen-heading"):
            yield Static(title, classes="screen-title")
            yield Static(subtitle, classes="screen-subtitle")

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"h", "j", "k", "l"}:
            return
        if isinstance(self.app.focused, (Input, TextArea)) and not isinstance(self.app.focused, RichLog):
            return
        focused = self.app.focused
        target: Any | None = focused if isinstance(focused, RichLog) else None
        if target is None:
            containers = list(self.query(ScrollableContainer))
            target = containers[0] if containers else None
        if target is None:
            return
        delta = {"h": (-4, 0), "j": (0, 3), "k": (0, -3), "l": (4, 0)}[event.key]
        target.scroll_relative(x=delta[0], y=delta[1], animate=False)
        event.prevent_default()
        event.stop()


class ScanProgressScreen(ModalScreen[None]):
    """Non-blocking progress screen shown after the TUI is already visible."""

    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self, title: str = "SCANNING SYSTEM") -> None:
        super().__init__()
        self.scan_title = title

    def compose(self) -> ComposeResult:
        with Container(id="scan-dialog"):
            yield Static(self.scan_title, id="scan-title")
            yield Static("Preparing scan…", id="scan-message", classes="help-text")
            yield ProgressBar(total=6, show_eta=False, id="scan-progress")
            yield Static(
                "The interface is already loaded. This scan updates runtime-state.yaml for future fast startups.",
                id="scan-detail",
                classes="help-text",
            )
            yield Button("Close", id="scan-close", classes="hidden")

    def update_progress(self, step: int, total: int, message: str) -> None:
        self.query_one("#scan-progress", ProgressBar).update(total=total, progress=step)
        self.query_one("#scan-message", Static).update(message)

    def show_error(self, message: str) -> None:
        self.query_one("#scan-title", Static).update("SCAN FAILED")
        self.query_one("#scan-message", Static).update(message)
        self.query_one("#scan-close", Button).remove_class("hidden")

    @on(Button.Pressed, "#scan-close")
    def close(self) -> None:
        self.app.pop_screen()


class HomeScreen(WizardScreen):
    """Primary application shell with management and setup tabs."""

    HORIZONTAL_BREAKPOINTS = [(0, "-compact"), (82, "-medium"), (118, "-wide")]
    VERTICAL_BREAKPOINTS = [(0, "-short"), (34, "-normal-height"), (46, "-tall")]

    def __init__(self) -> None:
        super().__init__()
        self._runtime_log_path: Path | None = None
        self._runtime_log_offset = 0
        self._runtime_log_identity: tuple[int, int] | None = None
        self._runtime_decoder = TerminalStreamDecoder()
        self._runtime_lines: list[str] = []
        self._runtime_follow = True
        self._runtime_process_was_running = False
        self._runtime_cleared = False

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="main-tabs"):
            with TabPane("Launch & Manage", id="manage-tab"):
                with ScrollableContainer(id="manage-scroll", classes="tab-shell", can_focus=True):
                    with Horizontal(classes="manager-toolbar"):
                        yield Select(
                            [(f"{item.name} — {item.path}", str(item.path)) for item in self.app.installations]
                            or [("No installations found", "__none__")],
                            value=(self.app.selected_manager_installation or "__none__"),
                            id="manage-installation-select",
                            allow_blank=False,
                        )
                        yield Button("Rescan", id="manager-rescan")
                        yield Button("Reload YAML", id="manager-reload")
                        yield Button("Scan folder", id="manager-scan")
                    yield Static(
                        "No ComfyUI installations are cached. Use Setup & Install or run Rescan.",
                        id="manager-empty",
                        classes="empty-state hidden",
                    )
                    with Container(id="manager-content"):
                        with Container(classes="manager-grid"):
                            with Container(classes="card manager-summary-card"):
                                yield Static("Instance", classes="card-title")
                                yield Static(id="manager-name", classes="instance-title")
                                yield Static(id="manager-description", classes="help-text")
                                yield Static(id="manager-path", classes="path-text")
                                yield Static(id="manager-runtime", classes="compact-detail")
                            with Container(classes="card manager-inventory-card"):
                                yield Static("Overview", classes="card-title")
                                yield Static(id="manager-overview")
                                yield Static(id="manager-repository", classes="help-text compact-detail")
                        with Container(classes="manager-actions-grid"):
                            yield Button("Launch", id="manager-launch", classes="success")
                            yield Button("Stop", id="manager-stop")
                            yield Button("Update", id="manager-update", classes="primary")
                            yield Button("Rollback", id="manager-rollback")
                            yield Button("View contents", id="manager-view")
                            yield Button("Edit details", id="manager-edit")
                            yield Button("Uninstall", id="manager-uninstall", classes="danger")
                        yield Static("Ready.", id="manager-status", classes="help-text manager-status")
                        with Container(classes="card manager-log-card"):
                            with Horizontal(classes="manager-log-toolbar"):
                                yield Static("Live runtime output", classes="card-title manager-log-title")
                                yield Button("Follow: On", id="manager-log-follow", classes="log-action")
                                yield Button("Clear output", id="manager-log-clear", classes="log-action")
                                yield Button("Browse logs", id="manager-log-browse", classes="log-action")
                                yield Button("Copy log", id="manager-log-copy", classes="log-action")
                            yield RichLog(
                                id="manager-log-preview",
                                wrap=False,
                                markup=False,
                                highlight=False,
                                auto_scroll=True,
                                max_lines=10000,
                                min_width=1,
                            )

            with TabPane("Setup & Install", id="setup-tab"):
                with Container(id="setup-layout"):
                    with ScrollableContainer(id="wizard", classes="home-wizard", can_focus=True):
                        with Container(classes="dashboard-grid"):
                            with Container(classes="card dashboard-card system-card"):
                                yield Static("System", classes="card-title")
                                yield Static(id="system-summary")
                                yield Button("Rescan system", id="rescan", classes="small-action")
                            with Container(classes="card dashboard-card profile-card"):
                                yield Static("Setup profile", classes="card-title")
                                yield Select(
                                    [(record.label, record.id) for record in self.app.profile_records],
                                    value=self.app.selected_profile_id,
                                    id="profile-select",
                                    allow_blank=False,
                                )
                                yield Static(id="profile-summary", classes="help-text compact-detail")
                            with Container(classes="card dashboard-card install-card"):
                                yield Static("Installation target", classes="card-title")
                                yield Select(
                                    [("New installation…", "__new__"), *[(f"{item.name} — {item.path}", str(item.path)) for item in self.app.installations]],
                                    value=self.app.selected_installation or "__new__",
                                    id="installation-select",
                                    allow_blank=False,
                                )
                                yield Static(id="installation-summary", classes="help-text compact-detail")
                                yield Button("Scan folder", id="scan-folder", classes="small-action")
                        with Container(classes="quick-actions-card"):
                            with Container(classes="quick-actions-grid"):
                                yield Button("Import setup", id="import-profile")
                                yield Button("Export setup", id="export-setup")
                                yield Button("Profiles", id="manage-profiles")
                                yield Button("Reload YAML", id="reload-settings")
                    with Container(classes="home-actionbar"):
                        yield Static(
                            "Choose a profile and target. Nothing changes until the review step.",
                            id="home-status",
                            classes="help-text",
                        )
                        yield Button("Continue", id="continue", classes="primary continue-button")

            with TabPane("Nodes & Plugins", id="nodes-tab"):
                with ScrollableContainer(id="nodes-scroll", classes="tab-shell asset-tab", can_focus=True):
                    with Container(classes="card"):
                        yield Static("ComfyUI installation", classes="card-title")
                        yield Select(
                            [(f"{item.name} — {item.path}", str(item.path)) for item in self.app.installations]
                            or [("No installations found", "__none__")],
                            value=(self.app.selected_manager_installation or "__none__"),
                            id="nodes-installation-select",
                            allow_blank=False,
                        )
                        yield Static(id="nodes-count", classes="compact-detail")
                    with Container(classes="asset-actions-grid nodes-actions-grid"):
                        yield Button("Rescan nodes", id="nodes-refresh", classes="primary")
                        yield Button("Open custom_nodes", id="nodes-open-folder")
                        yield Button("View instance", id="nodes-view-instance")
                    with Container(classes="card asset-list-card"):
                        yield Static("Installed nodes and plugins", classes="card-title")
                        yield RichLog(id="nodes-list", wrap=False, markup=False, highlight=False, max_lines=10000, syntax_mode="inventory")

            with TabPane("Workflows", id="workflows-tab"):
                with ScrollableContainer(id="workflows-scroll", classes="tab-shell asset-tab", can_focus=True):
                    with Container(classes="asset-summary-grid"):
                        with Container(classes="card"):
                            yield Static("Shared workflow library", classes="card-title")
                            yield Static(id="workflows-root", classes="path-text")
                            yield Static(id="workflows-count", classes="compact-detail")
                        with Container(classes="card"):
                            yield Static("Workflow sources", classes="card-title")
                            yield Static(id="workflows-source-count", classes="compact-detail")
                            yield Static("Native JSON workflows and directory-preserving packs.", classes="help-text")
                    with Container(classes="asset-actions-grid"):
                        yield Button("Set shared paths", id="workflows-configure-paths", classes="primary")
                        yield Button("Manage/import/export", id="workflows-manage")
                        yield Button("Download", id="workflows-download")
                        yield Button("Delete", id="workflows-delete", classes="danger")
                        yield Button("Tasks", id="workflows-tasks")
                        yield Button("Edit sources in TUI", id="workflows-edit-sources")
                        yield Button("Open in system editor", id="workflows-open-sources")
                        yield Button("Rescan", id="workflows-refresh")
                    with Container(classes="card asset-list-card"):
                        yield Static("Available workflows", classes="card-title")
                        yield RichLog(id="workflows-list", wrap=False, markup=False, highlight=False, max_lines=10000, syntax_mode="inventory")

            with TabPane("Models", id="models-tab"):
                with ScrollableContainer(id="models-scroll", classes="tab-shell asset-tab", can_focus=True):
                    with Container(classes="asset-summary-grid"):
                        with Container(classes="card"):
                            yield Static("Shared model library", classes="card-title")
                            yield Static(id="models-root", classes="path-text")
                            yield Static(id="models-count", classes="compact-detail")
                        with Container(classes="card"):
                            yield Static("Model sources", classes="card-title")
                            yield Static(id="models-source-count", classes="compact-detail")
                            yield Static("LoRAs are intentionally excluded and managed in the LoRAs tab.", classes="help-text")
                    with Container(classes="asset-actions-grid"):
                        yield Button("Set shared paths", id="models-configure-paths", classes="primary")
                        yield Button("Import model", id="models-import")
                        yield Button("Export model", id="models-export")
                        yield Button("Download", id="models-download")
                        yield Button("Delete", id="models-delete", classes="danger")
                        yield Button("Tasks", id="models-tasks")
                        yield Button("Edit sources in TUI", id="models-edit-sources")
                        yield Button("Open in system editor", id="models-open-sources")
                        yield Button("Rescan", id="models-refresh")
                    with Container(classes="card asset-list-card"):
                        yield Static("Installed models", classes="card-title")
                        yield RichLog(id="models-list", wrap=False, markup=False, highlight=False, max_lines=10000, syntax_mode="inventory")

            with TabPane("LoRAs", id="loras-tab"):
                with ScrollableContainer(id="loras-scroll", classes="tab-shell asset-tab", can_focus=True):
                    with Container(classes="asset-summary-grid"):
                        with Container(classes="card"):
                            yield Static("Shared LoRA library", classes="card-title")
                            yield Static(id="loras-root", classes="path-text")
                            yield Static(id="loras-count", classes="compact-detail")
                        with Container(classes="card"):
                            yield Static("LoRA sources", classes="card-title")
                            yield Static(id="loras-source-count", classes="compact-detail")
                            yield Static("LoRA catalogs and files are isolated from the Models workspace.", classes="help-text")
                    with Container(classes="asset-actions-grid"):
                        yield Button("Set shared paths", id="loras-configure-paths", classes="primary")
                        yield Button("Import LoRA", id="loras-import")
                        yield Button("Export LoRA", id="loras-export")
                        yield Button("Download", id="loras-download")
                        yield Button("Delete", id="loras-delete", classes="danger")
                        yield Button("Tasks", id="loras-tasks")
                        yield Button("Edit sources in TUI", id="loras-edit-sources")
                        yield Button("Open in system editor", id="loras-open-sources")
                        yield Button("Rescan", id="loras-refresh")
                    with Container(classes="card asset-list-card"):
                        yield Static("Installed LoRAs", classes="card-title")
                        yield RichLog(id="loras-list", wrap=False, markup=False, highlight=False, max_lines=10000, syntax_mode="inventory")

            with TabPane("Agents & Skills", id="agents-skills-tab"):
                with ScrollableContainer(id="agents-skills-scroll", classes="tab-shell asset-tab", can_focus=True):
                    with Container(classes="asset-summary-grid"):
                        with Container(classes="card"):
                            yield Static("Bundled Agent Skills", classes="card-title")
                            yield Static(id="skills-root", classes="path-text")
                            yield Static(id="skills-count", classes="compact-detail")
                        with Container(classes="card"):
                            yield Static("Configured agents", classes="card-title")
                            yield Static(id="agents-count", classes="compact-detail")
                            yield Static("Destinations are editable in agents-skills.yaml.", classes="help-text")
                    with Container(classes="asset-actions-grid"):
                        yield Button("Install skill", id="skills-install", classes="primary")
                        yield Button("Edit agent config", id="skills-edit-config")
                        yield Button("Open agent config", id="skills-open-config")
                        yield Button("Reload", id="skills-refresh")
                    with Container(classes="card asset-list-card"):
                        yield Static("Available skills", classes="card-title")
                        yield RichLog(id="skills-list", wrap=True, markup=False, highlight=False, max_lines=2000, syntax_mode="inventory")

            with TabPane("MCPs", id="mcps-tab"):
                with ScrollableContainer(id="mcps-scroll", classes="tab-shell asset-tab", can_focus=True):
                    with Container(classes="card"):
                        yield Static("Model Context Protocol servers", classes="card-title")
                        yield Static(id="mcps-count", classes="compact-detail")
                        yield Static("The manager stores portable MCP definitions without starting servers automatically.", classes="help-text")
                    with Container(classes="asset-actions-grid"):
                        yield Button("Edit MCP config", id="mcps-edit-config", classes="primary")
                        yield Button("Open MCP config", id="mcps-open-config")
                        yield Button("Reload", id="mcps-refresh")
                    with Container(classes="card asset-list-card"):
                        yield Static("Configured MCP servers", classes="card-title")
                        yield RichLog(id="mcps-list", wrap=True, markup=False, highlight=False, max_lines=2000, syntax_mode="inventory")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_all()
        self.cleanup_expired_logs()
        self.set_interval(0.2, self._poll_runtime_output)
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = "manage-tab" if self.app.installations else "setup-tab"
        self.query_one("#manager-rescan", Button).tooltip = "Run a full system, installation, node, workflow, model, and LoRA scan."
        self.query_one("#manager-reload", Button).tooltip = "Reload profiles and cached YAML without scanning the filesystem."
        self.query_one("#manager-scan", Button).tooltip = "Add another directory or drive to the next scan."
        self.query_one("#manager-launch", Button).tooltip = "Start this ComfyUI instance using its local launcher and .venv."
        self.query_one("#manager-stop", Button).tooltip = "Stop the instance if it was launched during this manager session."
        self.query_one("#manager-update", Button).tooltip = (
            "Check official ComfyUI for updates, create a lightweight working snapshot, update the core, and validate every installed node."
        )
        self.query_one("#manager-rollback", Button).tooltip = (
            "Restore the source revision and Python package manifest from a pre-update snapshot. Shared assets are never copied or removed."
        )
        self.query_one("#manager-view", Button).tooltip = "Inspect this installation without launching ComfyUI."
        self.query_one("#manager-uninstall", Button).tooltip = "Permanently remove this ComfyUI installation after explicit confirmation."
        self.query_one("#manager-log-follow", Button).tooltip = "Keep the live output pinned to the newest line."
        self.query_one("#manager-log-clear", Button).tooltip = "Clear this display and rotate to a fresh runtime log without deleting prior logs."
        self.query_one("#manager-log-browse", Button).tooltip = "Search, view, copy, delete, and configure retention for runtime logs."
        self.query_one("#manager-log-copy", Button).tooltip = "Copy the complete sanitized runtime log to the system clipboard."

    @work(thread=True, exclusive=True, group="log-cleanup")
    def cleanup_expired_logs(self) -> None:
        cleanup_all_installation_logs(item.path for item in self.app.installations)

    def refresh_all(self, *, prefer_manage: bool = False, reload_profiles: bool = False) -> None:
        if reload_profiles:
            self.app.reload_profiles()
        self._set_profile_options()
        self._set_installation_options()
        self._set_manager_options()
        self._set_nodes_options()
        self._update_system_summary()
        self._refresh_workspace_tabs()
        if prefer_manage and self.app.installations:
            self.query_one("#main-tabs", TabbedContent).active = "manage-tab"

    def _update_system_summary(self) -> None:
        info = self.app.platform_info
        cuda = f" · CUDA {info.cuda_version}" if info.cuda_version else ""
        self.query_one("#system-summary", Static).update(
            f"[b]{info.display_os}[/b] · {info.architecture}\n"
            f"[b]{info.accelerator}[/b] · {info.gpu_name or 'No supported GPU'}{cuda}\n"
            f"Packages: {info.package_manager or 'not detected'}"
        )

    def _set_profile_options(self) -> None:
        select = self.query_one("#profile-select", Select)
        options = [(record.label, record.id) for record in self.app.profile_records]
        select.set_options(options)
        preferred = self.app.selected_profile_id
        values = {value for _, value in options}
        if preferred not in values:
            preferred = "vanilla-comfyui" if "vanilla-comfyui" in values else next(iter(values), Select.BLANK)
        select.value = preferred
        if isinstance(preferred, str):
            self.app.selected_profile_id = preferred
            self._render_profile(preferred)

    def _set_installation_options(self) -> None:
        select = self.query_one("#installation-select", Select)
        options: list[tuple[str, str]] = [("New installation…", "__new__")]
        options.extend((f"{item.name} — {item.path}", str(item.path)) for item in self.app.installations)
        select.set_options(options)
        preferred = self.app.selected_installation or "__new__"
        if preferred not in {value for _, value in options}:
            preferred = "__new__"
        select.value = preferred
        self._render_installation(preferred)

    def _set_manager_options(self) -> None:
        select = self.query_one("#manage-installation-select", Select)
        options = [(f"{item.name} — {item.path}", str(item.path)) for item in self.app.installations]
        empty = not options
        select.set_options(options or [("No installations found", "__none__")])
        select.disabled = empty
        if empty:
            self.query_one("#manager-empty", Static).remove_class("hidden")
            self.query_one("#manager-content", Container).add_class("hidden")
            select.value = "__none__"
            return
        self.query_one("#manager-empty", Static).add_class("hidden")
        self.query_one("#manager-content", Container).remove_class("hidden")
        preferred = self.app.selected_manager_installation
        values = {value for _, value in options}
        if preferred not in values:
            preferred = str(self.app.installations[0].path)
        self.app.selected_manager_installation = preferred
        select.value = preferred
        self._render_manager(preferred)

    def _set_nodes_options(self) -> None:
        select = self.query_one("#nodes-installation-select", Select)
        options = [(f"{item.name} — {item.path}", str(item.path)) for item in self.app.installations]
        empty = not options
        select.set_options(options or [("No installations found", "__none__")])
        select.disabled = empty
        preferred = self.app.selected_manager_installation if not empty else "__none__"
        if not empty and preferred not in {value for _, value in options}:
            preferred = str(self.app.installations[0].path)
        select.value = preferred
        self._render_nodes(preferred)

    def _render_nodes(self, value: str) -> None:
        log = self.query_one("#nodes-list", RichLog)
        log.clear()
        if value == "__none__":
            self.query_one("#nodes-count", Static).update("No installation selected.")
            log.write(Text("Run a scan or create a ComfyUI installation first."))
            return
        nodes = self.app.cached_nodes.get(value, [])
        self.query_one("#nodes-count", Static).update(f"{len(nodes)} installed nodes and plugins in the cached scan")
        output: list[Text] = []
        for item in nodes:
            manager_id = str(item.get("manager_id") or "").strip()
            repository = str(item.get("repository") or "").strip()
            if manager_id and repository:
                source_label = f"Registry/Manager: {manager_id} · {repository}"
            elif manager_id:
                source_label = f"Registry/Manager: {manager_id}"
            elif repository:
                source_label = repository
            else:
                source_label = "unresolved local source"
            commit = str(item.get("commit") or "")[:12]
            suffix = f" @ {commit}" if commit else ""
            trust = str(item.get("resolution_trust") or "").strip()
            trust_suffix = f" · {trust}" if trust else ""
            output.append(Text(f"{item.get('name', 'Unnamed')}  —  {source_label}{suffix}{trust_suffix}"))
        log.write_lines(output)
        if not nodes:
            log.write(Text("No installed custom nodes were recorded for this instance."))

    def _render_profile(self, profile_id: str) -> None:
        record = self.app.profile_record(profile_id)
        if record is None:
            return
        profile = record.profile
        description = profile.get("description", "").strip()
        if len(description) > 130:
            description = description[:127].rstrip() + "…"
        self.query_one("#profile-summary", Static).update(
            f"[b]{profile.get('name')}[/b] · v{profile.get('version', 'unknown')} · {record.source}\n"
            f"[b]ABI:[/b] {record.compatibility_label}\n"
            f"{len(profile.get('nodes', []))} nodes · "
            f"{len(profile.get('accelerated_packages', []))} accelerated packages"
        )

    def _render_installation(self, value: str) -> None:
        if value == "__new__":
            self.query_one("#installation-summary", Static).update(
                "Install a fresh official or profile-recommended ComfyUI checkout."
            )
            return
        installation = self.app.installation_for(value)
        if installation:
            self.query_one("#installation-summary", Static).update(
                f"[b]{installation.name}[/b]\n{installation.path}\n"
                f"{installation.node_count} nodes · {installation.workflow_count} workflows · "
                f"venv {'ready' if installation.has_venv else 'missing'}"
            )

    def _render_manager(self, value: str) -> None:
        installation = self.app.installation_for(value)
        if installation is None:
            return
        running = self.app.installation_running(value)
        self.query_one("#manager-name", Static).update(f"[b]{installation.name}[/b]")
        self.query_one("#manager-description", Static).update(installation.description or "Detected ComfyUI installation.")
        self.query_one("#manager-path", Static).update(str(installation.path))
        self.query_one("#manager-runtime", Static).update(
            f"{'Running' if running else 'Stopped'} · venv {'ready' if installation.has_venv else 'missing'} · "
            f"{installation.version or 'version unknown'} · {installation.branch or 'branch unknown'}"
        )
        self.query_one("#manager-overview", Static).update(
            f"[b]{installation.node_count}[/b] custom nodes\n"
            f"[b]{installation.workflow_count}[/b] native workflows\n"
            f"Profile: {installation.profile_name or 'not recorded'}"
        )
        self.query_one("#manager-repository", Static).update(installation.repository or "No Git origin detected.")
        self._attach_runtime_log(installation)
        self.query_one("#manager-launch", Button).disabled = running or not installation.has_venv
        self.query_one("#manager-stop", Button).disabled = not running
        snapshots = ComfyUpdateManager(installation.path).list_snapshots()
        self.query_one("#manager-update", Button).disabled = running or not installation.has_venv
        self.query_one("#manager-rollback", Button).disabled = running or not snapshots
        rollback_label = f"Rollback ({len(snapshots)})" if snapshots else "Rollback"
        self.query_one("#manager-rollback", Button).label = rollback_label

    def _attach_runtime_log(self, installation: ComfyInstallation, *, force: bool = False) -> None:
        runtime_log = installation.path / ".comfy-setup" / "runtime.log"
        if not force and self._runtime_log_path == runtime_log:
            return

        cleanup_runtime_logs(installation.path)
        self._runtime_log_path = runtime_log
        try:
            stat = runtime_log.stat()
            self._runtime_log_identity = (stat.st_dev, stat.st_ino)
        except OSError:
            self._runtime_log_identity = None
        self._runtime_decoder = TerminalStreamDecoder()
        self._runtime_lines = []
        log_widget = self.query_one("#manager-log-preview", RichLog)
        log_widget.clear()

        lines, offset = read_log_tail(runtime_log)
        self._runtime_log_offset = offset
        if not lines:
            if self._runtime_cleared:
                return
            message = "No runtime output yet. Launch this instance to stream output here."
            self._runtime_lines.append(message)
            log_widget.write(Text(message), scroll_end=True)
            return

        self._runtime_cleared = False
        self._runtime_lines.extend(lines[-10000:])
        log_widget.write_lines((Text(line) for line in self._runtime_lines), scroll_end=True)

    def _append_runtime_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        log_widget = self.query_one("#manager-log-preview", RichLog)
        if self._runtime_lines == ["No runtime output yet. Launch this instance to stream output here."]:
            self._runtime_lines.clear()
            log_widget.clear()
        # If the user has scrolled upward, do not snap them back to the end.
        at_end = bool(getattr(log_widget, "is_vertical_scroll_end", True))
        should_follow = self._runtime_follow and at_end
        self._runtime_lines.extend(lines)
        if len(self._runtime_lines) > 10000:
            self._runtime_lines = self._runtime_lines[-10000:]
        log_widget.write_lines((Text(line) for line in lines), scroll_end=should_follow)

    def _poll_runtime_output(self) -> None:
        path = self._runtime_log_path
        if path is None:
            return
        try:
            stat = path.stat()
            size = stat.st_size
            identity = (stat.st_dev, stat.st_ino)
        except OSError:
            return

        if self._runtime_log_identity is not None and identity != self._runtime_log_identity:
            installation = self.app.installation_for(self.app.selected_manager_installation)
            if installation is not None:
                self._attach_runtime_log(installation, force=True)
            return
        self._runtime_log_identity = identity

        if size < self._runtime_log_offset:
            installation = self.app.installation_for(self.app.selected_manager_installation)
            if installation is not None:
                self._attach_runtime_log(installation, force=True)
            return
        if size > self._runtime_log_offset:
            try:
                with path.open("rb") as handle:
                    handle.seek(self._runtime_log_offset)
                    data = handle.read(size - self._runtime_log_offset)
            except OSError:
                return
            self._runtime_log_offset = size
            self._append_runtime_lines(self._runtime_decoder.feed(data))

        running = self.app.installation_running(self.app.selected_manager_installation)
        if self._runtime_process_was_running and not running:
            self._append_runtime_lines(self._runtime_decoder.flush())
            installation = self.app.installation_for(self.app.selected_manager_installation)
            if installation is not None:
                self.query_one("#manager-runtime", Static).update(
                    f"Stopped · venv {'ready' if installation.has_venv else 'missing'} · "
                    f"{installation.version or 'version unknown'} · {installation.branch or 'branch unknown'}"
                )
                self.query_one("#manager-launch", Button).disabled = not installation.has_venv
                self.query_one("#manager-stop", Button).disabled = True
                self.query_one("#manager-update", Button).disabled = not installation.has_venv
        self._runtime_process_was_running = running

    def _active_scroll_target(self) -> Any | None:
        focused = self.app.focused
        if isinstance(focused, RichLog):
            return focused
        if isinstance(focused, Input):
            return None
        tabs = self.query_one("#main-tabs", TabbedContent)
        selector = {
            "manage-tab": "#manage-scroll",
            "setup-tab": "#wizard",
            "nodes-tab": "#nodes-scroll",
            "workflows-tab": "#workflows-scroll",
            "models-tab": "#models-scroll",
            "loras-tab": "#loras-scroll",
            "agents-skills-tab": "#agents-skills-scroll",
            "mcps-tab": "#mcps-scroll",
        }.get(tabs.active, "#wizard")
        return self.query_one(selector, ScrollableContainer)

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"h", "j", "k", "l"}:
            return
        target = self._active_scroll_target()
        if target is None:
            return
        delta = {"h": (-4, 0), "j": (0, 3), "k": (0, -3), "l": (4, 0)}[event.key]
        target.scroll_relative(x=delta[0], y=delta[1], animate=False)
        event.prevent_default()
        event.stop()

    @on(Button.Pressed, "#manager-log-follow")
    def toggle_runtime_follow(self) -> None:
        self._runtime_follow = not self._runtime_follow
        button = self.query_one("#manager-log-follow", Button)
        button.label = f"Follow: {'On' if self._runtime_follow else 'Off'}"
        if self._runtime_follow:
            self.query_one("#manager-log-preview", RichLog).scroll_end(animate=False)


    @on(Button.Pressed, "#manager-log-clear")
    def clear_runtime_output(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation is None:
            return
        running = self.app.installation_running(self.app.selected_manager_installation)
        if not running:
            # A ComfyUI instance may have been launched by the automation CLI or
            # by an earlier manager session.  In that case it is not present in
            # this App object's subprocess table, but its managed relay still
            # owns runtime.log and must perform the rotation itself.
            running = bool(instance_status(installation.path).get("running"))
        if running:
            request_log_rotation(installation.path, reason="manual-clear")
            try:
                self._runtime_log_offset = self._runtime_log_path.stat().st_size if self._runtime_log_path else 0
            except OSError:
                self._runtime_log_offset = 0
        else:
            rotate_runtime_log(installation.path, reason="manual-clear")
            self._runtime_log_offset = 0
        self._runtime_decoder = TerminalStreamDecoder()
        self._runtime_lines = []
        self._runtime_cleared = True
        self.query_one("#manager-log-preview", RichLog).clear()
        self.query_one("#manager-status", Static).update(
            "Output display cleared. Previous output was preserved and a fresh runtime log was created."
        )

    @on(Button.Pressed, "#manager-log-browse")
    def browse_runtime_logs(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation is not None:
            self.app.push_screen(LogBrowserScreen(installation.path))

    @on(Button.Pressed, "#manager-log-copy")
    def copy_runtime_log(self) -> None:
        text = "\n".join(self._runtime_lines)
        if not text.strip():
            self.app.notify("The runtime log is empty.", severity="warning")
            return
        self.app.copy_to_clipboard(text)
        self.app.notify("Runtime log copied to the clipboard.")

    def _refresh_workspace_tabs(self) -> None:
        """Render only cached inventories; no filesystem scan occurs here."""
        try:
            paths = load_shared_asset_paths()
            assets = self.app.cached_assets
            models = assets.get("models", [])
            loras = assets.get("loras", [])
            workflows = assets.get("workflows", [])

            self.query_one("#models-root", Static).update(str(paths.models))
            self.query_one("#models-count", Static).update(
                f"{len(models)} files in the cached scan · LoRAs excluded"
            )
            self.query_one("#models-source-count", Static).update(
                f"{len(list_asset_sources('models'))} sources · {len(list_asset_entries('models'))} catalog entries"
            )
            model_log = self.query_one("#models-list", RichLog)
            model_log.clear()
            model_log.write_lines(
                Text(f"{item['relative_path']}  ({item['size']} bytes)") for item in models[:10000]
            )
            if not models:
                model_log.write(Text("No model files are present in the cached scan. Use Rescan after external changes."))

            self.query_one("#loras-root", Static).update(str(paths.models / "loras"))
            self.query_one("#loras-count", Static).update(f"{len(loras)} LoRA files in the cached scan")
            self.query_one("#loras-source-count", Static).update(
                f"{len(list_asset_sources('loras'))} sources · {len(list_asset_entries('loras'))} catalog entries"
            )
            lora_log = self.query_one("#loras-list", RichLog)
            lora_log.clear()
            lora_log.write_lines(
                Text(f"{item['relative_path']}  ({item['size']} bytes)") for item in loras[:10000]
            )
            if not loras:
                lora_log.write(Text("No LoRA files are present in the cached scan."))

            self.query_one("#workflows-root", Static).update(str(paths.workflows))
            self.query_one("#workflows-count", Static).update(
                f"{len(workflows)} files in the cached scan · directory structure preserved"
            )
            self.query_one("#workflows-source-count", Static).update(
                f"{len(list_asset_sources('workflows'))} sources · {len(list_asset_entries('workflows'))} catalog entries"
            )
            workflow_log = self.query_one("#workflows-list", RichLog)
            workflow_log.clear()
            workflow_log.write_lines(Text(item["relative_path"]) for item in workflows[:10000])
            if not workflows:
                workflow_log.write(Text("No workflow files are present in the cached scan."))

            skills = list_bundled_skills()
            targets = configured_agent_targets()
            self.query_one("#skills-root", Static).update(str(bundled_skills_root()))
            self.query_one("#skills-count", Static).update(f"{len(skills)} bundled portable skills")
            self.query_one("#agents-count", Static).update(f"{len(targets)} enabled agent destinations")
            skill_log = self.query_one("#skills-list", RichLog)
            skill_log.clear()
            skill_log.write_lines(Text(f"{skill.id} — {skill.description}") for skill in skills)
            if not skills:
                skill_log.write(Text("No bundled Agent Skills were found."))

            servers = list_mcp_servers()
            self.query_one("#mcps-count", Static).update(
                f"{len(servers)} configured servers · {sum(1 for item in servers if item.enabled)} enabled"
            )
            mcp_log = self.query_one("#mcps-list", RichLog)
            mcp_log.clear()
            mcp_lines: list[Text] = []
            for server in servers:
                endpoint = server.command or server.url or "endpoint not configured"
                state = "enabled" if server.enabled else "disabled"
                mcp_lines.append(Text(f"{server.name} [{server.transport}, {state}] — {endpoint}"))
            mcp_log.write_lines(mcp_lines)
            if not servers:
                mcp_log.write(Text("No MCP server definitions are configured."))
        except Exception as exc:
            self.app.notify(f"Could not reload workspace YAML: {exc}", severity="error")

    def _refresh_asset_tabs(self) -> None:
        """Compatibility alias used by child screens after a settings change."""
        self._refresh_workspace_tabs()

    @on(Button.Pressed, "#models-configure-paths")
    @on(Button.Pressed, "#loras-configure-paths")
    @on(Button.Pressed, "#workflows-configure-paths")
    def configure_shared_paths(self) -> None:
        self.app.push_screen(SharedAssetPathsScreen())

    @on(Button.Pressed, "#models-import")
    def import_model(self) -> None:
        self.app.push_screen(AssetImportScreen("models"))

    @on(Button.Pressed, "#loras-import")
    def import_lora(self) -> None:
        self.app.push_screen(AssetImportScreen("loras"))

    @on(Button.Pressed, "#models-export")
    def export_model(self) -> None:
        self.app.push_screen(AssetExportScreen("models"))

    @on(Button.Pressed, "#loras-export")
    def export_lora(self) -> None:
        self.app.push_screen(AssetExportScreen("loras"))

    @on(Button.Pressed, "#models-download")
    def download_model(self) -> None:
        self.app.push_screen(AssetDownloadScreen("models"))

    @on(Button.Pressed, "#loras-download")
    def download_lora(self) -> None:
        self.app.push_screen(AssetDownloadScreen("loras"))

    @on(Button.Pressed, "#workflows-download")
    def download_workflow(self) -> None:
        self.app.push_screen(AssetDownloadScreen("workflows"))

    @on(Button.Pressed, "#models-delete")
    def delete_model(self) -> None:
        self.app.push_screen(AssetDeleteScreen("models"))

    @on(Button.Pressed, "#loras-delete")
    def delete_lora(self) -> None:
        self.app.push_screen(AssetDeleteScreen("loras"))

    @on(Button.Pressed, "#workflows-delete")
    def delete_workflow(self) -> None:
        self.app.push_screen(AssetDeleteScreen("workflows"))

    @on(Button.Pressed, "#models-tasks")
    def model_tasks(self) -> None:
        self.app.push_screen(AssetTasksScreen("models"))

    @on(Button.Pressed, "#loras-tasks")
    def lora_tasks(self) -> None:
        self.app.push_screen(AssetTasksScreen("loras"))

    @on(Button.Pressed, "#workflows-tasks")
    def workflow_tasks(self) -> None:
        self.app.push_screen(AssetTasksScreen("workflows"))

    @on(Button.Pressed, "#models-edit-sources")
    def edit_model_sources(self) -> None:
        self.app.push_screen(YamlConfigEditorScreen("model-sources.yaml", "MODEL SOURCES"))

    @on(Button.Pressed, "#loras-edit-sources")
    def edit_lora_sources(self) -> None:
        self.app.push_screen(YamlConfigEditorScreen("lora-sources.yaml", "LORA SOURCES"))

    @on(Button.Pressed, "#workflows-edit-sources")
    def edit_workflow_sources(self) -> None:
        self.app.push_screen(YamlConfigEditorScreen("workflow-sources.yaml", "WORKFLOW SOURCES"))

    @on(Button.Pressed, "#models-open-sources")
    def open_model_sources(self) -> None:
        open_source_file(ensure_editable_config("model-sources.yaml"))

    @on(Button.Pressed, "#loras-open-sources")
    def open_lora_sources(self) -> None:
        open_source_file(ensure_editable_config("lora-sources.yaml"))

    @on(Button.Pressed, "#workflows-open-sources")
    def open_workflow_sources(self) -> None:
        open_source_file(ensure_editable_config("workflow-sources.yaml"))

    @on(Button.Pressed, "#workflows-manage")
    def workflow_manager_tab(self) -> None:
        self.app.push_screen(WorkflowHubScreen())

    @on(Button.Pressed, "#models-refresh")
    @on(Button.Pressed, "#loras-refresh")
    @on(Button.Pressed, "#workflows-refresh")
    def refresh_inventories(self) -> None:
        self.app.start_runtime_scan(prefer_manage=False, title="RESCANNING INVENTORIES")

    @on(Button.Pressed, "#nodes-refresh")
    def refresh_nodes(self) -> None:
        self.app.start_runtime_scan(
            prefer_manage=False,
            title="REFRESHING NODE REGISTRIES",
            force_catalog_refresh=True,
        )

    @on(Button.Pressed, "#nodes-open-folder")
    def open_nodes_folder(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation is None:
            self.app.notify("Choose an installation first.", severity="warning")
            return
        open_source_file(installation.path / "custom_nodes")

    @on(Button.Pressed, "#nodes-view-instance")
    def view_nodes_instance(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation is not None:
            self.app.push_screen(InstallationOverviewScreen(installation.path))

    @on(Button.Pressed, "#skills-install")
    def install_agent_skill(self) -> None:
        self.app.push_screen(AgentSkillInstallScreen())

    @on(Button.Pressed, "#skills-edit-config")
    def edit_agent_config(self) -> None:
        self.app.push_screen(YamlConfigEditorScreen("agents-skills.yaml", "AGENTS & SKILLS"))

    @on(Button.Pressed, "#skills-open-config")
    def open_agent_config(self) -> None:
        open_source_file(ensure_editable_config("agents-skills.yaml"))

    @on(Button.Pressed, "#mcps-edit-config")
    def edit_mcp_config(self) -> None:
        self.app.push_screen(YamlConfigEditorScreen("mcps.yaml", "MCP SERVERS"))

    @on(Button.Pressed, "#mcps-open-config")
    def open_mcp_config(self) -> None:
        open_source_file(ensure_editable_config("mcps.yaml"))

    @on(Button.Pressed, "#skills-refresh")
    @on(Button.Pressed, "#mcps-refresh")
    def reload_agent_workspaces(self) -> None:
        self.app.reload_from_yaml()

    @on(Select.Changed, "#profile-select")
    def profile_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.app.selected_profile_id = event.value
            self._render_profile(event.value)

    @on(Select.Changed, "#installation-select")
    def installation_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.app.selected_installation = event.value
            self._render_installation(event.value)

    @on(Select.Changed, "#manage-installation-select")
    def manager_installation_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.app.selected_manager_installation = event.value
            self._render_manager(event.value)
            nodes_select = self.query_one("#nodes-installation-select", Select)
            if not nodes_select.disabled:
                nodes_select.value = event.value

    @on(Select.Changed, "#nodes-installation-select")
    def nodes_installation_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.app.selected_manager_installation = event.value
            self._render_nodes(event.value)

    def _rescan(self, *, prefer_manage: bool = False) -> None:
        self.app.start_runtime_scan(prefer_manage=prefer_manage, title="SCANNING SYSTEM")

    @on(Button.Pressed, "#rescan")
    @on(Button.Pressed, "#manager-rescan")
    def rescan(self) -> None:
        self._rescan(prefer_manage=True)

    @on(Button.Pressed, "#manager-reload")
    @on(Button.Pressed, "#reload-settings")
    def reload_settings(self) -> None:
        self.app.reload_from_yaml()

    @on(Button.Pressed, "#scan-folder")
    @on(Button.Pressed, "#manager-scan")
    def scan_folder(self) -> None:
        self.app.push_screen(ScanFolderScreen())

    @on(Button.Pressed, "#manager-launch")
    def launch_selected(self) -> None:
        value = self.app.selected_manager_installation
        installation = self.app.installation_for(value)
        if installation is None:
            return
        try:
            self.app.launch_installation(installation)
        except LauncherError as exc:
            self.app.notify(str(exc), severity="error")
            return
        self.query_one("#manager-status", Static).update(
            f"Launched {installation.name}. Runtime output: {installation.path / '.comfy-setup' / 'runtime.log'}"
        )
        self._render_manager(value)

    @on(Button.Pressed, "#manager-stop")
    def stop_selected(self) -> None:
        value = self.app.selected_manager_installation
        installation = self.app.installation_for(value)
        if installation and self.app.stop_installation(value):
            self.query_one("#manager-status", Static).update(f"Stopped {installation.name}.")
        self._render_manager(value)

    @on(Button.Pressed, "#manager-update")
    def update_selected(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation:
            self.app.push_screen(UpdateInstallationScreen(installation.path))

    @on(Button.Pressed, "#manager-rollback")
    def rollback_selected(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation:
            self.app.push_screen(SnapshotManagerScreen(installation.path))

    @on(Button.Pressed, "#manager-view")
    def view_selected(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation:
            self.app.push_screen(InstallationOverviewScreen(installation.path))

    @on(Button.Pressed, "#manager-edit")
    def edit_selected(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation:
            self.app.push_screen(EditInstallationScreen(installation.path))

    @on(Button.Pressed, "#manager-uninstall")
    def uninstall_selected(self) -> None:
        installation = self.app.installation_for(self.app.selected_manager_installation)
        if installation:
            self.app.push_screen(UninstallInstallationScreen(installation.path))

    @on(Button.Pressed, "#import-profile")
    def import_screen(self) -> None:
        self.app.push_screen(ImportProfileScreen())

    @on(Button.Pressed, "#export-setup")
    def export_screen(self) -> None:
        self.app.push_screen(ExportSetupScreen())

    @on(Button.Pressed, "#manage-profiles")
    def manage_profiles(self) -> None:
        self.app.push_screen(ProfileLibraryScreen())

    @on(Button.Pressed, "#continue")
    def continue_install(self) -> None:
        record = self.app.profile_record(self.app.selected_profile_id)
        if record is None:
            self.app.notify("Choose a setup profile.", severity="error")
            return
        self.app.active_profile = copy.deepcopy(record.profile)
        target = None if self.app.selected_installation == "__new__" else Path(self.app.selected_installation)
        self.app.push_screen(DestinationScreen(target))


class InstallationOverviewScreen(WizardScreen):
    def __init__(self, installation_path: Path) -> None:
        super().__init__()
        self.installation_path = installation_path.expanduser().resolve()

    def compose(self) -> ComposeResult:
        installation = self.app.installation_for(str(self.installation_path))
        nodes = installed_nodes(self.installation_path)
        workflows = installed_workflows(self.installation_path)
        node_lines = [
            f"- **{node.name}** — `{node.repository or 'local/unpublished'}`"
            + (f" · `{node.commit[:12]}`" if node.commit else "")
            for node in nodes
        ] or ["- No custom-node directories found."]
        workflow_lines = [
            f"- `{_display_path(workflow.path, self.installation_path)}` · user **{workflow.user_name}**"
            for workflow in workflows
        ] or ["- No native workflow JSON files found."]
        yield Header()
        with Container(id="wizard"):
            yield from self.compose_header(
                "INSTALLATION OVERVIEW",
                "Inspect plugins, workflows, repository state, and environment readiness without launching ComfyUI.",
            )
            with ScrollableContainer(id="installation-overview-scroll", can_focus=True):
                yield Markdown(
                    f"## {installation.name if installation else self.installation_path.name}\n\n"
                    f"`{self.installation_path}`\n\n"
                    f"{installation.description if installation else ''}\n\n"
                    f"### Repository\n\n`{installation.repository if installation and installation.repository else 'not detected'}`\n\n"
                    f"### Custom nodes ({len(nodes)})\n\n" + "\n".join(node_lines) + "\n\n"
                    f"### Workflows ({len(workflows)})\n\n" + "\n".join(workflow_lines),
                    id="installation-overview-markdown",
                )
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Edit details", id="edit")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#edit")
    def edit(self) -> None:
        self.app.push_screen(EditInstallationScreen(self.installation_path))


class LogDeleteConfirmScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, record: RuntimeLogFile) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        with Container(id="log-delete-dialog"):
            yield Static("DELETE ARCHIVED LOG", classes="card-title")
            yield Static(
                f"Delete [b]{self.record.path.name}[/b]?\n\n{self.record.path}\n\n"
                "This removes the archived file permanently. The active runtime log is never deleted here.",
                classes="help-text",
            )
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="log-delete-cancel")
                yield Button("Delete log", id="log-delete-confirm", classes="danger")

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#log-delete-cancel")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#log-delete-confirm")
    def confirm(self) -> None:
        self.dismiss(True)


class LogBrowserScreen(WizardScreen):
    def __init__(self, installation_path: Path) -> None:
        super().__init__()
        self.installation_path = installation_path.expanduser().resolve()
        self.records: list[RuntimeLogFile] = []

    def compose(self) -> ComposeResult:
        retention = load_log_retention()
        yield Header()
        with ScrollableContainer(id="log-browser-shell", can_focus=True):
            yield from self.compose_header(
                "RUNTIME LOG BROWSER",
                "Search, inspect, copy, and remove archived logs. The active log remains protected.",
            )
            with Container(classes="card log-browser-controls"):
                with Horizontal(classes="path-field-row"):
                    yield Input(placeholder="Search filenames and recent log contents", id="log-search")
                    yield Button("Search", id="log-search-run")
                    yield Button("Refresh", id="log-refresh")
                yield Label("Log file")
                yield Select([("Loading logs…", "__none__")], value="__none__", id="log-file-select", allow_blank=False)
                yield Static(id="log-file-detail", classes="compact-detail")
                with Horizontal(classes="log-browser-actions"):
                    yield Button("Copy selected", id="log-copy", classes="primary")
                    yield Button("Delete selected", id="log-delete", classes="danger")
                with Horizontal(classes="retention-row"):
                    yield Select(
                        [
                            ("Daily (1 day)", "daily"),
                            ("Weekly (7 days)", "weekly"),
                            ("Monthly (30 days)", "monthly"),
                            ("Custom days", "custom"),
                        ],
                        value=retention.mode,
                        id="log-retention-mode",
                        allow_blank=False,
                    )
                    yield Input(str(retention.days), id="log-retention-days", type="integer")
                    yield Button("Save retention", id="log-retention-save")
                    yield Button("Clean now", id="log-clean-now")
                yield Static(
                    "Automatic cleanup is enabled. Archived logs older than the selected period are removed; the active log is preserved.",
                    id="log-browser-status",
                    classes="help-text",
                )
            yield RichLog(
                id="log-file-view",
                wrap=False,
                markup=False,
                highlight=False,
                max_lines=50000,
                auto_scroll=False,
                min_width=1,
            )
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        mode = self.query_one("#log-retention-mode", Select).value
        self.query_one("#log-retention-days", Input).disabled = mode != "custom"
        self.refresh_logs()

    def selected_record(self) -> RuntimeLogFile | None:
        value = self.query_one("#log-file-select", Select).value
        if not isinstance(value, str):
            return None
        return next((record for record in self.records if str(record.path) == value), None)

    def refresh_logs(self) -> None:
        query = self.query_one("#log-search", Input).value
        cleanup_runtime_logs(self.installation_path)
        self.records = search_runtime_logs(self.installation_path, query)
        select = self.query_one("#log-file-select", Select)
        options = [(record.label, str(record.path)) for record in self.records]
        select.set_options(options or [("No matching log files", "__none__")])
        select.value = str(self.records[0].path) if self.records else "__none__"
        self._render_selected()

    def _render_selected(self) -> None:
        record = self.selected_record()
        viewer = self.query_one("#log-file-view", RichLog)
        viewer.clear()
        delete = self.query_one("#log-delete", Button)
        if record is None:
            self.query_one("#log-file-detail", Static).update("No log selected.")
            delete.disabled = True
            viewer.write(Text("No matching log files."))
            return
        delete.disabled = record.active
        delete.tooltip = (
            "The active log is protected. Use Clear output on Launch & Manage to rotate it."
            if record.active
            else "Delete this archived log permanently."
        )
        self.query_one("#log-file-detail", Static).update(
            f"{record.path}\n{'Active runtime log' if record.active else 'Archived runtime log'} · {record.size:,} bytes"
        )
        try:
            text = read_log_text(record.path, max_bytes=8 * 1024 * 1024)
        except OSError as exc:
            viewer.write(Text(f"Could not read log: {exc}"))
            return
        lines = text.splitlines()
        if record.size > 8 * 1024 * 1024:
            viewer.write(Text("[Showing the final 8 MiB of this log]"))
        if not lines:
            viewer.write(Text("This log is empty."))
            return
        for raw in lines[-50000:]:
            cleaned = clean_terminal_lines(raw)
            viewer.write(Text(cleaned[0] if cleaned else ""))
        viewer.scroll_home(animate=False)

    @on(Select.Changed, "#log-file-select")
    def log_selected(self) -> None:
        self._render_selected()

    @on(Input.Submitted, "#log-search")
    @on(Button.Pressed, "#log-search-run")
    @on(Button.Pressed, "#log-refresh")
    def search_or_refresh(self) -> None:
        self.refresh_logs()

    @on(Button.Pressed, "#log-copy")
    def copy_selected(self) -> None:
        record = self.selected_record()
        if record is None:
            return
        try:
            text = read_log_text(record.path)
        except OSError as exc:
            self.app.notify(f"Could not read log: {exc}", severity="error")
            return
        self.app.copy_to_clipboard(text)
        self.query_one("#log-browser-status", Static).update(f"Copied {record.path.name} to the clipboard.")

    @on(Button.Pressed, "#log-delete")
    def delete_selected(self) -> None:
        record = self.selected_record()
        if record is None or record.active:
            self.app.notify("The active runtime log cannot be deleted.", severity="warning")
            return
        self.app.push_screen(LogDeleteConfirmScreen(record), lambda confirmed: self._delete_confirmed(record, confirmed))

    def _delete_confirmed(self, record: RuntimeLogFile, confirmed: bool | None) -> None:
        if not confirmed:
            return
        try:
            deleted = delete_runtime_log(self.installation_path, record.path)
        except ValueError as exc:
            self.app.notify(str(exc), severity="error")
            return
        self.query_one("#log-browser-status", Static).update(
            f"Deleted {record.path.name}." if deleted else "The selected log was already missing."
        )
        self.refresh_logs()

    @on(Select.Changed, "#log-retention-mode")
    def retention_mode_changed(self, event: Select.Changed) -> None:
        self.query_one("#log-retention-days", Input).disabled = event.value != "custom"

    def _retention_from_fields(self) -> LogRetention | None:
        mode = self.query_one("#log-retention-mode", Select).value
        if not isinstance(mode, str):
            return None
        try:
            days = max(1, int(self.query_one("#log-retention-days", Input).value or "30"))
        except ValueError:
            self.app.notify("Custom retention must be a whole number of days.", severity="error")
            return None
        return LogRetention(mode=mode, days=days, auto_cleanup=True)

    @on(Button.Pressed, "#log-retention-save")
    def save_retention(self) -> None:
        retention = self._retention_from_fields()
        if retention is None:
            return
        save_log_retention(retention)
        self.query_one("#log-retention-days", Input).value = str(retention.effective_days)
        self.query_one("#log-browser-status", Static).update(
            f"Retention saved: archived logs are kept for {retention.effective_days} day(s)."
        )

    @on(Button.Pressed, "#log-clean-now")
    def clean_now(self) -> None:
        retention = self._retention_from_fields()
        if retention is None:
            return
        removed = cleanup_runtime_logs(self.installation_path, retention)
        self.query_one("#log-browser-status", Static).update(
            f"Cleanup complete: removed {len(removed)} archived log file(s)."
        )
        self.refresh_logs()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()


class EditInstallationScreen(WizardScreen):
    def __init__(self, installation_path: Path) -> None:
        super().__init__()
        self.installation_path = installation_path.expanduser().resolve()

    def compose(self) -> ComposeResult:
        installation = self.app.installation_for(str(self.installation_path))
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header("EDIT INSTANCE DETAILS", "Customize how this installation appears in Launch & Manage.")
            with Container(classes="card compact-form"):
                yield Label("Display name")
                yield Input(installation.name if installation else self.installation_path.name, id="instance-name")
                yield Label("Brief description")
                yield Input(installation.description if installation else "", id="instance-description")
                yield Static(str(self.installation_path), classes="help-text path-text")
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="back")
                yield Button("Save", id="save", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        installation = self.app.installation_for(str(self.installation_path))
        save_instance_metadata(
            self.installation_path,
            name=self.query_one("#instance-name", Input).value,
            description=self.query_one("#instance-description", Input).value,
            profile_id=installation.profile_id if installation else None,
            profile_name=installation.profile_name if installation else None,
        )
        self.app.rescan_installations()
        self.app.pop_screen()
        if isinstance(self.app.screen, InstallationOverviewScreen):
            self.app.pop_screen()
        home = self.app.screen
        if isinstance(home, HomeScreen):
            home.refresh_all(prefer_manage=True)


class UninstallInstallationScreen(WizardScreen):
    def __init__(self, installation_path: Path) -> None:
        super().__init__()
        self.installation_path = installation_path.expanduser().resolve()

    def compose(self) -> ComposeResult:
        installation = self.app.installation_for(str(self.installation_path))
        name = installation.name if installation else self.installation_path.name
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header("UNINSTALL COMFYUI INSTANCE", "This permanently removes the selected installation directory.")
            with Container(classes="card error-box compact-form"):
                yield Static(f"[b]{name}[/b]\n{self.installation_path}")
                yield Static(
                    "This deletes the entire directory, including models, workflows, outputs, and local changes stored inside it. "
                    "External model directories referenced through extra_model_paths.yaml are not deleted.",
                    classes="help-text",
                )
                yield Label("Type UNINSTALL to confirm")
                yield Input("", id="uninstall-confirm")
                yield Static("Ready.", id="uninstall-status", classes="help-text")
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="back")
                yield Button("Permanently uninstall", id="uninstall", classes="danger")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#uninstall")
    def uninstall(self) -> None:
        if self.query_one("#uninstall-confirm", Input).value.strip() != "UNINSTALL":
            self.app.notify("Type UNINSTALL exactly to confirm.", severity="error")
            return
        if not is_comfyui_directory(self.installation_path):
            self.app.notify("The selected directory is no longer a valid ComfyUI installation.", severity="error")
            return
        home = Path.home().resolve()
        if self.installation_path in {Path(self.installation_path.anchor), home} or len(self.installation_path.parts) < 3:
            self.app.notify("Refusing to delete an unsafe directory.", severity="error")
            return
        self.query_one("#uninstall", Button).disabled = True
        self.query_one("#uninstall-status", Static).update("Removing installation…")
        self.perform_uninstall()

    @work(thread=True, exclusive=True)
    def perform_uninstall(self) -> None:
        self.app.stop_installation(str(self.installation_path))
        try:
            shutil.rmtree(self.installation_path)
            remove_instance_metadata(self.installation_path)
        except Exception as exc:
            self.app.call_from_thread(self.uninstall_failed, str(exc))
            return
        self.app.call_from_thread(self.uninstall_finished)

    def uninstall_failed(self, message: str) -> None:
        self.query_one("#uninstall", Button).disabled = False
        self.query_one("#uninstall-status", Static).update(f"Failed: {message}")

    def uninstall_finished(self) -> None:
        self.app.rescan_installations()
        while len(self.app.screen_stack) > 1:
            self.app.pop_screen()
        home = self.app.screen
        if isinstance(home, HomeScreen):
            home.refresh_all(prefer_manage=bool(self.app.installations))
            home.query_one("#home-status", Static).update("Installation removed.")


class UpdateInstallationScreen(WizardScreen):
    """Compatibility preflight, snapshot, official core update, and validation."""

    def __init__(self, installation_path: Path) -> None:
        super().__init__()
        self.installation_path = installation_path.expanduser().resolve()
        self.preflight: UpdatePreflight | None = None
        self._busy = False

    def compose(self) -> ComposeResult:
        installation = self.app.installation_for(str(self.installation_path))
        yield Header()
        with ScrollableContainer(classes="update-screen-shell", can_focus=True):
            yield from self.compose_header(
                "UPDATE COMFYUI",
                "Check official ComfyUI, preserve custom nodes, create a lightweight snapshot, and validate before you keep the update.",
            )
            with Container(classes="card update-summary-card"):
                yield Static(
                    f"[b]{installation.name if installation else self.installation_path.name}[/b]  •  {self.installation_path}",
                    id="update-instance",
                )
                yield Static("Checking official ComfyUI…", id="update-summary", classes="compact-detail")
                yield Markdown("", id="update-issues")
                yield Static("Core ComfyUI files", classes="card-title update-review-title")
                yield Static("Waiting for preflight…", id="update-files", classes="compact-detail update-file-list")
                yield Static("Core Python libraries", classes="card-title update-review-title")
                yield Static(
                    "Required changes are selected. Compatible manifest-only changes remain optional.",
                    id="update-package-help",
                    classes="help-text",
                )
                yield SelectionList(id="update-packages", disabled=True, compact=True)
            with Container(classes="card update-console-card"):
                yield Static("Update console", classes="card-title")
                yield RichLog(id="update-log", wrap=False, markup=False, highlight=False, min_width=1, classes="embedded-terminal")
        # Keep every action outside the scrolling review so the footer can
        # never cover it and keyboard focus never lands on an off-screen row.
        with Container(id="update-action-dock"):
            with Horizontal(classes="button-row screen-actions update-actions"):
                yield Button("Abort update", id="update-back")
                yield Button("Create new install", id="update-separate")
                yield Button("Check again", id="update-check")
                yield Button("Snapshots", id="update-snapshots")
            with Horizontal(
                classes="button-row screen-actions update-actions update-strategy-actions",
                id="update-strategy-actions",
            ):
                yield Button("Safe update", id="update-run", classes="primary", disabled=True)
                yield Button("Try to patch current setup", id="update-patch", disabled=True)
                yield Button("Continue anyway", id="update-anyway", classes="danger", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#update-run", Button).tooltip = "Proceed only when every protected compatibility check passes."
        self.query_one("#update-patch", Button).tooltip = (
            "Resolve selected core and custom-node requirements together while preserving compiled, profile, and local packages."
        )
        self.query_one("#update-anyway", Button).tooltip = (
            "Ignore preflight blockers. A snapshot is still created and any failed validation is rolled back automatically."
        )
        self.query_one("#update-separate", Button).tooltip = (
            "Return to Setup & Install with New installation selected. This is recommended when a fork, Python mismatch, or dependency conflict is detected."
        )
        self.check_update()

    def append_log(self, line: str) -> None:
        _write_raw_terminal(self.query_one("#update-log", RichLog), line)

    def append_notice(self, markup: str) -> None:
        _write_notice(self.query_one("#update-log", RichLog), markup)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for selector in (
            "#update-back", "#update-separate", "#update-check", "#update-snapshots",
            "#update-run", "#update-patch", "#update-anyway",
        ):
            self.query_one(selector, Button).disabled = busy
        self.query_one("#update-packages", SelectionList).disabled = busy or not bool(
            self.preflight and self.preflight.core_package_changes
        )
        if not busy:
            available = bool(self.preflight and self.preflight.update_available)
            self.query_one("#update-run", Button).disabled = not bool(
                available and self.preflight and not self.preflight.high_risk and self.preflight.baseline_pip_ok
            )
            self.query_one("#update-patch", Button).disabled = not bool(
                available and self.preflight and self.preflight.patchable
            )
            self.query_one("#update-anyway", Button).disabled = not available

    @work(thread=True, exclusive=True, group="update-preflight")
    def check_update(self) -> None:
        self.app.call_from_thread(self._set_busy, True)
        self.app.call_from_thread(self.append_notice, "[bold cyan]Checking official ComfyUI and installed custom-node requirements…[/bold cyan]")
        manager = ComfyUpdateManager(
            self.installation_path,
            runner=Runner(log=lambda line: self.app.call_from_thread(self.append_log, line)),
        )
        try:
            preflight = manager.preflight(fetch=True)
        except Exception as exc:
            self.app.call_from_thread(self.preflight_failed, str(exc))
            return
        self.app.call_from_thread(self.preflight_finished, preflight)

    def preflight_failed(self, message: str) -> None:
        self.preflight = None
        self._set_busy(False)
        self.query_one("#update-strategy-actions").display = False
        self.query_one("#update-packages", SelectionList).clear_options()
        self.query_one("#update-files", Static).update("Preflight failed before the file list could be prepared.")
        self.query_one("#update-summary", Static).update(f"[bold red]Update check failed:[/bold red] {message}")
        self.append_notice("[bold red]Update check failed.[/bold red]")
        self.append_log(message)

    def preflight_finished(self, preflight: UpdatePreflight) -> None:
        self.preflight = preflight
        self._set_busy(False)
        self.query_one("#update-strategy-actions").display = preflight.update_available
        status = (
            "Already current"
            if not preflight.update_available
            else f"{preflight.commits_behind} official commit(s) available"
        )
        resolution_status = (
            ("passes" if preflight.resolution_possible else "fails")
            if preflight.resolution_checked
            else "not needed"
        )
        self.query_one("#update-summary", Static).update(
            f"[b]{status}[/b]  •  current `{preflight.current_commit[:12]}`  →  official `{preflight.target_commit[:12]}`\n"
            f"{preflight.custom_node_count} custom nodes  •  "
            f"requirements {'changed' if preflight.requirements_changed else 'unchanged'}  •  "
            f"Python {'supported' if preflight.python_supported else 'outside declared range'}  •  "
            f"current pip check {'passes' if preflight.baseline_pip_ok else 'fails'}  •  "
            f"protected resolution {resolution_status}"
        )
        file_lines = preflight.core_file_changes[:60]
        file_text = "\n".join(file_lines) if file_lines else "No core file changes."
        if len(preflight.core_file_changes) > 60:
            file_text += f"\n…and {len(preflight.core_file_changes) - 60} more files"
        self.query_one("#update-files", Static).update(file_text)
        package_list = self.query_one("#update-packages", SelectionList)
        package_list.clear_options()
        choices = []
        for item in preflight.core_package_changes:
            if item.target_requirement is None:
                continue
            current = item.current_version or "not installed"
            required = "required" if item.required else "optional/compatible"
            choices.append(
                (f"{item.name}: {current} → {item.target_requirement} ({required})", item.name, item.selected)
            )
        package_list.add_options(choices)
        package_list.disabled = not bool(choices)
        issue_lines = []
        icons = {"blocking": "⛔", "warning": "⚠", "info": "✓"}
        for issue in preflight.issues:
            issue_lines.append(f"- {icons.get(issue.severity, '•')} **{issue.title}** — {issue.detail}")
        removed = [item for item in preflight.core_package_changes if item.target_requirement is None]
        if removed:
            issue_lines.append("\n**No longer required by core (left installed)**")
            issue_lines.extend(f"- `{item.name}` {item.current_version or ''}" for item in removed)
        if preflight.protected_packages:
            protected = ", ".join(preflight.protected_packages[:30])
            suffix = f" …and {len(preflight.protected_packages) - 30} more" if len(preflight.protected_packages) > 30 else ""
            issue_lines.append(f"\n**Protected from resolver changes:** {protected}{suffix}")
        if not issue_lines:
            issue_lines.append("- ✓ No known compatibility blockers were detected. A startup validation still runs after the update.")
        self.query_one("#update-issues", Markdown).update("\n".join(issue_lines))
        if preflight.high_risk:
            self.append_notice("[bold yellow]Compatibility risks were detected. A separate installation is recommended.[/bold yellow]")
        elif preflight.update_available:
            self.append_notice("[bold green]No known blockers were detected. Update is ready.[/bold green]")
        else:
            self.append_notice("[bold green]This installation already matches official ComfyUI.[/bold green]")

    @on(Button.Pressed, "#update-check")
    def check_again(self) -> None:
        if not self._busy:
            self.query_one("#update-log", RichLog).clear()
            self.check_update()

    @on(Button.Pressed, "#update-run")
    def run_update(self) -> None:
        self._start_update("safe")

    @on(Button.Pressed, "#update-patch")
    def patch_update(self) -> None:
        self._start_update("patch")

    @on(Button.Pressed, "#update-anyway")
    def force_update(self) -> None:
        self._start_update("force")

    def _start_update(self, strategy: str) -> None:
        if self.preflight is None or self._busy:
            return
        selected_packages = {str(value) for value in self.query_one("#update-packages", SelectionList).selected}
        self.app.stop_installation(str(self.installation_path))
        self._set_busy(True)
        labels = {"safe": "safe update", "patch": "protected patch", "force": "continue-anyway update"}
        self.append_notice(
            f"\n[bold cyan]Creating the pre-update snapshot and applying the {labels[strategy]}…[/bold cyan]"
        )
        self.perform_update(strategy, selected_packages)

    @work(thread=True, exclusive=True, group="update-run")
    def perform_update(self, strategy: str, selected_packages: set[str]) -> None:
        assert self.preflight is not None
        manager = ComfyUpdateManager(
            self.installation_path,
            runner=Runner(log=lambda line: self.app.call_from_thread(self.append_log, line)),
        )
        try:
            result = manager.update(
                self.preflight,
                strategy=strategy,
                selected_packages=selected_packages,
            )
        except Exception as exc:
            self.app.call_from_thread(self.update_failed_before_start, str(exc))
            return
        self.app.call_from_thread(self.update_finished, result)

    def update_failed_before_start(self, message: str) -> None:
        self._set_busy(False)
        self.query_one("#update-summary", Static).update(
            f"[bold red]Update was not started.[/bold red] {message}"
        )
        self.append_notice("\n[bold red]Update was not started; the installation is unchanged.[/bold red]")
        self.append_log(message)

    def update_finished(self, result: UpdateResult) -> None:
        self._set_busy(False)
        if result.success:
            self.query_one("#update-summary", Static).update(
                f"[bold green]Update complete.[/bold green] Official commit `{(result.current_commit or '')[:12]}` passed dependency and startup validation."
            )
            self.append_notice("\n[bold green]Update completed and all installed nodes passed startup validation.[/bold green]")
            self.preflight = None
            for selector in ("#update-run", "#update-patch", "#update-anyway"):
                self.query_one(selector, Button).disabled = True
            self.app.call_after_refresh(
                lambda: self.app.start_runtime_scan(
                    prefer_manage=True,
                    title="REFRESHING UPDATED INSTALLATION",
                    select_installation=self.installation_path,
                    completion_status=f"Updated installation verified: {self.installation_path}",
                )
            )
        else:
            message = result.error or "The updated installation did not pass validation."
            self.query_one("#update-summary", Static).update(
                f"[bold red]Update needs attention.[/bold red] {message}"
            )
            self.append_notice("\n[bold red]Update needs attention.[/bold red]")
            self.append_log(message)
            if result.rolled_back:
                self.append_notice(
                    "[bold green]The last working source and Python environment were restored automatically.[/bold green]"
                )
            elif result.snapshot:
                self.append_log(
                    f"Automatic rollback did not complete. Use Snapshots to restore `{result.snapshot.commit[:12]}` and its recorded Python environment."
                )
                if result.rollback_error:
                    self.append_log(f"Rollback error: {result.rollback_error}")
                self.preflight = None
                for selector in ("#update-run", "#update-patch", "#update-anyway"):
                    self.query_one(selector, Button).disabled = True
        self.query_one("#update-snapshots", Button).disabled = False

    @on(Button.Pressed, "#update-snapshots")
    def snapshots(self) -> None:
        if not self._busy:
            self.app.push_screen(SnapshotManagerScreen(self.installation_path))

    @on(Button.Pressed, "#update-separate")
    def install_separate(self) -> None:
        installation = self.app.installation_for(str(self.installation_path))
        if installation and installation.profile_id and self.app.profile_record(installation.profile_id):
            self.app.selected_profile_id = installation.profile_id
            self._open_separate_install(
                "A separate installation using the same profile is selected. Choose Continue to review it."
            )
            return
        self._set_busy(True)
        self.append_notice(
            "[bold cyan]Capturing this customized installation as an exact ABI-tagged profile before creating the new install…[/bold cyan]"
        )
        self.prepare_separate_profile()

    @work(thread=True, exclusive=True, group="update-separate")
    def prepare_separate_profile(self) -> None:
        try:
            profile, warnings = build_profile_from_installation(
                self.installation_path,
                name=f"{self.installation_path.name} Update-Safe Copy",
                exact_refs=True,
                include_top_level_packages=True,
                include_unpublished_plugins=True,
            )
            destination = user_profiles_dir() / f"{profile['id']}.comfyuisetup"
            write_profile_bundle(profile, destination)
        except Exception as exc:
            self.app.call_from_thread(self.separate_profile_failed, str(exc))
            return
        self.app.call_from_thread(self.separate_profile_ready, str(profile["id"]), warnings)

    def separate_profile_failed(self, message: str) -> None:
        self._set_busy(False)
        self.query_one("#update-summary", Static).update(
            "[bold red]A separate exact install could not be prepared automatically.[/bold red] "
            "The current installation is unchanged. Use Export Setup to resolve the reported profile issue."
        )
        self.append_log(message)
        self.app.notify("Exact profile capture failed; the current install was not changed.", severity="error")

    def separate_profile_ready(self, profile_id: str, warnings: list[str]) -> None:
        self.app.reload_profiles()
        self.app.selected_profile_id = profile_id
        if warnings:
            for warning in warnings:
                self.append_log(f"Profile warning: {warning}")
        self._open_separate_install(
            "An exact ABI-tagged copy profile is selected. Choose Continue to review the new destination."
        )

    def _open_separate_install(self, status_message: str) -> None:
        self.app.selected_installation = "__new__"
        while len(self.app.screen_stack) > 1:
            self.app.pop_screen()

        def switch_tab() -> None:
            home = self.app.screen
            if isinstance(home, HomeScreen):
                home.refresh_all()
                home.query_one("#main-tabs", TabbedContent).active = "setup-tab"
                home.query_one("#home-status", Static).update(status_message)

        self.app.call_after_refresh(switch_tab)

    @on(Button.Pressed, "#update-back")
    def back(self) -> None:
        if self._busy:
            return
        self.app.pop_screen()
        self.app.call_after_refresh(self._refresh_home)

    def _refresh_home(self) -> None:
        home = self.app.screen
        if isinstance(home, HomeScreen):
            home.refresh_all(prefer_manage=True)


class SnapshotManagerScreen(WizardScreen):
    """List and restore lightweight pre-update snapshots."""

    def __init__(self, installation_path: Path) -> None:
        super().__init__()
        self.installation_path = installation_path.expanduser().resolve()
        self.snapshots: list[SnapshotRecord] = []
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(classes="update-screen-shell", can_focus=True):
            yield from self.compose_header(
                "UPDATE SNAPSHOTS",
                "Restore the previous ComfyUI commit and exact Python package manifest without copying models, workflows, outputs, custom nodes, or the full virtual environment.",
            )
            with Container(classes="card compact-form snapshot-card"):
                yield Label("Snapshot")
                yield Select([("Loading…", "__none__")], value="__none__", id="snapshot-select", allow_blank=False)
                yield Static("Loading snapshots…", id="snapshot-detail", classes="builder-result")
            with Container(classes="card update-console-card"):
                yield Static("Rollback console", classes="card-title")
                yield RichLog(id="snapshot-log", wrap=False, markup=False, highlight=False, min_width=1, classes="embedded-terminal")
            with Horizontal(classes="button-row screen-actions update-actions"):
                yield Button("Back", id="snapshot-back")
                yield Button("Delete snapshot", id="snapshot-delete", classes="danger")
                yield Button("Roll back now", id="snapshot-rollback", classes="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.reload_snapshots()
        self.query_one("#snapshot-rollback", Button).tooltip = (
            "Stop this instance, restore the recorded source commit, synchronize the .venv to the saved package manifest, then run startup validation."
        )

    def append_log(self, line: str) -> None:
        _write_raw_terminal(self.query_one("#snapshot-log", RichLog), line)

    def append_notice(self, markup: str) -> None:
        _write_notice(self.query_one("#snapshot-log", RichLog), markup)

    def reload_snapshots(self) -> None:
        self.snapshots = ComfyUpdateManager(self.installation_path).list_snapshots()
        select = self.query_one("#snapshot-select", Select)
        options = [
            (
                f"{item.created_at[:19].replace('T', ' ')} — {item.commit[:12]} — {item.package_count} packages",
                item.snapshot_id,
            )
            for item in self.snapshots
        ]
        if not options:
            select.set_options([("No snapshots available", "__none__")])
            select.value = "__none__"
            select.disabled = True
            self.query_one("#snapshot-detail", Static).update("No pre-update snapshots exist for this installation.")
            self.query_one("#snapshot-delete", Button).disabled = True
            self.query_one("#snapshot-rollback", Button).disabled = True
            return
        select.set_options(options)
        select.disabled = False
        select.value = options[0][1]
        self.render_snapshot(options[0][1])

    def selected_snapshot(self) -> SnapshotRecord | None:
        value = self.query_one("#snapshot-select", Select).value
        if not isinstance(value, str):
            return None
        return next((item for item in self.snapshots if item.snapshot_id == value), None)

    def render_snapshot(self, snapshot_id: str) -> None:
        snapshot = next((item for item in self.snapshots if item.snapshot_id == snapshot_id), None)
        if snapshot is None:
            return
        warning_text = "\n".join(f"• {item}" for item in snapshot.warnings) or "No snapshot warnings."
        self.query_one("#snapshot-detail", Static).update(
            f"[b]Created:[/b] {snapshot.created_at}\n"
            f"[b]ComfyUI commit:[/b] `{snapshot.commit}`\n"
            f"[b]Branch:[/b] {snapshot.branch}\n"
            f"[b]Recorded environment:[/b] {snapshot.package_count} packages\n"
            f"[b]Custom nodes left in place:[/b] {snapshot.custom_node_count}\n\n"
            f"{warning_text}"
        )
        self.query_one("#snapshot-delete", Button).disabled = self._busy
        self.query_one("#snapshot-rollback", Button).disabled = self._busy or not snapshot.restorable

    @on(Select.Changed, "#snapshot-select")
    def snapshot_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.render_snapshot(event.value)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.query_one("#snapshot-back", Button).disabled = busy
        self.query_one("#snapshot-delete", Button).disabled = busy
        self.query_one("#snapshot-rollback", Button).disabled = busy
        self.query_one("#snapshot-select", Select).disabled = busy or not bool(self.snapshots)

    @on(Button.Pressed, "#snapshot-rollback")
    def rollback(self) -> None:
        snapshot = self.selected_snapshot()
        if snapshot is None or self._busy:
            return
        self.app.stop_installation(str(self.installation_path))
        self.set_busy(True)
        self.append_notice(f"[bold cyan]Restoring snapshot {snapshot.snapshot_id}…[/bold cyan]")
        self.append_log("Custom-node folders, models, workflows, and outputs remain in place.")
        self.perform_rollback(snapshot.snapshot_id)

    @work(thread=True, exclusive=True, group="rollback")
    def perform_rollback(self, snapshot_id: str) -> None:
        manager = ComfyUpdateManager(
            self.installation_path,
            runner=Runner(log=lambda line: self.app.call_from_thread(self.append_log, line)),
        )
        result = manager.rollback(snapshot_id)
        self.app.call_from_thread(self.rollback_finished, result)

    def rollback_finished(self, result: RollbackResult) -> None:
        self.set_busy(False)
        self.app.rescan_installations()
        if result.success:
            self.append_notice("\n[bold green]Rollback complete.[/bold green]")
            self.append_log(
                f"Restored {(result.current_commit or '')[:12]} and the recorded package environment."
            )
            self.app.notify("Rollback completed successfully.")
        else:
            self.append_notice("\n[bold red]Rollback needs attention.[/bold red]")
            self.append_log(result.error or "validation failed")
            self.app.notify("Rollback did not pass validation. Review the console.", severity="error")
        self.reload_snapshots()

    @on(Button.Pressed, "#snapshot-delete")
    def delete_snapshot(self) -> None:
        snapshot = self.selected_snapshot()
        if snapshot is None or self._busy:
            return
        try:
            ComfyUpdateManager(self.installation_path).delete_snapshot(snapshot.snapshot_id)
        except Exception as exc:
            self.app.notify(str(exc), severity="error")
            return
        self.append_log(f"Deleted snapshot {snapshot.snapshot_id}.")
        self.reload_snapshots()

    @on(Button.Pressed, "#snapshot-back")
    def back(self) -> None:
        if self._busy:
            return
        self.app.pop_screen()
        self.app.call_after_refresh(self._refresh_home)

    def _refresh_home(self) -> None:
        home = self.app.screen
        if isinstance(home, HomeScreen):
            home.refresh_all(prefer_manage=True)


class ThemeScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        options = self.app.theme_options
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "Appearance",
                "Choose a built-in theme or map an installed Vim/Neovim colorscheme to the manager UI.",
            )
            with Container(classes="card theme-card"):
                yield Label("Theme")
                yield Select(options, value=self.app.theme, id="theme-select", allow_blank=False)
                yield Static(
                    "Gruvbox is the default. Dark and Light are high-contrast built-ins. "
                    "Imported themes are saved in your user configuration directory.",
                    classes="help-text",
                )
                with Container(classes="theme-preview"):
                    yield Static("Primary", classes="preview-primary")
                    yield Static("Accent", classes="preview-accent")
                    yield Static("Success", classes="preview-success")
                    yield Static("Warning", classes="preview-warning")
                    yield Static("Error", classes="preview-error")
            with Container(classes="card theme-card"):
                yield Static("Import Vim / Neovim colors", classes="card-title")
                yield PathField(
                    "",
                    placeholder="colorscheme name or /path/to/colorscheme.vim",
                    input_id="vim-theme-source",
                    mode="file",
                    must_exist=True,
                    extensions=(".vim",),
                    browser_title="SELECT VIM COLORSCHEME",
                )
                yield Static(
                    "For a colorscheme name, the manager asks Neovim for resolved highlight colors when available, "
                    "then searches standard Vim/Neovim colors directories. A direct .vim path is also supported.",
                    classes="help-text",
                )
                with Horizontal(classes="button-row compact-buttons"):
                    yield Button("Import and apply", id="import-vim-theme", classes="primary")
                    yield Button("Remove imported", id="remove-theme")
                yield Static("Ready.", id="theme-status", classes="builder-result")
            with Horizontal(classes="button-row screen-actions"):
                yield Button("Back", id="back")
                yield Button("Use Gruvbox", id="reset-gruvbox")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#theme-select", Select).tooltip = "Apply a theme immediately and remember it for the next launch."
        self.query_one("#vim-theme-source", Input).tooltip = (
            "Examples: gruvbox, desert, ~/.vim/colors/solarized.vim, or C:\\Users\\you\\vimfiles\\colors\\theme.vim"
        )
        self._update_remove_state()

    def _update_remove_state(self) -> None:
        self.query_one("#remove-theme", Button).disabled = self.app.theme in BUILTIN_THEME_LABELS

    @on(Select.Changed, "#theme-select")
    def selected(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.app.apply_setup_theme(event.value)
            self._update_remove_state()
            self.query_one("#theme-status", Static).update(
                f"Applied [b]{self.app.theme_label}[/b]. The choice is saved for future launches."
            )

    @on(Button.Pressed, "#import-vim-theme")
    def import_theme(self) -> None:
        source = self.query_one("#vim-theme-source", Input).value.strip()
        try:
            theme = import_vim_theme(source)
            self.app.add_custom_theme(theme, apply=True)
            self.query_one("#theme-select", Select).set_options(self.app.theme_options)
            self.query_one("#theme-select", Select).value = theme.name
            self.query_one("#theme-status", Static).set_classes("builder-result success-box")
            self.query_one("#theme-status", Static).update(
                f"Imported and applied [b]{self.app.theme_label}[/b].\n"
                "The palette was mapped from Normal, Function, Type, Special, String, Warning, Error, and Visual groups."
            )
            self._update_remove_state()
        except ThemeImportError as exc:
            self.query_one("#theme-status", Static).set_classes("builder-result error-box")
            self.query_one("#theme-status", Static).update(f"[b]Theme import failed[/b]\n{exc}")

    @on(Button.Pressed, "#remove-theme")
    def remove_theme(self) -> None:
        current = self.app.theme
        if current in BUILTIN_THEME_LABELS:
            return
        self.app.remove_custom_theme(current)
        select = self.query_one("#theme-select", Select)
        select.set_options(self.app.theme_options)
        select.value = self.app.theme
        self.query_one("#theme-status", Static).update("Removed the imported theme and restored Gruvbox.")
        self._update_remove_state()

    @on(Button.Pressed, "#reset-gruvbox")
    def reset(self) -> None:
        self.app.apply_setup_theme("gruvbox")
        self.query_one("#theme-select", Select).value = "gruvbox"
        self.query_one("#theme-status", Static).update("Gruvbox restored as the active theme.")
        self._update_remove_state()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()


class ScanFolderScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "SCAN ANOTHER FOLDER",
                "Add any folder or drive root to the discovery scan. The path is used only for this session.",
            )
            with Container(classes="card"):
                yield Label("Folder to scan")
                yield PathField(
                    str(Path.cwd()), input_id="scan-root", mode="directory", must_exist=True,
                    browser_title="SELECT FOLDER TO SCAN",
                )
                yield Static("The scan is bounded and skips models, virtual environments, caches, and output folders.", classes="help-text")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Scan", id="scan", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#scan")
    def scan(self) -> None:
        path = Path(self.query_one("#scan-root", Input).value).expanduser()
        if not path.is_dir():
            self.app.notify("That folder does not exist.", severity="error")
            return
        self.app.extra_scan_roots.add(path.resolve())
        self.app.pop_screen()
        self.app.start_runtime_scan(prefer_manage=True, title="SCANNING SELECTED FOLDER")


class DestinationScreen(WizardScreen):
    def __init__(self, target: Path | None) -> None:
        super().__init__()
        self.target = target
        comfy = self.app.active_profile.get("comfyui", {}) if self.app else {}
        self.profile_repository = str(comfy.get("repository") or OFFICIAL_COMFYUI_REPOSITORY)
        self.profile_branch = str(comfy.get("branch") or "master")
        self.profile_preferred_commit = comfy.get("preferred_commit")

    def compose(self) -> ComposeResult:
        profile = self.app.active_profile
        default_target = self.target or (Path.cwd() / "ComfyUI")
        existing = is_comfyui_directory(default_target)
        default_mode = "existing" if existing else "official"
        profile_repo = self.profile_repository
        profile_label = f"Profile recommended — {profile_repo}"
        if len(profile_label) > 78:
            profile_label = "Profile recommended — …" + profile_repo[-55:]

        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "INSTALLATION TARGET",
                "Choose the destination and the exact ComfyUI repository policy to apply.",
            )
            with Container(classes="card"):
                yield Static(f"Profile: [b]{profile['name']}[/b]", classes="card-title")
                yield Label("ComfyUI directory")
                yield PathField(
                    str(default_target), input_id="target-dir", mode="directory", must_exist=False,
                    browser_title="SELECT COMFYUI DIRECTORY",
                )
                yield Static(
                    "An empty or missing directory receives a new clone. Existing checkouts are never silently replaced.",
                    classes="help-text",
                )

                yield Label("Repository source")
                yield Select(
                    [
                        ("Keep the existing checkout and its current origin", "existing"),
                        (f"Official ComfyUI — {OFFICIAL_COMFYUI_REPOSITORY}", "official"),
                        (profile_label, "profile"),
                        ("Custom GitHub repository", "custom"),
                    ],
                    value=default_mode,
                    id="repository-mode",
                    allow_blank=False,
                )
                yield Label("Custom repository URL")
                yield Input(self.profile_repository, id="custom-repository", disabled=default_mode != "custom")
                yield Label("Branch")
                yield Input(self.profile_branch, id="repository-branch", disabled=default_mode == "existing")
                yield Static(id="repository-preview", classes="builder-result")
                yield Static(
                    "A selected Official, Profile, or Custom repository is honored even for an existing checkout. "
                    "Before changing its origin or branch, the manager requires a clean Git tree and creates a local backup branch.",
                    classes="help-text",
                )
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Dependency options", id="next", classes="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#repository-mode", Select).tooltip = (
            "Official ComfyUI is always available. Choose Existing only when you explicitly want to keep the checkout's current origin."
        )
        self.query_one("#custom-repository", Input).tooltip = (
            "A public HTTPS GitHub repository, for example https://github.com/Comfy-Org/ComfyUI.git"
        )
        self._refresh_repository_preview()

    def _selected_repository(self) -> tuple[str | None, str]:
        mode = str(self.query_one("#repository-mode", Select).value)
        branch = self.query_one("#repository-branch", Input).value.strip() or "master"
        if mode == "existing":
            target_text = self.query_one("#target-dir", Input).value.strip()
            installation = self.app.installation_for(str(Path(target_text).expanduser().resolve())) if target_text else None
            repository = installation.repository if installation else None
            return repository, branch
        if mode == "official":
            return OFFICIAL_COMFYUI_REPOSITORY, branch
        if mode == "profile":
            return self.profile_repository, branch
        return self.query_one("#custom-repository", Input).value.strip() or None, branch

    def _refresh_repository_preview(self) -> None:
        mode = str(self.query_one("#repository-mode", Select).value)
        repository, branch = self._selected_repository()
        custom = self.query_one("#custom-repository", Input)
        branch_input = self.query_one("#repository-branch", Input)
        custom.disabled = mode != "custom"
        branch_input.disabled = mode == "existing"

        if mode == "existing":
            target_text = self.query_one("#target-dir", Input).value.strip()
            target = Path(target_text).expanduser() if target_text else Path()
            if target_text and is_comfyui_directory(target):
                installation = self.app.installation_for(str(target.resolve()))
                origin = installation.repository if installation else None
                detail = origin or "Existing checkout has no discoverable origin remote."
                self.query_one("#repository-preview", Static).update(
                    f"[b]Keep existing checkout[/b]\n{detail}"
                )
            else:
                self.query_one("#repository-preview", Static).update(
                    "[b]Existing checkout mode[/b]\nThe target must already be a valid ComfyUI checkout."
                )
            return

        self.query_one("#repository-preview", Static).update(
            f"[b]Repository that will be applied[/b]\n{repository or 'Enter a repository URL'}\nBranch: {branch}"
        )

    @on(Select.Changed, "#repository-mode")
    def repository_changed(self, event: Select.Changed) -> None:
        self._refresh_repository_preview()

    @on(Input.Changed, "#custom-repository")
    @on(Input.Changed, "#repository-branch")
    @on(Input.Changed, "#target-dir")
    def repository_input_changed(self, event: Input.Changed) -> None:
        self._refresh_repository_preview()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#next")
    def next_screen(self) -> None:
        target_text = self.query_one("#target-dir", Input).value.strip()
        if not target_text:
            self.app.notify("Choose a ComfyUI directory.", severity="error")
            return
        target = Path(target_text).expanduser()
        mode = str(self.query_one("#repository-mode", Select).value)
        repository, branch = self._selected_repository()

        if mode == "existing":
            if not is_comfyui_directory(target):
                self.app.notify(
                    "Existing-checkout mode requires a valid ComfyUI directory. Choose Official, Profile, or Custom for a new installation.",
                    severity="error",
                )
                return
            use_current_checkout = True
        else:
            if not repository:
                self.app.notify("Enter a custom repository URL.", severity="error")
                return
            use_current_checkout = False
            comfy = self.app.active_profile.setdefault("comfyui", {})
            comfy["repository"] = repository
            comfy["branch"] = branch
            if mode == "profile":
                comfy["preferred_commit"] = self.profile_preferred_commit
            else:
                comfy["preferred_commit"] = None

        self.app.repository_policy = {
            "mode": mode,
            "repository": repository,
            "branch": branch,
            "use_current_checkout": use_current_checkout,
        }
        self.app.pending_target = target
        self.app.push_screen(ConfigurationScreen())


class ConfigurationScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        profile = self.app.active_profile
        info = self.app.platform_info
        preferred = profile["python"]["preferred"]
        versions = [preferred, *profile["python"].get("fallbacks", [])]
        python_options = [(f"Python {value}", value) for value in dict.fromkeys(versions)]
        default_backup = Path.cwd() / "comfyui-wheel-backups"
        shared_paths = load_shared_asset_paths()
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "DEPENDENCY POLICY",
                "Choose how aggressively the manager should resolve operating-system packages and native builds.",
            )
            with Container(classes="card"):
                yield Label("Python runtime")
                yield Select(python_options, value=preferred, id="python-version", allow_blank=False)
                yield Label("Acceleration backend")
                yield Select(
                    [
                        (f"Auto-detect ({info.accelerator})", "auto"),
                        ("NVIDIA CUDA", "nvidia"),
                        ("AMD ROCm", "rocm"),
                        ("Apple Metal / MPS", "mps"),
                        ("CPU only", "cpu"),
                    ],
                    value="auto",
                    id="accelerator",
                    allow_blank=False,
                )
            with Container(classes="card"):
                yield Checkbox("Install missing operating-system packages automatically", value=True, id="auto-system")
                yield Checkbox("Build from source when no compatible wheel exists", value=True, id="source-builds")
                yield Checkbox("Pin custom nodes to the references stored in the profile", value=True, id="pin-refs")
                yield Checkbox("Update existing custom-node Git checkouts", value=False, id="update-nodes")
                yield Checkbox("Back up downloaded and locally built wheels", value=False, id="backup-builds")
                yield Label("Wheel backup directory")
                yield PathField(
                    str(default_backup), input_id="backup-dir", mode="directory", must_exist=False,
                    disabled=True, browser_title="SELECT WHEEL BACKUP DIRECTORY",
                )
                yield Static(
                    "Source builds use isolated build environments matched to the target Python, PyTorch, accelerator, and CUDA toolkit.",
                    classes="help-text",
                )
            with Container(classes="card"):
                yield Static("Shared external libraries", classes="card-title")
                yield Checkbox(
                    "Use one shared models library and one shared workflows library",
                    value=True,
                    id="configure-shared-assets",
                )
                yield Label("Shared models directory")
                yield PathField(
                    str(shared_paths.models), input_id="install-models-dir", mode="directory", must_exist=False,
                    browser_title="SELECT SHARED MODELS DIRECTORY",
                )
                yield Label("Shared workflows directory")
                yield PathField(
                    str(shared_paths.workflows), input_id="install-workflows-dir", mode="directory", must_exist=False,
                    browser_title="SELECT SHARED WORKFLOWS DIRECTORY",
                )
                yield Checkbox(
                    "Merge existing local workflows into the shared library before linking",
                    value=shared_paths.migrate_existing,
                    id="install-migrate-assets",
                )
                yield Static(
                    "The manager creates extra_model_paths.yaml and connects user/default/workflows for this instance. "
                    "One copy of each model, LoRA, and workflow can then serve every managed installation.",
                    classes="help-text",
                )
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Choose components", id="next", classes="primary")
        yield Footer()

    @on(Checkbox.Changed, "#backup-builds")
    def toggle_backup(self, event: Checkbox.Changed) -> None:
        self.query_one("#backup-dir", Input).disabled = not event.value
        self.query_one("#backup-dir-browse", Button).disabled = not event.value

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#next")
    def next_screen(self) -> None:
        accelerator = self.query_one("#accelerator", Select).value
        if accelerator == "auto":
            accelerator = self.app.platform_info.accelerator
        backup = self.query_one("#backup-builds", Checkbox).value
        self.app.configuration = {
            "target_dir": self.app.pending_target,
            "python_version": str(self.query_one("#python-version", Select).value),
            "accelerator": str(accelerator),
            "auto_install_system": self.query_one("#auto-system", Checkbox).value,
            "allow_source_builds": self.query_one("#source-builds", Checkbox).value,
            "pin_exact_refs": self.query_one("#pin-refs", Checkbox).value,
            "update_existing_nodes": self.query_one("#update-nodes", Checkbox).value,
            "install_command_alias": False,
            "backup_builds": backup,
            "backup_dir": Path(self.query_one("#backup-dir", Input).value).expanduser() if backup else None,
            "use_current_checkout": bool(self.app.repository_policy.get("use_current_checkout", False)),
            "repository_mode": self.app.repository_policy.get("mode", "official"),
            "repository": self.app.repository_policy.get("repository"),
            "repository_branch": self.app.repository_policy.get("branch", "master"),
            "configure_shared_assets": self.query_one("#configure-shared-assets", Checkbox).value,
            "shared_models_dir": Path(self.query_one("#install-models-dir", Input).value).expanduser(),
            "shared_workflows_dir": Path(self.query_one("#install-workflows-dir", Input).value).expanduser(),
            "migrate_existing_assets": self.query_one("#install-migrate-assets", Checkbox).value,
        }
        self.app.push_screen(ComponentsScreen())


class ComponentsScreen(WizardScreen):
    def _source_summary(self, item: dict[str, Any]) -> str:
        source = item.get("source", {})
        manager_id = source.get("manager_id")
        repository = source.get("repository")
        ref = source.get("ref")
        if manager_id and repository:
            return f"Manager: {manager_id} · Git fallback: {repository} · Ref: {ref or 'default'}"
        if manager_id:
            return f"Manager: {manager_id}"
        if repository:
            return f"Git: {repository} · Ref: {ref or 'default'}"
        if source.get("type") == "snapshot":
            return "Exact sanitized source snapshot from the working installation"
        if source.get("type") == "embedded":
            return "Embedded unpublished local plugin"
        return "Source information was not provided by the profile."

    def _component_row(
        self,
        *,
        item: dict[str, Any],
        prefix: str,
        supported: bool,
        unavailable: str | None = None,
    ) -> ComposeResult:
        classes = "component component-supported" if supported else "component component-disabled"
        with Horizontal(classes=classes):
            yield Switch(
                value=bool(item.get("selected", True) and supported),
                id=f"{prefix}--{item['id']}",
                disabled=not supported,
                classes="component-toggle",
            )
            with Container(classes="component-copy"):
                yield Static(str(item.get("name") or item["id"]), classes="component-name")
                description = str(item.get("description") or "No description supplied by the profile.")
                yield Static(description, classes="help-text component-description")
                if prefix == "node":
                    yield Static(self._source_summary(item), classes="component-source")
                if unavailable:
                    yield Static(unavailable, classes="status-warn")

    def compose(self) -> ComposeResult:
        info = self.app.platform_info
        accelerator = self.app.configuration["accelerator"]
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "PROFILE COMPONENTS",
                "Choose exactly which nodes and acceleration packages to install. Every row includes its source and compatibility status.",
            )
            with Container(classes="card"):
                yield Static("Custom nodes", classes="card-title")
                nodes = self.app.active_profile.get("nodes", [])
                if not nodes:
                    yield Static("This profile contains no third-party custom nodes.", classes="help-text")
                for node in nodes:
                    supported = info.os_name in node.get("platforms", []) and accelerator in node.get("accelerators", [])
                    unavailable = None if supported else f"Unavailable on {info.os_name}/{accelerator}."
                    yield from self._component_row(
                        item=node,
                        prefix="node",
                        supported=supported,
                        unavailable=unavailable,
                    )
            with Container(classes="card"):
                yield Static("Compiled and accelerated packages", classes="card-title")
                accelerated = self.app.active_profile.get("accelerated_packages", [])
                if not accelerated:
                    yield Static("This profile contains no separately managed acceleration packages.", classes="help-text")
                for item in accelerated:
                    supported = info.os_name in item.get("platforms", []) and accelerator in item.get("accelerators", [])
                    minimum = item.get("minimum_compute_capability")
                    if minimum and (not info.compute_capability or info.compute_capability < float(minimum)):
                        supported = False
                    unavailable = None if supported else f"Unavailable on {info.os_name}/{accelerator} or this GPU capability."
                    yield from self._component_row(
                        item=item,
                        prefix="accel",
                        supported=supported,
                        unavailable=unavailable,
                    )
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Review", id="next", classes="primary")
        yield Footer()

    def on_mount(self) -> None:
        for node in self.app.active_profile.get("nodes", []):
            self.query_one(f"#node--{node['id']}", Switch).tooltip = self._source_summary(node)

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#next")
    def next_screen(self) -> None:
        selected_nodes = {
            node["id"] for node in self.app.active_profile.get("nodes", [])
            if self.query_one(f"#node--{node['id']}", Switch).value
        }
        selected_acceleration = {
            item["id"] for item in self.app.active_profile.get("accelerated_packages", [])
            if self.query_one(f"#accel--{item['id']}", Switch).value
        }
        config = self.app.configuration
        self.app.options = InstallOptions(
            target_dir=config["target_dir"],
            python_version=config["python_version"],
            accelerator=config["accelerator"],
            selected_nodes=selected_nodes,
            selected_acceleration=selected_acceleration,
            auto_install_system=config["auto_install_system"],
            allow_source_builds=config["allow_source_builds"],
            backup_builds=config["backup_builds"],
            backup_dir=config["backup_dir"],
            pin_exact_refs=config["pin_exact_refs"],
            use_current_checkout=config["use_current_checkout"],
            update_existing_nodes=config["update_existing_nodes"],
            install_command_alias=config["install_command_alias"],
            configure_shared_assets=config["configure_shared_assets"],
            shared_models_dir=config["shared_models_dir"],
            shared_workflows_dir=config["shared_workflows_dir"],
            migrate_existing_assets=config["migrate_existing_assets"],
        )
        self.app.push_screen(ReviewScreen())


class ReviewScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="wizard"):
            yield from self.compose_header("REVIEW INSTALLATION", "Nothing changes until you start the installation.")
            with ScrollableContainer(id="review-scroll", can_focus=True):
                yield Markdown(id="review-markdown")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Start installation", id="install", classes="success")
        yield Footer()

    def on_mount(self) -> None:
        engine = InstallerEngine(self.app.active_profile, self.app.platform_info, self.app.options)
        plan = "\n".join(
            f"{index}. **{step.title}** — {step.detail}"
            for index, step in enumerate(engine.plan(), 1)
        )
        self.query_one("#review-markdown", Markdown).update(
            f"## Profile\n\n**{self.app.active_profile['name']}**\n\n"
            f"## Target\n\n`{self.app.options.target_dir}`\n\n"
            f"## Repository policy\n\n"
            f"- Mode: **{self.app.configuration.get('repository_mode', 'official')}**\n"
            f"- Repository: `{self.app.configuration.get('repository') or 'existing checkout origin'}`\n"
            f"- Branch: `{self.app.configuration.get('repository_branch', 'existing')}`\n\n"
            f"## Environment\n\n"
            f"- Accelerator: **{self.app.options.accelerator}**\n"
            f"- Python: **{self.app.options.python_version}**\n"
            f"- Source-build fallback: **{'enabled' if self.app.options.allow_source_builds else 'disabled'}**\n"
            f"- Wheel backup: **{self.app.options.backup_dir if self.app.options.backup_builds else 'disabled'}**\n"
            f"- Launcher: **local to the installation directory**\n"
            f"- Shared models: **{self.app.options.shared_models_dir if self.app.options.configure_shared_assets else 'disabled'}**\n"
            f"- Shared workflows: **{self.app.options.shared_workflows_dir if self.app.options.configure_shared_assets else 'disabled'}**\n\n"
            f"## Plan\n\n{plan}"
        )

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#install")
    def install(self) -> None:
        self.app.push_screen(InstallationScreen())


class InstallationScreen(WizardScreen):
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self._secret_event: threading.Event | None = None
        self._secret_result: dict[str, str | None] | None = None
        self._wheel_event: threading.Event | None = None
        self._wheel_result: dict[str, str] | None = None
        self._wheel_item: dict[str, Any] | None = None
        self._wheel_source_path: Path | None = None
        self._installing = False

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="installation-layout", can_focus=True):
            yield from self.compose_header(
                "INSTALLATION CONSOLE",
                "Every command, package-manager message, build log, password request, and recovery command stays inside this screen.",
            )
            yield Static("Preparing…", id="install-status")
            yield ProgressBar(total=100, id="install-progress")
            yield RichLog(id="install-log", highlight=False, markup=False, wrap=False, min_width=1, classes="embedded-terminal")
            with Container(id="secret-panel", classes="hidden prompt-panel"):
                yield Static("Administrator authentication required", id="secret-label", classes="card-title")
                with Horizontal(classes="console-input-row"):
                    yield Input(placeholder="Password", password=True, id="secret-input")
                    yield Button("Authorize", id="secret-submit", classes="primary")
                    yield Button("Cancel", id="secret-cancel")
            with Container(id="wheel-source-panel", classes="hidden prompt-panel"):
                yield Static("No compatible precompiled wheel was found", id="wheel-source-title", classes="card-title")
                yield Static(id="wheel-source-message", classes="help-text")
                yield Static(id="wheel-source-file", classes="path-text")
                with Horizontal(classes="wheel-source-fields"):
                    yield Select(
                        [
                            ("Custom/Local", "Custom/Local"),
                            ("3rd Party", "3rd Party"),
                            ("Official", "Official"),
                        ],
                        value="Custom/Local",
                        allow_blank=False,
                        id="wheel-source-label",
                        tooltip="Trust label written to the editable wheel-sources.yaml registry.",
                    )
                    yield PathField(
                        "",
                        placeholder="HTTPS source or local .whl file/directory",
                        input_id="wheel-source-input",
                        mode="any",
                        must_exist=True,
                        browser_title="SELECT LOCAL WHEEL OR WHEEL DIRECTORY",
                    )
                with Horizontal(classes="button-row wheel-source-actions"):
                    yield Button("Add source & retry", id="wheel-source-add", classes="primary")
                    yield Button("Open source file", id="wheel-source-edit")
                    yield Button("Retry sources", id="wheel-source-retry")
                    yield Button("Compile from source", id="wheel-source-build", classes="warning")
                    yield Button("Cancel installation", id="wheel-source-cancel", classes="danger")
            with Horizontal(id="shell-row", classes="console-input-row"):
                yield Static("$", classes="shell-prompt")
                yield Input(
                    placeholder="Recovery shell command (available after completion or an error)",
                    id="shell-command",
                    disabled=True,
                )
                yield Button("Run", id="shell-run", disabled=True)
                if os.name != "nt":
                    yield Button("Authorize sudo", id="shell-sudo", disabled=True)
            with Horizontal(id="install-actions", classes="button-row hidden"):
                yield Button("Retry installation", id="retry", classes="warning")
                yield Button("Return home", id="home")
                yield Button("Quit", id="quit", classes="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.append_notice(
            "[b]Embedded installation console ready.[/b] No command will suspend or leave the Textual interface."
        )
        self.start_installation()

    def on_unmount(self) -> None:
        """Release worker prompts so closing the screen cannot strand a thread."""
        if self._secret_result is not None:
            self._secret_result["value"] = None
        if self._secret_event is not None:
            self._secret_event.set()
        if self._wheel_result is not None:
            self._wheel_result["value"] = "cancel"
        if self._wheel_event is not None:
            self._wheel_event.set()

    def append_log(self, line: str) -> None:
        _write_raw_terminal(self.query_one("#install-log", RichLog), line)

    def append_notice(self, markup: str) -> None:
        _write_notice(self.query_one("#install-log", RichLog), markup)

    def update_progress(self, current: int, total: int, title: str) -> None:
        progress = self.query_one("#install-progress", ProgressBar)
        progress.update(total=total, progress=current)
        self.query_one("#install-status", Static).update(f"{current}/{total} • {title}")

    def request_secret(self, prompt: str) -> str | None:
        event = threading.Event()
        result: dict[str, str | None] = {"value": None}
        self.app.call_from_thread(self._show_secret_prompt, prompt, event, result)
        event.wait()
        return result["value"]

    def _show_secret_prompt(
        self,
        prompt: str,
        event: threading.Event,
        result: dict[str, str | None],
    ) -> None:
        self._secret_event = event
        self._secret_result = result
        panel = self.query_one("#secret-panel", Container)
        panel.remove_class("hidden")
        self.query_one("#secret-label", Static).update(prompt)
        password = self.query_one("#secret-input", Input)
        password.value = ""
        password.focus()
        self.append_notice("[yellow]Administrator authentication requested inside the manager.[/yellow]")

    def _finish_secret(self, value: str | None) -> None:
        if self._secret_result is not None:
            self._secret_result["value"] = value
        if self._secret_event is not None:
            self._secret_event.set()
        self._secret_event = None
        self._secret_result = None
        self.query_one("#secret-input", Input).value = ""
        self.query_one("#secret-panel", Container).add_class("hidden")

    @on(Button.Pressed, "#secret-submit")
    def submit_secret(self) -> None:
        self._finish_secret(self.query_one("#secret-input", Input).value)

    @on(Input.Submitted, "#secret-input")
    def submit_secret_input(self) -> None:
        self.submit_secret()

    @on(Button.Pressed, "#secret-cancel")
    def cancel_secret(self) -> None:
        self._finish_secret(None)

    def request_wheel_decision(self, item: dict[str, Any], message: str, source_path: Path) -> str:
        event = threading.Event()
        result = {"value": "cancel"}
        self.app.call_from_thread(
            self._show_wheel_source_prompt, item, message, source_path, event, result
        )
        event.wait()
        return result["value"]

    def _show_wheel_source_prompt(
        self,
        item: dict[str, Any],
        message: str,
        source_path: Path,
        event: threading.Event,
        result: dict[str, str],
    ) -> None:
        self._wheel_event = event
        self._wheel_result = result
        self._wheel_item = item
        self._wheel_source_path = source_path
        panel = self.query_one("#wheel-source-panel", Container)
        panel.remove_class("hidden")
        self.query_one("#wheel-source-title", Static).update(
            f"No compatible precompiled wheel: {item.get('name', item.get('id', 'package'))}"
        )
        self.query_one("#wheel-source-message", Static).update(
            message + "\nChoose a new source, edit the source list, compile in an isolated development environment, or cancel."
        )
        self.query_one("#wheel-source-file", Static).update(f"Wheel source YAML: {source_path}")
        field = self.query_one("#wheel-source-input", Input)
        field.value = ""
        self.query_one("#wheel-source-label", Select).value = "Custom/Local"
        build_button = self.query_one("#wheel-source-build", Button)
        build_button.disabled = not self.app.options.allow_source_builds
        build_button.tooltip = (
            "Compile in an isolated development environment."
            if self.app.options.allow_source_builds
            else "Source builds were disabled in the dependency policy step."
        )
        field.focus()
        self.append_notice("[yellow]Precompiled-wheel resolution needs a decision.[/yellow]")

    def _finish_wheel_decision(self, value: str) -> None:
        if self._wheel_result is not None:
            self._wheel_result["value"] = value
        if self._wheel_event is not None:
            self._wheel_event.set()
        self._wheel_event = None
        self._wheel_result = None
        self._wheel_item = None
        self.query_one("#wheel-source-panel", Container).add_class("hidden")

    @on(Button.Pressed, "#wheel-source-add")
    def add_wheel_source(self) -> None:
        value = self.query_one("#wheel-source-input", Input).value.strip()
        if not value or self._wheel_item is None or self._wheel_source_path is None:
            self.append_notice("[red]Enter a GitHub/Hugging Face URL, wheel URL, wheel page, or local directory.[/red]")
            return
        try:
            registry = WheelSourceRegistry(self._wheel_source_path)
            selected_label = self.query_one("#wheel-source-label", Select).value
            label = str(selected_label) if selected_label is not Select.BLANK else "Custom/Local"
            source = registry.add_source(
                package_id=self._wheel_item["id"],
                location=value,
                platform_info=self.app.platform_info,
                label=label,
            )
        except Exception as exc:
            self.append_notice("[red]Could not add wheel source.[/red]")
            self.append_log(str(exc))
            return
        self.append_log(f"Added {source.label} wheel source: {source.location}")
        self._finish_wheel_decision("retry")

    @on(Button.Pressed, "#wheel-source-edit")
    def edit_wheel_source_file(self) -> None:
        if self._wheel_source_path is None:
            return
        try:
            open_source_file(self._wheel_source_path)
            self.append_log(f"Opened wheel source file: {self._wheel_source_path}")
            self.append_log("Save the file in your editor, return here, then choose Retry sources.")
        except Exception as exc:
            self.append_log(f"Could not open the system editor: {exc}")
            self.append_log(f"Edit this file manually: {self._wheel_source_path}")

    @on(Button.Pressed, "#wheel-source-retry")
    def retry_wheel_sources(self) -> None:
        self._finish_wheel_decision("retry")

    @on(Button.Pressed, "#wheel-source-build")
    def build_wheel_from_source(self) -> None:
        self._finish_wheel_decision("build")

    @on(Button.Pressed, "#wheel-source-cancel")
    def cancel_wheel_install(self) -> None:
        self._finish_wheel_decision("cancel")

    def _set_console_enabled(self, enabled: bool) -> None:
        self.query_one("#shell-command", Input).disabled = not enabled
        self.query_one("#shell-run", Button).disabled = not enabled
        if os.name != "nt":
            self.query_one("#shell-sudo", Button).disabled = not enabled

    def start_installation(self) -> None:
        if self._installing:
            return
        self._installing = True
        self.query_one("#install-actions", Horizontal).add_class("hidden")
        self._set_console_enabled(False)
        self.append_notice("\n[bold cyan]Starting or resuming installation…[/bold cyan]")
        self.run_installation()

    @work(thread=True, exclusive=True, group="installation")
    def run_installation(self) -> None:
        try:
            engine = InstallerEngine(
                self.app.active_profile,
                self.app.platform_info,
                self.app.options,
                log=lambda line: self.app.call_from_thread(self.append_log, line),
                progress=lambda current, total, title: self.app.call_from_thread(
                    self.update_progress, current, total, title
                ),
                secret_provider=self.request_secret,
                wheel_decision_provider=self.request_wheel_decision,
            )
            result = engine.install()
        except Exception as exc:
            # Engine construction, callbacks, and unexpected integration errors
            # must still return control to the TUI with recovery actions enabled.
            result = InstallResult(
                success=False,
                target_dir=self.app.options.target_dir.expanduser().resolve(),
                failed_step="installer-worker",
                error=f"Unexpected installer worker error: {exc}",
            )
        self.app.call_from_thread(self.finish, result)

    def finish(self, result: InstallResult) -> None:
        self._installing = False
        self.app.install_result = result
        self._set_console_enabled(True)
        actions = self.query_one("#install-actions", Horizontal)
        actions.remove_class("hidden")
        retry = self.query_one("#retry", Button)
        if result.success:
            retry.disabled = True
            self.query_one("#install-status", Static).update("Complete • ComfyUI is ready")
            self.query_one("#install-progress", ProgressBar).update(progress=100, total=100)
            self.append_notice("\n[bold green]Installation completed successfully.[/bold green]")
            self.append_log(f"ComfyUI directory: {result.target_dir}")
            if result.launcher_path:
                self.append_log(f"Local launcher created at: {result.launcher_path}")
            self.append_log(f"Launch from the manager or run {result.target_dir / 'comfyui'}.")
        else:
            retry.disabled = False
            self.query_one("#install-status", Static).update(
                f"Needs attention • failed step: {result.failed_step or 'unknown'}"
            )
            self.append_notice("\n[bold red]Installation paused at the failing step.[/bold red]")
            self.append_log(result.error or "Unknown installation error")
            self.append_log(
                "Use the recovery shell below to repair the system, then choose Retry installation. "
                "Completed work will be reused."
            )
        self.query_one("#shell-command", Input).focus()

    def _shell_cwd(self) -> Path:
        target = self.app.options.target_dir.expanduser().resolve()
        if target.exists() and target.is_dir():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.parent

    def _shell_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        target = self.app.options.target_dir.expanduser().resolve()
        venv = target / ".venv"
        if os.name == "nt":
            scripts = venv / "Scripts"
        else:
            scripts = venv / "bin"
        if scripts.exists():
            env["VIRTUAL_ENV"] = str(venv)
            env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
        return env

    def _launch_shell_command(self) -> None:
        command = self.query_one("#shell-command", Input).value.strip()
        if not command:
            return
        self.query_one("#shell-command", Input).value = ""
        self.run_shell_command(command)

    @on(Button.Pressed, "#shell-run")
    def shell_run(self) -> None:
        self._launch_shell_command()

    @on(Input.Submitted, "#shell-command")
    def shell_submit(self) -> None:
        self._launch_shell_command()

    @on(Button.Pressed, "#shell-sudo")
    def shell_sudo(self) -> None:
        self.run_shell_command("sudo -v")

    @work(thread=True, exclusive=True, group="recovery-shell")
    def run_shell_command(self, text: str) -> None:
        self.app.call_from_thread(self._set_console_enabled, False)
        runner = Runner(
            log=lambda line: self.app.call_from_thread(self.append_log, line),
            secret_provider=self.request_secret,
        )
        try:
            if os.name == "nt":
                command = ["powershell", "-NoProfile", "-Command", text]
            else:
                shell = os.environ.get("SHELL") or "/bin/sh"
                stripped = text.lstrip()
                if stripped == "sudo" or stripped.startswith("sudo "):
                    privileged = stripped[4:].lstrip() or "-v"
                    command = ["sudo", shell, "-lc", privileged]
                else:
                    command = [shell, "-lc", text]
            runner.run(command, cwd=self._shell_cwd(), env=self._shell_environment())
        except Exception as exc:
            self.app.call_from_thread(self.append_notice, "[bold red]Shell command failed.[/bold red]")
            self.app.call_from_thread(self.append_log, str(exc))
        finally:
            self.app.call_from_thread(self._set_console_enabled, True)
            self.app.call_from_thread(self._focus_shell_input)

    def _focus_shell_input(self) -> None:
        self.query_one("#shell-command", Input).focus()

    @on(Button.Pressed, "#retry")
    def retry(self) -> None:
        self.start_installation()

    @on(Button.Pressed, "#home")
    def home(self) -> None:
        target = self.app.options.target_dir.expanduser().resolve()
        self.app.return_home_after_installation(target, success=self.app.install_result.success)

    @on(Button.Pressed, "#quit")
    def quit_app(self) -> None:
        self.app.exit()


class CompleteScreen(WizardScreen):
    """Compatibility screen retained for older saved UI state.

    New installations complete in :class:`InstallationScreen` so the embedded
    console and recovery shell remain available.
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="card"):
            yield Static("This installation result belongs to an older session. Return home and reopen it.")
            yield Button("Return home", id="home")
        yield Footer()

    @on(Button.Pressed, "#home")
    def home(self) -> None:
        target = self.app.install_result.target_dir.expanduser().resolve()
        self.app.return_home_after_installation(target, success=self.app.install_result.success)


class ImportProfileScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "IMPORT PORTABLE SETUP",
                "Import a .comfyuisetup profile, a profile JSON file, or convert a sanitized inventory archive.",
            )
            with Container(classes="card"):
                yield Label("Profile or inventory file")
                yield PathField(
                    "", input_id="import-path", mode="file", must_exist=True,
                    extensions=(".comfyuisetup", ".comfysetup", ".yaml", ".yml", ".json", ".tgz", ".tar.gz"),
                    browser_root=profiles_directory(), browser_title="SELECT SETUP OR INVENTORY FILE",
                )
                yield Label("Name when converting an inventory")
                yield Input("My Custom ComfyUI", id="inventory-name")
                yield Label("ComfyUI repository when converting an inventory")
                yield Input(OFFICIAL_COMFYUI_REPOSITORY, id="inventory-repository")
                yield Static(
                    "The name and repository fields are used only for .tgz/.tar.gz inventory archives. Portable profiles already contain them.",
                    classes="help-text",
                )
            yield Static("Ready.", id="import-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Import", id="import", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#import")
    def import_action(self) -> None:
        path = Path(self.query_one("#import-path", Input).value).expanduser()
        if not path.is_file():
            self.app.notify("Choose an existing file.", severity="error")
            return
        try:
            if path.name.lower().endswith((".tgz", ".tar.gz")):
                inventory = load_inventory(path)
                name = self.query_one("#inventory-name", Input).value.strip()
                repository = self.query_one("#inventory-repository", Input).value.strip()
                if not name or not repository:
                    raise ValueError("Inventory conversion needs a profile name and ComfyUI repository.")
                profile_id = "-".join(filter(None, __import__("re").split(r"[^a-z0-9]+", name.lower()))) or "custom-comfyui"
                profile = profile_from_inventory(inventory, profile_name=name, profile_id=profile_id, repository=repository)
                destination = user_profiles_dir() / f"{profile_id}.comfyuisetup"
                write_profile_bundle(profile, destination)
                record = import_profile(destination)
                message = f"Inventory converted and imported: {name}"
            else:
                record = import_profile(path)
                message = f"Imported profile: {record.name}"
            self.app.refresh_profile_views(record.id)
            self.query_one("#import-status", Static).update(
                f"[b]{message}[/b]\nSelected immediately in Setup & Install.\nProfile library: {user_profiles_dir()}"
            )
            self.query_one("#import-status", Static).set_classes("builder-result success-box")
        except Exception as exc:
            self.query_one("#import-status", Static).update(f"[b]Import failed[/b]\n{exc}")
            self.query_one("#import-status", Static).set_classes("builder-result error-box")


class DuplicateNodeChoiceScreen(ModalScreen[Path | None]):
    """Ask which duplicate source tree should remain in a portable profile."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, group: DuplicateNodeGroup) -> None:
        super().__init__()
        self.group = group

    def compose(self) -> ComposeResult:
        options = [(Text(str(path)), str(path)) for path in self.group.paths]
        with Container(id="duplicate-node-dialog"):
            yield Static("DUPLICATE CUSTOM NODE", classes="card-title")
            yield Static(
                f"The destination identity [b]{self.group.identity}[/b] exists in more than one configured "
                "custom-node root. Select the copy to include. Every other complete path shown here will be "
                "explicitly omitted from the exported profile.",
                classes="help-text",
            )
            yield Select(
                options,
                value=str(self.group.paths[0]),
                allow_blank=False,
                id="duplicate-node-keep",
            )
            yield Static(
                "\n".join(f"• {path}" for path in self.group.paths),
                id="duplicate-node-paths",
                classes="builder-result",
                markup=False,
            )
            with Horizontal(classes="button-row"):
                yield Button("Cancel export", id="duplicate-node-cancel")
                yield Button("Keep selected; omit others", id="duplicate-node-confirm", classes="primary")

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#duplicate-node-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#duplicate-node-confirm")
    def confirm(self) -> None:
        value = self.query_one("#duplicate-node-keep", Select).value
        if value is Select.BLANK:
            return
        self.dismiss(Path(str(value)).expanduser().resolve(strict=False))


class ExportSetupScreen(WizardScreen):
    def __init__(self) -> None:
        super().__init__()
        self._pending_export: tuple[Path, Path, str, str, bool, bool, bool] | None = None
        self._pending_duplicate_groups: list[DuplicateNodeGroup] = []
        self._omitted_node_paths: set[Path] = set()

    def compose(self) -> ComposeResult:
        default_install = self.app.installations[0].path if self.app.installations else Path.cwd()
        default_output = profiles_directory() / "my-comfyui-setup.comfyuisetup"
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "EXPORT WORKING SETUP",
                "Create a portable profile from a working ComfyUI installation. Models, outputs, credentials, and machine paths are excluded.",
            )
            with Container(classes="card"):
                yield Label("ComfyUI installation directory")
                yield PathField(
                    str(default_install), input_id="export-comfy-dir", mode="directory", must_exist=True,
                    browser_title="SELECT COMFYUI INSTALLATION",
                )
                yield Label("Profile name")
                yield Input("My Custom ComfyUI", id="export-name")
                yield Label("Publisher or author (optional)")
                yield Input("", id="export-publisher")
                yield Label("Output .comfyuisetup file")
                yield PathField(
                    str(default_output), input_id="export-output", mode="file", must_exist=False,
                    extensions=(".comfyuisetup",), browser_root=profiles_directory(),
                    browser_title="CHOOSE SETUP EXPORT FILE",
                )
                yield Checkbox(
                    "Create a reconstruction-first profile from repository references, node identities, and the verified Python environment",
                    value=True,
                    id="export-exact",
                )
                yield Checkbox(
                    "Capture top-level Python packages for non-exact compatibility exports",
                    value=True,
                    id="export-packages",
                )
                yield Checkbox(
                    "Embed unpublished local plugins only when they have no Git repository or Registry/Manager id",
                    value=True,
                    id="export-local-plugins",
                )
                yield Static(
                    "Exact export records every installed Python distribution and copies dependency manifests. Public custom nodes are listed only in custom_nodes.yml and reinstalled from Registry/Manager IDs or validated repositories. Only genuinely unresolved plugins may be sanitized and embedded; virtual environments, installed libraries, public node trees, and local wheels are never archived by new exports.",
                    classes="help-text",
                )
            yield Static("Ready to inspect the selected installation.", id="export-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Export portable profile", id="export", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#export")
    def export_action(self) -> None:
        comfy = Path(self.query_one("#export-comfy-dir", Input).value).expanduser()
        name = self.query_one("#export-name", Input).value.strip()
        publisher = self.query_one("#export-publisher", Input).value.strip()
        output = Path(self.query_one("#export-output", Input).value).expanduser()
        if not name or not is_comfyui_directory(comfy):
            self.app.notify("Choose a valid ComfyUI installation and profile name.", severity="error")
            return
        self.query_one("#export", Button).disabled = True
        self._pending_export = (
            comfy,
            output,
            name,
            publisher,
            self.query_one("#export-exact", Checkbox).value,
            self.query_one("#export-packages", Checkbox).value,
            self.query_one("#export-local-plugins", Checkbox).value,
        )
        self._omitted_node_paths = set()
        self._pending_duplicate_groups = duplicate_custom_nodes(comfy)
        if self._pending_duplicate_groups:
            self.query_one("#export-status", Static).update(
                "Duplicate custom-node identities were found. Choose which complete source path to retain for each one."
            )
            self._prompt_next_duplicate()
            return
        self._start_export()

    def _prompt_next_duplicate(self) -> None:
        if not self._pending_duplicate_groups:
            self._start_export()
            return
        group = self._pending_duplicate_groups[0]
        self.app.push_screen(
            DuplicateNodeChoiceScreen(group),
            lambda kept: self._duplicate_choice_done(group, kept),
        )

    def _duplicate_choice_done(self, group: DuplicateNodeGroup, kept: Path | None) -> None:
        if kept is None:
            self._pending_export = None
            self._pending_duplicate_groups = []
            self._omitted_node_paths = set()
            self.query_one("#export-status", Static).update("Export cancelled. No custom node was omitted.")
            self.query_one("#export", Button).disabled = False
            return
        for path in group.paths:
            if path.resolve(strict=False) != kept.resolve(strict=False):
                self._omitted_node_paths.add(path.resolve(strict=False))
        self._pending_duplicate_groups.pop(0)
        self._prompt_next_duplicate()

    def _start_export(self) -> None:
        if self._pending_export is None:
            self.query_one("#export", Button).disabled = False
            return
        self.query_one("#export-status", Static).update("Inspecting repositories, packages, and custom-node metadata…")
        self.do_export(*self._pending_export, omitted_node_paths=set(self._omitted_node_paths))

    @work(thread=True, exclusive=True)
    def do_export(
        self,
        comfy: Path,
        output: Path,
        name: str,
        publisher: str,
        exact: bool,
        packages: bool,
        local_plugins: bool,
        omitted_node_paths: set[Path],
    ) -> None:
        try:
            path, warnings = export_setup(
                comfy, output, name=name, publisher=publisher,
                exact_refs=exact,
                include_top_level_packages=packages,
                include_unpublished_plugins=local_plugins,
                omitted_node_paths=omitted_node_paths,
            )
            record = import_profile(path)
            message = (
                f"[b]Portable profile created and added to the profile library.[/b]\n{path}\n"
                "It is selected immediately in Setup & Install."
            )
            if warnings:
                message += "\n\nWarnings:\n" + "\n".join(f"• {warning}" for warning in warnings)
            self.app.call_from_thread(self.export_done, message, False, record.id)
        except Exception as exc:
            self.app.call_from_thread(self.export_done, f"[b]Export failed.[/b]\n{exc}", True, None)

    def export_done(self, message: str, failed: bool, profile_id: str | None) -> None:
        self._pending_export = None
        self._pending_duplicate_groups = []
        self._omitted_node_paths = set()
        status = self.query_one("#export-status", Static)
        status.update(message)
        status.set_classes("builder-result error-box" if failed else "builder-result success-box")
        if not failed and profile_id:
            self.app.refresh_profile_views(profile_id)
        self.query_one("#export", Button).disabled = False


class WorkflowHubScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        selected = self.app.selected_workflow_installation()
        libraries = discover_workflow_libraries(selected.path) if selected else []
        workflow_count = len(discover_workflow_files(selected.path)) if selected else 0
        yield Header()
        with Container(id="wizard"):
            yield from self.compose_header(
                "NATIVE COMFYUI WORKFLOWS",
                "Use ComfyUI's normal workflow JSON files directly, or share a directory-preserving workflow archive.",
            )
            with Container(classes="card workflow-actions-card"):
                yield Static("Workflow actions", classes="card-title")
                yield Static(
                    "Single workflows remain ordinary .json files. A .comfyworkflows archive preserves a whole folder tree, README, previews, metadata, and an optional setup profile or setup reference.",
                    classes="help-text",
                )
                with Container(classes="workflow-action-grid"):
                    yield Button("Browse saved workflows", id="workflow-browse")
                    yield Button("Import JSON or bundle", id="workflow-import", classes="primary")
                    yield Button("Export native JSON", id="workflow-export")
                    yield Button("Create workflow bundle", id="workflow-pack")
            with Container(classes="card"):
                yield Static("Detected workflow libraries", classes="card-title")
                if selected:
                    yield Static(
                        f"Selected ComfyUI: [b]{selected.path}[/b]\n"
                        f"Valid workflow JSON files: {workflow_count}",
                        classes="help-text",
                    )
                    if libraries:
                        for library in libraries[:3]:
                            yield Static(
                                f"• {library.user_name} ({library.source}): {library.path}",
                                classes="help-text",
                            )
                    else:
                        yield Static(
                            "No saved workflow library exists yet. It will be created when a workflow is imported.",
                            classes="help-text",
                        )
                    if len(libraries) > 3:
                        yield Static(
                            f"{len(libraries) - 3} additional workflow libraries are available through Browse saved workflows.",
                            classes="help-text",
                        )
                else:
                    yield Static(
                        "No ComfyUI installation is selected. You can still install a bundle into an exact custom directory.",
                        classes="help-text",
                    )
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#workflow-import", Button).tooltip = (
            "Install a native ComfyUI JSON workflow or a directory-preserving .comfyworkflows/.zip bundle."
        )
        self.query_one("#workflow-pack", Button).tooltip = (
            "Archive an existing workflow directory without flattening its folders."
        )

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#workflow-browse")
    def browse_workflows(self) -> None:
        self.app.push_screen(BrowseWorkflowsScreen())

    @on(Button.Pressed, "#workflow-import")
    def import_workflow(self) -> None:
        self.app.push_screen(ImportWorkflowScreen())

    @on(Button.Pressed, "#workflow-export")
    def export_workflow(self) -> None:
        self.app.push_screen(ExportWorkflowScreen())

    @on(Button.Pressed, "#workflow-pack")
    def export_pack(self) -> None:
        self.app.push_screen(ExportWorkflowPackScreen())


class BrowseWorkflowsScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        selected = self.app.selected_workflow_installation()
        workflows = discover_workflow_files(selected.path) if selected else []
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "SAVED WORKFLOWS",
                "These are native workflow JSON files already visible to the selected ComfyUI installation.",
            )
            with Container(classes="card"):
                if not selected:
                    yield Static("No ComfyUI installation is selected.", classes="help-text")
                elif not workflows:
                    yield Static("No valid workflow JSON files were found.", classes="help-text")
                else:
                    yield Static(f"Found {len(workflows)} workflows.", classes="card-title")
                    for path in workflows[:200]:
                        try:
                            summary = summarize_workflow(load_workflow_json(path))
                            details = f"{summary.node_count} nodes • {len(summary.node_types)} node types"
                        except Exception:
                            details = "Unable to inspect"
                        yield Static(f"[b]{path.name}[/b]\n{path}\n{details}", classes="component component-supported")
                    if len(workflows) > 200:
                        yield Static(f"Only the first 200 of {len(workflows)} workflows are shown.", classes="help-text")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()


class ImportWorkflowScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        selected = self.app.selected_workflow_installation()
        default_comfy = selected.path if selected else Path.cwd()
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "IMPORT WORKFLOW OR BUNDLE",
                "Install native JSON or a workflow archive while preserving every subdirectory stored in the archive.",
            )
            with Container(classes="card"):
                yield Label("Workflow JSON, .comfyworkflows, or compatible .zip archive")
                yield PathField(
                    "", input_id="workflow-import-path", mode="file", must_exist=True,
                    extensions=(".json", ".comfyworkflows", ".zip"),
                    browser_title="SELECT WORKFLOW OR BUNDLE",
                )
                yield Label("ComfyUI installation directory")
                yield PathField(
                    str(default_comfy), input_id="workflow-import-comfy", mode="directory", must_exist=True,
                    browser_title="SELECT COMFYUI INSTALLATION",
                )
                yield Label("ComfyUI user name")
                yield Input("default", id="workflow-import-user")
                yield Label("Installation destination")
                yield Select(
                    [
                        ("Use the archive's default subdirectory", "archive-default"),
                        ("Install at the native workflow-library root", "library-root"),
                        ("Use a custom subdirectory under the native library", "subfolder"),
                        ("Use an exact custom directory", "custom-directory"),
                    ],
                    value="archive-default",
                    id="workflow-import-mode",
                    allow_blank=False,
                )
                yield PathField(
                    "", input_id="workflow-import-destination", mode="directory", must_exist=False, disabled=True,
                    browser_title="SELECT CUSTOM WORKFLOW DESTINATION",
                )
                yield Checkbox("Install README, previews, and metadata sidecars", value=True, id="workflow-import-support")
                yield Checkbox("Import an embedded .comfyuisetup profile into the manager library", value=True, id="workflow-import-setup")
                yield Checkbox("Download a direct referenced .comfyuisetup URL", value=False, id="workflow-import-download-setup")
                yield Checkbox("Overwrite matching files", value=False, id="workflow-import-overwrite")
                yield Static(
                    "A bundle never installs models or public plugin source. If it contains or references a setup profile, that profile is imported separately and remains reviewable before you apply it.",
                    classes="help-text",
                )
            yield Static("Choose an artifact, then inspect or import it.", id="workflow-import-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Inspect", id="workflow-import-inspect")
                yield Button("Import", id="workflow-import-run", classes="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#workflow-import-path", Input).tooltip = (
            "Raw .json installs as one workflow. .comfyworkflows or compatible .zip installs its preserved workflow tree."
        )
        self.query_one("#workflow-import-destination", Input).tooltip = (
            "For a subfolder, enter a relative path such as vehicles/hot-rods. For an exact directory, enter any local path."
        )

    @on(Select.Changed, "#workflow-import-mode")
    def destination_mode_changed(self, event: Select.Changed) -> None:
        field = self.query_one("#workflow-import-destination", Input)
        disabled = event.value not in {"subfolder", "custom-directory"}
        field.disabled = disabled
        self.query_one("#workflow-import-destination-browse", Button).disabled = disabled
        if event.value == "subfolder":
            field.placeholder = "Example: shared/vehicle-workflows"
        elif event.value == "custom-directory":
            field.placeholder = "Exact workflow directory"
        else:
            field.placeholder = ""

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#workflow-import-inspect")
    def inspect_action(self) -> None:
        artifact = Path(self.query_one("#workflow-import-path", Input).value).expanduser()
        status = self.query_one("#workflow-import-status", Static)
        try:
            summary = inspect_workflow_artifact(artifact)
            if isinstance(summary, WorkflowBundleSummary):
                message = (
                    f"[b]{summary.name}[/b]\n"
                    f"Workflows: {summary.workflow_count} • Support files: {summary.support_file_count}\n"
                    f"Archive default: {summary.default_install_subdirectory or '(workflow-library root)'}\n"
                    f"README: {'yes' if summary.readme_included else 'no'} • "
                    f"Embedded setup: {'yes' if summary.setup_embedded else 'no'}"
                )
                if summary.setup_reference:
                    message += f"\nSetup reference: {summary.setup_reference}"
            else:
                message = (
                    f"[b]Native ComfyUI workflow JSON[/b]\n"
                    f"Nodes: {summary.node_count} • Node types: {len(summary.node_types)} • "
                    f"Model references: {len(summary.model_references)}"
                )
            status.update(message)
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Inspection failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")

    @on(Button.Pressed, "#workflow-import-run")
    def import_action(self) -> None:
        artifact = Path(self.query_one("#workflow-import-path", Input).value).expanduser()
        comfy_text = self.query_one("#workflow-import-comfy", Input).value.strip()
        comfy = Path(comfy_text).expanduser() if comfy_text else None
        user_name = self.query_one("#workflow-import-user", Input).value.strip() or "default"
        mode = self.query_one("#workflow-import-mode", Select).value
        destination_text = self.query_one("#workflow-import-destination", Input).value.strip()
        subfolder = destination_text if mode == "subfolder" else None
        custom_directory = Path(destination_text).expanduser() if mode == "custom-directory" and destination_text else None
        use_default = mode == "archive-default"
        overwrite = self.query_one("#workflow-import-overwrite", Checkbox).value
        status = self.query_one("#workflow-import-status", Static)
        try:
            if mode in {"subfolder", "custom-directory"} and not destination_text:
                raise WorkflowError("Enter the selected workflow destination.")
            result = import_workflow_artifact(
                artifact,
                comfy,
                user_name=user_name,
                subfolder=subfolder,
                use_bundle_default=use_default,
                custom_directory=custom_directory,
                overwrite=overwrite,
                install_support_files=self.query_one("#workflow-import-support", Checkbox).value,
                import_setup=self.query_one("#workflow-import-setup", Checkbox).value,
                download_setup_reference=self.query_one("#workflow-import-download-setup", Checkbox).value,
            )
            message = (
                f"[b]Workflow import completed.[/b]\n"
                f"Destination: {result.destination}\n"
                f"Workflows installed: {len(result.installed)}\n"
                f"Support files installed: {len(result.support_files)}"
            )
            if result.setup_profile:
                message += f"\nImported setup profile: {result.setup_profile}"
                self.app.reload_profiles()
                selected_profile_id: str | None = None
                for record in self.app.profile_records:
                    try:
                        if record.path.resolve() == result.setup_profile.resolve():
                            selected_profile_id = record.id
                            break
                    except OSError:
                        continue
                if selected_profile_id:
                    self.app.refresh_profile_views(selected_profile_id)
                    message += "\nThe imported setup is now selected on the main dashboard and can be reviewed/applied there."
            if result.setup_reference:
                message += f"\nSetup reference: {result.setup_reference}"
            if result.warnings:
                message += "\n\nWarnings:\n" + "\n".join(f"• {warning}" for warning in result.warnings)
            status.update(message)
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Import failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class ExportWorkflowScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        selected = self.app.selected_workflow_installation()
        detected = discover_workflow_files(selected.path) if selected else []
        default_source = detected[0] if detected else Path.cwd() / "workflow.json"
        default_output = Path.cwd() / default_source.name
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "EXPORT NATIVE WORKFLOW JSON",
                "Copy a saved workflow exactly as a normal ComfyUI JSON file—no wrapper and no custom format.",
            )
            with Container(classes="card"):
                yield Label("Existing workflow JSON")
                yield PathField(
                    str(default_source), input_id="workflow-export-source", mode="file", must_exist=True,
                    extensions=(".json",), browser_title="SELECT WORKFLOW JSON",
                )
                yield Label("Output JSON file")
                yield PathField(
                    str(default_output), input_id="workflow-export-output", mode="file", must_exist=False,
                    extensions=(".json",), browser_title="CHOOSE WORKFLOW EXPORT FILE",
                )
                yield Static(
                    "ComfyUI workflow JSON files are already portable and shareable. Use a workflow bundle only when you need a folder tree, README, metadata, previews, or setup information.",
                    classes="help-text",
                )
            yield Static("Ready.", id="workflow-export-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Export JSON", id="workflow-export-run", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#workflow-export-run")
    def export_action(self) -> None:
        source = Path(self.query_one("#workflow-export-source", Input).value).expanduser()
        output = Path(self.query_one("#workflow-export-output", Input).value).expanduser()
        status = self.query_one("#workflow-export-status", Static)
        try:
            workflow = load_workflow_json(source)
            summary = summarize_workflow(workflow)
            path = export_native_workflow(source, output)
            message = (
                f"[b]Native workflow JSON exported.[/b]\n{path}\n\n"
                f"Nodes: {summary.node_count} • Required node types: {len(summary.node_types)} • "
                f"Model references: {len(summary.model_references)}"
            )
            if summary.warnings:
                message += "\n\nWarnings:\n" + "\n".join(f"• {warning}" for warning in summary.warnings)
            status.update(message)
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Export failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class ExportWorkflowPackScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        selected = self.app.selected_workflow_installation()
        libraries = discover_workflow_libraries(selected.path) if selected else []
        default_root = libraries[0].path if libraries else Path.cwd()
        default_output = Path.cwd() / "my-workflow-bundle.comfyworkflows"
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "CREATE WORKFLOW BUNDLE",
                "Archive native workflows while keeping the complete directory structure and optional documentation/setup metadata.",
            )
            with Container(classes="card"):
                yield Label("Workflow source root")
                yield PathField(
                    str(default_root), input_id="workflow-pack-root", mode="directory", must_exist=True,
                    browser_title="SELECT WORKFLOW ROOT",
                )
                yield Label("Files or directories to include, separated by semicolons")
                yield PathField(
                    str(default_root), input_id="workflow-pack-sources", mode="directory", must_exist=True,
                    browser_title="SELECT WORKFLOW SOURCE DIRECTORY",
                )
                yield Checkbox("Search supplied directories recursively", value=True, id="workflow-pack-recursive")
                yield Checkbox("Include adjacent README, preview, and metadata files", value=True, id="workflow-pack-support")
                yield Static(
                    "Every selected file is stored relative to the source root. Nested folders are recreated exactly when the bundle is installed.",
                    classes="help-text",
                )
            with Container(classes="card"):
                yield Label("Bundle name")
                yield Input("My Workflow Bundle", id="workflow-pack-name")
                yield Label("Author or publisher")
                yield Input("", id="workflow-pack-publisher")
                yield Label("Description")
                yield Input("", id="workflow-pack-description")
                yield Label("Tags, comma separated")
                yield Input("", id="workflow-pack-tags")
                yield Label("Default install subdirectory under the native workflow library")
                yield Input("", id="workflow-pack-default-dir")
                yield Label("Optional README.md file")
                yield PathField(
                    "", input_id="workflow-pack-readme", mode="file", must_exist=True,
                    extensions=(".md", ".txt"), browser_title="SELECT README FILE",
                )
                yield Label("Optional .comfyuisetup file to embed")
                yield PathField(
                    "", input_id="workflow-pack-setup-file", mode="file", must_exist=True,
                    extensions=(".comfyuisetup", ".comfysetup"), browser_root=profiles_directory(),
                    browser_title="SELECT SETUP PROFILE TO EMBED",
                )
                yield Label("Optional .comfyuisetup URL, GitHub page, or download reference")
                yield PathField(
                    "", input_id="workflow-pack-setup-reference", mode="any", must_exist=False,
                    browser_root=profiles_directory(), browser_title="SELECT LOCAL SETUP REFERENCE",
                )
                yield Label("Companion YAML files, separated by semicolons")
                yield PathField(
                    "", input_id="workflow-pack-requirements-yaml", mode="file", must_exist=True,
                    extensions=(".yaml", ".yml"), browser_title="SELECT REQUIREMENTS YAML",
                )
                yield Static(
                    "Examples: models.yaml; nodes.yaml; asset-sources.yaml; libraries.yaml. These manifests describe requirements without embedding large assets.",
                    classes="help-text",
                )
                yield Label("Output .comfyworkflows file")
                yield PathField(
                    str(default_output), input_id="workflow-pack-output", mode="file", must_exist=False,
                    extensions=(".comfyworkflows", ".zip"), browser_title="CHOOSE WORKFLOW BUNDLE FILE",
                )
                yield Static(
                    "The archive contains workflows and selected documentation only. Models, LoRAs, checkpoints, credentials, and public plugin repositories are never added.",
                    classes="help-text",
                )
            yield Static("Ready.", id="workflow-pack-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Create bundle", id="workflow-pack-run", classes="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#workflow-pack-root", Input).tooltip = (
            "The directory relative to which all nested workflow paths are recorded. Usually user/default/workflows."
        )
        self.query_one("#workflow-pack-default-dir", Input).tooltip = (
            "Example: shared/vehicle-generation. Leave blank to default to the workflow-library root."
        )

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#workflow-pack-run")
    def export_action(self) -> None:
        raw_sources = self.query_one("#workflow-pack-sources", Input).value
        sources = [Path(value.strip()).expanduser() for value in raw_sources.split(";") if value.strip()]
        root_text = self.query_one("#workflow-pack-root", Input).value.strip()
        source_root = Path(root_text).expanduser() if root_text else None
        output = Path(self.query_one("#workflow-pack-output", Input).value).expanduser()
        name = self.query_one("#workflow-pack-name", Input).value.strip()
        publisher = self.query_one("#workflow-pack-publisher", Input).value.strip()
        description = self.query_one("#workflow-pack-description", Input).value.strip()
        tags = [value.strip() for value in self.query_one("#workflow-pack-tags", Input).value.split(",")]
        default_dir = self.query_one("#workflow-pack-default-dir", Input).value.strip()
        readme_text = self.query_one("#workflow-pack-readme", Input).value.strip()
        readme = Path(readme_text).expanduser() if readme_text else None
        setup_file_text = self.query_one("#workflow-pack-setup-file", Input).value.strip()
        setup_file = Path(setup_file_text).expanduser() if setup_file_text else None
        setup_reference = self.query_one("#workflow-pack-setup-reference", Input).value.strip() or None
        requirements_text = self.query_one("#workflow-pack-requirements-yaml", Input).value
        requirements_yaml = [Path(value.strip()).expanduser() for value in requirements_text.split(";") if value.strip()]
        recursive = self.query_one("#workflow-pack-recursive", Checkbox).value
        include_support = self.query_one("#workflow-pack-support", Checkbox).value
        status = self.query_one("#workflow-pack-status", Static)
        try:
            path = write_workflow_pack(
                sources,
                output,
                name=name,
                description=description,
                publisher=publisher,
                tags=tags,
                default_install_subdirectory=default_dir,
                setup_profile_path=setup_file,
                setup_reference=setup_reference,
                readme_path=readme,
                source_root=source_root,
                include_support_files=include_support,
                recursive=recursive,
                companion_yaml_paths=requirements_yaml,
            )
            summary = inspect_workflow_artifact(path)
            assert isinstance(summary, WorkflowBundleSummary)
            status.update(
                f"[b]Workflow bundle created.[/b]\n{path}\n\n"
                f"Workflows: {summary.workflow_count} • Support files: {summary.support_file_count}\n"
                f"Default install subdirectory: {summary.default_install_subdirectory or '(workflow-library root)'}"
            )
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Bundle creation failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")



class SharedAssetPathsScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        paths = load_shared_asset_paths()
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "SHARED MODELS AND WORKFLOWS",
                "Use one external library for every ComfyUI instance. This prevents duplicate downloads and makes setups portable.",
            )
            with Container(classes="card"):
                yield Label("External models directory")
                yield PathField(
                    str(paths.models), input_id="shared-models-path", mode="directory", must_exist=False,
                    browser_title="SELECT SHARED MODELS DIRECTORY",
                )
                yield Static(
                    "The manager creates standard subfolders for checkpoints, LoRAs, VAEs, ControlNet, audio, video, 3D, and other model types.",
                    classes="help-text",
                )
                yield Label("External workflows directory")
                yield PathField(
                    str(paths.workflows), input_id="shared-workflows-path", mode="directory", must_exist=False,
                    browser_title="SELECT SHARED WORKFLOWS DIRECTORY",
                )
                yield Static(
                    "Every managed ComfyUI instance links its native user/default/workflows directory to this shared library.",
                    classes="help-text",
                )
                yield Checkbox(
                    "Merge existing local workflows into the shared library before linking",
                    value=paths.migrate_existing,
                    id="shared-migrate",
                )
                yield Select(
                    [
                        ("Preserve existing shared files", "preserve"),
                        ("Rename incoming conflicts", "rename"),
                        ("Overwrite shared conflicts", "overwrite"),
                    ],
                    value=paths.conflict_policy,
                    id="shared-conflict-policy",
                    allow_blank=False,
                )
                yield Checkbox(
                    "Apply these paths to every detected ComfyUI installation",
                    value=True,
                    id="shared-apply-all",
                )
                yield Static(id="shared-path-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Open YAML", id="shared-open-yaml")
                yield Button("Save and apply", id="shared-save", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#shared-open-yaml")
    def open_yaml(self) -> None:
        self.app.push_screen(YamlConfigEditorScreen("asset-paths.yaml", "SHARED ASSET PATHS"))

    @on(Button.Pressed, "#shared-save")
    def save_paths(self) -> None:
        status = self.query_one("#shared-path-status", Static)
        try:
            paths = SharedAssetPaths(
                models=Path(self.query_one("#shared-models-path", Input).value).expanduser().resolve(),
                workflows=Path(self.query_one("#shared-workflows-path", Input).value).expanduser().resolve(),
                migrate_existing=self.query_one("#shared-migrate", Checkbox).value,
                conflict_policy=str(self.query_one("#shared-conflict-policy", Select).value),
            )
            config_path = save_shared_asset_paths(paths)
            create_shared_directories(paths)
            applied = []
            if self.query_one("#shared-apply-all", Checkbox).value:
                applied = apply_shared_assets_to_installations(
                    [item.path for item in self.app.installations],
                    paths,
                    migrate_existing=paths.migrate_existing,
                )
            failures = [item for item in applied if not item.get("success")]
            status.update(
                f"[b]Saved.[/b]\n{config_path}\nModels: {paths.models}\nWorkflows: {paths.workflows}\n"
                f"Applied: {len(applied) - len(failures)} · Failed: {len(failures)}"
            )
            status.set_classes("builder-result success-box" if not failures else "builder-result status-warn")
            for screen in self.app.screen_stack:
                if isinstance(screen, HomeScreen):
                    screen._refresh_asset_tabs()
        except Exception as exc:
            status.update(f"[b]Could not configure shared paths.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class YamlConfigurationHubScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "YAML CONFIGURATION",
                "Every editable manager configuration file can be opened inside the TUI or in the system editor.",
            )
            with Container(classes="card"):
                yield Select(
                    [(name, name) for name in EDITABLE_CONFIG_FILES],
                    value=EDITABLE_CONFIG_FILES[0],
                    id="yaml-config-file",
                    allow_blank=False,
                )
                yield Static(
                    "Source lists, shared paths, wheel rules, PyTorch releases, repositories, themes, installations, and task state are YAML.",
                    classes="help-text",
                )
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Open in system editor", id="yaml-config-external")
                yield Button("Edit in TUI", id="yaml-config-edit", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    def _filename(self) -> str:
        return str(self.query_one("#yaml-config-file", Select).value)

    @on(Button.Pressed, "#yaml-config-external")
    def external(self) -> None:
        open_source_file(ensure_editable_config(self._filename()))

    @on(Button.Pressed, "#yaml-config-edit")
    def edit(self) -> None:
        filename = self._filename()
        self.app.push_screen(YamlConfigEditorScreen(filename, filename.upper()))


class YamlConfigEditorScreen(WizardScreen):
    def __init__(self, filename: str, title: str) -> None:
        super().__init__()
        self.filename = filename
        self.screen_title = title
        self.path = ensure_editable_config(filename)

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                self.screen_title,
                "Edit the YAML directly. The manager validates it before saving. You can also open it in the system editor.",
            )
            yield Static(str(self.path), classes="path-text")
            yield TextArea(self.path.read_text(encoding="utf-8"), id="yaml-editor", language="yaml", show_line_numbers=True)
            yield Static(id="yaml-editor-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Open external editor", id="yaml-open-external")
                yield Button("Save YAML", id="yaml-save", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#yaml-open-external")
    def open_external(self) -> None:
        open_source_file(self.path)

    @on(Button.Pressed, "#yaml-save")
    def save_yaml(self) -> None:
        import yaml
        status = self.query_one("#yaml-editor-status", Static)
        text = self.query_one("#yaml-editor", TextArea).text
        try:
            payload = yaml.safe_load(text) or {}
            if not isinstance(payload, dict):
                raise ValueError("The YAML root must be a mapping.")
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")
            temporary.replace(self.path)
            status.update(f"[b]Saved and validated.[/b]\n{self.path}")
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]YAML was not saved.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class AssetDownloadScreen(WizardScreen):
    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def compose(self) -> ComposeResult:
        entries = list_asset_entries(self.kind)
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                f"DOWNLOAD {self.kind.upper()}",
                "Choose a catalog entry from the editable YAML source registry.",
            )
            with Container(classes="card"):
                yield Select(
                    [(f"{entry.name} — {entry.destination}", entry.id) for entry in entries] or [("No catalog entries", "__none__")],
                    value=entries[0].id if entries else "__none__",
                    id="asset-download-entry",
                    allow_blank=False,
                )
                yield Checkbox("Overwrite an existing file with the same name", value=False, id="asset-download-overwrite")
                yield Static(id="asset-download-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Edit catalog YAML", id="asset-download-edit")
                yield Button("Download", id="asset-download-run", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#asset-download-edit")
    def edit(self) -> None:
        filename = "model-sources.yaml" if self.kind == "models" else "workflow-sources.yaml"
        self.app.push_screen(YamlConfigEditorScreen(filename, f"{self.kind.upper()} SOURCES"))

    @on(Button.Pressed, "#asset-download-run")
    def download(self) -> None:
        entry_id = str(self.query_one("#asset-download-entry", Select).value)
        status = self.query_one("#asset-download-status", Static)
        if entry_id == "__none__":
            status.update("Add a catalog entry first.")
            return
        try:
            task = download_asset_entry(
                self.kind,
                entry_id,
                overwrite=self.query_one("#asset-download-overwrite", Checkbox).value,
            )
            status.update(f"[b]Download complete.[/b]\n{task.destination}")
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Download failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class AssetImportScreen(WizardScreen):
    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(f"IMPORT {self.kind.upper()}", "Copy an existing file into the shared external library.")
            with Container(classes="card"):
                yield Label("Source file")
                yield PathField(
                    "", input_id="asset-import-source", mode="file", must_exist=True,
                    browser_title=f"SELECT {self.kind.upper()} FILE",
                )
                yield Label("Destination subdirectory")
                yield Input("checkpoints" if self.kind == "models" else "", id="asset-import-destination")
                yield Checkbox("Overwrite an existing file", value=False, id="asset-import-overwrite")
                yield Static(id="asset-import-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Import", id="asset-import-run", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#asset-import-run")
    def run_import(self) -> None:
        status = self.query_one("#asset-import-status", Static)
        try:
            path = import_asset(
                self.kind,
                Path(self.query_one("#asset-import-source", Input).value),
                destination=self.query_one("#asset-import-destination", Input).value.strip(),
                overwrite=self.query_one("#asset-import-overwrite", Checkbox).value,
            )
            status.update(f"[b]Imported.[/b]\n{path}")
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Import failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class AssetExportScreen(WizardScreen):
    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def compose(self) -> ComposeResult:
        assets = list_installed_assets(self.kind)
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                f"EXPORT {self.kind.upper()}",
                "Copy one file out of the shared library without removing or changing the original.",
            )
            with Container(classes="card"):
                yield Select(
                    [(item["relative_path"], item["relative_path"]) for item in assets] or [("No files found", "__none__")],
                    value=assets[0]["relative_path"] if assets else "__none__",
                    id="asset-export-path",
                    allow_blank=False,
                )
                yield Label("Output file or directory")
                yield PathField(
                    "", input_id="asset-export-output", mode="any", must_exist=False,
                    browser_title=f"CHOOSE {self.kind.upper()} EXPORT PATH",
                )
                yield Checkbox("Overwrite an existing output file", value=False, id="asset-export-overwrite")
                yield Static(id="asset-export-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Export copy", id="asset-export-run", classes="primary")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#asset-export-run")
    def run_export(self) -> None:
        status = self.query_one("#asset-export-status", Static)
        relative = str(self.query_one("#asset-export-path", Select).value)
        output = self.query_one("#asset-export-output", Input).value.strip()
        if relative == "__none__" or not output:
            status.update("Choose a source file and output path.")
            return
        try:
            path = export_asset(
                self.kind,
                relative,
                Path(output),
                overwrite=self.query_one("#asset-export-overwrite", Checkbox).value,
            )
            status.update(f"[b]Exported copy.[/b]\n{path}")
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Export failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class AssetTasksScreen(WizardScreen):
    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def _tasks(self):
        return [task for task in list_download_tasks() if task.kind == self.kind]

    def compose(self) -> ComposeResult:
        tasks = self._tasks()
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                f"{self.kind.upper()} DOWNLOAD TASKS",
                "Review download history, retry a catalog item, or clear completed and failed records.",
            )
            with Container(classes="card"):
                yield Select(
                    [
                        (f"{task.status.upper()} · {task.asset_id} · {task.id}", task.id)
                        for task in reversed(tasks)
                    ] or [("No task records", "__none__")],
                    value=tasks[-1].id if tasks else "__none__",
                    id="asset-task-select",
                    allow_blank=False,
                )
                yield RichLog(id="asset-task-log", wrap=True, markup=False, highlight=False, max_lines=2000)
                yield Checkbox("Overwrite an existing destination when retrying", value=False, id="asset-task-overwrite")
                yield Static(id="asset-task-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Refresh", id="asset-task-refresh")
                yield Button("Clear finished", id="asset-task-clear")
                yield Button("Retry selected", id="asset-task-retry", classes="primary")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_log()

    def _refresh_log(self) -> None:
        log = self.query_one("#asset-task-log", RichLog)
        log.clear()
        tasks = self._tasks()
        if not tasks:
            log.write(Text("No download tasks have been recorded."))
            return
        output: list[Text] = []
        for task in reversed(tasks):
            total = f"/{task.total_bytes}" if task.total_bytes is not None else ""
            line = f"{task.status.upper():9} {task.asset_id}  {task.downloaded_bytes}{total} bytes  {task.destination}"
            if task.error:
                line += f"  ERROR: {task.error}"
            output.append(Text(line))
        log.write_lines(output)

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#asset-task-refresh")
    def refresh(self) -> None:
        self._refresh_log()

    @on(Button.Pressed, "#asset-task-clear")
    def clear_finished(self) -> None:
        removed = clear_download_tasks(self.kind)
        self.query_one("#asset-task-status", Static).update(f"Removed {removed} completed/failed task records.")
        self._refresh_log()

    @on(Button.Pressed, "#asset-task-retry")
    def retry(self) -> None:
        task_id = str(self.query_one("#asset-task-select", Select).value)
        status = self.query_one("#asset-task-status", Static)
        if task_id == "__none__":
            status.update("Choose a task first.")
            return
        try:
            task = retry_download_task(
                task_id,
                overwrite=self.query_one("#asset-task-overwrite", Checkbox).value,
            )
            status.update(f"[b]Retry completed.[/b]\n{task.destination}")
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Retry failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")
        self._refresh_log()


class AgentSkillInstallScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        skills = list_bundled_skills()
        targets = configured_agent_targets()
        first_agent = next(iter(targets), "__none__")
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "INSTALL AGENT SKILL",
                "Copy a bundled portable skill into a configured AI agent skills directory.",
            )
            with Container(classes="card compact-form"):
                yield Label("Bundled skill")
                yield Select(
                    [(item.name, item.id) for item in skills] or [("No skills found", "__none__")],
                    value=skills[0].id if skills else "__none__",
                    id="agent-skill-select",
                    allow_blank=False,
                )
                yield Label("AI agent")
                yield Select(
                    [(name, name) for name in targets] or [("No agents configured", "__none__")],
                    value=first_agent,
                    id="agent-target-select",
                    allow_blank=False,
                )
                yield Label("Skills directory")
                yield PathField(
                    str(targets.get(first_agent, "")), input_id="agent-target-path", mode="directory", must_exist=False,
                    browser_title="SELECT AGENT SKILLS DIRECTORY",
                )
                yield Checkbox("Replace an existing copy", value=False, id="agent-skill-overwrite")
                yield Static(
                    "Edit agents-skills.yaml to add another agent or change its destination.",
                    classes="help-text",
                )
                yield Static(id="agent-skill-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Install skill", id="agent-skill-run", classes="primary")
        yield Footer()

    @on(Select.Changed, "#agent-target-select")
    def target_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            target = configured_agent_targets().get(event.value)
            if target is not None:
                self.query_one("#agent-target-path", Input).value = str(target)

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#agent-skill-run")
    def run_install(self) -> None:
        skill_id = str(self.query_one("#agent-skill-select", Select).value)
        agent_id = str(self.query_one("#agent-target-select", Select).value)
        status = self.query_one("#agent-skill-status", Static)
        if skill_id == "__none__" or agent_id == "__none__":
            status.update("Choose a skill and configured agent first.")
            return
        raw_target = self.query_one("#agent-target-path", Input).value.strip()
        if not raw_target:
            status.update("Enter a skills directory.")
            return
        try:
            destination = install_skill(
                skill_id,
                agent_id,
                target_root=Path(raw_target),
                overwrite=self.query_one("#agent-skill-overwrite", Checkbox).value,
            )
            status.update(f"[b]Skill installed.[/b]\n{destination}")
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Installation failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class AssetDeleteScreen(WizardScreen):
    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def compose(self) -> ComposeResult:
        assets = list_installed_assets(self.kind)
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(f"DELETE {self.kind.upper()}", "Deletion is permanent. Choose a path from the shared library and type DELETE.")
            with Container(classes="card"):
                yield Select(
                    [(item["relative_path"], item["relative_path"]) for item in assets] or [("No files found", "__none__")],
                    value=assets[0]["relative_path"] if assets else "__none__",
                    id="asset-delete-path",
                    allow_blank=False,
                )
                yield Input(placeholder="Type DELETE", id="asset-delete-confirm")
                yield Static(id="asset-delete-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Delete file", id="asset-delete-run", classes="danger")
        yield Footer()

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#asset-delete-run")
    def run_delete(self) -> None:
        status = self.query_one("#asset-delete-status", Static)
        if self.query_one("#asset-delete-confirm", Input).value != "DELETE":
            status.update("Type DELETE exactly to confirm.")
            return
        relative = str(self.query_one("#asset-delete-path", Select).value)
        if relative == "__none__":
            return
        try:
            path = delete_asset(self.kind, relative)
            status.update(f"[b]Deleted.[/b]\n{path}")
            status.set_classes("builder-result success-box")
        except Exception as exc:
            status.update(f"[b]Delete failed.[/b]\n{exc}")
            status.set_classes("builder-result error-box")


class ProfileDeleteConfirmScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, record: ProfileRecord) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        with Container(id="profile-delete-dialog"):
            yield Static("REMOVE IMPORTED PROFILE", classes="card-title")
            yield Static(
                f"Remove [b]{self.record.name}[/b] from the manager profile library?\n\n"
                f"{self.record.path}\n\nThe original setup file outside the profile library is not deleted.",
                classes="help-text",
            )
            with Horizontal(classes="button-row"):
                yield Button("Cancel", id="profile-delete-cancel")
                yield Button("Remove profile", id="profile-delete-confirm", classes="danger")

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#profile-delete-cancel")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#profile-delete-confirm")
    def confirm(self) -> None:
        self.dismiss(True)


class ProfileBundleEditorScreen(WizardScreen):
    """Edit UTF-8 files inside an imported profile without extracting it."""

    def __init__(self, record: ProfileRecord) -> None:
        super().__init__()
        self.record = record
        self.files = list_profile_bundle_files(record.path)
        self._saving = False

    @property
    def editable_files(self) -> list[Any]:
        return [item for item in self.files if item.editable]

    def compose(self) -> ComposeResult:
        editable = self.editable_files
        first = editable[0] if editable else None
        protected = len(self.files) - len(editable)
        yield Header()
        with Container(id="profile-editor-layout"):
            yield from self.compose_header(
                f"EDIT PROFILE · {self.record.name}",
                "Changes are rebuilt transactionally and checksum-validated before the original archive is replaced.",
            )
            yield Static(str(self.record.path), classes="path-text")
            with Container(classes="card compact-form profile-file-picker"):
                yield Label("File inside archive")
                yield Select(
                    [
                        (f"{item.name}  ({item.size:,} bytes)", item.name)
                        for item in editable
                    ] or [("No editable UTF-8 text files", "__none__")],
                    value=first.name if first else "__none__",
                    id="profile-file-select",
                    allow_blank=False,
                )
                yield Static(
                    f"{len(editable)} editable text files · {protected} generated, binary, or oversized files protected. "
                    "metadata.yaml is regenerated automatically.",
                    classes="help-text",
                )
            yield TextArea(
                read_profile_bundle_file(self.record.path, first.name) if first else "",
                id="profile-file-editor",
                language=first.language if first else None,
                show_line_numbers=True,
                disabled=first is None,
            )
            yield Static("Ready.", id="profile-file-status", classes="builder-result")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Reload file", id="profile-file-reload", disabled=first is None)
                yield Button("Save, rebuild & validate", id="profile-file-save", classes="primary", disabled=first is None)
        yield Footer()

    def _selected_member(self) -> str | None:
        value = self.query_one("#profile-file-select", Select).value
        return value if isinstance(value, str) and value != "__none__" else None

    def _load_member(self, member: str) -> None:
        record = next(item for item in self.editable_files if item.name == member)
        editor = self.query_one("#profile-file-editor", TextArea)
        editor.language = record.language
        editor.load_text(read_profile_bundle_file(self.record.path, member))
        self.query_one("#profile-file-status", Static).update(
            f"Loaded {member}. Syntax: {record.language or 'plain text'}. Clipboard copies remain plain text."
        )

    @on(Select.Changed, "#profile-file-select")
    def selected_file(self, event: Select.Changed) -> None:
        if isinstance(event.value, str) and event.value != "__none__" and not self._saving:
            self._load_member(event.value)

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        if not self._saving:
            self.app.pop_screen()

    @on(Button.Pressed, "#profile-file-reload")
    def reload_file(self) -> None:
        member = self._selected_member()
        if member:
            self._load_member(member)

    @on(Button.Pressed, "#profile-file-save")
    def save_file(self) -> None:
        member = self._selected_member()
        if member is None or self._saving:
            return
        self._saving = True
        self.query_one("#profile-file-select", Select).disabled = True
        self.query_one("#profile-file-reload", Button).disabled = True
        self.query_one("#profile-file-save", Button).disabled = True
        status = self.query_one("#profile-file-status", Static)
        status.set_classes("builder-result")
        status.update(f"Rebuilding and validating {member}… The original remains untouched until validation passes.")
        self._save_profile_member(member, self.query_one("#profile-file-editor", TextArea).text)

    @work(thread=True, exclusive=True, group="profile-bundle-edit")
    def _save_profile_member(self, member: str, text: str) -> None:
        try:
            result = edit_profile_bundle_file(self.record.path, member, text)
        except Exception as exc:
            self.app.call_from_thread(self._save_failed, str(exc))
            return
        self.app.call_from_thread(self._save_completed, result.profile_id, member)

    def _restore_controls(self) -> None:
        self._saving = False
        self.query_one("#profile-file-select", Select).disabled = False
        self.query_one("#profile-file-reload", Button).disabled = False
        self.query_one("#profile-file-save", Button).disabled = False

    def _save_failed(self, message: str) -> None:
        self._restore_controls()
        status = self.query_one("#profile-file-status", Static)
        status.set_classes("builder-result error-box")
        status.update(f"[b]Profile was not changed.[/b]\n{message}")

    def _save_completed(self, profile_id: str, member: str) -> None:
        self.app.refresh_profile_views(selected_profile_id=profile_id)
        refreshed = self.app.profile_record(profile_id)
        if refreshed is not None:
            self.record = refreshed
        self.files = list_profile_bundle_files(self.record.path)
        self._restore_controls()
        self._load_member(member)
        status = self.query_one("#profile-file-status", Static)
        status.set_classes("builder-result success-box")
        status.update(
            f"[b]Saved and validated.[/b]\n{member}\nAll archive checksums were regenerated before replacement."
        )


class ProfileLibraryScreen(WizardScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="wizard", can_focus=True):
            yield from self.compose_header(
                "PROFILE LIBRARY",
                "Built-in examples and imported portable profiles are available to every detected ComfyUI installation.",
            )
            with Container(classes="card compact-form"):
                yield Label("Profile")
                yield Select(
                    [(record.label, record.id) for record in self.app.profile_records],
                    value=self.app.selected_profile_id,
                    id="profile-library-select",
                    allow_blank=False,
                )
                yield Static(id="profile-library-detail", classes="builder-result")
                yield Static("Ready.", id="profile-library-status", classes="help-text")
            with Horizontal(classes="button-row"):
                yield Button("Back", id="back")
                yield Button("Edit profile files", id="profile-edit-files")
                yield Button("Remove selected", id="profile-remove", classes="danger")
                yield Button("Import another profile", id="import", classes="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_library()

    def refresh_library(self) -> None:
        select = self.query_one("#profile-library-select", Select)
        options = [(record.label, record.id) for record in self.app.profile_records]
        select.set_options(options)
        values = {value for _, value in options}
        selected = self.app.selected_profile_id
        if selected not in values:
            selected = next(iter(values), Select.BLANK)
        select.value = selected
        if isinstance(selected, str):
            self._render_record(selected)

    def _render_record(self, profile_id: str) -> None:
        record = self.app.profile_record(profile_id)
        remove = self.query_one("#profile-remove", Button)
        edit = self.query_one("#profile-edit-files", Button)
        if record is None:
            self.query_one("#profile-library-detail", Static).update("No profile selected.")
            remove.disabled = True
            edit.disabled = True
            return
        self.app.selected_profile_id = record.id
        compatibility = record.compatibility
        exported_at = record.profile.get("export_metadata", {}).get("exported_at")
        self.query_one("#profile-library-detail", Static).update(
            f"[b]{record.name}[/b]\n"
            f"Source: {record.source}\n"
            f"ID: {record.id}\n"
            f"PEP 440 version: {record.profile.get('version', 'unknown')}\n"
            f"Created: {exported_at or record.profile.get('version', 'unknown')}\n"
            f"ABI tag: {record.abi_tag}\n"
            f"Compatibility: {record.compatibility_label}\n"
            f"Python ABI: {compatibility.get('python_abi', 'derived/unknown')}\n"
            f"Accelerator ABI: {compatibility.get('accelerator_abi', 'derived/unknown')}\n"
            f"PyTorch ABI: {compatibility.get('pytorch_abi', 'derived/unknown')}\n"
            f"Nodes: {len(record.profile.get('nodes', []))}\n"
            f"File: {record.path}\n\n"
            f"{record.profile.get('description', '')}"
        )
        remove.disabled = record.source != "imported"
        remove.tooltip = (
            "Remove this imported profile from the manager library."
            if record.source == "imported"
            else "Built-in and command-line profiles cannot be removed here."
        )
        editable_archive = (
            record.source == "imported"
            and record.path.is_file()
            and record.path.name.lower().endswith(PROFILE_EXTENSION)
        )
        edit.disabled = not editable_archive
        edit.tooltip = (
            "Edit UTF-8 files inside this archive; rebuild and validate before replacing it."
            if editable_archive
            else "Only imported .comfyuisetup archives have editable internal files."
        )

    @on(Select.Changed, "#profile-library-select")
    def selected(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self._render_record(event.value)

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.app.pop_screen()
        if isinstance(self.app.screen, HomeScreen):
            self.app.screen.refresh_all(reload_profiles=True)

    @on(Button.Pressed, "#import")
    def import_profile_screen(self) -> None:
        self.app.push_screen(ImportProfileScreen())

    @on(Button.Pressed, "#profile-edit-files")
    def edit_profile_files(self) -> None:
        value = self.query_one("#profile-library-select", Select).value
        if not isinstance(value, str):
            return
        record = self.app.profile_record(value)
        if record is None or record.source != "imported" or not record.path.name.lower().endswith(PROFILE_EXTENSION):
            self.app.notify("Select an imported .comfyuisetup archive first.", severity="warning")
            return
        try:
            self.app.push_screen(ProfileBundleEditorScreen(record))
        except Exception as exc:
            self.query_one("#profile-library-status", Static).update(f"Could not open profile editor: {exc}")

    @on(Button.Pressed, "#profile-remove")
    def remove_selected(self) -> None:
        value = self.query_one("#profile-library-select", Select).value
        if not isinstance(value, str):
            return
        record = self.app.profile_record(value)
        if record is None or record.source != "imported":
            self.app.notify("Only imported profiles can be removed.", severity="warning")
            return
        self.app.push_screen(ProfileDeleteConfirmScreen(record), lambda confirmed: self._remove_confirmed(record, confirmed))

    def _remove_confirmed(self, record: ProfileRecord, confirmed: bool | None) -> None:
        if not confirmed:
            return
        if not remove_imported_profile(record.id):
            self.query_one("#profile-library-status", Static).update("The imported profile file was already missing.")
            return
        self.app.refresh_profile_views()
        self.query_one("#profile-library-status", Static).update(f"Removed imported profile: {record.name}")



class ComfySetupApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "ComfyUI Setup Manager"
    SUB_TITLE = "Portable setups, workflows, and one-shot installation"
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("f1", "help", "Help"),
        ("t", "themes", "Theme"),
    ]

    def __init__(self, *, initial_profile: dict[str, Any] | None = None, initial_theme: str | None = None) -> None:
        super().__init__()
        selected_theme, custom_themes = load_theme_state()
        self._selected_setup_theme = initial_theme or selected_theme
        self.custom_themes = {theme.name: theme for theme in custom_themes}
        self.extra_scan_roots: set[Path] = set()
        self.profile_records: list[ProfileRecord] = []
        self.initial_profile = initial_profile
        self.selected_profile_id = initial_profile.get("id") if initial_profile else "vanilla-comfyui"
        self.selected_installation = "__new__"
        self.selected_manager_installation = ""
        self._launched_processes: dict[str, subprocess.Popen[bytes]] = {}
        self._scan_in_progress = False
        self._scan_select_installation: str | None = None
        self._scan_completion_status: str | None = None
        self.active_profile: dict[str, Any] = initial_profile or {}
        self.pending_target = Path.cwd() / "ComfyUI"
        self.repository_policy: dict[str, Any] = {
            "mode": "official",
            "repository": OFFICIAL_COMFYUI_REPOSITORY,
            "branch": "master",
            "use_current_checkout": False,
        }
        self.configuration: dict[str, Any] = {}
        self.options: InstallOptions
        self.install_result: InstallResult

        # Startup is intentionally cache-only. Hardware probing, Git commands,
        # directory walks, and asset indexing happen after Textual renders.
        self.runtime_state = load_runtime_state()
        self.platform_info: PlatformInfo = cached_platform_info(self.runtime_state)
        self.installations: list[ComfyInstallation] = cached_installations(self.runtime_state)
        self.cached_nodes: dict[str, list[dict[str, Any]]] = cached_nodes(self.runtime_state)
        self.cached_instance_workflows: dict[str, list[dict[str, Any]]] = cached_instance_workflows(self.runtime_state)
        self.cached_assets: dict[str, list[dict[str, Any]]] = cached_assets(self.runtime_state)
        self.needs_initial_scan = not bool(self.runtime_state.get("initialized", False))
        if self.installations:
            self.selected_manager_installation = str(self.installations[0].path)
        self.reload_profiles(initial_profile)

    def reload_profiles(self, additional: dict[str, Any] | None = None) -> None:
        if additional is None:
            additional = self.initial_profile
        self.profile_records = list_profiles()
        if additional:
            self.profile_records = [item for item in self.profile_records if item.id != additional["id"]]
            self.profile_records.append(ProfileRecord(additional, Path("<command-line>"), "command-line"))

    def refresh_profile_views(self, selected_profile_id: str | None = None) -> None:
        """Reload profile storage and refresh every mounted profile selector."""
        self.reload_profiles()
        if selected_profile_id and self.profile_record(selected_profile_id):
            self.selected_profile_id = selected_profile_id
        for screen in tuple(self.screen_stack):
            if isinstance(screen, HomeScreen):
                screen.refresh_all(reload_profiles=False)
            elif isinstance(screen, ProfileLibraryScreen):
                screen.refresh_library()

    def profile_record(self, profile_id: str) -> ProfileRecord | None:
        return next((record for record in self.profile_records if record.id == profile_id), None)

    def installation_for(self, value: str) -> ComfyInstallation | None:
        return next((item for item in self.installations if str(item.path) == value), None)

    def selected_workflow_installation(self) -> ComfyInstallation | None:
        if self.selected_installation != "__new__":
            selected = self.installation_for(self.selected_installation)
            if selected is not None:
                return selected
        return self.installations[0] if self.installations else None

    def _apply_runtime_state(self, state: dict[str, Any]) -> None:
        previous_manager = self.selected_manager_installation
        self.runtime_state = state
        self.platform_info = cached_platform_info(state)
        self.installations = cached_installations(state)
        self.cached_nodes = cached_nodes(state)
        self.cached_instance_workflows = cached_instance_workflows(state)
        self.cached_assets = cached_assets(state)
        self.needs_initial_scan = not bool(state.get("initialized", False))
        if self.selected_installation != "__new__" and not self.installation_for(self.selected_installation):
            self.selected_installation = "__new__"
        if previous_manager and self.installation_for(previous_manager):
            self.selected_manager_installation = previous_manager
        elif self.installations:
            self.selected_manager_installation = str(self.installations[0].path)
        else:
            self.selected_manager_installation = ""

    def reload_from_yaml(self) -> None:
        self.reload_profiles()
        self._apply_runtime_state(load_runtime_state())
        if isinstance(self.screen, HomeScreen):
            self.screen.refresh_all(reload_profiles=False)
        self.notify("Reloaded profiles, configuration, and cached runtime state from YAML.")

    def rescan_installations(self) -> None:
        """Synchronous service API retained for CLI/tests and completed wizards."""
        self._apply_runtime_state(
            scan_runtime_state(
                self.extra_scan_roots,
                refresh_catalog=not catalog_is_fresh(),
            )
        )

    def start_runtime_scan(
        self,
        *,
        prefer_manage: bool = False,
        title: str = "SCANNING SYSTEM",
        force_catalog_refresh: bool = False,
        catalog_only: bool = False,
        select_installation: Path | str | None = None,
        completion_status: str | None = None,
    ) -> None:
        if self._scan_in_progress:
            self.notify("A scan is already running.", severity="warning")
            return
        self._scan_in_progress = True
        self._scan_select_installation = (
            str(Path(select_installation).expanduser().resolve()) if select_installation else None
        )
        self._scan_completion_status = completion_status
        self.push_screen(ScanProgressScreen(title))
        self._run_runtime_scan(prefer_manage, force_catalog_refresh, catalog_only)

    @work(thread=True, exclusive=True, group="runtime-scan")
    def _run_runtime_scan(
        self,
        prefer_manage: bool,
        force_catalog_refresh: bool = False,
        catalog_only: bool = False,
    ) -> None:
        try:
            progress = lambda step, total, message: self.call_from_thread(
                self._update_runtime_scan_progress, step, total, message
            )
            if catalog_only:
                state = refresh_cached_node_catalog_state(
                    progress=progress,
                    force=force_catalog_refresh,
                    base_state=self.runtime_state,
                )
            else:
                state = scan_runtime_state(
                    self.extra_scan_roots,
                    refresh_catalog=not catalog_is_fresh(),
                    force_catalog_refresh=force_catalog_refresh,
                    progress=progress,
                )
        except Exception as exc:
            self.call_from_thread(self._runtime_scan_failed, str(exc))
            return
        self.call_from_thread(self._runtime_scan_completed, state, prefer_manage)

    def _update_runtime_scan_progress(self, step: int, total: int, message: str) -> None:
        if isinstance(self.screen, ScanProgressScreen):
            self.screen.update_progress(step, total, message)

    def _runtime_scan_failed(self, message: str) -> None:
        self._scan_in_progress = False
        self._scan_select_installation = None
        self._scan_completion_status = None
        if isinstance(self.screen, ScanProgressScreen):
            self.screen.show_error(message)
        else:
            self.notify(f"Scan failed: {message}", severity="error")

    def _runtime_scan_completed(self, state: dict[str, Any], prefer_manage: bool) -> None:
        self._scan_in_progress = False
        self._apply_runtime_state(state)
        selected = self._scan_select_installation
        completion_status = self._scan_completion_status
        self._scan_select_installation = None
        self._scan_completion_status = None
        if selected and self.installation_for(selected) is not None:
            self.selected_installation = selected
            self.selected_manager_installation = selected
        if isinstance(self.screen, ScanProgressScreen):
            self.pop_screen()
        if isinstance(self.screen, HomeScreen):
            self.screen.refresh_all(prefer_manage=prefer_manage, reload_profiles=True)
            if completion_status:
                self.screen.query_one("#home-status", Static).update(completion_status)
        self.notify("Scan complete. runtime-state.yaml has been updated.")

    def return_home_after_installation(self, target: Path, *, success: bool) -> None:
        """Return immediately, then discover the result with visible progress."""
        target = target.expanduser().resolve()
        if is_comfyui_directory(target):
            self.extra_scan_roots.add(target.parent)
        while len(self.screen_stack) > 1:
            self.pop_screen()
        if isinstance(self.screen, HomeScreen):
            self.screen.query_one("#home-status", Static).update(
                f"Refreshing installation inventory for {target}…"
            )
        completion_status = (
            f"Installation ready: {target}. Open Launch & Manage to start it."
            if success
            else f"Installation remains incomplete: {target}."
        )
        self.call_after_refresh(
            lambda: self.start_runtime_scan(
                prefer_manage=success,
                title="REFRESHING INSTALLATION INVENTORY",
                select_installation=target,
                completion_status=completion_status,
            )
        )

    def installation_running(self, value: str) -> bool:
        process = self._launched_processes.get(value)
        if process is None:
            return False
        if process.poll() is None:
            return True
        self._launched_processes.pop(value, None)
        handle = getattr(process, "_comfy_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        return False

    def launch_installation(self, installation: ComfyInstallation) -> subprocess.Popen[bytes]:
        key = str(installation.path)
        if self.installation_running(key):
            return self._launched_processes[key]
        process = launch_detached(installation.path)
        self._launched_processes[key] = process
        return process

    def stop_installation(self, value: str) -> bool:
        process = self._launched_processes.pop(value, None)
        if process is None:
            return False
        stop_process(process)
        return True

    @property
    def theme_label(self) -> str:
        return theme_display_name(self.theme or self._selected_setup_theme)

    @property
    def theme_options(self) -> list[tuple[str, str]]:
        options = [(label, name) for name, label in BUILTIN_THEME_LABELS.items()]
        options.extend(
            (f"Vim/Neovim · {theme_display_name(name)}", name)
            for name in sorted(self.custom_themes)
        )
        return options

    def _save_theme_preferences(self) -> None:
        save_theme_state(self.theme, self.custom_themes.values())

    def apply_setup_theme(self, name: str, *, persist: bool = True) -> None:
        available = {theme.name for theme in builtin_themes()} | set(self.custom_themes)
        if name not in available:
            name = "gruvbox"
        self.theme = name
        self._selected_setup_theme = name
        if persist:
            self._save_theme_preferences()
        if isinstance(self.screen, HomeScreen):
            for button in self.screen.query(Button):
                if button.id == "theme-settings":
                    button.label = f"Theme: {self.theme_label}"
                    break

    def add_custom_theme(self, theme: Any, *, apply: bool = False) -> None:
        self.custom_themes[theme.name] = theme
        self.register_theme(theme)
        if apply:
            self.apply_setup_theme(theme.name)
        else:
            self._save_theme_preferences()

    def remove_custom_theme(self, name: str) -> None:
        self.custom_themes.pop(name, None)
        self.apply_setup_theme("gruvbox")

    def get_default_screen(self) -> Screen:
        """Use the real dashboard as Textual's base screen.

        This prevents navigation from ever exposing Textual's otherwise blank
        internal default screen after a wizard completes.
        """
        return HomeScreen()

    def on_mount(self) -> None:
        for theme in builtin_themes():
            self.register_theme(theme)
        for theme in self.custom_themes.values():
            self.register_theme(theme)
        self.apply_setup_theme(self._selected_setup_theme, persist=False)
        if self.needs_initial_scan:
            self.call_after_refresh(
                lambda: self.start_runtime_scan(prefer_manage=True, title="FIRST-LAUNCH SYSTEM SCAN")
            )
        elif not catalog_is_fresh() or not node_resolution_state_is_current(self.runtime_state):
            title = (
                "DAILY COMFY REGISTRY & MANAGER CATALOG REFRESH"
                if not catalog_is_fresh()
                else "UPGRADING CACHED CUSTOM-NODE PROVENANCE"
            )
            self.call_after_refresh(
                lambda: self.start_runtime_scan(
                    prefer_manage=True,
                    title=title,
                    catalog_only=True,
                )
            )

    def action_themes(self) -> None:
        if not isinstance(self.screen, ThemeScreen):
            self.push_screen(ThemeScreen())

    def action_help(self) -> None:
        self.notify(
            "Tab and Shift+Tab move between controls. Enter activates the focused control. "
            "All text inputs, review documents, inventories, logs, and console output support mouse selection, "
            "Shift+Arrow selection, Ctrl/Cmd+A, and Ctrl/Cmd+C. Arrow keys or h/j/k/l scroll focused logs and pages. "
            "Mouse wheels and scrollbars also work. Escape goes back. T opens themes. Ctrl+Q quits.",
            title="Keyboard help",
            timeout=12,
        )
