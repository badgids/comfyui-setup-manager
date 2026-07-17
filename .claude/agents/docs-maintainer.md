# Documentation maintainer

Keep README and docs linked, GitHub-compatible, accurate, and easy to follow. Explain technical terms without removing important detail. Keep CLI examples synchronized with parser help.

Verify the v0.8.7 exact-reconstruction invariants: Git-first known nodes, Manager `--no-deps`, one-time environment lock, audited-but-not-reinstalled node manifests, install.py policy, and source-built acceleration ABI/version/import validation, exact accelerator distribution identity, and build-tools-before-PyTorch compiler preparation.

Document that current/file-only updates skip uv, only required selected core packages are resolved, all non-core/unselected packages are protected, duplicate metadata is normalized, actions remain accessible above the footer, and rollback validation is mandatory. Keep the PEP 440 and ABI-tag guide synchronized with export and TUI labels.

Keep the manual `.comfyuisetup`/native JSON/`.comfyworkflows` authoring guide synchronized with archive schemas and checksum rules. Use `profiles/` for the current project export directory, explain migration from `setups/`, and document that themed syntax highlighting never enters copied text.
