# Release auditor

Inspect source and archives for version mismatches, missing package data, personal paths, credentials, private sources, caches, unsupported claims, broken Markdown links, missing license files, and test artifacts.

Verify the v0.8.7 exact-reconstruction invariants: Git-first known nodes, Manager `--no-deps`, one-time environment lock, audited-but-not-reinstalled node manifests, install.py policy, and source-built acceleration ABI/version/import validation, exact accelerator distribution identity, and build-tools-before-PyTorch compiler preparation.

Verify updater tests cover no-op/file-only resolver skipping, duplicate metadata normalization, selective required core packages, protection of every non-core and unselected core package, accessible patch/force actions, no direct upstream requirements execution, `pip check`, startup/import validation, and automatic rollback. Inspect exported profile, archive metadata, and installation reports for normalized PEP 440 versions and ABI tags.

Verify the release contains `profiles/` rather than a project `setups/` directory, profile-directory migration coverage, theme-derived syntax/plain-copy coverage, the transactional profile archive editor and CLI commands, and the linked manual authoring guide.
