from __future__ import annotations

import argparse
import unittest

from comfy_setup.capabilities import CAPABILITIES
from comfy_setup.cli import build_parser


COMMAND_EXAMPLES = {
    "system info": ["system", "info"],
    "system info / system check": ["system", "check"],
    "system check": ["system", "check"],
    "installations discover --scan-root PATH": ["installations", "discover", "--scan-root", "."],
    "installations show": ["installations", "show", "."],
    "installations show / installations contents": ["installations", "contents", "."],
    "installations contents": ["installations", "contents", "."],
    "installations launch": ["installations", "launch", "."],
    "installations stop": ["installations", "stop", "."],
    "installations logs --follow": ["installations", "logs", ".", "--follow"],
    "installations logs --follow/--list-files/--search/--clear/--cleanup/--retention": ["installations", "logs", ".", "--list-files"],
    "installations edit": ["installations", "edit", ".", "--name", "Test"],
    "installations uninstall --confirm UNINSTALL": ["installations", "uninstall", ".", "--confirm", "UNINSTALL"],
    "install plan": ["install", "plan", "--target", "./ComfyUI"],
    "install run": ["install", "run", "--target", "./ComfyUI"],
    "profiles list": ["profiles", "list"],
    "profiles import": ["profiles", "import", "profile.comfyuisetup"],
    "profiles export / profiles from-installation": ["profiles", "export", "vanilla-comfyui", "out.comfyuisetup"],
    "profiles files / profiles read-file / profiles edit-file": ["profiles", "files", "profile.comfyuisetup"],
    "profiles remove --yes": ["profiles", "remove", "sample", "--yes"],
    "workflows libraries / workflows list": ["workflows", "libraries"],
    "workflows inspect": ["workflows", "inspect", "workflow.json"],
    "workflows import": ["workflows", "import", "workflow.json", "--workflow-dir", "./workflows"],
    "workflows export": ["workflows", "export", "workflow.json", "copy.json"],
    "workflows pack": ["workflows", "pack", "workflow.json", "--name", "Pack", "--output", "pack.comfyworkflows"],
    "updates check": ["updates", "check", "."],
    "updates run": ["updates", "run", "."],
    "snapshots list": ["snapshots", "list", "."],
    "snapshots rollback": ["snapshots", "rollback", ".", "snapshot"],
    "snapshots delete --yes": ["snapshots", "delete", ".", "snapshot", "--yes"],
    "themes list": ["themes", "list"],
    "themes select": ["themes", "select", "gruvbox"],
    "themes import-vim": ["themes", "import-vim", "desert"],
    "wheel-sources ...": ["wheel-sources", "list"],
    "shared-paths status/configure/apply": ["shared-paths", "status"],
    "models list/import/download/delete": ["models", "list"],
    "nodes list PATH": ["nodes", "list", "."],
    "config show runtime-state.yaml / installations discover": ["config", "show", "runtime-state.yaml"],
    "models sources/add-source/add-entry/edit-sources": ["models", "sources"],
    "loras list/import/download/delete": ["loras", "list"],
    "loras sources/add-source/add-entry/edit-sources": ["loras", "sources"],
    "skills list/install/targets": ["skills", "list"],
    "mcps list/edit-config": ["mcps", "list"],
    "workflows sources/add-source/add-entry/download/tasks": ["workflows", "sources"],
    "config ...": ["config", "list"],
}


class CLIParityTests(unittest.TestCase):
    def test_every_capability_has_a_parseable_cli_command(self) -> None:
        parser = build_parser()
        missing: list[str] = []
        for capability in CAPABILITIES:
            tokens = COMMAND_EXAMPLES.get(capability.cli)
            if tokens is None:
                missing.append(capability.cli)
                continue
            parsed = parser.parse_args(tokens)
            self.assertIsInstance(parsed, argparse.Namespace)
        self.assertEqual(missing, [])

    def test_capability_ids_are_unique(self) -> None:
        ids = [item.id for item in CAPABILITIES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_capabilities_name_both_interfaces(self) -> None:
        for item in CAPABILITIES:
            self.assertTrue(item.tui.strip())
            self.assertTrue(item.cli.strip())
