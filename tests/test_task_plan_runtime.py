from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bscli.core.capability import CapabilityRegistry, CapabilitySpec
from bscli.core.operations import OperationStore
from bscli.core.task_plan_runtime import TaskPlanRuntime
from bscli.core.task_plan_validation import validate_and_compile_task_plan
from bscli.core.task_plans import TaskPlanStore
from bscli.core.transforms import build_transform_registry


class FakeTaskHub:
    def __init__(self):
        self.events = []
        self.operations = []
        self.interactions = []
        self.completed = []
        self.failed = []

    def record_plan_event(self, **values):
        self.events.append(values)
        return {}

    def link_plan_operation(self, **values):
        self.operations.append(values)
        return {}

    def link_interaction(self, **values):
        self.interactions.append(values)
        return {}

    def complete_task(self, **values):
        self.completed.append(values)
        return {}

    def fail_task(self, **values):
        self.failed.append(values)
        return {}

    def mark_task_outcome_unknown(self, **values):
        self.failed.append(values)
        return {}

    def cancel_task(self, **values):
        return {}


class FakePlanService:
    def __init__(self, db_path):
        self.registry = CapabilityRegistry()
        self.registry.register(
            CapabilitySpec(
                name="source.items",
                version="1.0.0",
                description="Read source items",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"type": "object"},
                        }
                    },
                    "required": ["items"],
                    "additionalProperties": False,
                },
                effect="read",
                adapter="fake",
                workflow="source-v1",
            )
        )
        self.registry.register(
            CapabilitySpec(
                name="sink.prepare",
                version="1.0.0",
                description="Prepare sink write",
                input_schema={
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                effect="controlled_write",
                adapter="fake",
                workflow="sink-v1",
            )
        )
        self.operations = OperationStore(db_path)
        self.tasks = FakeTaskHub()
        self.interaction = None
        self.source_requires_login = False
        self.source_calls = 0

    def invoke(self, *, user_subject, capability_name, arguments, idempotency_key, **_):
        spec = self.registry.get(capability_name)
        operation, reused = self.operations.create(
            user_subject=user_subject,
            capability_name=capability_name,
            capability_version=spec.version,
            input_summary=arguments,
            input_identity=arguments,
            idempotency_key=idempotency_key,
        )
        if reused:
            stored = self.operations.get(operation["operation_id"])
            return self._response(stored, reused=True)
        self.operations.mark_running(operation["operation_id"])
        if capability_name == "source.items":
            self.source_calls += 1
            if self.source_requires_login and self.source_calls == 1:
                interaction_id = "login-interaction-0000000000001"
                self.interaction = self._interaction(
                    interaction_id, interaction_type="credential"
                )
                operation = self.operations.mark_requires_user_action(
                    operation["operation_id"],
                    code="LOGIN_REQUIRED",
                    message="login required",
                    next_action={"interaction": self.interaction},
                )
            else:
                operation = self.operations.mark_succeeded(
                    operation["operation_id"],
                    {
                        "items": [
                            {
                                "affair_id": "1",
                                "title": "流程一",
                                "date": "2026-08-30",
                                "category": "审批",
                            }
                        ]
                    },
                )
        else:
            interaction_id = "field-interaction-0000000000001"
            self.interaction = self._interaction(
                interaction_id, interaction_type="business_input"
            )
            operation = self.operations.mark_requires_user_action(
                operation["operation_id"],
                code="BUSINESS_INPUT_REQUIRED",
                message="field input required",
                next_action={"interaction": self.interaction},
            )
        return self._response(operation, reused=False)

    def _load_interaction(self, *, interaction_id, **_):
        if self.interaction is None or self.interaction["interactionId"] != interaction_id:
            raise KeyError(interaction_id)
        record = {
            "interaction_id": interaction_id,
            "user_subject": "user-1",
        }
        return record, {}, self.interaction

    @staticmethod
    def _interaction(interaction_id, *, interaction_type):
        return {
            "schemaVersion": "agentbridge.interaction.v1",
            "interactionId": interaction_id,
            "type": interaction_type,
            "state": "pending",
            "title": "Trusted interaction",
            "message": "Continue in the trusted surface.",
            "presentation": {"url": "https://agentbridge.test/card"},
            "resume": {"ready": False, "completed": False},
        }

    @staticmethod
    def _response(operation, *, reused):
        interaction = (operation.get("next_action") or {}).get("interaction")
        return {
            "protocolVersion": "0.1",
            "operationId": operation["operation_id"],
            "status": operation["status"],
            "result": operation.get("result"),
            "error": operation.get("error"),
            "nextAction": operation.get("next_action"),
            "interaction": interaction,
            "reused": reused,
        }


class TaskPlanRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "agentbridge.db"
        self.service = FakePlanService(self.db_path)
        self.plans = TaskPlanStore(self.db_path)
        self.transforms = build_transform_registry()
        self.runtime = TaskPlanRuntime(
            service=self.service,
            plans=self.plans,
            transforms=self.transforms,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def compile(self, *, include_sink):
        steps = [
            {
                "stepKey": "read",
                "kind": "capability",
                "capabilityName": "source.items",
            },
            {
                "stepKey": "draft",
                "kind": "transform",
                "transformName": "work_items_to_log_draft.v1",
                "dependsOn": ["read"],
                "bindings": {"items": {"step": "read", "pointer": "/items"}},
            },
        ]
        if include_sink:
            steps.append(
                {
                    "stepKey": "write",
                    "kind": "capability",
                    "capabilityName": "sink.prepare",
                    "dependsOn": ["draft"],
                    "bindings": {
                        "content": {"step": "draft", "pointer": "/draft"}
                    },
                }
            )
        return validate_and_compile_task_plan(
            {
                "schemaVersion": "agentbridge.task-plan.proposal.v1",
                "goal": "test plan",
                "steps": steps,
            },
            registry=self.service.registry,
            transforms=self.transforms,
            trusted_write_prepares={"sink.prepare"},
            hidden_commit_capabilities={"sink.commit"},
            scope_resolver=lambda name: frozenset(
                {"sink:write"} if name == "sink.prepare" else {"source:read"}
            ),
            granted_scopes={"source:read", "sink:write"},
        )

    def create_plan(self, *, include_sink):
        plan, _ = self.plans.create(
            user_subject="user-1",
            parent_task_id="task-1",
            compiled_plan=self.compile(include_sink=include_sink),
            proposal_source="agent_host",
            coordinator_lease_version=1,
            idempotency_key="plan-1",
        )
        return plan

    def test_read_and_transform_plan_completes_without_host_replanning(self):
        plan = self.create_plan(include_sink=False)

        response = self.runtime.start(plan["plan_id"], user_subject="user-1")

        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(response["plan"]["state"], "succeeded")
        self.assertEqual(
            [step["state"] for step in response["plan"]["steps"]],
            ["succeeded", "succeeded"],
        )
        self.assertEqual(len(self.service.tasks.completed), 1)

    def test_write_prepare_waits_then_resumes_to_plan_completion(self):
        plan = self.create_plan(include_sink=True)
        waiting = self.runtime.start(plan["plan_id"], user_subject="user-1")

        self.assertEqual(waiting["status"], "requires_user_action")
        self.assertEqual(waiting["plan"]["state"], "waiting_user")
        operation, _ = self.service.operations.create(
            user_subject="user-1",
            capability_name="sink.commit",
            capability_version="1.0.0",
            input_summary={},
            input_identity={},
            idempotency_key="commit-1",
        )
        self.service.operations.mark_running(operation["operation_id"])
        operation = self.service.operations.mark_succeeded(
            operation["operation_id"], {"verified": True}
        )
        completed = self.runtime.resume_after_capability(
            user_subject="user-1",
            interaction_id=waiting["interaction"]["interactionId"],
            response=self.service._response(operation, reused=False),
        )

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["plan"]["state"], "succeeded")
        self.assertEqual(completed["plan"]["steps"][-1]["state"], "succeeded")

    def test_login_completion_retries_the_same_step_with_a_new_attempt(self):
        self.service.source_requires_login = True
        plan = self.create_plan(include_sink=False)
        waiting = self.runtime.start(plan["plan_id"], user_subject="user-1")

        completed = self.runtime.resume_after_session(
            user_subject="user-1",
            interaction_id=waiting["interaction"]["interactionId"],
        )

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(self.service.source_calls, 2)
        stored = self.plans.get(plan["plan_id"], user_subject="user-1")
        self.assertEqual(stored["steps"][0]["attempt_count"], 2)

    def test_new_runtime_recovers_an_abandoned_running_step(self):
        plan = self.create_plan(include_sink=False)
        started = self.plans.begin_next_step(
            plan["plan_id"], user_subject="user-1"
        )
        self.assertEqual(started["state"], "running")
        restarted_runtime = TaskPlanRuntime(
            service=self.service,
            plans=self.plans,
            transforms=self.transforms,
        )

        summary = restarted_runtime.recover()

        self.assertEqual(summary["candidates"], 1)
        self.assertEqual(summary["restarted"], 1)
        self.assertEqual(summary["completed"], 1)
        stored = self.plans.get(plan["plan_id"], user_subject="user-1")
        self.assertEqual(stored["state"], "succeeded")
        self.assertEqual(stored["steps"][0]["attempt_count"], 2)

    def test_canceling_a_completed_plan_is_a_quiet_idempotent_read(self):
        plan = self.create_plan(include_sink=False)
        self.runtime.start(plan["plan_id"], user_subject="user-1")
        event_count = len(self.service.tasks.events)

        response = self.runtime.cancel(
            plan["plan_id"], user_subject="user-1", reason="too_late"
        )

        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(len(self.service.tasks.events), event_count)


if __name__ == "__main__":
    unittest.main()
