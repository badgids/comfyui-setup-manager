# Shared asset system maintainer

Maintain `shared_assets.py`, `asset_catalog.py`, the Models and Workflows tabs, source YAML, portable asset manifests, and related CLI commands.

Check these invariants:

- existing `extra_model_paths.yaml` sections are preserved;
- workflow directory trees are preserved;
- downloads cannot escape the configured shared root;
- partial or failed downloads leave no final file;
- profile exports contain public metadata but no local paths;
- uninstall never removes external shared libraries;
- TUI and CLI behavior remain identical.
- profile/archive metadata retains PEP 440 version and Python/accelerator/PyTorch ABI identity without embedding machine-specific paths.
