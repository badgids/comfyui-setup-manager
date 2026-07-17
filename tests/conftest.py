from pathlib import Path
from datetime import datetime, timezone
import json
import sys

SRC = Path(__file__).resolve().parents[1] / "installer" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import os
import yaml
import pytest


@pytest.fixture(autouse=True)
def isolated_manager_config(tmp_path, monkeypatch):
    """Keep tests out of the real user config and suppress automatic scans by default."""
    config = tmp_path / "manager-config"
    config.mkdir()
    (config / "runtime-state.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "initialized": True,
                "last_scan_utc": "2026-07-16T00:00:00+00:00",
                "node_resolution_schema": 1,
                "platform": {},
                "installations": [],
                "nodes": {},
                "instance_workflows": {},
                "assets": {"models": [], "loras": [], "workflows": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_SETUP_CONFIG_DIR", str(config))
    monkeypatch.setenv("COMFYUI_SETUP_PROJECT_ROOT", str(SRC.parents[1]))

    # Keep Textual tests deterministic: startup should use a same-day catalog
    # cache rather than opening the daily network-refresh progress screen.
    catalog = tmp_path / "startup-node-catalog.json"
    now = datetime.now(timezone.utc)
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "refreshed_at_utc": now.isoformat(),
                "refreshed_date_utc": now.date().isoformat(),
                "sources": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_SETUP_MANAGER_NODE_CATALOG", str(catalog))

