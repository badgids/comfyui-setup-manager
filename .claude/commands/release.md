Create a release:

1. Determine the next version using patch increments unless directed otherwise.
2. Update `comfy_setup.__version__`, `installer/pyproject.toml`, launchers, README, release notes, and examples.
3. Run `/test`, `/docs`, and `/audit` workflows.
4. Build the wheel and install it into a fresh temporary environment.
5. Run CLI smoke tests outside the source tree.
6. Mount important TUI screens when Textual is available.
7. Build clean ZIP and TAR.GZ archives.
8. Create SHA-256 checksums.
9. Update `VALIDATION.md` with only actually executed results.
10. Verify no-op resolver skipping, duplicate-metadata normalization, accessible selective transactional update actions, automatic rollback, and PEP 440/ABI-tag profile display in the built artifact.
11. Verify active-theme syntax/plain copying, transactional archive-member editing with CLI parity, and `profiles/` directory migration in the built artifact.
