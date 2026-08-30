from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from bscli.core.capability import CapabilityRegistry
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
    return {
        "schemaVersion": "agentbridge.planning-catalog.v1",
        "supportedStepKinds": ["capability", "transform"],
        "limits": {
            "maximumSteps": 12,
            "maximumWriteSinks": 1,
            "maximumGoalCharacters": 500,
        },
        "capabilities": capabilities,
        "transforms": [spec.to_dict() for spec in transforms.list()],
        "safety": {
            "hiddenCommitToolsExcluded": True,
            "arbitraryCodeExcluded": True,
            "arbitraryHttpExcluded": True,
            "writesRequireTrustedInteractions": True,
        },
    }
