from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bscli.core.task_plans import (
    TaskPlanConflict,
    TaskPlanNotFound,
    TaskPlanStore,
)


class TaskPlanStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.store = TaskPlanStore(Path(self.temporary.name) / "agentbridge.db")

    def tearDown(self):
        self.temporary.cleanup()

    def compiled(self):
        return {
            "goal": "读取并转换",
            "planHash": "a" * 64,
            "riskSummary": {
                "systems": ["oa"],
                "requiredScopes": ["oa:read"],
                "writeSinkCount": 0,
            },
            "steps": [
                {
                    "stepKey": "read",
                    "ordinal": 1,
                    "kind": "capability",
                    "title": "读取",
                    "capabilityName": "oa.workflow.done.list",
                    "transformName": None,
                    "version": "0.2.0",
                    "dependsOn": [],
                    "arguments": {},
                    "bindings": {},
                    "effect": "read",
                    "systemId": "oa",
                }
            ],
        }

    def test_create_and_idempotent_reuse(self):
        plan, reused = self.store.create(
            user_subject="user-1",
            parent_task_id="task-1",
            compiled_plan=self.compiled(),
            proposal_source="agent_host",
            coordinator_lease_version=1,
            idempotency_key="same",
        )
        replay, replayed = self.store.create(
            user_subject="user-1",
            parent_task_id="task-1",
            compiled_plan=self.compiled(),
            proposal_source="agent_host",
            coordinator_lease_version=1,
            idempotency_key="same",
        )

        self.assertFalse(reused)
        self.assertTrue(replayed)
        self.assertEqual(replay["plan_id"], plan["plan_id"])
        self.assertEqual(plan["steps"][0]["state"], "queued")

    def test_one_task_cannot_have_two_active_plans(self):
        self.store.create(
            user_subject="user-1",
            parent_task_id="task-1",
            compiled_plan=self.compiled(),
            proposal_source="agent_host",
            coordinator_lease_version=1,
            idempotency_key="one",
        )

        with self.assertRaises(TaskPlanConflict):
            self.store.create(
                user_subject="user-1",
                parent_task_id="task-1",
                compiled_plan={**self.compiled(), "planHash": "b" * 64},
                proposal_source="agent_host",
                coordinator_lease_version=1,
                idempotency_key="two",
            )

    def test_cross_user_read_is_hidden(self):
        plan, _ = self.store.create(
            user_subject="user-1",
            parent_task_id="task-1",
            compiled_plan=self.compiled(),
            proposal_source="agent_host",
            coordinator_lease_version=1,
            idempotency_key="one",
        )

        with self.assertRaises(TaskPlanNotFound):
            self.store.get(plan["plan_id"], user_subject="user-2")


if __name__ == "__main__":
    unittest.main()
