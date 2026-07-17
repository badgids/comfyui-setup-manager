Run the full local validation suite:

1. Compile every Python module.
2. Run `bash -n` on POSIX scripts.
3. Run all pytest tests.
4. Run CLI smoke tests separately.
5. Report skipped tests and explain why they were skipped.
6. Do not claim native platform testing that was not actually run.

Shared asset checks:

7. Run `tests/test_shared_assets.py`, `test_asset_catalog.py`, `test_portable_asset_manifests.py`, `test_shared_asset_cli.py`, and TUI static/runtime tests.
8. Verify a setup export contains companion YAML but no machine path.
9. Verify workflow packs preserve nested directories and companion YAML.
10. Run updater selection/protection/automatic-rollback tests and profile PEP 440/ABI-tag tests.
