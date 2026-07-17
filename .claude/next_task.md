# Next task

1. Read the newest user request, `PRD.md`, and `docs/shared-assets.md`.
2. Inspect `git status` or the current source tree before editing.
3. Keep Models, Workflows, Shared paths, setup profiles, and workflow packs consistent.
4. Keep TUI/CLI parity through `capabilities.py` and regression tests.
5. Do not embed models, public plugin source, secrets, or machine-specific paths.
6. Update linked documentation, packaged Agent Skills, and `.claude` instructions with every feature change.
7. Preserve selectable/copyable text behavior on every new input, console, inventory, review, and diagnostic surface. Derive syntax colors from the active theme while retaining sanitized plain clipboard/cache text.
8. Reconcile captured dependency manifests against verified source-environment versions before uv resolution.
9. Keep scans and other long operations off the Textual UI thread, show progress, and restore controls on every worker failure.
10. Run test, docs, and release audits before packaging.
11. Preserve the transactional updater: skip dependency work for current/file-only updates, review normal core changes, protect every non-core and unselected core package, normalize duplicate installed metadata, require `pip check` plus startup/import validation, and automatically roll back failures.
12. Preserve PEP 440 profile versions and Python/accelerator/PyTorch ABI tags in export, display, archive metadata, and installation reports.
13. Keep project exports under `profiles/`, preserve safe migration from `setups/`, and route internal `.comfyuisetup` edits through the transactional service with CLI parity.
