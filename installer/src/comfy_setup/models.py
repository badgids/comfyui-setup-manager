from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PlatformInfo:
    os_name: str
    system: str
    release: str
    architecture: str
    is_wsl: bool
    package_manager: str | None
    accelerator: str
    gpu_name: str | None = None
    compute_capability: float | None = None
    cuda_version: str | None = None
    rocm_version: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def display_os(self) -> str:
        if self.is_wsl:
            return f"WSL2 ({self.system} {self.release})"
        return f"{self.system} {self.release}".strip()

    def supports(self, platforms: list[str], accelerators: list[str]) -> bool:
        return self.os_name in platforms and self.accelerator in accelerators


@dataclass(slots=True)
class InstallOptions:
    target_dir: Path
    python_version: str
    accelerator: str
    selected_nodes: set[str]
    selected_acceleration: set[str]
    auto_install_system: bool = True
    allow_source_builds: bool = True
    backup_builds: bool = False
    backup_dir: Path | None = None
    pin_exact_refs: bool = True
    use_current_checkout: bool = True
    update_existing_nodes: bool = False
    install_command_alias: bool = False
    configure_shared_assets: bool = True
    shared_models_dir: Path | None = None
    shared_workflows_dir: Path | None = None
    migrate_existing_assets: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "target_dir": str(self.target_dir),
            "python_version": self.python_version,
            "accelerator": self.accelerator,
            "selected_nodes": sorted(self.selected_nodes),
            "selected_acceleration": sorted(self.selected_acceleration),
            "auto_install_system": self.auto_install_system,
            "allow_source_builds": self.allow_source_builds,
            "backup_builds": self.backup_builds,
            "backup_dir": str(self.backup_dir) if self.backup_dir else None,
            "pin_exact_refs": self.pin_exact_refs,
            "use_current_checkout": self.use_current_checkout,
            "update_existing_nodes": self.update_existing_nodes,
            "install_command_alias": self.install_command_alias,
            "configure_shared_assets": self.configure_shared_assets,
            "shared_models_dir": str(self.shared_models_dir) if self.shared_models_dir else None,
            "shared_workflows_dir": str(self.shared_workflows_dir) if self.shared_workflows_dir else None,
            "migrate_existing_assets": self.migrate_existing_assets,
        }


@dataclass(slots=True)
class InstallStep:
    key: str
    title: str
    detail: str
    optional: bool = False


@dataclass(slots=True)
class InstallResult:
    success: bool
    target_dir: Path
    completed_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_step: str | None = None
    error: str | None = None
    report_path: Path | None = None
    launcher_path: Path | None = None
