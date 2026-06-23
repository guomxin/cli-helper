from __future__ import annotations

import json
import sys
from typing import Any, Callable

from bscli.core.discovered import DiscoveredApi
from bscli.core.registry import CommandRegistry
from bscli.core.tool_manifest import export_tool_manifest


CommandRunner = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class BscliMcpServer:
    def __init__(
        self,
        registry: CommandRegistry,
        *,
        command_runner: CommandRunner,
        discovered_apis: list[DiscoveredApi] | None = None,
    ):
        self.registry = registry
        self.command_runner = command_runner
        self._tools_by_name = {
            tool["name"]: tool
            for tool in export_tool_manifest(
                registry,
                discovered_apis=discovered_apis or [],
            )["tools"]
        }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        try:
            if method == "initialize":
                return self._response(request_id, self._initialize_result())
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return self._response(request_id, self._list_tools_result())
            if method == "tools/call":
                return self._response(request_id, self._call_tool_result(request.get("params") or {}))
            return self._error(request_id, -32601, f"Unsupported MCP method: {method}")
        except ValueError as exc:
            return self._error(request_id, -32602, str(exc))
        except Exception as exc:  # pragma: no cover - defensive JSON-RPC boundary
            return self._error(request_id, -32000, f"BSCLI MCP server error: {exc}")

    def serve_stdio(self, *, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self.handle_request(request)
            except json.JSONDecodeError as exc:
                response = self._error(None, -32700, f"Invalid JSON: {exc}")
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()

    def _initialize_result(self) -> dict[str, Any]:
        return {
            "protocolVersion": "2025-06-18",
            "serverInfo": {
                "name": "bscli_mcp",
                "version": "0.1.0",
            },
            "capabilities": {
                "tools": {},
            },
        }

    def _list_tools_result(self) -> dict[str, Any]:
        tools = []
        for tool in self._tools_by_name.values():
            metadata = tool["metadata"]
            read_only = metadata["access"] == "read"
            tools.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": tool["input_schema"],
                    "annotations": {
                        "readOnlyHint": read_only,
                        "destructiveHint": not read_only,
                        "idempotentHint": read_only,
                        "openWorldHint": True,
                    },
                }
            )
        return {"tools": tools}

    def _call_tool_result(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if name not in self._tools_by_name:
            raise ValueError(
                f"Unknown BSCLI MCP tool: {name}. Call tools/list and choose one of the returned tool names."
            )
        tool = self._tools_by_name[name]
        metadata = tool["metadata"]
        arguments = self._validate_arguments(tool, params.get("arguments") or {})
        if metadata.get("discovered_api"):
            arguments = {"name": metadata["discovered_api"], **arguments}
        try:
            result = self.command_runner(
                metadata["system"],
                metadata["command"],
                arguments,
            )
        except Exception as exc:
            return self._tool_error_result(tool["name"], str(exc))
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": False,
        }

    def _validate_arguments(self, tool: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object.")

        schema = tool["input_schema"]
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        for name in required:
            if name not in arguments:
                raise ValueError(
                    f"Missing required argument '{name}' for tool {tool['name']}. "
                    "Call tools/list to inspect the required inputSchema."
                )

        if schema.get("additionalProperties") is False:
            for name in arguments:
                if name not in properties:
                    raise ValueError(
                        f"Unexpected argument '{name}' for tool {tool['name']}. "
                        f"Allowed arguments: {', '.join(properties) or '(none)'}."
                    )

        for name, value in arguments.items():
            expected_type = properties.get(name, {}).get("type")
            if expected_type and not self._matches_json_type(value, expected_type):
                raise ValueError(f"Argument '{name}' must be {expected_type}.")

        return dict(arguments)

    def _matches_json_type(self, value: Any, expected_type: str) -> bool:
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return (isinstance(value, int | float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        return True

    def _tool_error_result(self, tool_name: str, message: str) -> dict[str, Any]:
        structured = {
            "error": message,
            "tool": tool_name,
            "suggestions": [
                "Start the BSCLI daemon with: python -m bscli.cli.main --home .bscli daemon serve --host 127.0.0.1 --port 8765",
                "Open the OA page in the logged-in browser and ensure the BSCLI extension is connected.",
                "Run: python -m bscli.cli.main --home .bscli daemon status",
            ],
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(structured, ensure_ascii=False, indent=2),
                }
            ],
            "structuredContent": structured,
            "isError": True,
        }

    def _response(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
