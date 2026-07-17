from __future__ import annotations

from pathlib import Path

from .exporter import export_setup
from .inventory import load_inventory, profile_from_inventory
from .profile import write_profile_bundle


class BundleError(RuntimeError):
    pass


def create_portable_profile_from_inventory(
    inventory_archive: Path,
    output_path: Path,
    *,
    profile_name: str,
    repository: str,
) -> Path:
    try:
        inventory = load_inventory(inventory_archive)
        profile_id = "-".join(
            part for part in __import__("re").split(r"[^a-z0-9]+", profile_name.lower()) if part
        ) or "custom-comfyui"
        profile = profile_from_inventory(
            inventory,
            profile_name=profile_name,
            profile_id=profile_id,
            repository=repository,
        )
        return write_profile_bundle(profile, output_path)
    except Exception as exc:
        raise BundleError(str(exc)) from exc


def create_portable_profile_from_installation(
    comfyui_directory: Path,
    output_path: Path,
    *,
    profile_name: str,
    publisher: str = "",
) -> tuple[Path, list[str]]:
    try:
        return export_setup(
            comfyui_directory,
            output_path,
            name=profile_name,
            publisher=publisher,
        )
    except Exception as exc:
        raise BundleError(str(exc)) from exc
