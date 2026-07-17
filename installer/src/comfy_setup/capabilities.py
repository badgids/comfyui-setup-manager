from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    area: str
    description: str
    tui: str
    cli: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITIES: tuple[Capability, ...] = (
    Capability("system-info", "system", "Detect OS, GPU, accelerator, package manager, and build prerequisites.", "Setup & Install > System", "system info / system check"),
    Capability("installations-discover", "instances", "Discover existing ComfyUI installations and scan extra folders.", "Launch & Manage > Rescan / Scan folder", "installations discover --scan-root PATH"),
    Capability("installations-inspect", "instances", "View source, environment, custom nodes, workflows, and runtime status.", "Launch & Manage > View contents", "installations show / installations contents"),
    Capability("installations-launch", "instances", "Launch one instance using its local launcher and environment.", "Launch & Manage > Launch", "installations launch"),
    Capability("installations-stop", "instances", "Stop an instance started by the manager or CLI.", "Launch & Manage > Stop", "installations stop"),
    Capability("installations-logs", "instances", "Read, follow, clear, search, copy, retain, and delete managed runtime logs.", "Launch & Manage > Runtime output / Browse logs", "installations logs --follow/--list-files/--search/--clear/--cleanup/--retention"),
    Capability("installations-edit", "instances", "Edit the display name, description, and profile metadata.", "Launch & Manage > Edit details", "installations edit"),
    Capability("installations-uninstall", "instances", "Safely remove a selected ComfyUI installation.", "Launch & Manage > Uninstall", "installations uninstall --confirm UNINSTALL"),
    Capability("install-plan", "install", "Resolve a setup profile and show every planned installation step.", "Setup & Install > Review", "install plan"),
    Capability("install-run", "install", "Install ComfyUI, dependencies, nodes, acceleration packages, and local launchers.", "Setup & Install > Start installation", "install run"),
    Capability("profiles-list", "profiles", "List built-in/imported profiles with PEP 440 versions and Python/accelerator/PyTorch ABI tags.", "Setup & Install > Profiles", "profiles list"),
    Capability("profiles-import", "profiles", "Import a portable .comfyuisetup profile.", "Setup & Install > Import setup", "profiles import"),
    Capability("profiles-export", "profiles", "Export a profile or a working ComfyUI installation.", "Setup & Install > Export setup", "profiles export / profiles from-installation"),
    Capability("profiles-edit-files", "profiles", "Inspect and transactionally edit UTF-8 files inside a .comfyuisetup archive.", "Profiles > Edit profile files", "profiles files / profiles read-file / profiles edit-file"),
    Capability("profiles-remove", "profiles", "Remove an imported profile from the user library.", "Profiles > Remove", "profiles remove --yes"),
    Capability("workflows-list", "workflows", "Browse native workflows without launching ComfyUI.", "Workflows > Browse", "workflows libraries / workflows list"),
    Capability("workflows-inspect", "workflows", "Inspect one workflow or workflow pack.", "Workflows > Import > Inspect", "workflows inspect"),
    Capability("workflows-import", "workflows", "Install a JSON workflow or directory-preserving workflow archive.", "Workflows > Import", "workflows import"),
    Capability("workflows-export", "workflows", "Export one native ComfyUI workflow JSON file.", "Workflows > Export native JSON", "workflows export"),
    Capability("workflows-pack", "workflows", "Create a directory-preserving workflow archive with README and setup references.", "Workflows > Create workflow bundle", "workflows pack"),
    Capability("updates-check", "updates", "Review official core files/packages and protected environment compatibility.", "Launch & Manage > Update", "updates check"),
    Capability("updates-run", "updates", "Select core packages, snapshot, apply safe/patch/force/new/abort strategy, validate, and automatically roll back failures.", "Update > Safe update / Try to patch / Continue anyway / Create new / Abort", "updates run"),
    Capability("snapshots-list", "updates", "List lightweight pre-update snapshots.", "Launch & Manage > Rollback", "snapshots list"),
    Capability("snapshots-rollback", "updates", "Restore source and package state from a snapshot.", "Snapshots > Roll back now", "snapshots rollback"),
    Capability("snapshots-delete", "updates", "Delete a snapshot that is no longer needed.", "Snapshots > Delete", "snapshots delete --yes"),
    Capability("themes-list", "appearance", "List built-in and imported themes.", "Theme screen", "themes list"),
    Capability("themes-select", "appearance", "Select Gruvbox, Dark, Light, or an imported theme.", "Theme screen > Apply", "themes select"),
    Capability("themes-import", "appearance", "Import a Vim or Neovim colorscheme.", "Theme screen > Import", "themes import-vim"),
    Capability("wheel-sources", "resolver", "List, add, enable, disable, validate, and edit wheel sources.", "No-wheel prompt / source editor", "wheel-sources ..."),
    Capability("runtime-cache", "system", "Reload cached YAML immediately or run an explicit full rescan with visible progress.", "Launch & Manage > Reload YAML / Rescan", "config show runtime-state.yaml / installations discover"),
    Capability("nodes-list", "nodes", "List installed custom nodes and plugins for one ComfyUI installation.", "Nodes & Plugins tab", "nodes list PATH"),
    Capability("shared-paths", "assets", "Create external model and workflow libraries, then apply them to managed instances.", "Models/LoRAs/Workflows > Set shared paths", "shared-paths status/configure/apply"),
    Capability("models-library", "models", "List, import, download, delete, and catalog shared models while excluding LoRAs.", "Models tab", "models list/import/download/delete"),
    Capability("models-sources", "models", "Edit model download sources and catalog entries as YAML.", "Models > Edit sources", "models sources/add-source/add-entry/edit-sources"),
    Capability("loras-library", "loras", "List, import, download, delete, and catalog shared LoRAs in their dedicated library.", "LoRAs tab", "loras list/import/download/delete"),
    Capability("loras-sources", "loras", "Edit LoRA download sources and catalog entries as YAML.", "LoRAs > Edit sources", "loras sources/add-source/add-entry/edit-sources"),
    Capability("workflow-sources", "workflows", "Edit workflow download sources, download workflow artifacts, and track tasks.", "Workflows tab", "workflows sources/add-source/add-entry/download/tasks"),
    Capability("agent-skills", "agents", "List and install bundled portable skills into configured AI-agent skill directories.", "Agents & Skills tab", "skills list/install/targets"),
    Capability("mcp-definitions", "mcps", "List and edit portable MCP server definitions without automatically starting servers.", "MCPs tab", "mcps list/edit-config"),
    Capability("configuration", "configuration", "Inspect, validate, and edit every manager YAML configuration file.", "Workspace-specific YAML editors", "config ..."),
)


def capability_payload() -> list[dict[str, Any]]:
    return [item.to_dict() for item in CAPABILITIES]
