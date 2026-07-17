# Contributing

Thank you for improving ComfyUI Setup Manager.

## Before starting

Read:

- [PRD](PRD.md)
- [Architecture](docs/architecture.md)
- [Development guide](docs/development.md)
- [Testing guide](docs/testing.md)
- [Security policy](SECURITY.md)

## Required rules

- Keep TUI and CLI feature parity.
- Add tests for normal behavior, errors, and edge cases.
- Update documentation in the same change.
- Use YAML for editable manager configuration.
- Do not hardcode machine-specific paths.
- Do not weaken archive, source, or destructive-path validation.
- Do not commit models, virtual environments, caches, outputs, or credentials.

## Pull request checklist

- [ ] Version and release notes are correct when applicable.
- [ ] Unit tests pass.
- [ ] Edge and smoke tests pass.
- [ ] CLI help and structured output are checked.
- [ ] TUI screen mounts when UI behavior changes.
- [ ] Documentation links work.
- [ ] License headers and third-party notices are preserved.
- [ ] Release audit finds no personal paths or prohibited source strings.
