from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from bscli.core.registry import CommandRegistry
from bscli.core.trace import TraceStore


@dataclass(frozen=True)
class RuntimeContext:
    system: str
    http: Any | None = None
    browser: Any | None = None


class RuntimeEngine:
    def __init__(self, *, registry: CommandRegistry, trace_store: TraceStore):
        self.registry = registry
        self.trace_store = trace_store

    async def run(
        self,
        context: RuntimeContext,
        *,
        command_name: str,
        args: dict[str, Any],
    ) -> Any:
        command = self.registry.get(context.system, command_name)
        self._validate_args(command.args_schema, args)
        run_id = self.trace_store.start_run(
            system=context.system,
            command=command.name,
            args=args,
            access=command.access,
            strategy=command.strategy,
        )
        try:
            if command.strategy == "daemon_api":
                result = await self._run_daemon_api(context, command.api, args)
            else:
                raise NotImplementedError(f"strategy not implemented: {command.strategy}")
            self._verify(command.verify, result)
        except Exception as exc:
            self.trace_store.finish_run(run_id, status="error", error=str(exc))
            raise
        self.trace_store.finish_run(run_id, status="ok", result=result)
        return result

    async def _run_daemon_api(
        self,
        context: RuntimeContext,
        api: dict[str, Any],
        args: dict[str, Any],
    ) -> Any:
        if context.http is None:
            raise ValueError("daemon_api strategy requires an http client")
        method = api.get("method", "GET")
        path = api["path"]
        body_template = api.get("body")
        body = self._render_template(body_template, args) if body_template else None
        headers = self._render_template(api.get("headers", {}), args)
        return await context.http.request(method, path, json_body=body, headers=headers)

    def _validate_args(self, schema: dict[str, Any], args: dict[str, Any]) -> None:
        for name, spec in schema.items():
            if spec.get("required") and name not in args:
                raise ValueError(f"missing required argument: {name}")
            if name in args and spec.get("type") == "string" and not isinstance(args[name], str):
                raise ValueError(f"argument must be string: {name}")

    def _verify(self, verify: dict[str, Any], result: Any) -> None:
        if not verify:
            return
        if verify.get("type") != "json_path":
            raise ValueError(f"unsupported verify type: {verify.get('type')}")
        path = verify.get("path", "")
        if not path.startswith("$."):
            raise ValueError(f"unsupported json_path: {path}")
        cursor = result
        for part in path[2:].split("."):
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                raise ValueError(f"verify failed: missing {path}")
        if cursor in (None, "", []):
            raise ValueError(f"verify failed: empty {path}")

    def _render_template(self, value: Any, args: dict[str, Any]) -> Any:
        if isinstance(value, str):
            match = re.fullmatch(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}", value)
            if match:
                return args[match.group(1)]
            return re.sub(
                r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}",
                lambda m: str(args[m.group(1)]),
                value,
            )
        if isinstance(value, list):
            return [self._render_template(item, args) for item in value]
        if isinstance(value, dict):
            return {key: self._render_template(item, args) for key, item in value.items()}
        return value
