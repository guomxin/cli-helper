from __future__ import annotations

from typing import Any

from bscli.core.discovered import DiscoveredApi
from bscli.core.registry import CommandRegistry


def export_tool_manifest(
    registry: CommandRegistry,
    *,
    system: str | None = None,
    discovered_apis: list[DiscoveredApi] | None = None,
) -> dict[str, Any]:
    tools = [_command_to_tool(command) for command in registry.list(system)]
    tools.extend(_discovered_api_to_tool(api) for api in discovered_apis or [])
    return {
        "schema_version": "bscli.tool_manifest.v1",
        "tools": tools,
    }


def _command_to_tool(command) -> dict[str, Any]:
    return {
        "name": f"{command.system}__{command.name}",
        "description": command.description,
        "input_schema": _args_to_json_schema(command.args_schema),
        "metadata": {
            "system": command.system,
            "command": command.name,
            "access": command.access,
            "risk": command.risk,
            "strategy": command.strategy,
            "requires_confirmation": command.requires_confirmation,
        },
    }


def _discovered_api_to_tool(api: DiscoveredApi) -> dict[str, Any]:
    inspection = api.inspection or {}
    shape = inspection.get("data_shape") or "unknown response shape"
    count = inspection.get("item_count")
    count_text = f", last observed item count {count}" if count is not None else ""
    properties = {}
    required = []
    if api.requires_confirmation:
        properties["confirm"] = {
            "type": "boolean",
            "description": "Must be true to explicitly confirm this non-read or higher-risk API call.",
        }
        required.append("confirm")
    return {
        "name": api.tool_name,
        "description": api.description
        or f"Run discovered API '{api.name}' ({shape}{count_text}).",
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "metadata": {
            "system": api.system,
            "command": "discovered_run",
            "discovered_api": api.name,
            "access": api.access,
            "risk": api.risk,
            "strategy": "page_fetch",
            "requires_confirmation": api.requires_confirmation,
            "data_shape": shape,
            "sample_fields": inspection.get("sample_fields") or [],
        },
    }


def _args_to_json_schema(args_schema: dict[str, Any]) -> dict[str, Any]:
    properties = {}
    required = []
    for name, spec in args_schema.items():
        spec = spec or {}
        field = {
            key: value
            for key, value in spec.items()
            if key not in {"required"} and value is not None
        }
        properties[name] = field or {"type": "string"}
        if spec.get("required"):
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
