import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bscli.core.central_service import CentralCapabilityService
from bscli.core.interactions import InteractionStore
from bscli.core.operations import OperationStore
from bscli.core.tasks import TaskHubStore, TaskIntegrityError, TaskNotFound


class TaskHubStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskHubStore(Path(self.temp.name) / "agentbridge.db")

    def test_ensures_endpoint_and_task_idempotently_with_origin_subscription(self):
        endpoint, endpoint_reused = self._endpoint()
        task, task_reused = self._task(endpoint["endpoint_id"])
        endpoint_again, endpoint_reused_again = self._endpoint()
        task_again, task_reused_again = self._task(endpoint_again["endpoint_id"])

        self.assertFalse(endpoint_reused)
        self.assertFalse(task_reused)
        self.assertTrue(endpoint_reused_again)
        self.assertTrue(task_reused_again)
        self.assertEqual(endpoint_again["endpoint_id"], endpoint["endpoint_id"])
        self.assertEqual(task_again["task_id"], task["task_id"])
        self.assertEqual(
            [event["event_type"] for event in self.store.list_events(
                task_id=task["task_id"],
                user_subject="user-a",
            )],
            ["task.created"],
        )
        outbox = self.store.list_outbox(
            user_subject="user-a",
            endpoint_id=endpoint["endpoint_id"],
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["payload"]["eventType"], "task.created")
        self.assertNotIn("token_id", outbox[0]["payload"])

    def test_links_operations_and_interactions_without_mutating_source_contracts(self):
        endpoint, _ = self._endpoint()
        task, _ = self._task(endpoint["endpoint_id"])
        task = self.store.link_operation(
            task_id=task["task_id"],
            user_subject="user-a",
            operation={
                "operation_id": "operation-a",
                "user_subject": "user-a",
                "capability_name": "oa.leave.prepare",
                "status": "requires_user_action",
                "error": {"code": "FIELDS_REQUIRED"},
            },
        )
        task = self.store.link_interaction(
            task_id=task["task_id"],
            user_subject="user-a",
            interaction_record={
                "interaction_id": "interaction-a",
                "user_subject": "user-a",
            },
            interaction={
                "interactionId": "interaction-a",
                "type": "business_input",
                "state": "pending",
            },
        )

        self.assertEqual(task["status"], "waiting_user")
        self.assertEqual(task["current_operation_id"], "operation-a")
        self.assertEqual(task["current_interaction_id"], "interaction-a")
        self.assertEqual(
            self.store.task_id_for_operation(
                "operation-a",
                user_subject="user-a",
            ),
            task["task_id"],
        )
        self.assertEqual(
            self.store.task_id_for_interaction(
                "interaction-a",
                user_subject="user-a",
            ),
            task["task_id"],
        )
        candidates = self.store.recovery_candidates(
            user_subject="user-a",
            endpoint_id=endpoint["endpoint_id"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["interaction_id"], "interaction-a")
        listed = self.store.list_tasks(
            user_subject="user-a",
            endpoint_id=endpoint["endpoint_id"],
            active_only=True,
        )
        self.assertEqual([item["task_id"] for item in listed], [task["task_id"]])

    def test_rejects_cross_user_endpoint_task_and_artifact_access(self):
        endpoint, _ = self._endpoint()
        task, _ = self._task(endpoint["endpoint_id"])

        with self.assertRaises(TaskIntegrityError):
            self.store.ensure_endpoint(
                user_subject="user-b",
                token_id="token-b",
                agent_host="openclaw",
                endpoint_key="telegram:*:1001",
                client_type="telegram",
                external_subject="1001",
                conversation_ref="agent:main:telegram:direct:1001",
            )
        with self.assertRaises(TaskNotFound):
            self.store.get_task(task["task_id"], user_subject="user-b")
        with self.assertRaises(TaskIntegrityError):
            self.store.link_operation(
                task_id=task["task_id"],
                user_subject="user-a",
                operation={
                    "operation_id": "operation-b",
                    "user_subject": "user-b",
                    "status": "succeeded",
                },
            )

    def test_outcome_unknown_is_not_hidden_by_later_interaction_observation(self):
        endpoint, _ = self._endpoint()
        task, _ = self._task(endpoint["endpoint_id"])
        task = self.store.link_operation(
            task_id=task["task_id"],
            user_subject="user-a",
            operation={
                "operation_id": "operation-unknown",
                "user_subject": "user-a",
                "capability_name": "oa.leave.submit",
                "status": "unknown",
                "error": {"code": "RESULT_UNKNOWN"},
            },
        )
        task = self.store.link_interaction(
            task_id=task["task_id"],
            user_subject="user-a",
            interaction_record={
                "interaction_id": "interaction-late",
                "user_subject": "user-a",
            },
            interaction={
                "interactionId": "interaction-late",
                "type": "execution_authorization",
                "state": "completed",
            },
        )

        self.assertEqual(task["status"], "outcome_unknown")

    def test_central_service_recovers_only_the_bound_users_pending_interaction(self):
        service = CentralCapabilityService(
            home=Path(self.temp.name),
            base_url="http://oa.example.test/seeyon/main.do?method=main",
        )
        task_response = service.ensure_host_task(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            host_task_key="session|run",
            endpoint_key="telegram:*:1001",
            client_type="telegram",
            external_subject="1001",
            conversation_ref="agent:main:telegram:direct:1001",
            title="Login and read OA",
            route={"channel": "telegram", "to": "1001"},
        )
        session = service.sessions.get_or_create(
            user_subject="user-a",
            system_id="oa",
        )
        challenge = service.challenges.create(
            user_subject="user-a",
            system_id="oa",
            system_name="OA",
            session_id=session["session_id"],
            origin="http://oa.example.test",
            page_fingerprint="login-page",
            nonce=None,
            fields=[],
            card_base_url="https://cards.example.test",
            challenge_type="interactive_browser_login",
        )
        interaction = service._credential_interaction(challenge)
        observed = service.observe_host_task(
            user_subject="user-a",
            task_id=task_response["task"]["taskId"],
            interaction_ids=[interaction["interactionId"]],
        )
        recovered = service.recover_host_tasks(
            user_subject="user-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:1001",
        )

        self.assertEqual(observed["task"]["status"], "waiting_user")
        self.assertEqual(recovered["count"], 1)
        self.assertEqual(
            recovered["recoveries"][0]["task"]["taskId"],
            task_response["task"]["taskId"],
        )
        self.assertEqual(
            recovered["recoveries"][0]["interaction"]["interactionId"],
            interaction["interactionId"],
        )
        with self.assertRaises(TaskNotFound):
            service.recover_host_tasks(
                user_subject="user-b",
                agent_host="openclaw",
                endpoint_key="telegram:*:1001",
            )

    def test_task_schema_migrates_alongside_existing_ledgers(self):
        db_path = Path(self.temp.name) / "legacy-agentbridge.db"
        operations = OperationStore(db_path)
        operation, _ = operations.create(
            user_subject="user-a",
            capability_name="oa.workflow.pending.list",
            capability_version="1",
            input_summary={"limit": 1},
        )
        interactions = InteractionStore(db_path)
        interaction = interactions.register(
            interaction_type="business_input",
            user_subject="user-a",
            system_id="oa",
            session_id="session-a",
            operation_id=operation["operation_id"],
            resource_id="resource-a",
            title="Enter fields",
            message="Use the trusted page.",
            display={"fieldCount": 1},
            resume_spec={
                "kind": "capability",
                "capability": "oa.leave.prepare",
                "arguments": {"input_submission_id": "resource-a"},
            },
            created_at="2026-07-30T00:00:00+00:00",
            expires_at="2099-07-30T00:30:00+00:00",
        )

        task_store = TaskHubStore(db_path)

        self.assertEqual(
            operations.get(operation["operation_id"])["user_subject"],
            "user-a",
        )
        self.assertEqual(
            interactions.get(
                interaction["interaction_id"],
                user_subject="user-a",
            )["resource_id"],
            "resource-a",
        )
        endpoint, _ = task_store.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:1001",
            client_type="telegram",
            external_subject="1001",
            conversation_ref="agent:main:telegram:direct:1001",
        )
        task, _ = task_store.ensure_task(
            user_subject="user-a",
            agent_host="openclaw",
            host_task_key="session|run",
            origin_endpoint_id=endpoint["endpoint_id"],
            active_conversation_ref="agent:main:telegram:direct:1001",
            title="Migrated task",
        )
        self.assertEqual(task["status"], "active")

    def _endpoint(self):
        return self.store.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:1001",
            client_type="telegram",
            external_subject="1001",
            conversation_ref="agent:main:telegram:direct:1001",
            label="User A",
            capabilities=["direct_status", "trusted_interaction"],
            route={"channel": "telegram", "to": "1001"},
        )

    def _task(self, endpoint_id):
        return self.store.ensure_task(
            user_subject="user-a",
            agent_host="openclaw",
            host_task_key="agent:main:telegram:direct:1001|run-1",
            origin_endpoint_id=endpoint_id,
            active_conversation_ref="agent:main:telegram:direct:1001",
            title="OA Leave Request",
        )


if __name__ == "__main__":
    unittest.main()
