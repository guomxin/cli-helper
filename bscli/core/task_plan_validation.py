from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from bscli.core.capability import CapabilityRegistry
from bscli.core.transforms import TransformRegistry


MAX_PLAN_STEPS = 12
MAX_GOAL_CHARS = 500
_STEP_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ALLOWED_PROPOSAL_FIELDS = {"schemaVersion", "goal", "steps"}
_ALLOWED_STEP_FIELDS = {
    "stepKey",
    "kind",
    "capabilityName",
    "transformName",
    "dependsOn",
    "arguments",
    "bindings",
}
_ALLOWED_BINDING_FIELDS = {"step", "pointer"}


class TaskPlanBindingInput(BaseModel):
    """A typed, model-visible binding from an earlier step output."""

    model_config = ConfigDict(extra="forbid")

    step: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            description="Source stepKey. The source must be in this step's dependency chain.",
        ),
    ]
    pointer: Annotated[
        str,
        Field(
            max_length=512,
            description=(
                "JSON Pointer into the source step output, for example /items or /draft. "
                "Use an empty string for the entire output object."
            ),
        ),
    ]


class _TaskPlanStepInputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stepKey: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            pattern=r"^[a-z][a-z0-9_]{0,63}$",
            description="Unique lowercase step identifier, such as read_done or draft_log.",
        ),
    ]
    dependsOn: Annotated[
        list[str],
        Field(
            default_factory=list,
            max_length=12,
            description="Earlier stepKey values that must complete before this step.",
        ),
    ]
    arguments: Annotated[
        dict[str, Any],
        Field(
            default_factory=dict,
            description="Static arguments accepted by the selected capability or transform.",
        ),
    ]
    bindings: Annotated[
        dict[str, TaskPlanBindingInput],
        Field(
            default_factory=dict,
            description=(
                "Map each target input field to an earlier step output using step and pointer."
            ),
        ),
    ]


class TaskPlanCapabilityStepInput(_TaskPlanStepInputBase):
    kind: Literal["capability"]
    capabilityName: Annotated[
        str,
        Field(
            min_length=1,
            max_length=160,
            description="Exact capability name returned by agentbridge_task_plan_catalog.",
        ),
    ]


class TaskPlanTransformStepInput(_TaskPlanStepInputBase):
    kind: Literal["transform"]
    transformName: Annotated[
        str,
        Field(
            min_length=1,
            max_length=160,
            description="Exact deterministic transform name returned by the planning catalog.",
        ),
    ]


TaskPlanStepInput = Annotated[
    TaskPlanCapabilityStepInput | TaskPlanTransformStepInput,
    Field(discriminator="kind"),
]
_TASK_PLAN_STEP_ADAPTER = TypeAdapter(TaskPlanStepInput)


def task_plan_step_json_schema() -> dict[str, Any]:
    """Return the same step schema exposed by the MCP prepare tool."""

    return _TASK_PLAN_STEP_ADAPTER.json_schema()


def serialize_task_plan_steps(
    steps: Iterable[TaskPlanCapabilityStepInput | TaskPlanTransformStepInput],
) -> list[dict[str, Any]]:
    return [
        step.model_dump(exclude_none=True, exclude_unset=True)
        for step in steps
    ]


class PlanValidationError(ValueError):
    def __init__(self, code: str, message: str, *, step_key: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.step_key = step_key

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.step_key:
            result["stepKey"] = self.step_key
        return result


ScopeResolver = Callable[[str], frozenset[str]]


def validate_and_compile_task_plan(
    proposal: dict[str, Any],
    *,
    registry: CapabilityRegistry,
    transforms: TransformRegistry,
    trusted_write_prepares: Iterable[str],
    hidden_commit_capabilities: Iterable[str],
    scope_resolver: ScopeResolver,
    granted_scopes: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        raise PlanValidationError("PLAN_SCHEMA_INVALID", "计划必须是 JSON 对象。")
    unexpected = sorted(set(proposal) - _ALLOWED_PROPOSAL_FIELDS)
    if unexpected:
        raise PlanValidationError(
            "PLAN_SCHEMA_INVALID",
            f"计划包含未知字段：{', '.join(unexpected)}。",
        )
    if proposal.get("schemaVersion") != "agentbridge.task-plan.proposal.v1":
        raise PlanValidationError(
            "PLAN_SCHEMA_INVALID",
            "计划 schemaVersion 必须为 agentbridge.task-plan.proposal.v1。",
        )
    goal = _required_text(proposal.get("goal"), "goal", MAX_GOAL_CHARS)
    raw_steps = proposal.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanValidationError("PLAN_SCHEMA_INVALID", "计划至少需要一个步骤。")
    if len(raw_steps) > MAX_PLAN_STEPS:
        raise PlanValidationError(
            "PLAN_SCHEMA_INVALID",
            f"一期计划最多允许 {MAX_PLAN_STEPS} 个步骤。",
        )

    prepares = frozenset(trusted_write_prepares)
    hidden = frozenset(hidden_commit_capabilities)
    normalized: dict[str, dict[str, Any]] = {}
    source_order: dict[str, int] = {}
    write_steps: list[str] = []
    required_scopes: set[str] = set()

    for source_ordinal, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID",
                f"第 {source_ordinal} 个步骤必须是 JSON 对象。",
            )
        unknown = sorted(set(raw_step) - _ALLOWED_STEP_FIELDS)
        if unknown:
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID",
                f"步骤包含未知字段：{', '.join(unknown)}。",
            )
        step_key = _required_text(raw_step.get("stepKey"), "stepKey", 64)
        if not _STEP_KEY_RE.fullmatch(step_key):
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID",
                f"步骤标识不合法：{step_key}。",
                step_key=step_key,
            )
        if step_key in normalized:
            raise PlanValidationError(
                "PLAN_DUPLICATE_STEP_KEY",
                f"步骤标识重复：{step_key}。",
                step_key=step_key,
            )
        kind = raw_step.get("kind")
        if kind not in {"capability", "transform"}:
            raise PlanValidationError(
                "PLAN_STEP_KIND_NOT_ALLOWED",
                "一期只允许 capability 和 transform 步骤。",
                step_key=step_key,
            )
        depends_on = raw_step.get("dependsOn") or []
        if not isinstance(depends_on, list) or any(
            not isinstance(value, str) or not value.strip() for value in depends_on
        ):
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID",
                "dependsOn 必须是步骤标识数组。",
                step_key=step_key,
            )
        depends_on = list(dict.fromkeys(value.strip() for value in depends_on))
        arguments = raw_step.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID",
                "arguments 必须是 JSON 对象。",
                step_key=step_key,
            )
        bindings = _normalize_bindings(raw_step.get("bindings") or {}, step_key)

        capability_name = None
        transform_name = None
        if kind == "capability":
            capability_name = _required_text(
                raw_step.get("capabilityName"), "capabilityName", 160
            )
            if raw_step.get("transformName") is not None:
                raise PlanValidationError(
                    "PLAN_SCHEMA_INVALID",
                    "capability 步骤不能声明 transformName。",
                    step_key=step_key,
                )
            if capability_name in hidden:
                raise PlanValidationError(
                    "PLAN_CAPABILITY_NOT_ALLOWED",
                    f"隐藏提交能力不能进入模型计划：{capability_name}。",
                    step_key=step_key,
                )
            try:
                spec = registry.get(capability_name)
            except KeyError as exc:
                raise PlanValidationError(
                    "PLAN_CAPABILITY_NOT_ALLOWED",
                    f"能力未注册：{capability_name}。",
                    step_key=step_key,
                ) from exc
            if spec.effect != "read" and capability_name not in prepares:
                raise PlanValidationError(
                    "PLAN_CAPABILITY_NOT_ALLOWED",
                    f"能力不是模型可见的读取来源或可信 prepare：{capability_name}。",
                    step_key=step_key,
                )
            if capability_name in prepares:
                write_steps.append(step_key)
            required_scopes.update(scope_resolver(capability_name))
            input_schema = spec.input_schema
            output_schema = spec.output_schema
            effect = spec.effect
            system_id = spec.system
            title = spec.description
            version = spec.version
        else:
            transform_name = _required_text(
                raw_step.get("transformName"), "transformName", 160
            )
            if raw_step.get("capabilityName") is not None:
                raise PlanValidationError(
                    "PLAN_SCHEMA_INVALID",
                    "transform 步骤不能声明 capabilityName。",
                    step_key=step_key,
                )
            try:
                transform = transforms.get(transform_name)
            except KeyError as exc:
                raise PlanValidationError(
                    "PLAN_TRANSFORM_NOT_ALLOWED",
                    f"转换未注册：{transform_name}。",
                    step_key=step_key,
                ) from exc
            input_schema = transform.input_schema
            output_schema = transform.output_schema
            effect = "read"
            system_id = "agentbridge"
            title = transform.description
            version = transform.version

        _validate_partial_input(
            arguments,
            bindings=bindings,
            schema=input_schema,
            step_key=step_key,
        )
        normalized[step_key] = {
            "stepKey": step_key,
            "kind": kind,
            "capabilityName": capability_name,
            "transformName": transform_name,
            "version": version,
            "title": title[:240],
            "dependsOn": depends_on,
            "arguments": arguments,
            "bindings": bindings,
            "effect": effect,
            "systemId": system_id,
            "inputSchema": input_schema,
            "outputSchema": output_schema,
        }
        source_order[step_key] = source_ordinal

    if len(write_steps) > 1:
        raise PlanValidationError(
            "PLAN_MULTIPLE_WRITE_SINKS",
            "一期计划最多允许一个可信写入终点。",
        )

    ordered_keys = _topological_order(normalized, source_order)
    ancestors = _dependency_ancestors(normalized, ordered_keys)
    for step_key in ordered_keys:
        step = normalized[step_key]
        for target_name, binding in step["bindings"].items():
            source_key = binding["step"]
            if source_key not in normalized:
                raise PlanValidationError(
                    "PLAN_BINDING_INVALID",
                    f"绑定来源步骤不存在：{source_key}。",
                    step_key=step_key,
                )
            if source_key not in ancestors[step_key]:
                raise PlanValidationError(
                    "PLAN_BINDING_INVALID",
                    f"绑定来源 {source_key} 不在 {step_key} 的依赖链上。",
                    step_key=step_key,
                )
            source_type = _schema_type_at_pointer(
                normalized[source_key]["outputSchema"], binding["pointer"]
            )
            target_type = _property_schema(step["inputSchema"], target_name).get("type")
            if source_type is None or target_type is None:
                raise PlanValidationError(
                    "PLAN_SCHEMA_INCOMPATIBLE",
                    f"绑定 {source_key}{binding['pointer']} 到 {target_name} 缺少精确 Schema。",
                    step_key=step_key,
                )
            if not _types_compatible(source_type, target_type):
                raise PlanValidationError(
                    "PLAN_SCHEMA_INCOMPATIBLE",
                    f"绑定 {source_key}{binding['pointer']} 与字段 {target_name} 类型不兼容。",
                    step_key=step_key,
                )

    if write_steps and ordered_keys[-1] != write_steps[0]:
        raise PlanValidationError(
            "PLAN_WRITE_SINK_NOT_FINAL",
            "可信写入 prepare 必须是一期计划的最后一个业务步骤。",
            step_key=write_steps[0],
        )

    missing_scopes: list[str] = []
    if granted_scopes is not None:
        granted = frozenset(str(value) for value in granted_scopes)
        missing_scopes = sorted(required_scopes - granted)
        if missing_scopes:
            raise PlanValidationError(
                "PLAN_SCOPE_MISSING",
                f"当前 MCP 身份缺少权限：{', '.join(missing_scopes)}。",
            )

    compiled_steps: list[dict[str, Any]] = []
    for ordinal, key in enumerate(ordered_keys, start=1):
        step = normalized[key]
        compiled_steps.append(
            {
                key_: value
                for key_, value in step.items()
                if key_ not in {"inputSchema", "outputSchema"}
            }
            | {"ordinal": ordinal}
        )
    hash_input = {
        "schemaVersion": "agentbridge.task-plan.compiled.v1",
        "goal": goal,
        "steps": compiled_steps,
        "requiredScopes": sorted(required_scopes),
    }
    canonical = json.dumps(
        hash_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    plan_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    systems = sorted(
        {step["systemId"] for step in compiled_steps if step["systemId"] != "agentbridge"}
    )
    return {
        **hash_input,
        "planHash": plan_hash,
        "riskSummary": {
            "systems": systems,
            "requiredScopes": sorted(required_scopes),
            "writeSinkCount": len(write_steps),
            "writeSinkStepKey": write_steps[0] if write_steps else None,
            "writeSinkCapability": (
                normalized[write_steps[0]]["capabilityName"] if write_steps else None
            ),
        },
        "missingScopes": missing_scopes,
    }


def validate_runtime_arguments(
    arguments: dict[str, Any],
    *,
    schema: dict[str, Any],
    step_key: str,
) -> None:
    _validate_partial_input(arguments, bindings={}, schema=schema, step_key=step_key)


def resolve_json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise PlanValidationError("PLAN_BINDING_INVALID", "JSON Pointer 不合法。")
    current = value
    for encoded in pointer.split("/")[1:]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise PlanValidationError(
                    "PLAN_BINDING_INVALID",
                    f"JSON Pointer 找不到字段：{pointer}。",
                )
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                raise PlanValidationError(
                    "PLAN_BINDING_INVALID",
                    f"JSON Pointer 数组下标越界：{pointer}。",
                )
            current = current[index]
        else:
            raise PlanValidationError(
                "PLAN_BINDING_INVALID",
                f"JSON Pointer 无法继续解析：{pointer}。",
            )
    return current


def _normalize_bindings(value: Any, step_key: str) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise PlanValidationError(
            "PLAN_SCHEMA_INVALID", "bindings 必须是 JSON 对象。", step_key=step_key
        )
    result: dict[str, dict[str, str]] = {}
    for target_name, raw in value.items():
        if not isinstance(target_name, str) or not target_name:
            raise PlanValidationError(
                "PLAN_BINDING_INVALID", "绑定目标字段不合法。", step_key=step_key
            )
        if not isinstance(raw, dict) or set(raw) - _ALLOWED_BINDING_FIELDS:
            raise PlanValidationError(
                "PLAN_BINDING_INVALID",
                f"字段 {target_name} 的绑定定义不合法。",
                step_key=step_key,
            )
        source = _required_text(raw.get("step"), "binding.step", 64)
        pointer = raw.get("pointer")
        if not isinstance(pointer, str) or (
            pointer and not pointer.startswith("/")
        ):
            raise PlanValidationError(
                "PLAN_BINDING_INVALID",
                f"字段 {target_name} 的 JSON Pointer 不合法。",
                step_key=step_key,
            )
        result[target_name] = {"step": source, "pointer": pointer}
    return result


def _validate_partial_input(
    arguments: dict[str, Any],
    *,
    bindings: dict[str, Any],
    schema: dict[str, Any],
    step_key: str,
) -> None:
    if schema.get("type") not in {None, "object"}:
        raise PlanValidationError(
            "PLAN_SCHEMA_INCOMPATIBLE",
            "一期只支持对象类型的步骤输入 Schema。",
            step_key=step_key,
        )
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        unexpected = sorted((set(arguments) | set(bindings)) - set(properties))
        if unexpected:
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID",
                f"步骤包含能力未声明的参数：{', '.join(unexpected)}。",
                step_key=step_key,
            )
    missing = sorted(set(schema.get("required") or []) - set(arguments) - set(bindings))
    if missing:
        raise PlanValidationError(
            "PLAN_SCHEMA_INVALID",
            f"步骤缺少必需参数：{', '.join(missing)}。",
            step_key=step_key,
        )
    overlap = sorted(set(arguments) & set(bindings))
    if overlap:
        raise PlanValidationError(
            "PLAN_BINDING_INVALID",
            f"静态参数与绑定重复：{', '.join(overlap)}。",
            step_key=step_key,
        )
    for name, value in arguments.items():
        definition = properties.get(name)
        if isinstance(definition, dict):
            _validate_value(value, definition, name=name, step_key=step_key)


def _validate_value(
    value: Any,
    definition: dict[str, Any],
    *,
    name: str,
    step_key: str,
) -> None:
    expected = definition.get("type")
    if expected is not None and not _matches_type(value, expected):
        raise PlanValidationError(
            "PLAN_SCHEMA_INVALID",
            f"参数 {name} 类型必须是 {expected}。",
            step_key=step_key,
        )
    if "enum" in definition and value not in definition["enum"]:
        raise PlanValidationError(
            "PLAN_SCHEMA_INVALID",
            f"参数 {name} 不在允许值范围内。",
            step_key=step_key,
        )
    if isinstance(value, str):
        if len(value) < int(definition.get("minLength", 0)):
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID", f"参数 {name} 太短。", step_key=step_key
            )
        maximum = definition.get("maxLength")
        if maximum is not None and len(value) > int(maximum):
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID", f"参数 {name} 太长。", step_key=step_key
            )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in definition and value < definition["minimum"]:
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID", f"参数 {name} 小于最小值。", step_key=step_key
            )
        if "maximum" in definition and value > definition["maximum"]:
            raise PlanValidationError(
                "PLAN_SCHEMA_INVALID", f"参数 {name} 大于最大值。", step_key=step_key
            )


def _topological_order(
    steps: dict[str, dict[str, Any]], source_order: dict[str, int]
) -> list[str]:
    for step_key, step in steps.items():
        missing = [name for name in step["dependsOn"] if name not in steps]
        if missing:
            raise PlanValidationError(
                "PLAN_DEPENDENCY_MISSING",
                f"步骤依赖不存在：{', '.join(missing)}。",
                step_key=step_key,
            )
        if step_key in step["dependsOn"]:
            raise PlanValidationError(
                "PLAN_CYCLE_DETECTED", "步骤不能依赖自己。", step_key=step_key
            )
    remaining = {key: set(step["dependsOn"]) for key, step in steps.items()}
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            (key for key, deps in remaining.items() if not deps),
            key=source_order.get,
        )
        if not ready:
            raise PlanValidationError("PLAN_CYCLE_DETECTED", "计划依赖中存在环。")
        for key in ready:
            ordered.append(key)
            remaining.pop(key)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return ordered


def _dependency_ancestors(
    steps: dict[str, dict[str, Any]], ordered_keys: list[str]
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for key in ordered_keys:
        ancestors: set[str] = set()
        for dependency in steps[key]["dependsOn"]:
            ancestors.add(dependency)
            ancestors.update(result[dependency])
        result[key] = ancestors
    return result


def _schema_type_at_pointer(schema: dict[str, Any], pointer: str) -> Any:
    current: Any = schema
    if pointer == "":
        return current.get("type") if isinstance(current, dict) else None
    if not pointer.startswith("/"):
        return None
    for encoded in pointer.split("/")[1:]:
        token = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict):
            return None
        current_type = current.get("type")
        if current_type == "object" or "properties" in current:
            current = (current.get("properties") or {}).get(token)
        elif current_type == "array" and token.isdigit():
            current = current.get("items")
        else:
            return None
        if not isinstance(current, dict):
            return None
    return current.get("type") if isinstance(current, dict) else None


def _property_schema(schema: dict[str, Any], name: str) -> dict[str, Any]:
    value = (schema.get("properties") or {}).get(name)
    return value if isinstance(value, dict) else {}


def _types_compatible(source: Any, target: Any) -> bool:
    source_types = set(source if isinstance(source, list) else [source])
    target_types = set(target if isinstance(target, list) else [target])
    if source_types & target_types:
        return True
    return "integer" in source_types and "number" in target_types


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    mapping = {
        "string": str,
        "object": dict,
        "array": list,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "null": type(None),
    }
    python_type = mapping.get(expected)
    if python_type is None:
        return True
    if expected in {"integer", "number"} and isinstance(value, bool):
        return False
    return isinstance(value, python_type)


def _required_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanValidationError("PLAN_SCHEMA_INVALID", f"{name} 不能为空。")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise PlanValidationError(
            "PLAN_SCHEMA_INVALID", f"{name} 长度不能超过 {maximum}。"
        )
    return normalized
