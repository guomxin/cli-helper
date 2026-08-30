import unittest

from bscli.adapters.seeyon_central import build_central_capability_registry
from bscli.adapters.taihua import (
    TAIHUA_WORK_LOG_CREATE_CAPABILITY,
    TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY,
    build_taihua_capability_registry,
)
from bscli.core.task_plan_validation import (
    PlanValidationError,
    TaskPlanCapabilityStepInput,
    TaskPlanTransformStepInput,
    serialize_task_plan_steps,
    task_plan_step_json_schema,
    validate_and_compile_task_plan,
)
from bscli.core.planning_catalog import build_planning_catalog
from bscli.core.transforms import build_transform_registry


class TaskPlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_central_capability_registry()
        for spec in build_taihua_capability_registry().list():
            self.registry.register(spec)
        self.transforms = build_transform_registry()
        self.prepares = {TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY}
        self.commits = {TAIHUA_WORK_LOG_CREATE_CAPABILITY}

    def compile(self, proposal, *, granted=None):
        return validate_and_compile_task_plan(
            proposal,
            registry=self.registry,
            transforms=self.transforms,
            trusted_write_prepares=self.prepares,
            hidden_commit_capabilities=self.commits,
            scope_resolver=lambda name: (
                frozenset({"taihua:write:worklog"})
                if name.startswith("taihua.")
                else frozenset({"oa:read"})
            ),
            granted_scopes=granted,
        )

    def proposal(self):
        return {
            "schemaVersion": "agentbridge.task-plan.proposal.v1",
            "goal": "汇总今天 OA 已办并填写工作日志",
            "steps": [
                {
                    "stepKey": "read_done",
                    "kind": "capability",
                    "capabilityName": "oa.workflow.done.list",
                    "arguments": {
                        "start_date": "2026-08-30",
                        "end_date": "2026-08-30",
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
                {
                    "stepKey": "prepare_log",
                    "kind": "capability",
                    "capabilityName": TAIHUA_WORK_LOG_CREATE_PREPARE_CAPABILITY,
                    "dependsOn": ["draft_log"],
                    "arguments": {"log_date": "2026-08-30", "hours": 3},
                    "bindings": {
                        "content": {"step": "draft_log", "pointer": "/draft"}
                    },
                },
            ],
        }

    def test_valid_plan_is_topologically_compiled_and_scoped(self):
        compiled = self.compile(
            self.proposal(),
            granted={"oa:read", "taihua:write:worklog"},
        )

        self.assertEqual(
            [step["stepKey"] for step in compiled["steps"]],
            ["read_done", "draft_log", "prepare_log"],
        )
        self.assertEqual(
            compiled["requiredScopes"],
            ["oa:read", "taihua:write:worklog"],
        )
        self.assertEqual(compiled["riskSummary"]["writeSinkCount"], 1)
        self.assertEqual(len(compiled["planHash"]), 64)

    def test_hidden_commit_is_rejected(self):
        proposal = self.proposal()
        proposal["steps"][-1]["capabilityName"] = TAIHUA_WORK_LOG_CREATE_CAPABILITY

        with self.assertRaises(PlanValidationError) as raised:
            self.compile(proposal)

        self.assertEqual(raised.exception.code, "PLAN_CAPABILITY_NOT_ALLOWED")

    def test_missing_scope_is_rejected_before_execution(self):
        with self.assertRaises(PlanValidationError) as raised:
            self.compile(self.proposal(), granted={"oa:read"})

        self.assertEqual(raised.exception.code, "PLAN_SCOPE_MISSING")

    def test_binding_must_reference_dependency_chain(self):
        proposal = self.proposal()
        proposal["steps"][1]["dependsOn"] = []

        with self.assertRaises(PlanValidationError) as raised:
            self.compile(proposal)

        self.assertEqual(raised.exception.code, "PLAN_BINDING_INVALID")

    def test_cycle_is_rejected(self):
        proposal = self.proposal()
        proposal["steps"][0]["dependsOn"] = ["draft_log"]

        with self.assertRaises(PlanValidationError) as raised:
            self.compile(proposal)

        self.assertEqual(raised.exception.code, "PLAN_CYCLE_DETECTED")

    def test_write_sink_must_be_final(self):
        proposal = self.proposal()
        proposal["steps"].append(
            {
                "stepKey": "read_again",
                "kind": "capability",
                "capabilityName": "oa.workflow.done.list",
                "dependsOn": ["prepare_log"],
            }
        )

        with self.assertRaises(PlanValidationError) as raised:
            self.compile(proposal)

        self.assertEqual(raised.exception.code, "PLAN_WRITE_SINK_NOT_FINAL")

    def test_model_visible_step_schema_exposes_exact_fields(self):
        schema = task_plan_step_json_schema()

        self.assertEqual(schema["discriminator"]["propertyName"], "kind")
        capability = schema["$defs"]["TaskPlanCapabilityStepInput"]
        transform = schema["$defs"]["TaskPlanTransformStepInput"]
        binding = schema["$defs"]["TaskPlanBindingInput"]
        self.assertTrue(
            {"stepKey", "kind", "capabilityName"}.issubset(capability["required"])
        )
        self.assertTrue(
            {"stepKey", "kind", "transformName"}.issubset(transform["required"])
        )
        self.assertEqual(set(binding["required"]), {"step", "pointer"})

    def test_typed_steps_preserve_only_fields_supplied_by_host(self):
        steps = [
            TaskPlanCapabilityStepInput(
                stepKey="read_done",
                kind="capability",
                capabilityName="oa.workflow.done.list",
                arguments={"start_date": "2026-07-01"},
            ),
            TaskPlanTransformStepInput(
                stepKey="draft_log",
                kind="transform",
                transformName="work_items_to_log_draft.v1",
                dependsOn=["read_done"],
                bindings={
                    "items": {"step": "read_done", "pointer": "/items"}
                },
            ),
        ]

        self.assertEqual(
            serialize_task_plan_steps(steps),
            [
                {
                    "stepKey": "read_done",
                    "kind": "capability",
                    "capabilityName": "oa.workflow.done.list",
                    "arguments": {"start_date": "2026-07-01"},
                },
                {
                    "stepKey": "draft_log",
                    "dependsOn": ["read_done"],
                    "bindings": {
                        "items": {"step": "read_done", "pointer": "/items"}
                    },
                    "kind": "transform",
                    "transformName": "work_items_to_log_draft.v1",
                },
            ],
        )

    def test_planning_catalog_returns_scope_safe_prepare_examples(self):
        def resolve(name):
            if name.startswith("taihua."):
                return frozenset({"taihua:write:worklog"})
            return frozenset({"oa:read"})

        full = build_planning_catalog(
            registry=self.registry,
            transforms=self.transforms,
            trusted_write_prepares=self.prepares,
            hidden_commit_capabilities=self.commits,
            scope_resolver=resolve,
            granted_scopes={"oa:read", "taihua:write:worklog"},
        )
        limited = build_planning_catalog(
            registry=self.registry,
            transforms=self.transforms,
            trusted_write_prepares=self.prepares,
            hidden_commit_capabilities=self.commits,
            scope_resolver=resolve,
            granted_scopes={"oa:read"},
        )

        self.assertEqual(len(full["prepareInputGuide"]["examples"]), 2)
        self.assertEqual(len(limited["prepareInputGuide"]["examples"]), 1)
        self.assertEqual(
            limited["prepareInputGuide"]["examples"][0]["steps"][0]["stepKey"],
            "read_done",
        )
        self.assertNotIn(
            "taihua.work_log.create.prepare",
            str(limited["prepareInputGuide"]["examples"]),
        )


if __name__ == "__main__":
    unittest.main()
