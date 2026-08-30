from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from bscli.core.capability import CapabilityRegistry
from bscli.core.task_plan_validation import task_plan_step_json_schema
from bscli.core.transforms import TransformRegistry


ScopeResolver = Callable[[str], frozenset[str]]


def build_planning_catalog(
    *,
    registry: CapabilityRegistry,
    transforms: TransformRegistry,
    trusted_write_prepares: Iterable[str],
    hidden_commit_capabilities: Iterable[str],
    scope_resolver: ScopeResolver,
    granted_scopes: Iterable[str],
) -> dict[str, Any]:
    granted = frozenset(str(scope) for scope in granted_scopes)
    prepares = frozenset(trusted_write_prepares)
    hidden = frozenset(hidden_commit_capabilities)
    capabilities: list[dict[str, Any]] = []
    for spec in registry.list():
        if spec.name in hidden:
            continue
        if spec.effect != "read" and spec.name not in prepares:
            continue
        required = scope_resolver(spec.name)
        if not required.issubset(granted):
            continue
        capabilities.append(
            {
                "name": spec.name,
                "version": spec.version,
                "description": spec.description,
                "system": spec.system,
                "effect": spec.effect,
                "requiredScopes": sorted(required),
                "inputSchema": spec.input_schema,
                "outputSchema": spec.output_schema,
                "planningRole": (
                    "write_sink" if spec.name in prepares else "source"
                ),
                "mayRequireTrustedInteraction": spec.name in prepares,
            }
        )
    transforms_catalog = [spec.to_dict() for spec in transforms.list()]
    capability_names = {item["name"] for item in capabilities}
    transform_names = {item["name"] for item in transforms_catalog}
    examples: list[dict[str, Any]] = []
    if (
        "oa.workflow.done.list" in capability_names
        and "work_items_to_log_draft.v1" in transform_names
    ):
        preview_steps: list[dict[str, Any]] = [
            {
                "stepKey": "read_done",
                "kind": "capability",
                "capabilityName": "oa.workflow.done.list",
                "arguments": {
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-31",
                },
            },
            {
                "stepKey": "draft_log",
                "kind": "transform",
                "transformName": "work_items_to_log_draft.v1",
                "dependsOn": ["read_done"],
                "bindings": {
                    "items": {"step": "read_done", "pointer": "/items"}
                },
            },
        ]
        examples.append(
            {
                "title": "读取 OA 已办并生成可核对的日志草稿",
                "goal": "汇总 2026 年 7 月 OA 已办并生成工作日志草稿",
                "steps": preview_steps,
            }
        )
        if "taihua.work_log.create.prepare" in capability_names:
            examples.append(
                {
                    "title": "读取 OA 已办、生成草稿并打开泰华可信填单",
                    "goal": "汇总 2026 年 7 月 OA 已办并准备填写 3 小时工作日志",
                    "steps": preview_steps
                    + [
                        {
                            "stepKey": "prepare_log",
                            "kind": "capability",
                            "capabilityName": "taihua.work_log.create.prepare",
                            "dependsOn": ["draft_log"],
                            "arguments": {
                                "log_date": "2026-07-31",
                                "hours": 3,
                            },
                            "bindings": {
                                "content": {
                                    "step": "draft_log",
                                    "pointer": "/draft",
                                }
                            },
                        }
                    ],
                }
            )

    return {
        "schemaVersion": "agentbridge.planning-catalog.v1",
        "supportedStepKinds": ["capability", "transform"],
        "limits": {
            "maximumSteps": 12,
            "maximumWriteSinks": 1,
            "maximumGoalCharacters": 500,
        },
        "capabilities": capabilities,
        "transforms": transforms_catalog,
        "prepareInputGuide": {
            "requiredFields": ["goal", "steps"],
            "stepSchema": task_plan_step_json_schema(),
            "rules": [
                "capability 步骤使用 capabilityName，不得使用 transformName",
                "transform 步骤使用 transformName，不得使用 capabilityName",
                "bindings 的键是目标输入字段，值包含 step 和 JSON Pointer pointer",
                "arguments 只能使用所选能力或转换 inputSchema.properties 中明确声明的字段",
                "目录不支持的用户要求应保留在 goal 或最终说明中，不得臆造为步骤参数",
                "只使用本目录实际返回的能力与转换名称",
            ],
            "examples": examples,
        },
        "safety": {
            "hiddenCommitToolsExcluded": True,
            "arbitraryCodeExcluded": True,
            "arbitraryHttpExcluded": True,
            "writesRequireTrustedInteractions": True,
        },
    }
