from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from bscli.core.capability import CapabilityRegistry, CapabilitySpec
from bscli.core.operations import OperationStore


@dataclass(frozen=True)
class CapabilityContext:
    user_subject: str
    request_id: str
    operation_id: str
    spec: CapabilitySpec


class RequiresUserAction(RuntimeError):
    def __init__(self, code: str, message: str, *, next_action: dict) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action


class OutcomeUnknown(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


CapabilityHandler = Callable[[CapabilityContext, dict], Any]


class CapabilityEngine:
    def __init__(self, *, registry: CapabilityRegistry, operation_store: OperationStore) -> None:
        self.registry = registry
        self.operation_store = operation_store
        self._handlers: dict[str, CapabilityHandler] = {}

    def register_handler(self, capability_name: str, handler: CapabilityHandler) -> None:
        self.registry.get(capability_name)
        self._handlers[capability_name] = handler

    def invoke(
        self,
        *,
        user_subject: str,
        capability_name: str,
        arguments: dict,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        spec = self.registry.get(capability_name)
        _validate_json_object(arguments, spec.input_schema)
        effective_request_id = request_id or str(uuid4())
        operation, reused = self.operation_store.create(
            user_subject=user_subject,
            capability_name=spec.name,
            capability_version=spec.version,
            input_summary=_operation_input_summary(arguments, effect=spec.effect),
            input_identity=arguments,
            idempotency_key=idempotency_key,
            request_id=effective_request_id,
        )
        if reused:
            return _operation_response(operation, reused=True)

        handler = self._handlers.get(capability_name)
        if handler is None:
            operation = self.operation_store.mark_failed(
                operation["operation_id"],
                code="HANDLER_NOT_REGISTERED",
                message=f"No handler is registered for {capability_name}.",
            )
            return _operation_response(operation, reused=False)

        self.operation_store.mark_running(operation["operation_id"])
        context = CapabilityContext(
            user_subject=user_subject,
            request_id=operation["request_id"],
            operation_id=operation["operation_id"],
            spec=spec,
        )
        try:
            result = handler(context, arguments)
        except RequiresUserAction as exc:
            operation = self.operation_store.mark_requires_user_action(
                operation["operation_id"],
                code=exc.code,
                message=exc.message,
                next_action=exc.next_action,
            )
        except OutcomeUnknown as exc:
            operation = self.operation_store.mark_unknown(
                operation["operation_id"],
                code=exc.code,
                message=exc.message,
            )
        except Exception as exc:
            operation = self.operation_store.mark_failed(
                operation["operation_id"],
                code="CAPABILITY_EXECUTION_FAILED",
                message=str(exc) or exc.__class__.__name__,
            )
        else:
            operation = self.operation_store.mark_succeeded(operation["operation_id"], result)
        return _operation_response(operation, reused=False)


def _operation_response(operation: dict, *, reused: bool) -> dict:
    next_action = operation.get("next_action")
    interaction = (
        next_action.get("interaction")
        if isinstance(next_action, dict)
        and isinstance(next_action.get("interaction"), dict)
        else None
    )
    return {
        "protocolVersion": "0.1",
        "requestId": operation["request_id"],
        "operationId": operation["operation_id"],
        "status": operation["status"],
        "result": operation.get("result"),
        "error": operation.get("error"),
        "evidenceRefs": [],
        "nextAction": next_action,
        "interaction": interaction,
        "reused": reused,
    }


def _validate_json_object(value: Any, schema: dict) -> None:
    if not isinstance(value, dict):
        raise ValueError("capability input must be a JSON object")
    if schema.get("type") not in (None, "object"):
        raise ValueError("only object capability input schemas are supported")
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    missing = [name for name in required if name not in value]
    if missing:
        raise ValueError(f"missing required capability input: {', '.join(missing)}")
    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(value) - set(properties))
        if unexpected:
            raise ValueError(f"unexpected capability input: {', '.join(unexpected)}")
    for name, item in value.items():
        definition = properties.get(name)
        if not isinstance(definition, dict) or "type" not in definition:
            continue
        expected = definition["type"]
        if not _matches_json_type(item, expected):
            raise ValueError(f"capability input {name!r} must be {expected}")


def _matches_json_type(value: Any, expected: str) -> bool:
    types = {
        "string": str,
        "object": dict,
        "array": list,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "null": type(None),
    }
    expected_type = types.get(expected)
    if expected_type is None:
        return True
    if expected in {"integer", "number"} and isinstance(value, bool):
        return False
    return isinstance(value, expected_type)


def _operation_input_summary(arguments: dict, *, effect: str) -> dict:
    if effect == "read":
        return arguments
    summary = {}
    for name, value in arguments.items():
        if isinstance(value, str):
            summary[name] = {
                "redacted": True,
                "present": bool(value),
                "length": len(value),
            }
        elif isinstance(value, (bool, int, float)) or value is None:
            summary[name] = value
        elif isinstance(value, list):
            summary[name] = {"redacted": True, "item_count": len(value)}
        elif isinstance(value, dict):
            summary[name] = {"redacted": True, "field_count": len(value)}
        else:
            summary[name] = {"redacted": True, "type": type(value).__name__}
    return summary
