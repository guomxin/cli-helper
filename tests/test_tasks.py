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

    def test_waiting_interaction_is_broadcast_to_all_trusted_user_endpoints(self):
        origin, _ = self._endpoint()
        secondary, _ = self.store.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="wechat:direct:user-a",
            client_type="wechat",
            external_subject="wechat-user-a",
            conversation_ref="agent:main:wechat:direct:user-a",
            capabilities=["trusted_interaction", "direct_status"],
            route={"channel": "wechat", "to": "wechat-user-a"},
        )
        task, _ = self._task(origin["endpoint_id"])

        self.store.link_interaction(
            task_id=task["task_id"],
            user_subject="user-a",
            interaction_record={
                "interaction_id": "interaction-broadcast",
                "user_subject": "user-a",
            },
            interaction={
                "interactionId": "interaction-broadcast",
                "type": "execution_authorization",
                "state": "pending",
            },
        )

        secondary_outbox = self.store.list_outbox(
            user_subject="user-a",
            endpoint_id=secondary["endpoint_id"],
        )
        self.assertEqual(len(secondary_outbox), 2)
        self.assertEqual(
            secondary_outbox[0]["payload"]["eventType"],
            "task.created",
        )
        self.assertEqual(
            secondary_outbox[1]["payload"]["eventType"],
            "task.interaction.waiting",
        )
        self.assertEqual(
            secondary_outbox[1]["payload"]["payload"]["interactionId"],
            "interaction-broadcast",
        )

    def test_business_input_is_broadcast_to_all_trusted_user_endpoints(self):
        origin, _ = self._endpoint()
        secondary, _ = self.store.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:2002",
            client_type="telegram",
            external_subject="2002",
            conversation_ref="agent:main:telegram:direct:2002",
            capabilities=["trusted_interaction", "direct_status"],
            route={"channel": "telegram", "to": "2002"},
        )
        task, _ = self._task(origin["endpoint_id"])

        self.store.link_interaction(
            task_id=task["task_id"],
            user_subject="user-a",
            interaction_record={
                "interaction_id": "input-broadcast",
                "user_subject": "user-a",
            },
            interaction={
                "interactionId": "input-broadcast",
                "type": "business_input",
                "state": "pending",
            },
        )

        events = [
            item["payload"]["eventType"]
            for item in self.store.list_outbox(
                user_subject="user-a",
                endpoint_id=secondary["endpoint_id"],
            )
        ]
        self.assertEqual(
            events,
            ["task.created", "task.interaction.waiting"],
        )

    def test_endpoint_capabilities_are_merged_when_route_is_reobserved(self):
        endpoint, _ = self.store.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:1001",
            client_type="telegram",
            external_subject="1001",
            conversation_ref="agent:main:telegram:direct:1001",
            capabilities=["direct_status"],
        )
        updated, reused = self.store.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:1001",
            client_type="telegram",
            external_subject="1001",
            conversation_ref="agent:main:telegram:direct:1001",
            capabilities=["trusted_interaction"],
        )

        self.assertTrue(reused)
        self.assertEqual(updated["endpoint_id"], endpoint["endpoint_id"])
        self.assertEqual(
            set(updated["capabilities"]),
            {"direct_status", "trusted_interaction"},
        )

    def test_outbox_claim_and_ack_are_endpoint_and_user_bound(self):
        endpoint, _ = self._endpoint()
        task, _ = self._task(endpoint["endpoint_id"])

        claimed = self.store.claim_outbox(
            user_subject="user-a",
            endpoint_id=endpoint["endpoint_id"],
            limit=1,
        )
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["state"], "delivering")
        self.assertEqual(claimed[0]["attempt_count"], 1)
        self.assertEqual(
            self.store.claim_outbox(
                user_subject="user-a",
                endpoint_id=endpoint["endpoint_id"],
                limit=1,
            ),
            [],
        )
        acknowledged = self.store.acknowledge_outbox(
            user_subject="user-a",
            endpoint_id=endpoint["endpoint_id"],
            delivery_id=claimed[0]["delivery_id"],
            succeeded=True,
        )
        self.assertEqual(acknowledged["state"], "acknowledged")
        with self.assertRaises(TaskNotFound):
            self.store.acknowledge_outbox(
                user_subject="user-b",
                endpoint_id=endpoint["endpoint_id"],
                delivery_id=claimed[0]["delivery_id"],
                succeeded=True,
            )
        self.assertEqual(task["status"], "active")

    def test_outbox_delivery_stops_after_five_failed_attempts(self):
        endpoint, _ = self._endpoint()
        self._task(endpoint["endpoint_id"])
        delivery = None

        for _attempt in range(5):
            with self.store._connect() as connection:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET next_attempt_at = '2000-01-01T00:00:00+00:00'
                    WHERE endpoint_id = ? AND state IN ('pending', 'delivering')
                    """,
                    (endpoint["endpoint_id"],),
                )
            claimed = self.store.claim_outbox(
                user_subject="user-a",
                endpoint_id=endpoint["endpoint_id"],
                limit=1,
            )
            self.assertEqual(len(claimed), 1)
            delivery = self.store.acknowledge_outbox(
                user_subject="user-a",
                endpoint_id=endpoint["endpoint_id"],
                delivery_id=claimed[0]["delivery_id"],
                succeeded=False,
                retry_after_seconds=1,
            )

        self.assertEqual(delivery["state"], "failed")
        self.assertEqual(
            self.store.claim_outbox(
                user_subject="user-a",
                endpoint_id=endpoint["endpoint_id"],
            ),
            [],
        )

    def test_outbox_delivery_stops_after_five_expired_leases(self):
        endpoint, _ = self._endpoint()
        self._task(endpoint["endpoint_id"])
        delivery_id = None

        for attempt in range(5):
            with self.store._connect() as connection:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET next_attempt_at = '2000-01-01T00:00:00+00:00'
                    WHERE endpoint_id = ? AND state IN ('pending', 'delivering')
                    """,
                    (endpoint["endpoint_id"],),
                )
            claimed = self.store.claim_outbox(
                user_subject="user-a",
                endpoint_id=endpoint["endpoint_id"],
                limit=1,
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["attempt_count"], attempt + 1)
            delivery_id = claimed[0]["delivery_id"]

        with self.store._connect() as connection:
            connection.execute(
                """
                UPDATE notification_outbox
                SET next_attempt_at = '2000-01-01T00:00:00+00:00'
                WHERE delivery_id = ?
                """,
                (delivery_id,),
            )
        self.assertEqual(
            self.store.claim_outbox(
                user_subject="user-a",
                endpoint_id=endpoint["endpoint_id"],
            ),
            [],
        )
        with self.store._connect() as connection:
            row = connection.execute(
                """
                SELECT state, attempt_count FROM notification_outbox
                WHERE delivery_id = ?
                """,
                (delivery_id,),
            ).fetchone()
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["attempt_count"], 5)

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

    def test_successful_operation_finishes_task_and_leaves_active_view(self):
        endpoint, _ = self._endpoint()
        task, _ = self._task(endpoint["endpoint_id"])

        task = self.store.link_operation(
            task_id=task["task_id"],
            user_subject="user-a",
            operation={
                "operation_id": "operation-succeeded",
                "user_subject": "user-a",
                "capability_name": "oa.workflow.pending.list",
                "status": "succeeded",
                "error": None,
            },
        )

        self.assertEqual(task["status"], "succeeded")
        self.assertIsNotNone(task["finished_at"])
        self.assertEqual(
            self.store.list_tasks(
                user_subject="user-a",
                active_only=True,
            ),
            [],
        )

    def test_initialization_repairs_legacy_active_successful_tasks(self):
        db_path = Path(self.temp.name) / "repair-agentbridge.db"
        operations = OperationStore(db_path)
        operation, _ = operations.create(
            user_subject="user-a",
            capability_name="oa.workflow.pending.list",
            capability_version="1",
            input_summary={"limit": 20},
        )
        operation = operations.mark_succeeded(
            operation["operation_id"],
            {"count": 0},
        )
        task_store = TaskHubStore(db_path)
        endpoint, _ = task_store.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="workspace:account-a",
            client_type="workspace",
            external_subject="account-a",
            conversation_ref="agent:main:workspace:account-a",
        )
        task, _ = task_store.ensure_task(
            user_subject="user-a",
            agent_host="openclaw",
            host_task_key="agent:main:workspace:account-a|run-1",
            origin_endpoint_id=endpoint["endpoint_id"],
            active_conversation_ref="agent:main:workspace:account-a",
            title="List Pending OA Workflows",
        )
        task_store.link_operation(
            task_id=task["task_id"],
            user_subject="user-a",
            operation=operation,
        )
        with task_store._connect() as connection:
            connection.execute(
                """
                UPDATE agent_tasks
                SET status = 'active', finished_at = NULL
                WHERE task_id = ?
                """,
                (task["task_id"],),
            )

        repaired_store = TaskHubStore(db_path)
        repaired = repaired_store.get_task(
            task["task_id"],
            user_subject="user-a",
        )

        self.assertEqual(repaired["status"], "succeeded")
        self.assertIsNotNone(repaired["finished_at"])

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

    def test_central_service_presents_one_authorization_on_multiple_endpoints(self):
        service = CentralCapabilityService(
            home=Path(self.temp.name),
            base_url="http://oa.example.test/seeyon/main.do?method=main",
        )
        origin = service.ensure_host_task(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            host_task_key="telegram-task",
            endpoint_key="telegram:*:1001",
            client_type="telegram",
            external_subject="1001",
            conversation_ref="agent:main:telegram:direct:1001",
            title="Submit leave request",
            route={"channel": "telegram", "to": "1001"},
            capabilities=["trusted_interaction", "direct_status"],
        )
        service.tasks.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="wechat:direct:user-a",
            client_type="wechat",
            external_subject="wechat-user-a",
            conversation_ref="agent:main:wechat:direct:user-a",
            route={"channel": "wechat", "to": "wechat-user-a"},
            capabilities=["trusted_interaction", "direct_status"],
        )
        authorization = service.write_authorizations.create(
            user_subject="user-a",
            system_id="oa",
            session_id="session-a",
            capability_name="oa.leave.submit",
            capability_version="1",
            prepare_operation_id="prepare-a",
            plan={"reason": "Test"},
            summary={"title": "Submit leave request", "fields": []},
            card_base_url="https://cards.example.test",
        )
        interaction = service._execution_authorization_interaction(authorization)
        service.observe_host_task(
            user_subject="user-a",
            task_id=origin["task"]["taskId"],
            interaction_ids=[interaction["interactionId"]],
        )

        telegram = service.present_interaction(
            user_subject="user-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:1001",
            interaction_id=interaction["interactionId"],
        )
        wechat = service.claim_host_notifications(
            user_subject="user-a",
            agent_host="openclaw",
            endpoint_key="wechat:direct:user-a",
        )

        self.assertEqual(wechat["count"], 1)
        notification = wechat["notifications"][0]
        self.assertEqual(
            notification["deliveryMode"],
            "trusted_interaction",
        )
        self.assertNotEqual(
            telegram["interaction"]["presentation"]["url"],
            notification["interaction"]["presentation"]["url"],
        )
        self.assertTrue(
            telegram["interaction"]["presentation"]["individualized"]
        )
        acknowledged = service.acknowledge_host_notification(
            user_subject="user-a",
            agent_host="openclaw",
            endpoint_key="wechat:direct:user-a",
            delivery_id=notification["deliveryId"],
            succeeded=True,
        )
        self.assertEqual(
            acknowledged["delivery"]["state"],
            "acknowledged",
        )
        presentation_id = telegram["interaction"]["presentation"][
            "presentationId"
        ]
        csrf = service.write_authorizations.issue_csrf(
            authorization["authorization_id"],
            presentation_id=presentation_id,
        )
        service.write_authorizations.decide(
            authorization["authorization_id"],
            decision="approve",
            csrf_token=csrf,
            csrf_cookie=csrf,
            presentation_id=presentation_id,
        )
        service.get_interaction(
            user_subject="user-a",
            interaction_id=interaction["interactionId"],
        )
        terminal = service.claim_host_notifications(
            user_subject="user-a",
            agent_host="openclaw",
            endpoint_key="wechat:direct:user-a",
        )
        self.assertEqual(terminal["count"], 1)
        self.assertEqual(
            terminal["notifications"][0]["deliveryMode"],
            "status",
        )
        self.assertIn(
            "可信确认已完成",
            terminal["notifications"][0]["message"],
        )

    def test_central_service_presents_business_input_on_multiple_endpoints(self):
        service = CentralCapabilityService(
            home=Path(self.temp.name),
            base_url="http://oa.example.test/seeyon/main.do?method=main",
        )
        origin = service.ensure_host_task(
            user_subject="user-a",
            token_id="workspace-token",
            agent_host="openclaw",
            host_task_key="workspace-task",
            endpoint_key="workspace:account-a",
            client_type="web",
            external_subject="account-a",
            account_id="account-a",
            conversation_ref=(
                "agent:main:agentbridge-workspace:direct:account-a"
            ),
            title="Submit business trip",
            capabilities=["workspace.task.read"],
        )
        service.tasks.ensure_endpoint(
            user_subject="user-a",
            token_id="token-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:1001",
            client_type="telegram",
            external_subject="1001",
            conversation_ref="agent:main:telegram:direct:1001",
            route={"channel": "telegram", "to": "1001"},
            capabilities=["trusted_interaction", "direct_status"],
        )
        submission = service.field_submissions.create(
            user_subject="user-a",
            system_id="oa",
            session_id="session-a",
            capability_name="oa.business_trip.submit.prepare",
            capability_version="1",
            create_operation_id="prepare-input",
            form_schema={
                "title": "Business trip",
                "fields": [
                    {
                        "name": "reason",
                        "label": "Reason",
                        "control": "text",
                        "required": True,
                    }
                ],
            },
            card_base_url="https://cards.example.test",
        )
        interaction = service._business_input_interaction(submission)
        service.observe_host_task(
            user_subject="user-a",
            task_id=origin["task"]["taskId"],
            interaction_ids=[interaction["interactionId"]],
        )

        workspace = service.present_interaction(
            user_subject="user-a",
            agent_host="openclaw",
            endpoint_key="workspace:account-a",
            interaction_id=interaction["interactionId"],
        )
        telegram = service.claim_host_notifications(
            user_subject="user-a",
            agent_host="openclaw",
            endpoint_key="telegram:*:1001",
        )

        self.assertEqual(telegram["count"], 1)
        delivered = telegram["notifications"][0]
        self.assertEqual(delivered["deliveryMode"], "trusted_interaction")
        self.assertTrue(
            delivered["interaction"]["presentation"]["individualized"]
        )
        self.assertNotEqual(
            workspace["interaction"]["presentation"]["url"],
            delivered["interaction"]["presentation"]["url"],
        )

    def test_workspace_task_does_not_overwrite_registered_endpoint(self):
        service = CentralCapabilityService(
            home=Path(self.temp.name),
            base_url="http://oa.example.test/seeyon/main.do?method=main",
        )
        endpoint, _ = service.tasks.ensure_endpoint(
            user_subject="user-a",
            token_id="workspace-account:account-a",
            agent_host="openclaw",
            endpoint_key="workspace:account-a",
            client_type="web",
            external_subject="account-a",
            account_id="account-a",
            conversation_ref=(
                "agent:main:agentbridge-workspace:direct:account-a"
            ),
            label="Agent Workspace: alice",
            capabilities=["workspace.task.read"],
        )

        service.ensure_host_task(
            user_subject="user-a",
            token_id="telegram-token",
            agent_host="openclaw",
            host_task_key="workspace-session|run-1",
            endpoint_key="workspace:account-a",
            client_type="web",
            external_subject="account-a",
            account_id="account-a",
            conversation_ref=(
                "agent:main:agentbridge-workspace:direct:account-a"
            ),
            title="Read pending items",
            capabilities=["workspace.task.read"],
        )
        preserved = service.tasks.endpoint_for_key(
            user_subject="user-a",
            agent_host="openclaw",
            endpoint_key="workspace:account-a",
        )

        self.assertEqual(preserved["endpoint_id"], endpoint["endpoint_id"])
        self.assertEqual(
            preserved["token_id"],
            "workspace-account:account-a",
        )
        self.assertEqual(preserved["client_type"], "web")
        self.assertEqual(preserved["label"], "Agent Workspace: alice")

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
