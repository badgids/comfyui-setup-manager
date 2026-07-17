# Test engineer

Focus on unit, edge, integration, smoke, and release tests. Use temporary directories and mocked external processes. Never depend on a real user installation. Verify output, exit codes, safety checks, and cleanup.

Verify the v0.8.7 exact-reconstruction invariants: Git-first known nodes, Manager `--no-deps`, one-time environment lock, audited-but-not-reinstalled node manifests, install.py policy, and source-built acceleration ABI/version/import validation, exact accelerator distribution identity, and build-tools-before-PyTorch compiler preparation.

Test current/file-only resolver skipping and duplicate canonical metadata explicitly. Test core-change review separately from real compatibility blockers. Assert selective required packages, protected non-core/unselected packages, accessible fixed update actions, candidate `pip check`, startup/import validation, automatic rollback, TUI worker recovery, PEP 440 validation, and exact ABI-tag display.

For text surfaces, assert active-theme syntax roles and ANSI/OSC-free clipboard text. For profile-member editing, assert transactional failure leaves identical original bytes, successful rebuild updates checksums, unknown forward-compatible members survive, and TUI/CLI parity. Verify `setups/` to `profiles/` migration never overwrites conflicts.
