from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .configuration import load_editable_config

MCP_CONFIG_FILE = "mcps.yaml"


@dataclass(frozen=True, slots=True)
class MCPServer:
    id: str
    name: str
    transport: str
    command: str | None
    url: str | None
    enabled: bool
    notes: str


def list_mcp_servers() -> list[MCPServer]:
    payload = load_editable_config(MCP_CONFIG_FILE)
    output: list[MCPServer] = []
    for record in payload.get("servers", []):
        if not isinstance(record, dict) or not record.get("id"):
            continue
        output.append(
            MCPServer(
                id=str(record["id"]),
                name=str(record.get("name") or record["id"]),
                transport=str(record.get("transport") or "stdio"),
                command=str(record["command"]) if record.get("command") else None,
                url=str(record["url"]) if record.get("url") else None,
                enabled=bool(record.get("enabled", True)),
                notes=str(record.get("notes") or ""),
            )
        )
    return output
