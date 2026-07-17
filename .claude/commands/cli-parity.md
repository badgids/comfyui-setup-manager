Check TUI/CLI parity:

1. Enumerate every TUI button, selector action, and screen operation in `app.py`.
2. Compare them with `comfy_setup.capabilities.CAPABILITIES`.
3. Confirm every capability names a working CLI command.
4. Run parser help for each command.
5. Add a regression test for any missing or renamed command.

6. Compare Models, Workflows, and Shared paths TUI buttons against their command groups.
7. Verify every download, import, delete, source-edit, task, and path action has a non-TUI command.
